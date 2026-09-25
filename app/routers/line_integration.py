"""
Phase N2: LINE Owner通知 — API・Webhook エンドポイント

3つのルーターに分ける:
- line_connection_router: テナント単位のLINE連携管理
  （/api/v1/line-notifications/connection/...）。オーナー認証必須。
  line_user_idは絶対にレスポンスへ含めない。
- line_shop_settings_router: 店舗単位の通知ON/OFF設定
  （/api/v1/shops/{shop_id}/line-notifications/settings）。
  app.routers.callback_requests と全く同じ owner-auth + tenant/shop分離パターン。
- line_webhook_router: LINEプラットフォームからのWebhook受信
  （/webhook/line）。app.routers.billing の payjp webhook と同じ命名規則
  （/webhook/{provider}）を踏襲しつつ、PAY.jpと異なりLINEは署名検証の
  仕組みがあるため必ず検証する。

いずれもPhase N1（app/models/owner_notification.py,
app/services/owner_notifications.py, app/routers/reservations.py,
app/routers/realtime_voice.py）には一切変更を加えない。読み取り専用で
OwnerNotificationEventを参照するのはapp/services/line_notification_worker.py
のみ。
"""

import json
import logging
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.deps import get_current_user, get_db
from app.models.line_notification import LineLinkNonce, OwnerLineConnection, ShopLineNotificationSetting
from app.models.shop import Shop
from app.schemas.line_notification import (
    LineConnectionStatus,
    LineFriendAddInfo,
    LineLinkStartResponse,
    LineTestNotificationResponse,
    ShopLineNotificationSettingItem,
    ShopLineNotificationSettingUpdate,
)
from app.schemas.user import CurrentUser
from app.services.line_message_provider import get_line_message_provider
from app.services.line_signature import verify_line_signature

logger = logging.getLogger("receptra.line.integration")

NONCE_TTL_MINUTES = 10
# テスト通知のレート制限（おおよそ3回/分に相当する最小間隔。DBに永続化するため、
# 複数インスタンス・再起動を跨いでも正しく機能する。in-memoryのカウンタは使わない）。
TEST_NOTIFICATION_MIN_INTERVAL_SECONDS = 20

_LINE_ADD_FRIEND_BASE = "https://line.me/R/ti/p/"


# ============================================================
# テナント単位のLINE連携管理
# ============================================================

line_connection_router = APIRouter(prefix="/api/v1/line-notifications", tags=["line-notifications"])


