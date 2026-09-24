"""
Reservation (予約) スキーマ定義
リクエスト/レスポンスのPydantic モデル
"""

from datetime import datetime, date as date_type
from typing import Optional, List
from pydantic import BaseModel, Field, EmailStr, field_validator


class ReservationCreateRequest(BaseModel):
    """予約作成リクエストスキーマ（ゲスト予約・会員登録不要）"""
    shop_id: str = Field(..., description="店舗ID")
    reservation_date: datetime = Field(..., description="予約日時")
    number_of_people: int = Field(1, ge=1, le=999, description="人数（美容院・クリニックなどサービス単位の予約では省略可、既定値1）")
    service_id: Optional[str] = Field(None, description="サービスID（美容院・クリニック・スクール・フィットネスなど、サービス単位で予約する業種の場合に指定）")
    staff_id: Optional[str] = Field(None, description="スタッフ指名がある場合に指定（省略時は指名なし・お任せ）")
    coupon_code: Optional[str] = Field(None, max_length=50, description="クーポンコード（サービス単位の予約でのみ利用可能）")
    guest_name: str = Field(..., min_length=1, max_length=255, description="予約者名")
    guest_phone: str = Field(..., min_length=1, max_length=20, description="連絡先電話番号")
    guest_email: Optional[EmailStr] = Field(None, description="連絡先メールアドレス")
    special_requests: Optional[str] = Field(None, max_length=500, description="特別リクエスト")
    # Phase3B: Realtime Voice経由のcreate_reservation専用の冪等性キー。
    # 通常のWeb予約・チャット予約では一切指定しない（常にNone）。指定された場合のみ
    # create_reservation()内でDBの一意制約を用いた冪等処理の分岐に入る。
    idempotency_key: Optional[str] = Field(
        None, max_length=200,
        description="Realtime Voice等、サーバー側で二重書き込み防止が必要な呼び出し元専用の冪等性キー。通常のWeb予約・チャット予約では使用しない"
    )
    # Generic Resource Foundation Phase R4: resource_id（内部ID）は依然として
    # 一切公開しない（R2/R3から変更なし）。resource_typeは「部屋」「ベッド」
    # のようなカテゴリラベルのみで、Owner UI（shop-manage.html）が既に
    # 使っているALLOWED_RESOURCE_TYPESと同じ許可値のみ受け付ける。同一店舗に
    # 複数のresource_typeが混在し、これを省略した場合に安全に自動決定できない
    # 場合のみ、create_reservation()がreason_code="resource_type_required"で
    # 400を返す（このフィールドは省略可能。単一種別の店舗やTable/Staff経路の
    # 予約では一切不要）。
    resource_type: Optional[str] = Field(
        None, description="リソースの種別（部屋・ベッド等が混在する店舗で、自動判定できない場合にのみ指定。通常は省略可）"
    )

    @field_validator("resource_type")
    @classmethod
    def _validate_resource_type(cls, v: Optional[str]) -> Optional[str]:
        from app.schemas.resource import ALLOWED_RESOURCE_TYPES
        if v is not None and v not in ALLOWED_RESOURCE_TYPES:
            raise ValueError(f"resource_typeは次のいずれかである必要があります: {', '.join(ALLOWED_RESOURCE_TYPES)}")
        return v


class ReservationUpdateRequest(BaseModel):
    """予約更新リクエストスキーマ（オーナー操作用）"""
    status: Optional[str] = Field(None, description="ステータス (pending, confirmed, cancelled, no_show, completed)")
    cancellation_reason: Optional[str] = Field(None, max_length=500, description="キャンセル理由")
    number_of_people: Optional[int] = Field(None, ge=1, le=999, description="人数")
    special_requests: Optional[str] = Field(None, max_length=500, description="特別リクエスト")
    reservation_date: Optional[datetime] = Field(None, description="予約日時の変更（お客様との調整後にオーナーが日時を変更する場合）")


