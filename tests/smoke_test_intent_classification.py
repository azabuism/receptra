"""
Conversation Opening / Intent Classification（通話冒頭のご用件把握）スモークテスト

検証項目:
1. _INTENT_CLASSIFICATION_TEMPLATE の内容チェック（純粋な文字列検証）:
   - RESERVATION/CALLBACK・HUMAN_HANDOFF/QUESTION・INFORMATION/OTHER・UNCLEAR
     の4分類が含まれていること。
   - 「5秒」はあくまで目安であり、待機時間ではないという注記が含まれて
     いること（PHASE TELEPHONE GUIDANCE UPDATEで20秒から短縮）。
   - input_audio_buffer.commit / response.create のようなプロトコルレベルの
     文言が一切含まれていないこと（クライアント側の強制打ち切りを一切
     指示していないことの確認。谷村様の明示的な禁止事項）。
   - 既存Tool名（request_callback / get_shop_info）への言及があり、新しい
     Tool名を作り出していないこと。
2. _REALTIME_TOOLS が引き続き8個・既存の名前のまま変わっていないこと
   （9個目のToolを追加していないことの回帰確認）。
3. build_realtime_instructions() が組み立てるinstructions全体で、
   Conversation Opening / Intent ClassificationセクションがScopeの直後・
   Fast Reservation Flowの直前に挿入されており、既存の全セクション
   （Core Rules/Scope/Fast Reservation Flow/Examples/Shop Info/Constraints/
   Human Handoff/Shop Knowledge Rules/Customer Memory/Customer Context/
   Time Ambiguity/Booking Safety）の出現順序が壊れていないこと。
   Booking Safetyが常に最後という既存の不変条件も維持されていること。
4. staff_settingsが存在しない店舗（Phase1相当）でも、Intent Classification
   セクションが変わらず挿入されること（既存店舗への影響ゼロという方針の
   延長で、新セクションも常に固定で挿入されることの確認）。
5. ask_visit_reason_enabled=trueの店舗で、Visit Reasonセクションが
   Time Ambiguityの直後・Booking Safetyの直前という既存の位置関係を
   維持したままであること（Intent Classification追加がこの既存の
   挿入順を壊していないことの回帰確認）。
6. POST /api/v1/shops/{shop_id}/realtime-voice/session エンドポイントを
   実際に呼び出し、OpenAIへ送信されるsession_config（instructions/tools）に
   新セクションと既存8 Toolがそのまま含まれていること（OpenAI Realtime API
   は実際には呼び出さず、AsyncOpenAIクライアントをモックして決定的に検証する。
   ネットワーク・課金なし）。

注意: このテストはinstructionsという「自然文プロンプト」の内容を検証する
ものであり、AIが実際にその通りに振る舞うかどうかはRealtime API相手の
実機テストでのみ確認できる（他のNAME Forced Commit Observation PoC同様、
実機での挙動確認は別途行う）。
"""

import asyncio
import os
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_intent_classification.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-intent-classification"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402


