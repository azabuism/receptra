"""
BARIYON Receptra - FastAPI Main Application
24時間対応のAI受付プラットフォーム
"""

import asyncio
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
from app.models import receptionist, visitor, voice_call_log, outbound_call
from app.routers.users import router as users_router
from app.routers.receptionists import router as receptionists_router
from app.routers.auth import router as auth_router
from app.routers.vonage_voice import router as vonage_voice_router, test_router as voice_ai_test_router
from app.routers.shop_booking_ai import router as shop_booking_ai_router
from app.routers.realtime_voice import router as realtime_voice_router
from app.routers.ai_staff_settings import router as ai_staff_settings_router
from app.routers.shop_knowledge import router as shop_knowledge_router
from app.routers.visitors import router as visitors_router
from app.routers.shops import router as shops_router
from app.routers.shop_media import router as shop_media_router, media_router as shop_media_files_router
from app.routers.reservations import router as reservations_router
from app.routers.customers import router as customers_router
from app.routers.shop_tables import router as shop_tables_router
from app.routers.shop_resources import router as shop_resources_router
from app.routers.shop_closures import router as shop_closures_router
from app.routers.shop_break_time import router as shop_break_time_router
from app.routers.shop_hours_override import router as shop_hours_override_router
from app.routers.shop_images import router as shop_images_router, media_router as shop_images_files_router
from app.routers.taxonomy import router as taxonomy_router
from app.routers.languages import router as languages_router
from app.routers.reviews import router as reviews_router
from app.routers.mypage import router as mypage_router
from app.routers.coupons import router as coupons_router
from app.routers.services import router as services_router
from app.routers.staff import router as staff_router
from app.routers.staff_shift import router as staff_shift_router
from app.routers.billing import router as billing_router, webhook_router as payjp_webhook_router
from app.routers.outbound_calls import router as outbound_calls_router
from app.routers.callback_requests import router as callback_requests_router
from app.routers.owner_notifications import router as owner_notifications_router
from app.routers.pre_orders import router as pre_orders_router, shop_pre_orders_router
from app.routers.pre_order_products import router as pre_order_products_router
from app.routers.line_integration import (
    line_connection_router,
    line_shop_settings_router,
    line_webhook_router,
)

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

            # Generic Resource Foundation Phase R2: ReservationとResourceの
            # 関連付け用カラム。既存の全予約行はNULLのまま（既存動作への影響なし）。
            # resource_idの実際の割当（availability integration）はPhase R3以降。
            try:
                await conn.execute(text("ALTER TABLE reservations ADD COLUMN IF NOT EXISTS resource_id VARCHAR(36)"))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_reservations_resource ON reservations (resource_id)"))
            except Exception as alter_err:
                logger.warning(f"⚠️ reservations.resource_id カラム/インデックスの追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

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

            # Phase3E-1: スタッフ基盤拡張（表示名・指名予約受付設定・表示順）
            # 既存staffテーブルへのカラム追加のみ。既存行は display_name=NULL（name にフォールバック）、
            # nomination_allowed=true（既存の挙動を変えない）、sort_order=0（既存の並び順を変えない）
            # のデフォルト値で埋まるため、既存データへの影響はない。
            # これらのカラムはAvailability判定・Realtime AIロジックのどこからも参照しない。
            try:
                await conn.execute(text("ALTER TABLE staff ADD COLUMN IF NOT EXISTS display_name VARCHAR(255)"))
                await conn.execute(text("ALTER TABLE staff ADD COLUMN IF NOT EXISTS nomination_allowed BOOLEAN NOT NULL DEFAULT true"))
                await conn.execute(text("ALTER TABLE staff ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0"))
            except Exception as alter_err:
                logger.warning(f"⚠️ staff の display_name/nomination_allowed/sort_order カラム追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

            # Phase3E-1: 店舗のスタッフシフト管理機能フラグ（将来のPhase 3E-3以降で使用。
            # 本フェーズではAvailability判定・管理画面UIのどこからも参照しない列追加のみ）。
            try:
                await conn.execute(text("ALTER TABLE shops ADD COLUMN IF NOT EXISTS staff_schedule_enabled BOOLEAN NOT NULL DEFAULT false"))
            except Exception as alter_err:
                logger.warning(f"⚠️ shops.staff_schedule_enabled カラムの追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

            # Phase3E-3: Zero-Wait Greeting音声のDBキャッシュ列。
            # 既存ai_staff_settingsテーブルへのカラム追加のみ。既存行はすべて
            # NULLで埋まり、次回このAPIが呼ばれた際に自動的に生成・キャッシュされる
            # （app.services.realtime_voice_ai.get_or_generate_greeting_audio()参照）。
            # Availability判定・予約ロジックには一切関係しない。
            try:
                await conn.execute(text("ALTER TABLE ai_staff_settings ADD COLUMN IF NOT EXISTS greeting_audio_data BYTEA"))
                await conn.execute(text("ALTER TABLE ai_staff_settings ADD COLUMN IF NOT EXISTS greeting_audio_content_type VARCHAR(50)"))
                await conn.execute(text("ALTER TABLE ai_staff_settings ADD COLUMN IF NOT EXISTS greeting_audio_fingerprint VARCHAR(64)"))
                await conn.execute(text("ALTER TABLE ai_staff_settings ADD COLUMN IF NOT EXISTS greeting_audio_generated_at TIMESTAMP"))
            except Exception as alter_err:
                logger.warning(f"⚠️ ai_staff_settings のGreeting音声キャッシュ用カラム追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

            # Phase3H Workstream B: shop_knowledge テーブルへ、支払い方法の
            # ブランド単位詳細カラムを追加。既存の大分類カラム（payment_cash等）
            # は一切変更しない。全て nullable のため、既存行はNULL（未設定）で
            # 埋まり、後方互換性に影響しない。
            try:
                await conn.execute(text("ALTER TABLE shop_knowledge ADD COLUMN IF NOT EXISTS payment_credit_visa VARCHAR(15)"))
                await conn.execute(text("ALTER TABLE shop_knowledge ADD COLUMN IF NOT EXISTS payment_credit_mastercard VARCHAR(15)"))
                await conn.execute(text("ALTER TABLE shop_knowledge ADD COLUMN IF NOT EXISTS payment_credit_jcb VARCHAR(15)"))
                await conn.execute(text("ALTER TABLE shop_knowledge ADD COLUMN IF NOT EXISTS payment_credit_amex VARCHAR(15)"))
                await conn.execute(text("ALTER TABLE shop_knowledge ADD COLUMN IF NOT EXISTS payment_credit_diners VARCHAR(15)"))
                await conn.execute(text("ALTER TABLE shop_knowledge ADD COLUMN IF NOT EXISTS payment_credit_other VARCHAR(15)"))
                await conn.execute(text("ALTER TABLE shop_knowledge ADD COLUMN IF NOT EXISTS payment_qr_paypay VARCHAR(15)"))
                await conn.execute(text("ALTER TABLE shop_knowledge ADD COLUMN IF NOT EXISTS payment_qr_au_pay VARCHAR(15)"))
                await conn.execute(text("ALTER TABLE shop_knowledge ADD COLUMN IF NOT EXISTS payment_qr_d_barai VARCHAR(15)"))
                await conn.execute(text("ALTER TABLE shop_knowledge ADD COLUMN IF NOT EXISTS payment_qr_rakuten_pay VARCHAR(15)"))
                await conn.execute(text("ALTER TABLE shop_knowledge ADD COLUMN IF NOT EXISTS payment_qr_merpay VARCHAR(15)"))
                await conn.execute(text("ALTER TABLE shop_knowledge ADD COLUMN IF NOT EXISTS payment_qr_other VARCHAR(15)"))
                await conn.execute(text("ALTER TABLE shop_knowledge ADD COLUMN IF NOT EXISTS payment_emoney_transit_ic VARCHAR(15)"))
                await conn.execute(text("ALTER TABLE shop_knowledge ADD COLUMN IF NOT EXISTS payment_emoney_id VARCHAR(15)"))
                await conn.execute(text("ALTER TABLE shop_knowledge ADD COLUMN IF NOT EXISTS payment_emoney_quicpay VARCHAR(15)"))
                await conn.execute(text("ALTER TABLE shop_knowledge ADD COLUMN IF NOT EXISTS payment_emoney_rakuten_edy VARCHAR(15)"))
                await conn.execute(text("ALTER TABLE shop_knowledge ADD COLUMN IF NOT EXISTS payment_emoney_waon VARCHAR(15)"))
                await conn.execute(text("ALTER TABLE shop_knowledge ADD COLUMN IF NOT EXISTS payment_emoney_nanaco VARCHAR(15)"))
                await conn.execute(text("ALTER TABLE shop_knowledge ADD COLUMN IF NOT EXISTS payment_emoney_other VARCHAR(15)"))
            except Exception as alter_err:
                logger.warning(f"⚠️ shop_knowledge の支払いブランド詳細カラム追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

            # Phase3H Workstream C: 予約通知の連絡先（オーナー専用・非公開）用カラム。
            # ShopResponse等の公開スキーマには一切含めない設計のため、この2列自体は
            # 追加してもCustomer向けAPIの応答には影響しない。
            try:
                await conn.execute(text("ALTER TABLE shops ADD COLUMN IF NOT EXISTS reservation_notification_phone VARCHAR(20)"))
                await conn.execute(text("ALTER TABLE shops ADD COLUMN IF NOT EXISTS reservation_phone_notification_enabled BOOLEAN NOT NULL DEFAULT false"))
            except Exception as alter_err:
                logger.warning(f"⚠️ shops の予約通知連絡先用カラム追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

            # Phase 5A: 多言語AI受付。両カラムともNULL許可（未設定＝既存店舗は日本語のみ
            # として安全にフォールバックする。app.language_registry.effective_ai_languages /
            # effective_languages参照）。マスデータのbackfillは行わない
            # （既存行はNULLのままで正しく安全側のデフォルト動作になる）。
            try:
                await conn.execute(text("ALTER TABLE shops ADD COLUMN IF NOT EXISTS ai_supported_languages JSON"))
                await conn.execute(text("ALTER TABLE shops ADD COLUMN IF NOT EXISTS staff_supported_languages JSON"))
            except Exception as alter_err:
                logger.warning(f"⚠️ shops の対応言語用カラム追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

            # Phase 5A: 前回の会話言語（ソフトなヒントのみ。国籍・民族の推測には使わない）。
            try:
                await conn.execute(text("ALTER TABLE customer_memories ADD COLUMN IF NOT EXISTS last_conversation_language VARCHAR(10)"))
            except Exception as alter_err:
                logger.warning(f"⚠️ customer_memories.last_conversation_language カラム追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

            # Human Handoff基盤: callback_requestsテーブル自体はapp/models/__init__.pyで
            # importされているため、上記のBase.metadata.create_all()により新規テーブル
            # として自動作成される（他のPhase3系新規テーブルと同じ方針。ここでの
            # 明示的なCREATE TABLEは不要）。以下はShopへの新規カラム追加のみ。
            #
            # reservation_notification_email/reservation_email_notification_enabled:
            #   既存のreservation_notification_phone/reservation_phone_notification_enabled
            #   と同じ「連絡先の値」と「通知ON/OFF」を分離した設計を踏襲（値をNULLに
            #   戻さず単にOFFにするだけの運用も可能にするため）。
            # ai_phone_reception_enabled:
            #   デフォルトTRUEで追加し、既存店舗の動作（AIが電話に出る）を変更しない
            #   （shops.reservations_enabledの追加時と同じ「既存動作を壊さない」方針）。
            # transfer_to_staff_enabled/transfer_phone_number/
            # transfer_no_answer_fallback_to_callback:
            #   将来のライブ転送機能（本フェーズでは未実装）向けのデータモデルのみ。
            #   transfer_phone_numberはreservation_notification_phoneとは意図的に
            #   別カラムとし、混同しない（谷村様の明示的な指示）。
            try:
                await conn.execute(text("ALTER TABLE shops ADD COLUMN IF NOT EXISTS reservation_notification_email VARCHAR(255)"))
                await conn.execute(text("ALTER TABLE shops ADD COLUMN IF NOT EXISTS reservation_email_notification_enabled BOOLEAN NOT NULL DEFAULT false"))
                await conn.execute(text("ALTER TABLE shops ADD COLUMN IF NOT EXISTS ai_phone_reception_enabled BOOLEAN NOT NULL DEFAULT true"))
                await conn.execute(text("ALTER TABLE shops ADD COLUMN IF NOT EXISTS transfer_to_staff_enabled BOOLEAN NOT NULL DEFAULT false"))
                await conn.execute(text("ALTER TABLE shops ADD COLUMN IF NOT EXISTS transfer_phone_number VARCHAR(20)"))
                await conn.execute(text("ALTER TABLE shops ADD COLUMN IF NOT EXISTS transfer_no_answer_fallback_to_callback BOOLEAN NOT NULL DEFAULT true"))
            except Exception as alter_err:
                logger.warning(f"⚠️ shops のHuman Handoff/電話受付設定用カラム追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

            # CallbackRequestオーナー管理機能: 「お客様への折り返し対応が完了したか」を
            # 表す resolution_status を追加。既存の status カラム（担当者への通知試行の
            # 成否）とは意味が異なる別カラムのため、既存データへの影響なく追加できる
            # （既存行はすべて DEFAULT 'unhandled' ＝未対応 として扱われる）。
            try:
                await conn.execute(text(
                    "ALTER TABLE callback_requests ADD COLUMN IF NOT EXISTS resolution_status "
                    "VARCHAR(20) NOT NULL DEFAULT 'unhandled'"
                ))
            except Exception as alter_err:
                logger.warning(f"⚠️ callback_requests.resolution_status カラム追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

            # Fast Reservation Flow: 予約時に来店理由を確認するかどうかの
            # オーナー設定（ai_staff_settings.ask_visit_reason_enabled）。
            # デフォルトfalseのため、既存店舗の挙動は変更されない
            # （オーナーが明示的にONにした場合のみRealtime instructionsへ
            # 反映される。app.services.realtime_voice_ai._VISIT_REASON_TEMPLATE参照）。
            try:
                await conn.execute(text(
                    "ALTER TABLE ai_staff_settings ADD COLUMN IF NOT EXISTS ask_visit_reason_enabled "
                    "BOOLEAN NOT NULL DEFAULT false"
                ))
            except Exception as alter_err:
                logger.warning(f"⚠️ ai_staff_settings.ask_visit_reason_enabled カラム追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

            # Reservation Intelligence Phase B: 各予約が自身の占有時間（分）を
            # 持つようにする duration_minutes を追加。nullable（NULLは「無制限」
            # ではなく「Phase B以前に作成された既存予約」を意味し、参照する側
            # （app.routers.reservations._existing_duration_minutes）が必ず
            # shop.reservation_duration_minutes or 90 に解決してから使う設計。
            # 既存予約行には一切書き込みを行わない（そのままNULLのまま）ため、
            # 既存データへの破壊的変更はない。
            try:
                await conn.execute(text(
                    "ALTER TABLE reservations ADD COLUMN IF NOT EXISTS duration_minutes INTEGER"
                ))
            except Exception as alter_err:
                logger.warning(f"⚠️ reservations.duration_minutes カラム追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

            # Reservation Intelligence Phase D-1: 日跨ぎ営業時間・日跨ぎ勤務を
            # 明示的なBooleanで表現する（「closing<openingなら自動的に翌日」という
            # 暗黙推論は採用しない）。全てデフォルトFalseで追加するため、既存の
            # shop_hours/staff_weekly_shifts/staff_shift_overrides行の意味・挙動は
            # 一切変化しない（後方互換性維持。詳細は各モデルのコメント参照）。
            try:
                await conn.execute(text(
                    "ALTER TABLE shop_hours ADD COLUMN IF NOT EXISTS closes_next_day BOOLEAN NOT NULL DEFAULT false"
                ))
                await conn.execute(text(
                    "ALTER TABLE shop_hours ADD COLUMN IF NOT EXISTS last_order_next_day BOOLEAN NOT NULL DEFAULT false"
                ))
            except Exception as alter_err:
                logger.warning(f"⚠️ shop_hours の日跨ぎ用カラム追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

            try:
                await conn.execute(text(
                    "ALTER TABLE staff_weekly_shifts ADD COLUMN IF NOT EXISTS ends_next_day BOOLEAN NOT NULL DEFAULT false"
                ))
            except Exception as alter_err:
                logger.warning(f"⚠️ staff_weekly_shifts.ends_next_day カラム追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

            try:
                await conn.execute(text(
                    "ALTER TABLE staff_shift_overrides ADD COLUMN IF NOT EXISTS ends_next_day BOOLEAN NOT NULL DEFAULT false"
                ))
            except Exception as alter_err:
                logger.warning(f"⚠️ staff_shift_overrides.ends_next_day カラム追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

            # PHASE O3: pre_ordersテーブル自体はPHASE O2でBase.metadata.create_all()
            # により既に作成済み（他のPhase3系新規テーブルと同じ既存パターン）だが、
            # create_all()は既存テーブルへの新規カラム追加は行わないため、O3で
            # 追加したidempotency_key列はここで個別にALTERする（Reservationの
            # idempotency_key追加と全く同じパターン）。既存のPreOrder行（O2では
            # Public APIが存在しなかったため実運用データは無い）には一切影響しない。
            try:
                await conn.execute(text(
                    "ALTER TABLE pre_orders ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(200)"
                ))
                await conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ux_pre_orders_idempotency_key "
                    "ON pre_orders (idempotency_key)"
                ))
            except Exception as alter_err:
                logger.warning(f"⚠️ pre_orders.idempotency_key カラム/一意インデックスの追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

            # PHASE O4: 店舗単位の事前注文受付フラグ。既存店舗で突然ONにならない
            # よう、reservations_enabledとは異なりDEFAULT falseにする
            # （app/models/shop.pyのpre_order_enabledコメント参照）。
            # pre_order_productsテーブル自体は新規テーブルのため、この直前の
            # Base.metadata.create_all()で既に作成済み（ALTER不要）。
            try:
                await conn.execute(text(
                    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS pre_order_enabled BOOLEAN NOT NULL DEFAULT false"
                ))
            except Exception as alter_err:
                logger.warning(f"⚠️ shops.pre_order_enabled カラムの追加に失敗（既に存在する場合は無視して問題ありません）: {alter_err}")

        logger.info("✅ Database tables initialized")
        await engine.dispose()

        init_db(database_url)
        app.state.db_ready = True
        logger.info("✅ Database session factory initialized")
    except Exception as e:
        app.state.db_error = f"{type(e).__name__}: {e}"
        logger.error(f"❌ Database initialization failed: {e}")

    # Outbound AI Phase 1: 予約確定通知の架電ジョブをポーリングするバックグラウンド
    # ワーカー。Redis/Celery等の新規インフラは使わず、DB初期化が成功した場合にのみ
    # 起動するasyncioタスクとして実装する（app.services.outbound_call_workerの
    # docstring参照）。既存のRealtime Voice AI（Inbound、ブラウザ経由）とは完全に
    # 独立した別タスクであり、この起動・停止が既存の受付フローに影響することはない。
    # app.config.Settings.OUTBOUND_CALL_WORKER_ENABLED=Falseで無効化できる安全弁もある。
    outbound_worker_task = None
    if app.state.db_ready:
        from app.services.outbound_call_worker import run_outbound_worker_loop
        outbound_worker_task = asyncio.create_task(run_outbound_worker_loop())

    # Phase N2: LINE Owner通知配信ワーカー。上のOutboundワーカーと全く同じ理由・
    # 同じパターンで、DB初期化が成功した場合にのみ起動する独立したasyncioタスク。
    # Phase N1のOwnerNotificationEvent生成ロジック・既存の受付フローには
    # 一切影響しない（read-onlyでOwnerNotificationEventを参照するのみ）。
    # app.config.Settings.LINE_NOTIFICATION_WORKER_ENABLED=Falseで無効化できる
    # 安全弁もある。
    line_notification_worker_task = None
    if app.state.db_ready:
        from app.services.line_notification_worker import run_line_notification_worker_loop
        line_notification_worker_task = asyncio.create_task(run_line_notification_worker_loop())

    yield

    logger.info("🛑 BARIYON Receptra API shutting down...")

    if outbound_worker_task is not None:
        outbound_worker_task.cancel()
        try:
            await outbound_worker_task
        except asyncio.CancelledError:
            pass

    if line_notification_worker_task is not None:
        line_notification_worker_task.cancel()
        try:
            await line_notification_worker_task
        except asyncio.CancelledError:
            pass


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
    app.include_router(shop_resources_router)
    app.include_router(shop_closures_router)
    app.include_router(shop_break_time_router)
    app.include_router(shop_hours_override_router)
    app.include_router(shop_images_router)
    app.include_router(shop_images_files_router)
    app.include_router(taxonomy_router)
    app.include_router(languages_router)
    app.include_router(reviews_router)
    app.include_router(mypage_router)
    app.include_router(coupons_router)
    app.include_router(services_router)
    app.include_router(staff_router)
    app.include_router(staff_shift_router)
    app.include_router(vonage_voice_router)
    app.include_router(voice_ai_test_router)
    app.include_router(shop_booking_ai_router)
    app.include_router(realtime_voice_router)
    app.include_router(ai_staff_settings_router)
    app.include_router(shop_knowledge_router)
    app.include_router(billing_router)
    app.include_router(payjp_webhook_router)
    app.include_router(outbound_calls_router)
    app.include_router(callback_requests_router)
    app.include_router(owner_notifications_router)
    app.include_router(pre_orders_router)
    app.include_router(shop_pre_orders_router)
    app.include_router(pre_order_products_router)
    app.include_router(line_connection_router)
    app.include_router(line_shop_settings_router)
    app.include_router(line_webhook_router)

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
