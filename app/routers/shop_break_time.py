"""
ShopBreakTime (休憩・予約停止時間) エンドポイント
Reservation Intelligence Phase D-2

営業時間内の一部を「営業はしているが予約は受け付けない」時間帯として登録する
オーナー向けCRUD API。実際の予約可否判定への反映（check_availability /
create_reservation / update_reservation / get_availability）は
app/routers/reservations.py 側が本テーブルを直接参照して行う（このルーターは
CRUDと登録時の整合性チェックのみを担当し、判定ロジックをここに重複実装しない）。

登録時に行う整合性チェック（ユーザー承認済み仕様）:
1. 対象曜日のShopHoursが存在し、定休日でないこと（休憩は営業時間の内側にしか
   意味を持たないため）。
2. 休憩時間帯が、その曜日の営業時間（日跨ぎ対応込み）に完全に収まっていること。
3. 同じ曜日に既に登録されている他の休憩時間帯と重なっていないこと（触れるだけ
   ＝隣接は許容する。Reservation Intelligence Phase B/C以来の「触れるのはOK、
   超過はNG」という境界値の扱いと同じ）。
   この重複チェックはオーナーがこの画面を操作したときにだけ行われる低頻度の
   設定変更であり、予約の同時多重作成のような競合安全性は不要と判断し、
   FOR UPDATEロック等は使わない（シンプルなSELECT→検証→INSERTで十分。
   複雑なロック機構が必要になった場合はSTOPして報告する、という仕様書の
   方針に基づく判断）。
"""

import uuid
from datetime import datetime, time as time_type
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.schemas.user import CurrentUser
from app.models.shop import ShopBreakTime, ShopHours
from app.schemas.shop_break_time import ShopBreakTimeCreateRequest, ShopBreakTimeResponse
from app.routers.shop_closures import _get_owned_shop

router = APIRouter(prefix="/api/v1/shops", tags=["shop-break-times"])


def _effective_minutes(t: time_type, next_day: bool) -> int:
    """時刻(time)を、日跨ぎフラグを考慮した「起点日からの分数」に変換する"""
    return t.hour * 60 + t.minute + (1440 if next_day else 0)


@router.get(
    "/{shop_id}/break-times",
    response_model=List[ShopBreakTimeResponse],
    summary="休憩・予約停止時間の一覧を取得",
)
async def list_break_times(shop_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ShopBreakTime)
        .filter(ShopBreakTime.shop_id == shop_id)
        .order_by(ShopBreakTime.day_of_week, ShopBreakTime.start_time)
    )
    return [ShopBreakTimeResponse.from_orm(b) for b in result.scalars().all()]


@router.post(
    "/{shop_id}/break-times",
    response_model=ShopBreakTimeResponse,
    summary="休憩・予約停止時間を登録",
    description="営業時間内の一部を、予約を受け付けない時間帯として登録する",
)
async def create_break_time(
    shop_id: str,
    request: ShopBreakTimeCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_shop(shop_id, current_user, db)

    hours_result = await db.execute(
        select(ShopHours).filter(
            ShopHours.shop_id == shop_id, ShopHours.day_of_week == request.day_of_week
        )
    )
    hours = hours_result.scalar_one_or_none()
    if hours is None or hours.is_closed:
        raise HTTPException(
            status_code=400,
            detail="この曜日の営業時間が設定されていないため、休憩時間を登録できません。先に営業時間を設定してください",
        )

    start_eff = _effective_minutes(request.start_time, request.start_next_day)
    end_eff = _effective_minutes(request.end_time, request.end_next_day)
    opening_eff = _effective_minutes(hours.opening_time, False)
    closing_eff = _effective_minutes(hours.closing_time, hours.closes_next_day)

    # 「触れるのはOK、超過はNG」（Phase B/C以来の境界値の扱いと同じ）
    if start_eff < opening_eff or end_eff > closing_eff:
        raise HTTPException(
            status_code=400,
            detail="休憩時間は営業時間の範囲内で指定してください",
        )

    existing_result = await db.execute(
        select(ShopBreakTime).filter(
            ShopBreakTime.shop_id == shop_id, ShopBreakTime.day_of_week == request.day_of_week
        )
    )
    for existing in existing_result.scalars().all():
        existing_start_eff = _effective_minutes(existing.start_time, existing.start_next_day)
        existing_end_eff = _effective_minutes(existing.end_time, existing.end_next_day)
        # 触れるだけ（隣接）はOK。重なっている場合のみ拒否。
        if existing_start_eff < end_eff and existing_end_eff > start_eff:
            raise HTTPException(
                status_code=400,
                detail="指定した時間帯は既に登録されている休憩時間と重なっています",
            )

    now = datetime.utcnow()
    break_time = ShopBreakTime(
        id=str(uuid.uuid4()),
        shop_id=shop_id,
        day_of_week=request.day_of_week,
        start_time=request.start_time,
        end_time=request.end_time,
        start_next_day=request.start_next_day,
        end_next_day=request.end_next_day,
        created_at=now,
        updated_at=now,
    )
    db.add(break_time)
    await db.commit()
    await db.refresh(break_time)
    return ShopBreakTimeResponse.from_orm(break_time)


@router.delete(
    "/{shop_id}/break-times/{break_time_id}",
    summary="休憩・予約停止時間を削除",
)
async def delete_break_time(
    shop_id: str,
    break_time_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_shop(shop_id, current_user, db)
    break_time = await db.get(ShopBreakTime, break_time_id)
    if not break_time or break_time.shop_id != shop_id:
        raise HTTPException(status_code=404, detail="指定された休憩時間が見つかりません")
    await db.delete(break_time)
    await db.commit()
    return {"success": True}