class ReservationResponse(BaseModel):
    """予約レスポンススキーマ"""
    id: str
    shop_id: str
    customer_id: Optional[str] = None
    table_id: Optional[str] = None
    table_name: Optional[str] = None
    # ★★★ Generic Resource Foundation Phase R2: table_id/table_nameと同じ
    # additiveパターンでresource_id/resource_nameを追加。R2完了時点では
    # availability engineがresource_idを一切設定しないため、既存の全予約・
    # 新規作成される全予約を含め、両フィールドは常にNoneのまま返る
    # （既存consumerへの影響はゼロ。Phase R3以降で実際に値が入る想定）。
    resource_id: Optional[str] = None
    resource_name: Optional[str] = None
    service_id: Optional[str] = None
    service_name: Optional[str] = None
    staff_id: Optional[str] = None
    staff_name: Optional[str] = None
    coupon_id: Optional[str] = None
    coupon_code: Optional[str] = None
    discount_amount: Optional[int] = None
    guest_name: Optional[str] = None
    guest_phone: Optional[str] = None
    # Resource Lane V1.1 / Privacy Consistency: 予約一覧（GET /shop/{shop_id}）
    # では、Booking Board APIと全く同じ表示規約（app.schemas.callback_request.
    # mask_phone_for_list）でマスクした値をここに入れ、guest_phone自体はNoneに
    # 差し替えて返す（一覧経由でフルの電話番号がネットワーク越しに出ないように
    # するため）。既存のguest_phoneフィールド・型は変更しない後方互換の追加のみ。
    # 詳細取得（GET /{reservation_id}。既にオーナー認証・tenant分離済み）や
    # 予約作成/更新の直後レスポンスなど、他の用途ではこのフィールドは常にNoneの
    # まま・guest_phoneは従来通りフル値を返す（_to_response()自体は無変更）。
    guest_phone_masked: Optional[str] = None
    guest_email: Optional[str] = None
    reservation_date: datetime
    # Reservation Intelligence Phase B: この予約自身が占有する時間（分）。
    # Phase B以前に作成された予約はNULL（「無制限」ではなく単に未設定。
    # 空き状況判定側は必ず具体的な分数にフォールバックして扱う。
    # app.routers.reservations._existing_duration_minutes参照）。
    duration_minutes: Optional[int] = None
    number_of_people: int
    status: str
    special_requests: Optional[str]
    cancelled_at: Optional[datetime]
    cancellation_reason: Optional[str]
    arrived_at: Optional[datetime]
    total_price: Optional[int] = None
    payment_status: Optional[str] = None
    reservation_source: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ReservationCreateResponse(BaseModel):
    """予約作成レスポンススキーマ"""
    success: bool
    message: str
    reservation_id: str
    reservation: ReservationResponse


class ReservationListResponse(BaseModel):
    """予約一覧レスポンススキーマ"""
    total: int
    limit: int
    offset: int
    items: List[ReservationResponse]


# ===== Owner Booking Board (Reservation Intelligence可視化) Phase =====
#
# 設計方針（重要・必ず守ること）:
# - Booking Boardは「新しい予約エンジン」ではなく、既存Reservation Intelligence
#   （Phase D-1/D-2/D-3の営業セッション・休憩時間・特定日営業時間ロジック）を
#   オーナー向けに可視化するための読み取り専用レスポンスである。
# - guest_phoneは一覧のため必ずマスク済み（app.schemas.callback_request.
#   mask_phone_for_listと同じ表示規約「****」＋末尾4桁）で返す。フルの電話番号が
#   必要な場合は、既存の GET /api/v1/reservations/{reservation_id}
#   （本フェーズでオーナー認証・tenant分離を追加済み）を別途呼び出す。
# - effective_duration_minutesは、reservation.duration_minutesがNULLの既存予約
#   （Phase B以前に作成）についても必ず具体的な分数を返す
#   （app.routers.reservations._existing_duration_minutes経由。0分やnullを
#   返すことはない）。
# - day_statusは「営業日で予約0件」「定休日」「臨時休業」「営業時間未設定」を
#   明確に区別する（Board側で空配列だけを見て誤解しないようにするため）。


class BoardBreakTimeItem(BaseModel):
    """予約表の1休憩時間帯（表示用に時刻文字列へ変換済み）"""
    start_time: str = Field(..., description="開始時刻（HH:MM）")
    end_time: str = Field(..., description="終了時刻（HH:MM）")
    start_next_day: bool = Field(False, description="開始時刻がセッション開始日の翌日側であるか")
    end_next_day: bool = Field(False, description="終了時刻がセッション開始日の翌日側であるか")


class BoardReservationItem(BaseModel):
    """予約表の1予約分。guest_phoneは一覧のためマスク済み"""
    id: str
    reservation_date: datetime
    # Reservation Intelligence Phase B: NULLの場合も必ず具体的な分数
    # （_existing_duration_minutes経由）。0分やnullにはならない。
    effective_duration_minutes: int
    guest_name: Optional[str] = None
    guest_phone_masked: Optional[str] = None
    number_of_people: int
    status: str
    service_name: Optional[str] = None
    staff_name: Optional[str] = None
    table_name: Optional[str] = None
    # ★★★ Resource Lane V1: 既存のstaff_name/table_name（表示用の名前文字列のみ）に
    # 加えて追加。担当者別/テーブル別レーンへ予約をグループ化するための安定した
    # キーとして使う（名前の文字列一致でグループ化すると、同姓同名や表示名変更に
    # 弱いため）。既存フィールドは一切変更しない、後方互換の追加のみ。
    staff_id: Optional[str] = None
    table_id: Optional[str] = None
    special_requests: Optional[str] = None


