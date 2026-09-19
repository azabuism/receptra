"""
ソーシャル機能モデル
ユーザー（マイページ）と店舗の関係性: 行きたい店・いいね
"""

from datetime import datetime
import uuid

from sqlalchemy import Column, String, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import relationship
from app.database import Base


class UserShopRelation(Base):
    """ユーザーと店舗の関係（行きたい店 / いいね）

    relation_type:
        - "want_to_go": 行きたい店リストへの追加
        - "like": いいね
    """

    __tablename__ = "user_shop_relations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    shop_id = Column(String(36), ForeignKey("shops.id"), nullable=False, index=True)
    relation_type = Column(String(20), nullable=False)  # "want_to_go" | "like"

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # リレーション
    user = relationship("User", foreign_keys=[user_id])
    shop = relationship("Shop", foreign_keys=[shop_id], back_populates="user_shop_relations")

    __table_args__ = (
        UniqueConstraint("user_id", "shop_id", "relation_type", name="uq_user_shop_relation"),
        Index("ix_user_shop_relations_user", "user_id"),
        Index("ix_user_shop_relations_shop", "shop_id"),
        Index("ix_user_shop_relations_type", "relation_type"),
    )

    def __repr__(self):
        return f"<UserShopRelation(user_id={self.user_id}, shop_id={self.shop_id}, type={self.relation_type})>"
