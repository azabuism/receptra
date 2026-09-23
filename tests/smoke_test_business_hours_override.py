"""
Reservation Intelligence Phase D-3（特定日の営業時間 / Special Date Hours）
スモークテスト

背景（本フェーズで実装した内容）:

1. ShopHoursOverride（新規テーブル）を追加した。特定の1カレンダー日付だけの
   営業時間を、曜日ごとの通常営業時間（ShopHours）とは別に登録できる。
   UNIQUE(shop_id, target_date)。is_closedフィールドは意図的に持たせていない
   （休業はShopClosureの排他的な責務のまま）。

2. 優先順位: ShopClosure（終日休業） > ShopHoursOverride（特定日の営業時間）>
   ShopHours（曜日ごとの通常営業時間）。ShopHoursOverrideが存在する日は、
   通常は定休日の曜日であってもその日は営業日として扱われる。

3. _resolve_day_hours()という共通ヘルパーを新設し、_resolve_business_session()
   （CHECK/CREATE/UPDATEが使う）・_lookup_yesterday_overnight_session()（前日
   からの日跨ぎ継続セッション判定）・get_availability()（日別空き一覧）の
   3箇所すべてがこのヘルパー経由でOverride優先の判断をするため、判断ロジックは
   重複していない。

4. Phase D-1の「営業セッションは開始日に帰属する」という考え方をOverride
   セッションにも正しく拡張した（例: 12/31 18:00〜翌03:00のOverrideに対する
   1/1 01:00の予約は、1/1のWeekly Hoursではなく12/31のOverrideセッションに
   帰属する）。

5. Phase D-2のBreak Windows（ShopBreakTime）は無変更のまま、Overrideセッション
   内でも正しく機能する（session_dateの実際の曜日を使ってbreak検索する）。

検証項目:
A. 通常のOverride（フォールバック・境界値）
B. 通常は定休日の曜日をOverride登録で営業日にする
C. ShopClosureがOverrideより優先される
D. 日跨ぎOverrideの境界値マトリクス
E. 前日からのOverride継続セッション（Jan1 01:00 -> Dec31 override型のケース）
F. Break Windows(D-2)がOverrideセッション内でも正しく機能する
   （セッション範囲外のWeekly Breakはoverrideを無効化しない）
G. Staff Schedule(D-1)がOverride日でも独立してAND条件として機能する
H. Section30監査: 前日セッション継続と当日Overrideセッションの帰属先が
   曖昧にならないことの実証テスト
I. CHECK/CREATE/OWNER-UPDATE/GET-AVAILABILITYの一貫性
J. Overrideバリデーション（ShopHoursと同じ共通helperを使っている）
K. Owner API（tenant isolation・upsert・list・delete）
L. Realtime/チャット予約AIのhours_blockにOverrideが反映される（Section38）
M. Overrideを1件も登録していない店舗ではPhase D-2までと完全に同じ挙動（regression）

実行: python3 tests/smoke_test_business_hours_override.py
"""

