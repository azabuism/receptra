"""
PHASE O3: Pre-Order Backend Acceptance Engine — APIエンドポイント

Web/AI Chat/AI Voice/将来PSTNのすべてが将来共有するBackend Coreの
入口。判断ロジック自体はここには一切書かず、すべて
app.services.pre_orders（create_public_pre_order /
determine_pre_order_confirmation）に委譲する（Section5: routerに
散らばったロジックを書かない）。

★Section11/49: Public Create APIは、まだO4のOwner設定（店舗ごとの
pre-order enabled）が存在しないため、グローバルなfeature gate
（settings.PRE_ORDER_PUBLIC_CREATE_ENABLED、デフォルトFalse）で
守られている。gateが閉じている場合は404を返す（「このAPIが存在する
こと自体」を第三者に知らせない、というreservations.pyの他の
機能フラグ的な扱いとは異なり、意図的に「発見されないこと」を優先する
判断——今の所存在しないURLへのアクセスと区別がつかない404が最も安全）。

このファイルには2つのAPIRouterインスタンスがある
（app/routers/shop_media.pyと同じdual-prefix-router-per-fileパターン）:
- router: prefix="/api/v1/pre-orders" — Public Create、Owner詳細取得、
  Owner確認/却下
- shop_pre_orders_router: prefix="/api/v1/shops" — Owner一覧
  （app/routers/owner_notifications.pyと同じ/api/v1/shops配下の
  ネスト規約）
"""

import logging
import time
from datetime import date as date_type
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import get_db
from app.deps import get_current_user
from app.schemas.user import CurrentUser
from app.models.pre_order import PreOrder, PreOrderStatus, PreOrderConfirmationStatus
from app.models.shop import Shop
from app.schemas.pre_order import (
    PreOrderPublicCreateRequest,
    PreOrderPublicCreateResponse,
    PreOrderItemPublicResponse,
    PreOrderOwnerDetailResponse,
    PreOrderItemResponse,
    PreOrderOwnerListItem,
    PreOrderOwnerListResponse,
    PreOrderConfirmationUpdateRequest,
    customer_message_code,
)
from app.services.pre_orders import create_public_pre_order

logger = logging.getLogger("receptra.pre_orders")

router = APIRouter(prefix="/api/v1/pre-orders", tags=["pre-orders"])
shop_pre_orders_router = APIRouter(prefix="/api/v1/shops", tags=["pre-orders"])


def _http_error(status_code: int, detail: str, reason_code: Optional[str] = None) -> HTTPException:
    """app.routers.reservations._http_error() / app.services.pre_orders._http_error()
    と同じパターン（モジュールごとに同じ小さなhelperを複製する、という
    既存の規約に合わせる。他モジュールのprivate関数を直接importして結合を
    増やさない）。"""
    exc = HTTPException(status_code=status_code, detail=detail)
    exc.reason_code = reason_code
    return exc


# ===== Section21: app/routers/reservations.pyの_check_public_create_rate_limit()
# と全く同じ設計（プロセス内メモリ・shop_idキー付きスライディングウィンドウ）。
# 値もReservationのPublic Create APIと同じにする（新しい基準を発明しない）。
# 別エンドポイントのため、バケット自体はPreOrder専用に独立させる
# （Reservationの通常予約と事前注文が互いのレート制限を消費しないように）。=====
_PUBLIC_CREATE_RATE_WINDOW_SECONDS = 60
_PUBLIC_CREATE_RATE_MAX_REQUESTS = 10
_public_pre_order_create_requests: dict[str, list] = {}


def _check_public_create_rate_limit(shop_id: str) -> None:
    now = time.monotonic()
    bucket = _public_pre_order_create_requests.setdefault(shop_id, [])
    bucket[:] = [t for t in bucket if now - t < _PUBLIC_CREATE_RATE_WINDOW_SECONDS]
    if len(bucket) >= _PUBLIC_CREATE_RATE_MAX_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail="リクエストが多すぎます。しばらくしてから再度お試しください。",
        )
    bucket.append(now)


def _to_item_response(item) -> PreOrderItemResponse:
    return PreOrderItemResponse(
        id=item.id,
        product_id=item.product_id,
        product_name=item.product_name,
        quantity=item.quantity,
        variant=item.variant,
        unit_price=item.unit_price,
        item_note=item.item_note,
        created_at=item.created_at,
    )


