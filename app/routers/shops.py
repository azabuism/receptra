"""
Shop (店舗) エンドポイント
店舗の登録、検索、管理機能
"""

import logging
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
    ShopUpdateRequest, ShopHoursBulkUpdateRequest,
    ShopNotificationSettingsResponse, ShopNotificationSettingsUpdateRequest,
)
from app.deps import get_current_user, get_current_tenant
from app.schemas.user import CurrentUser
from app.models.user import Tenant
from app.taxonomy import resolve_business_type
from app.language_registry import effective_ai_languages, effective_languages

router = APIRouter(prefix="/api/v1/shops", tags=["shops"])
logger = logging.getLogger("receptra.shops")


def _build_shop_response(shop: Shop) -> ShopResponse:
    """ShopResponse を組み立てる。アップロード済み画像がある場合はそちらのURLを優先する"""
    response = ShopResponse.from_orm(shop)
    if getattr(shop, "thumbnail_mime_type", None):
        response.thumbnail_url = f"/api/v1/media/shop-thumbnail/{shop.id}"
    if getattr(shop, "cover_mime_type", None):
        response.cover_image_url = f"/api/v1/media/shop-cover/{shop.id}"
    if getattr(shop, "logo_mime_type", None):
        response.logo_url = f"/api/v1/media/shop-logo/{shop.id}"
    # Phase 5A: DB上はNone（未設定＝この機能導入前からの既存店舗）のままだが、
    # ShopResponse上は常に「実際に有効な言語リスト」を返す（from_orm はORMの
    # 属性値がNoneの場合、スキーマ側の default_factory を適用しないため、
    # ここで明示的にフォールバックする）。日本語は常に含まれる。
    response.ai_supported_languages = effective_ai_languages(shop.ai_supported_languages)
    response.staff_supported_languages = effective_languages(shop.staff_supported_languages)
    return response


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
            business_type=resolve_business_type(request.category),
            address=request.address,
            latitude=request.latitude,
            longitude=request.longitude,
            phone=request.phone,
            email=request.email,
            website=request.website,
            thumbnail_url=request.thumbnail_url,
            cover_image_url=request.cover_image_url,
            features=request.features or [],
            reservation_duration_minutes=request.reservation_duration_minutes or 90,
            ai_supported_languages=request.ai_supported_languages,
            staff_supported_languages=request.staff_supported_languages,
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
        shop_response = _build_shop_response(shop)
        
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
    shop_list = [_build_shop_response(shop) for shop in shops]
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
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db)
) -> ShopResponse:
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")
    if shop.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="この店舗を編集する権限がありません")

    update_data = request.dict(exclude_unset=True)

    # 予約受付をONにする操作は、課金アクティベーション（初期費用＋月額サブスク）が
    # 済んでいるテナントのみ許可する。既にONの店舗（既存店舗など）はそのまま維持できる。
    if update_data.get("reservations_enabled") is True and not shop.reservations_enabled:
        if tenant.subscription_status != "active":
            raise HTTPException(
                status_code=402,
                detail="予約受付を開始するには、お支払い設定（初期費用20,000円・月額5,000円）の完了が必要です。"
                       "「/api/v1/billing/activate」からお手続きください。",
            )

    for field, value in update_data.items():
        setattr(shop, field, value)
    if "category" in update_data:
        shop.business_type = resolve_business_type(update_data["category"])
    shop.updated_at = datetime.utcnow()

    await db.commit()

    result = await db.execute(
        select(Shop).options(selectinload(Shop.shop_hours)).filter(Shop.id == shop_id)
    )
    shop = result.scalar_one()
    return _build_shop_response(shop)


@router.get(
    "/{shop_id}/notification-settings",
    response_model=ShopNotificationSettingsResponse,
    summary="予約通知の連絡先設定を取得（オーナー専用）",
    description=(
        "新しい予約が入ったときにAIスタッフが電話で知らせる先の設定を取得する。"
        "オーナー認証必須・Customer向けAPIには一切含まれない非公開情報。"
    ),
)
async def get_shop_notification_settings(
    shop_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ShopNotificationSettingsResponse:
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")
    if shop.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="この店舗を編集する権限がありません")

    return ShopNotificationSettingsResponse(
        shop_id=shop.id,
        reservation_notification_phone=shop.reservation_notification_phone,
        reservation_phone_notification_enabled=bool(shop.reservation_phone_notification_enabled),
    )


