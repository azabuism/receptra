"""
Reservation (予約) モデル - 拡張版
既存の Reservation に業種別の詳細情報を JSON で保存
"""

from datetime import datetime
from typing import Optional, Dict, Any
import uuid
import enum

from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey, Index, Text, Integer, Enum, JSON
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
    """予約情報 - 業種対応版"""

    __tablename__ = "reservations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    shop_id = Column(String(36), ForeignKey("shops.id"), nullable=False, index=True)
    customer_id = Column(String(36), ForeignKey("customers.id"), nullable=True, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)  # ログイン中のプラットフォームアカウント（マイページの「よく行く店」集計に使用）
    staff_id = Column(String(36), ForeignKey("staff.id"), nullable=True)  # スタッフ指名（オプション）
    table_id = Column(String(36), ForeignKey("shop_tables.id"), nullable=True)  # 割り当てられたテーブル
    service_id = Column(String(36), ForeignKey("services.id"), nullable=True, index=True)  # 予約対象のサービス（美容院・クリニックなど、サービス単位で予約する業種の場合）

    # ゲスト予約情報（会員登録なしで予約する場合に使用）
    guest_name = Column(String(255), nullable=True)
    guest_phone = Column(String(20), nullable=True)
    guest_email = Column(String(255), nullable=True)

    # 予約情報
    reservation_date = Column(DateTime, nullable=False)  # 予約日時
    number_of_people = Column(Integer, nullable=False)  # 人数（飲食店向け）
    status = Column(String(50), default=ReservationStatus.PENDING, nullable=False)  # ステータス

    # 特別リクエスト
    special_requests = Column(Text, nullable=True)  # 備考
    
    # ★★★ 新規追加: 業種別の詳細情報を JSON で保存
    # 例：
    # 飲食店: {"party_size": 4, "seating_preference": "window", "allergies": [...]}
    # 美容: {"service_id": "xxx", "staff_id": "yyy", "duration_minutes": 60, "service_type": "cut"}
    # ホテル: {"room_type": "twin", "checkin_date": "2026-09-15", "checkout_date": "2026-09-17", "meal_plan": "breakfast"}
    # スクール: {"course_id": "xxx", "start_date": "2026-10-01", "duration_weeks": 12, "frequency": "weekly"}
    # 塾: {"subject": "math", "class_format": "individual", "time_slot": "16:00-17:00"}
    reservation_details = Column(JSON, nullable=True)  # 業種別詳細情報
    
    # キャンセル情報
    cancelled_at = Column(DateTime, nullable=True)
    cancellation_reason = Column(String(500), nullable=True)
    
    # 来店情報
    arrived_at = Column(DateTime, nullable=True)  # 実際の来店時刻
    table_number = Column(String(50), nullable=True)  # テーブル番号
    
    # 支払い情報（新規）
    total_price = Column(Integer, nullable=True)  # 合計金額（円）
    payment_method = Column(String(50), nullable=True)  # credit_card, cash, etc
    payment_status = Column(String(50), nullable=True)  # unpaid, paid, refunded
    
    # マーケティング情報
    reservation_source = Column(String(50), nullable=True)  # 検索、広告、クーポン等
    
    # タイムスタンプ
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # リレーション
    shop = relationship("Shop", back_populates="reservations")
    customer = relationship("Customer", back_populates="reservations")
    staff = relationship("Staff", back_populates="reservations", foreign_keys=[staff_id])
    table = relationship("ShopTable")
    service = relationship("Service", foreign_keys=[service_id])

    # インデックス
    __table_args__ = (
        Index("ix_reservations_shop", "shop_id"),
        Index("ix_reservations_customer", "customer_id"),
        Index("ix_reservations_date", "reservation_date"),
        Index("ix_reservations_status", "status"),
        Index("ix_reservations_staff", "staff_id"),
        Index("ix_reservations_user", "user_id"),
    )

    def __repr__(self):
        return f"<Reservation(id={self.id}, shop_id={self.shop_id}, customer_id={self.customer_id})>"
