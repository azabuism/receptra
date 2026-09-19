from fastapi import APIRouter, Request, HTTPException, Depends
from app.config import get_settings
from app.deps import get_current_tenant
from app.models.user import Tenant
from app.services import voice_ai
from vonage import Vonage, Auth
import logging

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/webhook/voice",
    tags=["vonage_voice"]
)

# 電話番号ごとの店舗振り分けは未実装（現状は単一のVonage番号を想定）。
# 複数店舗が個別の番号を持つようになったら、着信の `to` 番号から
# 店舗(Shop)を引いて shop_name に反映する処理をここに追加する。
DEFAULT_SHOP_NAME = "当店"

# Vonage 音声認識(ASR)・読み上げ(TTS)の言語コード
VOICE_LANGUAGE = "ja-JP"

# Vonage クライアント初期化 (Vonage 4.x API format)
vonage_client = None
try:
    settings = get_settings()
    if settings.VONAGE_API_KEY and settings.VONAGE_API_SECRET:
        private_key = None
        if settings.VONAGE_APPLICATION_ID:
            try:
                with open(settings.VONAGE_PRIVATE_KEY_PATH) as f:
                    private_key = f.read()
            except FileNotFoundError:
                logger.warning(f"⚠️ Private key file not found: {settings.VONAGE_PRIVATE_KEY_PATH}")

        # Vonage 4.x uses Auth() wrapper for credentials
        auth = Auth(
            api_key=settings.VONAGE_API_KEY,
            api_secret=settings.VONAGE_API_SECRET
        )

        vonage_client = Vonage(auth)
        logger.info("✅ Vonage client initialized successfully (Vonage 4.x)")
    else:
        logger.info("ℹ️ Vonage credentials not configured in environment")
except Exception as e:
    logger.warning(f"⚠️ Vonage client initialization warning: {e}")


def _speech_input_action(event_url: str) -> dict:
    return {
        "action": "input",
        "type": ["speech"],
        "speech": {
            "language": VOICE_LANGUAGE,
            "endOnSilence": 1.5,
            "startTimeout": 8,
            "maxDuration": 30,
        },
        "eventUrl": [event_url],
        "eventMethod": "POST",
    }


def _base_url(request: Request) -> str:
    # Railway等のプロキシ経由でも正しいhttps URLを組み立てる
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    return f"{scheme}://{host}"


@router.post("/answer")
async def answer_call(request: Request):
    """着信通話を受け入れて、AI受付との会話を開始するNCCOを返す"""
    try:
        data = await request.json()
        logger.info(f"Incoming call: {data}")

        conversation_uuid = data.get("conversation_uuid") or data.get("uuid")
        if conversation_uuid:
            voice_ai.start_session(conversation_uuid, shop_name=DEFAULT_SHOP_NAME)

        event_url = f"{_base_url(request)}/webhook/voice/speech"

        # NCCOはJSON「配列」で返す必要がある(オブジェクト単体では動作しない)
        ncco = [
            {
                "action": "talk",
                "text": f"お電話ありがとうございます。{DEFAULT_SHOP_NAME}です。ご用件をどうぞ。",
                "language": VOICE_LANGUAGE,
                "bargeIn": True,
            },
            _speech_input_action(event_url),
        ]
        return ncco
    except Exception as e:
        logger.error(f"Error in answer_call: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/speech")
async def handle_speech(request: Request):
    """音声認識結果を受け取り、AI受付の次の応答(NCCO)を返す"""
    try:
        data = await request.json()
        logger.info(f"Speech input event: {data}")

        conversation_uuid = data.get("conversation_uuid") or data.get("uuid")
        speech = data.get("speech") or {}
        results = speech.get("results") or []

        event_url = f"{_base_url(request)}/webhook/voice/speech"

        if not results:
            # 聞き取れなかった場合は聞き返して、再度音声入力を待つ
            ncco = [
                {
                    "action": "talk",
                    "text": "申し訳ございません、聞き取れませんでした。もう一度お願いいたします。",
                    "language": VOICE_LANGUAGE,
                    "bargeIn": True,
                },
                _speech_input_action(event_url),
            ]
            return ncco

        user_text = results[0].get("text", "")
        ai_result = await voice_ai.get_ai_reply(
            session_id=conversation_uuid or "unknown",
            user_text=user_text,
            shop_name=DEFAULT_SHOP_NAME,
        )

        ncco = [
            {
                "action": "talk",
                "text": ai_result["reply"],
                "language": VOICE_LANGUAGE,
                "bargeIn": not ai_result["end_call"],
            }
        ]
        if ai_result["end_call"]:
            if conversation_uuid:
                voice_ai.end_session(conversation_uuid)
            # NOTE: 現状は最後のtalkの後、明示的な通話切断は行っていない
            # (発信者側の切断待ち)。Vonage Call Control API
            # (PUT /v1/calls/{uuid} action=hangup) を使った能動的な
            # 切断は今後の改善事項。
        else:
            ncco.append(_speech_input_action(event_url))

        return ncco
    except Exception as e:
        logger.error(f"Error in handle_speech: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/events")
