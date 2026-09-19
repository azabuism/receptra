"""
サービス+スタッフ対応の予約フロー（空き状況計算・予約作成）のスモークテスト
一時的な SQLite DB + httpx.AsyncClient で、実際に近い形で一連のフローを検証する。
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta

# リポジトリのルートを import パスに追加する（tests/ から直接実行できるように）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_service_booking.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key"

import httpx  # noqa: E402


def _next_weekday_date(weekday: int) -> str:
    """指定した曜日(0=月)の直近の未来の日付をYYYY-MM-DD形式で返す"""
    today = datetime.utcnow().date()
    days_ahead = (weekday - today.weekday()) % 7
    days_ahead = days_ahead if days_ahead > 0 else 7  # 今日は避けて必ず未来にする
    return (today + timedelta(days=days_ahead)).isoformat()


async def main():
    from app.main import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. オーナー登録 → 店舗作成（美容室）
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-booking-smoke@example.com",
                "password": "password123",
                "display_name": "予約テストオーナー",
            })
            assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
            owner_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "予約テスト美容室",
                "category": "ヘアサロン",
                "address": "東京都新宿区3-3-3",
            }, headers=owner_headers)
            assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
            shop_id = r.json()["shop_id"]

            # 2. 営業時間を設定（月曜のみ 10:00-18:00, ラストオーダー17:00）
            target_date = _next_weekday_date(0)  # 次の月曜日
            r = await client.put(f"/api/v1/shops/{shop_id}/hours", json={
                "hours": [
                    {"day_of_week": i, "opening_time": "10:00", "closing_time": "18:00",
                     "is_closed": (i != 0), "last_order_time": "17:00"}
                    for i in range(7)
                ]
            }, headers=owner_headers)
            assert r.status_code == 200, f"set hours failed: {r.status_code} {r.text}"

            # 3. サービスを作成（60分のカット）
            r = await client.post("/api/v1/services", json={
                "shop_id": shop_id, "name": "カット", "base_price": 5000, "duration_minutes": 60,
            }, headers=owner_headers)
            assert r.status_code == 200, f"create service failed: {r.status_code} {r.text}"
            service_id = r.json()["id"]

            # 4. スタッフを2名登録し、片方だけにサービスを割り当てる
            r = await client.post("/api/v1/staff", json={
                "shop_id": shop_id, "name": "担当A",
            }, headers=owner_headers)
            assert r.status_code == 200, f"create staff A failed: {r.status_code} {r.text}"
            staff_a_id = r.json()["id"]

            r = await client.post("/api/v1/staff", json={
                "shop_id": shop_id, "name": "担当B",
            }, headers=owner_headers)
            assert r.status_code == 200, f"create staff B failed: {r.status_code} {r.text}"
            staff_b_id = r.json()["id"]

            r = await client.post(f"/api/v1/staff/{staff_a_id}/services/{service_id}", headers=owner_headers)
            assert r.status_code == 200, f"assign service to staff A failed: {r.status_code} {r.text}"
            # staff_b はこのサービスを提供しない（担当外）

            # 5. 空き状況を確認（サービス指定あり）→ 10:00〜17:00 の枠がavailableになっているはず
            r = await client.get(
                f"/api/v1/reservations/shop/{shop_id}/availability",
                params={"date": target_date, "service_id": service_id},
            )
            assert r.status_code == 200, f"availability failed: {r.status_code} {r.text}"
            avail = r.json()
            assert avail["is_open"] is True, avail
            assert avail["service_id"] == service_id
            slot_10 = next((s for s in avail["slots"] if s["time"] == "10:00"), None)
            assert slot_10 is not None and slot_10["available"] is True, avail["slots"]

            # 6. 担当外のスタッフ(staff_b)を指名すると予約できないこと
            r = await client.post("/api/v1/reservations/create", json={
                "shop_id": shop_id,
                "service_id": service_id,
                "staff_id": staff_b_id,
                "guest_name": "予約花子",
                "guest_phone": "090-1111-2222",
                "reservation_date": f"{target_date}T10:00:00",
            })
            assert r.status_code == 400, f"expected 400 for staff not offering service, got {r.status_code} {r.text}"

            # 7. サービス指定・スタッフ指名なしで予約作成 → staff_a が自動アサインされること
            r = await client.post("/api/v1/reservations/create", json={
                "shop_id": shop_id,
                "service_id": service_id,
                "guest_name": "予約花子",
                "guest_phone": "090-1111-2222",
                "reservation_date": f"{target_date}T10:00:00",
            })
            assert r.status_code in (200, 201), f"create service reservation failed: {r.status_code} {r.text}"
            data = r.json()
            reservation = data["reservation"]
            assert reservation["service_id"] == service_id
            assert reservation["service_name"] == "カット"
            assert reservation["staff_id"] == staff_a_id, reservation
            assert reservation["staff_name"] == "担当A"
            assert reservation["total_price"] == 5000, reservation
            assert reservation["payment_status"] == "unpaid"
            # number_of_people はリクエストで省略 → デフォルト1になっていること
            assert reservation["number_of_people"] == 1, reservation

            # 8. 同じ時間・同じサービスでもう一件予約 → staff_a は埋まっているので空いていないはず
            r = await client.get(
                f"/api/v1/reservations/shop/{shop_id}/availability",
                params={"date": target_date, "service_id": service_id},
            )
            avail2 = r.json()
            slot_10_after = next((s for s in avail2["slots"] if s["time"] == "10:00"), None)
            assert slot_10_after is not None and slot_10_after["available"] is False, avail2["slots"]

            r = await client.post("/api/v1/reservations/create", json={
                "shop_id": shop_id,
                "service_id": service_id,
                "guest_name": "予約次郎",
                "guest_phone": "090-3333-4444",
                "reservation_date": f"{target_date}T10:00:00",
            })
            assert r.status_code == 400, f"expected fully-booked rejection, got {r.status_code} {r.text}"

            # 9. 別の時間帯（11:30）なら引き続き予約できること
            r = await client.post("/api/v1/reservations/create", json={
                "shop_id": shop_id,
                "service_id": service_id,
                "guest_name": "予約次郎",
                "guest_phone": "090-3333-4444",
                "reservation_date": f"{target_date}T11:30:00",
            })
            assert r.status_code in (200, 201), f"create second reservation failed: {r.status_code} {r.text}"

            # 10. オーナー側の予約一覧にも service_name / staff_name が反映されていること
            r = await client.get(f"/api/v1/reservations/shop/{shop_id}", headers=owner_headers)
            assert r.status_code == 200, f"list shop reservations failed: {r.status_code} {r.text}"
            items = r.json()["items"]
            assert len(items) == 2
            assert all(item["service_name"] == "カット" for item in items)

            # 11. 通常の飲食店型（サービス指定なし）の予約は従来どおり動作すること（後方互換）
            r = await client.post("/api/v1/shops/register", json={
                "name": "予約テスト食堂", "category": "ラーメン", "address": "東京都新宿区4-4-4",
            }, headers=owner_headers)
            assert r.status_code in (200, 201)
            restaurant_shop_id = r.json()["shop_id"]

            r = await client.put(f"/api/v1/shops/{restaurant_shop_id}/hours", json={
                "hours": [
                    {"day_of_week": i, "opening_time": "10:00", "closing_time": "18:00",
                     "is_closed": (i != 0), "last_order_time": "17:00"}
                    for i in range(7)
                ]
            }, headers=owner_headers)
            assert r.status_code == 200

            r = await client.post("/api/v1/reservations/create", json={
                "shop_id": restaurant_shop_id,
                "guest_name": "飲食予約太郎",
                "guest_phone": "090-5555-6666",
                "reservation_date": f"{target_date}T12:00:00",
                "number_of_people": 3,
            })
            assert r.status_code in (200, 201), f"restaurant reservation (no service) failed: {r.status_code} {r.text}"
            restaurant_reservation = r.json()["reservation"]
            assert restaurant_reservation["service_id"] is None
            assert restaurant_reservation["number_of_people"] == 3
            assert restaurant_reservation["total_price"] is None

            print("ALL SERVICE BOOKING SMOKE TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
