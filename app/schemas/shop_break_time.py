"""
ShopBreakTime (休憩・予約停止時間) スキーマ定義
Reservation Intelligence Phase D-2

営業時間内の一部を「営業はしているが予約は受け付けない」時間帯として登録するための
リクエスト/レスポンススキーマ。ShopClosure（終日の臨時休業）とは別の概念。
"""

from datetime import datetime, time
from pydantic import BaseModel, Field, model_validator


class ShopBreakTimeCreateRequest(BaseModel):
    """休憩・予約停止時間の作成リクエスト"""
    day_of_week: int = Field(..., ge=0, le=6, description="曜日 (0=月, 6=日)")
    start_time: time = Field(..., description="休憩開始時刻")
    end_time: time = Field(..., description="休憩終了時刻")
    # Reservation Intelligence Phase D-1のcloses_next_day/ends_next_dayと同じ
    # 「暗黙推論しない」設計方針。休憩は開始時刻自体が日跨ぎ営業セッションの翌日側に
    # 位置しうる（例: 18:00〜翌03:00営業のうち翌00:00〜翌00:30の休憩）ため、
    # 終了側だけでなく開始側にも独立したフラグを持つ（app/models/shop.pyの
    # ShopBreakTimeモデルのdocstring参照）。
    start_next_day: bool = Field(default=False, description="開始時刻が翌日か")
    end_next_day: bool = Field(default=False, description="終了時刻が翌日か")

    @model_validator(mode="after")
    def _validate_positive_duration(self):
        """
        休憩時間は正の長さ（start < end、日跨ぎフラグ考慮後）を持たなければならない。
        start==endの「24時間」的な特殊扱いはしない（それはShopClosureの役割であり、
        休憩時間としては単純に無意味な入力として拒否する。ユーザー承認済み仕様）。

        start_next_day=True かつ end_next_day=False の組み合わせは、翌日側の
        start（1440分以降）が当日側のend（1439分以下）より後になることは
        あり得ないため、以下の効果的な分換算（effective minutes）による
        end_eff > start_eff の判定だけで自然に拒否される（暗黙推論はしていない。
        単に物理的に矛盾する入力を拒否しているだけ）。
        """
        start_min = self.start_time.hour * 60 + self.start_time.minute
        end_min = self.end_time.hour * 60 + self.end_time.minute
        start_eff = start_min + (1440 if self.start_next_day else 0)
        end_eff = end_min + (1440 if self.end_next_day else 0)
        if end_eff <= start_eff:
            raise ValueError(
                "終了時刻は開始時刻より後にしてください（日跨ぎの場合はstart_next_day/"
                "end_next_dayを適切に指定してください）"
            )
        return self


class ShopBreakTimeResponse(BaseModel):
    """休憩・予約停止時間のレスポンス"""
    id: str
    shop_id: str
    day_of_week: int
    start_time: time
    end_time: time
    start_next_day: bool = False
    end_next_day: bool = False
    created_at: datetime

    class Config:
        from_attributes = True