async def handle_events(request: Request):
    """通話イベントの処理（接続、切断など）"""
    try:
        data = await request.json()
        logger.info(f"Voice event: {data}")

        # イベント種類で処理を分岐
        status = data.get("status")
        uuid = data.get("uuid")
        conversation_uuid = data.get("conversation_uuid")

        if status in ("completed", "failed", "rejected", "busy", "cancelled"):
            logger.info(f"Call ended (status={status}): {uuid}")
            # 通話終了時にセッションが残っていれば掃除しておく
            for key in (conversation_uuid, uuid):
                if key:
                    voice_ai.end_session(key)

        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error in handle_events: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ===== テスト・検証用エンドポイント =====
# 実際のVonage電話番号の承認が下りる前に、AI会話ロジックの精度と
# トークン消費量を確認するためのシミュレーションAPI。
# オーナー認証必須（実際のOpenAI APIを呼び出し、料金が発生するため）。

test_router = APIRouter(prefix="/api/v1/voice-ai", tags=["voice_ai_test"])

SAMPLE_SCENARIOS: dict[str, list[str]] = {
    "reservation_confirm": [
        "もしもし、明日の19時に予約している田中と申しますが、お伺いできるか確認のお電話です。",
        "はい、19時に4名で伺います。よろしくお願いします。",
        "はい、大丈夫です。ありがとうございます。",
    ],
    "reservation_cancel": [
        "すみません、今週土曜19時に予約していた佐藤ですが、急用でキャンセルさせてください。",
        "はい、また改めて予約させていただきます。すみませんでした。",
    ],
    "business_call": [
        "お世話になっております、株式会社サンプルシステムズの鈴木と申します。御社の予約管理システムの件でご案内のお電話をさせていただきました。",
        "はい、予約集客を増やすためのWeb広告運用サービスのご案内です。担当者様にお繋ぎいただくことは可能でしょうか。",
        "かしこまりました、資料をお送りしますのでよろしくお願いいたします。",
    ],
}


@test_router.get("/simulate/scenarios")
async def list_scenarios():
    """用意されているテストシナリオの一覧"""
    return {"scenarios": list(SAMPLE_SCENARIOS.keys())}


@test_router.post("/simulate")
async def simulate_conversation(
    request: Request,
    tenant: Tenant = Depends(get_current_tenant),
):
    """
    電話AI会話ロジックを、実際の電話番号なしでテストする。

    リクエストボディ:
      {
        "scenario": "reservation_confirm" | "reservation_cancel" | "business_call",
        "utterances": ["自由入力の発話1", "発話2", ...],  // scenario指定時は省略可
        "model": "gpt-5.6-luna"  // 省略時はVOICE_AI_MODEL/OPENAI_MODELの設定値
        "shop_name": "任意の店舗名"  // 省略時は「当店」
      }

    実際にOpenAI APIを呼び出すため、わずかに課金が発生する点に注意。
    """
    body = await request.json()
    scenario_key = body.get("scenario")
    utterances = body.get("utterances")
    shop_name = body.get("shop_name") or DEFAULT_SHOP_NAME
    model_override = body.get("model")

    if not utterances:
        if scenario_key not in SAMPLE_SCENARIOS:
            raise HTTPException(
                status_code=400,
                detail=f"scenario は {list(SAMPLE_SCENARIOS.keys())} のいずれか、または utterances を指定してください",
            )
        utterances = SAMPLE_SCENARIOS[scenario_key]

    settings = get_settings()
    original_model_override = settings.VOICE_AI_MODEL
    if model_override:
        settings.VOICE_AI_MODEL = model_override

    session_id = f"sim-{tenant.id}-{id(body)}"
    voice_ai.start_session(session_id, shop_name=shop_name)

    turns = []
    try:
        for i, utterance in enumerate(utterances, start=1):
            result = await voice_ai.get_ai_reply(session_id, utterance, shop_name=shop_name)
            turns.append(
                {
                    "turn": i,
                    "caller_said": utterance,
                    "ai_reply": result["reply"],
                    "category": result["category"],
                    "urgency": result["urgency"],
                    "end_call": result["end_call"],
                    "summary": result["summary"],
                    "turn_usage": result["turn_usage"],
                }
            )
            if result["end_call"]:
                break
    finally:
        voice_ai.end_session(session_id)
        settings.VOICE_AI_MODEL = original_model_override

    # ターンごとの使用量を直接合算する（セッション内部の集計値には依存しない、単一の情報源）
    cumulative_usage = {
        "prompt_tokens": sum(t["turn_usage"]["prompt_tokens"] for t in turns),
        "completion_tokens": sum(t["turn_usage"]["completion_tokens"] for t in turns),
    }
    model_used = model_override or settings.VOICE_AI_MODEL or settings.OPENAI_MODEL
    cost_usd = voice_ai.estimate_cost_usd(cumulative_usage, model_used)

    return {
        "model": model_used,
        "shop_name": shop_name,
        "turns": turns,
        "total_turns": len(turns),
        "cumulative_usage": cumulative_usage,
        "total_tokens": cumulative_usage["prompt_tokens"] + cumulative_usage["completion_tokens"],
        "estimated_cost_usd": cost_usd,
        "estimated_cost_jpy": round(cost_usd * voice_ai.USD_TO_JPY_APPROX, 2) if cost_usd is not None else None,
        "note": "円換算は概算レート(1USD≈157円, 2026年9月時点)によるものです。正確な料金はOpenAIの請求ダッシュボードを確認してください。",
    }
