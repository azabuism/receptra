"""
来訪者スキーマ
"""

from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field, EmailStr


class VisitorBase(BaseModel):
    """来訪者基本スキーマ"""

    name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    phone: Optional[str] = Field(None, max_length=20)
    company: Optional[str] = Field(None, max_length=255)
    purpose: str = Field(..., min_length=1, max_length=500)
    host_id: str


class VisitorCreate(VisitorBase):
    """来訪者作成リクエスト"""

    pass


class VisitorUpdate(BaseModel):
    """来訪者更新リクエスト"""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    phone: Optional[str] = Field(None, max_length=20)
    company: Optional[str] = Field(None, max_length=255)
    purpose: Optional[str] = Field(None, min_length=1, max_length=500)
    host_id: Optional[str] = None


class VisitorResponse(VisitorBase):
    """来訪者レスポンス"""

    id: str
    tenant_id: str
    status: str
    check_in_at: Optional[datetime] = None
    check_out_at: Optional[datetime] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
