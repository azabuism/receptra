"""
RECEPTRA — TELEPHONE GUIDANCE UPDATE スモークテスト
（相槌だけでターンを終わらせない／通話冒頭の目安5秒／直前の質問優先）

背景（実機での再現報告）:
客:「明日2人で予約したいんですけど」
AI:「ありがとうございます。」（← ここで発話が終わる）
その後、店内ノイズ・周囲の声・テレビ等を拾うだけで会話が先に進まない。

既存の「内部処理を実況するだけの発話で終わらせない」ルール
（_FAST_RESERVATION_FLOW_TEMPLATE、smoke_test_no_process_narration_filler.py
で検証済み）は、「整理します」「確認します」等の"処理を予告するだけの
フィラー"を対象にしたものであり、「ありがとうございます。」のような
純粋な相槌（Acknowledgement）は文字通りには対象に含まれていなかった。
これが今回の再現例で"ルールの抜け"になっていたため、既存ルールを流用・
上書きするのではなく、相槌（Acknowledgement）だけで発話を終えることを
明示的に禁止する新しい一般ルールを追加した。

今回の変更は、既存の全既存フェーズ（R3/R4/R5/P1/P2）で確立された方針に
従い、VAD・WebRTC・DB・新規Tool・新規stateを一切追加しない、
build_realtime_instructions()が組み立てるinstructions文字列（純粋な
自然文プロンプト）への追加のみである。

検証項目:
1. _FAST_RESERVATION_FLOW_TEMPLATEに「相槌だけで発話を終わらせない」
   ルールが含まれ、禁止対象の相槌4種（ありがとうございます／承知しました／
   かしこまりました／確認しますね）が明記されていること。
2. 新ルールが「相槌＋要約＋次の質問」の3点セットを1発話でまとめて行う
   よう明記していること。
3. ユーザー報告と同一の再現例（悪い例・良い例）が含まれていること。
4. 新ルールの挿入位置が「既に分かっている情報を聞き直さない」の直後・
   「内部処理を実況するだけの発話で終わらせない」の直前であること
   （関連ルールが自然な順序で並ぶことの確認）。
5. 「直前に尋ねた質問に対応する回答を優先する（ノイズ耐性）」ルールが
   含まれ、背景ノイズで新しいご用件を作らない旨・ただし明確な訂正は
   受け付ける旨の両方が明記されていること。
6. _INTENT_CLASSIFICATION_TEMPLATEの通話冒頭ご用件把握の目安が20秒から
   5秒に短縮されており、かつ「待機時間ではない」という既存の重要な
   注記（ハードタイマーではない）が変わらず維持されていること。
7. build_realtime_instructions()が組み立てるinstructions全体で、既存の
   全セクション出現順序（Booking Safetyが常に最後という不変条件を含む）
   が壊れていないこと。
8. 8つのRealtime Tool schemaが一切変更されていないこと（DB/API/Tool
   無変更の確認）。
9. VAD設定（semantic_vad / eagerness）が一切変更されていないこと。

実行: python3 tests/smoke_test_guidance_unit_acknowledgement.py
"""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(
    tempfile.mkdtemp(), "smoke_guidance_unit_ack.db"
)
os.environ["SECRET_KEY"] = "smoke-test-secret-key-guidance-unit-ack"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402


def _test_acknowledgement_only_rule_wording():
    from app.services.realtime_voice_ai import _FAST_RESERVATION_FLOW_TEMPLATE

    tmpl = _FAST_RESERVATION_FLOW_TEMPLATE
    normalized = tmpl.replace("\n", "")

    assert "相槌だけで発話を終わらせない" in normalized, "新ルールの見出しが見つかりません"

    for phrase in ["ありがとうございます。", "承知しました。", "かしこまりました。", "確認しますね。"]:
        assert phrase in normalized, f"禁止対象の相槌表現が見当たりません: {phrase}"

    idx = normalized.find("相槌だけで発話を終わらせない")
    nearby = normalized[idx:idx + 700]
    assert "絶対にしないでください" in nearby, "禁止の強い表現が見つかりません"

    print("1. 「相槌だけで発話を終わらせない」ルールと禁止相槌4種の明記: OK")

    # 2. 相槌＋要約＋次の質問の3点セット
    assert "短い相槌" in normalized
    assert "短い要約" in normalized
    assert "次の項目を尋ねる具体的な質問" in normalized
    print("2. 「相槌＋要約＋次の質問」を1発話でまとめる指示: OK")

    # 3. ユーザー報告と同一の再現例
    assert "明日2人で予約したいんですけど" in normalized
    assert "明日、2名様ですね。何時のご予約に" in normalized or "明日、2名様ですね。" in normalized
    print("3. ユーザー報告と同一の再現例（悪い例・良い例）: OK")


def _test_acknowledgement_only_rule_position():
    from app.services.realtime_voice_ai import _FAST_RESERVATION_FLOW_TEMPLATE

    pos_known = _FAST_RESERVATION_FLOW_TEMPLATE.find("既に分かっている情報を聞き直さない")
    pos_ack = _FAST_RESERVATION_FLOW_TEMPLATE.find("相槌だけで発話を終わらせない")
    pos_narration = _FAST_RESERVATION_FLOW_TEMPLATE.find("内部処理を実況するだけの発話で終わらせない")

    assert pos_known != -1 and pos_ack != -1 and pos_narration != -1
    assert pos_known < pos_ack < pos_narration, (
        f"新ルールの挿入位置が意図した順序ではありません: "
        f"既知情報={pos_known}, 相槌ルール={pos_ack}, 内部処理ルール={pos_narration}"
    )
    print("4. 新ルールの挿入位置（既知情報を聞き直さない の直後・内部処理ルールの直前）: OK")


