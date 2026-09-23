"""
Reservation Intelligence Phase A: time_in_past reason_code スモークテスト

背景: check_single_slot_availability() / create_reservation() の過去日時判定は
以前 reason_code="invalid_request"（入力形式の不正）に丸めていたが、これは
誤り（日時の形式自体は正しく、単に既に過ぎているだけ）。AI側のTool説明文も
invalid_requestの場合「形式を確認して再度呼び出してください」と指示しており、
過去時刻のケースでこの案内は誤り（形式は直しようがない）。

本フェーズでの変更（すべてPython/instructions側。新規Tool・DBスキーマ・
VAD変更なし）:
- reason_codeを invalid_request から time_in_past へ分離
  （app/routers/reservations.py の check_single_slot_availability() /
  create_reservation() の2箇所）
- check_availability / create_reservation の両Tool descriptionに
  time_in_pastの案内方針を追記（過去である旨を短く伝え、翌日等へ黙って
  読み替えない、別の時間帯を改めて伺う）
- _TIME_AMBIGUITY_TEMPLATE に、AM/PM解釈で選んだ時刻であってもTool結果が
  time_in_pastで返ることがある旨のクロスリファレンスを追記

検証項目:
1. check_availability Toolに明確に過去の日時を渡すと、
   available=False, reason_code="time_in_past" が返ること
   （invalid_requestではないこと）。
2. create_reservation Toolに明確に過去の日時を渡すと、
   success=False, reason_code="time_in_past" が返ること。
3. _REALTIME_TOOLSのcheck_availability/create_reservation description文言に、
   time_in_pastの案内方針（過去である旨を伝える、翌日等へ黙って読み替えない、
   AI自身の推測で過去判定しない）が含まれていること。
4. _TIME_AMBIGUITY_TEMPLATEに、AM/PM判定とtime_in_pastは別物であるという
   クロスリファレンスが含まれていること。
5. 8 Tool schema・他のreason_code語彙（business_hours_not_configured等）が
   変更されていないことの回帰確認。

実行: python3 tests/smoke_test_time_in_past.py
"""

import asyncio
import os
import sys
import tempfile
import uuid
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_time_in_past.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-time-in-past"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402


def _test_instructions_wording():
    from app.services.realtime_voice_ai import _REALTIME_TOOLS, _TIME_AMBIGUITY_TEMPLATE

    check_availability_tool = next(t for t in _REALTIME_TOOLS if t["name"] == "check_availability")
    create_reservation_tool = next(t for t in _REALTIME_TOOLS if t["name"] == "create_reservation")

    for tool in (check_availability_tool, create_reservation_tool):
        desc = tool["description"]
        assert "time_in_past" in desc, f"{tool['name']}のdescriptionにtime_in_pastの説明がありません"
        assert "翌日や別の日時へ" in desc or "翌日や別の日時へお客様に断りなく" in desc, (
            f"{tool['name']}に「黙って翌日等へ読み替えない」旨の禁止が見当たりません"
        )
        assert "過去かどうかの判定は" in desc, (
            f"{tool['name']}に「過去判定はAI自身の推測で行わない」旨の指示が見当たりません"
        )

    assert "time_in_past" in _TIME_AMBIGUITY_TEMPLATE, "_TIME_AMBIGUITY_TEMPLATEにtime_in_pastへのクロスリファレンスがありません"
    assert "午前/午後の解釈をやり直すのではなく" in _TIME_AMBIGUITY_TEMPLATE.replace("\n", "")

    # 8 Tool schema / 既存reason_code語彙の回帰確認
    tool_names = [t["name"] for t in _REALTIME_TOOLS]
    assert tool_names == [
        "check_availability", "create_reservation", "get_shop_info", "find_customer",
        "confirm_customer_identity", "get_customer_context", "set_conversation_language",
        "request_callback",
    ], f"8 Tool schemaが変化しています: {tool_names}"
    for code in ("business_hours_not_configured", "shop_closed", "outside_business_hours", "fully_booked"):
        assert code in check_availability_tool["description"], f"既存reason_code説明が失われています: {code}"

    print("3-5. Tool description文言（time_in_past案内・翌日読み替え禁止・AI自身の推測禁止）/ "
          "_TIME_AMBIGUITY_TEMPLATEクロスリファレンス / 8 Tool schema回帰: OK")


async def main():
    _test_instructions_wording()

    from app.main import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-time-in-past@example.com",
                "password": "password123",
                "display_name": "TimeInPastテストオーナー",
            })
            assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
            owner_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "TimeInPastテスト整体院", "category": "整体",
                "address": "東京都渋谷区4-4-4",
            }, headers=owner_headers)
            assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
            shop_id = r.json()["shop_id"]

            # 毎日 00:00〜23:30 営業（曜日に関わらず過去判定だけを見たいため）
            hours_payload = {"hours": [
                {"day_of_week": d, "opening_time": "00:00:00", "closing_time": "23:30:00", "is_closed": False}
                for d in range(7)
            ]}
            r = await client.put(f"/api/v1/shops/{shop_id}/hours", json=hours_payload, headers=owner_headers)
            assert r.status_code == 200, f"set hours failed: {r.status_code} {r.text}"

            from app.database import AsyncSessionLocal
            from app.models.shop import Shop
            async with AsyncSessionLocal() as session:
                shop_obj = await session.get(Shop, shop_id)
                shop_obj.reservations_enabled = True
                await session.commit()

            # 明確に過去の日時（1日前の同時刻）を使う。
            yesterday = date.today() - timedelta(days=1)

            # ===== 1. check_availability =====
            r = await client.post(
                f"/api/v1/shops/{shop_id}/realtime-voice/tools/check-availability",
                json={"date": yesterday.isoformat(), "time": "10:00", "party_size": 2},
            )
            assert r.status_code == 200, f"check-availability failed: {r.status_code} {r.text}"
            body = r.json()
            assert body["available"] is False and body["reason_code"] == "time_in_past", (
                f"過去日時のcheck_availabilityでreason_codeが想定と異なります（invalid_requestではなくtime_in_pastが正しい）: {body}"
            )
            print("1. check_availability: 過去日時はreason_code=time_in_past（invalid_requestではない） OK")

            # ===== 2. create_reservation =====
            r = await client.post(
                f"/api/v1/shops/{shop_id}/realtime-voice/tools/create-reservation",
                json={
                    "date": yesterday.isoformat(), "time": "10:00", "party_size": 2,
                    "guest_name": "谷村あきら", "guest_phone": "09012345678",
                    "call_id": "smoke-test-call-" + str(uuid.uuid4()),
                },
            )
            assert r.status_code == 200, f"create-reservation failed: {r.status_code} {r.text}"
            body = r.json()
            assert body["success"] is False and body["reason_code"] == "time_in_past", (
                f"過去日時のcreate_reservationでreason_codeが想定と異なります: {body}"
            )
            print("2. create_reservation: 過去日時はreason_code=time_in_past（予約は成立しない） OK")

    print("\n全テストOK: Reservation Intelligence Phase A（time_in_past reason_code分離）")


if __name__ == "__main__":
    asyncio.run(main())