class BoardStaffRosterItem(BaseModel):
    """Resource Lane V1: 担当者別レーンのヘッダー用（その日の予約の有無に関わらず、
    有効な（is_active="active"）スタッフ全員を安定した並び順で返す）。"""
    id: str
    name: str


class BoardTableRosterItem(BaseModel):
    """Resource Lane V1: テーブル別レーンのヘッダー用（is_active=Trueのテーブル全員を
    安定した並び順で返す）。"""
    id: str
    name: str
    capacity: int


class BoardResponse(BaseModel):
    """予約表（Booking Board）1日分のレスポンス"""
    shop_id: str
    date: str
    # open: 営業セッションあり（予約0件の可能性もある） /
    # closed_regular: 定休日 / closed_temporary: 臨時休業（ShopClosure） /
    # hours_not_configured: 営業時間が一度も設定されていない
    day_status: str
    hours_source: Optional[str] = Field(None, description="override（特定日営業時間） または weekly（通常営業時間）")
    opening_time: Optional[str] = None
    closing_time: Optional[str] = None
    closes_next_day: bool = False
    session_start: Optional[datetime] = None
    session_end: Optional[datetime] = None
    closure_reason: Optional[str] = None
    break_times: List[BoardBreakTimeItem] = []
    reservations: List[BoardReservationItem] = []
    # ★★★ Resource Lane V1: 営業日か休業日かに関わらず常に含める（モード切替
    # 「担当者別」「テーブル別」の表示可否をBoard取得だけで決定でき、日付を
    # 切り替えるたびにモード選択肢が消えたり増えたりしないようにするため）。
    staff_roster: List[BoardStaffRosterItem] = []
    table_roster: List[BoardTableRosterItem] = []


class WeekReservationBlock(BaseModel):
    """週表示（Week View）のミニタイムライン用の最小限の1予約分。

    ★★★ Week View V1: PII最小化のため、guest_name/guest_phone(_masked)/
    guest_email/special_requests/service_name/staff_name/table_name/
    staff_id/table_id/statusは一切含めない（Daily Board側のBoardReservationItemとは
    意図的に別モデル。フィールドを間違って追加しないよう、共有せず新規定義する）。

    reservation_dateのみで開始位置の算出に十分（Reservationモデルの実装を確認済み:
    reservation_dateは「予約日時（開始時刻）」を表す単一のdatetime列であり、
    別途の開始時刻専用フィールドは存在しない）。effective_duration_minutesは
    Board APIと同じ_existing_duration_minutes()経由の値（NULLはデフォルト分数へ
    フォールバック済み）。
    """
    reservation_date: datetime
    effective_duration_minutes: int


class WeekDaySummary(BaseModel):
    """週表示（Week View）の1日分のサマリー。

    day_statusはBoardResponseと同じ4値（open/closed_regular/closed_temporary/
    hours_not_configured）を、_get_closure_for_date/_resolve_day_hoursという
    既存のReservation Intelligenceヘルパーをそのまま再利用して判定する
    （新しい休業判定ロジックは一切追加しない）。

    reservation_countとreservationsは、Daily Board側の「表示予約」定義
    （status !== 'cancelled'、shop-manage.htmlの_boardRenderBoard内activeCount等と
    完全に同一の定義）で事前にフィルタ済みの件数・一覧。個々のstatusは
    フロントに返す必要がないため含めない（キャンセル済み予約はcountにも
    reservations配列にも一切現れない）。
    """
    date: str
    day_status: str
    opening_time: Optional[str] = None
    closing_time: Optional[str] = None
    closes_next_day: bool = False
    session_start: Optional[datetime] = None
    session_end: Optional[datetime] = None
    closure_reason: Optional[str] = None
    reservation_count: int = 0
    reservations: List[WeekReservationBlock] = []


class WeekResponse(BaseModel):
    """週表示（Week View）V1のレスポンス。start_dateを月曜日として、
    必ず月曜〜日曜の7日分（daysの長さは常に7）を返す。

    ★★★ PII最小化: staff_roster/table_rosterを含め、Resource Lane
    （担当者別/テーブル別）に関する情報は一切含めない
    （Week ViewではResource Laneを表示しない仕様のため）。
    """
    shop_id: str
    start_date: str
    days: List[WeekDaySummary] = []


class AvailabilitySlot(BaseModel):
    """空き状況の1枠"""
    time: str = Field(..., description="開始時刻（HH:MM）")
    available: bool


