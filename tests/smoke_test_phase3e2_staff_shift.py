"""
Phase3E-2 (Staff Weekly Shift / Shift Override) スモークテスト

検証項目:
1. staff_schedule_enabled=false（デフォルト）の店舗では、シフト未設定でも
   従来通りの予約可否判定になる（=Phase3E-1までの挙動を完全に維持）
2. 週次シフトの登録・一覧・削除
3. staff_schedule_enabled=trueにした後、シフトを一切登録していないスタッフは
   「シフト未設定」として常に空き扱い（安全側フォールバック）
4. 週次シフトを登録すると、その曜日・時間外は予約不可、時間内は予約可能になる
5. override_type="day_off" でその日を終日不可にできる
6. override_type="hours" でその日だけ勤務時間を変更できる
7. override_type="unavailable" で勤務時間内の一部だけを不可にでき、
   それ以外の時間帯は通常どおり予約可能なまま
8. IDOR: 他テナントがシフトを操作できない
9. 入力バリデーション（終了時刻<=開始時刻、day_offにtime指定、不正なoverride_type）
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_phase3e2.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-3e2"

import httpx  # noqa: E402


def _next_weekday(target_weekday: int, weeks_ahead: int = 1) -> "__import__('datetime').date":
    from datetime import date
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
            # セットアップ: オーナーA、店舗、サービス、スタッフ
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-a-3e2@example.com", "password": "password123", "display_name": "オーナーA",
            })
            assert r.status_code in (200, 201), f"register A failed: {r.status_code} {r.text}"
            owner_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "3E2テスト店舗A", "category": "ヘアサロン", "address": "東京都渋谷区1-1-1",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
            shop_a = r.json()["shop_id"]

            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-b-3e2@example.com", "password": "password123", "display_name": "オーナーB",
            })
            assert r.status_code in (200, 201), f"register B failed: {r.status_code} {r.text}"
            owner_b = {"Authorization": f"Bearer {r.json()['access_token']}"}

            # 営業時間を月〜日 09:00-21:00 で設定（10分刻みラストオーダー20:00）
            hours_payload = {
                "hours": [
                    {"day_of_week": d, "is_closed": False, "opening_time": "09:00", "closing_time": "21:00", "last_order_time": "20:00"}
                    for d in range(7)
                ]
            }
            r = await client.put(f"/api/v1/shops/{shop_a}/hours", json=hours_payload, headers=owner_a)
            assert r.status_code == 200, f"set hours failed: {r.status_code} {r.text}"

            # サービス作成（60分）
            r = await client.post("/api/v1/services", json={
                "shop_id": shop_a, "name": "カット", "base_price": 4000, "duration_minutes": 60,
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create service failed: {r.status_code} {r.text}"
            service_id = r.json()["id"]

            # スタッフ作成 + サービス割り当て
            r = await client.post("/api/v1/staff", json={"shop_id": shop_a, "name": "テスト花子"}, headers=owner_a)
            assert r.status_code == 200, f"create staff failed: {r.status_code} {r.text}"
            staff_id = r.json()["id"]

            r = await client.post(f"/api/v1/staff/{staff_id}/services/{service_id}", headers=owner_a)
            assert r.status_code == 200, f"assign staff service failed: {r.status_code} {r.text}"

            # 予約受付を有効化（activate不要な経路を使う: reservations_enabledは
            # 課金アクティベーション必須のためAPI経由ではセットできない。
            # DBを直接更新してテストの前提を整える）
            from app.database import AsyncSessionLocal
            from app.models.shop import Shop
            async with AsyncSessionLocal() as session:
                shop_obj = await session.get(Shop, shop_a)
                shop_obj.reservations_enabled = True
                await session.commit()

            target_date = _next_weekday(0, weeks_ahead=2)  # 十分先の月曜日
            target_date_str = target_date.isoformat()

            # 1. staff_schedule_enabled=false（デフォルト）: シフト未設定でも従来通り予約可能
            r = await client.get(
                f"/api/v1/reservations/shop/{shop_a}/availability",
                params={"date": target_date_str, "service_id": service_id},
            )
            assert r.status_code == 200, f"availability(before enable) failed: {r.status_code} {r.text}"
            slots_before = {s["time"]: s["available"] for s in r.json()["slots"]}
            assert slots_before.get("10:00") is True, f"expected available before enabling schedule: {slots_before}"

            # 2. 週次シフト登録・一覧・削除
            r = await client.post(f"/api/v1/staff/{staff_id}/weekly-shifts", json={
                "day_of_week": 0, "start_time": "09:00", "end_time": "12:00",
            }, headers=owner_a)
            assert r.status_code == 200, f"create weekly shift 1 failed: {r.status_code} {r.text}"
            shift1_id = r.json()["id"]
            assert r.json()["start_time"] == "09:00" and r.json()["end_time"] == "12:00", r.json()

            r = await client.post(f"/api/v1/staff/{staff_id}/weekly-shifts", json={
                "day_of_week": 0, "start_time": "13:00", "end_time": "18:00",
            }, headers=owner_a)
            assert r.status_code == 200, f"create weekly shift 2 failed: {r.status_code} {r.text}"

            r = await client.get(f"/api/v1/staff/{staff_id}/weekly-shifts", headers=owner_a)
            assert r.status_code == 200 and len(r.json()) == 2, f"list weekly shifts failed: {r.status_code} {r.text}"

            # バリデーション: 終了<=開始は拒否
            r = await client.post(f"/api/v1/staff/{staff_id}/weekly-shifts", json={
                "day_of_week": 0, "start_time": "18:00", "end_time": "10:00",
            }, headers=owner_a)
            assert r.status_code == 422, f"expected validation error, got {r.status_code} {r.text}"

            # 3. staff_schedule_enabledをONにする前: まだ全スタッフ「シフト未設定」なので
            #    影響が出ないことを別スタッフで確認（安全側フォールバック）
            r = await client.post("/api/v1/staff", json={"shop_id": shop_a, "name": "シフト未設定次郎"}, headers=owner_a)
            assert r.status_code == 200
            staff2_id = r.json()["id"]
            r = await client.post(f"/api/v1/staff/{staff2_id}/services/{service_id}", headers=owner_a)
            assert r.status_code == 200

            # staff_schedule_enabledをON
            r = await client.patch(f"/api/v1/shops/{shop_a}", json={"staff_schedule_enabled": True}, headers=owner_a)
            assert r.status_code == 200, f"enable staff_schedule failed: {r.status_code} {r.text}"
            assert r.json()["staff_schedule_enabled"] is True, r.json()

            # シフト未設定スタッフ(staff2)だけを対象にした空き状況確認。
            # Phase3E-3で仕様変更（ユーザー承認済み）: このフォールバックは
            # 「常に予約可能」から「Fail Closed（予約不可）」へ明示的に反転された。
            # このテストファイルはPhase3E-2時点の挙動記録として残しつつ、
            # 現行の実際の挙動（Phase3E-3以降）に合わせてアサーションを更新する。
            # 詳細・意図的な反転の理由はtests/smoke_test_phase3e3_availability_safety.py
            # と app/routers/reservations.py の _is_staff_scheduled() docstring参照。
            r = await client.get(
                f"/api/v1/reservations/shop/{shop_a}/availability",
                params={"date": target_date_str, "service_id": service_id, "staff_id": staff2_id},
            )
            assert r.status_code == 200, f"availability(staff2, unset schedule) failed: {r.status_code} {r.text}"
            slots_staff2 = {s["time"]: s["available"] for s in r.json()["slots"]}
            assert slots_staff2.get("10:00") is False, (
                f"Phase3E-3でFail Closedに変更されたため、シフト未設定スタッフは"
                f"予約不可であるべき: {slots_staff2}"
            )

            # 4. staff（シフト設定済み）: 月曜09:00-12:00, 13:00-18:00のみ勤務
            r = await client.get(
                f"/api/v1/reservations/shop/{shop_a}/availability",
                params={"date": target_date_str, "service_id": service_id, "staff_id": staff_id},
            )
            assert r.status_code == 200, f"availability(staff, weekly shift) failed: {r.status_code} {r.text}"
            slots = {s["time"]: s["available"] for s in r.json()["slots"]}
            # 10:00開始・60分 → 09:00-12:00の枠内なので予約可能
            assert slots.get("10:00") is True, f"expected available within weekly shift window: {slots}"
            # 12:30開始・60分 → 12:00-13:00の休憩時間帯にかかるため不可
            assert slots.get("12:30") is False, f"expected unavailable during break: {slots}"
            # 20:00開始・60分 → 18:00で退勤済みのため不可（営業時間としては20:00が最終受付）
            assert slots.get("20:00") is False, f"expected unavailable after shift end: {slots}"

            # 5. override_type="day_off"
            r = await client.post(f"/api/v1/staff/{staff_id}/shift-overrides", json={
                "target_date": target_date_str, "override_type": "day_off",
            }, headers=owner_a)
            assert r.status_code == 200, f"create day_off override failed: {r.status_code} {r.text}"
            day_off_id = r.json()["id"]

            r = await client.get(
                f"/api/v1/reservations/shop/{shop_a}/availability",
                params={"date": target_date_str, "service_id": service_id, "staff_id": staff_id},
            )
            slots_dayoff = {s["time"]: s["available"] for s in r.json()["slots"]}
            assert slots_dayoff.get("10:00") is False, f"expected unavailable on day_off: {slots_dayoff}"

            # バリデーション: day_offにtime指定は拒否
            r = await client.post(f"/api/v1/staff/{staff_id}/shift-overrides", json={
                "target_date": target_date_str, "override_type": "day_off", "start_time": "09:00", "end_time": "10:00",
            }, headers=owner_a)
            assert r.status_code == 422, f"expected validation error for day_off with times, got {r.status_code}"

            # day_offを削除して次のケースへ
            r = await client.delete(f"/api/v1/staff/{staff_id}/shift-overrides/{day_off_id}", headers=owner_a)
            assert r.status_code == 200, f"delete day_off override failed: {r.status_code} {r.text}"

            # 6. override_type="hours"（その日だけ13:00-20:00に勤務時間変更）
            r = await client.post(f"/api/v1/staff/{staff_id}/shift-overrides", json={
                "target_date": target_date_str, "override_type": "hours", "start_time": "13:00", "end_time": "20:00",
            }, headers=owner_a)
            assert r.status_code == 200, f"create hours override failed: {r.status_code} {r.text}"
            hours_override_id = r.json()["id"]

            r = await client.get(
                f"/api/v1/reservations/shop/{shop_a}/availability",
                params={"date": target_date_str, "service_id": service_id, "staff_id": staff_id},
            )
            slots_hours = {s["time"]: s["available"] for s in r.json()["slots"]}
            # 週次シフトの09:00-12:00の枠はhoursオーバーライドにより上書きされ不可
            assert slots_hours.get("10:00") is False, f"expected weekly shift overridden by hours override: {slots_hours}"
            # 13:00-20:00の新しい勤務時間内は可能
            assert slots_hours.get("14:00") is True, f"expected available within hours override: {slots_hours}"

            r = await client.delete(f"/api/v1/staff/{staff_id}/shift-overrides/{hours_override_id}", headers=owner_a)
            assert r.status_code == 200

            # 7. override_type="unavailable"（15:00-16:00だけ部分的に不在、他は通常勤務）
            r = await client.post(f"/api/v1/staff/{staff_id}/shift-overrides", json={
                "target_date": target_date_str, "override_type": "unavailable",
                "start_time": "15:00", "end_time": "16:00", "note": "私用のため",
            }, headers=owner_a)
            assert r.status_code == 200, f"create unavailable override failed: {r.status_code} {r.text}"
            unavail_id = r.json()["id"]

            r = await client.get(
                f"/api/v1/reservations/shop/{shop_a}/availability",
                params={"date": target_date_str, "service_id": service_id, "staff_id": staff_id},
            )
            slots_unavail = {s["time"]: s["available"] for s in r.json()["slots"]}
            # 週次シフトの09:00-12:00枠は影響を受けず通常どおり
            assert slots_unavail.get("10:00") is True, f"expected unaffected morning slot: {slots_unavail}"
            # 13:00-18:00枠のうち、不在時間(15:00-16:00)と重なる開始時刻は不可
            assert slots_unavail.get("15:00") is False, f"expected unavailable during partial block: {slots_unavail}"
            assert slots_unavail.get("15:30") is False, f"expected unavailable during partial block: {slots_unavail}"
            # 不在時間の前後は通常どおり勤務扱い
            assert slots_unavail.get("13:00") is True, f"expected available before partial block: {slots_unavail}"
            assert slots_unavail.get("17:00") is True, f"expected available after partial block: {slots_unavail}"

            r = await client.get(f"/api/v1/staff/{staff_id}/shift-overrides", headers=owner_a)
            assert r.status_code == 200 and len(r.json()) == 1, f"list overrides failed: {r.status_code} {r.text}"

            r = await client.delete(f"/api/v1/staff/{staff_id}/shift-overrides/{unavail_id}", headers=owner_a)
            assert r.status_code == 200

            # バリデーション: 不正なoverride_type
            r = await client.post(f"/api/v1/staff/{staff_id}/shift-overrides", json={
                "target_date": target_date_str, "override_type": "invalid_type",
            }, headers=owner_a)
            assert r.status_code == 422, f"expected validation error for invalid override_type, got {r.status_code}"

            # 8. IDOR: オーナーBはshop_aのスタッフのシフトを操作できない
            r = await client.post(f"/api/v1/staff/{staff_id}/weekly-shifts", json={
                "day_of_week": 1, "start_time": "09:00", "end_time": "18:00",
            }, headers=owner_b)
            assert r.status_code == 403, f"expected 403 for cross-tenant weekly shift create, got {r.status_code}"

            r = await client.get(f"/api/v1/staff/{staff_id}/weekly-shifts", headers=owner_b)
            assert r.status_code == 403, f"expected 403 for cross-tenant weekly shift list, got {r.status_code}"

            r = await client.delete(f"/api/v1/staff/{staff_id}/weekly-shifts/{shift1_id}", headers=owner_b)
            assert r.status_code == 403, f"expected 403 for cross-tenant weekly shift delete, got {r.status_code}"

            r = await client.post(f"/api/v1/staff/{staff_id}/shift-overrides", json={
                "target_date": target_date_str, "override_type": "day_off",
            }, headers=owner_b)
            assert r.status_code == 403, f"expected 403 for cross-tenant override create, got {r.status_code}"

            # 後片付け
            r = await client.delete(f"/api/v1/staff/{staff_id}/weekly-shifts/{shift1_id}", headers=owner_a)
            assert r.status_code == 200

            print("ALL PHASE3E-2 STAFF SHIFT SMOKE TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
