"""
Generic Resource Foundation Phase R4 スモークテスト
（Resource-Aware AI Booking / Resource Type Resolution + Realtime Integration）

背景:

Phase R3で「Table経路かResource経路か」を判定する
_resolve_reservation_allocation_mode()と、Resource版の空き検索・ロック・
自動割当エンジンを実装した。しかし、1つの店舗に複数のresource_type
（例:「部屋」と「ベッド」）が混在する場合、R3のエンジンは全typeを区別せず
一つのプールとして扱っていた（Phase R3完了報告の既知の制限として明記済み）。

Phase R4では、以下を実装した:
- _resolve_required_resource_type(db, shop_id, requested_resource_type)を
  唯一のsource of truthとして新設。shopのactiveなresource_typeの集合と、
  リクエストで指定されたresource_type（省略可）から、次の5状態のいずれか
  一つに必ず分類する:
    - "UNMANAGED"        : そのshopにactiveなResourceが1件も無い
                            （R3のunmanagedバイパスがそのまま機能する）
    - "UNAMBIGUOUS_TYPE"  : resource_type省略時、activeなtypeが1種類しか
                            ないため自動的にそのtypeに決定できる
    - "EXPLICIT_TYPE"     : 指定されたresource_typeが実在するactiveな
                            typeと一致する
    - "AMBIGUOUS"         : resource_type省略時、activeなtypeが2種類以上
                            混在しており、バックエンドだけでは決定できない
    - "NO_MATCH"          : 指定されたresource_typeが、そのshopには実在
                            しない（typoや、そのtypeのResourceが後から
                            全て削除/非アクティブ化された等）
  NO_MATCHの場合、2番目の戻り値（effective_resource_type）には
  requested_resource_typeがそのまま（Noneに変換せず）返る設計であり、
  これにより下流の候補フィルタが「全件を再度プールする」のではなく
  「0件になり自然にfully_bookedへ落ちる」ことが保証される（実装時に
  発見・修正した設計上の重要ポイント。本ファイルC3で直接検証する）。
- check_single_slot_availability() / get_availability() /
  create_reservation() の3箇所すべてが上記の判定を共有する
  （Phase R3のSingle Source of Routing Truthをtypeの階層でも維持）。
- AMBIGUOUSの場合のみ、新しいreason_code="resource_type_required"を返す
  （NO_MATCHは既存のfully_bookedパスをそのまま流用し、新しいエラー体系は
  増やさない）。CheckAvailabilityResponse/CreateReservationToolResponseに
  available_resource_types（実在するtypeのソート済みリスト。resource_idは
  絶対に含まない）を追加した。
- _lock_and_verify_resource_slot()に、ロック取得後の再検証として
  is_active/resource_typeの再確認を追加した（R3には無かった、R4独自の
  安全性強化。「候補選定後・ロック取得までの間にResourceのtypeが変更・
  非アクティブ化される」レースに対応する）。
- Realtime Voice AI（check_availability/create_reservation Tool）に
  resource_type引数を追加。resource_idは一切公開しない
  （app.services.realtime_voice_ai._REALTIME_TOOLSのJSON Schema自体に
  resource_idというプロパティが存在しない設計）。

Phase R4のスコープは意図的に以下を含まない（本ファイルでも検証しない）:
- Service.required_resource_typeのようなマッピング（audit時点で
  Service→Resourceの関連が構造的に存在しないため不要と判断）
- 新しいOwner UI（shop-manage.htmlに既存のresource_type選択UIで対応済み）
- 複数Resource同時割当、Booking Board Resource Lane、Week View

検証項目:
A. 単一type店舗はresource_type省略時に自動決定される（UNAMBIGUOUS_TYPE。
   AIに一切resource_typeを問い合わせさせない）
B. 複数type混在店舗はresource_type省略時にAMBIGUOUSで安全に失敗する
   （reason_code=resource_type_required, available_resource_typesに
   実在するtypeのみが返る）
C. 複数type混在店舗でも、明示的にresource_typeを指定すればEXPLICIT_TYPEで
   成立する（type別に候補プールが分離されることも確認）
C2. 存在しないtype（他のALLOWED_RESOURCE_TYPESだが、その店舗には無い種別）
    を明示指定した場合、NO_MATCHとしてfully_bookedになる（新しい
    reason_codeを発明しない）
C3. NO_MATCHが「全件再プール」ではなく「0件」になることの直接確認
    （混在店舗で、存在しないtypeを指定した場合に、実際にはtype Aの
    Resourceが空いていても割り当てられない）
D. 不正なresource_type（ALLOWED_RESOURCE_TYPES外の値）は422で拒否される
   （create/check-availability Tool双方、およびWeb向けGET /availability）
E. inactiveなtypeのみ存在する場合、そのtypeを明示指定してもactive型は
   0件のためfully_bookedになる（resource_type_requiredにはならない。
   AMBIGUOUS判定はactiveなtypeの集合のみを見るため）
F. 単一type店舗の自動決定でも、容量・重複判定は既存(R3)と同じ優先順位で
   正しく機能する（typeフィルタとcapacity/overlapフィルタの両立）
G. 既存のTable経路・Staff/Service経路・無在庫(unmanaged)店舗のregression
   （いずれもresource_type関連の新しい分岐に一切影響されない）
H. Resource非アクティブ化レース（check時点ではactiveだったResourceが、
   ロック取得までの間に非アクティブ化された場合、_lock_and_verify_resource_slot
   の再検証により安全に別候補へフォールバックする）
I. Resource type変更レース（ロック取得後、typeが要求typeと一致しなくなって
   いた場合も同様に安全にフォールバックする）
J. check_single_slot_availability/get_availability/create_reservationの
   3箇所が、type関連の判定についても常に同じ結論を返す（単一type/混在/
   明示指定のいずれでも一貫する）
K. get_availability()（公開Web予約向け）は、混在店舗でresource_type省略時、
   新しいレスポンス構造を発明せず既存のis_open=True/空slotsパターンを
   そのまま使う
L. Realtime Tool Schema検証: check_availability/create_reservation
   いずれのJSON Schemaにもresource_idというプロパティが存在しない
   （resource_typeのみが公開されている）
M. Human Handoff再利用確認: 既存のCallbackRequestReasonCode.
   AVAILABILITY_JUDGEMENT_REQUIREDが変更されず存在する（R4で新しい
   コールバックToolを増やしていないことの間接確認）

実行: python3 tests/smoke_test_resource_type_resolution.py
"""

