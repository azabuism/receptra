"""
BOOKING BOARD WEEK VIEW V1（週次サマリー表示）スモークテスト

背景（本フェーズで実装した内容）:

新設: GET /api/v1/reservations/shop/{shop_id}/week?start_date=YYYY-MM-DD

Week ViewはDaily Board（GET /shop/{shop_id}/board）の上位に立つ「一覧レイヤー」であり、
新しい予約可否判定・営業時間解決ロジックは一切追加していない。既存の
_get_closure_for_date/_resolve_day_hours（Board APIと全く同じ関数）をそのまま
再利用し、月曜始まりで必ず7日分のサマリー（day_status・営業時間・予約件数・
ミニタイムライン用の最小限の予約データ）を1回のリクエストでまとめて返す。

設計方針（ユーザー確認済み・確定仕様）:
- reservation_count/reservationsは、Daily Board側（shop-manage.html）の
  「表示予約」定義（status !== 'cancelled'のみを除外）と完全に一致させる。
- PII最小化: guest_name/guest_phone(_masked)/guest_email/special_requests/
  service_name/staff_name/table_name/staff_id/table_id/status、および
  staff_roster/table_rosterは一切レスポンスに含めない（Week ViewはResource Laneを
  表示しない仕様のため）。個々の予約はreservation_date・effective_duration_minutes
  のみ。

検証項目:
A. 未認証は拒否される（401/403）
B. 不正なトークンは拒否される（401/403）
C. 他tenantのオーナーは403で拒否される
D. 正しいオーナーは200・常に7日分・月曜→日曜の順で返る
E. 通常営業日（open）: 0件予約
F. 通常営業日（open）: 複数予約・cancelled除外がDaily Board定義と一致
G. 定休日（closed_regular）
H. 臨時休業（closed_temporary、ShopClosure、closure_reason含む）
I. 営業時間未設定（hours_not_configured）
J. 日跨ぎ営業（overnight）: 翌日01:00の予約が前日のセッションに正しく帰属する
K. レスポンス全体にPII（guest_name/フル電話番号/email/special_requests/
   service_name/staff_name/table_name等のフィールド自体・値）が一切含まれない

実行: python3 tests/smoke_test_week_view.py
"""

import asyncio
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, date, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_week_view.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-week-view"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402

JST = timezone(timedelta(hours=9))


def _next_monday(weeks_ahead: int = 2):
    today = date.today()
    days_ahead = (0 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead + 7 * (weeks_ahead - 1))


