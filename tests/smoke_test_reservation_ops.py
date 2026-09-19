"""
予約管理・運用機能（キャンセル理由、無断キャンセル、日時変更）のスモークテスト
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_reservation_ops.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key"

import httpx  # noqa: E402


async def main():
    from app.main import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-resops-smoke@example.com",
                "password": "password123",
                "display_name": "予約運用テストオーナー",
            })
            assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
            owner_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "予約運用テスト食堂", "category": "ラーメン", "address": "東京都港区1-1-1",
            }, headers=owner_headers)
            assert r.status_code in (200, 201)
            shop_id = r.json()["shop_id"]

            future_date = (datetime.utcnow() + timedelta(days=5)).replace(microsecond=0)
            r = await client.post("/api/v1/reservations/create", json={
                "shop_id": shop_id,
                "guest_name": "運用太郎",
                "guest_phone": "090-1111-0000",
                "reservation_date": future_date.isoformat(),
                "number_of_people": 2,
            })
            assert r.status_code in (200, 201), f"create reservation failed: {r.status_code} {r.text}"
            reservation_id = r.json()["reservation_id"]

            # 1. 他人のオーナーは操作できないこと
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-resops-other@example.com", "password": "password123", "display_name": "別オーナー",
            })
            other_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
            r = await client.put(f"/api/v1/reservations/{reservation_id}", json={"status": "confirmed"}, headers=other_headers)
            assert r.status_code == 403, f"expected 403 for non-owner update, got {r.status_code} {r.text}"

            # 2. キャンセル理由付きでキャンセル
            r = await client.put(f"/api/v1/reservations/{reservation_id}", json={
                "status": "cancelled", "cancellation_reason": "お客様都合によるキャンセル",
            }, headers=owner_headers)
            assert r.status_code == 200, f"cancel with reason failed: {r.status_code} {r.text}"
            data = r.json()
            assert data["status"] == "cancelled"
            assert data["cancellation_reason"] == "お客様都合によるキャンセル"
            assert data["cancelled_at"] is not None

            # 3. 別の予約を無断キャンセル(no_show)にする
            r = await client.post("/api/v1/reservations/create", json={
                "shop_id": shop_id,
                "guest_name": "運用次郎",
                "guest_phone": "090-2222-0000",
                "reservation_date": future_date.isoformat(),
                "number_of_people": 1,
            })
            reservation_id2 = r.json()["reservation_id"]

            r = await client.put(f"/api/v1/reservations/{reservation_id2}", json={"status": "no_show"}, headers=owner_headers)
            assert r.status_code == 200, f"no_show update failed: {r.status_code} {r.text}"
            assert r.json()["status"] == "no_show"

            # 4. 日時の変更（未来の日時ならOK）
            new_date = (future_date + timedelta(days=2)).isoformat()
            r = await client.put(f"/api/v1/reservations/{reservation_id2}", json={"reservation_date": new_date}, headers=owner_headers)
            assert r.status_code == 200, f"reschedule failed: {r.status_code} {r.text}"
            assert r.json()["reservation_date"].startswith(new_date[:16]), r.json()["reservation_date"]

            # 5. 過去の日時への変更は拒否されること
            past_date = (datetime.utcnow() - timedelta(days=1)).isoformat()
            r = await client.put(f"/api/v1/reservations/{reservation_id2}", json={"reservation_date": past_date}, headers=owner_headers)
            assert r.status_code == 400, f"expected 400 for past reschedule, got {r.status_code} {r.text}"

            print("ALL RESERVATION OPS SMOKE TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
