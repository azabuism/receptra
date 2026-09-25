"""
Phase N1: 統一Owner Notification基盤

Web予約 / AI音声予約 / AIチャット予約 / Human Handoff（折り返し依頼）など、
複数の経路から発生する「オーナーが把握すべきイベント」を、単一の内部イベントログへ
集約するための最小限のデータモデル。

設計方針（重要・必ず守ること）:
- これは「通知の配信(Delivery)」ではなく「通知イベント(Event)」の記録である。
  LINE/Email/SMS等への実配信は将来フェーズ(N2以降)の責務であり、本モデルは
  一切の外部送信を行わない・依存しない（追加コストゼロ）。
- shop_id をこのモデルの主たる分離キーとする。CallbackRequest/OutboundCallJobと
  同じ方針で、tenant_idを直接持たず、常にShop.tenant_id経由で参照する
  （tenant分離はJOIN/権限チェック側で保証する）。
- PIIの複製を最小限にする。customer_name（既にBooking Board等で表示されている
  水準の情報）のみ許容し、customer_phone/customer_email/inquiry_textの生テキスト・
  CustomerMemoryの内容・医療関連情報は一切コピーしない。詳細が必要な場合は
  related_entity_id経由でオーナー認証済みの既存detail API
  （GET /api/v1/reservations/{id}、GET .../callback-requests/{id}）を参照する。
- event_type は「オーナーがどう対応するイベントか」を表し、reason/category
  （なぜ発生したか）とは意図的に分離する。event_typeを増やしすぎない
  （初期はRESERVATION_CREATED / OWNER_ACTION_REQUIREDの2種類のみ）。
- 重複防止は related_entity_type + related_entity_id + event_type の組で
  決定論的に行う（DB一意インデックスを最終防衛線とする。
  Reservation.idempotency_key / CallbackRequest.idempotency_keyと同じ設計思想）。
- Notification Event（本モデル）と予約経路の分析（booking_channel等）は別物。
  reservation_sourceを流用しない、booking_channelを本フェーズで追加しない。
"""

import uuid
import enum
from datetime import datetime

from sqlalchemy import Column, String, DateTime, ForeignKey, Index, Text

from app.database import Base


class OwnerNotificationEventType(str, enum.Enum):
    """「オーナーがどう対応するイベントか」を表す種別。増やしすぎない。"""
    RESERVATION_CREATED = "RESERVATION_CREATED"
    OWNER_ACTION_REQUIRED = "OWNER_ACTION_REQUIRED"


class OwnerNotificationPriority(str, enum.Enum):
    """将来のLINE配信(N2以降)で「ACTION_REQUIRED中心に送る」等の使い分けを
    可能にするための最小限の優先度。配信自体は本フェーズで行わない。
    """
    ACTION_REQUIRED = "ACTION_REQUIRED"
    NORMAL = "NORMAL"


class OwnerNotificationRelatedEntityType(str, enum.Enum):
    """related_entity_id が何を指すかを表す。将来イベント種別が増えても
    ここに追加していく（event_typeを増やす代わりに、こちらとreason側で表現する）。
    """
    RESERVATION = "reservation"
    CALLBACK_REQUEST = "callback_request"


class OwnerNotificationEvent(Base):
    """オーナーが把握すべきイベントの統一ログ（配信は行わない）。"""

    __tablename__ = "owner_notification_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    shop_id = Column(String(36), ForeignKey("shops.id"), nullable=False, index=True)

    event_type = Column(String(30), nullable=False, index=True)

    priority = Column(String(20), nullable=False, default=OwnerNotificationPriority.NORMAL.value)

    # オーナー向けUIにそのまま表示する日本語の見出し・本文。
    # messageは常にテンプレートから生成し、inquiry_textの生テキストや
    # CustomerMemory・医療関連情報を絶対にコピーしない。
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)

    # 何に関するイベントか（重複防止の決定論的キーの一部でもある）。
    related_entity_type = Column(String(30), nullable=False)
    related_entity_id = Column(String(36), nullable=False)

    # Booking Board等、既存UIで既に表示されている水準の情報のみ。
    # phone/emailは絶対にここへ複製しない。
    customer_name = Column(String(255), nullable=True)

    read_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (
        # 決定論的な重複防止: 同一のrelated_entity_type + related_entity_id +
        # event_typeについて、通知イベントは常に1件だけ。
        Index(
            "ux_owner_notification_events_entity_type",
            "related_entity_type", "related_entity_id", "event_type",
            unique=True,
        ),
        Index("ix_owner_notification_events_shop_created", "shop_id", "created_at"),
    )

    def __repr__(self):
        return (
            f"<OwnerNotificationEvent(id={self.id}, shop_id={self.shop_id}, "
            f"event_type={self.event_type}, read={self.read_at is not None})>"
        )