class AvailabilityResponse(BaseModel):
    """空き状況レスポンス"""
    shop_id: str
    date: str
    party_size: int
    service_id: Optional[str] = None
    staff_id: Optional[str] = None
    # Generic Resource Foundation Phase R4: 既存のservice_id/staff_idと同じ
    # 「クエリで指定された任意パラメータをレスポンスにそのままエコーする」
    # 規約に合わせる。省略時はNoneのまま返るため、既存クライアントの挙動は
    # 一切変化しない。
    resource_type: Optional[str] = None
    is_open: bool
    message: Optional[str] = None
    slots: List[AvailabilitySlot] = []


# ===== Realtime Voice AI Phase3A: check_availability Tool Calling用 =====
#
# 設計方針（重要）:
# - shop_id をここに含めない。Realtime AI（OpenAI側）が呼び出すTool引数には
#   日付・時刻・人数・（任意で）service_id/staff_idのみを渡し、通話中の
#   店舗のshop_idはRECEPTRAサーバー側がURLパス（/shops/{shop_id}/...）から
#   決定する。AIに他店舗のshop_idを自由に指定させる余地を作らないため。
# - レスポンスは意図的に最小限。availableがfalseの場合のみ、AIが次の案内を
#   判断できるよう短い機械可読な reason_code を付与する（自然言語の理由は
#   ここでは返さない。文言はAI側のinstructions/toolの説明文に任せる）。

class CheckAvailabilityRequest(BaseModel):
    """Realtime AIのcheck_availability Toolからの引数"""
    date: str = Field(..., description="日付（YYYY-MM-DD）")
    time: str = Field(..., description="時刻（HH:MM、24時間表記）")
    party_size: int = Field(1, ge=1, le=999, description="人数")
    service_id: Optional[str] = Field(None, description="サービスID（美容院・クリニック等、サービス単位で予約する業種の場合のみ）")
    staff_id: Optional[str] = Field(None, description="スタッフ指名がある場合のみ")
    # Generic Resource Foundation Phase R4: resource_id（DBの内部ID）は
    # 一切公開しない。あくまで「部屋」「ベッド」のようなカテゴリラベル
    # （app.schemas.resource.ALLOWED_RESOURCE_TYPESと同じ許可値）のみを
    # 受け付ける。同一店舗に複数のresource_typeが混在し、これを省略した
    # 場合に安全に自動決定できない場合のみ、reason_code=
    # "resource_type_required"が返る（このフィールドは省略可能。
    # 単一種別の店舗では一切指定不要）。
    resource_type: Optional[str] = Field(
        None, description="リソースの種別（部屋・ベッド等が混在する店舗で、自動判定できない場合にのみ指定。通常は省略可）"
    )

    @field_validator("resource_type")
    @classmethod
    def _validate_resource_type(cls, v: Optional[str]) -> Optional[str]:
        from app.schemas.resource import ALLOWED_RESOURCE_TYPES
        if v is not None and v not in ALLOWED_RESOURCE_TYPES:
            raise ValueError(f"resource_typeは次のいずれかである必要があります: {', '.join(ALLOWED_RESOURCE_TYPES)}")
        return v


class CheckAvailabilityResponse(BaseModel):
    """Realtime AIへ返す最小レスポンス（DBへの保存は行わない）"""
    available: bool
    date: str
    time: str
    party_size: int
    # available=False の場合のみ設定。候補:
    # fully_booked / outside_business_hours / shop_closed / business_hours_not_configured /
    # temporary_closure / service_unavailable / staff_unavailable / invalid_request /
    # time_in_past / temporarily_unavailable / break_time
    #
    # Reservation Intelligence Phase D-2: break_time（休憩・予約停止時間との重なり）を
    # 追加。営業時間内だが一時的に予約を受け付けない時間帯という点でoutside_business_hours
    # とは意味が異なるため、既存コードとの互換性を保ったまま新規reason_codeとして
    # 追加した（既存のreason_code体系・語彙は一切変更していない）。
    #
    # Phase3A当初は「入力不正」と「バックエンド側で確定できなかった」を
    # どちらもinvalid_requestに丸めていたが、ユーザー指摘により分離した:
    # - invalid_request      : 日付/時刻の形式不正など、引数自体が不正な場合
    # - temporarily_unavailable : 店舗未検出・DB/API障害・想定外の例外など、
    #                             入力は正しいが現時点で確定できない場合
    # AI側のTool説明文で、temporarily_unavailableの場合は「空いている」
    # 「満席」のどちらとも案内せず、時間を置くか店舗へ問い合わせるよう
    # 案内すること、invalid_requestの場合は指定形式を見直すことを
    # それぞれ指示している。
    #
    # Phase3B.1: shop_closed（設定済みの定休日）と business_hours_not_configured
    # （店舗側が営業時間を一度も設定していない）を区別した。原因も対応も異なる
    # ため（前者は正常な休業日、後者は店舗側の設定漏れ）、check_availability /
    # create_reservation / AI instructionsの3箇所で同じ語彙に統一している。
    #
    # Reservation Intelligence Phase A: time_in_past（指定された日時が既に
    # 過去である場合）を invalid_request から分離した。形式は正しいが値が
    # 既に過ぎているだけであり、「形式を確認して再度呼び出す」という
    # invalid_requestの対応とは案内内容が異なるため。
    #
    # Generic Resource Foundation Phase R4: resource_type_required
    # （同一店舗に複数のresource_typeが混在し、resource_typeを省略した
    # ままでは安全に自動決定できない場合）を追加。
    reason_code: Optional[str] = None
    # Generic Resource Foundation Phase R4: reason_code=="resource_type_required"
    # の場合のみ設定する。このshopに実在するresource_typeの列挙値のリスト
    # （resource_idは一切含まない）。AIはこれとTool description内の自然文言
    # マッピングを組み合わせて、実在する選択肢だけで1回だけ自然な確認質問が
    # できる（存在しない選択肢を創作しないため）。それ以外の場合は常にNone。
    available_resource_types: Optional[List[str]] = None