def _test_template_content():
    from app.services.realtime_voice_ai import _INTENT_CLASSIFICATION_TEMPLATE, _REALTIME_TOOLS

    tmpl = _INTENT_CLASSIFICATION_TEMPLATE

    # 1a. 4分類が全て含まれていること
    assert "RESERVATION" in tmpl, "RESERVATION分類が見つかりません"
    assert "CALLBACK" in tmpl and "HUMAN_HANDOFF" in tmpl, "CALLBACK/HUMAN_HANDOFF分類が見つかりません"
    assert "QUESTION" in tmpl and "INFORMATION" in tmpl, "QUESTION/INFORMATION分類が見つかりません"
    assert "OTHER" in tmpl and "UNCLEAR" in tmpl, "OTHER/UNCLEAR分類が見つかりません"

    # 1b. 「5秒」はあくまで目安であり、待機時間ではないことの明記
    # （PHASE TELEPHONE GUIDANCE UPDATEで20秒から5秒へ短縮。20秒という
    # 具体的な数値への言及自体はテンプレートから除去されている想定）。
    assert "5秒" in tmpl, "5秒への言及が見つかりません"
    assert "待機時間ではありません" in tmpl or "待たなければならない" in tmpl, (
        "5秒が待機時間ではないという注記が見つかりません"
    )
    # 早く分かった場合はすぐ動く、という具体例が含まれていること
    assert "2秒" in tmpl or "3秒" in tmpl, "早期確定時にすぐ動く旨の具体例が見つかりません"

    # 1c. プロトコルレベルの文言（クライアント側の強制介入）が一切含まれて
    # いないこと。これらはAI向けinstructionsとして意味を持たないだけでなく、
    # 「5秒でこれらを送信する」という誤読を絶対に招いてはならない。
    forbidden_terms = [
        "input_audio_buffer.commit",
        "response.create",
        "commit(",
        "音声トラックを停止",
        "audio track",
    ]
    for term in forbidden_terms:
        assert term not in tmpl, f"禁止されているプロトコル/クライアント制御文言が含まれています: {term}"

    # 1d. 既存Tool名への言及があり、新しいTool名を作っていないこと
    assert "request_callback" in tmpl, "既存のrequest_callbackツールへの言及が見つかりません"
    assert "get_shop_info" in tmpl, "既存のget_shop_infoツールへの言及が見つかりません"
    forbidden_new_tool_names = [
        "classify_intent", "detect_intent", "set_intent", "record_intent", "intent_classification",
    ]
    for name in forbidden_new_tool_names:
        assert name not in tmpl, f"新しいTool名らしき文言が含まれています（9個目のTool追加禁止）: {name}"

    # 1e. 来店理由とIntentを混同しないという注記
    assert "来店理由" in tmpl, "来店理由とIntentを混同しない旨の注記が見つかりません"

    # 2. 既存8 Toolがそのまま・9個目が追加されていないこと
    tool_names = [t["name"] for t in _REALTIME_TOOLS]
    assert tool_names == [
        "check_availability",
        "create_reservation",
        "get_shop_info",
        "find_customer",
        "confirm_customer_identity",
        "get_customer_context",
        "set_conversation_language",
        "request_callback",
    ], f"既存8 Toolの名前・順序が変わっています（9個目のTool追加は禁止）: {tool_names}"
    assert len(_REALTIME_TOOLS) == 8, f"Toolの数が8個ではありません: {len(_REALTIME_TOOLS)}"

    print("1-2. _INTENT_CLASSIFICATION_TEMPLATE内容・_REALTIME_TOOLS回帰: OK")