async def _get_connection(db: AsyncSession, tenant_id: str) -> OwnerLineConnection | None:
    result = await db.execute(
        select(OwnerLineConnection).filter(OwnerLineConnection.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()


@line_connection_router.get("/connection", response_model=LineConnectionStatus)
async def get_connection_status(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """line_user_idは絶対に返さない。連携済みかどうかのみ返す。"""
    connection = await _get_connection(db, current_user.tenant_id)
    if connection is None or connection.revoked_at is not None:
        return LineConnectionStatus(connected=False)
    return LineConnectionStatus(connected=True, connected_at=connection.connected_at)


@line_connection_router.get("/friend-add-info", response_model=LineFriendAddInfo)
async def get_friend_add_info(
    current_user: CurrentUser = Depends(get_current_user),
):
    """友だち追加用のQR/リンク生成に必要な公開情報。Basic IDは秘匿情報ではない
    （LINE公式アカウントの「友だち追加」を目的として一般公開される情報のため）。"""
    settings = get_settings()
    basic_id = (settings.LINE_BOT_BASIC_ID or "").strip()
    if not basic_id:
        return LineFriendAddInfo(configured=False)
    handle = basic_id if basic_id.startswith("@") else f"@{basic_id}"
    from urllib.parse import quote

    add_friend_url = f"{_LINE_ADD_FRIEND_BASE}{quote(handle)}"
    return LineFriendAddInfo(configured=True, basic_id=basic_id, add_friend_url=add_friend_url)


@line_connection_router.post("/connection/link/start", response_model=LineLinkStartResponse)
async def start_link(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """安全な乱数nonceを発行し、tenant_idに紐付けて保存する。
    フロントエンド(line-link.html)はこのnonceと、自身のURLクエリから得た
    linkTokenを使ってLINEのaccountLinkダイアログへリダイレクトする
    （linkToken自体はバックエンドを経由する必要がない。LINE側が検証する）。"""
    nonce = secrets.token_urlsafe(32)  # 43文字, 256bit相当。10〜255文字の範囲内。
    now = datetime.utcnow()
    db.add(
        LineLinkNonce(
            nonce=nonce,
            tenant_id=current_user.tenant_id,
            created_at=now,
            expires_at=now + timedelta(minutes=NONCE_TTL_MINUTES),
        )
    )
    await db.commit()
    return LineLinkStartResponse(nonce=nonce)


@line_connection_router.post("/connection/revoke")
async def revoke_connection(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    connection = await _get_connection(db, current_user.tenant_id)
    if connection is not None and connection.revoked_at is None:
        connection.revoked_at = datetime.utcnow()
        await db.commit()
    return {"revoked": True}


@line_connection_router.post("/connection/test", response_model=LineTestNotificationResponse)
async def send_test_notification(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """レート制限付きのテスト通知。自分自身の連携先にのみ送信可能
    （他テナントのline_user_idへは絶対に送れない設計 — current_user.tenant_id経由
    でのみ連携先を取得するため）。"""
    connection = await _get_connection(db, current_user.tenant_id)
    if connection is None or connection.revoked_at is not None:
        raise HTTPException(status_code=400, detail="LINE連携がまだ完了していません")

    now = datetime.utcnow()
    if connection.last_test_notification_at is not None:
        elapsed = (now - connection.last_test_notification_at).total_seconds()
        if elapsed < TEST_NOTIFICATION_MIN_INTERVAL_SECONDS:
            raise HTTPException(status_code=429, detail="テスト通知の送信間隔が短すぎます。しばらく待ってから再度お試しください")

    provider = get_line_message_provider()
    result = await provider.push_message(
        connection.line_user_id,
        "【テスト通知】RECEPTRAからのLINE通知は正常に届いています。",
    )
    connection.last_test_notification_at = now
    await db.commit()

    if result.success:
        return LineTestNotificationResponse(sent=True)
    return LineTestNotificationResponse(sent=False, reason=result.error_category)


# ============================================================
# 店舗単位の通知ON/OFF設定
# ============================================================

line_shop_settings_router = APIRouter(prefix="/api/v1/shops", tags=["line-notifications"])


async def _get_owned_shop(shop_id: str, current_user: CurrentUser, db: AsyncSession) -> Shop:
    """app.routers.callback_requests._get_owned_shop と全く同じチェック。"""
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")
    if shop.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="この店舗を操作する権限がありません")
    return shop


async def _get_or_create_shop_setting(db: AsyncSession, shop_id: str) -> ShopLineNotificationSetting:
    result = await db.execute(
        select(ShopLineNotificationSetting).filter(ShopLineNotificationSetting.shop_id == shop_id)
    )
    setting = result.scalar_one_or_none()
    if setting is None:
        setting = ShopLineNotificationSetting(shop_id=shop_id)
        db.add(setting)
        await db.commit()
        await db.refresh(setting)
    return setting


@line_shop_settings_router.get(
    "/{shop_id}/line-notifications/settings", response_model=ShopLineNotificationSettingItem
)
async def get_shop_line_settings(
    shop_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_shop(shop_id, current_user, db)
    setting = await _get_or_create_shop_setting(db, shop_id)
    return ShopLineNotificationSettingItem.model_validate(setting)


@line_shop_settings_router.patch(
    "/{shop_id}/line-notifications/settings", response_model=ShopLineNotificationSettingItem
)
async def update_shop_line_settings(
    shop_id: str,
    payload: ShopLineNotificationSettingUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_shop(shop_id, current_user, db)
    setting = await _get_or_create_shop_setting(db, shop_id)
    if payload.enabled is not None:
        setting.enabled = payload.enabled
    if payload.action_required_enabled is not None:
        setting.action_required_enabled = payload.action_required_enabled
    if payload.reservation_enabled is not None:
        setting.reservation_enabled = payload.reservation_enabled
    await db.commit()
    await db.refresh(setting)
    return ShopLineNotificationSettingItem.model_validate(setting)


# ============================================================
# LINE Webhook受信
# ============================================================

line_webhook_router = APIRouter(prefix="/webhook/line", tags=["line_webhook"])


async def _handle_follow_event(db: AsyncSession, event: dict) -> None:
    user_id = event.get("source", {}).get("userId")
    reply_token = event.get("replyToken")
    if not user_id or not reply_token:
        return
    provider = get_line_message_provider()
    link_token = await provider.issue_link_token(user_id)
    if not link_token:
        logger.warning("LINE follow event: linkToken発行に失敗しました")
        return
    link_url = f"https://receptra.bariyon.com/line-link.html?linkToken={link_token}"
    text = (
        "友だち追加ありがとうございます。\n"
        "以下のリンクからRECEPTRAアカウントとの連携を完了してください。\n"
        f"{link_url}\n"
        "（10分以内にご対応ください）"
    )
    await provider.reply_message(reply_token, text)


async def _handle_unfollow_event(db: AsyncSession, event: dict) -> None:
    user_id = event.get("source", {}).get("userId")
    if not user_id:
        return
    result = await db.execute(
        select(OwnerLineConnection).filter(
            OwnerLineConnection.line_user_id == user_id,
            OwnerLineConnection.revoked_at.is_(None),
        )
    )
    connection = result.scalar_one_or_none()
    if connection is not None:
        connection.revoked_at = datetime.utcnow()
        await db.commit()


async def _handle_account_link_event(db: AsyncSession, event: dict) -> None:
    link = event.get("link", {})
    if link.get("result") != "ok":
        logger.info("LINE accountLink event: result != ok のため無視します")
        return
    nonce_value = link.get("nonce")
    user_id = event.get("source", {}).get("userId")
    if not nonce_value or not user_id:
        return

    now = datetime.utcnow()
    result = await db.execute(select(LineLinkNonce).filter(LineLinkNonce.nonce == nonce_value))
    nonce_row = result.scalar_one_or_none()
    if nonce_row is None or nonce_row.consumed_at is not None or nonce_row.expires_at < now:
        logger.warning("LINE accountLink event: nonceが無効・期限切れ・使用済みのため連携を拒否しました")
        return

    nonce_row.consumed_at = now

    conn_result = await db.execute(
        select(OwnerLineConnection).filter(OwnerLineConnection.tenant_id == nonce_row.tenant_id)
    )
    connection = conn_result.scalar_one_or_none()
    if connection is None:
        connection = OwnerLineConnection(
            tenant_id=nonce_row.tenant_id,
            line_user_id=user_id,
            connected_at=now,
        )
        db.add(connection)
    else:
        connection.line_user_id = user_id
        connection.connected_at = now
        connection.revoked_at = None

    await db.commit()


@line_webhook_router.post("")
async def line_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """LINE Webhook受信エンドポイント。

    LINE_CHANNEL_SECRETが未設定の場合、LINE連携そのものが未セットアップと
    判断し、署名検証もペイロード処理も一切行わずに200のみ返す
    （設定前のデプロイでこのURLが叩かれても安全）。
    設定済みの場合は必ず署名を検証し、不一致・ヘッダー欠如なら403で拒否する
    （ペイロードを一切信用しない）。
    """
    settings = get_settings()
    channel_secret = (settings.LINE_CHANNEL_SECRET or "").strip()

    body = await request.body()

    if not channel_secret:
        return {"received": True}

    signature = request.headers.get("x-line-signature", "")
    if not verify_line_signature(body, signature, channel_secret):
        raise HTTPException(status_code=403, detail="invalid signature")

    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid payload")

    for event in payload.get("events", []):
        event_type = event.get("type")
        try:
            if event_type == "follow":
                await _handle_follow_event(db, event)
            elif event_type == "unfollow":
                await _handle_unfollow_event(db, event)
            elif event_type == "accountLink":
                await _handle_account_link_event(db, event)
            # それ以外のイベント種別（message等）は本フェーズでは対応不要のため無視する。
        except Exception:
            logger.exception("LINE webhook event処理中に例外が発生しました event_type=%s", event_type)

    return {"received": True}
