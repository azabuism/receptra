"""
Staff (スタッフ/講師) スキーマ定義
美容院のスタイリスト、スクールの講師、クリニックの担当医など
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, EmailStr


class StaffCreateRequest(BaseModel):
    """スタッフ登録リクエスト"""
    shop_id: str = Field(..., description="店舗ID")
    name: str = Field(..., min_length=1, max_length=255, description="氏名")
    email: Optional[EmailStr] = Field(None, description="連絡先メールアドレス")
    phone: Optional[str] = Field(None, max_length=20, description="連絡先電話番号")
    bio: Optional[str] = Field(None, max_length=2000, description="自己紹介")
    photo_url: Optional[str] = Field(None, max_length=500, description="プロフィール写真URL")
    specialty: Optional[str] = Field(None, max_length=255, description="専門分野")
    qualifications: Optional[str] = Field(None, max_length=1000, description="資格・経歴")
    position: Optional[str] = Field(None, max_length=100, description="職位")


class StaffUpdateRequest(BaseModel):
    """スタッフ更新リクエスト（送られたフィールドのみ更新）"""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=20)
    bio: Optional[str] = Field(None, max_length=2000)
    photo_url: Optional[str] = Field(None, max_length=500)
    specialty: Optional[str] = Field(None, max_length=255)
    qualifications: Optional[str] = Field(None, max_length=1000)
    position: Optional[str] = Field(None, max_length=100)
    is_active: Optional[bool] = None


class StaffResponse(BaseModel):
    """スタッフレスポンス"""
    id: str
    shop_id: str
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    bio: Optional[str] = None
    photo_url: Optional[str] = None
    specialty: Optional[str] = None
    qualifications: Optional[str] = None
    position: Optional[str] = None
    is_active: bool
    total_reservations: int
    average_rating: int
    created_at: datetime

    class Config:
        from_attributes = True


class StaffServiceAssignmentResponse(BaseModel):
    """スタッフが提供するサービスの割り当て情報"""
    service_id: str
    service_name: str
    base_price: float
    duration_minutes: Optional[int] = None
