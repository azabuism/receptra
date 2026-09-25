"""
RECEPTRA — PHASE N1 スモークテスト（統一Owner Notification基盤）

背景（Audit Reportで判明した内容の要約）:

Phase N1着手前の監査で、RECEPTRAには既に3つの独立した「通知に近い」仕組み
（Shop上の通知先電話/メール設定、OutboundCallJob/Log[電話通知の疑似送信]、
CallbackRequest[Human Handoff受付記録]）が存在するが、これらを横断する
統一イベントログは存在しないことが判明した。また、Reservation作成の
Single Source of Truthはcreate_reservation()（app/routers/reservations.py）の
1箇所のみで、Web予約/AI音声予約/AIチャット予約の3経路すべてがこれを経由する
ため、通知イベント生成をこの1箇所に仕込むだけで全経路をカバーできることが
確認された。さらに「オーナー自身が管理画面から予約を作成する」経路は現状
存在しないため、Section 6で懸念されていた「オーナー自身の予約でノイズ通知が
出る」リスクは今回のスコープでは発生しない（将来その経路が追加された時点で
再監査が必要）。

本フェーズで新設したもの:
- OwnerNotificationEvent（新規テーブル。alembicは使わずBase.metadata.create_all()
  で自動作成。他のPhase3系モデルと同じ確立済みパターン）
- app/services/owner_notifications.py: notify_reservation_created() /
  notify_callback_requested()（app.services.outbound_dispatchと全く同じ
  「専用DBセッション・失敗分離・DB一意インデックスによる冪等性」設計）
- app/routers/owner_notifications.py: GET .../notifications (一覧+未読数) /
  GET .../notifications/{id} (詳細) / PATCH .../notifications/{id}/read
  （すべてオーナー認証必須・tenant/shop分離必須。app.routers.callback_requests
  と全く同じauthパターン）
- frontend/public/shop-manage.html: 「お知らせ」カード（callback-requests-card
  と save-all-card の間に新設。全体を保存の対象外）

配信（LINE/Email/SMS）は本フェーズで一切実装しない。既存の予約エンジン
（get_availability/create_reservation本体のロジック）・R3/R4・Booking Board・
Week View・Realtime Voice Tool schema・VAD/WebRTC・CustomerMemory・
reservation_source・booking_channelには一切変更を加えていない。

検証項目（Audit Report Section 22の A〜Lに対応。M〜Tは既存の専用smoke testが
既にカバーしているため、本ファイルでは重複させず、フルリグレッションの
別実行で確認する）:

A. Web予約成功 → Reservation 1件 → RESERVATION_CREATED通知 1件
B. 同一Reservationに対してnotify_reservation_created()を2回呼んでも
   OwnerNotificationEventは重複生成されない（DB一意インデックスが最終防衛線）
C. AI Voice経由のcreate-reservation Toolでも同じ1箇所（create_reservation()）
   を通るため、RESERVATION_CREATED通知が1件生成される
D. request-callback Tool（Human Handoff）→ OWNER_ACTION_REQUIRED通知 1件
E. 同一CallbackRequestに対してnotify_callback_requested()を2回呼んでも
   重複生成されない
F. 他tenantのオーナーが一覧取得 → 403
G. 他tenantのオーナーが詳細取得 → 403
H. 未認証での一覧取得 → 401/403
I. 通知一覧のレスポンスにphone/emailが一切含まれない
J. 通知メッセージ生成ロジック（owner_notifications.py）がCustomerMemoryを
   一切参照・引用していない（ソースレベル）
K. 通知メッセージ生成ロジックがCallbackRequest.inquiry_text（お客様の生の
   お問い合わせ内容）を一切引用せず、reason_codeベースの一般的な文言のみを
   使っていること（ソースレベル + ランタイムでinquiry_textの内容が
   メッセージに含まれないことを確認）
L. 既読化（mark read）は所有オーナーのみ成功し、他tenant/未認証では失敗する
N9(safety). notify_reservation_created()内部でDBセッション生成自体が失敗しても
   例外が外へ伝播しない（予約作成のレスポンスに一切影響しないことの安全性保証）
FRONTEND. shop-manage.htmlの「お知らせ」カード（#owner-notices-card等）が
   正しく追加され、既存のcallback-requests-card/save-all-cardの間に
   挿入されていること。既存のCallbackRequest UI・全体を保存機能に回帰がないこと。

実行: python3 tests/smoke_test_owner_notification_foundation.py
"""

