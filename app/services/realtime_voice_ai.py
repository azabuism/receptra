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
- マニュアルを読み上げるような丁寧すぎる接客敬語ではなく、実際の店員が
  電話口で話すような、自然でくだけたテンポの話し言葉にしてください。
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

# 言語ルール（最重要・絶対に守ってください）
- この通話の基本言語は日本語です。お客様が日本語で話している間は、
  通話が終わるまで日本語を維持してください。通話の途中で勝手に英語や
  他の言語に切り替えることは絶対にしないでください。
- 電話番号・数字・日付・時刻・金額を読み上げるときも、英語の発音や
  英単語（"zero" "nine" "September" "hundred" 等）を使わないでください。
  文字としては半角数字（090-1234-5678等）で渡されていても、それを
  英語として読むのではなく、日本語の発音として自然に読んでください。
- 電話番号は1桁ずつ、日本語で区切って読み上げてください。
  例:「090-1234-5678」→「ゼロキューゼロ、イチニサンヨン、
  ゴーロクナナハチ」のように読みます。
- 日付・時刻は「9月25日の19時」「9月25日の夜7時」のように自然な日本語
  で話してください。「September twenty-fifth」のような英語表現は
  禁止です。
- 金額は「5,500円」を「ごせんごひゃくえん」のように、日本語の金額表現
  として話してください。
- 言語を切り替えてよいのは、お客様が明確に英語（または他の言語）で
  話しかけてきた場合、または「英語でお願いします」のように明示的に
  言語の変更を希望した場合だけです。数字や固有名詞が含まれるという
  理由だけで言語を切り替えないでください。
- 一度英語などに切り替えた後でも、お客様が日本語で話しかけ直したら、
  自然に日本語へ戻ってください。

# 話し方の見本（この温度感・テンポをそのまま真似てください）
客:「今日って空いてます？」
AI:「はい。何時頃がいいですか？」
客:「7時くらいかな」
AI:「7時ですね。何名様ですか？」
客:「3人。あ、やっぱ8時で」
AI:「はい、8時ですね。3名様で確認します。」
客:「電話番号は090-1234-5678です」
AI:「ゼロキューゼロ、イチニサンヨン、ゴーロクナナハチですね、ありがとうございます。」
客:「料金はいくら？」
AI:「5,500円です。」
客:「My name is John. English please.」
AI:（ここでは明示的に英語を希望しているため、英語に切り替えて応対する）

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

    # 会話品質の調査結果（2026年9月）を踏まえた明示設定。
    # 以前はvoice/turn_detectionを未指定にしており、OpenAI側の暗黙の
    # デフォルトに委ねていたことが「日本語がカタコト」「テンポが不自然」
    # という報告の一因と考えられたため、公式ドキュメントの推奨に沿って
    # 明示的に指定する。
    #
    # 重要: この2項目は、インストール済みのopenaiパッケージ(1.109.1)が
    # 生成する型定義(RealtimeAudioConfigOutputParam/InputParam)で
    # session.audio.output.voice / session.audio.input.turn_detection という
    # ネスト構造であることを直接確認した上で、その構造で送っている
    # （以前の実装案ではセッション直下にvoice/turn_detectionを置いていたが、
    # それは誤りだったため、実装時に修正した）。
    session_config: dict = {
        "type": "realtime",
        "model": settings.OPENAI_REALTIME_MODEL,
        "instructions": instructions,
        "audio": {
            "output": {
                # OpenAI公式のWebRTC接続ガイド・TTSガイドが
                # gpt-realtime-2.1との組み合わせで明示的に推奨している音声。
                "voice": settings.OPENAI_REALTIME_VOICE,
            },
            "input": {
                # 現行デフォルトのsemantic_vad
                # （無音時間ではなく発話内容の区切りで判定）を明示化。
                # eagernessは今後の実機テストでチューニングする前提の初期値。
                "turn_detection": {
                    "type": "semantic_vad",
                    "eagerness": settings.OPENAI_REALTIME_VAD_EAGERNESS,
                },
            },
        },
    }

    # reasoning（推論の深さ）: gpt-realtime-2.1のモデルページには
    # 「configurable reasoning effortに対応し、上げるほどレイテンシと
    # トークン使用量が増える」と記載があるが、インストール済みの
    # openai==1.109.1が生成する型定義には現時点でこのフィールドが
    # 一切存在しない（実装時にopenai/types/realtime配下を直接確認して
    # 確認済み）。つまり実際のAPIが本当にこのフィールドを受け付けるかは
    # 未検証。本番同等環境でしか実キーによる検証ができないため、
    # 送信自体は試すが、無効な場合に備えて空文字にすればこのフィールド
    # ごと送らないようにしてあり、デプロイ後にセッション発行が失敗する
    # 場合はまずこの値を空にして切り分けられるようにしている。
    if settings.OPENAI_REALTIME_REASONING_EFFORT:
        session_config["reasoning"] = {
            "effort": settings.OPENAI_REALTIME_REASONING_EFFORT,
        }

    client = _get_client()
    secret = await client.realtime.client_secrets.create(
        expires_after={
            "anchor": "created_at",
            "seconds": settings.REALTIME_CLIENT_SECRET_TTL_SECONDS,
        },
        session=session_config,
    )

    logger.info(
        "Realtimeセッションを発行 shop_id=%s model=%s voice=%s vad_eagerness=%s "
        "reasoning_effort=%s expires_at=%s",
        shop.id,
        settings.OPENAI_REALTIME_MODEL,
        settings.OPENAI_REALTIME_VOICE,
        settings.OPENAI_REALTIME_VAD_EAGERNESS,
        settings.OPENAI_REALTIME_REASONING_EFFORT,
        secret.expires_at,
    )

    return {
        "client_secret": secret.value,
        "expires_at": secret.expires_at,
        "model": settings.OPENAI_REALTIME_MODEL,
    }
