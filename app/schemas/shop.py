"""
Shop (店舗) スキーマ定義
リクエスト/レスポンスのPydantic モデル
"""

import re
import unicodedata
from datetime import datetime, time
from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.language_registry import validate_language_codes


def _validate_shop_language_field(v, field_name: str):
    """Shop.ai_supported_languages / staff_supported_languages 用の共通バリデーション。

    未対応の言語コードが送られた場合はここで明示的に拒否する（サイレントに
    無視して意図しない言語が有効になってしまうことを避けるため）。
    None（未指定）はそのまま許可する（既存店舗のデフォルト動作を壊さないため）。
    """
    if v is None:
        return None
    try:
        return validate_language_codes(v, field_name=field_name)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc


class ShopHoursCreate(BaseModel):
    """営業時間作成スキーマ"""
    day_of_week: int = Field(..., ge=0, le=6, description="曜日 (0=月, 6=日)")
    opening_time: time = Field(..., description="営業開始時刻")
    closing_time: time = Field(..., description="営業終了時刻")
    is_closed: bool = Field(default=False, description="定休日フラグ")
    last_order_time: Optional[time] = Field(None, description="ラストオーダー時刻")


class ShopHoursResponse(BaseModel):
    """営業時間レスポンススキーマ"""
    id: str
    shop_id: str
    day_of_week: int
    opening_time: time
    closing_time: time
    is_closed: bool
    last_order_time: Optional[time]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ShopRegisterRequest(BaseModel):
    """店舗登録リクエストスキーマ"""
    name: str = Field(..., min_length=1, max_length=255, description="店舗名")
    description: Optional[str] = Field(None, max_length=2000, description="店舗説明")
    category: str = Field(..., description="カテゴリ (RESTAURANT, CAFE, RAMEN, SUSHI, IZAKAYA, BAR, BEAUTY, SALON, CLINIC, OTHER)")
    address: str = Field(..., min_length=1, max_length=500, description="住所")
    latitude: Optional[float] = Field(None, ge=-90, le=90, description="緯度")
    longitude: Optional[float] = Field(None, ge=-180, le=180, description="経度")
    phone: Optional[str] = Field(None, max_length=20, description="電話番号")
    email: Optional[EmailStr] = Field(None, description="メールアドレス")
    website: Optional[str] = Field(None, max_length=500, description="ウェブサイトURL")
    thumbnail_url: Optional[str] = Field(None, max_length=500, description="サムネイル画像URL")
    cover_image_url: Optional[str] = Field(None, max_length=500, description="カバー画像URL")
    shop_hours: Optional[List[ShopHoursCreate]] = Field(None, description="営業時間リスト")
    features: Optional[List[str]] = Field(default_factory=list, description="お店の特徴タグ（例：個室あり、禁煙、駐車場あり）")
    reservation_duration_minutes: Optional[int] = Field(90, ge=15, le=600, description="1組あたりの標準滞在時間（分）")
    ai_supported_languages: Optional[List[str]] = Field(
        None, description="AI受付が対応してよい言語コードのリスト（例：[\"ja\", \"en\"]）。未指定の場合は日本語のみ"
    )
    staff_supported_languages: Optional[List[str]] = Field(
        None, description="店頭スタッフが対応できる言語コードのリスト（例：[\"ja\", \"en\"]）。未指定の場合は日本語のみ"
    )

    @field_validator("ai_supported_languages")
    @classmethod
    def _validate_ai_supported_languages(cls, v):
        return _validate_shop_language_field(v, "ai_supported_languages")

    @field_validator("staff_supported_languages")
    @classmethod
    def _validate_staff_supported_languages(cls, v):
        return _validate_shop_language_field(v, "staff_supported_languages")


