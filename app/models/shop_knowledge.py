"""
Shop Knowledge & FAQ（Phase3D）

RECEPTRAのAI受付が「駐車場はありますか？」「カードは使えますか？」等の
店舗情報に関する質問に、推測ではなくDBに登録された正しい情報だけで
回答できるようにするための知識ベース。

設計方針（重要・必ず守ること）:
- 大きく2種類の情報を、無理に1つの巨大なJSON/長文へ押し込まず、かつ
  過剰なテーブル分割も避けるため、2テーブルに分ける。
  1. ShopKnowledge: 構造化された店舗情報（駐車場・支払い方法・設備・
     来店前案内・キャンセルポリシー）。shop_idと1:1
     （AIStaffSettingsと全く同じ設計パターン）。
  2. ShopFAQ: 店舗独自のQ&A。shop_idと1:多。
- 「未設定（情報なし）」と「なし（明確にNo）」を区別する必要があるため、
  yes/no的な項目はBooleanではなくNullable Stringで持つ
  （None=未設定、"yes"/"no"/"conditional"）。"conditional"（条件付き）は
  facilities_notesで詳細を補足する想定。単純なbooleanにすると
  「未設定」と「なし」を区別できず、AIがハルシネーションで
  「駐車場はありません」と断定してしまう事故につながるため、これは
  安全上重要な設計判断（Phase3D仕様書 section12 参照）。
- parking_available / payment_cash / payment_credit_card / payment_debit_card /
  payment_qr_code / payment_emoney（大分類）は yes/no/未設定の3値のみで
  十分と判断（仕様書のUI例が3択のため）。facilities系の各項目、および
  Phase3Hで追加したブランド単位の支払い方法（payment_credit_visa等）は
  yes/no/conditional/未設定の4値を許容する
  （ブランドによっては「一部対応」等の条件付きケースがあり得るため）。
- alembicは運用していないため、app/main.py の lifespan() 内
  Base.metadata.create_all() により自動作成される新規テーブルとする
  （既存テーブルへのカラム追加ではないため、手動ALTERは不要）。
"""

import uuid
from datetime import datetime

from sqlalchemy import Column, String, Text, Integer, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from app.database import Base


