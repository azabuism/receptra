"""
Reservation Intelligence "Reservation Time Basis Consistency (JST/UTC Safety Fix)"
スモークテスト

背景（本フェーズで修正した内容）:

app/routers/shop_tables.py の _find_future_active_table_reservations()
（delete_table()・update_table()のcapacity decrease検証の両方が使う
「今後の有効な予約」判定）が、「現在時刻」の基準として datetime.utcnow()
（真のUTC時刻）を使っていた。

一方、RECEPTRAのReservation.reservation_dateは、Web予約・チャット予約・
Realtime Voice予約のすべての経路で一貫して「JST(日本時間)の壁時計時刻を
表すnaive datetime」として保存されている
（app/routers/reservations.pyの_reservation_basis_now()のdocstring、
Phase3E-3の調査結果を参照）。

そのため、JST-naiveなreservation_dateとUTC-naiveなdatetime.utcnow()を
直接比較すると、日本時間の日付境界付近で最大9時間のズレが生じ、
「JST基準では既に過去になっている予約」を「まだ未来」と誤判定しうる
バグがあった（delete_table()や、前フェーズで追加したcapacity decrease
検証を、本来ブロック不要なケースで誤ってブロックしてしまう）。

修正: reservations.py側がPhase3E-3で確立した既存のsingle source of truth
である _reservation_basis_now()（reservation_date系の値と比較するための
「JST基準に揃えた現在時刻」）を、_find_future_active_table_reservations()
内のdatetime.utcnow()の代わりにそのまま再利用した。新しいtimezone helper
は発明していない。status条件（pending/confirmed）・比較演算子(>=)の意味は
一切変更していない。

テスト設計上の注意（実時計に依存しない決定的な再現方法）:
日本には夏時間がなく、JSTは常にUTC+9固定であるため、
_reservation_basis_now() ≈ datetime.utcnow() + timedelta(hours=9)
が常に成り立つ。これを使い、実行時刻に関係なく常に同じ結果になる
相対オフセットで「JST基準では過去だが、utcnow()基準では未来に見える」
状態を決定的に作る（時刻のfreeze/monkeypatchや新しい依存を追加せず、
実際のHTTPリクエスト経由のE2Eテストのまま安全に固定時刻相当の再現性を
得ている）:
- reservation_date = 実行時のdatetime.utcnow() + 4時間
  → utcnow()基準では「未来」に見えるが、JST基準では約5時間前
    （既に過去）。修正前バグが誤って「未来」と判定していたケース。
- reservation_date = 実行時のdatetime.utcnow() + 10時間
  → JST基準でも真に未来（約1時間後）。修正の前後どちらでも
    正しく「未来」と判定されるべきケース（回帰確認）。

検証項目（Section35に対応）:
A.【最重要・RED】JST基準では過去(utcnow+4h)の予約はcapacity decrease/
  deleteをブロックしない
B. 真にJST未来(utcnow+10h)の予約はcapacity decrease/deleteをブロックする
C. exact-now境界（reservation_date == _reservation_basis_now()）は
  既存の>=semanticsにより「未来active」扱いのまま
D. cancelledの未来予約はブロックしない（今回の修正でstatus semanticsは
  変更していない）
E/F. delete_table()のfuture NG / JST-past OK
G/H. update_table()capacity decreaseのfuture NG / JST-past OK
I. 前々フェーズ（Capacity Mutation Safety）のregression
J. 前フェーズ（Resource Mutation Safety）のregression
   （create-reservation Tool経由の実際の未来予約でcapacity decreaseが
   正しくブロックされること）

実行: python3 tests/smoke_test_reservation_time_basis.py
"""

import asyncio
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, date, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_time_basis.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-time-basis"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402
from unittest.mock import patch  # noqa: E402

_JST = timezone(timedelta(hours=9))
_phone_counter = 0


def _next_weekday(target_weekday: int, weeks_ahead: int = 2):
    today = date.today()
    days_ahead = (target_weekday - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead + 7 * (weeks_ahead - 1))


