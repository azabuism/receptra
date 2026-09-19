"""
ShopTable (テーブル・席) スキーマ定義
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class ShopTableCreateRequest(BaseModel):
    """テーブル作成リクエスト"""
    name: str = Field(..., min_length=1, max_length=100, description="テーブル名（例：テーブルA）")
    capacity: int = Field(..., ge=1, le=999, description="席数")


class ShopTableUpdateRequest(BaseModel):
    """テーブル更新リクエスト（送られたフィールドのみ更新）"""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    capacity: Optional[int] = Field(None, ge=1, le=999)
    is_active: Optional[bool] = None


class ShopTableResponse(BaseModel):
    """テーブルレスポンス"""
    id: str
    shop_id: str
    name: str
    capacity: int
    is_active: bool
    display_order: int
    created_at: datetime

    class Config:
        from_attributes = True
