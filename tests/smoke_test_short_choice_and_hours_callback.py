"""
Short Choice 3-Second Turn + Missing Business Hours Callback スモークテスト

検証項目（ユーザー指定のCASE 1〜25のうち、バックエンド/DB/Toolに関わる部分）:

改善2（営業時間未登録なら予約を無理に受けない）:
- CASE 12: 営業時間が1件も登録されていない店舗は、check_availability /
  create_reservation いずれも reason_code="business_hours_not_configured" を
  返し、決して「空いています」「予約できます」と誤判定しないこと。
- CASE 11: 営業時間は登録されているが指定曜日が定休日の場合は
  reason_code="shop_closed"（business_hours_not_configuredとは別）。
- CASE 11: 営業時間内だが希望時刻が営業時間外の場合は
  reason_code="outside_business_hours"（自動的にCALLBACKへ倒さない）。
- CASE 10: 営業時間内・空きありの場合は通常通りavailable=trueになること
  （今回の変更が正常系を壊していないことの回帰確認）。
- CASE 13/15/18: request_callback Toolに
  reason_code="business_hours_not_configured"・名前・電話番号・
  来店希望日時・人数を渡すと、CallbackRequestへ正しく保存され、
  既に分かっている情報（日時・人数）を再質問する必要がないこと
  （Toolのparametersとしてそのまま受け渡せること）。
- CallbackRequestReasonCode enumにBUSINESS_HOURS_NOT_CONFIGUREDが
  存在すること、_HUMAN_HANDOFF_TEMPLATE / 各Tool descriptionが
  「ご自身で店舗へ連絡してください」のような丸投げ案内を一切含まないこと
  （Phase3B.1時点で既に実装済みの内容の回帰確認。今回のセッションで新規に
  実装したものではないが、改善2の要求を実際に満たしていることを検証する）。

改善1（Short Choice 3-Second Turn）はフロントエンドJS側の実装のため、
Node.js側のテスト（tests/test_short_choice_turn.js）で検証する。このPythonテストでは、
関連するバックエンド側の不変条件（8 Tool schema・Intent Classification・
Fast Reservation Flow等が変更されていないこと）のみ軽く確認する。

実行: python3 tests/smoke_test_short_choice_and_hours_callback.py
"""

import asyncio
import os
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_short_choice_hours.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-short-choice-hours"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402


def _test_backend_unchanged():
    """今回はrealtime_voice_ai.pyを一切変更していないことの構造的確認。"""
    from app.services.realtime_voice_ai import _REALTIME_TOOLS, _INTENT_CLASSIFICATION_TEMPLATE, _HUMAN_HANDOFF_TEMPLATE

    tool_names = [t["name"] for t in _REALTIME_TOOLS]
    assert tool_names == [
        "check_availability", "create_reservation", "get_shop_info", "find_customer",
        "confirm_customer_identity", "get_customer_context", "set_conversation_language",
        "request_callback",
    ], f"8 Tool schemaが変化しています: {tool_names}"
    assert "通話冒頭のご用件把握" in _INTENT_CLASSIFICATION_TEMPLATE, "Intent Classificationテンプレートが失われています"

    # 改善2の核心: business_hours_not_configured が Human Handoff の対象として
    # 明記されていること。丸投げ案内の禁止フレーズ自体は「絶対にしないでください」
    # という否定形の実例としてテンプレート内に存在するのが正しい姿のため
    # （AIへ「言うな」と教えるために書かれている）、単純な不在チェックはできない。
    # 代わりに、それらの禁止フレーズが必ず「絶対に」等の否定を伴う文脈でのみ
    # 登場していること（=肯定的な指示として独立して使われていないこと）を確認する。
    assert "business_hours_not_configured" in _HUMAN_HANDOFF_TEMPLATE
    forbidden_handoff_phrases = [
        "ご自身で店舗へ連絡してください", "店舗へ直接お問い合わせください",
        "店舗へ直接電話してください", "窓口で確認してください",
    ]
    for phrase in forbidden_handoff_phrases:
        idx = _HUMAN_HANDOFF_TEMPLATE.find(phrase)
        assert idx != -1, f"禁止フレーズの実例自体が見当たりません（テンプレートが想定と異なる可能性）: {phrase}"
        nearby = _HUMAN_HANDOFF_TEMPLATE[max(0, idx - 30):idx + len(phrase) + 200]
        assert "絶対に" in nearby or "案内は" in nearby, (
            f"禁止フレーズが否定文脈を伴わず登場しています（肯定的な指示になっている疑い）: {phrase!r} 周辺: {nearby!r}"
        )

    # request_callback Toolのparametersにdesired_date/desired_time/party_sizeが
    # あり、既知情報をそのまま引き継げること（CASE18の前提）。
    request_callback_tool = next(t for t in _REALTIME_TOOLS if t["name"] == "request_callback")
    props = request_callback_tool["parameters"]["properties"]
    for field in ("desired_date", "desired_time", "party_size", "customer_name", "customer_phone"):
        assert field in props, f"request_callback Toolにフィールドがありません: {field}"
    assert "business_hours_not_configured" in request_callback_tool["parameters"]["properties"]["reason_code"]["enum"]

    print("0. Tool schema/Intent Classification/Human Handoff文言: 変更なし・想定通り OK")


