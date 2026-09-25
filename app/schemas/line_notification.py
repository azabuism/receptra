"""
Phase N2: LINE Owner通知 — API用スキーマ

重要: line_user_id / Channel ID / Webhook URL / Access Token は
このスキーマ群のいずれにも一切含めない（公開APIへの露出を絶対に防ぐ）。
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class LineConnectionStatus(BaseModel):
    """テナント単位の連携状態。line_user_idは絶対に含めない。"""

    connected: bool
    connected_at: Optional[datetime] = None


class LineFriendAddInfo(BaseModel):
    """友だち追加用の公開情報（Basic IDは秘匿情報ではない）。"""

    configured: bool
    basic_id: Optional[str] = None
    add_friend_url: Optional[str] = None


class LineLinkStartResponse(BaseModel):
    nonce: str


class ShopLineNotificationSettingItem(BaseModel):
    shop_id: str
    enabled: bool
    action_required_enabled: bool
    reservation_enabled: bool

    class Config:
        from_attributes = True


class ShopLineNotificationSettingUpdate(BaseModel):
    enabled: Optional[bool] = None
    action_required_enabled: Optional[bool] = None
    reservation_enabled: Optional[bool] = None


class LineTestNotificationResponse(BaseModel):
    sent: bool
    reason: Optional[str] = None
