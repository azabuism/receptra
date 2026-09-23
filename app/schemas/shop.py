"""
Shop (店舗) スキーマ定義
リクエスト/レスポンスのPydantic モデル
"""

import re
import unicodedata
from datetime import datetime, time
from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

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


def _validate_hours_consistency(
    opening_time: time, closing_time: time, closes_next_day: bool,
    last_order_time: Optional[time], last_order_next_day: bool,
) -> None:
    """
    Reservation Intelligence Phase D-1で導入し、Phase D-3で共通helperへ抽出した、
    opening_time/closing_time/closes_next_day/last_order_time/last_order_next_day
    の組み合わせに矛盾がないかを検証する共通ロジック。

    ShopHoursCreate（曜日ごとの通常営業時間）とShopHoursOverrideCreateRequest
    （特定日の営業時間Override、Phase D-3）の両方から呼ばれる、唯一のsource of
    truth（コピー＆ペーストで重複させない）。呼び出し元でis_closed（ShopHoursの
    定休日フラグ。ShopHoursOverrideには存在しない）に該当する行はこの関数を
    呼び出す前にスキップすること。

    判定方針（ユーザー承認済み仕様。Phase D-1から変更なし）:
    1. closes_next_day は「closing_timeがopening_time以前（同日内には
       収まらない）ときに限りTrue」でなければならない。この対称性により
       「日跨ぎのつもりが指定し忘れた」入力ミスと「日跨ぎでないのに
       誤ってONにした」入力ミスの両方をサイレントに受理しない。
       opening_time == closing_time は closes_next_day=True の場合のみ有効
       （＝24時間営業。opening==closingを自動的に24時間営業と解釈すること
       はせず、あくまで明示的にcloses_next_day=Trueを指定した場合にのみ
       そう解釈される）。
    2. last_order_timeが指定されている場合、closes_next_dayと同じ考え方で
       last_order_next_dayを解釈した上で、opening_time以降・実質的な
       閉店時刻(closing_time、closes_next_day時は+24h)以前の範囲に
       収まっていなければならない。
    3. last_order_timeが未指定なのにlast_order_next_day=Trueは無意味な
       指定のため拒否する。

    問題があれば pydantic の model_validator から呼ばれる想定のため、
    ValueError を送出する（戻り値はNone）。
    """
    opening_min = opening_time.hour * 60 + opening_time.minute
    closing_min = closing_time.hour * 60 + closing_time.minute
    same_day_valid = closing_min > opening_min

    if closes_next_day == same_day_valid:
        if closes_next_day:
            raise ValueError(
                "closes_next_day=Trueですが、closing_timeがopening_timeより後（同日内）です。"
                "日跨ぎでない場合はcloses_next_day=Falseにしてください"
            )
        raise ValueError(
            "closing_timeがopening_time以前です。日跨ぎ営業（翌日に閉店）の場合は"
            "closes_next_day=Trueを指定してください"
        )

    closing_effective = closing_min + (1440 if closes_next_day else 0)

    if last_order_time is not None:
        lo_min = last_order_time.hour * 60 + last_order_time.minute
        lo_effective = lo_min + (1440 if last_order_next_day else 0)
        if not (opening_min <= lo_effective <= closing_effective):
            raise ValueError(
                "last_order_timeが営業時間（opening_time〜closing_time、"
                "日跨ぎの場合はlast_order_next_dayも含めて）の範囲内にありません"
            )
    elif last_order_next_day:
        raise ValueError("last_order_timeが指定されていないのにlast_order_next_day=Trueは指定できません")


class ShopHoursCreate(BaseModel):
    """営業時間作成スキーマ"""
    day_of_week: int = Field(..., ge=0, le=6, description="曜日 (0=月, 6=日)")
    opening_time: time = Field(..., description="営業開始時刻")
    closing_time: time = Field(..., description="営業終了時刻")
    is_closed: bool = Field(default=False, description="定休日フラグ")
    last_order_time: Optional[time] = Field(None, description="ラストオーダー時刻")
    # Reservation Intelligence Phase D-1: 日跨ぎ営業時間の明示的フラグ。
    # 「closing_time < opening_timeなら自動的に翌日」という暗黙推論は採用しない
    # （入力ミスと意図的な日跨ぎ営業を区別できなくする、というユーザー承認済みの
    # 設計判断）。デフォルトFalseで、既存データ・既存クライアントの挙動を完全に
    # 維持する。矛盾する組み合わせは_validate_overnight_consistency()で拒否する。
    closes_next_day: bool = Field(default=False, description="閉店時刻が翌日か（日跨ぎ営業）")
    last_order_next_day: bool = Field(default=False, description="ラストオーダー時刻が翌日か")

    @model_validator(mode="after")
    def _validate_overnight_consistency(self):
        """
        Phase D-1: 定休日（is_closed=True）の行は、時刻フィールド自体が意味を
        持たない（従来の挙動と同じ。check_single_slot_availability等も
        is_closed=Trueの時点で以降の時刻判定を行わずshop_closedとして扱う）
        ため、この検証はスキップする。それ以外は共通helper
        _validate_hours_consistency()（Phase D-3で抽出）に委譲する。
        """
        if self.is_closed:
            return self
        _validate_hours_consistency(
            self.opening_time, self.closing_time, self.closes_next_day,
            self.last_order_time, self.last_order_next_day,
        )
        return self


