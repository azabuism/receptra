"""
Review (レビュー) モデル
顧客が店舗にレビューを投稿
"""

from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey, Index, Text, Integer, Float
from sqlalchemy.orm import relationship
from app.database import Base


class Review(Base):
    """レビュー・口コミ"""

    __tablename__ = "reviews"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    shop_id = Column(String(36), ForeignKey("shops.id"), nullable=False, index=True)
    customer_id = Column(String(36), ForeignKey("customers.id"), nullable=False, index=True)
    reservation_id = Column(String(36), ForeignKey("reservations.id"), nullable=True)

    # レビュー内容
    title = Column(String(255), nullable=True)  # レビュータイトル
    comment = Column(Text, nullable=True)  # レビューコメント
    
    # 評価
    overall_rating = Column(Float, nullable=False)  # 総合評価 (1-5)
    food_rating = Column(Float, nullable=True)  # 食べ物の評価
    service_rating = Column(Float, nullable=True)  # サービスの評価
    atmosphere_rating = Column(Float, nullable=True)  # 雰囲気の評価
    value_rating = Column(Float, nullable=True)  # コスパの評価
    
    # メディア
    image_urls = Column(Text, nullable=True)  # JSON array of image URLs
    
    # ステータス
    is_verified = Column(Boolean, default=False, nullable=False)  # 実際に来店したか確認
    is_recommended = Column(Boolean, default=False, nullable=False)  # おすすめフラグ
    
    # 業者の返信
    shop_response = Column(Text, nullable=True)
    shop_response_at = Column(DateTime, nullable=True)
    
    # 統計
    helpful_count = Column(Integer, default=0)  # 役に立つボタンのカウント
    
    # タイムスタンプ
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # リレーション
    shop = relationship("Shop", back_populates="reviews")
    customer = relationship("Customer", back_populates="reviews")
    reservation = relationship("Reservation", foreign_keys=[reservation_id])

    # インデックス
    __table_args__ = (
        Index("ix_reviews_shop", "shop_id"),
        Index("ix_reviews_customer", "customer_id"),
        Index("ix_reviews_overall_rating", "overall_rating"),
        Index("ix_reviews_is_verified", "is_verified"),
    )

    def __repr__(self):
        return f"<Review(id={self.id}, shop_id={self.shop_id}, rating={self.overall_rating})>"
