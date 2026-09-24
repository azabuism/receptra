"""
Generic Resource Foundation Phase R3 スモークテスト
（Resource Availability Engine）

背景:

Phase R1でResourceモデル・CRUD APIを新設し（データ基盤のみ）、Phase R2で
Reservation.resource_id（nullable FK、CHECK制約なし）によるデータ層の
関連付け基盤を追加した。いずれの段階でも、実際の空き状況判定・自動割当・
二重予約防止には一切統合されていなかった。

Phase R3では、以下を実装した:
- どの店舗がTable経路かResource経路かを一意に決定する
  _resolve_reservation_allocation_mode()（唯一のsource of truth）。
  判定ルール:
    1. ShopTableが1件でも登録されていれば常に"table"
       （行の存在有無のみを見る。is_activeは問わない。既存の飲食店の
       予約フローを、Resourceの登録によって決して変えないための
       絶対的なガード）。
    2. ShopTableが0件、かつactiveなResourceが1件以上あれば"resource"。
    3. どちらも無ければ"table"（_find_available_table()自身の既存の
       「0件active tableならunmanaged=常に空席扱い」というバイパスに
       委ねる。Resource導入前と挙動は完全に同じ）。
  resource_typeやBusinessType/categoryは一切参照しない。
- Resource版の空き検索・ロック・自動割当（_find_available_resource /
  _lock_and_verify_resource_slot / _find_and_lock_available_resource）を、
  既存のTable版（_find_available_table等）と構造的に完全に対応する形で
  追加した（重複判定式・FOR UPDATE + 再検証・_MAX_LOCK_RETRYはすべて共通）。
- check_single_slot_availability() / get_availability() / create_reservation()
  の3箇所すべてが、上記の単一のallocation_mode判定を共有する。
- Resourceのcapacityがnull（収容人数の概念が無い種別）の場合は常に人数制限
  無しとして扱う。自動割当の優先順位は「具体的なcapacityを持つResourceを
  capacity昇順で優先し、capacity=Noneは最後（同順位内はdisplay_order→id）」。
- update_reservation()にもresource_id対応の人数変更時容量再検証・日時変更時
  重複再検証を追加した（本ファイルでは直接は検証しない。既存のcapacity_mutation
  系テストとの重複を避けるため）。

Phase R3のスコープは意図的に以下を含まない（本ファイルでも検証しない）:
- resource_typeによるフィルタリング（Audit ReportでOption A=フィルタ無し、
  かつ既知の制限として明記済み）
- Realtime Tool Schema変更、Booking Board Resource Lane、Week View、
  Owner手動割当、Resourceのスケジュール/メンテナンス時間、複数日予約、
  複数Resource同時割当（ReservationResource関連テーブル）

検証項目:
A. 容量による自動割当の優先順位（capacity昇順優先）＋Resource経路確認
   （table_id=NULL, resource_id populated）
B. capacity=NullのResourceは人数に関わらず絶対に除外されない
   （具体的なcapacityを持つ候補が全て埋まっている/不足している場合の
   最後の受け皿として機能する）
C. is_active=FalseのResourceは、shopがResource経路であっても候補から
   完全に除外される（別の空いているactive Resourceがあれば人数不足でも
   ghostは使われず、無ければfully_bookedになる）
D. 重複判定の境界値（既存10:00-11:00、新規10:30-11:30はNG、11:00-12:00は
   OK、09:00-10:00はOK。touching=重複ではない）
E. 既存予約のduration_minutesがNULL（Phase B以前相当のデータ）の場合、
   _existing_duration_minutes()のフォールバック値（shop既定値）が
   正しく使われる
F. 最有力候補（display_order最小）が既に埋まっている場合、次点候補へ
   自動的にフォールバックする
G. 同時多重予約防止（ほぼ同時に届いた2件のリクエスト。SQLiteでの
   行ロックの排他効果は限定的なため、最終的なDB整合性のみを参考情報として
   検証する。本番同等の排他制御の検証はPostgreSQL本番環境で行う）
H. 既存のTable経路のregression（ShopTableのみの店舗は従来通りtable_idが
   割り当てられ、resource_idは常にNULL）
I. 既存のStaff/Service経路のregression（サービス予約はstaff_idが対象、
   table_id/resource_idは共にNULLのまま）
J. ShopTable・Resourceが両方存在する「曖昧な店舗」でも、ルール#1により
   常にTable経路が選ばれる（Resourceの存在がRestaurantの既存動作を
   変えないことの直接確認）
K. check_single_slot_availability() / get_availability() /
   create_reservation() の3箇所が、同一スロットについて常に同じ結論を
   返す（Single Source of Routing Truthの実証）
L. 営業時間・休憩時間・臨時休業がResource経路でも既存のTable経路と
   同じ優先順位・同じロジックで適用される
M. テナント分離（他店舗のResourceが自店舗の候補に混入しない）
N. idempotency_key（Realtime Voice経由の再送）でResourceの二重割当が
   発生しない

実行: python3 tests/smoke_test_generic_resource_availability.py
"""

