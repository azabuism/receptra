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
    coupon_code: Optional[str] = Field(None, max_length=50, description="クーポンコード（サービス単位の予約でのみ利用可能）")
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
    coupon_id: Optional[str] = None
    coupon_code: Optional[str] = None
    discount_amount: Optional[int] = None
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


# ===== Realtime Voice AI Phase3A: check_availability Tool Calling用 =====
#
# 設計方針（重要）:
# - shop_id をここに含めない。Realtime AI（OpenAI側）が呼び出すTool引数には
#   日付・時刻・人数・（任意で）service_id/staff_idのみを渡し、通話中の
#   店舗のshop_idはRECEPTRAサーバー側がURLパス（/shops/{shop_id}/...）から
#   決定する。AIに他店舗のshop_idを自由に指定させる余地を作らないため。
# - レスポンスは意図的に最小限。availableがfalseの場合のみ、AIが次の案内を
#   判断できるよう短い機械可読な reason_code を付与する（自然言語の理由は
#   ここでは返さない。文言はAI側のinstructions/toolの説明文に任せる）。

class CheckAvailabilityRequest(BaseModel):
    """Realtime AIのcheck_availability Toolからの引数"""
    date: str = Field(..., description="日付（YYYY-MM-DD）")
    time: str = Field(..., description="時刻（HH:MM、24時間表記）")
    party_size: int = Field(1, ge=1, le=999, description="人数")
    service_id: Optional[str] = Field(None, description="サービスID（美容院・クリニック等、サービス単位で予約する業種の場合のみ）")
    staff_id: Optional[str] = Field(None, description="スタッフ指名がある場合のみ")


class CheckAvailabilityResponse(BaseModel):
    """Realtime AIへ返す最小レスポンス（DBへの保存は行わない）"""
    available: bool
    date: str
    time: str
    party_size: int
    # available=False の場合のみ設定。候補:
    # fully_booked / outside_business_hours / shop_closed / temporary_closure /
    # service_unavailable / staff_unavailable / invalid_request
    # invalid_request は「入力不正」だけでなく「バックエンド側で予約状況を
    # 確定できなかった（内部エラー等）」場合の安全側フォールバックとしても
    # 使う。AI側の説明文で、この場合は満席と案内せず確認できなかった旨を
    # 案内するよう指示する。
    reason_code: Optional[str] = None
