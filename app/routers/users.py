"""
ユーザー管理エンドポイント
GET /users, /users/{id}, PUT /users/{id}, DELETE /users/{id}
"""

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.deps import get_db, get_current_user
from app.models.user import User
from app.schemas.user import UserResponse, UserUpdate, CurrentUser

router = APIRouter()


@router.get("", response_model=List[UserResponse])
async def list_users(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    テナント内のすべてのアクティブユーザーを取得
    """
    stmt = select(User).where(
        User.tenant_id == current_user.tenant_id,
        User.is_active == True,
    )
    result = await db.execute(stmt)
    users = result.scalars().all()
    return users


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    特定のユーザー情報を取得
    テナント内のみアクセス可能
    """
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # テナント検証
    if user.tenant_id != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access user from different tenant",
        )

    return user


@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    user_update: UserUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    ユーザープロフィール情報を更新
    本人またはテナント管理者のみ可能
    """
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # テナント検証
    if user.tenant_id != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access user from different tenant",
        )

    # 権限検証（本人またはテナント管理者）
    if user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied",
        )

    # プロフィール更新
    if user_update.display_name is not None:
        user.display_name = user_update.display_name
    if user_update.avatar_url is not None:
        user.avatar_url = user_update.avatar_url
    if user_update.bio is not None:
        user.bio = user_update.bio

    db.add(user)
    await db.commit()
    await db.refresh(user)

    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    ユーザーを論理削除（is_active = False）
    本人またはテナント管理者のみ可能
    """
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # テナント検証
    if user.tenant_id != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access user from different tenant",
        )

    # 権限検証（本人またはテナント管理者）
    if user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied",
        )

    # 論理削除
    user.is_active = False
    db.add(user)
    await db.commit()
