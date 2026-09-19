"""
クーポン API（オーナー向け・最小構成）
店舗オーナーがクーポンを作成・一覧・停止できるようにする。
お客様側からの閲覧はマイページのサマリー（/api/v1/mypage/summary）に含まれる。
"""

from datetime import datetime
from typing import List
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.deps import get_current_user
from app.schemas.user import CurrentUser
from app.models.shop import Shop
from app.models.promotion import Coupon
from app.schemas.social import CouponCreateRequest, CouponResponse

router = APIRouter(prefix="/api/v1/shops/{shop_id}/coupons", tags=["coupons"])


def _build_coupon_response(coupon: Coupon) -> CouponResponse:
    return CouponResponse(
        id=coupon.id,
        shop_id=coupon.shop_id,
        shop_name=coupon.shop.name if coupon.shop else None,
        code=coupon.code,
        description=coupon.description,
        discount_type=coupon.discount_type,
        discount_value=coupon.discount_value,
        start_date=coupon.start_date,
        end_date=coupon.end_date,
        usage_limit=coupon.usage_limit,
        usage_count=coupon.usage_count,
        is_active=coupon.is_active,
    )


async def _get_owned_shop(shop_id: str, current_user: CurrentUser, db: AsyncSession) -> Shop:
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")
    if shop.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="この店舗を編集する権限がありません")
    return shop


@router.get(
    "",
    response_model=List[CouponResponse],
    summary="自分の店舗のクーポン一覧",
)
async def list_coupons(
    shop_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> List[CouponResponse]:
    await _get_owned_shop(shop_id, current_user, db)
    result = await db.execute(
        select(Coupon)
        .options(selectinload(Coupon.shop))
        .filter(Coupon.shop_id == shop_id)
        .order_by(Coupon.created_at.desc())
    )
    return [_build_coupon_response(c) for c in result.scalars().all()]


@router.post(
    "",
    response_model=CouponResponse,
    summary="クーポンを作成",
)
async def create_coupon(
    shop_id: str,
    request: CouponCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CouponResponse:
    await _get_owned_shop(shop_id, current_user, db)

    existing = await db.execute(select(Coupon).filter(Coupon.code == request.code))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="このクーポンコードは既に使用されています")

    coupon = Coupon(
        id=str(uuid.uuid4()),
        shop_id=shop_id,
        code=request.code,
        description=request.description,
        discount_type=request.discount_type,
        discount_value=request.discount_value,
        start_date=request.start_date,
        end_date=request.end_date,
        usage_limit=request.usage_limit,
        max_per_customer=request.max_per_customer,
        is_active=True,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(coupon)
    await db.commit()

    result = await db.execute(
        select(Coupon).options(selectinload(Coupon.shop)).filter(Coupon.id == coupon.id)
    )
    coupon = result.scalar_one()
    return _build_coupon_response(coupon)


@router.put(
    "/{coupon_id}/deactivate",
    response_model=CouponResponse,
    summary="クーポンを停止",
)
async def deactivate_coupon(
    shop_id: str,
    coupon_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CouponResponse:
    await _get_owned_shop(shop_id, current_user, db)
    result = await db.execute(
        select(Coupon).options(selectinload(Coupon.shop)).filter(
            Coupon.id == coupon_id, Coupon.shop_id == shop_id
        )
    )
    coupon = result.scalar_one_or_none()
    if not coupon:
        raise HTTPException(status_code=404, detail="クーポンが見つかりません")

    coupon.is_active = False
    coupon.updated_at = datetime.utcnow()
    await db.commit()

    result = await db.execute(
        select(Coupon).options(selectinload(Coupon.shop)).filter(Coupon.id == coupon_id)
    )
    coupon = result.scalar_one()
    return _build_coupon_response(coupon)