class ShopHoursResponse(BaseModel):
    """営業時間レスポンススキーマ"""
    id: str
    shop_id: str
    day_of_week: int
    opening_time: time
    closing_time: time
    is_closed: bool
    last_order_time: Optional[time]
    closes_next_day: bool = False
    last_order_next_day: bool = False
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
    # Human Handoff基盤: Email通知設定（店舗ごとに個別設定可能。未設定の場合、
    # 実際の通知解決時にはTenant.email（アカウント登録メール）へ
    # フォールバックする想定だが、このレスポンス自体には店舗固有の設定値
    # （reservation_notification_email）だけを返す。フォールバック先の
    # Tenant.emailそのものは、より機微度の高い情報のためこのAPIでは返さない）。
    reservation_notification_email: Optional[str] = None
    reservation_email_notification_enabled: bool = False

    class Config:
        from_attributes = True


class ShopNotificationSettingsUpdateRequest(BaseModel):
    """
    予約通知の連絡先設定 更新リクエスト（送られたフィールドのみ更新）。

    ON/OFFの整合性チェック（電話番号が無い状態でONにできない、番号を消したら
    自動でOFFに戻す等）は、既存データとのマージが必要なためこのスキーマ単体では
    完結できず、app/routers/shops.py の update_shop_notification_settings() で
    行う。Emailについても同じ整合性チェックを行う。
    """
    reservation_notification_phone: Optional[str] = Field(
        None, max_length=20, description="090-1234-5678のような自然な形式で入力可能"
    )
    reservation_phone_notification_enabled: Optional[bool] = None
    reservation_notification_email: Optional[str] = Field(
        None, max_length=255, description="通知を受け取るメールアドレス"
    )
    reservation_email_notification_enabled: Optional[bool] = None

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

    @field_validator("reservation_notification_email", mode="before")
    @classmethod
    def _validate_email(cls, v):
        if v is None:
            return None
        if not isinstance(v, str) or v.strip() == "":
            return None
        candidate = v.strip()
        # pydantic[email]のEmailStrはPUT全体を不必要に複雑化するため、ここでは
        # 既存のShop.email等と同じ軽量な形式チェックに留める（RFC完全準拠の
        # 検証はせず、明らかな入力ミスだけを弾く）。
        if "@" not in candidate or " " in candidate or candidate.count("@") != 1:
            raise ValueError("メールアドレスの形式が正しくありません")
        local, _, domain = candidate.partition("@")
        if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
            raise ValueError("メールアドレスの形式が正しくありません")
        return candidate


class ShopPhoneReceptionSettingsResponse(BaseModel):
    """
    Human Handoff基盤: AI電話受付ON/OFF・将来のライブ転送設定
    （オーナー専用・非公開）。

    重要:
    - ai_phone_reception_enabled は、ブラウザ経由のRealtime Voice AI
      （POST /api/v1/shops/{shop_id}/realtime-voice/session）が実際に参照する。
      OFFの店舗ではセッション自体が発行されず、OpenAI側のコストも一切
      発生しない（app.routers.realtime_voice.create_realtime_voice_session参照）。
    - RECEPTRAの電話（Vonage）着信経路（app/routers/vonage_voice.py）は、
      現時点では単一共有番号を前提としており店舗ごとの振り分けに対応して
      いない。そのためai_phone_reception_enabledも、transfer_*系のフィールドも、
      Vonage着信処理からはまだ一切参照されない（将来のtelephony ingress
      実装に備えた設定の土台。詳細は完了報告を参照）。
    - transfer_to_staff_enabled/transfer_phone_number/
      transfer_no_answer_fallback_to_callback は、将来のライブ転送機能向けの
      データモデルのみで、今回のフェーズでは実際に電話を転送する仕組み
      自体は実装しない。
    - transfer_phone_numberはreservation_notification_phone（担当者への事後
      通知専用番号）とは意味が異なる別フィールドであり、混同しないこと。
    """
    shop_id: str
    ai_phone_reception_enabled: bool = True
    transfer_to_staff_enabled: bool = False
    transfer_phone_number: Optional[str] = None
    transfer_no_answer_fallback_to_callback: bool = True

    class Config:
        from_attributes = True


class ShopPhoneReceptionSettingsUpdateRequest(BaseModel):
    """AI電話受付ON/OFF・ライブ転送設定 更新リクエスト（送られたフィールドのみ更新）。"""
    ai_phone_reception_enabled: Optional[bool] = None
    transfer_to_staff_enabled: Optional[bool] = None
    transfer_phone_number: Optional[str] = Field(
        None, max_length=20, description="担当者へライブ転送する電話番号（090-1234-5678のような自然な形式で入力可能）"
    )
    transfer_no_answer_fallback_to_callback: Optional[bool] = None

    @field_validator("transfer_phone_number", mode="before")
    @classmethod
    def _validate_and_normalize_transfer_phone(cls, v):
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
