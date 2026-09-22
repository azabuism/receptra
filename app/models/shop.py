"""
Shop (店舗) モデル - 拡張版
業種別対応のため business_type を追加
"""

from datetime import datetime, time
from typing import Optional
import uuid

from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey, Index, Text, Float, Integer, Time, Enum, JSON, LargeBinary, Date
from sqlalchemy.orm import relationship, deferred
from app.database import Base
import enum


class BusinessType(str, enum.Enum):
    """業種タイプ"""
    RESTAURANT = "restaurant"      # 飲食店
    BEAUTY = "beauty"             # 美容院
    HOTEL = "hotel"               # ホテル
    SCHOOL = "school"             # スクール
    CRAM_SCHOOL = "cram_school"   # 塾
    CLINIC = "clinic"             # 医院
    GYM = "gym"                   # ジム
    OTHER = "other"               # その他


class ShopCategory(str, enum.Enum):
    """店舗カテゴリ（細分類）"""
    # Restaurant
    RAMEN = "ramen"
    SUSHI = "sushi"
    IZAKAYA = "izakaya"
    CAFE = "cafe"
    BAR = "bar"
    # Beauty
    SALON = "salon"
    NAIL = "nail"
    SPA = "spa"
    # Hotel
    RESORT = "resort"
    BUSINESS = "business"
    # Other
    CLINIC = "clinic"
    GYM = "gym"
    OTHER = "other"


