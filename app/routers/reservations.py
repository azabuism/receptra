"""
Reservation (予約) エンドポイント
予約の作成、確認、管理機能、空き状況の計算
"""

import logging
import uuid
from datetime import datetime, date as date_type, timedelta, timezone
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
from app.services.outbound_dispatch import enqueue_reservation_confirmed_call
from app.schemas.reservation import (
    ReservationCreateRequest, ReservationResponse, ReservationCreateResponse,
    ReservationUpdateRequest, ReservationListResponse,
    AvailabilityResponse, AvailabilitySlot
)

logger = logging.getLogger("receptra.reservations")

router = APIRouter(prefix="/api/v1/reservations", tags=["reservations"])

VALID_STATUSES = {s.value for s in ReservationStatus}
SLOT_INTERVAL_MINUTES = 30

# ===== Phase3E-3: reservation_dateの基準（JST-local-naive）と揃えた「現在時刻」 =====
#
# 調査で確認した事実（推測ではない）:
# - Web予約(frontend/public/shop.html)は date+time を単純な文字列結合
#   ("YYYY-MM-DDTHH:MM:SS") で送信しており、タイムゾーン変換は一切行っていない。
# - チャット予約(app/routers/shop_booking_ai.py の_parse_ai_datetime)は、
#   「店舗のローカル時刻を表すnaive datetimeとして解釈する」と明記されており、
#   AIが仮にtzinfo付きの値を返してもtzinfoを剥がすだけで数値変換はしない。
# - Realtime Voice予約(app/routers/realtime_voice.py)は
#   datetime.combine(date, time) で、AIが聞き取った日本語の日時（お客様の
#   発話＝日本時間の壁時計時刻）をそのままnaive datetimeにしている。
# つまり3経路すべてが一貫して「reservation_dateはJST(日本時間)のnaive datetime」
# として送信・保存されている（経路間の不整合ではなく、単一の一貫した設計）。
#
# 一方、従来コードは「過去日時かどうか」の判定にdatetime.utcnow()（真のUTC時刻）を
# 使っていたため、JST-naive値とUTC-naive値を比較する誤り（最大9時間、実際には
# 既に過去の日時を「まだ未来」と誤判定しうる）があった。これは本フェーズで
# 修正すべき対象として調査で特定した。
#
# 修正方針（最小限）: reservation_date系の値と比較する「現在時刻」だけを、
# 同じ基準（JST-naive）に揃える。DBスキーマは一切変更せず、Reservationを
# timezone-awareにもしない。created_at/updated_at等の純粋な記録用タイムスタンプは
# 対象外（従来通りdatetime.utcnow()のままでよい。これらはreservation_dateとは
# 比較されない）。将来shop.timezoneのような店舗別タイムゾーン設定を追加する
# 余地を残すため、あえて「日本時間固定」の関数名にはせず、
# 「reservation_dateの基準に揃えた現在時刻」という位置づけのヘルパーにする。
_JST = timezone(timedelta(hours=9))


def _reservation_basis_now() -> datetime:
    """
    reservation_date（JST-local-naive）と同じ基準の「現在時刻」を返す。
    reservation_date系の値の過去/未来判定にはこの関数を必ず使うこと。
    datetime.utcnow()を直接使うと、JST-naiveな値と比較する際に最大9時間の
    ズレが生じる（Phase3E-3で修正した既知の不具合。詳細は上のコメント参照）。
    """
    return datetime.now(_JST).replace(tzinfo=None)


def _resolve_reservation_duration(service: Optional[Service], shop: Shop) -> int:
    """
    Phase3E-3: 予約時間（分）の解決ロジックを1箇所に統合する。
    優先順位: Service.duration_minutes → shop.reservation_duration_minutes → 90分。
    以前はcheck_single_slot_availability / get_availability / create_reservationの
    3箇所に全く同じ式が重複していた。ここに統合しただけで、優先順位・意味・
    デフォルト値は一切変更していない。
    """
    return (service.duration_minutes if service and service.duration_minutes else None) or shop.reservation_duration_minutes or 90


# Phase3E-3: 同時多重予約防止のためのロック再試行の上限。auto-assign（指名なし）で
# 複数のスタッフ/テーブル候補がロック後に競合と判明した場合、この回数まで
# 次点候補を試す。実際のスタッフ/テーブル数はこれよりずっと少ないのが通常のため、
# 十分に大きい値にして「候補を全部試し切れない」ケースを実質なくす。
_MAX_LOCK_RETRY = 50


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
        duration_minutes=reservation.duration_minutes,
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


def _existing_duration_minutes(existing: "Reservation", default_duration_minutes: int) -> int:
    """
    Reservation Intelligence Phase B: 既存予約自身の占有時間（分）を返す。

    重要（今回のPhase Bで修正した最重要バグ）: 以前はこの値の代わりに
    「今回の新規リクエストのduration_minutes」を既存予約の終了時刻計算に
    流用しており、既存予約と新規リクエストで長さが異なる場合に重複判定が
    誤る欠陥があった（例: 20:00〜22:00の既存予約に対し、21:30〜22:00
    〈30分〉の新規リクエストを流用すると21:30〜22:00の誤った終了時刻に
    なり、本来重複するはずの時間帯を見逃す）。

    existing.duration_minutes（Phase Bで追加した新列）が設定されていれば
    それを唯一のsource of truthとして使う。Phase B以前に作成された既存
    予約はこの列がNULLのため、その場合のみ店舗のデフォルト所要時間
    （shop.reservation_duration_minutes or 90）にフォールバックする
    （NULL=無制限とは絶対に扱わない。必ず具体的な分数に解決する）。
    """
    return existing.duration_minutes or default_duration_minutes


async def _lookup_yesterday_overnight_session(
    db: AsyncSession, shop_id: str, today_date: date_type, start_dt: datetime,
) -> tuple[Optional[ShopHours], Optional[date_type]]:
    """
    Reservation Intelligence Phase D-1: today_dateの前日（yesterday）のShopHoursが
    日跨ぎ営業（closes_next_day=True）であり、かつstart_dtがその実際の営業セッション
    範囲 [yesterday opening, yesterday closing + 1日) に収まっている場合にのみ、
    そのShopHoursと帰属日（yesterday_date）を返す。それ以外は (None, None)。

    日跨ぎを一切使わない店舗（yesterdayがcloses_next_day=Falseまたは定休日/未設定）
    では常に (None, None) を返し、呼び出し元は従来通りの判定にフォールバックする。
    """
    yesterday_date = today_date - timedelta(days=1)
    yesterday_weekday = yesterday_date.weekday()
    result = await db.execute(
        select(ShopHours).filter(ShopHours.shop_id == shop_id, ShopHours.day_of_week == yesterday_weekday)
    )
    yesterday_hours = result.scalar_one_or_none()
    if yesterday_hours is None or yesterday_hours.is_closed or not yesterday_hours.closes_next_day:
        return None, None

    session_start = datetime.combine(yesterday_date, yesterday_hours.opening_time)
    session_end = datetime.combine(yesterday_date, yesterday_hours.closing_time) + timedelta(days=1)
    if session_start <= start_dt < session_end:
        return yesterday_hours, yesterday_date
    return None, None


