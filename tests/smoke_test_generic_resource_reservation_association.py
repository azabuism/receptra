"""
Generic Resource Foundation Phase R2 スモークテスト
（Reservation ↔ Resource Association Foundation）

背景:

Phase R1でResourceモデル・CRUD APIを新設した（データ基盤のみ、Reservationとの
関連は一切無し）。Phase R2では、ReservationがどのResourceへ割り当てられているかを
「安全に保存・取得できる」データ層の関連付け基盤のみを追加した
（Reservation.resource_id nullable FK、CHECK制約なし=Option B1採用）。

Phase R2のスコープは意図的に以下を含まない（本ファイルでも検証しない）:
- availability engine（check_availability/create_reservation/get_availability）
  によるresource_idの自動割当・空き判定への統合
- Owner/Customer/Realtime AIがresource_idを指定できる書き込みAPI
  （本ファイルのSection Aで「そのようなAPIが存在しないこと」自体を検証する）
- update_reservation()でのresource_id再割当
- Booking Board（Resource Lane）・Week Viewへの表示
  （本ファイルのSection J/Kで「変更されていないこと」自体を検証する）

検証項目:
A. ReservationCreateRequest/ReservationUpdateRequestにresource_idフィールドが
   存在しない（外部書き込みAPIへ一切公開されていないことのスキーマレベル保証）
B. 既存予約（通常のcreate_reservation経由、テーブル予約）の後方互換性:
   詳細取得でresource_id/resource_nameが共にNoneで安全に返る
C. 一覧取得でも同様にresource_id=None、かつeager-loading追加によるエラーが
   発生しないこと
D. ORM経由でresource_idを設定した予約: 詳細取得でresource_id/resource_nameが
   正しく返る
E. resource_id設定済みの予約が混在していても一覧取得が正常応答する
F. 削除安全性: 今後の有効な予約(pending)が割り当てられたResourceの削除は
   ブロックされる
G. 同じ予約をcancelledにすると削除できるようになる
H. 過去日時の予約（statusはpendingのまま）が割り当てられたResourceは
   削除をブロックしない（既存ShopTable/Staffと同じ「今後の」定義）
I. Resource CRUDのテナント分離（Phase R1からの継続的な健全性の再確認）
J. Board API回帰: BoardReservationItemにresource関連フィールドが一切
   含まれない（Board未変更の確認）
K. Week API回帰: WeekReservationBlockにstaff_id/table_id/resource関連
   フィールドが一切含まれない（Week View未変更の確認、PII最小化維持）
L. 既存ShopTable/Staff CRUDのregression
M. 将来のStaff+Resource同時割当に向けた確認: DBレベルでstaff_idと
   resource_idを同一予約行に同時設定できる（CHECK制約が存在しないこと）

実行: python3 tests/smoke_test_generic_resource_reservation_association.py
"""

import asyncio
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_generic_resource_r2.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-generic-resource-r2"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402

_phone_counter = 0


def _next_phone() -> str:
    global _phone_counter
    _phone_counter += 1
    return f"080{_phone_counter:08d}"


