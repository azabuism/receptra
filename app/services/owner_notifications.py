"""
Phase N1: 統一Owner Notification基盤 — イベント生成（配信は行わない）

app.services.outbound_dispatch と全く同じ設計方針
（非同期・失敗分離・専用DBセッション・DB一意制約を最終防衛線とする冪等性）を
踏襲する。呼び出し元（create_reservation() / request_callback Tool）は、
それぞれの本体の書き込みが既にコミット済みになった直後にのみこれらの関数を
呼び出す。ここでの例外はどのような理由であれ一切呼び出し元へ伝播しない。

import方法の注意: app.services.outbound_dispatch と同じ理由により、
`from app.database import AsyncSessionLocal` ではなく
`import app.database as db_module` した上で `db_module.AsyncSessionLocal` として
都度参照する（モジュール読み込み時点のNoneを固定でコピーしてしまう事故を防ぐ）。
"""

import logging
from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy.exc import IntegrityError

import app.database as db_module
from app.models.owner_notification import (
    OwnerNotificationEvent,
    OwnerNotificationEventType,
    OwnerNotificationPriority,
    OwnerNotificationRelatedEntityType,
)
from app.models.callback_request import CallbackRequestReasonCode
from app.models.pre_order import PreOrderConfirmationStatus

logger = logging.getLogger("receptra.owner_notifications")


# reason_code -> オーナー向けの一般的な日本語文言。CallbackRequest.inquiry_textの
# 生テキストやCustomerMemory・医療関連情報は絶対にここへ含めない
# （「お客様から確認が必要なお問い合わせがあります」程度に留める、という
# Phase N1仕様のプライバシー方針に従う）。
_CALLBACK_REASON_MESSAGES_JA = {
    CallbackRequestReasonCode.BUSINESS_HOURS_NOT_CONFIGURED.value: "営業時間の確認が必要なお問い合わせがあります",
    CallbackRequestReasonCode.RESERVATION_NOT_ENABLED.value: "予約についてのお問い合わせがあります",
    CallbackRequestReasonCode.AVAILABILITY_JUDGEMENT_REQUIRED.value: "空き状況の確認が必要なお問い合わせがあります",
    CallbackRequestReasonCode.SHOP_KNOWLEDGE_UNAVAILABLE.value: "お店の情報について確認が必要なお問い合わせがあります",
    CallbackRequestReasonCode.STAFF_JUDGEMENT_REQUIRED.value: "スタッフの確認が必要なお問い合わせがあります",
    CallbackRequestReasonCode.OTHER.value: "確認が必要なお問い合わせがあります",
}


def _format_reservation_datetime(reservation_date: datetime) -> str:
    try:
        return reservation_date.strftime("%m月%d日 %H:%M")
    except Exception:
        return ""


async def notify_reservation_created(
    shop_id: str,
    reservation_id: str,
    guest_name: Optional[str],
    reservation_date: datetime,
    number_of_people: Optional[int],
) -> None:
    """新規予約成立の直後にのみ呼び出す。Web予約/AI音声予約/AIチャット予約は
    すべてcreate_reservation()という単一のSource of Truthを経由するため、
    呼び出し箇所はcreate_reservation()内の1箇所のみでよい（経路ごとに
    個別に呼び出す必要はなく、二重通知の懸念も生じない）。

    現時点でRECEPTRAには「オーナー自身が管理画面から予約を作成する」経路が
    存在しない（Phase N1監査で確認済み）。そのため、create_reservation()を
    通るすべての予約は顧客/AI起点であり、本関数を無条件に呼び出してよい
    （オーナー自身の操作を除外する判定は不要）。将来オーナー手動予約機能が
    追加された場合は、その時点でこの前提を再監査し、必要な除外ロジックを
    追加すること。
    """
    try:
        message_parts = [_format_reservation_datetime(reservation_date)]
        if number_of_people:
            message_parts.append(f"{number_of_people}名")
        if guest_name:
            message_parts.append(f"{guest_name}様")
        message = "　".join(p for p in message_parts if p) or "新しい予約が入りました"

        async with db_module.AsyncSessionLocal() as db:
            event = OwnerNotificationEvent(
                shop_id=shop_id,
                event_type=OwnerNotificationEventType.RESERVATION_CREATED.value,
                priority=OwnerNotificationPriority.NORMAL.value,
                title="新しい予約が入りました",
                message=message,
                related_entity_type=OwnerNotificationRelatedEntityType.RESERVATION.value,
                related_entity_id=reservation_id,
                customer_name=guest_name,
            )
            db.add(event)
            try:
                await db.commit()
            except IntegrityError:
                # 同一予約に対する重複生成（DB一意インデックスが最終防衛線）。
                # 想定内の競合として握りつぶす。
                await db.rollback()
    except Exception:
        # ここでの失敗は、呼び出し元の予約作成の成功に一切影響してはならない。
        # 専用セッションのため、呼び出し元のセッション状態には一切波及しない。
        logger.exception(
            "OwnerNotificationEvent(RESERVATION_CREATED)の生成に失敗しました"
            "（予約作成自体は成功済みのため処理を継続します） "
            "shop_id=%s reservation_id=%s",
            shop_id, reservation_id,
        )


