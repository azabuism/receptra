"""
AIスタッフ設定（Realtime Voice AI Phase2）スキーマ定義
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator

# Realtime API用voice一覧は app.services.realtime_voice_ai 側で一元管理する
# （テキスト読み上げ /audio/speech のvoice一覧とは別物。SDK/API仕様変更時に
# 再確認が必要なため、実体は1箇所にのみ定義する）。
from app.services.realtime_voice_ai import REALTIME_VOICES

_ALLOWED_POLITENESS = {"formal", "standard", "casual"}
_ALLOWED_BRIGHTNESS = {"bright", "neutral", "calm"}
_ALLOWED_ENERGY = {"high", "medium", "low"}
_ALLOWED_PRESETS = {"polite", "friendly", "luxury", "energetic", "calm", "custom"}


class AIStaffSettingsUpdateRequest(BaseModel):
    """AIスタッフ設定 保存/更新リクエスト（送られたフィールドのみ更新）"""

    staff_name: Optional[str] = Field(None, max_length=100, description="AIスタッフの名前")
    voice: Optional[str] = Field(None, description="Realtime API用voice")
    personality_preset: Optional[str] = Field(None, description="接客スタイルのプリセット名")
    politeness_level: Optional[str] = Field(None, description="敬語レベル: formal/standard/casual")
    brightness: Optional[str] = Field(None, description="明るさ: bright/neutral/calm")
    energy_level: Optional[str] = Field(None, description="テンション: high/medium/low")
    greeting: Optional[str] = Field(None, max_length=2000, description="電話に出たときの第一声")
    custom_instructions: Optional[str] = Field(None, max_length=4000, description="店舗独自の補助的な接客指示")
    ask_visit_reason_enabled: Optional[bool] = Field(
        None, description="予約時に来店理由を確認するかどうか"
    )

    @field_validator("voice")
    @classmethod
    def _validate_voice(cls, v):
        if v is None or v == "":
            return None
        if v not in REALTIME_VOICES:
            raise ValueError(f"voiceは次のいずれかを指定してください: {', '.join(REALTIME_VOICES)}")
        return v

    @field_validator("personality_preset")
    @classmethod
    def _validate_preset(cls, v):
        if v is None or v == "":
            return None
        if v not in _ALLOWED_PRESETS:
            raise ValueError(f"personality_presetは次のいずれかを指定してください: {', '.join(sorted(_ALLOWED_PRESETS))}")
        return v

    @field_validator("politeness_level")
    @classmethod
    def _validate_politeness(cls, v):
        if v is None or v == "":
            return None
        if v not in _ALLOWED_POLITENESS:
            raise ValueError(f"politeness_levelは次のいずれかを指定してください: {', '.join(sorted(_ALLOWED_POLITENESS))}")
        return v

    @field_validator("brightness")
    @classmethod
    def _validate_brightness(cls, v):
        if v is None or v == "":
            return None
        if v not in _ALLOWED_BRIGHTNESS:
            raise ValueError(f"brightnessは次のいずれかを指定してください: {', '.join(sorted(_ALLOWED_BRIGHTNESS))}")
        return v

    @field_validator("energy_level")
    @classmethod
    def _validate_energy(cls, v):
        if v is None or v == "":
            return None
        if v not in _ALLOWED_ENERGY:
            raise ValueError(f"energy_levelは次のいずれかを指定してください: {', '.join(sorted(_ALLOWED_ENERGY))}")
        return v


class AIStaffSettingsResponse(BaseModel):
    """AIスタッフ設定 レスポンス（未設定の場合はデフォルト値としてNoneが返る）"""

    shop_id: str
    staff_name: Optional[str] = None
    voice: Optional[str] = None
    personality_preset: Optional[str] = None
    politeness_level: Optional[str] = None
    brightness: Optional[str] = None
    energy_level: Optional[str] = None
    greeting: Optional[str] = None
    custom_instructions: Optional[str] = None
    ask_visit_reason_enabled: bool = False
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class VoicePreviewRequest(BaseModel):
    """voice試聴リクエスト"""

    voice: str = Field(..., description="試聴するRealtime API用voice")

    @field_validator("voice")
    @classmethod
    def _validate_voice(cls, v):
        if v not in REALTIME_VOICES:
            raise ValueError(f"voiceは次のいずれかを指定してください: {', '.join(REALTIME_VOICES)}")
        return v