@router.put(
    "/{shop_id}/notification-settings",
    response_model=ShopNotificationSettingsResponse,
    summary="予約通知の連絡先設定を保存（オーナー専用）",
    description=(
        "新しい予約が入ったときにAIスタッフが電話で知らせる先の番号とON/OFFを保存する。"
        "本フェーズでは実際の架電機能は実装せず、設定の保存のみを行う（Phase3H）。"
    ),
)
async def update_shop_notification_settings(
    shop_id: str,
    request: ShopNotificationSettingsUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ShopNotificationSettingsResponse:
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")
    if shop.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="この店舗を編集する権限がありません")

    update_data = request.dict(exclude_unset=True)

    # 送られなかったフィールドは既存値を維持した上で、更新後の電話番号を先に計算する。
    new_phone = (
        update_data["reservation_notification_phone"]
        if "reservation_notification_phone" in update_data
        else shop.reservation_notification_phone
    )
    # このリクエストで実際にenabledを指定したかどうかを区別する（重要）。
    # 「番号を消すだけ」のリクエスト（enabledは触っていない）と、
    # 「このリクエストで明示的にONにしようとしている」リクエストを区別しないと、
    # 既にON状態だった店舗の電話番号を消そうとしただけで、意図しない
    # 「ONにするには番号が必要です」エラーになってしまう
    # （番号を消す操作自体は常に成功すべきで、その代わりONは自動でOFFへ戻す）。
    requested_enabled = update_data.get("reservation_phone_notification_enabled")

    if requested_enabled is True and not new_phone:
        raise HTTPException(
            status_code=400,
            detail="通知をONにするには、先に電話番号を登録してください。",
        )

    new_enabled = requested_enabled if requested_enabled is not None else shop.reservation_phone_notification_enabled
    # 電話番号が空になった（またはそもそも無い）のにONのまま、という矛盾した
    # 状態を絶対に作らない。番号を消す操作自体は常に成功させ、その代わり
    # ONは安全側で自動的にOFFへ戻す。
    if not new_phone:
        new_enabled = False

    shop.reservation_notification_phone = new_phone
    shop.reservation_phone_notification_enabled = bool(new_enabled)
    shop.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(shop)

    # 電話番号は個人情報のため、フルでログに残さない（末尾4桁のみのマスク表示）。
    masked = ("****" + shop.reservation_notification_phone[-4:]) if shop.reservation_notification_phone else None
    logger.info(
        "予約通知連絡先を更新 shop_id=%s enabled=%s phone_masked=%s",
        shop_id, shop.reservation_phone_notification_enabled, masked,
    )

    return ShopNotificationSettingsResponse(
        shop_id=shop.id,
        reservation_notification_phone=shop.reservation_notification_phone,
        reservation_phone_notification_enabled=shop.reservation_phone_notification_enabled,
    )


@router.put(
    "/{shop_id}/hours",
    response_model=ShopResponse,
    summary="営業時間・定休日を更新",
    description="送信された曜日分の営業時間で丸ごと置き換える"
)
async def update_shop_hours(
    shop_id: str,
    request: ShopHoursBulkUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ShopResponse:
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")
    if shop.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="この店舗を編集する権限がありません")

    # 既存の営業時間をすべて削除してから、送信された内容で作り直す
    existing = await db.execute(select(ShopHours).filter(ShopHours.shop_id == shop_id))
    for hour in existing.scalars().all():
        await db.delete(hour)
    await db.flush()

    for hour in request.hours:
        db.add(ShopHours(
            id=str(uuid.uuid4()),
            shop_id=shop_id,
            day_of_week=hour.day_of_week,
            opening_time=hour.opening_time,
            closing_time=hour.closing_time,
            is_closed=hour.is_closed,
            last_order_time=hour.last_order_time,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        ))

    shop.updated_at = datetime.utcnow()
    await db.commit()

    result = await db.execute(
        select(Shop).options(selectinload(Shop.shop_hours)).filter(Shop.id == shop_id)
    )
    shop = result.scalar_one()
    return _build_shop_response(shop)


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

    try:
        await db.delete(shop)
        await db.commit()
    except HTTPException:
        raise
    except Exception as e:
        # Phase3H Workstream D: 想定外の関連データ（将来追加されるテーブル等）が
        # 残っていた場合でも、Ownerには生の技術的エラーではなく分かりやすい
        # メッセージを返す（起きた場合は開発者側でログを確認して対応する）。
        await db.rollback()
        logger.error("店舗削除に失敗 shop_id=%s: %s", shop_id, e)
        raise HTTPException(
            status_code=500,
            detail="店舗の削除に失敗しました。時間をおいて再度お試しいただくか、サポートまでご連絡ください。",
        )
    return {"success": True}


@router.get(
    "/search",
    response_model=ShopListResponse,
    summary="店舗を検索",
    description="キーワード、カテゴリ、位置情報で店舗を検索"
)
async def search_shops(
    keyword: Optional[str] = Query(None, description="キーワード検索"),
    category: Optional[str] = Query(None, description="カテゴリフィルタ（サブカテゴリー名の完全一致）"),
    business_type: Optional[str] = Query(None, description="業種フィルタ（restaurant, beauty, hotel, education, medical, fitness, entertainment, other）"),
    location: Optional[str] = Query(None, description="住所の部分一致検索（都道府県名・市区町村名など）"),
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
    - **category**: 詳細サブカテゴリーでフィルタ（例：ラーメン）
    - **business_type**: 業種（大分類）でフィルタ（例：restaurant）
    - **location**: 住所の部分一致検索（例：東京都、渋谷区）
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
        
        # カテゴリフィルタ（サブカテゴリー完全一致）
        if category:
            stmt = stmt.filter(Shop.category == category)

        # 業種フィルタ（大分類）
        if business_type:
            stmt = stmt.filter(Shop.business_type == business_type)

        # 住所の部分一致検索（都道府県・市区町村など）
        if location:
            stmt = stmt.filter(Shop.address.ilike(f"%{location}%"))

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
        
        shop_list = [_build_shop_response(shop) for shop in shops]
        
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
        
        return _build_shop_response(shop)
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"店舗情報取得に失敗しました: {str(e)}"
        )
