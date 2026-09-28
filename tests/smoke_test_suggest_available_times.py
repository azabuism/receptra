"""
Reservation Intelligence Phase E-1: suggest_available_times Tool Calling
スモークテスト

背景（今回追加した内容。ユーザー承認済み設計）:

RECEPTRA予約受付改善（3機能）のうちFeature 3。お客様が特定の1つの時刻を
まだ決めていない場合に、AIが自分で時刻を作り出す・推測するのではなく、
既存の唯一のsource of truthである空き状況判定
（app/routers/reservations.py の get_availability() から抽出した
_compute_day_availability()）から実際に空きがある時刻を最大3件だけ取得して
提示できるようにする、新しいRealtime Voice AI Tool
（POST /api/v1/shops/{shop_id}/realtime-voice/tools/suggest-available-times）
を追加した。

本フェーズで変更した内容（最小限。新しい予約可否判定ロジックは一切追加していない）:
1. app/routers/reservations.py: 元々get_availability()の内部に直接書かれて
   いた「1日分の空き状況を計算する」部分を _compute_day_availability() へ
   抽出（既存のcheck_single_slot_availability()と同じ理由での抽出。
   get_availability()自体の外部からの見え方＝レスポンス形・メッセージ文言は
   一切変更していない）。抽出にあわせ、各スロットの実際のdatetime
   （日跨ぎ営業でtarget_dateの翌日にまたがる場合の正しい順序比較のため）を
   内部専用に保持するslot_cursorsを追加した。
2. app/schemas/reservation.py: SuggestAvailableTimesToolRequest/Response/
   SuggestedTimeCandidateを追加（既存スキーマは一切変更していない）。
3. app/routers/realtime_voice.py: suggest_available_times_tool エンドポイント
   と、完全に決定論的な候補選定ロジック_select_time_candidates()を追加。
4. app/services/realtime_voice_ai.py: _REALTIME_TOOLSに9個目のToolとして
   suggest_available_timesを追加。あわせてcheck_availabilityの
   business_hours_not_configured案内に、Feature 1（既存の
   business_hours_not_configured判定ロジック自体は無変更）向けの
   最小限の7点の指示強化を追加した。
5. frontend/public/js/realtime-voice-engine.js: 既存のfunction_call
   ディスパッチに1分岐（callSuggestAvailableTimesTool）を追加しただけ
   （FAST TURN/ANSWER_WINDOW/Forced Commit/semantic_vad/HOTFIX14〜19/
   Phase A/B/C playback teardown等の既存メカニズムには一切触れていない。
   SPEAK_THEN_WORK_ACK_TOOLSにも追加していない＝ユーザー確認済み）。

候補選定アルゴリズム（完全に決定論的。ユーザー確認済みの具体例つき）:
- available=Trueのスロットのみが対象。
- 希望時刻(time)が指定されている場合: 希望時刻との差の絶対値が小さい順、
  同じ場合は時刻そのものが早い方を優先（例: 希望18:00、候補17:30/18:30
  → [17:30, 18:30]の順）。
- 希望時刻が指定されていない場合: 時刻が早い順。
- 最大3件。0件の場合はreason_code="no_availability_found"（他の日が
  空いているとは一切示唆しない）。

検証項目:
A. Tool schema回帰: 9個のTool（8個の既存Tool + suggest_available_times）。
   staff_idがparametersに含まれない。requiredはdate/party_sizeのみ。
B. _compute_day_availability抽出の互換性: GET /availability と
   suggest_available_times が同じavailable=trueスロット集合に基づいて
   動作していること（既存のget_availability()の挙動が変化していないこと）。
C. 希望時刻に近い順 + 同着タイブレーク（早い方優先。ユーザー確認済みの例）。
D. 希望時刻より前/後どちらの候補も正しく選ばれる。
E. 希望時刻未指定時は単純に時刻が早い順。
F. 満席（空きスロットが1件も無い） → reason_code=no_availability_found、
   success=True、candidates=[]（Tool呼び出し自体は正常）。
G. 埋まっているスロットは候補から除外される（テーブル競合）。
H. 休憩時間（ShopBreakTime）と重なるスロットは候補に一切現れない。
I. 過去の時刻は候補に現れない。
J. business_hours_not_configured（営業時間未設定）→ candidates=[]、
   success=True、reason_code設定。
K. shop_closed（定休日）→ candidates=[]、reason_code=shop_closed。
L. temporary_closure（臨時休業）→ candidates=[]、reason_code=temporary_closure。
M. スタッフ競合（サービス単位・スタッフ指名）: 指名スタッフが埋まっている
   時刻は候補から除外され、空いている時刻は候補に残る。
N. staff_not_identified / staff_name_ambiguous（Named Staff Safe Resolution
   と全く同じ挙動）。
O. リソース競合・resource_type_required（Generic Resource Foundation）。
P. 最大3件までしか返らない（候補が4件以上ある場合でも3件に絞られる）。
Q. invalid_request（不正な日付形式）→ success=False。
R. temporarily_unavailable（存在しない/非公開の店舗）→ success=False。
S. テナント分離: 店舗Aへの呼び出しが店舗Bのデータに一切影響されない。
T. 予約の確約ではないこと（候補提示後、他の経路で先に埋まった場合、
   create_reservationが独立に再判定して拒否する。既存の二重チェック方針の
   回帰確認）。

実行: python3 tests/smoke_test_suggest_available_times.py
"""

