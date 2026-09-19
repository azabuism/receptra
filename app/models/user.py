"""
ユーザー・テナント モデル
マルチテナント対応の認証モデル
"""

from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey, Index, Text
from sqlalchemy.orm import relationship
from app.database import Base


class Tenant(Base):
    """テナント（顧客企業）"""

    __tablename__ = "tenants"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    slug = Column(String(255), unique=True, nullable=False, index=True)
    email = Column(String(255), nullable=False)

    # メタデータ
    is_active = Column(Boolean, default=True, nullable=False)
    is_trial = Column(Boolean, default=True, nullable=False)
    trial_ends_at = Column(DateTime, nullable=True)

    # Subscription info
    subscription_status = Column(
        String(50),
        default="trial",  # trial, active, past_due, suspended, cancelled
        nullable=False,
    )

    # PAY.jp 課金情報
    payjp_customer_id = Column(String(255), nullable=True)
    payjp_subscription_id = Column(String(255), nullable=True)
    card_brand = Column(String(50), nullable=True)
    card_last4 = Column(String(4), nullable=True)
    setup_fee_paid_at = Column(DateTime, nullable=True)

    # 統計情報
    total_users = Column(String(50), default="0", nullable=False)

    # タイムスタンプ
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # リレーション
    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan")
    receptionists = relationship("Receptionist", back_populates="tenant", cascade="all, delete-orphan")
    visitors = relationship("Visitor", back_populates="tenant", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Tenant(id={self.id}, name={self.name}, slug={self.slug})>"


class User(Base):
    """ユーザー"""

    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)

    # 基本情報
    email = Column(String(255), nullable=False)
    display_name = Column(String(255), nullable=False)
    password_hash = Column(Text, nullable=False)

    # ステータス
    is_active = Column(Boolean, default=True, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)

    # プロフィール
    avatar_url = Column(String(500), nullable=True)
    bio = Column(Text, nullable=True)

    # ログイン関連
    last_login_at = Column(DateTime, nullable=True)
    last_login_ip = Column(String(50), nullable=True)

    # タイムスタンプ
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # リレーション
    tenant = relationship("Tenant", back_populates="users")

    # インデックス
    __table_args__ = (
        Index("ix_users_tenant_email", "tenant_id", "email", unique=True),
    )

    def __repr__(self):
        return f"<User(id={self.id}, email={self.email}, tenant_id={self.tenant_id})>"
