"""
Reservation (予約) スキーマ定義
リクエスト/レスポンスのPydantic モデル
"""

from datetime import datetime, date as date_type
from typing import Optional, List
from pydantic import BaseModel, Field, EmailStr


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
    service_id: Optional[str] = None
    service_name: Optional[str] = None
    staff_id: Optional[str] = None
    staff_name: Optional[str] = None
    coupon_id: Optional[str] = None
    coupon_code: Optional[str] = None
    discount_amount: Optional[int] = None
    guest_name: Optional[str] = None
    guest_phone: Optional[str] = None
    guest_email: Optional[str] = None
    reservation_date: datetime
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


class CheckAvailabilityResponse(BaseModel):
    """Realtime AIへ返す最小レスポンス（DBへの保存は行わない）"""
    available: bool
    date: str
    time: str
    party_size: int
    # available=False の場合のみ設定。候補:
    # fully_booked / outside_business_hours / shop_closed / business_hours_not_configured /
    # temporary_closure / service_unavailable / staff_unavailable / invalid_request /
    # temporarily_unavailable
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
    reason_code: Optional[str] = None


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
    # fully_booked / temporarily_unavailable
    #
    # check_availability(Phase3A)と同じ語彙をそのまま再利用している（AIが新しい
    # 概念を覚える必要をなくすため）。Phase3B.1でbusiness_hours_not_configured
    # （営業時間未設定）をshop_closed（定休日）と分離した。
    # fully_booked/staff_unavailableは「確認時点では
    # 空いていたが、予約確定時点で埋まっていた」ケースも含む（create_reservationは
    # 必ずその場でテーブル/スタッフの空き状況を再判定するため、Phase3Aの結果を
    # キャッシュして使い回すことはない）。
    reason_code: Optional[str] = None


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
