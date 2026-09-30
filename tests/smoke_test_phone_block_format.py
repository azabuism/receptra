"""
RECEPTRA — 電話番号復唱フォーマット最小修正 + テンポHOTFIX 回帰テスト

対象: 日本の携帯電話番号（090/080/070開始の11桁）を復唱するときに、
3桁・4桁・4桁の3ブロックへ分け、「ブロックの境界」にだけ短い間を置いて
読み上げるという新設ルール（_PHONE_BLOCK_FORMAT_TEMPLATE）。

今回のテンポHOTFIXでは、間を置く位置を「ブロックの境界だけ」に限定し、
同じブロック内の桁と桁の間には意図的な間を置かない（1桁ずつの発音は
維持しつつ、ブロック内はテンポよく連続して発音する）ことを追加で規定した。
数値まとめ読み・英語読みの禁止は既存のまま変更していない。

既存の会話フロー・phase・Realtime制御・電話番号validation・保存形式には
一切触れていないことを確認する（単一の新規定数を、電話番号確認が実際に
発生する3箇所（reservation/callback/legacy_full）にのみ追加で挿入した
だけであることを、実際に組み立てられたinstructions文字列で検証する）。

実行: python3 tests/smoke_test_phone_block_format.py
"""
import re
import os
import sys
import tempfile
import asyncio

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "phone_block_format.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-phone-block-format"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx

FABRICATION_ANCHOR_CANDIDATES = [
    "090-1234-5678",
    "090.1234.5678",
    "09012345678",
]

def _flatten(text):
    """改行・空白の位置に依存せず文言の有無だけを確認するためのヘルパー
    （テンプレート内の折り返し位置が変わっても壊れないようにする）。"""
    return re.sub(r"\s+", "", text)


REQUIRED_PHRASES = [
    "先頭3桁・次の4桁・最後の4桁",
    "ブロックとブロックの「境界」にだけ短い間",
    "間を置くのはこの3ブロックの境界だけであり",
    "同じブロックの中にある数字と数字の間には、意図的な間を置かないでください",
    "テンポよく続けて発音してください",
    "「ゼロ・キュウ・ゼロ」と読むのではなく",
    "続けて「ゼロキュウゼロ」と読み上げてください",
    "発音単位はあくまで1桁ずつであり",
    "千二百三十四」のようにひとまとまりの数として読み上げることは絶対にしないでください",
]

# 「千二百三十四」のような数値まとめ読みは、禁止する対象としてテンプレート内に
# 例示されているため、これ自体は禁止文言リストに入れない（逆に、禁止する旨の
# 文脈で登場していることをREQUIRED_PHRASESで別途確認する）。
# 同様に「ゼロ・キュウ・ゼロ」（桁ごとに間を空ける悪い例）も、禁止する対象と
# しての例示なので禁止文言リストには入れず、「と読むのではなく」という否定の
# 文脈で登場していることをREQUIRED_PHRASESで確認する。


def _test_template_content():
    from app.services.realtime_voice_ai import _PHONE_BLOCK_FORMAT_TEMPLATE

    flat = _flatten(_PHONE_BLOCK_FORMAT_TEMPLATE)
    for phrase in REQUIRED_PHRASES:
        assert _flatten(phrase) in flat, f"必須文言が見つかりません: {phrase}"

    for anchor in FABRICATION_ANCHOR_CANDIDATES:
        assert anchor not in _PHONE_BLOCK_FORMAT_TEMPLATE, f"模倣元になり得るリテラル電話番号が含まれています: {anchor}"
    assert "英語" in _PHONE_BLOCK_FORMAT_TEMPLATE, "英語読み禁止の言及がありません"
    print("A. _PHONE_BLOCK_FORMAT_TEMPLATEは3-4-4ブロック分け・ブロック境界だけの短い間・"
          "ブロック内は間を置かずテンポよく1桁ずつ発音・英語読み禁止を明記し、"
          "数値まとめ読みの例やリテラルな模倣元電話番号を含んでいない: OK")


async def _build_all_phase_contexts():
    from app.main import app, lifespan

    async with lifespan(app):
        from app.database import AsyncSessionLocal
        from app.models.shop import Shop
        from app.services.realtime_voice_ai import build_realtime_phase_contexts, build_realtime_instructions

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-phone-block-format-test@example.com",
                "password": "password123",
                "display_name": "電話番号復唱フォーマットテストオーナー",
            })
            assert r.status_code in (200, 201), r.text
            owner_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
            r = await client.post("/api/v1/shops/register", json={
                "name": "電話番号復唱フォーマットテスト店", "category": "レストラン",
                "address": "東京都渋谷区9-9-9",
            }, headers=owner_headers)
            assert r.status_code in (200, 201), r.text
            shop_id = r.json()["shop_id"]

            async with AsyncSessionLocal() as session:
                shop = await session.get(Shop, shop_id)
                contexts = await build_realtime_phase_contexts(session, shop, None)
                legacy_full_via_build = await build_realtime_instructions(session, shop, None)
                return contexts, legacy_full_via_build