def _next_phone():
    global _phone_counter
    _phone_counter += 1
    return f"080{_phone_counter:08d}"


async def main():
    from app.main import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-timebasis-a@example.com", "password": "password123",
                "display_name": "TimeBasisテストオーナー",
            })
            assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
            owner_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

            from app.database import AsyncSessionLocal
            from app.models.shop import Shop, ShopTable
            from app.models.reservation import Reservation
            from app.routers.reservations import _reservation_basis_now
            from sqlalchemy import select

            async def _new_shop(name: str):
                r = await client.post("/api/v1/shops/register", json={
                    "name": name, "category": "食堂", "address": "東京都渋谷区10-10-10",
                }, headers=owner_a)
                assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
                shop_id = r.json()["shop_id"]
                hours_payload = {"hours": [
                    {"day_of_week": d, "opening_time": "00:00:00", "closing_time": "23:59:00", "is_closed": False}
                    for d in range(7)
                ]}
                r = await client.put(f"/api/v1/shops/{shop_id}/hours", json=hours_payload, headers=owner_a)
                assert r.status_code == 200, f"set hours failed: {r.status_code} {r.text}"
                async with AsyncSessionLocal() as session:
                    shop_obj = await session.get(Shop, shop_id)
                    shop_obj.reservations_enabled = True
                    shop_obj.reservation_duration_minutes = 30
                    await session.commit()
                return shop_id

            async def _new_table(shop_id: str, capacity: int, name: str = "テストテーブル"):
                r = await client.post(f"/api/v1/shops/{shop_id}/tables", json={
                    "name": name, "capacity": capacity,
                }, headers=owner_a)
                assert r.status_code == 200, f"create table failed: {r.status_code} {r.text}"
                return r.json()["id"]

            async def _insert_reservation(shop_id: str, table_id: str, reservation_dt, number_of_people: int, status: str = "confirmed"):
                res_id = str(uuid.uuid4())
                async with AsyncSessionLocal() as session:
                    res = Reservation(
                        id=res_id, shop_id=shop_id, table_id=table_id, staff_id=None, service_id=None,
                        guest_name="TimeBasisテスト客", guest_phone=_next_phone(),
                        reservation_date=reservation_dt, duration_minutes=30, number_of_people=number_of_people,
                        status=status, reservation_source="manual",
                        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                    )
                    session.add(res)
                    await session.commit()
                return res_id

            async def _get_table(table_id: str):
                async with AsyncSessionLocal() as session:
                    return await session.get(ShopTable, table_id)

            # ===== A.【最重要・RED】JST基準では過去(utcnow+4h)はブロックしない =====
            shop_a = await _new_shop("TimeBasisテストA食堂")
            table_id = await _new_table(shop_a, 6)
            jst_past_dt = datetime.utcnow() + timedelta(hours=4)
            await _insert_reservation(shop_a, table_id, jst_past_dt, 5)

            jst_now_check = datetime.now(_JST).replace(tzinfo=None)
            assert jst_past_dt < jst_now_check, (
                f"テスト前提が崩れている: reservation_date({jst_past_dt})がJST基準で"
                f"過去になっていない(now={jst_now_check})"
            )

            r = await client.put(f"/api/v1/shops/{shop_a}/tables/{table_id}", json={
                "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 200, (
                f"【最重要】JST基準で過去の予約(5名)がcapacity decrease(6->4)を誤ってブロックした: "
                f"{r.status_code} {r.text}"
            )
            print("A.【最重要・RED】JST基準では過去(utcnow+4h)の予約はcapacity decreaseをブロックしない: OK")

            # ===== B. 真にJST未来(utcnow+10h)はブロックする =====
            shop_b = await _new_shop("TimeBasisテストB食堂")
            table_id = await _new_table(shop_b, 6)
            jst_future_dt = datetime.utcnow() + timedelta(hours=10)
            await _insert_reservation(shop_b, table_id, jst_future_dt, 5)

            jst_now_check = datetime.now(_JST).replace(tzinfo=None)
            assert jst_future_dt > jst_now_check, (
                f"テスト前提が崩れている: reservation_date({jst_future_dt})がJST基準で"
                f"未来になっていない(now={jst_now_check})"
            )

            r = await client.put(f"/api/v1/shops/{shop_b}/tables/{table_id}", json={
                "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 400, (
                f"真にJST未来の予約(5名)がcapacity decrease(6->4)を誤って許可した: {r.status_code} {r.text}"
            )
            t = await _get_table(table_id)
            assert t.capacity == 6
            print("B. 真にJST未来(utcnow+10h)の予約はcapacity decreaseをブロックする: OK")

            # ===== C. exact-now境界（既存の>=semanticsを維持） =====
            # 「reservation_date == その瞬間のnow」を実時間の経過なしに厳密に
            # 再現することは、実行に何らかの時間がかかる以上original不可能
            # （capture直後にPUTを送る間に実時間が進んでしまい、"=="の検証に
            # ならない）。そのため、この1ケースに限り標準ライブラリの
            # unittest.mock.patchで_reservation_basis_now()の戻り値をテスト内で
            # 固定し、クエリ実行時の「now」を厳密に一致させる（新しい重い
            # 依存を追加せず、標準ライブラリのみを使用。Section7の趣旨に反しない）。
            shop_c = await _new_shop("TimeBasisテストC食堂")
            table_id = await _new_table(shop_c, 6)
            fixed_now = datetime(2026, 6, 15, 12, 0, 0)
            await _insert_reservation(shop_c, table_id, fixed_now, 5)

            with patch("app.routers.shop_tables._reservation_basis_now", return_value=fixed_now):
                r = await client.put(f"/api/v1/shops/{shop_c}/tables/{table_id}", json={
                    "capacity": 4,
                }, headers=owner_a)
            assert r.status_code == 400, (
                f"exact-now境界(reservation_date == _reservation_basis_now())の予約が"
                f"「未来active」として扱われず、capacity decreaseが誤って成立した"
                f"（既存の>=semanticsが壊れている）: {r.status_code} {r.text}"
            )
            print("C. exact-now境界は既存の>=semanticsにより「未来active」扱いのまま: OK")

            # ===== D. cancelledの未来予約はブロックしない =====
            shop_d = await _new_shop("TimeBasisテストD食堂")
            table_id = await _new_table(shop_d, 6)
            future_dt = datetime.utcnow() + timedelta(hours=10)
            await _insert_reservation(shop_d, table_id, future_dt, 5, status="cancelled")

            r = await client.put(f"/api/v1/shops/{shop_d}/tables/{table_id}", json={
                "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 200, (
                f"cancelledの未来予約(5名)がcapacity decreaseをブロックしてしまった"
                f"（status semanticsが変わっている）: {r.status_code} {r.text}"
            )
            print("D. cancelledの未来予約はブロックしない（status semanticsは不変）: OK")

            # ===== E/F. delete_table()のfuture NG / JST-past OK =====
            shop_ef = await _new_shop("TimeBasisテストEF食堂")
            table_future = await _new_table(shop_ef, 6, "未来予約テーブル")
            await _insert_reservation(shop_ef, table_future, datetime.utcnow() + timedelta(hours=10), 3)
            r = await client.delete(f"/api/v1/shops/{shop_ef}/tables/{table_future}", headers=owner_a)
            assert r.status_code == 400, f"真にJST未来の予約があるTableのdeleteが誤って成立: {r.status_code} {r.text}"

            table_past = await _new_table(shop_ef, 6, "過去予約テーブル")
            await _insert_reservation(shop_ef, table_past, datetime.utcnow() + timedelta(hours=4), 3)
            r = await client.delete(f"/api/v1/shops/{shop_ef}/tables/{table_past}", headers=owner_a)
            assert r.status_code == 200, (
                f"JST基準で過去の予約しかないTableのdeleteが誤ってブロックされた: {r.status_code} {r.text}"
            )
            print("E/F. delete_table(): 真にJST未来はNG、JST基準で過去のみはOK: OK")

            # ===== G/H. update_table()capacity decreaseのfuture NG / JST-past OK =====
            # (A・Bで既に検証済みだが、Section35の項目に対応するE2Eの明示テストとして
            # 別テーブルで再確認する)
            shop_gh = await _new_shop("TimeBasisテストGH食堂")
            table_future2 = await _new_table(shop_gh, 6, "未来予約テーブル2")
            await _insert_reservation(shop_gh, table_future2, datetime.utcnow() + timedelta(hours=10), 5)
            r = await client.put(f"/api/v1/shops/{shop_gh}/tables/{table_future2}", json={
                "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 400, f"G: 真にJST未来の予約でcapacity decreaseが誤って成立: {r.status_code} {r.text}"

            table_past2 = await _new_table(shop_gh, 6, "過去予約テーブル2")
            await _insert_reservation(shop_gh, table_past2, datetime.utcnow() + timedelta(hours=4), 5)
            r = await client.put(f"/api/v1/shops/{shop_gh}/tables/{table_past2}", json={
                "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 200, f"H: JST基準で過去の予約でcapacity decreaseが誤ってブロックされた: {r.status_code} {r.text}"
            print("G/H. update_table()capacity decrease: 真にJST未来はNG、JST基準で過去のみはOK: OK")

            # ===== I. 前々フェーズ（Capacity Mutation Safety）のregression =====
            shop_i = await _new_shop("TimeBasisテストI食堂")
            table_id = await _new_table(shop_i, 4)
            target_date = _next_weekday(2, weeks_ahead=2)
            r = await client.post(
                f"/api/v1/shops/{shop_i}/realtime-voice/tools/create-reservation",
                json={
                    "date": target_date.isoformat(), "time": "13:00", "party_size": 2,
                    "guest_name": "TimeBasisテスト客I", "guest_phone": _next_phone(),
                    "call_id": "smoke-timebasis-" + str(uuid.uuid4()),
                },
            )
            assert r.status_code == 200 and r.json()["success"] is True, f"予約作成失敗: {r.status_code} {r.text}"
            reservation_id = r.json()["reservation_id"]
            r = await client.put(f"/api/v1/reservations/{reservation_id}", json={
                "number_of_people": 5,
            }, headers=owner_a)
            assert r.status_code == 400, f"前々フェーズのregression: 2->5への変更が誤って成立: {r.status_code} {r.text}"
            print("I. 前々フェーズ(Capacity Mutation Safety)のregression: OK")

            # ===== J. 前フェーズ（Resource Mutation Safety）のregression =====
            shop_j = await _new_shop("TimeBasisテストJ食堂")
            table_id = await _new_table(shop_j, 6)
            r = await client.post(
                f"/api/v1/shops/{shop_j}/realtime-voice/tools/create-reservation",
                json={
                    "date": target_date.isoformat(), "time": "14:00", "party_size": 5,
                    "guest_name": "TimeBasisテスト客J", "guest_phone": _next_phone(),
                    "call_id": "smoke-timebasis-" + str(uuid.uuid4()),
                },
            )
            assert r.status_code == 200 and r.json()["success"] is True, f"予約作成失敗: {r.status_code} {r.text}"
            r = await client.put(f"/api/v1/shops/{shop_j}/tables/{table_id}", json={
                "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 400, f"前フェーズのregression: 未来5名予約でのcapacity decreaseが誤って成立: {r.status_code} {r.text}"
            print("J. 前フェーズ(Resource Mutation Safety)のregression: OK")

            print("\n=== Reservation Time Basis Consistency スモークテスト: 全項目OK ===")


asyncio.run(main())
