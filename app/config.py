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
    ACCESS_TOKEN_EXPIRE_HOURS: int = 24

    # ===== OpenAI設定 =====
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"
    OPENAI_MAX_TOKENS: int = 2048

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

    # ===== 機能フラグ =====
    FEATURE_AI_RESPONSES: bool = True
    FEATURE_PAYMENT_PROCESSING: bool = True

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

