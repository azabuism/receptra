"""
Outbound AI Phase 1: 架電ジョブのバックグラウンドワーカー

Redis/Celery等の新規インフラは導入しない（RECEPTRA既存コードベースに
Queueシステムが存在しないため。app.config.Settings.REDIS_URLは定義されている
ものの実際には未使用というのが現状の実態）。app.main.py の lifespan() から
asyncio.create_task() で起動する単純なポーリングループとして実装する。

このワーカーは OutboundCallJob テーブルをポーリングし、PENDING かつ
next_attempt_at が到来したジョブを取り出して、実際の「発信」（Phase 1では
Fake providerによる模擬発信のみ）を行う。結果は OutboundCallLog に必ず
1行記録し、失敗時はエラーの種類だけをジョブに記録する（個人情報や生の
例外メッセージは記録しない）。

リトライ設計:
- 最大試行回数（デフォルト3回、app.config.Settings.OUTBOUND_CALL_MAX_ATTEMPTS）
  に達したジョブはFAILEDとして確定し、それ以上リトライしない
  （無限リトライを避ける。RECEPTRA Outbound AI Phase 基盤設計書 7節参照）。
- 失敗のたびに attempt_count × OUTBOUND_CALL_RETRY_BACKOFF_SECONDS 秒だけ
  next_attempt_at を後ろにずらす単純な線形バックオフ。
- Phase 1のFake providerは常に成功するため、実際にリトライが発生するのは
  DBの一時的な問題など、ごく限られたケースのみを想定している。
"""

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

import app.database as db_module
from app.config import get_settings
from app.models.outbound_call import (
    OutboundCallJob,
    OutboundCallJobStatus,
    OutboundCallLog,
)
from app.models.reservation import Reservation
from app.models.shop import Shop
from app.models.callback_request import CallbackRequest
from app.services.outbound_call_provider import get_outbound_call_provider
from app.services.outbound_voice_ai import build_reservation_confirmed_message, build_callback_requested_message

logger = logging.getLogger("receptra.outbound.worker")


def _mask_phone(phone: str | None) -> str | None:
    """電話番号を末尾4桁以外マスクする（app/routers/shops.pyの既存方針を踏襲）。"""
    if not phone or len(phone) < 4:
        return "****"
    return "****" + phone[-4:]


async def _process_one_job(db, job: OutboundCallJob, settings) -> None:
    """1件のジョブを処理する。例外はこの関数の外に伝播させず、呼び出し側の
    ループが継続できるようにする（1件の異常が他のジョブ処理を止めないため）。"""
    job.status = OutboundCallJobStatus.IN_PROGRESS.value
    job.attempt_count = (job.attempt_count or 0) + 1
    job.last_attempted_at = datetime.utcnow()
    await db.commit()

    shop = await db.get(Shop, job.shop_id)
    reservation = None
    callback_request = None
    message_text = None

    if job.call_type == "reservation_confirmed" and job.reservation_id:
        result = await db.execute(
            select(Reservation)
            .options(selectinload(Reservation.service))
            .filter(Reservation.id == job.reservation_id)
        )
        reservation = result.scalar_one_or_none()
        if shop is not None and reservation is not None:
            message_text = build_reservation_confirmed_message(shop, reservation)
    elif job.call_type == "callback_requested":
        # Human Handoff基盤: OutboundCallJobにcallback_request_id列は追加せず
        # （OutboundCallJob<->CallbackRequest間の循環FKを避け、店舗削除時の
        # カスケード順序を複雑化させないため）、CallbackRequest側が持つ
        # outbound_call_job_id の逆参照でこのジョブに対応する受付行を検索する
        # （app.services.outbound_dispatch.enqueue_callback_requested_call参照）。
        result = await db.execute(
            select(CallbackRequest).filter(CallbackRequest.outbound_call_job_id == job.id)
        )
        callback_request = result.scalar_one_or_none()
        if shop is not None and callback_request is not None:
            message_text = build_callback_requested_message(shop, callback_request)

    if shop is None or message_text is None:
        # サポート対象のcall_type（reservation_confirmed / callback_requested）
        # であっても、店舗・予約・折り返し受付のいずれかが既に削除されている等、
        # 実行不能なジョブはリトライしても解決しないため即座にFAILED確定する
        # （無限リトライを避ける。未サポートのcall_typeが混入した場合も同様に
        # 即FAILEDとし、既存のreservation_confirmed系ジョブの挙動は一切変えない）。
        job.status = OutboundCallJobStatus.FAILED.value
        job.last_error_category = "unsupported_or_missing_data"
        await db.commit()
        db.add(OutboundCallLog(
            job_id=job.id,
            shop_id=job.shop_id,
            reservation_id=job.reservation_id,
            call_type=job.call_type,
            category=job.category,
            provider="none",
            result_status="failed",
            to_phone_masked=_mask_phone(job.to_phone),
        ))
        await db.commit()
        return

    provider = get_outbound_call_provider()

    try:
        result = await provider.place_call(job.to_phone, message_text)
    except Exception:
        logger.exception("Outboundプロバイダー呼び出しで例外が発生しました job_id=%s provider=%s", job.id, provider.name)
        result = None

    if result is not None and result.success:
        job.status = OutboundCallJobStatus.COMPLETED.value
        job.last_error_category = None
        await db.commit()
        db.add(OutboundCallLog(
            job_id=job.id,
            shop_id=job.shop_id,
            reservation_id=job.reservation_id,
            call_type=job.call_type,
            category=job.category,
            provider=provider.name,
            provider_call_id=(result.provider_call_id if result else None),
            result_status=f"simulated_success" if provider.name == "fake" else "success",
            to_phone_masked=_mask_phone(job.to_phone),
            message_text=message_text,
        ))
        await db.commit()
        return

    # 失敗: リトライ判定
    error_category = (result.error_category if result else "provider_exception") or "unknown"
    job.last_error_category = error_category
    if job.attempt_count >= job.max_attempts:
        job.status = OutboundCallJobStatus.FAILED.value
        job.next_attempt_at = None
        await db.commit()
        db.add(OutboundCallLog(
            job_id=job.id,
            shop_id=job.shop_id,
            reservation_id=job.reservation_id,
            call_type=job.call_type,
            category=job.category,
            provider=provider.name,
            result_status="failed",
            to_phone_masked=_mask_phone(job.to_phone),
            message_text=message_text,
        ))
        await db.commit()
    else:
        backoff_seconds = settings.OUTBOUND_CALL_RETRY_BACKOFF_SECONDS * job.attempt_count
        job.status = OutboundCallJobStatus.PENDING.value
        job.next_attempt_at = datetime.utcnow() + timedelta(seconds=backoff_seconds)
        await db.commit()


