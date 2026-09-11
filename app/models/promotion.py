"""
Promotion (プロモーション) & Coupon (クーポン) モデル
業者の営業ツール
"""

from datetime import datetime
from typing import Optional
import uuid
import enum

from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey, Index, Text, Integer, Float, Enum
from sqlalchemy.orm import relationship
from app.database import Base


class PromotionType(str, enum.Enum):
    """プロモーション種別"""
    DISCOUNT = "discount"        # 割引
    COUPON = "coupon"           # クーポン
    CAMPAIGN = "campaign"       # キャンペーン
    SEASONAL = "seasonal"       # 季節限定
    LOYALTY = "loyalty"         # ロイヤルティ
    NEW_CUSTOMER = "new_customer"  # 新規顧客向け


class Promotion(Base):
    """プロモーション・キャンペーン"""

    __tablename__ = "promotions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    shop_id = Column(String(36), ForeignKey("shops.id"), nullable=False, index=True)

    # 基本情報
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    promo_type = Column(String(50), nullable=False)  # PromotionType
    
    # 割引情報
    discount_type = Column(String(50), nullable=True)  # "percentage" or "fixed"
    discount_value = Column(Float, nullable=True)  # 割引率 or 割引額
    max_discount_amount = Column(Float, nullable=True)  # 最大割引額
    
    # 有効期間
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    
    # 条件
    minimum_order_amount = Column(Float, nullable=True)  # 最小注文額
    minimum_party_size = Column(Integer, nullable=True)  # 最小人数
    
    # 使用制限
    usage_limit = Column(Integer, nullable=True)  # 使用回数制限
    usage_count = Column(Integer, default=0)  # 実際の使用回数
    max_per_customer = Column(Integer, nullable=True)  # 顧客あたりの最大使用回数
    
    # 画像・バナー
    image_url = Column(String(500), nullable=True)
    banner_url = Column(String(500), nullable=True)
    
    # 状態
    is_active = Column(Boolean, default=True, nullable=False)
    is_featured = Column(Boolean, default=False, nullable=False)  # トップに表示
    
    # 統計
    impressions = Column(Integer, default=0)  # 表示回数
    clicks = Column(Integer, default=0)  # クリック数
    conversions = Column(Integer, default=0)  # 成約数
    
    # タイムスタンプ
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # リレーション
    shop = relationship("Shop", back_populates="promotions")

    # インデックス
    __table_args__ = (
        Index("ix_promotions_shop", "shop_id"),
        Index("ix_promotions_end_date", "end_date"),
        Index("ix_promotions_is_active", "is_active"),
    )

    def __repr__(self):
        return f"<Promotion(id={self.id}, shop_id={self.shop_id}, title={self.title})>"


class Coupon(Base):
    """クーポン・割引コード"""

    __tablename__ = "coupons"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    shop_id = Column(String(36), ForeignKey("shops.id"), nullable=False, index=True)
    promotion_id = Column(String(36), ForeignKey("promotions.id"), nullable=True)

    # コード情報
    code = Column(String(50), nullable=False, unique=True)  # unique=True creates the index automatically
    description = Column(Text, nullable=True)
    
    # 割引情報
    discount_type = Column(String(50), nullable=False)  # "percentage" or "fixed"
    discount_value = Column(Float, nullable=False)
    
    # 有効期間
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    
    # 使用制限
    usage_limit = Column(Integer, nullable=True)
    usage_count = Column(Integer, default=0)
    max_per_customer = Column(Integer, nullable=True)
    
    # 状態
    is_active = Column(Boolean, default=True, nullable=False)
    
    # 統計
    total_discount_given = Column(Float, default=0.0)  # 総割引額
    
    # タイムスタンプ
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # リレーション
    shop = relationship("Shop", foreign_keys=[shop_id])
    promotion = relationship("Promotion", foreign_keys=[promotion_id])

    # インデックス
    __table_args__ = (
        Index("ix_coupons_shop", "shop_id"),
    )

    def __repr__(self):
        return f"<Coupon(id={self.id}, code={self.code})>"
