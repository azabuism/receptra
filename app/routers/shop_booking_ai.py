"""
店舗ページ AI予約（チャット予約 / 通話予約）

来店客が店舗ページ（shop.html）から、ログイン不要でAIと会話しながら
来店予約を行うための公開エンドポイント。電話AI受付（vonage_voice.py）と
会話ロジックの土台（OpenAI呼び出し部分）は共有するが、こちらは
「予約を実際に成立させる」ところまでを担当する。

設計方針:
- 認証不要（お客様は会員登録・ログイン不要で予約できるのが元々の方針のため）
- AIの応答はJSONで「予約に必要な情報が揃ったか(ready_to_book)」を返させ、
  実際の予約可否判定（営業時間・満席チェック等）は必ずバックエンド側
  （/api/v1/reservations/create と全く同じ検証ロジック）で行う。
  AIの自己申告だけで「予約完了」とお客様に案内することはしない。
- 予約成立/失敗時にお客様へ表示する文言は、AIの生成テキストをそのまま
  使わずシステム側で確定的に組み立て、セッション履歴もその文言で上書きする
  （override_last_assistant_message）。AIに正しい結果を"演技"させるのではなく、
  実際に起きたことをそのまま伝えるため。
"""

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import ValidationError

from app.deps import get_db
from app.models.shop import Shop, ShopHours, ShopHoursOverride
from app.schemas.reservation import ReservationCreateRequest
from app.routers.reservations import create_reservation
from app.services import voice_ai

logger = logging.getLogger("receptra.shop_booking_ai")

router = APIRouter(prefix="/api/v1/shops/{shop_id}/booking-ai", tags=["shop_booking_ai"])

JST = timezone(timedelta(hours=9))
_DAY_NAMES_JA = ["月", "火", "水", "木", "金", "土", "日"]

_REQUIRED_FIELD_LABELS = [
    ("reservation_date", "来店日時"),
    ("number_of_people", "人数"),
    ("guest_name", "お名前"),
    ("guest_phone", "電話番号"),
]


async def _get_shop_or_404(db: AsyncSession, shop_id: str) -> Shop:
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")
    return shop


def _date_str_jst(d) -> str:
    """datetime.date を「2026年9月24日（木曜日）」形式に整形する。
    Reservation Intelligence Phase D-3: app/services/realtime_voice_ai.py の
    同名関数と内容は同じだが、既存ファイルには手を加えない方針のため、ここに
    独立して実装している（_build_hours_block()と同じ意図的な重複）。
    """
    return f"{d.year}年{d.month}月{d.day}日（{_DAY_NAMES_JA[d.weekday()]}曜日）"


async def _build_override_lines(db: AsyncSession, shop_id: str) -> list:
    """
    Reservation Intelligence Phase D-3: 特定日の営業時間（ShopHoursOverride）の
    うち、本日以降のものをシステムプロンプト埋め込み用のテキスト行に整形する。
    app/services/realtime_voice_ai.py の同名関数と内容は同じ（Section38。詳細は
    同関数のdocstring参照）。既存ファイルには手を加えない方針のため、ここに
    独立して実装している。
    """
    today = datetime.now(JST).date()
    result = await db.execute(
        select(ShopHoursOverride)
        .where(ShopHoursOverride.shop_id == shop_id, ShopHoursOverride.target_date >= today)
        .order_by(ShopHoursOverride.target_date)
        .limit(100)
    )
    overrides = result.scalars().all()
    lines = []
    for o in overrides:
        opening = o.opening_time.strftime("%H:%M")
        closing = ("翌" if o.closes_next_day else "") + o.closing_time.strftime("%H:%M")
        line = f"{_date_str_jst(o.target_date)}: {opening}〜{closing}（通常の曜日ごとの営業時間とは異なる特別営業時間）"
        if o.last_order_time:
            lo = ("翌" if o.last_order_next_day else "") + o.last_order_time.strftime("%H:%M")
            line += f"、ラストオーダー{lo}"
        lines.append(line)
    return lines


async def _build_hours_block(db: AsyncSession, shop_id: str) -> str:
    result = await db.execute(select(ShopHours).where(ShopHours.shop_id == shop_id))
    rows = {h.day_of_week: h for h in result.scalars().all()}
    if not rows:
        # Phase3B.1: create_reservation()がShopHours未設定を予約不可
        # （business_hours_not_configured）に統一したため、AIへの案内も
        # 実態に合わせて修正する（以前は「予約確定時にご案内します」としていたが、
        # 実際には予約自体が成立しないため誤案内になっていた）。
        return "（営業時間がまだ設定されていないため、現在オンラインでは予約を確定できません。日時を伺った上で、確定できない旨を簡潔にご案内してください）"
    lines = []
    for day in range(7):
        h = rows.get(day)
        if not h or h.is_closed:
            lines.append(f"{_DAY_NAMES_JA[day]}曜: 定休日")
        else:
            opening = h.opening_time.strftime("%H:%M")
            closing = h.closing_time.strftime("%H:%M")
            lines.append(f"{_DAY_NAMES_JA[day]}曜: {opening}〜{closing}")

    # Reservation Intelligence Phase D-3: 特定日の営業時間が本日以降に1件以上
    # 登録されていれば末尾に追記する。1件も無い店舗では従来と完全に同じ
    # テキストになる（realtime_voice_ai.py の _build_hours_block()と同じ方針）。
    override_lines = await _build_override_lines(db, shop_id)
    if override_lines:
        lines.append("")
        lines.append("※以下の特定の日付は、上記の曜日ごとの営業時間より優先されます:")
        lines.extend(override_lines)

    return "\n".join(lines)