# ===== Realtime Voice AI Phase3B: create_reservation Tool Calling用 =====
#
# 設計方針（重要）:
# - shop_id・call_idをAIに渡すToolの引数(parameters)には含めない。
#   shop_idはcheck_availabilityと同じくURLパス由来、call_idはブラウザ側が
#   OpenAI Realtime APIのfunction_callイベント(response.output_item.done)から
#   直接読み取った値をリクエストボディに含めて送る（AI自身の出力JSONには含まれない）。
#   RECEPTRA側（このToolのエンドポイント実装）がcall_idを
#   "realtime_voice:{shop_id}:{call_id}" の形にnamespace化してから
#   ReservationCreateRequest.idempotency_keyとして既存create_reservation()へ渡す。
# - guest_email・coupon_codeはPhase3Bのスコープ外（音声受付の初期版では不要という
#   ユーザー判断）。ReservationCreateRequest構築時は常にNone/未指定として扱う。

class CreateReservationToolRequest(BaseModel):
    """Realtime AIのcreate_reservation Toolからの引数 + ブラウザが付与するcall_id"""
    date: str = Field(..., description="来店日（YYYY-MM-DD）")
    time: str = Field(..., description="来店時刻（HH:MM、24時間表記）")
    party_size: int = Field(..., ge=1, le=999, description="人数")
    guest_name: str = Field(..., min_length=1, max_length=255, description="予約者名")
    guest_phone: str = Field(..., min_length=1, max_length=20, description="連絡先電話番号")
    service_id: Optional[str] = Field(None, description="サービスID（美容院・クリニック等、サービス単位で予約する業種の場合のみ）")
    staff_id: Optional[str] = Field(None, description="スタッフ指名がある場合のみ")
    special_requests: Optional[str] = Field(None, max_length=500, description="特別なご要望（あれば）")
    # Generic Resource Foundation Phase R4: CheckAvailabilityRequest.resource_type
    # と全く同じ意味・同じ許可値（resource_idは一切公開しない）。省略可能。
    resource_type: Optional[str] = Field(
        None, description="リソースの種別（部屋・ベッド等が混在する店舗で、自動判定できない場合にのみ指定。通常は省略可）"
    )

    @field_validator("resource_type")
    @classmethod
    def _validate_resource_type(cls, v: Optional[str]) -> Optional[str]:
        from app.schemas.resource import ALLOWED_RESOURCE_TYPES
        if v is not None and v not in ALLOWED_RESOURCE_TYPES:
            raise ValueError(f"resource_typeは次のいずれかである必要があります: {', '.join(ALLOWED_RESOURCE_TYPES)}")
        return v
    # AIの出力ではなく、ブラウザがOpenAI Realtimeのfunction_callイベントから
    # 直接読み取ったcall_idをそのまま転送する（AI自身にはこの値を生成させない）。
    call_id: str = Field(..., min_length=1, max_length=128, description="OpenAI Realtime APIのfunction_call call_id（ブラウザが転送。AIの引数ではない）")
    # Phase 5A追加。find_customer等と同じくAIの引数ではなく、フロントエンドが
    # この通話を識別するために自動的に付与する値。この予約に紐づくCustomer Memory
    # のlast_conversation_language（次回接客時のソフトなヒント）を更新するためだけに
    # 使う（app.services.conversation_language.get_session_language参照）。
    # 省略された場合は単に言語のヒントを記録しないだけで、予約自体には一切影響しない。
    session_id: Optional[str] = Field(
        None, description="フロントエンドが付与する、この通話を識別するopaqueな値。AIの引数ではない"
    )


