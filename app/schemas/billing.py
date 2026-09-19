"""
課金（PAY.jp）関連スキーマ
"""

from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field


class BillingActivateRequest(BaseModel):
    """課金アクティベーション（カード登録＋初期費用決済＋月額サブスク開始）リクエスト"""
    payjp_token: str = Field(..., description="PAY.jp.js でトークン化されたカードトークン (tok_...)")


class BillingStatusResponse(BaseModel):
    """現在の課金ステータス"""
    subscription_status: str  # trial, active, past_due, suspended, cancelled
    is_active: bool
    setup_fee_paid: bool
    setup_fee_amount: int
    monthly_amount: int
    card_brand: Optional[str] = None
    card_last4: Optional[str] = None

    class Config:
        from_attributes = True


class BillingActivateResponse(BaseModel):
    success: bool
    message: str
    status: BillingStatusResponse
