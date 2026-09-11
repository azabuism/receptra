"""
受付スタッフサービス
"""

import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.receptionist import Receptionist


class ReceptionistService:
    """受付スタッフサービス"""

    @staticmethod
    async def create_receptionist(
        db: AsyncSession,
        tenant_id: str,
        name: str,
        email: str,
        phone: str = None,
        role: str = "receptionist",
        shift: str = "all-day",
    ) -> Receptionist:
        """
        新しい受付スタッフを作成

        Args:
            db: AsyncSession
            tenant_id: テナントID
            name: 受付スタッフ名
            email: メールアドレス
            phone: 電話番号
            role: ロール（receptionist, manager）
            shift: シフト（morning, afternoon, night, all-day）

        Returns:
            Receptionist オブジェクト
        """
        receptionist = Receptionist(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            name=name,
            email=email,
            phone=phone,
            role=role,
            shift=shift,
            is_active=True,
        )

        db.add(receptionist)
        await db.commit()
        await db.refresh(receptionist)

        return receptionist

    @staticmethod
    async def get_receptionist_by_id(
        db: AsyncSession, receptionist_id: str
    ) -> Receptionist:
        """受付スタッフをIDで取得"""
        return await db.get(Receptionist, receptionist_id)

    @staticmethod
    async def get_receptionists_by_tenant(
        db: AsyncSession, tenant_id: str
    ) -> list[Receptionist]:
        """テナント内のすべての受付スタッフを取得"""
        stmt = select(Receptionist).where(
            Receptionist.tenant_id == tenant_id,
            Receptionist.is_active == True,
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def update_receptionist(
        db: AsyncSession,
        receptionist: Receptionist,
        name: str = None,
        phone: str = None,
        role: str = None,
        shift: str = None,
    ) -> Receptionist:
        """受付スタッフ情報を更新"""
        if name is not None:
            receptionist.name = name
        if phone is not None:
            receptionist.phone = phone
        if role is not None:
            receptionist.role = role
        if shift is not None:
            receptionist.shift = shift

        db.add(receptionist)
        await db.commit()
        await db.refresh(receptionist)

        return receptionist

    @staticmethod
    async def delete_receptionist(
        db: AsyncSession, receptionist: Receptionist
    ) -> None:
        """受付スタッフを論理削除"""
        receptionist.is_active = False
        db.add(receptionist)
        await db.commit()
