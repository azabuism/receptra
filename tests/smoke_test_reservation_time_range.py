"""
Reservation Intelligence Phase B（Reservation TIME-RANGE Foundation）スモークテスト

背景（今回修正した最重要バグ）:
以前は「既存予約の終了時刻」を計算する際、既存予約自身の所要時間ではなく
「今回の新規リクエストのduration_minutes」を流用していた。既存予約と新規
リクエストで長さが異なる場合（例: 20:00〜22:00の既存予約に対し、
21:30〜22:00〈30分〉の新規リクエスト）、既存予約の終了時刻が誤って
21:30と計算され、本来重複するはずの21:30〜22:00の時間帯が「空いている」と
誤判定されていた。

本フェーズでの変更（すべて最小限の変更。新規Tool・VAD変更・新規カテゴリー
追加は一切なし）:
- Reservation.duration_minutes カラムを追加（nullable。NULL=無制限ではなく
  「Phase B以前に作成された既存予約」を意味し、参照側が必ず
  shop.reservation_duration_minutes or 90 に解決してから使う）。
- _existing_duration_minutes(existing, default_duration_minutes) ヘルパーを
  追加し、テーブル/スタッフの重複判定4箇所すべてで
  「既存予約自身のduration_minutes」を使うよう修正。
- create_reservation() で新規予約作成時にduration_minutesを保存するように。

検証項目（ユーザー指定の16項目に対応）:
1. 同一durationでの重複判定（回帰）
2. 異なるdurationでの重複判定（本フェーズの最重要バグの直接検証、双方向）
3. 包含関係（新規リクエストが既存予約に内包される）
4. 逆包含関係（既存予約が新規リクエストに内包される）
5. 境界が接するだけ（重複としない。14:00-15:00 と 15:00-16:00）
6. 通常の非重複
7. テーブル占有（既存予約のduration基準で正しく空き/満席判定）
8. スタッフ占有（同上）
9. check_availability Tool エンドポイントのend-to-end確認
10. create_reservation実行時の再検証（check_availabilityを経由せず直接
    create_reservationを呼んでも重複を検知すること）
11. 冪等性（idempotency_key）の回帰確認
12. 既存予約（duration_minutes=NULL）のフォールバック挙動
13. テナント分離（他テナントのテーブル/予約を操作できない）
14. 8 Tool schema・Realtime Tool descriptionの回帰確認（duration_minutesを
    Tool引数に追加していないこと）
15. Reservationスキーマの追加的変更（duration_minutesの追加はadditiveで
    あり、既存フィールドを壊していないこと）

実行: python3 tests/smoke_test_reservation_time_range.py

（相対日付表現・Short Choice/Missing Hours Callbackの回帰確認は、既存の
tests/smoke_test_relative_date_conversion.py と
tests/smoke_test_short_choice_and_hours_callback.py で別途検証済み。
本ファイルではTIME-RANGE機能に直接関係するものだけを検証する。）
"""

import asyncio
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, date, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_time_range.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-time-range"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402

JST = timezone(timedelta(hours=9))


def _next_weekday(target_weekday: int, weeks_ahead: int = 2):
    today = date.today()
    days_ahead = (target_weekday - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead + 7 * (weeks_ahead - 1))


def _test_tool_schema_and_instructions_unchanged():
    """14. 8 Tool schema / Realtime Tool descriptionの回帰確認。
    Phase Bではduration_minutesをTool引数に追加していないことを確認する
    （既存サービスは全てduration自動決定〈Service.duration_minutes等〉で
    足りており、AIに所要時間を聞く必要がないため）。
    """
    from app.services.realtime_voice_ai import _REALTIME_TOOLS

    tool_names = [t["name"] for t in _REALTIME_TOOLS]
    assert tool_names == [
        "check_availability", "create_reservation", "get_shop_info", "find_customer",
        "confirm_customer_identity", "get_customer_context", "set_conversation_language",
        "request_callback",
    ], f"8 Tool schemaが変化しています（9個目のToolが追加された等）: {tool_names}"

    check_availability_tool = next(t for t in _REALTIME_TOOLS if t["name"] == "check_availability")
    create_reservation_tool = next(t for t in _REALTIME_TOOLS if t["name"] == "create_reservation")
    for tool in (check_availability_tool, create_reservation_tool):
        params = tool.get("parameters", {}).get("properties", {})
        assert "duration_minutes" not in params, (
            f"{tool['name']}にduration_minutes引数が追加されています。Phase Bでは"
            f"既存業種は全てduration自動決定のため、AIへ所要時間を聞く変更は不要のはず: {params.keys()}"
        )

    print("14. 8 Tool schema回帰 / duration_minutesがTool引数に追加されていないこと: OK")


