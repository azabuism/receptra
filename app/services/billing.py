"""
PAY.jp 課金サービス

レセプトラの店舗オーナー課金モデル：
  - 初期導入費: 20,000円（税込・初回のみ、キャンペーン価格。通常40,000円）
  - 月額利用料: 5,000円（税込）

課金のタイミング：予約受付・電話リマインダーなど「実際に機能を使い始める」時点で
カード登録＋初期費用決済＋月額サブスク開始を行う（登録＝即課金ではない）。

PAYJP_API_KEY  = 公開鍵 (pk_live_... / pk_test_...) … フロントエンドのトークン化に使用
PAYJP_SECRET_KEY = 秘密鍵 (sk_live_... / sk_test_...) … このサービスのサーバーサイドAPI呼び出しに使用
"""

import logging
from typing import Optional

import payjp

from app.config import get_settings
from app.models.user import Tenant

logger = logging.getLogger("receptra.billing")

# ===== 料金設定 =====
SETUP_FEE_AMOUNT = 20000  # 初期導入費（キャンペーン価格）円
MONTHLY_AMOUNT = 5000  # 月額利用料 円
MONTHLY_PLAN_ID = "receptra-standard-monthly"


class BillingError(Exception):
    """課金処理で発生したエラー（PAY.jpエラーをラップし、ユーザー向けメッセージを保持する）"""

    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _configure_payjp() -> None:
    settings = get_settings()
    if not settings.PAYJP_SECRET_KEY:
        raise BillingError(
            "PAY.jpのシークレットキーがサーバーに設定されていません。管理者に連絡してください。",
            status_code=500,
        )
    payjp.api_key = settings.PAYJP_SECRET_KEY


def _wrap_payjp_error(e: Exception) -> BillingError:
    """PAY.jpのエラーを日本語のユーザー向けメッセージに変換する"""
    # payjp.error.CardError はカード自体の問題（残高不足・番号誤りなど）
    if isinstance(e, payjp.error.CardError):
        return BillingError(f"カード決済に失敗しました: {e.user_message or str(e)}")
    if isinstance(e, payjp.error.InvalidRequestError):
        logger.error("PAY.jp invalid request: %s", e)
        return BillingError("決済情報の処理中にエラーが発生しました。カード情報をご確認のうえ再度お試しください。")
    if isinstance(e, payjp.error.AuthenticationError):
        logger.error("PAY.jp authentication error: %s", e)
        return BillingError("決済サービスの認証に失敗しました。管理者に連絡してください。", status_code=500)
    if isinstance(e, payjp.error.APIConnectionError):
        logger.error("PAY.jp connection error: %s", e)
        return BillingError("決済サービスへの接続に失敗しました。時間をおいて再度お試しください。", status_code=502)
    if isinstance(e, payjp.error.PayjpException):
        logger.error("PAY.jp error: %s", e)
        return BillingError("決済処理中にエラーが発生しました。時間をおいて再度お試しください。")
    logger.exception("Unexpected billing error")
    return BillingError("予期しないエラーが発生しました。管理者に連絡してください。", status_code=500)


def ensure_monthly_plan() -> "payjp.Plan":
    """月額プランがPAY.jp側に存在することを保証する（無ければ作成、あれば取得）。冪等。"""
    _configure_payjp()
    try:
        return payjp.Plan.retrieve(MONTHLY_PLAN_ID)
    except payjp.error.InvalidRequestError:
        # 存在しない場合は作成
        try:
            return payjp.Plan.create(
                id=MONTHLY_PLAN_ID,
                amount=MONTHLY_AMOUNT,
                currency="jpy",
                interval="month",
                name="レセプトラ 店舗オーナープラン（月額）",
            )
        except Exception as e:  # noqa: BLE001
            raise _wrap_payjp_error(e)
    except Exception as e:  # noqa: BLE001
        raise _wrap_payjp_error(e)


