"""
Reservation Intelligence Phase C（Reservation Boundary & Mutation Safety）
スモークテスト

背景（本フェーズで修正した内容）:

1. 営業時間境界の不整合（Phase B完了報告で発見）:
   以前、create_reservation()だけが
   `hours.last_order_time or hours.closing_time`（所要時間を一切考慮しない）
   という緩い判定式を使っており、check_single_slot_availability()/
   get_availability()（`hours.last_order_time or (closing_time - duration)`）
   とは異なっていた。そのため「checkではNGのはずの予約がcreateでは成立して
   しまう」不整合があった。

   さらに、last_order_timeが設定されている店舗では、従来どちらの経路も
   「開始時刻がlast_order_time以前か」しか見ておらず、「終了時刻
   (start+duration)がclosing_timeを超えないか」を一切確認していなかった。

   本フェーズでは _validate_reservation_time_window() という共通helperへ
   一本化し、check_single_slot_availability() / get_availability() /
   create_reservation() / update_reservation() の4箇所すべてで同じ判定を
   使うようにした。

2. Owner予約編集（PUT /api/v1/reservations/{id}）でreservation_dateを
   変更する際、営業時間・臨時休業・既存予約との重複・スタッフシフトの
   再検証が一切行われていなかった（Phase B完了報告で発見した既知の不整合）。
   本フェーズで、日時が変更される場合のみ、create_reservation()と同じ
   一連のルールを再検証してから保存するよう修正した。変更対象の予約自身は
   重複判定から除外する（self-exclusion）。

検証項目（ユーザー指定の25〜33項目に対応）:
A. 営業時間境界（開始のみ営業時間内でも終了が閉店後なら不可）
B. 境界が接するだけは許容、1分でも超えたら不可
C. last_order_time設定時も終了時刻がclosing_timeを超えたら不可（今回の
   最重要修正の直接検証）
D. 既存予約との重複（Phase B回帰）
E. Owner Update時の重複検知
F. Update時のself-exclusion（自分自身との重複と誤判定しない）
G. Update時も営業時間ルールが適用される
H. レガシー予約（duration_minutes=NULL）のUpdate時フォールバック
I. ShopClosure（臨時休業）がcreate/updateの両方でブロックする
J. テナント分離（他テナントの予約をUpdateできない）
K. Update時のスタッフシフト再検証

実行: python3 tests/smoke_test_reservation_boundary_and_update.py
"""

