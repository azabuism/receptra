"""
Phase N2: LINE Owner通知（配信層）

Phase N1 (app/models/owner_notification.py) の Event生成ロジックには一切
変更を加えない。N1 = Event生成、N2 = Delivery(配信) を完全に分離するという
仕様上の絶対要件を、コードの構造そのもので担保する:
- ここで定義するテーブルは「配信」「連携」「設定」のみを表し、
  OwnerNotificationEventそのものは一切変更しない（読み取り専用で参照するのみ）。
- 実際の配信トリガーは app/services/line_notification_worker.py の
  バックグラウンドポーリングであり、app/routers/reservations.py や
  app/routers/realtime_voice.py、app/services/owner_notifications.py には
  一切手を入れない（呼び出しフックの追加すら行わない）。

設計方針:
- OwnerLineConnection は tenant_id単位（Shop.tenant_id / User.tenant_idの
  関係上、1テナントが複数Shopを持てるため、LINE連携はテナント単位で共有し、
  通知のON/OFFのみShop単位で個別に制御できるようにする）。
- LINE userId は絶対に公開APIやオーナー向けUIへ露出しない
  （「連携済み」の有無のみを表示する）。DBにのみ保持する。
- LineLinkNonce は使い捨て・有効期限付き・リプレイ防止のための最小限のテーブル。
- LineNotificationDelivery は OwnerNotificationEvent 1件につき最大1行のみ
  存在する（notification_event_id にDB一意制約を張り、決定論的な重複配信防止の
  最終防衛線とする。プロセス再起動・再デプロイ・リトライを跨いで有効）。
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String, Text

from app.database import Base


class LineNotificationDeliveryStatus(str, enum.Enum):
    """LINE配信の状態。OwnerNotificationEvent.read_atとは完全に独立している
    （LINEへ送った/届いた/失敗した、という配信状態と「オーナーが既読にしたか」は
    別物であり、本ステータスがどう変化してもread_atを自動更新することは絶対にない）。
    """
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class OwnerLineConnection(Base):
    """テナント単位のLINE連携状態。1テナントにつき最大1行
    （複数店舗を持つオーナーでも連携は1つで共有し、通知ON/OFFは
    ShopLineNotificationSetting側で店舗ごとに制御する）。"""

    __tablename__ = "owner_line_connections"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=False, unique=True, index=True)

    # LINEのuserId。絶対に公開APIやUIへ露出しない（DB内部でのみ使用する）。
    line_user_id = Column(String(64), nullable=False, index=True)

    connected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    # 解除済み（unfollowイベント、またはオーナー自身の「連携解除」操作）の場合に設定。
    # 行自体は削除しない（履歴として残し、再連携時はこの行を更新する）。
    revoked_at = Column(DateTime, nullable=True)

    # テスト通知のレート制限用（インメモリではなくDBで管理し、再起動・複数インスタンス
    # を跨いでも正しく機能するようにする）。
    last_test_notification_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<OwnerLineConnection(tenant_id={self.tenant_id}, revoked={self.revoked_at is not None})>"


class ShopLineNotificationSetting(Base):
    """店舗単位のLINE通知ON/OFF設定。既存のreservation_notification_phone/email
    (Shopモデル)とは別物であり、それらを流用・変更しない。"""

    __tablename__ = "shop_line_notification_settings"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    shop_id = Column(String(36), ForeignKey("shops.id"), nullable=False, unique=True, index=True)

    # マスタースイッチ。Falseならこの店舗のイベントは一切LINE配信しない。
    enabled = Column(Boolean, nullable=False, default=True)
    # OWNER_ACTION_REQUIRED（折り返し依頼など）。仕様上デフォルトON。
    action_required_enabled = Column(Boolean, nullable=False, default=True)
    # RESERVATION_CREATED。予約成立通知はLINEのメッセージ量が多くなりやすく、
    # 現時点のLINE料金体系（Audit Report参照）を踏まえ、デフォルトOFF
    # （オーナーが希望する場合のみON）とする。
    reservation_enabled = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<ShopLineNotificationSetting(shop_id={self.shop_id}, enabled={self.enabled})>"


class LineLinkNonce(Base):
    """LINEアカウント連携(accountLink)フローの使い捨てnonce。
    有効期限付き・単回使用・tenant_idに紐付ける（なりすまし・リプレイ防止）。"""

    __tablename__ = "line_link_nonces"

    nonce = Column(String(255), primary_key=True)
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    # 使用済みになった時刻。一度セットされたら二度と再利用させない（リプレイ防止）。
    consumed_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<LineLinkNonce(tenant_id={self.tenant_id}, consumed={self.consumed_at is not None})>"


class LineNotificationDelivery(Base):
    """OwnerNotificationEvent 1件ごとのLINE配信試行記録。
    OwnerNotificationEvent本体（read_at含む）は一切変更しない。

    決定論的な重複防止: notification_event_id に一意制約。
    プロセス再起動・再デプロイ・ワーカーのリトライを跨いでも、
    同一イベントに対するLINE配信行はこのテーブルに常に1行だけ存在する。
    """

    __tablename__ = "line_notification_deliveries"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    notification_event_id = Column(
        String(36), ForeignKey("owner_notification_events.id"), nullable=False, unique=True, index=True
    )

    status = Column(String(20), nullable=False, default=LineNotificationDeliveryStatus.PENDING.value)
    # 失敗理由の分類のみ（生の例外メッセージやPIIは含めない。
    # app.services.outbound_call_worker.OutboundCallJob.last_error_categoryと同方針）。
    error_category = Column(String(100), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_line_notification_deliveries_status", "status"),
    )

    def __repr__(self):
        return f"<LineNotificationDelivery(event_id={self.notification_event_id}, status={self.status})>"