import asyncio
import os
import sys
import tempfile
import uuid
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_suggest_times.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-suggest-times"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402


def _next_weekday(target_weekday: int, weeks_ahead: int = 3):
    today = date.today()
    days_ahead = (target_weekday - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead + 7 * (weeks_ahead - 1))


def _test_tool_schema():
    from app.services.realtime_voice_ai import _REALTIME_TOOLS

    tool_names = [t["name"] for t in _REALTIME_TOOLS]
    assert tool_names == [
        "check_availability", "create_reservation", "get_shop_info", "find_customer",
        "confirm_customer_identity", "get_customer_context", "set_conversation_language",
        "request_callback", "suggest_available_times",
    ], f"9 Tool schemaが想定と異なります（既存8Tool + suggest_available_timesの順序・件数を確認）: {tool_names}"

    suggest_tool = next(t for t in _REALTIME_TOOLS if t["name"] == "suggest_available_times")
    params = suggest_tool.get("parameters", {})
    props = params.get("properties", {})
    assert "staff_id" not in props, f"suggest_available_timesのparametersにstaff_id（内部ID）が公開されています: {props.keys()}"
    assert set(params.get("required", [])) == {"date", "party_size"}, (
        f"suggest_available_timesのrequiredが想定と異なります: {params.get('required')}"
    )

    # 他の既存8Toolのparameters（プロパティ名の集合）が一切変更されていないことの回帰確認。
    check_tool = next(t for t in _REALTIME_TOOLS if t["name"] == "check_availability")
    assert set(check_tool["parameters"]["properties"].keys()) == {
        "date", "time", "party_size", "service_id", "staff_name", "resource_type",
    }, f"check_availabilityのparametersが変更されています: {check_tool['parameters']['properties'].keys()}"

    print("A. Tool schema回帰（9個・staff_id非公開・required最小）: OK")


