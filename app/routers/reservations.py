"""
Reservation (予約) エンドポイント
予約の作成、確認、管理機能
"""

import uuid
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.reservation import Reservation
from app.models.shop import Shop
from app.models.customer import Customer
from app.schemas.reservation import (
    ReservationCreateRequest, ReservationResponse, ReservationCreateResponse,
    ReservationUpdateRequest, ReservationListResponse
)

router = APIRouter(prefix="/api/v1/reservations", tags=["reservations"])


@router.post(
    "/create",
    response_model=ReservationCreateResponse,
    summary="新規予約を作成",
    description="新しい予約を作成して予約IDを取得"
)
async def create_reservation(
    request: ReservationCreateRequest,
    db: AsyncSession = Depends(get_db)
) -> ReservationCreateResponse:
    """
    予約を作成します
    
    - **shop_id**: 店舗ID（必須）
    - **customer_id**: 顧客ID（必須）
    - **reservation_date**: 予約日時（必須）
    - **number_of_people**: 人数（必須）
    - **special_requests**: 特別リクエスト（オプション）
    """
    try:
        # 店舗の存在確認
        shop = await db.get(Shop, request.shop_id)
        if not shop:
            raise HTTPException(
                status_code=404,
                detail="指定された店舗が見つかりません"
            )
        
        # 顧客の存在確認
        customer = await db.get(Customer, request.customer_id)
        if not customer:
            raise HTTPException(
                status_code=404,
                detail="指定された顧客が見つかりません"
            )
        
        # 予約日時が過去でないか確認
        if request.reservation_date <= datetime.utcnow():
            raise HTTPException(
                status_code=400,
                detail="過去の日時で予約することはできません"
            )
        
        # 予約IDを生成
        reservation_id = str(uuid.uuid4())
        
        # 予約オブジェクトを作成
        reservation = Reservation(
            id=reservation_id,
            shop_id=request.shop_id,
            customer_id=request.customer_id,
            reservation_date=request.reservation_date,
            number_of_people=request.number_of_people,
            status="PENDING",
            special_requests=request.special_requests,
            reservation_source="api",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        db.add(reservation)
        
        # 店舗の予約数をインクリメント
        if shop.total_reservations is None:
            shop.total_reservations = 0
        shop.total_reservations += 1
        shop.updated_at = datetime.utcnow()
        
        # 顧客の予約数をインクリメント
        if customer.total_reservations is None:
            customer.total_reservations = 0
        customer.total_reservations += 1
        customer.updated_at = datetime.utcnow()
        
        await db.commit()
        await db.refresh(reservation)
        
        # レスポンス作成
        reservation_response = ReservationResponse.from_orm(reservation)
        
        return ReservationCreateResponse(
            success=True,
            message="予約が正常に作成されました",
            reservation_id=reservation_id,
            reservation=reservation_response
        )
    
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"予約作成に失敗しました: {str(e)}"
        )


@router.get(
    "/{reservation_id}",
    response_model=ReservationResponse,
    summary="予約詳細を取得",
    description="指定された予約IDの詳細情報を取得"
)
async def get_reservation(
    reservation_id: str,
    db: AsyncSession = Depends(get_db)
) -> ReservationResponse:
    """
    指定された予約の詳細情報を取得します
    """
    try:
        reservation = await db.get(Reservation, reservation_id)
        
        if not reservation:
            raise HTTPException(
                status_code=404,
                detail="予約が見つかりません"
            )
        
        return ReservationResponse.from_orm(reservation)
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"予約情報取得に失敗しました: {str(e)}"
        )


