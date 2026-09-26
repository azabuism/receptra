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
import secrets
import time
import unicodedata
from datetime import datetime, date as date_type, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.deps import get_db
from app.models.ai_staff_settings import AIStaffSettings
from app.models.shop import Shop
from app.models.staff import Staff
from app.models.reservation import Reservation, ReservationStatus
from app.models.callback_request import CallbackRequest, CallbackRequestStatus, CallbackRequestReasonCode
from app.services import realtime_voice_ai
from app.schemas.reservation import (
    CheckAvailabilityRequest, CheckAvailabilityResponse,
    CreateReservationToolRequest, CreateReservationToolResponse,
    FindCustomerToolRequest, FindCustomerToolResponse,
    ConfirmCustomerIdentityToolRequest, ConfirmCustomerIdentityToolResponse,
    GetCustomerContextToolRequest, GetCustomerContextToolResponse,
    SetConversationLanguageToolRequest, SetConversationLanguageToolResponse,
    RequestCallbackToolRequest, RequestCallbackToolResponse,
    ReservationCreateRequest,
)
from app.schemas.shop_knowledge import GetShopInfoToolRequest
from app.routers.reservations import check_single_slot_availability, create_reservation
from app.routers.shop_knowledge import get_shop_info_for_ai
from app.services.customer_memory import (
    find_customer_candidate_record, upsert_customer_memory_for_reservation,
)
from app.services import customer_context
from app.services import conversation_language as conversation_language_state
from app.services.outbound_dispatch import enqueue_callback_requested_call
from app.services.owner_notifications import notify_callback_requested

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


# Phase3D: get_shop_info Tool Calling用の別バケット。DB書き込みを伴わない
# 読み取り専用の問い合わせであり、1通話中に複数トピックを何度も尋ねられる
# 可能性があるため、check_availability用と同程度の緩めの制限にする
# （create_reservation用の厳しめのバケットとは完全に独立させる）。
_SHOP_INFO_TOOL_RATE_LIMIT_WINDOW_SECONDS = 60
_SHOP_INFO_TOOL_RATE_LIMIT_MAX_REQUESTS = 30
_recent_shop_info_tool_requests: dict[str, list] = {}


def _check_shop_info_rate_limit(shop_id: str) -> None:
    now = time.monotonic()
    bucket = _recent_shop_info_tool_requests.setdefault(shop_id, [])
    bucket[:] = [t for t in bucket if now - t < _SHOP_INFO_TOOL_RATE_LIMIT_WINDOW_SECONDS]
    if len(bucket) >= _SHOP_INFO_TOOL_RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail="リクエストが多すぎます。しばらくしてから再度お試しください。",
        )
    bucket.append(now)


# Outbound AI Phase 4A: find_customer Tool Calling用の別バケット。DB書き込みを
# 伴わない読み取り専用の問い合わせであり、get_shop_infoと同程度の緩めの制限にする
# （他のToolのクォータとは完全に独立させる）。
_CUSTOMER_LOOKUP_RATE_LIMIT_WINDOW_SECONDS = 60
_CUSTOMER_LOOKUP_RATE_LIMIT_MAX_REQUESTS = 30
_recent_customer_lookup_requests: dict[str, list] = {}


def _check_customer_lookup_rate_limit(shop_id: str) -> None:
    now = time.monotonic()
    bucket = _recent_customer_lookup_requests.setdefault(shop_id, [])
    bucket[:] = [t for t in bucket if now - t < _CUSTOMER_LOOKUP_RATE_LIMIT_WINDOW_SECONDS]
    if len(bucket) >= _CUSTOMER_LOOKUP_RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail="リクエストが多すぎます。しばらくしてから再度お試しください。",
        )
    bucket.append(now)


# Outbound AI Phase 4B: confirm_customer_identity Tool Calling用の別バケット。
# DB書き込みを伴わない（in-memory状態の更新のみの）操作であり、find_customerと
# 同程度だが、1通話中に何度も呼ばれる想定ではないためやや控えめに設定する
# （他のToolのクォータとは完全に独立させる）。
_CONFIRM_IDENTITY_RATE_LIMIT_WINDOW_SECONDS = 60
_CONFIRM_IDENTITY_RATE_LIMIT_MAX_REQUESTS = 20
_recent_confirm_identity_requests: dict[str, list] = {}


def _check_confirm_identity_rate_limit(shop_id: str) -> None:
    now = time.monotonic()
    bucket = _recent_confirm_identity_requests.setdefault(shop_id, [])
    bucket[:] = [t for t in bucket if now - t < _CONFIRM_IDENTITY_RATE_LIMIT_WINDOW_SECONDS]
    if len(bucket) >= _CONFIRM_IDENTITY_RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail="リクエストが多すぎます。しばらくしてから再度お試しください。",
        )
    bucket.append(now)


# Outbound AI Phase 4B: get_customer_context Tool Calling用の別バケット。
# find_customerと同程度の読み取り専用の緩めの制限にする
# （他のToolのクォータとは完全に独立させる）。
_CUSTOMER_CONTEXT_RATE_LIMIT_WINDOW_SECONDS = 60
_CUSTOMER_CONTEXT_RATE_LIMIT_MAX_REQUESTS = 30
_recent_customer_context_requests: dict[str, list] = {}


def _check_customer_context_rate_limit(shop_id: str) -> None:
    now = time.monotonic()
    bucket = _recent_customer_context_requests.setdefault(shop_id, [])
    bucket[:] = [t for t in bucket if now - t < _CUSTOMER_CONTEXT_RATE_LIMIT_WINDOW_SECONDS]
    if len(bucket) >= _CUSTOMER_CONTEXT_RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail="リクエストが多すぎます。しばらくしてから再度お試しください。",
        )
    bucket.append(now)


# Phase 5A: set_conversation_language Tool Calling用の別バケット。
# DB書き込みを伴わない（in-memory状態の更新のみの）操作だが、会話の途中で
# 何度も言語が行き来する可能性を考慮し、confirm_customer_identityよりは
# やや緩めに設定する（他のToolのクォータとは完全に独立させる）。
_SET_CONVERSATION_LANGUAGE_RATE_LIMIT_WINDOW_SECONDS = 60
_SET_CONVERSATION_LANGUAGE_RATE_LIMIT_MAX_REQUESTS = 30
_recent_set_conversation_language_requests: dict[str, list] = {}


def _check_set_conversation_language_rate_limit(shop_id: str) -> None:
    now = time.monotonic()
    bucket = _recent_set_conversation_language_requests.setdefault(shop_id, [])
    bucket[:] = [
        t for t in bucket if now - t < _SET_CONVERSATION_LANGUAGE_RATE_LIMIT_WINDOW_SECONDS
    ]
    if len(bucket) >= _SET_CONVERSATION_LANGUAGE_RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail="リクエストが多すぎます。しばらくしてから再度お試しください。",
        )
    bucket.append(now)


