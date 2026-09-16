from fastapi import APIRouter, Request, HTTPException
from app.config import get_settings
import vonage
import logging

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/webhook/voice",
    tags=["vonage_voice"]
)

# Vonage クライアント初期化
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

        vonage_client = vonage.Vonage(
            api_key=settings.VONAGE_API_KEY,
            api_secret=settings.VONAGE_API_SECRET,
            application_id=settings.VONAGE_APPLICATION_ID,
            private_key=private_key
        )
        logger.info("✅ Vonage client initialized successfully")
    else:
        logger.info("ℹ️ Vonage credentials not configured in environment")
except Exception as e:
    logger.warning(f"⚠️ Vonage client initialization warning: {e}")


@router.post("/answer")
async def answer_call(request: Request):
    """着信通話を受け入れて、IVR応答を返す"""
    try:
        data = await request.json()
        logger.info(f"Incoming call: {data}")

        # 通話を受け入れて、テキスト音声で応答
        return {
            "action": "talk",
            "text": "ありがとうございます。RECEPTRA へようこそ。",
            "bargeIn": True
        }
    except Exception as e:
        logger.error(f"Error in answer_call: {e}")
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

        if status == "completed":
            logger.info(f"Call completed: {uuid}")
        elif status == "failed":
            logger.warning(f"Call failed: {uuid}")

        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error in handle_events: {e}")
        raise HTTPException(status_code=500, detail=str(e))
