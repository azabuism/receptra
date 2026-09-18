"""
BARIYON Receptra - FastAPI Main Application
24時間対応のAI受付プラットフォーム
"""

import logging
import os
import pathlib
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.database import init_db
from app.models.user import Base
from app.models import receptionist, visitor
from app.routers.users import router as users_router
from app.routers.receptionists import router as receptionists_router
from app.routers.auth import router as auth_router
from app.routers.vonage_voice import router as vonage_voice_router
from app.routers.visitors import router as visitors_router
from app.routers.shops import router as shops_router
from app.routers.reservations import router as reservations_router
from app.routers.customers import router as customers_router

logger = logging.getLogger(__name__)
settings = get_settings()


def _normalize_database_url(database_url: str) -> str:
    """Railway 等が発行する DATABASE_URL は postgres:// / postgresql:// 形式のことが多いが、
    asyncpg ドライバーを使うには postgresql+asyncpg:// である必要があるため補正する。"""
    if database_url.startswith("postgres://"):
        return "postgresql+asyncpg://" + database_url[len("postgres://"):]
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql://"):]
    return database_url


@asynccontextmanager
async def lifespan(app: FastAPI):
    """アプリケーションのライフサイクル管理"""
    logger.info("🚀 BARIYON Receptra API starting up...")

    app.state.db_ready = False
    app.state.db_error = None

    try:
        database_url = _normalize_database_url(settings.DATABASE_URL)
        engine = create_async_engine(
            database_url,
            echo=settings.ENVIRONMENT == "development",
            future=True,
        )

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        logger.info("✅ Database tables initialized")
        await engine.dispose()

        init_db(database_url)
        app.state.db_ready = True
        logger.info("✅ Database session factory initialized")
    except Exception as e:
        app.state.db_error = f"{type(e).__name__}: {e}"
        logger.error(f"❌ Database initialization failed: {e}")

    yield

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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS if hasattr(settings, 'CORS_ORIGINS') else ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
    app.include_router(users_router, prefix="/api/v1/users", tags=["users"])
    app.include_router(receptionists_router, prefix="/api/v1/receptionists", tags=["receptionists"])
    app.include_router(visitors_router, prefix="/api/v1/visitors", tags=["visitors"])
    app.include_router(shops_router)
    app.include_router(reservations_router)
    app.include_router(customers_router)
    app.include_router(vonage_voice_router)

    # 静的ファイル配信設定 - /receptra/ ルート
    base_dir = pathlib.Path(__file__).parent.parent
    frontend_path = base_dir / "frontend" / "dist"

    logger.info(f"🔍 Looking for frontend at: {frontend_path}")
    logger.info(f"📁 Path exists: {frontend_path.exists()}")

    if frontend_path.exists():
        app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")
        logger.info(f"✅ Static files mounted at / from {frontend_path}")
    else:
        logger.error(f"❌ Frontend directory not found at {frontend_path}")

    @app.get("/health", tags=["health"])
    async def health_check():
        """ヘルスチェック"""
        return {
            "status": "ok",
            "service": "BARIYON Receptra API",
            "version": "1.0.0",
            "environment": settings.ENVIRONMENT,
            "database": {
                "ready": getattr(app.state, "db_ready", False),
                "error": getattr(app.state, "db_error", None),
            },
        }

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


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
