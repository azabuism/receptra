"""
Human Handoff基盤: CallbackRequest オーナー管理API用スキーマ

設計方針（重要・必ず守ること）:
- このAPI群はすべてオーナー認証必須・tenant/shop分離必須。Customer向けAPIや
  AI（request_callback Tool）向けエンドポイントとは完全に別物であり、
  admin bypassやdebug用の抜け道は一切作らない
  （app.routers.shops の notification-settings / phone-reception-settings と
  全く同じ設計方針を踏襲する）。
- customer_phoneは一覧（List）では末尾4桁以外をマスクして返す
  （app.routers.shops._mask的な既存の "****" + 末尾4桁 という表示規約を踏襲）。
  詳細（Detail）取得時のみ、オーナー認証・tenant/shop分離を経た上でフルの
  電話番号を返す。
- statusカラム（担当者への通知試行の成否）とresolution_status
  （お客様への折り返し対応そのものが完了したか）は意味が異なるため、
  両方を別フィールドとしてそのまま返す（1つに混ぜない）。
- resolution_status_label は
  app.models.callback_request.CALLBACK_REQUEST_RESOLUTION_STATUS_LABELS_JA
  という単一の対応表から解決する（フロントエンドでラベルをハードコードしない）。
"""

from datetime import date, datetime, time
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from app.models.callback_request import (
    CallbackRequestResolutionStatus,
    CALLBACK_REQUEST_RESOLUTION_STATUS_LABELS_JA,
)


def mask_phone_for_list(phone: Optional[str]) -> Optional[str]:
    """一覧表示用に電話番号をマスクする（末尾4桁以外を****に置き換え）。

    app.routers.shops.update_shop_notification_settings や
    app.services.outbound_call_worker._mask_phone() と同じ表示規約。
    """
    if not phone:
        return None
    if len(phone) <= 4:
        return "****"
    return "****" + phone[-4:]


class CallbackRequestListItem(BaseModel):
    """折り返し依頼 一覧の1件分（電話番号はマスク済み）。"""

    id: str
    created_at: datetime
    customer_name: Optional[str] = None
    customer_phone_masked: Optional[str] = None
    inquiry_text: Optional[str] = None
    desired_date: Optional[date] = None
    desired_time: Optional[time] = None
    party_size: Optional[int] = None
    service_name: Optional[str] = None
    reason_code: str
    status: str
    resolution_status: str
    resolution_status_label: str

    class Config:
        from_attributes = True


class CallbackRequestListResponse(BaseModel):
    shop_id: str
    total: int
    items: List[CallbackRequestListItem]


class CallbackRequestDetail(BaseModel):
    """折り返し依頼 詳細（オーナー認証＋tenant/shop分離済みのため電話番号は非マスク）。"""

    id: str
    shop_id: str
    created_at: datetime
    updated_at: datetime
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    inquiry_text: Optional[str] = None
    desired_date: Optional[date] = None
    desired_time: Optional[time] = None
    party_size: Optional[int] = None
    service_id: Optional[str] = None
    service_name: Optional[str] = None
    reason_code: str
    status: str
    resolution_status: str
    resolution_status_label: str

    class Config:
        from_attributes = True


class CallbackRequestResolutionUpdateRequest(BaseModel):
    """お客様への折り返し対応状態の更新リクエスト（オーナー専用）。"""

    resolution_status: str = Field(..., description="unhandled(未対応) / in_progress(対応中) / handled(対応済み)")

    @field_validator("resolution_status")
    @classmethod
    def _validate_resolution_status(cls, v: str) -> str:
        valid = {s.value for s in CallbackRequestResolutionStatus}
        if v not in valid:
            raise ValueError(f"resolution_statusは次のいずれかである必要があります: {', '.join(sorted(valid))}")
        return v


def resolution_status_label(value: str) -> str:
    return CALLBACK_REQUEST_RESOLUTION_STATUS_LABELS_JA.get(value, value)
