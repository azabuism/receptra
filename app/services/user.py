"""
ユーザー・テナント サービス
ビジネスロジック層
"""

from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.security import hash_password, verify_password
from app.models.user import User, Tenant


class TenantService:
    """テナント管理サービス"""

    @staticmethod
    async def create_tenant(
        db: AsyncSession,
        name: str,
        slug: str,
        email: str,
    ) -> Tenant:
        """新しいテナントを作成"""
        tenant = Tenant(
            name=name,
            slug=slug,
            email=email,
            is_active=True,
            is_trial=True,
        )
        db.add(tenant)
        await db.flush()
        await db.refresh(tenant)
        return tenant

    @staticmethod
    async def get_tenant_by_slug(db: AsyncSession, slug: str) -> Optional[Tenant]:
        """slug でテナントを取得"""
        result = await db.execute(
            select(Tenant).where(Tenant.slug == slug)
        )
        return result.scalars().first()

    @staticmethod
    async def get_tenant_by_id(db: AsyncSession, tenant_id: str) -> Optional[Tenant]:
        """ID でテナントを取得"""
        result = await db.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        return result.scalars().first()


class UserService:
    """ユーザー管理サービス"""

    @staticmethod
    async def create_user(
        db: AsyncSession,
        tenant_id: str,
        email: str,
        display_name: str,
        password: str,
        is_admin: bool = False,
    ) -> Optional[User]:
        """新しいユーザーを作成"""
        # メールアドレスが既に存在するか確認
        existing_user = await UserService.get_user_by_email(db, tenant_id, email)
        if existing_user:
            return None

        # ユーザーを作成
        user = User(
            tenant_id=tenant_id,
            email=email,
            display_name=display_name,
            password_hash=hash_password(password),
            is_active=True,
            is_admin=is_admin,
            is_verified=False,
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
        return user

    @staticmethod
    async def get_user_by_id(db: AsyncSession, user_id: str) -> Optional[User]:
        """ID でユーザーを取得"""
        result = await db.execute(
            select(User).where(User.id == user_id)
        )
        return result.scalars().first()

    @staticmethod
    async def get_user_by_email(
        db: AsyncSession,
        tenant_id: str,
        email: str,
    ) -> Optional[User]:
        """テナント内でメールアドレスからユーザーを取得"""
        result = await db.execute(
            select(User).where(
                User.tenant_id == tenant_id,
                User.email == email,
            )
        )
        return result.scalars().first()

    @staticmethod
    async def authenticate_user(
        db: AsyncSession,
        tenant_id: str,
        email: str,
        password: str,
    ) -> Optional[User]:
        """ユーザーを認証（メール+パスワード）"""
        user = await UserService.get_user_by_email(db, tenant_id, email)
        if not user:
            return None

        if not verify_password(password, user.password_hash):
            return None

        if not user.is_active:
            return None

        return user

    @staticmethod
    async def update_user(
        db: AsyncSession,
        user_id: str,
        display_name: Optional[str] = None,
        avatar_url: Optional[str] = None,
        bio: Optional[str] = None,
    ) -> Optional[User]:
        """ユーザーを更新"""
        user = await UserService.get_user_by_id(db, user_id)
        if not user:
            return None

        if display_name is not None:
            user.display_name = display_name
        if avatar_url is not None:
            user.avatar_url = avatar_url
        if bio is not None:
            user.bio = bio

        await db.flush()
        await db.refresh(user)
        return user

    @staticmethod
    async def get_tenant_users(
        db: AsyncSession,
        tenant_id: str,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[User], int]:
        """テナントのユーザー一覧を取得"""
        query = select(User).where(User.tenant_id == tenant_id)

        # 総数を取得
        total_result = await db.execute(
            select(func.count()).select_from(User).where(User.tenant_id == tenant_id)
        )
        total = total_result.scalars().first() or 0

        # ページネーション
        query = query.offset(skip).limit(limit)
        result = await db.execute(query)
        users = result.scalars().all()

        return users, total