class CreateReservationToolResponse(BaseModel):
    """Realtime AIへ返す最小レスポンス"""
    success: bool
    reservation_id: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None
    party_size: Optional[int] = None
    guest_name: Optional[str] = None
    # success=Falseの場合のみ設定。候補:
    # invalid_request / reservation_not_enabled / shop_closed / business_hours_not_configured /
    # temporary_closure / outside_business_hours / service_unavailable / staff_unavailable /
    # fully_booked / time_in_past / temporarily_unavailable / break_time
    #
    # Reservation Intelligence Phase D-2: break_time（休憩・予約停止時間との重なり）を
    # 追加。CheckAvailabilityResponseと同じ語彙。
    # check_availability(Phase3A)と同じ語彙をそのまま再利用している（AIが新しい
    # 概念を覚える必要をなくすため）。Phase3B.1でbusiness_hours_not_configured
    # （営業時間未設定）をshop_closed（定休日）と分離した。
    # fully_booked/staff_unavailableは「確認時点では
    # 空いていたが、予約確定時点で埋まっていた」ケースも含む（create_reservationは
    # 必ずその場でテーブル/スタッフの空き状況を再判定するため、Phase3Aの結果を
    # キャッシュして使い回すことはない）。
    # Reservation Intelligence Phase A: time_in_past（指定日時が既に過去）を
    # invalid_requestから分離。check_availabilityと同じ理由・同じ語彙。
    #
    # Generic Resource Foundation Phase R4: resource_type_required
    # （CheckAvailabilityResponseと同じ語彙）を追加。
    reason_code: Optional[str] = None
    # Generic Resource Foundation Phase R4: CheckAvailabilityResponseの
    # available_resource_typesと全く同じ意味（reason_code=="resource_type_required"
    # の場合のみ設定。resource_idは一切含まない）。
    available_resource_types: Optional[List[str]] = None


# ===== Outbound AI Phase 4A: find_customer Tool Calling用 =====
#
# 設計方針（重要・必ず守ること）:
# - shop_idはcheck_availability/create_reservationと同じくURLパス由来のみを使い、
#   引数(parameters)には含めない。
# - レスポンスは意図的に最小限にする。内部Customer MemoryのID・来店回数・
#   前回利用日・過去の予約内容等は一切含めない（本人確認前にAIへ渡してよいのは
#   「候補の氏名」だけ、というPrivacy Gateの設計をスキーマレベルでも強制する）。
#   本人確認後の詳細利用に関するtool（get_customer_context等）はPhase 4Aの
#   スコープ外（MVPでは「以前利用した可能性がある」と分かるだけで十分という
#   仕様書section15の判断に基づく）。
class FindCustomerToolRequest(BaseModel):
    """Realtime AIのfind_customer Toolからの引数 + フロントエンドが付与するsession_id"""
    phone: str = Field(..., min_length=1, max_length=20, description="お客様から伺った電話番号")
    # Outbound AI Phase4B追加。AIの出力JSONには一切含まれない
    # （Tool定義のparametersにこの項目自体が存在しない）。フロントエンドが
    # この通話を識別するために自動的に付与する値で、後続の
    # confirm_customer_identity / get_customer_contextの本人確認状態を
    # この通話に安全に紐付けるためだけに使う（app.services.customer_context
    # 参照）。省略された場合（session_id無し）でも候補の検索・表示名の
    # 返却自体はPhase4Aと全く同じ挙動のまま行うが、pending candidateの
    # 登録は行わない（confirm_customer_identityが呼び出せなくなるだけで、
    # find_customer自体の安全性・Privacy Gateには一切影響しない）。
    session_id: Optional[str] = Field(
        None, description="フロントエンドが付与する、この通話を識別するopaqueな値。AIの引数ではない"
    )


class FindCustomerToolResponse(BaseModel):
    """Realtime AIへ返す最小レスポンス（Privacy Gate: 本人確認前に渡してよい情報のみ）"""
    success: bool = True
    # "not_found"（該当なし） / "candidate_found"（候補が見つかった。本人確認が必要）
    status: str = "not_found"
    # status="candidate_found"の場合のみ設定。候補の氏名（表示用）のみで、
    # 内部ID・電話番号そのもの・来店回数・過去の予約内容は一切含めない。
    candidate_display_name: Optional[str] = None
    # Outbound AI Phase4B追加。confirm_customer_identity専用のopaqueな
    # 一時参照値（app.services.customer_context.issue_candidate参照）。
    # フロントエンドはこの値をJS変数として保持し、confirm_customer_identity
    # 呼び出し時に自動転送するが、AIへ渡すfunction_call_output
    # （Realtimeモデルが実際に読む内容）からは必ず取り除く。AIはこの値を
    # 一切知らない・扱わない（仕様書section17「Candidate Reference」）。
    candidate_reference: Optional[str] = None
    # success=Falseの場合のみ設定（invalid_request / temporarily_unavailable）。
    reason_code: Optional[str] = None


