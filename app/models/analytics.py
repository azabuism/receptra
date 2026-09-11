"""
ShopAnalytics (分析データ) モデル
営業ツール用の分析・統計データ
"""

from datetime import datetime, date
from typing import Optional
import uuid

from sqlalchemy import Column, String, DateTime, Date, Boolean, ForeignKey, Index, Text, Integer, Float
from sqlalchemy.orm import relationship
from app.database import Base


class ShopAnalytics(Base):
    """店舗の日次分析データ"""

    __tablename__ = "shop_analytics"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    shop_id = Column(String(36), ForeignKey("shops.id"), nullable=False, index=True)

    # 日付情報
    analytics_date = Column(Date, nullable=False)  # 分析対象日
    
    # ========== 予約データ ==========
    total_reservations = Column(Integer, default=0)  # 予約数
    confirmed_reservations = Column(Integer, default=0)  # 確定予約数
    cancelled_reservations = Column(Integer, default=0)  # キャンセル数
    no_show_reservations = Column(Integer, default=0)  # ノーショー数
    
    # ========== 来店データ ==========
    actual_visits = Column(Integer, default=0)  # 実際の来店数
    average_party_size = Column(Float, default=0.0)  # 平均来店人数
    total_guests = Column(Integer, default=0)  # 総来店人数
    
    # ========== 売上データ ==========
    total_revenue = Column(Float, default=0.0)  # 売上高
    average_spend_per_guest = Column(Float, default=0.0)  # 1人あたりの平均単価
    average_spend_per_party = Column(Float, default=0.0)  # グループ単価
    
    # ========== クーポン・割引 ==========
    total_discount_given = Column(Float, default=0.0)  # 総割引額
    coupon_usage_count = Column(Integer, default=0)  # クーポン使用回数
    promotion_impact = Column(Float, default=0.0)  # プロモーション経由の売上
    
    # ========== ユーザー動向 ==========
    new_customers = Column(Integer, default=0)  # 新規顧客数
    repeat_customers = Column(Integer, default=0)  # リピート顧客数
    vip_customers = Column(Integer, default=0)  # VIP顧客数
    
    # ========== 検索・発見 ==========
    search_impressions = Column(Integer, default=0)  # 検索結果での表示回数
    search_clicks = Column(Integer, default=0)  # 検索からのクリック数
    direct_visits = Column(Integer, default=0)  # 直接アクセス
    
    # ========== レビュー・評価 ==========
    new_reviews = Column(Integer, default=0)  # 新規レビュー数
    average_review_rating = Column(Float, default=0.0)  # 平均レビュー評価
    
    # ========== トラフィック分析 ==========
    page_views = Column(Integer, default=0)  # ページビュー
    unique_visitors = Column(Integer, default=0)  # ユニークユーザー
    bounce_rate = Column(Float, default=0.0)  # 直帰率
    
    # ========== 時間帯分析 ==========
    peak_hour = Column(String(50), nullable=True)  # ピークの時間帯
    peak_hour_visits = Column(Integer, default=0)  # ピーク時間の来店数
    
    # ========== 予測・推奨 ==========
    predicted_revenue = Column(Float, nullable=True)  # 予測売上
    recommended_promotion = Column(Text, nullable=True)  # 推奨プロモーション（JSON）
    
    # ========== 競合分析 ==========
    rank_in_category = Column(Integer, nullable=True)  # カテゴリ内のランク
    category_average_rating = Column(Float, nullable=True)  # カテゴリ平均評価
    
    # タイムスタンプ
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # リレーション
    shop = relationship("Shop", back_populates="analytics")

    # インデックス
    __table_args__ = (
        Index("ix_shop_analytics_shop_date", "shop_id", "analytics_date", unique=True),
        Index("ix_shop_analytics_date", "analytics_date"),
    )

    def __repr__(self):
        return f"<ShopAnalytics(shop_id={self.shop_id}, date={self.analytics_date})>"


class MonthlyAnalytics(Base):
    """店舗の月次分析データ（集計）"""

    __tablename__ = "monthly_analytics"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    shop_id = Column(String(36), ForeignKey("shops.id"), nullable=False, index=True)

    # 年月
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    
    # 集計データ
    total_revenue = Column(Float, default=0.0)
    total_visits = Column(Integer, default=0)
    total_reservations = Column(Integer, default=0)
    average_rating = Column(Float, default=0.0)
    new_customers = Column(Integer, default=0)
    repeat_customers = Column(Integer, default=0)
    
    # 前月比
    revenue_growth_rate = Column(Float, nullable=True)  # 前月比売上成長率
    visit_growth_rate = Column(Float, nullable=True)  # 前月比来店成長率
    
    # タイムスタンプ
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # インデックス
    __table_args__ = (
        Index("ix_monthly_analytics_shop_month", "shop_id", "year", "month", unique=True),
    )

    def __repr__(self):
        return f"<MonthlyAnalytics(shop_id={self.shop_id}, {self.year}/{self.month})>"