class ShopKnowledge(Base):
    """店舗ごとの構造化知識（駐車場・支払い・設備・来店前案内・キャンセル）。shop_idと1:1。"""

    __tablename__ = "shop_knowledge"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # shop_id との1:1関係。AIStaffSettingsと同じくlazy creation
    # （オーナーが初めて店舗情報設定画面を保存した時点で作成される）。
    shop_id = Column(
        String(36),
        ForeignKey("shops.id"),
        nullable=False,
        unique=True,
        index=True,
    )

    # ===== 駐車場 =====
    # None=未設定 / "yes" / "no"
    parking_available = Column(String(10), nullable=True)
    parking_spaces = Column(Integer, nullable=True)
    # 例: "専用駐車場" "提携駐車場" "コインパーキング案内のみ"（自由入力・短文）
    parking_type = Column(String(100), nullable=True)
    # 例: "無料" "1時間200円"
    parking_fee = Column(String(100), nullable=True)
    # 例: "店舗裏"
    parking_location = Column(String(255), nullable=True)
    # 提携駐車場名など
    partner_parking = Column(String(255), nullable=True)
    # 満車時の案内文
    parking_full_guidance = Column(Text, nullable=True)
    # その他補足（例: 「店舗正面の2台は他店舗専用です」）
    parking_notes = Column(Text, nullable=True)

    # ===== 支払い方法 =====
    # 各 None=未設定 / "yes" / "no"
    payment_cash = Column(String(10), nullable=True)
    payment_credit_card = Column(String(10), nullable=True)
    payment_debit_card = Column(String(10), nullable=True)
    payment_qr_code = Column(String(10), nullable=True)  # PayPay等QR決済
    payment_emoney = Column(String(10), nullable=True)  # 電子マネー
    # 対応ブランド等の自由記述補足（例: "VISA/Mastercard/JCB/PayPay対応"）
    payment_notes = Column(Text, nullable=True)

    # ===== 支払い方法（Phase3H: ブランド単位の詳細） =====
    # 上記の payment_credit_card / payment_qr_code / payment_emoney（大分類）は
    # 後方互換のためそのまま維持し、意味も変更しない。既存店舗のデータ・既存の
    # get_shop_info(payment)の大分類回答はこれまで通り動作する。
    # 以下はオーナーがブランド単位で対応状況を登録できるようにするための追加の
    # 詳細カラムであり、大分類とは独立して保持する（大分類が空でもブランド単位
    # だけ設定されているケース、その逆のケースの両方をサポートする）。
    # 各 None=未設定 / "yes"=対応 / "no"=非対応 / "conditional"=条件付き
    # （例:一部ブランドのみ・上限あり等。詳細は既存payment_notesで補足する想定）。
    # クレジットカード（ブランド別）
    payment_credit_visa = Column(String(15), nullable=True)
    payment_credit_mastercard = Column(String(15), nullable=True)
    payment_credit_jcb = Column(String(15), nullable=True)
    payment_credit_amex = Column(String(15), nullable=True)  # American Express
    payment_credit_diners = Column(String(15), nullable=True)  # Diners Club
    payment_credit_other = Column(String(15), nullable=True)  # その他ブランド（詳細はpayment_notesへ）
    # QRコード決済（ブランド別）
    payment_qr_paypay = Column(String(15), nullable=True)
    payment_qr_au_pay = Column(String(15), nullable=True)
    payment_qr_d_barai = Column(String(15), nullable=True)  # d払い
    payment_qr_rakuten_pay = Column(String(15), nullable=True)  # 楽天ペイ
    payment_qr_merpay = Column(String(15), nullable=True)  # メルペイ
    payment_qr_other = Column(String(15), nullable=True)  # その他QR決済（詳細はpayment_notesへ）
    # 電子マネー（ブランド別）
    payment_emoney_transit_ic = Column(String(15), nullable=True)  # 交通系IC（Suica/PASMO等）
    payment_emoney_id = Column(String(15), nullable=True)  # iD
    payment_emoney_quicpay = Column(String(15), nullable=True)  # QUICPay
    payment_emoney_rakuten_edy = Column(String(15), nullable=True)  # 楽天Edy
    payment_emoney_waon = Column(String(15), nullable=True)  # WAON
    payment_emoney_nanaco = Column(String(15), nullable=True)  # nanaco
    payment_emoney_other = Column(String(15), nullable=True)  # その他電子マネー（詳細はpayment_notesへ）

    # ===== 設備・利用条件 =====
    # 各 None=未設定 / "yes" / "no" / "conditional"（条件付き。詳細はfacilities_notesへ）
    wifi_available = Column(String(15), nullable=True)
    private_room_available = Column(String(15), nullable=True)
    wheelchair_accessible = Column(String(15), nullable=True)
    children_allowed = Column(String(15), nullable=True)
    smoking_policy = Column(String(15), nullable=True)  # yes=喫煙可 / no=禁煙 / conditional=分煙等
    pets_allowed = Column(String(15), nullable=True)
    elevator_available = Column(String(15), nullable=True)
    # 上記設備共通の自由記述補足（例: "入口に段差があります。車椅子でご来店の際は事前にご連絡ください。"）
    facilities_notes = Column(Text, nullable=True)

    # ===== 来店前案内（将来Outbound AIの予約前フォローでも再利用） =====
    required_items = Column(Text, nullable=True)  # 持ち物
    pre_visit_instructions = Column(Text, nullable=True)  # 来店前の注意事項
    arrival_guidance = Column(Text, nullable=True)  # 到着時間の案内（例: 初回は10分前に）

    # ===== キャンセル・遅刻ポリシー =====
    # AIはここに書かれていないキャンセル料・ルールを勝手に作ってはならない。
    cancellation_policy = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    shop = relationship("Shop", foreign_keys=[shop_id], back_populates="shop_knowledge")

    def __repr__(self):
        return f"<ShopKnowledge(shop_id={self.shop_id})>"


class ShopFAQ(Base):
    """店舗独自のFAQ（よくある質問）。shop_idと1:多。"""

    __tablename__ = "shop_faqs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    shop_id = Column(String(36), ForeignKey("shops.id"), nullable=False, index=True)

    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)

    # 非公開化（削除せず一時的に回答対象から外す）用。ONにすると
    # get_shop_info(topic="faq") の検索対象・オーナー一覧の既定表示から除外可能
    # （オーナー管理画面では一覧自体は表示し、有効/無効の切り替えのみ行う想定）。
    is_active = Column(Boolean, nullable=False, default=True)

    # 管理画面での表示順（小さいほど先に表示）。
    sort_order = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    shop = relationship("Shop", foreign_keys=[shop_id], back_populates="shop_faqs")

    def __repr__(self):
        return f"<ShopFAQ(id={self.id}, shop_id={self.shop_id})>"