class ShopUpdateRequest(BaseModel):
    """店舗更新リクエストスキーマ（送られたフィールドのみ更新）"""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)
    category: Optional[str] = None
    address: Optional[str] = Field(None, min_length=1, max_length=500)
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    phone: Optional[str] = Field(None, max_length=20)
    email: Optional[EmailStr] = None
    website: Optional[str] = Field(None, max_length=500)
    thumbnail_url: Optional[str] = Field(None, max_length=500)
    cover_image_url: Optional[str] = Field(None, max_length=500)
    features: Optional[List[str]] = None
    reservation_duration_minutes: Optional[int] = Field(None, ge=15, le=600)
    reservations_enabled: Optional[bool] = Field(
        None, description="予約受付をON/OFFする。ONにするには課金アクティベーションが必要"
    )
    staff_schedule_enabled: Optional[bool] = Field(
        None, description="Phase3E-2: スタッフシフト（週次シフト・日付調整）による予約可否判定をON/OFFする"
    )
    ai_supported_languages: Optional[List[str]] = Field(
        None, description="AI受付が対応してよい言語コードのリスト（例：[\"ja\", \"en\"]）。日本語は除外しても常に有効"
    )
    staff_supported_languages: Optional[List[str]] = Field(
        None, description="店頭スタッフが対応できる言語コードのリスト（例：[\"ja\", \"en\"]）"
    )

    @field_validator("ai_supported_languages")
    @classmethod
    def _validate_ai_supported_languages(cls, v):
        return _validate_shop_language_field(v, "ai_supported_languages")

    @field_validator("staff_supported_languages")
    @classmethod
    def _validate_staff_supported_languages(cls, v):
        return _validate_shop_language_field(v, "staff_supported_languages")


class ShopHoursBulkUpdateRequest(BaseModel):
    """営業時間の一括更新スキーマ（送信された曜日分だけ置き換え）"""
    hours: List[ShopHoursCreate] = Field(..., description="曜日ごとの営業時間リスト")


# 日本国内の電話番号として妥当と判断する最低限のパターン。
# 「0」から始まり、残り9〜10桁の数字（合計10桁または11桁）。
# 携帯電話(090/080/070)・IP電話(050)は11桁、固定電話・フリーダイヤル(0120/0800等)は
# 10桁が一般的だが、市外局番の桁数までは厳密に検証しない
# （仕様書の「厳しすぎて正しい番号を弾かないこと」という要件を優先する）。
_JP_PHONE_DIGITS_RE = re.compile(r"^0\d{9,10}$")


def normalize_jp_phone_digits(raw: str) -> str:
    """
    電話番号の入力文字列を、数字のみの正規化された文字列に変換する。

    - 全角数字・全角ハイフン等はNFKC正規化でまず半角に変換する。
    - ハイフン・スペース・括弧など数字以外の文字は全て除去する。
    - 戻り値は例えば "09012345678" のような数字のみの文字列
      （将来Outbound AIが発信する際の正規化済みの発信先として利用する想定）。
    """
    normalized = unicodedata.normalize("NFKC", raw)
    return re.sub(r"\D", "", normalized)


# Outbound AI Phase 4A: Customer Memory Foundationの電話番号lookup専用の正規化。
#
# 既存のnormalize_jp_phone_digits()を土台として再利用し（乱立させない）、
# それだけでは吸収できない「国番号付き表記」のケースのみをこの関数で追加処理する:
# - 09012345678 のような国内形式はそのまま。
# - +819012345678 / 819012345678 / 81-90-1234-5678 のような国番号(81)付き表記は、
#   先頭の"81"を取り除いた上で"0"を補い、国内形式に変換する
#   （090-1234-5678と+81-90-1234-5678が同一の顧客記憶として扱われるようにするため。
#   仕様書Phase 4A section4-Dで明示的に要求されている）。
# 欠落桁の補完や、市外局番の推測などは一切行わない。変換後も
# _JP_PHONE_DIGITS_RE（0始まり・合計10〜11桁）に一致しない場合はNoneを返し、
# 呼び出し側（app.services.customer_memory）はCustomer Memoryの作成/検索を
# 安全にスキップする（不正確な正規化で別人を同一人物として扱うより、
# 「今回は認識できなかった」として通常の予約受付を継続する方を優先する）。
def normalize_jp_phone_national(raw: Optional[str]) -> Optional[str]:
    """
    Customer Memoryのlookup/upsert専用: 電話番号を日本の国内形式
    （0始まり・数字のみ）に正規化する。認識できない形式はNoneを返す。
    """
    if not raw:
        return None
    digits = normalize_jp_phone_digits(raw)
    if digits.startswith("81") and not digits.startswith("810"):
        # "81" (国番号) + "0"を除いた市外局番以下、という国際表記の可能性。
        # 先頭の"81"を"0"に置き換える（例: "819012345678" -> "09012345678"）。
        candidate = "0" + digits[2:]
    else:
        candidate = digits
    if _JP_PHONE_DIGITS_RE.match(candidate):
        return candidate
    return None