async def main():
    from app.main import app, lifespan
    from app.schemas.reservation import ReservationCreateRequest, ReservationUpdateRequest

    # ===== A. 外部書き込みAPIにresource_idが公開されていないこと =====
    create_fields = set(ReservationCreateRequest.model_fields.keys())
    update_fields = set(ReservationUpdateRequest.model_fields.keys())
    assert "resource_id" not in create_fields, "ReservationCreateRequestにresource_idが公開されている（Section48違反）"
    assert "resource_id" not in update_fields, "ReservationUpdateRequestにresource_idが公開されている（Section48違反）"
    print("A. ReservationCreateRequest/ReservationUpdateRequestにresource_idが存在しない: OK")

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            from app.database import AsyncSessionLocal
            from app.models.shop import Shop
            from app.models.reservation import Reservation

            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-resource-r2-a@example.com", "password": "password123",
                "display_name": "GenericResourceR2テストオーナーA",
            })
            assert r.status_code in (200, 201), f"register A failed: {r.status_code} {r.text}"
            owner_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

            async def _new_shop(name: str):
                r = await client.post("/api/v1/shops/register", json={
                    "name": name, "category": "食堂", "address": "東京都渋谷区9-9-9",
                }, headers=owner_a)
                assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
                shop_id = r.json()["shop_id"]

                hours_payload = {"hours": [
                    {"day_of_week": d, "opening_time": "10:00:00", "closing_time": "22:00:00", "is_closed": False}
                    for d in range(7)
                ]}
                r = await client.put(f"/api/v1/shops/{shop_id}/hours", json=hours_payload, headers=owner_a)
                assert r.status_code == 200, f"set hours failed: {r.status_code} {r.text}"

                async with AsyncSessionLocal() as session:
                    shop_obj = await session.get(Shop, shop_id)
                    shop_obj.reservations_enabled = True
                    shop_obj.reservation_duration_minutes = 60
                    await session.commit()
                return shop_id

            shop_a = await _new_shop("GenericResourceR2テストA店")

            # ★★★ Phase R3対応: このshop_aは元々「既存の飲食店（ShopTable運用）」を
            # 想定したテストシナリオであり、B/C節は「Resource未割当の予約は
            # resource_id=Noneのまま」という後方互換性を検証する。
            # Phase R3で_resolve_reservation_allocation_mode()が導入され、
            # 「ShopTableが0件かつactive Resourceが1件以上」という条件が
            # 新たにResource pathへ自動的にルーティングされるようになったため、
            # 何もしなければ以下で作るResourceがこの店舗のplain reservationに
            # 自動割当されてしまい、このB/C節の前提が崩れる（R3新規動作としては
            # 正しいが、この節の検証意図とは無関係な副作用）。
            # ShopTableを1件登録しておくことで、ルーティングルール#1
            # （ShopTable存在時は常にtable path。行のis_activeは問わない）
            # によりshop_aをTable pathへ明示的に固定する。
            # ただし、このテストのB～E節はもともと「テーブル管理なし
            # （unmanaged、常に空席扱い）」の店舗を前提に、同一時間帯へ
            # 複数の予約を作成して検証している（Phase R2時点ではshop_aに
            # ShopTableが0件で、_find_available_table()のunmanagedバイパス
            # により常に成功していた）。もしここでactiveなShopTableを
            # 残すと、_find_available_table()が「管理下のテーブルが1件ある」
            # と判定して本来のテーブル重複チェックが働いてしまい、B～E節が
            # 想定していない"満席"エラーで壊れてしまう。
            # そこで作成直後にis_active=Falseへ更新し、「ルーティング判定
            # （ルール#1: 行の存在有無のみを見る）はTable pathに固定されるが、
            # 実際の空席判定（_find_available_table()内のis_active==True件数）
            # は0件のまま＝unmanagedバイパス」という、Phase R2時点の
            # shop_aの実質的な挙動（常に空席扱い）を完全に保ったまま、
            # R3の新しいルーティングだけを意図通りに固定する。
            r = await client.post(f"/api/v1/shops/{shop_a}/tables", json={
                "name": "既存テーブル(非アクティブ・ルーティング固定用)", "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 200, f"ShopTable作成失敗（R3ルーティング固定用）: {r.status_code} {r.text}"
            _routing_pin_table_id = r.json()["id"]
            r = await client.put(f"/api/v1/shops/{shop_a}/tables/{_routing_pin_table_id}", json={
                "is_active": False,
            }, headers=owner_a)
            assert r.status_code == 200, f"ShopTable非アクティブ化失敗（R3ルーティング固定用）: {r.status_code} {r.text}"

            # Resourceを1つ用意（Phase R1のCRUD APIをそのまま使用）
            r = await client.post(f"/api/v1/shops/{shop_a}/resources", json={
                "name": "個室A", "resource_type": "room", "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 200, f"Resource作成失敗: {r.status_code} {r.text}"
            resource_id = r.json()["id"]

            # 営業時間(10:00-22:00)内に収まる固定の時刻にする（現在時刻の時分に依存しない）。
            future_date = (datetime.utcnow() + timedelta(days=5)).replace(
                hour=14, minute=0, second=0, microsecond=0
            )

            # ===== B/C. 既存予約（Resource未割当）の後方互換性 =====
            r = await client.post("/api/v1/reservations/create", json={
                "shop_id": shop_a,
                "guest_name": "R2互換太郎",
                "guest_phone": _next_phone(),
                "reservation_date": future_date.isoformat(),
                "number_of_people": 2,
            })
            assert r.status_code in (200, 201), f"create reservation失敗: {r.status_code} {r.text}"
            plain_reservation_id = r.json()["reservation_id"]
            plain_body = r.json()["reservation"]
            assert plain_body["resource_id"] is None and plain_body["resource_name"] is None, (
                f"Resource未割当の予約でresource_id/resource_nameがNoneでない: {plain_body}"
            )

            r = await client.get(f"/api/v1/reservations/{plain_reservation_id}", headers=owner_a)
            assert r.status_code == 200, f"詳細取得失敗: {r.status_code} {r.text}"
            assert r.json()["resource_id"] is None and r.json()["resource_name"] is None
            print("B. 既存予約（Resource未割当）の後方互換性（resource_id/resource_nameはNone）: OK")

            r = await client.get(f"/api/v1/reservations/shop/{shop_a}", headers=owner_a)
            assert r.status_code == 200, f"一覧取得失敗（eager-loading追加によるエラーの可能性）: {r.status_code} {r.text}"
            items = r.json()["items"]
            assert any(i["id"] == plain_reservation_id and i["resource_id"] is None for i in items)
            print("C. 一覧取得でもresource_id=Noneで正常応答（N+1/lazy-loadエラー無し）: OK")

            # ===== D/E. ORM経由でresource_idを設定した予約 =====
            r = await client.post("/api/v1/reservations/create", json={
                "shop_id": shop_a,
                "guest_name": "R2紐付け花子",
                "guest_phone": _next_phone(),
                "reservation_date": future_date.isoformat(),
                "number_of_people": 2,
            })
            assert r.status_code in (200, 201), f"create reservation失敗: {r.status_code} {r.text}"
            linked_reservation_id = r.json()["reservation_id"]

            async with AsyncSessionLocal() as session:
                res = await session.get(Reservation, linked_reservation_id)
                res.resource_id = resource_id
                await session.commit()

            r = await client.get(f"/api/v1/reservations/{linked_reservation_id}", headers=owner_a)
            assert r.status_code == 200, f"詳細取得失敗: {r.status_code} {r.text}"
            body = r.json()
            assert body["resource_id"] == resource_id, f"resource_idが正しく返らない: {body}"
            assert body["resource_name"] == "個室A", f"resource_nameが正しく返らない: {body}"
            print("D. ORM経由でresource_idを設定した予約: 詳細取得でresource_id/resource_nameが正しく返る: OK")

            r = await client.get(f"/api/v1/reservations/shop/{shop_a}", headers=owner_a)
            assert r.status_code == 200, f"一覧取得失敗: {r.status_code} {r.text}"
            items = r.json()["items"]
            linked_item = next(i for i in items if i["id"] == linked_reservation_id)
            assert linked_item["resource_id"] == resource_id and linked_item["resource_name"] == "個室A"
            print("E. resource_id設定済みの予約が混在していても一覧取得が正常応答: OK")

            # ===== F/G. 削除安全性（今後の有効な予約） =====
            r = await client.delete(f"/api/v1/shops/{shop_a}/resources/{resource_id}", headers=owner_a)
            assert r.status_code == 400, f"今後の有効な予約があるにも関わらず削除できてしまった: {r.status_code} {r.text}"
            print("F. 今後の有効な予約(pending)が割り当てられたResourceの削除はブロックされる: OK")

            r = await client.put(f"/api/v1/reservations/{linked_reservation_id}", json={
                "status": "cancelled", "cancellation_reason": "テストのためキャンセル",
            }, headers=owner_a)
            assert r.status_code == 200, f"キャンセル失敗: {r.status_code} {r.text}"

            r = await client.delete(f"/api/v1/shops/{shop_a}/resources/{resource_id}", headers=owner_a)
            assert r.status_code == 200, f"cancelled化後も削除できない: {r.status_code} {r.text}"
            print("G. 予約をcancelledにすると削除できるようになる: OK")

            # ===== H. 過去日時の予約は削除をブロックしない =====
            r = await client.post(f"/api/v1/shops/{shop_a}/resources", json={
                "name": "個室B", "resource_type": "room", "capacity": 2,
            }, headers=owner_a)
            assert r.status_code == 200
            resource_b_id = r.json()["id"]

            past_reservation_id = str(uuid.uuid4())
            async with AsyncSessionLocal() as session:
                past_reservation = Reservation(
                    id=past_reservation_id,
                    shop_id=shop_a,
                    guest_name="過去予約太郎",
                    guest_phone=_next_phone(),
                    reservation_date=datetime.utcnow() - timedelta(days=3),
                    number_of_people=1,
                    status="pending",
                    resource_id=resource_b_id,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                session.add(past_reservation)
                await session.commit()

            r = await client.delete(f"/api/v1/shops/{shop_a}/resources/{resource_b_id}", headers=owner_a)
            assert r.status_code == 200, f"過去予約のみのResourceが削除できない: {r.status_code} {r.text}"
            print("H. 過去日時の予約(status=pending)が割り当てられたResourceは削除をブロックしない: OK")

            # ===== I. テナント分離の再確認 =====
            r = await client.post("/api/v1/shops/{}/resources".format(shop_a), json={
                "name": "個室C", "resource_type": "room",
            }, headers=owner_a)
            assert r.status_code == 200
            resource_c_id = r.json()["id"]

            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-resource-r2-b@example.com", "password": "password123",
                "display_name": "GenericResourceR2テスト他テナント",
            })
            owner_b = {"Authorization": f"Bearer {r.json()['access_token']}"}
            r = await client.delete(f"/api/v1/shops/{shop_a}/resources/{resource_c_id}", headers=owner_b)
            assert r.status_code == 403, f"他テナントが削除できてしまった: {r.status_code} {r.text}"
            print("I. Resource CRUDのテナント分離（継続的な健全性の再確認）: OK")

            # ===== J. Board API回帰 =====
            # Phase R2〜R4当時はBoard（Booking Board）にresource関連フィールドを
            # 一切含めない方針だった（当時のSection21）。Phase R5 Part Aで
            # Resource Lane（設備・部屋別レーン）をBoardへ正式に統合したため、
            # この方針は意図的に変更された——BoardReservationItemには
            # resource_id/resource_nameが常にキーとして存在するようになった
            # （staff_id/table_idと全く同じ「割当が無ければNone」という既存
            # 規約に合わせた追加専用フィールド。値そのものの正しさはこのファイル
            # の他セクション、および tests/smoke_test_booking_board_resource_lane.py
            # で別途検証済みのため、ここでは「キーが存在すること」のみを
            # regressionとして確認する）。
            r = await client.get(f"/api/v1/reservations/shop/{shop_a}/board", params={
                "date": future_date.date().isoformat(),
            }, headers=owner_a)
            assert r.status_code == 200, f"Board取得失敗: {r.status_code} {r.text}"
            board_body = r.json()
            assert "resource_roster" in board_body, (
                "Phase R5 Part AでBoardResponseにresource_rosterが追加されているはずが見つからない"
            )
            for item in board_body["reservations"]:
                assert "resource_id" in item and "resource_name" in item, (
                    "Phase R5 Part AでBoardReservationItemにresource_id/resource_nameが"
                    "追加されているはずが見つからない"
                )
            print("J. Board API: Phase R5 Part Aで追加されたresource_id/resource_name/resource_rosterが"
                  "正しく存在する（後方互換の追加専用フィールド）: OK")

            # ===== K. Week API回帰: staff_id/table_id/resource系フィールド不在の確認 =====
            monday = future_date.date() - timedelta(days=future_date.date().weekday())
            r = await client.get(f"/api/v1/reservations/shop/{shop_a}/week", params={
                "start_date": monday.isoformat(),
            }, headers=owner_a)
            assert r.status_code == 200, f"Week取得失敗: {r.status_code} {r.text}"
            week_body = r.json()
            for day in week_body["days"]:
                for block in day["reservations"]:
                    for forbidden in ("staff_id", "table_id", "resource_id", "resource_name"):
                        assert forbidden not in block, (
                            f"WeekReservationBlockに{forbidden}が混入している（Section22/PII最小化違反）"
                        )
            print("K. Week API回帰: staff_id/table_id/resource系フィールドが一切含まれない（PII最小化維持）: OK")

            # ===== L. 既存ShopTable/Staff CRUDのregression =====
            r = await client.post(f"/api/v1/shops/{shop_a}/tables", json={
                "name": "テーブルA", "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 200, f"ShopTable作成のregression: {r.status_code} {r.text}"
            table_id = r.json()["id"]
            r = await client.delete(f"/api/v1/shops/{shop_a}/tables/{table_id}", headers=owner_a)
            assert r.status_code == 200, f"ShopTable削除のregression: {r.status_code} {r.text}"

            r = await client.post("/api/v1/staff", json={
                "shop_id": shop_a, "name": "スタッフA",
            }, headers=owner_a)
            assert r.status_code == 200, f"Staff作成のregression: {r.status_code} {r.text}"
            staff_id = r.json()["id"]
            r = await client.delete(f"/api/v1/staff/{staff_id}", headers=owner_a)
            assert r.status_code == 200, f"Staff削除のregression: {r.status_code} {r.text}"
            print("L. 既存ShopTable/Staff CRUDのregression: OK")

            # ===== M. 将来のStaff+Resource同時割当に向けた確認 =====
            r = await client.post("/api/v1/staff", json={
                "shop_id": shop_a, "name": "同時割当検証スタッフ",
            }, headers=owner_a)
            assert r.status_code == 200
            combo_staff_id = r.json()["id"]

            combo_reservation_id = str(uuid.uuid4())
            async with AsyncSessionLocal() as session:
                combo_reservation = Reservation(
                    id=combo_reservation_id,
                    shop_id=shop_a,
                    guest_name="同時割当検証太郎",
                    guest_phone=_next_phone(),
                    reservation_date=future_date,
                    number_of_people=1,
                    status="pending",
                    staff_id=combo_staff_id,
                    resource_id=None,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                session.add(combo_reservation)
                await session.commit()

                # DBレベルでstaff_idとresource_idを同一行に同時設定できることを確認
                # （CHECK制約が存在しないことの直接的な証明。Section14/46）。
                r2 = await client.post(f"/api/v1/shops/{shop_a}/resources", json={
                    "name": "同時割当検証個室", "resource_type": "room",
                }, headers=owner_a)
                assert r2.status_code == 200
                combo_resource_id = r2.json()["id"]

                combo_reservation.resource_id = combo_resource_id
                await session.commit()

                refreshed = await session.get(Reservation, combo_reservation_id)
                assert refreshed.staff_id == combo_staff_id and refreshed.resource_id == combo_resource_id, (
                    "staff_idとresource_idを同一予約行に同時設定できなかった（Option B1の前提が崩れている）"
                )
            print("M. staff_idとresource_idを同一予約行に同時設定可能（CHECK制約なし、将来のStaff+Resource同時割当への互換性）: OK")

            print("\n=== Generic Resource Foundation Phase R2 スモークテスト: 全項目OK ===")


if __name__ == "__main__":
    asyncio.run(main())
