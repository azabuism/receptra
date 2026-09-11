"""
来訪者サービス
"""

import uuid
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.visitor import Visitor


class VisitorService:
    """来訪者サービス"""

    @staticmethod
    async def create_visitor(
        db: AsyncSession,
        tenant_id: str,
        name: str,
        email: str,
        purpose: str,
        host_id: str,
        phone: str = None,
        company: str = None,
    ) -> Visitor:
        """
        新しい来訪者を登録

        Args:
            db: AsyncSession
            tenant_id: テナントID
            name: 来訪者名
            email: メールアドレス
            purpose: 訪問目的
            host_id: ホスト（受け入れ担当者）のID
            phone: 電話番号
            company: 会社名

        Returns:
            Visitor オブジェクト
        """
        visitor = Visitor(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            name=name,
            email=email,
            purpose=purpose,
            host_id=host_id,
            phone=phone,
            company=company,
            status="pending",
            is_active=True,
        )

        db.add(visitor)
        await db.commit()
        await db.refresh(visitor)

        return visitor

    @staticmethod
    async def get_visitor_by_id(
        db: AsyncSession, visitor_id: str
    ) -> Visitor:
        """来訪者をIDで取得"""
        return await db.get(Visitor, visitor_id)

    @staticmethod
    async def get_visitors_by_tenant(
        db: AsyncSession, tenant_id: str, status: str = None
    ) -> list[Visitor]:
        """テナント内のすべての来訪者を取得"""
        stmt = select(Visitor).where(
            Visitor.tenant_id == tenant_id,
            Visitor.is_active == True,
        )
        if status:
            stmt = stmt.where(Visitor.status == status)
        
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def update_visitor(
        db: AsyncSession,
        visitor: Visitor,
        name: str = None,
        phone: str = None,
        company: str = None,
        purpose: str = None,
        host_id: str = None,
    ) -> Visitor:
        """来訪者情報を更新"""
        if name is not None:
            visitor.name = name
        if phone is not None:
            visitor.phone = phone
        if company is not None:
            visitor.company = company
        if purpose is not None:
            visitor.purpose = purpose
        if host_id is not None:
            visitor.host_id = host_id

        db.add(visitor)
        await db.commit()
        await db.refresh(visitor)

        return visitor

    @staticmethod
    async def check_in_visitor(
        db: AsyncSession, visitor: Visitor
    ) -> Visitor:
        """来訪者をチェックイン"""
        visitor.status = "checked_in"
        visitor.check_in_at = datetime.utcnow()
        db.add(visitor)
        await db.commit()
        await db.refresh(visitor)
        return visitor

    @staticmethod
    async def check_out_visitor(
        db: AsyncSession, visitor: Visitor
    ) -> Visitor:
        """来訪者をチェックアウト"""
        visitor.status = "checked_out"
        visitor.check_out_at = datetime.utcnow()
        db.add(visitor)
        await db.commit()
        await db.refresh(visitor)
        return visitor

    @staticmethod
    async def delete_visitor(
        db: AsyncSession, visitor: Visitor
    ) -> None:
        """来訪者を論理削除"""
        visitor.is_active = False
        db.add(visitor)
        await db.commit()
