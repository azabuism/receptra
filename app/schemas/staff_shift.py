"""
Phase3E-2: スタッフシフト管理スキーマ
週次シフト（StaffWeeklyShift）・日付単位のシフト調整（StaffShiftOverride）
"""

import re
from datetime import date as date_type, time as time_type, datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _parse_hhmm(value: str) -> time_type:
    if not isinstance(value, str) or not _TIME_RE.match(value):
        raise ValueError("時刻はHH:MM形式（24時間表記）で指定してください")
    h, m = value.split(":")
    return time_type(hour=int(h), minute=int(m))


class WeeklyShiftCreateRequest(BaseModel):
    """週次シフト1コマの登録リクエスト（同じ曜日に複数登録して分割シフトを表現できる）"""
    day_of_week: int = Field(..., ge=0, le=6, description="0=月,1=火,2=水,3=木,4=金,5=土,6=日")
    start_time: str = Field(..., description="開始時刻（HH:MM、24時間表記）")
    end_time: str = Field(..., description="終了時刻（HH:MM、24時間表記）")

    @field_validator("start_time", "end_time")
    @classmethod
    def _validate_time_format(cls, v: str) -> str:
        _parse_hhmm(v)
        return v

    @model_validator(mode="after")
    def _validate_range(self):
        start = _parse_hhmm(self.start_time)
        end = _parse_hhmm(self.end_time)
        if end <= start:
            raise ValueError("終了時刻は開始時刻より後にしてください")
        return self


class WeeklyShiftResponse(BaseModel):
    id: str
    staff_id: str
    day_of_week: int
    start_time: str
    end_time: str
    created_at: datetime

    class Config:
        from_attributes = True

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def _format_time(cls, v):
        if isinstance(v, time_type):
            return v.strftime("%H:%M")
        return v


_VALID_OVERRIDE_TYPES = {"day_off", "hours", "unavailable"}


class ShiftOverrideCreateRequest(BaseModel):
    """日付単位のシフト調整（休み／その日だけの時間変更／部分的な不在）の登録リクエスト"""
    target_date: date_type = Field(..., description="対象日（YYYY-MM-DD）")
    override_type: str = Field(..., description="day_off | hours | unavailable")
    start_time: Optional[str] = Field(None, description="開始時刻（HH:MM）。day_off以外は必須")
    end_time: Optional[str] = Field(None, description="終了時刻（HH:MM）。day_off以外は必須")
    note: Optional[str] = Field(None, max_length=255, description="メモ（任意）")

    @field_validator("override_type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        if v not in _VALID_OVERRIDE_TYPES:
            raise ValueError(f"override_typeは {'/'.join(sorted(_VALID_OVERRIDE_TYPES))} のいずれかにしてください")
        return v

    @model_validator(mode="after")
    def _validate_times(self):
        if self.override_type == "day_off":
            if self.start_time is not None or self.end_time is not None:
                raise ValueError("day_offの場合はstart_time/end_timeを指定しないでください")
        else:
            if not self.start_time or not self.end_time:
                raise ValueError("hours/unavailableの場合はstart_time/end_timeが必須です")
            start = _parse_hhmm(self.start_time)
            end = _parse_hhmm(self.end_time)
            if end <= start:
                raise ValueError("終了時刻は開始時刻より後にしてください")
        return self


class ShiftOverrideResponse(BaseModel):
    id: str
    staff_id: str
    target_date: date_type
    override_type: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    note: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def _format_time(cls, v):
        if isinstance(v, time_type):
            return v.strftime("%H:%M")
        return v
