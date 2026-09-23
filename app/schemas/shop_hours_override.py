"""
ShopHoursOverride (特定日の営業時間) スキーマ定義
Reservation Intelligence Phase D-3

特定のカレンダー日付だけの営業時間を、通常の曜日ごとの営業時間（ShopHours）とは
別に登録するためのリクエスト/レスポンススキーマ。ShopHoursOverrideモデルの
docstring（app/models/shop.py）に3つの責務（ShopHours/ShopHoursOverride/
ShopClosure）の分離方針を記載済みなので、ここでは繰り返さない。

is_closedフィールドは意図的に持たせない（Section8。休業はShopClosureの
排他的な責務のまま）。
"""

from datetime import datetime, date, time
from pydantic import BaseModel, Field, model_validator

from app.schemas.shop import _validate_hours_consistency


class ShopHoursOverrideUpsertRequest(BaseModel):
    """
    特定日の営業時間の作成・更新（upsert）リクエスト。

    target_dateはURLパス側で指定する（PUT /shops/{shop_id}/hours-overrides/
    {target_date}）ため、ここには含めない（同じ日付を2箇所で別々に指定できて
    しまう食い違いのリスクを避けるため）。
    """
    opening_time: time = Field(..., description="その日の営業開始時刻")
    closing_time: time = Field(..., description="その日の営業終了時刻")
    last_order_time: time | None = Field(None, description="その日のラストオーダー時刻")
    # Phase D-1/D-2と同じ「暗黙推論しない」設計方針（ShopHours.closes_next_day参照）。
    closes_next_day: bool = Field(default=False, description="閉店時刻が翌日か（日跨ぎ営業）")
    last_order_next_day: bool = Field(default=False, description="ラストオーダー時刻が翌日か")

    @model_validator(mode="after")
    def _validate_consistency(self):
        """
        ShopHoursCreateと全く同じ整合性検証を、共通helper
        _validate_hours_consistency()（app/schemas/shop.py、Phase D-3で抽出）に
        委譲する（コピー＆ペーストしない。opening==closing等の既存の境界値の
        扱いも完全に同一のまま、Special Hours用に再解釈しない）。

        ShopHoursCreateと異なり、is_closedフィールド自体が存在しないため、
        スキップ分岐は不要（Special Hoursの行が存在する＝常に「営業する日」
        という意味しか持たないため）。
        """
        _validate_hours_consistency(
            self.opening_time, self.closing_time, self.closes_next_day,
            self.last_order_time, self.last_order_next_day,
        )
        return self


class ShopHoursOverrideResponse(BaseModel):
    """特定日の営業時間のレスポンス"""
    id: str
    shop_id: str
    target_date: date
    opening_time: time
    closing_time: time
    last_order_time: time | None = None
    closes_next_day: bool = False
    last_order_next_day: bool = False
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
