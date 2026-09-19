"""
マイページ・口コミ・クーポン関連スキーマ（Pydantic）
"""

from datetime import datetime
from typing import Optional, List, Literal

from pydantic import BaseModel, Field


# ===== レビュー（口コミ） =====

class ReviewCreateRequest(BaseModel):
    """レビュー投稿リクエスト（ログイン中のユーザー向け）"""

    shop_id: str
    title: Optional[str] = Field(None, max_length=255)
    comment: Optional[str] = Field(None, max_length=2000)
    overall_rating: float = Field(..., ge=1, le=5)
    food_rating: Optional[float] = Field(None, ge=1, le=5)
    service_rating: Optional[float] = Field(None, ge=1, le=5)
    atmosphere_rating: Optional[float] = Field(None, ge=1, le=5)
    value_rating: Optional[float] = Field(None, ge=1, le=5)


class ReviewOwnerReplyRequest(BaseModel):
    """オーナーからのレビュー返信リクエスト"""

    shop_response: str = Field(..., min_length=1, max_length=2000)


class ReviewResponse(BaseModel):
    """レビュー レスポンス"""

    id: str
    shop_id: str
    shop_name: Optional[str] = None
    reviewer_name: Optional[str] = None
    title: Optional[str] = None
    comment: Optional[str] = None
    overall_rating: float
    food_rating: Optional[float] = None
    service_rating: Optional[float] = None
    atmosphere_rating: Optional[float] = None
    value_rating: Optional[float] = None
    shop_response: Optional[str] = None
    shop_response_at: Optional[datetime] = None
    created_at: datetime
    is_mine: bool = False

    class Config:
        from_attributes = True


# ===== 行きたい店・いいね =====

RelationType = Literal["want_to_go", "like"]


class RelationToggleRequest(BaseModel):
    """行きたい店・いいねのトグル（追加されていれば削除、無ければ追加）"""

    shop_id: str
    relation_type: RelationType


class RelationToggleResponse(BaseModel):
    """トグル後の状態"""

    shop_id: str
    relation_type: RelationType
    active: bool  # トグル後、関係が存在するかどうか


class ShopRelationStatus(BaseModel):
    """特定の店舗に対する現在ログイン中ユーザーの関係"""

    shop_id: str
    want_to_go: bool
    like: bool


# ===== マイページに表示する店舗の簡易情報 =====

class MyPageShopSummary(BaseModel):
    """マイページ内で使う店舗の要約情報"""

    id: str
    name: str
    category: Optional[str] = None
    business_type: Optional[str] = None
    thumbnail_url: Optional[str] = None
    average_rating: Optional[float] = None
    visit_count: Optional[int] = None  # よく行く店の集計に利用


# ===== クーポン =====

class CouponCreateRequest(BaseModel):
    """クーポン作成リクエスト（オーナー向け・最小構成）"""

    code: str = Field(..., min_length=1, max_length=50)
    description: Optional[str] = Field(None, max_length=1000)
    discount_type: Literal["percentage", "fixed"]
    discount_value: float = Field(..., gt=0)
    start_date: datetime
    end_date: datetime
    usage_limit: Optional[int] = Field(None, ge=1)
    max_per_customer: Optional[int] = Field(None, ge=1)


class CouponResponse(BaseModel):
    """クーポン レスポンス"""

    id: str
    shop_id: str
    shop_name: Optional[str] = None
    code: str
    description: Optional[str] = None
    discount_type: str
    discount_value: float
    start_date: datetime
    end_date: datetime
    usage_limit: Optional[int] = None
    usage_count: int
    is_active: bool

    class Config:
        from_attributes = True


# ===== マイページ サマリー =====

class MyPageSummaryResponse(BaseModel):
    """マイページのトップに表示するサマリー情報"""

    display_name: Optional[str] = None
    frequent_shops: List[MyPageShopSummary] = []       # よく行く店
    want_to_go_shops: List[MyPageShopSummary] = []     # 行きたい店
    liked_shops: List[MyPageShopSummary] = []          # いいねした店
    like_count: int = 0                                # いいねの数
    my_reviews: List[ReviewResponse] = []               # 自分の口コミ
    available_coupons: List[CouponResponse] = []        # 使えるクーポン
    pending_review_shops: List[MyPageShopSummary] = []  # 来店済みでまだ口コミ未投稿のお店（おすすめ）
