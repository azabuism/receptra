"""
Staff API Router
スタッフ/講師管理エンドポイント（美容院のスタイリスト、スクールの講師、クリニックの担当医など）
"""

import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.schemas.user import CurrentUser
from app.models import Staff, Shop, Service, StaffService
from app.models.reservation import Reservation
from app.schemas.staff import (
    StaffCreateRequest, StaffUpdateRequest, StaffResponse, StaffServiceAssignmentResponse
)

router = APIRouter(prefix="/api/v1/staff", tags=["staff"])


def _to_response(staff: Staff) -> StaffResponse:
    return StaffResponse(
        id=staff.id,
        shop_id=staff.shop_id,
        name=staff.name,
        email=staff.email,
        phone=staff.phone,
        bio=staff.bio,
        photo_url=staff.photo_url,
        specialty=staff.specialty,
        qualifications=staff.qualifications,
        position=staff.position,
        is_active=(staff.is_active == "active"),
        total_reservations=staff.total_reservations or 0,
        average_rating=staff.average_rating or 0,
        created_at=staff.created_at,
    )


async def _get_owned_shop(shop_id: str, current_user: CurrentUser, db: AsyncSession) -> Shop:
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")
    if shop.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="この店舗を編集する権限がありません")
    return shop


async def _get_owned_staff(staff_id: str, current_user: CurrentUser, db: AsyncSession) -> Staff:
    staff = await db.get(Staff, staff_id)
    if not staff:
        raise HTTPException(status_code=404, detail="スタッフが見つかりません")
    await _get_owned_shop(staff.shop_id, current_user, db)
    return staff


@router.get("", response_model=List[StaffResponse], summary="スタッフ一覧を取得")
async def get_staff(
    shop_id: Optional[str] = Query(None, description="店舗IDで絞り込み"),
    specialty: Optional[str] = Query(None, description="専門分野で絞り込み"),
    is_active: Optional[bool] = Query(None, description="有効/無効で絞り込み"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """スタッフ一覧を取得（店舗ページ・予約フォームからの公開参照用）"""
    query = select(Staff)

    if shop_id:
        query = query.filter(Staff.shop_id == shop_id)
    if specialty:
        query = query.filter(Staff.specialty.ilike(f"%{specialty}%"))
    if is_active is not None:
        query = query.filter(Staff.is_active == ("active" if is_active else "inactive"))

    query = query.order_by(Staff.created_at).offset(skip).limit(limit)
    result = await db.execute(query)
    return [_to_response(s) for s in result.scalars().all()]


@router.get("/{staff_id}", response_model=StaffResponse, summary="スタッフ詳細を取得")
async def get_staff_by_id(staff_id: str, db: AsyncSession = Depends(get_db)):
    staff = await db.get(Staff, staff_id)
    if not staff:
        raise HTTPException(status_code=404, detail="スタッフが見つかりません")
    return _to_response(staff)


@router.post("", response_model=StaffResponse, summary="スタッフを新規登録")
async def create_staff(
    request: StaffCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_shop(request.shop_id, current_user, db)

    now = datetime.utcnow()
    staff = Staff(
        id=str(uuid.uuid4()),
        shop_id=request.shop_id,
        name=request.name,
        email=request.email,
        phone=request.phone,
        bio=request.bio,
        photo_url=request.photo_url,
        specialty=request.specialty,
        qualifications=request.qualifications,
        position=request.position,
        is_active="active",
        total_reservations=0,
        average_rating=0,
        created_at=now,
        updated_at=now,
    )
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    return _to_response(staff)


@router.put("/{staff_id}", response_model=StaffResponse, summary="スタッフ情報を更新")
async def update_staff(
    staff_id: str,
    request: StaffUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    staff = await _get_owned_staff(staff_id, current_user, db)

    update_data = request.dict(exclude_unset=True)
    if "is_active" in update_data:
        is_active_bool = update_data.pop("is_active")
        staff.is_active = "active" if is_active_bool else "inactive"
    for field, value in update_data.items():
        setattr(staff, field, value)
    staff.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(staff)
    return _to_response(staff)


@router.delete("/{staff_id}", summary="スタッフを削除")
async def delete_staff(
    staff_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    staff = await _get_owned_staff(staff_id, current_user, db)

    # 今後の有効な予約がこのスタッフに割り当てられている場合は削除をブロック
    # （Staff.reservations は cascade="all, delete-orphan" のため、無条件削除だと
    #   予約履歴ごと消えてしまう）
    upcoming = await db.execute(
        select(Reservation).filter(
            Reservation.staff_id == staff_id,
            Reservation.status.in_(["pending", "confirmed"]),
            Reservation.reservation_date >= datetime.utcnow(),
        )
    )
    if upcoming.scalars().first():
        raise HTTPException(
            status_code=400,
            detail="このスタッフに割り当てられた今後の予約があるため削除できません。先に予約を確認・キャンセルしてください"
        )

    await db.delete(staff)
    await db.commit()
    return {"success": True}


@router.post(
    "/{staff_id}/services/{service_id}",
    response_model=StaffServiceAssignmentResponse,
    summary="スタッフに提供可能なサービスを割り当て"
)
async def add_staff_service(
    staff_id: str,
    service_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    staff = await _get_owned_staff(staff_id, current_user, db)

    service = await db.get(Service, service_id)
    if not service:
        raise HTTPException(status_code=404, detail="サービスが見つかりません")
    if service.shop_id != staff.shop_id:
        raise HTTPException(status_code=400, detail="スタッフと異なる店舗のサービスは割り当てられません")

    existing = await db.execute(
        select(StaffService).filter(
            StaffService.staff_id == staff_id,
            StaffService.service_id == service_id,
        )
    )
    if existing.scalars().first():
        raise HTTPException(status_code=400, detail="このサービスはすでに割り当て済みです")

    staff_service = StaffService(
        id=str(uuid.uuid4()),
        staff_id=staff_id,
        service_id=service_id,
        created_at=datetime.utcnow(),
    )
    db.add(staff_service)
    await db.commit()

    return StaffServiceAssignmentResponse(
        service_id=service.id,
        service_name=service.name,
        base_price=service.base_price,
        duration_minutes=service.duration_minutes,
    )


@router.get(
    "/{staff_id}/services",
    response_model=List[StaffServiceAssignmentResponse],
    summary="スタッフが提供するサービス一覧を取得"
)
async def get_staff_services(staff_id: str, db: AsyncSession = Depends(get_db)):
    staff = await db.get(Staff, staff_id)
    if not staff:
        raise HTTPException(status_code=404, detail="スタッフが見つかりません")

    result = await db.execute(
        select(Service)
        .join(StaffService, StaffService.service_id == Service.id)
        .filter(StaffService.staff_id == staff_id)
    )
    return [
        StaffServiceAssignmentResponse(
            service_id=s.id,
            service_name=s.name,
            base_price=s.base_price,
            duration_minutes=s.duration_minutes,
        )
        for s in result.scalars().all()
    ]


@router.delete("/{staff_id}/services/{service_id}", summary="スタッフからサービスの割り当てを解除")
async def remove_staff_service(
    staff_id: str,
    service_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_staff(staff_id, current_user, db)

    result = await db.execute(
        select(StaffService).filter(
            StaffService.staff_id == staff_id,
            StaffService.service_id == service_id,
        )
    )
    staff_service = result.scalars().first()
    if not staff_service:
        raise HTTPException(status_code=404, detail="この組み合わせの割り当てが見つかりません")

    await db.delete(staff_service)
    await db.commit()
    return {"success": True}