# ===== Outbound AI Phase 4B: confirm_customer_identity Tool Calling用 =====
#
# 設計方針（重要・必ず守ること。仕様書Phase4B section5-8「AIだけを信用しない」）:
# - AIの出力JSONに含まれるのはconfirmed(boolean)のみ。session_id・
#   candidate_referenceはToolのparameters自体に存在せず、フロントエンドが
#   find_customerの結果から保持していた値を、AIに一切見せずに自動転送する。
#   これにより、AIが「どのcandidateを確認するか」自体を選ぶ余地を構造的に
#   排除する（AIが担うのは「お客様が肯定したかどうか」という意味判断のみ）。
class ConfirmCustomerIdentityToolRequest(BaseModel):
    """Realtime AIのconfirm_customer_identity Toolからの引数(confirmed) + フロントエンドが自動転送する識別情報"""
    confirmed: bool = Field(..., description="お客様が候補の氏名を明確に肯定した場合のみtrue。それ以外は必ずfalse")
    session_id: Optional[str] = Field(
        None, description="フロントエンドが付与する、この通話を識別するopaqueな値。AIの引数ではない"
    )
    candidate_reference: Optional[str] = Field(
        None, description="フロントエンドがfind_customerの結果から保持し自動転送する値。AIの引数ではない"
    )


class ConfirmCustomerIdentityToolResponse(BaseModel):
    """Realtime AIへ返す最小レスポンス"""
    success: bool = True
    # verified / rejected / no_pending_candidate / reference_mismatch / temporarily_unavailable
    status: str = "no_pending_candidate"


# ===== Outbound AI Phase 4B: get_customer_context Tool Calling用 =====
#
# 設計方針（重要・必ず守ること）:
# - AIから受け取る引数は無い（parameters自体が空のobject）。session_idのみ
#   フロントエンドが自動転送し、対象のCustomerMemoryはバックエンド側の
#   本人確認状態(app.services.customer_context)からのみ特定する。AIが
#   phone/shop_id/customer_id等を指定して任意の顧客情報を引き出せる設計を
#   構造的に排除する（仕様書section7「AIだけを信用しない」）。
# - レスポンスは仕様書section9-12の許可リストのみ。医療系業種では
#   last_service_name/last_service_available_now/last_staff_nameは
#   常にNone（app.services.customer_context.build_customer_context参照）。
class GetCustomerContextToolRequest(BaseModel):
    """Realtime AIのget_customer_context Toolからの引数は無し。session_idのみフロントエンドが自動転送する"""
    session_id: Optional[str] = Field(
        None, description="フロントエンドが付与する、この通話を識別するopaqueな値。AIの引数ではない"
    )


class GetCustomerContextToolResponse(BaseModel):
    """Realtime AIへ返す最小レスポンス（本人確認済みの場合のみ意味のある値が入る）"""
    success: bool = True
    # not_verified / context_available / no_context / temporarily_unavailable
    status: str = "not_verified"
    display_name: Optional[str] = None
    visit_count: Optional[int] = None
    last_seen_at: Optional[datetime] = None
    last_reservation_at: Optional[datetime] = None
    # 以下2項目は医療系業種では常にNone（診療内容を示しうるため。section12）。
    last_service_name: Optional[str] = None
    last_service_available_now: Optional[bool] = None
    last_staff_name: Optional[str] = None
    # Phase 5A追加。前回の会話で実際に使われていた言語コード（例: "en"）。
    # あくまで次回接客時のソフトなヒントであり、これを理由にAIが自動的に
    # 言語を切り替えてよいわけではない（instructions側で明示。国籍・民族の
    # 推測材料にもしない）。医療系業種でも除外の対象外（診療内容を示さないため）。
    last_conversation_language: Optional[str] = None


