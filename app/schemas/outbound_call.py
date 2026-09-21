"""
Outbound AI Phase 1: 通知履歴レスポンススキーマ

オーナー専用（Customer向けAPIには一切含めない）。電話番号は必ずマスク済みの
形でのみ返す（app.models.outbound_call.OutboundCallLog.to_phone_masked）。

Phase 1.5（Owner Notification UX）での最小拡張:
Owner UIの通知履歴表示に「予約者名」「予約日時」が必要なため、
reservation_guest_name / reservation_date を追加する（値は
app.routers.outbound_calls 側で関連するReservationから補完する）。
既存フィールドは一切変更しない。追加してよいのはReservation側の
非機微情報のみで、以下は今後も追加しないこと:
- 顧客電話番号（Reservation.guest_phone）
- 通知先電話番号そのもの（マスク済みto_phone_maskedのみ許可）
- 内部provider情報・failure stack・他tenant情報
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
    # Phase 1.5で追加（Owner UIの通知履歴表示用）。紐づくReservationが
    # 既に削除されている場合はNone（予約者名・電話番号ではなく、あくまで
    # 「どの予約に対する通知か」を表示するための補助情報）。
    reservation_guest_name: Optional[str] = None
    reservation_date: Optional[datetime] = None

    class Config:
        from_attributes = True


class OutboundCallLogListResponse(BaseModel):
    logs: List[OutboundCallLogResponse]