async def _resolve_business_session(
    db: AsyncSession, shop_id: str, start_dt: datetime,
) -> tuple[Optional[ShopHours], Optional[date_type], Optional[str]]:
    """
    Reservation Intelligence Phase D-1: 指定した予約開始日時(start_dt)が、
    どの「営業セッション」に属するかを決定する共通ロジック。
    check_single_slot_availability() / create_reservation() / update_reservation()
    の3箇所から呼ばれる（get_availability()は日付単位の空き一覧を返す性質上、
    別ロジックのまま自身のcalendar dateの営業時間のみを扱う。深夜側からの
    前日セッション検索が必要な単発チェックとは目的が異なるため、意図的に
    本関数を使わない。詳細は同関数のコメント参照）。

    重要な設計原則（ユーザー承認済み仕様。calendar dateとbusiness session date
    の混同を避けるため、この判定をこの関数1箇所に一本化し、AIには一切
    計算させない）:

    営業セッションは「開始日」に帰属する。例えば月曜18:00〜翌03:00営業の
    場合、火曜01:00の予約は「火曜日の営業セッション」ではなく「月曜日から
    継続する営業セッション」に属する。

    判定順序:
    1. start_dtの暦日（today）自身のShopHoursを取得する。
    2. todayのShopHoursが存在し、定休日でなく、start_dt.time()がtodayの
       opening_time以降であれば、todayのセッションに属するとみなす
       （実際にそのセッション内に収まるか＝last_order_time/closing_timeを
       超えていないかの最終判定は、本関数の戻り値を使って呼び出し元が
       _validate_reservation_time_window()で行う。ここでは「どの日の
       ShopHoursを基準に判定すべきか」だけを決定し、"outside_business_hours"
       の判定自体はここでは行わない。これは既存4箇所の呼び出し順序
       ―― 例えばcheck_single_slot_availability()のservice_unavailable判定が
       境界判定より先に行われる順序 ―― を変えないための意図的な設計）。
    3. 2に該当しない場合（start_dt.time()がtodayの開店前、またはtoday自体が
       定休日／未設定）、前日（yesterday）からの日跨ぎ継続セッションを
       _lookup_yesterday_overnight_session()で確認する。該当すれば
       yesterdayのセッションに属する。
    4. どちらにも属さない場合は、todayの状態から理由コードを返す（既存の
       reason_code体系をそのまま維持）:
       - todayのShopHours行が存在しない → "business_hours_not_configured"
       - todayが定休日 → "shop_closed"
       日跨ぎを一切使わない店舗では、3のyesterday確認は常にno-op
       （yesterday.closes_next_dayが常にFalseのため）であり、1・2・4だけの
       判定は既存の「if hours is None: ...` / `if hours.is_closed: ...`」
       と完全に同じ結果になる。

    戻り値: (owning_hours, session_date, reason_code)
    - 成立時: (このセッションの基準となるShopHours行, セッションが帰属する
      暦日, None)
    - 不成立時: (None, None, reason_code)

    session_dateは、ShopClosure判定や_validate_reservation_time_window()に
    「このセッションがどの日に帰属するか」として渡す、start_dt.date()
    （calendar date）とは独立した値である点に注意。
    """
    today_date = start_dt.date()
    today_weekday = today_date.weekday()

    today_result = await db.execute(
        select(ShopHours).filter(ShopHours.shop_id == shop_id, ShopHours.day_of_week == today_weekday)
    )
    today_hours = today_result.scalar_one_or_none()

    if today_hours is not None and not today_hours.is_closed:
        if start_dt.time() >= today_hours.opening_time:
            return today_hours, today_date, None
        # start_dtがtodayの開店時刻より前 → 前日からの日跨ぎ継続の可能性を確認
        yesterday_hours, yesterday_date = await _lookup_yesterday_overnight_session(
            db, shop_id, today_date, start_dt
        )
        if yesterday_hours is not None:
            return yesterday_hours, yesterday_date, None
        # 前日からの継続でもない → todayのセッションとして扱い、最終的な
        # "outside_business_hours"判定はhandleに委ねる（開店前だが日跨ぎ
        # ではない、という従来通りのケース）。
        return today_hours, today_date, None

    # todayのShopHoursが存在しない、または定休日 → 前日からの日跨ぎ継続を確認
    yesterday_hours, yesterday_date = await _lookup_yesterday_overnight_session(
        db, shop_id, today_date, start_dt
    )
    if yesterday_hours is not None:
        return yesterday_hours, yesterday_date, None

    if today_hours is None:
        return None, None, "business_hours_not_configured"
    return None, None, "shop_closed"


def _validate_reservation_time_window(
    hours: ShopHours, session_date: date_type, start_dt: datetime, duration_minutes: int
) -> Optional[str]:
    """
    Reservation Intelligence Phase C/D-1: 予約枠（start_dt 〜
    start_dt+duration_minutes）全体が、営業セッション(hours, session_date)に
    収まっているかどうかを判定する共通ロジック。

    Phase Cでの背景（変更なし）: check_single_slot_availability() /
    get_availability() / create_reservation() / update_reservation() の
    4箇所すべてから呼ばれる唯一のsource of truth。

    Phase D-1での変更点: 引数を (target_date, start_time) という「同日内の
    時刻」の組から、(session_date, start_dt) という「セッションの帰属日
    ＋実際の日時（datetime）」の組に変更した。日跨ぎセッションでは
    「00:30」のような時刻だけでは実際にどのcalendar dateを指すのか一意に
    定まらないため（前日の継続か、当日自身のセッションか）、呼び出し元
    （_resolve_business_session()の結果、またはget_availability()自身の
    target_date）が既に確定させたsession_dateと、曖昧さのない実datetimeで
    ある start_dt を直接受け取ることで、この関数自身が日付を推測する必要を
    なくした（AIはもちろん、この関数もdatetimeの取り違えを起こさない設計）。

    判定内容（Phase Cから変更なし。closes_next_day/last_order_next_dayを
    考慮した実datetimeで同じ3条件を評価するだけ）:
    1. start_dt が session_start（session_date + opening_time）以降であること。
    2. start_dt が「最終予約開始可能時刻」（last_order_time が設定されて
       いればその値。last_order_next_day=Trueなら+1日。未設定なら
       session_close から duration_minutes を差し引いた時刻）以前である
       こと。
    3. 予約の終了時刻（start_dt + duration_minutes）が session_close
       （session_date + closing_time。closes_next_day=Trueなら+1日）を
       超えないこと。

    境界値の扱いはPhase Cから変更なし（「触れるのはOK、超過はNG」。
    例: 予約終了時刻がclosing_timeにちょうど一致する場合はOK）。

    戻り値: 問題なければNone。問題があれば既存のreason_code文字列
    "outside_business_hours"（新規reason_codeは追加しない。既存のAI案内
    文言・テストとの互換性を保つため）。
    """
    session_start_dt = datetime.combine(session_date, hours.opening_time)
    session_close_dt = datetime.combine(session_date, hours.closing_time)
    if hours.closes_next_day:
        session_close_dt += timedelta(days=1)

    if start_dt < session_start_dt:
        return "outside_business_hours"

    if hours.last_order_time is not None:
        last_order_dt = datetime.combine(session_date, hours.last_order_time)
        if hours.last_order_next_day:
            last_order_dt += timedelta(days=1)
    else:
        last_order_dt = session_close_dt - timedelta(minutes=duration_minutes)

    if start_dt > last_order_dt:
        return "outside_business_hours"

    end_dt = start_dt + timedelta(minutes=duration_minutes)
    if end_dt > session_close_dt:
        return "outside_business_hours"

    return None


