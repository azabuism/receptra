"""
Phase N1: 統一Owner Notification基盤 — オーナー管理エンドポイント

一覧・詳細・既読化。すべてオーナー認証必須・tenant/shop分離必須。
app.routers.callback_requests と全く同じ owner-auth パターン
（get_current_user → shop取得 → tenant_id一致確認 → 対象行のshop_id一致確認）を
踏襲する。admin bypassやdebug用の抜け道は一切作らない。

Public APIは存在しない（本モデルは常にオーナー認証済みルートからのみ触れる）。
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_db
from app.models.owner_notification import OwnerNotificationEvent
from app.models.shop import Shop
from app.schemas.owner_notification import (
    OwnerNotificationEventItem,
    OwnerNotificationEventListResponse,
)
from app.schemas.user import CurrentUser

router = APIRouter(prefix="/api/v1/shops", tags=["owner-notifications"])
logger = logging.getLogger("receptra.owner_notifications_api")


async def _get_owned_shop(shop_id: str, current_user: CurrentUser, db: AsyncSession) -> Shop:
    """app.routers.callback_requests._get_owned_shop と全く同じチェック。"""
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")
    if shop.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="この店舗を操作する権限がありません")
    return shop


def _to_item(event: OwnerNotificationEvent) -> OwnerNotificationEventItem:
    return OwnerNotificationEventItem(
        id=event.id,
        shop_id=event.shop_id,
        event_type=event.event_type,
        priority=event.priority,
        title=event.title,
        message=event.message,
        related_entity_type=event.related_entity_type,
        related_entity_id=event.related_entity_id,
        customer_name=event.customer_name,
        is_read=event.read_at is not None,
        read_at=event.read_at,
        created_at=event.created_at,
    )


@router.get(
    "/{shop_id}/notifications",
    response_model=OwnerNotificationEventListResponse,
    summary="お知らせ（統一Owner Notification）の一覧を取得（オーナー専用）",
    description=(
        "新規予約・対応が必要な問い合わせ等のイベント一覧を取得する。"
        "オーナー認証必須・tenant/shop分離必須。phone/emailは一切含まない。"
    ),
)
async def list_owner_notifications(
    shop_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OwnerNotificationEventListResponse:
    await _get_owned_shop(shop_id, current_user, db)

    stmt = (
        select(OwnerNotificationEvent)
        .filter(OwnerNotificationEvent.shop_id == shop_id)
        .order_by(OwnerNotificationEvent.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()

    unread_stmt = select(func.count()).select_from(OwnerNotificationEvent).filter(
        OwnerNotificationEvent.shop_id == shop_id,
        OwnerNotificationEvent.read_at.is_(None),
    )
    unread_count = (await db.execute(unread_stmt)).scalar_one()

    total_stmt = select(func.count()).select_from(OwnerNotificationEvent).filter(
        OwnerNotificationEvent.shop_id == shop_id
    )
    total = (await db.execute(total_stmt)).scalar_one()

    return OwnerNotificationEventListResponse(
        shop_id=shop_id,
        total=total,
        unread_count=unread_count,
        items=[_to_item(r) for r in rows],
    )


@router.get(
    "/{shop_id}/notifications/{notification_id}",
    response_model=OwnerNotificationEventItem,
    summary="お知らせ1件の詳細を取得（オーナー専用）",
    description="オーナー認証必須・tenant/shop分離必須。他店舗のnotification_idでは404を返す。",
)
async def get_owner_notification(
    shop_id: str,
    notification_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OwnerNotificationEventItem:
    await _get_owned_shop(shop_id, current_user, db)

    event = await db.get(OwnerNotificationEvent, notification_id)
    if not event or event.shop_id != shop_id:
        raise HTTPException(status_code=404, detail="お知らせが見つかりません")

    return _to_item(event)


@router.patch(
    "/{shop_id}/notifications/{notification_id}/read",
    response_model=OwnerNotificationEventItem,
    summary="お知らせを既読にする（オーナー専用）",
    description="オーナー認証必須・tenant/shop分離必須。他店舗のnotification_idでは404を返す。",
)
async def mark_owner_notification_read(
    shop_id: str,
    notification_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OwnerNotificationEventItem:
    shop = await _get_owned_shop(shop_id, current_user, db)

    event = await db.get(OwnerNotificationEvent, notification_id)
    if not event or event.shop_id != shop.id:
        raise HTTPException(status_code=404, detail="お知らせが見つかりません")

    if event.read_at is None:
        event.read_at = datetime.utcnow()
        await db.commit()
        await db.refresh(event)

    logger.info(
        "お知らせを既読化 shop_id=%s notification_id=%s",
        shop_id, notification_id,
    )

    return _to_item(event)
