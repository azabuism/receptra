"""
BARIYON Receptra - Application Configuration
環境変数に基づいた設定管理
"""

from typing import List, Optional
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """アプリケーション設定"""

    # ===== 環境設定 =====
    ENVIRONMENT: str = "development"  # development, staging, production
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"

    # ===== API設定 =====
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "BARIYON Receptra"
    PROJECT_VERSION: str = "1.0.0"

    # ===== サーバー設定 =====
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8000
    ALLOWED_HOSTS: List[str] = ["*"]

    # ===== CORS設定 =====
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:5173",
    ]

    # ===== データベース設定 =====
    DATABASE_URL: str = "postgresql+asyncpg://receptra:receptra@localhost:5432/receptra"
    DATABASE_ECHO: bool = False
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 10

    # ===== Redis設定 =====
    REDIS_URL: str = "redis://localhost:6379/0"

    # ===== JWT設定 =====
    SECRET_KEY: str = "your-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    # オーナー/ユーザーが「更新する度に再ログインが必要」と感じないよう、
    # セッションの有効期限を90日に設定（従来は24時間）。
    # デプロイやサーバー再起動では SECRET_KEY は変わらないため、
    # 既存トークンはこの期限内であれば引き続き有効。
    ACCESS_TOKEN_EXPIRE_HOURS: int = 24 * 90  # 2160時間 = 90日

    # ===== OpenAI設定 =====
    OPENAI_API_KEY: str = ""
    # 注意: 旧デフォルトの "gpt-4o" は2026年にAPI提供が終了したモデル系列のため、
    # 現行の提供モデルに変更。電話AI応答は低遅延・低コストを優先し軽量モデルを既定値とする。
    # 精度が不足する場合は環境変数 OPENAI_MODEL / VOICE_AI_MODEL で上位モデルに切り替え可能。
    OPENAI_MODEL: str = "gpt-5.6-luna"
    OPENAI_MAX_TOKENS: int = 2048

    # ===== 電話AI（受付会話）設定 =====
    # 通話1ターンあたりの応答生成に使うモデル・トークン上限。未設定時はOPENAI_MODELを使用。
    VOICE_AI_MODEL: Optional[str] = None
    VOICE_AI_MAX_TOKENS: int = 300
    # 1通話あたりの最大ターン数（暴走・異常な長時間通話によるコスト増を防ぐ安全装置）
    VOICE_AI_MAX_TURNS: int = 8

    # ===== 店舗ページ AI予約（チャット・通話）設定 =====
    # お客様が店舗ページから直接AIと会話して予約するための設定。
    # 日時・人数・お名前・電話番号などヒアリング項目が多いため、電話受付より
    # ターン数上限をやや大きめに取る。
    BOOKING_AI_MAX_TOKENS: int = 400
    BOOKING_AI_MAX_TURNS: int = 10

    # ===== OpenAI Realtime API（ブラウザ⇔WebRTC直結の音声AI、Phase1〜）=====
    # 音声そのものはブラウザとOpenAIの間をWebRTCで直接流れ、RECEPTRAのサーバーは
    # 経由しない。サーバーは店舗情報からシステムプロンプトを組み立て、短命の
    # ephemeralトークン(client secret)を発行するだけの役割に限定する。
    # 2026年9月時点の現行世代モデル。日本語品質・barge-in性能・コストは
    # Phase1の実機テストで比較検討し、安さだけで決め打ちしない方針。
    OPENAI_REALTIME_MODEL: str = "gpt-realtime-2.1"
    # ephemeralトークンの有効期限（秒）。この秒数以内にブラウザがWebRTC接続を
    # 開始する必要がある（OpenAI仕様上10〜7200秒）。一度接続が始まった通話自体の
    # 継続時間には影響しないため、通話時間の安全装置はフロントエンド側で別途設ける。
    REALTIME_CLIENT_SECRET_TTL_SECONDS: int = 60
    # 会話品質調査（2026年9月）の結果を踏まえた明示設定。環境変数で
    # 上書きできるようにし、コード変更なしでA/Bテストできるようにする。
    # voice: OpenAI公式のWebRTC接続ガイド・TTSガイドがgpt-realtime-2.1との
    # 組み合わせで明示的に推奨している音声（未指定時のデフォルトに頼らない）。
    OPENAI_REALTIME_VOICE: str = "marin"
    # turn_detection.eagerness: semantic_vadの踏み込みの早さ。
    # FAST TURN 2（2026年9月）: 実機テストで「発話終了後の無駄な待ち時間
    # （環境ノイズ等をsemantic_vadが発話継続と誤判定している可能性）」が
    # 報告されたことを受け、auto(=medium)からhighへ変更。OpenAI公式ドキュメント
    # （developers.openai.com/api/docs/guides/realtime-vad）に基づく唯一の
    # 公式チューニングパラメータであり、server_vadへの移行や独自の秒数打ち切り
    # ロジックは一切導入していない。semantic_vad自体の「言葉に基づく」判定方式は
    # 変わらないため、highにしても「まだ話している途中」と判定できる発話を
    # 強制的に打ち切ることはない（判定の踏み込みの速さが変わるのみ）。
    # 環境変数OPENAI_REALTIME_VAD_EAGERNESSで従来のauto/lowにいつでも戻せる。
    OPENAI_REALTIME_VAD_EAGERNESS: str = "high"
    # reasoning.effort: 上げるほどレイテンシ・トークン使用量が増えると
    # 公式ドキュメントに明記されているため、単純な受付対話向けに低めに設定。
    OPENAI_REALTIME_REASONING_EFFORT: str = "low"

    # ===== OpenAI TTS（Phase3C.1: Zero-Wait Greeting PoC用の事前生成音声）=====
    # 通常の会話音声(Realtime API)とは別物。第一声だけを通話開始前に
    # 静的音声として事前生成するために、古典的なテキスト読み上げ
    # (POST /v1/audio/speech)を使う。gpt-4o-mini-ttsはinstructions
    # パラメータに対応し、tts-1/tts-1-hdより自然な音声品質が期待できる
    # （2026年9月時点の判断。将来のモデル更新時は要見直し）。
    OPENAI_TTS_MODEL: str = "gpt-4o-mini-tts"

    # ===== PAY.JP設定 =====
    PAYJP_API_KEY: str = ""
    PAYJP_SECRET_KEY: str = ""

    # ===== Vonage Voice API設定 =====
    VONAGE_API_KEY: str = ""
    VONAGE_API_SECRET: str = ""
    VONAGE_APPLICATION_ID: str = ""
    VONAGE_PRIVATE_KEY_PATH: str = "./vonage_private_key.key"

    # ===== Slack通知設定 =====
    SLACK_WEBHOOK_URL: Optional[str] = None

    # ===== Outbound AI Phase 1: 予約確定通知の架電設定 =====
    # "fake" のみサポート（本フェーズではVonage審査待ちのため実発信は行わない）。
    # 将来 "vonage" を追加する際も、この値を変更するだけで済むよう
    # app.services.outbound_call_provider 側でアダプター化してある。
    OUTBOUND_CALL_PROVIDER: str = "fake"
    # バックグラウンドのジョブポーリングワーカー自体のON/OFF（安全弁）。
    OUTBOUND_CALL_WORKER_ENABLED: bool = True
    OUTBOUND_CALL_POLL_INTERVAL_SECONDS: int = 5
    OUTBOUND_CALL_MAX_ATTEMPTS: int = 3
    # リトライ時のバックオフ秒数（attempt_count回目の失敗後、この値 × attempt_count 秒待つ）。
    OUTBOUND_CALL_RETRY_BACKOFF_SECONDS: int = 30

    # ===== Phase N2: LINE Messaging API設定（Owner通知のLINE配信）=====
    # 値が空文字列（デフォルト）の場合、LINE連携機能全体が安全に無効化される
    # （Webhookは署名検証をスキップして200のみ返し何も処理しない、配信は
    # FakeLineMessageProviderに自動フォールバックする）。他のAPIキー系設定
    # (OPENAI_API_KEY / PAYJP_API_KEY / VONAGE_API_KEY)と全く同じ方針。
    # 値は必ずRailwayの環境変数として設定し、DBやフロントエンド・ログ・
    # 公開APIレスポンスには絶対に含めないこと。
    LINE_CHANNEL_ACCESS_TOKEN: str = ""
    LINE_CHANNEL_SECRET: str = ""
    # Basic ID (LINE ID, 例: "@abcdefg") は友だち追加用に公開する情報であり、
    # Channel ID/Secret/Access Token/userIdとは性質が異なるため、
    # オーナー向けUIへ表示してよい。
    LINE_BOT_BASIC_ID: str = ""

    # LINE通知配信バックグラウンドワーカーのON/OFF（安全弁。
    # OUTBOUND_CALL_WORKER_ENABLEDと同じ設計思想）。
    LINE_NOTIFICATION_WORKER_ENABLED: bool = True
    LINE_NOTIFICATION_WORKER_POLL_INTERVAL_SECONDS: int = 5

    # ===== 機能フラグ =====
    FEATURE_AI_RESPONSES: bool = True
    FEATURE_PAYMENT_PROCESSING: bool = True

    # ===== PHASE O3: Pre-Order Backend Acceptance Engine =====
    # O3ではPublic PreOrder Create APIをBackendとして実装するが、まだ
    # pre-order enabledというOwner設定（O4で追加予定）が存在しないため、
    # このグローバルなfeature gateをデフォルトFalse（無効）としておくことで、
    # O4以前に第三者がAPIを発見して任意店舗へPreOrderを大量作成できる状態を
    # 避ける（仕様書Section11/49）。Railway環境変数で明示的にTrueにしない限り、
    # 本番のPublic Create APIは常に403で無効化されたままになる。
    PRE_ORDER_PUBLIC_CREATE_ENABLED: bool = False

    # ===== テスト設定 =====
    TESTING: bool = False

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


@lru_cache
def get_settings() -> Settings:
    """設定を取得（キャッシュ）"""
    return Settings()

