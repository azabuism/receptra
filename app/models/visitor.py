"""
来訪者モデル
"""

import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship

from app.database import Base


class Visitor(Base):
    """来訪者"""

    __tablename__ = "visitors"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)
    
    # 基本情報
    name = Column(String(255), nullable=False)
    phone = Column(String(20), nullable=True)
    email = Column(String(255), nullable=False)
    company = Column(String(255), nullable=True)
    
    # 訪問情報
    purpose = Column(String(500), nullable=False)
    host_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    
    # ステータス
    status = Column(String(50), default="pending", nullable=False)  # pending, checked_in, checked_out
    
    # チェックイン・チェックアウト
    check_in_at = Column(DateTime, nullable=True)
    check_out_at = Column(DateTime, nullable=True)
    
    # ステータス
    is_active = Column(Boolean, default=True, nullable=False)
    
    # タイムスタンプ
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # インデックス
    __table_args__ = (
        Index("ix_visitors_email_tenant", "email", "tenant_id"),
        Index("ix_visitors_status_tenant", "status", "tenant_id"),
    )
    
    # リレーション
    tenant = relationship("Tenant", back_populates="visitors")
    host = relationship("User")
