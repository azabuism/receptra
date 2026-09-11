"""
Shop (店舗) モデル
業者が登録する店舗情報
"""

from datetime import datetime, time
from typing import Optional
import uuid

from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey, Index, Text, Float, Integer, Time, Enum
from sqlalchemy.orm import relationship
from app.database import Base
import enum


class ShopCategory(str, enum.Enum):
    """店舗カテゴリ"""
    RESTAURANT = "restaurant"      # レストラン
    CAFE = "cafe"                  # カフェ
    RAMEN = "ramen"               # ラーメン
    SUSHI = "sushi"               # 寿司
    IZAKAYA = "izakaya"           # 居酒屋
    BAR = "bar"                   # バー
    BEAUTY = "beauty"             # 美容
    SALON = "salon"               # サロン
    CLINIC = "clinic"             # クリニック
    OTHER = "other"               # その他


class Shop(Base):
    """店舗情報"""

    __tablename__ = "shops"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)

    # 基本情報
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    category = Column(String(50), nullable=False)  # ShopCategory
    
    # 住所・位置情報
    address = Column(String(500), nullable=False)
    latitude = Column(Float, nullable=True)  # GPS緯度
    longitude = Column(Float, nullable=True)  # GPS経度
    
    # 連絡先
    phone = Column(String(20), nullable=True)
    email = Column(String(255), nullable=True)
    website = Column(String(500), nullable=True)
    
    # 画像
    thumbnail_url = Column(String(500), nullable=True)
    cover_image_url = Column(String(500), nullable=True)
    
    # 営業情報
    is_active = Column(Boolean, default=True, nullable=False)
    is_featured = Column(Boolean, default=False, nullable=False)  # 特集フラグ
    
    # 統計情報
    total_reservations = Column(Integer, default=0)
    total_reviews = Column(Integer, default=0)
    average_rating = Column(Float, default=0.0)  # 平均評価
    
    # タイムスタンプ
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # リレーション
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    shop_hours = relationship("ShopHours", back_populates="shop", cascade="all, delete-orphan")
    reservations = relationship("Reservation", back_populates="shop", cascade="all, delete-orphan")
    reviews = relationship("Review", back_populates="shop", cascade="all, delete-orphan")
    promotions = relationship("Promotion", back_populates="shop", cascade="all, delete-orphan")
    analytics = relationship("ShopAnalytics", back_populates="shop", cascade="all, delete-orphan")

    # インデックス
    __table_args__ = (
        Index("ix_shops_tenant", "tenant_id"),
        Index("ix_shops_category", "category"),
        Index("ix_shops_is_active", "is_active"),
    )

    def __repr__(self):
        return f"<Shop(id={self.id}, name={self.name}, tenant_id={self.tenant_id})>"


class ShopHours(Base):
    """営業時間"""

    __tablename__ = "shop_hours"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    shop_id = Column(String(36), ForeignKey("shops.id"), nullable=False, index=True)

    # 曜日 (0=Monday, 6=Sunday)
    day_of_week = Column(Integer, nullable=False)  # 0-6
    
    # 営業時間
    opening_time = Column(Time, nullable=False)
    closing_time = Column(Time, nullable=False)
    
    # 定休日フラグ
    is_closed = Column(Boolean, default=False, nullable=False)
    
    # 最後の注文受付時間
    last_order_time = Column(Time, nullable=True)

    # タイムスタンプ
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # リレーション
    shop = relationship("Shop", back_populates="shop_hours")

    # インデックス
    __table_args__ = (
        Index("ix_shop_hours_shop_day", "shop_id", "day_of_week", unique=True),
    )

    def __repr__(self):
        return f"<ShopHours(shop_id={self.shop_id}, day={self.day_of_week})>"
