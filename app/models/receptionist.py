"""
受付スタッフモデル
"""

import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship

from app.database import Base


class Receptionist(Base):
    """受付スタッフ"""

    __tablename__ = "receptionists"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)
    
    # 基本情報
    name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False)
    phone = Column(String(20), nullable=True)
    
    # ロール・シフト
    role = Column(String(50), default="receptionist", nullable=False)  # receptionist, manager
    shift = Column(String(50), default="all-day", nullable=False)  # morning, afternoon, night, all-day
    
    # ステータス
    is_active = Column(Boolean, default=True, nullable=False)
    
    # タイムスタンプ
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # インデックス（外部キーのインデックスは自動作成されるため、ここでは email のみ）
    __table_args__ = (
        Index("ix_receptionists_email_tenant", "email", "tenant_id"),
    )
    
    # リレーション
    tenant = relationship("Tenant", back_populates="receptionists")
