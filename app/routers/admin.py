"""
管理者向け 運用ツール（Phase3F・Workstream C）

目的:
Production環境には検証・デモ目的で複数店舗が登録されており、これらを
安全に棚卸し・整理するための最小限のエンドポイント。

設計方針（重要）:
- 棚卸し（GET）に加え、ユーザー承認済みのDB整理作業を実施するための
  限定的な削除エンドポイントを含む。
- 削除は必ず明示的な shop_id リストのみを対象とする（ワイルドカード・
  テナント一括・LIKE検索による削除は一切行わない）。
- Tenant / User / 課金情報（PAY.jp・サブスクリプション・カード情報）は
  このエンドポイントの削除対象に一切含めない。
- get_current_admin（User.is_admin=True）必須。一般オーナーは利用不可。
- 大規模な管理画面やダッシュボードを新規に作るのではなく、Phase3Fの
  店舗棚卸し・整理に必要な最小限の機能のみを提供する。
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_admin
from app.models.ai_staff_settings import AIStaffSettings
from app.models.analytics import MonthlyAnalytics, ShopAnalytics
from app.models.promotion import Coupon, Promotion
from app.models.reservation import Reservation
from app.models.review import Review
from app.models.service import Service
from app.models.shop import MenuItem, Shop, ShopClosure, ShopHours, ShopPhoto, ShopTable
from app.models.shop_knowledge import ShopFAQ, ShopKnowledge
from app.models.social import UserShopRelation
from app.models.staff import Staff, StaffService
from app.models.staff_shift import StaffShiftOverride, StaffWeeklyShift
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
            "coupon_count": await _count(Coupon, shop.id),
            "staff_count": await _count(Staff, shop.id),
            "service_count": await _count(Service, shop.id),
            "faq_count": await _count(ShopFAQ, shop.id),
            "has_ai_staff_settings": has_ai_settings,
            "has_shop_knowledge": has_knowledge,
        })

    return {"total": len(items), "shops": items}


class ShopsDeleteRequest(BaseModel):
    shop_ids: List[str]
    dry_run: bool = True


@router.post(
    "/shops/{shop_id}/test-enable-reservations",
    summary="【Phase3G Workstream2 検証専用・一時的】指定した明示shop_id 1件のみ、"
            "課金アクティベーションを経由せずreservations_enabledを直接ONにする",
)
async def test_enable_reservations(
    shop_id: str,
    current_user=Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    重要（一時的なテスト専用エンドポイント）:
    通常、Shop.reservations_enabledは app/routers/shops.py の PATCH /shops/{shop_id}
    経由でのみONにでき、その際テナントの課金アクティベーション
    (Tenant.subscription_status == "active") が必須という仕様になっている
    （初期費用・月額サブスクを経ていない店舗が予約受付を開始できないようにする
    ための意図的なゲート）。

    Phase3G Workstream2（Coupon Reservation Integrity）のProduction E2Eでは、
    実際にcreate_reservation/cancel_reservationのコードパスをProduction DBに
    対して検証する必要があるが、Tenant/User/課金情報には一切変更を加えない
    という方針があるため、通常の課金アクティベーションを経由することはしない。
    その代わり、このエンドポイントは指定された1つのshop_idについてのみ、
    Tenant側には一切触れず、Shop.reservations_enabledだけを直接trueにする
    （billing/subscription/PAY.jpは一切参照・変更しない）。

    このエンドポイントはWorkstream2のE2Eテストが完了次第、コードごと削除する
    （Workstream3のAdmin API Security Reviewで、恒久的なAPIとして残さない
    方針と合わせて確認済み）。
    """
    shop = (await db.execute(select(Shop).filter(Shop.id == shop_id))).scalar_one_or_none()
    if shop is None:
        return {"found": False, "shop_id": shop_id}

    shop.reservations_enabled = True
    shop.updated_at = datetime.utcnow()
    await db.commit()

    return {"found": True, "shop_id": shop_id, "reservations_enabled": True}


