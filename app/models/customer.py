"""
Customer (顧客) モデル
Receptraプラットフォームの顧客
"""

from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey, Index, Text, Integer, Float
from sqlalchemy.orm import relationship
from app.database import Base


class Customer(Base):
    """顧客情報"""

    __tablename__ = "customers"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)

    # 基本情報
    email = Column(String(255), nullable=False)
    phone = Column(String(20), nullable=True)
    display_name = Column(String(255), nullable=False)
    
    # プロフィール
    avatar_url = Column(String(500), nullable=True)
    
    # 住所
    address = Column(String(500), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    
    # 認証
    is_verified = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    
    # 顧客統計
    total_reservations = Column(Integer, default=0)  # 累計予約数
    total_visits = Column(Integer, default=0)  # 実際に来店した回数
    total_spent = Column(Float, default=0.0)  # 累計支払い額
    
    # リピート情報
    last_visit_date = Column(DateTime, nullable=True)  # 最後の来店日
    first_visit_date = Column(DateTime, nullable=True)  # 初来店日
    
    # セグメンテーション
    vip_level = Column(String(50), nullable=True)  # VIP, Premium, Regular等
    customer_lifetime_value = Column(Float, default=0.0)  # CLV
    
    # 環境設定
    newsletter_subscribed = Column(Boolean, default=True, nullable=False)
    sms_subscribed = Column(Boolean, default=False, nullable=False)
    
    # ログイン情報
    last_login_at = Column(DateTime, nullable=True)
    
    # タイムスタンプ
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # リレーション
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    reservations = relationship("Reservation", back_populates="customer", cascade="all, delete-orphan")
    reviews = relationship("Review", back_populates="customer", cascade="all, delete-orphan")

    # インデックス
    __table_args__ = (
        Index("ix_customers_tenant_email", "tenant_id", "email", unique=True),
        Index("ix_customers_vip_level", "vip_level"),
    )

    def __repr__(self):
        return f"<Customer(id={self.id}, email={self.email}, tenant_id={self.tenant_id})>"
