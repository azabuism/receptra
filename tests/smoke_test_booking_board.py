"""
Owner Booking Board V1（Reservation Intelligence可視化）スモークテスト

背景（本フェーズで実装した内容）:

1. 新設: GET /api/v1/reservations/shop/{shop_id}/board?date=YYYY-MM-DD
   既存の GET /shop/{shop_id} 一覧（日付フィルタ無し・古い順・最大200件）では
   1日単位の予約表を効率よく描画できないため、日付を開始日とする「営業セッション」
   （Reservation Intelligence Phase D-1の_resolve_business_session()と同じ
   「セッションは開始日に帰属する」考え方）に属する予約・休憩時間・営業時間を
   まとめて返す新エンドポイントを追加した。オーナー認証必須・tenant分離必須。
   guest_phoneは一覧のためマスク済み（"****"+末尾4桁、CallbackRequestと同じ規約）。

2. 安全化: GET /api/v1/reservations/{reservation_id}
   従来current_user依存を一切持たない未認証・未使用（grep調査で呼び出し元皆無を
   確認済み）のエンドポイントに、オーナー認証・tenant分離チェックを追加した。
   Booking Boardの「フル電話番号を見るための詳細取得」はこの安全化した既存
   エンドポイントを再利用する（新しいreveal専用APIは作らない）。

検証項目:
A. 営業日・予約ありの基本ケース（effective_duration_minutesの計算、NULL
   duration_minutesのフォールバック、休憩時間の表示）
B. 日跨ぎ営業セッションの継続（火曜01:00の予約が月曜の予約表に表示される）
C. 翌日自身が独立した営業時間を持つ場合のセッション境界の打ち切り
D. 定休日（closed_regular）
E. 営業時間未設定（hours_not_configured）
F. 臨時休業（closed_temporary、ShopClosure）
G. 電話番号マスキング（一覧にフル電話番号が一切含まれない）
H. Tenant isolation（Tenant Aのトークンで Tenant B の予約表を取得できない）
   ※ テストDB内で作成した架空のTenant A/Bのみを使用。本番データ・他tenantの
   実データには一切アクセスしない。
I. GET /api/v1/reservations/{reservation_id} の認証必須化（未認証401/403、
   他tenantオーナー403、正しいオーナーはフル電話番号を取得できる）

実行: python3 tests/smoke_test_booking_board.py
"""

