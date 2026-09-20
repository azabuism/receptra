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
from datetime import datetime, date as date_type

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db
from app.models.shop import Shop
from app.services import realtime_voice_ai
from app.schemas.reservation import CheckAvailabilityRequest, CheckAvailabilityResponse
from app.routers.reservations import check_single_slot_availability

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


# Phase3A: check_availability Tool Calling用の別バケット。
# 1通話の中でRealtime AIが複数回（日時を変えて）呼び出す可能性があるため、
# セッション発行(/session)用の制限より高めに設定する。
_TOOL_RATE_LIMIT_WINDOW_SECONDS = 60
_TOOL_RATE_LIMIT_MAX_REQUESTS = 30
_recent_tool_requests: dict[str, list] = {}


def _check_tool_rate_limit(shop_id: str) -> None:
    now = time.monotonic()
    bucket = _recent_tool_requests.setdefault(shop_id, [])
    bucket[:] = [t for t in bucket if now - t < _TOOL_RATE_LIMIT_WINDOW_SECONDS]
    if len(bucket) >= _TOOL_RATE_LIMIT_MAX_REQUESTS:
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


@router.post("/tools/check-availability", response_model=CheckAvailabilityResponse)
async def check_availability_tool(
    shop_id: str,
    request: CheckAvailabilityRequest,
    db: AsyncSession = Depends(get_db),
) -> CheckAvailabilityResponse:
    """
    Realtime Voice AI Phase3A: check_availability Tool Calling専用エンドポイント（認証不要）。

    設計方針（重要・必ず守ること）:
    - shop_id はURLパスの値のみを使う。リクエストボディ(CheckAvailabilityRequest)には
      shop_idを含めていない。これはRealtime AI（OpenAI側のFunction Calling引数）に
      他店舗のshop_idを自由に指定させないため。実際に通話している店舗のshop_idは
      ブラウザ側が「今接続しているセッションのshop_id」から付与する。
    - このエンドポイントは、どんな失敗（店舗未検出・不正な日付/時刻・想定外の例外等）
      であっても available=True を返してはならない。Realtime AIが「たぶん空いている」
      と推測することを防ぐため、FastAPI（このエンドポイント）とDBを唯一の正とする。
      失敗時は available=False, reason_code="invalid_request" を返す
      （レート制限のみ既存の/sessionと同様429を返す。呼び出し側(ブラウザ)で
      429やネットワーク断が発生した場合も、AIには available=false 相当として
      安全に案内させる必要がある — フロントエンド側の実装で対応する）。
    """
    _check_tool_rate_limit(shop_id)

    def _safe_fallback(reason_code: str) -> CheckAvailabilityResponse:
        return CheckAvailabilityResponse(
            available=False,
            date=request.date,
            time=request.time,
            party_size=request.party_size,
            reason_code=reason_code,
        )

    try:
        shop = await db.get(Shop, shop_id)
        if not shop or not shop.is_active:
            return _safe_fallback("invalid_request")

        try:
            target_date = date_type.fromisoformat(request.date)
            target_time = datetime.strptime(request.time, "%H:%M").time()
        except ValueError:
            return _safe_fallback("invalid_request")

        available, reason_code = await check_single_slot_availability(
            db,
            shop,
            target_date,
            target_time,
            request.party_size,
            request.service_id,
            request.staff_id,
        )
        return CheckAvailabilityResponse(
            available=available,
            date=request.date,
            time=request.time,
            party_size=request.party_size,
            reason_code=reason_code,
        )
    except HTTPException:
        raise
    except Exception as e:
        # 例外の詳細をAIやレスポンスに漏らさず、必ず安全側(available=False)で返す。
        logger.error("check_availability Tool処理に失敗 (shop_id=%s): %s", shop_id, e)
        return _safe_fallback("invalid_request")
