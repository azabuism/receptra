"""
【一時ファイル・テスト後に必ず削除】Outbound AI Phase 4A Production E2E検証用デバッグルーター

Phase 4A（Customer Memory Foundation）のProduction E2Eでは、実際に予約を成立させて
create_reservation_tool経由のCustomer Memory upsertを検証する必要があるため、
Phase 1と同様の課金アクティベーションスキップ用エンドポイントが必要になる
（reservations_enabledは実際のPAY.jp課金なしにはONにできない仕様のため）。

find_customer / create_reservation の各Toolエンドポイント自体は元々認証不要の
公開エンドポイント（app/routers/realtime_voice.py）であり、本ルーターを経由せず
直接呼び出して検証する。本ルーターが提供するのは以下の3つのみ:

1. 課金アクティベーションのスキップ（force-enable-reservations）
   Phase 1の_outbound_e2e_debug.pyと全く同じ設計・同じ安全策をそのまま踏襲。
2. Customer Memoryの直接確認（customer-memories一覧）
   find_customerツールのレスポンスはPrivacy Gateにより意図的に最小限
   （候補氏名のみ）に絞られているため、visit_count・normalized_phone・
   last_reservation_id等の内部状態を検証するには、この一時エンドポイントで
   直接DBの値を確認する必要がある。
3. 自テナント削除（cleanup用。Phase 1/1.5と全く同じ設計）。

安全設計（重要・Phase 1/1.5と同じ二重防御をそのまま踏襲）:
- 全エンドポイントが「呼び出し元ユーザー自身のtenant」にのみ作用する。
- 実データを操作・閲覧するエンドポイントは全て、対象のshop.name /
  tenant.nameが E2E_MARKER ("【E2E-TEMP】") で始まっていることを確認する
  （実装ミスがあっても、実店舗・実テナントの名前はこの形式を持たないため
  最終防衛線として機能する）。
- customer-memories一覧のレスポンスでも、電話番号は必ずマスクして返す
  （生の番号を含めない。既存の"****"+下4桁マスク方針を踏襲）。
- force-enable-reservationsは実際の課金（PAY.jp等）には一切触れず、DB上の
  reservations_enabled / tenant.subscription_status フラグを直接書き換えるのみ。
- Vonage・実電話発信には一切関与しない。
"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models.customer_memory import CustomerMemory
from app.models.shop import Shop
from app.models.user import Tenant
from app.schemas.user import CurrentUser

logger = logging.getLogger("receptra.p4a.e2e_debug")

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


class E2ECustomerMemoryView(BaseModel):
    id: str
    shop_id: str
    display_name: str
    normalized_phone_masked: str
    visit_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    last_reservation_id: Optional[str] = None

    class Config:
        from_attributes = True


class E2ECustomerMemoryListResponse(BaseModel):
    memories: List[E2ECustomerMemoryView]


def _to_memory_view(row: CustomerMemory) -> E2ECustomerMemoryView:
    return E2ECustomerMemoryView(
        id=row.id,
        shop_id=row.shop_id,
        display_name=row.display_name,
        normalized_phone_masked=_mask_phone(row.normalized_phone) or "",
        visit_count=row.visit_count,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        last_reservation_id=row.last_reservation_id,
    )


@router.get(
    "/shops/{shop_id}/customer-memories",
    response_model=E2ECustomerMemoryListResponse,
    summary="【一時】Customer Memoryの内部状態を直接確認する（E2E-TEMP店舗専用・電話番号はマスク済み）",
)
async def list_customer_memories(
    shop_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> E2ECustomerMemoryListResponse:
    shop = await _load_marked_shop(shop_id, current_user, db)
    result = await db.execute(
        select(CustomerMemory)
        .filter(CustomerMemory.shop_id == shop.id)
        .order_by(CustomerMemory.created_at.asc())
    )
    rows = result.scalars().all()
    return E2ECustomerMemoryListResponse(memories=[_to_memory_view(r) for r in rows])


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
