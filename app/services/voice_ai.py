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
{caller_context_block}
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

BOOKING_SYSTEM_PROMPT_TEMPLATE = """\
あなたは飲食店・サロン等の予約管理システム「RECEPTRA」のAI予約受付です。
お客様がWebサイト上のチャットまたは音声通話で、店舗「{shop_name}」への来店予約を行うお手伝いをします。
実際のスタッフのように丁寧な日本語（敬語）で対応してください。

# 店舗の営業時間（曜日ごと）
{hours_block}

# 本日の日付
{today_str}（「明日」「今週土曜」などの相対的な日時表現はこれを基準に解釈してください）

# あなたの役割
1. お客様との自然な会話で、以下の予約に必要な情報を聞き取ってください（一度に全部聞かず、会話として自然に）:
   - 来店希望日時（日付と時間）
   - 人数
   - お名前
   - 連絡先電話番号
   - （あれば）アレルギーや個室希望などのご要望
2. 営業時間外や定休日を希望された場合は、その場で丁寧に伝えて別の日時を提案してもらってください。
3. 必要な情報がすべて揃い、お客様も内容に同意したら、reply では「確認いたします、少々お待ちください」
   等の一時的な案内をし、ready_to_book を true にしてください。実際に予約が取れるかどうかの最終判定と
   お客様への最終案内は、システム側が別途行います（あなたの reply 本文はこの時点では表示されません）。
4. 世間話や予約以外の簡単な質問（雰囲気、定休日など）には答えて構いませんが、このプロンプトに無い情報
   （メニュー詳細・料金など）を聞かれた場合は「詳しくは店舗に直接お問い合わせください」と案内してください。
5. お客様が予約する意思がない、または用件が終わった場合は end_call を true にしてください。

# 出力形式
必ず次のキーを持つJSONオブジェクトのみを出力してください（説明文や前後の文章は不要）:
{{
  "reply": "お客様に表示する返答文（日本語、簡潔に、1〜2文程度）",
  "ready_to_book": true | false,
  "reservation_date": "YYYY-MM-DDTHH:MM:SS形式の来店希望日時。情報が揃っていなければnull",
  "number_of_people": 人数（整数）。不明ならnull,
  "guest_name": "お客様のお名前。不明ならnull",
  "guest_phone": "連絡先電話番号。不明ならnull",
  "special_requests": "特別なご要望。なければnull",
  "end_call": true | false
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
#                "started_at": datetime, "phone_number": str|None, "tenant_id": str|None}
_sessions: dict[str, dict] = {}


def _build_caller_context_block(caller_context: Optional[str]) -> str:
    if not caller_context:
        return ""
    return (
        "\n# このお客様の過去の通話履歴（当店記録より、新しい順）\n"
        f"{caller_context}\n"
        "上記の履歴があるお客様です。最初の応答で「いつもありがとうございます」等、"
        "常連のお客様への一言を自然に添えてください（馴れ馴れしくなりすぎず、簡潔に）。\n"
    )


def _new_session(
    shop_name: str,
    caller_context: Optional[str] = None,
    phone_number: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> dict:
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        shop_name=shop_name,
        caller_context_block=_build_caller_context_block(caller_context),
    )
    return {
        "messages": [{"role": "system", "content": system_prompt}],
        "shop_name": shop_name,
        "turn_count": 0,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        "started_at": datetime.now(timezone.utc),
        "phone_number": phone_number,
        "tenant_id": tenant_id,
        # AIが明示的に end_call=true を返す前に通話が打ち切られた場合
        # (発信者が途中で切った、テストページでリセットされた等)でも
        # それまでの直近の分類結果からログを残せるようにするための記録。
        "last_category": None,
        "last_urgency": None,
        "last_summary": None,
    }


def start_session(
    session_id: str,
    shop_name: str = "当店",
    caller_context: Optional[str] = None,
    phone_number: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> dict:
    """通話開始時にセッションを初期化（既存があれば上書きしない）"""
    if session_id not in _sessions:
        _sessions[session_id] = _new_session(shop_name, caller_context, phone_number, tenant_id)
    return _sessions[session_id]


def end_session(session_id: str) -> Optional[dict]:
    """通話終了時にセッションを破棄し、最終状態を返す（ログ用）"""
    return _sessions.pop(session_id, None)


def override_last_assistant_message(session_id: str, new_content: str) -> None:
    """
    直前のAI発話をシステム側で生成した確定的な文言に差し替える。
    店舗ページのAI予約で、AIのJSON応答の reply はまだお客様に見せず、
    実際の予約作成（空き状況チェック等）の結果を踏まえてこちらで組み立てた
    案内文をその代わりに表示・履歴として残すために使う。
    """
    session = _sessions.get(session_id)
    if not session:
        return
    for msg in reversed(session["messages"]):
        if msg["role"] == "assistant":
            msg["content"] = new_content
            break


def build_fallback_log_fields(session_data: dict) -> dict:
    """
    AIが自発的に end_call=true で会話を締めくくる前に通話が終わった場合
    (発信者が途中で電話を切った、テストページで「新しい通話を開始」を
    押してリセットされた等)でも、それまでの会話で分かっている直近の
    分類結果を使って、次回のために最低限のログを残すためのフォールバック値。
    """
    return {
        "category": session_data.get("last_category") or "unclear",
        "urgency": session_data.get("last_urgency"),
        "summary": session_data.get("last_summary")
        or "（通話が途中で終了したため要約なし。必要に応じて内容をご確認ください）",
    }


_CATEGORY_LABELS = {
    "reservation": "予約関連",
    "business_call": "営業電話",
    "unclear": "内容不明",
}


async def get_caller_context(db, phone_number: str, tenant_id: Optional[str], limit: int = 3) -> Optional[str]:
    """
    指定の電話番号(・店舗)について、過去の通話ログを新しい順に取得し、
    システムプロンプトに埋め込むための短い要約テキストを組み立てる。
    履歴が無ければ None を返す（＝通常の初回応対になる）。
    """
    from sqlalchemy import select
    from app.models.voice_call_log import VoiceCallLog

    if not phone_number:
        return None

    query = select(VoiceCallLog).where(VoiceCallLog.phone_number == phone_number)
    query = query.where(VoiceCallLog.tenant_id == tenant_id) if tenant_id is not None else query.where(
        VoiceCallLog.tenant_id.is_(None)
    )
    query = query.order_by(VoiceCallLog.created_at.desc()).limit(limit)

    result = await db.execute(query)
    logs = list(result.scalars().all())
    if not logs:
        return None

    lines = []
    for log in reversed(logs):  # 古い順に並べ直して自然な時系列にする
        date_str = log.created_at.strftime("%m/%d")
        label = _CATEGORY_LABELS.get(log.category, log.category or "不明")
        lines.append(f"・{date_str} {label}: {log.summary or '(要約なし)'}")
    return "\n".join(lines)


async def save_call_log(
    db,
    phone_number: Optional[str],
    tenant_id: Optional[str],
    category: Optional[str],
    urgency: Optional[str],
    summary: Optional[str],
) -> None:
    """通話終了時に、次回の来店・架電時に参照できるよう要約をDBへ保存する"""
    from app.models.voice_call_log import VoiceCallLog

    if not phone_number:
        return
    try:
        log = VoiceCallLog(
            phone_number=phone_number,
            tenant_id=tenant_id,
            category=category,
            urgency=urgency,
            summary=summary,
        )
        db.add(log)
        await db.commit()
    except Exception as e:
        logger.error("通話ログの保存に失敗 (phone=%s): %s", phone_number, e)
        await db.rollback()


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

    # 通話が途中で打ち切られた場合のフォールバックログ用に、直近の分類結果を記録しておく
    session["last_category"] = parsed.get("category", "unclear")
    session["last_urgency"] = parsed.get("urgency")
    if parsed.get("summary"):
        session["last_summary"] = parsed.get("summary")

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


# ===== 店舗ページ AI予約（チャット・通話で予約） =====
# お客様が店舗ページから直接AIと会話して来店予約を行うための会話ロジック。
# 電話受付AI（get_ai_reply）とは出力スキーマが異なる（予約に必要な項目を
# 構造化して受け取る）ため、別関数として実装する。


def _new_booking_session(shop_name: str, hours_block: str, today_str: str, shop_id: str) -> dict:
    system_prompt = BOOKING_SYSTEM_PROMPT_TEMPLATE.format(
        shop_name=shop_name,
        hours_block=hours_block,
        today_str=today_str,
    )
    return {
        "messages": [{"role": "system", "content": system_prompt}],
        "shop_id": shop_id,
        "shop_name": shop_name,
        "turn_count": 0,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        "started_at": datetime.now(timezone.utc),
        "booked": False,
    }


def start_booking_session(session_id: str, shop_name: str, hours_block: str, today_str: str, shop_id: str) -> dict:
    """お客様がAI予約チャット/通話を開始した際にセッションを初期化する"""
    if session_id not in _sessions:
        _sessions[session_id] = _new_booking_session(shop_name, hours_block, today_str, shop_id)
    return _sessions[session_id]


async def get_booking_ai_reply(session_id: str, user_text: str) -> dict:
    """
    お客様の発話（チャット入力 or 音声認識結果）を受け取り、AI予約受付の
    次の応答を生成する。

    戻り値:
      {
        "reply": str, "ready_to_book": bool,
        "reservation_date": str|None, "number_of_people": int|None,
        "guest_name": str|None, "guest_phone": str|None,
        "special_requests": str|None, "end_call": bool,
        "turn_usage": {...}, "cumulative_usage": {...}, "model": str,
      }
    """
    settings = get_settings()
    session = _sessions.setdefault(
        session_id, _new_booking_session("当店", "（営業時間情報なし）", "", "")
    )
    model = _resolve_model()

    session["messages"].append({"role": "user", "content": user_text})
    session["turn_count"] += 1

    # 安全装置: 想定外に会話が長引いた場合は強制的に終話させる
    force_end = session["turn_count"] >= settings.BOOKING_AI_MAX_TURNS

    client = _get_client()
    try:
        response = await _create_completion(client, model, session["messages"], settings.BOOKING_AI_MAX_TOKENS)
    except Exception as e:
        logger.error("OpenAI呼び出しエラー (booking session=%s): %s", session_id, e)
        return {
            "reply": "申し訳ございません、只今システムが混み合っております。恐れ入りますが、後ほどお店まで直接お電話いただけますでしょうか。",
            "ready_to_book": False,
            "reservation_date": None,
            "number_of_people": None,
            "guest_name": None,
            "guest_phone": None,
            "special_requests": None,
            "end_call": True,
            "turn_usage": {"prompt_tokens": 0, "completion_tokens": 0},
            "cumulative_usage": dict(session["usage"]),
            "model": model,
            "error": str(e),
        }

    raw_content = response.choices[0].message.content or "{}"
    try:
        parsed = json.loads(raw_content)
    except json.JSONDecodeError:
        logger.warning("AI予約応答のJSON解析に失敗 (session=%s): %s", session_id, raw_content)
        parsed = {
            "reply": "申し訳ございません、もう一度お願いできますでしょうか。",
            "ready_to_book": False,
            "reservation_date": None,
            "number_of_people": None,
            "guest_name": None,
            "guest_phone": None,
            "special_requests": None,
            "end_call": False,
        }

    reply_text = parsed.get("reply") or "かしこまりました。"
    end_call = bool(parsed.get("end_call")) or force_end
    ready_to_book = bool(parsed.get("ready_to_book")) and not session.get("booked")

    # 会話履歴にはAIの発話文のみを積む（呼び出し元が予約結果に応じて上書きする場合がある）
    session["messages"].append({"role": "assistant", "content": reply_text})

    usage = response.usage
    turn_usage = {
        "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
    }
    session["usage"]["prompt_tokens"] += turn_usage["prompt_tokens"]
    session["usage"]["completion_tokens"] += turn_usage["completion_tokens"]

    return {
        "reply": reply_text,
        "ready_to_book": ready_to_book,
        "reservation_date": parsed.get("reservation_date"),
        "number_of_people": parsed.get("number_of_people"),
        "guest_name": parsed.get("guest_name"),
        "guest_phone": parsed.get("guest_phone"),
        "special_requests": parsed.get("special_requests"),
        "end_call": end_call,
        "turn_usage": turn_usage,
        "cumulative_usage": dict(session["usage"]),
        "model": model,
    }


def mark_session_booked(session_id: str) -> None:
    """予約が成立したセッションに印を付け、二重に予約作成が走らないようにする"""
    session = _sessions.get(session_id)
    if session:
        session["booked"] = True
