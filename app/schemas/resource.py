"""
Resource (予約リソース) スキーマ定義 — Generic Resource Foundation Phase R1

app/schemas/shop_table.pyと同じ構造・同じ検証の厳密さに合わせている。
attributesはPhase R1では一切スキーマに含めない（app/models/resource.pyの
docstring・Generic Resource Architecture Audit Section10/11/22参照）。
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator


# resource_typeの許可値。DB enumにはしない（app/models/resource.pyの
# docstring参照）が、自由入力を完全無制限にはせず、API層でこの許可リストと
# 照合する（Generic Resource Architecture Audit Section7/22の確定仕様）。
# 将来、この一覧以外の種別が必要になっても、DB migrationは不要で
# この一覧を更新するだけで済む。
ALLOWED_RESOURCE_TYPES = (
    "room",           # 部屋（個室、貸室等）
    "bed",            # ベッド（整体・医療の施術/診察ベッド等）
    "chair",          # 椅子（美容室のチェア等）
    "vehicle",         # 車両（レンタカー等）
    "karaoke_room",   # カラオケルーム
    "classroom",      # 教室
    "equipment",      # 設備・機材
    "other",          # その他
)


class ResourceCreateRequest(BaseModel):
    """予約リソース作成リクエスト"""
    name: str = Field(..., min_length=1, max_length=100, description="リソース名（例：個室A、施術ベッド1）")
    resource_type: str = Field(..., description=f"種別。許可値: {', '.join(ALLOWED_RESOURCE_TYPES)}")
    # ShopTable.capacityとは異なりnullable。「収容人数」の概念が意味を持たない
    # 種別（車両・設備等）を想定するため必須にしない
    # （Generic Resource Architecture Audit Section9の確定仕様）。
    capacity: Optional[int] = Field(None, gt=0, le=999, description="収容可能人数（任意。概念が無い種別では省略可）")

    @field_validator("resource_type")
    @classmethod
    def _validate_resource_type(cls, v: str) -> str:
        if v not in ALLOWED_RESOURCE_TYPES:
            raise ValueError(f"resource_typeは次のいずれかである必要があります: {', '.join(ALLOWED_RESOURCE_TYPES)}")
        return v


class ResourceUpdateRequest(BaseModel):
    """予約リソース更新リクエスト（送られたフィールドのみ更新）"""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    resource_type: Optional[str] = Field(None, description=f"種別。許可値: {', '.join(ALLOWED_RESOURCE_TYPES)}")
    capacity: Optional[int] = Field(None, gt=0, le=999)
    is_active: Optional[bool] = None

    @field_validator("resource_type")
    @classmethod
    def _validate_resource_type(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ALLOWED_RESOURCE_TYPES:
            raise ValueError(f"resource_typeは次のいずれかである必要があります: {', '.join(ALLOWED_RESOURCE_TYPES)}")
        return v


class ResourceResponse(BaseModel):
    """予約リソースレスポンス"""
    id: str
    shop_id: str
    name: str
    resource_type: str
    capacity: Optional[int] = None
    is_active: bool
    display_order: int
    created_at: datetime

    class Config:
        from_attributes = True
