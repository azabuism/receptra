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
import re
import time
import unicodedata
from datetime import datetime, date as date_type, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db
from app.models.shop import Shop
from app.models.reservation import Reservation, ReservationStatus
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


# ===== Phase3B.1: Layer2 - 別call_idによる実質的な重複予約の短時間検知 =====
#
# 設計方針（重要・必ず守ること。ユーザー承認済みの方針）:
# - Layer1（idempotency_key: "realtime_voice:{shop_id}:{call_id}" によるDB一意
#   インデックス ux_reservations_idempotency_key）は一切変更しない。「同一call_id
#   の再送」はLayer1がDBレベルで完全に保証し続ける。
# - Layer2は「異なるcall_idだが実質的に同じ予約意図」を対象とする別問題であり、
#   DBの一意制約ではなく、アプリケーションレベルの短時間（3分以内）重複検知に
#   よって“大幅に軽減”するものである。理論上、2つのリクエストがこの検索より
#   先に両方とも「候補ゼロ」を通過すれば2件成立し得るが、それはLayer2が
#   保証する範囲外として明示的に許容する（ユーザー承認済み）。
# - 予約内容（日時・電話番号・氏名等の組み合わせ）そのものへの永続的なUNIQUE制約は
#   絶対に追加しない。同じお客様が後日、意図的に全く同じ条件で予約する可能性が
#   あるため。
# - この仕組みはRealtime Voice専用の create_reservation_tool エンドポイント内
#   にのみ実装し、共通の create_reservation() 本体には一切組み込まない。
#   これにより、Web予約・shop_booking_ai（チャット予約）・管理画面予約の挙動は
#   完全に変更されない。
_DUPLICATE_DETECTION_WINDOW_SECONDS = 180  # 3分（ユーザー承認済みの初期値）

# Layer2の重複候補に含める予約ステータス。キャンセル済み・ノーショー・完了済みは
# 「現在有効な予約」ではないため、重複判定の対象から除外する。
_ACTIVE_STATUSES_FOR_DEDUP = (ReservationStatus.PENDING.value, ReservationStatus.CONFIRMED.value)


def _normalize_phone_for_dedup(phone: Optional[str]) -> str:
    """
    Layer2の重複比較専用の電話番号正規化。DBへ保存するguest_phoneの値そのものは
    一切変更しない（この関数の戻り値は比較にのみ使う）。

    許可される処理のみ行う: 全角英数字・記号の半角化(Unicode NFKC正規化)、
    ハイフン・空白（半角/全角）・括弧の除去。
    禁止されている処理は一切行わない: 欠落桁の補完、国番号の推測、先頭0の
    自動追加、AIによる修正の反映。
    """
    if not phone:
        return ""
    normalized = unicodedata.normalize("NFKC", phone)
    for ch in ("-", " ", "　", "(", ")"):
        normalized = normalized.replace(ch, "")
    return normalized


def _normalize_name_for_dedup(name: Optional[str]) -> str:
    """
    Layer2の重複比較専用の氏名正規化。DBへ保存するguest_nameの値そのものは
    一切変更しない。前後の空白除去、および連続する空白（全角スペース・タブ・
    改行含む）の1つへの整理のみを行う。漢字⇔カナ変換・読み仮名推測等の
    推測的な正規化は一切行わない。
    """
    if not name:
        return ""
    collapsed = re.sub(r"[ 　\t\r\n]+", " ", name)
    return collapsed.strip()


async def _find_recent_duplicate_reservation(
    db: AsyncSession,
    shop_id: str,
    reservation_dt: datetime,
    party_size: int,
    guest_phone: str,
    guest_name: str,
    service_id: Optional[str],
    staff_id: Optional[str],
) -> Optional[Reservation]:
    """
    直近_DUPLICATE_DETECTION_WINDOW_SECONDS秒以内に、同じ店舗のRealtime Voice経由
    （idempotency_keyが "realtime_voice:{shop_id}:" で始まる）で作成された、
    現在有効なステータスの予約の中から、内容が実質的に一致するものを探す。

    絞り込みはDB問い合わせ（shop_id・idempotency_keyのnamespaceプレフィックス・
    created_at・status）だけで行い、値そのものの一致判定（電話番号・氏名の
    正規化比較を含む）は必ずPython側で行う。既存DBのguest_phoneは正規化されずに
    保存されているため、SQL側で正規化した値との完全一致検索は行わない
    （新しい入力側だけをnormalizeしてSQL検索すると、表記ゆれのある既存データを
    取りこぼすため）。
    """
    now = datetime.utcnow()
    window_start = now - timedelta(seconds=_DUPLICATE_DETECTION_WINDOW_SECONDS)
    prefix = f"realtime_voice:{shop_id}:"

    result = await db.execute(
        select(Reservation).filter(
            Reservation.shop_id == shop_id,
            Reservation.idempotency_key.isnot(None),
            Reservation.idempotency_key.like(f"{prefix}%"),
            Reservation.created_at >= window_start,
            Reservation.status.in_(_ACTIVE_STATUSES_FOR_DEDUP),
        )
    )
    candidates = result.scalars().all()
    if not candidates:
        return None

    target_phone = _normalize_phone_for_dedup(guest_phone)
    target_name = _normalize_name_for_dedup(guest_name)

    for candidate in candidates:
        if candidate.reservation_date != reservation_dt:
            continue
        if candidate.number_of_people != party_size:
            continue
        if _normalize_phone_for_dedup(candidate.guest_phone) != target_phone:
            continue
        if _normalize_name_for_dedup(candidate.guest_name) != target_name:
            continue
        # service_id/staff_id: 双方Noneなら一致、片方だけ値ありなら不一致
        # （continueでスキップされる）、両方値ありならID完全一致を要求する。
        # 単純な != 比較でこの3パターンを正しく判定できる。
        if candidate.service_id != service_id:
            continue
        if candidate.staff_id != staff_id:
            continue
        return candidate

    return None


