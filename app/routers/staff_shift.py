"""
Phase3E-2: スタッフシフト管理 API Router
週次シフト（StaffWeeklyShift）・日付単位のシフト調整（StaffShiftOverride）

設計方針（重要）:
- 全エンドポイントはオーナー認証必須（顧客向けに公開する情報ではない。実際の
  予約可否判定への反映はapp/routers/reservations.py側が直接DBを参照して行う）。
- 所有権チェックはapp/routers/staff.pyの_get_owned_staffをそのまま再利用し、
  認可ロジックを二重実装しない。
- 編集はadd/remove方式（PUTでの部分更新は提供しない）。既存のStaffService
  割り当てAPIと同じ思想。
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.schemas.user import CurrentUser
from app.models.staff_shift import StaffWeeklyShift, StaffShiftOverride
from app.routers.staff import _get_owned_staff
from app.schemas.staff_shift import (
    WeeklyShiftCreateRequest, WeeklyShiftResponse,
    ShiftOverrideCreateRequest, ShiftOverrideResponse,
    _parse_hhmm,
)

router = APIRouter(prefix="/api/v1/staff/{staff_id}", tags=["staff_shift"])


# ===== 週次シフト =====

@router.post("/weekly-shifts", response_model=WeeklyShiftResponse, summary="週次シフトを1コマ登録")
async def create_weekly_shift(
    staff_id: str,
    request: WeeklyShiftCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_staff(staff_id, current_user, db)

    now = datetime.utcnow()
    shift = StaffWeeklyShift(
        staff_id=staff_id,
        day_of_week=request.day_of_week,
        start_time=_parse_hhmm(request.start_time),
        end_time=_parse_hhmm(request.end_time),
        created_at=now,
        updated_at=now,
    )
    db.add(shift)
    await db.commit()
    await db.refresh(shift)
    return shift


@router.get("/weekly-shifts", response_model=List[WeeklyShiftResponse], summary="週次シフト一覧を取得")
async def list_weekly_shifts(
    staff_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_staff(staff_id, current_user, db)
    result = await db.execute(
        select(StaffWeeklyShift)
        .filter(StaffWeeklyShift.staff_id == staff_id)
        .order_by(StaffWeeklyShift.day_of_week, StaffWeeklyShift.start_time)
    )
    return list(result.scalars().all())


@router.delete("/weekly-shifts/{shift_id}", summary="週次シフトを削除")
async def delete_weekly_shift(
    staff_id: str,
    shift_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_staff(staff_id, current_user, db)
    shift = await db.get(StaffWeeklyShift, shift_id)
    if not shift or shift.staff_id != staff_id:
        raise HTTPException(status_code=404, detail="指定されたシフトが見つかりません")
    await db.delete(shift)
    await db.commit()
    return {"success": True}


# ===== 日付単位のシフト調整（休み・時間変更・部分的な不在） =====

@router.post("/shift-overrides", response_model=ShiftOverrideResponse, summary="日付単位のシフト調整を登録")
async def create_shift_override(
    staff_id: str,
    request: ShiftOverrideCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_staff(staff_id, current_user, db)

    now = datetime.utcnow()
    override = StaffShiftOverride(
        staff_id=staff_id,
        target_date=request.target_date,
        override_type=request.override_type,
        start_time=_parse_hhmm(request.start_time) if request.start_time else None,
        end_time=_parse_hhmm(request.end_time) if request.end_time else None,
        note=request.note,
        created_at=now,
        updated_at=now,
    )
    db.add(override)
    await db.commit()
    await db.refresh(override)
    return override


@router.get("/shift-overrides", response_model=List[ShiftOverrideResponse], summary="日付単位のシフト調整一覧を取得")
async def list_shift_overrides(
    staff_id: str,
    date_from: Optional[str] = Query(None, description="この日付以降のみ取得（YYYY-MM-DD）"),
    date_to: Optional[str] = Query(None, description="この日付以前のみ取得（YYYY-MM-DD）"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_staff(staff_id, current_user, db)
    query = select(StaffShiftOverride).filter(StaffShiftOverride.staff_id == staff_id)
    if date_from:
        query = query.filter(StaffShiftOverride.target_date >= date_from)
    if date_to:
        query = query.filter(StaffShiftOverride.target_date <= date_to)
    query = query.order_by(StaffShiftOverride.target_date)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.delete("/shift-overrides/{override_id}", summary="日付単位のシフト調整を削除")
async def delete_shift_override(
    staff_id: str,
    override_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_staff(staff_id, current_user, db)
    override = await db.get(StaffShiftOverride, override_id)
    if not override or override.staff_id != staff_id:
        raise HTTPException(status_code=404, detail="指定されたシフト調整が見つかりません")
    await db.delete(override)
    await db.commit()
    return {"success": True}