# Human Handoff基盤: request_callback Tool Calling用の別バケット。DB書き込みを
# 伴い、かつ成立すると担当者への電話/Email通知(Outbound)につながるため、
# 「無駄な通知を大量発生させない」という設計方針に沿って、create_reservationと
# 同程度の厳しめの制限にする（他のToolのクォータとは完全に独立させる。
# AI側の会話ルールで乱用を防ぐのが一次防御、これは技術的な最終防衛線）。
_CALLBACK_TOOL_RATE_LIMIT_WINDOW_SECONDS = 60
_CALLBACK_TOOL_RATE_LIMIT_MAX_REQUESTS = 10
_recent_callback_tool_requests: dict[str, list] = {}


def _check_callback_tool_rate_limit(shop_id: str) -> None:
    now = time.monotonic()
    bucket = _recent_callback_tool_requests.setdefault(shop_id, [])
    bucket[:] = [t for t in bucket if now - t < _CALLBACK_TOOL_RATE_LIMIT_WINDOW_SECONDS]
    if len(bucket) >= _CALLBACK_TOOL_RATE_LIMIT_MAX_REQUESTS:
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

    Phase3G追記: このページ（shop-ai-realtime-voice.html）は、以前は特定の
    shop_idをフロントエンドにハードコードしたホワイトリスト
    （ZERO_WAIT_GREETING_SHOP_IDS）でZero-Wait Greetingの対象店舗を判定して
    いた。Phase3Fで固定E2Eテスト店舗を削除した際にこの配列を空にしたため、
    Zero-Wait Greeting自体が全店舗で無効化されたままになっていた。
    本エンドポイントのレスポンスに zero_wait_eligible を追加し、
    フロントエンドはこの値を見て「このページを読み込むたびに」対象可否を
    判定する（ハードコードされたshop_id一覧に依存しない）。

    zero_wait_eligibleの判定基準（新しいBooleanカラムを増やさず、既存の状態
    から判断する）: この店舗にAIStaffSettings行が存在するかどうか。
    - 存在する店舗 = オーナーが一度でもAIスタッフ設定（声・名前等）を保存
      済み = get_or_generate_greeting_audio()がDBキャッシュを持てる
      （初回のみTTS生成、以後はキャッシュ読み出しのみで高速・低コスト）。
    - 存在しない店舗 = 一度もAIスタッフ設定を保存していない = キャッシュを
      保持する行が無いため、Zero-Wait用の音声プリロードのたびに毎回OpenAI
      TTSを呼ぶことになってしまう（get_or_generate_greeting_audioの
      docstring参照）。この場合はZero-Wait非対象とし、既存のPhase3C
      （Realtime自身の第一声）にのみ委ねる。AI電話受付自体は
      AIStaffSettingsの有無に関わらず全店舗で利用可能なままで、
      Zero-Wait（体感速度の最適化）のみが対象を限定される。
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

    has_ai_staff_settings = (
        await db.execute(
            select(AIStaffSettings.id).filter(AIStaffSettings.shop_id == shop_id).limit(1)
        )
    ).scalar_one_or_none() is not None

    return {
        "shop_id": shop_id,
        "greeting_text": greeting_text,
        "zero_wait_eligible": has_ai_staff_settings,
    }


# Phase3E-3: Zero-Wait Greeting音声用の別バケット。ページ読み込み時に1回
# フェッチされる想定の読み取り中心のリクエストのため、/greeting-textと同程度の
# 緩めの制限にする（キャッシュミス時のみ内部でOpenAI TTSを呼ぶが、頻度は
# 設定変更直後のみで、通常のリクエストの大半はDBキャッシュを読むだけ）。
_GREETING_AUDIO_RATE_LIMIT_WINDOW_SECONDS = 60
_GREETING_AUDIO_RATE_LIMIT_MAX_REQUESTS = 20
_recent_greeting_audio_requests: dict[str, list] = {}


def _check_greeting_audio_rate_limit(shop_id: str) -> None:
    now = time.monotonic()
    bucket = _recent_greeting_audio_requests.setdefault(shop_id, [])
    bucket[:] = [t for t in bucket if now - t < _GREETING_AUDIO_RATE_LIMIT_WINDOW_SECONDS]
    if len(bucket) >= _GREETING_AUDIO_RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail="リクエストが多すぎます。しばらくしてから再度お試しください。",
        )
    bucket.append(now)


@router.get("/greeting-audio")
async def get_realtime_voice_greeting_audio(shop_id: str, db: AsyncSession = Depends(get_db)):
    """
    Phase3E-3: Zero-Wait Greeting用の事前生成音声(mp3)を返す（会員登録不要・
    認証不要。/greeting-textと同じ公開範囲の考え方）。

    設計方針（重要）:
    - Phase3C.1のPoC（frontend/public/greeting_audio/{shop_id}.mp3を開発者が
      手動配置する運用）を置き換える。音声の生成・キャッシュ・
      staff_name/voice/greeting変更時の自動再生成は、すべて
      app.services.realtime_voice_ai.get_or_generate_greeting_audio()に
      一元化されている（このエンドポイントは薄いラッパーに過ぎない）。
    - このエンドポイントが失敗する場合（店舗未検出・非公開・生成エラー等）は
      必ず404/502を返す。フロントエンド(shop-ai-realtime-voice.html)の
      既存のZero-Wait失敗時フォールバック（Phase3Cの第一声にフォールバック）は
      「音声URLのfetchが失敗する」ことだけを前提に作られており、静的ファイルが
      404になる場合と全く同じ扱いになるため、フロント側の変更は不要。
    - Cache-Control: no-store を明示する。このエンドポイントのURLはshop_id
      固定で変わらないため、ブラウザやその手前の中間キャッシュに古い音声を
      キャッシュされてしまうと、DB側でキャッシュを更新してもお客様には
      古い音声が届き続けてしまう。キャッシュの正しさは常にこのエンドポイント
      内部のフィンガープリント比較だけに依存させる。
    """
    _check_greeting_audio_rate_limit(shop_id)

    shop = await db.get(Shop, shop_id)
    if not shop or not shop.is_active:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")

    try:
        result = await realtime_voice_ai.get_or_generate_greeting_audio(db, shop)
    except RuntimeError as e:
        logger.error("greeting-audio生成に失敗（設定エラー） shop_id=%s: %s", shop_id, e)
        raise HTTPException(status_code=502, detail="音声の準備に失敗しました。しばらくしてから再度お試しください。")
    except Exception as e:
        logger.error("greeting-audio取得に失敗 shop_id=%s: %s", shop_id, e)
        raise HTTPException(status_code=502, detail="音声の準備に失敗しました。しばらくしてから再度お試しください。")

    return Response(
        content=result["audio_bytes"],
        media_type=result.get("content_type") or "audio/mpeg",
        headers={"Cache-Control": "no-store", "X-Greeting-Audio-Cache": result.get("cache", "unknown")},
    )


