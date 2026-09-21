"""
管理者向け 運用ツール（Phase3F・Workstream C）

目的:
Production環境には検証・デモ目的で複数店舗が登録されており、これらを
安全に棚卸し・整理するための最小限の読み取り専用エンドポイント。

設計方針（重要）:
- 参照系（GET）のみ。削除・変更操作はここには一切含まない
  （削除はユーザー承認後、DB操作として別途・限定的に実施する）。
- get_current_admin（User.is_admin=True）必須。一般オーナーは利用不可。
- 大規模な管理画面やダッシュボードを新規に作るのではなく、Phase3Fの
  店舗棚卸しに必要な最小限の集計のみを提供する。
"""

from typing import List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_admin
from app.models.ai_staff_settings import AIStaffSettings
from app.models.reservation import Reservation
from app.models.service import Service
from app.models.shop import Shop
from app.models.shop_knowledge import ShopFAQ, ShopKnowledge
from app.models.staff import Staff
from app.models.user import Tenant

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/shops-inventory", summary="全店舗の棚卸し一覧を取得（管理者限定・参照専用）")
async def get_shops_inventory(
    current_user=Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    shops = (await db.execute(select(Shop).order_by(Shop.created_at))).scalars().all()

    tenant_rows = (await db.execute(select(Tenant))).scalars().all()
    tenants_by_id = {t.id: t for t in tenant_rows}

    async def _count(model, shop_id: str) -> int:
        result = await db.execute(
            select(func.count()).select_from(model).filter(model.shop_id == shop_id)
        )
        return int(result.scalar() or 0)

    items = []
    for shop in shops:
        tenant = tenants_by_id.get(shop.tenant_id)
        has_ai_settings = (
            await db.execute(
                select(AIStaffSettings.id).filter(AIStaffSettings.shop_id == shop.id).limit(1)
            )
        ).scalar_one_or_none() is not None
        has_knowledge = (
            await db.execute(
                select(ShopKnowledge.id).filter(ShopKnowledge.shop_id == shop.id).limit(1)
            )
        ).scalar_one_or_none() is not None

        items.append({
            "shop_id": shop.id,
            "name": shop.name,
            "tenant_id": shop.tenant_id,
            "tenant_name": tenant.name if tenant else None,
            "tenant_email": tenant.email if tenant else None,
            "is_active": shop.is_active,
            "created_at": shop.created_at.isoformat() if shop.created_at else None,
            "reservation_count": await _count(Reservation, shop.id),
            "staff_count": await _count(Staff, shop.id),
            "service_count": await _count(Service, shop.id),
            "faq_count": await _count(ShopFAQ, shop.id),
            "has_ai_staff_settings": has_ai_settings,
            "has_shop_knowledge": has_knowledge,
        })

    return {"total": len(items), "shops": items}
