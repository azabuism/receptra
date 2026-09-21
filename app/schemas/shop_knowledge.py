"""
Shop Knowledge & FAQ（Phase3D）スキーマ定義
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator

# 「あり/なし/未設定」の3値のみを許可するフィールド用（駐車場・支払い方法）
_YES_NO = {"yes", "no"}
# 「あり/なし/条件付き/未設定」の4値を許可するフィールド用（設備・利用条件）
_YES_NO_CONDITIONAL = {"yes", "no", "conditional"}


def _normalize_empty(v):
    if v is None or v == "":
        return None
    return v


class ShopKnowledgeUpdateRequest(BaseModel):
    """店舗情報（構造化知識）保存/更新リクエスト（送られたフィールドのみ更新）"""

    # 駐車場
    parking_available: Optional[str] = Field(None, description="あり/なし/未設定: yes/no/None")
    parking_spaces: Optional[int] = Field(None, ge=0, description="駐車台数")
    parking_type: Optional[str] = Field(None, max_length=100)
    parking_fee: Optional[str] = Field(None, max_length=100)
    parking_location: Optional[str] = Field(None, max_length=255)
    partner_parking: Optional[str] = Field(None, max_length=255)
    parking_full_guidance: Optional[str] = Field(None, max_length=2000)
    parking_notes: Optional[str] = Field(None, max_length=2000)

    # 支払い方法
    payment_cash: Optional[str] = Field(None, description="yes/no/None")
    payment_credit_card: Optional[str] = Field(None, description="yes/no/None")
    payment_debit_card: Optional[str] = Field(None, description="yes/no/None")
    payment_qr_code: Optional[str] = Field(None, description="yes/no/None")
    payment_emoney: Optional[str] = Field(None, description="yes/no/None")
    payment_notes: Optional[str] = Field(None, max_length=1000)

    # 支払い方法（Phase3H: ブランド単位の詳細。yes/no/conditional/None）
    payment_credit_visa: Optional[str] = Field(None, description="yes/no/conditional/None")
    payment_credit_mastercard: Optional[str] = Field(None, description="yes/no/conditional/None")
    payment_credit_jcb: Optional[str] = Field(None, description="yes/no/conditional/None")
    payment_credit_amex: Optional[str] = Field(None, description="yes/no/conditional/None")
    payment_credit_diners: Optional[str] = Field(None, description="yes/no/conditional/None")
    payment_credit_other: Optional[str] = Field(None, description="yes/no/conditional/None")
    payment_qr_paypay: Optional[str] = Field(None, description="yes/no/conditional/None")
    payment_qr_au_pay: Optional[str] = Field(None, description="yes/no/conditional/None")
    payment_qr_d_barai: Optional[str] = Field(None, description="yes/no/conditional/None")
    payment_qr_rakuten_pay: Optional[str] = Field(None, description="yes/no/conditional/None")
    payment_qr_merpay: Optional[str] = Field(None, description="yes/no/conditional/None")
    payment_qr_other: Optional[str] = Field(None, description="yes/no/conditional/None")
    payment_emoney_transit_ic: Optional[str] = Field(None, description="yes/no/conditional/None")
    payment_emoney_id: Optional[str] = Field(None, description="yes/no/conditional/None")
    payment_emoney_quicpay: Optional[str] = Field(None, description="yes/no/conditional/None")
    payment_emoney_rakuten_edy: Optional[str] = Field(None, description="yes/no/conditional/None")
    payment_emoney_waon: Optional[str] = Field(None, description="yes/no/conditional/None")
    payment_emoney_nanaco: Optional[str] = Field(None, description="yes/no/conditional/None")
    payment_emoney_other: Optional[str] = Field(None, description="yes/no/conditional/None")

    # 設備・利用条件
    wifi_available: Optional[str] = Field(None, description="yes/no/conditional/None")
    private_room_available: Optional[str] = Field(None, description="yes/no/conditional/None")
    wheelchair_accessible: Optional[str] = Field(None, description="yes/no/conditional/None")
    children_allowed: Optional[str] = Field(None, description="yes/no/conditional/None")
    smoking_policy: Optional[str] = Field(None, description="yes(喫煙可)/no(禁煙)/conditional(分煙等)/None")
    pets_allowed: Optional[str] = Field(None, description="yes/no/conditional/None")
    elevator_available: Optional[str] = Field(None, description="yes/no/conditional/None")
    facilities_notes: Optional[str] = Field(None, max_length=2000)

    # 来店前案内
    required_items: Optional[str] = Field(None, max_length=2000)
    pre_visit_instructions: Optional[str] = Field(None, max_length=2000)
    arrival_guidance: Optional[str] = Field(None, max_length=1000)

    # キャンセル・遅刻ポリシー
    cancellation_policy: Optional[str] = Field(None, max_length=2000)

    @field_validator(
        "parking_available", "payment_cash", "payment_credit_card",
        "payment_debit_card", "payment_qr_code", "payment_emoney",
        mode="before",
    )
    @classmethod
    def _validate_yes_no(cls, v):
        v = _normalize_empty(v)
        if v is None:
            return None
        if v not in _YES_NO:
            raise ValueError(f"次のいずれかを指定してください: {', '.join(sorted(_YES_NO))}")
        return v

    @field_validator(
        "wifi_available", "private_room_available", "wheelchair_accessible",
        "children_allowed", "smoking_policy", "pets_allowed", "elevator_available",
        "payment_credit_visa", "payment_credit_mastercard", "payment_credit_jcb",
        "payment_credit_amex", "payment_credit_diners", "payment_credit_other",
        "payment_qr_paypay", "payment_qr_au_pay", "payment_qr_d_barai",
        "payment_qr_rakuten_pay", "payment_qr_merpay", "payment_qr_other",
        "payment_emoney_transit_ic", "payment_emoney_id", "payment_emoney_quicpay",
        "payment_emoney_rakuten_edy", "payment_emoney_waon", "payment_emoney_nanaco",
        "payment_emoney_other",
        mode="before",
    )
    @classmethod
    def _validate_yes_no_conditional(cls, v):
        v = _normalize_empty(v)
        if v is None:
            return None
        if v not in _YES_NO_CONDITIONAL:
            raise ValueError(f"次のいずれかを指定してください: {', '.join(sorted(_YES_NO_CONDITIONAL))}")
        return v

    @field_validator(
        "parking_type", "parking_fee", "parking_location", "partner_parking",
        "parking_full_guidance", "parking_notes", "payment_notes",
        "facilities_notes", "required_items", "pre_visit_instructions",
        "arrival_guidance", "cancellation_policy",
        mode="before",
    )
    @classmethod
    def _validate_free_text(cls, v):
        return _normalize_empty(v)


class ShopKnowledgeResponse(BaseModel):
    """店舗情報（構造化知識）レスポンス（未設定の場合はNoneが返る）"""

    shop_id: str

    parking_available: Optional[str] = None
    parking_spaces: Optional[int] = None
    parking_type: Optional[str] = None
    parking_fee: Optional[str] = None
    parking_location: Optional[str] = None
    partner_parking: Optional[str] = None
    parking_full_guidance: Optional[str] = None
    parking_notes: Optional[str] = None

    payment_cash: Optional[str] = None
    payment_credit_card: Optional[str] = None
    payment_debit_card: Optional[str] = None
    payment_qr_code: Optional[str] = None
    payment_emoney: Optional[str] = None
    payment_notes: Optional[str] = None

    payment_credit_visa: Optional[str] = None
    payment_credit_mastercard: Optional[str] = None
    payment_credit_jcb: Optional[str] = None
    payment_credit_amex: Optional[str] = None
    payment_credit_diners: Optional[str] = None
    payment_credit_other: Optional[str] = None
    payment_qr_paypay: Optional[str] = None
    payment_qr_au_pay: Optional[str] = None
    payment_qr_d_barai: Optional[str] = None
    payment_qr_rakuten_pay: Optional[str] = None
    payment_qr_merpay: Optional[str] = None
    payment_qr_other: Optional[str] = None
    payment_emoney_transit_ic: Optional[str] = None
    payment_emoney_id: Optional[str] = None
    payment_emoney_quicpay: Optional[str] = None
    payment_emoney_rakuten_edy: Optional[str] = None
    payment_emoney_waon: Optional[str] = None
    payment_emoney_nanaco: Optional[str] = None
    payment_emoney_other: Optional[str] = None

    wifi_available: Optional[str] = None
    private_room_available: Optional[str] = None
    wheelchair_accessible: Optional[str] = None
    children_allowed: Optional[str] = None
    smoking_policy: Optional[str] = None
    pets_allowed: Optional[str] = None
    elevator_available: Optional[str] = None
    facilities_notes: Optional[str] = None

    required_items: Optional[str] = None
    pre_visit_instructions: Optional[str] = None
    arrival_guidance: Optional[str] = None

    cancellation_policy: Optional[str] = None

    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ShopFAQCreateRequest(BaseModel):
    """FAQ新規作成リクエスト"""

    question: str = Field(..., min_length=1, max_length=1000)
    answer: str = Field(..., min_length=1, max_length=4000)
    is_active: bool = Field(True)
    sort_order: int = Field(0)


class ShopFAQUpdateRequest(BaseModel):
    """FAQ更新リクエスト（送られたフィールドのみ更新）"""

    question: Optional[str] = Field(None, min_length=1, max_length=1000)
    answer: Optional[str] = Field(None, min_length=1, max_length=4000)
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class ShopFAQResponse(BaseModel):
    """FAQ レスポンス"""

    id: str
    shop_id: str
    question: str
    answer: str
    is_active: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class GetShopInfoToolRequest(BaseModel):
    """
    Realtime Voice AI get_shop_info Tool専用リクエスト（Phase3D、認証不要）。

    重要: shop_id/tenant_idはこのリクエストボディに一切含めない
    （check_availability/create_reservationと同じ方針）。実際にどの店舗かは
    必ずURLパス(app/routers/realtime_voice.py側)から決まる値のみを使う。
    topicの正当性チェック（VALID_SHOP_INFO_TOPICSに含まれるか）はここでは
    行わず、app/routers/shop_knowledge.py の get_shop_info_for_ai() 側で
    reason_code="invalid_request"として安全側に処理する
    （Toolのenum定義と二重に検証することを許容する設計）。
    """

    topic: str = Field(..., min_length=1, max_length=50)
    query: Optional[str] = Field(None, max_length=200)
