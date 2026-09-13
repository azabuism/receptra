"""
BusinessType (業種) モデル
複数業種対応のためのビジネスタイプ定義
"""

import enum


class BusinessType(str, enum.Enum):
    """業種タイプ"""
    RESTAURANT = "restaurant"      # 飲食店
    BEAUTY = "beauty"             # 美容院（ヘア、ネイル、スパ）
    HOTEL = "hotel"               # ホテル
    SCHOOL = "school"             # スクール
    CRAM_SCHOOL = "cram_school"   # 塾
    CLINIC = "clinic"             # 医院
    GYM = "gym"                   # ジム
    OTHER = "other"               # その他
