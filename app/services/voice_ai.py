"""
電話AI受付 会話ロジック

Vonage Voice APIの音声認識(speech-to-text)結果を受け取り、OpenAI APIで
次の発話（IVR応答）を生成する。予約確認・キャンセル・変更、営業電話の
1次対応（ヒアリング）を1つの会話フローとして扱う。

設計方針:
- 通話1本 = 1セッション（Vonageの conversation_uuid をキーに管理）
- セッション状態はプロセス内メモリで保持する簡易実装（MVP）。
  Railway上でインスタンスが複数/再起動する構成になった場合は、
  Redis等の外部ストアに移す必要がある（現状は単一インスタンス運用のため許容）。
- モデルには「返答文 / 分類 / 緊急度 / 通話終了フラグ / 要約」を
  JSON形式で同時に出力させ、電話の会話制御（次にinputを続けるか、
  切るか）とダッシュボード用ログの両方に使う。
- トークン使用量（入力/出力）をターンごと・通話ごとに集計し、
  精度検証・コスト試算に使えるようにする。
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from openai import AsyncOpenAI

from app.config import get_settings

logger = logging.getLogger("receptra.voice_ai")

# 2026年9月時点でOpenAI公式価格ページ(developers.openai.com/api/docs/pricing)から
# 確認した100万トークンあたりの価格(USD)。実際の価格は変動するため、
# 正確なコスト管理が必要な場合は請求ダッシュボードの実測値を優先すること。
_PRICING_USD_PER_1M_TOKENS = {
    "gpt-5.6-luna": {"input": 0.10, "output": 0.60},
    "gpt-5.6-terra": {"input": 1.00, "output": 6.00},
    "gpt-5.6-sol": {"input": 2.00, "output": 10.00},
    "gpt-6-astra": {"input": 5.00, "output": 25.00},
}
# 参考換算レート（2026年9月時点、概算）。円換算は目安として扱うこと。
USD_TO_JPY_APPROX = 157.0

SYSTEM_PROMPT_TEMPLATE = """\
あなたは飲食店・サロン等の予約管理システム「RECEPTRA」の電話AI受付です。
店舗名「{shop_name}」にかかってきた電話に、実際のスタッフのように丁寧な日本語（敬語）で対応します。

# あなたの役割
1. 電話がかかってきたら、まず要件が次のどちらかを自然な会話で見極めます。
   - 「reservation」: 来店予定のお客様からの、予約の確認・来店連絡・キャンセル・日時変更などの連絡
   - 「business_call」: 取引先・業者からの営業電話（商品提案、システム導入案内など）
   - 判別がつかない場合は「unclear」とし、丁寧に用件を聞き返してください。

2. reservation の場合:
   - 来店予定日時、氏名（分かれば）、要件（来店確認/キャンセル/変更）を聞き取る
   - キャンセルや変更の申し出は必ず内容を復唱して確認する
   - urgency: キャンセル・変更・特別対応の依頼は "high"、単なる来店確認の返事は "medium"

3. business_call の場合:
   - 会社名、担当者名、営業内容を簡潔に聞き取る
   - 「担当者に申し伝えます」という趣旨で丁寧に電話を終える
   - urgency は常に "low"

4. 用件の聞き取りが完了したら、丁寧にお礼を言い、通話を終了してよい状態にする(end_call: true)。
   1つの通話は長くても4〜5往復程度で要件を聞き取り切ること。

