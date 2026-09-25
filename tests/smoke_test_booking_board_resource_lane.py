"""
Owner Booking Board Resource Lane V2 (Phase R5 Part A) スモークテスト

背景:

既存のOwner Booking Board（GET /api/v1/reservations/shop/{shop_id}/board）は
V1/V1.1で「担当者別」「テーブル別」レーン（staff_roster/table_roster +
BoardReservationItem.staff_id/table_id）を実装済みだったが、Generic Resource
Foundation（Phase R1〜R4）で導入されたResource（部屋・ベッド・車両等）は
Booking Boardに一切反映されていなかった（BoardReservationItemに
resource_id/resource_nameが無く、selectinloadもResourceを含んでいなかった）。

Phase R5 Part Aでは、以下を追加した:
- BoardResponse.resource_roster（BoardResourceRosterItemのリスト。
  staff_roster/table_rosterと全く同じ「is_active=Trueのみ、display_order順、
  day_statusに関わらず常に返す」規約）
- BoardReservationItem.resource_id / resource_name（staff_id/staff_name・
  table_id/table_nameと全く同じ、後方互換の追加専用フィールド）
- Board取得クエリへのselectinload(Reservation.resource)追加（追加の
  ラウンドトリップクエリは発生させない）

Booking BoardはGeneric Resourceの自動割当ロジック（
_resolve_reservation_allocation_mode等）を一切呼ばない。Reservation.resource_id
に実際に記録されている値をそのまま表示するだけであり、割当を推測・合成しない
（Phase R5仕様書Section16「Board must reflect actual Reservation assignment,
not infer allocation」）。

フロントエンド（frontend/public/shop-manage.html）側の対応（本ファイルでは
直接検証しない。JS純粋関数はtests/test_booking_board.jsで別途検証）:
- 表示モードに'resource'を追加（ボタンラベル「設備・部屋別」。内部識別子
  resource_id/resource_type/allocation_mode等は一切表示しない）
- _boardBuildResourceLanes()にresourceRoster分岐を追加（staff/tableと
  全く同じ構造。ロスターに存在しないID(無効化済みだが実際に紐付いている
  予約)は予約自身の名前で追加のレーンを作る。未割当は常に最後のレーンにまとめる）
- 予約詳細ボトムシートに「設備・部屋」行を追加（staff_name/table_nameと
  同じ表示パターン）

Phase R5 Part Aのスコープは意図的に以下を含まない（本ファイルでも検証しない）:
- Resourceの手動再割当・Owner Resource CRUD UIの変更
- Week Viewへのresource反映（Week Viewは一切変更していない。既存の
  smoke_test_week_view.pyのregressionで別途保証される）
- Generic Resource自動割当ロジック自体の変更（Phase R3/R4から一切変更なし）

検証項目:
A. 基本ケース: Resource 2件・それぞれに予約1件。resource_roster・各予約の
   resource_id/resource_nameが正しく返る
B. 同一Resourceへの複数予約（時系列順に複数件、いずれも同一resource_idを持つ）
C. 未割当（resource_id=NULL）の予約もreservationsから消えない
D. 非アクティブ化されたResource: resource_rosterからは除外されるが、
   既存予約のresource_id/resource_nameは維持される（histrical booking
   disappearanceが起きない）
E. resource_rosterはその日の予約の有無に関わらず、day_statusにも関わらず
   常に返る（定休日でも返る。staff_roster/table_rosterと同じ規約）
F. 既存Table経路のregression: ShopTableのみの店舗ではresource_roster=[]、
   各予約のresource_id/resource_nameは常にNone
G. 既存Staff/Service経路のregression: サービス予約でもresource_id/
   resource_nameは常にNone
H. ShopTable・Resourceが両方存在する店舗でも、Boardは実際の割当
   （reservation.table_id / reservation.resource_id）をそのまま表示する
   （allocation_modeを推測しない。Table予約はtable_idのみ、Resource予約は
   resource_idのみを持つ）
I. Tenant isolation（他tenantの予約表・resource_rosterを取得できない）
J. レスポンスにresource_id（内部ID）以外の内部概念（allocation_mode等の
   文字列）が一切含まれない

実行: python3 tests/smoke_test_booking_board_resource_lane.py
"""