def _assert_section_order(instructions: str, *, expect_visit_reason: bool):
    """
    build_realtime_instructions()が組み立てたinstructions全体の中で、
    各セクションの出現順序が既存の不変条件・新セクション挿入位置の両方を
    満たしていることを検証する。
    """
    markers = {
        "core_rules": "話し方の絶対ルール",
        "scope": "会話範囲のルール",
        "intent_classification": "通話冒頭のご用件把握",
        # 同様に、Intent Classificationセクション内の前方参照文
        # （「このすぐ後の『Fast Reservation Flow』セクションの進め方に従って
        # ください」）と誤って一致しないよう、実際の見出し行の全文で一意に指定する。
        "fast_reservation_flow": "予約受付を短時間で終える「Fast Reservation Flow」の絶対ルール",
        "examples": "話し方の見本",
        "constraints": "現時点での制約",
        "human_handoff": "折り返し対応（Human Handoff）に関する絶対ルール",
        # Intent Classificationセクション内の前方参照文（「この後の『店舗情報の質問への
        # 回答ルール』セクションの進め方に従って回答してください」）と誤って一致しない
        # よう、実際の見出し行にしか現れない末尾の定型句まで含めて一意に指定する。
        "shop_knowledge_rules": "店舗情報の質問への回答ルール（重要・必ず守ってください）",
        "customer_memory": "常連のお客様の認識に関するルール",
        "customer_context": "本人確認後のご利用情報",
        "time_ambiguity": "時刻の午前/午後（AM/PM）解釈ルール",
        # _TIME_AMBIGUITY_TEMPLATE自身の本文が「後述の『予約成立の宣言に関する
        # 絶対ルール』で必ず行う」と前方参照するため、実際の見出し行にしか
        # 現れない末尾の定型句（最重要・必ず守ってください）まで含めて一意に指定する。
        "booking_safety": "予約成立の宣言に関する絶対ルール（最重要・必ず守ってください）",
    }
    positions = {}
    for key, marker in markers.items():
        idx = instructions.find(marker)
        assert idx != -1, f"期待したセクションがinstructionsに見つかりません: {key} ({marker!r})"
        positions[key] = idx

    ordered_keys = [
        "core_rules", "scope", "intent_classification", "fast_reservation_flow",
        "examples", "constraints", "human_handoff", "shop_knowledge_rules",
        "customer_memory", "customer_context", "time_ambiguity", "booking_safety",
    ]
    for a, b in zip(ordered_keys, ordered_keys[1:]):
        assert positions[a] < positions[b], (
            f"セクション順序が崩れています: {a}({positions[a]}) が {b}({positions[b]}) より後ろにあります"
        )

    # Booking Safetyは常に最後という既存の不変条件。
    # 注意: _BOOKING_SAFETY_TEMPLATE自身の本文が「上の『話し方の見本』の
    # 電話番号読み上げの例と同じテンポです」のように他セクション名へ
    # 本文中で言及することがある（この挙動は今回の変更以前から存在する）ため、
    # 単純な「他マーカーの部分文字列が再出現しないこと」ではなく、
    # 「見出し位置としてはbooking_safetyが最大（＝最後）であること」で判定する。
    assert positions["booking_safety"] == max(positions.values()), (
        f"Booking Safetyが最後のセクションではありません: {positions}"
    )

    # 注意: Intent Classificationセクション自身が「来店理由の確認については、
    # この後の該当セクションの案内に従ってください」と前方参照するため、
    # 「来店理由の確認について」という部分文字列だけでは実際のVisit Reason
    # 見出し（「来店理由の確認について（この店舗ではオーナー設定により
    # ONになっています。重要・必ず守ってください）」）と区別できない。
    # 実際の見出し行にしか現れないオーナー設定への言及まで含めて判定する。
    visit_reason_heading = "来店理由の確認について（この店舗ではオーナー設定によりONになっています"
    if expect_visit_reason:
        visit_reason_idx = instructions.find(visit_reason_heading)
        assert visit_reason_idx != -1, "ask_visit_reason_enabled=trueなのにVisit Reasonセクションが見つかりません"
        assert positions["time_ambiguity"] < visit_reason_idx < positions["booking_safety"], (
            "Visit ReasonセクションがTime Ambiguityの直後・Booking Safetyの直前という既存の位置関係から外れています"
        )
    else:
        assert visit_reason_heading not in instructions, (
            "ask_visit_reason_enabled=false/未設定なのにVisit Reasonセクションが挿入されています"
        )


async def _test_build_instructions_direct(db_session_factory, shop_id, shop_id_no_settings):
    from app.database import AsyncSessionLocal
    from app.models.shop import Shop
    from app.models.ai_staff_settings import AIStaffSettings
    from app.services.realtime_voice_ai import build_realtime_instructions
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        shop = await session.get(Shop, shop_id)
        assert shop is not None

        # 4. staff_settingsが存在しない店舗（Phase1相当）
        shop_no_settings = await session.get(Shop, shop_id_no_settings)
        assert shop_no_settings is not None
        instructions_no_settings = await build_realtime_instructions(session, shop_no_settings, None)
        _assert_section_order(instructions_no_settings, expect_visit_reason=False)
        print("4. staff_settings=Noneの店舗でもIntent Classificationセクションが挿入される: OK")

        # 3. staff_settingsはあるが ask_visit_reason_enabled=false（デフォルト）の店舗
        res = await session.execute(select(AIStaffSettings).filter(AIStaffSettings.shop_id == shop_id))
        staff_settings = res.scalars().first()
        instructions_default = await build_realtime_instructions(session, shop, staff_settings)
        _assert_section_order(instructions_default, expect_visit_reason=False)
        print("3. 通常店舗でのセクション出現順序（Intent Classification含む）: OK")


async def _test_visit_reason_enabled(client, owner_headers, shop_id):
    from app.database import AsyncSessionLocal
    from app.models.shop import Shop
    from app.models.ai_staff_settings import AIStaffSettings
    from app.services.realtime_voice_ai import build_realtime_instructions
    from sqlalchemy import select

    r = await client.put(
        f"/api/v1/shops/{shop_id}/ai-staff-settings",
        json={"ask_visit_reason_enabled": True},
        headers=owner_headers,
    )
    assert r.status_code == 200, f"ask_visit_reason_enabled有効化に失敗: {r.status_code} {r.text}"

    async with AsyncSessionLocal() as session:
        shop = await session.get(Shop, shop_id)
        res = await session.execute(select(AIStaffSettings).filter(AIStaffSettings.shop_id == shop_id))
        staff_settings = res.scalars().first()
        assert staff_settings is not None and staff_settings.ask_visit_reason_enabled is True

        instructions = await build_realtime_instructions(session, shop, staff_settings)
        _assert_section_order(instructions, expect_visit_reason=True)

    print("5. ask_visit_reason_enabled=trueでもVisit Reasonの既存位置関係が壊れていない: OK")