import asyncio
import os
import sys
import tempfile
import uuid
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_generic_resource_r3.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-generic-resource-r3"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402

_phone_counter = 0


def _next_phone() -> str:
    global _phone_counter
    _phone_counter += 1
    return f"090{_phone_counter:08d}"


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
                "email": "owner-resource-r3-a@example.com", "password": "password123",
                "display_name": "GenericResourceR3テストオーナーA",
            })
            assert r.status_code in (200, 201), f"register A failed: {r.status_code} {r.text}"
            owner_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-resource-r3-b@example.com", "password": "password123",
                "display_name": "GenericResourceR3テストオーナーB",
            })
            assert r.status_code in (200, 201), f"register B failed: {r.status_code} {r.text}"
            owner_b = {"Authorization": f"Bearer {r.json()['access_token']}"}

            async def _new_shop(name: str, owner_headers, opening="09:00:00", closing="21:00:00", duration=60):
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
                    shop_obj.reservation_duration_minutes = duration
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

            async def _check(shop_id, date_str, time_str, party_size=2, service_id=None, staff_id=None):
                payload = {"date": date_str, "time": time_str, "party_size": party_size}
                if service_id:
                    payload["service_id"] = service_id
                if staff_id:
                    payload["staff_id"] = staff_id
                r = await client.post(
                    f"/api/v1/shops/{shop_id}/realtime-voice/tools/check-availability", json=payload,
                )
                assert r.status_code == 200, f"check-availability failed: {r.status_code} {r.text}"
                return r.json()

            async def _availability_slot(shop_id, date_str, time_str, party_size=2):
                r = await client.get(
                    f"/api/v1/reservations/shop/{shop_id}/availability",
                    params={"date": date_str, "party_size": party_size},
                )
                assert r.status_code == 200, f"get_availability failed: {r.status_code} {r.text}"
                slots = {s["time"]: s["available"] for s in r.json()["slots"]}
                assert time_str in slots, f"スロット{time_str}が一覧に無い: {r.json()}"
                return slots[time_str]

            async def _create(shop_id, dt: datetime, party_size, phone=None, service_id=None, staff_id=None):
                payload = {
                    "shop_id": shop_id,
                    "guest_name": "R3テスト客",
                    "guest_phone": phone or _next_phone(),
                    "reservation_date": dt.isoformat(),
                    "number_of_people": party_size,
                }
                if service_id:
                    payload["service_id"] = service_id
                if staff_id:
                    payload["staff_id"] = staff_id
                return await client.post("/api/v1/reservations/create", json=payload)

            async def _get_reservation(reservation_id, owner_headers):
                r = await client.get(f"/api/v1/reservations/{reservation_id}", headers=owner_headers)
                assert r.status_code == 200, f"詳細取得失敗: {r.status_code} {r.text}"
                return r.json()

            # ===================================================
            # A. 容量による自動割当の優先順位 + Resource経路確認
            # ===================================================
            shop_cap = await _new_shop("R3容量優先テスト店", owner_a)
            room_a = await _new_resource(shop_cap, owner_a, "個室A", capacity=2)
            room_b = await _new_resource(shop_cap, owner_a, "個室B", capacity=4)
            room_c = await _new_resource(shop_cap, owner_a, "個室C(容量無制限)", capacity=None)

            a_date = _next_weekday(2, weeks_ahead=3)
            a_dt = datetime.combine(a_date, datetime.strptime("14:00", "%H:%M").time())

            r = await _create(shop_cap, a_dt, party_size=2)
            assert r.status_code in (200, 201), f"A1 create失敗: {r.status_code} {r.text}"
            body = r.json()["reservation"]
            assert body["resource_id"] == room_a, f"party=2で個室A(容量2)が優先されるはずが: {body}"
            assert body["table_id"] is None, f"Resource経路なのにtable_idが設定されている: {body}"
            print("A1. party=2 → capacity最小(個室A, capacity=2)が優先される: OK")

            r = await _create(shop_cap, a_dt, party_size=3)
            assert r.status_code in (200, 201), f"A2 create失敗: {r.status_code} {r.text}"
            body = r.json()["reservation"]
            assert body["resource_id"] == room_b, f"party=3で個室A除外後、個室B(容量4)が選ばれるはずが: {body}"
            assert body["table_id"] is None
            print("A2. party=3 → 個室A(容量不足)は除外され、個室B(容量4)が選ばれる: OK")
            print("A. Resource経路の自動割当確認（table_id=NULL, resource_id設定済み）: OK")

            # ===================================================
            # B. capacity=NullのResourceは人数に関わらず除外されない
            # ===================================================
            r = await _create(shop_cap, a_dt, party_size=50)
            assert r.status_code in (200, 201), f"B create失敗（NULL容量のResourceが誤って除外されている）: {r.status_code} {r.text}"
            body = r.json()["reservation"]
            assert body["resource_id"] == room_c, (
                f"個室A/Bが容量不足で除外された後、capacity=Noneの個室Cが選ばれるはずが: {body}"
            )
            print("B. capacity=NoneのResourceは人数(50名)に関わらず候補から除外されない: OK")

            # ===================================================
            # C. is_active=FalseのResourceは候補から完全に除外される
            # ===================================================
            shop_inactive = await _new_shop("R3非アクティブResourceテスト店", owner_a)
            active_room = await _new_resource(shop_inactive, owner_a, "現役の個室", capacity=4)
            ghost_room = await _new_resource(shop_inactive, owner_a, "非アクティブの個室", capacity=4)
            await _set_resource(shop_inactive, owner_a, ghost_room, is_active=False)

            c_dt = datetime.combine(_next_weekday(3, weeks_ahead=3), datetime.strptime("14:00", "%H:%M").time())
            r = await _create(shop_inactive, c_dt, party_size=2)
            assert r.status_code in (200, 201), f"C1 create失敗: {r.status_code} {r.text}"
            assert r.json()["reservation"]["resource_id"] == active_room, "唯一のactive Resourceが割り当てられるはずが違う"

            # 同じ時間帯にもう1件。ghost_roomが候補に入っていれば成功してしまうが、
            # is_active=Falseは絶対に候補に含まれないため、正しくは満席(400)になるはず。
            r = await _create(shop_inactive, c_dt, party_size=2)
            assert r.status_code == 400, (
                f"非アクティブResourceが誤って候補に含まれ、成立してしまった: {r.status_code} {r.text}"
            )
            assert "満席" in r.json()["detail"]
            print("C. is_active=FalseのResourceは、空席が無くても候補に一切含まれない（fully_bookedになる）: OK")

            # ===================================================
            # D. 重複判定の境界値
            # ===================================================
            shop_overlap = await _new_shop("R3境界値テスト店", owner_a, opening="06:00:00", closing="23:00:00")
            room_overlap = await _new_resource(shop_overlap, owner_a, "境界テスト用個室")

            d_date = _next_weekday(4, weeks_ahead=3).isoformat()
            d_existing_dt = datetime.combine(date.fromisoformat(d_date), datetime.strptime("10:00", "%H:%M").time())
            r = await _create(shop_overlap, d_existing_dt, party_size=1)
            assert r.status_code in (200, 201), f"D existing create失敗: {r.status_code} {r.text}"

            body = await _check(shop_overlap, d_date, "10:30", party_size=1)
            assert body["available"] is False and body["reason_code"] == "fully_booked", (
                f"10:30-11:30(既存10:00-11:00と重なる)はNGのはずが: {body}"
            )
            body = await _check(shop_overlap, d_date, "11:00", party_size=1)
            assert body["available"] is True, f"11:00-12:00(既存の終了時刻に触れるだけ)はOKのはずが: {body}"
            body = await _check(shop_overlap, d_date, "09:00", party_size=1)
            assert body["available"] is True, f"09:00-10:00(既存の開始時刻に触れるだけ)はOKのはずが: {body}"
            print("D. Resource経路の重複判定境界値（touching=重複ではない）: OK")

            # ===================================================
            # E. 既存予約のduration_minutes=NULLのフォールバック
            # ===================================================
            shop_duration = await _new_shop("R3duration fallbackテスト店", owner_a, duration=45)
            room_duration = await _new_resource(shop_duration, owner_a, "durationテスト用個室")

            e_date = _next_weekday(5, weeks_ahead=3)
            legacy_dt = datetime.combine(e_date, datetime.strptime("13:00", "%H:%M").time())
            legacy_id = str(uuid.uuid4())
            async with AsyncSessionLocal() as session:
                legacy = Reservation(
                    id=legacy_id, shop_id=shop_duration, resource_id=room_duration,
                    guest_name="レガシー予約", guest_phone=_next_phone(),
                    reservation_date=legacy_dt, duration_minutes=None, number_of_people=1,
                    status="pending", created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                )
                session.add(legacy)
                await session.commit()

            e_date_str = e_date.isoformat()
            body = await _check(shop_duration, e_date_str, "13:30", party_size=1)
            assert body["available"] is False and body["reason_code"] == "fully_booked", (
                f"duration_minutes=NULLのfallback(45分)により13:30はNGのはずが: {body}"
            )
            body = await _check(shop_duration, e_date_str, "13:45", party_size=1)
            assert body["available"] is True, f"fallback終了時刻(13:45)に触れるだけの13:45はOKのはずが: {body}"
            body = await _check(shop_duration, e_date_str, "12:00", party_size=1)
            assert body["available"] is True, f"fallback開始時刻より十分前の12:00はOKのはずが: {body}"
            print("E. 既存予約のduration_minutes=NULLは_existing_duration_minutes()のfallback(店舗既定値)で判定される: OK")

            # ===================================================
            # F. 最有力候補が埋まっている場合の次点候補への自動フォールバック
            # ===================================================
            shop_fallback = await _new_shop("R3フォールバックテスト店", owner_a)
            r1 = await _new_resource(shop_fallback, owner_a, "候補1")
            r2 = await _new_resource(shop_fallback, owner_a, "候補2")
            # display_orderを明示的に分け、候補1が必ず最初にソートされるようにする
            # （どちらもcapacity=Noneのため、display_order→idの順でしか決まらない）。
            await _set_resource(shop_fallback, owner_a, r2, resource_type="room")  # no-op update確認は不要だが型を合わせる
            async with AsyncSessionLocal() as session:
                from app.models.resource import Resource
                res2 = await session.get(Resource, r2)
                res2.display_order = 1
                await session.commit()

            f_dt = datetime.combine(_next_weekday(6, weeks_ahead=3), datetime.strptime("14:00", "%H:%M").time())
            r = await _create(shop_fallback, f_dt, party_size=1)
            assert r.status_code in (200, 201), f"F1 create失敗: {r.status_code} {r.text}"
            assert r.json()["reservation"]["resource_id"] == r1, "最初の予約は候補1(display_order最小)が選ばれるはずが違う"

            r = await _create(shop_fallback, f_dt, party_size=1)
            assert r.status_code in (200, 201), f"F2 create失敗（候補2への自動フォールバックが機能していない）: {r.status_code} {r.text}"
            assert r.json()["reservation"]["resource_id"] == r2, (
                f"候補1が埋まっている時、候補2へ自動フォールバックするはずが: {r.json()['reservation']}"
            )
            print("F. 最有力候補(候補1)が埋まっている場合、次点候補(候補2)へ自動的にフォールバックする: OK")

            # ===================================================
            # G. 同時多重予約防止（参考情報。SQLiteの限界を明記）
            # ===================================================
            shop_race = await _new_shop("R3同時多重予約テスト店", owner_a)
            room_race = await _new_resource(shop_race, owner_a, "競合テスト用個室")
            g_dt = datetime.combine(_next_weekday(0, weeks_ahead=4), datetime.strptime("15:00", "%H:%M").time())

            results = await asyncio.gather(
                _create(shop_race, g_dt, party_size=1, phone="09011110001"),
                _create(shop_race, g_dt, party_size=1, phone="09011110002"),
            )
            statuses = [res.status_code for res in results]
            assert 200 in statuses or 201 in statuses, f"少なくとも1件は成立するはずが: {statuses}"

            async with AsyncSessionLocal() as session:
                res = await session.execute(
                    select(Reservation).filter(
                        Reservation.resource_id == room_race,
                        Reservation.status.in_(["pending", "confirmed"]),
                        Reservation.reservation_date == g_dt,
                    )
                )
                overlapping = list(res.scalars().all())
            if len(overlapping) == 1:
                print("G. 同時多重予約防止: SQLite環境でも重複は1件のみでした（参考情報）")
            else:
                print(
                    f"G. 同時多重予約防止: SQLite環境の制約により{len(overlapping)}件の重複が発生"
                    f"（想定内。FOR UPDATEの行ロックはSQLiteダイアレクトでは実効性が限定的なため。"
                    f"statuses={statuses}。実際の排他制御の検証はPostgreSQL本番環境で行う）"
                )

            # ===================================================
            # H. 既存Table経路のregression
            # ===================================================
            shop_table_regress = await _new_shop("R3 Table経路regressionテスト店", owner_a)
            table_id_h = await _new_table(shop_table_regress, owner_a, "テーブルA", capacity=4)
            h_dt = datetime.combine(_next_weekday(1, weeks_ahead=4), datetime.strptime("14:00", "%H:%M").time())
            r = await _create(shop_table_regress, h_dt, party_size=2)
            assert r.status_code in (200, 201), f"H create失敗: {r.status_code} {r.text}"
            body = r.json()["reservation"]
            assert body["table_id"] == table_id_h, f"ShopTableのみの店舗ではtable_idが割り当てられるはずが: {body}"
            assert body["resource_id"] is None, f"Table経路の予約なのにresource_idが設定されている: {body}"
            print("H. 既存Table経路のregression（ShopTableのみの店舗はtable_id割当・resource_idは常にNULL）: OK")

            # ===================================================
            # I. 既存Staff/Service経路のregression
            # ===================================================
            shop_staff_regress = await _new_shop("R3 Staff経路regressionテスト店", owner_a)
            r = await client.post("/api/v1/services", json={
                "shop_id": shop_staff_regress, "name": "カット", "base_price": 3000, "duration_minutes": 60,
            }, headers=owner_a)
            assert r.status_code == 200, f"service作成失敗: {r.status_code} {r.text}"
            service_id = r.json()["id"]

            r = await client.post("/api/v1/staff", json={
                "shop_id": shop_staff_regress, "name": "スタッフI",
            }, headers=owner_a)
            assert r.status_code == 200, f"staff作成失敗: {r.status_code} {r.text}"
            staff_id_i = r.json()["id"]

            r = await client.post(f"/api/v1/staff/{staff_id_i}/services/{service_id}", headers=owner_a)
            assert r.status_code == 200, f"staff-service紐付け失敗: {r.status_code} {r.text}"

            i_dt = datetime.combine(_next_weekday(2, weeks_ahead=4), datetime.strptime("14:00", "%H:%M").time())
            r = await _create(shop_staff_regress, i_dt, party_size=1, service_id=service_id)
            assert r.status_code in (200, 201), f"I create失敗: {r.status_code} {r.text}"
            body = r.json()["reservation"]
            assert body["table_id"] is None, f"サービス予約なのにtable_idが設定されている: {body}"
            assert body["resource_id"] is None, f"サービス予約なのにresource_idが設定されている: {body}"
            print("I. 既存Staff/Service経路のregression（サービス予約はtable_id/resource_idともに常にNULL）: OK")

            # ===================================================
            # J. ShopTable・Resourceが両方存在する曖昧な店舗
            # ===================================================
            shop_ambiguous = await _new_shop("R3曖昧な店舗テスト", owner_a)
            table_id_j = await _new_table(shop_ambiguous, owner_a, "テーブルJ", capacity=4)
            await _new_resource(shop_ambiguous, owner_a, "個室J", capacity=4)

            j_dt = datetime.combine(_next_weekday(3, weeks_ahead=4), datetime.strptime("14:00", "%H:%M").time())
            r = await _create(shop_ambiguous, j_dt, party_size=2)
            assert r.status_code in (200, 201), f"J create失敗: {r.status_code} {r.text}"
            body = r.json()["reservation"]
            assert body["table_id"] == table_id_j, f"ShopTableが存在する以上、常にTable経路が選ばれるはずが: {body}"
            assert body["resource_id"] is None, f"曖昧な店舗でResourceが誤って割り当てられている: {body}"
            print("J. ShopTable・Resourceが両方存在する店舗でも、ルール#1により常にTable経路が選ばれる: OK")

            # ===================================================
            # K. 3箇所（check/get_availability/create）の一貫性
            # ===================================================
            shop_consistency = await _new_shop("R3一貫性テスト店", owner_a)
            await _new_resource(shop_consistency, owner_a, "一貫性テスト用個室", capacity=4)
            k_date = _next_weekday(4, weeks_ahead=4)
            k_date_str = k_date.isoformat()
            k_dt = datetime.combine(k_date, datetime.strptime("15:00", "%H:%M").time())

            body = await _check(shop_consistency, k_date_str, "15:00", party_size=2)
            assert body["available"] is True, f"予約前のcheck-availabilityはTrueのはずが: {body}"
            slot_avail = await _availability_slot(shop_consistency, k_date_str, "15:00", party_size=2)
            assert slot_avail is True, "予約前のget_availabilityはTrueのはずが違う"

            r = await _create(shop_consistency, k_dt, party_size=2)
            assert r.status_code in (200, 201), f"K create失敗: {r.status_code} {r.text}"

            body = await _check(shop_consistency, k_date_str, "15:00", party_size=2)
            assert body["available"] is False and body["reason_code"] == "fully_booked", (
                f"予約後のcheck-availabilityはFalse(fully_booked)のはずが: {body}"
            )
            slot_avail = await _availability_slot(shop_consistency, k_date_str, "15:00", party_size=2)
            assert slot_avail is False, "予約後のget_availabilityはFalseのはずが違う"
            print("K. check_single_slot_availability/get_availability/create_reservationは常に同じ結論（Single Source of Routing Truth）: OK")

            # ===================================================
            # L. 営業時間・休憩時間・臨時休業のResource経路での回帰確認
            # ===================================================
            shop_hours = await _new_shop("R3営業時間regressionテスト店", owner_a)
            await _new_resource(shop_hours, owner_a, "営業時間テスト用個室", capacity=4)
            l_date = _next_weekday(5, weeks_ahead=4)
            l_date_str = l_date.isoformat()

            r = await client.post(f"/api/v1/shops/{shop_hours}/break-times", json={
                "day_of_week": l_date.weekday(), "start_time": "12:00", "end_time": "13:00",
            }, headers=owner_a)
            assert r.status_code == 200, f"break-time作成失敗: {r.status_code} {r.text}"

            body = await _check(shop_hours, l_date_str, "11:00", party_size=2)
            assert body["available"] is True, f"休憩に触れるだけの11:00はOKのはずが: {body}"
            body = await _check(shop_hours, l_date_str, "12:30", party_size=2)
            assert body["available"] is False and body["reason_code"] == "break_time", (
                f"休憩時間内の12:30はNG(break_time)のはずが: {body}"
            )

            closure_date = _next_weekday(6, weeks_ahead=5)
            r = await client.post(f"/api/v1/shops/{shop_hours}/closures", json={
                "start_date": closure_date.isoformat(), "end_date": closure_date.isoformat(),
                "reason": "臨時休業テスト",
            }, headers=owner_a)
            assert r.status_code == 200, f"closure作成失敗: {r.status_code} {r.text}"
            body = await _check(shop_hours, closure_date.isoformat(), "14:00", party_size=2)
            assert body["available"] is False and body["reason_code"] == "temporary_closure", (
                f"臨時休業日はNG(temporary_closure)のはずが: {body}"
            )
            print("L. Resource経路でも営業時間・休憩時間・臨時休業が既存と同じ優先順位・同じロジックで適用される: OK")

            # ===================================================
            # M. テナント分離
            # ===================================================
            shop_iso_a = await _new_shop("R3テナント分離テストA店", owner_a)
            await _new_resource(shop_iso_a, owner_a, "A店の個室", capacity=2)
            shop_iso_b = await _new_shop("R3テナント分離テストB店", owner_b)
            await _new_resource(shop_iso_b, owner_b, "B店の大部屋", capacity=999)

            m_dt = datetime.combine(_next_weekday(0, weeks_ahead=6), datetime.strptime("14:00", "%H:%M").time())
            # A店には収容999名を満たすResourceは存在しない。B店のResourceへ誤って
            # クロステナントに割り当てられてしまえば成立してしまうが、
            # _find_available_resource()のshop_idフィルタにより、正しくは
            # A店側のみで判定され、容量不足で満席(400)になるはず。
            r = await _create(shop_iso_a, m_dt, party_size=999)
            assert r.status_code == 400, (
                f"A店の予約がB店のResourceへ漏れて成立してしまった（テナント分離違反）: {r.status_code} {r.text}"
            )
            assert "満席" in r.json()["detail"]
            print("M. テナント分離: 他店舗のResourceは自店舗の予約候補に一切混入しない: OK")

            # ===================================================
            # N. idempotency_key（Realtime Voice経由の再送）
            # ===================================================
            shop_idem = await _new_shop("R3 idempotencyテスト店", owner_a)
            await _new_resource(shop_idem, owner_a, "idempotencyテスト用個室", capacity=4)
            n_date = _next_weekday(1, weeks_ahead=6)
            call_id = "smoke-r3-idem-" + str(uuid.uuid4())
            idem_payload = {
                "date": n_date.isoformat(), "time": "16:00", "party_size": 2,
                "guest_name": "冪等性太郎", "guest_phone": _next_phone(), "call_id": call_id,
            }
            r = await client.post(
                f"/api/v1/shops/{shop_idem}/realtime-voice/tools/create-reservation", json=idem_payload,
            )
            assert r.status_code == 200, f"N1 create失敗: {r.status_code} {r.text}"
            body1 = r.json()
            assert body1["success"] is True, f"N1 successがFalse: {body1}"
            reservation_id_1 = body1["reservation_id"]

            r = await client.post(
                f"/api/v1/shops/{shop_idem}/realtime-voice/tools/create-reservation", json=idem_payload,
            )
            assert r.status_code == 200, f"N2(再送)失敗: {r.status_code} {r.text}"
            body2 = r.json()
            assert body2["success"] is True, f"N2 successがFalse: {body2}"
            assert body2["reservation_id"] == reservation_id_1, (
                f"同一call_idの再送で別の予約IDが返ってしまった: {body1} / {body2}"
            )

            async with AsyncSessionLocal() as session:
                res = await session.execute(
                    select(Reservation).filter(Reservation.shop_id == shop_idem)
                )
                idem_reservations = list(res.scalars().all())
            assert len(idem_reservations) == 1, (
                f"idempotency_keyの再送でResource予約が2重に作られてしまった: {len(idem_reservations)}件"
            )
            assert idem_reservations[0].resource_id is not None, "Resourceが割り当てられていない"
            print("N. idempotency_key（Realtime Voice再送）でResourceの二重割当・二重予約は発生しない: OK")

            print("\n=== Generic Resource Foundation Phase R3 スモークテスト: 全項目OK ===")


if __name__ == "__main__":
    asyncio.run(main())