async def _find_available_table(
    db: AsyncSession, shop_id: str, party_size: int, start: datetime, duration_minutes: int,
    default_duration_minutes: int, excluded_table_ids: Optional[set] = None,
) -> tuple[Optional[str], bool]:
    """
    条件に合う空きテーブルを探す。
    戻り値: (table_id または None, テーブルが1件も登録されていないか)
    テーブルが1件も登録されていない場合は容量チェックを行わず None を返して常に予約可能とする。

    excluded_table_ids: Phase3E-3で追加。_find_and_lock_available_table()が
    ロック後の再チェックで競合と判明した候補を除外して次点を探すために使う。
    通常の呼び出し（check_availability等）では指定しない＝従来と完全に同じ挙動。
    候補の並び順は (capacity, id) の昇順に固定する（Phase3E-3で追加。同時多重
    予約防止のロック取得順序を、同時に走る複数リクエスト間で一致させるため。
    以前は明示的な順序がなかったため、複数の空きテーブルがある場合の自動選択が
    DBの返却順という不定なものだったが、これは元々ドキュメント化された仕様
    ではなく、この決定的な順序への変更は安全側の改善として扱う）。

    default_duration_minutes: Reservation Intelligence Phase Bで追加。
    既存予約自身のduration_minutesがNULL（Phase B以前に作成された予約）
    だった場合にのみ使うフォールバック値。呼び出し元は必ず
    shop.reservation_duration_minutes or 90 を渡すこと。
    """
    tables_result = await db.execute(
        select(ShopTable).filter(ShopTable.shop_id == shop_id, ShopTable.is_active == True)
    )
    tables = tables_result.scalars().all()
    if not tables:
        return None, True

    excluded = excluded_table_ids or set()
    candidates = sorted(
        [t for t in tables if t.capacity >= party_size and t.id not in excluded],
        key=lambda t: (t.capacity, t.id),
    )
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
            existing_end = existing_start + timedelta(
                minutes=_existing_duration_minutes(existing, default_duration_minutes)
            )
            if existing_start < end and existing_end > start:
                conflicting = True
                break
        if not conflicting:
            return table.id, False

    return None, False


async def _lock_and_verify_table_slot(
    db: AsyncSession, table_id: str, start: datetime, duration_minutes: int, default_duration_minutes: int,
    exclude_reservation_id: Optional[str] = None,
) -> bool:
    """
    Phase3E-3: 同時多重予約防止。対象テーブルの行をSELECT ... FOR UPDATEで
    ロックしたうえで、ロック取得後に改めて予約重複を再チェックする。
    create_reservation()のトランザクション内でのみ呼び出すこと
    （check_availability/check_single_slot_availability側は高速・ロックフリーの
    ままにするため、絶対にここからは呼ばない）。

    default_duration_minutes: Reservation Intelligence Phase B追加。
    _find_available_table()と同じ意味（既存予約自身のduration_minutesが
    NULLの場合のみのフォールバック）。

    exclude_reservation_id: Reservation Intelligence Phase C追加。
    update_reservation()（Owner予約編集）が、変更対象のReservation自身を
    重複判定から除外するために使う。create_reservation()（新規作成）では
    まだそのReservationが存在しないため常にNoneのまま呼び出され、従来通りの
    挙動になる。
    """
    await db.execute(select(ShopTable.id).filter(ShopTable.id == table_id).with_for_update())
    end = start + timedelta(minutes=duration_minutes)
    overlap_filters = [
        Reservation.table_id == table_id,
        Reservation.status.in_(["pending", "confirmed"]),
    ]
    if exclude_reservation_id is not None:
        overlap_filters.append(Reservation.id != exclude_reservation_id)
    overlap_result = await db.execute(select(Reservation).filter(*overlap_filters))
    for existing in overlap_result.scalars().all():
        existing_start = existing.reservation_date
        existing_end = existing_start + timedelta(
            minutes=_existing_duration_minutes(existing, default_duration_minutes)
        )
        if existing_start < end and existing_end > start:
            return False
    return True


async def _find_and_lock_available_table(
    db: AsyncSession, shop_id: str, party_size: int, start: datetime, duration_minutes: int,
    default_duration_minutes: int,
) -> tuple[Optional[str], bool]:
    """
    Phase3E-3: create_reservation()専用。_find_available_table()で候補を探した
    直後、実際にINSERTする前に候補のテーブル行をFOR UPDATEでロックし、ロック
    取得後に予約重複を再チェックしてから確定する。check_availability時点の
    チェックとcreate_reservation実行時点の間に別のリクエストが割り込んでも、
    同じテーブル×重複時間帯へ二重に予約が確定することを防ぐ。

    ロック後に競合が判明した場合（＝別のリクエストが先にそのテーブルを
    確保した）は、その候補を除外して次点のテーブル候補を再検索する
    （_MAX_LOCK_RETRY回まで）。テーブルが1件も登録されていない
    （unmanaged=True）店舗では、ロック対象がないため従来通り常に予約可能。

    default_duration_minutes: Reservation Intelligence Phase B追加。
    _find_available_table()/_lock_and_verify_table_slot()へそのまま渡す。
    """
    excluded: set = set()
    for _ in range(_MAX_LOCK_RETRY):
        table_id, unmanaged = await _find_available_table(
            db, shop_id, party_size, start, duration_minutes, default_duration_minutes,
            excluded_table_ids=excluded,
        )
        if unmanaged:
            return table_id, True
        if table_id is None:
            return None, False
        if await _lock_and_verify_table_slot(db, table_id, start, duration_minutes, default_duration_minutes):
            return table_id, False
        excluded.add(table_id)
    return None, False


