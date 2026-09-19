"""
Reservation (予約) エンドポイント
予約の作成、確認、管理機能、空き状況の計算
"""

import uuid
from datetime import datetime, date as date_type, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.deps import get_current_user, get_optional_current_user
from app.schemas.user import CurrentUser
from app.models.reservation import Reservation, ReservationStatus
from app.models.shop import Shop, ShopHours, ShopTable, ShopClosure
from app.models.service import Service
from app.models.staff import Staff, StaffService
from app.models.promotion import Coupon
from app.schemas.reservation import (
    ReservationCreateRequest, ReservationResponse, ReservationCreateResponse,
    ReservationUpdateRequest, ReservationListResponse,
    AvailabilityResponse, AvailabilitySlot
)

router = APIRouter(prefix="/api/v1/reservations", tags=["reservations"])

VALID_STATUSES = {s.value for s in ReservationStatus}
SLOT_INTERVAL_MINUTES = 30


def _to_response(reservation: Reservation) -> ReservationResponse:
    return ReservationResponse(
        id=reservation.id,
        shop_id=reservation.shop_id,
        customer_id=reservation.customer_id,
        table_id=reservation.table_id,
        table_name=(reservation.table.name if reservation.table else None),
        service_id=reservation.service_id,
        service_name=(reservation.service.name if reservation.service else None),
        staff_id=reservation.staff_id,
        staff_name=(reservation.staff.name if reservation.staff else None),
        coupon_id=reservation.coupon_id,
        coupon_code=(reservation.coupon.code if reservation.coupon else None),
        discount_amount=reservation.discount_amount,
        guest_name=reservation.guest_name,
        guest_phone=reservation.guest_phone,
        guest_email=reservation.guest_email,
        reservation_date=reservation.reservation_date,
        number_of_people=reservation.number_of_people,
        status=reservation.status,
        special_requests=reservation.special_requests,
        cancelled_at=reservation.cancelled_at,
        cancellation_reason=reservation.cancellation_reason,
        arrived_at=reservation.arrived_at,
        total_price=reservation.total_price,
        payment_status=reservation.payment_status,
        reservation_source=reservation.reservation_source,
        created_at=reservation.created_at,
        updated_at=reservation.updated_at,
    )


async def _get_closure_for_date(db: AsyncSession, shop_id: str, target_date: date_type) -> Optional[ShopClosure]:
    result = await db.execute(
        select(ShopClosure).filter(
            ShopClosure.shop_id == shop_id,
            ShopClosure.start_date <= target_date,
            ShopClosure.end_date >= target_date,
        )
    )
    return result.scalars().first()


async def _find_available_table(
    db: AsyncSession, shop_id: str, party_size: int, start: datetime, duration_minutes: int
) -> tuple[Optional[str], bool]:
    """
    条件に合う空きテーブルを探す。
    戻り値: (table_id または None, テーブルが1件も登録されていないか)
    テーブルが1件も登録されていない場合は容量チェックを行わず None を返して常に予約可能とする。
    """
    tables_result = await db.execute(
        select(ShopTable).filter(ShopTable.shop_id == shop_id, ShopTable.is_active == True)
    )
    tables = tables_result.scalars().all()
    if not tables:
        return None, True

    candidates = sorted([t for t in tables if t.capacity >= party_size], key=lambda t: t.capacity)
    if not candidates:
        return None, False

    end = start + timedelta(minutes=duration_minutes)

    for table in candidates:
        overlap_result = await db.execute(
            select(Reservation).filter(
                Reservation.table_id == table.id,
                Reservation.status.in_(["pending", "confirmed"]),
            )
        )
        conflicting = False
        for existing in overlap_result.scalars().all():
            existing_start = existing.reservation_date
            existing_end = existing_start + timedelta(minutes=duration_minutes)
            if existing_start < end and existing_end > start:
                conflicting = True
                break
        if not conflicting:
            return table.id, False

    return None, False