# PHASE O5.5 Speak-Then-Work: ack-fallback-audio用の別バケット。ページ読み込み時に
# 1回だけプリロードされる想定の読み取り中心のリクエストのため、/greeting-audioと
# 同程度の緩めの制限にする。
_ACK_FALLBACK_AUDIO_RATE_LIMIT_WINDOW_SECONDS = 60
_ACK_FALLBACK_AUDIO_RATE_LIMIT_MAX_REQUESTS = 20
_recent_ack_fallback_audio_requests: dict[str, list] = {}


def _check_ack_fallback_audio_rate_limit(shop_id: str) -> None:
    now = time.monotonic()
    bucket = _recent_ack_fallback_audio_requests.setdefault(shop_id, [])
    bucket[:] = [t for t in bucket if now - t < _ACK_FALLBACK_AUDIO_RATE_LIMIT_WINDOW_SECONDS]
    if len(bucket) >= _ACK_FALLBACK_AUDIO_RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail="リクエストが多すぎます。しばらくしてから再度お試しください。",
        )
    bucket.append(now)


@router.get("/ack-fallback-audio")
async def get_realtime_voice_ack_fallback_audio(shop_id: str, db: AsyncSession = Depends(get_db)):
    """
    PHASE O5.5 Speak-Then-Work: Tool呼び出し確定時にAI自身がまだ何も発話して
    いなかった場合にのみ再生する、固定文言の安全網音声(mp3)を返す
    （会員登録不要・認証不要。/greeting-audioと同じ公開範囲の考え方）。

    重要（Zero-Wait Greetingとの違い・必ず守ること）:
    - これはZero-Wait Greetingとは完全に独立した別機能であり、
      AIStaffSettings.greeting_audio_* カラム・
      app.services.realtime_voice_ai.get_or_generate_greeting_audio()には
      一切触れない（Greeting関連コード変更禁止の指示を守るため）。
    - 文言は固定（app.services.realtime_voice_ai._ACK_FALLBACK_TEXT）。
      店舗・会話内容によって変わらないため、キャッシュはDBではなくvoice単位の
      プロセス内メモリのみで十分（get_or_generate_ack_fallback_audio()参照）。
      新規のDBカラム・マイグレーションは一切追加していない。
    - voiceは通話のRealtime voice設定と同じもの（AIStaffSettings.voice、
      未設定ならOPENAI_REALTIME_VOICE）を使う。
    - Cache-Control: no-store（/greeting-audioと同じ理由。ただし本エンドポイントの
      内容自体はvoiceが変わらない限り不変なため実害は小さいが、念のため既存の
      音声配信エンドポイントと方針を統一する）。
    """
    _check_ack_fallback_audio_rate_limit(shop_id)

    shop = await db.get(Shop, shop_id)
    if not shop or not shop.is_active:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")

    settings = get_settings()
    result = await db.execute(
        select(AIStaffSettings.voice).filter(AIStaffSettings.shop_id == shop_id)
    )
    staff_voice = result.scalar_one_or_none()
    voice = staff_voice or settings.OPENAI_REALTIME_VOICE

    try:
        audio_bytes = await realtime_voice_ai.get_or_generate_ack_fallback_audio(voice)
    except RuntimeError as e:
        logger.error("ack-fallback-audio生成に失敗（設定エラー） shop_id=%s: %s", shop_id, e)
        raise HTTPException(status_code=502, detail="音声の準備に失敗しました。しばらくしてから再度お試しください。")
    except Exception as e:
        logger.error("ack-fallback-audio取得に失敗 shop_id=%s: %s", shop_id, e)
        raise HTTPException(status_code=502, detail="音声の準備に失敗しました。しばらくしてから再度お試しください。")

    return Response(
        content=audio_bytes,
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )


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

    # Human Handoff基盤: AI電話受付がOFFの店舗では、Realtimeセッション自体を
    # 一切発行しない（OpenAI Realtime APIへのclient_secrets.create()呼び出しに
    # 一切到達しないため、OpenAI側のコストが完全にゼロになる）。
    # 谷村様の明示的な指示: 「AIが一度応答してからOFFである旨を説明して切る」
    # という設計は禁止されており、そもそもセッションを開始しないこと。
    # shop.ai_phone_reception_enabledのデフォルトはTrue（既存店舗の動作を
    # 変更しない）。
    if not shop.ai_phone_reception_enabled:
        logger.info("AI電話受付がOFFのためRealtimeセッションを発行しません shop_id=%s", shop_id)
        return JSONResponse(
            status_code=403,
            content={
                "detail": "現在、こちらの音声AI受付はご利用いただけません。",
                "reason_code": "ai_phone_reception_disabled",
            },
        )

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

    # Outbound AI Phase 4B: この通話全体を識別するための、RECEPTRA独自の
    # opaqueなsession識別子を発行する。OpenAI Realtime側のclient_secret/
    # セッション概念とは完全に別物で、Customer Memory Phase4Bの本人確認状態
    # （find_customerのpending candidate・confirm_customer_identityの
    # verified状態）を「この通話」に安全に紐付けるためだけに使う
    # （app.services.customer_context参照）。高エントロピー
    # (secrets.token_urlsafe)のため、推測によるなりすましは現実的に不可能。
    # DBへの保存やこの関数内での事前登録は不要（各Toolエンドポイントが
    # 受け取った値をそのままdictのキーとして使うのみ）。
    voice_session_id = secrets.token_urlsafe(24)

    # Phase 5B: set_conversation_language の書き込み側isolationを強化するため、
    # この時点でvoice_session_idがどの店舗のものかを先に記録しておく
    # （app.services.conversation_language.register_voice_session参照）。
    conversation_language_state.register_voice_session(shop_id, voice_session_id)

    return {
        "shop_id": shop_id,
        "shop_name": shop.name,
        "voice_session_id": voice_session_id,
        **session_info,
    }


_STAFF_NAME_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_staff_name_token(s: str) -> str:
    """
    Phase R5 Part B: Named Staff Safe Resolution専用の氏名比較用正規化。

    重要（fuzzy matchingではない・必ず守ること）: ここで行うのはUnicode NFKC
    正規化（全角/半角の統一。例:全角英数字・半角カタカナ→標準形）と
    空白の完全除去・大文字小文字の統一のみであり、類似度に基づく曖昧一致
    （編集距離・音の近さ等）は一切行わない。「山田」と「山本」のような
    見た目が近いだけの異なる文字列は、この正規化を経てもなお一致しない。
    Section49「fuzzy matchingは最終確定に使わない」を、そもそも比較ロジック
    自体にfuzzy要素を含めないことで満たす、決定的（deterministic）な変換。
    """
    s = unicodedata.normalize("NFKC", s)
    s = _STAFF_NAME_WHITESPACE_RE.sub("", s)
    return s.casefold()


