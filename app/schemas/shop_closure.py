"""
ShopClosure (臨時休業日) スキーマ定義
"""

from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, Field, model_validator


class ShopClosureCreateRequest(BaseModel):
    """臨時休業日作成リクエスト"""
    start_date: date = Field(..., description="休業開始日")
    end_date: date = Field(..., description="休業終了日（単日の場合は開始日と同じ）")
    reason: Optional[str] = Field(None, max_length=200, description="休業理由（例：天候、設備メンテナンス）")

    @model_validator(mode="after")
    def check_date_order(self):
        if self.end_date < self.start_date:
            raise ValueError("終了日は開始日以降にしてください")
        return self


class ShopClosureResponse(BaseModel):
    """臨時休業日レスポンス"""
    id: str
    shop_id: str
    start_date: date
    end_date: date
    reason: Optional[str] = None
    created_at: datetime
    cancelled_count: int = 0

    class Config:
        from_attributes = True