# ===== Phase 5A: set_conversation_language Tool Calling用 =====
#
# 設計方針（重要・必ず守ること）:
# - AIから受け取る引数はlanguage_codeのみ。shop_idはURLパス由来、
#   voice_session_idはフロントエンドが自動転送する（find_customer等と同じ
#   パターン）。AIはどの店舗のどの通話かを一切指定できない。
# - language_codeはapp.language_registryに存在する既知のコードであり、
#   かつその店舗のai_supported_languagesに実際に含まれる場合のみ受理する
#   （app.services.conversation_language.set_session_language参照）。
#   許可されていない言語が指定された場合は、状態を変更せずsuccess=Falseで
#   返す（Toolとしてはエラーにせず、AIは現在の言語のまま会話を継続する）。
# - この状態は「現在の会話で使う言語」という会話進行上のヒントに過ぎず、
#   予約・顧客識別等の実際の処理には一切影響しない（あくまで
#   CustomerMemory.last_conversation_languageへの参考記録・次回以降の
#   ソフトなヒントとしてのみ使われる）。
class SetConversationLanguageToolRequest(BaseModel):
    """Realtime AIのset_conversation_language Toolからの引数 + フロントエンドが付与するsession_id"""
    language_code: str = Field(
        ..., min_length=2, max_length=10,
        description="お客様が明確にその言語で話した、または切り替えを依頼した言語コード（例: en, zh, ko, ja）",
    )
    session_id: Optional[str] = Field(
        None, description="フロントエンドが付与する、この通話を識別するopaqueな値。AIの引数ではない"
    )


class SetConversationLanguageToolResponse(BaseModel):
    """Realtime AIへ返す最小レスポンス"""
    success: bool = True
    # accepted（実際に切り替えを記録した） / not_allowed（この店舗では許可されていない言語、
    # またはsession_idが無く記録できなかった） / temporarily_unavailable
    status: str = "not_allowed"


# ===== Human Handoff基盤: request_callback Tool Calling用 =====
#
# 設計方針（重要・必ず守ること）:
# - shop_id・tenant_id・voice_session_id・call_idはAIに渡すToolのparameters
#   には一切含めない。他のToolと同じく、shop_idはURLパス由来、call_idは
#   ブラウザ側がOpenAI Realtime APIのfunction_callイベントから直接読み取った
#   値をリクエストボディへ追加して転送する（AI自身の出力JSONには含まれない）。
#   RECEPTRA側（このToolのエンドポイント実装）がcall_idを
#   "realtime_voice:{shop_id}:{call_id}" の形にnamespace化し、
#   CallbackRequest.idempotency_keyのDB一意インデックスで同一Tool呼び出しの
#   二重受付を最終防衛する（create_reservationと同じ設計パターン）。
# - inquiry_textはAIが要約した簡潔な用件テキストを想定する。生の音声内容
#   そのものではない（そもそもRealtime APIはブラウザ⇔OpenAI直結のため、
#   RECEPTRAサーバーは生の音声を経由しない）。
# - successは「CallbackRequestがDBへ保存できたかどうか」だけを表す。
#   担当者への実際の通知（Email送信・電話ジョブのenqueue）が成功したか
#   どうかはAIへ一切伝えない（AIが「担当者へ連絡済みです」のように
#   通知の成否まで確約してしまうことを防ぐため）。
class RequestCallbackToolRequest(BaseModel):
    """Realtime AIのrequest_callback Toolからの引数 + ブラウザが付与するcall_id/session_id"""
    customer_name: str = Field(..., min_length=1, max_length=255, description="お客様のお名前")
    customer_phone: str = Field(..., min_length=1, max_length=20, description="折り返し先電話番号")
    inquiry_text: str = Field(..., min_length=1, max_length=1000, description="お問い合わせ内容の簡潔な要約")
    desired_date: Optional[str] = Field(None, description="来店・予約希望日（YYYY-MM-DD、分かる場合のみ）")
    desired_time: Optional[str] = Field(None, description="来店・予約希望時刻（HH:MM、24時間表記、分かる場合のみ）")
    party_size: Optional[int] = Field(None, ge=1, le=999, description="人数（分かる場合のみ）")
    service_id: Optional[str] = Field(None, description="サービスID（分かる場合のみ）")
    reason_code: Optional[str] = Field(None, max_length=50, description="折り返しが必要になった理由")
    # AIの出力ではなく、ブラウザがOpenAI Realtimeのfunction_callイベントから
    # 直接読み取ったcall_idをそのまま転送する（AI自身にはこの値を生成させない）。
    call_id: str = Field(..., min_length=1, max_length=128, description="OpenAI Realtime APIのfunction_call call_id（ブラウザが転送。AIの引数ではない）")
    session_id: Optional[str] = Field(
        None, description="フロントエンドが付与する、この通話を識別するopaqueな値。AIの引数ではない"
    )


class RequestCallbackToolResponse(BaseModel):
    """Realtime AIへ返す最小レスポンス"""
    success: bool
    # success=Falseの場合のみ設定。候補: invalid_request / temporarily_unavailable
    reason_code: Optional[str] = None