def _today_str_jst() -> str:
    now = datetime.now(JST)
    return f"{now.year}年{now.month}月{now.day}日（{_DAY_NAMES_JA[now.weekday()]}曜日）"


def _parse_ai_datetime(value) -> Optional[datetime]:
    """AIが出力した日時文字列を、店舗のローカル時刻を表すnaive datetimeとして解釈する"""
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    dt = None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt


@router.post("/start")
async def booking_start(shop_id: str, db: AsyncSession = Depends(get_db)):
    """お客様がAI予約チャット/通話を開始する（認証不要）"""
    shop = await _get_shop_or_404(db, shop_id)
    hours_block = await _build_hours_block(db, shop_id)
    session_id = f"book-{shop_id}-{uuid.uuid4()}"
    voice_ai.start_booking_session(
        session_id,
        shop_name=shop.name,
        hours_block=hours_block,
        today_str=_today_str_jst(),
        shop_id=shop_id,
    )
    return {
        "session_id": session_id,
        "shop_id": shop_id,
        "shop_name": shop.name,
        "reservations_enabled": bool(shop.reservations_enabled),
    }


@router.post("/chat")
async def booking_chat(shop_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """
    お客様の1発話をAI予約受付に送り、応答を1ターン分返す。
    必要な情報が揃った時点で、実際に /api/v1/reservations/create と
    同じ検証ロジックで予約作成を試み、その結果に応じた案内を返す。
    """
    body = await request.json()
    session_id = body.get("session_id")
    message = body.get("message")

    if not session_id:
        raise HTTPException(status_code=400, detail="session_id は必須です（先に /start を呼んでください）")
    if not message:
        raise HTTPException(status_code=400, detail="message は必須です")

    result = await voice_ai.get_booking_ai_reply(session_id, message)

    reply_text = result["reply"]
    end_call = result["end_call"]
    booked = False
    reservation_info = None

    if result["ready_to_book"]:
        missing = [label for key, label in _REQUIRED_FIELD_LABELS if not result.get(key)]
        if missing:
            reply_text = f"恐れ入りますが、{'・'.join(missing)}をもう一度教えていただけますでしょうか。"
            voice_ai.override_last_assistant_message(session_id, reply_text)
        else:
            reservation_dt = _parse_ai_datetime(result.get("reservation_date"))
            if reservation_dt is None:
                reply_text = "恐れ入ります、ご希望の日時をもう一度、日付と時間で教えていただけますでしょうか。"
                voice_ai.override_last_assistant_message(session_id, reply_text)
            else:
                try:
                    req = ReservationCreateRequest(
                        shop_id=shop_id,
                        reservation_date=reservation_dt,
                        number_of_people=int(result.get("number_of_people") or 1),
                        guest_name=str(result.get("guest_name"))[:255],
                        guest_phone=str(result.get("guest_phone"))[:20],
                        special_requests=(result.get("special_requests") or None),
                    )
                except (ValidationError, ValueError, TypeError) as e:
                    logger.warning("AI予約リクエストの構築に失敗 (session=%s): %s", session_id, e)
                    reply_text = "恐れ入ります、お名前・電話番号・人数をもう一度教えていただけますでしょうか。"
                    voice_ai.override_last_assistant_message(session_id, reply_text)
                else:
                    try:
                        booking_response = await create_reservation(req, db, None)
                    except HTTPException as e:
                        reply_text = f"申し訳ございません、{e.detail}。恐れ入りますが、別の日時をお伺いしてもよろしいでしょうか。"
                        end_call = False
                        voice_ai.override_last_assistant_message(session_id, reply_text)
                    else:
                        reservation = booking_response.reservation
                        date_str = reservation.reservation_date.strftime("%Y年%m月%d日 %H:%M")
                        reply_text = (
                            f"{result.get('guest_name')}様、ご予約ありがとうございます。"
                            f"{date_str}に{reservation.number_of_people}名でお席をご用意いたしました。"
                            "当日のご来店をスタッフ一同お待ちしております。"
                        )
                        booked = True
                        end_call = True
                        reservation_info = {
                            "reservation_id": booking_response.reservation_id,
                            "reservation_date": reservation.reservation_date.isoformat(),
                            "number_of_people": reservation.number_of_people,
                        }
                        voice_ai.override_last_assistant_message(session_id, reply_text)
                        voice_ai.mark_session_booked(session_id)

    if end_call:
        voice_ai.end_session(session_id)

    return {
        "session_id": session_id,
        "reply": reply_text,
        "end_call": end_call,
        "booked": booked,
        "reservation": reservation_info,
        "cumulative_usage": result["cumulative_usage"],
        "model": result["model"],
    }


@router.post("/end")
async def booking_end(shop_id: str, request: Request):
    """セッションを破棄する（ページを閉じる/リセットする際に呼ぶ）"""
    body = await request.json()
    session_id = body.get("session_id")
    if session_id:
        voice_ai.end_session(session_id)
    return {"ended": True}