# 出力形式
必ず次のキーを持つJSONオブジェクトのみを出力してください（説明文や前後の文章は不要）:
{{
  "reply": "電話口で実際に話す返答文（日本語、1〜2文程度、簡潔に）",
  "category": "reservation" | "business_call" | "unclear",
  "urgency": "high" | "medium" | "low" | null,
  "end_call": true | false,
  "summary": "通話終了時のみ: オーナーへの引き継ぎ用の要約（誰が・何の用件か）。継続中はnull"
}}
"""

_client: Optional[AsyncOpenAI] = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


# session_id -> {"messages": [...], "shop_name": str, "turn_count": int,
#                "usage": {"prompt_tokens": int, "completion_tokens": int},
#                "started_at": datetime}
_sessions: dict[str, dict] = {}


def _new_session(shop_name: str) -> dict:
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT_TEMPLATE.format(shop_name=shop_name)}
        ],
        "shop_name": shop_name,
        "turn_count": 0,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        "started_at": datetime.now(timezone.utc),
    }


def start_session(session_id: str, shop_name: str = "当店") -> dict:
    """通話開始時にセッションを初期化（既存があれば上書きしない）"""
    if session_id not in _sessions:
        _sessions[session_id] = _new_session(shop_name)
    return _sessions[session_id]


def end_session(session_id: str) -> Optional[dict]:
    """通話終了時にセッションを破棄し、最終状態を返す（ログ用）"""
    return _sessions.pop(session_id, None)


def _resolve_model() -> str:
    settings = get_settings()
    return settings.VOICE_AI_MODEL or settings.OPENAI_MODEL


async def _create_completion(client: AsyncOpenAI, model: str, messages: list, max_tokens: int):
    """
    OpenAIのモデル世代によってサポートするパラメータが異なるため
    (例: gpt-5.6系は max_tokens ではなく max_completion_tokens を要求し、
    temperature のカスタム値も受け付けずデフォルト値のみ許可する)、
    まず現行仕様のパラメータ一式で呼び出し、モデルがサポートしないパラメータを
    エラー内容から特定して1つずつ取り除きながら再試行する。
    """
    kwargs = dict(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
        temperature=0.4,
        max_completion_tokens=max_tokens,
    )

    for _ in range(len(kwargs)):
        try:
            return await client.chat.completions.create(**kwargs)
        except Exception as e:
            message = str(e)
            adjusted = False

            if "max_completion_tokens" in kwargs and "max_completion_tokens" in message and (
                "unsupported_parameter" in message or "Unsupported parameter" in message
            ):
                logger.info("モデル %s は max_completion_tokens 未対応のため max_tokens にフォールバック", model)
                kwargs.pop("max_completion_tokens")
                kwargs["max_tokens"] = max_tokens
                adjusted = True
            elif "temperature" in kwargs and "temperature" in message and (
                "unsupported_value" in message or "Unsupported value" in message
            ):
                logger.info("モデル %s はカスタムtemperature未対応のためデフォルト値にフォールバック", model)
                kwargs.pop("temperature")
                adjusted = True

            if not adjusted:
                raise

    # ここには通常到達しない（全パラメータを外しても失敗する異常系）
    return await client.chat.completions.create(
        model=model, messages=messages, response_format={"type": "json_object"}
    )


def estimate_cost_usd(usage: dict, model: str) -> Optional[float]:
    pricing = _PRICING_USD_PER_1M_TOKENS.get(model)
    if not pricing:
        return None
    cost = (
        usage.get("prompt_tokens", 0) / 1_000_000 * pricing["input"]
        + usage.get("completion_tokens", 0) / 1_000_000 * pricing["output"]
    )
    return round(cost, 6)


async def get_ai_reply(session_id: str, user_text: str, shop_name: str = "当店") -> dict:
    """
    ユーザー発話（音声認識結果）を受け取り、AIの次の応答を生成する。

    戻り値:
      {
        "reply": str, "category": str, "urgency": str|None,
        "end_call": bool, "summary": str|None,
        "turn_usage": {"prompt_tokens": int, "completion_tokens": int},
        "cumulative_usage": {"prompt_tokens": int, "completion_tokens": int},
        "model": str,
      }
    """
    settings = get_settings()
    session = _sessions.setdefault(session_id, _new_session(shop_name))
    model = _resolve_model()

    session["messages"].append({"role": "user", "content": user_text})
    session["turn_count"] += 1

    # 安全装置: 想定外に会話が長引いた場合は強制的に終話させる
    force_end = session["turn_count"] >= settings.VOICE_AI_MAX_TURNS

    client = _get_client()
    try:
        response = await _create_completion(client, model, session["messages"], settings.VOICE_AI_MAX_TOKENS)
    except Exception as e:
        logger.error("OpenAI呼び出しエラー (session=%s): %s", session_id, e)
        # API障害時のフォールバック応答（通話自体は継続させず丁寧に終える）
        return {
            "reply": "申し訳ございません、只今システムが混み合っております。恐れ入りますが、後ほどお店まで直接お電話いただけますでしょうか。",
            "category": "unclear",
            "urgency": "medium",
            "end_call": True,
            "summary": f"AI応答エラーのため強制終話: {e}",
            "turn_usage": {"prompt_tokens": 0, "completion_tokens": 0},
            "cumulative_usage": dict(session["usage"]),
            "model": model,
            "error": str(e),
        }

    raw_content = response.choices[0].message.content or "{}"
    try:
        parsed = json.loads(raw_content)
    except json.JSONDecodeError:
        logger.warning("AI応答のJSON解析に失敗 (session=%s): %s", session_id, raw_content)
        parsed = {
            "reply": "申し訳ございません、もう一度お願いできますでしょうか。",
            "category": "unclear",
            "urgency": None,
            "end_call": False,
            "summary": None,
        }

    reply_text = parsed.get("reply") or "かしこまりました。"
    end_call = bool(parsed.get("end_call")) or force_end
    if force_end and not parsed.get("summary"):
        parsed["summary"] = "（会話が長引いたため自動終話。手動で内容を確認してください）"

    # 会話履歴にはAIの発話文のみを積む（JSON構造そのものは次ターンの文脈として不要）
    session["messages"].append({"role": "assistant", "content": reply_text})

    usage = response.usage
    turn_usage = {
        "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
    }
    session["usage"]["prompt_tokens"] += turn_usage["prompt_tokens"]
    session["usage"]["completion_tokens"] += turn_usage["completion_tokens"]

    if end_call:
        # ログ用に最終状態を残してからセッションを畳む
        logger.info(
            "通話終了 session=%s turns=%s usage=%s",
            session_id,
            session["turn_count"],
            session["usage"],
        )

    return {
        "reply": reply_text,
        "category": parsed.get("category", "unclear"),
        "urgency": parsed.get("urgency"),
        "end_call": end_call,
        "summary": parsed.get("summary"),
        "turn_usage": turn_usage,
        "cumulative_usage": dict(session["usage"]),
        "model": model,
    }
