"""
Reservation (予約) スキーマ定義
リクエスト/レスポンスのPydantic モデル
"""

from datetime import datetime, date as date_type
from typing import Optional, List
from pydantic import BaseModel, Field, EmailStr


class ReservationCreateRequest(BaseModel):
    """予約作成リクエストスキーマ（ゲスト予約・会員登録不要）"""
    shop_id: str = Field(..., description="店舗ID")
    reservation_date: datetime = Field(..., description="予約日時")
    number_of_people: int = Field(1, ge=1, le=999, description="人数（美容院・クリニックなどサービス単位の予約では省略可、既定値1）")
    service_id: Optional[str] = Field(None, description="サービスID（美容院・クリニック・スクール・フィットネスなど、サービス単位で予約する業種の場合に指定）")
    staff_id: Optional[str] = Field(None, description="スタッフ指名がある場合に指定（省略時は指名なし・お任せ）")
    guest_name: str = Field(..., min_length=1, max_length=255, description="予約者名")
    guest_phone: str = Field(..., min_length=1, max_length=20, description="連絡先電話番号")
    guest_email: Optional[EmailStr] = Field(None, description="連絡先メールアドレス")
    special_requests: Optional[str] = Field(None, max_length=500, description="特別リクエスト")


class ReservationUpdateRequest(BaseModel):
    """予約更新リクエストスキーマ（オーナー操作用）"""
    status: Optional[str] = Field(None, description="ステータス (pending, confirmed, cancelled, no_show, completed)")
    cancellation_reason: Optional[str] = Field(None, max_length=500, description="キャンセル理由")
    number_of_people: Optional[int] = Field(None, ge=1, le=999, description="人数")
    special_requests: Optional[str] = Field(None, max_length=500, description="特別リクエスト")
    reservation_date: Optional[datetime] = Field(None, description="予約日時の変更（お客様との調整後にオーナーが日時を変更する場合）")


class ReservationResponse(BaseModel):
    """予約レスポンススキーマ"""
    id: str
    shop_id: str
    customer_id: Optional[str] = None
    table_id: Optional[str] = None
    table_name: Optional[str] = None
    service_id: Optional[str] = None
    service_name: Optional[str] = None
    staff_id: Optional[str] = None
    staff_name: Optional[str] = None
    guest_name: Optional[str] = None
    guest_phone: Optional[str] = None
    guest_email: Optional[str] = None
    reservation_date: datetime
    number_of_people: int
    status: str
    special_requests: Optional[str]
    cancelled_at: Optional[datetime]
    cancellation_reason: Optional[str]
    arrived_at: Optional[datetime]
    total_price: Optional[int] = None
    payment_status: Optional[str] = None
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
    items: List[ReservationResponse]


class AvailabilitySlot(BaseModel):
    """空き状況の1枠"""
    time: str = Field(..., description="開始時刻（HH:MM）")
    available: bool


class AvailabilityResponse(BaseModel):
    """空き状況レスポンス"""
    shop_id: str
    date: str
    party_size: int
    service_id: Optional[str] = None
    staff_id: Optional[str] = None
    is_open: bool
    message: Optional[str] = None
    slots: List[AvailabilitySlot] = []
