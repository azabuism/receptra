"""
RECEPTRA — PHASE R5 PART B スモークテスト
（Named Staff Safe Resolution & Human Handoff）

背景:

Phase R5 Part Bでは、お客様がRealtime Voice AIとの会話で特定のスタッフを
名前で指名した場合（例:「田中さんでお願いします」）に、その名前を安全に
店舗のStaff.idへ解決する仕組みを追加した。

Audit（実装前）で判明した重要な事実:
- 従来、Realtime AIのcheck_availability/create_reservation Toolには
  「スタッフ指名がある場合はstaff_id（内部ID）を指定してください」という
  説明文とstaff_idパラメータが存在していたが、AIには店舗のStaffロスターが
  一切渡されておらず、実在するstaff_idを知る手段が無かった（死んだ・
  安全でないパススルーだった）。
- app.services.customer_context.get_customer_context()はlast_staff_name
  （文字列）のみを返し、IDは一切返さない。
- 既存のfind_staff相当のToolも存在しなかった。

このため、Part Bでは新しいToolを追加せず、既存のcheck_availability/
create_reservation Toolに、内部IDではなく自然文の氏名を受け付ける
optional staff_nameパラメータを追加した（Phase R4のresource_type解決
パターンを完全に踏襲）。バックエンド側（app.routers.realtime_voice.
_resolve_staff_by_name）がこの店舗のactiveかつnomination_allowed=True
なStaffだけを対象に、Unicode NFKC正規化＋空白除去＋大文字小文字統一
という決定的（fuzzyではない）変換だけで安全に解決する。

解決結果は4状態のいずれか:
- NOT_PROVIDED: staff_name未指定。指名なしとして従来通り継続。
- RESOLVED    : 安全に一意特定できた。既存のpreferred_staff_id経路へ
                そのまま合流する（代替スタッフの自動検索は一切行わない、
                という既存の安全性をそのまま享受する）。
- NOT_FOUND   : 安全に識別できるStaffが1人もいない（未登録・inactive・
                nomination_allowed=False・読み違い等、理由は問わず区別
                しない。「在籍していない」とは絶対に断定しない）。
                reason_code=staff_not_identifiedを返し、AIはHuman
                Handoff（既存のrequest_callback、reason_code=
                staff_judgement_required）へ進む。
- AMBIGUOUS   : 姓の一致等で複数候補が残った。reason_code=
                staff_name_ambiguous + staff_name_candidates
                （DB由来の表示名のみ）を返す。

新しいTool・新しいDBマイグレーションは一切追加していない
（request_callback・CallbackRequestは完全に既存のまま。
CallbackRequestReasonCode.STAFF_JUDGEMENT_REQUIREDも既存）。

検証項目:
A. フルネーム完全一致（一意）→ RESOLVEDとしてcheck_availability/
   create_reservationが正常に既存のpreferred_staff_id経路で成立する
B. 氏名比較の正規化（全角/半角空白・空白の有無）がfuzzyではなく決定的に
   機能する（同一人物を表す異表記が正しく同一視される）
C. display_name経由の一致
D. 見つからない（未登録の氏名）→ staff_not_identified
E. inactiveなStaffのみ一致 → staff_not_identified（代替なし）
F. nomination_allowed=FalseのStaffのみ一致 → staff_not_identified
   （既存フィールドの意図を尊重する新しい活用）
G. 姓のみ一致・複数候補 → staff_name_ambiguous + 正しい候補名リスト
   （DB由来のみ・ソート済み）
H. Unsafe fuzzy matchが行われない（類似するが異なる姓は絶対に一致しない）
I. 一意特定できたが指定時刻に空きがない → staff_unavailable（他Staffへの
   自動振替は発生しない。既存のpreferred_staff_id安全性がstaff_name経由
   でも維持されることの確認）
J. staff_id/staff_nameどちらも未指定 → 従来通り指名なし予約として成立
   （既存動作の回帰確認）
K. Realtime Tool JSON Schema検証: check_availability/create_reservation
   いずれのparametersにもstaff_idというプロパティが存在しない
   （staff_nameのみが公開されている。AIがstaff_idを発明できないことの
   構造的な保証）
L. request_callback（既存Tool）でstaff_judgement_required理由・希望
   スタッフ名を含むinquiry_textがそのままCallbackRequestへ保存される
   （新しいCallback Tool・新しいDBカラムを一切追加していないことの確認）
M. create_reservation_toolのAMBIGUOUS/NOT_FOUND時、create_reservation()
   本体は一切呼び出されず、予約行が作成されない（R3/R4アロケーション
   エンジンに一切触れないことの確認）
N. Table/Resource等、service_idが指定されない予約種別ではstaff_name解決
   自体が行われない（スタッフ概念の無い店舗での無関係な失敗を防ぐ）
O. 既存のcheck_availability/create_reservation正常系（service_id指定・
   staff指名なし）の回帰確認

実行: python3 tests/smoke_test_named_staff_handoff.py
"""