async def _find_available_staff_for_service(
    db: AsyncSession, shop_id: str, service_id: str, start: datetime, duration_minutes: int,
    preferred_staff_id: Optional[str] = None
) -> tuple[Optional[str], bool]:
    """
    指定したサービスを提供できる、かつその時間帯が空いているスタッフを探す。
    戻り値: (staff_id または None, このサービスにスタッフが1人も割り当てられていないか)
    スタッフが1人も割り当てられていない場合は指名なしの空き状況チェックを行わず、
    常に (None, True) を返す（テーブル管理の _find_available_table と同じ考え方）。
    """
    staff_result = await db.execute(
        select(Staff)
        .join(StaffService, StaffService.staff_id == Staff.id)
        .filter(
            StaffService.service_id == service_id,
            Staff.shop_id == shop_id,
            Staff.is_active == "active",
        )
    )
    eligible_staff = list(staff_result.scalars().all())
    if not eligible_staff:
        return None, True

    if preferred_staff_id:
        eligible_staff = [s for s in eligible_staff if s.id == preferred_staff_id]
        if not eligible_staff:
            return None, False

    end = start + timedelta(minutes=duration_minutes)

    for staff in eligible_staff:
        overlap_result = await db.execute(
            select(Reservation).filter(
                Reservation.staff_id == staff.id,
                Reservation.status.in_(["pending", "confirmed"]),
            )
        )
        conflicting = False
        for existing in overlap_result.scalars().all():
            existing_start = existing.reservation_date
            existing_end = existing_start + timedelta(minutes=duration_minutes)
            if existing_start < end and existing_end > start:
                conflicting = True
                break
        if not conflicting:
            return staff.id, False

    return None, False


async def _resolve_coupon_for_booking(
    db: AsyncSession, shop_id: str, code: str, base_amount: float
) -> tuple[Coupon, int, int]:
    """
    予約時に入力されたクーポンコードを検証し、割引後の合計金額を計算する。
    戻り値: (Coupon, discounted_total(円), discount_amount(円))
    無効なクーポンの場合は HTTPException(400) を送出する。
    """
    result = await db.execute(
        select(Coupon).filter(Coupon.shop_id == shop_id, Coupon.code == code)
    )
    coupon = result.scalar_one_or_none()
    if not coupon:
        raise HTTPException(status_code=400, detail="クーポンコードが見つかりません")
    if not coupon.is_active:
        raise HTTPException(status_code=400, detail="このクーポンは現在ご利用いただけません")

    now = datetime.utcnow()
    if coupon.start_date and now < coupon.start_date:
        raise HTTPException(status_code=400, detail="このクーポンはまだご利用いただけません")
    if coupon.end_date and now > coupon.end_date:
        raise HTTPException(status_code=400, detail="このクーポンの有効期限が切れています")
    if coupon.usage_limit is not None and (coupon.usage_count or 0) >= coupon.usage_limit:
        raise HTTPException(status_code=400, detail="このクーポンは利用上限に達しています")

    if coupon.discount_type == "percentage":
        raw_discount = base_amount * (coupon.discount_value / 100.0)
    else:
        raw_discount = coupon.discount_value

    discount_amount = int(round(max(0.0, min(raw_discount, base_amount))))
    discounted_total = int(round(base_amount)) - discount_amount
    return coupon, discounted_total, discount_amount


