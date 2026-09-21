"""
【一時ファイル・テスト後に必ず削除】Outbound AI Phase 1 Production E2E検証用デバッグルーター

このルーターはPhase 1（予約確定通知キュー）のProduction E2E検証のためだけに
一時的に追加するものであり、検証完了後は本ファイルごと削除し、再デプロイ後に
OpenAPIから消えていることを確認する（ユーザー指示による必須手順）。

安全設計（重要）:
- 全エンドポイントが「呼び出し元ユーザー自身のtenant」にのみ作用する
  （他tenantのデータを対象にすることは一切できない。既存のオーナー認証パターン
  - shop.tenant_id != current_user.tenant_id なら403 - を踏襲）。
- さらに、実データを直接操作する（作成ではなく「変更・削除」する）エンドポイントは
  全て、対象のshop.name / tenant.nameが E2E_MARKER ("【E2E-TEMP】") で始まっている
  ことを二重にチェックする。既存の実店舗・実テナント（デモクリニック、ダニエル等）は
  この名前を持たないため、たとえ実装ミスがあってもこのマーカーチェックが
  最終防衛線として働き、実データには絶対に触れられない。
- force-enable-billing-bypassは実際の課金（PAY.jp等）には一切触れず、DB上の
  reservations_enabled / tenant.subscription_status フラグを直接書き換えるのみ。
  E2Eテストのためだけに課金アクティベーションフロー（初期費用・月額サブスク）を
  スキップする目的であり、実際の金銭のやり取りは一切発生しない。
- 電話番号は本ルーターのレスポンスでも常にマスクして返す（生の番号を含めない）。

【2回目のデプロイでの再追加について】
1回目のE2E検証でPhase 1のパイプライン自体は全て正常動作することを確認したが、
最後のクリーンアップ手順（DELETE /api/v1/shops/{shop_id}）で新たなバグ
（OutboundCallLog.job_id に対応するORM relationshipが無く、削除順序が
Postgres上でFK制約違反になる）を発見・修正した。その修正の検証と、
テストデータの完全なクリーンアップ（自テナント削除）のために本ルーターを
再度一時的に追加する。クリーンアップ完了後は再度削除する。
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models.outbound_call import OutboundCallJob, OutboundCallCategory, OutboundCallJobStatus
from app.models.reservation import Reservation
from app.models.shop import Shop
from app.models.user import Tenant, User
from app.schemas.user import CurrentUser
from app.services.outbound_dispatch import enqueue_reservation_confirmed_call

logger = logging.getLogger("receptra.outbound.e2e_debug")

router = APIRouter(prefix="/api/v1/_e2e_debug", tags=["_e2e_debug_TEMPORARY"])

E2E_MARKER = "【E2E-TEMP】"


def _mask_phone(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    return "****" + phone[-4:] if len(phone) >= 4 else "****"


async def _load_marked_shop(shop_id: str, current_user: CurrentUser, db: AsyncSession) -> Shop:
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")
    if shop.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="この店舗を操作する権限がありません")
    if not shop.name.startswith(E2E_MARKER):
        raise HTTPException(
            status_code=403,
            detail=f"このエンドポイントは店舗名が{E2E_MARKER}で始まる一時テスト店舗にのみ使用できます",
        )
    return shop


async def _load_marked_own_tenant(current_user: CurrentUser, db: AsyncSession) -> Tenant:
    tenant = await db.get(Tenant, current_user.tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="テナントが見つかりません")
    if not tenant.name.startswith(E2E_MARKER):
        raise HTTPException(
            status_code=403,
            detail=f"このエンドポイントはテナント名が{E2E_MARKER}で始まる一時テストテナントにのみ使用できます",
        )
    return tenant


# ===== レスポンス/リクエストスキーマ（本ファイル専用・一時的） =====

class E2EJobView(BaseModel):
    id: str
    shop_id: str
    reservation_id: Optional[str] = None
    call_type: str
    category: str
    status: str
    attempt_count: int
    max_attempts: int
    last_error_category: Optional[str] = None
    next_attempt_at: Optional[datetime] = None
    to_phone_masked: Optional[str] = None
    idempotency_key: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class E2EJobListResponse(BaseModel):
    jobs: List[E2EJobView]


class E2EForceEnableResponse(BaseModel):
    shop_id: str
    reservations_enabled: bool
    tenant_subscription_status: str


class E2EDuplicateEnqueueResponse(BaseModel):
    reservation_id: str
    matching_job_count: int
    jobs: List[E2EJobView]


class E2EParkedJobRequest(BaseModel):
    reservation_id: Optional[str] = None
    delay_seconds: int = Field(90, ge=0, le=3600)
    to_phone: Optional[str] = None
    attempt_count: int = Field(0, ge=0, le=2)


class E2EForcedFailureJobRequest(BaseModel):
    reservation_id: Optional[str] = None
    to_phone: Optional[str] = None


class E2ECreatedJobResponse(BaseModel):
    job: E2EJobView


def _to_job_view(job: OutboundCallJob) -> E2EJobView:
    return E2EJobView(
        id=job.id,
        shop_id=job.shop_id,
        reservation_id=job.reservation_id,
        call_type=job.call_type,
        category=job.category,
        status=job.status,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        last_error_category=job.last_error_category,
        next_attempt_at=job.next_attempt_at,
        to_phone_masked=_mask_phone(job.to_phone),
        idempotency_key=job.idempotency_key,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.post(
    "/shops/{shop_id}/force-enable-reservations",
    response_model=E2EForceEnableResponse,
    summary="【一時】課金アクティベーションをスキップしてreservations_enabledをONにする（E2E-TEMP店舗専用）",
)
async def force_enable_reservations(
    shop_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> E2EForceEnableResponse:
    shop = await _load_marked_shop(shop_id, current_user, db)
    tenant = await _load_marked_own_tenant(current_user, db)

    tenant.subscription_status = "active"
    shop.reservations_enabled = True
    shop.updated_at = datetime.utcnow()
    await db.commit()

    return E2EForceEnableResponse(
        shop_id=shop.id,
        reservations_enabled=shop.reservations_enabled,
        tenant_subscription_status=tenant.subscription_status,
    )


@router.get(
    "/shops/{shop_id}/outbound-jobs",
    response_model=E2EJobListResponse,
    summary="【一時】OutboundCallJobの状態を直接確認する（E2E-TEMP店舗専用）",
)
async def list_outbound_jobs(
    shop_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> E2EJobListResponse:
    shop = await _load_marked_shop(shop_id, current_user, db)
    result = await db.execute(
        select(OutboundCallJob)
        .filter(OutboundCallJob.shop_id == shop.id)
        .order_by(OutboundCallJob.created_at.asc())
    )
    jobs = result.scalars().all()
    return E2EJobListResponse(jobs=[_to_job_view(j) for j in jobs])


@router.post(
    "/shops/{shop_id}/reservations/{reservation_id}/force-duplicate-enqueue",
    response_model=E2EDuplicateEnqueueResponse,
    summary="【一時】同一予約へのenqueueを2回連続で呼び、二重ジョブが作られないことを検証する（E2E-TEMP店舗専用）",
)
async def force_duplicate_enqueue(
    shop_id: str,
    reservation_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> E2EDuplicateEnqueueResponse:
    shop = await _load_marked_shop(shop_id, current_user, db)

    reservation = await db.get(Reservation, reservation_id)
    if not reservation or reservation.shop_id != shop.id:
        raise HTTPException(status_code=404, detail="予約が見つかりません")

    await enqueue_reservation_confirmed_call(
        shop_id=shop.id,
        reservation_id=reservation.id,
        notification_enabled=True,
        notification_phone=shop.reservation_notification_phone or "09000000000",
    )
    await enqueue_reservation_confirmed_call(
        shop_id=shop.id,
        reservation_id=reservation.id,
        notification_enabled=True,
        notification_phone=shop.reservation_notification_phone or "09000000000",
    )

    idempotency_key = f"outbound:reservation_confirmed:{reservation.id}"
    result = await db.execute(
        select(OutboundCallJob).filter(OutboundCallJob.idempotency_key == idempotency_key)
    )
    jobs = result.scalars().all()

    return E2EDuplicateEnqueueResponse(
        reservation_id=reservation.id,
        matching_job_count=len(jobs),
        jobs=[_to_job_view(j) for j in jobs],
    )


@router.post(
    "/shops/{shop_id}/parked-jobs",
    response_model=E2ECreatedJobResponse,
    summary="【一時】未来のnext_attempt_atを持つqueued/retryジョブを直接作成する（worker再起動後の再取得検証用・E2E-TEMP店舗専用）",
)
async def create_parked_job(
    shop_id: str,
    request: E2EParkedJobRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> E2ECreatedJobResponse:
    shop = await _load_marked_shop(shop_id, current_user, db)

    reservation_id = None
    if request.reservation_id:
        reservation = await db.get(Reservation, request.reservation_id)
        if not reservation or reservation.shop_id != shop.id:
            raise HTTPException(status_code=404, detail="予約が見つかりません")
        reservation_id = reservation.id

    to_phone = request.to_phone or shop.reservation_notification_phone or "09000000000"

    job = OutboundCallJob(
        shop_id=shop.id,
        reservation_id=reservation_id,
        call_type="reservation_confirmed",
        category=OutboundCallCategory.TRANSACTIONAL.value,
        to_phone=to_phone,
        status=OutboundCallJobStatus.PENDING.value,
        attempt_count=request.attempt_count,
        next_attempt_at=datetime.utcnow() + timedelta(seconds=request.delay_seconds),
        idempotency_key=f"outbound:e2e_parked:{uuid.uuid4()}",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    return E2ECreatedJobResponse(job=_to_job_view(job))


@router.post(
    "/shops/{shop_id}/forced-failure-jobs",
    response_model=E2ECreatedJobResponse,
    summary="【一時】worker側で未対応のcall_typeを持つジョブを作成し、失敗時にReservationへ影響しないことを検証する（E2E-TEMP店舗専用）",
)
async def create_forced_failure_job(
    shop_id: str,
    request: E2EForcedFailureJobRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> E2ECreatedJobResponse:
    shop = await _load_marked_shop(shop_id, current_user, db)

    reservation_id = None
    if request.reservation_id:
        reservation = await db.get(Reservation, request.reservation_id)
        if not reservation or reservation.shop_id != shop.id:
            raise HTTPException(status_code=404, detail="予約が見つかりません")
        reservation_id = reservation.id

    to_phone = request.to_phone or shop.reservation_notification_phone or "09000000000"

    job = OutboundCallJob(
        shop_id=shop.id,
        reservation_id=reservation_id,
        call_type="e2e_forced_failure",
        category=OutboundCallCategory.TRANSACTIONAL.value,
        to_phone=to_phone,
        status=OutboundCallJobStatus.PENDING.value,
        idempotency_key=f"outbound:e2e_forced_failure:{uuid.uuid4()}",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    return E2ECreatedJobResponse(job=_to_job_view(job))


@router.delete(
    "/tenants/self",
    status_code=204,
    summary="【一時】呼び出し元自身のE2E-TEMPテナント・ユーザーを完全に削除する（店舗を先に削除しておく必要あり）",
)
async def delete_own_e2e_tenant(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    tenant = await _load_marked_own_tenant(current_user, db)

    remaining_shops = await db.execute(
        select(Shop.id).filter(Shop.tenant_id == tenant.id).limit(1)
    )
    if remaining_shops.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=400,
            detail="先に本番の DELETE /api/v1/shops/{shop_id} でこのテナントの店舗を削除してください",
        )

    await db.delete(tenant)
    await db.commit()
