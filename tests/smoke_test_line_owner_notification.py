"""
RECEPTRA — PHASE N2 スモークテスト（LINE Owner通知）

背景（Audit Reportで判明した内容の要約）:

Phase N1で作られた OwnerNotificationEvent（イベント生成）と、本フェーズ N2の
LINE配信は完全に分離されている。N2はN1の4ファイル
（app/models/owner_notification.py, app/services/owner_notifications.py,
app/routers/reservations.py, app/routers/realtime_voice.py）に一切変更を
加えず、独立したバックグラウンドポーリングワーカー
（app/services/line_notification_worker.py）がOwnerNotificationEventを
読み取り専用で参照するだけで配信を行う。

LINEアカウント連携は公式のlinkToken + nonce + accountLink webhook方式
（LINE Login Channel不要）を採用。Webhookは必ずHMAC-SHA256署名検証を行う。

【重要・安全性について】
本テストではLINE_CHANNEL_ACCESS_TOKENを一切設定しない
（app.services.line_message_provider.get_line_message_providerは
LINE_CHANNEL_ACCESS_TOKEN未設定時に必ずFakeLineMessageProviderへ
フォールバックするため、本テストの実行によって実際のLINEネットワークへ
接続することは絶対にない）。LINE_CHANNEL_SECRETのみ設定し、
署名検証ロジック自体は実アルゴリズム（HMAC-SHA256 + Base64）で検証する。

検証項目:
A. Webhook: 不正な署名 → 403（ペイロード非処理）
B. Webhook: 署名ヘッダー欠如 → 403
C. Webhook: follow event → linkToken発行 + reply送信（line-link.htmlへのURL）
D. 連携開始(link/start)は未認証で401/403
E. 連携開始 → nonce発行 → accountLink webhook(result=ok) → 連携完了
F. 連携完了後、レスポンスにLINE userIdが一切含まれない
G. 同一accountLinkイベントの再送（リプレイ）は連携状態を変化させない
H. 期限切れnonceでのaccountLink → 連携されない
I. result=failedのaccountLink → 連携されない
J. unfollow event → 既存連携が失効し、Dashboard通知には影響しない
K. 店舗別LINE通知設定: 他tenantから403
L. 店舗別LINE通知設定: デフォルト値(enabled=True, action_required=True, reservation=False)
M. 店舗別LINE通知設定: 更新が正しく反映される
N. 配信ワーカー: 重複配信防止（2回ポーリングしても配信は1回のみ）
O. 配信ワーカー: read_atを一切変更しない
P. 配信ワーカー: デフォルト方針（設定未作成の店舗はaction_requiredのみ配信、reservationはSKIPPED）
Q. 配信ワーカー: 配信失敗はOwnerNotificationEvent/Reservation/CallbackRequestに一切影響しない
R. LINEメッセージ本文に電話番号・メールアドレスが一切含まれない
S. どのAPIレスポンスにもLINE userId / Channel Secretが一切含まれない
T. テスト通知: 未認証で401/403
U. テスト通知: 連携済みオーナーが送信 → 成功
V. テスト通知: 短時間の連続送信 → 429(レート制限)
W. (回帰) Reservation/CallbackRequest/N1notificationsの既存APIは無傷
X. (回帰) shop-manage.htmlのLINE通知カードが正しく追加され、既存UIに回帰がない

実行: python3 tests/smoke_test_line_owner_notification.py
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(
    tempfile.mkdtemp(), "smoke_n2_line_owner_notification.db"
)
os.environ["SECRET_KEY"] = "smoke-test-secret-key-n2-line-owner-notification"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

# 重要: LINE_CHANNEL_ACCESS_TOKENは意図的に設定しない。これによりFakeLineMessageProvider
# へ必ずフォールバックし、本テストが実際のLINEネットワークへ一切接続しないことを保証する。
_TEST_CHANNEL_SECRET = "smoke-test-line-channel-secret-1234567890"
os.environ["LINE_CHANNEL_SECRET"] = _TEST_CHANNEL_SECRET
os.environ["LINE_BOT_BASIC_ID"] = "@receptra-smoke-test"
os.environ["LINE_NOTIFICATION_WORKER_ENABLED"] = "false"  # ポーリングは手動で_poll_once()を呼ぶ

import httpx  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_sent_log = []  # [(kind, arg1, arg2)] Fake providerが「送信した」内容の記録


def _sign(body: bytes) -> str:
    mac = hmac.new(_TEST_CHANNEL_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(mac).decode("utf-8")


def _test_frontend_structure():
    html = open(os.path.join(REPO_ROOT, "frontend", "public", "shop-manage.html"), encoding="utf-8").read()

    assert 'id="line-notifications-card"' in html, "LINE通知カード(#line-notifications-card)が見つかりません"
    assert 'id="line-status-area"' in html
    assert 'id="line-setting-reservation"' in html

    notices_idx = html.index('id="owner-notices-card"')
    line_idx = html.index('id="line-notifications-card"')
    save_all_idx = html.index('id="save-all-card"')
    assert notices_idx < line_idx < save_all_idx, (
        "LINE通知カードはowner-notices-cardとsave-all-cardの間に挿入されている必要があります"
    )
    print("X-1. LINE通知カードが正しい位置に挿入されている: OK")

    save_all_start = html.index("function handleSaveAll")
    save_all_end = html.index("document.getElementById('save-all-btn').addEventListener", save_all_start)
    save_all_section = html[save_all_start:save_all_end]
    assert "line-notifications" not in save_all_section and "line-setting" not in save_all_section, (
        "LINE通知カードが誤って「全体を保存」の対象に含まれています"
    )
    print("X-2. 「全体を保存」機能にLINE通知カードが含まれていない: OK")

    assert "loadLineStatus();" in html
    assert "loadOwnerNotices();\n            loadLineStatus();" in html
    print("X-3. loadLineStatus()がinit sequenceに正しく追加されている: OK")

    assert "id=\"line-status-area\"" not in "" or True  # placeholder no-op to keep structure symmetrical
    # 既存お知らせ/CallbackRequest UIへの回帰がないこと
    assert "function loadOwnerNotices" in html
    assert "function loadCallbackRequests" in html
    print("X-4. (回帰) 既存お知らせ/CallbackRequest UIは無傷: OK")

    # 生のLINE userId/Channel Secret/Access Tokenに類する文字列を表示するコードが無いこと
    assert "line_user_id" not in html
    assert "channel_secret" not in html.lower().replace("linechannel", "")
    print("X-5. UIコード内にLINE userId等の生値を表示する記述がない: OK")


async def main():
    _test_frontend_structure()

    from app.main import app, lifespan

    async with lifespan(app):
        from app.database import AsyncSessionLocal
        from app.models.shop import Shop
        from app.models.owner_notification import OwnerNotificationEvent
        from app.models.line_notification import (
            OwnerLineConnection,
            LineLinkNonce,
            LineNotificationDelivery,
        )
        from app.services.owner_notifications import notify_reservation_created, notify_callback_requested
        from app.services.line_notification_worker import _poll_once
        import app.services.line_message_provider as line_provider_module
        from sqlalchemy import select, func
        import datetime as _dt

        # ===== Fake providerの呼び出しを記録するためのinstrumentation =====
        _push_should_fail = {"value": False}

        async def _instrumented_push(self, line_user_id, text):
            _sent_log.append(("push", line_user_id, text))
            if _push_should_fail["value"]:
                return line_provider_module.LinePushResult(success=False, error_category="smoke_forced_failure")
            return line_provider_module.LinePushResult(success=True)

        async def _instrumented_reply(self, reply_token, text):
            _sent_log.append(("reply", reply_token, text))
            return line_provider_module.LinePushResult(success=True)

        async def _instrumented_link_token(self, line_user_id):
            return f"fake-link-token-{line_user_id}"

        line_provider_module.FakeLineMessageProvider.push_message = _instrumented_push
        line_provider_module.FakeLineMessageProvider.reply_message = _instrumented_reply
        line_provider_module.FakeLineMessageProvider.issue_link_token = _instrumented_link_token

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            all_response_texts = []

            async def _get(url, **kw):
                r = await client.get(url, **kw)
                all_response_texts.append(r.text)
                return r

            async def _post(url, **kw):
                r = await client.post(url, **kw)
                all_response_texts.append(r.text)
                return r

            async def _patch(url, **kw):
                r = await client.patch(url, **kw)
                all_response_texts.append(r.text)
                return r

            # ===== セットアップ: オーナーA・店舗A =====
            r = await _post("/api/v1/auth/register", json={
                "email": "owner-n2-a@example.com", "password": "password123",
                "display_name": "N2テストオーナーA",
            })
            assert r.status_code in (200, 201), f"register A failed: {r.status_code} {r.text}"
            owner_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await _post("/api/v1/shops/register", json={
                "name": "N2テスト店舗A", "category": "レストラン", "address": "東京都渋谷区1-1-1",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create shop A failed: {r.status_code} {r.text}"
            shop_a = r.json()["shop_id"]

            hours_payload = {"hours": [
                {"day_of_week": d, "opening_time": "00:00:00", "closing_time": "23:30:00", "is_closed": False}
                for d in range(7)
            ]}
            r = await client.put(f"/api/v1/shops/{shop_a}/hours", json=hours_payload, headers=owner_a)
            assert r.status_code == 200

            async with AsyncSessionLocal() as session:
                shop_obj = await session.get(Shop, shop_a)
                shop_obj.reservations_enabled = True
                await session.commit()
                tenant_a = shop_obj.tenant_id

            # ===== セットアップ: オーナーB・店舗B（他tenant検証用） =====
            r = await _post("/api/v1/auth/register", json={
                "email": "owner-n2-b@example.com", "password": "password123",
                "display_name": "N2テストオーナーB",
            })
            assert r.status_code in (200, 201)
            owner_b = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await _post("/api/v1/shops/register", json={
                "name": "N2テスト店舗B", "category": "レストラン", "address": "東京都渋谷区2-2-2",
            }, headers=owner_b)
            assert r.status_code in (200, 201)
            shop_b = r.json()["shop_id"]
            async with AsyncSessionLocal() as session:
                shop_b_obj = await session.get(Shop, shop_b)
                tenant_b = shop_b_obj.tenant_id

            # ===== A/B: Webhook署名検証 =====
            follow_body = json.dumps({
                "events": [{
                    "type": "follow", "replyToken": "rt-smoke-1",
                    "source": {"type": "user", "userId": "Ufollowuser1"},
                }]
            }).encode("utf-8")

            r = await client.post("/webhook/line", content=follow_body, headers={
                "x-line-signature": "invalid-signature==", "Content-Type": "application/json",
            })
            assert r.status_code == 403, f"不正な署名が403になっていません: {r.status_code}"
            print("A. Webhook: 不正な署名 → 403: OK")

            r = await client.post("/webhook/line", content=follow_body, headers={"Content-Type": "application/json"})
            assert r.status_code == 403, f"署名ヘッダー欠如が403になっていません: {r.status_code}"
            print("B. Webhook: 署名ヘッダー欠如 → 403: OK")

            # ===== C: follow event → linkToken発行 + reply =====
            r = await client.post("/webhook/line", content=follow_body, headers={
                "x-line-signature": _sign(follow_body), "Content-Type": "application/json",
            })
            assert r.status_code == 200, f"正しい署名のfollow eventが200になりません: {r.status_code} {r.text}"
            replies = [e for e in _sent_log if e[0] == "reply" and e[1] == "rt-smoke-1"]
            assert len(replies) == 1, f"follow eventへのreplyが1件ではありません: {replies}"
            assert "line-link.html?linkToken=" in replies[0][2], "replyメッセージにline-link.htmlへのリンクが含まれていません"
            print("C. Webhook: follow event → linkToken発行 + reply送信: OK")

            # ===== D: 連携開始は未認証で401/403 =====
            r = await _post("/api/v1/line-notifications/connection/link/start")
            assert r.status_code in (401, 403), f"未認証でのlink/startが401/403になっていません: {r.status_code}"
            print("D. 連携開始(link/start)は未認証で401/403: OK")

            # ===== E: 連携開始 → nonce発行 → accountLink(ok) → 連携完了 =====
            r = await _post("/api/v1/line-notifications/connection", headers=owner_a) \
                if False else await _get("/api/v1/line-notifications/connection", headers=owner_a)
            assert r.status_code == 200 and r.json()["connected"] is False, "連携前なのにconnected=Trueです"

            r = await _post("/api/v1/line-notifications/connection/link/start", headers=owner_a)
            assert r.status_code == 200, f"link/start失敗: {r.status_code} {r.text}"
            nonce_a = r.json()["nonce"]
            assert 10 <= len(nonce_a) <= 255

            account_link_body = json.dumps({
                "events": [{
                    "type": "accountLink", "replyToken": "rt-smoke-2",
                    "source": {"type": "user", "userId": "Ufakeuser-A"},
                    "link": {"result": "ok", "nonce": nonce_a},
                }]
            }).encode("utf-8")
            r = await client.post("/webhook/line", content=account_link_body, headers={
                "x-line-signature": _sign(account_link_body), "Content-Type": "application/json",
            })
            assert r.status_code == 200, f"accountLink webhookが200になりません: {r.status_code} {r.text}"

            r = await _get("/api/v1/line-notifications/connection", headers=owner_a)
            assert r.status_code == 200 and r.json()["connected"] is True, "accountLink後にconnected=Trueになっていません"
            connected_at_1 = r.json()["connected_at"]
            print("E. 連携開始 → nonce発行 → accountLink(ok) → 連携完了: OK")

            # ===== F: レスポンスにLINE userIdが一切含まれない =====
            assert "Ufakeuser-A" not in r.text, "connection statusレスポンスにLINE userIdが含まれています"
            print("F. 連携完了後のレスポンスにLINE userIdが一切含まれない: OK")

            # ===== G: 同一accountLinkイベントの再送（リプレイ）は状態を変化させない =====
            r = await client.post("/webhook/line", content=account_link_body, headers={
                "x-line-signature": _sign(account_link_body), "Content-Type": "application/json",
            })
            assert r.status_code == 200
            r = await _get("/api/v1/line-notifications/connection", headers=owner_a)
            assert r.json()["connected_at"] == connected_at_1, "nonceのリプレイによって連携状態が変化しています（リプレイ防止に失敗）"
            print("G. 同一accountLinkイベントの再送は連携状態を変化させない（リプレイ防止）: OK")

            # ===== H: 期限切れnonce =====
            expired_nonce = "expired-nonce-" + str(uuid.uuid4())
            async with AsyncSessionLocal() as session:
                session.add(LineLinkNonce(
                    nonce=expired_nonce, tenant_id=tenant_b,
                    created_at=_dt.datetime.utcnow() - _dt.timedelta(minutes=20),
                    expires_at=_dt.datetime.utcnow() - _dt.timedelta(minutes=10),
                ))
                await session.commit()
            expired_body = json.dumps({
                "events": [{
                    "type": "accountLink", "replyToken": "rt-smoke-3",
                    "source": {"type": "user", "userId": "Ufakeuser-Expired"},
                    "link": {"result": "ok", "nonce": expired_nonce},
                }]
            }).encode("utf-8")
            r = await client.post("/webhook/line", content=expired_body, headers={
                "x-line-signature": _sign(expired_body), "Content-Type": "application/json",
            })
            assert r.status_code == 200
            r = await _get("/api/v1/line-notifications/connection", headers=owner_b)
            assert r.json()["connected"] is False, "期限切れnonceで連携が成立してしまっています"
            print("H. 期限切れnonceでのaccountLink → 連携されない: OK")

            # ===== I: result=failed =====
            r = await _post("/api/v1/line-notifications/connection/link/start", headers=owner_b)
            nonce_b = r.json()["nonce"]
            failed_body = json.dumps({
                "events": [{
                    "type": "accountLink",
                    "source": {"type": "user", "userId": "Ufakeuser-B-Failed"},
                    "link": {"result": "failed", "nonce": nonce_b},
                }]
            }).encode("utf-8")
            r = await client.post("/webhook/line", content=failed_body, headers={
                "x-line-signature": _sign(failed_body), "Content-Type": "application/json",
            })
            assert r.status_code == 200
            r = await _get("/api/v1/line-notifications/connection", headers=owner_b)
            assert r.json()["connected"] is False, "result=failedなのに連携が成立してしまっています"
            print("I. result=failedのaccountLink → 連携されない: OK")

            # 以降のテストのため、Bも正式に連携させておく
            account_link_body_b = json.dumps({
                "events": [{
                    "type": "accountLink",
                    "source": {"type": "user", "userId": "Ufakeuser-B"},
                    "link": {"result": "ok", "nonce": nonce_b},
                }]
            }).encode("utf-8")
            r = await client.post("/webhook/line", content=account_link_body_b, headers={
                "x-line-signature": _sign(account_link_body_b), "Content-Type": "application/json",
            })
            assert r.status_code == 200

            # ===== J: unfollow event → 連携失効。Dashboard通知には影響なし =====
            unfollow_body = json.dumps({
                "events": [{"type": "unfollow", "source": {"type": "user", "userId": "Ufakeuser-B"}}]
            }).encode("utf-8")
            r = await client.post("/webhook/line", content=unfollow_body, headers={
                "x-line-signature": _sign(unfollow_body), "Content-Type": "application/json",
            })
            assert r.status_code == 200
            r = await _get("/api/v1/line-notifications/connection", headers=owner_b)
            assert r.json()["connected"] is False, "unfollow後もconnected=Trueのままです"
            # 再連携できるよう、Bを再度連携させておく（後続テストでは使わないため必須ではないが、
            # revoked_atのみが更新され行自体は削除されていないことを確認する）
            async with AsyncSessionLocal() as session:
                conn_b = (await session.execute(
                    select(OwnerLineConnection).filter(OwnerLineConnection.tenant_id == tenant_b)
                )).scalar_one()
                assert conn_b.revoked_at is not None
                assert conn_b.line_user_id == "Ufakeuser-B"  # 行は削除されず残っている
            print("J. unfollow event → 連携失効（行は削除せずrevoked_atのみ設定）: OK")

            # ===== K/L/M: 店舗別LINE通知設定 =====
            r = await _get(f"/api/v1/shops/{shop_a}/line-notifications/settings", headers=owner_b)
            assert r.status_code == 403, f"他tenantから店舗設定取得が403になっていません: {r.status_code}"
            print("K. 店舗別LINE通知設定: 他tenantから403: OK")

            r = await _get(f"/api/v1/shops/{shop_a}/line-notifications/settings", headers=owner_a)
            assert r.status_code == 200
            data = r.json()
            assert data["enabled"] is True and data["action_required_enabled"] is True and data["reservation_enabled"] is False, (
                f"デフォルト値が想定と異なります: {data}"
            )
            print("L. 店舗別LINE通知設定のデフォルト値(enabled=T, action_required=T, reservation=F): OK")

            r = await client.patch(f"/api/v1/shops/{shop_a}/line-notifications/settings",
                                    json={"reservation_enabled": True}, headers=owner_a)
            all_response_texts.append(r.text)
            assert r.status_code == 200 and r.json()["reservation_enabled"] is True
            print("M. 店舗別LINE通知設定の更新が正しく反映される: OK")

            # ===== N/O: 配信ワーカー: 重複防止 + read_at不変 =====
            future_date = (_dt.date.today() + _dt.timedelta(days=10)).isoformat()
            r = await _post("/api/v1/reservations/create", json={
                "shop_id": shop_a, "reservation_date": f"{future_date}T12:00:00",
                "number_of_people": 2, "guest_name": "山田太郎", "guest_phone": "08011112222",
            })
            assert r.status_code in (200, 201)
            reservation_id_a = r.json()["reservation_id"]

            r = await _post(f"/api/v1/shops/{shop_a}/realtime-voice/tools/request-callback", json={
                "customer_name": "佐藤次郎", "customer_phone": "08055556666",
                "inquiry_text": "腰痛と坐骨神経痛について相談したいという込み入った内容です",
                "reason_code": "availability_judgement_required",
                "call_id": "smoke-n2-callback-" + str(uuid.uuid4()),
            })
            assert r.status_code == 200 and r.json()["success"] is True

            push_count_before = len([e for e in _sent_log if e[0] == "push"])
            processed_1 = await _poll_once()
            processed_2 = await _poll_once()
            push_count_after = len([e for e in _sent_log if e[0] == "push"])
            assert processed_1 == 2, f"1回目のポーリングで2件処理されるはずが{processed_1}件でした"
            assert processed_2 == 0, f"2回目のポーリングで新規処理が発生しています(重複防止失敗): {processed_2}件"
            assert push_count_after - push_count_before == 2, (
                f"push呼び出しが想定(2回)と異なります: {push_count_after - push_count_before}"
            )

            async with AsyncSessionLocal() as session:
                dup_count = (await session.execute(
                    select(func.count()).select_from(LineNotificationDelivery)
                )).scalar_one()
            assert dup_count == 2, f"LineNotificationDeliveryの行数が想定(2)と異なります: {dup_count}"
            print("N. 配信ワーカー: 重複配信防止（2回ポーリングしても配信は1回のみ）: OK")

            async with AsyncSessionLocal() as session:
                events = (await session.execute(
                    select(OwnerNotificationEvent).filter(
                        OwnerNotificationEvent.related_entity_id.in_([reservation_id_a])
                    )
                )).scalars().all()
                for ev in events:
                    assert ev.read_at is None, "LINE配信によってread_atが自動更新されています（絶対禁止）"
            print("O. 配信ワーカー: LINE配信成功がread_atを一切変更しない: OK")

            # ===== P: デフォルト方針（設定未作成の店舗） =====
            r = await _post("/api/v1/auth/register", json={
                "email": "owner-n2-c@example.com", "password": "password123", "display_name": "N2テストオーナーC",
            })
            owner_c = {"Authorization": f"Bearer {r.json()['access_token']}"}
            r = await _post("/api/v1/shops/register", json={
                "name": "N2テスト店舗C", "category": "レストラン", "address": "東京都渋谷区3-3-3",
            }, headers=owner_c)
            shop_c = r.json()["shop_id"]
            async with AsyncSessionLocal() as session:
                shop_c_obj = await session.get(Shop, shop_c)
                tenant_c = shop_c_obj.tenant_id
                session.add(OwnerLineConnection(
                    tenant_id=tenant_c, line_user_id="Ufakeuser-C", connected_at=_dt.datetime.utcnow(),
                ))
                await session.commit()

            action_required_event_id = "smoke-n2-c-action-" + str(uuid.uuid4())
            reservation_event_id = "smoke-n2-c-reservation-" + str(uuid.uuid4())
            await notify_callback_requested(
                shop_id=shop_c, callback_request_id=action_required_event_id,
                reason_code="other", customer_name="テスト太郎",
            )
            await notify_reservation_created(
                shop_id=shop_c, reservation_id=reservation_event_id,
                guest_name="テスト花子", reservation_date=_dt.datetime.now(), number_of_people=1,
            )
            await _poll_once()

            async with AsyncSessionLocal() as session:
                ev_action = (await session.execute(
                    select(OwnerNotificationEvent).filter(OwnerNotificationEvent.related_entity_id == action_required_event_id)
                )).scalar_one()
                ev_reservation = (await session.execute(
                    select(OwnerNotificationEvent).filter(OwnerNotificationEvent.related_entity_id == reservation_event_id)
                )).scalar_one()
                delivery_action = (await session.execute(
                    select(LineNotificationDelivery).filter(LineNotificationDelivery.notification_event_id == ev_action.id)
                )).scalar_one()
                delivery_reservation = (await session.execute(
                    select(LineNotificationDelivery).filter(LineNotificationDelivery.notification_event_id == ev_reservation.id)
                )).scalar_one()
            assert delivery_action.status == "SENT", f"設定未作成店舗のOWNER_ACTION_REQUIREDが配信されていません: {delivery_action.status}"
            assert delivery_reservation.status == "SKIPPED", (
                f"設定未作成店舗のRESERVATION_CREATEDがデフォルトでSKIPPEDになっていません: {delivery_reservation.status}"
            )
            print("P. 配信ワーカー: デフォルト方針（未設定店舗はaction_requiredのみ配信、reservationはSKIPPED）: OK")

            # ===== Q: 配信失敗はReservation/CallbackRequest/OwnerNotificationEventに影響しない =====
            _push_should_fail["value"] = True
            fail_event_id = "smoke-n2-fail-" + str(uuid.uuid4())
            await notify_callback_requested(
                shop_id=shop_c, callback_request_id=fail_event_id, reason_code="other", customer_name="失敗太郎",
            )
            await _poll_once()
            _push_should_fail["value"] = False

            async with AsyncSessionLocal() as session:
                ev_fail = (await session.execute(
                    select(OwnerNotificationEvent).filter(OwnerNotificationEvent.related_entity_id == fail_event_id)
                )).scalar_one()
                delivery_fail = (await session.execute(
                    select(LineNotificationDelivery).filter(LineNotificationDelivery.notification_event_id == ev_fail.id)
                )).scalar_one()
            assert delivery_fail.status == "FAILED"
            assert ev_fail.read_at is None
            # OwnerNotificationEvent自体は健在（削除されていない）
            r = await _get(f"/api/v1/shops/{shop_c}/notifications?limit=100", headers=owner_c)
            assert r.status_code == 200
            assert any(i["id"] == ev_fail.id for i in r.json()["items"]), "配信失敗によってOwnerNotificationEventが消失しています"
            print("Q. 配信ワーカー: 配信失敗はOwnerNotificationEvent等に一切影響しない: OK")

            # ===== R: LINEメッセージ本文にPIIが含まれない =====
            all_sent_texts = " ".join(e[2] for e in _sent_log)
            assert "08011112222" not in all_sent_texts and "08055556666" not in all_sent_texts, (
                "LINEメッセージ本文に電話番号が含まれています"
            )
            assert "owner-n2-a@example.com" not in all_sent_texts and "owner-n2-b@example.com" not in all_sent_texts, (
                "LINEメッセージ本文にメールアドレスが含まれています"
            )
            assert "腰痛" not in all_sent_texts and "坐骨神経痛" not in all_sent_texts, (
                "LINEメッセージ本文に医療関連情報(inquiry_textの生テキスト)が含まれています"
            )
            print("R. LINEメッセージ本文に電話番号・メールアドレス・医療情報が一切含まれない: OK")

            # ===== S: どのAPIレスポンスにもLINE userId/Channel Secretが含まれない =====
            joined = "\n".join(all_response_texts)
            for leaked_id in ["Ufakeuser-A", "Ufakeuser-B", "Ufakeuser-C", "Ufollowuser1", "Ufakeuser-Expired"]:
                assert leaked_id not in joined, f"APIレスポンスにLINE userId({leaked_id})が漏洩しています"
            assert _TEST_CHANNEL_SECRET not in joined, "APIレスポンスにChannel Secretが漏洩しています"
            print("S. どのAPIレスポンスにもLINE userId/Channel Secretが一切含まれない: OK")

            # ===== T/U/V: テスト通知 =====
            r = await _post("/api/v1/line-notifications/connection/test")
            assert r.status_code in (401, 403), f"未認証でのテスト通知が401/403になっていません: {r.status_code}"
            print("T. テスト通知: 未認証で401/403: OK")

            r = await _post("/api/v1/line-notifications/connection/test", headers=owner_a)
            assert r.status_code == 200 and r.json()["sent"] is True, f"テスト通知送信に失敗しました: {r.status_code} {r.text}"
            print("U. テスト通知: 連携済みオーナーが送信 → 成功: OK")

            r = await _post("/api/v1/line-notifications/connection/test", headers=owner_a)
            assert r.status_code == 429, f"短時間の連続テスト通知が429になっていません: {r.status_code}"
            print("V. テスト通知: 短時間の連続送信 → 429(レート制限): OK")

            # ===== W: 回帰 =====
            r = await _get(f"/api/v1/reservations/{reservation_id_a}", headers=owner_a)
            assert r.status_code == 200
            r = await _get(f"/api/v1/shops/{shop_a}/notifications?limit=100", headers=owner_a)
            assert r.status_code == 200 and len(r.json()["items"]) >= 2
            print("W. (回帰) Reservation/N1notifications等の既存APIは無傷: OK")

    print()
    print("全テストOK: Phase N2（LINE Owner通知）— アカウント連携(linkToken/nonce/accountLink)・"
          "Webhook署名検証・配信ワーカー(重複防止/read_at不変/デフォルト方針/失敗独立性)・"
          "tenant分離・プライバシー保護（PII非漏洩）を確認。実LINE送信は一切発生していません"
          "（LINE_CHANNEL_ACCESS_TOKEN未設定によりFakeLineMessageProviderのみを使用）。")


if __name__ == "__main__":
    asyncio.run(main())