async def _resolve_staff_by_name(
    db: AsyncSession, shop_id: str, staff_name: Optional[str],
) -> tuple[str, Optional[str], Optional[List[str]]]:
    """
    Phase R5 Part B: Named Staff Safe Resolution。

    お客様が実際に発話したスタッフの氏名（自然文。内部IDではない）を、
    この店舗に安全に一意特定できるStaff.idへ解決する、唯一のsource of
    truth。Realtime AIはこの関数の結果を経由してのみstaff_idを得る
    （AI自身がstaff_idを推測・生成することは一切ない。Section44
    「Critical Staff-ID Rule」）。

    対象はこの店舗の is_active=="active" かつ nomination_allowed=True の
    Staffのみ（Staff.nomination_allowedはPhase3E-1で追加された既存の
    フィールドで、その説明コメント通り「このスタッフを名指しでの指名予約
    対象にしない」という店舗側の設定意図をそのまま尊重する。従来この
    フィールドはAvailability判定・Realtime AIロジックのどこからも
    参照されていなかったが、Named Staff Safe Resolutionはまさにこの
    フィールドの意図と一致するため、ここで初めて安全に活用する）。
    inactiveなStaff・nomination_allowed=FalseのStaffは候補に一切現れず、
    結果的に常にNOT_FOUND側の扱いになる。これは「在籍していない」と
    断定するわけではなく、単に「安全に識別できない」という意味であり、
    呼び出し側（Tool descriptionの指示）は必ずHuman Handoffへ進む
    （Section42「DBに見つからない≠現実にいない」を守るため、この関数
    自体は絶対値の判定を一切行わない）。

    戻り値: (status, staff_id, candidate_names)
    - "NOT_PROVIDED": staff_nameが指定されていない（空文字含む）。
      呼び出し側は名前による制約が一切無いものとして通常どおり続行する。
    - "RESOLVED": 安全に一意特定できた。第2戻り値がstaff_id。
    - "NOT_FOUND": 安全に識別できるStaffが1人もいない（未登録・inactive・
      nomination_allowed=False・読み違い等、理由は問わず区別しない）。
    - "AMBIGUOUS": 姓の一致等により複数候補が残り、安全に一意へ絞り込めない。
      第3戻り値が候補の表示名リスト（DB由来のみ、ソート済み）。AIは
      これだけを使って1回だけ確認質問をしてよい（存在しない候補名を
      AI自身が創作することは絶対に許可しない。Section48）。

    一致判定は2段階（いずれも上記の決定的な正規化のみを使う）:
    1. フルネーム一致: 正規化した引数が、Staff.name または
       Staff.display_name の正規化形と完全に一致する。
    2. 姓のみ一致: フルネーム一致が0件の場合のみ、Staff.nameの先頭の
       空白区切りトークン（姓と想定）の正規化形と完全に一致する
       （「田中さんで」のように姓のみが発話された場合の安全な絞り込み。
       同姓が複数いればAMBIGUOUSになる、という仕様上の意図された挙動）。
    """
    if not staff_name or not staff_name.strip():
        return "NOT_PROVIDED", None, None

    query_norm = _normalize_staff_name_token(staff_name)
    if not query_norm:
        return "NOT_PROVIDED", None, None

    staff_result = await db.execute(
        select(Staff).filter(
            Staff.shop_id == shop_id,
            Staff.is_active == "active",
            Staff.nomination_allowed == True,  # noqa: E712
        )
    )
    all_staff = list(staff_result.scalars().all())
    if not all_staff:
        return "NOT_FOUND", None, None

    full_matches: dict = {}
    surname_matches: dict = {}

    for s in all_staff:
        display = s.display_name or s.name
        name_variants = {_normalize_staff_name_token(s.name)}
        if s.display_name:
            name_variants.add(_normalize_staff_name_token(s.display_name))
        if query_norm in name_variants:
            full_matches[s.id] = display
            continue
        raw_parts = unicodedata.normalize("NFKC", s.name).strip().split()
        if raw_parts:
            surname_norm = _normalize_staff_name_token(raw_parts[0])
            if surname_norm and surname_norm == query_norm:
                surname_matches[s.id] = display

    if len(full_matches) == 1:
        return "RESOLVED", next(iter(full_matches)), None
    if len(full_matches) > 1:
        return "AMBIGUOUS", None, sorted(set(full_matches.values()))

    if len(surname_matches) == 1:
        return "RESOLVED", next(iter(surname_matches)), None
    if len(surname_matches) > 1:
        return "AMBIGUOUS", None, sorted(set(surname_matches.values()))

    return "NOT_FOUND", None, None


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

        # Phase R5 Part B: Named Staff Safe Resolution。
        # request.staff_idはRealtime AIのTool定義から既に除外されているため
        # 通常は常にNoneだが、防御的にstaff_idが無い場合にのみstaff_name
        # 解決を行う（staff_idが指定されていればそちらを優先し、従来通り
        # 一切変更しない＝後方互換）。
        # 重要: staff_id/staff_nameは、この後のcheck_single_slot_availability()
        # 内部でもservice_idが解決できた場合（if service:分岐）のみ参照される
        # （Table/Resource経路ではstaff_idは一切見られない、既存の仕様）。
        # そのため、service_idが指定されていない（＝Table/Resource等、そもそも
        # スタッフ指名という概念が存在しない店舗・予約種別）の場合は、
        # staff_name解決自体を一切行わない。ここで無条件に解決してしまうと、
        # 「スタッフ概念が無い店舗で誤ってstaff_nameが渡った」場合に、
        # Resourceの空き状況とは無関係に予約全体を失敗させてしまう
        # （Section16「Table+Resource混在でも実際の割当のみを反映」と同じ
        # 考え方で、無関係な経路に新しい失敗要因を持ち込まない）。
        effective_staff_id = request.staff_id
        if request.service_id and not effective_staff_id and request.staff_name:
            staff_status, resolved_staff_id, staff_candidates = await _resolve_staff_by_name(
                db, shop_id, request.staff_name,
            )
            if staff_status == "RESOLVED":
                effective_staff_id = resolved_staff_id
            elif staff_status == "NOT_FOUND":
                return CheckAvailabilityResponse(
                    available=False, date=request.date, time=request.time,
                    party_size=request.party_size, reason_code="staff_not_identified",
                )
            elif staff_status == "AMBIGUOUS":
                return CheckAvailabilityResponse(
                    available=False, date=request.date, time=request.time,
                    party_size=request.party_size, reason_code="staff_name_ambiguous",
                    staff_name_candidates=staff_candidates,
                )
            # NOT_PROVIDED: 指名なしとして、従来通りそのまま続行する。

        available, reason_code, available_resource_types = await check_single_slot_availability(
            db,
            shop,
            target_date,
            target_time,
            request.party_size,
            request.service_id,
            effective_staff_id,
            request.resource_type,
        )
        return CheckAvailabilityResponse(
            available=available,
            date=request.date,
            time=request.time,
            party_size=request.party_size,
            reason_code=reason_code,
            available_resource_types=available_resource_types,
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

    def _safe_failure(
        reason_code: str, available_resource_types: Optional[List[str]] = None,
        staff_name_candidates: Optional[List[str]] = None,
    ) -> CreateReservationToolResponse:
        # Generic Resource Foundation Phase R4: reason_code=="resource_type_required"
        # の場合のみavailable_resource_typesが渡る。Phase R5 Part B:
        # reason_code=="staff_name_ambiguous"の場合のみstaff_name_candidatesが
        # 渡る。他のreason_codeではいずれも常にNoneのままであり、既存の呼び出し元
        # （他のreason_code）の挙動は一切変化しない。
        return CreateReservationToolResponse(
            success=False, reason_code=reason_code, available_resource_types=available_resource_types,
            staff_name_candidates=staff_name_candidates,
        )

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

        # Phase R5 Part B: Named Staff Safe Resolution。check_availability_tool
        # と全く同じ考え方。request.staff_idが指定されていればそちらを優先する
        # （後方互換）。ここで解決できない場合は、create_reservation()本体を
        # 一切呼び出さずに（＝R3/R4のアロケーションエンジンに一切触れずに）
        # 安全に失敗を返す。check_availability_toolと同じ理由で、
        # service_idが指定されていない場合（Table/Resource等）は解決自体を
        # 行わない。
        effective_staff_id = request.staff_id
        if request.service_id and not effective_staff_id and request.staff_name:
            staff_status, resolved_staff_id, staff_candidates = await _resolve_staff_by_name(
                db, shop_id, request.staff_name,
            )
            if staff_status == "RESOLVED":
                effective_staff_id = resolved_staff_id
            elif staff_status == "NOT_FOUND":
                return _safe_failure("staff_not_identified")
            elif staff_status == "AMBIGUOUS":
                return _safe_failure("staff_name_ambiguous", staff_name_candidates=staff_candidates)
            # NOT_PROVIDED: 指名なしとして、従来通りそのまま続行する。

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
            effective_staff_id,
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
            staff_id=effective_staff_id,
            guest_name=request.guest_name,
            guest_phone=request.guest_phone,
            guest_email=None,
            special_requests=request.special_requests,
            idempotency_key=idempotency_key,
            # Generic Resource Foundation Phase R4: check_availability Toolと
            # 完全に同じ意味・同じ許可値をそのまま転送する。
            resource_type=request.resource_type,
        )

        try:
            booking_response = await create_reservation(create_request, db, None)
        except HTTPException as e:
            reason_code = getattr(e, "reason_code", None) or "temporarily_unavailable"
            # Generic Resource Foundation Phase R4: reason_code=="resource_type_required"
            # の場合のみcreate_reservation()の_http_error()が付与する
            # .available_resource_types属性を読み取る（.reason_codeと同じ
            # getattrパターン。付与されていない場合は安全にNoneのまま）。
            available_resource_types = getattr(e, "available_resource_types", None)
            logger.info(
                "create_reservation Tool: 予約不可 (shop_id=%s, reason_code=%s, detail=%s)",
                shop_id, reason_code, e.detail,
            )
            return _safe_failure(reason_code, available_resource_types)

        reservation = booking_response.reservation

        # Outbound AI Phase 4A: 予約成立後にのみ、Customer Memoryへ安全に
        # upsertする（Web予約・チャット予約・管理画面予約には一切影響しない、
        # このRealtime Voice専用エンドポイント内だけの処理。Phase3B.1の
        # Layer2重複検知と同じ「専用エンドポイント内にのみ実装し、共通の
        # create_reservation()本体には一切組み込まない」という方針を踏襲）。
        # 冪等性・失敗分離はapp.services.customer_memory側で保証されており、
        # ここでの呼び出しが例外を投げることは無い（予約成立レスポンスに
        # 一切影響しない）。同じreservationに対する2回目以降の呼び出し
        # （Layer2重複検知で既存予約を返す場合を含む）は内部で自動的に
        # 無視される。
        # Phase 5A: この通話で実際に使われた言語（session state）を、次回接客時の
        # ソフトなヒントとしてCustomer Memoryに書き添える。
        # Phase 5B.1追記: session stateはPOST /session発行時点で"ja"に初期化される
        # ため（register_voice_session参照）、通話が最初から最後まで日本語のまま
        # 進んだ場合も、ここで正しく"ja"が渡り、前回訪問時の値（例:"en"）が
        # 古いまま残ることはない。session_idが無い・有効期限切れ等で本当に
        # 状態を取得できない場合のみNoneのまま渡し、_upsert_once側で
        # 「今回は不明」として何も上書きしない（既存の安全側フォールバックは維持）。
        conversation_language = conversation_language_state.get_session_language(
            shop_id, request.session_id
        ) if request.session_id else None

        await upsert_customer_memory_for_reservation(
            shop_id=shop_id,
            reservation_id=reservation.id,
            guest_name=reservation.guest_name,
            guest_phone=reservation.guest_phone,
            conversation_language=conversation_language,
        )

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


