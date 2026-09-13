"""
Staff (スタッフ/講師) モデル
美容院のスタイリスト、スクールの講師など
"""

from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import Column, String, DateTime, ForeignKey, Index, Text, Integer
from sqlalchemy.orm import relationship
from app.database import Base


class Staff(Base):
    """スタッフ/講師情報"""

    __tablename__ = "staff"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    shop_id = Column(String(36), ForeignKey("shops.id"), nullable=False, index=True)
    
    # 基本情報
    name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=True)
    phone = Column(String(20), nullable=True)
    
    # プロフィール
    bio = Column(Text, nullable=True)  # 自己紹介
    photo_url = Column(String(500), nullable=True)  # プロフィール写真
    
    # 専門分野・資格（業種別）
    # 美容: スタイリスト、カラーリスト、etc
    # スクール: 担当科目、資格
    specialty = Column(String(255), nullable=True)
    qualifications = Column(Text, nullable=True)  # 資格・経歴
    
    # 勤務情報
    position = Column(String(100), nullable=True)  # 職位（スタイリスト、インストラクター等）
    
    # ステータス
    is_active = Column(String(50), default="active", nullable=False)
    
    # 統計
    total_reservations = Column(Integer, default=0)
    average_rating = Column(Integer, default=0)  # 平均評価（1-5）
    
    # タイムスタンプ
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # リレーション
    shop = relationship("Shop", foreign_keys=[shop_id])
    staff_services = relationship("StaffService", back_populates="staff", cascade="all, delete-orphan")
    reservations = relationship("Reservation", back_populates="staff", cascade="all, delete-orphan")

    # インデックス
    __table_args__ = (
        Index("ix_staff_shop", "shop_id"),
        Index("ix_staff_is_active", "is_active"),
    )

    def __repr__(self):
        return f"<Staff(id={self.id}, shop_id={self.shop_id}, name={self.name})>"


class StaffService(Base):
    """スタッフが提供するサービス マッピング"""

    __tablename__ = "staff_services"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    staff_id = Column(String(36), ForeignKey("staff.id"), nullable=False, index=True)
    service_id = Column(String(36), ForeignKey("services.id"), nullable=False, index=True)
    
    # タイムスタンプ
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # リレーション
    staff = relationship("Staff", back_populates="staff_services")
    service = relationship("Service", back_populates="staff_services")

    # インデックス
    __table_args__ = (
        Index("ix_staff_services_staff_service", "staff_id", "service_id", unique=True),
    )

    def __repr__(self):
        return f"<StaffService(staff_id={self.staff_id}, service_id={self.service_id})>"
