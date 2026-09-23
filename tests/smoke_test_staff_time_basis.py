"""
Staff Reservation Time Basis Consistency smoke test.

app/routers/staff.py の delete_staff() が「今後の有効な予約」を判定する際、
以前は datetime.utcnow()（真のUTC時刻）を reservation_date（JST-naiveな値）
と比較していたため、日本時間の日付境界付近で最大9時間のズレが生じ、既に
JST基準では過去になった予約を「まだ未来」と誤判定してdelete_staff()を誤って
ブロックする可能性があった。

修正は、app/routers/reservations.pyの既存single source of truthである
_reservation_basis_now()（JST-naive基準の「現在時刻」）を再利用するだけ。
status条件・>=という境界・staff_idでの絞り込み・エラーメッセージ・
HTTPステータス・delete_staffの他の挙動は一切変更していない。

再現・検証方法（実時計に依存しない決定的な方法。
smoke_test_reservation_time_basis.pyと同じ技法）:
日本には夏時間がなくJSTは常にUTC+9固定なので、
_reservation_basis_now() ≈ datetime.utcnow() + timedelta(hours=9)
が常に成り立つ。

reservation_date = 実行時のdatetime.utcnow() + timedelta(hours=4) は、
- 誤ったutcnow()基準では reservation_date >= utcnow() → True（"未来"と誤判定）
- 正しいJST基準では reservation_date(utcnow+4h) < _reservation_basis_now()(≈utcnow+9h)
  → 実際には過去
という食い違いを、いつ実行しても再現できる（セクションA）。

reservation_date = 実行時のdatetime.utcnow() + timedelta(hours=10) は、
正しいJST基準でも真に未来（セクションB以降の「本物の未来」ケース）。

exact-now境界（セクションE）だけは実時計のズレを避けるため、
unittest.mock.patchで app.routers.staff._reservation_basis_now を固定する
（stdlibのみ。新しい重いdependencyは追加しない）。
"""
import asyncio
import os
import sys
import tempfile
import uuid
from datetime import datetime, date, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_stafftime.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-staff-time-basis"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402

BASE_URL = "http://test"
_phone_counter = [90000000]


def _next_phone() -> str:
    _phone_counter[0] += 1
    return f"090{_phone_counter[0]:08d}"[:11]


def _next_weekday(target_weekday: int, weeks_ahead: int = 2):
    today = date.today()
    days_ahead = (target_weekday - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead + 7 * (weeks_ahead - 1))


async def _new_shop(client: httpx.AsyncClient, owner: dict, name: str, session_factory=None) -> str:
    r = await client.post("/api/v1/shops/register", json={
        "name": name, "category": "美容室", "address": "東京都渋谷区5-5-5",
    }, headers=owner)
    assert r.status_code in (200, 201), r.text
    shop_id = r.json()["shop_id"]

    if session_factory is not None:
        # J/K(Capacity/Resource Mutation Safety regression)は実際の
        # create-reservation Toolを経由するため、営業時間・予約受付ON・
        # 予約時間枠の設定が必要（smoke_test_resource_mutation_safety.pyと
        # 同じセットアップ）。
        hours_payload = {"hours": [
            {"day_of_week": d, "opening_time": "10:00:00", "closing_time": "22:00:00", "is_closed": False}
            for d in range(7)
        ]}
        r = await client.put(f"/api/v1/shops/{shop_id}/hours", json=hours_payload, headers=owner)
        assert r.status_code == 200, f"set hours failed: {r.status_code} {r.text}"

        from app.models.shop import Shop
        async with session_factory() as session:
            shop_obj = await session.get(Shop, shop_id)
            shop_obj.reservations_enabled = True
            shop_obj.reservation_duration_minutes = 60
            await session.commit()

    return shop_id


async def _new_staff(client: httpx.AsyncClient, owner: dict, shop_id: str, name: str) -> str:
    r = await client.post("/api/v1/staff", json={
        "shop_id": shop_id, "name": name,
    }, headers=owner)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _insert_reservation(session_factory, shop_id, staff_id, reservation_date, status="confirmed", number_of_people=2):
    from app.models.reservation import Reservation

    res_id = str(uuid.uuid4())
    async with session_factory() as session:
        res = Reservation(
            id=res_id, shop_id=shop_id, table_id=None, staff_id=staff_id, service_id=None,
            guest_name="テスト客", guest_phone=_next_phone(),
            reservation_date=reservation_date, duration_minutes=60, number_of_people=number_of_people,
            status=status, reservation_source="manual",
            created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        )
        session.add(res)
        await session.commit()
    return res_id


