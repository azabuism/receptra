"""
Outbound AI Phase 1: 通知履歴の閲覧エンドポイント（オーナー専用）

app.routers.shops の notification-settings エンドポイントと同じ
オーナー認証パターン（tenant_idの一致確認）を踏襲する。電話番号は必ず
マスク済みの形でのみ返す。本フェーズでは架電自体はFake providerによる
シミュレーションのみのため、ここに表示される履歴も実際の通話記録ではなく
模擬架電の記録であることに注意。

Phase 1.5（Owner Notification UX）での最小拡張:
Owner UIが通知履歴に予約者名・予約日時を表示できるよう、関連する
Reservationをselectinloadで取得しレスポンスに補完する。新しいAPI
エンドポイントは追加しない。顧客電話番号・通知先電話番号（マスクなしの生値）・
内部provider詳細・他tenant情報は引き続き一切含めない
（app.schemas.outbound_call.OutboundCallLogResponseのdocstring参照）。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.deps import get_current_user
from app.models.outbound_call import OutboundCallLog
from app.models.shop import Shop
from app.schemas.outbound_call import OutboundCallLogListResponse, OutboundCallLogResponse
from app.schemas.user import CurrentUser

logger = logging.getLogger("receptra.outbound.router")

router = APIRouter(prefix="/api/v1/shops", tags=["outbound_calls"])


def _to_log_response(log: OutboundCallLog) -> OutboundCallLogResponse:
    """OutboundCallLog ORM行をレスポンスへ変換する。

    reservation_guest_name / reservation_date は、紐づくReservationが
    まだ存在する場合のみ埋める（Reservation.outbound_call_logsのcascade
    delete-orphanにより、通常はReservationが削除されればこのLog行も
    一緒に削除されるため、Noneになるのは想定外のデータ不整合時のみ）。
    """
    reservation = log.reservation
    return OutboundCallLogResponse(
        id=log.id,
        reservation_id=log.reservation_id,
        call_type=log.call_type,
        category=log.category,
        provider=log.provider,
        result_status=log.result_status,
        to_phone_masked=log.to_phone_masked,
        created_at=log.created_at,
        reservation_guest_name=(reservation.guest_name if reservation else None),
        reservation_date=(reservation.reservation_date if reservation else None),
    )


@router.get(
    "/{shop_id}/outbound-calls",
    response_model=OutboundCallLogListResponse,
    summary="Outbound通知履歴を取得（オーナー専用）",
    description=(
        "予約確定通知の架電履歴を取得する。本フェーズ（Outbound AI Phase 1）では"
        "実際の電話発信は行わず、Fake providerによる模擬架電の記録のみが表示される。"
    ),
)
async def get_outbound_call_logs(
    shop_id: str,
    limit: int = Query(50, ge=1, le=200),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OutboundCallLogListResponse:
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")
    if shop.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="この店舗を閲覧する権限がありません")

    result = await db.execute(
        select(OutboundCallLog)
        .options(selectinload(OutboundCallLog.reservation))
        .filter(OutboundCallLog.shop_id == shop_id)
        .order_by(OutboundCallLog.created_at.desc())
        .limit(limit)
    )
    logs = result.scalars().all()

    return OutboundCallLogListResponse(
        logs=[_to_log_response(log) for log in logs]
    )
