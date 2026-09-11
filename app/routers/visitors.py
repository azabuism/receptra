"""
来訪者管理エンドポイント
POST /visitors, GET /visitors, /visitors/{id}, PUT /visitors/{id}, DELETE /visitors/{id}
POST /visitors/{id}/check-in, POST /visitors/{id}/check-out
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_current_user
from app.models.visitor import Visitor
from app.schemas.visitor import (
    VisitorCreate,
    VisitorUpdate,
    VisitorResponse,
)
from app.schemas.user import CurrentUser
from app.services.visitor import VisitorService

router = APIRouter()


@router.post("", response_model=VisitorResponse, status_code=status.HTTP_201_CREATED)
async def create_visitor(
    visitor_data: VisitorCreate,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    新しい来訪者を登録
    """
    visitor = await VisitorService.create_visitor(
        db=db,
        tenant_id=current_user.tenant_id,
        name=visitor_data.name,
        email=visitor_data.email,
        phone=visitor_data.phone,
        company=visitor_data.company,
        purpose=visitor_data.purpose,
        host_id=visitor_data.host_id,
    )
    return visitor


@router.get("", response_model=List[VisitorResponse])
async def list_visitors(
    status: Optional[str] = Query(None),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    テナント内のすべての来訪者を取得
    オプション: ステータスでフィルタリング (pending/checked_in/checked_out)
    """
    visitors = await VisitorService.get_visitors_by_tenant(
        db=db,
        tenant_id=current_user.tenant_id,
        status=status,
    )
    return visitors


@router.get("/{visitor_id}", response_model=VisitorResponse)
async def get_visitor(
    visitor_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    特定の来訪者情報を取得
    テナント内のみアクセス可能
    """
    visitor = await VisitorService.get_visitor_by_id(
        db=db,
        visitor_id=visitor_id,
    )

    if not visitor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Visitor not found",
        )

    # テナント検証
    if visitor.tenant_id != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access visitor from different tenant",
        )

    return visitor


@router.put("/{visitor_id}", response_model=VisitorResponse)
async def update_visitor(
    visitor_id: str,
    visitor_update: VisitorUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    来訪者情報を更新
    """
    visitor = await VisitorService.get_visitor_by_id(
        db=db,
        visitor_id=visitor_id,
    )

    if not visitor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Visitor not found",
        )

    # テナント検証
    if visitor.tenant_id != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access visitor from different tenant",
        )

    # 情報を更新
    visitor = await VisitorService.update_visitor(
        db=db,
        visitor=visitor,
        name=visitor_update.name,
        phone=visitor_update.phone,
        company=visitor_update.company,
        purpose=visitor_update.purpose,
        host_id=visitor_update.host_id,
    )

    return visitor


@router.post("/{visitor_id}/check-in", response_model=VisitorResponse)
async def check_in_visitor(
    visitor_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    来訪者をチェックイン
    ステータスを 'checked_in' に変更し、check_in_at を記録
    """
    visitor = await VisitorService.get_visitor_by_id(
        db=db,
        visitor_id=visitor_id,
    )

    if not visitor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Visitor not found",
        )

    # テナント検証
    if visitor.tenant_id != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access visitor from different tenant",
        )

    visitor = await VisitorService.check_in_visitor(db=db, visitor=visitor)
    return visitor


@router.post("/{visitor_id}/check-out", response_model=VisitorResponse)
async def check_out_visitor(
    visitor_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    来訪者をチェックアウト
    ステータスを 'checked_out' に変更し、check_out_at を記録
    """
    visitor = await VisitorService.get_visitor_by_id(
        db=db,
        visitor_id=visitor_id,
    )

    if not visitor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Visitor not found",
        )

    # テナント検証
    if visitor.tenant_id != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access visitor from different tenant",
        )

    visitor = await VisitorService.check_out_visitor(db=db, visitor=visitor)
    return visitor


@router.delete("/{visitor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_visitor(
    visitor_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    来訪者を論理削除
    """
    visitor = await VisitorService.get_visitor_by_id(
        db=db,
        visitor_id=visitor_id,
    )

    if not visitor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Visitor not found",
        )

    # テナント検証
    if visitor.tenant_id != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access visitor from different tenant",
        )

    await VisitorService.delete_visitor(db=db, visitor=visitor)
