"""
Human Handoff基盤: 折り返し受付（CallbackRequest）

RECEPTRAのRealtime Voice AIが安全に回答・予約できないと判断した場合に、
「店舗へ直接お問い合わせください」とお客様へ丸投げするのではなく、RECEPTRA側で
用件を受け付け、店舗の担当者へ引き継ぐための最小限のデータモデル。

設計方針（重要・必ず守ること）:
- ここに保存する内容は、担当者が折り返し対応するために必要な最小限の情報のみ。
  inquiry_textは生の音声そのものではなく、AIが要約した簡潔な用件テキストを
  想定する（Realtime APIはブラウザ⇔OpenAI直結のため、RECEPTRAサーバーは
  そもそも生の音声を経由しない。app.services.realtime_voice_aiの既存設計と
  同じ制約）。
- shop_id はこのモデルの主たる分離キー。tenant_idを直接持たず、常に
  Shop.tenant_id経由で参照する（他のモデル、例えばOutboundCallJob/
  CustomerMemoryと同じ方針。tenant分離はJOIN/権限チェック側で保証する）。
- idempotency_key は create_reservation / OutboundCallJob と同じ
  "realtime_voice:{shop_id}:{call_id}" 形式を想定し、DBの一意インデックスを
  同一Tool呼び出しの二重受付防止の最終防衛線とする。
- statusは「担当者への通知（Email/電話ジョブのenqueue）が試みられたか」を
  表すだけで、「CallbackRequestという記録自体が保存されたかどうか」とは
  独立している。通知が失敗してもこの行自体を削除・無効化しない
  （通知はベストエフォートの下流処理であり、受付の記録そのものは常に残す）。
"""

import uuid
import enum
from datetime import datetime

from sqlalchemy import Column, String, DateTime, ForeignKey, Index, Text, Integer, Date, Time
from sqlalchemy.orm import relationship

from app.database import Base


class CallbackRequestStatus(str, enum.Enum):
    """担当者への通知が試みられたかどうかの状態（受付記録そのものの成否ではない）。"""
    PENDING = "pending"                        # 受付済み・通知はまだ試みていない/処理中
    NOTIFIED = "notified"                      # 電話ジョブのenqueue・Email通知のいずれかが成功
    NOTIFICATION_FAILED = "notification_failed"  # 通知手段が有効だったが、いずれも失敗


class CallbackRequestReasonCode(str, enum.Enum):
    """request_callback Toolのparameters.reason_codeと対応する（AIが選択する値）。"""
    BUSINESS_HOURS_NOT_CONFIGURED = "business_hours_not_configured"
    RESERVATION_NOT_ENABLED = "reservation_not_enabled"
    AVAILABILITY_JUDGEMENT_REQUIRED = "availability_judgement_required"
    SHOP_KNOWLEDGE_UNAVAILABLE = "shop_knowledge_unavailable"
    STAFF_JUDGEMENT_REQUIRED = "staff_judgement_required"
    OTHER = "other"


class CallbackRequest(Base):
    """AI電話受付からの折り返し受付（Human Handoff）の記録。"""

    __tablename__ = "callback_requests"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    shop_id = Column(String(36), ForeignKey("shops.id"), nullable=False, index=True)

    # この通話を識別するopaqueな値（app.services.realtime_voice_aiが発行する
    # voice_session_idをそのまま保存。それ自体はPIIではないが、将来の相関調査用）。
    voice_session_id = Column(String(100), nullable=True)

    reason_code = Column(String(50), nullable=False, default=CallbackRequestReasonCode.OTHER.value)

    # 折り返し対応に必要な最小限の情報。いずれもnullable
    # （お客様が名乗らなかった場合等でも受付自体は保存できるようにするため。
    # ただしAPI層では customer_name・customer_phone・inquiry_text は
    # 実務上ほぼ必須になる想定）。
    customer_name = Column(String(255), nullable=True)
    customer_phone = Column(String(20), nullable=True)
    inquiry_text = Column(Text, nullable=True)

    desired_date = Column(Date, nullable=True)
    desired_time = Column(Time, nullable=True)
    party_size = Column(Integer, nullable=True)
    # Serviceへの参照はあくまで情報として保持するのみ（ORM relationshipは
    # 定義しない。Service削除時にこの列がNULLになる必要は無く、単なる
    # 参考情報のスナップショットとして扱う）。
    service_id = Column(String(36), ForeignKey("services.id"), nullable=True)

    status = Column(String(20), nullable=False, default=CallbackRequestStatus.PENDING.value, index=True)

    # 通知チャネルごとの結果（"sent" / "failed" / "skipped"（対象Emailが
    # 設定されていない等）。実際にメールを送信する仕組み自体は本フェーズでは
    # まだ実装しない。値の置き場所だけ先に用意する）。
    email_notification_status = Column(String(20), nullable=True)

    # 担当者への電話通知（callback_requested種別のOutboundCallJob）をenqueueした
    # 場合、そのジョブへの参照。enqueue自体を行わなかった/失敗した場合はNULL。
    outbound_call_job_id = Column(String(36), ForeignKey("outbound_call_jobs.id"), nullable=True)

    # "realtime_voice:{shop_id}:{call_id}" 形式。create_reservationと同じ
    # 冪等性パターンで、DBの一意インデックスにより同一Tool呼び出しの
    # 二重受付をDBレベルで最終防衛する。
    idempotency_key = Column(String(200), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ux_callback_requests_idempotency_key", "idempotency_key", unique=True),
        Index("ix_callback_requests_shop_created", "shop_id", "created_at"),
    )

    shop = relationship("Shop", back_populates="callback_requests")

    def __repr__(self):
        return f"<CallbackRequest(id={self.id}, shop_id={self.shop_id}, status={self.status})>"