async def _poll_once(settings) -> int:
    """1回分のポーリング。処理したジョブ件数を返す。

    app.database.AsyncSessionLocal は init_db() 実行後（lifespan起動時）に
    差し替えられるモジュール属性のため、`from app.database import
    AsyncSessionLocal` のような名前の直接importは使わず、必ず
    db_module.AsyncSessionLocal として都度参照する
    （app.services.outbound_dispatchのdocstring・app/deps.pyと同じ理由）。
    """
    processed = 0
    async with db_module.AsyncSessionLocal() as db:
        now = datetime.utcnow()
        stmt = (
            select(OutboundCallJob)
            .filter(
                OutboundCallJob.status == OutboundCallJobStatus.PENDING.value,
                (OutboundCallJob.next_attempt_at.is_(None)) | (OutboundCallJob.next_attempt_at <= now),
            )
            .order_by(OutboundCallJob.created_at.asc())
            .limit(10)
            .with_for_update(skip_locked=True)
        )
        result = await db.execute(stmt)
        jobs = result.scalars().all()

        for job in jobs:
            try:
                await _process_one_job(db, job, settings)
                processed += 1
            except Exception:
                logger.exception("Outboundジョブ処理中に予期しない例外が発生しました job_id=%s", job.id)
                try:
                    await db.rollback()
                except Exception:
                    pass
    return processed


async def run_outbound_worker_loop() -> None:
    """app.main.py の lifespan() から起動される常駐ループ。

    shutdown時はこのタスク自体がasyncio.CancelledErrorでキャンセルされる
    想定（lifespan側でtask.cancel()する）。ここでは特別なクリーンアップは
    不要（各ポーリングは独立したDBセッションで完結するため）。
    """
    settings = get_settings()
    if not settings.OUTBOUND_CALL_WORKER_ENABLED:
        logger.info("OUTBOUND_CALL_WORKER_ENABLED=Falseのため、Outboundワーカーは起動しません")
        return

    logger.info(
        "Outboundワーカーを起動しました provider=%s interval=%ds",
        settings.OUTBOUND_CALL_PROVIDER, settings.OUTBOUND_CALL_POLL_INTERVAL_SECONDS,
    )
    try:
        while True:
            try:
                await _poll_once(settings)
            except Exception:
                logger.exception("Outboundワーカーのポーリングで予期しない例外が発生しました")
            await asyncio.sleep(settings.OUTBOUND_CALL_POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        logger.info("Outboundワーカーを停止しました")
        raise