import asyncio
import os
import sys
import tempfile
import uuid
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_board_resource_lane_r5.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-board-resource-lane-r5"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402

_phone_counter = 0


def _next_phone() -> str:
    global _phone_counter
    _phone_counter += 1
    return f"070{_phone_counter:08d}"


def _next_weekday(target_weekday: int, weeks_ahead: int = 3):
    today = date.today()
    days_ahead = (target_weekday - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead + 7 * (weeks_ahead - 1))


async def main():
    from app.main import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            from app.database import AsyncSessionLocal
            from app.models.shop import Shop
            from app.models.reservation import Reservation
            from sqlalchemy import select

            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-board-resource-r5-a@example.com", "password": "password123",
                "display_name": "BoardResourceLaneR5テストオーナーA",
            })
            assert r.status_code in (200, 201), f"register A failed: {r.status_code} {r.text}"
            owner_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-board-resource-r5-b@example.com", "password": "password123",
                "display_name": "BoardResourceLaneR5テストオーナーB",
            })
            assert r.status_code in (200, 201), f"register B failed: {r.status_code} {r.text}"
            owner_b = {"Authorization": f"Bearer {r.json()['access_token']}"}

            async def _new_shop(name: str, owner_headers, opening="09:00:00", closing="21:00:00"):
                r = await client.post("/api/v1/shops/register", json={
                    "name": name, "category": "その他", "address": "東京都渋谷区9-9-9",
                }, headers=owner_headers)
                assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
                shop_id = r.json()["shop_id"]
                r = await client.put(f"/api/v1/shops/{shop_id}/hours", json={"hours": [
                    {"day_of_week": d, "opening_time": opening, "closing_time": closing, "is_closed": False}
                    for d in range(7)
                ]}, headers=owner_headers)
                assert r.status_code == 200, f"set hours failed: {r.status_code} {r.text}"
                async with AsyncSessionLocal() as session:
                    shop_obj = await session.get(Shop, shop_id)
                    shop_obj.reservations_enabled = True
                    await session.commit()
                return shop_id

            async def _new_resource(shop_id, owner_headers, name, capacity=None, resource_type="room"):
                r = await client.post(f"/api/v1/shops/{shop_id}/resources", json={
                    "name": name, "resource_type": resource_type, "capacity": capacity,
                }, headers=owner_headers)
                assert r.status_code == 200, f"Resource作成失敗: {r.status_code} {r.text}"
                return r.json()["id"]

            async def _set_resource(shop_id, owner_headers, resource_id, **fields):
                r = await client.put(f"/api/v1/shops/{shop_id}/resources/{resource_id}", json=fields, headers=owner_headers)
                assert r.status_code == 200, f"Resource更新失敗: {r.status_code} {r.text}"

            async def _new_table(shop_id, owner_headers, name, capacity):
                r = await client.post(f"/api/v1/shops/{shop_id}/tables", json={
                    "name": name, "capacity": capacity,
                }, headers=owner_headers)
                assert r.status_code == 200, f"ShopTable作成失敗: {r.status_code} {r.text}"
                return r.json()["id"]

            async def _insert_reservation(shop_id, dt, duration_minutes=60, guest_name="花子", guest_phone=None,
                                           status="confirmed", number_of_people=2, staff_id=None, table_id=None,
                                           resource_id=None):
                async with AsyncSessionLocal() as session:
                    res = Reservation(
                        id=str(uuid.uuid4()), shop_id=shop_id,
                        guest_name=guest_name, guest_phone=guest_phone or _next_phone(),
                        reservation_date=dt, duration_minutes=duration_minutes,
                        number_of_people=number_of_people, status=status,
                        staff_id=staff_id, table_id=table_id, resource_id=resource_id,
                        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                    )
                    session.add(res)
                    await session.commit()
                    return res.id

            async def _board(shop_id, date_str, headers):
                return await client.get(
                    f"/api/v1/reservations/shop/{shop_id}/board", params={"date": date_str}, headers=headers
                )

            async def _clear_reservations(shop_id):
                async with AsyncSessionLocal() as session:
                    result = await session.execute(select(Reservation).filter(Reservation.shop_id == shop_id))
                    for res in result.scalars().all():
                        await session.delete(res)
                    await session.commit()

            # ===================================================
            # A. 基本ケース: Resource 2件・それぞれに予約1件
            # ===================================================
            shop_a = await _new_shop("R5 Board Resource基本テスト店", owner_a)
            room_a1 = await _new_resource(shop_a, owner_a, "個室A", capacity=4, resource_type="room")
            room_a2 = await _new_resource(shop_a, owner_a, "個室B", capacity=2, resource_type="room")

            a_date = _next_weekday(0, weeks_ahead=3)
            res_1 = await _insert_reservation(shop_a, datetime.combine(a_date, datetime.strptime("14:00", "%H:%M").time()), resource_id=room_a1)
            res_2 = await _insert_reservation(shop_a, datetime.combine(a_date, datetime.strptime("15:00", "%H:%M").time()), resource_id=room_a2)

            r = await _board(shop_a, a_date.isoformat(), owner_a)
            assert r.status_code == 200, f"board(A) failed: {r.status_code} {r.text}"
            body = r.json()
            assert {res["id"] for res in body["resource_roster"]} == {room_a1, room_a2}, (
                f"resource_rosterに両方のResourceが含まれるはずが: {body['resource_roster']}"
            )
            roster_by_id = {res["id"]: res for res in body["resource_roster"]}
            assert roster_by_id[room_a1]["name"] == "個室A" and roster_by_id[room_a1]["resource_type"] == "room"
            items_by_id = {it["id"]: it for it in body["reservations"]}
            assert items_by_id[res_1]["resource_id"] == room_a1 and items_by_id[res_1]["resource_name"] == "個室A"
            assert items_by_id[res_2]["resource_id"] == room_a2 and items_by_id[res_2]["resource_name"] == "個室B"
            print("A. 基本ケース: resource_roster・各予約のresource_id/resource_nameが正しく返る: OK")

            # ===================================================
            # B. 同一Resourceへの複数予約
            # ===================================================
            res_3 = await _insert_reservation(shop_a, datetime.combine(a_date, datetime.strptime("18:00", "%H:%M").time()), resource_id=room_a1)
            r = await _board(shop_a, a_date.isoformat(), owner_a)
            body = r.json()
            items_by_id = {it["id"]: it for it in body["reservations"]}
            same_resource_ids = {items_by_id[res_1]["resource_id"], items_by_id[res_3]["resource_id"]}
            assert same_resource_ids == {room_a1}, f"同一Resourceへの複数予約は同じresource_idを持つはずが: {same_resource_ids}"
            print("B. 同一Resourceへの複数予約が同じresource_idで正しく返る: OK")

            await _clear_reservations(shop_a)

            # ===================================================
            # C. 未割当（resource_id=NULL）の予約が消えない
            # ===================================================
            res_unassigned = await _insert_reservation(shop_a, datetime.combine(a_date, datetime.strptime("14:00", "%H:%M").time()))
            r = await _board(shop_a, a_date.isoformat(), owner_a)
            body = r.json()
            items_by_id = {it["id"]: it for it in body["reservations"]}
            assert res_unassigned in items_by_id, "未割当の予約がreservations一覧から消えてしまった"
            assert items_by_id[res_unassigned]["resource_id"] is None and items_by_id[res_unassigned]["resource_name"] is None
            print("C. 未割当（resource_id=NULL）の予約が一覧から消えない: OK")

            await _clear_reservations(shop_a)

            # ===================================================
            # D. 非アクティブ化されたResource: rosterから除外されるが、
            #    既存予約のresource_id/resource_nameは維持される
            # ===================================================
            res_on_room_a2 = await _insert_reservation(shop_a, datetime.combine(a_date, datetime.strptime("14:00", "%H:%M").time()), resource_id=room_a2)
            await _set_resource(shop_a, owner_a, room_a2, is_active=False)

            r = await _board(shop_a, a_date.isoformat(), owner_a)
            body = r.json()
            assert room_a2 not in {res["id"] for res in body["resource_roster"]}, (
                f"非アクティブ化したResourceはresource_rosterに出ないはずが: {body['resource_roster']}"
            )
            items_by_id = {it["id"]: it for it in body["reservations"]}
            assert items_by_id[res_on_room_a2]["resource_id"] == room_a2, (
                "Resourceを非アクティブ化しても、既存予約のresource_idは維持されるはず"
            )
            assert items_by_id[res_on_room_a2]["resource_name"] == "個室B", (
                "Resourceを非アクティブ化しても、既存予約のresource_nameは維持されるはず（historical booking disappearanceは起きない）"
            )
            print("D. 非アクティブ化Resource: rosterから除外・既存予約の紐付け(id/name)は維持: OK")

            # 元に戻す（後続テストに影響しないよう）
            await _set_resource(shop_a, owner_a, room_a2, is_active=True)
            await _clear_reservations(shop_a)

            # ===================================================
            # E. resource_rosterはday_status/予約有無に関わらず常に返る
            # ===================================================
            r = await _board(shop_a, a_date.isoformat(), owner_a)  # 予約0件の営業日
            body = r.json()
            assert body["day_status"] == "open"
            assert {res["id"] for res in body["resource_roster"]} == {room_a1, room_a2}, (
                f"予約が0件でもresource_rosterは常に返るはずが: {body['resource_roster']}"
            )

            # 定休日にしても同様
            from app.models.shop import ShopHours
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(ShopHours).filter(ShopHours.shop_id == shop_a, ShopHours.day_of_week == a_date.weekday())
                )
                a_hours = result.scalar_one()
                a_hours.is_closed = True
                await session.commit()
            r = await _board(shop_a, a_date.isoformat(), owner_a)
            body = r.json()
            assert body["day_status"] == "closed_regular"
            assert {res["id"] for res in body["resource_roster"]} == {room_a1, room_a2}, (
                f"定休日でもresource_rosterは常に返るはずが: {body['resource_roster']}"
            )
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(ShopHours).filter(ShopHours.shop_id == shop_a, ShopHours.day_of_week == a_date.weekday())
                )
                a_hours = result.scalar_one()
                a_hours.is_closed = False
                await session.commit()
            print("E. resource_rosterはday_status（予約0件・定休日）に関わらず常に返る: OK")

            # ===================================================
            # F. 既存Table経路のregression
            # ===================================================
            shop_table = await _new_shop("R5 Board Table経路regressionテスト店", owner_a)
            table_f = await _new_table(shop_table, owner_a, "テーブルF", capacity=4)
            f_date = _next_weekday(1, weeks_ahead=3)
            res_f = await _insert_reservation(shop_table, datetime.combine(f_date, datetime.strptime("14:00", "%H:%M").time()), table_id=table_f)
            r = await _board(shop_table, f_date.isoformat(), owner_a)
            body = r.json()
            assert body["resource_roster"] == [], f"Resourceを一切登録していない店舗のresource_rosterは空のはずが: {body['resource_roster']}"
            item = {it["id"]: it for it in body["reservations"]}[res_f]
            assert item["table_id"] == table_f and item["resource_id"] is None and item["resource_name"] is None, (
                f"Table予約のresource_id/resource_nameは常にNoneのはずが: {item}"
            )
            print("F. 既存Table経路のregression（resource_roster=[]、resource_id/nameは常にNone）: OK")

            # ===================================================
            # G. 既存Staff/Service経路のregression
            # ===================================================
            shop_staff = await _new_shop("R5 Board Staff経路regressionテスト店", owner_a)
            r = await client.post("/api/v1/staff", json={"shop_id": shop_staff, "name": "スタッフG"}, headers=owner_a)
            assert r.status_code == 200, f"staff作成失敗: {r.status_code} {r.text}"
            staff_g = r.json()["id"]
            g_date = _next_weekday(2, weeks_ahead=3)
            res_g = await _insert_reservation(shop_staff, datetime.combine(g_date, datetime.strptime("14:00", "%H:%M").time()), staff_id=staff_g)
            r = await _board(shop_staff, g_date.isoformat(), owner_a)
            body = r.json()
            item = {it["id"]: it for it in body["reservations"]}[res_g]
            assert item["staff_id"] == staff_g and item["resource_id"] is None and item["resource_name"] is None, (
                f"サービス予約のresource_id/resource_nameは常にNoneのはずが: {item}"
            )
            print("G. 既存Staff/Service経路のregression（resource_id/nameは常にNone）: OK")

            # ===================================================
            # H. ShopTable・Resourceが両方存在する店舗でも、実際の割当をそのまま表示
            # ===================================================
            shop_mixed = await _new_shop("R5 Board Table+Resource混在テスト店", owner_a)
            table_h = await _new_table(shop_mixed, owner_a, "テーブルH", capacity=4)
            room_h = await _new_resource(shop_mixed, owner_a, "個室H", capacity=4, resource_type="room")
            h_date = _next_weekday(3, weeks_ahead=3)
            res_h_table = await _insert_reservation(shop_mixed, datetime.combine(h_date, datetime.strptime("14:00", "%H:%M").time()), table_id=table_h)
            res_h_resource = await _insert_reservation(shop_mixed, datetime.combine(h_date, datetime.strptime("15:00", "%H:%M").time()), resource_id=room_h)

            r = await _board(shop_mixed, h_date.isoformat(), owner_a)
            body = r.json()
            assert len(body["table_roster"]) == 1 and len(body["resource_roster"]) == 1, (
                f"table_roster/resource_rosterが両方とも正しく返るはずが: {body['table_roster']} / {body['resource_roster']}"
            )
            items_by_id = {it["id"]: it for it in body["reservations"]}
            assert items_by_id[res_h_table]["table_id"] == table_h and items_by_id[res_h_table]["resource_id"] is None
            assert items_by_id[res_h_resource]["resource_id"] == room_h and items_by_id[res_h_resource]["table_id"] is None
            print("H. Table・Resourceが両方存在する店舗でも、実際の予約割当（table_id/resource_id）をそのまま反映する: OK")

            # ===================================================
            # I. Tenant isolation
            # ===================================================
            r = await _board(shop_a, a_date.isoformat(), owner_b)
            assert r.status_code == 403, f"他tenantの予約表を取得できてしまった: {r.status_code} {r.text}"
            print("I. Tenant isolation（他tenantのBoard・resource_rosterを403で拒否）: OK")

            # ===================================================
            # J. レスポンスに内部概念（allocation_mode等）が一切含まれない
            # ===================================================
            r = await _board(shop_mixed, h_date.isoformat(), owner_a)
            assert "allocation_mode" not in r.text, "レスポンスにallocation_modeという内部概念の文字列が漏れている"
            print("J. レスポンスにallocation_mode等の内部概念が一切含まれない: OK")

            print("\n=== Owner Booking Board Resource Lane V2 (Phase R5 Part A) スモークテスト: 全項目OK ===")


if __name__ == "__main__":
    asyncio.run(main())