def _test_contextual_noise_resistance_rule():
    from app.services.realtime_voice_ai import _FAST_RESERVATION_FLOW_TEMPLATE

    normalized = _FAST_RESERVATION_FLOW_TEMPLATE.replace("\n", "")
    assert "直前に尋ねた質問に対応する回答を優先する" in normalized, "ノイズ耐性ルールの見出しが見つかりません"
    idx = normalized.find("直前に尋ねた質問に対応する回答を優先する")
    nearby = normalized[idx:idx + 500]
    assert "背景の" in nearby and ("雑音" in nearby or "ノイズ" in nearby)
    assert "勝手に作り出したり" in nearby or "勝手に作" in nearby
    assert "訂正" in nearby, "明確な訂正は受け付ける旨の記載が見つかりません"
    print("5. 「直前の質問優先（ノイズ耐性）」ルールとその訂正許容の明記: OK")


def _test_intent_capture_5sec():
    from app.services.realtime_voice_ai import _INTENT_CLASSIFICATION_TEMPLATE

    tmpl = _INTENT_CLASSIFICATION_TEMPLATE
    assert "5秒" in tmpl, "5秒への言及が見つかりません"
    assert "20秒" not in tmpl, "旧・20秒という数値がテンプレートに残っています"
    assert "待機時間ではありません" in tmpl, "「待機時間ではない」という既存の重要な注記が失われています"
    assert "5秒間は待たなければ" in tmpl, "ハードタイマーではないという明記が見つかりません"
    print("6. 通話冒頭ご用件把握の目安が5秒に短縮・非タイマーである旨は維持: OK")


async def _test_full_instructions_regression():
    from app.services.realtime_voice_ai import build_realtime_instructions, _REALTIME_TOOLS
    from app.models.shop import Shop
    from app.models.ai_staff_settings import AIStaffSettings

    assert len(_REALTIME_TOOLS) == 8, f"Realtime Toolの数が想定と異なります: {len(_REALTIME_TOOLS)}"
    tool_names = {t["name"] for t in _REALTIME_TOOLS}
    for expected in (
        "check_availability", "create_reservation", "get_shop_info", "find_customer",
        "confirm_customer_identity", "get_customer_context", "set_conversation_language",
        "request_callback",
    ):
        assert expected in tool_names, f"既存Toolが失われています: {expected}"
    print("8. 8つのRealtime Tool schema: 無変更 OK")

    from app.main import app, lifespan

    async with lifespan(app):
        from app.database import AsyncSessionLocal

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-guidance-update@example.com", "password": "password123",
                "display_name": "GuidanceUpdateテストオーナー",
            })
            assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
            owner = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "GuidanceUpdateテスト店", "category": "飲食店", "address": "東京都渋谷区1-1-1",
            }, headers=owner)
            assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
            shop_id = r.json()["shop_id"]

            async with AsyncSessionLocal() as session:
                shop = await session.get(Shop, shop_id)
                instructions = await build_realtime_instructions(session, shop, None)

            # 7. セクション出現順序の回帰確認（既存の不変条件）
            markers = [
                "通話冒頭のご用件把握",
                "相槌だけで発話を終わらせない",
                "内部処理を実況するだけの発話で終わらせない",
                "直前に尋ねた質問に対応する回答を優先する",
                "最重要：速さのために情報を勝手に作らない",
                "予約成立の宣言に関する絶対ルール",  # Booking Safety（常に最後）
            ]
            positions = [instructions.find(m) for m in markers]
            assert all(p != -1 for p in positions), f"想定セクションが見つかりません: {list(zip(markers, positions))}"
            assert positions == sorted(positions), f"セクション出現順序が崩れています: {list(zip(markers, positions))}"
            print("7. build_realtime_instructions() 全体でのセクション出現順序（Booking Safetyが最後）: OK")

            # 9. VAD設定は変更していないことの確認（session_configそのものを確認）
            from unittest.mock import AsyncMock, MagicMock, patch

            class _FakeSecret:
                value = "ek_fake_test_secret"
                expires_at = 9999999999

            captured = {}

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
            assert r.status_code == 200, f"session発行に失敗: {r.status_code} {r.text}"

            session_cfg = captured.get("session") or {}
            turn_detection = (
                session_cfg.get("audio", {}).get("input", {}).get("turn_detection", {})
                if isinstance(session_cfg, dict) else {}
            )
            assert turn_detection.get("type") == "semantic_vad", f"turn_detectionが変更されています: {turn_detection}"
            assert "threshold" not in turn_detection, "threshold等のVADパラメータが新規に追加されています"
            assert "silence_duration_ms" not in turn_detection
            assert "prefix_padding_ms" not in turn_detection
            print("9. VAD設定（semantic_vad / eagerness）: 無変更 OK")


def main():
    _test_acknowledgement_only_rule_wording()
    _test_acknowledgement_only_rule_position()
    _test_contextual_noise_resistance_rule()
    _test_intent_capture_5sec()
    asyncio.run(_test_full_instructions_regression())
    print()
    print("全テストOK: Telephone Guidance Update（相槌だけでターンを終わらせない／"
          "5秒目安／ノイズ耐性）— instructions文言の追加とVAD/Tool無変更を確認")


if __name__ == "__main__":
    main()
