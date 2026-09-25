"""
レビュー（口コミ）API
- ログイン中のプラットフォームユーザーが店舗にレビューを投稿
- 誰でも店舗のレビュー一覧を閲覧可能
- 店舗オーナーはレビューに返信できる
"""

from datetime import datetime
from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.deps import get_current_user, get_optional_current_user
from app.schemas.user import CurrentUser
from app.models.review import Review
from app.models.shop import Shop
from app.schemas.social import (
    ReviewCreateRequest,
    ReviewOwnerReplyRequest,
    ReviewResponse,
)

router = APIRouter(prefix="/api/v1/reviews", tags=["reviews"])


def _build_review_response(review: Review, current_user_id: Optional[str] = None) -> ReviewResponse:
    # Phase W2監査で発見: display_nameが未設定のプラットフォームユーザーの場合、
    # 完全に未認証・公開のレビュー一覧（GET /api/v1/reviews/shop/{shop_id}）に
    # そのユーザーの実メールアドレスがreviewer_nameとしてそのまま露出していた。
    # review.customer側（display_nameが無ければNoneのまま）と同じ方針に揃え、
    # emailへはフォールバックしない。フロントエンド(shop.html)側は元々
    # `r.reviewer_name || 'ゲスト'` で表示しており、Noneでも問題なく
    # 「ゲスト」として表示される（表示ロジック自体は無変更）。
    reviewer_name = None
    if review.user is not None:
        reviewer_name = review.user.display_name
    elif review.customer is not None:
        reviewer_name = getattr(review.customer, "display_name", None)

    return ReviewResponse(
        id=review.id,
        shop_id=review.shop_id,
        shop_name=review.shop.name if review.shop else None,
        reviewer_name=reviewer_name,
        title=review.title,
        comment=review.comment,
        overall_rating=review.overall_rating,
        food_rating=review.food_rating,
        service_rating=review.service_rating,
        atmosphere_rating=review.atmosphere_rating,
        value_rating=review.value_rating,
        shop_response=review.shop_response,
        shop_response_at=review.shop_response_at,
        created_at=review.created_at,
        is_mine=bool(current_user_id and review.user_id == current_user_id),
    )


async def _recalculate_shop_rating(db: AsyncSession, shop_id: str) -> None:
    """店舗の総レビュー数・平均評価を再計算する"""
    result = await db.execute(select(Review.overall_rating).filter(Review.shop_id == shop_id))
    ratings = [row[0] for row in result.all() if row[0] is not None]

    shop = await db.get(Shop, shop_id)
    if shop is None:
        return
    shop.total_reviews = len(ratings)
    shop.average_rating = round(sum(ratings) / len(ratings), 2) if ratings else 0.0
    shop.updated_at = datetime.utcnow()


@router.post(
    "",
    response_model=ReviewResponse,
    summary="レビュー（口コミ）を投稿",
    description="ログイン中のユーザーとして店舗にレビューを投稿する",
)
async def create_review(
    request: ReviewCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReviewResponse:
    shop = await db.get(Shop, request.shop_id)
    if not shop or not shop.is_active:
        raise HTTPException(status_code=404, detail="指定された店舗が見つかりません")

    review = Review(
        id=str(uuid.uuid4()),
        shop_id=request.shop_id,
        customer_id=None,
        user_id=current_user.id,
        title=request.title,
        comment=request.comment,
        overall_rating=request.overall_rating,
        food_rating=request.food_rating,
        service_rating=request.service_rating,
        atmosphere_rating=request.atmosphere_rating,
        value_rating=request.value_rating,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(review)
    await db.flush()

    await _recalculate_shop_rating(db, request.shop_id)
    await db.commit()

    result = await db.execute(
        select(Review)
        .options(selectinload(Review.user), selectinload(Review.customer), selectinload(Review.shop))
        .filter(Review.id == review.id)
    )
    review = result.scalar_one()
    return _build_review_response(review, current_user.id)


@router.get(
    "/shop/{shop_id}",
    response_model=List[ReviewResponse],
    summary="店舗のレビュー一覧を取得",
    description="指定した店舗の全レビューを新しい順に取得する（閲覧はログイン不要）",
)
async def list_shop_reviews(
    shop_id: str,
    current_user: Optional[CurrentUser] = Depends(get_optional_current_user),
    db: AsyncSession = Depends(get_db),
) -> List[ReviewResponse]:
    result = await db.execute(
        select(Review)
        .options(selectinload(Review.user), selectinload(Review.customer), selectinload(Review.shop))
        .filter(Review.shop_id == shop_id)
        .order_by(Review.created_at.desc())
    )
    reviews = result.scalars().all()
    current_user_id = current_user.id if current_user else None
    return [_build_review_response(r, current_user_id) for r in reviews]


@router.put(
    "/{review_id}/reply",
    response_model=ReviewResponse,
    summary="レビューにオーナーが返信",
    description="自分の店舗のレビューに対してオーナーが返信する",
)
async def reply_to_review(
    review_id: str,
    request: ReviewOwnerReplyRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReviewResponse:
    result = await db.execute(
        select(Review)
        .options(selectinload(Review.user), selectinload(Review.customer), selectinload(Review.shop))
        .filter(Review.id == review_id)
    )
    review = result.scalar_one_or_none()
    if not review:
        raise HTTPException(status_code=404, detail="レビューが見つかりません")

    if not review.shop or review.shop.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="このレビューに返信する権限がありません")

    review.shop_response = request.shop_response
    review.shop_response_at = datetime.utcnow()
    review.updated_at = datetime.utcnow()
    await db.commit()

    result = await db.execute(
        select(Review)
        .options(selectinload(Review.user), selectinload(Review.customer), selectinload(Review.shop))
        .filter(Review.id == review_id)
    )
    review = result.scalar_one()
    return _build_review_response(review)