@router.post("/tools/find-customer", response_model=FindCustomerToolResponse)
async def find_customer_tool(
    shop_id: str,
    request: FindCustomerToolRequest,
    db: AsyncSession = Depends(get_db),
) -> FindCustomerToolResponse:
    """
    Outbound AI Phase 4A: find_customer Tool Calling専用エンドポイント（認証不要）。

    設計方針（重要・必ず守ること）:
    - shop_idはcheck_availability/create_reservation/get_shop_infoと同じく
      URLパスの値のみを使い、リクエストボディには含めない。Realtime AI（LLM側）に
      他店舗のshop_idを自由に指定させる余地を作らない。Customer Memoryのlookupは
      必ずこのshop_id内だけで完結させる（他店舗・他tenantのデータには一切触れない）。
    - Privacy Gate: 本人確認前にAIへ渡してよいのは「候補の表示名」だけ。来店回数・
      前回利用日・過去の予約内容・内部Customer MemoryのIDは一切返さない
      （FindCustomerToolResponseのdocstring参照。スキーマレベルでも
      これらのフィールド自体が存在しない設計にしている）。
    - 実際のlookupロジックは一切ここで再実装せず、必ず
      app.services.customer_memory.find_customer_candidate_record()をそのまま
      呼び出す（電話番号正規化・「電話番号=本人確定」としない設計・DB障害時の
      安全なフォールバックは全てそちら側の責務。返す内部id自体はAIへ渡さず、
      Phase4Bのpending candidate登録にのみ使う）。
    - 該当なし・DB障害等どの場合であってもnot_foundとして安全側に倒す
      （見つかったはずの候補を見失うことはあっても、存在しない候補を
      でっち上げることは絶対にしない）。

    Outbound AI Phase 4B追記:
    - request.session_id（フロントエンドが自動付与。AIの引数ではない）が
      指定されている場合、候補が見つかった時点でapp.services.customer_context.
      issue_candidate()を呼び、この通話に対するpending candidateを登録した上で
      candidate_referenceを発行する。session_id省略時（何らかの理由で
      フロントエンドが未対応・未設定の場合）は、Phase4Aと全く同じ挙動
      （候補の検索・表示名の返却のみ）にフォールバックし、pending candidateの
      登録自体を行わない（この場合、後続のconfirm_customer_identityは
      no_pending_candidateになるだけで、find_customer自体の安全性には
      影響しない）。
    - candidate_referenceはレスポンスのフィールドとしては存在するが、
      フロントエンド側がAIへ渡すfunction_call_outputからは必ず取り除く
      設計になっている（frontend/public/shop-ai-realtime-voice.html
      callFindCustomerTool参照）。このエンドポイント自体はその除去を
      行わない（HTTPレスポンスとしては値を返す）。
    """
    _check_customer_lookup_rate_limit(shop_id)

    try:
        shop = await db.get(Shop, shop_id)
        if not shop or not shop.is_active:
            return FindCustomerToolResponse(success=False, reason_code="temporarily_unavailable")

        record = await find_customer_candidate_record(shop_id, request.phone)
        if record is None:
            return FindCustomerToolResponse(success=True, status="not_found")

        candidate_reference: Optional[str] = None
        if request.session_id:
            candidate_reference = customer_context.issue_candidate(
                shop_id=shop_id,
                voice_session_id=request.session_id,
                customer_memory_id=record["id"],
                display_name=record["display_name"],
            )

        return FindCustomerToolResponse(
            success=True,
            status="candidate_found",
            candidate_display_name=record["display_name"],
            candidate_reference=candidate_reference,
        )
    except HTTPException:
        raise
    except Exception as e:
        # 例外の詳細をAIやレスポンスに漏らさず、必ず安全側(not_found相当)で返す。
        logger.error("find_customer Tool処理に失敗 (shop_id=%s): %s", shop_id, e)
        return FindCustomerToolResponse(success=False, reason_code="temporarily_unavailable")