async def main():
    from app.main import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-stafftime@example.com", "password": "password123",
                "display_name": "StaffTimeオーナー",
            })
            assert r.status_code in (200, 201), r.text
            owner = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r2 = await client.post("/api/v1/auth/register", json={
                "email": "owner-stafftime-other@example.com", "password": "password123",
                "display_name": "他テナントオーナー",
            })
            assert r2.status_code in (200, 201), r2.text
            other_owner = {"Authorization": f"Bearer {r2.json()['access_token']}"}

            from app.database import AsyncSessionLocal

            utcnow = datetime.utcnow()
            jst_past_but_utc_naive_future = utcnow + timedelta(hours=4)   # JST基準では過去
            jst_genuinely_future = utcnow + timedelta(hours=10)          # JST基準でも未来

            # ===== A. RED再現ケース: JST-past-but-UTC-naive-future の予約が
            #          あっても、修正後はdelete_staffがブロックしない(critical) =====
            shop_a = await _new_shop(client, owner, "A店")
            staff_a = await _new_staff(client, owner, shop_a, "スタッフA")
            await _insert_reservation(AsyncSessionLocal, shop_a, staff_a, jst_past_but_utc_naive_future, status="confirmed")
            r = await client.delete(f"/api/v1/staff/{staff_a}", headers=owner)
            assert r.status_code == 200, f"A(RED再現): JST基準で過去の予約のせいで誤ってブロックされた: {r.status_code} {r.text}"
            print("A. RED再現ケース(JST-past-but-utc-naive-future) → delete OK: PASS")

            # ===== B. JST-past → delete OK =====
            shop_b = await _new_shop(client, owner, "B店")
            staff_b = await _new_staff(client, owner, shop_b, "スタッフB")
            await _insert_reservation(AsyncSessionLocal, shop_b, staff_b, jst_past_but_utc_naive_future, status="pending")
            r = await client.delete(f"/api/v1/staff/{staff_b}", headers=owner)
            assert r.status_code == 200, f"B: JST-pastのpending予約でdelete OKになるはず: {r.status_code} {r.text}"
            print("B. JST-past(pending) → delete OK: PASS")

            # ===== C. JST-future pending → delete NG =====
            shop_c = await _new_shop(client, owner, "C店")
            staff_c = await _new_staff(client, owner, shop_c, "スタッフC")
            await _insert_reservation(AsyncSessionLocal, shop_c, staff_c, jst_genuinely_future, status="pending")
            r = await client.delete(f"/api/v1/staff/{staff_c}", headers=owner)
            assert r.status_code == 400, f"C: JST-futureのpending予約でdelete NGになるはず: {r.status_code} {r.text}"
            print("C. JST-future(pending) → delete NG: PASS")

            # ===== D. JST-future confirmed → delete NG =====
            shop_d = await _new_shop(client, owner, "D店")
            staff_d = await _new_staff(client, owner, shop_d, "スタッフD")
            await _insert_reservation(AsyncSessionLocal, shop_d, staff_d, jst_genuinely_future, status="confirmed")
            r = await client.delete(f"/api/v1/staff/{staff_d}", headers=owner)
            assert r.status_code == 400, f"D: JST-futureのconfirmed予約でdelete NGになるはず: {r.status_code} {r.text}"
            print("D. JST-future(confirmed) → delete NG: PASS")

            # ===== E. exact-now境界 (reservation_date == _reservation_basis_now()) → delete NG (>=を維持) =====
            shop_e = await _new_shop(client, owner, "E店")
            staff_e = await _new_staff(client, owner, shop_e, "スタッフE")
            fixed_now = datetime.now(timezone(timedelta(hours=9))).replace(tzinfo=None)
            await _insert_reservation(AsyncSessionLocal, shop_e, staff_e, fixed_now, status="confirmed")
            with patch("app.routers.staff._reservation_basis_now", return_value=fixed_now):
                r = await client.delete(f"/api/v1/staff/{staff_e}", headers=owner)
            assert r.status_code == 400, f"E: exact-now境界(>=)でdelete NGになるはず: {r.status_code} {r.text}"
            print("E. exact-now境界(>=維持) → delete NG: PASS")

            # ===== F. cancelled future → delete OK =====
            shop_f = await _new_shop(client, owner, "F店")
            staff_f = await _new_staff(client, owner, shop_f, "スタッフF")
            await _insert_reservation(AsyncSessionLocal, shop_f, staff_f, jst_genuinely_future, status="cancelled")
            r = await client.delete(f"/api/v1/staff/{staff_f}", headers=owner)
            assert r.status_code == 200, f"F: cancelledな未来予約はdelete OKになるはず: {r.status_code} {r.text}"
            print("F. cancelled future → delete OK: PASS")

            # ===== G. 別スタッフの今後の予約は対象スタッフのdeleteを妨げない =====
            shop_g = await _new_shop(client, owner, "G店")
            staff_g_target = await _new_staff(client, owner, shop_g, "スタッフG-対象")
            staff_g_other = await _new_staff(client, owner, shop_g, "スタッフG-別")
            await _insert_reservation(AsyncSessionLocal, shop_g, staff_g_other, jst_genuinely_future, status="confirmed")
            r = await client.delete(f"/api/v1/staff/{staff_g_target}", headers=owner)
            assert r.status_code == 200, f"G: 別スタッフの未来予約は対象スタッフのdeleteを妨げないはず: {r.status_code} {r.text}"
            r_other_still_blocked = await client.delete(f"/api/v1/staff/{staff_g_other}", headers=owner)
            assert r_other_still_blocked.status_code == 400, "G: 予約を持つ側のスタッフは引き続きブロックされるはず"
            print("G. 別スタッフの今後の予約 → 対象スタッフのdelete OK / 予約保有スタッフはNG: PASS")

            # ===== H. tenant isolation: 他テナントは操作不可 =====
            shop_h = await _new_shop(client, owner, "H店")
            staff_h = await _new_staff(client, owner, shop_h, "スタッフH")
            r = await client.delete(f"/api/v1/staff/{staff_h}", headers=other_owner)
            assert r.status_code == 403, f"H: 他テナントはこのスタッフを削除できないはず: {r.status_code} {r.text}"
            print("H. tenant isolation(他テナントdelete) → 403: PASS")

            # ===== I. Table time basis regression (shop_tables.pyの修正が壊れていないか) =====
            shop_i = await _new_shop(client, owner, "I店")
            r = await client.post(f"/api/v1/shops/{shop_i}/tables", json={
                "name": "テーブルI", "capacity": 6,
            }, headers=owner)
            assert r.status_code == 200, r.text
            table_i = r.json()["id"]
            await _insert_reservation(AsyncSessionLocal, shop_i, None, jst_past_but_utc_naive_future, status="confirmed")
            # 上のヘルパーはstaff_idを想定しているが、table_idを直接指定し直す
            from app.database import AsyncSessionLocal as ASL
            from app.models.reservation import Reservation as ResModel
            async with ASL() as session:
                from sqlalchemy import select as sa_select
                result = await session.execute(
                    sa_select(ResModel).filter(ResModel.shop_id == shop_i).order_by(ResModel.created_at.desc())
                )
                res_row = result.scalars().first()
                res_row.table_id = table_i
                await session.commit()
            r = await client.delete(f"/api/v1/shops/{shop_i}/tables/{table_i}", headers=owner)
            assert r.status_code == 200, f"I: Table time basis regression失敗: {r.status_code} {r.text}"
            print("I. Table time basis regression(delete_table, JST-past) → delete OK: PASS")

            # ===== J. Capacity Mutation Safety regression =====
            # create-reservation Toolで実際に予約を作り、Owner PUT /api/v1/reservations/{id}
            # で定員超過へのnumber_of_people変更がNGになることを確認する
            # (smoke_test_capacity_mutation.pyと同じ経路・同じ検証)。
            target_date = _next_weekday(2, weeks_ahead=2)
            shop_j = await _new_shop(client, owner, "J店", session_factory=AsyncSessionLocal)
            r = await client.post(f"/api/v1/shops/{shop_j}/tables", json={
                "name": "テーブルJ", "capacity": 6,
            }, headers=owner)
            assert r.status_code == 200, r.text
            r = await client.post(
                f"/api/v1/shops/{shop_j}/realtime-voice/tools/create-reservation",
                json={
                    "date": target_date.isoformat(), "time": "13:00", "party_size": 2,
                    "guest_name": "定員テスト客", "guest_phone": _next_phone(),
                    "call_id": "smoke-stafftime-j-" + str(uuid.uuid4()),
                },
            )
            assert r.status_code == 200 and r.json().get("success") is True, f"J: 予約作成失敗: {r.status_code} {r.text}"
            reservation_j = r.json()["reservation_id"]
            r = await client.put(f"/api/v1/reservations/{reservation_j}", json={
                "number_of_people": 8,
            }, headers=owner)
            assert r.status_code == 400, f"J: Capacity Mutation Safety regression失敗(2→8名がNGになるはず): {r.status_code} {r.text}"
            print("J. Capacity Mutation Safety regression(2→8名 NG) → PASS")

            # ===== K. Resource Mutation Safety regression =====
            # 今後の5名予約があるテーブルのcapacityを6→4へ減らそうとしてNGになる
            # ことを確認する(smoke_test_resource_mutation_safety.pyと同じ経路)。
            shop_k = await _new_shop(client, owner, "K店", session_factory=AsyncSessionLocal)
            r = await client.post(f"/api/v1/shops/{shop_k}/tables", json={
                "name": "テーブルK", "capacity": 6,
            }, headers=owner)
            assert r.status_code == 200, r.text
            table_k = r.json()["id"]
            r = await client.post(
                f"/api/v1/shops/{shop_k}/realtime-voice/tools/create-reservation",
                json={
                    "date": target_date.isoformat(), "time": "14:00", "party_size": 5,
                    "guest_name": "定員テスト客K", "guest_phone": _next_phone(),
                    "call_id": "smoke-stafftime-k-" + str(uuid.uuid4()),
                },
            )
            assert r.status_code == 200 and r.json().get("success") is True, f"K: 予約作成失敗: {r.status_code} {r.text}"
            r = await client.put(f"/api/v1/shops/{shop_k}/tables/{table_k}", json={
                "capacity": 4,
            }, headers=owner)
            assert r.status_code == 400, f"K: Resource Mutation Safety regression失敗(6→4がNGになるはず): {r.status_code} {r.text}"
            print("K. Resource Mutation Safety regression(future 5名予約が6→4をNG) → PASS")

            print("\n=== ALL smoke_test_staff_time_basis.py CHECKS PASSED ===")


asyncio.run(main())
