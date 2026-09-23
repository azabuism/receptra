"""
ShopTable (テーブル・席) エンドポイント
飲食店などのテーブル/席の登録・編集・削除
"""

import uuid
from datetime import datetime
from types import SimpleNamespace
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
from app.routers.reservations import _validate_table_capacity, _reservation_basis_now

router = APIRouter(prefix="/api/v1/shops", tags=["shop-tables"])

# delete_table()が元々使っていた「今後の有効な予約」の定義（status=pending/confirmed
# かつ reservation_date >= 現在時刻）。Resource Mutation Safetyでもこの定義を
# そのまま再利用する（新しい定義を発明しない。Section4/10/11）。
#
# Reservation Time Basis Consistencyフェーズで修正: 以前は「現在時刻」に
# datetime.utcnow()（真のUTC時刻）を使っていたが、reservation_date自体は
# JST-naiveな値として保存されている（app/routers/reservations.pyの
# _reservation_basis_now()のdocstring参照。3つの予約経路すべてがJSTの
# 壁時計時刻をnaive datetimeとして保存する、単一の一貫した設計）。
# そのため日本時間の日付境界付近で最大9時間のズレが生じ、既にJST基準では
# 過去になった予約を「まだ未来」と誤判定しうるバグがあった
# （delete_table()/update_table()のcapacity decrease検証を誤ってブロック
# する可能性があった）。
#
# 修正は、reservations.py側がPhase3E-3で確立した既存のsingle source of
# truthである_reservation_basis_now()（reservation_date系の値と比較する
# ための「JST基準に揃えた現在時刻」）を、新しいtimezone helperを発明せずに
# そのまま再利用するだけ。datetime.utcnow()をこの関数に置き換えたこと
# 以外、クエリの構造・status条件・比較演算子(>=)は一切変更していない。
_ACTIVE_RESERVATION_STATUSES = ("pending", "confirmed")


async def _find_future_active_table_reservations(db: AsyncSession, table_id: str) -> List[Reservation]:
    """
    指定テーブルに割り当てられた「今後の有効な予約」を返す。
    delete_table()が元から持っていたクエリ条件をそのまま抽出したもので、
    status条件・>=という境界の意味は一切変更していない。「現在時刻」の
    基準のみ、reservations.pyのsingle source of truthである
    _reservation_basis_now()（JST-naive基準）に統一した
    （Reservation Time Basis Consistencyフェーズ）。
    """
    result = await db.execute(
        select(Reservation).filter(
            Reservation.table_id == table_id,
            Reservation.status.in_(_ACTIVE_RESERVATION_STATUSES),
            Reservation.reservation_date >= _reservation_basis_now()
        )
    )
    return list(result.scalars().all())


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

    # Resource Mutation Safety: capacityがリクエストに含まれ、かつ現在値より
    # 減少する場合のみ、既存の今後の有効な予約と矛盾しないかを検証する。
    # 増加・同値の場合は既存予約を壊す可能性がないため検証不要（Section6/7/15）。
    #
    # 検証は「どの予約を見るか」（_find_future_active_table_reservations、
    # delete_table()と同じ定義）と「その予約人数が新capacityに収まるか」
    # （_validate_table_capacity、Capacity Mutation Safetyフェーズと同じ
    # source of truth）を分離したまま行う（Section14。1つの巨大なhelperへ
    # 統合しない）。SimpleNamespaceで新capacityだけを持つ軽量なオブジェクトを
    # 作り、_validate_table_capacity(table, party_size)の既存シグネチャ
    # （table.capacityを参照するだけ）をそのまま再利用する。
    #
    # 検証はtable.capacityへの代入より前に行うため、失敗時は
    # capacity・name等いずれのフィールドも一切適用されない（atomicity。
    # Section17/18）。
    if "capacity" in update_data and update_data["capacity"] is not None:
        new_capacity = update_data["capacity"]
        if new_capacity < table.capacity:
            future_reservations = await _find_future_active_table_reservations(db, table_id)
            prospective_table = SimpleNamespace(capacity=new_capacity)
            conflicting = [
                r for r in future_reservations
                if _validate_table_capacity(prospective_table, r.number_of_people) is not None
            ]
            if conflicting:
                raise HTTPException(
                    status_code=400,
                    detail="この席には、変更後の定員を超える今後の予約があるため、定員を変更できません"
                )

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
    # （Resource Mutation Safetyフェーズで_find_future_active_table_reservations()
    # へ抽出。判定条件・挙動は一切変更していない）
    upcoming = await _find_future_active_table_reservations(db, table_id)
    if upcoming:
        raise HTTPException(
            status_code=400,
            detail="このテーブルに割り当てられた今後の予約があるため削除できません。先に予約を確認・キャンセルしてください"
        )

    await db.delete(table)
    await db.commit()
    return {"success": True}
