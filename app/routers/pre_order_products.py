"""
PHASE O4: 事前注文の商品マスター（PreOrderProduct）— Owner認証済みCRUD

app/routers/shop_media.pyの「/{shop_id}/menu」パターン（shop配下にネストした
CRUD）にURL構造を合わせる。PreOrderProductはMenuItemと同じく「店舗が持つ
カタログ的な子エンティティ」であり、Owner UI（shop-manage.html）上でも
メニューCRUDと同じ操作感（一覧・追加・編集・削除）を提供する想定のため。

file uploadが無いため、shop_media.pyのForm/Fileではなく通常のJSON body
（Pydantic schema）を使う。

Public向けエンドポイントは存在しない（Section21/35: 価格・自動確定ルールは
Owner認証済みルートからのみ参照可能。Public Product schemaはO5で検討）。
"""

import logging
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.schemas.user import CurrentUser
from app.models.shop import Shop
from app.models.pre_order import PreOrderProduct
from app.schemas.pre_order_product import (
    PreOrderProductCreateRequest,
    PreOrderProductUpdateRequest,
    PreOrderProductResponse,
)

logger = logging.getLogger("receptra.pre_order_products")

router = APIRouter(prefix="/api/v1/shops", tags=["pre-order-products"])


async def _get_owned_shop(shop_id: str, current_user: CurrentUser, db: AsyncSession) -> Shop:
    """app.routers.services._get_owned_shop / shop_media._get_owned_shopと
    同じチェック（モジュールごとの複製規約）。"""
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")
    if shop.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="この店舗を編集する権限がありません")
    return shop


def _to_response(product: PreOrderProduct) -> PreOrderProductResponse:
    return PreOrderProductResponse(
        id=product.id,
        shop_id=product.shop_id,
        name=product.name,
        description=product.description,
        price=product.price,
        is_active=product.is_active,
        display_order=product.display_order,
        auto_confirm_max_quantity=product.auto_confirm_max_quantity,
        minimum_lead_time_minutes=product.minimum_lead_time_minutes,
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


@router.get(
    "/{shop_id}/pre-order-products",
    response_model=List[PreOrderProductResponse],
    summary="事前注文の商品一覧を取得（オーナー専用）",
    description="価格・自動確定ルールを含む完全な情報を返す。オーナー認証必須・tenant分離必須。",
)
async def list_pre_order_products(
    shop_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> List[PreOrderProductResponse]:
    await _get_owned_shop(shop_id, current_user, db)
    result = await db.execute(
        select(PreOrderProduct).filter(PreOrderProduct.shop_id == shop_id)
        .order_by(PreOrderProduct.display_order, PreOrderProduct.created_at)
    )
    return [_to_response(p) for p in result.scalars().all()]


@router.post(
    "/{shop_id}/pre-order-products",
    response_model=PreOrderProductResponse,
    summary="事前注文の商品を新規作成（オーナー専用）",
)
async def create_pre_order_product(
    shop_id: str,
    request: PreOrderProductCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PreOrderProductResponse:
    await _get_owned_shop(shop_id, current_user, db)
    if request.shop_id != shop_id:
        raise HTTPException(status_code=400, detail="URLのshop_idとbodyのshop_idが一致しません")

    # Section10の一意性設計（(shop_id, name)にDB一意インデックス）に対する
    # 事前チェック。DB制約に頼るだけでなく、分かりやすい409を返す。
    existing = await db.execute(
        select(PreOrderProduct).filter(
            PreOrderProduct.shop_id == shop_id, PreOrderProduct.name == request.name
        )
    )
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail="同じ名前の商品が既に登録されています")

    result = await db.execute(
        select(PreOrderProduct).filter(PreOrderProduct.shop_id == shop_id)
        .order_by(PreOrderProduct.display_order.desc())
    )
    last = result.scalars().first()
    next_order = (last.display_order + 1) if last else 0

    now = datetime.utcnow()
    product = PreOrderProduct(
        id=str(uuid.uuid4()),
        shop_id=shop_id,
        name=request.name,
        description=request.description,
        price=request.price,
        auto_confirm_max_quantity=request.auto_confirm_max_quantity,
        minimum_lead_time_minutes=request.minimum_lead_time_minutes,
        is_active=True,
        display_order=next_order,
        created_at=now,
        updated_at=now,
    )
    db.add(product)
    await db.commit()
    await db.refresh(product)
    return _to_response(product)


async def _get_owned_product(shop_id: str, product_id: str, current_user: CurrentUser, db: AsyncSession) -> PreOrderProduct:
    await _get_owned_shop(shop_id, current_user, db)
    product = await db.get(PreOrderProduct, product_id)
    if not product or product.shop_id != shop_id:
        raise HTTPException(status_code=404, detail="商品が見つかりません")
    return product


@router.patch(
    "/{shop_id}/pre-order-products/{product_id}",
    response_model=PreOrderProductResponse,
    summary="事前注文の商品を更新（オーナー専用）",
    description="送られたフィールドのみ更新。is_active=falseで一時的に受付停止できる。",
)
async def update_pre_order_product(
    shop_id: str,
    product_id: str,
    request: PreOrderProductUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PreOrderProductResponse:
    product = await _get_owned_product(shop_id, product_id, current_user, db)

    update_data = request.dict(exclude_unset=True)
    if "name" in update_data and update_data["name"] != product.name:
        existing = await db.execute(
            select(PreOrderProduct).filter(
                PreOrderProduct.shop_id == shop_id,
                PreOrderProduct.name == update_data["name"],
                PreOrderProduct.id != product_id,
            )
        )
        if existing.scalars().first():
            raise HTTPException(status_code=409, detail="同じ名前の商品が既に登録されています")

    for field, value in update_data.items():
        setattr(product, field, value)
    product.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(product)
    return _to_response(product)


@router.delete(
    "/{shop_id}/pre-order-products/{product_id}",
    summary="事前注文の商品を削除（オーナー専用）",
    description=(
        "ハードデリート（app/routers/services.py・shop_media.pyのMenuItem削除と"
        "同じ規約）。PreOrderItemは商品名をsnapshotで保持するため、削除しても"
        "過去の事前注文表示には一切影響しない（Section22）。一時的に受付を"
        "止めたいだけの場合はPATCHでis_active=falseにすることを推奨する。"
    ),
)
async def delete_pre_order_product(
    shop_id: str,
    product_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    product = await _get_owned_product(shop_id, product_id, current_user, db)
    await db.delete(product)
    await db.commit()
    return {"success": True}
