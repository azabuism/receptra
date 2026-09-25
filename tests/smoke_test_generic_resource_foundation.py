"""
Generic Resource Foundation Phase R1 スモークテスト

背景:

RECEPTRAをレストラン以外の業種（美容室のチェア、整体のベッド、ホテルの
部屋、レンタカーの車両、カラオケルーム、教室など）へ拡張するため、業種を
問わない「予約リソース」(Resource) モデル・CRUD APIを新設した
（Generic Resource Architecture Audit → Phase R1 Audit Report参照）。

Phase R1のスコープはデータ基盤のみ:
- Resource model / CRUD API / tenant isolation / owner認証
のみを対象とし、以下は意図的に一切変更していない（本ファイルでも検証しない）:
- Reservation.resource_id（まだ存在しない）
- availability engine（check_availability / create_reservation等）
- Realtime Tool / Booking Board / Week View
- ShopTable / Staff / Service（既存モデル）
- BusinessType enum

検証項目:
A. Resource作成（capacity指定あり）
B. Resource作成（capacity省略・nullable確認）
C. 一覧取得（display_order → created_at の安定順序）
D. 更新（name/resource_type/capacity/is_active）
E. 不正なresource_typeは400
F. 空nameは400
G. capacity<=0は400
H. is_active切替（無効化→再度有効化）
I. 削除（Phase R1ではReservation依存が無いため無条件で成立）
J. テナント分離（他テナントのGET/POST/PUT/DELETEはすべて403）
K. 店舗スコープの一覧漏洩なし（別店舗のResourceが混ざらない）
L. 存在しないresource_idの更新/削除は404
M. 既存ShopTable CRUDのregression（Resource追加による影響がないこと）
N. 既存Staff CRUDのregression（Resource追加による影響がないこと）

実行: python3 tests/smoke_test_generic_resource_foundation.py
"""

import asyncio
import os
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_generic_resource.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-generic-resource"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402


