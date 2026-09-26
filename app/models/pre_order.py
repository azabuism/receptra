"""
PreOrder / PreOrderItem モデル — PHASE O2: 商品事前注文・受取予約の
安全なデータ基盤

★★★ 最重要原則（絶対に守ること。仕様書 Section 2 / FINAL PRINCIPLE）:

Reservation（「何人で来店するか」）とPreOrder（「何を何個受け取るか」）は
完全に別概念であり、混同しない。

    予約人数   → Reservation.number_of_people
    商品の数量 → PreOrderItem.quantity

「ドーナツ40個」を Reservation.number_of_people = 40 として保存すること
は絶対に行わない。本モデルはReservationとは独立した新規テーブルとして
追加する（既存Reservationテーブルへのカラム追加・変更は一切行わない）。

設計方針（既存コードベースの確立済みパターンに合わせている。詳細はPHASE O2
着手前の監査コメント・各Sectionの参照を参照）:

- id: 他の全モデルと同じ String(36) + uuid4() 方式（ネイティブUUID型では
  ない。app/models/reservation.py・resource.py・callback_request.py等と
  完全に同じ規約）。

- pickup_at: 「予約日時」ではなく「受取予定日時」（Section5）。
  app/routers/reservations.pyで確立済みの JST-local-naive
  （datetime.now(_JST).replace(tzinfo=None) と同じ基準のnaive datetime）
  に合わせる。新しいtimezone architectureは作らない（Section5の明示指示）。

- status / confirmation_status: DB enumにしない。Reservation.status /
  CallbackRequest.status・resolution_statusと同じ「Python str-Enumで
  型安全性を確保しつつ、DB列はString」という既存規約に合わせる
  （将来値が増えてもDB migrationを要求しない）。

- 金額: unit_priceはInteger（円）、nullable。Decimalは既存コードベースの
  どこにも使われていない（MenuItem.price / Reservation.total_price /
  discount_amount はすべてInteger円）ため、新しい型を持ち込まずこれに
  合わせる。floatは使わない（Section11の明示指示）。

- product_name: PreOrderItemにsnapshotとして保持する。将来Product
  master（Section9で検討したが、対応する既存テーブルが無いため今回は
  追加しない。YAGNI）が出来て商品名が変更されても、過去の注文明細の
  表示が変わらないようにするため。

- customer_name / customer_phone: 必須（NOT NULL）。Reservationの
  guest_name / guest_phoneとは異なりnullableにしない。Reservationは
  「ログイン済みCustomer経由」と「ゲスト予約」の2経路を持つため
  guest_*列がnullableだが、PreOrderは現時点でCustomer/CustomerMemoryと
  直接統合しない（Section12）ため、常に注文者名・電話番号を受け取る
  前提でNOT NULLとする。

- CustomerMemoryとは直接統合しない・privacy gateも変更しない（Section12）。

- product_idのような将来のProduct masterへのFKは、対応するテーブルが
  存在しない現時点では追加しない（存在しないテーブルへのFKを作らない。
  Section9のYAGNI原則）。
"""

from datetime import datetime
import uuid
import enum

from sqlalchemy import Column, String, DateTime, ForeignKey, Index, Text, Integer, Boolean
from sqlalchemy.orm import relationship

from app.database import Base


class PreOrderStatus(str, enum.Enum):
    """事前注文のライフサイクル（オペレーション上の進捗）。
    confirmation_status（店舗が対応可能と確認できたか）とは別軸
    （Section7参照。二重状態管理ではなく、意図的に分離した設計。
    下記PreOrderConfirmationStatusのdocstringに理由を記載）。"""
    PENDING = "pending"        # 受付済み・未処理
    CONFIRMED = "confirmed"    # 店舗が準備を開始することを確定
    READY = "ready"            # 受取可能（準備完了）
    COMPLETED = "completed"    # 受取完了
    CANCELLED = "cancelled"    # キャンセル


class PreOrderConfirmationStatus(str, enum.Enum):
    """「店舗が実際に作れることを確認済みか」を表す、statusとは独立した軸。

    Section7の「二重状態管理を無意味に作らない」という指示を踏まえて検討した
    結果、以下の理由でstatusとは別カラムとして残すことにした（重複ではない）:
    - confirmation_statusは「この数量・内容を店舗が物理的に対応できるか」
      という受注可否の判断（Section7の例：ドーナツ4個は自動確定可能、
      40個は店舗確認が必要）を表す、受注時点の一度きりの判断に近い軸。
    - statusはそれとは別に、受注後の運機オペレーション進捗
      （準備開始→受取可能→受取完了、または途中でのキャンセル）を表す。
      confirmation_statusには「受取可能」「受取完了」に相当する概念が無く、
      statusにも「店舗が対応可能と判断したか」という受注可否の概念が無い
      ため、片方だけでは両方の意味を表現できない。
    - 将来（O3以降）、confirmation_status が OWNER_CONFIRMATION_REQUIRED の
      間はOWNER_ACTION_REQUIRED通知（Section17）に接続する想定だが、その
      自動判定ロジック自体は本フェーズでは実装しない（Section19: 大量注文
      ルール・lead time等は作らない。foundationのみ）。
    """
    CONFIRMED = "confirmed"                                      # 対応可能と確認済み（自動 or 店舗確認済み）
    OWNER_CONFIRMATION_REQUIRED = "owner_confirmation_required"  # 店舗の確認が必要
    REJECTED = "rejected"                                        # 店舗が対応不可と判断