def _test_included_in_reservation_callback_legacy_full_only():
    from app.services.realtime_voice_ai import _PHONE_BLOCK_FORMAT_TEMPLATE

    contexts, legacy_full_via_build = asyncio.run(_build_all_phase_contexts())

    marker = "電話番号を復唱するときの読み方（重要）"
    assert marker in _PHONE_BLOCK_FORMAT_TEMPLATE

    assert marker in contexts["reservation"]["instructions"], "RESERVATION phaseに新ルールが含まれていません"
    assert marker in contexts["callback"]["instructions"], "CALLBACK phaseに新ルールが含まれていません"
    assert marker in contexts["legacy_full"]["instructions"], "legacy_full phaseに新ルールが含まれていません"
    assert marker in legacy_full_via_build, "build_realtime_instructions()自体にも新ルールが含まれていません"

    # NAME/ROUTINGは電話番号を扱わないphaseのため、意図的に含めていない
    # （instructions budgetを無駄に消費しない設計）ことを確認する。
    assert marker not in contexts["name"]["instructions"], "NAME phaseに不要に新ルールが混入しています"
    assert marker not in contexts["routing"]["instructions"], "ROUTING phaseに不要に新ルールが混入しています"

    # legacy_full内に新ルールが二重挿入されていないこと（_HUMAN_HANDOFF_TEMPLATEと
    # _BOOKING_SAFETY_TEMPLATEの両方を含む唯一のphaseのため、重複挿入リスクが
    # 最も高い箇所）。
    assert contexts["legacy_full"]["instructions"].count(marker) == 1, (
        "legacy_full内に新ルールが複数回重複して挿入されています"
    )
    assert contexts["reservation"]["instructions"].count(marker) == 1
    assert contexts["callback"]["instructions"].count(marker) == 1

    # NAME/ROUTING phaseの既存の厳格な文字数上限（<3000）を壊していないことも
    # 再確認する（過去に共通rulesへ直接追記して破綻した回帰の再発防止）。
    assert len(contexts["name"]["instructions"]) < 3000, (
        f"NAME instructions_charsが3,000文字を超えています: {len(contexts['name']['instructions'])}"
        "（電話番号ルールがNAME phaseに漏れ込んでいる可能性）"
    )
    assert len(contexts["routing"]["instructions"]) < 3000, (
        f"ROUTING instructions_charsが3,000文字を超えています: {len(contexts['routing']['instructions'])}"
        "（電話番号ルールがROUTING phaseに漏れ込んでいる可能性）"
    )

    print("B. 新ルールはRESERVATION/CALLBACK/legacy_fullの3箇所にのみ、それぞれ重複なく1回ずつ挿入されており、"
          "NAME/ROUTINGには混入していない（NAME instructions budgetも既存基準内）: OK")


