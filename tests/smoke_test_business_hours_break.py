"""
Reservation Intelligence Phase D-2（Break Windows / 休憩・予約停止時間）
スモークテスト

背景（本フェーズで実装した内容）:

1. ShopBreakTime（新規テーブル）を追加した。ShopHours（曜日ごとの営業時間枠）の
   UNIQUE(shop_id, day_of_week)制約には一切手を加えず、休憩は「同じ曜日に複数行」を
   許容する別テーブルとして追加した。

2. Phase D-1の「営業セッションは開始日に帰属する」という考え方を休憩にもそのまま
   適用し、休憩の開始・終了それぞれに独立したnext_dayフラグ（start_next_day/
   end_next_day）を持たせた（休憩は開始時刻自体が日跨ぎセッションの翌日側に
   位置しうるため、Phase D-1のend側だけの単一フラグでは表現できない）。

3. 休憩の登録時（Owner API）に、対象曜日の営業時間内に完全に収まっているか、
   他の休憩と重なっていないか（触れるだけの隣接はOK）を検証する。

4. check_single_slot_availability() / create_reservation() / update_reservation() /
   get_availability() の4箇所すべてで、休憩時間との重なりを同じロジック
   （_overlaps_break_time()）で判定する。available=Falseの理由は新規reason_code
   "break_time"（既存のreason_code体系とは別の、休憩専用の1つだけを追加）。

5. ShopClosure（終日休業）は休憩より優先される（終日休業判定が先）。

検証項目:
A. 通常営業時間（日跨ぎ不使用）の休憩境界値（触れるのはOK、重なりはNG）
B. 1日に複数の休憩（隣接は許容、重ならない限りOK）
C. 日跨ぎ営業（18:00〜翌03:00）での日跨ぎ休憩（翌00:00〜翌00:30）
D. 休憩自体が日付をまたぐケース（23:30〜翌00:30）
E. 休憩登録時のバリデーション（営業時間外の休憩は拒否、ゼロ長は拒否）
F. 休憩登録時の重複拒否（重なりはNG、隣接はOK）
G. CHECK/CREATE/OWNER-UPDATE/GET-AVAILABILITYの一貫性
H. 休憩を1件も登録していない店舗ではPhase D-1までと完全に同じ挙動（regression）

実行: python3 tests/smoke_test_business_hours_break.py
"""

