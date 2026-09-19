"""
サービス・スタッフ管理機能のスモークテスト（services.py / staff.py の書き直し検証）
一時的な SQLite DB + httpx.AsyncClient で、実際に近い形で一連のフローを検証する。
"""

import asyncio
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta

# リポジトリのルートを import パスに追加する（tests/ から直接実行できるように）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_services_staff.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key"

import httpx  # noqa: E402


async def main():
    from app.main import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. オーナーA登録 → ログイン → 店舗作成
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-a-smoke@example.com",
                "password": "password123",
                "display_name": "オーナーA",
            })
            assert r.status_code in (200, 201), f"register(owner A) failed: {r.status_code} {r.text}"
            owner_a_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "スモーク美容室",
                "category": "ヘアサロン",
                "address": "東京都渋谷区2-2-2",
            }, headers=owner_a_headers)
            assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
            shop_id = r.json()["shop_id"]

            # 2. オーナーB登録（権限チェック検証用の「他人」役）
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-b-smoke@example.com",
                "password": "password123",
                "display_name": "オーナーB",
            })
            assert r.status_code in (200, 201), f"register(owner B) failed: {r.status_code} {r.text}"
            owner_b_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

            # 3. 認証なしでサービス作成 → 拒否されること
            r = await client.post("/api/v1/services", json={
                "shop_id": shop_id, "name": "カット", "base_price": 4000,
            })
            assert r.status_code in (401, 403), f"expected auth failure, got {r.status_code} {r.text}"

            # 4. オーナーAがサービスを作成
            r = await client.post("/api/v1/services", json={
                "shop_id": shop_id,
                "name": "カット",
                "description": "シャンプー込み",
                "base_price": 4000,
                "duration_minutes": 45,
                "service_type": "カット",
            }, headers=owner_a_headers)
            assert r.status_code == 200, f"create service failed: {r.status_code} {r.text}"
            service = r.json()
            service_id = service["id"]
            assert service["is_active"] is True
            assert service["base_price"] == 4000

            # 5. オーナーB（他人）はこのサービスを更新できないこと
            r = await client.put(f"/api/v1/services/{service_id}", json={
                "base_price": 1,
            }, headers=owner_b_headers)
            assert r.status_code == 403, f"expected 403 for non-owner update, got {r.status_code} {r.text}"

            # 6. オーナーAは更新できる（is_active の bool <-> 文字列変換も検証）
            r = await client.put(f"/api/v1/services/{service_id}", json={
                "base_price": 4500, "is_active": False,
            }, headers=owner_a_headers)
            assert r.status_code == 200, f"update service failed: {r.status_code} {r.text}"
            assert r.json()["base_price"] == 4500
            assert r.json()["is_active"] is False

            # 7. 公開一覧・詳細取得（認証不要）
            r = await client.get(f"/api/v1/services?shop_id={shop_id}")
            assert r.status_code == 200 and len(r.json()) == 1, f"list services failed: {r.status_code} {r.text}"

            r = await client.get(f"/api/v1/services/{service_id}")
            assert r.status_code == 200, f"get service failed: {r.status_code} {r.text}"

            # 8. オーナーAがスタッフを登録
            r = await client.post("/api/v1/staff", json={
                "shop_id": shop_id,
                "name": "スタイリスト鈴木",
                "specialty": "カット・カラー",
                "position": "スタイリスト",
            }, headers=owner_a_headers)
            assert r.status_code == 200, f"create staff failed: {r.status_code} {r.text}"
            staff = r.json()
            staff_id = staff["id"]
            assert staff["is_active"] is True

            # 9. 他人はスタッフを削除できないこと
            r = await client.delete(f"/api/v1/staff/{staff_id}", headers=owner_b_headers)
            assert r.status_code == 403, f"expected 403 for non-owner delete, got {r.status_code} {r.text}"

            # 10. スタッフにサービスを割り当て
            r = await client.post(f"/api/v1/staff/{staff_id}/services/{service_id}", headers=owner_a_headers)
            assert r.status_code == 200, f"assign staff service failed: {r.status_code} {r.text}"
            assert r.json()["service_id"] == service_id

            # 10b. 重複割り当ては拒否されること
            r = await client.post(f"/api/v1/staff/{staff_id}/services/{service_id}", headers=owner_a_headers)
            assert r.status_code == 400, f"expected 400 for duplicate assignment, got {r.status_code} {r.text}"

            # 11. スタッフが提供するサービス一覧を取得（公開）
            r = await client.get(f"/api/v1/staff/{staff_id}/services")
            assert r.status_code == 200 and len(r.json()) == 1, f"get staff services failed: {r.status_code} {r.text}"

            # 12. 今後の予約がある場合、スタッフ削除がブロックされること
            #     （create_reservation はまだ staff_id を受け付けないため、DB に直接挿入して検証する）
            from app.database import AsyncSessionLocal
            from app.models.reservation import Reservation, ReservationStatus

            async with AsyncSessionLocal() as session:
                reservation = Reservation(
                    id=str(uuid.uuid4()),
                    shop_id=shop_id,
                    staff_id=staff_id,
                    guest_name="予約太郎",
                    guest_phone="090-9999-9999",
                    reservation_date=datetime.utcnow() + timedelta(days=1),
                    number_of_people=1,
                    status=ReservationStatus.CONFIRMED.value,
                    reservation_source="online",
                )
                session.add(reservation)
                await session.commit()
                reservation_id = reservation.id

            r = await client.delete(f"/api/v1/staff/{staff_id}", headers=owner_a_headers)
            assert r.status_code == 400, f"expected staff deletion to be blocked, got {r.status_code} {r.text}"

            # 13. 割り当て解除 → 予約をキャンセル → 今度は削除できること
            r = await client.delete(f"/api/v1/staff/{staff_id}/services/{service_id}", headers=owner_a_headers)
            assert r.status_code == 200, f"unassign staff service failed: {r.status_code} {r.text}"

            async with AsyncSessionLocal() as session:
                res = await session.get(Reservation, reservation_id)
                res.status = ReservationStatus.CANCELLED.value
                await session.commit()

            r = await client.delete(f"/api/v1/staff/{staff_id}", headers=owner_a_headers)
            assert r.status_code == 200, f"delete staff failed after cancelling reservation: {r.status_code} {r.text}"

            # 14. サービスも削除できること
            r = await client.delete(f"/api/v1/services/{service_id}", headers=owner_a_headers)
            assert r.status_code == 200, f"delete service failed: {r.status_code} {r.text}"

            print("ALL SERVICE/STAFF SMOKE TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
