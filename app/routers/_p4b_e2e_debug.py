"""
【一時ファイル・テスト後に必ず削除】Outbound AI Phase 4B Production E2E検証用デバッグルーター

Phase 4B（Returning Customer Context）のProduction E2Eでは、実際に予約を成立させて
create_reservation_tool経由の予約作成・find_customer/confirm_customer_identity/
get_customer_contextの一連のRealtime Voiceフローを検証する必要があるため、
Phase 1/1.5/4Aと同様の課金アクティベーションスキップ用エンドポイントが必要になる
（reservations_enabledは実際のPAY.jp課金なしにはONにできない仕様のため）。

find_customer / confirm_customer_identity / get_customer_context /
check_availability / create_reservation の各Toolエンドポイント自体は元々
認証不要の公開エンドポイント（app/routers/realtime_voice.py）であり、本ルーターを
経由せず直接呼び出して検証する。本ルーターが提供するのは以下の2つのみ:

1. 課金アクティベーションのスキップ（force-enable-reservations）
   Phase 1/4Aの_outbound_e2e_debug.py / _p4a_e2e_debug.pyと全く同じ設計・
   同じ安全策をそのまま踏襲。
2. 自テナント削除（cleanup用。Phase 1/1.5/4Aと全く同じ設計）。

Phase 4Aの_p4a_e2e_debug.pyと異なり、customer-memories一覧の直接確認エンドポイントは
今回設けない。理由: Phase 4Bで検証したいCustomer Contextの内容（来店回数・
前回予約日時・前回サービス・前回担当者・医療系業種での非開示等）は、まさに
get_customer_context Tool自体のレスポンスとして直接観測できるため、DBの内部状態を
別途覗く一時エンドポイントを追加する必要が無い（むしろ追加しないことで、
一時デバッグルーターの攻撃面・削除し忘れリスクを最小化する）。

追記: 一時的に「孤立したE2E-TEMPテナントの一覧・強制削除」用の認証不要
エンドポイント（/orphaned-tenants, /orphaned-tenants/{tenant_id}）を、
ブラウザ側の状態喪失に対する復旧策として追加したことがあったが、
本番環境に認証なしで到達する削除系エンドポイントを常設することは
セキュリティ上望ましくないため、ユーザーの明示許可のもとその場で
1回だけ使用した直後に完全に削除済み（本ファイルはこの2エンドポイントを
含まない状態が正）。

安全設計（重要・Phase 1/1.5/4Aと同じ二重防御をそのまま踏襲）:
- 全エンドポイントが「呼び出し元ユーザー自身のtenant」にのみ作用する。
- 実データを操作するエンドポイントは全て、対象のshop.name / tenant.nameが
  E2E_MARKER ("【E2E-TEMP】") で始まっていることを確認する（実装ミスがあっても、
  実店舗・実テナントの名前はこの形式を持たないため最終防衛線として機能する）。
- force-enable-reservationsは実際の課金（PAY.jp等）には一切触れず、DB上の
  reservations_enabled / tenant.subscription_status フラグを直接書き換えるのみ。
- Vonage・実電話発信には一切関与しない。
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models.shop import Shop
from app.models.user import Tenant
from app.schemas.user import CurrentUser

logger = logging.getLogger("receptra.p4b.e2e_debug")

router = APIRouter(prefix="/api/v1/_e2e_debug_p4b", tags=["_e2e_debug_p4b_TEMPORARY"])

E2E_MARKER = "【E2E-TEMP】"


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


class E2EForceEnableResponse(BaseModel):
    shop_id: str
    reservations_enabled: bool
    tenant_subscription_status: str


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


@router.delete(
    "/tenants/self",
    status_code=204,
    summary="【一時】呼び出し元自身のE2E-TEMPテナント・ユーザーを完全に削除する（店舗を先に削除しておく必要あり）",
)
async def delete_own_e2e_tenant(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    tenant = await db.get(Tenant, current_user.tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="テナントが見つかりません")
    if not tenant.name.startswith(E2E_MARKER):
        raise HTTPException(
            status_code=403,
            detail=f"このエンドポイントはテナント名が{E2E_MARKER}で始まる一時テストテナントにのみ使用できます",
        )

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
