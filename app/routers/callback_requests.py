"""
Human Handoff基盤: CallbackRequest オーナー管理エンドポイント

一覧・詳細・対応状態更新・削除。すべてオーナー認証必須・tenant/shop分離必須。
app.routers.shops の notification-settings / phone-reception-settings と
全く同じ owner-auth パターン（get_current_user → shop取得 → tenant_id一致確認）を
踏襲する。admin bypassやdebug用の抜け道は一切作らない（谷村様の明示的な指示）。

削除APIについて（特に重要）:
- current_user.tenant_id と shop.tenant_id の一致確認（他テナントの店舗を
  操作できないこと）
- 対象 CallbackRequest.shop_id と URLパスの shop_id の一致確認（他店舗の
  CallbackRequestをURL誤り・IDOR的な操作で削除できないこと）
- 上記いずれかが不一致の場合は403（存在しないIDの場合は404）を返し、
  admin権限による迂回や、認証を経由しないdebug専用の削除経路は一切設けない。
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_db
from app.models.callback_request import CallbackRequest
from app.models.service import Service
from app.models.shop import Shop
from app.schemas.callback_request import (
    CallbackRequestDetail,
    CallbackRequestListItem,
    CallbackRequestListResponse,
    CallbackRequestResolutionUpdateRequest,
    mask_phone_for_list,
    resolution_status_label,
)
from app.schemas.user import CurrentUser

router = APIRouter(prefix="/api/v1/shops", tags=["callback-requests"])
logger = logging.getLogger("receptra.callback_requests")


async def _get_owned_shop(shop_id: str, current_user: CurrentUser, db: AsyncSession) -> Shop:
    """指定shop_idの店舗を取得し、現在のユーザーのテナントに属することを確認する。

    app.routers.shops の各オーナー専用エンドポイントと全く同じチェック。
    """
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")
    if shop.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="この店舗を操作する権限がありません")
    return shop


async def _resolve_service_names(db: AsyncSession, service_ids: set[str]) -> dict[str, str]:
    """service_idの集合から {service_id: service.name} を引く。

    CallbackRequest.service_idは意図的にORM relationshipを持たない設計
    （Service削除時にこの列を触る必要がないため）。そのため参照は
    ベストエフォートの別クエリで行い、既に削除済み・存在しないIDは
    単に結果に含めない（＝呼び出し側でNoneとして扱われ、一覧・詳細取得
    自体は失敗させない）。
    """
    service_ids = {sid for sid in service_ids if sid}
    if not service_ids:
        return {}
    result = await db.execute(select(Service.id, Service.name).filter(Service.id.in_(service_ids)))
    return {row.id: row.name for row in result.all()}


@router.get(
    "/{shop_id}/callback-requests",
    response_model=CallbackRequestListResponse,
    summary="折り返し依頼の一覧を取得（オーナー専用）",
    description=(
        "AI電話受付がHuman Handoffで受け付けた折り返し依頼の一覧を取得する。"
        "オーナー認証必須・tenant/shop分離必須。電話番号は末尾4桁以外をマスクして返す"
        "（詳細取得APIでのみフルの電話番号を返す）。"
    ),
)
async def list_callback_requests(
    shop_id: str,
    resolution_status: Optional[str] = Query(
        None, description="unhandled/in_progress/handledで絞り込む（未指定なら全件）"
    ),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CallbackRequestListResponse:
    await _get_owned_shop(shop_id, current_user, db)

    stmt = select(CallbackRequest).filter(CallbackRequest.shop_id == shop_id)
    if resolution_status:
        stmt = stmt.filter(CallbackRequest.resolution_status == resolution_status)
    stmt = stmt.order_by(CallbackRequest.created_at.desc()).offset(offset).limit(limit)

    result = await db.execute(stmt)
    rows = result.scalars().all()

    service_names = await _resolve_service_names(db, {r.service_id for r in rows if r.service_id})

    items = [
        CallbackRequestListItem(
            id=r.id,
            created_at=r.created_at,
            customer_name=r.customer_name,
            customer_phone_masked=mask_phone_for_list(r.customer_phone),
            inquiry_text=r.inquiry_text,
            desired_date=r.desired_date,
            desired_time=r.desired_time,
            party_size=r.party_size,
            service_name=service_names.get(r.service_id) if r.service_id else None,
            reason_code=r.reason_code,
            status=r.status,
            resolution_status=r.resolution_status,
            resolution_status_label=resolution_status_label(r.resolution_status),
        )
        for r in rows
    ]

    return CallbackRequestListResponse(shop_id=shop_id, total=len(items), items=items)


@router.get(
    "/{shop_id}/callback-requests/{callback_request_id}",
    response_model=CallbackRequestDetail,
    summary="折り返し依頼の詳細を取得（オーナー専用）",
    description=(
        "折り返し依頼1件の詳細を取得する。オーナー認証必須・tenant/shop分離必須。"
        "一覧APIとは異なり、電話番号はマスクせずフルで返す。"
    ),
)
async def get_callback_request(
    shop_id: str,
    callback_request_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CallbackRequestDetail:
    await _get_owned_shop(shop_id, current_user, db)

    cr = await db.get(CallbackRequest, callback_request_id)
    if not cr or cr.shop_id != shop_id:
        raise HTTPException(status_code=404, detail="折り返し依頼が見つかりません")

    service_name = None
    if cr.service_id:
        names = await _resolve_service_names(db, {cr.service_id})
        service_name = names.get(cr.service_id)

    return CallbackRequestDetail(
        id=cr.id,
        shop_id=cr.shop_id,
        created_at=cr.created_at,
        updated_at=cr.updated_at,
        customer_name=cr.customer_name,
        customer_phone=cr.customer_phone,
        inquiry_text=cr.inquiry_text,
        desired_date=cr.desired_date,
        desired_time=cr.desired_time,
        party_size=cr.party_size,
        service_id=cr.service_id,
        service_name=service_name,
        reason_code=cr.reason_code,
        status=cr.status,
        resolution_status=cr.resolution_status,
        resolution_status_label=resolution_status_label(cr.resolution_status),
    )


@router.patch(
    "/{shop_id}/callback-requests/{callback_request_id}",
    response_model=CallbackRequestDetail,
    summary="折り返し依頼の対応状態を更新（オーナー専用）",
    description=(
        "お客様への折り返し対応状態（未対応/対応中/対応済み）を更新する。"
        "担当者への通知試行の成否を表すstatusカラムはこのAPIでは一切変更しない"
        "（意味が異なるため別カラムのまま独立して管理する）。"
    ),
)
async def update_callback_request_resolution(
    shop_id: str,
    callback_request_id: str,
    request: CallbackRequestResolutionUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CallbackRequestDetail:
    await _get_owned_shop(shop_id, current_user, db)

    cr = await db.get(CallbackRequest, callback_request_id)
    if not cr or cr.shop_id != shop_id:
        raise HTTPException(status_code=404, detail="折り返し依頼が見つかりません")

    cr.resolution_status = request.resolution_status
    await db.commit()
    await db.refresh(cr)

    logger.info(
        "折り返し依頼の対応状態を更新 shop_id=%s callback_request_id=%s resolution_status=%s",
        shop_id, callback_request_id, cr.resolution_status,
    )

    service_name = None
    if cr.service_id:
        names = await _resolve_service_names(db, {cr.service_id})
        service_name = names.get(cr.service_id)

    return CallbackRequestDetail(
        id=cr.id,
        shop_id=cr.shop_id,
        created_at=cr.created_at,
        updated_at=cr.updated_at,
        customer_name=cr.customer_name,
        customer_phone=cr.customer_phone,
        inquiry_text=cr.inquiry_text,
        desired_date=cr.desired_date,
        desired_time=cr.desired_time,
        party_size=cr.party_size,
        service_id=cr.service_id,
        service_name=service_name,
        reason_code=cr.reason_code,
        status=cr.status,
        resolution_status=cr.resolution_status,
        resolution_status_label=resolution_status_label(cr.resolution_status),
    )


@router.delete(
    "/{shop_id}/callback-requests/{callback_request_id}",
    summary="折り返し依頼を削除（オーナー専用）",
    description=(
        "折り返し依頼1件を削除する。current_userのtenant確認・shopの所有権確認・"
        "対象CallbackRequest.shop_idの一致確認をすべて行い、他テナント/他店舗の"
        "データは絶対に削除できない。admin権限による迂回や認証不要のdebug削除経路は"
        "一切存在しない。"
    ),
)
async def delete_callback_request(
    shop_id: str,
    callback_request_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    shop = await _get_owned_shop(shop_id, current_user, db)

    cr = await db.get(CallbackRequest, callback_request_id)
    if not cr or cr.shop_id != shop.id:
        raise HTTPException(status_code=404, detail="折り返し依頼が見つかりません")

    await db.delete(cr)
    await db.commit()

    logger.info(
        "折り返し依頼を削除 shop_id=%s callback_request_id=%s tenant_id=%s",
        shop_id, callback_request_id, current_user.tenant_id,
    )

    return {"success": True}
