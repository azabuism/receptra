"""
Reservation (予約) エンドポイント
予約の作成、確認、管理機能、空き状況の計算
"""

import logging
import uuid
from datetime import datetime, date as date_type, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.deps import get_current_user, get_optional_current_user
from app.schemas.user import CurrentUser
from app.models.reservation import Reservation, ReservationStatus
from app.models.shop import Shop, ShopHours, ShopTable, ShopClosure
from app.models.service import Service
from app.models.staff import Staff, StaffService
from app.models.staff_shift import StaffWeeklyShift, StaffShiftOverride
from app.models.promotion import Coupon
from app.schemas.reservation import (
    ReservationCreateRequest, ReservationResponse, ReservationCreateResponse,
    ReservationUpdateRequest, ReservationListResponse,
    AvailabilityResponse, AvailabilitySlot
)

logger = logging.getLogger("receptra.reservations")

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


def _http_error(status_code: int, detail: str, reason_code: Optional[str] = None) -> HTTPException:
    """
    Phase3B: HTTPExceptionに、Realtime Voice Tool層が文字列の部分一致に頼らず
    機械可読なreason_codeで失敗理由を判別できるよう、任意の.reason_code属性を
    追加で持たせる。detail/status_code自体は従来と完全に同一であり、既存の
    Web予約・チャット予約側（shop_booking_ai.pyのe.detail参照等）の挙動には
    一切影響しない。reason_codeを見ない既存呼び出し元からは通常のHTTPExceptionと
    区別がつかない。
    """
    exc = HTTPException(status_code=status_code, detail=detail)
    exc.reason_code = reason_code
    return exc


async def _get_reservation_by_idempotency_key(db: AsyncSession, idempotency_key: str) -> Optional[Reservation]:
    """Phase3B: 指定したidempotency_keyを持つ既存予約を1件取得する（無ければNone）"""
    result = await db.execute(
        select(Reservation)
        .options(
            selectinload(Reservation.table), selectinload(Reservation.staff),
            selectinload(Reservation.service), selectinload(Reservation.coupon),
        )
        .filter(Reservation.idempotency_key == idempotency_key)
    )
    return result.scalar_one_or_none()


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


async def _is_staff_scheduled(
    db: AsyncSession, staff_id: str, start_dt: datetime, duration_minutes: int
) -> bool:
    """
    Phase3E-2: 指定した日時（start_dt〜start_dt+duration_minutes）に、指定スタッフが
    シフト上「勤務している」かどうかを判定する。この関数は_find_available_staff_for_service
    からenforce_schedule=Trueの場合にのみ呼ばれ、shop.staff_schedule_enabledが
    Falseの店舗（デフォルト・既存の全店舗）では一切呼ばれない。

    判定ロジック:
    1. スタッフに週次シフト・日付調整のいずれも一度も登録されていない場合は
       「シフト未設定」として常にTrue（勤務可能）を返す。staff_schedule_enabledを
       ONにしただけで、シフト未登録の全スタッフが一律予約不可になる事故を防ぐための
       安全側フォールバック（_find_available_table/_find_available_staff_for_serviceの
       「管理対象データが0件なら常に空き扱い」という既存の設計思想と一貫させている）。
    2. 対象日にoverride_type="day_off"が1件でもあれば終日不可。
    3. 対象日にoverride_type="hours"があれば、その時間帯を週次シフトの代わりに使う
       （無ければ週次シフトのその曜日の時間帯を使う。どちらも無ければ「その日は
       勤務日として登録されていない」として不可）。
    4. 予約したい時間帯が上記の勤務時間帯に完全に収まっているかを確認する。
    5. 対象日にoverride_type="unavailable"があれば、その時間帯と重なっていないかを
       確認する（重なっていれば不可。重ならない部分は通常どおり勤務扱いのまま）。
    """
    target_date = start_dt.date()
    end_dt = start_dt + timedelta(minutes=duration_minutes)
    weekday = target_date.weekday()

    any_weekly = await db.execute(
        select(StaffWeeklyShift.id).filter(StaffWeeklyShift.staff_id == staff_id).limit(1)
    )
    has_any_weekly = any_weekly.scalar_one_or_none() is not None
    any_override_ever = await db.execute(
        select(StaffShiftOverride.id).filter(StaffShiftOverride.staff_id == staff_id).limit(1)
    )
    has_any_override = any_override_ever.scalar_one_or_none() is not None
    if not has_any_weekly and not has_any_override:
        return True

    overrides_result = await db.execute(
        select(StaffShiftOverride).filter(
            StaffShiftOverride.staff_id == staff_id,
            StaffShiftOverride.target_date == target_date,
        )
    )
    overrides = list(overrides_result.scalars().all())

    if any(o.override_type == "day_off" for o in overrides):
        return False

    hours_overrides = [o for o in overrides if o.override_type == "hours"]
    if hours_overrides:
        base_windows = [
            (datetime.combine(target_date, o.start_time), datetime.combine(target_date, o.end_time))
            for o in hours_overrides
        ]
    else:
        weekly_result = await db.execute(
            select(StaffWeeklyShift).filter(
                StaffWeeklyShift.staff_id == staff_id,
                StaffWeeklyShift.day_of_week == weekday,
            )
        )
        weekly = list(weekly_result.scalars().all())
        if not weekly:
            return False
        base_windows = [
            (datetime.combine(target_date, w.start_time), datetime.combine(target_date, w.end_time))
            for w in weekly
        ]

    if not any(bw_start <= start_dt and end_dt <= bw_end for bw_start, bw_end in base_windows):
        return False

    unavailable_overrides = [o for o in overrides if o.override_type == "unavailable"]
    for o in unavailable_overrides:
        u_start = datetime.combine(target_date, o.start_time)
        u_end = datetime.combine(target_date, o.end_time)
        if u_start < end_dt and u_end > start_dt:
            return False

    return True


