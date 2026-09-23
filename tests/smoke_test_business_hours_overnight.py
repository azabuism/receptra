"""
Reservation Intelligence Phase D-1（Overnight Business Hours + Overnight Staff Shifts）
スモークテスト

背景（本フェーズで実装した内容）:

1. ShopHoursに closes_next_day / last_order_next_day という明示的なBooleanを
   追加した（「closing_time < opening_timeなら自動的に翌日」という暗黙推論は
   採用していない）。デフォルトFalseで既存店舗の挙動は完全に維持される。

2. 「営業セッションは開始日に帰属する」という仕様のもと、
   _resolve_business_session() というヘルパーで、calendar dateとbusiness
   session dateを区別しながら、深夜側の予約が前日から継続する日跨ぎ
   セッションに属するかどうかをdeterministicに判定するようにした
   （AIには一切計算させない）。

3. ShopClosure（臨時休業）は「営業セッションの開始日」を基準に判定する
   よう変更した（前日から続く日跨ぎセッションの場合、翌日側calendar dateの
   ShopClosureはそのセッションに影響しない）。

4. StaffWeeklyShift / StaffShiftOverride にも同じ思想で ends_next_day を
   追加し、_is_staff_scheduled() が日跨ぎ勤務を正しく判定できるようにした。

検証項目（ユーザー指定のA〜Hに対応）:
A. 通常営業時間のregression（日跨ぎを一切使わない場合、従来と同じ挙動）
B. 日跨ぎ（18:00〜翌03:00）の境界値
C. 曜日境界（火曜01:00が月曜sessionとして正しく判定される）
D. ShopClosureが「開始日」基準で判定される
E. last_order_timeの日跨ぎ表現
F. Staff shiftの日跨ぎ
G. Staff overrideの日跨ぎ（前日開始overrideの翌日側）
H. 既存の複数shift/day（非日跨ぎ）のregression

実行: python3 tests/smoke_test_business_hours_overnight.py
"""

import asyncio
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, date, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_overnight.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-overnight"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402

JST = timezone(timedelta(hours=9))


