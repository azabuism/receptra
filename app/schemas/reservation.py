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