import asyncio
import os
import re
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(
    tempfile.mkdtemp(), "smoke_n1_owner_notification_foundation.db"
)
os.environ["SECRET_KEY"] = "smoke-test-secret-key-n1-owner-notification-foundation"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _test_frontend_structure():
    shop_manage_html = open(
        os.path.join(REPO_ROOT, "frontend", "public", "shop-manage.html"), encoding="utf-8"
    ).read()

    # FRONTEND: card presence and correct insertion point
    assert 'id="owner-notices-card"' in shop_manage_html, "お知らせカード(#owner-notices-card)が見つかりません"
    assert 'id="owner-notices-list"' in shop_manage_html, "#owner-notices-listが見つかりません"
    assert 'id="owner-notices-unread-badge"' in shop_manage_html, "#owner-notices-unread-badgeが見つかりません"

    cb_idx = shop_manage_html.index('id="callback-requests-card"')
    notices_idx = shop_manage_html.index('id="owner-notices-card"')
    save_all_idx = shop_manage_html.index('id="save-all-card"')
    assert cb_idx < notices_idx < save_all_idx, (
        "お知らせカードはcallback-requests-cardとsave-all-cardの間に挿入されている必要があります"
    )
    print("FRONTEND-1. お知らせカードがcallback-requests-cardとsave-all-cardの間に正しく挿入: OK")

    # 全体を保存(handleSaveAll)の対象セクション配列に、お知らせカードが
    # 誤って追加されていないこと（一覧管理カードは個別保存が方針のため）
    save_all_start = shop_manage_html.index("function handleSaveAll")
    save_all_end = shop_manage_html.index(
        "document.getElementById('save-all-btn').addEventListener", save_all_start
    )
    save_all_section = shop_manage_html[save_all_start:save_all_end]
    assert "owner-notices" not in save_all_section and "OwnerNotice" not in save_all_section, (
        "お知らせカードが誤って「全体を保存」の対象に含まれています"
    )
    print("FRONTEND-2. 「全体を保存」機能にお知らせカードが含まれていない（回帰なし・意図通り除外）: OK")

    # loadOwnerNotices()がinit sequenceから呼ばれていること
    assert "loadOwnerNotices();" in shop_manage_html, "loadOwnerNotices()の呼び出しが見つかりません"
    init_seq_idx = shop_manage_html.index("loadCallbackRequests();\n            loadOwnerNotices();")
    assert init_seq_idx > 0, "loadOwnerNotices()がloadCallbackRequests()の直後に呼ばれていません"
    print("FRONTEND-3. loadOwnerNotices()がinit sequenceに正しく追加されている: OK")

    # markOwnerNoticeReadがPATCH .../read を呼んでいること
    assert "/notifications/' + encodeURIComponent(notificationId) + '/read'" in shop_manage_html
    assert re.search(r"markOwnerNoticeRead[\s\S]{0,300}method:\s*'PATCH'", shop_manage_html), (
        "markOwnerNoticeReadがPATCHメソッドで既読化APIを呼んでいません"
    )
    print("FRONTEND-4. markOwnerNoticeReadが正しくPATCH .../readを呼んでいる: OK")

    # 既存CallbackRequest UIへの回帰がないこと
    assert "function loadCallbackRequests()" in shop_manage_html
    assert "function renderCallbackRequestsList()" in shop_manage_html
    print("FRONTEND-5. (回帰) 既存CallbackRequest一覧UIは無傷: OK")


