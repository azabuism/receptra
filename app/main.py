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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.database import init_db
from app.models.user import Base
from app.models import receptionist, visitor, voice_call_log
from app.routers.users import router as users_router
from app.routers.receptionists import router as receptionists_router
from app.routers.auth import router as auth_router
from app.routers.vonage_voice import router as vonage_voice_router, test_router as voice_ai_test_router
from app.routers.shop_booking_ai import router as shop_booking_ai_router
from app.routers.realtime_voice import router as realtime_voice_router
from app.routers.ai_staff_settings import router as ai_staff_settings_router
from app.routers.visitors import router as visitors_router
from app.routers.shops import router as shops_router
from app.routers.shop_media import router as shop_media_router, media_router as shop_media_files_router
from app.routers.reservations import router as reservations_router
from app.routers.customers import router as customers_router
from app.routers.shop_tables import router as shop_tables_router
from app.routers.shop_closures import router as shop_closures_router
from app.routers.shop_images import router as shop_images_router, media_router as shop_images_files_router
from app.routers.taxonomy import router as taxonomy_router
from app.routers.reviews import router as reviews_router
from app.routers.mypage import router as mypage_router
from app.routers.coupons import router as coupons_router
from app.routers.services import router as services_router
from app.routers.staff import router as staff_router
from app.routers.billing import router as billing_router, webhook_router as payjp_webhook_router

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

            # create_all は既存テーブルへの新規カラム追加は行わないため、
            # 後から追加したカラムはここで個別に ALTER する。
            try:
                await conn.execute(text("ALTER TABLE shops ADD COLUMN IF NOT EXISTS features JSON"))
            except Exception as alter_err:
                logger.warning(f"⚠️ features カラムの追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

            try:
                await conn.execute(text("ALTER TABLE shops ADD COLUMN IF NOT EXISTS reservation_duration_minutes INTEGER DEFAULT 90"))
            except Exception as alter_err:
                logger.warning(f"⚠️ reservation_duration_minutes カラムの追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

            # 予約テーブル: ゲスト予約対応・テーブル割り当て対応のためのカラム追加
            try:
                await conn.execute(text("ALTER TABLE reservations ALTER COLUMN customer_id DROP NOT NULL"))
            except Exception as alter_err:
                logger.warning(f"⚠️ customer_id の NOT NULL 解除に失敗（既に解除済みの場合は無視して問題ありません）: {alter_err}")
            try:
                await conn.execute(text("ALTER TABLE reservations ADD COLUMN IF NOT EXISTS guest_name VARCHAR(255)"))
                await conn.execute(text("ALTER TABLE reservations ADD COLUMN IF NOT EXISTS guest_phone VARCHAR(20)"))
                await conn.execute(text("ALTER TABLE reservations ADD COLUMN IF NOT EXISTS guest_email VARCHAR(255)"))
                await conn.execute(text("ALTER TABLE reservations ADD COLUMN IF NOT EXISTS table_id VARCHAR(36)"))
            except Exception as alter_err:
                logger.warning(f"⚠️ reservations のゲスト予約用カラム追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

            # 店舗のロゴ・サムネイル・カバー画像（アップロード対応）
            try:
                await conn.execute(text("ALTER TABLE shops ADD COLUMN IF NOT EXISTS logo_data BYTEA"))
                await conn.execute(text("ALTER TABLE shops ADD COLUMN IF NOT EXISTS logo_mime_type VARCHAR(100)"))
                await conn.execute(text("ALTER TABLE shops ADD COLUMN IF NOT EXISTS thumbnail_data BYTEA"))
                await conn.execute(text("ALTER TABLE shops ADD COLUMN IF NOT EXISTS thumbnail_mime_type VARCHAR(100)"))
                await conn.execute(text("ALTER TABLE shops ADD COLUMN IF NOT EXISTS cover_data BYTEA"))
                await conn.execute(text("ALTER TABLE shops ADD COLUMN IF NOT EXISTS cover_mime_type VARCHAR(100)"))
            except Exception as alter_err:
                logger.warning(f"⚠️ shops の画像用カラム追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

            # 業種（business_type）カラム：カテゴリーのタクソノミー導入に伴う追加
            try:
                await conn.execute(text("ALTER TABLE shops ADD COLUMN IF NOT EXISTS business_type VARCHAR(50)"))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_shops_business_type ON shops (business_type)"))
            except Exception as alter_err:
                logger.warning(f"⚠️ business_type カラムの追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

            # マイページ機能: 予約・レビューをログイン中のプラットフォームアカウントに紐付けるための user_id カラム
            try:
                await conn.execute(text("ALTER TABLE reservations ADD COLUMN IF NOT EXISTS user_id VARCHAR(36)"))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_reservations_user ON reservations (user_id)"))
            except Exception as alter_err:
                logger.warning(f"⚠️ reservations.user_id カラムの追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

            # 予約システム作り込み: サービス単位（美容院・クリニックなど）の予約に対応するための service_id カラム
            try:
                await conn.execute(text("ALTER TABLE reservations ADD COLUMN IF NOT EXISTS service_id VARCHAR(36)"))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_reservations_service ON reservations (service_id)"))
            except Exception as alter_err:
                logger.warning(f"⚠️ reservations.service_id カラムの追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

            # 予約システム作り込み: 予約時のクーポン適用（coupon_id・discount_amount）に対応するためのカラム
            try:
                await conn.execute(text("ALTER TABLE reservations ADD COLUMN IF NOT EXISTS coupon_id VARCHAR(36)"))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_reservations_coupon ON reservations (coupon_id)"))
                await conn.execute(text("ALTER TABLE reservations ADD COLUMN IF NOT EXISTS discount_amount INTEGER"))
            except Exception as alter_err:
                logger.warning(f"⚠️ reservations.coupon_id/discount_amount カラムの追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

            try:
                await conn.execute(text("ALTER TABLE reviews ALTER COLUMN customer_id DROP NOT NULL"))
            except Exception as alter_err:
                logger.warning(f"⚠️ reviews.customer_id の NOT NULL 解除に失敗（既に解除済みの場合は無視して問題ありません）: {alter_err}")

            try:
                await conn.execute(text("ALTER TABLE reviews ADD COLUMN IF NOT EXISTS user_id VARCHAR(36)"))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_reviews_user ON reviews (user_id)"))
            except Exception as alter_err:
                logger.warning(f"⚠️ reviews.user_id カラムの追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

            # PAY.jp課金連携: テナントの課金情報カラム
            try:
                await conn.execute(text("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS payjp_customer_id VARCHAR(255)"))
                await conn.execute(text("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS payjp_subscription_id VARCHAR(255)"))
                await conn.execute(text("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS card_brand VARCHAR(50)"))
                await conn.execute(text("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS card_last4 VARCHAR(4)"))
                await conn.execute(text("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS setup_fee_paid_at TIMESTAMP"))
            except Exception as alter_err:
                logger.warning(f"⚠️ tenants の課金用カラム追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

            # PAY.jp課金連携: 予約受付ON/OFFカラム。既存店舗は既にサービス提供中のため
            # デフォルトTrueで追加して維持し、以後の新規登録店舗はアプリ側でFalseを明示的にセットする。
            try:
                await conn.execute(text("ALTER TABLE shops ADD COLUMN IF NOT EXISTS reservations_enabled BOOLEAN NOT NULL DEFAULT true"))
            except Exception as alter_err:
                logger.warning(f"⚠️ shops.reservations_enabled カラムの追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

            # Phase3B: Realtime Voice経由のcreate_reservation専用の冪等性キー。
            # 既存のWeb予約・チャット予約・管理画面予約は一切これを使用せず常にNULLのまま
            # （create_reservation()の新しい分岐はidempotency_keyが指定された場合のみ通る）。
            # PostgreSQLの一意インデックスはNULL同士を重複とみなさないため、既存の全予約行
            # （このカラム追加時点では必ずNULL）には一切影響を与えずに追加できる。
            # 同一キーでの同時多重INSERTをDBレベルで確実に1件だけに絞るための最終防衛線として、
            # アプリケーション側のSELECTだけに頼らずCREATE UNIQUE INDEXを必ず張る。
            try:
                await conn.execute(text("ALTER TABLE reservations ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(200)"))
                await conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ux_reservations_idempotency_key "
                    "ON reservations (idempotency_key)"
                ))
            except Exception as alter_err:
                logger.warning(f"⚠️ reservations.idempotency_key カラム/一意インデックスの追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

            # 既存店舗（旧カテゴリー体系で登録されたもの）を新しいタクソノミーに移行する
            try:
                from app.taxonomy import LEGACY_CATEGORY_MAP
                for old_value, (business_type, new_category) in LEGACY_CATEGORY_MAP.items():
                    await conn.execute(
                        text(
                            "UPDATE shops SET business_type = :business_type, category = :new_category "
                            "WHERE business_type IS NULL AND category = :old_value"
                        ),
                        {"business_type": business_type, "new_category": new_category, "old_value": old_value},
                    )
            except Exception as migrate_err:
                logger.warning(f"⚠️ 既存店舗のカテゴリー移行に失敗しました: {migrate_err}")

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
    app.include_router(shop_media_router)
    app.include_router(shop_media_files_router)
    app.include_router(reservations_router)
    app.include_router(customers_router)
    app.include_router(shop_tables_router)
    app.include_router(shop_closures_router)
    app.include_router(shop_images_router)
    app.include_router(shop_images_files_router)
    app.include_router(taxonomy_router)
    app.include_router(reviews_router)
    app.include_router(mypage_router)
    app.include_router(coupons_router)
    app.include_router(services_router)
    app.include_router(staff_router)
    app.include_router(vonage_voice_router)
    app.include_router(voice_ai_test_router)
    app.include_router(shop_booking_ai_router)
    app.include_router(realtime_voice_router)
    app.include_router(ai_staff_settings_router)
    app.include_router(billing_router)
    app.include_router(payjp_webhook_router)

    @app.get("/api/v1/debug/db-status", tags=["debug"])
    async def debug_db_status():
        """DB初期化状況の診断用エンドポイント（静的ファイルマウントより前に登録して確実に到達可能にする）"""
        return {
            "ready": getattr(app.state, "db_ready", False),
            "error": getattr(app.state, "db_error", None),
        }

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
