"""
認証 ルーター
/api/v1/auth エンドポイント
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.security import create_access_token
from app.database import AsyncSessionLocal
from app.models.user import Tenant, User
from app.schemas.user import (
    TenantCreate,
    UserCreate,
    UserLogin,
    UserResponse,
    TokenResponse,
    TenantResponse,
    UserDetailResponse,
)
from app.services.user import TenantService, UserService
from app.deps import get_current_user, get_db


router = APIRouter()
settings = get_settings()


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(
    user_create: UserCreate,
    db: AsyncSession = Depends(get_db),
):
    """
    新しいテナント と ユーザーを登録

    - 新規テナント作成（tenant_slug が指定されない場合）
    - または既存テナントにユーザーを追加（tenant_slug が指定される場合）
    """
    # tenant_slug が指定されている場合、既存テナントに追加
    if user_create.tenant_slug:
        tenant = await TenantService.get_tenant_by_slug(
            db, user_create.tenant_slug
        )
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tenant '{user_create.tenant_slug}' not found",
            )
    else:
        # 新しいテナントを作成
        # tenant_slug をメールアドレスのローカル部から生成
        email_local = user_create.email.split("@")[0]
        slug = email_local.lower().replace(".", "-")

        # 既存テナント確認
        existing_tenant = await TenantService.get_tenant_by_slug(db, slug)
        if existing_tenant:
            slug = f"{slug}-{int(db.generate_uuid())[:8]}"

        tenant = await TenantService.create_tenant(
            db=db,
            name=f"{user_create.display_name}'s Workspace",
            slug=slug,
            email=user_create.email,
        )

    # ユーザーを作成
    user = await UserService.create_user(
        db=db,
        tenant_id=tenant.id,
        email=user_create.email,
        display_name=user_create.display_name,
        password=user_create.password,
        is_admin=True,  # 最初のユーザーは管理者
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered in this tenant",
        )

    await db.commit()

    # トークンを生成
    access_token = create_access_token(
        user_id=user.id,
        tenant_id=tenant.id,
        secret_key=settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
        expires_in_hours=settings.ACCESS_TOKEN_EXPIRE_HOURS,
    )

    return TokenResponse(
        access_token=access_token,
        user=UserResponse.from_orm(user),
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    login_data: UserLogin,
    db: AsyncSession = Depends(get_db),
):
    """
    ユーザーログイン

    - tenant_slug が指定されない場合：メールアドレスから自動検出
    - tenant_slug が指定される場合：指定されたテナントで認証
    """
    # テナントを取得
    if login_data.tenant_slug:
        tenant = await TenantService.get_tenant_by_slug(db, login_data.tenant_slug)
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tenant '{login_data.tenant_slug}' not found",
            )
    else:
        # メールアドレスからテナントを検出
        # 注：簡略化のため、最初のテナントを使用
        # 実装では複数テナント対応が必要
        result = await db.execute(
            "SELECT DISTINCT tenant_id FROM users WHERE email = :email LIMIT 1",
            {"email": login_data.email},
        )
        tenant_id = result.scalar()
        if not tenant_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )
        tenant = await TenantService.get_tenant_by_id(db, tenant_id)

    # ユーザーを認証
    user = await UserService.authenticate_user(
        db=db,
        tenant_id=tenant.id,
        email=login_data.email,
        password=login_data.password,
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # トークンを生成
    access_token = create_access_token(
        user_id=user.id,
        tenant_id=tenant.id,
        secret_key=settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
        expires_in_hours=settings.ACCESS_TOKEN_EXPIRE_HOURS,
    )

    return TokenResponse(
        access_token=access_token,
        user=UserResponse.from_orm(user),
    )


@router.get("/me", response_model=UserDetailResponse)
async def get_current_user_info(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    現在のユーザー情報を取得
    """
    user = await UserService.get_user_by_id(db, current_user.id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return UserDetailResponse.from_orm(user)


@router.post("/logout")
async def logout():
    """
    ログアウト

    注：JWT は ステートレスなため、クライアント側でトークンを削除するだけで OK
    ここはプレースホルダー
    """
    return {"message": "Logged out successfully"}


@router.get("/health")
async def auth_health():
    """認証システムのヘルスチェック"""
    return {"status": "ok", "service": "auth"}