async def _staff_scheduled_on_own_day(
    db: AsyncSession, staff_id: str, start_dt: datetime, end_dt: datetime, session_date: date_type,
) -> bool:
    """
    Reservation Intelligence Phase D-1: Phase3E-3の_is_staff_scheduled()判定
    ロジック（override優先・day_off・unavailable差し引き）そのものを、
    「session_date（＝勤務セッションの開始日）を基準に判定する」形に切り出した
    もの。判定の優先順位・意味は一切変更していない（Phase3E-3のdocstring参照）。

    Phase D-1で追加したのはends_next_dayの考慮のみ:
    override_type="hours"/"unavailable"のstart_time〜end_time、および
    StaffWeeklyShiftのstart_time〜end_timeが日跨ぎ（ends_next_day=True）の
    場合、終了時刻をsession_dateの翌日として扱う。

    _is_staff_scheduled()から、start_dtの暦日自身（today）を基準とした判定と、
    前日（yesterday）から継続する日跨ぎ勤務を基準とした判定の、両方から
    同じロジックで呼ばれる。
    """
    weekday = session_date.weekday()
    overrides_result = await db.execute(
        select(StaffShiftOverride).filter(
            StaffShiftOverride.staff_id == staff_id,
            StaffShiftOverride.target_date == session_date,
        )
    )
    overrides = list(overrides_result.scalars().all())

    if any(o.override_type == "day_off" for o in overrides):
        return False

    hours_overrides = [o for o in overrides if o.override_type == "hours"]
    if hours_overrides:
        base_windows = []
        for o in hours_overrides:
            w_start = datetime.combine(session_date, o.start_time)
            w_end = datetime.combine(session_date, o.end_time)
            if o.ends_next_day:
                w_end += timedelta(days=1)
            base_windows.append((w_start, w_end))
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
        base_windows = []
        for w in weekly:
            w_start = datetime.combine(session_date, w.start_time)
            w_end = datetime.combine(session_date, w.end_time)
            if w.ends_next_day:
                w_end += timedelta(days=1)
            base_windows.append((w_start, w_end))

    if not any(bw_start <= start_dt and end_dt <= bw_end for bw_start, bw_end in base_windows):
        return False

    unavailable_overrides = [o for o in overrides if o.override_type == "unavailable"]
    for o in unavailable_overrides:
        u_start = datetime.combine(session_date, o.start_time)
        u_end = datetime.combine(session_date, o.end_time)
        if o.ends_next_day:
            u_end += timedelta(days=1)
        if u_start < end_dt and u_end > start_dt:
            return False

    return True


async def _is_staff_scheduled(
    db: AsyncSession, staff_id: str, start_dt: datetime, duration_minutes: int
) -> bool:
    """
    Phase3E-2: 指定した日時（start_dt〜start_dt+duration_minutes）に、指定スタッフが
    シフト上「勤務している」かどうかを判定する。この関数は_find_available_staff_for_service
    からenforce_schedule=Trueの場合にのみ呼ばれ、shop.staff_schedule_enabledが
    Falseの店舗（デフォルト・既存の全店舗）では一切呼ばれない。

    判定ロジック（優先順位。Phase3E-3で確定・明文化。以後この関数がSSOTとする）:
    0. staff_schedule_enabledがFalseの店舗ではこの関数自体が一切呼ばれない
       （呼び出し元_find_available_staff_for_serviceのenforce_schedule引数を参照）。
       staff_schedule_enabled=Trueの場合のみ、以下の判定が適用される。
    1. 対象日にDate Override（StaffShiftOverride）があれば、その日はWeekly Shiftを
       完全に無視し、Override側だけでその日の勤務時間帯を決める：
       - override_type="day_off" が1件でもあれば、その日は終日不可（勤務時間帯なし）。
       - override_type="hours" があれば、その時間帯（複数行の分割も可）だけが
         その日の勤務時間帯になる（Weekly Shiftは一切参照しない）。
       Date Overrideが無い日は、Weekly Shiftのその曜日の時間帯がそのまま
       勤務時間帯になる。
    2. 予約したい時間帯が、1で決まった勤務時間帯（複数コマの場合はそのいずれか）に
       完全に収まっているかを確認する。収まっていなければ不可。
    3. 対象日にoverride_type="unavailable"があれば、その時間帯を2の勤務時間帯から
       差し引く（勤務時間帯を置き換えるのではなく、重なる部分だけを不可にする）。
       予約したい時間帯がこの不在時間と重なっていれば不可。
    4. Phase3E-3で確定: スタッフに週次シフト・日付調整のいずれも一度も
       登録されていない場合は「シフト未設定」として常にFalse（勤務不可＝
       Fail Closed）を返す。Phase3E-2では逆に常にTrue（安全側フォールバックとして
       常に予約可能扱い）としていたが、これはオーナーが意図せずシフト未設定の
       スタッフに予約が入り続けてしまう危険があったため、本フェーズで明示的に
       反転した（ユーザー承認済みの仕様変更）。この状態を放置しないよう、
       shop-manage.html側にシフト未設定スタッフの警告UIを追加している
       （app/routers/staff.py の get_staff_schedule_warnings()を参照）。

    Reservation Intelligence Phase D-1追記: 上記1〜3の実際の判定は
    _staff_scheduled_on_own_day()に切り出した（ロジック自体は無変更）。
    本関数はまずstart_dtの暦日自身（today）を基準にそれを試し、収まらない
    場合にのみ、前日（yesterday）から継続する日跨ぎ勤務
    （StaffWeeklyShift.ends_next_day / StaffShiftOverride.ends_next_day）を
    基準に再度試す。app.routers.reservations._resolve_business_session()
    （ShopHoursの日跨ぎ営業セッション判定）と同じ設計思想であり、
    calendar dateとsession dateを混同しない。日跨ぎ勤務を一切登録していない
    スタッフでは、yesterday側の判定は常に「その日の勤務時間帯に収まらない」
    （yesterdayの日付でdatetimeを組み立てるため、todayの時刻とは必ず
    ズレる）ため無条件にFalseとなり、既存の複数シフト/日の挙動には一切
    影響しない。
    """
    target_date = start_dt.date()
    end_dt = start_dt + timedelta(minutes=duration_minutes)

    any_weekly = await db.execute(
        select(StaffWeeklyShift.id).filter(StaffWeeklyShift.staff_id == staff_id).limit(1)
    )
    has_any_weekly = any_weekly.scalar_one_or_none() is not None
    any_override_ever = await db.execute(
        select(StaffShiftOverride.id).filter(StaffShiftOverride.staff_id == staff_id).limit(1)
    )
    has_any_override = any_override_ever.scalar_one_or_none() is not None
    if not has_any_weekly and not has_any_override:
        # Phase3E-3: Fail Closed（Phase3E-2からの明示的な反転。上記docstring参照）
        return False

    if await _staff_scheduled_on_own_day(db, staff_id, start_dt, end_dt, target_date):
        return True

    # Phase D-1: todayの勤務時間帯に収まらない場合、前日から継続する日跨ぎ
    # 勤務の可能性を確認する。
    yesterday_date = target_date - timedelta(days=1)
    if await _staff_scheduled_on_own_day(db, staff_id, start_dt, end_dt, yesterday_date):
        return True

    return False