async def main():
    _test_frontend_structure()

    from app.main import app, lifespan

    async with lifespan(app):
        from app.database import AsyncSessionLocal
        from app.models.shop import Shop
        from app.models.owner_notification import OwnerNotificationEvent
        from app.models.callback_request import CallbackRequest
        from app.services.owner_notifications import (
            notify_reservation_created,
            notify_callback_requested,
        )
        from sqlalchemy import select, func

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # ===== セットアップ: オーナーA・店舗A =====
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-n1-a@example.com", "password": "password123",
                "display_name": "N1テストオーナーA",
            })
            assert r.status_code in (200, 201), f"register A failed: {r.status_code} {r.text}"
            owner_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "N1テスト店舗A", "category": "レストラン", "address": "東京都渋谷区1-1-1",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create shop A failed: {r.status_code} {r.text}"
            shop_a = r.json()["shop_id"]

            hours_payload = {"hours": [
                {"day_of_week": d, "opening_time": "00:00:00", "closing_time": "23:30:00", "is_closed": False}
                for d in range(7)
            ]}
            r = await client.put(f"/api/v1/shops/{shop_a}/hours", json=hours_payload, headers=owner_a)
            assert r.status_code == 200, f"set hours A failed: {r.status_code} {r.text}"

            async with AsyncSessionLocal() as session:
                shop_obj = await session.get(Shop, shop_a)
                shop_obj.reservations_enabled = True
                await session.commit()

            # ===== セットアップ: オーナーB・店舗B（他tenant検証用） =====
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-n1-b@example.com", "password": "password123",
                "display_name": "N1テストオーナーB",
            })
            assert r.status_code in (200, 201), f"register B failed: {r.status_code} {r.text}"
            owner_b = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "N1テスト店舗B", "category": "レストラン", "address": "東京都渋谷区2-2-2",
            }, headers=owner_b)
            assert r.status_code in (200, 201), f"create shop B failed: {r.status_code} {r.text}"
            shop_b = r.json()["shop_id"]

            import datetime as _dt
            future_date = (_dt.date.today() + _dt.timedelta(days=10)).isoformat()

            # ===== A: Web予約成功 → Reservation 1件 → RESERVATION_CREATED 1件 =====
            r = await client.post("/api/v1/reservations/create", json={
                "shop_id": shop_a, "reservation_date": f"{future_date}T12:00:00",
                "number_of_people": 2, "guest_name": "山田太郎", "guest_phone": "08011112222",
            })
            assert r.status_code in (200, 201), f"web booking failed: {r.status_code} {r.text}"
            web_reservation_id = r.json()["reservation_id"]

            r = await client.get(f"/api/v1/shops/{shop_a}/notifications", headers=owner_a)
            assert r.status_code == 200, f"list notifications failed: {r.status_code} {r.text}"
            body = r.json()
            matching = [i for i in body["items"] if i["related_entity_id"] == web_reservation_id]
            assert len(matching) == 1, f"Web予約のRESERVATION_CREATED通知が1件ではありません: {matching}"
            assert matching[0]["event_type"] == "RESERVATION_CREATED"
            assert matching[0]["customer_name"] == "山田太郎"
            print("A. Web予約成功 → Reservation 1件 → RESERVATION_CREATED通知 1件: OK")

            # ===== B: 重複防止（同一Reservationへ2回生成試行） =====
            await notify_reservation_created(
                shop_id=shop_a, reservation_id=web_reservation_id,
                guest_name="山田太郎", reservation_date=_dt.datetime.now(), number_of_people=2,
            )
            async with AsyncSessionLocal() as session:
                count = (await session.execute(
                    select(func.count()).select_from(OwnerNotificationEvent).filter(
                        OwnerNotificationEvent.related_entity_id == web_reservation_id,
                        OwnerNotificationEvent.event_type == "RESERVATION_CREATED",
                    )
                )).scalar_one()
            assert count == 1, f"重複防止に失敗し、同一予約の通知が{count}件生成されています"
            print("B. 同一Reservationへの重複通知生成防止（DB一意インデックス）: OK")

            # ===== C: AI Voice経由のcreate-reservation Tool =====
            call_id = "smoke-n1-call-" + str(uuid.uuid4())
            r = await client.post(
                f"/api/v1/shops/{shop_a}/realtime-voice/tools/create-reservation",
                json={
                    "date": future_date, "time": "13:00", "party_size": 3,
                    "guest_name": "鈴木花子", "guest_phone": "08033334444",
                    "call_id": call_id,
                },
            )
            assert r.status_code == 200, f"voice create-reservation failed: {r.status_code} {r.text}"
            voice_result = r.json()
            assert voice_result["success"] is True, f"voice create-reservation not success: {voice_result}"
            voice_reservation_id = voice_result["reservation_id"]

            r = await client.get(f"/api/v1/shops/{shop_a}/notifications?limit=100", headers=owner_a)
            assert r.status_code == 200
            matching = [i for i in r.json()["items"] if i["related_entity_id"] == voice_reservation_id]
            assert len(matching) == 1 and matching[0]["event_type"] == "RESERVATION_CREATED", (
                f"AI Voice予約のRESERVATION_CREATED通知が正しく1件生成されていません: {matching}"
            )
            print("C. AI Voice経由のcreate-reservation ToolでもRESERVATION_CREATED通知が1件生成される"
                  "（単一のcreate_reservation()経由であることの確認）: OK")

            # ===== D: request-callback Tool (Human Handoff) =====
            cb_call_id = "smoke-n1-callback-" + str(uuid.uuid4())
            r = await client.post(
                f"/api/v1/shops/{shop_a}/realtime-voice/tools/request-callback",
                json={
                    "customer_name": "佐藤次郎", "customer_phone": "08055556666",
                    "inquiry_text": "本日の空き状況について、通常の判定では答えられない込み入った内容の確認依頼です。",
                    "reason_code": "availability_judgement_required",
                    "call_id": cb_call_id,
                },
            )
            assert r.status_code == 200, f"request-callback failed: {r.status_code} {r.text}"
            assert r.json()["success"] is True

            async with AsyncSessionLocal() as session:
                cr = (await session.execute(
                    select(CallbackRequest).filter(
                        CallbackRequest.idempotency_key == f"realtime_voice:{shop_a}:{cb_call_id}"
                    )
                )).scalar_one()
                callback_request_id = cr.id

            r = await client.get(f"/api/v1/shops/{shop_a}/notifications?limit=100", headers=owner_a)
            matching = [i for i in r.json()["items"] if i["related_entity_id"] == callback_request_id]
            assert len(matching) == 1 and matching[0]["event_type"] == "OWNER_ACTION_REQUIRED", (
                f"CallbackRequestのOWNER_ACTION_REQUIRED通知が正しく1件生成されていません: {matching}"
            )
            assert matching[0]["priority"] == "ACTION_REQUIRED"
            action_required_notification_id = matching[0]["id"]
            print("D. request-callback Tool(Human Handoff) → OWNER_ACTION_REQUIRED通知 1件（priority=ACTION_REQUIRED）: OK")

            # K(runtime): inquiry_textの生テキストがメッセージに含まれないこと
            assert "込み入った内容" not in matching[0]["message"] and "本日の空き状況について" not in matching[0]["message"], (
                "CallbackRequestのinquiry_text（お客様の生のお問い合わせ内容）が通知メッセージに漏洩しています"
            )
            print("K(runtime). 通知メッセージにinquiry_textの生テキストが含まれない（reason_codeベースの一般文言のみ）: OK")

            # ===== E: 重複防止（同一CallbackRequestへ2回生成試行） =====
            await notify_callback_requested(
                shop_id=shop_a, callback_request_id=callback_request_id,
                reason_code="availability_judgement_required", customer_name="佐藤次郎",
            )
            async with AsyncSessionLocal() as session:
                count = (await session.execute(
                    select(func.count()).select_from(OwnerNotificationEvent).filter(
                        OwnerNotificationEvent.related_entity_id == callback_request_id,
                        OwnerNotificationEvent.event_type == "OWNER_ACTION_REQUIRED",
                    )
                )).scalar_one()
            assert count == 1, f"重複防止に失敗し、同一CallbackRequestの通知が{count}件生成されています"
            print("E. 同一CallbackRequestへの重複通知生成防止（DB一意インデックス）: OK")

            # ===== F: 他tenantによる一覧取得 → 403 =====
            r = await client.get(f"/api/v1/shops/{shop_a}/notifications", headers=owner_b)
            assert r.status_code == 403, f"他tenantの一覧取得が403になっていません: {r.status_code} {r.text}"
            print("F. 他tenantオーナーによる notification list → 403: OK")

            # ===== G: 他tenantによる詳細取得 → 403 =====
            r = await client.get(
                f"/api/v1/shops/{shop_a}/notifications/{action_required_notification_id}", headers=owner_b
            )
            assert r.status_code == 403, f"他tenantの詳細取得が403になっていません: {r.status_code} {r.text}"
            print("G. 他tenantオーナーによる notification detail → 403: OK")

            # ===== H: 未認証 → 401/403 =====
            r = await client.get(f"/api/v1/shops/{shop_a}/notifications")
            assert r.status_code in (401, 403), f"未認証アクセスが401/403になっていません: {r.status_code}"
            print("H. 未認証での notification list → 401/403: OK")

            # ===== I: phone/emailが一覧レスポンスに一切含まれない =====
            r = await client.get(f"/api/v1/shops/{shop_a}/notifications?limit=100", headers=owner_a)
            raw = r.text
            assert "08011112222" not in raw and "08033334444" not in raw and "08055556666" not in raw, (
                "notification一覧レスポンスに顧客の電話番号が含まれています"
            )
            assert "owner-n1-a@example.com" not in raw and "owner-n1-b@example.com" not in raw, (
                "notification一覧レスポンスにオーナーのメールアドレスが含まれています"
            )
            for item in r.json()["items"]:
                assert "customer_phone" not in item and "customer_email" not in item and "phone" not in item, (
                    f"notification一覧のアイテムにphone系フィールドが含まれています: {item.keys()}"
                )
            print("I. notification一覧レスポンスにphone/emailが一切含まれない: OK")

            # ===== J: CustomerMemoryを一切参照していない（ソースレベル） =====
            owner_notifications_src = open(
                os.path.join(REPO_ROOT, "app", "services", "owner_notifications.py"), encoding="utf-8"
            ).read()
            code_lines_for_j = [
                l for l in owner_notifications_src.splitlines()
                if "CustomerMemory" in l and "生テキスト" not in l and not l.strip().startswith("#")
            ]
            assert not code_lines_for_j, (
                f"owner_notifications.pyが実コード上でCustomerMemoryを参照しています（プライバシー方針違反）: {code_lines_for_j}"
            )
            assert "from app.models.customer_memory" not in owner_notifications_src, (
                "owner_notifications.pyがCustomerMemoryモデルをimportしています（プライバシー方針違反）"
            )
            print("J. 通知メッセージ生成ロジックがCustomerMemoryモデルを実コード上で一切参照していない（ソースレベル）: OK")

            # ===== K(ソースレベル): inquiry_textを一切引用していない =====
            code_lines = [
                l for l in owner_notifications_src.splitlines()
                if "inquiry_text" in l and not l.strip().startswith("#")
            ]
            # docstring内の説明文（"inquiry_textの生テキスト・CustomerMemory..."という
            # 注意書き）を除き、実コード（変数参照・関数引数など）としては一切登場しないこと
            code_lines = [l for l in code_lines if "生テキスト" not in l]
            assert not code_lines, (
                f"owner_notifications.pyがinquiry_textをコード上で直接引用しています（プライバシー方針違反）: {code_lines}"
            )
            router_src = open(
                os.path.join(REPO_ROOT, "app", "routers", "realtime_voice.py"), encoding="utf-8"
            ).read()
            assert re.search(
                r"notify_callback_requested\(\s*shop_id=shop_id,\s*callback_request_id=callback_request\.id,"
                r"\s*reason_code=reason_code,\s*customer_name=request\.customer_name,\s*\)",
                router_src,
            ), "notify_callback_requested()の呼び出しがinquiry_textを渡さない形になっていません"
            print("K(ソースレベル). notify_callback_requested()呼び出しがinquiry_textを一切渡していない: OK")

            # ===== L: 既読化はownerのみ成功 =====
            r = await client.patch(
                f"/api/v1/shops/{shop_a}/notifications/{action_required_notification_id}/read"
            )
            assert r.status_code in (401, 403), f"未認証での既読化が401/403になっていません: {r.status_code}"

            r = await client.patch(
                f"/api/v1/shops/{shop_a}/notifications/{action_required_notification_id}/read",
                headers=owner_b,
            )
            assert r.status_code == 403, f"他tenantオーナーによる既読化が403になっていません: {r.status_code}"

            r = await client.patch(
                f"/api/v1/shops/{shop_a}/notifications/{action_required_notification_id}/read",
                headers=owner_a,
            )
            assert r.status_code == 200, f"所有オーナーによる既読化が失敗しました: {r.status_code} {r.text}"
            detail = r.json()
            assert detail["is_read"] is True and detail["read_at"] is not None
            print("L. 既読化(mark read)は所有オーナーのみ成功し、他tenant/未認証では失敗する: OK")

            # ===== N9(safety): 通知生成が内部で失敗しても例外が外へ伝播しない =====
            import app.services.owner_notifications as owner_notif_module

            class _BoomSessionFactory:
                def __call__(self):
                    raise RuntimeError("simulated DB session failure (smoke test)")

            original_factory = owner_notif_module.db_module.AsyncSessionLocal
            owner_notif_module.db_module.AsyncSessionLocal = _BoomSessionFactory()
            try:
                await notify_reservation_created(
                    shop_id=shop_a, reservation_id="does-not-matter-" + str(uuid.uuid4()),
                    guest_name="test", reservation_date=_dt.datetime.now(), number_of_people=1,
                )
                await notify_callback_requested(
                    shop_id=shop_a, callback_request_id="does-not-matter-" + str(uuid.uuid4()),
                    reason_code="other", customer_name="test",
                )
            finally:
                owner_notif_module.db_module.AsyncSessionLocal = original_factory
            print("N9(safety). notify_reservation_created/notify_callback_requestedは内部でDBセッション生成が"
                  "失敗しても例外を外へ伝播しない（呼び出し元の予約作成/CallbackRequest保存の成功に一切影響しない）: OK")

            # ===== 回帰確認: Reservation自体・CallbackRequest自体は無傷 =====
            r = await client.get(f"/api/v1/reservations/{web_reservation_id}", headers=owner_a)
            assert r.status_code == 200, "(回帰) Reservation詳細取得に問題があります"
            r = await client.get(f"/api/v1/shops/{shop_a}/callback-requests/{callback_request_id}", headers=owner_a)
            assert r.status_code == 200, "(回帰) CallbackRequest詳細取得に問題があります"
            print("(回帰) Reservation/CallbackRequest本体の既存APIは無傷: OK")

    print()
    print("全テストOK: Phase N1（統一Owner Notification基盤）— Web予約/AI音声予約/Human Handoffからの"
          "通知イベント生成・重複防止・オーナー認証・tenant分離・プライバシー保護を確認")


if __name__ == "__main__":
    asyncio.run(main())
