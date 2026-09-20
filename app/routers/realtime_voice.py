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
from app.schemas.reservation import (
    CheckAvailabilityRequest, CheckAvailabilityResponse,
    CreateReservationToolRequest, CreateReservationToolResponse,
    ReservationCreateRequest,
)
from app.routers.reservations import check_single_slot_availability, create_reservation

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


# Phase3B: create_reservation Tool Calling用のさらに別バケット。DB書き込みを
# 伴う操作であり、1通話中に何度も予約を作り直すことは想定していないため、
# check_availability用のバケットより厳しめに設定し、互いのクォータを
# 消費し合わないよう完全に独立させる。
_BOOKING_TOOL_RATE_LIMIT_WINDOW_SECONDS = 60
_BOOKING_TOOL_RATE_LIMIT_MAX_REQUESTS = 10
_recent_booking_tool_requests: dict[str, list] = {}


def _check_booking_tool_rate_limit(shop_id: str) -> None:
    now = time.monotonic()
    bucket = _recent_booking_tool_requests.setdefault(shop_id, [])
    bucket[:] = [t for t in bucket if now - t < _BOOKING_TOOL_RATE_LIMIT_WINDOW_SECONDS]
    if len(bucket) >= _BOOKING_TOOL_RATE_LIMIT_MAX_REQUESTS:
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
    - 失敗時のreason_codeは2種類に分ける（ユーザー指摘によりPhase3A完了後に修正）:
      - "invalid_request": 日付/時刻の形式が不正など、引数自体が不正な場合
        （＝入力を直せば解決しうる）
      - "temporarily_unavailable": 店舗未検出・想定外の例外等、入力は正しいが
        現時点でこちらの都合で確定できない場合（＝入力を直しても解決しない）
      レート制限のみ既存の/sessionと同様429を返す。呼び出し側(ブラウザ)で
      429やネットワーク断が発生した場合も、AIには
      temporarily_unavailable相当として安全に案内させる必要がある
      （フロントエンド側の実装で対応する）。
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
            # shop_idはAIの引数ではなくURLパス由来のため、これが起きるのは
            # 店舗の非公開化等こちら側の事情であり、AIやお客様の入力ミスではない。
            return _safe_fallback("temporarily_unavailable")

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
        # DB/内部エラーであり、お客様の入力が悪いわけではないためtemporarily_unavailable。
        logger.error("check_availability Tool処理に失敗 (shop_id=%s): %s", shop_id, e)
        return _safe_fallback("temporarily_unavailable")


@router.post("/tools/create-reservation", response_model=CreateReservationToolResponse)
async def create_reservation_tool(
    shop_id: str,
    request: CreateReservationToolRequest,
    db: AsyncSession = Depends(get_db),
) -> CreateReservationToolResponse:
    """
    Realtime Voice AI Phase3B: create_reservation Tool Calling専用エンドポイント（認証不要）。

    設計方針（重要・必ず守ること）:
    - shop_idはcheck_availabilityと同じくURLパスの値のみを使い、リクエストボディには
      含めない。call_idもAIの引数ではなく、ブラウザがOpenAI Realtimeのfunction_call
      イベント(response.output_item.doneのitem.call_id)から直接読み取った値をそのまま
      転送したものである（AI自身にidempotency_keyやcall_idを生成させない）。
      ここで "realtime_voice:{shop_id}:{call_id}" にnamespace化してから、既存の
      create_reservation()へReservationCreateRequest.idempotency_keyとして渡す。
      同一キーによる同時多重INSERTは、最終的にDBの一意インデックス
      (ux_reservations_idempotency_key)がすべて拒否・吸収する
      （create_reservation()側の実装を参照。ここではSELECTベースの事前チェックに
      一切頼らない設計になっている）。
    - 実際の予約作成ロジック（reservations_enabled判定・営業時間/定休日/満席判定・
      テーブル/スタッフ割当・DB書き込み）は一切ここで再実装せず、
      POST /api/v1/reservations/create と全く同じcreate_reservation()をそのまま呼び出す。
      Phase3Aのcheck_availabilityの結果をキャッシュして使い回すことはせず、
      create_reservation()が呼び出された時点で必ずゼロから空き状況を再判定する
      （create_reservation()自体の既存動作そのままであり、ここで特別な対応は不要）。
    - create_reservation()が送出するHTTPExceptionには、Phase3Bで追加した
      .reason_code属性（文字列の部分一致に頼らない、機械可読な失敗理由）が
      付与されている。付与されていない（想定外の）場合は必ず安全側の
      temporarily_unavailableにフォールバックし、絶対にsuccess=Trueを返さない。
    - guest_email・coupon_codeはPhase3Bのスコープ外のため、常にNone/未指定として
      既存ReservationCreateRequestを組み立てる。
    """
    _check_booking_tool_rate_limit(shop_id)

    def _safe_failure(reason_code: str) -> CreateReservationToolResponse:
        return CreateReservationToolResponse(success=False, reason_code=reason_code)

    try:
        shop = await db.get(Shop, shop_id)
        if not shop or not shop.is_active:
            # shop_idはAIの引数ではなくURLパス由来のため、これが起きるのは
            # 店舗の非公開化等こちら側の事情であり、AIやお客様の入力ミスではない。
            return _safe_failure("temporarily_unavailable")

        try:
            target_date = date_type.fromisoformat(request.date)
            target_time = datetime.strptime(request.time, "%H:%M").time()
        except ValueError:
            return _safe_failure("invalid_request")

        reservation_dt = datetime.combine(target_date, target_time)
        idempotency_key = f"realtime_voice:{shop_id}:{request.call_id}"

        create_request = ReservationCreateRequest(
            shop_id=shop_id,
            reservation_date=reservation_dt,
            number_of_people=request.party_size,
            service_id=request.service_id,
            staff_id=request.staff_id,
            guest_name=request.guest_name,
            guest_phone=request.guest_phone,
            guest_email=None,
            special_requests=request.special_requests,
            idempotency_key=idempotency_key,
        )

        try:
            booking_response = await create_reservation(create_request, db, None)
        except HTTPException as e:
            reason_code = getattr(e, "reason_code", None) or "temporarily_unavailable"
            logger.info(
                "create_reservation Tool: 予約不可 (shop_id=%s, reason_code=%s, detail=%s)",
                shop_id, reason_code, e.detail,
            )
            return _safe_failure(reason_code)

        reservation = booking_response.reservation
        return CreateReservationToolResponse(
            success=True,
            reservation_id=booking_response.reservation_id,
            date=reservation.reservation_date.strftime("%Y-%m-%d"),
            time=reservation.reservation_date.strftime("%H:%M"),
            party_size=reservation.number_of_people,
            guest_name=reservation.guest_name,
        )
    except HTTPException:
        raise
    except Exception as e:
        # 例外の詳細をAIやレスポンスに漏らさず、必ず安全側(success=False)で返す。
        logger.error("create_reservation Tool処理に失敗 (shop_id=%s): %s", shop_id, e)
        return _safe_failure("temporarily_unavailable")