async def _find_available_staff_for_service(
    db: AsyncSession, shop_id: str, service_id: str, start: datetime, duration_minutes: int,
    default_duration_minutes: int,
    preferred_staff_id: Optional[str] = None, enforce_schedule: bool = False,
    excluded_staff_ids: Optional[set] = None,
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

    excluded_staff_ids: Phase3E-3で追加。_find_and_lock_available_staff_for_service()が
    ロック後の再チェックで競合と判明した候補を除外して次点を探すために使う。
    通常の呼び出し（check_availability等）では指定しない＝従来と完全に同じ挙動。

    候補の並び順はstaff.idの昇順に固定する（Phase3E-3で追加。同時多重予約防止の
    ロック取得順序を、同時に走る複数リクエスト間で一致させるため。以前は
    明示的な順序がなかったため、複数の空きスタッフがいる場合の自動選択が
    DBの返却順という不定なものだったが、ドキュメント化された仕様ではなく、
    この決定的な順序への変更は安全側の改善として扱う）。

    default_duration_minutes: Reservation Intelligence Phase Bで追加。
    _find_available_table()と同じ意味（既存予約自身のduration_minutesが
    NULLの場合のみのフォールバック）。呼び出し元は必ず
    shop.reservation_duration_minutes or 90 を渡すこと。
    """
    staff_result = await db.execute(
        select(Staff)
        .join(StaffService, StaffService.staff_id == Staff.id)
        .filter(
            StaffService.service_id == service_id,
            Staff.shop_id == shop_id,
            Staff.is_active == "active",
        )
        .order_by(Staff.id)
    )
    eligible_staff = list(staff_result.scalars().all())
    if not eligible_staff:
        return None, True

    if preferred_staff_id:
        eligible_staff = [s for s in eligible_staff if s.id == preferred_staff_id]
        if not eligible_staff:
            return None, False

    excluded = excluded_staff_ids or set()
    eligible_staff = [s for s in eligible_staff if s.id not in excluded]

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
            existing_end = existing_start + timedelta(
                minutes=_existing_duration_minutes(existing, default_duration_minutes)
            )
            if existing_start < end and existing_end > start:
                conflicting = True
                break
        if not conflicting:
            return staff.id, False

    return None, False


async def _lock_and_verify_staff_slot(
    db: AsyncSession, staff_id: str, start: datetime, duration_minutes: int, default_duration_minutes: int,
    exclude_reservation_id: Optional[str] = None,
) -> bool:
    """
    Phase3E-3: 同時多重予約防止。対象スタッフの行をSELECT ... FOR UPDATEで
    ロックしたうえで、ロック取得後に改めて予約重複を再チェックする。
    create_reservation()のトランザクション内でのみ呼び出すこと
    （check_availability/check_single_slot_availability側は高速・ロックフリーの
    ままにするため、絶対にここからは呼ばない。シフトの再チェックはここでは
    行わない＝シフトはロック対象ではなく、_find_available_staff_for_service側の
    通常ロジックで既に確認済みのため）。

    default_duration_minutes: Reservation Intelligence Phase B追加。
    _find_available_table()と同じ意味（既存予約自身のduration_minutesが
    NULLの場合のみのフォールバック）。

    exclude_reservation_id: Reservation Intelligence Phase C追加。
    update_reservation()（Owner予約編集）が、変更対象のReservation自身を
    重複判定から除外するために使う（_lock_and_verify_table_slot()と同じ
    考え方）。create_reservation()では常にNoneのまま呼び出され、従来通りの
    挙動になる。
    """
    await db.execute(select(Staff.id).filter(Staff.id == staff_id).with_for_update())
    end = start + timedelta(minutes=duration_minutes)
    overlap_filters = [
        Reservation.staff_id == staff_id,
        Reservation.status.in_(["pending", "confirmed"]),
    ]
    if exclude_reservation_id is not None:
        overlap_filters.append(Reservation.id != exclude_reservation_id)
    overlap_result = await db.execute(select(Reservation).filter(*overlap_filters))
    for existing in overlap_result.scalars().all():
        existing_start = existing.reservation_date
        existing_end = existing_start + timedelta(
            minutes=_existing_duration_minutes(existing, default_duration_minutes)
        )
        if existing_start < end and existing_end > start:
            return False
    return True


async def _find_and_lock_available_staff_for_service(
    db: AsyncSession, shop_id: str, service_id: str, start: datetime, duration_minutes: int,
    default_duration_minutes: int,
    preferred_staff_id: Optional[str] = None, enforce_schedule: bool = False,
) -> tuple[Optional[str], bool]:
    """
    Phase3E-3: create_reservation()専用。_find_available_staff_for_service()で
    候補を探した直後、実際にINSERTする前に候補のスタッフ行をFOR UPDATEでロックし、
    ロック取得後に予約重複を再チェックしてから確定する。check_availability時点の
    チェックとcreate_reservation実行時点の間に別のリクエストが割り込んでも、
    同じスタッフ×重複時間帯へ二重に予約が確定することを防ぐ。

    ロック後に競合が判明した場合（＝別のリクエストが先にそのスタッフを確保した）:
    - 指名予約（preferred_staff_id指定あり）の場合は代替候補を探さず不可として返す
      （お客様が指名していないスタッフを勝手に割り当てるべきではないため）。
    - 指名なし（auto-assign）の場合は、その候補を除外して次点のスタッフ候補を
      再検索する（_MAX_LOCK_RETRY回まで）。

    このサービスにスタッフが1人も割り当てられていない（unmanaged=True）場合は
    従来通りロック対象がなく常に予約可能（このパスの二重予約チェックは
    Phase3E-3のスコープ外。Phase3D以前からの「管理対象データが0件なら常に
    空き扱い」という既存設計をそのまま維持する）。

    default_duration_minutes: Reservation Intelligence Phase B追加。
    _find_available_staff_for_service()/_lock_and_verify_staff_slot()へ
    そのまま渡す。
    """
    excluded: set = set()
    for _ in range(_MAX_LOCK_RETRY):
        candidate_id, unmanaged = await _find_available_staff_for_service(
            db, shop_id, service_id, start, duration_minutes, default_duration_minutes,
            preferred_staff_id, enforce_schedule, excluded_staff_ids=excluded,
        )
        if unmanaged:
            return candidate_id, True
        if candidate_id is None:
            return None, False
        if await _lock_and_verify_staff_slot(db, candidate_id, start, duration_minutes, default_duration_minutes):
            return candidate_id, False
        if preferred_staff_id:
            return None, False
        excluded.add(candidate_id)
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
    start_dt = datetime.combine(target_date, target_time)

    # Reservation Intelligence Phase D-1: この日時がどの営業セッション
    # （開始日）に属するかをまず決定する（詳細は_resolve_business_session()の
    # docstring参照）。日跨ぎを一切使わない店舗ではsession_dateは常に
    # target_dateと一致し、reason_codeも従来のbusiness_hours_not_configured/
    # shop_closedとまったく同じ条件・同じタイミングで返るため、既存の挙動は
    # 変化しない。
    owning_hours, session_date, resolve_reason = await _resolve_business_session(db, shop.id, start_dt)
    if owning_hours is None:
        return False, resolve_reason

    # Phase D-1: ShopClosureは「営業セッションの開始日」を基準に判定する
    # （ユーザー承認済み仕様。前日から続く日跨ぎセッションの場合、翌日側の
    # calendar dateのShopClosureはこのセッションに影響させない）。
    closure = await _get_closure_for_date(db, shop.id, session_date)
    if closure:
        return False, "temporary_closure"

    service: Optional[Service] = None
    if service_id:
        service = await db.get(Service, service_id)
        if not service or service.shop_id != shop.id or service.is_active != "active":
            return False, "service_unavailable"

    duration = _resolve_reservation_duration(service, shop)
    # Reservation Intelligence Phase C/D-1: 営業時間境界（開始・終了の両方、
    # 日跨ぎ対応込み）の判定を共通helperへ一本化（詳細は
    # _validate_reservation_time_window()のdocstring参照）。
    boundary_reason = _validate_reservation_time_window(owning_hours, session_date, start_dt, duration)
    if boundary_reason is not None:
        return False, boundary_reason

    # create_reservation / get_availability と同じ比較方法。Phase3E-3で
    # datetime.utcnow()からreservation_dateと同じ基準(JST-naive)の
    # _reservation_basis_now()に修正した（調査で確認した実際のタイムゾーン
    # 不整合バグの修正。詳細は_reservation_basis_now()のdocstring参照）。
    #
    # Reservation Intelligence Phase A追記: 以前はここも invalid_request
    # （入力形式の不正）に丸めていたが、「日時が既に過去である」ことと
    # 「日付/時刻の形式が不正である」ことは原因も対応も全く異なるため分離した。
    # 前者はAI側で「その時刻は過去です」と自然に案内し、別の時刻を伺うべき
    # ケースであり、後者のように「形式を確認して再度呼び出す」対応は誤り
    # （形式は正しいが、時刻そのものが既に過ぎているだけのため）。
    # AI側の反応方針は _REALTIME_TOOLS の check_availability description
    # 参照。
    if start_dt <= _reservation_basis_now():
        return False, "time_in_past"

    # Reservation Intelligence Phase B: 既存予約自身のduration_minutesがNULL
    # （Phase B以前に作成された予約）の場合にのみ使うフォールバック値。
    # _resolve_reservation_duration()と同じ優先順位（Service→店舗デフォルト→90分）
    # だが、こちらは「既存予約」の、上のdurationは「今回の新規リクエスト」の
    # 所要時間であり、意味が異なる2つの値であることに注意。
    default_duration = shop.reservation_duration_minutes or 90

    if service:
        found_staff_id, unmanaged = await _find_available_staff_for_service(
            db, shop.id, service.id, start_dt, duration, default_duration, staff_id,
            enforce_schedule=bool(shop.staff_schedule_enabled),
        )
        if unmanaged or found_staff_id is not None:
            return True, None
        return False, ("staff_unavailable" if staff_id else "fully_booked")
    else:
        table_id, unmanaged = await _find_available_table(
            db, shop.id, party_size, start_dt, duration, default_duration
        )
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

    Phase3G Workstream2追記（Coupon Reservation Integrity）:
    - このSELECTに .with_for_update() を追加し、このCouponの行ロックを
      create_reservation()のトランザクション終了(commit/rollback)まで保持する。
      理由: usage_limit判定→usage_count加算が「読んで＋1して書く」という
      非atomicな処理のため、ロックなしでは usage_limit=1 のクーポンに
      ほぼ同時に2件の予約が来た場合、両方が「まだ上限に達していない」と
      判定してしまい、上限を超えて成立してしまう（Phase3F監査で発覚した
      不整合の根本原因の一つ）。同一coupon_idに対する同時リクエストは
      このロックにより直列化される（スタッフ／テーブルの
      _find_and_lock_available_* と同じ考え方）。
    - usage_limit判定に使うのは、この後の cancel_reservation / update_reservation
      側の変更（Phase3G参照）により「キャンセルされた予約分は差し引かれた
      現在有効な利用数」を表すよう保守される coupon.usage_count 自体。
      予約作成時に coupon_code を追加・変更・解除する手段は現状のAPIに
      存在しないため（ReservationUpdateRequestにcoupon関連フィールドは無い）、
      予約作成時と予約キャンセル時の2箇所だけを正しく保守すれば
      ライフサイクル全体が正しくなる。
    """
    result = await db.execute(
        select(Coupon)
        .filter(Coupon.shop_id == shop_id, Coupon.code == code)
        .with_for_update()
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


async def _release_coupon_usage_if_any(db: AsyncSession, reservation: Reservation) -> None:
    """
    Phase3G Workstream2: 予約がキャンセルされる際、その予約がクーポンを
    使用していた場合、Coupon.usage_count / total_discount_given を
    「現在有効な利用数」に戻す（減算する）。

    設計方針（重要）:
    - 対象は status が「これまでcancelledではなかった」→「cancelledになる」
      という遷移が実際に起きる場合のみ（呼び出し側で判定済みであることを
      前提とする）。同じ予約を誤って二重にキャンセル操作しても、この関数
      自体は「まだcancelledではない予約」に対してのみ呼ばれるため二重減算
      にはならない。
    - no_show・completedへの遷移では呼ばない（予約枠自体は実際に確保され、
      店舗側の受け入れコストが発生しているため、クーポンは「使用済み」の
      ままとする。この解釈はPhase3G調査時点の判断であり、将来的にビジネス
      要件として明示的に変わる可能性がある）。
    - Coupon行を .with_for_update() でロックしてから減算する
      （_resolve_coupon_for_bookingと同じ行を対象にした排他制御）。
    - usage_count は 0 未満にはしない（既存データの手動修正や、本関数が
      導入される前に作られたキャンセル済み予約など、想定外の状態からでも
      安全側に倒す）。
    """
    if not reservation.coupon_id:
        return
    result = await db.execute(
        select(Coupon).filter(Coupon.id == reservation.coupon_id).with_for_update()
    )
    coupon = result.scalar_one_or_none()
    if not coupon:
        return
    coupon.usage_count = max(0, (coupon.usage_count or 0) - 1)
    if reservation.discount_amount:
        coupon.total_discount_given = max(
            0.0, (coupon.total_discount_given or 0.0) - reservation.discount_amount
        )
    coupon.updated_at = datetime.utcnow()


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

    duration = _resolve_reservation_duration(service, shop)

    # Reservation Intelligence Phase D-1: 日跨ぎ営業（closes_next_day=True）の
    # 場合、closing_time・last_order_timeが「翌日」の時刻であることを考慮して
    # 実datetimeを組み立てる。日跨ぎを使わない店舗（closes_next_day=False）
    # では従来と完全に同じdatetimeになり、挙動は変化しない。
    #
    # 注意（意図的なスコープ限定。Phase D-1完了報告の既知の制限として明記）:
    # ここで扱うのは「target_date自身が開始日であるセッション」のみ。
    # 前日から継続する日跨ぎセッションの深夜側スロット（例: 月曜18:00〜翌03:00
    # 営業に対してdate=火曜で問い合わせた場合の00:00〜03:00スロット）は、
    # この日付単位の空き一覧エンドポイントでは今回対象外とする
    # （check_single_slot_availability() / create_reservation() /
    # update_reservation()は_resolve_business_session()経由で正しく
    # 前日セッションを検出するため、実際の予約可否判定には影響しない）。
    closing_dt = datetime.combine(target_date, hours.closing_time)
    if hours.closes_next_day:
        closing_dt += timedelta(days=1)

    if hours.last_order_time is not None:
        latest_start_dt = datetime.combine(target_date, hours.last_order_time)
        if hours.last_order_next_day:
            latest_start_dt += timedelta(days=1)
    else:
        latest_start_dt = closing_dt - timedelta(minutes=duration)

    opening_dt = datetime.combine(target_date, hours.opening_time)

    if latest_start_dt < opening_dt:
        return AvailabilityResponse(**common, is_open=True, message="本日は予約可能な時間枠がありません", slots=[])

    # Phase3E-3: reservation_dateと同じ基準(JST-naive)の「現在時刻」に統一
    # （datetime.utcnow()との比較は最大9時間ズレるバグだった。上部コメント参照）。
    now = _reservation_basis_now()
    # Reservation Intelligence Phase B: 既存予約自身のduration_minutesがNULLの
    # 場合のみのフォールバック値。check_single_slot_availability()と同じ考え方。
    default_duration = shop.reservation_duration_minutes or 90
    slots: List[AvailabilitySlot] = []
    cursor = opening_dt
    while cursor <= latest_start_dt:
        if cursor > now:
            # Reservation Intelligence Phase C: latest_start_dtまでの30分刻みの
            # 候補のうち、last_order_timeが設定されている店舗では「開始時刻は
            # last_order_time以内でも、終了時刻がclosing_timeを超える」候補が
            # 混在し得る（_validate_reservation_time_window()のdocstring参照）。
            # そのような候補はスロット自体を生成しない（営業時間外の時刻が
            # 一覧に出ないようにする。closure/is_closedと同じ扱い）。
            if _validate_reservation_time_window(hours, target_date, cursor, duration) is not None:
                cursor += timedelta(minutes=SLOT_INTERVAL_MINUTES)
                continue
            if service:
                found_staff_id, unmanaged = await _find_available_staff_for_service(
                    db, shop_id, service.id, cursor, duration, default_duration, staff_id,
                    enforce_schedule=bool(shop.staff_schedule_enabled),
                )
                available = unmanaged or (found_staff_id is not None)
            else:
                table_id, unmanaged = await _find_available_table(
                    db, shop_id, party_size, cursor, duration, default_duration
                )
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

        # Phase3E-3: reservation_dateと同じ基準(JST-naive)の「現在時刻」に統一
        # （datetime.utcnow()との比較は最大9時間ズレるバグだった。ファイル上部の
        # _reservation_basis_now()のdocstring参照）。
        # Reservation Intelligence Phase A追記: check_single_slot_availability()と
        # 同様の理由で invalid_request から time_in_past へ分離（詳細は同関数の
        # コメント参照）。
        if request.reservation_date <= _reservation_basis_now():
            raise _http_error(400, "過去の日時で予約することはできません", reason_code="time_in_past")

        service: Optional[Service] = None
        if request.service_id:
            service = await db.get(Service, request.service_id)
            if not service or service.shop_id != request.shop_id:
                raise _http_error(404, "指定されたサービスが見つかりません", reason_code="service_unavailable")
            if service.is_active != "active":
                raise _http_error(400, "このサービスは現在受付を停止しています", reason_code="service_unavailable")

        # Reservation Intelligence Phase D-1: この予約開始日時がどの営業
        # セッション（開始日）に属するかを決定する（詳細は
        # _resolve_business_session()のdocstring参照）。日跨ぎを一切使わない
        # 店舗ではsession_dateは常にrequest.reservation_date.date()と一致し、
        # reason_codeも従来のbusiness_hours_not_configured/shop_closedと
        # まったく同じ条件で返るため、既存の挙動は変化しない
        # （Phase3B.1の「営業時間未設定」と「定休日」の区別もそのまま維持）。
        owning_hours, session_date, resolve_reason = await _resolve_business_session(
            db, request.shop_id, request.reservation_date
        )
        if owning_hours is None:
            reason_detail = {
                "business_hours_not_configured": "この店舗は営業時間が設定されていないため、オンラインでの予約確定ができません",
                "shop_closed": "ご指定の日は定休日です",
            }.get(resolve_reason, "ご指定の時間は営業時間外です")
            raise _http_error(400, reason_detail, reason_code=resolve_reason)

        # Phase D-1: ShopClosureは「営業セッションの開始日」を基準に判定する
        # （ユーザー承認済み仕様。Section4参照）。
        closure = await _get_closure_for_date(db, request.shop_id, session_date)
        if closure:
            raise _http_error(400, "ご指定の日は臨時休業のため予約できません", reason_code="temporary_closure")

        duration = _resolve_reservation_duration(service, shop)
        # Reservation Intelligence Phase C/D-1: 以前はここだけ
        # `hours.last_order_time or hours.closing_time`（durationを一切
        # 考慮しない）という、check_single_slot_availability()/get_availability()
        # とは異なる緩い判定式を使っており、両者の間で「checkはNGなのに
        # createはOK」という不整合が起き得た（Phase B完了報告で発見、Phase C
        # で修正）。共通helperへ統一し、開始時刻だけでなく終了時刻が
        # closing_timeを超えないことも必ず確認する（日跨ぎ対応込み。詳細は
        # _validate_reservation_time_window()のdocstring参照）。
        boundary_reason = _validate_reservation_time_window(
            owning_hours, session_date, request.reservation_date, duration
        )
        if boundary_reason is not None:
            raise _http_error(400, "ご指定の時間は営業時間外です", reason_code=boundary_reason)

        # Reservation Intelligence Phase B: 既存予約自身のduration_minutesが
        # NULL（Phase B以前に作成された予約）の場合にのみ使うフォールバック値。
        # check_single_slot_availability()/get_availability()と同じ考え方。
        default_duration = shop.reservation_duration_minutes or 90

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

            # Phase3E-3: check_availability時点の空きチェックだけに頼らず、
            # ここ（実際にINSERTする直前）でFOR UPDATEロック＋再チェックを行う
            # ことで、ほぼ同時に届いた2件のリクエストが同じスタッフ×時間帯を
            # 二重に確保することを防ぐ（_find_and_lock_available_staff_for_service
            # のdocstring参照）。
            found_staff_id, unmanaged = await _find_and_lock_available_staff_for_service(
                db, request.shop_id, service.id, request.reservation_date, duration, default_duration,
                request.staff_id, enforce_schedule=bool(shop.staff_schedule_enabled),
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
            # Phase3E-3: テーブル予約も同様にFOR UPDATEロック＋再チェックで
            # 同時多重予約を防ぐ（_find_and_lock_available_tableのdocstring参照）。
            table_id, unmanaged = await _find_and_lock_available_table(
                db, request.shop_id, request.number_of_people, request.reservation_date, duration,
                default_duration,
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
            duration_minutes=duration,
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

        # Outbound AI Phase 1: 予約が新規に成立した直後にのみ通知ジョブをenqueueする
        # （idempotency_keyによる早期return・IntegrityError競合時の早期returnは
        # 「新規成立」ではないためこの行を通らず、二重通知は起きない）。
        # enqueue_reservation_confirmed_call()自身が非同期・失敗分離を保証しており、
        # 専用のDBセッションで完結するため、ここでの例外送出やこのdbセッションへの
        # 影響は一切発生しない設計（app.services.outbound_dispatchのdocstring参照）。
        await enqueue_reservation_confirmed_call(
            shop_id=shop.id,
            reservation_id=reservation.id,
            notification_enabled=bool(shop.reservation_phone_notification_enabled),
            notification_phone=shop.reservation_notification_phone,
        )

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
        # Phase3G Workstream2: cancel_reservationと同様、同一予約への同時更新
        # (特にstatus=cancelledへの同時呼び出し)によるクーポン利用数の
        # 二重解放を防ぐため、予約行をFOR UPDATEでロックする。
        result = await db.execute(
            select(Reservation)
            .options(selectinload(Reservation.table), selectinload(Reservation.staff), selectinload(Reservation.service), selectinload(Reservation.coupon))
            .filter(Reservation.id == reservation_id)
            .with_for_update()
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
                    # Phase3G Workstream2: cancelledへの遷移が実際に起きる
                    # 場合のみクーポン利用数を解放する（旧ステータスが既に
                    # cancelledだった場合はこのif自体に入らないため、
                    # 二重減算にはならない）。
                    await _release_coupon_usage_if_any(db, reservation)
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
            # Phase3E-3: reservation_dateと同じ基準(JST-naive)の「現在時刻」に統一
            if request.reservation_date <= _reservation_basis_now():
                raise HTTPException(status_code=400, detail="過去の日時には変更できません")

            # Reservation Intelligence Phase C: 日時が変更される場合のみ、
            # create_reservation()と同じ一連のルール（臨時休業・営業時間・
            # 既存予約との重複・スタッフシフト）を再検証してから保存する。
            # 以前はここで一切の再検証が行われておらず、Ownerが既存の別予約
            # と重なる時間へ自由に変更できてしまう（ダブルブッキングを作り
            # 出せてしまう）既知の不整合があった（Phase B完了報告で発見）。
            # guest_name等、日時に無関係な項目だけの変更ではこのブロックに
            # 入らないため、不要な検証は増えない。
            # Reservation Intelligence Phase D-1: この新しい予約開始日時が
            # どの営業セッション（開始日）に属するかを決定する（詳細は
            # _resolve_business_session()のdocstring参照）。日跨ぎを一切
            # 使わない店舗ではsession_dateは常にreservation_date.date()と
            # 一致し、reason判定もPhase Cから変化しない。
            owning_hours, session_date, resolve_reason = await _resolve_business_session(
                db, reservation.shop_id, request.reservation_date
            )
            if owning_hours is None:
                reason_detail = {
                    "business_hours_not_configured": "この店舗は営業時間が設定されていないため、その日時には変更できません",
                    "shop_closed": "ご指定の日は定休日のため、その日時には変更できません",
                }.get(resolve_reason, "ご指定の時間は営業時間外のため、その日時には変更できません")
                raise HTTPException(status_code=400, detail=reason_detail)

            # Phase D-1: ShopClosureは「営業セッションの開始日」を基準に判定する
            # （Section4参照）。
            new_closure = await _get_closure_for_date(db, reservation.shop_id, session_date)
            if new_closure:
                raise HTTPException(status_code=400, detail="ご指定の日は臨時休業のため、その日時には変更できません")

            # この予約自身の占有時間。Phase B以降に作成された予約は
            # reservation.duration_minutesを持つが、Phase B以前のlegacy予約は
            # NULLのため、既存の_resolve_reservation_duration()（Service→
            # 店舗デフォルト→90分）にフォールバックする（Phase Bのfallback
            # ルールをそのまま再利用。ここで新しいルールは作らない）。
            effective_duration = reservation.duration_minutes or _resolve_reservation_duration(reservation.service, shop)

            boundary_reason = _validate_reservation_time_window(
                owning_hours, session_date, request.reservation_date, effective_duration
            )
            if boundary_reason is not None:
                raise HTTPException(status_code=400, detail="ご指定の時間は営業時間外のため、その日時には変更できません")

            default_duration = shop.reservation_duration_minutes or 90

            # table_id/staff_idの変更自体は現状のReservationUpdateRequestでは
            # サポートされていない（既に割り当て済みのresourceは固定のまま）。
            # そのため、候補探索(_find_available_table等)は不要で、既に確定
            # している自分自身のresourceについてだけ、新しい時間帯で重複が
            # ないかを確認すればよい。exclude_reservation_id=reservation.idで
            # 自分自身を重複判定から除外する（自分の元の予約と比較して誤って
            # 「重複」と判定しないため）。
            if reservation.staff_id:
                if shop.staff_schedule_enabled:
                    # Reservation Intelligence Phase C: create_reservation経路
                    # (_find_available_staff_for_service経由)と同じシフト
                    # チェックを、Owner編集でも同様に適用する。既存の
                    # _is_staff_scheduled()をそのまま再利用し、判定ロジックを
                    # 重複実装しない。
                    scheduled = await _is_staff_scheduled(
                        db, reservation.staff_id, request.reservation_date, effective_duration
                    )
                    if not scheduled:
                        raise HTTPException(status_code=400, detail="ご指定の時間はスタッフの勤務時間外のため変更できません")
                slot_ok = await _lock_and_verify_staff_slot(
                    db, reservation.staff_id, request.reservation_date, effective_duration, default_duration,
                    exclude_reservation_id=reservation.id,
                )
                if not slot_ok:
                    raise HTTPException(status_code=400, detail="ご指定の時間は既に他の予約が入っているため変更できません")
            elif reservation.table_id:
                slot_ok = await _lock_and_verify_table_slot(
                    db, reservation.table_id, request.reservation_date, effective_duration, default_duration,
                    exclude_reservation_id=reservation.id,
                )
                if not slot_ok:
                    raise HTTPException(status_code=400, detail="ご指定の時間は既に他の予約が入っているため変更できません")

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
        # Phase3G Workstream2: 同一reservation_idへの同時キャンセル呼び出し
        # (二重クリック・リトライ等)が、両方とも「まだcancelledではない」と
        # 読んでしまいクーポン利用数を二重解放することを防ぐため、
        # 予約行自体もFOR UPDATEでロックする。
        result = await db.execute(
            select(Reservation).filter(Reservation.id == reservation_id).with_for_update()
        )
        reservation = result.scalar_one_or_none()
        if not reservation:
            raise HTTPException(status_code=404, detail="予約が見つかりません")

        # 既に取り消し済みの予約への重複呼び出しでは、クーポン利用数を
        # 二重解放しない。
        if reservation.status != ReservationStatus.CANCELLED.value:
            await _release_coupon_usage_if_any(db, reservation)

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