@router.get(
    "/shop/{shop_id}/availability",
    response_model=AvailabilityResponse,
    summary="指定日の空き状況を取得",
    description="営業時間・定休日・テーブルの空き状況から、予約可能な時間枠を計算"
)
async def get_availability(
    shop_id: str,
    date: str = Query(..., description="日付（YYYY-MM-DD）"),
    party_size: int = Query(1, ge=1, le=999, description="人数"),
    service_id: Optional[str] = Query(None, description="サービスID（美容院・クリニック・スクール・フィットネスなど、サービス単位で予約する業種の場合に指定）"),
    staff_id: Optional[str] = Query(None, description="スタッフ指名がある場合に指定（省略時は指名なし）"),
    db: AsyncSession = Depends(get_db)
) -> AvailabilityResponse:
    shop = await db.get(Shop, shop_id)
    if not shop or not shop.is_active:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")

    service: Optional[Service] = None
    if service_id:
        service = await db.get(Service, service_id)
        if not service or service.shop_id != shop_id:
            raise HTTPException(status_code=404, detail="指定されたサービスが見つかりません")

    try:
        target_date = date_type.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="日付の形式が正しくありません（YYYY-MM-DD）")

    common = dict(shop_id=shop_id, date=date, party_size=party_size, service_id=service_id, staff_id=staff_id)

    closure = await _get_closure_for_date(db, shop_id, target_date)
    if closure:
        reason_text = (closure.reason or "").strip()
        message = f"臨時休業日です（{reason_text}）" if reason_text else "臨時休業日です"
        return AvailabilityResponse(**common, is_open=False, message=message, slots=[])

    weekday = target_date.weekday()  # 0=月, 6=日
    hours_result = await db.execute(
        select(ShopHours).filter(ShopHours.shop_id == shop_id, ShopHours.day_of_week == weekday)
    )
    hours = hours_result.scalar_one_or_none()

    if hours is None:
        return AvailabilityResponse(**common, is_open=False, message="この店舗の営業時間が設定されていません", slots=[])
    if hours.is_closed:
        return AvailabilityResponse(**common, is_open=False, message="定休日です", slots=[])

    duration = (service.duration_minutes if service and service.duration_minutes else None) or shop.reservation_duration_minutes or 90
    latest_start_time = hours.last_order_time or (
        (datetime.combine(target_date, hours.closing_time) - timedelta(minutes=duration)).time()
    )

    opening_dt = datetime.combine(target_date, hours.opening_time)
    latest_start_dt = datetime.combine(target_date, latest_start_time)

    if latest_start_dt < opening_dt:
        return AvailabilityResponse(**common, is_open=True, message="本日は予約可能な時間枠がありません", slots=[])

    now = datetime.utcnow()
    slots: List[AvailabilitySlot] = []
    cursor = opening_dt
    while cursor <= latest_start_dt:
        if cursor > now:
            if service:
                found_staff_id, unmanaged = await _find_available_staff_for_service(
                    db, shop_id, service.id, cursor, duration, staff_id
                )
                available = unmanaged or (found_staff_id is not None)
            else:
                table_id, unmanaged = await _find_available_table(db, shop_id, party_size, cursor, duration)
                available = unmanaged or (table_id is not None)
            slots.append(AvailabilitySlot(time=cursor.strftime("%H:%M"), available=available))
        cursor += timedelta(minutes=SLOT_INTERVAL_MINUTES)

    return AvailabilityResponse(**common, is_open=True, slots=slots)


