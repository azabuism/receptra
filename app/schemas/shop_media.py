"""
店舗写真・メニュー スキーマ定義
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class ShopPhotoResponse(BaseModel):
    """店舗写真レスポンススキーマ"""
    id: str
    shop_id: str
    kind: str
    url: str
    display_order: int
    created_at: datetime

    class Config:
        from_attributes = True


class MenuItemResponse(BaseModel):
    """メニュー項目レスポンススキーマ"""
    id: str
    shop_id: str
    category: str
    name: str
    description: Optional[str] = None
    price: Optional[int] = None
    photo_url: Optional[str] = None
    is_available: bool
    display_order: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ReorderItem(BaseModel):
    id: str
    display_order: int


class ReorderRequest(BaseModel):
    items: List[ReorderItem] = Field(default_factory=list)