async def main():
    from app.main import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-resource-r1-a@example.com", "password": "password123",
                "display_name": "GenericResourceテストオーナーA",
            })
            assert r.status_code in (200, 201), f"register A failed: {r.status_code} {r.text}"
            owner_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

            async def _new_shop(name: str):
                r = await client.post("/api/v1/shops/register", json={
                    "name": name, "category": "食堂", "address": "東京都渋谷区9-9-9",
                }, headers=owner_a)
                assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
                return r.json()["shop_id"]

            shop_a = await _new_shop("GenericResourceテストA店")

            # ===== A. Resource作成（capacity指定あり） =====
            r = await client.post(f"/api/v1/shops/{shop_a}/resources", json={
                "name": "個室A", "resource_type": "room", "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 200, f"Resource作成(capacity指定)失敗: {r.status_code} {r.text}"
            body = r.json()
            assert body["name"] == "個室A" and body["resource_type"] == "room" and body["capacity"] == 4
            assert body["is_active"] is True
            room_id = body["id"]
            print("A. Resource作成（capacity指定あり）: OK")

            # ===== B. Resource作成（capacity省略・nullable確認） =====
            r = await client.post(f"/api/v1/shops/{shop_a}/resources", json={
                "name": "レンタカーA", "resource_type": "vehicle",
            }, headers=owner_a)
            assert r.status_code == 200, f"Resource作成(capacity省略)失敗: {r.status_code} {r.text}"
            body = r.json()
            assert body["capacity"] is None, f"capacity省略時にNoneでない: {body['capacity']}"
            vehicle_id = body["id"]
            print("B. Resource作成（capacity省略・nullable確認）: OK")

            # ===== C. 一覧取得（安定順序） =====
            r = await client.get(f"/api/v1/shops/{shop_a}/resources", headers=owner_a)
            assert r.status_code == 200, f"一覧取得失敗: {r.status_code} {r.text}"
            listed = r.json()
            assert [x["id"] for x in listed] == [room_id, vehicle_id], (
                f"一覧の並び順が作成順(display_order)と一致しない: {[x['id'] for x in listed]}"
            )
            print("C. 一覧取得（display_order順の安定順序）: OK")

            # ===== D. 更新 =====
            r = await client.put(f"/api/v1/shops/{shop_a}/resources/{room_id}", json={
                "name": "個室A(改)", "resource_type": "bed", "capacity": 2,
            }, headers=owner_a)
            assert r.status_code == 200, f"更新失敗: {r.status_code} {r.text}"
            body = r.json()
            assert body["name"] == "個室A(改)" and body["resource_type"] == "bed" and body["capacity"] == 2
            print("D. 更新（name/resource_type/capacity）: OK")

            # ===== E. 不正なresource_typeは400 =====
            r = await client.post(f"/api/v1/shops/{shop_a}/resources", json={
                "name": "不正種別", "resource_type": "spaceship",
            }, headers=owner_a)
            assert r.status_code == 422, f"不正なresource_typeが拒否されない: {r.status_code} {r.text}"
            print("E. 不正なresource_typeは拒否される: OK")

            # ===== F. 空nameは400 =====
            r = await client.post(f"/api/v1/shops/{shop_a}/resources", json={
                "name": "", "resource_type": "room",
            }, headers=owner_a)
            assert r.status_code == 422, f"空nameが拒否されない: {r.status_code} {r.text}"
            print("F. 空nameは拒否される: OK")

            # ===== G. capacity<=0は400 =====
            r = await client.post(f"/api/v1/shops/{shop_a}/resources", json={
                "name": "定員ゼロ", "resource_type": "room", "capacity": 0,
            }, headers=owner_a)
            assert r.status_code == 422, f"capacity<=0が拒否されない: {r.status_code} {r.text}"
            print("G. capacity<=0は拒否される: OK")

            # ===== H. is_active切替 =====
            r = await client.put(f"/api/v1/shops/{shop_a}/resources/{room_id}", json={
                "is_active": False,
            }, headers=owner_a)
            assert r.status_code == 200 and r.json()["is_active"] is False, "無効化に失敗"
            r = await client.put(f"/api/v1/shops/{shop_a}/resources/{room_id}", json={
                "is_active": True,
            }, headers=owner_a)
            assert r.status_code == 200 and r.json()["is_active"] is True, "再有効化に失敗"
            print("H. is_active切替（無効化→再有効化）: OK")

            # ===== I. 削除（Phase R1では無条件で成立） =====
            r = await client.delete(f"/api/v1/shops/{shop_a}/resources/{vehicle_id}", headers=owner_a)
            assert r.status_code == 200, f"削除失敗: {r.status_code} {r.text}"
            r = await client.get(f"/api/v1/shops/{shop_a}/resources", headers=owner_a)
            assert vehicle_id not in [x["id"] for x in r.json()], "削除後も一覧に残っている"
            print("I. 削除（Reservation依存が無いため無条件で成立）: OK")

            # ===== J. テナント分離 =====
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-resource-r1-b@example.com", "password": "password123",
                "display_name": "GenericResourceテスト他テナントオーナー",
            })
            assert r.status_code in (200, 201)
            owner_b = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.get(f"/api/v1/shops/{shop_a}/resources", headers=owner_b)
            assert r.status_code == 403, f"他テナントがGETできてしまった: {r.status_code} {r.text}"
            r = await client.post(f"/api/v1/shops/{shop_a}/resources", json={
                "name": "侵入テスト", "resource_type": "room",
            }, headers=owner_b)
            assert r.status_code == 403, f"他テナントがPOSTできてしまった: {r.status_code} {r.text}"
            r = await client.put(f"/api/v1/shops/{shop_a}/resources/{room_id}", json={
                "name": "改ざん",
            }, headers=owner_b)
            assert r.status_code == 403, f"他テナントがPUTできてしまった: {r.status_code} {r.text}"
            r = await client.delete(f"/api/v1/shops/{shop_a}/resources/{room_id}", headers=owner_b)
            assert r.status_code == 403, f"他テナントがDELETEできてしまった: {r.status_code} {r.text}"
            print("J. テナント分離（他テナントのGET/POST/PUT/DELETEはすべて403）: OK")

            # ===== K. 店舗スコープの一覧漏洩なし =====
            shop_b_for_a = await _new_shop("GenericResourceテストA店-2号店")
            r = await client.post(f"/api/v1/shops/{shop_b_for_a}/resources", json={
                "name": "2号店の個室", "resource_type": "room",
            }, headers=owner_a)
            assert r.status_code == 200
            r = await client.get(f"/api/v1/shops/{shop_a}/resources", headers=owner_a)
            names = [x["name"] for x in r.json()]
            assert "2号店の個室" not in names, "別店舗のResourceが一覧に混入している"
            print("K. 店舗スコープの一覧漏洩なし: OK")

            # ===== L. 存在しないresource_idは404 =====
            fake_id = str(uuid.uuid4())
            r = await client.put(f"/api/v1/shops/{shop_a}/resources/{fake_id}", json={"name": "x"}, headers=owner_a)
            assert r.status_code == 404, f"存在しないresource_idのPUTが404でない: {r.status_code}"
            r = await client.delete(f"/api/v1/shops/{shop_a}/resources/{fake_id}", headers=owner_a)
            assert r.status_code == 404, f"存在しないresource_idのDELETEが404でない: {r.status_code}"
            print("L. 存在しないresource_idの更新/削除は404: OK")

            # ===== M. 既存ShopTable CRUDのregression =====
            r = await client.post(f"/api/v1/shops/{shop_a}/tables", json={
                "name": "テーブルA", "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 200, f"ShopTable作成のregression: {r.status_code} {r.text}"
            table_id = r.json()["id"]
            # Phase W1: GET一覧も_get_owned_shop()で認証必須化されたため
            # （他テナントへのtable_id漏洩防止）、owner_aのトークンを付与する。
            # 未認証アクセスが拒否されること自体はPhase W1側の新規テスト
            # （smoke_test_public_shop_page_web_booking.pyのC項目）で検証済み。
            r = await client.get(f"/api/v1/shops/{shop_a}/tables", headers=owner_a)
            assert r.status_code == 200 and any(t["id"] == table_id for t in r.json()), "ShopTable一覧のregression"
            r = await client.delete(f"/api/v1/shops/{shop_a}/tables/{table_id}", headers=owner_a)
            assert r.status_code == 200, f"ShopTable削除のregression: {r.status_code} {r.text}"
            print("M. 既存ShopTable CRUDのregression: OK")

            # ===== N. 既存Staff CRUDのregression =====
            r = await client.post("/api/v1/staff", json={
                "shop_id": shop_a, "name": "スタッフA",
            }, headers=owner_a)
            assert r.status_code == 200, f"Staff作成のregression: {r.status_code} {r.text}"
            staff_id = r.json()["id"]
            r = await client.get("/api/v1/staff?shop_id=" + shop_a)
            assert r.status_code == 200 and any(s["id"] == staff_id for s in r.json()), "Staff一覧のregression"
            r = await client.delete(f"/api/v1/staff/{staff_id}", headers=owner_a)
            assert r.status_code == 200, f"Staff削除のregression: {r.status_code} {r.text}"
            print("N. 既存Staff CRUDのregression: OK")

            print("\n=== Generic Resource Foundation Phase R1 スモークテスト: 全項目OK ===")


if __name__ == "__main__":
    asyncio.run(main())
