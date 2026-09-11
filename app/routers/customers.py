"""
Customer (顧客) エンドポイント
顧客の登録、検索、管理機能
"""

import uuid
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.customer import Customer
from app.schemas.customer import (
    CustomerRegisterRequest, CustomerResponse, CustomerRegisterResponse,
    CustomerUpdateRequest, CustomerListResponse
)

router = APIRouter(prefix="/api/v1/customers", tags=["customers"])


@router.post(
    "/register",
    response_model=CustomerRegisterResponse,
    summary="新規顧客を登録",
    description="新しい顧客情報を登録して顧客IDを取得"
)
async def register_customer(
    request: CustomerRegisterRequest,
    db: AsyncSession = Depends(get_db)
) -> CustomerRegisterResponse:
    """
    顧客を登録します
    
    - **email**: メールアドレス（必須・ユニーク）
    - **display_name**: 表示名（必須）
    - **phone**: 電話番号（オプション）
    - **address**: 住所（オプション）
    """
    try:
        # テナントID（仮：実装時は認証から取得）
        tenant_id = "default-tenant"
        
        # メールアドレスの重複確認
        existing_customer = await db.scalar(
            db.query(Customer).filter(
                and_(
                    Customer.tenant_id == tenant_id,
                    Customer.email == request.email
                )
            ).statement
        )
        
        if existing_customer:
            raise HTTPException(
                status_code=400,
                detail="このメールアドレスは既に登録されています"
            )
        
        # 顧客IDを生成
        customer_id = str(uuid.uuid4())
        
        # 顧客オブジェクトを作成
        customer = Customer(
            id=customer_id,
            tenant_id=tenant_id,
            email=request.email,
            phone=request.phone,
            display_name=request.display_name,
            avatar_url=request.avatar_url,
            address=request.address,
            latitude=request.latitude,
            longitude=request.longitude,
            is_verified=False,
            is_active=True,
            total_reservations=0,
            total_visits=0,
            total_spent=0.0,
            first_visit_date=datetime.utcnow(),
            vip_level="regular",
            customer_lifetime_value=0.0,
            newsletter_subscribed=False,
            sms_subscribed=False,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        db.add(customer)
        await db.commit()
        await db.refresh(customer)
        
        # レスポンス作成
        customer_response = CustomerResponse.from_orm(customer)
        
        return CustomerRegisterResponse(
            success=True,
            message="顧客が正常に登録されました",
            customer_id=customer_id,
            customer=customer_response
        )
    
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"顧客登録に失敗しました: {str(e)}"
        )


@router.get(
    "/{customer_id}",
    response_model=CustomerResponse,
    summary="顧客詳細を取得",
    description="指定された顧客IDの詳細情報を取得"
)
async def get_customer(
    customer_id: str,
    db: AsyncSession = Depends(get_db)
) -> CustomerResponse:
    """
    指定された顧客の詳細情報を取得します
    """
    try:
        customer = await db.get(Customer, customer_id)
        
        if not customer:
            raise HTTPException(
                status_code=404,
                detail="顧客が見つかりません"
            )
        
        return CustomerResponse.from_orm(customer)
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"顧客情報取得に失敗しました: {str(e)}"
        )


@router.get(
    "/search/by-email",
    response_model=CustomerResponse,
    summary="メールアドレスで顧客を検索",
    description="指定されたメールアドレスの顧客を検索"
)
async def get_customer_by_email(
    email: str = Query(..., description="メールアドレス"),
    db: AsyncSession = Depends(get_db)
) -> CustomerResponse:
    """
    メールアドレスで顧客を検索します
    """
    try:
        tenant_id = "default-tenant"
        
        customer = await db.scalar(
            db.query(Customer).filter(
                and_(
                    Customer.tenant_id == tenant_id,
                    Customer.email == email
                )
            ).statement
        )
        
        if not customer:
            raise HTTPException(
                status_code=404,
                detail="指定されたメールアドレスの顧客が見つかりません"
            )
        
        return CustomerResponse.from_orm(customer)
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"顧客検索に失敗しました: {str(e)}"
        )


@router.put(
    "/{customer_id}",
    response_model=CustomerResponse,
    summary="顧客情報を更新",
    description="顧客情報を更新"
)
async def update_customer(
    customer_id: str,
    request: CustomerUpdateRequest,
    db: AsyncSession = Depends(get_db)
) -> CustomerResponse:
    """
    顧客情報を更新します
    """
    try:
        customer = await db.get(Customer, customer_id)
        
        if not customer:
            raise HTTPException(
                status_code=404,
                detail="顧客が見つかりません"
            )
        
        # 更新可能なフィールド
        if request.display_name is not None:
            customer.display_name = request.display_name
        if request.phone is not None:
            customer.phone = request.phone
        if request.avatar_url is not None:
            customer.avatar_url = request.avatar_url
        if request.address is not None:
            customer.address = request.address
        if request.latitude is not None:
            customer.latitude = request.latitude
        if request.longitude is not None:
            customer.longitude = request.longitude
        if request.newsletter_subscribed is not None:
            customer.newsletter_subscribed = request.newsletter_subscribed
        if request.sms_subscribed is not None:
            customer.sms_subscribed = request.sms_subscribed
        
        customer.updated_at = datetime.utcnow()
        
        await db.commit()
        await db.refresh(customer)
        
        return CustomerResponse.from_orm(customer)
    
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"顧客情報更新に失敗しました: {str(e)}"
        )
