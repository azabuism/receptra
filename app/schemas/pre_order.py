"""
PreOrder / PreOrderItem スキーマ定義 — PHASE O2/O3

★★★ 重要な設計原則（Section2 / FINAL PRINCIPLE）:
「4人で予約」(Reservation.number_of_people) と「ドーナツ40個注文」
(PreOrderItem.quantity) は完全に別概念。本スキーマはReservationの
概念・フィールドを一切流用しない。

O2で追加した PreOrderItemCreate / PreOrderCreate / PreOrderItemResponse /
PreOrderResponse は、app/services/pre_orders.pyの内部helper
（create_pre_order_with_items()）向けの「信頼済みデータ用」schemaとして
そのまま維持する（unit_price/internal_noteを含む＝サーバー内部やO2の
単体テストが直接構築する場合の完全な表現）。

PHASE O3で追加したのは、実際にネットワーク越しに受け取る/返す
「境界（boundary）」専用のschema群:
- PreOrderItemPublicCreate / PreOrderPublicCreateRequest:
  Public Create APIのリクエスト。unit_price/internal_note/status/
  confirmation_status等、顧客が制御してはいけないフィールドは
  そもそも定義せず、extra="forbid"で未知のフィールドも拒否する
  （Section8/9/43: 価格注入・internal_note上書き・状態偽装を防ぐ）。
- PreOrderPublicCreateResponse: internal_note・tenant_id等の
  owner-only情報を一切含まない（Section29）。
- PreOrderOwnerDetailResponse / PreOrderOwnerListItem/ListResponse:
  オーナー専用。internal_noteを含めてよい（internal_noteは「店舗内部専用」
  であり、オーナー自身は当然閲覧できるべき情報のため。Public向けにのみ
  隠す設計。Section23の趣旨に沿った解釈）。
- PreOrderConfirmationUpdateRequest: オーナーによるconfirm/reject
  （Section33/34）。
"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, EmailStr, field_validator

from app.models.pre_order import PreOrderStatus, PreOrderConfirmationStatus


class PreOrderItemCreate(BaseModel):
    """事前注文の商品明細（1商品×数量）の作成リクエスト。"""
    product_name: str = Field(
        ..., min_length=1, max_length=255,
        description="商品名。将来Product masterが出来ても、注文時点の名称をsnapshotとして保持する",
    )
    # gt=0によりquantity=0・負数は自動的に拒否される（Section10）。
    quantity: int = Field(..., gt=0, description="数量。1以上の整数のみ")
    variant: Optional[str] = Field(None, max_length=255, description="サイズ・味・色などのバリエーション（任意）")
    # ge=0のみ強制（マイナス価格を防ぐ）。floatではなくint（円）。
    # 不明な場合はNone（AIが価格を推測して埋めてはならない。Section11）。
    unit_price: Optional[int] = Field(None, ge=0, description="単価（円）。不明な場合はNone")
    item_note: Optional[str] = Field(None, max_length=1000, description="この商品明細に対する備考")


class PreOrderCreate(BaseModel):
    """事前注文の作成リクエスト（O3のAPI実装時に流用する想定のfoundation
    schema。O2時点ではAPIエンドポイントには接続しない）。"""
    shop_id: str = Field(..., min_length=1, description="店舗ID")
    customer_name: str = Field(..., min_length=1, max_length=255, description="注文者名")
    customer_phone: str = Field(..., min_length=1, max_length=20, description="連絡先電話番号")
    customer_email: Optional[EmailStr] = Field(None, description="連絡先メールアドレス（任意）")
    # 「受取予定日時」。Reservation.reservation_dateと同じJST-local-naive基準
    # （app/models/pre_order.pyのdocstring・Section5参照）。
    pickup_at: datetime = Field(..., description="受取予定日時")
    # Section25: 1 PreOrderにつき複数のPreOrderItemを持てる（商品40個だから
    # 40行作るのではなく、商品ごとに1行）。min_length=1で商品明細ゼロ件を防ぐ。
    items: List[PreOrderItemCreate] = Field(..., min_length=1, description="商品明細（最低1件）")
    customer_note: Optional[str] = Field(None, max_length=1000, description="お客様からの要望（例:「Happy Birthdayと書いてください」）")
    internal_note: Optional[str] = Field(None, max_length=1000, description="店舗内部用メモ。Public/Ownerレスポンスには含めない")


class PreOrderItemResponse(BaseModel):
    id: str
    product_name: str
    quantity: int
    variant: Optional[str] = None
    unit_price: Optional[int] = None
    item_note: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class PreOrderResponse(BaseModel):
    """Public/Owner向けレスポンス。internal_noteは意図的に含めない
    （Section23: 店舗内部用メモをPublicレスポンスで返す設計にしない）。"""
    id: str
    shop_id: str
    customer_name: str
    customer_phone: str
    customer_email: Optional[str] = None
    pickup_at: datetime
    status: str
    confirmation_status: str
    customer_note: Optional[str] = None
    items: List[PreOrderItemResponse] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============================================================
# PHASE O3: Public Create API（境界schema）
# ============================================================

class PreOrderItemPublicCreate(BaseModel):
    """Public Create APIが受け取る商品明細。unit_priceフィールドは
    そもそも存在しない（Section8: 顧客側が価格を自由入力できないようにする）。
    extra="forbid"により、"unit_price"等の未知フィールドを送ってきても
    リクエスト全体がバリデーションエラーで拒否される（Section43）。"""
    model_config = ConfigDict(extra="forbid")

    product_name: str = Field(..., min_length=1, max_length=255, description="商品名")
    quantity: int = Field(..., gt=0, description="数量。1以上の整数のみ")
    variant: Optional[str] = Field(None, max_length=255, description="サイズ・味・色などのバリエーション（任意）")

    @field_validator("product_name")
    @classmethod
    def _trim_product_name(cls, v: str) -> str:
        # Section17: product_nameはtrimする。trim後に空文字になる場合は
        # min_length=1と同じ扱いで拒否する。
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("product_nameは空文字にできません")
        return trimmed

    @field_validator("variant")
    @classmethod
    def _trim_variant(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        trimmed = v.strip()
        return trimmed or None


class PreOrderPublicCreateRequest(BaseModel):
    """Public PreOrder Create APIのリクエスト（POST /api/v1/pre-orders/create）。

    意図的に定義しないフィールド（Section8/9/29/43）:
    unit_price / total_price / price / internal_note / status /
    confirmation_status / tenant_id — これらを送っても extra="forbid" に
    よりリクエスト全体が422で拒否される（無視して受理するのではなく、
    明確なエラーとして拒否する。仕様書Section43の"reject unknown/forbidden
    fields"の解釈として、無視より拒否の方が「価格を注入しようとした」ことを
    呼び出し元が確実に知れるため安全と判断した）。
    """
    model_config = ConfigDict(extra="forbid")

    shop_id: str = Field(..., min_length=1, description="店舗ID")
    customer_name: str = Field(..., min_length=1, max_length=255, description="注文者名")
    customer_phone: str = Field(..., min_length=1, max_length=20, description="連絡先電話番号")
    customer_email: Optional[EmailStr] = Field(None, description="連絡先メールアドレス（任意）")
    pickup_at: datetime = Field(..., description="受取予定日時（JST-local-naive。過去日時は拒否される）")
    items: List[PreOrderItemPublicCreate] = Field(..., min_length=1, description="商品明細（最低1件）")
    customer_note: Optional[str] = Field(None, max_length=1000, description="お客様からの要望")
    # Section19: Reservationのidempotency_keyと同じ仕組み。Web/AI Chat/AI Voice/
    # 将来PSTNいずれの経路でも、同一キーでの再送は新規作成せず既存のPreOrderを
    # そのまま返す。省略可（省略した場合はDB一意インデックスによる冪等性保護は
    # 働かない。将来O5のWeb UIはsubmit時に必ずこのキーを生成して送る想定）。
    idempotency_key: Optional[str] = Field(None, min_length=1, max_length=200, description="冪等キー（任意）")

    @field_validator("customer_name")
    @classmethod
    def _trim_customer_name(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("customer_nameは空文字にできません")
        return trimmed

    @field_validator("customer_phone")
    @classmethod
    def _trim_customer_phone(cls, v: str) -> str:
        # Section17: 既存共通のphone normalizerは存在しない（O2監査で確認済み）
        # ため、独自の複雑な正規化は行わず、trim＋空文字拒否のみに留める。
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("customer_phoneは空文字にできません")
        return trimmed


class PreOrderItemPublicResponse(BaseModel):
    """Public Create APIのレスポンスに含める商品明細。unit_priceは常にNoneに
    なる想定だが、将来Product masterと接続された際に自然に値が入るよう
    フィールド自体は残す（O3時点でPublicリクエストからunit_priceを
    受け取らないことと、レスポンスにunit_priceフィールドが存在すること自体は
    矛盾しない）。"""
    id: str
    product_name: str
    quantity: int
    variant: Optional[str] = None
    unit_price: Optional[int] = None

    class Config:
        from_attributes = True


# confirmation_status → 顧客向けに機械可読な状態コード。日本語の説明文言は
# 意図的にBackendへ大量にハードコードしない（Section30）。Frontend/AIが
# このコードを見て、その場に応じた自然な文言に変換する想定。
_CUSTOMER_MESSAGE_CODES = {
    PreOrderConfirmationStatus.CONFIRMED.value: "CONFIRMED",
    PreOrderConfirmationStatus.OWNER_CONFIRMATION_REQUIRED.value: "OWNER_CONFIRMATION_REQUIRED",
    PreOrderConfirmationStatus.REJECTED.value: "REJECTED",
}


def customer_message_code(confirmation_status: str) -> str:
    return _CUSTOMER_MESSAGE_CODES.get(confirmation_status, "OWNER_CONFIRMATION_REQUIRED")


class PreOrderPublicCreateResponse(BaseModel):
    """Public Create APIのレスポンス。internal_note・tenant_id・owner専用の
    メタデータは一切含めない（Section29）。confirmation_statusが
    owner_confirmation_requiredの場合でも、これが「注文確定」を意味しない
    ことをcustomer_message_codeで明示する（Section30）。"""
    success: bool = True
    pre_order_id: str
    shop_id: str
    pickup_at: datetime
    status: str
    confirmation_status: str
    customer_message_code: str
    items: List[PreOrderItemPublicResponse] = []
    created_at: datetime


# ============================================================
# PHASE O3: Owner向けAPI（境界schema）
# ============================================================

class PreOrderOwnerDetailResponse(BaseModel):
    """オーナー専用の詳細レスポンス。internal_noteはオーナー自身が読むための
    ものであるため、Publicレスポンスとは異なり含める（Section23の趣旨:
    「店舗内部専用」＝オーナーには見える、公開されないの意）。
    tenant_idは含めない（shop_idで十分。Reservationの既存Response規約と
    同じ最小情報の原則）。"""
    id: str
    shop_id: str
    customer_name: str
    customer_phone: str
    customer_email: Optional[str] = None
    pickup_at: datetime
    status: str
    confirmation_status: str
    customer_note: Optional[str] = None
    internal_note: Optional[str] = None
    items: List[PreOrderItemResponse] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PreOrderOwnerListItem(BaseModel):
    """オーナー一覧用の1行分。詳細（電話番号等）は既存のCallbackRequest/
    Reservation一覧と同じ思想で、フル情報は詳細APIでのみ取得する設計とも
    考えられるが、PreOrderのcustomer_phoneはReservationのguest_phoneほど
    高頻度に一覧表示されるものではなく、店舗が受取時に電話確認する運用上
    一覧でも必要になる場面が多いため、O3では一覧・詳細共通のフル表現とする
    （過剰な設計を避け、O4で実際のUI要件が固まった時点でマスク要否を
    再検討する）。"""
    id: str
    shop_id: str
    customer_name: str
    customer_phone: str
    pickup_at: datetime
    status: str
    confirmation_status: str
    item_count: int
    total_quantity: int
    created_at: datetime

    class Config:
        from_attributes = True


class PreOrderOwnerListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: List[PreOrderOwnerListItem] = []


class PreOrderConfirmationUpdateRequest(BaseModel):
    """オーナーによるPreOrder確認（Section33）。confirmation_statusとして
    受け付けるのは confirmed / rejected のみ。owner_confirmation_required
    （システムが初期作成時にのみ設定する状態）へ戻すAPIはO3では提供しない。"""
    confirmation_status: str = Field(..., description="confirmed(対応可能) / rejected(対応不可)")

    @field_validator("confirmation_status")
    @classmethod
    def _validate_confirmation_status(cls, v: str) -> str:
        valid = {
            PreOrderConfirmationStatus.CONFIRMED.value,
            PreOrderConfirmationStatus.REJECTED.value,
        }
        if v not in valid:
            raise ValueError(f"confirmation_statusは次のいずれかである必要があります: {', '.join(sorted(valid))}")
        return v


__all__ = [
    "PreOrderItemCreate",
    "PreOrderCreate",
    "PreOrderItemResponse",
    "PreOrderResponse",
    "PreOrderItemPublicCreate",
    "PreOrderPublicCreateRequest",
    "PreOrderItemPublicResponse",
    "PreOrderPublicCreateResponse",
    "customer_message_code",
    "PreOrderOwnerDetailResponse",
    "PreOrderOwnerListItem",
    "PreOrderOwnerListResponse",
    "PreOrderConfirmationUpdateRequest",
    "PreOrderStatus",
    "PreOrderConfirmationStatus",
]