@router.post("/tools/confirm-customer-identity", response_model=ConfirmCustomerIdentityToolResponse)
async def confirm_customer_identity_tool(
    shop_id: str,
    request: ConfirmCustomerIdentityToolRequest,
    db: AsyncSession = Depends(get_db),
) -> ConfirmCustomerIdentityToolResponse:
    """
    Outbound AI Phase 4B: confirm_customer_identity Tool Calling専用
    エンドポイント（認証不要）。

    設計方針（重要・必ず守ること。仕様書section5-8「AIだけを信用しない」）:
    - shop_idはcheck_availability等と同じくURLパスの値のみを使う。
    - session_id・candidate_referenceはAIの出力JSONには一切含まれず
      （ConfirmCustomerIdentityToolRequestのdocstring参照）、フロントエンド
      (shop-ai-realtime-voice.html)がfind_customer呼び出し時にサーバーから
      受け取った値を、AIに一切見せずに自動転送する。AIが渡せるのは
      confirmed(true/false)のみであり、「どのcandidateを確認するか」自体を
      AIが選ぶ余地は構造的に存在しない。
    - 実際の状態遷移ロジックは一切ここで再実装せず、必ず
      app.services.customer_context.confirm_candidate()をそのまま呼び出す
      （pending candidateの有無・shop一致・candidate_reference一致・
      有効期限の判定は全てそちら側の責務）。
    - session_id・candidate_referenceのいずれかが欠落している場合
      （フロントエンドの不具合等）は、常にno_pending_candidateとして
      安全側に倒す（verified状態を絶対に作らない）。
    """
    _check_confirm_identity_rate_limit(shop_id)

    try:
        shop = await db.get(Shop, shop_id)
        if not shop or not shop.is_active:
            return ConfirmCustomerIdentityToolResponse(success=False, status="temporarily_unavailable")

        if not request.session_id or not request.candidate_reference:
            return ConfirmCustomerIdentityToolResponse(success=True, status="no_pending_candidate")

        status = customer_context.confirm_candidate(
            shop_id=shop_id,
            voice_session_id=request.session_id,
            candidate_reference=request.candidate_reference,
            confirmed=request.confirmed,
        )
        return ConfirmCustomerIdentityToolResponse(success=True, status=status)
    except HTTPException:
        raise
    except Exception as e:
        # 例外の詳細をAIやレスポンスに漏らさず、必ず安全側(temporarily_unavailable)で返す。
        logger.error("confirm_customer_identity Tool処理に失敗 (shop_id=%s): %s", shop_id, e)
        return ConfirmCustomerIdentityToolResponse(success=False, status="temporarily_unavailable")