class ShopNotificationSettingsResponse(BaseModel):
    """
    予約通知の連絡先設定レスポンス（Phase3H Workstream C・オーナー専用）。

    重要: このスキーマはShopResponseとは完全に独立しており、
    Customer向けAPI（検索・詳細等）では絶対に使用しない。
    """
    shop_id: str
    reservation_notification_phone: Optional[str] = None
    reservation_phone_notification_enabled: bool = False

    class Config:
        from_attributes = True


class ShopNotificationSettingsUpdateRequest(BaseModel):
    """
    予約通知の連絡先設定 更新リクエスト（送られたフィールドのみ更新）。

    ON/OFFの整合性チェック（電話番号が無い状態でONにできない、番号を消したら
    自動でOFFに戻す等）は、既存データとのマージが必要なためこのスキーマ単体では
    完結できず、app/routers/shops.py の update_shop_notification_settings() で
    行う。
    """
    reservation_notification_phone: Optional[str] = Field(
        None, max_length=20, description="090-1234-5678のような自然な形式で入力可能"
    )
    reservation_phone_notification_enabled: Optional[bool] = None

    @field_validator("reservation_notification_phone", mode="before")
    @classmethod
    def _validate_and_normalize_phone(cls, v):
        if v is None:
            return None
        if not isinstance(v, str) or v.strip() == "":
            return None
        digits = normalize_jp_phone_digits(v)
        if not _JP_PHONE_DIGITS_RE.match(digits):
            raise ValueError(
                "電話番号の形式が正しくありません（例：090-1234-5678、または市外局番付きの固定電話番号）"
            )
        return digits


class ShopResponse(BaseModel):
    """店舗レスポンススキーマ"""
    id: str
    tenant_id: str
    name: str
    description: Optional[str]
    category: str
    business_type: Optional[str] = None
    address: str
    latitude: Optional[float]
    longitude: Optional[float]
    phone: Optional[str]
    email: Optional[str]
    website: Optional[str]
    thumbnail_url: Optional[str]
    cover_image_url: Optional[str]
    is_active: bool
    is_featured: bool
    reservations_enabled: bool = False
    staff_schedule_enabled: bool = False
    total_reservations: Optional[int]
    total_reviews: Optional[int]
    average_rating: Optional[float]
    created_at: datetime
    updated_at: datetime
    shop_hours: Optional[List[ShopHoursResponse]] = []
    features: Optional[List[str]] = []
    reservation_duration_minutes: Optional[int] = 90
    logo_url: Optional[str] = None
    # DB上はNone（未設定）でありうるため型自体はOptionalにするが、
    # _build_shop_response()が必ずeffective_ai_languages/effective_languages
    # で上書きするため、実際にAPIレスポンスとして外へ出る値が
    # Noneになることは無い（app.routers.shops._build_shop_response参照）。
    ai_supported_languages: Optional[List[str]] = Field(
        default_factory=lambda: ["ja"], description="AI受付が実際に対応する言語コードのリスト（日本語を必ず含む）"
    )
    staff_supported_languages: Optional[List[str]] = Field(
        default_factory=lambda: ["ja"], description="店頭スタッフが対応できる言語コードのリスト"
    )

    class Config:
        from_attributes = True


class ShopSearchQuery(BaseModel):
    """店舗検索クエリスキーマ"""
    keyword: Optional[str] = Field(None, description="キーワード検索")
    category: Optional[str] = Field(None, description="カテゴリフィルタ")
    latitude: Optional[float] = Field(None, description="検索中心点の緯度")
    longitude: Optional[float] = Field(None, description="検索中心点の経度")
    radius_km: Optional[float] = Field(5.0, ge=0.1, le=50, description="検索半径（km）")
    min_rating: Optional[float] = Field(None, ge=0, le=5, description="最小評価")
    sort_by: Optional[str] = Field("rating", description="ソート順 (rating, distance, popular, new)")
    limit: int = Field(20, ge=1, le=100, description="取得件数")
    offset: int = Field(0, ge=0, description="オフセット")


class ShopListResponse(BaseModel):
    """店舗一覧レスポンススキーマ"""
    total: int = Field(..., description="総件数")
    limit: int
    offset: int
    items: List[ShopResponse]


class ShopRegisterResponse(BaseModel):
    """店舗登録レスポンススキーマ"""
    success: bool
    message: str
    shop_id: str
    shop: ShopResponse


class ErrorResponse(BaseModel):
    """エラーレスポンススキーマ"""
    success: bool = False
    message: str
    error_code: Optional[str] = None
