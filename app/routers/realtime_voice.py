"""
OpenAI Realtime API（ブラウザ ⇔ WebRTC 直結）用の最小限のバックエンド

Phase1のスコープ:
- ブラウザがOpenAI Realtime APIへWebRTCで直接接続するための、短命の
  ephemeralトークン(client secret)を発行するエンドポイントのみを提供する。
- Function Calling・予約DB接続・メニュー/料金の注入はまだ行わない
  （Phase3以降で段階的に追加する）。
- 音声そのものはブラウザとOpenAIの間を直接WebRTCで流れるため、
  このサーバーを経由しない。

既存の shop_booking_ai.py（チャット予約）・voice_ai.py・vonage_voice.py・
shop.html は一切変更していない。
"""

import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db
from app.models.shop import Shop
from app.services import realtime_voice_ai

logger = logging.getLogger("receptra.realtime_voice")

router = APIRouter(prefix="/api/v1/shops/{shop_id}/realtime-voice", tags=["realtime_voice"])

# いたずら目的の大量リクエストによるOpenAI側コスト濫用を防ぐための
# 簡易レート制限（プロセス内メモリ保持のPhase1向け最小実装。
# 既存のセッション管理(voice_ai.py の _sessions)と同様、単一ワーカー前提）。
_RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMIT_MAX_REQUESTS = 10
_recent_requests: dict[str, list] = {}


def _check_rate_limit(shop_id: str) -> None:
    now = time.monotonic()
    bucket = _recent_requests.setdefault(shop_id, [])
    bucket[:] = [t for t in bucket if now - t < _RATE_LIMIT_WINDOW_SECONDS]
    if len(bucket) >= _RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail="リクエストが多すぎます。しばらくしてから再度お試しください。",
        )
    bucket.append(now)


@router.post("/session")
async def create_realtime_voice_session(shop_id: str, db: AsyncSession = Depends(get_db)):
    """
    ブラウザ用の短命ephemeralトークンを発行する（会員登録不要・認証不要）。
    OPENAI_API_KEY自体はサーバー側にのみ保持し、レスポンスには含めない。
    """
    _check_rate_limit(shop_id)

    shop = await db.get(Shop, shop_id)
    if not shop or not shop.is_active:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")

    try:
        session_info = await realtime_voice_ai.create_realtime_session(db, shop)
    except Exception as e:
        # 例外メッセージにAPIキー等の秘密情報が含まれる可能性があるため、
        # 詳細はログにのみ出力し、レスポンスには一般的なメッセージのみ返す。
        logger.error("Realtimeセッション発行に失敗 (shop_id=%s): %s", shop_id, e)
        raise HTTPException(
            status_code=502,
            detail="音声AIサービスへの接続準備に失敗しました。しばらくしてから再度お試しください。",
        )

    return {
        "shop_id": shop_id,
        "shop_name": shop.name,
        **session_info,
    }
