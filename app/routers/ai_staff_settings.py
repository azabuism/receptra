"""
AIスタッフ設定 API（Realtime Voice AI Phase2）

店舗オーナーが自店のRealtime Voice AI受付の声・名前・性格・話し方を
設定するためのCRUD + voice試聴エンドポイント。

セキュリティ方針（app/routers/shops.py・staff.py と同じ既存パターンを踏襲）:
- すべての変更系エンドポイントは既存の get_current_user（JWT認証）を使用する。
- shop_idはクライアントから渡された値をそのまま信用せず、ログイン中の
  オーナー（current_user.tenant_id）がそのshopを実際に所有しているかを
  必ずサーバー側で検証する（_get_owned_shop）。他店舗の設定を取得・変更
  することはできない。
- 参照系（GET設定）は既存店舗ページ用ではなく管理画面専用のため、これも
  認証必須にしている（Realtime Voice AI自体からの読み取りは
  app.services.realtime_voice_ai 内で直接DBから行っており、この
  エンドポイントを経由しない）。
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.schemas.user import CurrentUser
from app.models.shop import Shop
from app.models.ai_staff_settings import AIStaffSettings
from app.schemas.ai_staff_settings import (
    AIStaffSettingsUpdateRequest,
    AIStaffSettingsResponse,
    VoicePreviewRequest,
)
from app.services import realtime_voice_ai

router = APIRouter(prefix="/api/v1/shops/{shop_id}/ai-staff-settings", tags=["ai_staff_settings"])


async def _get_owned_shop(shop_id: str, current_user: CurrentUser, db: AsyncSession) -> Shop:
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")
    if shop.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="この店舗を編集する権限がありません")
    return shop


async def _get_settings_row(shop_id: str, db: AsyncSession) -> AIStaffSettings | None:
    result = await db.execute(select(AIStaffSettings).where(AIStaffSettings.shop_id == shop_id))
    return result.scalars().first()


def _to_response(shop_id: str, row: AIStaffSettings | None) -> AIStaffSettingsResponse:
    if row is None:
        # レコードが存在しない場合は全項目Noneのデフォルトレスポンスを返す
        # （フロント側はこれをもって「Phase1相当のデフォルト動作」として表示する）。
        return AIStaffSettingsResponse(shop_id=shop_id)
    return AIStaffSettingsResponse(
        shop_id=shop_id,
        staff_name=row.staff_name,
        voice=row.voice,
        personality_preset=row.personality_preset,
        politeness_level=row.politeness_level,
        brightness=row.brightness,
        energy_level=row.energy_level,
        greeting=row.greeting,
        custom_instructions=row.custom_instructions,
        updated_at=row.updated_at,
    )


@router.get("", response_model=AIStaffSettingsResponse, summary="AIスタッフ設定を取得")
async def get_ai_staff_settings(
    shop_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_shop(shop_id, current_user, db)
    row = await _get_settings_row(shop_id, db)
    return _to_response(shop_id, row)


@router.put("", response_model=AIStaffSettingsResponse, summary="AIスタッフ設定を保存（無ければ新規作成）")
async def save_ai_staff_settings(
    shop_id: str,
    request: AIStaffSettingsUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_shop(shop_id, current_user, db)

    row = await _get_settings_row(shop_id, db)
    update_data = request.model_dump(exclude_unset=True)
    now = datetime.utcnow()

    if row is None:
        # lazy creation: 初めて保存されたタイミングでのみレコードを作成する。
        # 既存店舗全件へ強制的に空レコードを作る必要はない
        # （ai_staff_settings が無い店舗はrealtime_voice_ai側で
        # Phase1相当のデフォルト動作にフォールバックするため）。
        row = AIStaffSettings(
            id=str(uuid.uuid4()),
            shop_id=shop_id,
            created_at=now,
            updated_at=now,
        )
        db.add(row)

    for field, value in update_data.items():
        setattr(row, field, value)
    row.updated_at = now

    await db.commit()
    await db.refresh(row)
    return _to_response(shop_id, row)


@router.get("/voice-options", summary="Realtime API用voice一覧を取得")
async def get_voice_options(
    shop_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    現在利用可能なRealtime API用voiceの一覧を返す。
    重要: この一覧は app.services.realtime_voice_ai.REALTIME_VOICES を
    そのまま参照しており、ハードコードの二重管理を避けている。
    一覧自体は今後OpenAI側の仕様変更により増減しうる（コード側コメント参照）。
    """
    await _get_owned_shop(shop_id, current_user, db)
    return {"voices": realtime_voice_ai.REALTIME_VOICES}


@router.post("/voice-preview", summary="voiceを試聴するための短命トークンを発行")
async def create_voice_preview(
    shop_id: str,
    request: VoicePreviewRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    店舗管理画面の「試聴」ボタン用に、短命のephemeralトークンを発行する。
    OPENAI_API_KEY自体はブラウザへ一切渡さない（既存のRealtime Voice AI
    セッション発行と同じ設計）。
    """
    await _get_owned_shop(shop_id, current_user, db)

    try:
        preview = await realtime_voice_ai.create_voice_preview_session(request.voice)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail="voice試聴の準備に失敗しました。しばらくしてから再度お試しください。",
        ) from e

    return preview
