"""
RECEPTRA — JAPAN PHONE NUMBER LENGTH GUARD（12桁電話番号の捏造防止）回帰テスト

実機で、お客様が「080-3964-4468」（11桁: 08039644468）と発話したにも
かかわらず、AIが最終確認で「080-3964-44468」（12桁: 080396444468）という
1桁多い番号を復唱・確定しようとした不具合が確認された。

監査の結果、原因はどこか1箇所の「桁を書き換える」バグではなく、
customer_phone/customer_nameの妥当性（桁数チェック等）をエンドポイント側では
一切検証せず、AI側の会話ルール（1桁ずつ復唱確認する等）だけに委ねる、という
既存の設計方針（app/routers/realtime_voice.pyのrequest_callback_tool()の
docstringに明記されていた）そのものにあった。つまりpromptが正しく機能して
いる限りは防げるが、モデルが誤認識した場合にそれを止める決定論的な仕組みが
どこにも無かった。

今回の対応は2層:
  (1) プロンプト層: _PHONE_BLOCK_FORMAT_TEMPLATEに、復唱する前に桁数
      （10桁または11桁）を確認し、それ以外なら推測で直さずもう一度尋ね直す、
      という指示を追加。
  (2) tool boundary層: request_callback_tool()エンドポイントで、
      Customer Memory機能が既に持っているnormalize_jp_phone_national()
      （0始まり10〜11桁 / 81・+81始まりを0始まりへ変換した上で同条件、
      それ以外はNone）を再利用し、Noneの場合はDB保存せずsuccess=False,
      reason_code="phone_invalid_digit_count"を返す。新しい判定ロジックは
      重複して作っていない。

実行: python3 tests/smoke_test_phone_length_guard.py
"""
import os
import re
import sys
import uuid
import tempfile
import asyncio

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "phone_length_guard.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-phone-length-guard"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx


def _flatten(text):
    return re.sub(r"\s+", "", text)


def _test_normalize_jp_phone_national_matrix():
    """既存の単一の判定関数（normalize_jp_phone_national）が、今回の
    必須回帰ケースすべてに対して正しくACCEPT/REJECTを返すことを確認する。
    新しい判定ロジックを重複して作らず、既存関数をそのまま検証する。"""
    from app.schemas.shop import normalize_jp_phone_national

    accept_cases = [
        ("08039644468", "11桁"),
        ("09012345678", "11桁"),
        ("0981234567", "10桁"),
        ("080-3964-4468", "ハイフンあり・digits=08039644468・11桁"),
    ]
    reject_cases = [
        ("080396444468", "12桁"),
        ("090123456789", "12桁"),
        ("123456789", "9桁"),
    ]

    for raw, desc in accept_cases:
        result = normalize_jp_phone_national(raw)
        assert result is not None, f"ACCEPTされるべき番号がREJECTされました: {raw} ({desc})"

    for raw, desc in reject_cases:
        result = normalize_jp_phone_national(raw)
        assert result is None, f"REJECTされるべき番号がACCEPTされました: {raw} ({desc}) -> {result}"

    print("A. normalize_jp_phone_national()は必須回帰ケース（10桁/11桁ACCEPT、"
          "9桁/12桁REJECT、ハイフンあり11桁ACCEPT）すべてで正しく判定する: OK")


async def _setup_shop():
    from app.main import app, lifespan

    cm = lifespan(app)
    await cm.__aenter__()
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")

    r = await client.post("/api/v1/auth/register", json={
        "email": "owner-phone-length-guard-test@example.com",
        "password": "password123",
        "display_name": "電話番号桁数ガードテストオーナー",
    })
    assert r.status_code in (200, 201), r.text
    owner_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = await client.post("/api/v1/shops/register", json={
        "name": "電話番号桁数ガードテスト店", "category": "レストラン",
        "address": "東京都渋谷区9-9-9",
    }, headers=owner_headers)
    assert r.status_code in (200, 201), r.text
    shop_id = r.json()["shop_id"]
    return client, cm, shop_id


