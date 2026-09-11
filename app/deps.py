"""
依存性注入（Dependency Injection）
FastAPI の Depends で使用
"""

from typing import Optional, Any
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.security import verify_token
import app.database as db_module
from app.models.user import User, Tenant
from app.schemas.user import CurrentUser


# セキュリティスキーム
security = HTTPBearer()


async def get_db() -> AsyncSession:
    """データベースセッション を取得"""
    if db_module.AsyncSessionLocal is None:
        raise RuntimeError("Database not initialized")
    async with db_module.AsyncSessionLocal() as session:
        yield session


async def get_current_user(
    credentials: Any = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    """
    現在のユーザー情報を取得（JWT トークンから）

    Raises:
        HTTPException: トークンが無効な場合
    """
    settings = get_settings()
    token = credentials.credentials

    # トークンを検証
    token_data = verify_token(token, settings.SECRET_KEY, settings.ALGORITHM)
    if not token_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # ユーザーをデータベースから取得
    user = await db.get(User, token_data.sub)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # ユーザーが有効か確認
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user",
        )

    # テナント ID の一致を確認
    if user.tenant_id != token_data.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant mismatch",
        )

    return CurrentUser(
        id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        display_name=user.display_name,
        is_admin=user.is_admin,
    )


async def get_current_admin(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """
    現在のユーザーが管理者であることを確認

    Raises:
        HTTPException: 管理者でない場合
    """
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privilege required",
        )

    return current_user


async def get_current_tenant(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Tenant:
    """
    現在のユーザーのテナント情報を取得

    Raises:
        HTTPException: テナントが見つからない場合
    """
    tenant = await db.get(Tenant, current_user.tenant_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    if not tenant.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant is inactive",
        )

    return tenant


async def get_optional_current_user(
    credentials: Optional[Any] = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> Optional[CurrentUser]:
    """
    現在のユーザー情報を取得（オプション - トークンがない場合は None）
    """
    if not credentials:
        return None

    settings = get_settings()
    token = credentials.credentials

    # トークンを検証
    token_data = verify_token(token, settings.SECRET_KEY, settings.ALGORITHM)
    if not token_data:
        return None

    # ユーザーをデータベースから取得
    user = await db.get(User, token_data.sub)
    if not user or not user.is_active:
        return None

    return CurrentUser(
        id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        display_name=user.display_name,
        is_admin=user.is_admin,
    )
