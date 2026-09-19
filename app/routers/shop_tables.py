"""
ShopTable (テーブル・席) エンドポイント
飲食店などのテーブル/席の登録・編集・削除
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
from app.models.shop import Shop, ShopTable
from app.models.reservation import Reservation
from app.schemas.shop_table import (
    ShopTableCreateRequest, ShopTableUpdateRequest, ShopTableResponse
)

router = APIRouter(prefix="/api/v1/shops", tags=["shop-tables"])


async def _get_owned_shop(shop_id: str, current_user: CurrentUser, db: AsyncSession) -> Shop:
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")
    if shop.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="この店舗を編集する権限がありません")
    return shop


@router.get(
    "/{shop_id}/tables",
    response_model=List[ShopTableResponse],
    summary="テーブル一覧を取得"
)
async def list_tables(shop_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ShopTable).filter(ShopTable.shop_id == shop_id).order_by(ShopTable.display_order, ShopTable.created_at)
    )
    return [ShopTableResponse.from_orm(t) for t in result.scalars().all()]


@router.post(
    "/{shop_id}/tables",
    response_model=ShopTableResponse,
    summary="テーブルを追加"
)
async def create_table(
    shop_id: str,
    request: ShopTableCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await _get_owned_shop(shop_id, current_user, db)

    count_result = await db.execute(select(ShopTable).filter(ShopTable.shop_id == shop_id))
    display_order = len(count_result.scalars().all())

    table = ShopTable(
        id=str(uuid.uuid4()),
        shop_id=shop_id,
        name=request.name,
        capacity=request.capacity,
        is_active=True,
        display_order=display_order,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    db.add(table)
    await db.commit()
    await db.refresh(table)
    return ShopTableResponse.from_orm(table)


@router.put(
    "/{shop_id}/tables/{table_id}",
    response_model=ShopTableResponse,
    summary="テーブル情報を更新"
)
async def update_table(
    shop_id: str,
    table_id: str,
    request: ShopTableUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await _get_owned_shop(shop_id, current_user, db)

    table = await db.get(ShopTable, table_id)
    if not table or table.shop_id != shop_id:
        raise HTTPException(status_code=404, detail="テーブルが見つかりません")

    update_data = request.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(table, field, value)
    table.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(table)
    return ShopTableResponse.from_orm(table)


@router.delete(
    "/{shop_id}/tables/{table_id}",
    summary="テーブルを削除"
)
async def delete_table(
    shop_id: str,
    table_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await _get_owned_shop(shop_id, current_user, db)

    table = await db.get(ShopTable, table_id)
    if not table or table.shop_id != shop_id:
        raise HTTPException(status_code=404, detail="テーブルが見つかりません")

    # 今後の有効な予約がまだ割り当てられている場合は削除をブロック
    upcoming = await db.execute(
        select(Reservation).filter(
            Reservation.table_id == table_id,
            Reservation.status.in_(["pending", "confirmed"]),
            Reservation.reservation_date >= datetime.utcnow()
        )
    )
    if upcoming.scalars().first():
        raise HTTPException(
            status_code=400,
            detail="このテーブルに割り当てられた今後の予約があるため削除できません。先に予約を確認・キャンセルしてください"
        )

    await db.delete(table)
    await db.commit()
    return {"success": True}