@router.post("/tools/get-customer-context", response_model=GetCustomerContextToolResponse)
async def get_customer_context_tool(
    shop_id: str,
    request: GetCustomerContextToolRequest,
    db: AsyncSession = Depends(get_db),
) -> GetCustomerContextToolResponse:
    """
    Outbound AI Phase 4B: get_customer_context Tool Calling専用エンドポイント
    （認証不要）。

    設計方針（重要・必ず守ること。仕様書section7「AIだけを信用しない」）:
    - このToolのparameters自体が空のobjectであり、AIから受け取る引数は
      存在しない（app.services.realtime_voice_ai._REALTIME_TOOLS参照）。
      session_idのみフロントエンドが自動転送する。
    - このshop_id・session_idの組み合わせがconfirm_customer_identityにより
      verified状態になっていない限り、絶対に詳細情報を返さない
      （statusをnot_verifiedにする）。この判定は
      app.services.customer_context.get_verified_customer_memory_id()の
      戻り値のみで行い、AI側の自己申告に一切依存しない。
    - 実際のContext構築（何を返してよいか・医療系業種の制限）は一切ここで
      再実装せず、必ずapp.services.customer_context.build_customer_context()
      をそのまま呼び出す。
    """
    _check_customer_context_rate_limit(shop_id)

    try:
        shop = await db.get(Shop, shop_id)
        if not shop or not shop.is_active:
            return GetCustomerContextToolResponse(success=False, status="temporarily_unavailable")

        if not request.session_id:
            return GetCustomerContextToolResponse(success=True, status="not_verified")

        customer_memory_id = customer_context.get_verified_customer_memory_id(
            shop_id=shop_id, voice_session_id=request.session_id,
        )
        if customer_memory_id is None:
            return GetCustomerContextToolResponse(success=True, status="not_verified")

        ctx = await customer_context.build_customer_context(shop, customer_memory_id)
        if ctx is None:
            return GetCustomerContextToolResponse(success=True, status="no_context")

        return GetCustomerContextToolResponse(success=True, status="context_available", **ctx)
    except HTTPException:
        raise
    except Exception as e:
        # 例外の詳細をAIやレスポンスに漏らさず、必ず安全側(temporarily_unavailable)で返す。
        logger.error("get_customer_context Tool処理に失敗 (shop_id=%s): %s", shop_id, e)
        return GetCustomerContextToolResponse(success=False, status="temporarily_unavailable")


@router.post("/tools/set-conversation-language", response_model=SetConversationLanguageToolResponse)
async def set_conversation_language_tool(
    shop_id: str,
    request: SetConversationLanguageToolRequest,
    db: AsyncSession = Depends(get_db),
) -> SetConversationLanguageToolResponse:
    """
    Phase 5A: set_conversation_language Tool Calling専用エンドポイント（認証不要）。

    設計方針（重要・必ず守ること）:
    - shop_idはURLパス由来のみを使い、リクエストボディには含めない。
      session_idもAIの引数ではなく、フロントエンドがRealtimeセッション確立時に
      保持しているvoice_session_idをそのまま転送したものである（find_customer等
      と同じパターン）。AIが指定できるのはlanguage_codeのみ。
    - 実際の検証（言語コードとして有効か・その店舗のai_supported_languagesに
      含まれるか）は一切ここで再実装せず、必ず
      app.services.conversation_language.set_session_language()をそのまま呼び出す。
      許可されていない言語だった場合は状態を変更せず、status="not_allowed"で
      返す（HTTPエラーにはしない。AIは現在の言語のまま会話を継続してよい）。
    - この状態はあくまで「現在の会話で使ってよい言語」という会話進行上の
      ヒントであり、予約・顧客識別等の実際の処理には一切影響しない。
    """
    _check_set_conversation_language_rate_limit(shop_id)

    try:
        shop = await db.get(Shop, shop_id)
        if not shop or not shop.is_active:
            return SetConversationLanguageToolResponse(success=False, status="temporarily_unavailable")

        if not request.session_id:
            return SetConversationLanguageToolResponse(success=True, status="not_allowed")

        accepted = conversation_language_state.set_session_language(
            shop_id=shop_id,
            voice_session_id=request.session_id,
            language_code=request.language_code,
            ai_supported_languages_raw=shop.ai_supported_languages,
        )
        return SetConversationLanguageToolResponse(
            success=True, status="accepted" if accepted else "not_allowed"
        )
    except HTTPException:
        raise
    except Exception as e:
        # 例外の詳細をAIやレスポンスに漏らさず、必ず安全側(temporarily_unavailable)で返す。
        logger.error("set_conversation_language Tool処理に失敗 (shop_id=%s): %s", shop_id, e)
        return SetConversationLanguageToolResponse(success=False, status="temporarily_unavailable")


