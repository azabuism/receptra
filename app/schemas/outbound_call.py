"""
Outbound AI Phase 1: 通知履歴レスポンススキーマ

オーナー専用（Customer向けAPIには一切含めない）。電話番号は必ずマスク済みの
形でのみ返す（app.models.outbound_call.OutboundCallLog.to_phone_masked）。
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class OutboundCallLogResponse(BaseModel):
    id: str
    reservation_id: Optional[str] = None
    call_type: str
    category: str
    provider: str
    result_status: str
    to_phone_masked: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class OutboundCallLogListResponse(BaseModel):
    logs: List[OutboundCallLogResponse]