class Shop(Base):
    """店舗情報 - 業種対応版"""

    __tablename__ = "shops"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)

    # 基本情報
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    
    # ★★★ 新規: 業種タイプ（主分類）
    business_type = Column(String(50), nullable=True)  # BusinessType enum
    
    # 既存: カテゴリ（細分類）
    category = Column(String(50), nullable=False)  # ShopCategory
    
    # 住所・位置情報
    address = Column(String(500), nullable=False)
    latitude = Column(Float, nullable=True)  # GPS緯度
    longitude = Column(Float, nullable=True)  # GPS経度
    
    # 連絡先（お客様にも公開される、店舗の一般連絡先）
    phone = Column(String(20), nullable=True)
    email = Column(String(255), nullable=True)
    website = Column(String(500), nullable=True)

    # Phase3H Workstream C: 予約通知の連絡先（オーナー専用・非公開）。
    # 上記の phone とは全く別の目的のフィールドであることに注意。
    # phone は「お客様がお店に電話する」ための番号（ShopResponse等で
    # 公開されている）。こちらは逆に「新しい予約が入ったときに、AIスタッフ
    # から電話で知らせる先」の番号で、店舗のオーナー本人とは限らず、店長・
    # 受付担当・事務所などの場合もある。ShopResponse・ShopUpdateRequest・
    # 検索/詳細などのCustomer向けAPIには絶対に含めず、専用のオーナー認証
    # 付きエンドポイント（GET/PUT /api/v1/shops/{shop_id}/notification-settings）
    # からのみ読み書きする（app/routers/shops.py参照）。
    # 保存形式は数字のみに正規化した文字列（例："09012345678"）。
    reservation_notification_phone = Column(String(20), nullable=True)
    # このフラグがTrueの場合のみ、将来のOutbound AI機能が実際に電話をかける
    # （本フェーズでは架電処理自体は未実装。設定の土台のみ）。
    # 電話番号が未登録のままTrueにはできない、電話番号を削除したらFalseに
    # 戻す、という整合性はAPI層（update_shop_notification_settings）で保証する。
    reservation_phone_notification_enabled = Column(Boolean, nullable=False, default=False)
    
    # 画像
    thumbnail_url = Column(String(500), nullable=True)
    cover_image_url = Column(String(500), nullable=True)

    # お店の特徴（タグのリスト。例: ["個室あり", "禁煙", "駐車場あり"]）
    features = Column(JSON, nullable=True, default=list)

    # ★★★ Phase 5A: 多言語AI受付 ★★★
    # AI受付（Realtime音声）が実際に応答してよい言語のコードリスト（例: ["ja", "en"]）。
    # 言語コードの定義・検証・デフォルトへのフォールバックは app/language_registry.py を
    # 単一の情報源として使うこと（このカラムの値だけを見て判断してはいけない）。
    # None（未設定）はこの機能導入前からの既存店舗を表し、日本語のみとして扱われる
    # （app.language_registry.effective_ai_languages）。日本語は無効化不可の必須言語。
    ai_supported_languages = Column(JSON, nullable=True, default=None)

    # 店頭スタッフ（人間）が対応できる言語のコードリスト（例: ["ja", "en", "zh"]）。
    # AI受付が対応してよい言語（ai_supported_languages）とは独立した設定であり、
    # 意味も異なる（スタッフが話せる言語であって、AIが話してよい言語ではない）。
    # None（未設定）は日本語のみとして扱われる（app.language_registry.effective_languages）。
    staff_supported_languages = Column(JSON, nullable=True, default=None)

    # 予約設定：1組あたりの標準滞在時間（分）。空き状況の計算に使用
    reservation_duration_minutes = Column(Integer, nullable=True, default=90)

    # ロゴ・サムネイル・カバー画像（アップロードされたもの。deferred で一覧取得時には読み込まない）
    logo_data = deferred(Column(LargeBinary, nullable=True))
    logo_mime_type = Column(String(100), nullable=True)
    thumbnail_data = deferred(Column(LargeBinary, nullable=True))
    thumbnail_mime_type = Column(String(100), nullable=True)
    cover_data = deferred(Column(LargeBinary, nullable=True))
    cover_mime_type = Column(String(100), nullable=True)

    # 営業情報
    is_active = Column(Boolean, default=True, nullable=False)
    is_featured = Column(Boolean, default=False, nullable=False)  # 特集フラグ

    # 予約受付（PAY.jp課金アクティベーション後に有効化。既存店舗はマイグレーションでTrueのまま維持）
    reservations_enabled = Column(Boolean, default=False, nullable=False)

    # Phase3E-1: スタッフシフト管理機能フラグ（将来のPhase 3E-3以降で使用）。
    # 本フェーズではAvailability判定・管理画面UIのどこからも参照しない（列の追加のみ）。
    staff_schedule_enabled = Column(Boolean, default=False, nullable=False)

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
    # ★★★ 新規リレーション
    services = relationship("Service", back_populates="shop", cascade="all, delete-orphan")
    staff = relationship("Staff", back_populates="shop", cascade="all, delete-orphan")
    photos = relationship("ShopPhoto", back_populates="shop", cascade="all, delete-orphan")
    menu_items = relationship("MenuItem", back_populates="shop", cascade="all, delete-orphan")
    tables = relationship("ShopTable", back_populates="shop", cascade="all, delete-orphan")
    closures = relationship("ShopClosure", back_populates="shop", cascade="all, delete-orphan")
    # ★★★ マイページ機能: 店舗削除時にいいね・行きたい店・クーポンも一緒に削除する
    user_shop_relations = relationship("UserShopRelation", back_populates="shop", cascade="all, delete-orphan")
    coupons = relationship("Coupon", back_populates="shop", cascade="all, delete-orphan", foreign_keys="Coupon.shop_id")
    # Phase3H Workstream D: 店舗削除時にAIStaffSettings/ShopKnowledge/ShopFAQも
    # 一緒に削除されるようにする（従来これらの3つだけリレーション未定義のため、
    # 削除しようとするとFK制約違反で500エラーになっていた不具合の修正）。
    ai_staff_settings = relationship("AIStaffSettings", back_populates="shop", cascade="all, delete-orphan", uselist=False)
    shop_knowledge = relationship("ShopKnowledge", back_populates="shop", cascade="all, delete-orphan", uselist=False)
    shop_faqs = relationship("ShopFAQ", back_populates="shop", cascade="all, delete-orphan")

    # Outbound AI Phase 1: 店舗削除時にジョブ・ログも一緒に削除されるようにする
    # （Production E2E検証で、これが無いと予約経由の間接カスケードだけでは
    # FK制約違反でdelete_shop()が失敗するケースがあることが判明したため追加）。
    outbound_call_jobs = relationship("OutboundCallJob", back_populates="shop", cascade="all, delete-orphan")
    outbound_call_logs = relationship("OutboundCallLog", back_populates="shop", cascade="all, delete-orphan")

    # Outbound AI Phase 4A: Customer Memory Foundation。店舗削除時に、その店舗の
    # 顧客記憶も一緒に削除する（他店舗・他tenantとは元々共有していないデータの
    # ため、Shopが無くなれば意味を持たない）。FK自体もondelete="CASCADE"にして
    # あるため、ORM cascadeとDB制約の二重の防御になっている
    # （app/models/customer_memory.pyのdocstring参照）。
    customer_memories = relationship("CustomerMemory", back_populates="shop", cascade="all, delete-orphan")

    # インデックス
    __table_args__ = (
        Index("ix_shops_tenant", "tenant_id"),
        Index("ix_shops_business_type", "business_type"),  # ★★★ 新規
        Index("ix_shops_category", "category"),
        Index("ix_shops_is_active", "is_active"),
    )

    def __repr__(self):
        return f"<Shop(id={self.id}, name={self.name}, business_type={self.business_type})>"


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


