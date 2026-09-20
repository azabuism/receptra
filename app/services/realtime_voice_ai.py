"""
OpenAI Realtime API（ブラウザ ⇔ WebRTC 直結）用のセッション構築ロジック

設計方針:
- 音声そのものはブラウザとOpenAIの間をWebRTCで直接流れ、RECEPTRAのサーバーは
  一切経由しない。サーバー側の役割は「店舗情報から会話のシステムプロンプトを
  組み立て、OpenAI公式が現在推奨している短命のephemeralトークン(client secret)
  を発行してブラウザへ渡す」ことだけに限定する。
  OPENAI_API_KEY自体はここでサーバー側にのみ保持し、ブラウザには一切渡さない。
- Phase1のスコープでは Function Calling・メニュー/料金/予約DBの参照は行わず、
  店舗名と営業時間のみをシステムプロンプトに含める簡易版とする。
  既存の shop_booking_ai.py（チャット予約）・voice_ai.py（電話AI/店舗ページ
  チャット・従来型音声）は一切変更しない、完全に別レイヤーの実装。
- OpenAI Realtime APIのモデル名・イベント名・セッション設定の項目名は
  この数ヶ月でも変更されているため、実装時点（2026年9月）で
  openai公式Pythonライブラリ(openai==1.109.1)に実際に存在する
  `client.realtime.client_secrets.create()` の型定義を確認した上で実装している。
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.shop import Shop, ShopHours

logger = logging.getLogger("receptra.realtime_voice_ai")

JST = timezone(timedelta(hours=9))
_DAY_NAMES_JA = ["月", "火", "水", "木", "金", "土", "日"]

_client: Optional[AsyncOpenAI] = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


async def _build_hours_block(db: AsyncSession, shop_id: str) -> str:
    """
    店舗の営業時間をシステムプロンプト埋め込み用のテキストに整形する。
    shop_booking_ai.py の同名処理と内容は同じだが、既存ファイルには手を
    加えない方針のため、ここに独立して実装している（意図的な重複）。
    """
    result = await db.execute(select(ShopHours).where(ShopHours.shop_id == shop_id))
    rows = {h.day_of_week: h for h in result.scalars().all()}
    if not rows:
        return "（営業時間の登録がありません。ご希望日時はそのまま伺ってください）"
    lines = []
    for day in range(7):
        h = rows.get(day)
        if not h or h.is_closed:
            lines.append(f"{_DAY_NAMES_JA[day]}曜: 定休日")
        else:
            opening = h.opening_time.strftime("%H:%M")
            closing = h.closing_time.strftime("%H:%M")
            lines.append(f"{_DAY_NAMES_JA[day]}曜: {opening}〜{closing}")
    return "\n".join(lines)


def _today_str_jst() -> str:
    now = datetime.now(JST)
    return f"{now.year}年{now.month}月{now.day}日（{_DAY_NAMES_JA[now.weekday()]}曜日）"


REALTIME_INSTRUCTIONS_TEMPLATE = """\
あなたは飲食店・サロン等の予約管理システム「RECEPTRA」の音声AI受付です。
店舗「{shop_name}」の音声通話に、実際の受付スタッフのように応対してください。

# 話し方の絶対ルール
- 1回の発話は1〜2文、できるだけ短く話してください。長い前置きや繰り返しの
  お礼は不要です。
- 一度に複数のことを質問しないでください。人間の受付スタッフのように、
  一つずつ確認してください。
  悪い例:「お問い合わせいただきありがとうございます。ご予約について
  承知いたしました。それではご希望のお日にちとお時間についてお伺いさせて
  いただいてもよろしいでしょうか？」
  良い例:「はい。ご希望の日時はいつですか？」
- お客様が話し始めたら、あなたの発話は途中でも止めてください。
- お客様が言い直した場合（例:「3人、いや4人です」）は、最後に言った内容を
  正として自然に応じてください。聞き返して確認しても構いません。

# 店舗の営業時間（曜日ごと）
{hours_block}

# 本日の日付
{today_str}（「明日」「今週土曜」などの相対的な日時表現はこれを基準に解釈してください）

# 現時点での制約（重要・必ず守ってください）
このバージョンでは、まだ予約データベース・空き状況・メニュー・料金を
参照する機能を持っていません。日時やご希望人数などのヒアリングはしてよい
ですが、実際に予約が取れるかどうかの確定的な回答（「空いています」
「予約完了です」等）はまだしないでください。ヒアリングが終わったら、
「担当の者が確認してご連絡いたします」という趣旨で丁寧に案内してください。
存在しない予約状況やメニュー・料金を想像で答えることは絶対にしないで
ください。
"""


async def build_realtime_instructions(db: AsyncSession, shop: Shop) -> str:
    hours_block = await _build_hours_block(db, shop.id)
    return REALTIME_INSTRUCTIONS_TEMPLATE.format(
        shop_name=shop.name,
        hours_block=hours_block,
        today_str=_today_str_jst(),
    )


async def create_realtime_session(db: AsyncSession, shop: Shop) -> dict:
    """
    ブラウザ用の短命ephemeralトークン(client secret)を発行する。

    ブラウザはこのトークンをBearerトークンとしてOpenAIのWebRTCエンドポイントに
    直接接続する（RECEPTRAのサーバーは音声そのものを中継しない）。
    """
    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY が設定されていません")

    instructions = await build_realtime_instructions(db, shop)

    client = _get_client()
    secret = await client.realtime.client_secrets.create(
        expires_after={
            "anchor": "created_at",
            "seconds": settings.REALTIME_CLIENT_SECRET_TTL_SECONDS,
        },
        session={
            "type": "realtime",
            "model": settings.OPENAI_REALTIME_MODEL,
            "instructions": instructions,
        },
    )

    logger.info(
        "Realtimeセッションを発行 shop_id=%s model=%s expires_at=%s",
        shop.id,
        settings.OPENAI_REALTIME_MODEL,
        secret.expires_at,
    )

    return {
        "client_secret": secret.value,
        "expires_at": secret.expires_at,
        "model": settings.OPENAI_REALTIME_MODEL,
    }
