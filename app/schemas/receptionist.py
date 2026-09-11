"""
受付スタッフスキーマ
"""

from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field, EmailStr


class ReceptionistBase(BaseModel):
    """受付スタッフ基本スキーマ"""

    name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    phone: Optional[str] = Field(None, max_length=20)
    role: str = Field(default="receptionist", pattern="^(receptionist|manager)$")
    shift: str = Field(default="all-day", pattern="^(morning|afternoon|night|all-day)$")


class ReceptionistCreate(ReceptionistBase):
    """受付スタッフ作成リクエスト"""

    pass


class ReceptionistUpdate(BaseModel):
    """受付スタッフ更新リクエスト"""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    phone: Optional[str] = Field(None, max_length=20)
    role: Optional[str] = Field(None, pattern="^(receptionist|manager)$")
    shift: Optional[str] = Field(None, pattern="^(morning|afternoon|night|all-day)$")


class ReceptionistResponse(ReceptionistBase):
    """受付スタッフレスポンス"""

    id: str
    tenant_id: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
