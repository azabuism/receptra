"""
ユーザー・テナント スキーマ（Pydantic）
リクエスト/レスポンス定義
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


# ===== Tenant スキーマ =====
class TenantBase(BaseModel):
    """テナント基本情報"""

    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=255, pattern="^[a-z0-9-]+$")
    email: EmailStr


class TenantCreate(TenantBase):
    """テナント作成リクエスト"""

    pass


class TenantUpdate(BaseModel):
    """テナント更新リクエスト"""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    email: Optional[EmailStr] = None


class TenantResponse(TenantBase):
    """テナント レスポンス"""

    id: str
    is_active: bool
    is_trial: bool
    trial_ends_at: Optional[datetime] = None
    subscription_status: str
    total_users: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ===== User スキーマ =====
class UserBase(BaseModel):
    """ユーザー基本情報"""

    email: EmailStr
    display_name: str = Field(..., min_length=1, max_length=255)


class UserCreate(UserBase):
    """ユーザー作成リクエスト（新規登録）"""

    password: str = Field(..., min_length=8)
    tenant_slug: Optional[str] = None  # 既存テナントに追加する場合


class UserLogin(BaseModel):
    """ユーザーログイン リクエスト"""

    email: EmailStr
    password: str
    tenant_slug: Optional[str] = None  # マルチテナント対応


class UserUpdate(BaseModel):
    """ユーザー更新リクエスト"""

    display_name: Optional[str] = Field(None, min_length=1, max_length=255)
    avatar_url: Optional[str] = None
    bio: Optional[str] = None


class UserResponse(UserBase):
    """ユーザー レスポンス"""

    id: str
    tenant_id: str
    is_active: bool
    is_admin: bool
    is_verified: bool
    avatar_url: Optional[str] = None
    bio: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UserDetailResponse(UserResponse):
    """ユーザー詳細 レスポンス（テナント情報含む）"""

    tenant: TenantResponse


# ===== 認証関連 スキーマ =====
class TokenResponse(BaseModel):
    """トークン レスポンス"""

    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class CurrentUser(BaseModel):
    """現在のユーザー情報（依存性注入用）"""

    id: str
    tenant_id: str
    email: str
    display_name: str
    is_admin: bool
