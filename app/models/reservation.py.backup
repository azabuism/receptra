"""
Reservation (予約) モデル
顧客が店舗の予約をする
"""

from datetime import datetime
from typing import Optional
import uuid
import enum

from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey, Index, Text, Integer, Enum
from sqlalchemy.orm import relationship
from app.database import Base


class ReservationStatus(str, enum.Enum):
    """予約ステータス"""
    PENDING = "pending"          # 保留中（確認待ち）
    CONFIRMED = "confirmed"      # 確定
    CANCELLED = "cancelled"      # キャンセル
    NO_SHOW = "no_show"         # ノーショー
    COMPLETED = "completed"      # 完了（来店済み）


class Reservation(Base):
    """予約情報"""

    __tablename__ = "reservations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    shop_id = Column(String(36), ForeignKey("shops.id"), nullable=False, index=True)
    customer_id = Column(String(36), ForeignKey("customers.id"), nullable=False, index=True)

    # 予約情報
    reservation_date = Column(DateTime, nullable=False)  # 予約日時
    number_of_people = Column(Integer, nullable=False)  # 人数
    status = Column(String(50), default=ReservationStatus.PENDING, nullable=False)  # ステータス
    
    # 特別リクエスト
    special_requests = Column(Text, nullable=True)  # 備考
    
    # キャンセル情報
    cancelled_at = Column(DateTime, nullable=True)
    cancellation_reason = Column(String(500), nullable=True)
    
    # 来店情報
    arrived_at = Column(DateTime, nullable=True)  # 実際の来店時刻
    table_number = Column(String(50), nullable=True)  # テーブル番号
    
    # マーケティング情報
    reservation_source = Column(String(50), nullable=True)  # 検索、広告、クーポン等
    
    # タイムスタンプ
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # リレーション
    shop = relationship("Shop", back_populates="reservations")
    customer = relationship("Customer", back_populates="reservations")

    # インデックス
    __table_args__ = (
        Index("ix_reservations_shop", "shop_id"),
        Index("ix_reservations_customer", "customer_id"),
        Index("ix_reservations_date", "reservation_date"),
        Index("ix_reservations_status", "status"),
    )

    def __repr__(self):
        return f"<Reservation(id={self.id}, shop_id={self.shop_id}, customer_id={self.customer_id})>"
