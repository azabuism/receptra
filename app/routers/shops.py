"""
Shop (店舗) エンドポイント
店舗の登録、検索、管理機能
"""

import uuid
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.shop import Shop, ShopHours
from app.schemas.shop import (
    ShopRegisterRequest, ShopResponse, ShopRegisterResponse,
    ShopSearchQuery, ShopListResponse, ErrorResponse, ShopHoursResponse,
    ShopUpdateRequest
)
from app.deps import get_current_user
from app.schemas.user import CurrentUser

router = APIRouter(prefix="/api/v1/shops", tags=["shops"])


@router.post(
    "/register",
    response_model=ShopRegisterResponse,
    summary="新規店舗を登録",
    description="新しい店舗情報を登録して店舗IDを取得"
)
async def register_shop(
    request: ShopRegisterRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ShopRegisterResponse:
    """
    店舗を登録します
    
    - **name**: 店舗名（必須）
    - **category**: カテゴリ（必須）
    - **address**: 住所（必須）
    - **latitude/longitude**: GPS座標（オプション）
    """
    try:
        # 店舗ID生成
        shop_id = str(uuid.uuid4())
        
        tenant_id = current_user.tenant_id
        
        # 店舗オブジェクト作成
        shop = Shop(
            id=shop_id,
            tenant_id=tenant_id,
            name=request.name,
            description=request.description,
            category=request.category,
            address=request.address,
            latitude=request.latitude,
            longitude=request.longitude,
            phone=request.phone,
            email=request.email,
            website=request.website,
            thumbnail_url=request.thumbnail_url,
            cover_image_url=request.cover_image_url,
            features=request.features or [],
            is_active=True,
            is_featured=False,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        db.add(shop)
        
        # 営業時間を追加
        if request.shop_hours:
            for hour in request.shop_hours:
                shop_hour = ShopHours(
                    id=str(uuid.uuid4()),
                    shop_id=shop_id,
                    day_of_week=hour.day_of_week,
                    opening_time=hour.opening_time,
                    closing_time=hour.closing_time,
                    is_closed=hour.is_closed,
                    last_order_time=hour.last_order_time,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
                db.add(shop_hour)
        
        await db.commit()

        # shop_hours リレーションを明示的にロードしてから応答を構築
        # (コミット後にリレーションへ遅延アクセスすると、AsyncSession環境では
        #  MissingGreenlet エラーになるため、selectinload で事前に読み込む)
        result = await db.execute(
            select(Shop)
            .options(selectinload(Shop.shop_hours))
            .filter(Shop.id == shop_id)
        )
        shop = result.scalar_one()

        # レスポンス作成
        shop_response = ShopResponse.from_orm(shop)
        
        return ShopRegisterResponse(
            success=True,
            message="店舗が正常に登録されました",
            shop_id=shop_id,
            shop=shop_response
        )
    
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"店舗登録に失敗しました: {str(e)}"
        )


@router.get(
    "/mine",
    response_model=ShopListResponse,
    summary="自分のテナントの店舗一覧を取得",
    description="ログイン中のユーザーのテナントが登録した店舗一覧を取得"
)
async def get_my_shops(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ShopListResponse:
    """
    ログイン中のユーザー（テナント）が登録した店舗一覧を取得します
    """
    stmt = select(Shop).options(selectinload(Shop.shop_hours)).filter(Shop.tenant_id == current_user.tenant_id).order_by(Shop.created_at.desc())
    result = await db.execute(stmt)
    shops = result.scalars().all()
    shop_list = [ShopResponse.from_orm(shop) for shop in shops]
    return ShopListResponse(
        total=len(shop_list),
        limit=len(shop_list),
        offset=0,
        items=shop_list
    )


@router.patch(
    "/{shop_id}",
    response_model=ShopResponse,
    summary="店舗情報を更新",
    description="送られたフィールドのみ更新（特徴タグ、紹介文などの後編集に利用）"
)
async def update_shop(
    shop_id: str,
    request: ShopUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ShopResponse:
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")
    if shop.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="この店舗を編集する権限がありません")

    update_data = request.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(shop, field, value)
    shop.updated_at = datetime.utcnow()

    await db.commit()

    result = await db.execute(
        select(Shop).options(selectinload(Shop.shop_hours)).filter(Shop.id == shop_id)
    )
    shop = result.scalar_one()
    return ShopResponse.from_orm(shop)


@router.delete(
    "/{shop_id}",
    summary="店舗を削除",
    description="店舗と、その写真・メニュー・営業時間などの関連データをまとめて削除"
)
async def delete_shop(
    shop_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")
    if shop.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="この店舗を削除する権限がありません")

    await db.delete(shop)
    await db.commit()
    return {"success": True}


@router.get(
    "/search",
    response_model=ShopListResponse,
    summary="店舗を検索",
    description="キーワード、カテゴリ、位置情報で店舗を検索"
)
async def search_shops(
    keyword: Optional[str] = Query(None, description="キーワード検索"),
    category: Optional[str] = Query(None, description="カテゴリフィルタ"),
    latitude: Optional[float] = Query(None, description="検索中心点の緯度"),
    longitude: Optional[float] = Query(None, description="検索中心点の経度"),
    radius_km: float = Query(5.0, ge=0.1, le=50, description="検索半径（km）"),
    min_rating: Optional[float] = Query(None, ge=0, le=5, description="最小評価"),
    sort_by: str = Query("rating", description="ソート順"),
    limit: int = Query(20, ge=1, le=100, description="取得件数"),
    offset: int = Query(0, ge=0, description="オフセット"),
    db: AsyncSession = Depends(get_db)
) -> ShopListResponse:
    """
    店舗を検索します
    
    - **keyword**: キーワード検索（店舗名、説明から検索）
    - **category**: カテゴリでフィルタ（例：RESTAURANT）
    - **latitude/longitude**: GPS座標で近くの店舗を検索
    - **radius_km**: 検索半径（デフォルト5km）
    - **min_rating**: 最小評価でフィルタ
    - **sort_by**: ソート順（rating, distance, popular, new）
    """
    try:
        # 基本フィルタ：アクティブな店舗のみ
        stmt = select(Shop).options(selectinload(Shop.shop_hours)).filter(Shop.is_active == True)
        
        # キーワード検索
        if keyword:
            search_term = f"%{keyword}%"
            stmt = stmt.filter(
                or_(
                    Shop.name.ilike(search_term),
                    Shop.description.ilike(search_term)
                )
            )
        
        # カテゴリフィルタ
        if category:
            stmt = stmt.filter(Shop.category == category)
        
        # 最小評価フィルタ
        if min_rating is not None:
            stmt = stmt.filter(Shop.average_rating >= min_rating)
        
        # GPS距離フィルタ（簡易版）
        if latitude and longitude:
            lat_range = radius_km / 111
            lon_range = radius_km / 111
            
            stmt = stmt.filter(
                and_(
                    Shop.latitude.between(latitude - lat_range, latitude + lat_range),
                    Shop.longitude.between(longitude - lon_range, longitude + lon_range)
                )
            )
        
        # ソート処理
        if sort_by == "new":
            stmt = stmt.order_by(Shop.created_at.desc())
        elif sort_by == "popular":
            stmt = stmt.order_by(Shop.total_reservations.desc().nullslast())
        else:  # rating (デフォルト)
            stmt = stmt.order_by(Shop.average_rating.desc().nullslast())
        
        # 総件数取得
        count_stmt = select(func.count()).select_from(Shop).where(stmt.whereclause)
        total = await db.scalar(count_stmt)
        
        # ページネーション
        stmt = stmt.offset(offset).limit(limit)
        result = await db.execute(stmt)
        shops = result.scalars().all()
        
        shop_list = [ShopResponse.from_orm(shop) for shop in shops]
        
        return ShopListResponse(
            total=total or 0,
            limit=limit,
            offset=offset,
            items=shop_list
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"検索に失敗しました: {str(e)}"
        )


@router.get(
    "/{shop_id}",
    response_model=ShopResponse,
    summary="店舗詳細を取得",
    description="指定された店舗IDの詳細情報を取得"
)
async def get_shop(
    shop_id: str,
    db: AsyncSession = Depends(get_db)
) -> ShopResponse:
    """
    指定された店舗の詳細情報を取得します
    """
    try:
        result = await db.execute(
            select(Shop)
            .options(selectinload(Shop.shop_hours))
            .filter(Shop.id == shop_id)
        )
        shop = result.scalar_one_or_none()
        
        if not shop:
            raise HTTPException(
                status_code=404,
                detail="店舗が見つかりません"
            )
        
        return ShopResponse.from_orm(shop)
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"店舗情報取得に失敗しました: {str(e)}"
        )