# Phase3C.1: Zero-Wait Greeting用の第一声テキスト取得エンドポイント専用のバケット。
# ページ読み込み時に1回呼ばれる想定の軽量な読み取り専用リクエストのため、
# /sessionより緩めに設定する。
_GREETING_TEXT_RATE_LIMIT_WINDOW_SECONDS = 60
_GREETING_TEXT_RATE_LIMIT_MAX_REQUESTS = 20
_recent_greeting_text_requests: dict[str, list] = {}


def _check_greeting_text_rate_limit(shop_id: str) -> None:
    now = time.monotonic()
    bucket = _recent_greeting_text_requests.setdefault(shop_id, [])
    bucket[:] = [t for t in bucket if now - t < _GREETING_TEXT_RATE_LIMIT_WINDOW_SECONDS]
    if len(bucket) >= _GREETING_TEXT_RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail="リクエストが多すぎます。しばらくしてから再度お試しください。",
        )
    bucket.append(now)


@router.get("/greeting-text")
async def get_realtime_voice_greeting_text(shop_id: str, db: AsyncSession = Depends(get_db)):
    """
    Phase3C.1: Zero-Wait Greeting用。この店舗のRealtime instructions／事前生成
    音声が前提としている第一声の文字列を返す（会員登録不要・認証不要）。

    重要: この文字列はお客様に電話で話しかける内容そのものであり秘匿情報では
    ないため、/session と同様に認証不要で公開する。用途はZero-Wait Greeting
    成功時に、Realtimeの会話履歴へ「この文言は既に話した」と伝える
    (conversation.item.create、response.createは送らない＝再度喋らせない)ため
    のみ。事前生成音声ファイル自体は別途静的ファイルとして配置されるため、
    このエンドポイントは文字列のみを返し、音声データは一切扱わない。
    """
    _check_greeting_text_rate_limit(shop_id)

    shop = await db.get(Shop, shop_id)
    if not shop or not shop.is_active:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")

    try:
        greeting_text = await realtime_voice_ai.get_effective_greeting_text(db, shop)
    except Exception as e:
        logger.error("greeting-text取得に失敗 (shop_id=%s): %s", shop_id, e)
        raise HTTPException(
            status_code=502,
            detail="第一声情報の取得に失敗しました。しばらくしてから再度お試しください。",
        )

    return {"shop_id": shop_id, "greeting_text": greeting_text}


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
    - Phase3B.1: Layer1（上記のidempotency_keyによるDB一意制約）に加えて、
      create_reservation()を呼ぶ前に_find_recent_duplicate_reservation()による
      Layer2（別call_idだが3分以内・実質的に同一内容の重複検知）を行う。
      Layer2はこのエンドポイント内にのみ実装しており、共通create_reservation()
      本体・Web予約・shop_booking_ai・管理画面予約には一切影響しない。
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

        # Phase3B.1 Layer2: 別call_id（＝Layer1のidempotency_keyでは検知できない）
        # だが実質的に同一の予約意図によるリクエストを、DBへ新規INSERTする前に
        # 検知する。一致した場合は新規予約を作らず、既存予約をこのリクエストの
        # 結果としてそのまま返す（お客様から見れば、ネットワーク再試行等があっても
        # 予約は正常に1件成立したように見える）。
        duplicate = await _find_recent_duplicate_reservation(
            db,
            shop_id,
            reservation_dt,
            request.party_size,
            request.guest_phone,
            request.guest_name,
            request.service_id,
            request.staff_id,
        )
        if duplicate is not None:
            logger.info(
                "Layer2: 別call_idによる短時間重複を検知し、既存予約(id=%s)を返します "
                "(shop_id=%s, new_call_id=%s)",
                duplicate.id, shop_id, request.call_id,
            )
            return CreateReservationToolResponse(
                success=True,
                reservation_id=duplicate.id,
                date=duplicate.reservation_date.strftime("%Y-%m-%d"),
                time=duplicate.reservation_date.strftime("%H:%M"),
                party_size=duplicate.number_of_people,
                guest_name=duplicate.guest_name,
            )

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
