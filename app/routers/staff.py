"""
Staff API Router
スタッフ/講師管理エンドポイント
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.models import Staff, Shop, Service, StaffService

router = APIRouter(
    prefix="/api/v1/staff",
    tags=["staff"]
)


@router.get("")
def get_staff(
    shop_id: Optional[str] = Query(None),
    specialty: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """Get staff members by shop or specialty"""
    query = db.query(Staff)
    
    if shop_id:
        query = query.filter(Staff.shop_id == shop_id)
    if specialty:
        query = query.filter(Staff.specialty.ilike(f"%{specialty}%"))
    
    staff = query.offset(skip).limit(limit).all()
    return [
        {
            "id": s.id,
            "shop_id": s.shop_id,
            "name": s.name,
            "email": s.email,
            "phone": s.phone,
            "specialty": s.specialty,
            "position": s.position,
            "is_active": s.is_active,
            "total_reservations": s.total_reservations,
            "average_rating": s.average_rating,
        }
        for s in staff
    ]


@router.get("/{staff_id}")
def get_staff_by_id(staff_id: str, db: Session = Depends(get_db)):
    """Get a specific staff member"""
    staff = db.query(Staff).filter(Staff.id == staff_id).first()
    if not staff:
        raise HTTPException(status_code=404, detail="Staff not found")
    
    return {
        "id": staff.id,
        "shop_id": staff.shop_id,
        "name": staff.name,
        "email": staff.email,
        "phone": staff.phone,
        "bio": staff.bio,
        "photo_url": staff.photo_url,
        "specialty": staff.specialty,
        "qualifications": staff.qualifications,
        "position": staff.position,
        "is_active": staff.is_active,
        "total_reservations": staff.total_reservations,
        "average_rating": staff.average_rating,
    }


@router.post("")
def create_staff(
    shop_id: str,
    name: str,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    bio: Optional[str] = None,
    photo_url: Optional[str] = None,
    specialty: Optional[str] = None,
    qualifications: Optional[str] = None,
    position: Optional[str] = None,
    db: Session = Depends(get_db) = None,
):
    """Create a new staff member"""
    
    shop = db.query(Shop).filter(Shop.id == shop_id).first()
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")
    
    staff = Staff(
        shop_id=shop_id,
        name=name,
        email=email,
        phone=phone,
        bio=bio,
        photo_url=photo_url,
        specialty=specialty,
        qualifications=qualifications,
        position=position,
    )
    
    db.add(staff)
    db.commit()
    db.refresh(staff)
    
    return {
        "id": staff.id,
        "shop_id": staff.shop_id,
        "name": staff.name,
        "email": staff.email,
        "phone": staff.phone,
        "specialty": staff.specialty,
        "position": staff.position,
        "is_active": staff.is_active,
    }


@router.put("/{staff_id}")
def update_staff(
    staff_id: str,
    name: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    bio: Optional[str] = None,
    photo_url: Optional[str] = None,
    specialty: Optional[str] = None,
    qualifications: Optional[str] = None,
    position: Optional[str] = None,
    is_active: Optional[str] = None,
    db: Session = Depends(get_db) = None,
):
    """Update staff information"""
    staff = db.query(Staff).filter(Staff.id == staff_id).first()
    if not staff:
        raise HTTPException(status_code=404, detail="Staff not found")
    
    if name is not None:
        staff.name = name
    if email is not None:
        staff.email = email
    if phone is not None:
        staff.phone = phone
    if bio is not None:
        staff.bio = bio
    if photo_url is not None:
        staff.photo_url = photo_url
    if specialty is not None:
        staff.specialty = specialty
    if qualifications is not None:
        staff.qualifications = qualifications
    if position is not None:
        staff.position = position
    if is_active is not None:
        staff.is_active = is_active
    
    db.add(staff)
    db.commit()
    db.refresh(staff)
    
    return {"message": "Staff updated"}


@router.delete("/{staff_id}")
def delete_staff(staff_id: str, db: Session = Depends(get_db)):
    """Delete a staff member"""
    staff = db.query(Staff).filter(Staff.id == staff_id).first()
    if not staff:
        raise HTTPException(status_code=404, detail="Staff not found")
    
    db.delete(staff)
    db.commit()
    return {"message": "Staff deleted"}


@router.post("/{staff_id}/services/{service_id}")
def add_staff_service(
    staff_id: str,
    service_id: str,
    db: Session = Depends(get_db)
):
    """Add a service to staff member"""
    staff = db.query(Staff).filter(Staff.id == staff_id).first()
    if not staff:
        raise HTTPException(status_code=404, detail="Staff not found")
    
    service = db.query(Service).filter(Service.id == service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")
    
    existing = db.query(StaffService).filter(
        StaffService.staff_id == staff_id,
        StaffService.service_id == service_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Service already added")
    
    staff_service = StaffService(staff_id=staff_id, service_id=service_id)
    db.add(staff_service)
    db.commit()
    return {"message": "Service added"}


@router.get("/{staff_id}/services")
def get_staff_services(staff_id: str, db: Session = Depends(get_db)):
    """Get services provided by staff"""
    staff = db.query(Staff).filter(Staff.id == staff_id).first()
    if not staff:
        raise HTTPException(status_code=404, detail="Staff not found")
    
    services = db.query(Service).join(
        StaffService,
        StaffService.service_id == Service.id
    ).filter(StaffService.staff_id == staff_id).all()
    
    return [{"id": s.id, "name": s.name, "base_price": s.base_price} for s in services]


@router.delete("/{staff_id}/services/{service_id}")
def remove_staff_service(
    staff_id: str,
    service_id: str,
    db: Session = Depends(get_db)
):
    """Remove a service from staff"""
    staff_service = db.query(StaffService).filter(
        StaffService.staff_id == staff_id,
        StaffService.service_id == service_id
    ).first()
    if not staff_service:
        raise HTTPException(status_code=404, detail="Service not found for this staff")
    
    db.delete(staff_service)
    db.commit()
    return {"message": "Service removed"}
