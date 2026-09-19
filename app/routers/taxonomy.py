"""
カテゴリー分類（タクソノミー）エンドポイント

トップページ・サブカテゴリー選択ページ・検索結果ページが、業種とサブカテゴリーの一覧を
取得するために利用する。フロントエンドとバックエンドで分類がずれないよう、この内容が唯一の
情報源となる。
"""

from fastapi import APIRouter
from app.taxonomy import taxonomy_as_dict

router = APIRouter(prefix="/api/v1/taxonomy", tags=["taxonomy"])


@router.get("", summary="カテゴリー分類の一覧を取得")
async def get_taxonomy():
    return taxonomy_as_dict()