class PreOrder(Base):
    """商品事前注文・受取予約（弁当・ドーナツ・ケーキ等の「何を何個」）。

    Reservation（「何人で来店するか」）とは完全に独立した新規テーブル。
    既存Reservationテーブルへの変更は一切ない。"""

    __tablename__ = "pre_orders"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    shop_id = Column(String(36), ForeignKey("shops.id"), nullable=False, index=True)

    # 注文者情報。CustomerMemoryとは直接統合しない（Section12）。
    customer_name = Column(String(255), nullable=False)
    customer_phone = Column(String(20), nullable=False)
    customer_email = Column(String(255), nullable=True)

    # 受取予定日時。「予約日時」ではなく「受取予定日時」（Section5）。
    # Reservation.reservation_dateと同じJST-local-naive基準
    # （app/routers/reservations.pyの_JST / _now_in_reservation_basis()参照）。
    pickup_at = Column(DateTime, nullable=False, index=True)

    status = Column(String(20), nullable=False, default=PreOrderStatus.PENDING.value, index=True)
    # PHASE O3実装時の検討事項（結論: O2時点のCONFIRMEDのまま変更しない）:
    # Section2の安全原則（根拠のない注文をconfirmedにしない）を守るための
    # 実質的な防衛線は、このDB列デフォルトではなく、実際の新規作成経路が
    # 必ず経由する app/services/pre_orders.py の create_public_pre_order() /
    # determine_pre_order_confirmation() 側にある（Web/AI Chat/AI Voice/
    # 将来PSTNのいずれも、この関数の戻り値をPreOrder()生成時に明示的に渡し、
    # 列デフォルトに頼ることは一切ない）。一時、列デフォルト自体も
    # OWNER_CONFIRMATION_REQUIREDへ変更するdefense-in-depth案を検討したが、
    # (1) 実際の安全性には寄与しない（上記の理由で列デフォルトが使われる
    # コードパスが存在しない）、(2) O2で書いた既存の正当な回帰テスト
    # （tests/smoke_test_phase_o2_preorder_foundation.py）がこの列デフォルトを
    # 明示的に検証しており、変更するとO2で確立した仕様に対する不要な回帰に
    # なる、という2点から、変更しない方針に確定した。
    confirmation_status = Column(
        String(30), nullable=False, default=PreOrderConfirmationStatus.CONFIRMED.value
    )
    # PHASE O3: Web/AI Chat/AI Voice/将来PSTNいずれの経路からの二重送信も
    # 同一注文として扱うための冪等キー。Reservation.idempotency_keyと
    # 完全に同じ設計（DB一意インデックスを最終防衛線とする）。Reservationとは
    # 異なりWeb経路でも使用を想定する（Section19）。
    idempotency_key = Column(String(200), nullable=True)

    # お客様からの要望（例:「Happy Birthdayと書いてください」）と、店舗内部用
    # メモは明確に区別する（Section23）。internal_noteはPublicレスポンス
    # スキーマ（app/schemas/pre_order.pyのPreOrderResponse）に含めない設計とする。
    customer_note = Column(Text, nullable=True)
    internal_note = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    shop = relationship("Shop", back_populates="pre_orders")
    items = relationship(
        "PreOrderItem", back_populates="pre_order", cascade="all, delete-orphan",
        order_by="PreOrderItem.created_at",
    )

    __table_args__ = (
        # 典型的な将来query（Section15）: 店舗別+今日/明日受取、店舗別+status別
        Index("ix_pre_orders_shop_pickup", "shop_id", "pickup_at"),
        Index("ix_pre_orders_shop_status", "shop_id", "status"),
        # PHASE O3: Reservationのux_reservations_idempotency_keyと全く同じ設計
        # （同一キーでの同時多重INSERTをDBレベルで確実に1件だけに絞る最終防衛線）。
        # PostgreSQLの一意インデックスはNULL同士を重複とみなさないため、
        # idempotency_keyを指定しないリクエスト（NULLのまま）同士は影響しない。
        Index("ux_pre_orders_idempotency_key", "idempotency_key", unique=True),
    )

    def __repr__(self):
        return f"<PreOrder(id={self.id}, shop_id={self.shop_id}, pickup_at={self.pickup_at}, status={self.status})>"