def _get_or_create_customer(tenant: Tenant, payjp_token: str) -> "payjp.Customer":
    """テナントに紐づくPAY.jp顧客を取得、無ければ新規作成してカードを登録する。
    既に顧客が存在する場合は新しいトークンのカードをデフォルトカードとして追加する。
    """
    try:
        if tenant.payjp_customer_id:
            customer = payjp.Customer.retrieve(tenant.payjp_customer_id)
            card = customer.cards.create(card=payjp_token)
            customer.default_card = card.id
            customer.save()
            return customer

        customer = payjp.Customer.create(
            email=tenant.email,
            description=f"{tenant.name} ({tenant.slug})",
            card=payjp_token,
            metadata={"tenant_id": tenant.id, "tenant_slug": tenant.slug},
        )
        return customer
    except Exception as e:  # noqa: BLE001
        raise _wrap_payjp_error(e)


def _extract_card_info(customer: "payjp.Customer") -> tuple[Optional[str], Optional[str]]:
    try:
        default_card_id = customer.get("default_card")
        for card in customer.cards.get("data", []):
            if card.get("id") == default_card_id:
                return card.get("brand"), card.get("last4")
        # フォールバック：先頭のカード
        cards = customer.cards.get("data", [])
        if cards:
            return cards[0].get("brand"), cards[0].get("last4")
    except Exception:  # noqa: BLE001
        logger.warning("Could not extract card info from PAY.jp customer", exc_info=True)
    return None, None


def activate_subscription(tenant: Tenant, payjp_token: str) -> Tenant:
    """課金アクティベーションのメインフロー。

    1. PAY.jp顧客の取得/作成＋カード登録
    2. 初期導入費（20,000円）の一括請求 ※未払いの場合のみ（再開時は二重課金しない）
    3. 月額プラン（5,000円/月）のサブスクリプション作成 ※既存が無い場合のみ
    4. テナントのステータスを更新

    呼び出し元でDBセッションのcommitを行うこと。
    """
    if tenant.subscription_status == "active":
        raise BillingError("すでに課金が有効になっています。")

    _configure_payjp()

    customer = _get_or_create_customer(tenant, payjp_token)
    tenant.payjp_customer_id = customer.id

    card_brand, card_last4 = _extract_card_info(customer)
    if card_brand:
        tenant.card_brand = card_brand
    if card_last4:
        tenant.card_last4 = card_last4

    # 初期導入費（初回のみ）
    if tenant.setup_fee_paid_at is None:
        try:
            payjp.Charge.create(
                customer=customer.id,
                amount=SETUP_FEE_AMOUNT,
                currency="jpy",
                description="レセプトラ 初期導入費（キャンペーン価格）",
            )
        except Exception as e:  # noqa: BLE001
            raise _wrap_payjp_error(e)
        from datetime import datetime

        tenant.setup_fee_paid_at = datetime.utcnow()

    # 月額サブスクリプション
    plan = ensure_monthly_plan()

    if not tenant.payjp_subscription_id:
        try:
            subscription = payjp.Subscription.create(
                customer=customer.id,
                plan=plan.id,
            )
        except Exception as e:  # noqa: BLE001
            raise _wrap_payjp_error(e)
        tenant.payjp_subscription_id = subscription.id
    else:
        # 解約済みサブスクリプションの再開などは、新規サブスクリプションを作り直す
        try:
            existing = payjp.Subscription.retrieve(tenant.payjp_subscription_id)
            if existing.get("status") == "canceled":
                subscription = payjp.Subscription.create(customer=customer.id, plan=plan.id)
                tenant.payjp_subscription_id = subscription.id
        except Exception as e:  # noqa: BLE001
            raise _wrap_payjp_error(e)

    tenant.subscription_status = "active"
    return tenant


def cancel_subscription(tenant: Tenant) -> Tenant:
    """月額サブスクリプションを解約する。初期費用の返金は行わない。"""
    if not tenant.payjp_subscription_id:
        raise BillingError("有効なサブスクリプションが見つかりません。")

    _configure_payjp()
    try:
        subscription = payjp.Subscription.retrieve(tenant.payjp_subscription_id)
        subscription.cancel()
    except Exception as e:  # noqa: BLE001
        raise _wrap_payjp_error(e)

    tenant.subscription_status = "cancelled"
    return tenant


def is_billing_active(tenant: Tenant) -> bool:
    return tenant.subscription_status == "active"