def _to_public_response(pre_order: PreOrder) -> PreOrderPublicCreateResponse:
    return PreOrderPublicCreateResponse(
        pre_order_id=pre_order.id,
        shop_id=pre_order.shop_id,
        pickup_at=pre_order.pickup_at,
        status=pre_order.status,
        confirmation_status=pre_order.confirmation_status,
        customer_message_code=customer_message_code(pre_order.confirmation_status),
        items=[
            PreOrderItemPublicResponse(
                id=item.id, product_id=item.product_id, product_name=item.product_name,
                quantity=item.quantity, variant=item.variant, unit_price=item.unit_price,
            )
            for item in pre_order.items
        ],
        created_at=pre_order.created_at,
    )


def _to_owner_detail_response(pre_order: PreOrder) -> PreOrderOwnerDetailResponse:
    return PreOrderOwnerDetailResponse(
        id=pre_order.id,
        shop_id=pre_order.shop_id,
        customer_name=pre_order.customer_name,
        customer_phone=pre_order.customer_phone,
        customer_email=pre_order.customer_email,
        pickup_at=pre_order.pickup_at,
        status=pre_order.status,
        confirmation_status=pre_order.confirmation_status,
        customer_note=pre_order.customer_note,
        internal_note=pre_order.internal_note,
        items=[_to_item_response(item) for item in pre_order.items],
        created_at=pre_order.created_at,
        updated_at=pre_order.updated_at,
    )


async def _get_owned_pre_order(pre_order_id: str, current_user: CurrentUser, db: AsyncSession) -> PreOrder:
    """app.routers.reservations.get_reservation()と同じtenant分離パターン
    （flat detail endpointのため、_get_owned_shop的なshop-nested helperは
    使わず、既存のReservation詳細取得と同じインライン方式に合わせる）。"""
    result = await db.execute(
        select(PreOrder).options(selectinload(PreOrder.items)).filter(PreOrder.id == pre_order_id)
    )
    pre_order = result.scalar_one_or_none()
    if not pre_order:
        raise HTTPException(status_code=404, detail="事前注文が見つかりません")
    shop = await db.get(Shop, pre_order.shop_id)
    if not shop or shop.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="この事前注文を閲覧する権限がありません")
    return pre_order