class PreOrderItem(Base):
    """事前注文の商品明細（1商品×数量）。1 PreOrderにつき複数行を持てる
    （Section25: 「ドーナツ40個」を40行の別注文にせず、商品単位で1行にまとめる。
    例: チョコドーナツ×20 / シュガードーナツ×20 の2行）。"""

    __tablename__ = "pre_order_items"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    pre_order_id = Column(String(36), ForeignKey("pre_orders.id"), nullable=False, index=True)

    # 将来Product masterが出来ても過去の注文明細の表示が変わらないよう、
    # 注文時点の商品名をsnapshotとして保持する（Section8）。
    # product_idのような将来のFKは、対応するテーブルが存在しない現時点では
    # 追加しない（Section9のYAGNI原則）。
    product_name = Column(String(255), nullable=False)
    quantity = Column(Integer, nullable=False)  # 1以上（app/schemas/pre_order.pyでgt=0を強制）

    variant = Column(String(255), nullable=True)  # サイズ・味・色などのバリエーション
    # 単価（円）。float禁止（Section11）。既存コードベースはDecimalを一切
    # 使わずInteger円で統一しているため（MenuItem.price等）、それに合わせる。
    # 不明な場合はNULL。AIが価格を推測して埋めることは禁止（Section11）。
    unit_price = Column(Integer, nullable=True)
    item_note = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    pre_order = relationship("PreOrder", back_populates="items")

    __table_args__ = (
        Index("ix_pre_order_items_pre_order", "pre_order_id"),
    )

    def __repr__(self):
        return (
            f"<PreOrderItem(id={self.id}, pre_order_id={self.pre_order_id}, "
            f"product_name={self.product_name}, quantity={self.quantity})>"
        )


# ============================================================
# PHASE O4: Pre-Order Owner Settings & Product Rules
# ============================================================

class PreOrderProduct(Base):
    """事前注文の商品マスター（PHASE O4で新規追加）。

    ★設計判断（Section1/2の監査結果）: 既存MenuItem（app/models/shop.py）は
    「閲覧用メニュー」であり、数量・注文概念を一切持たない
    （price以外はcategory/description/image/is_available/display_orderのみ）。
    数量上限・準備時間という注文受付ルールをMenuItemへ後付けすると、
    「メニューに表示するかどうか」と「事前注文をいくつまで自動受付するか」
    という別々の関心事が1テーブルに混在してしまう。よってMenuItemを流用せず、
    事前注文専用の新規テーブルとしてPreOrderProductを追加する
    （Section2の判断: 汎用化は安全ではないため専用モデルを作る）。

    ★PreOrderItemとの関係（Section10/11）: PreOrderItemは本テーブルへのFKを
    持たない。Public Create APIのitemはこれまで通りproduct_name（文字列）で
    届き、create_public_pre_order()が受取時にshopの有効なPreOrderProductと
    「trim済み完全一致のみ」で照合する（曖昧な部分一致・あいまい検索は行わない。
    Section10）。一致しない場合は「未知の商品」として扱い、reject せず
    owner_confirmation_requiredにする（Section31）。この設計により、
    商品削除・商品名変更は過去のPreOrderItem行（snapshot）に一切影響しない
    （Section22の削除安全性は、そもそもFK参照が無いことで構造的に満たされる）。

    ★name一意性: 同一shop内で同名商品が複数存在すると、Public Create APIからの
    商品名照合が曖昧になり得る（例: 同名で条件の異なる2商品があると、どちらの
    ルールを適用すべきか一意に決まらない）。Section10の「曖昧な商品名matching
    禁止」を安全側で徹底するため、(shop_id, name)にDB一意インデックスを設ける。

    ★auto_confirm_max_quantity / minimum_lead_time_minutes がNULLの意味
    （Section6/7）: 「無制限」ではなく「自動確定しない」（安全側のデフォルト）。
    determine_pre_order_confirmation()はどちらかがNULLの商品を含む注文を
    必ずowner_confirmation_requiredにする。
    """

    __tablename__ = "pre_order_products"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    shop_id = Column(String(36), ForeignKey("shops.id"), nullable=False, index=True)

    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    # 価格（円）。MenuItem.price / PreOrderItem.unit_priceと同じInteger・
    # nullable（Section4）。floatは使わない。
    price = Column(Integer, nullable=True)

    # MenuItem.is_available / ShopTable.is_activeと同じBoolean規約に合わせる
    # （Service.is_activeのString("active"/"inactive")規約は採用しない。
    # 本テーブルはMenuItemに構造が近いため、より一般的な既存パターンを踏襲）。
    is_active = Column(Boolean, default=True, nullable=False)
    display_order = Column(Integer, default=0, nullable=False)

    # Section5/6: 自動確定できる最大数量。NULL = 自動確定しない（安全側）。
    auto_confirm_max_quantity = Column(Integer, nullable=True)
    # Section7: 受取時刻の何分前までの注文なら自動確定してよいか。
    # NULL = 自動確定しない（安全側）。
    minimum_lead_time_minutes = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    shop = relationship("Shop", back_populates="pre_order_products")

    __table_args__ = (
        Index("ix_pre_order_products_shop", "shop_id"),
        Index("ix_pre_order_products_shop_active", "shop_id", "is_active"),
        Index("ux_pre_order_products_shop_name", "shop_id", "name", unique=True),
    )

    def __repr__(self):
        return (
            f"<PreOrderProduct(id={self.id}, shop_id={self.shop_id}, "
            f"name={self.name}, is_active={self.is_active})>"
        )