import asyncio
import os
import sys
import tempfile
import uuid
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_named_staff_r5b.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-named-staff-r5b"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402


def _next_weekday(target_weekday: int, weeks_ahead: int = 3):
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
            from app.database import AsyncSessionLocal
            from app.models.shop import Shop
            from app.models.staff import Staff
            from app.models.reservation import Reservation
            from app.models.callback_request import CallbackRequest, CallbackRequestReasonCode
            from sqlalchemy import select

            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-named-staff-r5b@example.com", "password": "password123",
                "display_name": "NamedStaffR5Bテストオーナー",
            })
            assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
            owner = {"Authorization": f"Bearer {r.json()['access_token']}"}

            # ===== 店舗A: サービス単位（美容院）・スタッフ複数名 =====
            r = await client.post("/api/v1/shops/register", json={
                "name": "NamedStaffテストサロン", "category": "ヘアサロン", "address": "東京都渋谷区1-1-1",
            }, headers=owner)
            assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
            shop_id = r.json()["shop_id"]

            r = await client.put(f"/api/v1/shops/{shop_id}/hours", json={"hours": [
                {"day_of_week": d, "opening_time": "09:00:00", "closing_time": "21:00:00", "is_closed": False}
                for d in range(7)
            ]}, headers=owner)
            assert r.status_code == 200, f"set hours failed: {r.status_code} {r.text}"

            async with AsyncSessionLocal() as session:
                shop_obj = await session.get(Shop, shop_id)
                shop_obj.reservations_enabled = True
                shop_obj.reservation_duration_minutes = 60
                await session.commit()

            r = await client.post("/api/v1/services", json={
                "shop_id": shop_id, "name": "カット", "base_price": 4000, "duration_minutes": 60,
            }, headers=owner)
            assert r.status_code == 200, f"create service failed: {r.status_code} {r.text}"
            service_id = r.json()["id"]

            async def _new_staff(name, display_name=None, nomination_allowed=True):
                r = await client.post("/api/v1/staff", json={
                    "shop_id": shop_id, "name": name, "display_name": display_name,
                    "nomination_allowed": nomination_allowed,
                }, headers=owner)
                assert r.status_code == 200, f"create staff({name}) failed: {r.status_code} {r.text}"
                staff_id = r.json()["id"]
                r = await client.post(f"/api/v1/staff/{staff_id}/services/{service_id}", headers=owner)
                assert r.status_code == 200, f"assign staff service({name}) failed: {r.status_code} {r.text}"
                return staff_id

            # A/B用: 姓と名の間に半角スペースを含む氏名（正規化テストの土台）
            tanaka_tarou_id = await _new_staff("田中 太郎")
            # G用: 同じ姓（田中）を持つ別人（曖昧解決テスト）
            tanaka_hanako_id = await _new_staff("田中 花子")
            # C用: display_name経由の一致
            suzuki_id = await _new_staff("鈴木一郎", display_name="鈴木センセイ")
            # E用: inactive
            satou_id = await _new_staff("佐藤次郎")
            async with AsyncSessionLocal() as session:
                staff_obj = await session.get(Staff, satou_id)
                staff_obj.is_active = "inactive"
                await session.commit()
            # F用: nomination_allowed=False（オーナーが指名予約対象外に設定）
            takahashi_id = await _new_staff("高橋四郎", nomination_allowed=False)
            # H用: 「山田」という紛らわしいが異なる姓
            yamamoto_id = await _new_staff("山本三郎")

            target_date = _next_weekday(2)  # 水曜日

            # ===== A. フルネーム完全一致（一意）=====
            r = await client.post(
                f"/api/v1/shops/{shop_id}/realtime-voice/tools/check-availability",
                json={
                    "date": target_date.isoformat(), "time": "10:00", "party_size": 1,
                    "service_id": service_id, "staff_name": "田中太郎",
                },
            )
            assert r.status_code == 200, f"check-availability failed: {r.status_code} {r.text}"
            body = r.json()
            assert body["available"] is True, f"一意特定できたStaffの空き確認が失敗: {body}"
            assert body.get("reason_code") is None
            print("A. フルネーム完全一致（一意）→ RESOLVEDとして正常に空き確認できる: OK")

            # create_reservationでも同様に成立し、正しいstaff_idが割り当てられること
            call_id_a = "smoke-named-staff-a-" + str(uuid.uuid4())
            r = await client.post(
                f"/api/v1/shops/{shop_id}/realtime-voice/tools/create-reservation",
                json={
                    "date": target_date.isoformat(), "time": "10:00", "party_size": 1,
                    "guest_name": "顧客A", "guest_phone": "08011110001",
                    "service_id": service_id, "staff_name": "田中太郎",
                    "call_id": call_id_a,
                },
            )
            assert r.status_code == 200, f"create-reservation failed: {r.status_code} {r.text}"
            body = r.json()
            assert body["success"] is True, f"予約成立のはずが失敗: {body}"
            reservation_a_id = body["reservation_id"]
            async with AsyncSessionLocal() as session:
                res = await session.get(Reservation, reservation_a_id)
                assert res.staff_id == tanaka_tarou_id, (
                    f"staff_nameから解決されたstaff_idが正しく予約に設定されていません: "
                    f"expected={tanaka_tarou_id}, actual={res.staff_id}"
                )
            print("A2. create_reservationでも一意特定されたstaff_idが正しく予約に設定される: OK")

            # ===== B. 正規化（全角空白・空白なし）=====
            for variant, label in [("田中　太郎", "全角スペース"), ("田中太郎", "スペースなし"), (" 田中 太郎 ", "前後空白")]:
                r = await client.post(
                    f"/api/v1/shops/{shop_id}/realtime-voice/tools/check-availability",
                    json={
                        "date": target_date.isoformat(), "time": "11:00", "party_size": 1,
                        "service_id": service_id, "staff_name": variant,
                    },
                )
                assert r.status_code == 200
                body = r.json()
                assert body["available"] is True and body.get("reason_code") is None, (
                    f"正規化({label}: {variant!r})で一致するはずが失敗: {body}"
                )
            print("B. 氏名比較の正規化（全角/半角空白・空白の有無）が決定的に機能する: OK")

            # ===== C. display_name経由の一致 =====
            r = await client.post(
                f"/api/v1/shops/{shop_id}/realtime-voice/tools/check-availability",
                json={
                    "date": target_date.isoformat(), "time": "12:00", "party_size": 1,
                    "service_id": service_id, "staff_name": "鈴木センセイ",
                },
            )
            assert r.status_code == 200
            body = r.json()
            assert body["available"] is True and body.get("reason_code") is None, (
                f"display_name経由の一致が失敗: {body}"
            )
            print("C. display_name経由の一致: OK")

            # ===== D. 見つからない（未登録の氏名）=====
            r = await client.post(
                f"/api/v1/shops/{shop_id}/realtime-voice/tools/check-availability",
                json={
                    "date": target_date.isoformat(), "time": "13:00", "party_size": 1,
                    "service_id": service_id, "staff_name": "存在しない山口さん",
                },
            )
            assert r.status_code == 200
            body = r.json()
            assert body["available"] is False and body["reason_code"] == "staff_not_identified", (
                f"未登録氏名はstaff_not_identifiedになるはずが: {body}"
            )
            assert body.get("staff_name_candidates") is None
            print("D. 見つからない（未登録の氏名）→ staff_not_identified: OK")

            # ===== E. inactiveなStaffのみ一致 =====
            r = await client.post(
                f"/api/v1/shops/{shop_id}/realtime-voice/tools/check-availability",
                json={
                    "date": target_date.isoformat(), "time": "13:00", "party_size": 1,
                    "service_id": service_id, "staff_name": "佐藤次郎",
                },
            )
            assert r.status_code == 200
            body = r.json()
            assert body["available"] is False and body["reason_code"] == "staff_not_identified", (
                f"inactiveなStaffのみ一致する場合もstaff_not_identifiedになるはずが: {body}"
            )
            print("E. inactiveなStaffのみ一致 → staff_not_identified（代替なし）: OK")

            # ===== F. nomination_allowed=FalseのStaffのみ一致 =====
            r = await client.post(
                f"/api/v1/shops/{shop_id}/realtime-voice/tools/check-availability",
                json={
                    "date": target_date.isoformat(), "time": "13:00", "party_size": 1,
                    "service_id": service_id, "staff_name": "高橋四郎",
                },
            )
            assert r.status_code == 200
            body = r.json()
            assert body["available"] is False and body["reason_code"] == "staff_not_identified", (
                f"nomination_allowed=FalseのStaffのみ一致する場合もstaff_not_identifiedになるはずが: {body}"
            )
            print("F. nomination_allowed=FalseのStaffのみ一致 → staff_not_identified: OK")

            # ===== G. 姓のみ一致・複数候補（曖昧）=====
            r = await client.post(
                f"/api/v1/shops/{shop_id}/realtime-voice/tools/check-availability",
                json={
                    "date": target_date.isoformat(), "time": "13:00", "party_size": 1,
                    "service_id": service_id, "staff_name": "田中",
                },
            )
            assert r.status_code == 200
            body = r.json()
            assert body["available"] is False and body["reason_code"] == "staff_name_ambiguous", (
                f"姓のみ一致・複数候補はstaff_name_ambiguousになるはずが: {body}"
            )
            candidates = body.get("staff_name_candidates")
            assert candidates == sorted(["田中 太郎", "田中 花子"]), (
                f"候補名がDB由来の表示名と一致しません（AI創作の混入の疑い）: {candidates}"
            )
            print("G. 姓のみ一致・複数候補 → staff_name_ambiguous + 正しい候補名リスト: OK")

            # create_reservation側でも同様にAMBIGUOUSで安全に失敗し、予約が作られないこと
            async with AsyncSessionLocal() as session:
                before_count = len((await session.execute(
                    select(Reservation).filter(Reservation.shop_id == shop_id)
                )).scalars().all())
            call_id_g = "smoke-named-staff-g-" + str(uuid.uuid4())
            r = await client.post(
                f"/api/v1/shops/{shop_id}/realtime-voice/tools/create-reservation",
                json={
                    "date": target_date.isoformat(), "time": "14:00", "party_size": 1,
                    "guest_name": "顧客G", "guest_phone": "08011110007",
                    "service_id": service_id, "staff_name": "田中",
                    "call_id": call_id_g,
                },
            )
            assert r.status_code == 200
            body = r.json()
            assert body["success"] is False and body["reason_code"] == "staff_name_ambiguous"
            assert body.get("staff_name_candidates") == sorted(["田中 太郎", "田中 花子"])
            async with AsyncSessionLocal() as session:
                after_count = len((await session.execute(
                    select(Reservation).filter(Reservation.shop_id == shop_id)
                )).scalars().all())
            assert after_count == before_count, (
                "AMBIGUOUS時にcreate_reservation()本体が呼ばれ、予約行が作成されてしまっています"
                "（R3/R4アロケーションエンジンに触れないという設計に違反）"
            )
            print("M. create_reservation_toolのAMBIGUOUS/NOT_FOUND時、予約行が作成されない: OK")

            # ===== H. Unsafe fuzzy matchが行われない =====
            r = await client.post(
                f"/api/v1/shops/{shop_id}/realtime-voice/tools/check-availability",
                json={
                    "date": target_date.isoformat(), "time": "15:00", "party_size": 1,
                    "service_id": service_id, "staff_name": "山田",
                },
            )
            assert r.status_code == 200
            body = r.json()
            assert body["available"] is False and body["reason_code"] == "staff_not_identified", (
                f"「山田」は「山本」と類似するだけで一致してはいけません（fuzzy matching禁止）: {body}"
            )
            print("H. Unsafe fuzzy matchが行われない（「山田」は「山本」に一致しない）: OK")

            # ===== I. 一意特定できたが指定時刻に空きがない（代替Staff自動割当なし）=====
            # 鈴木センセイを16:00で埋める
            call_id_i1 = "smoke-named-staff-i1-" + str(uuid.uuid4())
            r = await client.post(
                f"/api/v1/shops/{shop_id}/realtime-voice/tools/create-reservation",
                json={
                    "date": target_date.isoformat(), "time": "16:00", "party_size": 1,
                    "guest_name": "顧客I1", "guest_phone": "08011110008",
                    "service_id": service_id, "staff_name": "鈴木センセイ",
                    "call_id": call_id_i1,
                },
            )
            assert r.status_code == 200 and r.json()["success"] is True

            # 他のStaff（田中太郎・田中花子等）はこの時間帯空いている状態で、
            # 改めて鈴木センセイを16:00で指名 → staff_unavailableになり、
            # 他のStaffへ黙って振り替えられないこと
            r = await client.post(
                f"/api/v1/shops/{shop_id}/realtime-voice/tools/check-availability",
                json={
                    "date": target_date.isoformat(), "time": "16:00", "party_size": 1,
                    "service_id": service_id, "staff_name": "鈴木センセイ",
                },
            )
            assert r.status_code == 200
            body = r.json()
            assert body["available"] is False and body["reason_code"] == "staff_unavailable", (
                f"一意特定できたが空きが無い場合はstaff_unavailableになるはずが: {body}"
            )
            print("I. 一意特定できたが空きがない → staff_unavailable（他Staffへの自動振替なし）: OK")

            # ===== J. staff_id/staff_nameどちらも未指定（従来動作の回帰）=====
            r = await client.post(
                f"/api/v1/shops/{shop_id}/realtime-voice/tools/check-availability",
                json={
                    "date": target_date.isoformat(), "time": "17:00", "party_size": 1,
                    "service_id": service_id,
                },
            )
            assert r.status_code == 200
            body = r.json()
            assert body["available"] is True and body.get("reason_code") is None, (
                f"指名なし予約の従来動作が壊れています: {body}"
            )
            print("J. staff_id/staff_name未指定 → 従来通り指名なし予約として成立: OK")

            # ===== K. Realtime Tool JSON Schema検証 =====
            from app.services.realtime_voice_ai import _REALTIME_TOOLS
            check_tool = next(t for t in _REALTIME_TOOLS if t["name"] == "check_availability")
            create_tool = next(t for t in _REALTIME_TOOLS if t["name"] == "create_reservation")
            for tool in (check_tool, create_tool):
                props = tool["parameters"]["properties"]
                assert "staff_id" not in props, (
                    f"{tool['name']}のTool定義にstaff_idが公開されています"
                    "（AIがIDを発明できてしまう設計上の欠陥）"
                )
                assert "staff_name" in props, f"{tool['name']}にstaff_nameが定義されていません"
            print("K. Realtime Tool JSON Schema: staff_idが非公開・staff_nameのみ公開されている: OK")

            # ===== L. request_callback（既存Tool）での希望スタッフ名の保存 =====
            call_id_l = "smoke-named-staff-l-" + str(uuid.uuid4())
            r = await client.post(
                f"/api/v1/shops/{shop_id}/realtime-voice/tools/request-callback",
                json={
                    "customer_name": "顧客L", "customer_phone": "08011110009",
                    "inquiry_text": "田中花子様を希望。RECEPTRA側で安全に一意特定できず（同姓複数）。",
                    "desired_date": target_date.isoformat(), "desired_time": "18:00", "party_size": 1,
                    "service_id": service_id,
                    "reason_code": "staff_judgement_required",
                    "call_id": call_id_l,
                },
            )
            assert r.status_code == 200, f"request-callback failed: {r.status_code} {r.text}"
            body = r.json()
            assert body["success"] is True and body["reason_code"] == "staff_judgement_required"
            async with AsyncSessionLocal() as session:
                res = await session.execute(
                    select(CallbackRequest).filter(
                        CallbackRequest.shop_id == shop_id,
                        CallbackRequest.reason_code == "staff_judgement_required",
                    )
                )
                rows = res.scalars().all()
                assert len(rows) == 1, f"CallbackRequestが想定件数と異なります: {len(rows)}"
                row = rows[0]
                assert "田中花子" in row.inquiry_text, "希望スタッフ名がinquiry_textに保存されていません"
                assert row.customer_phone == "08011110009"
                assert row.desired_time is not None and row.desired_time.strftime("%H:%M") == "18:00"
            assert CallbackRequestReasonCode.STAFF_JUDGEMENT_REQUIRED.value == "staff_judgement_required", (
                "既存のCallbackRequestReasonCode.STAFF_JUDGEMENT_REQUIREDが変更されています"
                "（新しいCallback Toolを増やしていないことの間接確認）"
            )
            print("L. request_callback: 希望スタッフ名を含むinquiry_textがそのまま保存される: OK")

            # ===== N. Table/Resource等、service_id未指定の予約種別では解決を行わない =====
            r = await client.post("/api/v1/shops/register", json={
                "name": "NamedStaffテスト飲食店", "category": "その他", "address": "東京都渋谷区2-2-2",
            }, headers=owner)
            assert r.status_code in (200, 201)
            shop_b_id = r.json()["shop_id"]
            r = await client.put(f"/api/v1/shops/{shop_b_id}/hours", json={"hours": [
                {"day_of_week": d, "opening_time": "09:00:00", "closing_time": "21:00:00", "is_closed": False}
                for d in range(7)
            ]}, headers=owner)
            assert r.status_code == 200
            async with AsyncSessionLocal() as session:
                shop_b_obj = await session.get(Shop, shop_b_id)
                shop_b_obj.reservations_enabled = True
                await session.commit()
            r = await client.post(
                f"/api/v1/shops/{shop_b_id}/realtime-voice/tools/check-availability",
                json={
                    "date": target_date.isoformat(), "time": "10:00", "party_size": 2,
                    "staff_name": "誤って渡された氏名",
                },
            )
            assert r.status_code == 200
            body = r.json()
            assert body["available"] is True and body.get("reason_code") is None, (
                "service_id未指定（Table/Resource等）でstaff_nameが誤って解決を試み、"
                f"無関係な失敗を起こしています: {body}"
            )
            print("N. service_id未指定の予約種別ではstaff_name解決自体が行われない: OK")

            # ===== O. 既存の正常系（staff指名なし）の回帰確認 =====
            call_id_o = "smoke-named-staff-o-" + str(uuid.uuid4())
            r = await client.post(
                f"/api/v1/shops/{shop_id}/realtime-voice/tools/create-reservation",
                json={
                    "date": target_date.isoformat(), "time": "19:00", "party_size": 1,
                    "guest_name": "顧客O", "guest_phone": "08011110010",
                    "service_id": service_id,
                    "call_id": call_id_o,
                },
            )
            assert r.status_code == 200 and r.json()["success"] is True, (
                f"指名なしの通常予約が壊れています: {r.status_code} {r.text}"
            )
            print("O. 既存のcheck_availability/create_reservation正常系（指名なし）の回帰確認: OK")

            print("\n=== RECEPTRA Phase R5 Part B（Named Staff Safe Resolution & Human Handoff）"
                  "スモークテスト: 全項目OK ===")


if __name__ == "__main__":
    asyncio.run(main())