@router.post(
    "/shops/delete",
    summary="指定した shop_id のみを対象に、関連データを含めて完全削除する（管理者限定・明示ID限定）",
)
async def delete_shops(
    payload: ShopsDeleteRequest,
    current_user=Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    重要な安全設計:
    - 本エンドポイントは payload.shop_ids に明示的に列挙された shop_id のみを
      対象とする。ワイルドカード・LIKE検索・テナント一括削除は一切行わない。
    - Tenant / User / 課金情報（PAY.jp・サブスクリプション・カード情報）は
      削除対象に一切含めない（そもそも参照すらしない）。
    - Shop に紐づく全ての子テーブルを、FK依存関係を考慮した順序で明示的に
      DELETEし、孤立レコード（orphan）を残さない。
    - dry_run=True（デフォルト）の場合は削除件数のカウントのみを行い、
      実際のDELETEは実行しない（コミットもしない）。
    """
    if not payload.shop_ids:
        return {"error": "shop_ids must be a non-empty explicit list", "results": []}

    # 重複排除しつつ順序を保持
    shop_ids = list(dict.fromkeys(payload.shop_ids))

    results = []

    for shop_id in shop_ids:
        shop = (
            await db.execute(select(Shop).filter(Shop.id == shop_id))
        ).scalar_one_or_none()

        if shop is None:
            results.append({
                "shop_id": shop_id,
                "found": False,
                "counts": {},
            })
            continue

        staff_id_subq = select(Staff.id).filter(Staff.shop_id == shop_id).scalar_subquery()

        # (テーブル名, モデル, フィルタ条件) を厳密な依存順に列挙
        steps = [
            ("reviews", Review, Review.shop_id == shop_id),
            ("reservations", Reservation, Reservation.shop_id == shop_id),
            ("staff_services", StaffService, StaffService.staff_id.in_(staff_id_subq)),
            ("staff_weekly_shifts", StaffWeeklyShift, StaffWeeklyShift.staff_id.in_(staff_id_subq)),
            ("staff_shift_overrides", StaffShiftOverride, StaffShiftOverride.staff_id.in_(staff_id_subq)),
            ("staff", Staff, Staff.shop_id == shop_id),
            ("coupons", Coupon, Coupon.shop_id == shop_id),
            ("promotions", Promotion, Promotion.shop_id == shop_id),
            ("services", Service, Service.shop_id == shop_id),
            ("shop_tables", ShopTable, ShopTable.shop_id == shop_id),
            ("shop_photos", ShopPhoto, ShopPhoto.shop_id == shop_id),
            ("menu_items", MenuItem, MenuItem.shop_id == shop_id),
            ("shop_hours", ShopHours, ShopHours.shop_id == shop_id),
            ("shop_closures", ShopClosure, ShopClosure.shop_id == shop_id),
            ("shop_analytics", ShopAnalytics, ShopAnalytics.shop_id == shop_id),
            ("monthly_analytics", MonthlyAnalytics, MonthlyAnalytics.shop_id == shop_id),
            ("user_shop_relations", UserShopRelation, UserShopRelation.shop_id == shop_id),
            ("ai_staff_settings", AIStaffSettings, AIStaffSettings.shop_id == shop_id),
            ("shop_knowledge", ShopKnowledge, ShopKnowledge.shop_id == shop_id),
            ("shop_faqs", ShopFAQ, ShopFAQ.shop_id == shop_id),
        ]

        counts = {}
        for table_name, model, condition in steps:
            if payload.dry_run:
                count_result = await db.execute(
                    select(func.count()).select_from(model).filter(condition)
                )
                counts[table_name] = int(count_result.scalar() or 0)
            else:
                delete_result = await db.execute(delete(model).where(condition))
                counts[table_name] = delete_result.rowcount or 0

        # 最後に Shop 本体
        if payload.dry_run:
            counts["shop"] = 1
        else:
            await db.execute(delete(Shop).where(Shop.id == shop_id))
            counts["shop"] = 1

        results.append({
            "shop_id": shop_id,
            "shop_name": shop.name,
            "found": True,
            "dry_run": payload.dry_run,
            "counts": counts,
        })

    if not payload.dry_run:
        await db.commit()

    return {"dry_run": payload.dry_run, "results": results}