async def _find_available_staff_for_service(
    db: AsyncSession, shop_id: str, service_id: str, start: datetime, duration_minutes: int,
    preferred_staff_id: Optional[str] = None, enforce_schedule: bool = False
) -> tuple[Optional[str], bool]:
    """
    指定したサービスを提供できる、かつその時間帯が空いているスタッフを探す。
    戻り値: (staff_id または None, このサービスにスタッフが1人も割り当てられていないか)
    スタッフが1人も割り当てられていない場合は指名なしの空き状況チェックを行わず、
    常に (None, True) を返す（テーブル管理の _find_available_table と同じ考え方）。

    enforce_schedule: Phase3E-2で追加。Trueの場合のみ、_is_staff_scheduled()による
    シフトチェックを予約重複チェックに加えて行う。呼び出し元は必ず
    shop.staff_schedule_enabledの値をそのまま渡すこと。この引数はデフォルトFalseで、
    Falseの間は_is_staff_scheduled()を一切呼ばない＝Phase3E-1までと完全に同じ挙動になる。
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
        if enforce_schedule:
            scheduled = await _is_staff_scheduled(db, staff.id, start, duration_minutes)
            if not scheduled:
                continue

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


async def check_single_slot_availability(
    db: AsyncSession,
    shop: Shop,
    target_date: date_type,
    target_time,
    party_size: int,
    service_id: Optional[str] = None,
    staff_id: Optional[str] = None,
) -> tuple[bool, Optional[str]]:
    """
    Realtime Voice AI Phase3A: check_availability Tool Calling用。

    指定された単一の日時について、実際に予約可能かどうかを判定する。
    GET /shop/{shop_id}/availability （1日分のスロット一覧を計算するエンドポイント）
    と全く同じ営業時間・臨時休業・卓/スタッフの空き判定ロジックを、1つの
    日時に対してだけ適用する。ロジックを別系統で二重実装しないよう、
    _get_closure_for_date / _find_available_table / _find_available_staff_for_service
    を直接再利用する。

    戻り値: (available, reason_code)。reason_code は available=False の
    ときのみ設定する（候補は app.schemas.reservation.CheckAvailabilityResponse
    のコメントを参照）。

    重要: この関数はあくまで「現時点の空き状況の判定」のみを行い、DBへの
    書き込みは一切行わない（Phase3Aではcreate_reservationは実装しない）。

    注意: shop.reservations_enabled（オンライン予約受付の課金アクティベーション
    フラグ）はここではチェックしない。既存の GET /shop/{shop_id}/availability も
    このフラグを見ておらず（空き状況の閲覧自体は無料機能で、実際に予約を
    確定するcreate_reservationの側でのみこのフラグを見る設計になっている）、
    その既存の一貫性に合わせるため。
    """
    closure = await _get_closure_for_date(db, shop.id, target_date)
    if closure:
        return False, "temporary_closure"

    weekday = target_date.weekday()
    hours_result = await db.execute(
        select(ShopHours).filter(ShopHours.shop_id == shop.id, ShopHours.day_of_week == weekday)
    )
    hours = hours_result.scalar_one_or_none()
    # Phase3B.1: 「営業時間が未設定（店舗側の設定漏れ）」と「定休日（設定はある
    # が休みの日）」を区別する。create_reservation()側も同じ区別に統一済み。
    if hours is None:
        return False, "business_hours_not_configured"
    if hours.is_closed:
        return False, "shop_closed"

    service: Optional[Service] = None
    if service_id:
        service = await db.get(Service, service_id)
        if not service or service.shop_id != shop.id or service.is_active != "active":
            return False, "service_unavailable"

    duration = (service.duration_minutes if service and service.duration_minutes else None) or shop.reservation_duration_minutes or 90
    latest_start_time = hours.last_order_time or (
        (datetime.combine(target_date, hours.closing_time) - timedelta(minutes=duration)).time()
    )

    if target_time < hours.opening_time or target_time > latest_start_time:
        return False, "outside_business_hours"

    start_dt = datetime.combine(target_date, target_time)
    # create_reservation / get_availability と同じ比較方法（既存の挙動に
    # 合わせる。タイムゾーンの厳密な扱いはPhase3Aのスコープ外）。
    if start_dt <= datetime.utcnow():
        return False, "invalid_request"

    if service:
        found_staff_id, unmanaged = await _find_available_staff_for_service(
            db, shop.id, service.id, start_dt, duration, staff_id,
            enforce_schedule=bool(shop.staff_schedule_enabled),
        )
        if unmanaged or found_staff_id is not None:
            return True, None
        return False, ("staff_unavailable" if staff_id else "fully_booked")
    else:
        table_id, unmanaged = await _find_available_table(db, shop.id, party_size, start_dt, duration)
        if unmanaged or table_id is not None:
            return True, None
        return False, "fully_booked"


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
                    db, shop_id, service.id, cursor, duration, staff_id,
                    enforce_schedule=bool(shop.staff_schedule_enabled),
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
    # Phase3B: idempotency_keyが指定されている場合（Realtime Voice経由のみを想定。
    # 通常のWeb予約・チャット予約は指定しないため、この分岐には入らず従来通り動作する）、
    # 既に同一キーで成立済みの予約があれば、新たに作成せずそれをそのまま成功として返す。
    # ここでは意図的に営業時間・満席等の再検証を行わない。既に一度正当に成立した予約の
    # 再送であることが確定しているため（reason: 予約成立後に状況が変わっていても、
    # 既に成立した事実を覆す判定をここで行うべきではない）。
    if request.idempotency_key:
        existing = await _get_reservation_by_idempotency_key(db, request.idempotency_key)
        if existing:
            return ReservationCreateResponse(
                success=True,
                message="予約リクエストを受け付けました。店舗からの確定連絡をお待ちください",
                reservation_id=existing.id,
                reservation=_to_response(existing),
            )

    try:
        shop = await db.get(Shop, request.shop_id)
        if not shop or not shop.is_active:
            raise _http_error(404, "指定された店舗が見つかりません", reason_code="temporarily_unavailable")

        if not shop.reservations_enabled:
            raise _http_error(400, "この店舗は現在予約を受け付けていません", reason_code="reservation_not_enabled")

        if request.reservation_date <= datetime.utcnow():
            raise _http_error(400, "過去の日時で予約することはできません", reason_code="invalid_request")

        closure = await _get_closure_for_date(db, request.shop_id, request.reservation_date.date())
        if closure:
            raise _http_error(400, "ご指定の日は臨時休業のため予約できません", reason_code="temporary_closure")

        service: Optional[Service] = None
        if request.service_id:
            service = await db.get(Service, request.service_id)
            if not service or service.shop_id != request.shop_id:
                raise _http_error(404, "指定されたサービスが見つかりません", reason_code="service_unavailable")
            if service.is_active != "active":
                raise _http_error(400, "このサービスは現在受付を停止しています", reason_code="service_unavailable")

        weekday = request.reservation_date.weekday()
        hours_result = await db.execute(
            select(ShopHours).filter(ShopHours.shop_id == request.shop_id, ShopHours.day_of_week == weekday)
        )
        hours = hours_result.scalar_one_or_none()
        # Phase3B.1: 以前は `if hours is not None:` により、ShopHoursが1件も
        # 登録されていない店舗では営業時間チェック自体を丸ごとスキップしており、
        # Phase3Aのcheck_availability（hours is None を shop_closed 扱い）や
        # 既存WebのGET /availability（hours is None を「営業時間未設定」として
        # 予約不可扱い）と意味が食い違っていた。実際に本番調査で
        # reservations_enabled=True かつ ShopHours 0件の店舗が存在し、この経路
        # 経由でのみ予約が成立してしまうことを確認したため、Source of Truthである
        # ここで意味を統一する。「定休日（設定はあるが休みの日）」と
        # 「営業時間が未設定（店舗側の設定漏れ）」は原因も対応も異なるため、
        # reason_codeを分離する。
        if hours is None:
            raise _http_error(
                400, "この店舗は営業時間が設定されていないため、オンラインでの予約確定ができません",
                reason_code="business_hours_not_configured",
            )
        if hours.is_closed:
            raise _http_error(400, "ご指定の日は定休日です", reason_code="shop_closed")
        req_time = request.reservation_date.time()
        latest_start_time = hours.last_order_time or hours.closing_time
        if req_time < hours.opening_time or req_time > latest_start_time:
            raise _http_error(400, "ご指定の時間は営業時間外です", reason_code="outside_business_hours")

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
                    raise _http_error(400, "指定されたスタッフはこのサービスを提供していません", reason_code="staff_unavailable")

            found_staff_id, unmanaged = await _find_available_staff_for_service(
                db, request.shop_id, service.id, request.reservation_date, duration, request.staff_id,
                enforce_schedule=bool(shop.staff_schedule_enabled),
            )
            if not unmanaged and found_staff_id is None:
                detail = (
                    "ご指名のスタッフは満席です。他の時間かスタッフをお試しください"
                    if request.staff_id else
                    "ご希望の時間はスタッフの空きがありません。他の時間をお試しください"
                )
                raise _http_error(400, detail, reason_code="staff_unavailable")
            final_staff_id = found_staff_id if not unmanaged else request.staff_id
        else:
            table_id, unmanaged = await _find_available_table(
                db, request.shop_id, request.number_of_people, request.reservation_date, duration
            )
            if not unmanaged and table_id is None:
                raise _http_error(400, "ご希望の時間は満席です。他の時間をお試しください", reason_code="fully_booked")

        coupon: Optional[Coupon] = None
        discount_amount = 0
        total_price = int(round(service.base_price)) if service else None
        if request.coupon_code:
            if not service:
                raise _http_error(400, "クーポンはサービスの予約にのみご利用いただけます", reason_code="invalid_request")
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
            idempotency_key=request.idempotency_key,
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

        try:
            await db.commit()
        except IntegrityError as ie:
            # Phase3B: 同一idempotency_keyでの同時多重INSERT（Request A/Bが共に
            # 「SELECT時点では存在しない」を通過した後、片方がcommitに成功し、
            # もう片方がDBの一意制約(ux_reservations_idempotency_key)違反で
            # ここに到達するケース）。これはidempotency_keyが指定されている場合
            # にのみ起こり得る想定のため、その場合は先にcommitできた側の予約を
            # 取得して同じ成功結果を返す（rollback → 既存Reservation再取得 →
            # 同じ成功結果、というDB一意制約を最終防衛線とした設計）。
            await db.rollback()
            if request.idempotency_key:
                existing = await _get_reservation_by_idempotency_key(db, request.idempotency_key)
                if existing:
                    logger.info(
                        "idempotency_key=%s のcommit競合を検知。先に成立した予約(id=%s)を返します",
                        request.idempotency_key, existing.id,
                    )
                    return ReservationCreateResponse(
                        success=True,
                        message="予約リクエストを受け付けました。店舗からの確定連絡をお待ちください",
                        reservation_id=existing.id,
                        reservation=_to_response(existing),
                    )
            # idempotency_keyが無い、または競合後も既存予約が見つからない場合
            # （＝idempotency_keyに起因しない、本当に想定外の一意制約違反）は
            # 従来通り安全側の500として扱う。
            raise _http_error(500, f"予約作成に失敗しました: {ie}", reason_code="temporarily_unavailable")

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
        raise _http_error(500, f"予約作成に失敗しました: {str(e)}", reason_code="temporarily_unavailable")


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
