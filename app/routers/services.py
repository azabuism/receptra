"""
Services API Router
業種別のサービス管理エンドポイント
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.models import Service, Shop

router = APIRouter(
    prefix="/api/v1/services",
    tags=["services"]
)


@router.get("")
def get_services(
    shop_id: Optional[str] = Query(None),
    business_type: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """
    Get services
    
    Parameters:
    - shop_id: Filter by shop ID
    - business_type: Filter by business type (restaurant, beauty, hotel, etc)
    - skip: Number of records to skip
    - limit: Max number of records to return
    """
    query = db.query(Service)
    
    if shop_id:
        query = query.filter(Service.shop_id == shop_id)
    
    if business_type:
        query = query.join(Shop).filter(Shop.business_type == business_type)
    
    services = query.offset(skip).limit(limit).all()
    return [
        {
            "id": s.id,
            "shop_id": s.shop_id,
            "name": s.name,
            "description": s.description,
            "base_price": s.base_price,
            "duration_minutes": s.duration_minutes,
            "service_type": s.service_type,
            "is_active": s.is_active,
            "created_at": s.created_at,
        }
        for s in services
    ]


@router.get("/{service_id}")
def get_service(service_id: str, db: Session = Depends(get_db)):
    """Get a specific service"""
    service = db.query(Service).filter(Service.id == service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")
    
    return {
        "id": service.id,
        "shop_id": service.shop_id,
        "name": service.name,
        "description": service.description,
        "base_price": service.base_price,
        "duration_minutes": service.duration_minutes,
        "service_type": service.service_type,
        "is_active": service.is_active,
        "created_at": service.created_at,
    }


@router.post("")
def create_service(
    shop_id: str,
    name: str,
    base_price: float,
    description: Optional[str] = None,
    duration_minutes: Optional[int] = None,
    service_type: Optional[str] = None,
    db: Session = Depends(get_db) = None,
):
    """Create a new service"""
    
    shop = db.query(Shop).filter(Shop.id == shop_id).first()
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")
    
    service = Service(
        shop_id=shop_id,
        name=name,
        base_price=base_price,
        description=description,
        duration_minutes=duration_minutes,
        service_type=service_type,
    )
    
    db.add(service)
    db.commit()
    db.refresh(service)
    
    return {
        "id": service.id,
        "shop_id": service.shop_id,
        "name": service.name,
        "description": service.description,
        "base_price": service.base_price,
        "duration_minutes": service.duration_minutes,
        "service_type": service.service_type,
        "is_active": service.is_active,
        "created_at": service.created_at,
    }


@router.put("/{service_id}")
def update_service(
    service_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    base_price: Optional[float] = None,
    duration_minutes: Optional[int] = None,
    service_type: Optional[str] = None,
    is_active: Optional[str] = None,
    db: Session = Depends(get_db) = None,
):
    """Update a service"""
    service = db.query(Service).filter(Service.id == service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")
    
    if name is not None:
        service.name = name
    if description is not None:
        service.description = description
    if base_price is not None:
        service.base_price = base_price
    if duration_minutes is not None:
        service.duration_minutes = duration_minutes
    if service_type is not None:
        service.service_type = service_type
    if is_active is not None:
        service.is_active = is_active
    
    db.add(service)
    db.commit()
    db.refresh(service)
    
    return {
        "id": service.id,
        "shop_id": service.shop_id,
        "name": service.name,
        "description": service.description,
        "base_price": service.base_price,
        "duration_minutes": service.duration_minutes,
        "service_type": service.service_type,
        "is_active": service.is_active,
        "created_at": service.created_at,
    }


@router.delete("/{service_id}")
def delete_service(service_id: str, db: Session = Depends(get_db)):
    """Delete a service"""
    service = db.query(Service).filter(Service.id == service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")
    
    db.delete(service)
    db.commit()
    return {"message": "Service deleted successfully"}