class ShopPhotoKind(str, enum.Enum):
    """店舗写真の種類"""
    EXTERIOR = "exterior"   # 外観
    INTERIOR = "interior"   # 内装
    OTHER = "other"         # その他


class ShopPhoto(Base):
    """店舗写真（外観・内装など）"""

    __tablename__ = "shop_photos"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    shop_id = Column(String(36), ForeignKey("shops.id"), nullable=False, index=True)

    kind = Column(String(20), nullable=False, default="other")  # ShopPhotoKind
    image_data = Column(LargeBinary, nullable=False)
    mime_type = Column(String(100), nullable=False)
    display_order = Column(Integer, default=0, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    shop = relationship("Shop", back_populates="photos")

    __table_args__ = (
        Index("ix_shop_photos_shop_kind", "shop_id", "kind"),
    )

    def __repr__(self):
        return f"<ShopPhoto(id={self.id}, shop_id={self.shop_id}, kind={self.kind})>"


class MenuItem(Base):
    """メニュー項目（料理・サービスなど）"""

    __tablename__ = "menu_items"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    shop_id = Column(String(36), ForeignKey("shops.id"), nullable=False, index=True)

    category = Column(String(100), nullable=False, default="その他")  # 例: フード、ドリンク
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    price = Column(Integer, nullable=True)  # 円

    image_data = Column(LargeBinary, nullable=True)
    mime_type = Column(String(100), nullable=True)

    is_available = Column(Boolean, default=True, nullable=False)  # False = 売り切れ・提供停止
    display_order = Column(Integer, default=0, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    shop = relationship("Shop", back_populates="menu_items")

    __table_args__ = (
        Index("ix_menu_items_shop_category", "shop_id", "category"),
    )

    def __repr__(self):
        return f"<MenuItem(id={self.id}, shop_id={self.shop_id}, name={self.name})>"


class ShopTable(Base):
    """テーブル・席（予約の空き状況計算に使用）"""

    __tablename__ = "shop_tables"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    shop_id = Column(String(36), ForeignKey("shops.id"), nullable=False, index=True)

    name = Column(String(100), nullable=False)  # 例: テーブルA、カウンター1
    capacity = Column(Integer, nullable=False)  # 席数
    is_active = Column(Boolean, default=True, nullable=False)  # False = 一時的に使用不可
    display_order = Column(Integer, default=0, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    shop = relationship("Shop", back_populates="tables")

    __table_args__ = (
        Index("ix_shop_tables_shop", "shop_id"),
    )

    def __repr__(self):
        return f"<ShopTable(id={self.id}, shop_id={self.shop_id}, name={self.name})>"


class ShopClosure(Base):
    """臨時休業日（不定期休業）。定休日とは別に特定の期間を休業扱いにする"""

    __tablename__ = "shop_closures"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    shop_id = Column(String(36), ForeignKey("shops.id"), nullable=False, index=True)

    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    reason = Column(String(200), nullable=True)  # 例：「天候」「設備メンテナンス」

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    shop = relationship("Shop", back_populates="closures")

    __table_args__ = (
        Index("ix_shop_closures_shop", "shop_id"),
        Index("ix_shop_closures_dates", "start_date", "end_date"),
    )

    def __repr__(self):
        return f"<ShopClosure(id={self.id}, shop_id={self.shop_id}, {self.start_date}~{self.end_date})>"
