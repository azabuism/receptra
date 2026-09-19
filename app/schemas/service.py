"""
Service (サービス) スキーマ定義
業種別のサービス・施術・コース（美容院のカット、クリニックの診療メニューなど）
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class ServiceCreateRequest(BaseModel):
    """サービス作成リクエスト"""
    shop_id: str = Field(..., description="店舗ID")
    name: str = Field(..., min_length=1, max_length=255, description="サービス名")
    description: Optional[str] = Field(None, max_length=2000, description="サービス説明")
    base_price: float = Field(..., ge=0, description="基本料金（円）")
    duration_minutes: Optional[int] = Field(None, ge=1, le=1440, description="施術・所要時間（分）")
    service_type: Optional[str] = Field(None, max_length=100, description="細分類（例：カット、カラー）")


class ServiceUpdateRequest(BaseModel):
    """サービス更新リクエスト（送られたフィールドのみ更新）"""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)
    base_price: Optional[float] = Field(None, ge=0)
    duration_minutes: Optional[int] = Field(None, ge=1, le=1440)
    service_type: Optional[str] = Field(None, max_length=100)
    is_active: Optional[bool] = None


class ServiceResponse(BaseModel):
    """サービスレスポンス"""
    id: str
    shop_id: str
    name: str
    description: Optional[str] = None
    base_price: float
    duration_minutes: Optional[int] = None
    service_type: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