@router.get(
    "/shop/{shop_id}",
    response_model=ReservationListResponse,
    summary="店舗の予約一覧を取得",
    description="指定された店舗の予約一覧を取得（ステータスで絞込可能）"
)
async def get_shop_reservations(
    shop_id: str,
    status: Optional[str] = Query(None, description="ステータスフィルタ"),
    limit: int = Query(20, ge=1, le=100, description="取得件数"),
    offset: int = Query(0, ge=0, description="オフセット"),
    db: AsyncSession = Depends(get_db)
) -> ReservationListResponse:
    """
    店舗の予約一覧を取得します
    
    - **status**: フィルタするステータス（PENDING, CONFIRMED, CANCELLED, NO_SHOW, COMPLETED）
    """
    try:
        # 店舗の存在確認
        shop = await db.get(Shop, shop_id)
        if not shop:
            raise HTTPException(
                status_code=404,
                detail="指定された店舗が見つかりません"
            )
        
        # 基本クエリ
        query = db.query(Reservation).filter(Reservation.shop_id == shop_id)
        
        # ステータスフィルタ
        if status:
            query = query.filter(Reservation.status == status)
        
        # 総件数取得
        total = await db.scalar(func.count(Reservation.id).select_from(query.statement.get_final_table()))
        
        # ソート（予約日時の昇順）
        reservations = await db.scalars(
            query.order_by(Reservation.reservation_date.asc())
            .offset(offset)
            .limit(limit)
        )
        
        reservation_list = [ReservationResponse.from_orm(r) for r in reservations]
        
        return ReservationListResponse(
            total=total or 0,
            limit=limit,
            offset=offset,
            items=reservation_list
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"予約一覧取得に失敗しました: {str(e)}"
        )


@router.put(
    "/{reservation_id}",
    response_model=ReservationResponse,
    summary="予約を更新",
    description="予約情報を更新（ステータス変更、特別リクエスト編集など）"
)
async def update_reservation(
    reservation_id: str,
    request: ReservationUpdateRequest,
    db: AsyncSession = Depends(get_db)
) -> ReservationResponse:
    """
    予約を更新します
    
    - **status**: ステータス変更
    - **cancellation_reason**: キャンセル理由
    - **number_of_people**: 人数変更
    - **special_requests**: 特別リクエスト編集
    """
    try:
        reservation = await db.get(Reservation, reservation_id)
        
        if not reservation:
            raise HTTPException(
                status_code=404,
                detail="予約が見つかりません"
            )
        
        # ステータス更新時の処理
        if request.status and request.status != reservation.status:
            if request.status == "CANCELLED":
                reservation.cancelled_at = datetime.utcnow()
                reservation.cancellation_reason = request.cancellation_reason
            elif request.status == "COMPLETED":
                reservation.arrived_at = datetime.utcnow()
            
            reservation.status = request.status
        
        # その他のフィールド更新
        if request.number_of_people:
            reservation.number_of_people = request.number_of_people
        
        if request.special_requests is not None:
            reservation.special_requests = request.special_requests
        
        reservation.updated_at = datetime.utcnow()
        
        await db.commit()
        await db.refresh(reservation)
        
        return ReservationResponse.from_orm(reservation)
    
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"予約更新に失敗しました: {str(e)}"
        )


@router.delete(
    "/{reservation_id}",
    summary="予約をキャンセル",
    description="予約をキャンセルします"
)
async def cancel_reservation(
    reservation_id: str,
    reason: Optional[str] = Query(None, description="キャンセル理由"),
    db: AsyncSession = Depends(get_db)
):
    """
    予約をキャンセルします
    """
    try:
        reservation = await db.get(Reservation, reservation_id)
        
        if not reservation:
            raise HTTPException(
                status_code=404,
                detail="予約が見つかりません"
            )
        
        reservation.status = "CANCELLED"
        reservation.cancelled_at = datetime.utcnow()
        reservation.cancellation_reason = reason
        reservation.updated_at = datetime.utcnow()
        
        await db.commit()
        
        return {
            "success": True,
            "message": "予約がキャンセルされました",
            "reservation_id": reservation_id
        }
    
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"予約キャンセルに失敗しました: {str(e)}"
        )
