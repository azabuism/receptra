"""
Services API Router
業種別のサービス管理エンドポイント（施術・コース・診療メニューなど）
"""

import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.schemas.user import CurrentUser
from app.models import Service, Shop
from app.schemas.service import ServiceCreateRequest, ServiceUpdateRequest, ServiceResponse

router = APIRouter(prefix="/api/v1/services", tags=["services"])


def _to_response(service: Service) -> ServiceResponse:
    return ServiceResponse(
        id=service.id,
        shop_id=service.shop_id,
        name=service.name,
        description=service.description,
        base_price=service.base_price,
        duration_minutes=service.duration_minutes,
        service_type=service.service_type,
        is_active=(service.is_active == "active"),
        created_at=service.created_at,
        updated_at=service.updated_at,
    )


async def _get_owned_shop(shop_id: str, current_user: CurrentUser, db: AsyncSession) -> Shop:
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")
    if shop.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="この店舗を編集する権限がありません")
    return shop


async def _get_owned_service(service_id: str, current_user: CurrentUser, db: AsyncSession) -> Service:
    service = await db.get(Service, service_id)
    if not service:
        raise HTTPException(status_code=404, detail="サービスが見つかりません")
    await _get_owned_shop(service.shop_id, current_user, db)
    return service


@router.get("", response_model=List[ServiceResponse], summary="サービス一覧を取得")
async def get_services(
    shop_id: Optional[str] = Query(None, description="店舗IDで絞り込み"),
    business_type: Optional[str] = Query(None, description="業種で絞り込み"),
    is_active: Optional[bool] = Query(None, description="有効/無効で絞り込み"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """サービス一覧を取得（店舗ページ・予約フォームからの公開参照用）"""
    query = select(Service)

    if shop_id:
        query = query.filter(Service.shop_id == shop_id)
    if business_type:
        query = query.join(Shop, Shop.id == Service.shop_id).filter(Shop.business_type == business_type)
    if is_active is not None:
        query = query.filter(Service.is_active == ("active" if is_active else "inactive"))

    query = query.order_by(Service.created_at).offset(skip).limit(limit)
    result = await db.execute(query)
    return [_to_response(s) for s in result.scalars().all()]


@router.get("/{service_id}", response_model=ServiceResponse, summary="サービス詳細を取得")
async def get_service(service_id: str, db: AsyncSession = Depends(get_db)):
    service = await db.get(Service, service_id)
    if not service:
        raise HTTPException(status_code=404, detail="サービスが見つかりません")
    return _to_response(service)


@router.post("", response_model=ServiceResponse, summary="サービスを新規作成")
async def create_service(
    request: ServiceCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_shop(request.shop_id, current_user, db)

    now = datetime.utcnow()
    service = Service(
        id=str(uuid.uuid4()),
        shop_id=request.shop_id,
        name=request.name,
        description=request.description,
        base_price=request.base_price,
        duration_minutes=request.duration_minutes,
        service_type=request.service_type,
        is_active="active",
        created_at=now,
        updated_at=now,
    )
    db.add(service)
    await db.commit()
    await db.refresh(service)
    return _to_response(service)


@router.put("/{service_id}", response_model=ServiceResponse, summary="サービスを更新")
async def update_service(
    service_id: str,
    request: ServiceUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = await _get_owned_service(service_id, current_user, db)

    update_data = request.dict(exclude_unset=True)
    if "is_active" in update_data:
        is_active_bool = update_data.pop("is_active")
        service.is_active = "active" if is_active_bool else "inactive"
    for field, value in update_data.items():
        setattr(service, field, value)
    service.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(service)
    return _to_response(service)


@router.delete("/{service_id}", summary="サービスを削除")
async def delete_service(
    service_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = await _get_owned_service(service_id, current_user, db)
    await db.delete(service)
    await db.commit()
    return {"success": True}
