"""
マイページ API
ログイン中のユーザー（お客様側アカウント）向けの機能：
- よく行く店（予約履歴から集計）
- 行きたい店 / いいね（UserShopRelation）
- 自分の口コミ
- 使えるクーポン
- （追加アイデア）来店済みでまだ口コミを書いていないお店のリマインド
"""

from datetime import datetime
from typing import List
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.deps import get_current_user
from app.schemas.user import CurrentUser
from app.models.shop import Shop
from app.models.reservation import Reservation, ReservationStatus
from app.models.review import Review
from app.models.promotion import Coupon
from app.models.social import UserShopRelation
from app.schemas.social import (
    MyPageShopSummary,
    MyPageSummaryResponse,
    RelationToggleRequest,
    RelationToggleResponse,
    ShopRelationStatus,
    CouponResponse,
)
from app.routers.reviews import _build_review_response

router = APIRouter(prefix="/api/v1/mypage", tags=["mypage"])


def _shop_summary(shop: Shop, visit_count: int = None) -> MyPageShopSummary:
    return MyPageShopSummary(
        id=shop.id,
        name=shop.name,
        category=shop.category,
        business_type=shop.business_type,
        thumbnail_url=shop.thumbnail_url,
        average_rating=shop.average_rating,
        visit_count=visit_count,
    )


@router.get(
    "/summary",
    response_model=MyPageSummaryResponse,
    summary="マイページのサマリーを取得",
)
async def get_mypage_summary(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MyPageSummaryResponse:
    user_id = current_user.id

    # --- よく行く店（予約回数で集計） ---
    freq_result = await db.execute(
        select(Reservation.shop_id, func.count(Reservation.id).label("cnt"))
        .filter(Reservation.user_id == user_id)
        .group_by(Reservation.shop_id)
        .order_by(func.count(Reservation.id).desc())
        .limit(5)
    )
    freq_rows = freq_result.all()
    frequent_shop_ids = [row[0] for row in freq_rows]
    frequent_counts = {row[0]: row[1] for row in freq_rows}
    frequent_shops: List[MyPageShopSummary] = []
    if frequent_shop_ids:
        shops_result = await db.execute(select(Shop).filter(Shop.id.in_(frequent_shop_ids)))
        shops_by_id = {s.id: s for s in shops_result.scalars().all()}
        for shop_id in frequent_shop_ids:
            shop = shops_by_id.get(shop_id)
            if shop:
                frequent_shops.append(_shop_summary(shop, visit_count=frequent_counts.get(shop_id)))

    # --- 行きたい店 / いいね ---
    relations_result = await db.execute(
        select(UserShopRelation).options(selectinload(UserShopRelation.shop)).filter(
            UserShopRelation.user_id == user_id
        )
    )
    relations = relations_result.scalars().all()
    want_to_go_shops = [_shop_summary(r.shop) for r in relations if r.relation_type == "want_to_go" and r.shop]
    liked_shops = [_shop_summary(r.shop) for r in relations if r.relation_type == "like" and r.shop]

    # --- 自分の口コミ ---
    reviews_result = await db.execute(
        select(Review)
        .options(selectinload(Review.user), selectinload(Review.customer), selectinload(Review.shop))
        .filter(Review.user_id == user_id)
        .order_by(Review.created_at.desc())
    )
    my_review_rows = reviews_result.scalars().all()
    my_reviews = [_build_review_response(r, user_id) for r in my_review_rows]
    reviewed_shop_ids = {r.shop_id for r in my_review_rows}

    # --- 使えるクーポン（よく行く・行きたい・いいねした・口コミを書いたお店を対象に） ---
    related_shop_ids = set(frequent_shop_ids)
    related_shop_ids |= {s.id for s in want_to_go_shops}
    related_shop_ids |= {s.id for s in liked_shops}
    related_shop_ids |= reviewed_shop_ids

    available_coupons: List[CouponResponse] = []
    if related_shop_ids:
        now = datetime.utcnow()
        coupons_result = await db.execute(
            select(Coupon)
            .options(selectinload(Coupon.shop))
            .filter(
                Coupon.shop_id.in_(related_shop_ids),
                Coupon.is_active.is_(True),
                Coupon.start_date <= now,
                Coupon.end_date >= now,
            )
        )
        for coupon in coupons_result.scalars().all():
            if coupon.usage_limit is not None and coupon.usage_count >= coupon.usage_limit:
                continue
            available_coupons.append(
                CouponResponse(
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
            )

    # --- 追加アイデア: 来店済みだがまだ口コミを書いていないお店 ---
    completed_result = await db.execute(
        select(Reservation.shop_id)
        .filter(
            Reservation.user_id == user_id,
            Reservation.status == ReservationStatus.COMPLETED.value,
        )
        .distinct()
    )
    completed_shop_ids = {row[0] for row in completed_result.all()}
    pending_ids = completed_shop_ids - reviewed_shop_ids
    pending_review_shops: List[MyPageShopSummary] = []
    if pending_ids:
        shops_result = await db.execute(select(Shop).filter(Shop.id.in_(pending_ids)))
        pending_review_shops = [_shop_summary(s) for s in shops_result.scalars().all()]

    return MyPageSummaryResponse(
        display_name=current_user.display_name or current_user.email,
        frequent_shops=frequent_shops,
        want_to_go_shops=want_to_go_shops,
        liked_shops=liked_shops,
        like_count=len(liked_shops),
        my_reviews=my_reviews,
        available_coupons=available_coupons,
        pending_review_shops=pending_review_shops,
    )


@router.post(
    "/relations/toggle",
    response_model=RelationToggleResponse,
    summary="行きたい店・いいねをトグル",
    description="既に登録済みなら解除、未登録なら登録する",
)
async def toggle_relation(
    request: RelationToggleRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RelationToggleResponse:
    shop = await db.get(Shop, request.shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="指定された店舗が見つかりません")

    result = await db.execute(
        select(UserShopRelation).filter(
            UserShopRelation.user_id == current_user.id,
            UserShopRelation.shop_id == request.shop_id,
            UserShopRelation.relation_type == request.relation_type,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        await db.delete(existing)
        await db.commit()
        return RelationToggleResponse(shop_id=request.shop_id, relation_type=request.relation_type, active=False)

    relation = UserShopRelation(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        shop_id=request.shop_id,
        relation_type=request.relation_type,
        created_at=datetime.utcnow(),
    )
    db.add(relation)
    await db.commit()
    return RelationToggleResponse(shop_id=request.shop_id, relation_type=request.relation_type, active=True)


@router.get(
    "/relations/{shop_id}",
    response_model=ShopRelationStatus,
    summary="指定した店舗に対する自分の関係を取得",
    description="行きたい店・いいねの登録状況を返す",
)
async def get_relation_status(
    shop_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ShopRelationStatus:
    result = await db.execute(
        select(UserShopRelation).filter(
            UserShopRelation.user_id == current_user.id,
            UserShopRelation.shop_id == shop_id,
        )
    )
    relations = result.scalars().all()
    types = {r.relation_type for r in relations}
    return ShopRelationStatus(
        shop_id=shop_id,
        want_to_go="want_to_go" in types,
        like="like" in types,
    )