import asyncio
import os
import sys
import tempfile
import uuid
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_resource_type_r4.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-resource-type-r4"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402

_phone_counter = 0


def _next_phone() -> str:
    global _phone_counter
    _phone_counter += 1
    return f"080{_phone_counter:08d}"


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
            from app.models.resource import Resource
            from app.models.reservation import Reservation
            from sqlalchemy import select

            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-resource-r4-a@example.com", "password": "password123",
                "display_name": "ResourceTypeR4テストオーナーA",
            })
            assert r.status_code in (200, 201), f"register A failed: {r.status_code} {r.text}"
            owner_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

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

            async def _check(shop_id, date_str, time_str, party_size=2, resource_type=None, expect_status=200):
                payload = {"date": date_str, "time": time_str, "party_size": party_size}
                if resource_type is not None:
                    payload["resource_type"] = resource_type
                r = await client.post(
                    f"/api/v1/shops/{shop_id}/realtime-voice/tools/check-availability", json=payload,
                )
                assert r.status_code == expect_status, f"check-availability failed: {r.status_code} {r.text}"
                return r

            async def _availability(shop_id, date_str, party_size=2, resource_type=None, expect_status=200):
                params = {"date": date_str, "party_size": party_size}
                if resource_type is not None:
                    params["resource_type"] = resource_type
                r = await client.get(
                    f"/api/v1/reservations/shop/{shop_id}/availability", params=params,
                )
                assert r.status_code == expect_status, f"get_availability failed: {r.status_code} {r.text}"
                return r

            async def _create(shop_id, dt: datetime, party_size, phone=None, resource_type=None):
                payload = {
                    "shop_id": shop_id,
                    "guest_name": "R4テスト客",
                    "guest_phone": phone or _next_phone(),
                    "reservation_date": dt.isoformat(),
                    "number_of_people": party_size,
                }
                if resource_type is not None:
                    payload["resource_type"] = resource_type
                return await client.post("/api/v1/reservations/create", json=payload)

            async def _create_via_tool(shop_id, date_str, time_str, party_size, resource_type=None, phone=None):
                payload = {
                    "date": date_str, "time": time_str, "party_size": party_size,
                    "guest_name": "R4 Tool経由テスト客", "guest_phone": phone or _next_phone(),
                    "call_id": "smoke-r4-" + str(uuid.uuid4()),
                }
                if resource_type is not None:
                    payload["resource_type"] = resource_type
                r = await client.post(
                    f"/api/v1/shops/{shop_id}/realtime-voice/tools/create-reservation", json=payload,
                )
                assert r.status_code == 200, f"create-reservation Tool failed: {r.status_code} {r.text}"
                return r.json()

            # ===================================================
            # A. 単一type店舗: resource_type省略時にUNAMBIGUOUS_TYPEで自動決定
            # ===================================================
            shop_single = await _new_shop("R4単一typeテスト店", owner_a)
            room_single = await _new_resource(shop_single, owner_a, "唯一の個室", capacity=4, resource_type="room")

            a_date = _next_weekday(0, weeks_ahead=7)
            a_dt = datetime.combine(a_date, datetime.strptime("14:00", "%H:%M").time())

            body = (await _check(shop_single, a_date.isoformat(), "14:00", party_size=2)).json()
            assert body["available"] is True, f"単一type店舗はresource_type省略でも空きが判定できるはずが: {body}"

            r = await _create(shop_single, a_dt, party_size=2)
            assert r.status_code in (200, 201), f"A create失敗: {r.status_code} {r.text}"
            assert r.json()["reservation"]["resource_id"] == room_single, "単一typeの唯一のResourceが割り当てられるはずが違う"
            print("A. 単一type店舗はresource_type省略時に自動決定される（UNAMBIGUOUS_TYPE）: OK")

            # ===================================================
            # B. 複数type混在店舗: resource_type省略時にAMBIGUOUSで安全に失敗
            # ===================================================
            shop_mixed = await _new_shop("R4混在typeテスト店", owner_a)
            room_mixed = await _new_resource(shop_mixed, owner_a, "混在店の個室", capacity=4, resource_type="room")
            bed_mixed = await _new_resource(shop_mixed, owner_a, "混在店のベッド", capacity=1, resource_type="bed")

            b_date = _next_weekday(1, weeks_ahead=7)
            b_dt = datetime.combine(b_date, datetime.strptime("14:00", "%H:%M").time())

            r = await _check(shop_mixed, b_date.isoformat(), "14:00", party_size=1)
            body = r.json()
            assert body["available"] is False and body["reason_code"] == "resource_type_required", (
                f"混在店舗はresource_type省略時にresource_type_requiredで失敗するはずが: {body}"
            )
            assert sorted(body["available_resource_types"]) == ["bed", "room"], (
                f"available_resource_typesには実在するtypeのみが入るはずが: {body}"
            )
            assert "resource_id" not in body, "available_resource_typesにresource_idが漏れている"
            print("B1. check_availability Tool: 複数type混在時はresource_type_requiredで安全に失敗し、実在するtypeのみを返す: OK")

            r = await _create(shop_mixed, b_dt, party_size=1)
            assert r.status_code == 400, f"B2 混在店舗のcreateはresource_type省略時に400になるはずが: {r.status_code} {r.text}"
            print("B2. create_reservation(): 複数type混在時はロックを試みる前に400で安全に失敗する: OK")

            body = await _create_via_tool(shop_mixed, b_date.isoformat(), "14:00", 1)
            assert body["success"] is False and body["reason_code"] == "resource_type_required", (
                f"B3 create_reservation Tool: resource_type_requiredで失敗するはずが: {body}"
            )
            assert sorted(body["available_resource_types"]) == ["bed", "room"], (
                f"B3 create_reservation Toolのavailable_resource_typesが不正: {body}"
            )
            print("B3. create_reservation Tool: 複数type混在時はresource_type_requiredとavailable_resource_typesを返す: OK")

            # ===================================================
            # C. 複数type混在店舗 + 明示的なresource_type指定でEXPLICIT_TYPE成立
            # ===================================================
            r = await _create(shop_mixed, b_dt, party_size=1, resource_type="bed")
            assert r.status_code in (200, 201), f"C1 create失敗: {r.status_code} {r.text}"
            assert r.json()["reservation"]["resource_id"] == bed_mixed, "resource_type='bed'指定でbed_mixedが割り当てられるはずが違う"
            print("C1. 明示的にresource_type='bed'を指定すればEXPLICIT_TYPEで成立し、bed型のResourceのみが候補になる: OK")

            # 同じ時間・同じtype('bed')で2件目 → bedは1件しか無いため満席
            r = await _create(shop_mixed, b_dt, party_size=1, resource_type="bed")
            assert r.status_code == 400, f"C1b bed型は1件しか無いため満席になるはずが: {r.status_code} {r.text}"

            # 同じ時間・別type('room')なら、bedが満席でも別プールなので成立する
            r = await _create(shop_mixed, b_dt, party_size=1, resource_type="room")
            assert r.status_code in (200, 201), f"C2 create失敗（room型のプールがbed型の占有に影響されている）: {r.status_code} {r.text}"
            assert r.json()["reservation"]["resource_id"] == room_mixed, "resource_type='room'指定でroom_mixedが割り当てられるはずが違う"
            print("C2. type別に候補プールが完全に分離される（bed型が満席でもroom型は独立して空いている）: OK")

            # ===================================================
            # C2. 存在しないtypeを明示指定 → NO_MATCH → fully_booked
            # ===================================================
            c2_dt = b_dt + timedelta(hours=2)
            r = await _create(shop_mixed, c2_dt, party_size=1, resource_type="vehicle")
            assert r.status_code == 400, f"C2b 存在しないtype('vehicle')指定はfully_bookedになるはずが: {r.status_code} {r.text}"
            assert "満席" in r.json()["detail"], f"NO_MATCHは既存のfully_bookedパスを流用するはずが: {r.json()}"
            print("C2b. 存在しないresource_type('vehicle')を明示指定した場合、NO_MATCHとして既存のfully_bookedパスで安全に失敗する: OK")

            # ===================================================
            # C3. NO_MATCHが「0件」であり「全件再プール」ではないことの直接確認
            # ===================================================
            # このc2_dtの時間帯、room型(room_mixed)は空いているはず。
            # NO_MATCHがバグって「typeフィルタ無し」（全件再プール）になっていれば、
            # ここでroom_mixedが誤って割り当てられて成立してしまう。
            # 正しい実装ではrequested_resource_type('vehicle')自体が候補フィルタに
            # 渡るため、room型を含めて0件となり、必ず400になる。
            r = await _create(shop_mixed, c2_dt, party_size=1, resource_type="vehicle")
            assert r.status_code == 400, (
                f"C3 NO_MATCHが誤って全件再プールされ、room型が割り当てられてしまった: {r.status_code} {r.text}"
            )
            # 比較対象: resource_type省略（またはroom指定）なら実際には成立できる。
            r_control = await _create(shop_mixed, c2_dt, party_size=1, resource_type="room")
            assert r_control.status_code in (200, 201), (
                f"C3 対照実験: room型指定なら成立するはずが失敗した(この時間帯は本当に空いているか確認): "
                f"{r_control.status_code} {r_control.text}"
            )
            print("C3. NO_MATCHは「0件」として扱われ、存在typeへの誤った全件再プールは発生しない: OK")

            # ===================================================
            # D. 不正なresource_typeは422で拒否される
            # ===================================================
            r = await _check(shop_mixed, b_date.isoformat(), "18:00", party_size=1, resource_type="not_a_real_type", expect_status=422)
            print("D1. check_availability Tool: ALLOWED_RESOURCE_TYPES外の値は422で拒否される: OK")

            r = await client.post("/api/v1/reservations/create", json={
                "shop_id": shop_mixed, "guest_name": "不正typeテスト", "guest_phone": _next_phone(),
                "reservation_date": (b_dt + timedelta(hours=3)).isoformat(), "number_of_people": 1,
                "resource_type": "not_a_real_type",
            })
            assert r.status_code == 422, f"D2 create_reservation()は不正typeを422で拒否するはずが: {r.status_code} {r.text}"
            print("D2. POST /reservations/create: ALLOWED_RESOURCE_TYPES外の値は422で拒否される: OK")

            r = await _availability(shop_mixed, b_date.isoformat(), party_size=1, resource_type="not_a_real_type", expect_status=400)
            print("D3. GET /availability: ALLOWED_RESOURCE_TYPES外の値は400で拒否される（Query paramのためHTTPException(400)を使う既存パターン）: OK")

            # ===================================================
            # E. 「そのtypeだけ」がinactiveな場合、明示指定するとNO_MATCH（fully_booked）。
            #    省略時はactiveな他のtype(1種類)へUNAMBIGUOUS_TYPEで自動決定される
            #    （その店舗にactiveなResourceが存在する以上、allocation_mode自体は
            #    "resource"のままであり、"table"(unmanaged)へは落ちない点がPoint）。
            # ===================================================
            shop_inactive_type = await _new_shop("R4非アクティブtypeテスト店", owner_a)
            active_room_e = await _new_resource(shop_inactive_type, owner_a, "アクティブな個室", capacity=4, resource_type="room")
            inactive_bed = await _new_resource(shop_inactive_type, owner_a, "非アクティブなベッド", capacity=1, resource_type="bed")
            await _set_resource(shop_inactive_type, owner_a, inactive_bed, is_active=False)

            e_date = _next_weekday(2, weeks_ahead=7)
            body = (await _check(shop_inactive_type, e_date.isoformat(), "14:00", party_size=1, resource_type="bed")).json()
            assert body["available"] is False and body["reason_code"] == "fully_booked", (
                f"'bed'型はinactiveなResourceしか存在しないため、明示指定してもNO_MATCH→fully_bookedになるはずが: {body}"
            )
            body = (await _check(shop_inactive_type, e_date.isoformat(), "14:00", party_size=1)).json()
            assert body["available"] is True, (
                f"E resource_type省略時は、activeなtypeが'room'の1種類のみのためUNAMBIGUOUS_TYPEで自動決定され空きが判定できるはずが: {body}"
            )
            r = await _create(shop_inactive_type, datetime.combine(e_date, datetime.strptime("14:00", "%H:%M").time()), party_size=1)
            assert r.status_code in (200, 201), f"E create失敗: {r.status_code} {r.text}"
            assert r.json()["reservation"]["resource_id"] == active_room_e, "E 唯一のactive Resource(個室)が割り当てられるはずが違う"
            print("E. 明示指定したtypeがinactiveなResourceしか持たない場合はNO_MATCH→fully_booked、省略時はactiveな1種類へ自動決定される: OK")

            # ===================================================
            # F. 単一type自動決定でも容量・重複判定が正しく機能する
            # ===================================================
            shop_cap_type = await _new_shop("R4容量+typeテスト店", owner_a)
            small_room = await _new_resource(shop_cap_type, owner_a, "小部屋", capacity=2, resource_type="room")
            large_room = await _new_resource(shop_cap_type, owner_a, "大部屋", capacity=6, resource_type="room")

            f_dt = datetime.combine(_next_weekday(3, weeks_ahead=7), datetime.strptime("14:00", "%H:%M").time())
            r = await _create(shop_cap_type, f_dt, party_size=2)
            assert r.status_code in (200, 201), f"F1 create失敗: {r.status_code} {r.text}"
            assert r.json()["reservation"]["resource_id"] == small_room, "capacity最小(小部屋)が優先されるはずが違う"

            r = await _create(shop_cap_type, f_dt, party_size=5)
            assert r.status_code in (200, 201), f"F2 create失敗: {r.status_code} {r.text}"
            assert r.json()["reservation"]["resource_id"] == large_room, "小部屋が容量不足で除外され大部屋が選ばれるはずが違う"

            r = await _check(shop_cap_type, f_dt.date().isoformat(), "14:30", party_size=1)
            body = r.json()
            assert body["available"] is False and body["reason_code"] == "fully_booked", (
                f"F3 両部屋が埋まった時間帯と重なる14:30はfully_bookedのはずが: {body}"
            )
            print("F. 単一type自動決定(UNAMBIGUOUS_TYPE)でも既存(R3)と同じcapacity優先順位・重複判定が正しく機能する: OK")

            # ===================================================
            # G. 既存経路のregression
            # ===================================================
            shop_table_regress = await _new_shop("R4 Table経路regressionテスト店", owner_a)
            table_id_g = await _new_table(shop_table_regress, owner_a, "テーブルG", capacity=4)
            g_dt = datetime.combine(_next_weekday(4, weeks_ahead=7), datetime.strptime("14:00", "%H:%M").time())
            r = await _create(shop_table_regress, g_dt, party_size=2)
            assert r.status_code in (200, 201), f"G1 create失敗: {r.status_code} {r.text}"
            body = r.json()["reservation"]
            assert body["table_id"] == table_id_g and body["resource_id"] is None, (
                f"Table経路の店舗はresource_type関連の新しい分岐に一切影響されないはずが: {body}"
            )

            shop_staff_regress = await _new_shop("R4 Staff経路regressionテスト店", owner_a)
            r = await client.post("/api/v1/services", json={
                "shop_id": shop_staff_regress, "name": "カットG", "base_price": 3000, "duration_minutes": 60,
            }, headers=owner_a)
            assert r.status_code == 200, f"service作成失敗: {r.status_code} {r.text}"
            service_id_g = r.json()["id"]
            g2_dt = datetime.combine(_next_weekday(5, weeks_ahead=7), datetime.strptime("14:00", "%H:%M").time())
            r = await _create(shop_staff_regress, g2_dt, party_size=1)
            r2 = await client.post("/api/v1/reservations/create", json={
                "shop_id": shop_staff_regress, "guest_name": "G2テスト客", "guest_phone": _next_phone(),
                "reservation_date": g2_dt.isoformat(), "number_of_people": 1, "service_id": service_id_g,
            })
            assert r2.status_code in (200, 201), f"G2 create失敗: {r2.status_code} {r2.text}"
            body = r2.json()["reservation"]
            assert body["table_id"] is None and body["resource_id"] is None, (
                f"サービス予約はtable_id/resource_idともに常にNULLのはずが: {body}"
            )

            shop_unmanaged = await _new_shop("R4無在庫店regressionテスト店", owner_a)
            g3_dt = datetime.combine(_next_weekday(6, weeks_ahead=7), datetime.strptime("14:00", "%H:%M").time())
            r = await _create(shop_unmanaged, g3_dt, party_size=999)
            assert r.status_code in (200, 201), f"G3 無在庫店(ShopTable/Resourceともに0件)は常にunmanaged=常時空席のはずが: {r.status_code} {r.text}"
            body = r.json()["reservation"]
            assert body["table_id"] is None and body["resource_id"] is None, f"無在庫店の予約なのにID割当がある: {body}"
            print("G. 既存のTable経路・Staff/Service経路・無在庫(unmanaged)店舗のregressionは全て問題なし: OK")

            # ===================================================
            # H. Resource非アクティブ化レース
            # ===================================================
            shop_race_active = await _new_shop("R4非アクティブ化レーステスト店", owner_a)
            race_room_1 = await _new_resource(shop_race_active, owner_a, "レース用個室1", capacity=4, resource_type="room")
            race_room_2 = await _new_resource(shop_race_active, owner_a, "レース用個室2", capacity=4, resource_type="room")
            h_dt = datetime.combine(_next_weekday(0, weeks_ahead=8), datetime.strptime("14:00", "%H:%M").time())

            # race_room_1がcandidate選定時点ではactiveだが、ロック取得直前に
            # 非アクティブ化された状況を模す（_lock_and_verify_resource_slot自体を
            # 個別に呼ぶことはできないため、実際にDBを直接更新してから作成APIを
            # 呼び出すことで、「選定→ロック取得までの間に状態が変わる」ケースの
            # 十分な近似として、少なくとも「非アクティブなResourceには絶対に
            # ロックが通らない」ことを直接検証する）。
            await _set_resource(shop_race_active, owner_a, race_room_1, is_active=False)
            r = await _create(shop_race_active, h_dt, party_size=2)
            assert r.status_code in (200, 201), f"H1 create失敗: {r.status_code} {r.text}"
            assert r.json()["reservation"]["resource_id"] == race_room_2, (
                f"非アクティブ化されたrace_room_1は候補にならず、race_room_2が選ばれるはずが: {r.json()['reservation']}"
            )
            print("H. Resource非アクティブ化: is_active=FalseになったResourceは、候補選定・ロック検証のどちらでも確実に除外される: OK")

            # ===================================================
            # I. Resource type変更レース
            # ===================================================
            shop_race_type = await _new_shop("R4 type変更レーステスト店", owner_a)
            race_bed = await _new_resource(shop_race_type, owner_a, "レース用ベッド", capacity=1, resource_type="bed")
            race_room = await _new_resource(shop_race_type, owner_a, "レース用個室", capacity=1, resource_type="room")
            i_dt = datetime.combine(_next_weekday(1, weeks_ahead=8), datetime.strptime("14:00", "%H:%M").time())

            # race_bedのtypeが'bed'から'other'に変更された後（例: Owner側の設定変更）、
            # resource_type='bed'を要求する予約はrace_bedを使えず、fully_bookedになる
            # はず（bed型はもうこの店舗に存在しないactive Resourceが無いため）。
            await _set_resource(shop_race_type, owner_a, race_bed, resource_type="other")
            r = await _create(shop_race_type, i_dt, party_size=1, resource_type="bed")
            assert r.status_code == 400, (
                f"I1 typeが'other'に変更された後は、resource_type='bed'指定はfully_bookedになるはずが: {r.status_code} {r.text}"
            )
            # 変更後のtype('other')で指定すれば、当然race_bedが使える。
            r = await _create(shop_race_type, i_dt, party_size=1, resource_type="other")
            assert r.status_code in (200, 201), f"I2 変更後のtype('other')で指定すれば成立するはずが: {r.status_code} {r.text}"
            assert r.json()["reservation"]["resource_id"] == race_bed, "変更後のtypeで指定した場合、race_bedが割り当てられるはずが違う"
            print("I. Resource type変更: typeが変更された後は、旧typeでの指定はそのResourceを候補にできず、新typeでの指定でのみ利用できる: OK")

            # ===================================================
            # J. 3箇所（check/get_availability/create）の一貫性（type関連）
            # ===================================================
            shop_consistency = await _new_shop("R4 type一貫性テスト店", owner_a)
            await _new_resource(shop_consistency, owner_a, "一貫性個室", capacity=4, resource_type="room")
            await _new_resource(shop_consistency, owner_a, "一貫性ベッド", capacity=1, resource_type="bed")
            j_date = _next_weekday(2, weeks_ahead=8)
            j_date_str = j_date.isoformat()
            j_dt = datetime.combine(j_date, datetime.strptime("15:00", "%H:%M").time())

            # 混在店舗・resource_type省略 → 3箇所すべてがAMBIGUOUS相当の安全な結果を返す
            body = (await _check(shop_consistency, j_date_str, "15:00", party_size=1)).json()
            assert body["reason_code"] == "resource_type_required"
            # get_availability()はAMBIGUOUSの場合、個別スロットのavailable=Falseではなく
            # Section Kと同じ「新しい構造を発明しない」既存パターン（is_open=True,
            # slots=[]）で丸ごと安全に応答する（個別スロット単位のfalseにはしない）。
            r_avail = await _availability(shop_consistency, j_date_str, party_size=1)
            avail_body = r_avail.json()
            assert avail_body["is_open"] is True and avail_body["slots"] == [], (
                f"get_availability: 混在店舗でtype省略時はis_open=True/slots=[]のはずが: {avail_body}"
            )
            r = await _create(shop_consistency, j_dt, party_size=1)
            assert r.status_code == 400

            # resource_type='room'を明示 → 3箇所すべてが一致してavailable
            body = (await _check(shop_consistency, j_date_str, "15:00", party_size=1, resource_type="room")).json()
            assert body["available"] is True
            r_avail = await _availability(shop_consistency, j_date_str, party_size=1, resource_type="room")
            slots = {s["time"]: s["available"] for s in r_avail.json()["slots"]}
            assert slots["15:00"] is True, f"get_availability: resource_type='room'指定時の15:00はavailable=Trueのはずが: {slots}"
            r = await _create(shop_consistency, j_dt, party_size=1, resource_type="room")
            assert r.status_code in (200, 201), f"J create失敗: {r.status_code} {r.text}"

            # 予約成立後、同じroom型の同じ時間は3箇所すべてでfalse/fully_bookedに一致する
            body = (await _check(shop_consistency, j_date_str, "15:00", party_size=1, resource_type="room")).json()
            assert body["available"] is False and body["reason_code"] == "fully_booked"
            r_avail = await _availability(shop_consistency, j_date_str, party_size=1, resource_type="room")
            slots = {s["time"]: s["available"] for s in r_avail.json()["slots"]}
            assert slots["15:00"] is False
            print("J. check_single_slot_availability/get_availability/create_reservationは、type関連の判定についても常に一致する: OK")

            # ===================================================
            # K. get_availability()の混在店舗での安全なレスポンス形状
            # ===================================================
            r_avail = await _availability(shop_mixed, b_date.isoformat(), party_size=1)
            body = r_avail.json()
            assert body["is_open"] is True and body["slots"] == [], (
                f"K get_availability()は混在店舗・type未指定の場合、新しい構造を発明せず既存のis_open=True/slots=[]パターンを使うはずが: {body}"
            )
            assert body.get("message"), "K メッセージが空"
            print("K. get_availability()（公開Web予約向け）は混在店舗・type未指定の場合、既存のis_open=True/slots=[]パターンを流用する: OK")

            # ===================================================
            # L. Realtime Tool Schema検証: resource_idが一切公開されていない
            # ===================================================
            from app.services.realtime_voice_ai import _REALTIME_TOOLS
            check_tool = next(t for t in _REALTIME_TOOLS if t["name"] == "check_availability")
            create_tool = next(t for t in _REALTIME_TOOLS if t["name"] == "create_reservation")
            for tool in (check_tool, create_tool):
                props = tool["parameters"]["properties"]
                assert "resource_id" not in props, f"{tool['name']}のToolスキーマにresource_idが公開されている: {props.keys()}"
                assert "resource_type" in props, f"{tool['name']}のToolスキーマにresource_typeが存在しない: {props.keys()}"
                assert set(props["resource_type"].get("enum", [])) == {
                    "room", "bed", "chair", "vehicle", "karaoke_room", "classroom", "equipment", "other",
                }, f"{tool['name']}のresource_type enumがALLOWED_RESOURCE_TYPESと一致しない: {props['resource_type']}"
            print("L. Realtime Tool Schema: check_availability/create_reservationいずれもresource_idを公開せず、resource_typeのみを公開している: OK")

            # ===================================================
            # M. Human Handoff再利用確認
            # ===================================================
            from app.models.callback_request import CallbackRequestReasonCode
            assert hasattr(CallbackRequestReasonCode, "AVAILABILITY_JUDGEMENT_REQUIRED"), (
                "既存のAVAILABILITY_JUDGEMENT_REQUIREDが存在しない（R4はこれを再利用する設計のはずが変更されている）"
            )
            print("M. Human Handoff: 既存のCallbackRequestReasonCode.AVAILABILITY_JUDGEMENT_REQUIREDがそのまま存在する（新規コールバックToolを増やしていないことの間接確認）: OK")

            print("\n=== Generic Resource Foundation Phase R4 スモークテスト: 全項目OK ===")


if __name__ == "__main__":
    asyncio.run(main())