@router.post("/tools/get-shop-info")
async def get_shop_info_tool(
    shop_id: str,
    request: GetShopInfoToolRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Realtime Voice AI Phase3D: get_shop_info Tool Calling専用エンドポイント（認証不要）。

    設計方針（重要・必ず守ること）:
    - shop_idはcheck_availability/create_reservationと同じくURLパスの値のみを使い、
      リクエストボディ(GetShopInfoToolRequest)にはshop_id/tenant_idを一切含めない。
      Realtime AI（LLM側）に他店舗のshop_idを自由に指定させる余地を作らない。
    - 実際のデータ取得・「未設定」と「明確にNo」の区別・FAQのILIKE検索は一切ここで
      再実装せず、必ずapp/routers/shop_knowledge.py の get_shop_info_for_ai()
      （ShopKnowledge/ShopFAQテーブルのみを参照し、内部メモ・売上・顧客情報・
      他店舗データを一切含まない設計の関数）をそのまま呼び出す。
    - このエンドポイントが返す形は必ず {"success": bool, ...} で、成功時は必ず
      "known"(bool)と"data"(dict|None)を含む。DBエラー等の技術的失敗
      （reason_code="temporarily_unavailable"）と、「情報が未設定なだけ」
      （success=True, known=False）を絶対に混同しない
      （get_shop_info_for_ai()自体がこの区別を内部で保証している）。
    - 店舗が存在しない・非公開の場合も、お客様やAIの入力ミスではなくこちら側の
      事情であるため、reason_code="temporarily_unavailable"として安全側に倒す
      （check_availability/create_reservationと同じ方針）。
    """
    _check_shop_info_rate_limit(shop_id)

    try:
        shop = await db.get(Shop, shop_id)
        if not shop or not shop.is_active:
            return {"success": False, "reason_code": "temporarily_unavailable"}

        return await get_shop_info_for_ai(db, shop_id, request.topic, request.query)
    except HTTPException:
        raise
    except Exception as e:
        # 例外の詳細をAIやレスポンスに漏らさず、必ず安全側(success=False)で返す。
        logger.error("get_shop_info Tool処理に失敗 (shop_id=%s): %s", shop_id, e)
        return {"success": False, "reason_code": "temporarily_unavailable"}


@router.post("/tools/request-callback", response_model=RequestCallbackToolResponse)
async def request_callback_tool(
    shop_id: str,
    request: RequestCallbackToolRequest,
    db: AsyncSession = Depends(get_db),
) -> RequestCallbackToolResponse:
    """
    Human Handoff基盤: request_callback Tool Calling専用エンドポイント（認証不要）。

    設計方針（重要・必ず守ること）:
    - shop_idは他のToolと同じくURLパスの値のみを使う。call_idもAIの引数ではなく、
      ブラウザがOpenAI Realtimeのfunction_callイベントから読み取った値をそのまま
      転送したものであり、create_reservation_toolと全く同じ
      "realtime_voice:{shop_id}:{call_id}" 形式でidempotency_keyへnamespace化する。
      同一キーによる同時多重INSERTは、最終的にDBの一意インデックス
      (ux_callback_requests_idempotency_key)が最終防衛する
      （create_reservation_toolと同じ、SELECTベースの事前チェックに頼らない設計）。
    - このToolの目的は「AIが安全に回答・予約できない場合に、担当者へ引き継ぐ」ことで
      あり、CallbackRequest行のDB保存が確実に成功して初めてsuccess=Trueを返す。
      成功後にOutbound通知（担当者への電話）のenqueueを試みるが、これは
      ベストエフォートの下流処理であり、失敗してもCallbackRequest保存自体
      （＝AIへ返すsuccess=True）を取り消さない。AIが「担当者から折り返します」と
      お客様へ案内した後で、その根拠となる受付記録自体が消えることは絶対にない
      設計にするため（Email通知は本フェーズでは実際に送信する仕組みを実装しない。
      email_notification_statusへ"skipped"を記録し、値の置き場所だけ用意する）。
    - reason_codeはCallbackRequestReasonCodeの既知の値のみを受け付け、未知の値や
      未指定の場合は安全側で"other"にフォールバックする（AIが将来追加される
      reason_codeを古いプロンプトのまま送ってきても失敗にはしない）。
    - desired_date/desired_time/party_sizeはAIが把握している場合のみの参考情報
      であり、ここで空き状況の再判定は一切行わない（Human Handoffの時点で、AIは
      既に「今は自分で安全に判断できない」と判断済みであるため、Toolの引数を
      検証のうえそのまま保存するのみ。形式が不正な値は静かに無視してNoneのまま
      保存する＝受付自体は失敗させない）。
    - customer_phone/customer_nameの妥当性（本人確認・桁数チェック等）はAI側の
      会話ルール（既存のBOOKING_SAFETY同様、1桁ずつ復唱確認する等）に委ね、この
      エンドポイントでは追加の検証を行わない（create_reservation_toolのguest_phone
      と同じ方針）。
    """
    _check_callback_tool_rate_limit(shop_id)

    def _safe_failure() -> RequestCallbackToolResponse:
        return RequestCallbackToolResponse(success=False, reason_code=None)

    try:
        shop = await db.get(Shop, shop_id)
        if not shop or not shop.is_active:
            # shop_idはAIの引数ではなくURLパス由来のため、これが起きるのは
            # 店舗の非公開化等こちら側の事情であり、AIやお客様の入力ミスではない。
            return _safe_failure()

        valid_reason_codes = {c.value for c in CallbackRequestReasonCode}
        reason_code = (
            request.reason_code if request.reason_code in valid_reason_codes
            else CallbackRequestReasonCode.OTHER.value
        )

        desired_date_val = None
        if request.desired_date:
            try:
                desired_date_val = date_type.fromisoformat(request.desired_date)
            except ValueError:
                desired_date_val = None

        desired_time_val = None
        if request.desired_time:
            try:
                desired_time_val = datetime.strptime(request.desired_time, "%H:%M").time()
            except ValueError:
                desired_time_val = None

        idempotency_key = f"realtime_voice:{shop_id}:{request.call_id}"

        callback_request = CallbackRequest(
            shop_id=shop_id,
            voice_session_id=request.session_id,
            reason_code=reason_code,
            customer_name=request.customer_name,
            customer_phone=request.customer_phone,
            inquiry_text=request.inquiry_text,
            desired_date=desired_date_val,
            desired_time=desired_time_val,
            party_size=request.party_size,
            service_id=request.service_id,
            status=CallbackRequestStatus.PENDING.value,
            # Email送信の仕組み自体は本フェーズでは未実装のため、常に"skipped"。
            email_notification_status="skipped",
            idempotency_key=idempotency_key,
        )
        db.add(callback_request)

        try:
            await db.commit()
        except IntegrityError:
            # create_reservation_toolと同じLayer1設計: 同一call_idでの同時多重
            # INSERT（Request A/Bが共にcommit前を通過し、片方だけがDBの一意制約
            # ux_callback_requests_idempotency_keyに違反してここへ到達するケース）。
            # 先にcommitできた側の既存行を取得し、同じ成功結果を返す。
            await db.rollback()
            result = await db.execute(
                select(CallbackRequest).filter(CallbackRequest.idempotency_key == idempotency_key)
            )
            existing = result.scalar_one_or_none()
            if existing:
                logger.info(
                    "request_callback Tool: idempotency_key=%s のcommit競合を検知。"
                    "先に成立した受付(id=%s)を返します (shop_id=%s)",
                    idempotency_key, existing.id, shop_id,
                )
                return RequestCallbackToolResponse(success=True, reason_code=existing.reason_code)
            logger.error(
                "request_callback Tool: idempotency_keyの一意制約違反後、既存行が見つかりません "
                "(shop_id=%s, call_id=%s)", shop_id, request.call_id,
            )
            return _safe_failure()

        # ここに到達した時点でCallbackRequestのDB保存は確定済み（success=Trueが確定）。
        # 以降のOutbound通知(電話)enqueueはベストエフォートの下流処理であり、
        # 失敗してもこの結果（success=True）を変更しない。
        try:
            job_id = await enqueue_callback_requested_call(
                shop_id=shop_id,
                callback_request_id=callback_request.id,
                notification_enabled=bool(shop.reservation_phone_notification_enabled),
                notification_phone=shop.reservation_notification_phone,
            )
            if job_id:
                callback_request.outbound_call_job_id = job_id
                callback_request.status = CallbackRequestStatus.NOTIFIED.value
                await db.commit()
        except Exception:
            # 通知enqueueの失敗はCallbackRequest保存自体の成功（success=True）に
            # 一切影響させない（既にcommit済みのため、受付記録は確実に残る）。
            logger.exception(
                "request_callback Tool: Outbound通知enqueueに失敗しました"
                "（受付自体は保存済みのため処理を継続します） "
                "shop_id=%s callback_request_id=%s",
                shop_id, callback_request.id,
            )

        # Phase N1: 統一Owner Notification基盤。上のOutbound通知(電話)enqueueと
        # 同じく、CallbackRequest保存が確定した後にのみ・専用DBセッションで・
        # 失敗分離で呼び出す。inquiry_textの生テキストは一切渡さない
        # （notify_callback_requested内部でreason_codeベースの一般的な文言に変換する）。
        await notify_callback_requested(
            shop_id=shop_id,
            callback_request_id=callback_request.id,
            reason_code=reason_code,
            customer_name=request.customer_name,
        )

        return RequestCallbackToolResponse(success=True, reason_code=reason_code)
    except HTTPException:
        raise
    except Exception as e:
        # 例外の詳細をAIやレスポンスに漏らさず、必ず安全側(success=False)で返す。
        logger.error("request_callback Tool処理に失敗 (shop_id=%s): %s", shop_id, e)
        return _safe_failure()