import asyncio
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, date, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_override.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-override"
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
                "email": "owner-override-a@example.com", "password": "password123",
                "display_name": "特定日テストオーナー",
            })
            assert r.status_code in (200, 201), f"register owner_a failed: {r.status_code} {r.text}"
            owner_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-override-b@example.com", "password": "password123",
                "display_name": "他テナントオーナー",
            })
            assert r.status_code in (200, 201), f"register owner_b failed: {r.status_code} {r.text}"
            owner_b = {"Authorization": f"Bearer {r.json()['access_token']}"}

            from app.database import AsyncSessionLocal
            from app.models.shop import Shop
            from app.models.reservation import Reservation
            from sqlalchemy import select

            async def _enable_reservations(shop_id: str):
                async with AsyncSessionLocal() as session:
                    shop_obj = await session.get(Shop, shop_id)
                    shop_obj.reservations_enabled = True
                    await session.commit()

            async def _set_duration(shop_id: str, minutes: int):
                async with AsyncSessionLocal() as session:
                    shop_obj = await session.get(Shop, shop_id)
                    shop_obj.reservation_duration_minutes = minutes
                    await session.commit()

            async def _enable_staff_schedule(shop_id: str):
                r = await client.patch(f"/api/v1/shops/{shop_id}", json={"staff_schedule_enabled": True}, headers=owner_a)
                assert r.status_code == 200, f"enable staff_schedule_enabled failed: {r.status_code} {r.text}"

            async def _clear_reservations(shop_id: str):
                async with AsyncSessionLocal() as session:
                    result = await session.execute(select(Reservation).filter(Reservation.shop_id == shop_id))
                    for res in result.scalars().all():
                        await session.delete(res)
                    await session.commit()

            async def _check(shop_id: str, date_str: str, time_str: str, party_size: int = 2, extra=None):
                payload = {"date": date_str, "time": time_str, "party_size": party_size}
                if extra:
                    payload.update(extra)
                r = await client.post(
                    f"/api/v1/shops/{shop_id}/realtime-voice/tools/check-availability", json=payload,
                )
                assert r.status_code == 200, f"check-availability failed: {r.status_code} {r.text}"
                return r.json()

            async def _create(shop_id: str, date_str: str, time_str: str, phone: str, extra=None):
                payload = {
                    "date": date_str, "time": time_str, "party_size": 2,
                    "guest_name": "特定日テスト", "guest_phone": phone,
                    "call_id": "smoke-override-" + str(uuid.uuid4()),
                }
                if extra:
                    payload.update(extra)
                return await client.post(
                    f"/api/v1/shops/{shop_id}/realtime-voice/tools/create-reservation", json=payload,
                )

            async def _upsert_override(shop_id: str, target_date: str, opening, closing, headers=None, **kw):
                body = {"opening_time": opening, "closing_time": closing}
                body.update(kw)
                return await client.put(
                    f"/api/v1/shops/{shop_id}/hours-overrides/{target_date}", json=body,
                    headers=headers or owner_a,
                )

            # ---- shop_a: 通常営業（月〜土 10:00-20:00、日曜定休） ----
            r = await client.post("/api/v1/shops/register", json={
                "name": "特定日テスト食堂", "category": "食堂", "address": "東京都渋谷区5-5-5",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create shop_a failed: {r.status_code} {r.text}"
            shop_a = r.json()["shop_id"]
            r = await client.put(f"/api/v1/shops/{shop_a}/hours", json={"hours": [
                {"day_of_week": d, "opening_time": "10:00:00", "closing_time": "20:00:00", "is_closed": (d == 6)}
                for d in range(7)
            ]}, headers=owner_a)
            assert r.status_code == 200, f"set hours(shop_a) failed: {r.status_code} {r.text}"
            await _enable_reservations(shop_a)
            r = await client.post(f"/api/v1/shops/{shop_a}/tables", json={"name": "テーブルA", "capacity": 4}, headers=owner_a)
            assert r.status_code == 200

            # ---- shop_overnight: 全曜日18:00〜翌03:00（Phase D-1/D-2と同じ形） ----
            r = await client.post("/api/v1/shops/register", json={
                "name": "特定日テスト深夜営業", "category": "その他", "address": "東京都渋谷区6-6-6",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create shop_overnight failed: {r.status_code} {r.text}"
            shop_overnight = r.json()["shop_id"]
            r = await client.put(f"/api/v1/shops/{shop_overnight}/hours", json={"hours": [
                {"day_of_week": d, "opening_time": "18:00:00", "closing_time": "03:00:00",
                 "is_closed": False, "closes_next_day": True}
                for d in range(7)
            ]}, headers=owner_a)
            assert r.status_code == 200, f"set hours(shop_overnight) failed: {r.status_code} {r.text}"
            await _enable_reservations(shop_overnight)
            r = await client.post(f"/api/v1/shops/{shop_overnight}/tables", json={"name": "ルームA", "capacity": 4}, headers=owner_a)
            assert r.status_code == 200

            # ---- shop_c: 昼営業のみ（10:00-20:00、日跨ぎ不使用）。前日Override継続テスト用 ----
            r = await client.post("/api/v1/shops/register", json={
                "name": "特定日テスト前日継続", "category": "その他", "address": "東京都渋谷区7-7-7",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create shop_c failed: {r.status_code} {r.text}"
            shop_c = r.json()["shop_id"]
            r = await client.put(f"/api/v1/shops/{shop_c}/hours", json={"hours": [
                {"day_of_week": d, "opening_time": "10:00:00", "closing_time": "20:00:00", "is_closed": False}
                for d in range(7)
            ]}, headers=owner_a)
            assert r.status_code == 200
            await _enable_reservations(shop_c)
            r = await client.post(f"/api/v1/shops/{shop_c}/tables", json={"name": "テーブルC", "capacity": 4}, headers=owner_a)
            assert r.status_code == 200

            # ===== M. Overrideゼロ件のregression（先に確認しておく） =====
            await _set_duration(shop_a, 60)
            m_date = _next_weekday(1, weeks_ahead=2)  # 火曜（Override未登録）
            body = await _check(shop_a, m_date.isoformat(), "12:00")
            assert body["available"] is True, f"Override未登録時の火曜12:00はOKのはずが: {body}"
            print("M. Overrideを1件も登録していない店舗はPhase D-2までと同じ挙動: OK")

            # ===== A. 通常のOverride（フォールバック・境界値） =====
            await _set_duration(shop_a, 60)
            a_date = _next_weekday(1, weeks_ahead=3)  # 火曜（通常10:00-20:00）
            r = await _upsert_override(shop_a, a_date.isoformat(), "10:00:00", "15:00:00")
            assert r.status_code == 200, f"create override(A) failed: {r.status_code} {r.text}"

            body = await _check(shop_a, a_date.isoformat(), "14:00")  # Override内
            assert body["available"] is True, f"14:00(override内)はOKのはずが: {body}"
            body = await _check(shop_a, a_date.isoformat(), "16:00")  # Weeklyなら営業中だがOverrideでは閉店後
            assert body["available"] is False and body["reason_code"] == "outside_business_hours", (
                f"16:00(通常Weeklyなら営業中、Overrideでは閉店後)はNGのはずが: {body}"
            )
            # 別の火曜（Override未登録）は通常通りWeekly Hoursに従う（16:00もOK）
            other_tue = _next_weekday(1, weeks_ahead=5)
            body = await _check(shop_a, other_tue.isoformat(), "16:00")
            assert body["available"] is True, f"Override未登録の別の火曜16:00はOKのはずが: {body}"
            print("A. 通常のOverride（フォールバック・境界値）: OK")

            # ===== B. 通常は定休日の曜日をOverride登録で営業日にする =====
            b_date = _next_weekday(6, weeks_ahead=2)  # 日曜（Weeklyでは定休日）
            other_sun = _next_weekday(6, weeks_ahead=3)
            body = await _check(shop_a, other_sun.isoformat(), "13:00")
            assert body["available"] is False and body["reason_code"] == "shop_closed", (
                f"Override未登録の日曜はshop_closedのはずが: {body}"
            )
            r = await _upsert_override(shop_a, b_date.isoformat(), "12:00:00", "18:00:00")
            assert r.status_code == 200, f"create override(B) failed: {r.status_code} {r.text}"
            body = await _check(shop_a, b_date.isoformat(), "13:00")
            assert body["available"] is True, f"Override登録済みの日曜13:00はOKのはずが: {body}"
            body = await _check(shop_a, b_date.isoformat(), "11:00")  # Override開店前
            assert body["available"] is False and body["reason_code"] == "outside_business_hours", (
                f"Override開店前(11:00)はNGのはずが: {body}"
            )
            print("B. 通常は定休日の曜日をOverride登録で営業日にする: OK")

            # ===== C. ShopClosureがOverrideより優先される =====
            c_date = _next_weekday(2, weeks_ahead=3)  # 水曜
            r = await _upsert_override(shop_a, c_date.isoformat(), "10:00:00", "15:00:00")
            assert r.status_code == 200, f"create override(C) failed: {r.status_code} {r.text}"
            body = await _check(shop_a, c_date.isoformat(), "12:00")
            assert body["available"] is True, f"Closure登録前は12:00OKのはずが: {body}"
            r = await client.post(f"/api/v1/shops/{shop_a}/closures", json={
                "start_date": c_date.isoformat(), "end_date": c_date.isoformat(), "reason": "臨時休業テスト",
            }, headers=owner_a)
            assert r.status_code == 200, f"create closure(C) failed: {r.status_code} {r.text}"
            body = await _check(shop_a, c_date.isoformat(), "12:00")
            assert body["available"] is False and body["reason_code"] == "temporary_closure", (
                f"ShopClosureがOverrideより優先されるはずが: {body}"
            )
            print("C. ShopClosureがOverrideより優先される: OK")

            # ===== D. 日跨ぎOverrideの境界値マトリクス =====
            await _clear_reservations(shop_overnight)
            d_date = _next_weekday(3, weeks_ahead=3)  # 木曜
            r = await _upsert_override(
                shop_overnight, d_date.isoformat(), "20:00:00", "02:00:00",
                closes_next_day=True, last_order_time="01:30:00", last_order_next_day=True,
            )
            assert r.status_code == 200, f"create override(D, overnight) failed: {r.status_code} {r.text}"
            await _set_duration(shop_overnight, 30)
            d_next = d_date + timedelta(days=1)

            body = await _check(shop_overnight, d_date.isoformat(), "19:59")  # Override開店前
            assert body["available"] is False, f"19:59(override開店前)はNGのはずが: {body}"
            body = await _check(shop_overnight, d_date.isoformat(), "20:00")  # Override開店ちょうど
            assert body["available"] is True, f"20:00(override開店)はOKのはずが: {body}"
            body = await _check(shop_overnight, d_next.isoformat(), "01:30")  # ラストオーダーちょうど
            assert body["available"] is True, f"翌01:30(last_orderちょうど)はOKのはずが: {body}"
            body = await _check(shop_overnight, d_next.isoformat(), "02:00")  # ラストオーダー超過
            assert body["available"] is False and body["reason_code"] == "outside_business_hours", (
                f"翌02:00(last_order超過)はNGのはずが: {body}"
            )
            body = await _check(shop_overnight, d_next.isoformat(), "01:29")  # 終了時刻(02:00)まで30分に収まらない境界
            end_dt_ok = True  # 01:29+30分=01:59<=02:00なのでOKのはず
            assert body["available"] is True, f"翌01:29(終了が02:00に収まる)はOKのはずが: {body}"
            print("D. 日跨ぎOverrideの境界値マトリクス: OK")

            # ===== E. 前日からのOverride継続セッション =====
            await _clear_reservations(shop_c)
            e_date = _next_weekday(4, weeks_ahead=3)  # 金曜
            e_next = e_date + timedelta(days=1)
            r = await _upsert_override(
                shop_c, e_date.isoformat(), "18:00:00", "03:00:00",
                closes_next_day=True, last_order_time="02:30:00", last_order_next_day=True,
            )
            assert r.status_code == 200, f"create override(E, overnight) failed: {r.status_code} {r.text}"
            await _set_duration(shop_c, 30)

            # 翌日側（e_next）は本来10:00-20:00の通常営業(Weekly)だが、開店前(01:00)は
            # 前日(e_date)のOverrideセッション継続として判定されるはず。
            body = await _check(shop_c, e_next.isoformat(), "01:00")
            assert body["available"] is True, f"翌日01:00(前日Override継続)はOKのはずが: {body}"

            # ShopClosureは「セッションの帰属日」(=e_date)を基準に判定される。
            # e_next（calendar date）にClosureを設定しても、e_date起点のセッションには
            # 影響しないはず（Phase D-1のcalendar date非依存の精度をOverrideでも確認）。
            r = await client.post(f"/api/v1/shops/{shop_c}/closures", json={
                "start_date": e_next.isoformat(), "end_date": e_next.isoformat(), "reason": "翌日休業テスト",
            }, headers=owner_a)
            assert r.status_code == 200
            body = await _check(shop_c, e_next.isoformat(), "01:00")
            assert body["available"] is True, (
                f"e_next Closureはe_date起点セッションに影響しないはずが: {body}"
            )
            # 一方、e_next 11:00（e_next自身のWeeklyセッション）はそのClosureで正しくNGになる
            body = await _check(shop_c, e_next.isoformat(), "11:00")
            assert body["available"] is False and body["reason_code"] == "temporary_closure", (
                f"e_next 11:00はe_next自身のClosureでNGのはずが: {body}"
            )
            print("E. 前日からのOverride継続セッション: OK")

            # ===== F. Break WindowsがOverrideセッション内でも正しく機能する =====
            # shop_overnight（Weekly 18:00〜翌03:00）を使う。break-time登録時の
            # 既存バリデーション（shop_break_time.py。本フェーズでは無変更）は
            # Weekly ShopHoursの範囲内かどうかで判定するため、Weeklyの全時間帯
            # (18:00〜翌03:00)に収まるbreakだけを登録できる。
            f_date = _next_weekday(5, weeks_ahead=4)  # 土曜。他セクションの曜日と重複しないよう選ぶ
            f_weekday = f_date.weekday()

            # Weekly全体には収まるが、これから登録する「短縮されたOverride」の
            # 実際のセッション範囲(18:00-20:00)の外側になるbreak(翌02:00-02:30)。
            # Section15-18の監査点: このbreakは短縮Overrideを無効化してはいけない。
            r = await client.post(f"/api/v1/shops/{shop_overnight}/break-times", json={
                "day_of_week": f_weekday, "start_time": "02:00", "end_time": "02:30",
                "start_next_day": True, "end_next_day": True,
            }, headers=owner_a)
            assert r.status_code == 200, f"out-of-override-range break create failed: {r.status_code} {r.text}"

            # 同じ曜日に、これから登録するOverrideの実際のセッション範囲(18:00-20:00)
            # 内に収まるbreak(19:00-19:30)も登録する。
            r = await client.post(f"/api/v1/shops/{shop_overnight}/break-times", json={
                "day_of_week": f_weekday, "start_time": "19:00", "end_time": "19:30",
            }, headers=owner_a)
            assert r.status_code == 200, f"in-override-range break create failed: {r.status_code} {r.text}"

            # Overrideでこの日だけ18:00-20:00（日跨ぎなし）という、Weeklyよりずっと
            # 短いセッションにする。
            r = await _upsert_override(shop_overnight, f_date.isoformat(), "18:00:00", "20:00:00")
            assert r.status_code == 200, f"create override(F) failed: {r.status_code} {r.text}"
            await _set_duration(shop_overnight, 30)

            # セッション範囲内・break時間外(18:30)はOverrideとして正常に予約可能
            # （セッション範囲外のbreak(翌02:00-02:30)がOverride自体を無効化して
            # いないことの実証）。
            body = await _check(shop_overnight, f_date.isoformat(), "18:30")
            assert body["available"] is True, (
                f"override session範囲外のbreakがoverride自体を無効化してはいけないはずが: {body}"
            )
            # セッション範囲内のbreak(19:00-19:30)はきちんと機能する
            body = await _check(shop_overnight, f_date.isoformat(), "19:00")
            assert body["available"] is False and body["reason_code"] == "break_time", (
                f"override session内のbreak(19:00)はNGのはずが: {body}"
            )
            body = await _check(shop_overnight, f_date.isoformat(), "19:30")  # breakに隣接のみ
            assert body["available"] is True, f"break(19:00-19:30)に隣接する19:30はOKのはずが: {body}"

            # このOverrideはcloses_next_day=Falseの短縮セッションのため、翌日側への
            # 日跨ぎ継続は発生しないはず（closes_next_day=Trueだった場合の翌02:00の
            # breakは、そもそも翌日への継続自体が起きないため無関係になる）。
            f_next = f_date + timedelta(days=1)
            body = await _check(shop_overnight, f_next.isoformat(), "01:00")
            # f_next自身はWeekly 18:00-翌03:00なので01:00は開店前 →
            # 前日(f_date)からの継続を確認するが、f_dateのOverrideはcloses_next_day=False
            # のため継続セッションは存在しないはず（前日のWeeklyへの巻き戻りも発生しない、
            # Overrideが優先されてWeeklyは完全に無視されるため）。
            assert body["available"] is False and body["reason_code"] == "outside_business_hours", (
                f"短縮Override(closes_next_day=False)の翌日への日跨ぎ継続は発生しないはずが: {body}"
            )
            print("F. Break WindowsがOverrideセッション内でも正しく機能する: OK")

            # ===== G. Staff ScheduleがOverride日でも独立してAND条件として機能する =====
            r = await client.post("/api/v1/shops/register", json={
                "name": "特定日テストスタッフ", "category": "ヘアサロン", "address": "東京都渋谷区8-8-8",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create shop_g failed: {r.status_code} {r.text}"
            shop_g = r.json()["shop_id"]
            r = await client.put(f"/api/v1/shops/{shop_g}/hours", json={"hours": [
                {"day_of_week": d, "opening_time": "10:00:00", "closing_time": "20:00:00", "is_closed": False}
                for d in range(7)
            ]}, headers=owner_a)
            assert r.status_code == 200
            await _enable_reservations(shop_g)
            await _enable_staff_schedule(shop_g)
            await _set_duration(shop_g, 60)

            r = await client.post("/api/v1/services", json={
                "shop_id": shop_g, "name": "カット", "base_price": 5000, "duration_minutes": 60,
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create service failed: {r.status_code} {r.text}"
            service_id = r.json()["id"]

            r = await client.post("/api/v1/staff", json={"shop_id": shop_g, "name": "特定日テストスタッフA"}, headers=owner_a)
            assert r.status_code == 200, f"create staff failed: {r.status_code} {r.text}"
            staff_id = r.json()["id"]
            r = await client.post(f"/api/v1/staff/{staff_id}/services/{service_id}", headers=owner_a)
            assert r.status_code == 200, f"link staff-service failed: {r.status_code} {r.text}"

            g_date = _next_weekday(5, weeks_ahead=3)  # 土曜
            r = await _upsert_override(shop_g, g_date.isoformat(), "10:00:00", "15:00:00")
            assert r.status_code == 200, f"create override(G) failed: {r.status_code} {r.text}"

            r = await client.post(f"/api/v1/staff/{staff_id}/weekly-shifts", json={
                "day_of_week": g_date.weekday(), "start_time": "08:00", "end_time": "12:00",
            }, headers=owner_a)
            assert r.status_code == 200, f"set staff weekly shift failed: {r.status_code} {r.text}"

            body = await _check(shop_g, g_date.isoformat(), "11:00", extra={"service_id": service_id, "staff_id": staff_id})
            assert body["available"] is True, f"11:00(override内AND staff勤務内)はOKのはずが: {body}"
            body = await _check(shop_g, g_date.isoformat(), "13:00", extra={"service_id": service_id, "staff_id": staff_id})
            assert body["available"] is False and body["reason_code"] == "staff_unavailable", (
                f"13:00(override内だがstaff勤務外)はNGのはずが: {body}"
            )
            body = await _check(shop_g, g_date.isoformat(), "09:00", extra={"service_id": service_id, "staff_id": staff_id})
            assert body["available"] is False and body["reason_code"] == "outside_business_hours", (
                f"09:00(override開店前)はNGのはずが: {body}"
            )
            print("G. Staff ScheduleがOverride日でも独立してAND条件として機能する: OK")

            # ===== H. Section30監査: セッション帰属の曖昧さが無いことの実証 =====
            await _clear_reservations(shop_overnight)
            h_date = _next_weekday(0, weeks_ahead=5)  # 月曜（前日=日曜はWeekly 18:00-翌03:00のまま）
            # 当日(月曜)に、開店01:00-05:00という「前日セッションの継続時間帯とも
            # 重なって見える」Overrideを登録する（Section30の仕様書の例そのもの:
            # 前日18:00〜翌03:00 かつ 当日01:00〜05:00 の両方が設定されている状況）。
            r = await _upsert_override(shop_overnight, h_date.isoformat(), "01:00:00", "05:00:00")
            assert r.status_code == 200, f"create override(H) failed: {r.status_code} {r.text}"
            await _set_duration(shop_overnight, 30)

            # 前日(日曜)にShopClosureを設定する。前日セッション(Weekly 18:00-翌03:00)の
            # 継続と判定されるなら月曜00:30はNG(temporary_closure)になるはずで、
            # 当日(月曜)Overrideセッションと判定されるなら影響を受けないはず。
            h_prev = h_date - timedelta(days=1)
            r = await client.post(f"/api/v1/shops/{shop_overnight}/closures", json={
                "start_date": h_prev.isoformat(), "end_date": h_prev.isoformat(), "reason": "H監査用",
            }, headers=owner_a)
            assert r.status_code == 200

            # 月曜00:30: 当日Overrideの開店(01:00)より前 → 前日からの日跨ぎ継続を確認する
            # 分岐に入り、前日(日曜)のWeeklyセッション(18:00-翌03:00)に該当 →
            # 前日のShopClosureが効いてNGになるはず。
            body = await _check(shop_overnight, h_date.isoformat(), "00:30")
            assert body["available"] is False and body["reason_code"] == "temporary_closure", (
                f"月曜00:30は前日セッション継続としてClosureの影響を受けるはずが: {body}"
            )

            # 月曜01:00ちょうど: 当日Overrideの開店時刻ちょうど → today優先の決定的な
            # 分岐（start_dt.time() >= today_hours.opening_time）により、前日からの
            # 継続は一切検討されず、当日Overrideセッションとして扱われるはず
            # （前日のClosureの影響を受けない）。
            body = await _check(shop_overnight, h_date.isoformat(), "01:00")
            assert body["available"] is True, (
                f"月曜01:00は当日Overrideセッションとして扱われ、前日Closureの影響を受けないはずが: {body}"
            )
            # 月曜02:00（当日Override内、前日セッションとの重複が疑われる時間帯）も同様
            body = await _check(shop_overnight, h_date.isoformat(), "02:00")
            assert body["available"] is True, (
                f"月曜02:00も当日Overrideセッションとして一意に判定されるはずが: {body}"
            )
            print("H. Section30監査（前日継続と当日Overrideの帰属先は一意で曖昧さなし）: OK")

            # ===== I. CHECK/CREATE/OWNER-UPDATE/GET-AVAILABILITYの一貫性 =====
            await _clear_reservations(shop_a)
            i_date = _next_weekday(3, weeks_ahead=4)  # 木曜
            r = await _upsert_override(shop_a, i_date.isoformat(), "10:00:00", "15:00:00")
            assert r.status_code == 200, f"create override(I) failed: {r.status_code} {r.text}"
            await _set_duration(shop_a, 60)
            i_date_str = i_date.isoformat()

            # CHECK
            body = await _check(shop_a, i_date_str, "16:00")
            assert body["available"] is False and body["reason_code"] == "outside_business_hours", f"I-CHECK: {body}"

            # CREATE
            r = await _create(shop_a, i_date_str, "16:00", "09050003001")
            assert r.status_code == 200, f"I-CREATE: {r.status_code} {r.text}"
            create_body = r.json()
            assert create_body["success"] is False and create_body["reason_code"] == "outside_business_hours", (
                f"I-CREATE: {create_body}"
            )

            # GET-AVAILABILITY
            r = await client.get(f"/api/v1/reservations/shop/{shop_a}/availability", params={"date": i_date_str, "party_size": 2})
            assert r.status_code == 200, f"I-AVAILABILITY: {r.status_code} {r.text}"
            avail_body = r.json()
            slot_16 = next((s for s in avail_body["slots"] if s["time"] == "16:00"), None)
            assert slot_16 is None, f"I-AVAILABILITY: 16:00はOverride閉店後のためスロット自体が無いはずが: {avail_body}"
            slot_13 = next((s for s in avail_body["slots"] if s["time"] == "13:00"), None)
            assert slot_13 is not None and slot_13["available"] is True, f"I-AVAILABILITY 13:00枠: {avail_body}"

            # OWNER-UPDATE
            r = await _create(shop_a, i_date_str, "10:00", "09050003002")
            assert r.status_code == 200 and r.json()["success"] is True, f"I準備予約作成failed: {r.status_code} {r.text}"
            reservation_id = r.json()["reservation_id"]
            dt_outside = datetime.combine(i_date, datetime.strptime("16:00", "%H:%M").time())
            r = await client.put(f"/api/v1/reservations/{reservation_id}", json={
                "reservation_date": dt_outside.isoformat(),
            }, headers=owner_a)
            assert r.status_code == 400, f"I-OWNER-UPDATE: Override閉店後への変更はNGのはずが: {r.status_code} {r.text}"
            print("I. CHECK/CREATE/OWNER-UPDATE/GET-AVAILABILITYの一貫性: OK")

            # ===== J. Overrideバリデーション（共通helper経由であることの確認） =====
            j_date = (_next_weekday(2, weeks_ahead=8)).isoformat()
            # opening==closingでcloses_next_day未指定 → 拒否（ShopHoursと同じ意味）
            r = await _upsert_override(shop_a, j_date, "10:00:00", "10:00:00")
            assert r.status_code == 422, f"opening==closing(closes_next_day未指定)は拒否されるはずが: {r.status_code} {r.text}"
            # opening==closingでcloses_next_day=True → 24時間営業として許容
            r = await _upsert_override(shop_a, j_date, "10:00:00", "10:00:00", closes_next_day=True)
            assert r.status_code == 200, f"opening==closing(closes_next_day=True、24時間営業)は許容されるはずが: {r.status_code} {r.text}"
            # last_orderが営業時間範囲外 → 拒否
            j_date2 = (_next_weekday(2, weeks_ahead=9)).isoformat()
            r = await _upsert_override(shop_a, j_date2, "10:00:00", "15:00:00", last_order_time="16:00:00")
            assert r.status_code == 422, f"営業時間外のlast_orderは拒否されるはずが: {r.status_code} {r.text}"
            # closing < opening でcloses_next_day=False → 拒否
            r = await _upsert_override(shop_a, j_date2, "20:00:00", "10:00:00")
            assert r.status_code == 422, f"closing<openingでcloses_next_day=Falseは拒否されるはずが: {r.status_code} {r.text}"
            print("J. Overrideバリデーション（ShopHoursと同じ共通helper経由）: OK")

            # ===== K. Owner API（tenant isolation・upsert・list・delete） =====
            k_date = (_next_weekday(4, weeks_ahead=8)).isoformat()
            # 他テナント(owner_b)はshop_aのOverrideを作成・削除できない
            r = await _upsert_override(shop_a, k_date, "10:00:00", "15:00:00", headers=owner_b)
            assert r.status_code == 403, f"他テナントによるOverride作成は403のはずが: {r.status_code} {r.text}"

            # upsert: 同じ日付に2回PUTすると更新される（新規行が増えない）
            r = await _upsert_override(shop_a, k_date, "10:00:00", "15:00:00")
            assert r.status_code == 200, f"upsert(1回目) failed: {r.status_code} {r.text}"
            r = await client.get(f"/api/v1/shops/{shop_a}/hours-overrides")
            assert r.status_code == 200
            count_before = len([o for o in r.json() if o["target_date"] == k_date])
            assert count_before == 1, f"1回目upsert後は1件のはずが: {count_before}"

            r = await _upsert_override(shop_a, k_date, "11:00:00", "16:00:00")
            assert r.status_code == 200, f"upsert(2回目、更新) failed: {r.status_code} {r.text}"
            r = await client.get(f"/api/v1/shops/{shop_a}/hours-overrides")
            matches = [o for o in r.json() if o["target_date"] == k_date]
            assert len(matches) == 1, f"2回目upsert後も1件(更新)のはずが: {len(matches)}"
            assert matches[0]["opening_time"].startswith("11:00"), f"2回目upsertの内容が反映されていないはずが: {matches[0]}"

            # delete: 他テナントは削除できない
            r = await client.delete(f"/api/v1/shops/{shop_a}/hours-overrides/{k_date}", headers=owner_b)
            assert r.status_code == 403, f"他テナントによるOverride削除は403のはずが: {r.status_code} {r.text}"
            # delete: 本人は削除できる
            r = await client.delete(f"/api/v1/shops/{shop_a}/hours-overrides/{k_date}", headers=owner_a)
            assert r.status_code == 200, f"Override削除 failed: {r.status_code} {r.text}"
            r = await client.get(f"/api/v1/shops/{shop_a}/hours-overrides")
            matches = [o for o in r.json() if o["target_date"] == k_date]
            assert len(matches) == 0, f"削除後は0件のはずが: {len(matches)}"
            # delete: 存在しない日付は404
            r = await client.delete(f"/api/v1/shops/{shop_a}/hours-overrides/{k_date}", headers=owner_a)
            assert r.status_code == 404, f"存在しないOverrideの削除は404のはずが: {r.status_code} {r.text}"
            print("K. Owner API（tenant isolation・upsert・list・delete）: OK")

            # ===== L. チャット予約AIのhours_blockにOverrideが反映される =====
            l_date = _next_weekday(5, weeks_ahead=6)  # 土曜（未来の日付、hours_block表示対象）
            r = await _upsert_override(shop_a, l_date.isoformat(), "09:00:00", "12:00:00")
            assert r.status_code == 200, f"create override(L) failed: {r.status_code} {r.text}"

            r = await client.post(f"/api/v1/shops/{shop_a}/booking-ai/start")
            assert r.status_code == 200, f"booking-ai/start failed: {r.status_code} {r.text}"
            session_id = r.json()["session_id"]

            from app.services import voice_ai
            session = voice_ai._sessions[session_id]
            system_prompt = session["messages"][0]["content"]
            assert l_date.isoformat() in system_prompt or _to_ja_date_fragment(l_date) in system_prompt, (
                "hours_blockにOverride日付が含まれていないはずが含まれるべき"
            )
            assert "09:00" in system_prompt and "12:00" in system_prompt, (
                "hours_blockにOverrideの時刻が含まれていないはずが含まれるべき"
            )
            print("L. チャット予約AIのhours_blockにOverrideが反映される: OK")

            print("\n=== Phase D-3（特定日の営業時間 / Special Date Hours）: 全項目OK ===")


def _to_ja_date_fragment(d: date) -> str:
    return f"{d.year}年{d.month}月{d.day}日"


if __name__ == "__main__":
    asyncio.run(main())