async def _call_request_callback(client, shop_id, customer_phone):
    call_id = "smoke-test-phone-length-guard-" + str(uuid.uuid4())
    r = await client.post(
        f"/api/v1/shops/{shop_id}/realtime-voice/tools/request-callback",
        json={
            "customer_name": "谷村あきら",
            "customer_phone": customer_phone,
            "inquiry_text": "電話番号桁数ガードの回帰テスト用の折り返し依頼。",
            "reason_code": "other",
            "call_id": call_id,
        },
    )
    assert r.status_code == 200, f"request-callback failed: {r.status_code} {r.text}"
    return r.json(), call_id


async def _run_endpoint_cases():
    client, cm, shop_id = await _setup_shop()
    try:
        from app.database import AsyncSessionLocal
        from app.models.callback_request import CallbackRequest
        from sqlalchemy import select

        # ACCEPT: 11桁（ハイフン無し）。success=True・DBへ保存され、
        # customer_phoneは渡した値のまま（新しい正規化処理を加えていない）。
        body, call_id = await _call_request_callback(client, shop_id, "08039644468")
        assert body["success"] is True and body.get("reason_code") in (None, "other"), (
            f"11桁の正しい番号がACCEPTされませんでした: {body}"
        )
        async with AsyncSessionLocal() as session:
            idem = f"realtime_voice:{shop_id}:{call_id}"
            row = (await session.execute(
                select(CallbackRequest).where(CallbackRequest.idempotency_key == idem)
            )).scalar_one_or_none()
            assert row is not None, "ACCEPTされたはずの11桁番号のCallbackRequestがDBに保存されていません"
            assert row.customer_phone == "08039644468", (
                f"DB保存されたcustomer_phoneが渡した値と異なります（新しい正規化が"
                f"紛れ込んだ可能性）: {row.customer_phone!r}"
            )
        print("B1. 11桁（ハイフン無し, 08039644468）はACCEPTされ、渡した値のまま"
              "DBへ保存される（新しい正規化処理の混入なし）: OK")

        # ACCEPT: ハイフンあり11桁。DB保存値は「ハイフンを含む渡された生の文字列
        # そのまま」であること（既存のDB保存形式を一切変更していないことの確認）。
        body, call_id = await _call_request_callback(client, shop_id, "080-3964-4468")
        assert body["success"] is True, f"ハイフンあり11桁番号がACCEPTされませんでした: {body}"
        async with AsyncSessionLocal() as session:
            idem = f"realtime_voice:{shop_id}:{call_id}"
            row = (await session.execute(
                select(CallbackRequest).where(CallbackRequest.idempotency_key == idem)
            )).scalar_one_or_none()
            assert row is not None
            assert row.customer_phone == "080-3964-4468", (
                f"ハイフンありの入力がDB保存時に書き換えられています（保存形式は"
                f"変更しない方針のはず）: {row.customer_phone!r}"
            )
        print("B2. ハイフンあり11桁（080-3964-4468, digits=08039644468）はACCEPTされ、"
              "DBにはハイフンを含む元の文字列がそのまま保存される（保存形式無変更）: OK")

        # ACCEPT: 10桁。
        body, call_id = await _call_request_callback(client, shop_id, "0981234567")
        assert body["success"] is True, f"10桁の正しい番号がACCEPTされませんでした: {body}"
        print("B3. 10桁（0981234567）はACCEPTされる: OK")

        # REJECT: 実機不具合そのもの（本来11桁のはずが1桁多い12桁）。
        # success=False・reason_code=phone_invalid_digit_count・DB未保存・
        # 桁の自動修正（例えば末尾を削って11桁に丸める等）が行われていないこと。
        body, call_id = await _call_request_callback(client, shop_id, "080396444468")
        assert body["success"] is False, (
            f"12桁の不正な番号がACCEPTされてしまいました（実機不具合の再現）: {body}"
        )
        assert body.get("reason_code") == "phone_invalid_digit_count", (
            f"12桁REJECT時のreason_codeが想定と異なります: {body}"
        )
        async with AsyncSessionLocal() as session:
            idem = f"realtime_voice:{shop_id}:{call_id}"
            row = (await session.execute(
                select(CallbackRequest).where(CallbackRequest.idempotency_key == idem)
            )).scalar_one_or_none()
            assert row is None, (
                "REJECTされたはずの12桁番号のCallbackRequestがDBに保存されています"
                "（無効な番号がそのまま確定してしまっている）"
            )
        print("C1. 12桁（080396444468, 実機不具合の再現ケース）はREJECTされ、"
              "success=False・reason_code=phone_invalid_digit_count・DB未保存"
              "（勝手な桁の修正も行われない）: OK")

        # REJECT: 12桁（別パターン）。
        body, call_id = await _call_request_callback(client, shop_id, "090123456789")
        assert body["success"] is False and body.get("reason_code") == "phone_invalid_digit_count"
        print("C2. 12桁（090123456789）もREJECTされる: OK")

        # REJECT: 9桁。
        body, call_id = await _call_request_callback(client, shop_id, "123456789")
        assert body["success"] is False and body.get("reason_code") == "phone_invalid_digit_count"
        print("C3. 9桁（123456789）もREJECTされる: OK")

    finally:
        await client.aclose()
        await cm.__aexit__(None, None, None)


