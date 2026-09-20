"""
Phase3E-1 (Staff Foundation) スモークテスト

検証項目:
1. 新規スタッフ作成時に display_name / nomination_allowed / sort_order が保存される
2. 未指定時のデフォルト値（display_name=None→nameにフォールバックはクライアント側の責務、
   nomination_allowed=true、sort_order=0）
3. 更新（PUT）で display_name / nomination_allowed / sort_order を個別に更新できる
4. 公開エンドポイント（GET /api/v1/staff, GET /api/v1/staff/{id}）のレスポンスに
   email/phone が含まれないこと（Phase3E-1のセキュリティ修正）
5. オーナー認証エンドポイント（POST/PUT）のレスポンスには email/phone が引き続き含まれること
6. 旧クライアント互換性：display_name/nomination_allowed/sort_orderを一切送らない
   POSTリクエストでも従来通り成功すること
7. 他テナントのスタッフをIDだけで更新・削除できないこと（IDOR）
"""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_phase3e1.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-3e1"

import httpx  # noqa: E402


async def main():
    from app.main import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # オーナーA
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-a-3e1@example.com",
                "password": "password123",
                "display_name": "オーナーA",
            })
            assert r.status_code in (200, 201), f"register A failed: {r.status_code} {r.text}"
            owner_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "3E1テスト店舗A", "category": "ヘアサロン", "address": "東京都渋谷区1-1-1",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create shop A failed: {r.status_code} {r.text}"
            shop_a = r.json()["shop_id"]

            # オーナーB（別テナント、IDOR検証用）
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-b-3e1@example.com",
                "password": "password123",
                "display_name": "オーナーB",
            })
            assert r.status_code in (200, 201), f"register B failed: {r.status_code} {r.text}"
            owner_b = {"Authorization": f"Bearer {r.json()['access_token']}"}

            # 1. 新フィールドを指定してスタッフ作成
            r = await client.post("/api/v1/staff", json={
                "shop_id": shop_a,
                "name": "山田花子",
                "display_name": "はなこ先生",
                "email": "hanako@example.com",
                "phone": "090-1234-5678",
                "nomination_allowed": False,
                "sort_order": 5,
            }, headers=owner_a)
            assert r.status_code == 200, f"create staff failed: {r.status_code} {r.text}"
            created = r.json()
            staff_id = created["id"]
            assert created["display_name"] == "はなこ先生", created
            assert created["nomination_allowed"] is False, created
            assert created["sort_order"] == 5, created
            assert created["email"] == "hanako@example.com", "owner response must keep email"
            assert created["phone"] == "090-1234-5678", "owner response must keep phone"

            # 2. デフォルト値の確認（新フィールド未指定）
            r = await client.post("/api/v1/staff", json={
                "shop_id": shop_a,
                "name": "旧クライアント太郎",
            }, headers=owner_a)
            assert r.status_code == 200, f"legacy-shaped create failed: {r.status_code} {r.text}"
            legacy_created = r.json()
            legacy_staff_id = legacy_created["id"]
            assert legacy_created["display_name"] is None, legacy_created
            assert legacy_created["nomination_allowed"] is True, "default must be true (no behavior change)"
            assert legacy_created["sort_order"] == 0, legacy_created

            # 3. 更新（PUT）で新フィールドを個別更新
            r = await client.put(f"/api/v1/staff/{staff_id}", json={
                "sort_order": 10,
            }, headers=owner_a)
            assert r.status_code == 200, f"update sort_order failed: {r.status_code} {r.text}"
            updated = r.json()
            assert updated["sort_order"] == 10, updated
            assert updated["nomination_allowed"] is False, "untouched field must be preserved"
            assert updated["display_name"] == "はなこ先生", "untouched field must be preserved"

            r = await client.put(f"/api/v1/staff/{staff_id}", json={
                "nomination_allowed": True,
                "display_name": "花子",
            }, headers=owner_a)
            assert r.status_code == 200, f"update nomination/display_name failed: {r.status_code} {r.text}"
            updated2 = r.json()
            assert updated2["nomination_allowed"] is True, updated2
            assert updated2["display_name"] == "花子", updated2

            # 4. 公開エンドポイント：email/phoneが含まれないこと
            r = await client.get(f"/api/v1/staff?shop_id={shop_a}")
            assert r.status_code == 200, f"public list failed: {r.status_code} {r.text}"
            public_list = r.json()
            assert len(public_list) == 2, public_list
            for item in public_list:
                assert "email" not in item, f"email leaked in public list: {item}"
                assert "phone" not in item, f"phone leaked in public list: {item}"
            # display_name/nomination_allowed/sort_orderは公開レスポンスにも含まれる
            hanako_public = next(x for x in public_list if x["id"] == staff_id)
            assert hanako_public["display_name"] == "花子", hanako_public
            assert hanako_public["nomination_allowed"] is True, hanako_public
            assert hanako_public["sort_order"] == 10, hanako_public

            r = await client.get(f"/api/v1/staff/{staff_id}")
            assert r.status_code == 200, f"public detail failed: {r.status_code} {r.text}"
            public_detail = r.json()
            assert "email" not in public_detail, f"email leaked in public detail: {public_detail}"
            assert "phone" not in public_detail, f"phone leaked in public detail: {public_detail}"

            # 5. オーナー操作（PUT）のレスポンスには引き続きemail/phoneが含まれる
            r = await client.put(f"/api/v1/staff/{staff_id}", json={"bio": "更新テスト"}, headers=owner_a)
            assert r.status_code == 200, f"update bio failed: {r.status_code} {r.text}"
            owner_view = r.json()
            assert owner_view["email"] == "hanako@example.com", "owner PUT response must keep email"
            assert owner_view["phone"] == "090-1234-5678", "owner PUT response must keep phone"

            # 6. 旧クライアント互換性：新フィールド未送信でも従来通り成功
            r = await client.put(f"/api/v1/staff/{legacy_staff_id}", json={
                "position": "見習い",
            }, headers=owner_a)
            assert r.status_code == 200, f"legacy-shaped update failed: {r.status_code} {r.text}"
            legacy_updated = r.json()
            assert legacy_updated["position"] == "見習い", legacy_updated
            assert legacy_updated["nomination_allowed"] is True, "must remain default"
            assert legacy_updated["sort_order"] == 0, "must remain default"

            # 7. IDOR: オーナーBは shop_a のスタッフを更新・削除できないこと
            r = await client.put(f"/api/v1/staff/{staff_id}", json={"name": "乗っ取り"}, headers=owner_b)
            assert r.status_code == 403, f"expected 403 for cross-tenant update, got {r.status_code} {r.text}"

            r = await client.delete(f"/api/v1/staff/{staff_id}", headers=owner_b)
            assert r.status_code == 403, f"expected 403 for cross-tenant delete, got {r.status_code} {r.text}"

            # 後片付け（今回作成したスタッフを削除できること）
            r = await client.delete(f"/api/v1/staff/{staff_id}", headers=owner_a)
            assert r.status_code == 200, f"cleanup delete (staff_id) failed: {r.status_code} {r.text}"
            r = await client.delete(f"/api/v1/staff/{legacy_staff_id}", headers=owner_a)
            assert r.status_code == 200, f"cleanup delete (legacy_staff_id) failed: {r.status_code} {r.text}"

            print("ALL PHASE3E-1 STAFF FOUNDATION SMOKE TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
