"""
受付スタッフ管理エンドポイント
POST /receptionists, GET /receptionists, /receptionists/{id}, PUT /receptionists/{id}, DELETE /receptionists/{id}
"""

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_current_user, get_current_admin
from app.models.receptionist import Receptionist
from app.schemas.receptionist import (
    ReceptionistCreate,
    ReceptionistUpdate,
    ReceptionistResponse,
)
from app.schemas.user import CurrentUser
from app.services.receptionist import ReceptionistService

router = APIRouter()


@router.post("", response_model=ReceptionistResponse, status_code=status.HTTP_201_CREATED)
async def create_receptionist(
    receptionist_data: ReceptionistCreate,
    current_user: CurrentUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    新しい受付スタッフを追加
    テナント管理者のみ可能
    """
    receptionist = await ReceptionistService.create_receptionist(
        db=db,
        tenant_id=current_user.tenant_id,
        name=receptionist_data.name,
        email=receptionist_data.email,
        phone=receptionist_data.phone,
        role=receptionist_data.role,
        shift=receptionist_data.shift,
    )
    return receptionist


@router.get("", response_model=List[ReceptionistResponse])
async def list_receptionists(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    テナント内のすべての受付スタッフを取得
    """
    receptionists = await ReceptionistService.get_receptionists_by_tenant(
        db=db,
        tenant_id=current_user.tenant_id,
    )
    return receptionists


@router.get("/{receptionist_id}", response_model=ReceptionistResponse)
async def get_receptionist(
    receptionist_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    特定の受付スタッフ情報を取得
    テナント内のみアクセス可能
    """
    receptionist = await ReceptionistService.get_receptionist_by_id(
        db=db,
        receptionist_id=receptionist_id,
    )

    if not receptionist:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Receptionist not found",
        )

    # テナント検証
    if receptionist.tenant_id != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access receptionist from different tenant",
        )

    return receptionist


@router.put("/{receptionist_id}", response_model=ReceptionistResponse)
async def update_receptionist(
    receptionist_id: str,
    receptionist_update: ReceptionistUpdate,
    current_user: CurrentUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    受付スタッフ情報を更新
    テナント管理者のみ可能
    """
    receptionist = await ReceptionistService.get_receptionist_by_id(
        db=db,
        receptionist_id=receptionist_id,
    )

    if not receptionist:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Receptionist not found",
        )

    # テナント検証
    if receptionist.tenant_id != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access receptionist from different tenant",
        )

    # 情報を更新
    receptionist = await ReceptionistService.update_receptionist(
        db=db,
        receptionist=receptionist,
        name=receptionist_update.name,
        phone=receptionist_update.phone,
        role=receptionist_update.role,
        shift=receptionist_update.shift,
    )

    return receptionist


@router.delete("/{receptionist_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_receptionist(
    receptionist_id: str,
    current_user: CurrentUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    受付スタッフを論理削除
    テナント管理者のみ可能
    """
    receptionist = await ReceptionistService.get_receptionist_by_id(
        db=db,
        receptionist_id=receptionist_id,
    )

    if not receptionist:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Receptionist not found",
        )

    # テナント検証
    if receptionist.tenant_id != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access receptionist from different tenant",
        )

    # 論理削除
    await ReceptionistService.delete_receptionist(db=db, receptionist=receptionist)