def _test_endpoint_accepts_and_rejects():
    asyncio.run(_run_endpoint_cases())


def _test_prompt_level_pre_readback_check():
    """復唱する「前」に桁数を確認する、という指示が_PHONE_BLOCK_FORMAT_TEMPLATE
    （既存のsingle source of truthをそのまま拡張。新しい別テンプレートを
    重複して作っていない）に含まれていることを確認する。"""
    from app.services.realtime_voice_ai import _PHONE_BLOCK_FORMAT_TEMPLATE

    flat = _flatten(_PHONE_BLOCK_FORMAT_TEMPLATE)
    required = [
        "先頭の0を含めて10桁または11桁です",
        "復唱する前に",
        "10桁・11桁のどちらでもない場合",
        "桁を推測で足したり削ったりして辻褄を合わせることも、絶対にしないでください",
        "お電話番号をもう一度お願いします",
    ]
    for phrase in required:
        assert _flatten(phrase) in flat, f"必須文言が見つかりません: {phrase}"

    # これまでの3-4-4・ブロック内連続読みの仕様が維持されていることも再確認する
    # （今回のテンポ調整を元に戻していないことの回帰確認）。
    assert _flatten("先頭3桁・次の4桁・最後の4桁") in flat
    assert _flatten("同じブロックの中にある数字と数字の間には、意図的な間を置かないでください") in flat

    print("D. _PHONE_BLOCK_FORMAT_TEMPLATEに、復唱前の桁数確認（10桁/11桁でなければ"
          "推測で直さずもう一度尋ね直す）が追加され、既存の3-4-4・ブロック内連続読みの"
          "テンポ仕様は維持されている: OK")


def _test_tool_description_explains_new_reason_code():
    """request_callback Toolの説明文に、reason_code=phone_invalid_digit_countの
    場合の振る舞い（システムエラー扱いしない、桁を自分で直さない、お客様へ番号を
    もう一度尋ね直す）が明記されていることを確認する。"""
    from app.services.realtime_voice_ai import _REALTIME_TOOLS

    request_callback_tool = next(t for t in _REALTIME_TOOLS if t["name"] == "request_callback")
    desc = request_callback_tool["description"]
    flat = _flatten(desc)

    assert _flatten("reason_code=phone_invalid_digit_count") in flat
    assert _flatten("桁を自分で足したり") in flat
    assert _flatten("お電話番号をもう一度お願いします") in flat
    # 既存のphone_not_confirmedの特別扱いの説明が壊れていないことも確認する。
    assert _flatten("reason_code=phone_not_confirmed") in flat

    print("E. request_callback Toolの説明文にreason_code=phone_invalid_digit_countの"
          "専用ハンドリング（システムエラー扱いしない・桁を自分で直さない・"
          "お客様へ再度尋ね直す）が明記されており、既存のphone_not_confirmed"
          "の説明も維持されている: OK")


