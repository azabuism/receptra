"""
Reservation (予約) スキーマ定義
リクエスト/レスポンスのPydantic モデル
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class ReservationCreateRequest(BaseModel):
    """予約作成リクエストスキーマ"""
    shop_id: str = Field(..., description="店舗ID")
    customer_id: str = Field(..., description="顧客ID")
    reservation_date: datetime = Field(..., description="予約日時")
    number_of_people: int = Field(..., ge=1, le=999, description="人数")
    special_requests: Optional[str] = Field(None, max_length=500, description="特別リクエスト")


class ReservationUpdateRequest(BaseModel):
    """予約更新リクエストスキーマ"""
    status: Optional[str] = Field(None, description="ステータス (PENDING, CONFIRMED, CANCELLED, NO_SHOW, COMPLETED)")
    cancellation_reason: Optional[str] = Field(None, max_length=500, description="キャンセル理由")
    number_of_people: Optional[int] = Field(None, ge=1, le=999, description="人数")
    special_requests: Optional[str] = Field(None, max_length=500, description="特別リクエスト")


class ReservationResponse(BaseModel):
    """予約レスポンススキーマ"""
    id: str
    shop_id: str
    customer_id: str
    reservation_date: datetime
    number_of_people: int
    status: str
    special_requests: Optional[str]
    cancelled_at: Optional[datetime]
    cancellation_reason: Optional[str]
    arrived_at: Optional[datetime]
    table_number: Optional[str]
    reservation_source: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ReservationCreateResponse(BaseModel):
    """予約作成レスポンススキーマ"""
    success: bool
    message: str
    reservation_id: str
    reservation: ReservationResponse


class ReservationListResponse(BaseModel):
    """予約一覧レスポンススキーマ"""
    total: int
    limit: int
    offset: int
    items: list[ReservationResponse]
