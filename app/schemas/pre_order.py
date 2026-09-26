"""
PreOrder / PreOrderItem スキーマ定義 — PHASE O2: Pre-Order DB Foundation

★★★ 重要な設計原則（Section2 / FINAL PRINCIPLE）:
「4人で予約」(Reservation.number_of_people) と「ドーナツ40個注文」
(PreOrderItem.quantity) は完全に別概念。本スキーマはReservationの
概念・フィールドを一切流用しない。

O2時点では、Public/Owner APIエンドポイントは実装しない（Section20）。
本スキーマは「model/schemaの単体テストに必要な最小限のservice helper」
（app/services/pre_orders.py）を支える、O3でそのまま使える純粋な
schema foundationとして用意する（Section21）。
"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, EmailStr

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


__all__ = [
    "PreOrderItemCreate",
    "PreOrderCreate",
    "PreOrderItemResponse",
    "PreOrderResponse",
    "PreOrderStatus",
    "PreOrderConfirmationStatus",
]