import asyncio
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, date, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_boundary_update.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-boundary-update"
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
                "email": "owner-boundary-a@example.com", "password": "password123",
                "display_name": "Boundaryテストオーナー",
            })
            assert r.status_code in (200, 201), f"register A failed: {r.status_code} {r.text}"
            owner_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "Boundaryテスト食堂", "category": "食堂", "address": "東京都渋谷区6-6-6",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
            shop_id = r.json()["shop_id"]

            # 営業時間: 10:00〜20:00、last_order_timeは未設定（テストA/Bで使用）
            hours_payload = {"hours": [
                {"day_of_week": d, "opening_time": "10:00:00", "closing_time": "20:00:00", "is_closed": False}
                for d in range(7)
            ]}
            r = await client.put(f"/api/v1/shops/{shop_id}/hours", json=hours_payload, headers=owner_a)
            assert r.status_code == 200, f"set hours failed: {r.status_code} {r.text}"

            from app.database import AsyncSessionLocal
            from app.models.shop import Shop
            from app.models.reservation import Reservation
            from sqlalchemy import select

            async with AsyncSessionLocal() as session:
                shop_obj = await session.get(Shop, shop_id)
                shop_obj.reservations_enabled = True
                await session.commit()

            r = await client.post(f"/api/v1/shops/{shop_id}/tables", json={
                "name": "テーブルA", "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 200, f"create table failed: {r.status_code} {r.text}"
            table_id = r.json()["id"]

            target_date = _next_weekday(2, weeks_ahead=2)

            async def _set_duration(minutes: int):
                async with AsyncSessionLocal() as session:
                    shop_obj = await session.get(Shop, shop_id)
                    shop_obj.reservation_duration_minutes = minutes
                    await session.commit()

            async def _clear_reservations():
                async with AsyncSessionLocal() as session:
                    result = await session.execute(select(Reservation).filter(Reservation.shop_id == shop_id))
                    for res in result.scalars().all():
                        await session.delete(res)
                    await session.commit()

            async def _check(time_str: str):
                r = await client.post(
                    f"/api/v1/shops/{shop_id}/realtime-voice/tools/check-availability",
                    json={"date": target_date.isoformat(), "time": time_str, "party_size": 2},
                )
                assert r.status_code == 200, f"check-availability failed: {r.status_code} {r.text}"
                return r.json()

            async def _create(time_str: str, phone: str):
                return await client.post(
                    f"/api/v1/shops/{shop_id}/realtime-voice/tools/create-reservation",
                    json={
                        "date": target_date.isoformat(), "time": time_str, "party_size": 2,
                        "guest_name": "境界テスト", "guest_phone": phone,
                        "call_id": "smoke-boundary-" + str(uuid.uuid4()),
                    },
                )

            # ===== A. 営業時間境界（10:00-20:00, duration=90分） =====
            await _clear_reservations()
            await _set_duration(90)

            body = await _check("18:30")
            assert body["available"] is True, f"18:30開始(90分,20:00終了)はOKのはずが: {body}"
            r = await _create("18:30", "09040000001")
            assert r.status_code == 200 and r.json()["success"] is True, f"18:30開始のcreateが失敗: {r.status_code} {r.text}"

            await _clear_reservations()
            body = await _check("19:00")
            assert body["available"] is False and body["reason_code"] == "outside_business_hours", (
                f"19:00開始(90分,20:30終了)はNGのはずが: {body}"
            )
            r = await _create("19:00", "09040000002")
            assert r.status_code == 200 and r.json().get("success") is False and r.json().get("reason_code") == "outside_business_hours", (
                f"【重要】19:00開始のcreateが誤って成立、またはreason_codeが不一致: {r.status_code} {r.text}"
            )

            body = await _check("20:00")
            assert body["available"] is False and body["reason_code"] == "outside_business_hours", f"20:00開始はNGのはずが: {body}"

            body = await _check("09:30")
            assert body["available"] is False and body["reason_code"] == "outside_business_hours", f"開店前(09:30)はNGのはずが: {body}"
            print("A. 営業時間境界（開始のみ営業時間内でも終了が閉店後なら不可・開店前も不可）: OK")

            # ===== B. 境界が接するだけは許容、1分でも超えたら不可 =====
            await _clear_reservations()
            await _set_duration(60)
            body = await _check("10:00")
            assert body["available"] is True, f"10:00開始(60分)はOKのはずが: {body}"
            body = await _check("19:00")
            assert body["available"] is True, f"19:00開始(60分,20:00終了・境界接触)はOKのはずが: {body}"

            await _set_duration(61)
            body = await _check("19:00")
            assert body["available"] is False and body["reason_code"] == "outside_business_hours", (
                f"19:00開始(61分,20:01終了・1分超過)はNGのはずが: {body}"
            )
            print("B. 境界touching（接するだけはOK、1分でも超えたらNG）: OK")

            # ===== C. last_order_time設定時も終了時刻がclosing_timeを超えたら不可 =====
            # （今回の最重要修正: 以前はlast_order_timeが設定されていると、
            # 開始時刻さえlast_order_time以内なら終了時刻のチェックを一切
            # 行っていなかった）
            hours_payload_lo = {"hours": [
                {"day_of_week": d, "opening_time": "10:00:00", "closing_time": "20:00:00",
                 "last_order_time": "19:00:00", "is_closed": False}
                for d in range(7)
            ]}
            r = await client.put(f"/api/v1/shops/{shop_id}/hours", json=hours_payload_lo, headers=owner_a)
            assert r.status_code == 200, f"set hours(last_order_time) failed: {r.status_code} {r.text}"

            await _clear_reservations()
            await _set_duration(90)
            # last_order_time=19:00なので開始時刻自体は許容範囲内だが、
            # 90分後の20:30は closing_time(20:00) を超える → 不可のはず
            body = await _check("19:00")
            assert body["available"] is False and body["reason_code"] == "outside_business_hours", (
                f"【最重要】last_order_time(19:00)以内でも終了時刻(20:30)が"
                f"closing_time(20:00)を超える予約が誤って空きありと判定された: {body}"
            )
            r = await _create("19:00", "09040000003")
            assert r.status_code == 200 and r.json().get("success") is False and r.json().get("reason_code") == "outside_business_hours", (
                f"【最重要】last_order_time以内でも終了時刻超過の予約がcreateで誤って成立: {r.status_code} {r.text}"
            )

            # last_order_time(19:00)通りにdurationを合わせれば成立するはず(18:30開始,90分→20:00終了)
            body = await _check("18:30")
            assert body["available"] is True, f"18:30開始(90分,20:00終了ちょうど)はOKのはずが: {body}"
            print("C. last_order_time設定時でも終了時刻がclosing_timeを超えたら不可（最重要修正の直接検証）: OK")

            # last_order_time設定を元に戻す（以降のテストに影響させない）
            r = await client.put(f"/api/v1/shops/{shop_id}/hours", json=hours_payload, headers=owner_a)
            assert r.status_code == 200

            # ===== D. 既存予約との重複（Phase B回帰） =====
            await _clear_reservations()
            await _set_duration(120)
            r1 = await _create("14:00", "09040000004")
            assert r1.status_code == 200 and r1.json()["success"] is True, f"14:00-16:00予約作成失敗: {r1.status_code} {r1.text}"

            await _set_duration(30)
            body = await _check("15:00")
            assert body["available"] is False and body["reason_code"] == "fully_booked", f"15:00-15:30は重複のはずが: {body}"
            body = await _check("16:00")
            assert body["available"] is True, f"16:00-16:30(既存予約終了直後)はOKのはずが: {body}"
            print("D. 既存予約との重複（Phase B回帰）: OK")

            # ===== E/F. Owner Update時の重複検知・自己除外・営業時間 =====
            await _clear_reservations()
            await _set_duration(60)
            dt_a = datetime.combine(target_date, datetime.min.time().replace(hour=14, minute=0))
            dt_b = datetime.combine(target_date, datetime.min.time().replace(hour=16, minute=0))
            ra = await _create("14:00", "09040000005")
            assert ra.status_code == 200 and ra.json()["success"] is True, f"予約A作成失敗: {ra.status_code} {ra.text}"
            reservation_a_id = ra.json()["reservation_id"]
            rb = await _create("16:00", "09040000006")
            assert rb.status_code == 200 and rb.json()["success"] is True, f"予約B作成失敗: {rb.status_code} {rb.text}"

            # E. Aを、Bと重なる16:30へ変更 → NG
            dt_conflict = datetime.combine(target_date, datetime.min.time().replace(hour=16, minute=30))
            r = await client.put(f"/api/v1/reservations/{reservation_a_id}", json={
                "reservation_date": dt_conflict.isoformat(),
            }, headers=owner_a)
            assert r.status_code == 400, f"Bと重なる時間へのUpdateが成立してしまった（ダブルブッキング）: {r.status_code} {r.text}"
            print("E. Owner Update時の重複検知（ダブルブッキング防止）: OK")

            # F. Self-exclusion: Aを同じ14:00〜15:00のまま更新 → OK（自分自身との重複扱いにしない）
            r = await client.put(f"/api/v1/reservations/{reservation_a_id}", json={
                "reservation_date": dt_a.isoformat(),
            }, headers=owner_a)
            assert r.status_code == 200, f"Self-exclusion失敗: 自分自身と同じ時間へのUpdateが拒否された: {r.status_code} {r.text}"
            print("F. Update時のself-exclusion（自分自身との重複と誤判定しない）: OK")

            # Aを、Bと重ならない15:00〜16:00へ変更 → OK（境界がBの開始16:00に接するだけ）
            dt_touch = datetime.combine(target_date, datetime.min.time().replace(hour=15, minute=0))
            r = await client.put(f"/api/v1/reservations/{reservation_a_id}", json={
                "reservation_date": dt_touch.isoformat(),
            }, headers=owner_a)
            assert r.status_code == 200, f"境界が接するだけの時間へのUpdateが失敗: {r.status_code} {r.text}"

            # G. Update時も営業時間ルールが適用される: Aを19:30(60分→20:30終了)へ変更 → NG
            dt_after_hours = datetime.combine(target_date, datetime.min.time().replace(hour=19, minute=30))
            r = await client.put(f"/api/v1/reservations/{reservation_a_id}", json={
                "reservation_date": dt_after_hours.isoformat(),
            }, headers=owner_a)
            assert r.status_code == 400, f"営業時間外へのUpdateが成立してしまった: {r.status_code} {r.text}"
            print("G. Update時も営業時間ルールが適用される（AI createと同じルール）: OK")

            # ===== H. レガシー予約（duration_minutes=NULL）のUpdate時フォールバック =====
            await _clear_reservations()
            await _set_duration(90)
            legacy_id = str(uuid.uuid4())
            async with AsyncSessionLocal() as session:
                session.add(Reservation(
                    id=legacy_id, shop_id=shop_id, customer_id=None, user_id=None,
                    staff_id=None, table_id=table_id, service_id=None,
                    guest_name="レガシー予約", guest_phone="09040000007",
                    reservation_date=datetime.combine(target_date, datetime.min.time().replace(hour=12, minute=0)),
                    duration_minutes=None,  # Phase B以前のレガシー予約を再現
                    number_of_people=2, status="confirmed",
                    created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                ))
                await session.commit()
            # shop.reservation_duration_minutes=90分にフォールバックするはずなので、
            # 12:00〜13:30として扱われる。この予約自身を13:00へ動かそうとすると、
            # 移動後の13:00〜14:30は元の12:00〜13:30と別物だが、他に予約はないのでOKのはず。
            dt_legacy_new = datetime.combine(target_date, datetime.min.time().replace(hour=13, minute=0))
            r = await client.put(f"/api/v1/reservations/{legacy_id}", json={
                "reservation_date": dt_legacy_new.isoformat(),
            }, headers=owner_a)
            assert r.status_code == 200, (
                f"レガシー予約(duration_minutes=NULL)のUpdateがフォールバック解決に失敗: {r.status_code} {r.text}"
            )
            # 移動後、13:30〜14:00にもう1件作ろうとすると、90分フォールバックで
            # 13:00〜14:30と重複するはずなのでNGになることを確認
            r2 = await _create("13:30", "09040000008")
            assert r2.status_code == 200 and r2.json().get("success") is False and r2.json().get("reason_code") == "fully_booked", (
                f"レガシー予約のフォールバックduration(90分)が正しく重複判定に使われていない: {r2.status_code} {r2.text}"
            )
            print("H. レガシー予約(duration_minutes=NULL)のUpdate時フォールバック: OK")

            # ===== I. ShopClosure（臨時休業）がcreate/updateの両方でブロックする =====
            await _clear_reservations()
            closure_date = _next_weekday(3, weeks_ahead=3)
            r = await client.post(f"/api/v1/shops/{shop_id}/closures", json={
                "start_date": closure_date.isoformat(), "end_date": closure_date.isoformat(),
                "reason": "臨時休業テスト",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create closure failed: {r.status_code} {r.text}"

            r_create_closure = await client.post(
                f"/api/v1/shops/{shop_id}/realtime-voice/tools/create-reservation",
                json={
                    "date": closure_date.isoformat(), "time": "12:00", "party_size": 2,
                    "guest_name": "臨時休業テスト", "guest_phone": "09040000009",
                    "call_id": "smoke-closure-" + str(uuid.uuid4()),
                },
            )
            assert (
                r_create_closure.status_code == 200
                and r_create_closure.json().get("success") is False
                and r_create_closure.json().get("reason_code") == "temporary_closure"
            ), f"臨時休業日へのcreateがブロックされていない: {r_create_closure.status_code} {r_create_closure.text}"

            normal_dt = datetime.combine(target_date, datetime.min.time().replace(hour=12, minute=0))
            rn = await _create("12:00", "09040000010")
            assert rn.status_code == 200 and rn.json()["success"] is True, f"通常日の予約作成に失敗: {rn.status_code} {rn.text}"
            normal_reservation_id = rn.json()["reservation_id"]
            closure_dt = datetime.combine(closure_date, datetime.min.time().replace(hour=12, minute=0))
            r_update_closure = await client.put(f"/api/v1/reservations/{normal_reservation_id}", json={
                "reservation_date": closure_dt.isoformat(),
            }, headers=owner_a)
            assert r_update_closure.status_code == 400, (
                f"臨時休業日へのUpdateがブロックされていない: {r_update_closure.status_code} {r_update_closure.text}"
            )
            print("I. ShopClosure（臨時休業）がcreate/updateの両方でブロックする: OK")

            # ===== J. テナント分離（他テナントの予約をUpdateできない） =====
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-boundary-b@example.com", "password": "password123",
                "display_name": "Boundaryテストオーナー B",
            })
            assert r.status_code in (200, 201), f"register B failed: {r.status_code} {r.text}"
            owner_b = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.put(f"/api/v1/reservations/{normal_reservation_id}", json={
                "special_requests": "乗っ取りテスト",
            }, headers=owner_b)
            assert r.status_code == 403, f"テナント分離の破れ: 他テナントの予約をUpdateできてしまった: {r.status_code} {r.text}"
            print("J. テナント分離（他テナントの予約をUpdateできない）: OK")

            # ===== K. Update時のスタッフシフト再検証 =====
            await _clear_reservations()
            r = await client.post("/api/v1/services", json={
                "shop_id": shop_id, "name": "施術60分", "base_price": 5000, "duration_minutes": 60,
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create service failed: {r.status_code} {r.text}"
            service_id = r.json()["id"]
            r = await client.post("/api/v1/staff", json={"shop_id": shop_id, "name": "境界テスト担当"}, headers=owner_a)
            assert r.status_code == 200, f"create staff failed: {r.status_code} {r.text}"
            staff_id = r.json()["id"]
            r = await client.post(f"/api/v1/staff/{staff_id}/services/{service_id}", headers=owner_a)
            assert r.status_code == 200, f"assign staff-service failed: {r.status_code} {r.text}"

            r = await client.patch(f"/api/v1/shops/{shop_id}", json={"staff_schedule_enabled": True}, headers=owner_a)
            assert r.status_code == 200, f"enable staff_schedule failed: {r.status_code} {r.text}"

            weekday = target_date.weekday()
            r = await client.post(f"/api/v1/staff/{staff_id}/weekly-shifts", json={
                "day_of_week": weekday, "start_time": "10:00", "end_time": "18:00",
            }, headers=owner_a)
            assert r.status_code == 200, f"create weekly shift failed: {r.status_code} {r.text}"

            r = await client.post(
                f"/api/v1/shops/{shop_id}/realtime-voice/tools/create-reservation",
                json={
                    "date": target_date.isoformat(), "time": "11:00", "party_size": 1,
                    "service_id": service_id, "staff_id": staff_id,
                    "guest_name": "シフトテスト", "guest_phone": "09040000011",
                    "call_id": "smoke-shift-" + str(uuid.uuid4()),
                },
            )
            assert r.status_code == 200 and r.json()["success"] is True, f"シフト内予約作成失敗: {r.status_code} {r.text}"
            shift_reservation_id = r.json()["reservation_id"]

            # 17:30へ変更（60分だと18:30終了になりシフト18:00を超える）→ NG
            dt_out_of_shift = datetime.combine(target_date, datetime.min.time().replace(hour=17, minute=30))
            r = await client.put(f"/api/v1/reservations/{shift_reservation_id}", json={
                "reservation_date": dt_out_of_shift.isoformat(),
            }, headers=owner_a)
            assert r.status_code == 400, f"スタッフのシフト外へのUpdateが成立してしまった: {r.status_code} {r.text}"

            # 16:00へ変更（60分で17:00終了、シフト内）→ OK
            dt_in_shift = datetime.combine(target_date, datetime.min.time().replace(hour=16, minute=0))
            r = await client.put(f"/api/v1/reservations/{shift_reservation_id}", json={
                "reservation_date": dt_in_shift.isoformat(),
            }, headers=owner_a)
            assert r.status_code == 200, f"シフト内へのUpdateが失敗: {r.status_code} {r.text}"
            print("K. Update時のスタッフシフト再検証（create経路と同じ_is_staff_scheduled()を再利用）: OK")

    print("\n全テストOK: Reservation Intelligence Phase C（Reservation Boundary & Mutation Safety）")


if __name__ == "__main__":
    asyncio.run(main())