async def _test_session_endpoint_transmits_new_section(client, shop_id):
    captured = {}

    class _FakeSecret:
        value = "ek_fake_test_secret"
        expires_at = 9999999999

    async def fake_create(**kwargs):
        captured["session"] = kwargs.get("session")
        return _FakeSecret()

    fake_client_secrets = MagicMock()
    fake_client_secrets.create = AsyncMock(side_effect=fake_create)
    fake_realtime = MagicMock()
    fake_realtime.client_secrets = fake_client_secrets
    fake_openai_client = MagicMock()
    fake_openai_client.realtime = fake_realtime

    with patch("app.services.realtime_voice_ai._get_client", return_value=fake_openai_client):
        r = await client.post(f"/api/v1/shops/{shop_id}/realtime-voice/session")
        assert r.status_code == 200, f"/realtime-voice/session failed: {r.status_code} {r.text}"
        body = r.json()
        assert body.get("client_secret") == "ek_fake_test_secret"
        # レスポンスにはinstructionsを一切含めない、という既存の秘匿方針の回帰確認
        assert "instructions" not in body, "レスポンスにinstructionsが漏れています（秘匿方針違反）"

    assert "session" in captured, "OpenAIへ送信されたsession_configを捕捉できませんでした"
    session_config = captured["session"]

    sent_instructions = session_config.get("instructions", "")
    assert "通話冒頭のご用件把握" in sent_instructions, (
        "実際にOpenAIへ送信されるinstructionsにIntent Classificationセクションが含まれていません"
    )
    assert "予約成立の宣言に関する絶対ルール" in sent_instructions, (
        "既存のBooking Safetyセクションが送信instructionsから失われています"
    )

    sent_tools = session_config.get("tools", [])
    sent_tool_names = [t["name"] for t in sent_tools]
    assert sent_tool_names == [
        "check_availability", "create_reservation", "get_shop_info", "find_customer",
        "confirm_customer_identity", "get_customer_context", "set_conversation_language",
        "request_callback",
    ], f"実際に送信されるtoolsが既存8個から変化しています: {sent_tool_names}"

    print("6. /realtime-voice/session が実際に送信するsession_configにIntent Classification + 既存8Toolが含まれる: OK")


async def main():
    _test_template_content()

    from app.main import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-intent-classification@example.com",
                "password": "password123",
                "display_name": "IntentClassificationテストオーナー",
            })
            assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
            owner_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "IntentClassificationテスト整体院", "category": "整体",
                "address": "東京都渋谷区1-1-1",
            }, headers=owner_headers)
            assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
            shop_id = r.json()["shop_id"]

            # staff_settingsを一度も保存していない、Phase1相当の別店舗
            r = await client.post("/api/v1/shops/register", json={
                "name": "IntentClassificationテスト食堂（設定なし）", "category": "定食屋",
                "address": "東京都渋谷区2-2-2",
            }, headers=owner_headers)
            assert r.status_code in (200, 201), f"create shop(no settings) failed: {r.status_code} {r.text}"
            shop_id_no_settings = r.json()["shop_id"]

            # shop_idにstaff_settingsレコードを存在させておく（ask_visit_reason_enabled=falseのデフォルト状態）
            r = await client.put(
                f"/api/v1/shops/{shop_id}/ai-staff-settings",
                json={"staff_name": "テスト太郎"},
                headers=owner_headers,
            )
            assert r.status_code == 200, f"initial ai-staff-settings save failed: {r.status_code} {r.text}"

            await _test_build_instructions_direct(None, shop_id, shop_id_no_settings)
            await _test_visit_reason_enabled(client, owner_headers, shop_id)
            await _test_session_endpoint_transmits_new_section(client, shop_id)

            print("Conversation Opening / Intent Classification smoke test: ALL CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
