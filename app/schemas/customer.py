"""
Customer (顧客) スキーマ定義
リクエスト/レスポンスのPydantic モデル
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field


class CustomerRegisterRequest(BaseModel):
    """顧客登録リクエストスキーマ"""
    email: EmailStr = Field(..., description="メールアドレス")
    phone: Optional[str] = Field(None, max_length=20, description="電話番号")
    display_name: str = Field(..., min_length=1, max_length=255, description="表示名")
    avatar_url: Optional[str] = Field(None, max_length=500, description="アバター画像URL")
    address: Optional[str] = Field(None, max_length=500, description="住所")
    latitude: Optional[float] = Field(None, ge=-90, le=90, description="緯度")
    longitude: Optional[float] = Field(None, ge=-180, le=180, description="経度")


class CustomerUpdateRequest(BaseModel):
    """顧客情報更新リクエストスキーマ"""
    display_name: Optional[str] = Field(None, min_length=1, max_length=255, description="表示名")
    phone: Optional[str] = Field(None, max_length=20, description="電話番号")
    avatar_url: Optional[str] = Field(None, max_length=500, description="アバター画像URL")
    address: Optional[str] = Field(None, max_length=500, description="住所")
    latitude: Optional[float] = Field(None, ge=-90, le=90, description="緯度")
    longitude: Optional[float] = Field(None, ge=-180, le=180, description="経度")
    newsletter_subscribed: Optional[bool] = Field(None, description="ニュースレター登録")
    sms_subscribed: Optional[bool] = Field(None, description="SMS登録")


class CustomerResponse(BaseModel):
    """顧客レスポンススキーマ"""
    id: str
    tenant_id: str
    email: str
    phone: Optional[str]
    display_name: str
    avatar_url: Optional[str]
    address: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    is_verified: bool
    is_active: bool
    total_reservations: Optional[int]
    total_visits: Optional[int]
    total_spent: Optional[float]
    last_visit_date: Optional[datetime]
    first_visit_date: Optional[datetime]
    vip_level: Optional[str]
    customer_lifetime_value: Optional[float]
    newsletter_subscribed: bool
    sms_subscribed: bool
    last_login_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CustomerRegisterResponse(BaseModel):
    """顧客登録レスポンススキーマ"""
    success: bool
    message: str
    customer_id: str
    customer: CustomerResponse


class CustomerListResponse(BaseModel):
    """顧客一覧レスポンススキーマ"""
    total: int
    limit: int
    offset: int
    items: list[CustomerResponse]