async def notify_callback_requested(
    shop_id: str,
    callback_request_id: str,
    reason_code: str,
    customer_name: Optional[str],
) -> None:
    """CallbackRequest（Human Handoff）の保存が成功した直後にのみ呼び出す。
    inquiry_textの生テキスト・CustomerMemory・医療関連情報は一切含めず、
    reason_codeに応じた一般的な日本語文言のみを使う。
    """
    try:
        message = _CALLBACK_REASON_MESSAGES_JA.get(
            reason_code, _CALLBACK_REASON_MESSAGES_JA[CallbackRequestReasonCode.OTHER.value]
        )
        if customer_name:
            message = f"{customer_name}様より、{message}"

        async with db_module.AsyncSessionLocal() as db:
            event = OwnerNotificationEvent(
                shop_id=shop_id,
                event_type=OwnerNotificationEventType.OWNER_ACTION_REQUIRED.value,
                priority=OwnerNotificationPriority.ACTION_REQUIRED.value,
                title="対応が必要です",
                message=message,
                related_entity_type=OwnerNotificationRelatedEntityType.CALLBACK_REQUEST.value,
                related_entity_id=callback_request_id,
                customer_name=customer_name,
            )
            db.add(event)
            try:
                await db.commit()
            except IntegrityError:
                # 同一CallbackRequestに対する重複生成（DB一意インデックスが最終防衛線）。
                await db.rollback()
    except Exception:
        # ここでの失敗は、呼び出し元のCallbackRequest保存の成功に一切影響してはならない。
        logger.exception(
            "OwnerNotificationEvent(OWNER_ACTION_REQUIRED)の生成に失敗しました"
            "（折り返し受付自体は成功済みのため処理を継続します） "
            "shop_id=%s callback_request_id=%s",
            shop_id, callback_request_id,
        )


def _format_pickup_datetime(pickup_at: datetime) -> str:
    try:
        return pickup_at.strftime("%m月%d日 %H:%M")
    except Exception:
        return ""


def _summarize_pre_order_items(items: List[Tuple[str, int]], max_length: int = 60) -> str:
    """PII最小化・通知本文の肥大化防止のため、商品概要を短く要約する
    （Section24: 商品概要は含めてよいが、電話番号・email・住所は含めない）。
    商品名は既にBooking Board等と同水準の非機微情報のため、要約に含める。"""
    parts = [f"{name}×{qty}" for name, qty in items]
    summary = "、".join(parts)
    if len(summary) > max_length:
        summary = summary[: max_length - 1] + "…"
    return summary or "商品明細あり"


async def notify_pre_order_created(
    shop_id: str,
    pre_order_id: str,
    customer_name: Optional[str],
    pickup_at: datetime,
    items: List[Tuple[str, int]],
    confirmation_status: str,
) -> None:
    """PHASE O3: PreOrder作成成功の直後にのみ呼び出す。notify_reservation_created()
    と全く同じ設計（専用DBセッション・失敗分離・DB一意インデックスによる冪等性）。

    priorityは、この時点でOWNER_CONFIRMATION_REQUIREDである場合のみ
    ACTION_REQUIRED（Section23）。O3時点ではdetermine_pre_order_confirmation()が
    常にOWNER_CONFIRMATION_REQUIREDを返すため、実質的に常にACTION_REQUIREDに
    なるが、将来O4以降で自動確定（CONFIRMED）が実装された場合に備え、
    confirmation_statusを見て分岐する形にしておく。

    ★PII最小化（Section24）: 通知payloadには受取日時・商品概要・要確認フラグの
    みを含め、customer_phone/customer_email/customer_noteの生テキストは
    一切含めない（customer_nameのみ、RESERVATION_CREATEDと同水準で許容）。
    """
    try:
        is_action_required = confirmation_status == PreOrderConfirmationStatus.OWNER_CONFIRMATION_REQUIRED.value

        message_parts = [_format_pickup_datetime(pickup_at), _summarize_pre_order_items(items)]
        if customer_name:
            message_parts.append(f"{customer_name}様")
        if is_action_required:
            message_parts.append("店舗確認が必要です")
        message = "　".join(p for p in message_parts if p) or "新しい事前注文が入りました"

        async with db_module.AsyncSessionLocal() as db:
            event = OwnerNotificationEvent(
                shop_id=shop_id,
                event_type=OwnerNotificationEventType.PRE_ORDER_CREATED.value,
                priority=(
                    OwnerNotificationPriority.ACTION_REQUIRED.value
                    if is_action_required else OwnerNotificationPriority.NORMAL.value
                ),
                title="新しい事前注文が入りました",
                message=message,
                related_entity_type=OwnerNotificationRelatedEntityType.PRE_ORDER.value,
                related_entity_id=pre_order_id,
                customer_name=customer_name,
            )
            db.add(event)
            try:
                await db.commit()
            except IntegrityError:
                # 同一PreOrderに対する重複生成（DB一意インデックスが最終防衛線。
                # related_entity_type+related_entity_id+event_typeの組で一意）。
                await db.rollback()
    except Exception:
        # ここでの失敗は、呼び出し元のPreOrder作成の成功に一切影響してはならない
        # （Section25: notification failureはlogのみ、PreOrderはrollbackしない）。
        logger.exception(
            "OwnerNotificationEvent(PRE_ORDER_CREATED)の生成に失敗しました"
            "（PreOrder作成自体は成功済みのため処理を継続します） "
            "shop_id=%s pre_order_id=%s",
            shop_id, pre_order_id,
        )
