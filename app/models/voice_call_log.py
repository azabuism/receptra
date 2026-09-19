"""
電話AI受付 通話ログ

電話番号ごとに過去の通話内容（要約）を蓄積し、同じお客様から再度電話が
あった際に「いつもありがとうございます」のような文脈のある応対を
AIができるようにするための記録。

tenant_id は将来の複数店舗対応（着信番号→店舗の振り分け）を見据えて
nullable にしている。現状は着信番号から店舗を特定する仕組みがまだ無いため、
実際の電話(Vonage Webhook)経由の通話は tenant_id=None（店舗をまたがない
共有プール）として記録し、ブラウザのテストチャット（オーナー認証あり）
経由の通話は tenant_id を設定して店舗ごとに履歴を分離する。
"""

import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, ForeignKey, Index, Text
from app.database import Base


class VoiceCallLog(Base):
    """電話AI受付の通話履歴（要約ログ）"""

    __tablename__ = "voice_call_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=True, index=True)

    phone_number = Column(String(20), nullable=False, index=True)
    category = Column(String(50), nullable=True)  # reservation, business_call, unclear
    urgency = Column(String(20), nullable=True)  # high, medium, low
    summary = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_voice_call_logs_tenant_phone", "tenant_id", "phone_number"),
    )