@router.post(
    "/create",
    response_model=ReservationCreateResponse,
    summary="新規予約を作成（ゲスト予約）",
    description="会員登録なしで新しい予約を作成して予約IDを取得"
)
async def create_reservation(
    request: ReservationCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_optional_current_user),
) -> ReservationCreateResponse:
    try:
        shop = await db.get(Shop, request.shop_id)
        if not shop or not shop.is_active:
            raise HTTPException(status_code=404, detail="指定された店舗が見つかりません")

        if request.reservation_date <= datetime.utcnow():
            raise HTTPException(status_code=400, detail="過去の日時で予約することはできません")

        closure = await _get_closure_for_date(db, request.shop_id, request.reservation_date.date())
        if closure:
            raise HTTPException(status_code=400, detail="ご指定の日は臨時休業のため予約できません")

        service: Optional[Service] = None
        if request.service_id:
            service = await db.get(Service, request.service_id)
            if not service or service.shop_id != request.shop_id:
                raise HTTPException(status_code=404, detail="指定されたサービスが見つかりません")
            if service.is_active != "active":
                raise HTTPException(status_code=400, detail="このサービスは現在受付を停止しています")

        weekday = request.reservation_date.weekday()
        hours_result = await db.execute(
            select(ShopHours).filter(ShopHours.shop_id == request.shop_id, ShopHours.day_of_week == weekday)
        )
        hours = hours_result.scalar_one_or_none()
        if hours is not None:
            if hours.is_closed:
                raise HTTPException(status_code=400, detail="ご指定の日は定休日です")
            req_time = request.reservation_date.time()
            latest_start_time = hours.last_order_time or hours.closing_time
            if req_time < hours.opening_time or req_time > latest_start_time:
                raise HTTPException(status_code=400, detail="ご指定の時間は営業時間外です")

        duration = (service.duration_minutes if service and service.duration_minutes else None) or shop.reservation_duration_minutes or 90

        table_id = None
        final_staff_id = None
        if service:
            if request.staff_id:
                staff_check = await db.execute(
                    select(StaffService).join(Staff, Staff.id == StaffService.staff_id).filter(
                        StaffService.staff_id == request.staff_id,
                        StaffService.service_id == service.id,
                        Staff.shop_id == request.shop_id,
                        Staff.is_active == "active",
                    )
                )
                if not staff_check.scalars().first():
                    raise HTTPException(status_code=400, detail="指定されたスタッフはこのサービスを提供していません")

            found_staff_id, unmanaged = await _find_available_staff_for_service(
                db, request.shop_id, service.id, request.reservation_date, duration, request.staff_id
            )
            if not unmanaged and found_staff_id is None:
                detail = (
                    "ご指名のスタッフは満席です。他の時間かスタッフをお試しください"
                    if request.staff_id else
                    "ご希望の時間はスタッフの空きがありません。他の時間をお試しください"
                )
                raise HTTPException(status_code=400, detail=detail)
            final_staff_id = found_staff_id if not unmanaged else request.staff_id
        else:
            table_id, unmanaged = await _find_available_table(
                db, request.shop_id, request.number_of_people, request.reservation_date, duration
            )
            if not unmanaged and table_id is None:
                raise HTTPException(status_code=400, detail="ご希望の時間は満席です。他の時間をお試しください")

        coupon: Optional[Coupon] = None
        discount_amount = 0
        total_price = int(round(service.base_price)) if service else None
        if request.coupon_code:
            if not service:
                raise HTTPException(status_code=400, detail="クーポンはサービスの予約にのみご利用いただけます")
            coupon, total_price, discount_amount = await _resolve_coupon_for_booking(
                db, request.shop_id, request.coupon_code, service.base_price
            )

        reservation_id = str(uuid.uuid4())
        reservation = Reservation(
            id=reservation_id,
            shop_id=request.shop_id,
            customer_id=None,
            user_id=current_user.id if current_user else None,
            staff_id=final_staff_id,
            table_id=table_id,
            service_id=service.id if service else None,
            coupon_id=coupon.id if coupon else None,
            guest_name=request.guest_name,
            guest_phone=request.guest_phone,
            guest_email=request.guest_email,
            reservation_date=request.reservation_date,
            number_of_people=request.number_of_people,
            status=ReservationStatus.PENDING.value,
            special_requests=request.special_requests,
            total_price=total_price,
            discount_amount=(discount_amount if coupon else None),
            payment_status=("unpaid" if service else None),
            reservation_source=("coupon" if coupon else "online"),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(reservation)

        if coupon:
            coupon.usage_count = (coupon.usage_count or 0) + 1
            coupon.total_discount_given = (coupon.total_discount_given or 0.0) + discount_amount
            coupon.updated_at = datetime.utcnow()

        if shop.total_reservations is None:
            shop.total_reservations = 0
        shop.total_reservations += 1
        shop.updated_at = datetime.utcnow()

        await db.commit()

        result = await db.execute(
            select(Reservation).options(selectinload(Reservation.table), selectinload(Reservation.staff), selectinload(Reservation.service), selectinload(Reservation.coupon)).filter(Reservation.id == reservation_id)
        )
        reservation = result.scalar_one()

        return ReservationCreateResponse(
            success=True,
            message="予約リクエストを受け付けました。店舗からの確定連絡をお待ちください",
            reservation_id=reservation_id,
            reservation=_to_response(reservation)
        )

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"予約作成に失敗しました: {str(e)}")


@router.get(
    "/{reservation_id}",
    response_model=ReservationResponse,
    summary="予約詳細を取得"
)
async def get_reservation(
    reservation_id: str,
    db: AsyncSession = Depends(get_db)
) -> ReservationResponse:
    result = await db.execute(
        select(Reservation).options(selectinload(Reservation.table), selectinload(Reservation.staff), selectinload(Reservation.service), selectinload(Reservation.coupon)).filter(Reservation.id == reservation_id)
    )
    reservation = result.scalar_one_or_none()
    if not reservation:
        raise HTTPException(status_code=404, detail="予約が見つかりません")
    return _to_response(reservation)


