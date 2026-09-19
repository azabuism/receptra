"""
ShopClosure (臨時休業日) エンドポイント
臨時休業日の登録・削除、および該当期間の予約の自動キャンセル
"""

import uuid
from datetime import datetime, time, date as date_type
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.deps import get_current_user
from app.schemas.user import CurrentUser
from app.models.shop import Shop, ShopClosure
from app.models.reservation import Reservation
from app.schemas.shop_closure import ShopClosureCreateRequest, ShopClosureResponse
from app.services.notifications import notify_guest_of_cancellation

router = APIRouter(prefix="/api/v1/shops", tags=["shop-closures"])


async def _get_owned_shop(shop_id: str, current_user: CurrentUser, db: AsyncSession) -> Shop:
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")
    if shop.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="この店舗を編集する権限がありません")
    return shop


@router.get(
    "/{shop_id}/closures",
    response_model=List[ShopClosureResponse],
    summary="臨時休業日の一覧を取得"
)
async def list_closures(shop_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ShopClosure).filter(ShopClosure.shop_id == shop_id).order_by(ShopClosure.start_date)
    )
    return [ShopClosureResponse.from_orm(c) for c in result.scalars().all()]


@router.post(
    "/{shop_id}/closures",
    response_model=ShopClosureResponse,
    summary="臨時休業日を登録",
    description="指定した期間を臨時休業にする。該当期間に確認待ち・確定中の予約がある場合は自動的にキャンセルする"
)
async def create_closure(
    shop_id: str,
    request: ShopClosureCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await _get_owned_shop(shop_id, current_user, db)

    closure = ShopClosure(
        id=str(uuid.uuid4()),
        shop_id=shop_id,
        start_date=request.start_date,
        end_date=request.end_date,
        reason=request.reason,
        created_at=datetime.utcnow(),
    )
    db.add(closure)
    await db.flush()

    # 休業期間にかかる確認待ち・確定中の予約を自動キャンセル
    range_start = datetime.combine(request.start_date, time.min)
    range_end = datetime.combine(request.end_date, time.max)

    result = await db.execute(
        select(Reservation).options(selectinload(Reservation.table)).filter(
            Reservation.shop_id == shop_id,
            Reservation.status.in_(["pending", "confirmed"]),
            Reservation.reservation_date >= range_start,
            Reservation.reservation_date <= range_end,
        )
    )
    affected = result.scalars().all()

    reason_text = (request.reason or "").strip()
    message = (
        f"{reason_text}の都合により、誠に勝手ながらご予約をキャンセルさせていただきます。"
        if reason_text
        else "臨時休業のため、誠に勝手ながらご予約をキャンセルさせていただきます。"
    )

    for reservation in affected:
        reservation.status = "cancelled"
        reservation.cancelled_at = datetime.utcnow()
        reservation.cancellation_reason = message
        reservation.updated_at = datetime.utcnow()
        await notify_guest_of_cancellation(reservation, message)

    await db.commit()
    await db.refresh(closure)

    response = ShopClosureResponse.from_orm(closure)
    response.cancelled_count = len(affected)
    return response


@router.delete(
    "/{shop_id}/closures/{closure_id}",
    summary="臨時休業日を削除"
)
async def delete_closure(
    shop_id: str,
    closure_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await _get_owned_shop(shop_id, current_user, db)

    closure = await db.get(ShopClosure, closure_id)
    if not closure or closure.shop_id != shop_id:
        raise HTTPException(status_code=404, detail="臨時休業日が見つかりません")

    await db.delete(closure)
    await db.commit()
    return {"success": True}
