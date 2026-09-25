"""
Phase N1: 統一Owner Notification基盤 — オーナー管理API用スキーマ

app.schemas.callback_request と全く同じ設計方針を踏襲する:
- このAPI群はすべてオーナー認証必須・tenant/shop分離必須。
- 一覧・詳細ともにphone/emailを一切含まない（本モデル自体がそもそも
  customer_name以外のPIIを保持しないため、マスク処理も不要）。
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class OwnerNotificationEventItem(BaseModel):
    """お知らせ 一覧・詳細共通の1件分。"""

    id: str
    shop_id: str
    event_type: str
    priority: str
    title: str
    message: str
    related_entity_type: str
    related_entity_id: str
    customer_name: Optional[str] = None
    is_read: bool
    read_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class OwnerNotificationEventListResponse(BaseModel):
    shop_id: str
    total: int
    unread_count: int
    items: List[OwnerNotificationEventItem]
