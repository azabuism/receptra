"""
ShopHoursOverride (特定日の営業時間) エンドポイント
Reservation Intelligence Phase D-3

特定のカレンダー日付だけの営業時間を登録するオーナー向けCRUD API。
実際の予約可否判定・空き状況計算・Realtime AIへの反映は
app/routers/reservations.py 側の _resolve_day_hours() 等が本テーブルを直接
参照して行う（このルーターはCRUDのみを担当し、判定ロジックをここに重複実装
しない。ShopBreakTime（Phase D-2）と同じ設計方針）。

エンドポイント構成（ユーザー承認済み仕様。Section19-24の監査結果に基づく設計）:
- GET    /{shop_id}/hours-overrides                  一覧取得
- PUT    /{shop_id}/hours-overrides/{target_date}     作成・更新（upsert）
- DELETE /{shop_id}/hours-overrides/{target_date}     削除

upsertを選んだ理由（既存規約の監査結果）: 曜日ごとの通常営業時間(ShopHours)は
「1週間分をまとめて丸ごと置き換えるPUT」という規約だが、これは常にちょうど7行
存在する固定集合だからこそ成立する規約であり、疎な日付キーの集合である
Special Hoursにはそのまま当てはまらない。一方、休憩時間(ShopBreakTime、
Phase D-2)は複数行を許容するPOST作成＋DELETEのみだが、Special Hoursは
UNIQUE(shop_id, target_date)の単一行であり、オーナー画面の「[編集][削除]」
という単一レコード編集の要件（Section21）に最も自然に合うのは、target_date
をキーとした単一リソースのPUT（作成済みなら更新、無ければ新規作成）である。
そのため両者の中間、「target_date単位のPUT upsert」という設計にした。
"""

import uuid
from datetime import date as date_type, datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.schemas.user import CurrentUser
from app.models.shop import ShopHoursOverride
from app.schemas.shop_hours_override import ShopHoursOverrideUpsertRequest, ShopHoursOverrideResponse
from app.routers.shop_closures import _get_owned_shop

router = APIRouter(prefix="/api/v1/shops", tags=["shop-hours-overrides"])


@router.get(
    "/{shop_id}/hours-overrides",
    response_model=List[ShopHoursOverrideResponse],
    summary="特定日の営業時間の一覧を取得",
)
async def list_hours_overrides(shop_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ShopHoursOverride)
        .filter(ShopHoursOverride.shop_id == shop_id)
        .order_by(ShopHoursOverride.target_date)
    )
    return [ShopHoursOverrideResponse.from_orm(o) for o in result.scalars().all()]


@router.put(
    "/{shop_id}/hours-overrides/{target_date}",
    response_model=ShopHoursOverrideResponse,
    summary="特定日の営業時間を登録・更新",
    description="指定した1日だけの営業時間を、通常の曜日ごとの営業時間とは別に登録する（既に登録済みの日付であれば内容を更新する）",
)
async def upsert_hours_override(
    shop_id: str,
    target_date: date_type,
    request: ShopHoursOverrideUpsertRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_shop(shop_id, current_user, db)

    existing_result = await db.execute(
        select(ShopHoursOverride).filter(
            ShopHoursOverride.shop_id == shop_id, ShopHoursOverride.target_date == target_date
        )
    )
    existing = existing_result.scalar_one_or_none()

    now = datetime.utcnow()
    if existing is not None:
        existing.opening_time = request.opening_time
        existing.closing_time = request.closing_time
        existing.last_order_time = request.last_order_time
        existing.closes_next_day = request.closes_next_day
        existing.last_order_next_day = request.last_order_next_day
        existing.updated_at = now
        override = existing
    else:
        override = ShopHoursOverride(
            id=str(uuid.uuid4()),
            shop_id=shop_id,
            target_date=target_date,
            opening_time=request.opening_time,
            closing_time=request.closing_time,
            last_order_time=request.last_order_time,
            closes_next_day=request.closes_next_day,
            last_order_next_day=request.last_order_next_day,
            created_at=now,
            updated_at=now,
        )
        db.add(override)

    await db.commit()
    await db.refresh(override)
    return ShopHoursOverrideResponse.from_orm(override)


@router.delete(
    "/{shop_id}/hours-overrides/{target_date}",
    summary="特定日の営業時間を削除",
    description="削除すると、その日は通常の曜日ごとの営業時間（ShopHours）に従う",
)
async def delete_hours_override(
    shop_id: str,
    target_date: date_type,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_shop(shop_id, current_user, db)
    result = await db.execute(
        select(ShopHoursOverride).filter(
            ShopHoursOverride.shop_id == shop_id, ShopHoursOverride.target_date == target_date
        )
    )
    override = result.scalar_one_or_none()
    if override is None:
        raise HTTPException(status_code=404, detail="指定された特定日の営業時間が見つかりません")
    await db.delete(override)
    await db.commit()
    return {"success": True}