async def main():
    _test_tool_schema()

    from app.main import app, lifespan

    async with lifespan(app):
        from app.database import AsyncSessionLocal
        from app.models.shop import Shop
        from app.models.reservation import Reservation
        from app.models.staff import Staff
        from sqlalchemy import select

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # ===== セットアップ: テーブル予約の居酒屋（店舗A） =====
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-suggest-times-a@example.com", "password": "password123",
                "display_name": "SuggestTimesテストオーナーA",
            })
            assert r.status_code in (200, 201), f"register A failed: {r.status_code} {r.text}"
            owner_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "SuggestTimesテスト居酒屋", "category": "居酒屋", "address": "東京都渋谷区9-9-9",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create shop A failed: {r.status_code} {r.text}"
            shop_a = r.json()["shop_id"]

            r = await client.put(f"/api/v1/shops/{shop_a}/hours", json={"hours": [
                {"day_of_week": d, "opening_time": "11:00:00", "closing_time": "23:00:00", "is_closed": False}
                for d in range(7)
            ]}, headers=owner_a)
            assert r.status_code == 200, f"set hours A failed: {r.status_code} {r.text}"

            async with AsyncSessionLocal() as session:
                shop_obj = await session.get(Shop, shop_a)
                shop_obj.reservations_enabled = True
                # 30分単位（30分刻みのスロット間隔と揃える）にすることで、
                # 「特定の1スロットだけを埋める／空ける」というテスト構成を
                # 単純にできるようにする（60分等にすると、1件の予約が隣接する
                # 複数スロットにもまたがって影響してしまうため）。
                shop_obj.reservation_duration_minutes = 30
                await session.commit()

            r = await client.post(f"/api/v1/shops/{shop_a}/tables", json={
                "name": "テーブルA", "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 200, f"create table A failed: {r.status_code} {r.text}"
            table_a_id = r.json()["id"]

            target_date = _next_weekday(2, weeks_ahead=3)  # 定休日等の影響がない平日
            target_date_str = target_date.isoformat()

            def _dt(hh_mm: str) -> datetime:
                hh, mm = hh_mm.split(":")
                return datetime.combine(target_date, datetime.min.time().replace(hour=int(hh), minute=int(mm)))

            async def _insert_reservation(*, table_id=None, staff_id=None, service_id=None,
                                           start: datetime, duration_minutes=30, shop_id=shop_a) -> str:
                rid = str(uuid.uuid4())
                async with AsyncSessionLocal() as session:
                    session.add(Reservation(
                        id=rid, shop_id=shop_id,
                        customer_id=None, user_id=None,
                        staff_id=staff_id, table_id=table_id, service_id=service_id,
                        guest_name="既存予約テスト", guest_phone="09011110000",
                        reservation_date=start, duration_minutes=duration_minutes,
                        number_of_people=2, status="confirmed",
                        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                    ))
                    await session.commit()
                return rid

            async def _clear_reservations(shop_id):
                async with AsyncSessionLocal() as session:
                    result = await session.execute(select(Reservation).filter(Reservation.shop_id == shop_id))
                    for res in result.scalars().all():
                        await session.delete(res)
                    await session.commit()

            async def _suggest(shop_id, **kwargs):
                payload = {"date": target_date_str, "party_size": 2}
                payload.update(kwargs)
                r = await client.post(
                    f"/api/v1/shops/{shop_id}/realtime-voice/tools/suggest-available-times",
                    json=payload,
                )
                return r

            # ===== C. 希望時刻に近い順 + 同着タイブレーク（早い方優先） =====
            # shop_aのreservation_duration_minutesは30分（スロット間隔と同じ）に
            # 設定済みのため、18:00に1件だけ予約を入れると18:00のスロットのみが
            # 塞がり、17:30・18:30はどちらも空いたまま（触れるだけの隣接はOKという
            # 既存の境界値ルール）。希望時刻18:00からの差はどちらも30分の同着となり、
            # ユーザー確認済みの具体例（希望18:00、候補17:30/18:30→[17:30,18:30]の順）
            # を直接再現できる。
            await _clear_reservations(shop_a)
            await _insert_reservation(table_id=table_a_id, start=_dt("18:00"))
            r = await _suggest(shop_a, time="18:00")
            assert r.status_code == 200, f"suggest failed: {r.status_code} {r.text}"
            body = r.json()
            assert body["success"] is True, f"success=Trueのはず: {body}"
            times = [c["time"] for c in body["candidates"]]
            assert times[:2] == ["17:30", "18:30"], (
                f"タイブレーク（早い方優先）が仕様と異なります。期待=['17:30','18:30']先頭, 実際={times}"
            )
            print("C. 希望時刻に近い順 + 同着タイブレーク（早い方優先。ユーザー確認済み例）: OK")

            # ===== D. 希望時刻より前/後どちらの候補も正しく選ばれる =====
            await _clear_reservations(shop_a)
            # 12:00のみ空け、他は全部埋める形ではなく、逆に12:00と16:00だけ空ける。
            busy = []
            cursor = _dt("11:00")
            end = _dt("22:30")
            while cursor <= end:
                hh_mm = cursor.strftime("%H:%M")
                if hh_mm not in ("12:00", "16:00"):
                    busy.append(hh_mm)
                cursor += timedelta(minutes=30)
            for hhmm in busy:
                await _insert_reservation(table_id=table_a_id, start=_dt(hhmm), duration_minutes=30)
            r = await _suggest(shop_a, time="14:00")
            body = r.json()
            times = [c["time"] for c in body["candidates"]]
            assert times == ["12:00", "16:00"], f"前後どちらの候補も含み、近い順であるべき: {times}"
            print("D. 希望時刻より前/後どちらの候補も正しく選ばれる: OK")

            # ===== E. 希望時刻未指定時は単純に時刻が早い順 =====
            r = await _suggest(shop_a)
            body = r.json()
            times = [c["time"] for c in body["candidates"]]
            assert times == ["12:00", "16:00"], f"希望時刻未指定時は早い順であるべき: {times}"
            print("E. 希望時刻未指定時は時刻が早い順: OK")

            # ===== F. 満席 → no_availability_found（success=Trueのまま） =====
            await _clear_reservations(shop_a)
            cursor = _dt("11:00")
            while cursor <= end:
                await _insert_reservation(table_id=table_a_id, start=cursor, duration_minutes=60)
                cursor += timedelta(minutes=30)
            r = await _suggest(shop_a, time="18:00")
            body = r.json()
            assert body["success"] is True, f"満席でもTool呼び出し自体はsuccess=Trueのはず: {body}"
            assert body["candidates"] == [], f"満席なのにcandidatesが空でない: {body}"
            assert body["reason_code"] == "no_availability_found", f"reason_code不一致: {body}"
            print("F. 満席 → no_availability_found（success=True）: OK")

            # ===== G. 埋まっているスロットは候補から除外される（テーブル競合） =====
            await _clear_reservations(shop_a)
            await _insert_reservation(table_id=table_a_id, start=_dt("18:00"))
            r = await _suggest(shop_a, time="18:00")
            body = r.json()
            times = [c["time"] for c in body["candidates"]]
            assert "18:00" not in times, f"埋まっている18:00が候補に含まれています: {times}"
            assert "17:30" in times, f"隣接する空き17:30が候補に含まれていません: {times}"
            print("G. 埋まっているスロットの候補除外（テーブル競合）: OK")

            # ===== H. 休憩時間と重なるスロットは候補に現れない =====
            await _clear_reservations(shop_a)
            r = await client.post(f"/api/v1/shops/{shop_a}/break-times", json={
                "day_of_week": target_date.weekday(), "start_time": "15:00:00", "end_time": "16:00:00",
            }, headers=owner_a)
            assert r.status_code == 200, f"create break-time failed: {r.status_code} {r.text}"
            r = await _suggest(shop_a, time="15:00")
            body = r.json()
            times = [c["time"] for c in body["candidates"]]
            assert "15:00" not in times and "15:30" not in times, f"休憩時間帯が候補に含まれています: {times}"
            # 休憩を削除（後続テストに影響しないよう）
            r = await client.get(f"/api/v1/shops/{shop_a}/break-times")
            for bt in r.json():
                if bt["day_of_week"] == target_date.weekday():
                    await client.delete(f"/api/v1/shops/{shop_a}/break-times/{bt['id']}", headers=owner_a)
            print("H. 休憩時間と重なるスロットの候補除外: OK")

            # ===== I. 過去の時刻は候補に現れない =====
            # 本日を対象日にし、現在時刻より前の候補が一切含まれないことを確認する。
            await _clear_reservations(shop_a)
            today = date.today()
            r = await client.put(f"/api/v1/shops/{shop_a}/hours", json={"hours": [
                {"day_of_week": d, "opening_time": "00:00:00", "closing_time": "23:30:00", "is_closed": False}
                for d in range(7)
            ]}, headers=owner_a)
            assert r.status_code == 200, f"set 24h hours failed: {r.status_code} {r.text}"
            r = await client.post(
                f"/api/v1/shops/{shop_a}/realtime-voice/tools/suggest-available-times",
                json={"date": today.isoformat(), "party_size": 2},
            )
            body = r.json()
            now_jst_naive = datetime.utcnow() + timedelta(hours=9)
            for c in body["candidates"]:
                slot_dt = datetime.combine(today, datetime.strptime(c["time"], "%H:%M").time())
                assert slot_dt > now_jst_naive - timedelta(minutes=1), (
                    f"過去の時刻が候補に含まれています: {c['time']} (now~{now_jst_naive})"
                )
            print("I. 過去の時刻が候補に現れない: OK")

            # 平日営業時間に戻す（後続テストへの影響防止）
            r = await client.put(f"/api/v1/shops/{shop_a}/hours", json={"hours": [
                {"day_of_week": d, "opening_time": "11:00:00", "closing_time": "23:00:00", "is_closed": False}
                for d in range(7)
            ]}, headers=owner_a)
            assert r.status_code == 200

            # ===== J. business_hours_not_configured =====
            r = await client.post("/api/v1/shops/register", json={
                "name": "SuggestTimes未設定店舗", "category": "居酒屋", "address": "東京都渋谷区1-2-3",
            }, headers=owner_a)
            assert r.status_code in (200, 201)
            shop_noconfig = r.json()["shop_id"]
            r = await client.post(
                f"/api/v1/shops/{shop_noconfig}/realtime-voice/tools/suggest-available-times",
                json={"date": target_date_str, "party_size": 2},
            )
            body = r.json()
            assert body["success"] is True and body["candidates"] == [], f"未設定時の応答が不正: {body}"
            assert body["reason_code"] == "business_hours_not_configured", f"reason_code不一致: {body}"
            print("J. business_hours_not_configured（success=True, candidates=[]）: OK")

            # ===== K. shop_closed（定休日） =====
            r = await client.put(f"/api/v1/shops/{shop_a}/hours", json={"hours": [
                {"day_of_week": d, "opening_time": "11:00:00", "closing_time": "23:00:00",
                 "is_closed": (d == target_date.weekday())}
                for d in range(7)
            ]}, headers=owner_a)
            assert r.status_code == 200
            r = await _suggest(shop_a)
            body = r.json()
            assert body["reason_code"] == "shop_closed" and body["candidates"] == [], f"定休日判定ミス: {body}"
            print("K. shop_closed（定休日）: OK")

            # 元に戻す
            r = await client.put(f"/api/v1/shops/{shop_a}/hours", json={"hours": [
                {"day_of_week": d, "opening_time": "11:00:00", "closing_time": "23:00:00", "is_closed": False}
                for d in range(7)
            ]}, headers=owner_a)
            assert r.status_code == 200

            # ===== L. temporary_closure（臨時休業） =====
            r = await client.post(f"/api/v1/shops/{shop_a}/closures", json={
                "start_date": target_date_str, "end_date": target_date_str, "reason": "設備点検",
            }, headers=owner_a)
            assert r.status_code == 200, f"create closure failed: {r.status_code} {r.text}"
            r = await _suggest(shop_a)
            body = r.json()
            assert body["reason_code"] == "temporary_closure" and body["candidates"] == [], f"臨時休業判定ミス: {body}"
            r = await client.get(f"/api/v1/shops/{shop_a}/closures", headers=owner_a)
            for cl in r.json():
                if cl["start_date"] == target_date_str:
                    await client.delete(f"/api/v1/shops/{shop_a}/closures/{cl['id']}", headers=owner_a)
            print("L. temporary_closure（臨時休業）: OK")

            # ===== M. スタッフ競合（サービス単位・スタッフ指名） =====
            r = await client.post("/api/v1/shops/register", json={
                "name": "SuggestTimesテストサロン", "category": "ヘアサロン", "address": "東京都渋谷区4-4-4",
            }, headers=owner_a)
            assert r.status_code in (200, 201)
            shop_salon = r.json()["shop_id"]
            r = await client.put(f"/api/v1/shops/{shop_salon}/hours", json={"hours": [
                {"day_of_week": d, "opening_time": "09:00:00", "closing_time": "19:00:00", "is_closed": False}
                for d in range(7)
            ]}, headers=owner_a)
            assert r.status_code == 200
            async with AsyncSessionLocal() as session:
                shop_obj = await session.get(Shop, shop_salon)
                shop_obj.reservations_enabled = True
                shop_obj.reservation_duration_minutes = 60
                await session.commit()
            r = await client.post("/api/v1/services", json={
                "shop_id": shop_salon, "name": "カット", "base_price": 4000, "duration_minutes": 60,
            }, headers=owner_a)
            assert r.status_code == 200
            service_id = r.json()["id"]
            r = await client.post("/api/v1/staff", json={
                "shop_id": shop_salon, "name": "田中 太郎", "nomination_allowed": True,
            }, headers=owner_a)
            assert r.status_code == 200
            staff_id = r.json()["id"]
            r = await client.post(f"/api/v1/staff/{staff_id}/services/{service_id}", headers=owner_a)
            assert r.status_code == 200

            await _insert_reservation(staff_id=staff_id, service_id=service_id, start=_dt("11:00"),
                                       duration_minutes=60, shop_id=shop_salon)
            r = await client.post(
                f"/api/v1/shops/{shop_salon}/realtime-voice/tools/suggest-available-times",
                json={"date": target_date_str, "party_size": 1, "service_id": service_id,
                      "staff_name": "田中太郎", "time": "11:00"},
            )
            body = r.json()
            assert body["success"] is True
            times = [c["time"] for c in body["candidates"]]
            assert "11:00" not in times, f"指名スタッフが埋まっている11:00が候補に含まれています: {times}"
            assert len(times) > 0, f"他の時刻が候補に残っているべき: {body}"
            print("M. スタッフ競合（指名スタッフの埋まっている時刻を除外）: OK")

            # ===== N. staff_not_identified / staff_name_ambiguous =====
            r = await client.post(
                f"/api/v1/shops/{shop_salon}/realtime-voice/tools/suggest-available-times",
                json={"date": target_date_str, "party_size": 1, "service_id": service_id,
                      "staff_name": "存在しない氏名太郎"},
            )
            body = r.json()
            assert body["success"] is True and body["candidates"] == [], f"staff_not_identified応答が不正: {body}"
            assert body["reason_code"] == "staff_not_identified", f"reason_code不一致: {body}"

            r = await client.post("/api/v1/staff", json={
                "shop_id": shop_salon, "name": "田中 花子", "nomination_allowed": True,
            }, headers=owner_a)
            assert r.status_code == 200
            staff2_id = r.json()["id"]
            r = await client.post(f"/api/v1/staff/{staff2_id}/services/{service_id}", headers=owner_a)
            assert r.status_code == 200
            r = await client.post(
                f"/api/v1/shops/{shop_salon}/realtime-voice/tools/suggest-available-times",
                json={"date": target_date_str, "party_size": 1, "service_id": service_id, "staff_name": "田中"},
            )
            body = r.json()
            assert body["reason_code"] == "staff_name_ambiguous", f"曖昧判定ミス: {body}"
            assert set(body.get("staff_name_candidates") or []) == {"田中 太郎", "田中 花子"}, (
                f"候補名リストが不正: {body}"
            )
            print("N. staff_not_identified / staff_name_ambiguous: OK")

            # ===== O. リソース競合・resource_type_required =====
            r = await client.post("/api/v1/shops/register", json={
                "name": "SuggestTimesテストカラオケ", "category": "カラオケ", "address": "東京都渋谷区7-7-7",
            }, headers=owner_a)
            assert r.status_code in (200, 201)
            shop_ks = r.json()["shop_id"]
            r = await client.put(f"/api/v1/shops/{shop_ks}/hours", json={"hours": [
                {"day_of_week": d, "opening_time": "10:00:00", "closing_time": "22:00:00", "is_closed": False}
                for d in range(7)
            ]}, headers=owner_a)
            assert r.status_code == 200
            async with AsyncSessionLocal() as session:
                shop_obj = await session.get(Shop, shop_ks)
                shop_obj.reservations_enabled = True
                shop_obj.reservation_duration_minutes = 60
                await session.commit()
            # 単一種別(room)のみ作成 → resource_type省略でも一意に決定できるはず
            r = await client.post(f"/api/v1/shops/{shop_ks}/resources", json={
                "name": "個室A", "resource_type": "room", "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 200, f"create resource failed: {r.status_code} {r.text}"
            room_id = r.json()["id"]
            r = await client.post(f"/api/v1/shops/{shop_ks}/resources", json={
                "name": "個室B", "resource_type": "room", "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 200
            room2_id = r.json()["id"]

            r = await client.post(
                f"/api/v1/shops/{shop_ks}/realtime-voice/tools/suggest-available-times",
                json={"date": target_date_str, "party_size": 2, "time": "12:00"},
            )
            body = r.json()
            assert body["success"] is True and len(body["candidates"]) > 0, f"単一種別リソースで候補が出るはず: {body}"
            print("O1. 単一resource_typeで自動決定・候補生成: OK")

            # 別種別(bed)を混在させ、resource_type未指定ではAMBIGUOUS
            r = await client.post(f"/api/v1/shops/{shop_ks}/resources", json={
                "name": "ベッドA", "resource_type": "bed", "capacity": 1,
            }, headers=owner_a)
            assert r.status_code == 200
            r = await client.post(
                f"/api/v1/shops/{shop_ks}/realtime-voice/tools/suggest-available-times",
                json={"date": target_date_str, "party_size": 2, "time": "12:00"},
            )
            body = r.json()
            assert body["reason_code"] == "resource_type_required", f"resource_type_required判定ミス: {body}"
            assert set(body.get("available_resource_types") or []) == {"room", "bed"}, (
                f"available_resource_typesが不正: {body}"
            )
            assert body["candidates"] == []
            print("O2. resource_type_required（複数種混在）: OK")

            print("\n=== Reservation Intelligence Phase E-1（suggest_available_times）: A/C〜O 全項目OK ===")

            # ===== P. 最大3件までしか返らない =====
            await _clear_reservations(shop_a)
            r = await client.put(f"/api/v1/shops/{shop_a}/hours", json={"hours": [
                {"day_of_week": d, "opening_time": "11:00:00", "closing_time": "23:00:00", "is_closed": False}
                for d in range(7)
            ]}, headers=owner_a)
            assert r.status_code == 200
            r = await _suggest(shop_a)  # 何も予約が無いので大量に空きがあるはず
            body = r.json()
            assert len(body["candidates"]) <= 3, f"候補が3件を超えています: {body['candidates']}"
            assert len(body["candidates"]) == 3, f"十分な空きがあるのに3件返っていません: {body['candidates']}"
            print("P. 最大3件までしか返らない: OK")

            # ===== Q. invalid_request（不正な日付形式） =====
            r = await client.post(
                f"/api/v1/shops/{shop_a}/realtime-voice/tools/suggest-available-times",
                json={"date": "2026/99/99", "party_size": 2},
            )
            assert r.status_code == 200, f"バリデーションエラーでも200で構造化応答を返すはず: {r.status_code} {r.text}"
            body = r.json()
            assert body["success"] is False and body["reason_code"] == "invalid_request", f"invalid_request判定ミス: {body}"
            print("Q. invalid_request（不正な日付形式）→ success=False: OK")

            # ===== R. temporarily_unavailable（存在しない店舗） =====
            r = await client.post(
                f"/api/v1/shops/nonexistent-shop-id-xyz/realtime-voice/tools/suggest-available-times",
                json={"date": target_date_str, "party_size": 2},
            )
            body = r.json()
            assert body["success"] is False and body["reason_code"] == "temporarily_unavailable", (
                f"temporarily_unavailable判定ミス: {body}"
            )
            print("R. temporarily_unavailable（存在しない店舗）→ success=False: OK")

            # ===== S. テナント分離 =====
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-suggest-times-b@example.com", "password": "password123",
                "display_name": "SuggestTimesテストオーナーB",
            })
            assert r.status_code in (200, 201)
            owner_b = {"Authorization": f"Bearer {r.json()['access_token']}"}
            r = await client.post("/api/v1/shops/register", json={
                "name": "SuggestTimesテスト居酒屋B", "category": "居酒屋", "address": "東京都渋谷区2-2-2",
            }, headers=owner_b)
            assert r.status_code in (200, 201)
            shop_b = r.json()["shop_id"]
            r = await client.put(f"/api/v1/shops/{shop_b}/hours", json={"hours": [
                {"day_of_week": d, "opening_time": "11:00:00", "closing_time": "23:00:00", "is_closed": False}
                for d in range(7)
            ]}, headers=owner_b)
            assert r.status_code == 200
            async with AsyncSessionLocal() as session:
                shop_obj = await session.get(Shop, shop_b)
                shop_obj.reservations_enabled = True
                await session.commit()
            r = await client.post(f"/api/v1/shops/{shop_b}/tables", json={
                "name": "テーブルB", "capacity": 4,
            }, headers=owner_b)
            assert r.status_code == 200
            table_b_id = r.json()["id"]
            # 店舗Bを丸ごと予約で埋める（shop_idを明示指定し、店舗Aには一切影響しない）
            cursor = _dt("11:00")
            while cursor <= end:
                await _insert_reservation(table_id=table_b_id, start=cursor, duration_minutes=60, shop_id=shop_b)
                cursor += timedelta(minutes=30)

            # 店舗A（Pで空けたばかり）は依然として候補があるはず、店舗Bは満席のはず
            r = await _suggest(shop_a)
            body_a = r.json()
            r = await client.post(
                f"/api/v1/shops/{shop_b}/realtime-voice/tools/suggest-available-times",
                json={"date": target_date_str, "party_size": 2},
            )
            body_b = r.json()
            assert len(body_a["candidates"]) > 0, f"店舗Aに候補が残っているはず: {body_a}"
            assert body_b["candidates"] == [] and body_b["reason_code"] == "no_availability_found", (
                f"店舗Bは満席のはずが店舗Aのデータが混入している可能性: {body_b}"
            )
            print("S. テナント分離（店舗Aの候補が店舗Bの予約状況に影響されない）: OK")

            # ===== T. 予約の確約ではない（create_reservationによる独立した再判定） =====
            await _clear_reservations(shop_a)
            r = await _suggest(shop_a, time="18:00")
            body = r.json()
            assert len(body["candidates"]) > 0, f"事前準備: 候補が無い: {body}"
            chosen_time = body["candidates"][0]["time"]

            # 提示直後に「別経路」で同じ枠が先に埋まったことを模擬する。
            await _insert_reservation(table_id=table_a_id, start=_dt(chosen_time), duration_minutes=60)

            call_id = "smoke-suggest-times-race-" + str(uuid.uuid4())
            r = await client.post(
                f"/api/v1/shops/{shop_a}/realtime-voice/tools/create-reservation",
                json={
                    "date": target_date_str, "time": chosen_time, "party_size": 2,
                    "guest_name": "顧客T", "guest_phone": "08099990000",
                    "call_id": call_id,
                },
            )
            assert r.status_code == 200
            create_body = r.json()
            assert create_body["success"] is False, (
                f"suggest_available_timesの候補は予約の確約ではなく、create_reservationは"
                f"独立に再判定して拒否するはずが成功しました: {create_body}"
            )
            assert create_body["reason_code"] == "fully_booked", f"reason_code不一致: {create_body}"
            print("T. 予約の確約ではないこと（create_reservationの独立再判定で拒否される）: OK")

            # ===== B. _compute_day_availability抽出の互換性（GET /availabilityとの整合） =====
            await _clear_reservations(shop_a)
            await _insert_reservation(table_id=table_a_id, start=_dt("18:00"), duration_minutes=60)
            r = await client.get(
                f"/api/v1/reservations/shop/{shop_a}/availability",
                params={"date": target_date_str, "party_size": 2},
            )
            assert r.status_code == 200, f"GET availability failed: {r.status_code} {r.text}"
            avail_body = r.json()
            available_times_from_get = {s["time"] for s in avail_body["slots"] if s["available"]}

            r = await _suggest(shop_a)
            suggest_body = r.json()
            suggested_times = {c["time"] for c in suggest_body["candidates"]}
            assert suggested_times.issubset(available_times_from_get), (
                f"suggest_available_timesの候補がGET /availabilityのavailable=trueスロット集合の"
                f"部分集合になっていません（_compute_day_availability抽出に不整合の可能性）: "
                f"suggested={suggested_times}, available_from_get={available_times_from_get}"
            )
            assert "18:00" not in available_times_from_get, "GET /availability側でも18:00は空きなしのはず"
            print("B. _compute_day_availability抽出の互換性（GET /availabilityとの整合）: OK")

            print("\n=== Reservation Intelligence Phase E-1（suggest_available_times）: 全項目OK ===")


if __name__ == "__main__":
    asyncio.run(main())
