"""
マイページ・口コミ返信機能のスモークテスト
一時的な SQLite DB + httpx.AsyncClient で、実際に近い形で一連のフローを検証する。
"""

import asyncio
import os
import sys
import tempfile

# リポジトリのルートを import パスに追加する（tests/ から直接実行できるように）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key"

import httpx  # noqa: E402


async def main():
    from app.main import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. オーナー登録 → ログイン
            owner_email = "owner-smoke@example.com"
            r = await client.post("/api/v1/auth/register", json={
                "email": owner_email,
                "password": "password123",
                "display_name": "オーナー太郎",
            })
            assert r.status_code in (200, 201), f"register(owner) failed: {r.status_code} {r.text}"
            owner_token = r.json()["access_token"]
            owner_headers = {"Authorization": f"Bearer {owner_token}"}

            # 2. 店舗作成
            r = await client.post("/api/v1/shops/register", json={
                "name": "スモークテスト食堂",
                "category": "ラーメン",
                "address": "東京都渋谷区1-1-1",
            }, headers=owner_headers)
            assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
            shop_id = r.json()["shop_id"]

            # 3. カスタマー（プラットフォームユーザー）登録 → ログイン
            customer_email = "customer-smoke@example.com"
            r = await client.post("/api/v1/auth/register", json={
                "email": customer_email,
                "password": "password123",
                "display_name": "テスト花子",
            })
            assert r.status_code in (200, 201), f"register(customer) failed: {r.status_code} {r.text}"
            customer_token = r.json()["access_token"]
            customer_headers = {"Authorization": f"Bearer {customer_token}"}

            # 4. ログイン中のユーザーとして予約を作成（user_id が紐付くことを確認）
            r = await client.post("/api/v1/reservations/create", json={
                "shop_id": shop_id,
                "guest_name": "テスト花子",
                "guest_phone": "090-1234-5678",
                "reservation_date": "2099-01-01T12:00:00",
                "number_of_people": 2,
            }, headers=customer_headers)
            assert r.status_code in (200, 201), f"create reservation failed: {r.status_code} {r.text}"

            # 4b. 未ログインでもゲスト予約が引き続き作成できること（後方互換）
            r = await client.post("/api/v1/reservations/create", json={
                "shop_id": shop_id,
                "guest_name": "ゲスト太郎",
                "guest_phone": "090-0000-0000",
                "reservation_date": "2099-01-02T12:00:00",
                "number_of_people": 1,
            })
            assert r.status_code in (200, 201), f"guest reservation failed: {r.status_code} {r.text}"

            # 5. 行きたい店・いいねをトグル
            r = await client.post("/api/v1/mypage/relations/toggle", json={
                "shop_id": shop_id, "relation_type": "want_to_go",
            }, headers=customer_headers)
            assert r.status_code == 200, f"toggle want_to_go failed: {r.status_code} {r.text}"
            assert r.json()["active"] is True

            r = await client.post("/api/v1/mypage/relations/toggle", json={
                "shop_id": shop_id, "relation_type": "like",
            }, headers=customer_headers)
            assert r.status_code == 200, f"toggle like failed: {r.status_code} {r.text}"
            assert r.json()["active"] is True

            r = await client.get(f"/api/v1/mypage/relations/{shop_id}", headers=customer_headers)
            assert r.status_code == 200
            assert r.json() == {"shop_id": shop_id, "want_to_go": True, "like": True}

            # 6. レビュー投稿
            r = await client.post("/api/v1/reviews", json={
                "shop_id": shop_id,
                "title": "美味しかったです",
                "comment": "スープが最高でした！",
                "overall_rating": 4.5,
            }, headers=customer_headers)
            assert r.status_code == 200, f"create review failed: {r.status_code} {r.text}"
            review_id = r.json()["id"]
            assert r.json()["reviewer_name"] == "テスト花子"

            # 7. 店舗のレビュー一覧を取得（公開）
            r = await client.get(f"/api/v1/reviews/shop/{shop_id}")
            assert r.status_code == 200
            assert len(r.json()) == 1

            # 8. オーナーが返信
            r = await client.put(f"/api/v1/reviews/{review_id}/reply", json={
                "shop_response": "ご来店ありがとうございました！またお待ちしております。",
            }, headers=owner_headers)
            assert r.status_code == 200, f"reply failed: {r.status_code} {r.text}"
            assert r.json()["shop_response"] is not None

            # 8b. 他人（カスタマー自身）は返信できないこと
            r = await client.put(f"/api/v1/reviews/{review_id}/reply", json={
                "shop_response": "なりすまし返信",
            }, headers=customer_headers)
            assert r.status_code == 403, f"expected 403 for non-owner reply, got {r.status_code}"

            # 9. オーナーがクーポン作成
            r = await client.post(f"/api/v1/shops/{shop_id}/coupons", json={
                "code": "SMOKE10",
                "description": "スモークテスト用10%オフ",
                "discount_type": "percentage",
                "discount_value": 10,
                "start_date": "2020-01-01T00:00:00",
                "end_date": "2099-12-31T00:00:00",
            }, headers=owner_headers)
            assert r.status_code == 200, f"create coupon failed: {r.status_code} {r.text}"

            # 10. マイページサマリー
            r = await client.get("/api/v1/mypage/summary", headers=customer_headers)
            assert r.status_code == 200, f"mypage summary failed: {r.status_code} {r.text}"
            summary = r.json()
            assert len(summary["frequent_shops"]) == 1, summary
            assert summary["frequent_shops"][0]["visit_count"] == 1
            assert len(summary["want_to_go_shops"]) == 1
            assert len(summary["liked_shops"]) == 1
            assert summary["like_count"] == 1
            assert len(summary["my_reviews"]) == 1
            assert summary["my_reviews"][0]["shop_response"] is not None
            assert len(summary["available_coupons"]) == 1
            assert summary["available_coupons"][0]["code"] == "SMOKE10"


            # 11. ログイン確認（既存の /auth/login のバグ修正検証）
            r = await client.post("/api/v1/auth/login", json={
                "email": customer_email, "password": "password123",
            })
            assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"


            # 12. 店舗削除（いいね・行きたい店・クーポン・レビュー・予約が残っていても削除できること）
            r = await client.delete(f"/api/v1/shops/{shop_id}", headers=owner_headers)
            assert r.status_code == 200, f"delete shop with related rows failed: {r.status_code} {r.text}"

            print("ALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