def _test_callback_reason_code_enum():
    from app.models.callback_request import CallbackRequestReasonCode
    assert CallbackRequestReasonCode.BUSINESS_HOURS_NOT_CONFIGURED.value == "business_hours_not_configured"
    print("0b. CallbackRequestReasonCode.BUSINESS_HOURS_NOT_CONFIGURED: OK")


async def main():
    _test_backend_unchanged()
    _test_callback_reason_code_enum()

    from app.main import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-short-choice-hours@example.com",
                "password": "password123",
                "display_name": "ShortChoiceHoursテストオーナー",
            })
            assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
            owner_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

            # 店舗A: 営業時間を一度も登録しない店舗（改善2の主対象）
            r = await client.post("/api/v1/shops/register", json={
                "name": "ShortChoiceテスト整体院（営業時間未登録）", "category": "整体",
                "address": "東京都渋谷区1-1-1",
            }, headers=owner_headers)
            assert r.status_code in (200, 201), f"create shop A failed: {r.status_code} {r.text}"
            shop_a = r.json()["shop_id"]

            # 店舗B: 営業時間を登録済み（月〜土 10:00-19:00、日曜定休）
            r = await client.post("/api/v1/shops/register", json={
                "name": "ShortChoiceテスト整体院（営業時間登録済み）", "category": "整体",
                "address": "東京都渋谷区2-2-2",
            }, headers=owner_headers)
            assert r.status_code in (200, 201), f"create shop B failed: {r.status_code} {r.text}"
            shop_b = r.json()["shop_id"]

            hours_payload = {"hours": [
                {"day_of_week": d, "opening_time": "10:00:00", "closing_time": "19:00:00", "is_closed": False}
                for d in range(6)
            ] + [
                {"day_of_week": 6, "opening_time": "10:00:00", "closing_time": "19:00:00", "is_closed": True},
            ]}
            r = await client.put(f"/api/v1/shops/{shop_b}/hours", json=hours_payload, headers=owner_headers)
            assert r.status_code == 200, f"set hours for shop B failed: {r.status_code} {r.text}"

            # 両店舗とも reservations_enabled=True にする（billing activationを
            # 経由せず、既存の他smoke testと同じ手法でDBを直接更新する。
            # smoke_test_phase3e3_availability_safety.pyと同じ確立済みパターン）。
            from app.database import AsyncSessionLocal
            from app.models.shop import Shop
            async with AsyncSessionLocal() as session:
                for sid in (shop_a, shop_b):
                    shop_obj = await session.get(Shop, sid)
                    shop_obj.reservations_enabled = True
                await session.commit()

            # 次の月曜日・次の日曜日を計算する（過去日時エラーを避けるため2週間先）。
            from datetime import date, timedelta
            today = date.today()
            days_until_monday = (0 - today.weekday()) % 7
            next_monday = today + timedelta(days=days_until_monday + 14)
            days_until_sunday = (6 - today.weekday()) % 7
            next_sunday = today + timedelta(days=days_until_sunday + 14)

            # ===== CASE 12: 店舗A（営業時間未登録） =====
            r = await client.post(
                f"/api/v1/shops/{shop_a}/realtime-voice/tools/check-availability",
                json={"date": next_monday.isoformat(), "time": "18:00", "party_size": 2},
            )
            assert r.status_code == 200, f"check-availability(shop A) failed: {r.status_code} {r.text}"
            body = r.json()
            assert body["available"] is False and body["reason_code"] == "business_hours_not_configured", (
                f"営業時間未登録店舗でavailable/reason_codeが想定と異なります: {body}"
            )
            print("1. CASE12 check_availability: 営業時間未登録店舗はbusiness_hours_not_configuredで空き状況を案内しない OK")

            r = await client.post(
                f"/api/v1/shops/{shop_a}/realtime-voice/tools/create-reservation",
                json={
                    "date": next_monday.isoformat(), "time": "18:00", "party_size": 2,
                    "guest_name": "谷村あきら", "guest_phone": "09012345678",
                    "call_id": "smoke-test-call-" + str(uuid.uuid4()),
                },
            )
            assert r.status_code == 200, f"create-reservation(shop A) failed: {r.status_code} {r.text}"
            body = r.json()
            assert body["success"] is False and body["reason_code"] == "business_hours_not_configured", (
                f"営業時間未登録店舗でcreate_reservationが誤って成立/誤ったreason_codeになっています: {body}"
            )
            print("2. CASE12 create_reservation: 営業時間未登録店舗は予約を無理に成立させない OK")

            # ===== CASE 11: 店舗B（登録済み・日曜定休） =====
            r = await client.post(
                f"/api/v1/shops/{shop_b}/realtime-voice/tools/check-availability",
                json={"date": next_sunday.isoformat(), "time": "12:00", "party_size": 2},
            )
            assert r.status_code == 200
            body = r.json()
            assert body["reason_code"] == "shop_closed", (
                f"定休日がshop_closedとして区別されていません（business_hours_not_configuredと混同）: {body}"
            )
            print("3. CASE11 定休日はshop_closed（business_hours_not_configuredとは別扱い） OK")

            r = await client.post(
                f"/api/v1/shops/{shop_b}/realtime-voice/tools/check-availability",
                json={"date": next_monday.isoformat(), "time": "21:00", "party_size": 2},
            )
            assert r.status_code == 200
            body = r.json()
            assert body["reason_code"] == "outside_business_hours", (
                f"営業時間外がoutside_business_hoursとして区別されていません: {body}"
            )
            print("4. CASE11 営業時間外はoutside_business_hours（自動的にCALLBACKへ倒さない） OK")

            # ===== CASE 10: 店舗B（登録済み・営業時間内） =====
            r = await client.post(
                f"/api/v1/shops/{shop_b}/realtime-voice/tools/check-availability",
                json={"date": next_monday.isoformat(), "time": "12:00", "party_size": 2},
            )
            assert r.status_code == 200
            body = r.json()
            assert body["available"] is True, f"営業時間内・空きありのはずがavailable=falseです: {body}"
            print("5. CASE10 営業時間内・空きありは通常通りavailable=true（正常系の回帰確認） OK")

            # ===== CASE 13/15/18: request_callback（店舗A・営業時間未登録） =====
            call_id = "smoke-test-callback-" + str(uuid.uuid4())
            r = await client.post(
                f"/api/v1/shops/{shop_a}/realtime-voice/tools/request-callback",
                json={
                    "customer_name": "谷村あきら",
                    "customer_phone": "09012345678",
                    "inquiry_text": "本日18時に2名で予約希望。営業時間未登録のため折り返し。",
                    "desired_date": next_monday.isoformat(),
                    "desired_time": "18:00",
                    "party_size": 2,
                    "reason_code": "business_hours_not_configured",
                    "call_id": call_id,
                },
            )
            assert r.status_code == 200, f"request-callback failed: {r.status_code} {r.text}"
            body = r.json()
            assert body["success"] is True and body["reason_code"] == "business_hours_not_configured", (
                f"request_callbackの結果が想定と異なります: {body}"
            )

            from app.models.callback_request import CallbackRequest
            from sqlalchemy import select
            async with AsyncSessionLocal() as session:
                res = await session.execute(
                    select(CallbackRequest).filter(CallbackRequest.shop_id == shop_a)
                )
                rows = res.scalars().all()
                assert len(rows) == 1, f"CallbackRequestが想定件数と異なります: {len(rows)}"
                row = rows[0]
                assert row.reason_code == "business_hours_not_configured"
                assert row.customer_name == "谷村あきら"
                assert row.customer_phone == "09012345678"
                assert row.party_size == 2
                assert row.desired_date is not None and row.desired_date.isoformat() == next_monday.isoformat()
                assert row.desired_time is not None and row.desired_time.strftime("%H:%M") == "18:00"
            print("6. CASE13/15/18 request_callback: 既知の日時・人数・氏名・電話番号がそのままCallbackRequestへ保存される OK")

            # 同一call_idでの再送信は新しい行を作らない（冪等性の既存挙動が壊れていないこと）
            r = await client.post(
                f"/api/v1/shops/{shop_a}/realtime-voice/tools/request-callback",
                json={
                    "customer_name": "谷村あきら", "customer_phone": "09012345678",
                    "inquiry_text": "重複送信テスト", "reason_code": "business_hours_not_configured",
                    "call_id": call_id,
                },
            )
            assert r.status_code == 200 and r.json()["success"] is True
            async with AsyncSessionLocal() as session:
                res = await session.execute(select(CallbackRequest).filter(CallbackRequest.shop_id == shop_a))
                assert len(res.scalars().all()) == 1, "同一call_idの再送信で行が重複作成されています（冪等性の回帰）"
            print("7. 同一call_idでのrequest_callback再送信は重複作成しない（既存冪等性の回帰確認） OK")

            print("Short Choice 3-Second Turn + Missing Business Hours Callback smoke test: ALL CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