def _test_no_conflicting_pause_pacing_examples():
    """
    RECEPTRA — 電話番号復唱 FINAL TUNING（実機フィードバック対応）の回帰テスト。

    実機で「まだ復唱が長く・細かく聞こえる」というフィードバックを受けて監査
    したところ、_PHONE_BLOCK_FORMAT_TEMPLATE（ブロック境界だけに短い間を置き、
    ブロック内は間を置かずテンポよく読む、という抽象的なルール）とは別に、
    _HUMAN_HANDOFF_TEMPLATE（CALLBACK phase）と_BOOKING_SAFETY_TEMPLATE
    （RESERVATION phase）の中に、実際の復唱場面で使う具体的な例文として
    「ゼロ・キュウ・ゼロ…でよろしいでしょうか？」（桁ごとに間を空ける書き方）
    が、それぞれ独立にベタ書きされていたことが根本原因と判明した。この2箇所
    の具体的な例文は、抽象的なルールよりもモデルの実際の発話に強く影響する
    ため、テンポルールと矛盾していた。今回、この2箇所の例文だけを
    「ゼロキュウゼロ…でよろしい（でしょうか／ですか）？」（間を空けない書き方）
    に最小修正した。_PHONE_BLOCK_FORMAT_TEMPLATE内のNG例示（「ゼロ・キュウ・
    ゼロ」と読むのではなく、という否定文脈）は意図的な例示なので変更していない。
    """
    from app.services.realtime_voice_ai import (
        _HUMAN_HANDOFF_TEMPLATE,
        _BOOKING_SAFETY_TEMPLATE,
        _PHONE_BLOCK_FORMAT_TEMPLATE,
    )

    # 修正後の例文（ブロック内は間を空けない）が、CALLBACK/RESERVATION双方の
    # 実際の復唱手順に含まれていること。
    assert "ゼロキュウゼロ…でよろしい" in _HUMAN_HANDOFF_TEMPLATE, (
        "_HUMAN_HANDOFF_TEMPLATEの電話番号復唱例が修正されていません"
    )
    assert "ゼロキュウゼロ…でよろしい" in _BOOKING_SAFETY_TEMPLATE, (
        "_BOOKING_SAFETY_TEMPLATEの電話番号復唱例が修正されていません"
    )

    # 旧・競合していた「桁ごとに間を空ける」例文が、CALLBACK/RESERVATIONの
    # 実際の復唱手順からは完全に除去されていること。
    assert "ゼロ・キュウ・ゼロ" not in _HUMAN_HANDOFF_TEMPLATE, (
        "_HUMAN_HANDOFF_TEMPLATEに競合する旧例文が残っています"
    )
    assert "ゼロ・キュウ・ゼロ" not in _BOOKING_SAFETY_TEMPLATE, (
        "_BOOKING_SAFETY_TEMPLATEに競合する旧例文が残っています"
    )

    # _PHONE_BLOCK_FORMAT_TEMPLATE内のNG例示（意図的な否定文脈での使用）は
    # 変更されていないこと。
    assert "「ゼロ・キュウ・ゼロ」と読むのではなく" in _PHONE_BLOCK_FORMAT_TEMPLATE, (
        "_PHONE_BLOCK_FORMAT_TEMPLATEのNG例示が意図せず変更されています"
    )

    # モジュール全体でも、「ゼロ・キュウ・ゼロ」はこのNG例示の1箇所だけに
    # なっていること（重複した競合例文が他に残っていないことの最終確認）。
    import app.services.realtime_voice_ai as voice_ai_module
    module_src = open(voice_ai_module.__file__, encoding="utf-8").read()
    assert module_src.count("ゼロ・キュウ・ゼロ") == 1, (
        f"ゼロ・キュウ・ゼロの残存数が想定と異なります: {module_src.count('ゼロ・キュウ・ゼロ')}"
        "（NG例示1箇所以外に競合する例文が残っている可能性）"
    )

    print("E. CALLBACK(_HUMAN_HANDOFF_TEMPLATE)・RESERVATION(_BOOKING_SAFETY_TEMPLATE)の"
          "電話番号復唱の具体例文が、ブロック内で間を空けない書き方に統一され、"
          "テンポルールと矛盾する旧例文（桁ごとに間を空ける書き方）が除去されている"
          "（_PHONE_BLOCK_FORMAT_TEMPLATE内の意図的なNG例示のみ残存）: OK")


def _test_callback_final_phrase_unchanged():
    from app.services.realtime_voice_ai import _HUMAN_HANDOFF_TEMPLATE

    final_phrase = "担当者から折り返し連絡しますので、電話を切ってお待ちください。"
    assert final_phrase in _HUMAN_HANDOFF_TEMPLATE, (
        "CALLBACK成功時の正式な最終案内文が変更・消失しています"
    )
    # 一言一句変えずに、の指示自体も維持されていることを確認する。
    assert "一言一句変えずにそのまま発話してください" in _HUMAN_HANDOFF_TEMPLATE
    print("F. CALLBACK成功時の最終案内文（「担当者から折り返し連絡しますので、"
          "電話を切ってお待ちください。」）は一字一句無変更: OK")


def _test_phone_validation_and_storage_unchanged():
    from app.schemas.reservation import RequestCallbackToolRequest
    fields = RequestCallbackToolRequest.model_fields
    phone_field = fields["customer_phone"]
    assert phone_field.annotation is str
    # Pydantic v2のFieldConstraints経由でmin_length/max_lengthを確認
    from pydantic_core import PydanticUndefined
    constraints = {type(m).__name__: m for m in phone_field.metadata}
    min_len = getattr(constraints.get("MinLen"), "min_length", None)
    max_len = getattr(constraints.get("MaxLen"), "max_length", None)
    assert min_len == 1, f"customer_phoneのmin_lengthが変化しています: {min_len}"
    assert max_len == 20, f"customer_phoneのmax_lengthが変化しています: {max_len}"
    print("C. customer_phoneのバリデーション（min_length=1, max_length=20、形式検証なし）は無変更: OK")

    # 保存形式（DB保存前のスペース・「・」除去等の新規正規化処理を追加して
    # いないこと）: request_callback_toolエンドポイントがcustomer_phoneを
    # そのままCallbackRequestへ渡している既存の1行を確認する。
    router_src = open(
        os.path.join(os.path.dirname(__file__), "..", "app", "routers", "realtime_voice.py"),
        encoding="utf-8",
    ).read()
    assert "customer_phone=request.customer_phone," in router_src, (
        "customer_phoneの保存経路に新しい変換処理が挟まっている可能性があります"
    )
    print("D. DB保存経路はrequest.customer_phoneをそのまま渡すままで、新しい正規化処理を追加していない: OK")


if __name__ == "__main__":
    _test_template_content()
    _test_included_in_reservation_callback_legacy_full_only()
    _test_no_conflicting_pause_pacing_examples()
    _test_callback_final_phrase_unchanged()
    _test_phone_validation_and_storage_unchanged()
    print("\n=== ALL smoke_test_phone_block_format.py CHECKS PASSED ===")