import asyncio
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, date, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_break.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-break"
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
                "email": "owner-break-a@example.com", "password": "password123",
                "display_name": "休憩テストオーナー",
            })
            assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
            owner_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

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

            async def _clear_reservations(shop_id: str):
                async with AsyncSessionLocal() as session:
                    result = await session.execute(select(Reservation).filter(Reservation.shop_id == shop_id))
                    for res in result.scalars().all():
                        await session.delete(res)
                    await session.commit()

            async def _check(shop_id: str, date_str: str, time_str: str, party_size: int = 2):
                payload = {"date": date_str, "time": time_str, "party_size": party_size}
                r = await client.post(
                    f"/api/v1/shops/{shop_id}/realtime-voice/tools/check-availability", json=payload,
                )
                assert r.status_code == 200, f"check-availability failed: {r.status_code} {r.text}"
                return r.json()

            async def _create(shop_id: str, date_str: str, time_str: str, phone: str):
                payload = {
                    "date": date_str, "time": time_str, "party_size": 2,
                    "guest_name": "休憩テスト", "guest_phone": phone,
                    "call_id": "smoke-break-" + str(uuid.uuid4()),
                }
                return await client.post(
                    f"/api/v1/shops/{shop_id}/realtime-voice/tools/create-reservation", json=payload,
                )

            # ---- shop_plain: 通常営業（09:00-18:00、日跨ぎ不使用） ----
            r = await client.post("/api/v1/shops/register", json={
                "name": "休憩テスト食堂", "category": "食堂", "address": "東京都渋谷区3-3-3",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create shop_plain failed: {r.status_code} {r.text}"
            shop_plain = r.json()["shop_id"]

            r = await client.put(f"/api/v1/shops/{shop_plain}/hours", json={"hours": [
                {"day_of_week": d, "opening_time": "09:00:00", "closing_time": "18:00:00", "is_closed": False}
                for d in range(7)
            ]}, headers=owner_a)
            assert r.status_code == 200, f"set hours(plain) failed: {r.status_code} {r.text}"
            await _enable_reservations(shop_plain)
            r = await client.post(f"/api/v1/shops/{shop_plain}/tables", json={"name": "テーブルA", "capacity": 4}, headers=owner_a)
            assert r.status_code == 200

            # ---- shop_overnight: 18:00〜翌03:00 ----
            r = await client.post("/api/v1/shops/register", json={
                "name": "休憩テスト深夜営業", "category": "その他", "address": "東京都渋谷区4-4-4",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create shop_overnight failed: {r.status_code} {r.text}"
            shop_overnight = r.json()["shop_id"]

            r = await client.put(f"/api/v1/shops/{shop_overnight}/hours", json={"hours": [
                {
                    "day_of_week": d, "opening_time": "18:00:00", "closing_time": "03:00:00",
                    "is_closed": False, "closes_next_day": True,
                }
                for d in range(7)
            ]}, headers=owner_a)
            assert r.status_code == 200, f"set hours(overnight) failed: {r.status_code} {r.text}"
            await _enable_reservations(shop_overnight)
            r = await client.post(f"/api/v1/shops/{shop_overnight}/tables", json={"name": "ルームA", "capacity": 4}, headers=owner_a)
            assert r.status_code == 200

            # ===== H. 休憩ゼロ件のregression（先に確認しておく） =====
            await _clear_reservations(shop_plain)
            await _set_duration(shop_plain, 60)
            h_date = _next_weekday(2, weeks_ahead=2).isoformat()
            body = await _check(shop_plain, h_date, "12:00")
            assert body["available"] is True, f"休憩未登録時の12:00はOKのはずが: {body}"
            print("H. 休憩を1件も登録していない店舗はPhase D-1までと同じ挙動: OK")

            # ===== A. 通常営業時間の休憩境界値 =====
            await _set_duration(shop_plain, 60)
            r = await client.post(f"/api/v1/shops/{shop_plain}/break-times", json={
                "day_of_week": 2, "start_time": "12:00", "end_time": "13:00",
            }, headers=owner_a)
            assert r.status_code == 200, f"create break(plain) failed: {r.status_code} {r.text}"
            break_plain_id = r.json()["id"]

            a_date = h_date  # 同じ水曜
            body = await _check(shop_plain, a_date, "11:00")  # 11:00-12:00 触れるだけ
            assert body["available"] is True, f"11:00-12:00（休憩に触れるだけ）はOKのはずが: {body}"

            body = await _check(shop_plain, a_date, "13:00")  # 13:00-14:00 触れるだけ
            assert body["available"] is True, f"13:00-14:00（休憩に触れるだけ）はOKのはずが: {body}"

            body = await _check(shop_plain, a_date, "12:00")  # 12:00-13:00 完全に休憩内
            assert body["available"] is False and body["reason_code"] == "break_time", (
                f"12:00-13:00（休憩内）はNG(break_time)のはずが: {body}"
            )

            body = await _check(shop_plain, a_date, "11:30")  # 11:30-12:30 休憩にまたがる
            assert body["available"] is False and body["reason_code"] == "break_time", (
                f"11:30-12:30（休憩にまたがる）はNGのはずが: {body}"
            )

            body = await _check(shop_plain, a_date, "12:30")  # 12:30-13:30 休憩からはみ出る
            assert body["available"] is False and body["reason_code"] == "break_time", (
                f"12:30-13:30（休憩からはみ出る）はNGのはずが: {body}"
            )
            print("A. 通常営業時間の休憩境界値（触れるのはOK、重なりはNG）: OK")

            # ===== B. 1日に複数の休憩（隣接は許容） =====
            r = await client.post(f"/api/v1/shops/{shop_plain}/break-times", json={
                "day_of_week": 2, "start_time": "15:00", "end_time": "15:15",
            }, headers=owner_a)
            assert r.status_code == 200, f"create 2nd break failed: {r.status_code} {r.text}"

            await _set_duration(shop_plain, 15)
            body = await _check(shop_plain, a_date, "14:45")  # 14:45-15:00 触れるだけ
            assert body["available"] is True, f"14:45-15:00はOKのはずが: {body}"
            body = await _check(shop_plain, a_date, "15:00")  # 15:00-15:15 完全に休憩内
            assert body["available"] is False and body["reason_code"] == "break_time", f"15:00-15:15はNGのはずが: {body}"
            body = await _check(shop_plain, a_date, "15:15")  # 15:15-15:30 触れるだけ
            assert body["available"] is True, f"15:15-15:30はOKのはずが: {body}"
            # 隣接する休憩を追加（重ならない）
            r = await client.post(f"/api/v1/shops/{shop_plain}/break-times", json={
                "day_of_week": 2, "start_time": "13:00", "end_time": "13:30",
            }, headers=owner_a)
            assert r.status_code == 200, f"adjacent break(13:00-13:30, 隣接)failed: {r.status_code} {r.text}"
            r = await client.get(f"/api/v1/shops/{shop_plain}/break-times")
            assert r.status_code == 200 and len(r.json()) == 3, f"break-times一覧が3件のはずが: {r.status_code} {r.text}"
            print("B. 1日に複数の休憩（隣接は許容、重ならない限りOK）: OK")

            # ===== E. 休憩登録時のバリデーション =====
            # 営業時間外の休憩（09:00-18:00の店に08:00-09:30は範囲外）
            r = await client.post(f"/api/v1/shops/{shop_plain}/break-times", json={
                "day_of_week": 3, "start_time": "08:00", "end_time": "09:30",
            }, headers=owner_a)
            assert r.status_code == 400, f"営業時間外の休憩は拒否されるはずが: {r.status_code} {r.text}"

            # ゼロ長（start==end）は拒否（スキーマレベルのバリデーション -> 422）
            r = await client.post(f"/api/v1/shops/{shop_plain}/break-times", json={
                "day_of_week": 3, "start_time": "12:00", "end_time": "12:00",
            }, headers=owner_a)
            assert r.status_code == 422, f"ゼロ長の休憩は拒否されるはずが: {r.status_code} {r.text}"

            # 逆転（start > end、next_dayフラグなし）も拒否
            r = await client.post(f"/api/v1/shops/{shop_plain}/break-times", json={
                "day_of_week": 3, "start_time": "13:00", "end_time": "12:00",
            }, headers=owner_a)
            assert r.status_code == 422, f"逆転した休憩は拒否されるはずが: {r.status_code} {r.text}"

            # 定休日が設定されていない曜日への休憩登録は拒否（day_of_week=4はまだhours未設定 -
            # 実際にはshop_plainは全曜日hours設定済みなので、代わりに未設定シナリオを別途確認）
            print("E. 休憩登録時のバリデーション（営業時間外・ゼロ長・逆転は拒否）: OK")

            # ===== F. 休憩登録時の重複拒否 =====
            r = await client.post(f"/api/v1/shops/{shop_plain}/break-times", json={
                "day_of_week": 2, "start_time": "12:30", "end_time": "13:30",
            }, headers=owner_a)
            assert r.status_code == 400, f"12:00-13:00と重なる12:30-13:30は拒否されるはずが: {r.status_code} {r.text}"

            # 既存の13:00-13:30休憩（Bで登録済み）に隣接する13:30-14:30は、
            # 重ならない（触れるだけ）ため許容されるはず
            r = await client.post(f"/api/v1/shops/{shop_plain}/break-times", json={
                "day_of_week": 2, "start_time": "13:30", "end_time": "14:30",
            }, headers=owner_a)
            assert r.status_code == 200, f"13:00-13:30に隣接する13:30-14:30は許容されるはずが: {r.status_code} {r.text}"
            print("F. 休憩登録時の重複拒否（重なりはNG、隣接はOK）: OK")

            # ===== C. 日跨ぎ営業での日跨ぎ休憩 =====
            await _clear_reservations(shop_overnight)
            mon_c = _next_weekday(0, weeks_ahead=3)
            tue_c = mon_c + timedelta(days=1)
            r = await client.post(f"/api/v1/shops/{shop_overnight}/break-times", json={
                "day_of_week": 0, "start_time": "00:00", "end_time": "00:30",
                "start_next_day": True, "end_next_day": True,
            }, headers=owner_a)
            assert r.status_code == 200, f"overnight break(翌00:00-翌00:30) failed: {r.status_code} {r.text}"

            await _set_duration(shop_overnight, 30)
            # 月曜23:30-翌00:00 触れるだけ
            body = await _check(shop_overnight, mon_c.isoformat(), "23:30")
            assert body["available"] is True, f"月曜23:30-翌00:00（休憩に触れるだけ）はOKのはずが: {body}"

            body = await _check(shop_overnight, tue_c.isoformat(), "00:00")  # 翌00:00-翌00:30 休憩内
            assert body["available"] is False and body["reason_code"] == "break_time", (
                f"火曜00:00(月曜session継続、休憩内)はNGのはずが: {body}"
            )

            body = await _check(shop_overnight, tue_c.isoformat(), "00:30")  # 翌00:30-翌01:00 触れるだけ
            assert body["available"] is True, f"火曜00:30（休憩に触れるだけ）はOKのはずが: {body}"

            r = await _create(shop_overnight, tue_c.isoformat(), "00:00", "09050001001")
            assert r.status_code == 200, f"create(Tool経由)はstatus 200で返るはずが: {r.status_code} {r.text}"
            create_c_body = r.json()
            assert create_c_body["success"] is False and create_c_body["reason_code"] == "break_time", (
                f"火曜00:00のcreateは休憩理由(reason_code=break_time)でNGのはずが: {create_c_body}"
            )
            print("C. 日跨ぎ営業での日跨ぎ休憩（翌00:00〜翌00:30）: OK")

            # ===== D. 休憩自体が日付をまたぐケース（23:30〜翌00:30） =====
            await _clear_reservations(shop_overnight)
            # Cを使った休憩を個別に削除してから、Dの休憩(23:30-翌00:30)だけの状態にする
            r_list = await client.get(f"/api/v1/shops/{shop_overnight}/break-times")
            for bt in r_list.json():
                await client.delete(f"/api/v1/shops/{shop_overnight}/break-times/{bt['id']}", headers=owner_a)

            r = await client.post(f"/api/v1/shops/{shop_overnight}/break-times", json={
                "day_of_week": 0, "start_time": "23:30", "end_time": "00:30",
                "start_next_day": False, "end_next_day": True,
            }, headers=owner_a)
            assert r.status_code == 200, f"straddling break(23:30-翌00:30) failed: {r.status_code} {r.text}"

            mon_d = _next_weekday(0, weeks_ahead=4)
            tue_d = mon_d + timedelta(days=1)
            await _set_duration(shop_overnight, 30)

            body = await _check(shop_overnight, mon_d.isoformat(), "23:00")  # 23:00-23:30 触れるだけ
            assert body["available"] is True, f"23:00-23:30（休憩に触れるだけ）はOKのはずが: {body}"

            body = await _check(shop_overnight, mon_d.isoformat(), "23:30")  # 23:30-翌00:00 休憩内
            assert body["available"] is False and body["reason_code"] == "break_time", f"23:30-翌00:00はNGのはずが: {body}"

            body = await _check(shop_overnight, tue_d.isoformat(), "00:15")  # 翌00:15-翌00:45 休憩とまたがる
            assert body["available"] is False and body["reason_code"] == "break_time", f"翌00:15-翌00:45はNGのはずが: {body}"

            body = await _check(shop_overnight, tue_d.isoformat(), "00:30")  # 翌00:30-翌01:00 触れるだけ
            assert body["available"] is True, f"翌00:30-翌01:00（休憩に触れるだけ）はOKのはずが: {body}"
            print("D. 休憩自体が日付をまたぐケース（23:30〜翌00:30）: OK")

            # ===== G. CHECK/CREATE/OWNER-UPDATE/GET-AVAILABILITYの一貫性 =====
            await _clear_reservations(shop_plain)
            g_date = _next_weekday(3, weeks_ahead=6)  # 木曜（休憩未登録の曜日を新規に使う）
            r = await client.put(f"/api/v1/shops/{shop_plain}/hours", json={"hours": [
                {"day_of_week": d, "opening_time": "09:00:00", "closing_time": "18:00:00", "is_closed": False}
                for d in range(7)
            ]}, headers=owner_a)
            assert r.status_code == 200
            r = await client.post(f"/api/v1/shops/{shop_plain}/break-times", json={
                "day_of_week": g_date.weekday(), "start_time": "12:00", "end_time": "13:00",
            }, headers=owner_a)
            assert r.status_code == 200, f"create break(G) failed: {r.status_code} {r.text}"

            await _set_duration(shop_plain, 60)
            g_date_str = g_date.isoformat()

            # CHECK
            body = await _check(shop_plain, g_date_str, "12:00")
            assert body["available"] is False and body["reason_code"] == "break_time", f"G-CHECK: {body}"

            # CREATE（Realtime Tool経由。実処理は既存create_reservation()そのもの）
            r = await _create(shop_plain, g_date_str, "12:00", "09050002001")
            assert r.status_code == 200, f"G-CREATE: {r.status_code} {r.text}"
            create_body = r.json()
            assert create_body["success"] is False and create_body["reason_code"] == "break_time", (
                f"G-CREATE: {create_body}"
            )

            # GET-AVAILABILITY
            r = await client.get(f"/api/v1/reservations/shop/{shop_plain}/availability", params={"date": g_date_str, "party_size": 2})
            assert r.status_code == 200, f"G-AVAILABILITY: {r.status_code} {r.text}"
            avail_body = r.json()
            slot_12 = next((s for s in avail_body["slots"] if s["time"] == "12:00"), None)
            assert slot_12 is not None and slot_12["available"] is False, f"G-AVAILABILITY 12:00枠: {avail_body}"
            slot_11 = next((s for s in avail_body["slots"] if s["time"] == "11:00"), None)
            assert slot_11 is not None and slot_11["available"] is True, f"G-AVAILABILITY 11:00枠: {avail_body}"

            # OWNER-UPDATE: 別の時間に有効な予約を先に作り、それを休憩時間へ更新しようとしてNGになることを確認
            r = await _create(shop_plain, g_date_str, "09:00", "09050002002")
            assert r.status_code == 200 and r.json()["success"] is True, f"G準備予約作成failed: {r.status_code} {r.text}"
            reservation_id = r.json()["reservation_id"]

            dt_break = datetime.combine(g_date, datetime.strptime("12:00", "%H:%M").time())
            r = await client.put(f"/api/v1/reservations/{reservation_id}", json={
                "reservation_date": dt_break.isoformat(),
            }, headers=owner_a)
            assert r.status_code == 400, f"G-OWNER-UPDATE: 休憩時間への変更はNGのはずが: {r.status_code} {r.text}"
            assert "休憩" in r.json().get("detail", ""), f"G-OWNER-UPDATE detail: {r.json()}"
            print("G. CHECK/CREATE/OWNER-UPDATE/GET-AVAILABILITYの一貫性: OK")

            print("\n=== Phase D-2（Break Windows / 休憩・予約停止時間）: 全項目OK ===")


if __name__ == "__main__":
    asyncio.run(main())
