"""
BARIYON Receptra - FastAPI Main Application
24時間対応のAI受付プラットフォーム
"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.database import init_db
from app.models.user import Base
from app.models import receptionist, visitor  # SQLAlchemy base for metadata
from app.routers.users import router as users_router
from app.routers.receptionists import router as receptionists_router
from app.routers.auth import router as auth_router
from app.routers.visitors import router as visitors_router
from app.routers.shops import router as shops_router
from app.routers.reservations import router as reservations_router
from app.routers.customers import router as customers_router

logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """アプリケーションのライフサイクル管理"""
    # Startup
    logger.info("🚀 BARIYON Receptra API starting up...")

    # Database initialization
    try:
        database_url = settings.DATABASE_URL
        engine = create_async_engine(
            database_url,
            echo=settings.ENVIRONMENT == "development",
            future=True,
        )

        # Create all tables
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        logger.info("✅ Database tables initialized")
        await engine.dispose()

        # Initialize AsyncSessionLocal for dependency injection
        init_db(database_url)
        logger.info("✅ Database session factory initialized")
    except Exception as e:
        logger.error(f"❌ Database initialization failed: {e}")

    yield

    # Shutdown
    logger.info("🛑 BARIYON Receptra API shutting down...")


def create_app() -> FastAPI:
    """FastAPI アプリケーションファクトリー"""
    app = FastAPI(
        title="BARIYON Receptra API",
        description="24時間対応のAI受付プラットフォーム",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # CORS設定
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS if hasattr(settings, 'CORS_ORIGINS') else ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ルーター登録
    app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
    app.include_router(users_router, prefix="/api/v1/users", tags=["users"])
    app.include_router(receptionists_router, prefix="/api/v1/receptionists", tags=["receptionists"])
    app.include_router(visitors_router, prefix="/api/v1/visitors", tags=["visitors"])
    app.include_router(shops_router)  # shops_router has its own prefix and tags
    app.include_router(reservations_router)  # reservations_router has its own prefix and tags
    app.include_router(customers_router)  # customers_router has its own prefix and tags

    # ヘルスチェック
    @app.get("/health", tags=["health"])
    async def health_check():
        """ヘルスチェック"""
        return {
            "status": "ok",
            "service": "BARIYON Receptra API",
            "version": "1.0.0",
            "environment": settings.ENVIRONMENT,
        }

    # ルートエンドポイント
    @app.get("/", tags=["root"])
    async def root():
        """ルートエンドポイント"""
        return {
            "name": "BARIYON Receptra API",
            "description": "24時間対応のAI受付プラットフォーム",
            "version": "1.0.0",
            "docs": "/docs",
        }

    logger.info("✅ FastAPI application created successfully")
    return app


# アプリケーションインスタンス
app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