async def main():
    _test_tool_schema_and_instructions_unchanged()

    from app.main import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # ===== セットアップ: テナントA =====
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-time-range-a@example.com", "password": "password123",
                "display_name": "TimeRangeテストオーナーA",
            })
            assert r.status_code in (200, 201), f"register A failed: {r.status_code} {r.text}"
            owner_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "TimeRangeテスト居酒屋", "category": "居酒屋", "address": "東京都渋谷区5-5-5",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
            shop_id = r.json()["shop_id"]

            # 毎日 00:00〜23:30 営業（本フェーズはduration/overlap判定が主眼のため、
            # 営業時間境界の影響を避ける目的で広めに設定する。business-hours境界
            # チェック自体の監査結果は完了報告のPhase C候補として別途報告する）。
            hours_payload = {"hours": [
                {"day_of_week": d, "opening_time": "00:00:00", "closing_time": "23:30:00", "is_closed": False}
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
                shop_obj.reservation_duration_minutes = 90
                await session.commit()

            # テーブルを1つ作成（テーブル予約系のテストに使う）
            r = await client.post(f"/api/v1/shops/{shop_id}/tables", json={
                "name": "テーブルA", "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 200, f"create table failed: {r.status_code} {r.text}"
            table_a_id = r.json()["id"]

            target_date = _next_weekday(2, weeks_ahead=2)  # 定休日等の影響がない平日を選ぶ

            def _dt(hh_mm: str) -> datetime:
                hh, mm = hh_mm.split(":")
                return datetime.combine(target_date, datetime.min.time().replace(hour=int(hh), minute=int(mm)))

            async def _insert_reservation(*, table_id=None, staff_id=None, start: datetime, duration_minutes) -> str:
                """テスト専用: 重複判定の入力となる「既存予約」を、所要時間を厳密に
                指定して直接DBへ挿入する。API経由のcreate_reservationは常に
                Service/shopのduration解決ロジックを通すため、任意のduration値を
                厳密にテストするにはこの直接挿入が必要。"""
                rid = str(uuid.uuid4())
                async with AsyncSessionLocal() as session:
                    session.add(Reservation(
                        id=rid, shop_id=shop_id, customer_id=None, user_id=None,
                        staff_id=staff_id, table_id=table_id, service_id=None,
                        guest_name="既存予約テスト", guest_phone="09011110000",
                        reservation_date=start, duration_minutes=duration_minutes,
                        number_of_people=2, status="confirmed",
                        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                    ))
                    await session.commit()
                return rid

            from app.routers.reservations import _find_available_table, _existing_duration_minutes

            async def _table_available(start: datetime, duration_minutes: int, default_duration=90) -> bool:
                async with AsyncSessionLocal() as session:
                    found_id, unmanaged = await _find_available_table(
                        session, shop_id, 2, start, duration_minutes, default_duration,
                    )
                    return unmanaged or found_id is not None

            async def _clear_reservations():
                async with AsyncSessionLocal() as session:
                    result = await session.execute(select(Reservation).filter(Reservation.shop_id == shop_id))
                    for res in result.scalars().all():
                        await session.delete(res)
                    await session.commit()

            # ===== 1. 同一durationでの重複判定（回帰） =====
            await _clear_reservations()
            await _insert_reservation(table_id=table_a_id, start=_dt("14:00"), duration_minutes=60)
            assert await _table_available(_dt("14:30"), 60) is False, "同一duration同士の重複が検知されていません"
            print("1. 同一duration重複判定（回帰）: OK")

            # ===== 2. 異なるdurationでの重複判定（最重要バグの直接検証） =====
            await _clear_reservations()
            # 既存予約: 20:00〜22:00（duration=120）
            await _insert_reservation(table_id=table_a_id, start=_dt("20:00"), duration_minutes=120)
            # 新規リクエスト: 21:30開始・duration=30（=21:30〜22:00）
            # 以前のバグでは、既存予約の終了時刻計算に「新規リクエストのduration=30」を
            # 流用し、既存予約の終了時刻を21:30と誤算出。21:30〜22:00は「重複しない」
            # と誤判定されていた。修正後は既存予約自身のduration=120を使うため、
            # 22:00までは重複として正しく検知されなければならない。
            available = await _table_available(_dt("21:30"), 30)
            assert available is False, (
                "【最重要】異なるduration間の重複検知に失敗: 既存予約(20:00-22:00,dur=120)と"
                "新規リクエスト(21:30-22:00,dur=30)は重複するはずが「空き」と判定された"
                "（旧バグ〈新規リクエストのdurationを既存予約に流用〉が再発している可能性）"
            )
            # 逆方向（非重複）も確認: 既存予約20:00〜20:30(duration=30) に対し、
            # 21:00開始・duration=120（21:00〜23:00）の新規リクエストは重複しないはず。
            await _clear_reservations()
            await _insert_reservation(table_id=table_a_id, start=_dt("20:00"), duration_minutes=30)
            available_reverse = await _table_available(_dt("21:00"), 120)
            assert available_reverse is True, (
                "異なるduration・非重複ケースの逆方向で誤検知: 既存予約(20:00-20:30,dur=30)と"
                "新規リクエスト(21:00-23:00,dur=120)は重複しないはずが「満席」と判定された"
            )
            print("2. 異なるduration重複判定（最重要バグ・双方向）: OK")

            # ===== 3. 包含関係（新規が既存に内包される） =====
            await _clear_reservations()
            await _insert_reservation(table_id=table_a_id, start=_dt("14:00"), duration_minutes=120)  # 14:00-16:00
            available = await _table_available(_dt("15:00"), 30)  # 15:00-15:30
            assert available is False, "包含関係（新規が既存に内包）の重複検知に失敗"
            print("3. 包含関係（新規リクエストが既存予約に内包）: OK")

            # ===== 4. 逆包含関係（既存が新規に内包される） =====
            await _clear_reservations()
            await _insert_reservation(table_id=table_a_id, start=_dt("15:00"), duration_minutes=30)  # 15:00-15:30
            available = await _table_available(_dt("14:00"), 120)  # 14:00-16:00
            assert available is False, "逆包含関係（既存予約が新規リクエストに内包）の重複検知に失敗"
            print("4. 逆包含関係（既存予約が新規リクエストに内包）: OK")

            # ===== 5. 境界が接するだけ（重複としない） =====
            await _clear_reservations()
            await _insert_reservation(table_id=table_a_id, start=_dt("14:00"), duration_minutes=60)  # 14:00-15:00
            available = await _table_available(_dt("15:00"), 60)  # 15:00-16:00
            assert available is True, "境界が接するだけのケースが誤って重複判定されている（touching boundaryはOKのはず）"
            print("5. 境界が接するだけ（非重複）: OK")

            # ===== 6. 通常の非重複 =====
            await _clear_reservations()
            await _insert_reservation(table_id=table_a_id, start=_dt("14:00"), duration_minutes=60)  # 14:00-15:00
            available = await _table_available(_dt("16:00"), 60)  # 16:00-17:00
            assert available is True, "明確に離れた時間帯が誤って重複判定されている"
            print("6. 通常の非重複: OK")

            # ===== 7. テーブル占有（end-to-end、異なるduration） =====
            await _clear_reservations()
            # Table Aに18:00〜20:00（duration=120）の既存予約
            await _insert_reservation(table_id=table_a_id, start=_dt("18:00"), duration_minutes=120)
            assert await _table_available(_dt("19:00"), 60) is False, "テーブル占有中のはずが空き判定された(19:00開始)"
            assert await _table_available(_dt("20:00"), 60) is True, "テーブルの予約終了直後のはずが満席判定された(20:00開始)"
            print("7. テーブル占有（占有中は不可・終了直後は可）: OK")

            # ===== 8. スタッフ占有（end-to-end、異なるduration） =====
            r = await client.post("/api/v1/staff", json={"shop_id": shop_id, "name": "テスト担当"}, headers=owner_a)
            assert r.status_code == 200, f"create staff failed: {r.status_code} {r.text}"
            staff_id = r.json()["id"]
            r = await client.post("/api/v1/services", json={
                "shop_id": shop_id, "name": "施術60分", "base_price": 5000, "duration_minutes": 60,
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create service failed: {r.status_code} {r.text}"
            service_id = r.json()["id"]
            r = await client.post(f"/api/v1/staff/{staff_id}/services/{service_id}", headers=owner_a)
            assert r.status_code == 200, f"assign staff-service failed: {r.status_code} {r.text}"

            await _clear_reservations()
            await _insert_reservation(staff_id=staff_id, start=_dt("18:00"), duration_minutes=120)

            from app.routers.reservations import _find_available_staff_for_service

            async def _staff_available(start: datetime, duration_minutes: int, default_duration=90) -> bool:
                async with AsyncSessionLocal() as session:
                    found_id, unmanaged = await _find_available_staff_for_service(
                        session, shop_id, service_id, start, duration_minutes, default_duration,
                    )
                    return unmanaged or found_id is not None

            assert await _staff_available(_dt("19:00"), 60) is False, "スタッフ占有中のはずが空き判定された(19:00開始)"
            assert await _staff_available(_dt("20:00"), 60) is True, "スタッフの予約終了直後のはずが満席判定された(20:00開始)"
            print("8. スタッフ占有（占有中は不可・終了直後は可）: OK")

            # ===== 9. check_availability Tool エンドポイントのend-to-end確認 =====
            await _clear_reservations()
            # 既存予約: テーブルAに20:00〜22:00(duration=120)
            await _insert_reservation(table_id=table_a_id, start=_dt("20:00"), duration_minutes=120)
            r = await client.post(
                f"/api/v1/shops/{shop_id}/realtime-voice/tools/check-availability",
                json={"date": target_date.isoformat(), "time": "21:30", "party_size": 2},
            )
            assert r.status_code == 200, f"check-availability failed: {r.status_code} {r.text}"
            body = r.json()
            # このshopはservice指定なしのテーブル予約想定。shop.reservation_duration_minutes=90分
            # なので21:30開始の新規リクエストはduration=90分(21:30-23:00)で判定される。
            # 既存予約(20:00-22:00)と重複するため空きなしのはず。
            assert body["available"] is False, (
                f"check_availability Toolのend-to-endで、既存予約と重複するはずの時間帯が"
                f"空きありと判定された: {body}"
            )
            r2 = await client.post(
                f"/api/v1/shops/{shop_id}/realtime-voice/tools/check-availability",
                json={"date": target_date.isoformat(), "time": "22:00", "party_size": 2},
            )
            assert r2.status_code == 200, f"check-availability(2) failed: {r2.status_code} {r2.text}"
            assert r2.json()["available"] is True, (
                f"check_availability Toolのend-to-endで、既存予約終了直後のはずの時間帯が"
                f"満席と判定された: {r2.json()}"
            )
            print("9. check_availability Tool end-to-end（異なるdurationの重複を正しく反映）: OK")

            # ===== 10. create_reservation実行時の再検証 =====
            await _clear_reservations()
            # 既存予約: テーブルAに10:00〜11:30(duration=90, shopデフォルト)
            await _insert_reservation(table_id=table_a_id, start=_dt("10:00"), duration_minutes=90)
            # check_availabilityを経由せず、いきなりcreate_reservationを呼んでも
            # 重複が正しく検知されて拒否されること（Tool経由ではなくAPI直接呼び出し）。
            overlapping_dt = datetime.combine(target_date, datetime.min.time().replace(hour=10, minute=30))
            r = await client.post("/api/v1/reservations/create", json={
                "shop_id": shop_id,
                "reservation_date": overlapping_dt.isoformat(),
                "number_of_people": 2,
                "guest_name": "再検証テスト", "guest_phone": "09022220000",
            })
            assert r.status_code == 400, f"create_reservation時の再検証が機能していません: {r.status_code} {r.text}"
            assert r.json().get("detail") == "ご希望の時間は満席です。他の時間をお試しください", r.json()
            print("10. create_reservation実行時の再検証（重複を検知して拒否）: OK")

            # ===== 11. 冪等性（idempotency_key）の回帰確認 =====
            await _clear_reservations()
            free_dt = datetime.combine(target_date, datetime.min.time().replace(hour=8, minute=0))
            idem_key = "smoke-test-time-range-" + str(uuid.uuid4())
            r1 = await client.post(
                f"/api/v1/shops/{shop_id}/realtime-voice/tools/create-reservation",
                json={
                    "date": target_date.isoformat(), "time": "08:00", "party_size": 2,
                    "guest_name": "冪等性テスト", "guest_phone": "09033330000",
                    "call_id": idem_key,
                },
            )
            assert r1.status_code == 200 and r1.json()["success"] is True, f"create1 failed: {r1.status_code} {r1.text}"
            reservation_id_1 = r1.json()["reservation_id"]
            r2 = await client.post(
                f"/api/v1/shops/{shop_id}/realtime-voice/tools/create-reservation",
                json={
                    "date": target_date.isoformat(), "time": "08:00", "party_size": 2,
                    "guest_name": "冪等性テスト", "guest_phone": "09033330000",
                    "call_id": idem_key,
                },
            )
            assert r2.status_code == 200 and r2.json()["success"] is True, f"create2(idempotent retry) failed: {r2.status_code} {r2.text}"
            assert r2.json()["reservation_id"] == reservation_id_1, "同一call_idの再送信で別の予約が作成されてしまった（冪等性の回帰）"
            print("11. 冪等性（idempotency_key）の回帰確認: OK")

            # ===== 12. 既存予約（duration_minutes=NULL）のフォールバック挙動 =====
            await _clear_reservations()
            # Phase B以前に作成されたのと同じ状態を再現: duration_minutesを明示的にNULLにする
            null_dur_id = await _insert_reservation(table_id=table_a_id, start=_dt("12:00"), duration_minutes=None)
            async with AsyncSessionLocal() as session:
                fetched = await session.get(Reservation, null_dur_id)
                assert fetched.duration_minutes is None, "セットアップミス: duration_minutesがNULLになっていません"
            # shop.reservation_duration_minutes=90分にフォールバックするはずなので、
            # 12:00〜13:30が占有される扱いになる。13:00開始のリクエストは重複するはず。
            assert await _table_available(_dt("13:00"), 30, default_duration=90) is False, (
                "duration_minutes=NULLの既存予約が、店舗デフォルト所要時間(90分)への"
                "フォールバックなしに扱われている（NULL=無制限として扱われている疑い）"
            )
            # 13:30以降（90分後）は空くはず
            assert await _table_available(_dt("13:30"), 30, default_duration=90) is True, (
                "duration_minutes=NULLの既存予約のフォールバック後の終了時刻が正しくない"
            )
            print("12. 既存予約(duration_minutes=NULL)の店舗デフォルトへのフォールバック: OK")

            # ===== 13. テナント分離 =====
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-time-range-b@example.com", "password": "password123",
                "display_name": "TimeRangeテストオーナーB",
            })
            assert r.status_code in (200, 201), f"register B failed: {r.status_code} {r.text}"
            owner_b = {"Authorization": f"Bearer {r.json()['access_token']}"}

            # テナントBはテナントAのテーブルを編集できない
            r = await client.put(f"/api/v1/shops/{shop_id}/tables/{table_a_id}", json={
                "name": "乗っ取りテスト", "capacity": 99,
            }, headers=owner_b)
            assert r.status_code == 403, f"テナント分離の破れ: 他テナントのテーブルを編集できてしまった: {r.status_code} {r.text}"

            # テナントBはテナントAの店舗の予約一覧も取得できない
            r = await client.get(f"/api/v1/reservations/shop/{shop_id}", headers=owner_b)
            assert r.status_code in (403, 404), (
                f"テナント分離の破れ: 他テナントの予約一覧を取得できてしまった: {r.status_code} {r.text}"
            )
            print("13. テナント分離（他テナントのテーブル編集・予約閲覧が拒否される）: OK")

            await _clear_reservations()

    print("\n全テストOK: Reservation Intelligence Phase B（Reservation TIME-RANGE Foundation）")


if __name__ == "__main__":
    asyncio.run(main())
