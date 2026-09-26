"""
PreOrderProduct スキーマ定義 — PHASE O4: Pre-Order Owner Settings & Product Rules

Owner認証済みCRUD専用（Section21/35）。Public向けschemaはO5で別途検討する
（本フェーズでは事前注文商品の価格・自動確定ルールを不特定多数へ公開しない）。

app/schemas/service.pyのCreate/Update/Responseパターンに合わせる:
Create requiredフィールド + shop_id、Update全フィールドoptional（PATCH部分更新）、
Responseはfrom_attributes=Trueでモデルをそのまま写す。
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class PreOrderProductCreateRequest(BaseModel):
    shop_id: str = Field(..., min_length=1, description="店舗ID")
    name: str = Field(..., min_length=1, max_length=255, description="商品名")
    description: Optional[str] = Field(None, max_length=2000, description="商品説明")
    # Section4: 円・nullable。floatは使わない。未登録可（AI/Webが推測しない）。
    price: Optional[int] = Field(None, ge=0, description="価格（円）。未登録の場合はNone")
    # Section5/6: NULL＝自動確定しない（安全側デフォルト）。
    auto_confirm_max_quantity: Optional[int] = Field(
        None, gt=0, description="この数量まで自動確定してよい上限。未設定の場合は常にowner_confirmation_required"
    )
    # Section7: NULL＝自動確定しない（安全側デフォルト）。
    minimum_lead_time_minutes: Optional[int] = Field(
        None, ge=0, description="受取時刻の何分前までの注文なら自動確定してよいか。未設定の場合は常にowner_confirmation_required"
    )

    @field_validator("name")
    @classmethod
    def _trim_name(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("nameは空文字にできません")
        return trimmed


class PreOrderProductUpdateRequest(BaseModel):
    """PATCH部分更新用。exclude_unset=Trueで送られたフィールドのみ反映する
    （app/routers/shops.pyのupdate_shop()と同じ規約）。"""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)
    price: Optional[int] = Field(None, ge=0)
    auto_confirm_max_quantity: Optional[int] = Field(None, gt=0)
    minimum_lead_time_minutes: Optional[int] = Field(None, ge=0)
    is_active: Optional[bool] = None
    display_order: Optional[int] = Field(None, ge=0)

    @field_validator("name")
    @classmethod
    def _trim_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("nameは空文字にできません")
        return trimmed


class PreOrderProductResponse(BaseModel):
    id: str
    shop_id: str
    name: str
    description: Optional[str] = None
    price: Optional[int] = None
    is_active: bool
    display_order: int
    auto_confirm_max_quantity: Optional[int] = None
    minimum_lead_time_minutes: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


__all__ = [
    "PreOrderProductCreateRequest",
    "PreOrderProductUpdateRequest",
    "PreOrderProductResponse",
]