def _next_weekday(target_weekday: int, weeks_ahead: int = 2):
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
            # ===== セットアップ =====
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-overnight-a@example.com", "password": "password123",
                "display_name": "日跨ぎテストオーナー",
            })
            assert r.status_code in (200, 201), f"register A failed: {r.status_code} {r.text}"
            owner_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

            # ---- shop_regular: 通常営業（日跨ぎ不使用）の食堂。Aのregressionに使う ----
            r = await client.post("/api/v1/shops/register", json={
                "name": "通常営業食堂", "category": "食堂", "address": "東京都渋谷区1-1-1",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create shop_regular failed: {r.status_code} {r.text}"
            shop_regular = r.json()["shop_id"]

            hours_payload_regular = {"hours": [
                {"day_of_week": d, "opening_time": "10:00:00", "closing_time": "20:00:00", "is_closed": False}
                for d in range(7)
            ]}
            r = await client.put(f"/api/v1/shops/{shop_regular}/hours", json=hours_payload_regular, headers=owner_a)
            assert r.status_code == 200, f"set hours(regular) failed: {r.status_code} {r.text}"

            # ---- shop_overnight: 全曜日 18:00〜翌03:00。B〜Gに使う ----
            r = await client.post("/api/v1/shops/register", json={
                "name": "深夜営業カラオケ", "category": "その他", "address": "東京都渋谷区2-2-2",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create shop_overnight failed: {r.status_code} {r.text}"
            shop_overnight = r.json()["shop_id"]

            hours_payload_overnight = {"hours": [
                {
                    "day_of_week": d, "opening_time": "18:00:00", "closing_time": "03:00:00",
                    "is_closed": False, "closes_next_day": True,
                }
                for d in range(7)
            ]}
            r = await client.put(f"/api/v1/shops/{shop_overnight}/hours", json=hours_payload_overnight, headers=owner_a)
            assert r.status_code == 200, f"set hours(overnight) failed: {r.status_code} {r.text}"

            from app.database import AsyncSessionLocal
            from app.models.shop import Shop
            from app.models.reservation import Reservation
            from sqlalchemy import select

            async def _enable_reservations(shop_id: str):
                async with AsyncSessionLocal() as session:
                    shop_obj = await session.get(Shop, shop_id)
                    shop_obj.reservations_enabled = True
                    await session.commit()

            await _enable_reservations(shop_regular)
            await _enable_reservations(shop_overnight)

            r = await client.post(f"/api/v1/shops/{shop_regular}/tables", json={
                "name": "テーブルA", "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 200, f"create table(regular) failed: {r.status_code} {r.text}"

            r = await client.post(f"/api/v1/shops/{shop_overnight}/tables", json={
                "name": "ルームA", "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 200, f"create table(overnight) failed: {r.status_code} {r.text}"

            async def _clear_reservations(shop_id: str):
                async with AsyncSessionLocal() as session:
                    result = await session.execute(select(Reservation).filter(Reservation.shop_id == shop_id))
                    for res in result.scalars().all():
                        await session.delete(res)
                    await session.commit()

            async def _check(shop_id: str, date_str: str, time_str: str, party_size: int = 2, service_id=None, staff_id=None):
                payload = {"date": date_str, "time": time_str, "party_size": party_size}
                if service_id:
                    payload["service_id"] = service_id
                if staff_id:
                    payload["staff_id"] = staff_id
                r = await client.post(
                    f"/api/v1/shops/{shop_id}/realtime-voice/tools/check-availability",
                    json=payload,
                )
                assert r.status_code == 200, f"check-availability failed: {r.status_code} {r.text}"
                return r.json()

            async def _create(shop_id: str, date_str: str, time_str: str, phone: str, service_id=None, staff_id=None):
                payload = {
                    "date": date_str, "time": time_str, "party_size": 2,
                    "guest_name": "日跨ぎテスト", "guest_phone": phone,
                    "call_id": "smoke-overnight-" + str(uuid.uuid4()),
                }
                if service_id:
                    payload["service_id"] = service_id
                if staff_id:
                    payload["staff_id"] = staff_id
                return await client.post(
                    f"/api/v1/shops/{shop_id}/realtime-voice/tools/create-reservation",
                    json=payload,
                )

            # ===== A. 通常営業時間のregression（日跨ぎ不使用） =====
            await _clear_reservations(shop_regular)
            reg_date = _next_weekday(2, weeks_ahead=2)  # 水曜
            reg_date_str = reg_date.isoformat()

            body = await _check(shop_regular, reg_date_str, "19:00")  # 90分デフォルト -> 20:30終了 のはずが shop_regular はデフォルト90分
            # shop_regular のデフォルト所要時間は90分。19:00開始は20:30終了で20:00超過 -> NG
            assert body["available"] is False and body["reason_code"] == "outside_business_hours", (
                f"通常営業19:00開始(90分,20:30終了)はNGのはずが: {body}"
            )
            body = await _check(shop_regular, reg_date_str, "18:00")  # 18:00+90分=19:30 <= 20:00 -> OK
            assert body["available"] is True, f"通常営業18:00開始(90分,19:30終了)はOKのはずが: {body}"
            r = await _create(shop_regular, reg_date_str, "18:00", "09040001001")
            assert r.status_code == 200 and r.json()["success"] is True, f"通常営業createが失敗: {r.status_code} {r.text}"
            print("A. 通常営業時間のregression（日跨ぎ不使用）: OK")

            # ===== B. 日跨ぎ（18:00〜翌03:00）の境界値 =====
            await _clear_reservations(shop_overnight)
            mon_b = _next_weekday(0, weeks_ahead=2)
            tue_b = mon_b + timedelta(days=1)
            mon_b_str = mon_b.isoformat()
            tue_b_str = tue_b.isoformat()

            async def _set_duration_overnight(minutes: int):
                async with AsyncSessionLocal() as session:
                    shop_obj = await session.get(Shop, shop_overnight)
                    shop_obj.reservation_duration_minutes = minutes
                    await session.commit()

            await _set_duration_overnight(120)
            body = await _check(shop_overnight, mon_b_str, "17:00")  # 17:00-19:00
            assert body["available"] is False, f"17:00-19:00はNGのはずが: {body}"

            body = await _check(shop_overnight, mon_b_str, "18:00")  # 18:00-20:00
            assert body["available"] is True, f"18:00-20:00はOKのはずが: {body}"

            body = await _check(shop_overnight, mon_b_str, "23:00")  # 23:00-翌01:00
            assert body["available"] is True, f"23:00-翌01:00はOKのはずが: {body}"

            await _set_duration_overnight(60)
            body = await _check(shop_overnight, tue_b_str, "02:00")  # 翌02:00-03:00
            assert body["available"] is True, f"翌02:00-03:00(60分)はOKのはずが: {body}"

            await _set_duration_overnight(61)
            body = await _check(shop_overnight, tue_b_str, "02:00")  # 翌02:00-03:01
            assert body["available"] is False, f"翌02:00-03:01(61分)はNGのはずが: {body}"

            await _set_duration_overnight(30)
            body = await _check(shop_overnight, tue_b_str, "03:00")  # 翌03:00開始
            assert body["available"] is False, f"翌03:00開始はNGのはずが: {body}"
            print("B. 日跨ぎ（18:00〜翌03:00）の境界値: OK")

            # ===== C. 曜日境界（火曜01:00が月曜sessionとして正しく判定される） =====
            await _clear_reservations(shop_overnight)
            mon_c = _next_weekday(0, weeks_ahead=5)
            tue_c = mon_c + timedelta(days=1)
            await _set_duration_overnight(60)
            body = await _check(shop_overnight, tue_c.isoformat(), "01:00")  # 翌01:00-02:00
            assert body["available"] is True, f"火曜01:00(月曜session継続)はOKのはずが: {body}"
            r = await _create(shop_overnight, tue_c.isoformat(), "01:00", "09040001002")
            assert r.status_code == 200 and r.json()["success"] is True, (
                f"火曜01:00のcreateが失敗（月曜sessionとして成立するはずが）: {r.status_code} {r.text}"
            )
            print("C. 曜日境界（火曜01:00が月曜sessionとして正しく判定される）: OK")

            # ===== D. ShopClosureが「開始日」基準で判定される =====
            await _clear_reservations(shop_overnight)
            await _set_duration_overnight(60)

            # D-1: 月曜がShopClosure -> 月曜18:00〜火曜03:00の営業セッション全体NG
            mon_d1 = _next_weekday(0, weeks_ahead=6)
            tue_d1 = mon_d1 + timedelta(days=1)
            r = await client.post(f"/api/v1/shops/{shop_overnight}/closures", json={
                "start_date": mon_d1.isoformat(), "end_date": mon_d1.isoformat(), "reason": "月曜closureテスト",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create closure(mon) failed: {r.status_code} {r.text}"

            body = await _check(shop_overnight, mon_d1.isoformat(), "23:00")
            assert body["available"] is False and body["reason_code"] == "temporary_closure", (
                f"月曜closure時の月曜23:00はNGのはずが: {body}"
            )
            body = await _check(shop_overnight, tue_d1.isoformat(), "01:00")
            assert body["available"] is False and body["reason_code"] == "temporary_closure", (
                f"月曜closure時の火曜01:00（月曜session継続）はNGのはずが: {body}"
            )
            print("D-1. 月曜ShopClosureが月曜18:00〜翌03:00セッション全体をブロックする: OK")

            # D-2: 火曜だけShopClosure -> 月曜18:00〜火曜03:00には影響しないが、
            #      火曜18:00開始の火曜自身のsessionはNG
            mon_d2 = _next_weekday(0, weeks_ahead=7)
            tue_d2 = mon_d2 + timedelta(days=1)
            r = await client.post(f"/api/v1/shops/{shop_overnight}/closures", json={
                "start_date": tue_d2.isoformat(), "end_date": tue_d2.isoformat(), "reason": "火曜closureテスト",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create closure(tue) failed: {r.status_code} {r.text}"

            body = await _check(shop_overnight, mon_d2.isoformat(), "23:00")
            assert body["available"] is True, f"火曜closureは月曜23:00に影響しないはずが: {body}"
            body = await _check(shop_overnight, tue_d2.isoformat(), "01:00")
            assert body["available"] is True, f"火曜closureは月曜session継続の火曜01:00に影響しないはずが: {body}"
            body = await _check(shop_overnight, tue_d2.isoformat(), "18:00")
            assert body["available"] is False and body["reason_code"] == "temporary_closure", (
                f"火曜18:00開始（火曜自身のsession）は火曜closureでNGのはずが: {body}"
            )
            print("D-2. 火曜ShopClosureは月曜session継続部分に影響せず、火曜自身のsessionのみブロックする: OK")

            # ===== E. last_order_timeの日跨ぎ表現 =====
            await _clear_reservations(shop_overnight)
            mon_e = _next_weekday(0, weeks_ahead=8)
            tue_e = mon_e + timedelta(days=1)
            hours_payload_lo = {"hours": [
                {
                    "day_of_week": d, "opening_time": "18:00:00", "closing_time": "03:00:00",
                    "is_closed": False, "closes_next_day": True,
                    "last_order_time": "01:30:00", "last_order_next_day": True,
                }
                for d in range(7)
            ]}
            r = await client.put(f"/api/v1/shops/{shop_overnight}/hours", json=hours_payload_lo, headers=owner_a)
            assert r.status_code == 200, f"set hours(last_order overnight) failed: {r.status_code} {r.text}"

            await _set_duration_overnight(90)  # 01:30開始 + 90分 = 03:00ちょうど（closingに接する）
            body = await _check(shop_overnight, tue_e.isoformat(), "01:30")
            assert body["available"] is True, f"翌01:30開始(90分,03:00終了=closing接触)はOKのはずが: {body}"

            body = await _check(shop_overnight, tue_e.isoformat(), "01:31")
            assert body["available"] is False, f"翌01:31開始はNGのはずが: {body}"
            print("E. last_order_timeの日跨ぎ表現: OK")

            # 通常営業時間(shop_overnight)に戻す（以降のテストで使うため）
            r = await client.put(f"/api/v1/shops/{shop_overnight}/hours", json=hours_payload_overnight, headers=owner_a)
            assert r.status_code == 200, f"restore hours(overnight) failed: {r.status_code} {r.text}"

            # ===== F. Staff shiftの日跨ぎ =====
            r = await client.post("/api/v1/services", json={
                "shop_id": shop_overnight, "name": "指名予約", "base_price": 3000, "duration_minutes": 60,
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create service failed: {r.status_code} {r.text}"
            service_id = r.json()["id"]

            r = await client.post("/api/v1/staff", json={"shop_id": shop_overnight, "name": "深夜太郎"}, headers=owner_a)
            assert r.status_code == 200, f"create staff failed: {r.status_code} {r.text}"
            staff_id = r.json()["id"]
            r = await client.post(f"/api/v1/staff/{staff_id}/services/{service_id}", headers=owner_a)
            assert r.status_code == 200, f"assign staff service failed: {r.status_code} {r.text}"

            r = await client.patch(f"/api/v1/shops/{shop_overnight}", json={"staff_schedule_enabled": True}, headers=owner_a)
            assert r.status_code == 200, f"enable staff_schedule failed: {r.status_code} {r.text}"

            # バリデーション: ends_next_dayなしで終了<開始は拒否（既存regression）
            r = await client.post(f"/api/v1/staff/{staff_id}/weekly-shifts", json={
                "day_of_week": 0, "start_time": "18:00", "end_time": "10:00",
            }, headers=owner_a)
            assert r.status_code == 422, f"ends_next_dayなしの日跨ぎ入力は拒否されるはずが: {r.status_code} {r.text}"

            r = await client.post(f"/api/v1/staff/{staff_id}/weekly-shifts", json={
                "day_of_week": 0, "start_time": "18:00", "end_time": "03:00", "ends_next_day": True,
            }, headers=owner_a)
            assert r.status_code == 200, f"create overnight weekly shift failed: {r.status_code} {r.text}"
            assert r.json()["ends_next_day"] is True, r.json()

            await _clear_reservations(shop_overnight)
            mon_f = _next_weekday(0, weeks_ahead=9)
            tue_f = mon_f + timedelta(days=1)

            body = await _check(shop_overnight, mon_f.isoformat(), "23:00", service_id=service_id, staff_id=staff_id)
            # 23:00開始 + デフォルト90分 -> 翌00:30終了。shift(18:00-翌03:00)に収まる -> OK
            assert body["available"] is True, f"深夜太郎 23:00(shift内)はOKのはずが: {body}"

            # 60分でスタッフ側のshift境界をピンポイントに確認
            r_svc_update = await client.put(f"/api/v1/services/{service_id}", json={"duration_minutes": 60}, headers=owner_a)
            assert r_svc_update.status_code == 200, f"update service duration failed: {r_svc_update.status_code} {r_svc_update.text}"

            body = await _check(shop_overnight, tue_f.isoformat(), "02:00", service_id=service_id, staff_id=staff_id)
            assert body["available"] is True, f"深夜太郎 火曜02:00(60分,shift境界内)はOKのはずが: {body}"

            body = await _check(shop_overnight, tue_f.isoformat(), "02:30", service_id=service_id, staff_id=staff_id)
            assert body["available"] is False, f"深夜太郎 火曜02:30(60分,03:30終了・shift超過)はNGのはずが: {body}"
            print("F. Staff shiftの日跨ぎ: OK")

            # ===== G. Staff overrideの日跨ぎ（前日開始overrideの翌日側） =====
            await _clear_reservations(shop_overnight)
            mon_g = _next_weekday(0, weeks_ahead=10)
            tue_g = mon_g + timedelta(days=1)

            r = await client.post(f"/api/v1/staff/{staff_id}/shift-overrides", json={
                "target_date": mon_g.isoformat(), "override_type": "hours",
                "start_time": "20:00", "end_time": "02:00", "ends_next_day": True,
                "note": "月曜だけ延長",
            }, headers=owner_a)
            assert r.status_code == 200, f"create overnight override failed: {r.status_code} {r.text}"
            assert r.json()["ends_next_day"] is True, r.json()

            body = await _check(shop_overnight, tue_g.isoformat(), "01:00", service_id=service_id, staff_id=staff_id)
            # override(20:00〜翌02:00)がweekly shift(18:00〜翌03:00)を置き換える。
            # 火曜01:00開始+60分=02:00終了 -> overrideの範囲内 -> OK
            assert body["available"] is True, f"深夜太郎 火曜01:00(月曜overrideの翌日側)はOKのはずが: {body}"

            body = await _check(shop_overnight, tue_g.isoformat(), "01:30", service_id=service_id, staff_id=staff_id)
            # 01:30+60分=02:30終了 -> overrideの02:00を超える -> NG
            assert body["available"] is False, f"深夜太郎 火曜01:30(overrideの02:00超過)はNGのはずが: {body}"
            print("G. Staff overrideの日跨ぎ（前日開始overrideの翌日側）: OK")

            # ===== H. 既存の複数shift/day（非日跨ぎ）のregression =====
            r = await client.post("/api/v1/staff", json={"shop_id": shop_overnight, "name": "通常次郎"}, headers=owner_a)
            assert r.status_code == 200
            staff2_id = r.json()["id"]
            r = await client.post(f"/api/v1/staff/{staff2_id}/services/{service_id}", headers=owner_a)
            assert r.status_code == 200

            r = await client.post(f"/api/v1/staff/{staff2_id}/weekly-shifts", json={
                "day_of_week": 0, "start_time": "09:00", "end_time": "12:00",
            }, headers=owner_a)
            assert r.status_code == 200, f"create split shift 1 failed: {r.status_code} {r.text}"
            assert r.json()["ends_next_day"] is False, r.json()

            r = await client.post(f"/api/v1/staff/{staff2_id}/weekly-shifts", json={
                "day_of_week": 0, "start_time": "13:00", "end_time": "18:00",
            }, headers=owner_a)
            assert r.status_code == 200, f"create split shift 2 failed: {r.status_code} {r.text}"

            r = await client.get(f"/api/v1/staff/{staff2_id}/weekly-shifts", headers=owner_a)
            assert r.status_code == 200 and len(r.json()) == 2, f"list split shifts failed: {r.status_code} {r.text}"
            print("H. 既存の複数shift/day（非日跨ぎ）のregression: OK")

            print("\n=== Phase D-1（Overnight Business Hours + Overnight Staff Shifts）: 全項目OK ===")


if __name__ == "__main__":
    asyncio.run(main())
