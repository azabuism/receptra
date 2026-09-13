"""
Service (サービス) モデル
業種別のサービス定義（美容のカット、ホテルの部屋タイプなど）
"""

from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import Column, String, DateTime, ForeignKey, Index, Text, Float, Integer
from sqlalchemy.orm import relationship
from app.database import Base


class Service(Base):
    """サービス情報（業種別）"""

    __tablename__ = "services"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    shop_id = Column(String(36), ForeignKey("shops.id"), nullable=False, index=True)
    
    # サービス基本情報
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    
    # 価格・時間
    base_price = Column(Float, nullable=False)  # 基本料金
    duration_minutes = Column(Integer, nullable=True)  # 施術時間（分）
    
    # 業種別フィールド（JSON で保存する場合もある）
    # 美容: カット、パーマ、カラーなど
    # ホテル: シングル、ツイン、スイートなど
    # スクール: コース名
    service_type = Column(String(100), nullable=True)  # 細分類（オプション）
    
    # ステータス
    is_active = Column(String(50), default="active", nullable=False)
    
    # タイムスタンプ
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # リレーション
    shop = relationship("Shop", foreign_keys=[shop_id])
    staff_services = relationship("StaffService", back_populates="service", cascade="all, delete-orphan")

    # インデックス
    __table_args__ = (
        Index("ix_services_shop", "shop_id"),
        Index("ix_services_is_active", "is_active"),
    )

    def __repr__(self):
        return f"<Service(id={self.id}, shop_id={self.shop_id}, name={self.name})>"