def _test_no_pii_in_new_log_statement():
    """新しく追加したガードのログ出力が、生の電話番号そのものを含んでいない
    （桁数などPII-freeなメタデータのみ）ことをソースコードで確認する。"""
    router_src = open(
        os.path.join(os.path.dirname(__file__), "..", "app", "routers", "realtime_voice.py"),
        encoding="utf-8",
    ).read()

    marker = "妥当な桁数(10桁または11桁)ではないため拒否"
    idx = router_src.find(marker)
    assert idx != -1, "今回追加したログメッセージが見つかりません"
    # ログ呼び出し全体（logger.warning( ... ) ブロック）を抜き出して検証する。
    call_start = router_src.rfind("logger.warning(", 0, idx)
    call_end = router_src.find(")\n", idx)
    log_call = router_src[call_start:call_end]

    # customer_phoneというフィールド名自体（メッセージ文中の日本語説明や、
    # 桁数を計算するためのlen(re.sub(...))の中）が触れられることは問題ない。
    # PIIとして問題なのは、生の電話番号の「値」がログ引数としてそのまま
    # 渡されること。ログ呼び出しの引数リスト（フォーマット文字列の後）に、
    # request.customer_phoneが素の値として（len()等で加工されずに）渡されて
    # いないことを確認する。
    assert "request.customer_phone," not in log_call, (
        "customer_phoneの値が加工されずそのままログ引数として渡されています"
        "（PII漏洩の可能性）: " + log_call
    )
    assert "len(re.sub(" in log_call and "request.customer_phone" in log_call, (
        "桁数計算(len(re.sub(...)))の中でのみcustomer_phoneを参照している、"
        "という想定と異なる形になっています: " + log_call
    )
    assert "%d" in log_call, "桁数(digit_length相当の%d)がログに含まれていません"
    assert "shop_id" in log_call, "shop_idがログに含まれていません"

    print("F. 新しく追加したログ出力は、生の電話番号(customer_phone)を含まず、"
          "shop_id・digit_length等のPII-freeなメタデータのみを記録している: OK")


def _test_phone_validation_pydantic_schema_unchanged():
    """RequestCallbackToolRequest.customer_phoneのPydantic制約（min_length=1,
    max_length=20、形式検証なし）自体は今回も変更していないことを確認する
    （桁数の妥当性チェックはPydanticのFieldレベルではなく、エンドポイント内の
    決定論的ガードとして追加したため）。"""
    from app.schemas.reservation import RequestCallbackToolRequest
    fields = RequestCallbackToolRequest.model_fields
    phone_field = fields["customer_phone"]
    assert phone_field.annotation is str
    constraints = {type(m).__name__: m for m in phone_field.metadata}
    min_len = getattr(constraints.get("MinLen"), "min_length", None)
    max_len = getattr(constraints.get("MaxLen"), "max_length", None)
    assert min_len == 1, f"customer_phoneのmin_lengthが変化しています: {min_len}"
    assert max_len == 20, f"customer_phoneのmax_lengthが変化しています: {max_len}"
    print("G. RequestCallbackToolRequest.customer_phoneのPydantic制約"
          "（min_length=1, max_length=20、形式検証なし）自体は無変更"
          "（桁数チェックはFieldレベルではなくエンドポイント内の決定論的ガード）: OK")


if __name__ == "__main__":
    _test_normalize_jp_phone_national_matrix()
    _test_endpoint_accepts_and_rejects()
    _test_prompt_level_pre_readback_check()
    _test_tool_description_explains_new_reason_code()
    _test_no_pii_in_new_log_statement()
    _test_phone_validation_pydantic_schema_unchanged()
    print("\n=== ALL smoke_test_phone_length_guard.py CHECKS PASSED ===")
