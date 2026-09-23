"""
Phase3E-2: スタッフシフト管理モデル

- StaffWeeklyShift: スタッフの週次基本勤務時間（曜日ごとに複数登録可・分割シフト対応）
- StaffShiftOverride: 日付単位のシフト調整（終日休み／その日だけの時間変更／勤務時間内の部分的な不在）

新規テーブルのため、alembicは使わずapp/main.pyのlifespan()内
Base.metadata.create_all()で自動作成される（ai_staff_settings/shop_knowledgeと同じパターン）。
"""

import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, Date, Time, ForeignKey, Index, Integer, Boolean
from sqlalchemy.orm import relationship

from app.database import Base


class StaffWeeklyShift(Base):
    """スタッフの週次基本勤務時間（曜日ごとに複数行＝分割シフトを許容する）"""

    __tablename__ = "staff_weekly_shifts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    staff_id = Column(String(36), ForeignKey("staff.id"), nullable=False, index=True)

    # 0=月, 1=火, 2=水, 3=木, 4=金, 5=土, 6=日
    # Python標準のdatetime.weekday()の規約に合わせる（既存ShopHours.day_of_weekと同じ）
    day_of_week = Column(Integer, nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)

    # ★★★ Reservation Intelligence Phase D-1: 日跨ぎ勤務（例: 18:00〜翌03:00）を
    # 明示的に表現するためのフラグ。app.models.shop.ShopHours.closes_next_dayと
    # 同じ設計思想（暗黙推論しない・デフォルトFalseで既存データを完全に維持）。
    ends_next_day = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    staff = relationship("Staff", foreign_keys=[staff_id])

    __table_args__ = (
        Index("ix_staff_weekly_shifts_staff_day", "staff_id", "day_of_week"),
    )

    def __repr__(self):
        return f"<StaffWeeklyShift(staff_id={self.staff_id}, day={self.day_of_week}, {self.start_time}-{self.end_time})>"


class StaffShiftOverride(Base):
    """
    スタッフの日付単位シフト調整。

    override_type:
    - "day_off"     : 指定日は終日勤務不可（start_time/end_timeはNULL）
    - "hours"       : 指定日の勤務時間を週次シフトの代わりにstart_time-end_timeに置き換える
    - "unavailable" : 指定日の通常勤務時間（週次シフト、または上のhoursで置き換えた時間）の
                       うち、start_time-end_timeの範囲だけを不可にする（それ以外の時間は
                       通常どおり勤務扱いのまま）
    """

    __tablename__ = "staff_shift_overrides"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    staff_id = Column(String(36), ForeignKey("staff.id"), nullable=False, index=True)
    target_date = Column(Date, nullable=False, index=True)
    override_type = Column(String(20), nullable=False)
    start_time = Column(Time, nullable=True)
    end_time = Column(Time, nullable=True)
    # ★★★ Reservation Intelligence Phase D-1: override_type="hours"/"unavailable"で
    # start_time/end_timeが日跨ぎ（例: 18:00〜翌03:00の時間変更、または
    # 23:00〜翌01:00の部分的な不在）であることを示す。day_offでは常にFalseのまま
    # 無視される。StaffWeeklyShift.ends_next_dayと同じ設計思想。
    ends_next_day = Column(Boolean, nullable=False, default=False)
    note = Column(String(255), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    staff = relationship("Staff", foreign_keys=[staff_id])

    __table_args__ = (
        Index("ix_staff_shift_overrides_staff_date", "staff_id", "target_date"),
    )

    def __repr__(self):
        return f"<StaffShiftOverride(staff_id={self.staff_id}, date={self.target_date}, type={self.override_type})>"