async def main():
    from app.main import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # ===== セットアップ: Tenant A / Tenant B（架空のテストオーナー） =====
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-week-a@example.com", "password": "password123",
                "display_name": "週表示テストオーナーA",
            })
            assert r.status_code in (200, 201), f"register A failed: {r.status_code} {r.text}"
            owner_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-week-b@example.com", "password": "password123",
                "display_name": "週表示テストオーナーB",
            })
            assert r.status_code in (200, 201), f"register B failed: {r.status_code} {r.text}"
            owner_b = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "週表示テスト居酒屋", "category": "居酒屋", "address": "東京都渋谷区2-2-2",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create shop_a failed: {r.status_code} {r.text}"
            shop_a = r.json()["shop_id"]

            # 全曜日 18:00〜23:00（日跨ぎなし）を基本パターンにする
            hours_payload = {"hours": [
                {"day_of_week": d, "opening_time": "18:00:00", "closing_time": "23:00:00", "is_closed": False, "closes_next_day": False}
                for d in range(7)
            ]}
            r = await client.put(f"/api/v1/shops/{shop_a}/hours", json=hours_payload, headers=owner_a)
            assert r.status_code == 200, f"set hours(shop_a) failed: {r.status_code} {r.text}"

            from app.database import AsyncSessionLocal
            from app.models.shop import Shop, ShopHours, ShopClosure
            from app.models.reservation import Reservation
            from sqlalchemy import select

            async def _enable_reservations(shop_id: str):
                async with AsyncSessionLocal() as session:
                    shop_obj = await session.get(Shop, shop_id)
                    shop_obj.reservations_enabled = True
                    await session.commit()

            await _enable_reservations(shop_a)

            mon = _next_monday(weeks_ahead=2)
            mon_str = mon.isoformat()
            week_dates = [mon + timedelta(days=i) for i in range(7)]
            week_date_strs = [d.isoformat() for d in week_dates]

            async def _insert_reservation(shop_id, dt, guest_name="太郎", guest_phone="09012345678",
                                           guest_email="taro@example.com", special_requests="窓際希望",
                                           status="confirmed", number_of_people=2, duration_minutes=60):
                async with AsyncSessionLocal() as session:
                    res = Reservation(
                        id=str(uuid.uuid4()), shop_id=shop_id,
                        guest_name=guest_name, guest_phone=guest_phone, guest_email=guest_email,
                        special_requests=special_requests,
                        reservation_date=dt, duration_minutes=duration_minutes,
                        number_of_people=number_of_people, status=status,
                        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                    )
                    session.add(res)
                    await session.commit()
                    return res.id

            async def _week(shop_id, start_date_str, headers=None):
                kwargs = {"params": {"start_date": start_date_str}}
                if headers is not None:
                    kwargs["headers"] = headers
                return await client.get(f"/api/v1/reservations/shop/{shop_id}/week", **kwargs)

            # ===== A. 未認証は拒否される =====
            r = await _week(shop_a, mon_str)
            assert r.status_code in (401, 403), f"未認証は401/403のはずが: {r.status_code} {r.text}"
            print("A. 未認証は拒否される: OK")

            # ===== B. 不正なトークン =====
            r = await _week(shop_a, mon_str, headers={"Authorization": "Bearer this-is-not-a-valid-token"})
            assert r.status_code in (401, 403), f"不正トークンは401/403のはずが: {r.status_code} {r.text}"
            print("B. 不正なトークンは拒否される: OK")

            # ===== C. 他tenantのオーナーは403 =====
            r = await _week(shop_a, mon_str, headers=owner_b)
            assert r.status_code == 403, f"他tenantは403のはずが: {r.status_code} {r.text}"
            print("C. 他tenantのオーナーは403で拒否される: OK")

            # ===== D. 正しいオーナーは200・常に7日分・月曜→日曜の順 =====
            r = await _week(shop_a, mon_str, headers=owner_a)
            assert r.status_code == 200, f"week(D) failed: {r.status_code} {r.text}"
            body = r.json()
            assert body["shop_id"] == shop_a
            assert body["start_date"] == mon_str, f"start_dateは月曜日のはずが: {body['start_date']}"
            assert len(body["days"]) == 7, f"必ず7日分のはずが: {len(body['days'])}"
            assert [dday["date"] for dday in body["days"]] == week_date_strs, (
                f"月曜→日曜の順で日付が並ぶはずが: {[dday['date'] for dday in body['days']]} != {week_date_strs}"
            )
            for dday in body["days"]:
                assert dday["day_status"] == "open", f"全日open想定のはずが: {dday}"
                assert dday["opening_time"] == "18:00" and dday["closing_time"] == "23:00"
                assert dday["reservation_count"] == 0 and dday["reservations"] == []
            print("D. 正しいオーナー: 200・常に7日分・月曜→日曜の順・0件予約(open): OK")

            # ===== F. 複数予約・cancelled除外がDaily Board定義と一致 =====
            mon_dt = mon_dt_ = mon
            confirmed_id = await _insert_reservation(shop_a, datetime(mon.year, mon.month, mon.day, 19, 0), status="confirmed")
            pending_id = await _insert_reservation(shop_a, datetime(mon.year, mon.month, mon.day, 20, 0), status="pending")
            no_show_id = await _insert_reservation(shop_a, datetime(mon.year, mon.month, mon.day, 21, 0), status="no_show")
            completed_id = await _insert_reservation(shop_a, datetime(mon.year, mon.month, mon.day, 21, 30), status="completed")
            cancelled_id = await _insert_reservation(shop_a, datetime(mon.year, mon.month, mon.day, 22, 0), status="cancelled")

            r = await _week(shop_a, mon_str, headers=owner_a)
            body = r.json()
            mon_day = body["days"][0]
            # Daily Board (shop-manage.html) の定義: status !== 'cancelled' のみ除外。
            # pending/confirmed/no_show/completedはすべてカウントに含む。
            assert mon_day["reservation_count"] == 4, f"cancelledのみ除外して4件のはずが: {mon_day}"
            assert len(mon_day["reservations"]) == 4, f"reservations配列も4件のはずが: {mon_day}"
            returned_starts = sorted(rb["reservation_date"] for rb in mon_day["reservations"])
            expected_starts = sorted([
                datetime(mon.year, mon.month, mon.day, 19, 0).isoformat(),
                datetime(mon.year, mon.month, mon.day, 20, 0).isoformat(),
                datetime(mon.year, mon.month, mon.day, 21, 0).isoformat(),
                datetime(mon.year, mon.month, mon.day, 21, 30).isoformat(),
            ])
            assert returned_starts == expected_starts, f"reservation_date一覧が一致しないはずが: {returned_starts} != {expected_starts}"
            for rb in mon_day["reservations"]:
                assert rb["effective_duration_minutes"] == 60
            print("F. 複数予約・cancelled除外がDaily Board定義(status!=='cancelled')と一致: OK")

            await _clear(AsyncSessionLocal, Reservation, shop_a)

            # ===== G. 定休日（closed_regular） =====
            wed = mon + timedelta(days=2)
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(ShopHours).filter(ShopHours.shop_id == shop_a, ShopHours.day_of_week == wed.weekday())
                )
                wed_hours = result.scalar_one()
                wed_hours.is_closed = True
                await session.commit()

            r = await _week(shop_a, mon_str, headers=owner_a)
            body = r.json()
            wed_day = body["days"][2]
            assert wed_day["date"] == wed.isoformat()
            assert wed_day["day_status"] == "closed_regular", f"定休日判定ミス: {wed_day}"
            assert wed_day["reservation_count"] == 0 and wed_day["reservations"] == []
            print("G. 定休日（closed_regular）: OK")

            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(ShopHours).filter(ShopHours.shop_id == shop_a, ShopHours.day_of_week == wed.weekday())
                )
                wed_hours = result.scalar_one()
                wed_hours.is_closed = False
                await session.commit()

            # ===== H. 臨時休業（closed_temporary） =====
            thu = mon + timedelta(days=3)
            thu_str = thu.isoformat()
            r = await client.post(f"/api/v1/shops/{shop_a}/closures", json={
                "start_date": thu_str, "end_date": thu_str, "reason": "設備点検のため",
            }, headers=owner_a)
            assert r.status_code == 200, f"create closure failed: {r.status_code} {r.text}"

            r = await _week(shop_a, mon_str, headers=owner_a)
            body = r.json()
            thu_day = body["days"][3]
            assert thu_day["date"] == thu_str
            assert thu_day["day_status"] == "closed_temporary", f"臨時休業判定ミス: {thu_day}"
            assert thu_day["closure_reason"] == "設備点検のため", f"closure_reason mismatch: {thu_day}"
            print("H. 臨時休業（closed_temporary、closure_reason含む）: OK")

            async with AsyncSessionLocal() as session:
                result = await session.execute(select(ShopClosure).filter(ShopClosure.shop_id == shop_a))
                for c in result.scalars().all():
                    await session.delete(c)
                await session.commit()

            # ===== I. 営業時間未設定（hours_not_configured） =====
            r = await client.post("/api/v1/shops/register", json={
                "name": "週表示テスト・営業時間未設定店", "category": "その他", "address": "東京都渋谷区3-3-3",
            }, headers=owner_a)
            assert r.status_code in (200, 201)
            shop_noconfig = r.json()["shop_id"]
            r = await _week(shop_noconfig, mon_str, headers=owner_a)
            assert r.status_code == 200
            body = r.json()
            assert len(body["days"]) == 7
            for dday in body["days"]:
                assert dday["day_status"] == "hours_not_configured", f"未設定判定ミス: {dday}"
                assert dday["reservation_count"] == 0 and dday["reservations"] == []
            print("I. 営業時間未設定（hours_not_configured、7日分すべて）: OK")

            # ===== J. 日跨ぎ営業（overnight） =====
            hours_payload_overnight = {"hours": [
                {"day_of_week": d, "opening_time": "18:00:00", "closing_time": "03:00:00", "is_closed": False, "closes_next_day": True}
                for d in range(7)
            ]}
            r = await client.put(f"/api/v1/shops/{shop_a}/hours", json=hours_payload_overnight, headers=owner_a)
            assert r.status_code == 200, f"set overnight hours failed: {r.status_code} {r.text}"

            tue = mon + timedelta(days=1)
            overnight_id = await _insert_reservation(shop_a, datetime(tue.year, tue.month, tue.day, 1, 0))

            r = await _week(shop_a, mon_str, headers=owner_a)
            body = r.json()
            mon_day, tue_day = body["days"][0], body["days"][1]
            mon_ids = {rb["reservation_date"] for rb in mon_day["reservations"]}
            tue_ids = {rb["reservation_date"] for rb in tue_day["reservations"]}
            overnight_start_iso = datetime(tue.year, tue.month, tue.day, 1, 0).isoformat()
            assert overnight_start_iso in mon_ids, f"火曜01:00の予約は月曜のセッションに属するはずが: {mon_day}"
            assert overnight_start_iso not in tue_ids, f"火曜01:00の予約が火曜側にも重複してはいけない: {tue_day}"
            assert mon_day["closes_next_day"] is True
            print("J. 日跨ぎ営業（overnight）: 翌日01:00の予約が前日セッションに正しく帰属: OK")

            await _clear(AsyncSessionLocal, Reservation, shop_a)
            # 営業時間を通常パターンに戻す
            r = await client.put(f"/api/v1/shops/{shop_a}/hours", json=hours_payload, headers=owner_a)
            assert r.status_code == 200

            # ===== K. PII: レスポンス全体に個人情報が一切含まれない =====
            pii_id = await _insert_reservation(
                shop_a, datetime(mon.year, mon.month, mon.day, 19, 0),
                guest_name="機密太郎", guest_phone="09011112222", guest_email="himitsu@example.com",
                special_requests="アレルギー対応が必要です",
            )
            r = await _week(shop_a, mon_str, headers=owner_a)
            assert r.status_code == 200
            raw_text = r.text
            body = r.json()

            forbidden_substrings = [
                "機密太郎", "09011112222", "himitsu@example.com", "アレルギー対応が必要です",
                "1112222",  # フル電話番号の一部・マスク済みでも末尾4桁以外は含まれないはず
            ]
            for s in forbidden_substrings:
                assert s not in raw_text, f"PIIがWeek APIレスポンスに含まれてはならない: {s!r} in response"

            forbidden_keys = [
                "guest_name", "guest_phone", "guest_phone_masked", "guest_email",
                "special_requests", "service_name", "staff_name", "table_name",
                "staff_id", "table_id", "staff_roster", "table_roster", "status",
            ]
            for key in forbidden_keys:
                assert f'"{key}"' not in raw_text, f"PII/Resource Laneに関わるキーが含まれてはならない: {key!r}"

            mon_day = body["days"][0]
            assert set(mon_day["reservations"][0].keys()) == {"reservation_date", "effective_duration_minutes"}, (
                f"予約1件のフィールドはこの2つのみのはずが: {mon_day['reservations'][0].keys()}"
            )
            print("K. レスポンス全体にPII・Resource Lane関連フィールドが一切含まれない: OK")

            await _clear(AsyncSessionLocal, Reservation, shop_a)

            print("\nALL WEEK VIEW CHECKS PASSED")


async def _clear(AsyncSessionLocal, Reservation, shop_id):
    from sqlalchemy import select
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Reservation).filter(Reservation.shop_id == shop_id))
        for res in result.scalars().all():
            await session.delete(res)
        await session.commit()


if __name__ == "__main__":
    asyncio.run(main())
