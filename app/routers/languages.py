"""
対応言語レジストリ（Phase 5A）エンドポイント

app.taxonomy / app.routers.taxonomy と全く同じ考え方: フロントエンド
（shop-manage.htmlの言語選択チェックボックス・shop.htmlの対応言語表示）と
バックエンドの言語コード定義がずれないよう、app.language_registry を
唯一の情報源として、そのまま返すだけの認証不要な公開エンドポイントにする。
"""

from fastapi import APIRouter
from app.language_registry import languages_as_dict, REQUIRED_AI_LANGUAGE

router = APIRouter(prefix="/api/v1/languages", tags=["languages"])


@router.get("", summary="対応言語一覧を取得")
async def get_languages():
    result = languages_as_dict()
    result["required_ai_language"] = REQUIRED_AI_LANGUAGE
    return result
