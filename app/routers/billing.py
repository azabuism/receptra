"""
課金（PAY.jp）ルーター
/api/v1/billing エンドポイント + PAY.jp Webhook受信
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.deps import get_current_tenant, get_db
from app.models.user import Tenant
from app.schemas.billing import (
    BillingActivateRequest,
    BillingActivateResponse,
    BillingStatusResponse,
)
from app.services import billing as billing_service

logger = logging.getLogger("receptra.billing.router")

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])


def _build_status(tenant: Tenant) -> BillingStatusResponse:
    return BillingStatusResponse(
        subscription_status=tenant.subscription_status,
        is_active=tenant.subscription_status == "active",
        setup_fee_paid=tenant.setup_fee_paid_at is not None,
        setup_fee_amount=billing_service.SETUP_FEE_AMOUNT,
        monthly_amount=billing_service.MONTHLY_AMOUNT,
        card_brand=tenant.card_brand,
        card_last4=tenant.card_last4,
    )


@router.get(
    "/public-key",
    summary="PAY.jp公開鍵を取得（フロントエンドのカードトークン化用）",
)
async def get_public_key():
    settings = get_settings()
    return {"public_key": settings.PAYJP_API_KEY}


@router.get(
    "/status",
    response_model=BillingStatusResponse,
    summary="現在の課金ステータスを取得",
)
async def get_billing_status(
    tenant: Tenant = Depends(get_current_tenant),
) -> BillingStatusResponse:
    return _build_status(tenant)


@router.post(
    "/activate",
    response_model=BillingActivateResponse,
    summary="カード登録＋初期費用決済＋月額サブスク開始",
    description="予約受付など課金対象機能を初めて有効化する際に呼び出す",
)
async def activate_billing(
    request: BillingActivateRequest,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
) -> BillingActivateResponse:
    try:
        tenant = billing_service.activate_subscription(tenant, request.payjp_token)
        await db.commit()
        await db.refresh(tenant)
    except billing_service.BillingError as e:
        await db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.message)

    return BillingActivateResponse(
        success=True,
        message="お支払い設定が完了しました。予約受付などの機能をご利用いただけます。",
        status=_build_status(tenant),
    )


@router.post(
    "/cancel",
    response_model=BillingStatusResponse,
    summary="月額サブスクリプションを解約",
)
async def cancel_billing(
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
) -> BillingStatusResponse:
    try:
        tenant = billing_service.cancel_subscription(tenant)
        await db.commit()
        await db.refresh(tenant)
    except billing_service.BillingError as e:
        await db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.message)

    return _build_status(tenant)


webhook_router = APIRouter(prefix="/webhook/payjp", tags=["payjp_webhook"])


@webhook_router.post("")
async def payjp_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    PAY.jp Webhook受信エンドポイント。

    注意: PAY.jpのWebhookには署名検証の仕組みが無いため、URLを推測されにくいものにする、
    受信内容を鵜呑みにせず必ずAPI側で該当リソースを再取得して確認する、といった運用上の
    注意が必要。ここでは受信ログを残し、サブスクリプションの支払い失敗イベントのみ
    テナントステータスに反映する最小実装とする。
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid payload")

    event_type = payload.get("type", "")
    logger.info("PAY.jp webhook received: %s", event_type)

    if event_type in ("charge.failed", "subscription.deleted"):
        data = payload.get("data", {})
        customer_id = data.get("customer")
        if customer_id:
            from sqlalchemy import select

            result = await db.execute(
                select(Tenant).filter(Tenant.payjp_customer_id == customer_id)
            )
            tenant = result.scalar_one_or_none()
            if tenant:
                tenant.subscription_status = "past_due" if event_type == "charge.failed" else "cancelled"
                await db.commit()
                logger.info(
                    "Tenant %s subscription_status updated to %s via webhook",
                    tenant.id,
                    tenant.subscription_status,
                )

    return {"received": True}