@router.get(
    "/shop/{shop_id}",
    response_model=ReservationListResponse,
    summary="店舗の予約一覧を取得（オーナー用）",
    description="指定された店舗の予約一覧を取得（ステータスで絞込可能）。オーナー本人のみ閲覧可能"
)
async def get_shop_reservations(
    shop_id: str,
    status: Optional[str] = Query(None, description="ステータスフィルタ"),
    limit: int = Query(50, ge=1, le=200, description="取得件数"),
    offset: int = Query(0, ge=0, description="オフセット"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ReservationListResponse:
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="指定された店舗が見つかりません")
    if shop.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="この店舗の予約を閲覧する権限がありません")

    stmt = select(Reservation).options(selectinload(Reservation.table), selectinload(Reservation.staff), selectinload(Reservation.service), selectinload(Reservation.coupon)).filter(Reservation.shop_id == shop_id)
    if status:
        stmt = stmt.filter(Reservation.status == status.lower())

    all_result = await db.execute(stmt)
    all_items = all_result.scalars().all()
    total = len(all_items)

    stmt = stmt.order_by(Reservation.reservation_date.asc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    reservations = result.scalars().all()

    return ReservationListResponse(
        total=total, limit=limit, offset=offset,
        items=[_to_response(r) for r in reservations]
    )


@router.put(
    "/{reservation_id}",
    response_model=ReservationResponse,
    summary="予約を更新（オーナー用）",
    description="予約のステータス変更・内容編集。オーナー本人のみ操作可能"
)
async def update_reservation(
    reservation_id: str,
    request: ReservationUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ReservationResponse:
    try:
        result = await db.execute(
            select(Reservation).options(selectinload(Reservation.table), selectinload(Reservation.staff), selectinload(Reservation.service), selectinload(Reservation.coupon)).filter(Reservation.id == reservation_id)
        )
        reservation = result.scalar_one_or_none()
        if not reservation:
            raise HTTPException(status_code=404, detail="予約が見つかりません")

        shop = await db.get(Shop, reservation.shop_id)
        if not shop or shop.tenant_id != current_user.tenant_id:
            raise HTTPException(status_code=403, detail="この予約を操作する権限がありません")

        if request.status is not None:
            new_status = request.status.lower()
            if new_status not in VALID_STATUSES:
                raise HTTPException(status_code=400, detail=f"不正なステータスです（{', '.join(sorted(VALID_STATUSES))}）")
            if new_status != reservation.status:
                if new_status == "cancelled":
                    reservation.cancelled_at = datetime.utcnow()
                    reservation.cancellation_reason = request.cancellation_reason
                elif new_status == "completed":
                    reservation.arrived_at = datetime.utcnow()
                reservation.status = new_status

        if request.number_of_people:
            reservation.number_of_people = request.number_of_people
        if request.special_requests is not None:
            reservation.special_requests = request.special_requests
        if request.reservation_date is not None:
            if request.reservation_date <= datetime.utcnow():
                raise HTTPException(status_code=400, detail="過去の日時には変更できません")
            reservation.reservation_date = request.reservation_date

        reservation.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(reservation, attribute_names=["table", "staff", "service", "coupon"])

        return _to_response(reservation)

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"予約更新に失敗しました: {str(e)}")


@router.delete(
    "/{reservation_id}",
    summary="予約をキャンセル"
)
async def cancel_reservation(
    reservation_id: str,
    reason: Optional[str] = Query(None, description="キャンセル理由"),
    db: AsyncSession = Depends(get_db)
):
    try:
        reservation = await db.get(Reservation, reservation_id)
        if not reservation:
            raise HTTPException(status_code=404, detail="予約が見つかりません")

        reservation.status = ReservationStatus.CANCELLED.value
        reservation.cancelled_at = datetime.utcnow()
        reservation.cancellation_reason = reason
        reservation.updated_at = datetime.utcnow()

        await db.commit()

        return {"success": True, "message": "予約がキャンセルされました", "reservation_id": reservation_id}

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"予約キャンセルに失敗しました: {str(e)}")
