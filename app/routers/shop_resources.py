"""
Resource (予約リソース) エンドポイント — Generic Resource Foundation
Phase R1（CRUD基盤）+ Phase R2（削除安全性）

app/routers/shop_tables.pyと同じCRUD構造・同じtenant isolationパターンに
合わせている。

★★★ 重要な既存パターンとの差異（意図的な判断。Audit Report Section14参照）:
ShopTable/Staffの一覧取得(GET)は無認証（予約フォーム・店舗ページからの
公開参照用）だが、Resourceは現時点で顧客向けの参照経路が一切存在しない
（Booking Board・Realtime Tool・Week View等、いずれも未統合）ため、
GETも含めた全エンドポイントをowner認証必須とする。将来Reservation
Integration Phase以降、顧客向けの参照が必要になった時点で、その時の
要件に応じて個別に無認証化を検討する（先回りして今から無認証にしない）。

★★★ Phase R2で追加: Reservation.resource_id（nullable FK）が新設された
ため、削除時に「今後の有効な予約」チェックが必要になった
（_find_future_active_resource_reservations()。shop_tables.pyの
_find_future_active_table_reservations()と全く同じstatus/日時の定義を
再利用。新しい定義を発明しない）。
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
from app.models.reservation import Reservation
from app.schemas.resource import (
    ResourceCreateRequest, ResourceUpdateRequest, ResourceResponse
)
# shop_tables.py/staff.pyのdelete safetyヘルパーと全く同じsingle source of
# truth（JST-naive基準の「現在時刻」）を再利用する。新しいtimezone helperは
# 発明しない。
from app.routers.reservations import _reservation_basis_now

router = APIRouter(prefix="/api/v1/shops", tags=["shop-resources"])

# shop_tables.py の _ACTIVE_RESERVATION_STATUSES と全く同じ定義
# （新しい定義を発明しない。Section16「独自定義禁止」）。
_ACTIVE_RESERVATION_STATUSES = ("pending", "confirmed")


async def _find_future_active_resource_reservations(db: AsyncSession, resource_id: str) -> List[Reservation]:
    """
    指定リソースに割り当てられた「今後の有効な予約」を返す。
    app/routers/shop_tables.pyの_find_future_active_table_reservations()と
    全く同じ条件（status in (pending, confirmed) かつ reservation_date >=
    _reservation_basis_now()）。Phase R2完了時点ではresource_idを設定する
    書き込みAPIが存在しないため、実運用では常に空リストを返すが、将来
    Phase R3以降でresource_idが実際に割り当てられるようになった時点から、
    このチェックが安全に機能する。
    """
    result = await db.execute(
        select(Reservation).filter(
            Reservation.resource_id == resource_id,
            Reservation.status.in_(_ACTIVE_RESERVATION_STATUSES),
            Reservation.reservation_date >= _reservation_basis_now()
        )
    )
    return list(result.scalars().all())


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

    # Phase R2: 今後の有効な予約がまだ割り当てられている場合は削除をブロック
    # （shop_tables.py/staff.pyと全く同じ削除安全パターン）。
    upcoming = await _find_future_active_resource_reservations(db, resource_id)
    if upcoming:
        raise HTTPException(
            status_code=400,
            detail="このリソースに割り当てられた今後の予約があるため削除できません。先に予約を確認・キャンセルしてください"
        )

    await db.delete(resource)
    await db.commit()
    return {"success": True}