import asyncio
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, date, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_board.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-board"
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
            # ===== セットアップ: Tenant A（架空のテストオーナー） =====
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-board-a@example.com", "password": "password123",
                "display_name": "予約表テストオーナーA",
            })
            assert r.status_code in (200, 201), f"register A failed: {r.status_code} {r.text}"
            owner_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "予約表テスト居酒屋", "category": "居酒屋", "address": "東京都渋谷区9-9-9",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create shop_a failed: {r.status_code} {r.text}"
            shop_a = r.json()["shop_id"]

            # 全曜日 18:00〜翌03:00（日跨ぎ）を基本パターンにする
            hours_payload = {"hours": [
                {
                    "day_of_week": d, "opening_time": "18:00:00", "closing_time": "03:00:00",
                    "is_closed": False, "closes_next_day": True,
                }
                for d in range(7)
            ]}
            r = await client.put(f"/api/v1/shops/{shop_a}/hours", json=hours_payload, headers=owner_a)
            assert r.status_code == 200, f"set hours(shop_a) failed: {r.status_code} {r.text}"

            from app.database import AsyncSessionLocal
            from app.models.shop import Shop, ShopHours
            from app.models.reservation import Reservation
            from sqlalchemy import select

            async def _enable_reservations(shop_id: str):
                async with AsyncSessionLocal() as session:
                    shop_obj = await session.get(Shop, shop_id)
                    shop_obj.reservations_enabled = True
                    await session.commit()

            await _enable_reservations(shop_a)

            mon = _next_weekday(0, weeks_ahead=2)
            tue = mon + timedelta(days=1)
            wed = mon + timedelta(days=2)
            mon_str, tue_str, wed_str = mon.isoformat(), tue.isoformat(), wed.isoformat()

            async def _insert_reservation(shop_id, dt, duration_minutes, guest_name="太郎", guest_phone="09012345678", status="confirmed", number_of_people=2):
                async with AsyncSessionLocal() as session:
                    res = Reservation(
                        id=str(uuid.uuid4()), shop_id=shop_id,
                        guest_name=guest_name, guest_phone=guest_phone,
                        reservation_date=dt, duration_minutes=duration_minutes,
                        number_of_people=number_of_people, status=status,
                        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                    )
                    session.add(res)
                    await session.commit()
                    return res.id

            async def _board(shop_id, date_str, headers):
                return await client.get(
                    f"/api/v1/reservations/shop/{shop_id}/board", params={"date": date_str}, headers=headers
                )

            # ===== A. 基本ケース: 月曜20:00開始・NULL duration_minutesのフォールバック =====
            res_id_a = await _insert_reservation(shop_a, datetime(mon.year, mon.month, mon.day, 20, 0), duration_minutes=None)
            r = await _board(shop_a, mon_str, owner_a)
            assert r.status_code == 200, f"board(A) failed: {r.status_code} {r.text}"
            body = r.json()
            assert body["day_status"] == "open", f"day_status should be open: {body}"
            assert body["opening_time"] == "18:00" and body["closing_time"] == "03:00", f"hours mismatch: {body}"
            assert body["closes_next_day"] is True, f"closes_next_day should be True: {body}"
            assert len(body["reservations"]) == 1, f"expected 1 reservation: {body}"
            item = body["reservations"][0]
            assert item["id"] == res_id_a
            # shop.reservation_duration_minutesはデフォルト(90)のまま変更していないため、
            # NULL duration_minutesは90分にフォールバックするはず
            assert item["effective_duration_minutes"] == 90, f"NULL duration_minutes should fallback to 90: {item}"
            print("A. 基本ケース（NULL duration_minutesの90分フォールバック）: OK")

            # ===== G. 電話番号マスキング =====
            assert item["guest_phone_masked"] == "****5678", f"phone mask mismatch: {item}"
            assert "09012345678" not in r.text, "フル電話番号が一覧レスポンスに含まれてはならない"
            print("G. 電話番号マスキング（フル電話番号が一覧に含まれない）: OK")

            await _clear(AsyncSessionLocal, Reservation, shop_a)

            # ===== B. 日跨ぎ継続: 火曜01:00の予約は月曜の予約表に表示される =====
            # (火曜自身にはまだ独自の営業時間を設定していない前提。全曜日同一設定のため
            #  火曜も18:00〜翌03:00を持つが、01:00は火曜のopening_time(18:00)より前 =>
            #  月曜からの継続と判定されるはず)
            res_id_b = await _insert_reservation(shop_a, datetime(tue.year, tue.month, tue.day, 1, 0), duration_minutes=60)
            r_mon = await _board(shop_a, mon_str, owner_a)
            r_tue = await _board(shop_a, tue_str, owner_a)
            assert r_mon.status_code == 200 and r_tue.status_code == 200
            mon_body, tue_body = r_mon.json(), r_tue.json()
            mon_ids = {x["id"] for x in mon_body["reservations"]}
            tue_ids = {x["id"] for x in tue_body["reservations"]}
            assert res_id_b in mon_ids, f"火曜01:00の予約は月曜の継続セッションに属するはず: {mon_body}"
            assert res_id_b not in tue_ids, f"火曜01:00の予約が火曜側の予約表にも重複して出てはいけない: {tue_body}"
            print("B. 日跨ぎ継続（火曜01:00 -> 月曜の予約表）: OK")

            await _clear(AsyncSessionLocal, Reservation, shop_a)

            # ===== C. 翌日自身が独立した早い営業時間を持つ場合の境界打ち切り =====
            # 火曜だけ01:00開店に変更する
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(ShopHours).filter(ShopHours.shop_id == shop_a, ShopHours.day_of_week == tue.weekday())
                )
                tue_hours = result.scalar_one()
                tue_hours.opening_time = datetime.strptime("01:00", "%H:%M").time()
                tue_hours.closing_time = datetime.strptime("10:00", "%H:%M").time()
                tue_hours.closes_next_day = False
                await session.commit()

            res_id_c1 = await _insert_reservation(shop_a, datetime(tue.year, tue.month, tue.day, 0, 30), duration_minutes=30)  # 月曜継続側(01:00より前)
            res_id_c2 = await _insert_reservation(shop_a, datetime(tue.year, tue.month, tue.day, 1, 30), duration_minutes=30)  # 火曜自身のセッション(01:00以降)

            r_mon = await _board(shop_a, mon_str, owner_a)
            r_tue = await _board(shop_a, tue_str, owner_a)
            mon_ids = {x["id"] for x in r_mon.json()["reservations"]}
            tue_ids = {x["id"] for x in r_tue.json()["reservations"]}
            assert res_id_c1 in mon_ids and res_id_c1 not in tue_ids, (
                f"火曜00:30(火曜開店01:00より前)は月曜継続のはず: mon={mon_ids} tue={tue_ids}"
            )
            assert res_id_c2 in tue_ids and res_id_c2 not in mon_ids, (
                f"火曜01:30(火曜自身の開店後)は火曜自身のセッションのはず: mon={mon_ids} tue={tue_ids}"
            )
            print("C. 翌日自身の開店時刻によるセッション境界の打ち切り: OK")

            await _clear(AsyncSessionLocal, Reservation, shop_a)
            # 火曜の営業時間を元に戻す
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(ShopHours).filter(ShopHours.shop_id == shop_a, ShopHours.day_of_week == tue.weekday())
                )
                tue_hours = result.scalar_one()
                tue_hours.opening_time = datetime.strptime("18:00", "%H:%M").time()
                tue_hours.closing_time = datetime.strptime("03:00", "%H:%M").time()
                tue_hours.closes_next_day = True
                await session.commit()

            # ===== D. 定休日 =====
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(ShopHours).filter(ShopHours.shop_id == shop_a, ShopHours.day_of_week == wed.weekday())
                )
                wed_hours = result.scalar_one()
                wed_hours.is_closed = True
                await session.commit()
            r = await _board(shop_a, wed_str, owner_a)
            assert r.status_code == 200 and r.json()["day_status"] == "closed_regular", f"定休日判定ミス: {r.json()}"
            print("D. 定休日（closed_regular）: OK")
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(ShopHours).filter(ShopHours.shop_id == shop_a, ShopHours.day_of_week == wed.weekday())
                )
                wed_hours = result.scalar_one()
                wed_hours.is_closed = False
                await session.commit()

            # ===== E. 営業時間未設定 =====
            r = await client.post("/api/v1/shops/register", json={
                "name": "営業時間未設定店", "category": "その他", "address": "東京都渋谷区5-5-5",
            }, headers=owner_a)
            assert r.status_code in (200, 201)
            shop_noconfig = r.json()["shop_id"]
            r = await _board(shop_noconfig, mon_str, owner_a)
            assert r.status_code == 200 and r.json()["day_status"] == "hours_not_configured", f"未設定判定ミス: {r.json()}"
            print("E. 営業時間未設定（hours_not_configured）: OK")

            # ===== F. 臨時休業 =====
            r = await client.post(f"/api/v1/shops/{shop_a}/closures", json={
                "start_date": mon_str, "end_date": mon_str, "reason": "設備点検",
            }, headers=owner_a)
            assert r.status_code == 200, f"create closure failed: {r.status_code} {r.text}"
            r = await _board(shop_a, mon_str, owner_a)
            body = r.json()
            assert body["day_status"] == "closed_temporary", f"臨時休業判定ミス: {body}"
            assert body["closure_reason"] == "設備点検", f"closure_reason mismatch: {body}"
            print("F. 臨時休業（closed_temporary）: OK")
            # 後続テストに影響しないよう削除
            async with AsyncSessionLocal() as session:
                from app.models.shop import ShopClosure
                result = await session.execute(select(ShopClosure).filter(ShopClosure.shop_id == shop_a))
                for c in result.scalars().all():
                    await session.delete(c)
                await session.commit()

            # ===== 休憩時間の表示 =====
            r = await client.post(f"/api/v1/shops/{shop_a}/break-times", json={
                "day_of_week": mon.weekday(), "start_time": "23:30:00", "end_time": "00:30:00",
                "start_next_day": False, "end_next_day": True,
            }, headers=owner_a)
            assert r.status_code == 200, f"create break-time failed: {r.status_code} {r.text}"
            r = await _board(shop_a, mon_str, owner_a)
            body = r.json()
            assert len(body["break_times"]) == 1, f"break_times should have 1 entry: {body}"
            bt = body["break_times"][0]
            assert bt["start_time"] == "23:30" and bt["end_time"] == "00:30" and bt["end_next_day"] is True, (
                f"break-time display mismatch: {bt}"
            )
            print("休憩時間の表示（日跨ぎend_next_day含む）: OK")

            # ===== H. Tenant isolation（テストDB内の架空Tenant Bのみ使用） =====
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-board-b@example.com", "password": "password123",
                "display_name": "予約表テストオーナーB",
            })
            assert r.status_code in (200, 201)
            owner_b = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await _board(shop_a, mon_str, owner_b)
            assert r.status_code == 403, f"Tenant Bが Tenant Aの予約表を取得できてしまった: {r.status_code} {r.text}"
            print("H. Tenant isolation（他tenantの予約表を403で拒否）: OK")

            # ===== I. GET /reservations/{id} の認証必須化 =====
            res_id_detail = await _insert_reservation(shop_a, datetime(mon.year, mon.month, mon.day, 20, 0), duration_minutes=60, guest_phone="08099998888")

            r = await client.get(f"/api/v1/reservations/{res_id_detail}")
            assert r.status_code in (401, 403), f"未認証アクセスは401/403のはずが: {r.status_code} {r.text}"

            r = await client.get(f"/api/v1/reservations/{res_id_detail}", headers=owner_b)
            assert r.status_code == 403, f"他tenantオーナーは403のはずが: {r.status_code} {r.text}"

            r = await client.get(f"/api/v1/reservations/{res_id_detail}", headers=owner_a)
            assert r.status_code == 200, f"正しいオーナーは200のはずが: {r.status_code} {r.text}"
            assert r.json()["guest_phone"] == "08099998888", f"正しいオーナーはフル電話番号を取得できるはずが: {r.json()}"
            print("I. GET /reservations/{id} の認証必須化・tenant分離・フル電話番号取得: OK")

            print("\nALL BOOKING BOARD CHECKS PASSED")


async def _clear(AsyncSessionLocal, Reservation, shop_id):
    from sqlalchemy import select
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Reservation).filter(Reservation.shop_id == shop_id))
        for res in result.scalars().all():
            await session.delete(res)
        await session.commit()


if __name__ == "__main__":
    asyncio.run(main())
