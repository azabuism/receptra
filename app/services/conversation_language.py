"""
Phase 5A: 多言語AI受付 - 現在の会話言語の状態管理

役割:
- OpenAI Realtime      = 「今、明らかに別の言語で話しかけられた／切り替えを
  依頼された」ことの意味判断のみ（app.services.realtime_voice_aiの指示文）
- このモジュール（Python） = set_conversation_language Toolが呼ばれた際の
  「本当にその言語がこの店舗で許可されているか」のサーバー側検証と、
  1通話分の状態保持

設計方針（app.services.customer_contextの本人確認状態の設計を踏襲）:
- 状態は必ず (shop_id, voice_session_id) の組み合わせでbindingする。
  別tab・別通話・別shopへの流用を防ぐため。
- AIがこのToolへ渡せる引数は language_code のみ（1つの文字列）。shop_id・
  voice_session_idはAIの出力JSONには一切含まれず、フロントエンド
  （shop-ai-realtime-voice.html）がRealtimeセッション確立時に保持している
  値を自動的に付与する（仕様書Phase5A section36「shop_idはAIに渡させない」
  と同じ設計）。
- 保存前に必ずapp.language_registryでその店舗のai_supported_languagesに
  含まれるかを検証する。含まれない場合は状態を変更せず失敗を返す
  （AIが許可されていない言語に「切り替えた」ことをサーバー側の記録として
  残さないため。仕様書section35のセキュリティテスト対象）。
- プロセス内メモリ(dict)のみで保持する。理由はapp.services.customer_context
  と全く同じ（Railway本番は単一worker構成であることを確認済み。Phase4Bで
  導入した前提を再利用するのみで、新規インフラは導入しない。仕様書section49
  のSTOP条件「新規インフラの必要性」に抵触しない）。
- server再起動・有効期限切れでこの状態が失われた場合は、単に「まだ
  set_conversation_languageが呼ばれていない」状態に戻るだけであり、
  安全側にフォールバックする（次にget_customer_context等が
  last_conversation_languageを見る際は、CustomerMemory側の前回値
  （ヒントとしてのみ使う）を参照するのみで、当セッションの状態を必須には
  しない設計のため）。
- 有効期限はapp.services.customer_contextの本人確認済み状態と同じ30分
  （MAX_CALL_DURATION_MSと同じ値。1通話の枠内では絶対に失効しない）。
"""

import logging
import time as time_module
from typing import Optional

from app.language_registry import effective_ai_languages, is_valid_language_code

logger = logging.getLogger("receptra.conversation_language")

_SESSION_LANGUAGE_TTL_SECONDS = 1800  # 30分。customer_contextの本人確認済み状態と同じ値。

# key: voice_session_id
_session_languages: dict[str, dict] = {}


def _purge_expired() -> None:
    now = time_module.monotonic()
    expired = [
        k for k, v in _session_languages.items()
        if now - v["set_at"] > _SESSION_LANGUAGE_TTL_SECONDS
    ]
    for k in expired:
        _session_languages.pop(k, None)


def set_session_language(
    shop_id: str,
    voice_session_id: str,
    language_code: str,
    ai_supported_languages_raw,
) -> bool:
    """
    set_conversation_language Toolの中核ロジック。

    language_codeが (a) レジストリに存在する有効なコードであり、かつ
    (b) その店舗の実際に有効なAI対応言語リスト（ai_supported_languages_raw
    をeffective_ai_languagesで正規化した結果）に含まれる場合のみ、状態を
    更新してTrueを返す。それ以外は状態を一切変更せずFalseを返す
    （呼び出し元はエラーにはせず、現在の言語のまま会話を継続する）。
    """
    if not voice_session_id or not shop_id:
        return False
    if not is_valid_language_code(language_code):
        return False

    allowed = effective_ai_languages(ai_supported_languages_raw)
    if language_code not in allowed:
        logger.info(
            "set_conversation_language: 許可されていない言語のため無視しました "
            "shop_id=%s language_code=%s allowed=%s",
            shop_id, language_code, allowed,
        )
        return False

    _purge_expired()
    _session_languages[voice_session_id] = {
        "shop_id": shop_id,
        "language_code": language_code,
        "set_at": time_module.monotonic(),
    }
    return True


def get_session_language(shop_id: str, voice_session_id: str) -> Optional[str]:
    """
    現在の会話言語を取得する。未設定・別店舗・有効期限切れの場合はNone
    （呼び出し元は「不明」として扱い、日本語などへの決め打ちは行わない）。
    """
    if not voice_session_id:
        return None
    _purge_expired()
    state = _session_languages.get(voice_session_id)
    if state is None or state["shop_id"] != shop_id:
        return None
    return state["language_code"]