@router.post(
    "/create",
    response_model=PreOrderPublicCreateResponse,
    summary="事前注文を受け付ける（Public / 未認証）",
    description=(
        "Web/AI Chat/AI Voice/将来PSTNが共通で呼び出す事前注文の受付API。"
        "根拠のない自動確定は行わず、常にconfirmation_status="
        "owner_confirmation_requiredで作成し、店舗へ通知する（FINAL PRINCIPLE）。"
        "O4以前は settings.PRE_ORDER_PUBLIC_CREATE_ENABLED によりデフォルト無効。"
    ),
)
async def create_pre_order_public(
    request: PreOrderPublicCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> PreOrderPublicCreateResponse:
    # Section11/49: グローバルfeature gate。閉じている場合は、O4以前に
    # 第三者がこのAPIの存在を発見できないよう、通常の「無効化されたAPI」の
    # 400/403ではなく404（存在しないURLと区別がつかない扱い）にする。
    settings = get_settings()
    if not settings.PRE_ORDER_PUBLIC_CREATE_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")

    _check_public_create_rate_limit(request.shop_id)

    try:
        pre_order = await create_public_pre_order(db, request)
        return _to_public_response(pre_order)
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise _http_error(500, f"事前注文の受付に失敗しました: {str(e)}", reason_code="temporarily_unavailable")


@router.get(
    "/{pre_order_id}",
    response_model=PreOrderOwnerDetailResponse,
    summary="事前注文の詳細を取得（オーナー専用）",
    description="internal_noteを含む完全な詳細を返す。オーナー認証必須・tenant分離必須。",
)
async def get_pre_order(
    pre_order_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PreOrderOwnerDetailResponse:
    pre_order = await _get_owned_pre_order(pre_order_id, current_user, db)
    return _to_owner_detail_response(pre_order)


@router.patch(
    "/{pre_order_id}/confirmation",
    response_model=PreOrderOwnerDetailResponse,
    summary="事前注文を確認/却下する（オーナー専用）",
    description=(
        "confirmation_status を owner_confirmation_required から "
        "confirmed または rejected へ更新する。O3時点では顧客への"
        "通知（SMS/email/LINE等）は一切トリガーしない（状態更新のみ）。"
    ),
)
async def update_pre_order_confirmation(
    pre_order_id: str,
    request: PreOrderConfirmationUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PreOrderOwnerDetailResponse:
    pre_order = await _get_owned_pre_order(pre_order_id, current_user, db)

    # Section34: 許容される遷移は owner_confirmation_required → confirmed/rejected
    # のみ。既にconfirmed/rejected/completed/cancelledのものへの再遷移は拒否する
    # （最小限のstate-transition validation）。
    if pre_order.confirmation_status != PreOrderConfirmationStatus.OWNER_CONFIRMATION_REQUIRED.value:
        raise _http_error(
            400,
            "この事前注文は既に確認済み/却下済みのため、確認状態を変更できません",
            reason_code="invalid_state_transition",
        )
    if pre_order.status in (PreOrderStatus.COMPLETED.value, PreOrderStatus.CANCELLED.value):
        raise _http_error(
            400,
            "この事前注文は完了済み/キャンセル済みのため、確認状態を変更できません",
            reason_code="invalid_state_transition",
        )

    try:
        pre_order.confirmation_status = request.confirmation_status
        # 設計判断（O3の裁量事項として最終報告で明示する）: confirmation_status
        # とstatusという2軸モデルの一貫性を保つため、confirm/rejectに応じて
        # statusも自然に進める。confirmed→PENDING（受取待ち）のまま進める設計も
        # あり得るが、店舗が「対応可能」と回答した時点でPENDING→CONFIRMEDへ
        # 進めるのが2軸モデルの趣旨に最も自然に合致すると判断した。
        if request.confirmation_status == PreOrderConfirmationStatus.CONFIRMED.value:
            pre_order.status = PreOrderStatus.CONFIRMED.value
        elif request.confirmation_status == PreOrderConfirmationStatus.REJECTED.value:
            pre_order.status = PreOrderStatus.CANCELLED.value

        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise _http_error(500, f"確認状態の更新に失敗しました: {str(e)}", reason_code="temporarily_unavailable")

    result = await db.execute(
        select(PreOrder).options(selectinload(PreOrder.items)).filter(PreOrder.id == pre_order_id)
    )
    pre_order = result.scalar_one()
    return _to_owner_detail_response(pre_order)


@shop_pre_orders_router.get(
    "/{shop_id}/pre-orders",
    response_model=PreOrderOwnerListResponse,
    summary="店舗の事前注文一覧を取得（オーナー専用）",
    description="pickup_date・statusで絞込可能な最小限の一覧。オーナー認証必須・tenant分離必須。",
)
async def get_shop_pre_orders(
    shop_id: str,
    pickup_date: Optional[date_type] = Query(None, description="受取予定日（YYYY-MM-DD）で絞込"),
    status: Optional[str] = Query(None, description="statusで絞込"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PreOrderOwnerListResponse:
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="指定された店舗が見つかりません")
    if shop.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="この店舗の事前注文を閲覧する権限がありません")

    stmt = select(PreOrder).options(selectinload(PreOrder.items)).filter(PreOrder.shop_id == shop_id)
    if pickup_date is not None:
        # pickup_atはJST-local-naive datetime。日付のみでの絞込のため、
        # 当日00:00:00〜翌日00:00:00未満の範囲で判定する。
        from datetime import datetime, timedelta
        start = datetime.combine(pickup_date, datetime.min.time())
        end = start + timedelta(days=1)
        stmt = stmt.filter(PreOrder.pickup_at >= start, PreOrder.pickup_at < end)
    if status:
        stmt = stmt.filter(PreOrder.status == status.lower())

    all_result = await db.execute(stmt)
    all_items = all_result.scalars().all()
    total = len(all_items)

    stmt = stmt.order_by(PreOrder.pickup_at.asc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    pre_orders = result.scalars().all()

    return PreOrderOwnerListResponse(
        total=total, limit=limit, offset=offset,
        items=[
            PreOrderOwnerListItem(
                id=p.id, shop_id=p.shop_id, customer_name=p.customer_name,
                customer_phone=p.customer_phone, pickup_at=p.pickup_at,
                status=p.status, confirmation_status=p.confirmation_status,
                item_count=len(p.items),
                total_quantity=sum(item.quantity for item in p.items),
                created_at=p.created_at,
            )
            for p in pre_orders
        ],
    )
