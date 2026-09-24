"""
Resource (予約リソース) エンドポイント — Generic Resource Foundation Phase R1

app/routers/shop_tables.pyと同じCRUD構造・同じtenant isolationパターンに
合わせている。Phase R1では予約（Reservation）との統合を一切行わないため、
delete時の「今後の有効な予約」チェックはまだ存在しない
（app/models/resource.pyのdocstring参照。将来のReservation Integration
Phaseで、_find_future_active_table_reservations()と同型のヘルパーを
追加する想定）。

★★★ 重要な既存パターンとの差異（意図的な判断。Audit Report Section14参照）:
ShopTable/Staffの一覧取得(GET)は無認証（予約フォーム・店舗ページからの
公開参照用）だが、Resourceは現時点で顧客向けの参照経路が一切存在しない
（Booking Board・Realtime Tool・Week View等、いずれも未統合）ため、
GETも含めた全エンドポイントをowner認証必須とする。将来Reservation
Integration Phase以降、顧客向けの参照が必要になった時点で、その時の
要件に応じて個別に無認証化を検討する（先回りして今から無認証にしない）。
"""

import uuid
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.schemas.user import CurrentUser
from app.models.shop import Shop
from app.models.resource import Resource
from app.schemas.resource import (
    ResourceCreateRequest, ResourceUpdateRequest, ResourceResponse
)

router = APIRouter(prefix="/api/v1/shops", tags=["shop-resources"])


async def _get_owned_shop(shop_id: str, current_user: CurrentUser, db: AsyncSession) -> Shop:
    """app/routers/shop_tables.pyの同名関数と同じtenant isolationパターン
    （個別ファイルでの重複だが、既存の複数routerファイルでも同様に個別定義
    されている既存の慣習に合わせている。共通utilへの抽出は本フェーズの
    スコープ外）。"""
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")
    if shop.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="この店舗を編集する権限がありません")
    return shop


@router.get(
    "/{shop_id}/resources",
    response_model=List[ResourceResponse],
    summary="予約リソース一覧を取得（オーナー用）"
)
async def list_resources(
    shop_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await _get_owned_shop(shop_id, current_user, db)

    result = await db.execute(
        select(Resource).filter(Resource.shop_id == shop_id).order_by(Resource.display_order, Resource.created_at)
    )
    return [ResourceResponse.from_orm(r) for r in result.scalars().all()]


@router.post(
    "/{shop_id}/resources",
    response_model=ResourceResponse,
    summary="予約リソースを追加"
)
async def create_resource(
    shop_id: str,
    request: ResourceCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await _get_owned_shop(shop_id, current_user, db)

    count_result = await db.execute(select(Resource).filter(Resource.shop_id == shop_id))
    display_order = len(count_result.scalars().all())

    resource = Resource(
        id=str(uuid.uuid4()),
        shop_id=shop_id,
        name=request.name,
        resource_type=request.resource_type,
        capacity=request.capacity,
        is_active=True,
        display_order=display_order,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    db.add(resource)
    await db.commit()
    await db.refresh(resource)
    return ResourceResponse.from_orm(resource)


@router.put(
    "/{shop_id}/resources/{resource_id}",
    response_model=ResourceResponse,
    summary="予約リソース情報を更新"
)
async def update_resource(
    shop_id: str,
    resource_id: str,
    request: ResourceUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await _get_owned_shop(shop_id, current_user, db)

    resource = await db.get(Resource, resource_id)
    if not resource or resource.shop_id != shop_id:
        raise HTTPException(status_code=404, detail="予約リソースが見つかりません")

    update_data = request.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(resource, field, value)
    resource.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(resource)
    return ResourceResponse.from_orm(resource)


@router.delete(
    "/{shop_id}/resources/{resource_id}",
    summary="予約リソースを削除"
)
async def delete_resource(
    shop_id: str,
    resource_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await _get_owned_shop(shop_id, current_user, db)

    resource = await db.get(Resource, resource_id)
    if not resource or resource.shop_id != shop_id:
        raise HTTPException(status_code=404, detail="予約リソースが見つかりません")

    # Phase R1ではReservationとの統合が無いため、今後の有効な予約チェックは
    # まだ存在しない（app/models/resource.pyのdocstring参照）。
    await db.delete(resource)
    await db.commit()
    return {"success": True}
