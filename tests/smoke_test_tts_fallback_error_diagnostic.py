"""
RECEPTRA — FAST TURN EMERGENCY DIAGNOSTIC（2026年9月）
「/ack-fallback-audio, /rate-limit-fallback-audio が実機で502を返した」事象
（Safari Consoleでの実機証拠）の調査指示に基づくテスト。

監査で判明した事実（実コード確認）:
  - get_or_generate_ack_fallback_audio() / get_or_generate_rate_limit_
    fallback_audio() は、client.audio.speech.create() が任意の例外を送出した
    場合、呼び出し元のrouter（app/routers/realtime_voice.py）側の
    `except Exception as e: logger.error(...); raise HTTPException(502, ...)`
    により、OpenAI側が実際に返したstatus_code/error.type/error.code/
    error.message・retry-after等のrate limit情報を一切保持せず、
    「502・詳細不明」として握りつぶしていた。
  - 実機スクリーンショットでは、これら2 endpointの502が
    `POST https://api.openai.com/v1/realtime/calls` の429と同時に発生して
    おり、同じOpenAI APIキー・同じプロジェクトのrate limitが根本原因である
    可能性が高い（TTS呼び出しも429で失敗し、それが汎用except節で502に
    変換されているだけ、という仮説）。

今回の修正（診断専用・会話ロジック/HOTFIX10-13・レスポンスコード自体には
一切触れない）:
  両関数のclient.audio.speech.create()呼び出しをtry/exceptで囲み、
  新規ヘルパー_log_tts_fallback_error_diag()で、例外オブジェクトから安全に
  取得できる範囲のstatus_code/error.type/error.code/error.param/
  error.message/retry-after のみを抽出してlogger.error()に記録したうえで、
  例外をそのままraiseし直す（router側の既存のRuntimeError/Exception分岐と
  502レスポンスは一切変更しない）。

本テストが確認する項目:
  1. 正常系: 変更前と同じくTTS生成・キャッシュが動作する（回帰なし）
  2. 異常系: OpenAI風の例外（status_code/body/response.headersを持つ）から
     error.type/code/message/param・retry-afterを正しく抽出してログに残す
  3. 異常系でも例外はそのままraiseされる（router側のHTTPException変換は
     変更されていないことの前提条件）
  4. 属性を持たない素のExceptionでもクラッシュせず、Noneとして安全に扱う
  5. ログにAPIキー・secret・音声内容が一切含まれない
  6. ack fallbackとrate limit fallbackの両方で同じ診断ヘルパーが使われている
     （実装が分岐せず一貫していることの確認）
  7. 診断ヘルパー自体は例外を再送出しない・追加のretryを行わない
     （挙動を変えない診断専用ヘルパーであることの確認）

実行: python3 tests/smoke_test_tts_fallback_error_diagnostic.py
"""

import asyncio
import logging
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import realtime_voice_ai  # noqa: E402


class _FakeOpenAIError(Exception):
    """OpenAI SDKのAPIStatusError系例外がstatus_code/body/responseを持つ形を
    模したテスト用の例外（実際のopenaiパッケージには依存しない）。"""

    def __init__(self, status_code, body, retry_after=None):
        super().__init__("fake openai error")
        self.status_code = status_code
        self.body = body
        if retry_after is not None:
            fake_response = MagicMock()
            fake_response.headers = {"retry-after": retry_after}
            self.response = fake_response


class _ListLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(self.format(record))


async def main():
    # ----- 1. 正常系: 回帰確認 -----
    realtime_voice_ai._ack_fallback_audio_cache.clear()
    realtime_voice_ai._rate_limit_fallback_audio_cache.clear()

    fake_bytes = b"FAKE_MP3_BYTES"
    mock_speech_response = MagicMock()
    mock_speech_response.aread = AsyncMock(return_value=fake_bytes)
    mock_client = MagicMock()
    mock_client.audio.speech.create = AsyncMock(return_value=mock_speech_response)

    with patch.object(realtime_voice_ai, "_get_client", return_value=mock_client):
        result = await realtime_voice_ai.get_or_generate_ack_fallback_audio("alloy")
        assert result == fake_bytes, "正常系: ack fallback音声が期待通り返らない"
        result2 = await realtime_voice_ai.get_or_generate_rate_limit_fallback_audio("alloy")
        assert result2 == fake_bytes, "正常系: rate limit fallback音声が期待通り返らない"
    print("1. 正常系（回帰）: ack/rate-limit fallback共に変更前と同じく生成・キャッシュされる: OK")

    # ----- 2〜3. 異常系: 診断情報抽出 + 例外がそのままraiseされる -----
    realtime_voice_ai._ack_fallback_audio_cache.clear()
    handler = _ListLogHandler()
    realtime_voice_ai.logger.addHandler(handler)
    realtime_voice_ai.logger.setLevel(logging.DEBUG)
    try:
        fake_error = _FakeOpenAIError(
            status_code=429,
            body={
                "error": {
                    "type": "rate_limit_exceeded",
                    "code": "rate_limit_exceeded",
                    "message": "Rate limit reached for TTS requests. Please try again in 3.2s.",
                    "param": None,
                }
            },
            retry_after="4",
        )
        failing_client = MagicMock()
        failing_client.audio.speech.create = AsyncMock(side_effect=fake_error)

        raised = None
        with patch.object(realtime_voice_ai, "_get_client", return_value=failing_client):
            try:
                await realtime_voice_ai.get_or_generate_ack_fallback_audio("alloy")
            except _FakeOpenAIError as e:
                raised = e

        assert raised is fake_error, "異常系: 例外がそのままraiseされていない（router側のHTTPException変換前提が壊れる）"
        assert "alloy" not in realtime_voice_ai._ack_fallback_audio_cache, "異常系: 失敗時にキャッシュへ書き込まれてはいけない"
        print("2. 異常系: 例外はそのままraiseされ、キャッシュも汚染されない: OK")

        joined_logs = "\n".join(handler.records)
        assert "429" in joined_logs, "診断ログにstatus_code(429)が含まれていない"
        assert "rate_limit_exceeded" in joined_logs, "診断ログにerror.type/codeが含まれていない"
        assert "Rate limit reached for TTS requests" in joined_logs, "診断ログにerror.messageが含まれていない"
        assert "retry_after=4" in joined_logs, "診断ログにretry-afterが含まれていない"
        print("3. 異常系: status_code/error.type/error.code/error.message/retry-afterが診断ログに正しく抽出される: OK")
    finally:
        realtime_voice_ai.logger.removeHandler(handler)

    # ----- 4. 属性を持たない素のExceptionでもクラッシュしない -----
    realtime_voice_ai._rate_limit_fallback_audio_cache.clear()
    handler2 = _ListLogHandler()
    realtime_voice_ai.logger.addHandler(handler2)
    try:
        plain_error = RuntimeError("何か普通の例外")
        failing_client2 = MagicMock()
        failing_client2.audio.speech.create = AsyncMock(side_effect=plain_error)
        raised2 = None
        with patch.object(realtime_voice_ai, "_get_client", return_value=failing_client2):
            try:
                await realtime_voice_ai.get_or_generate_rate_limit_fallback_audio("alloy")
            except RuntimeError as e:
                raised2 = e
        assert raised2 is plain_error, "素のExceptionでも例外がそのままraiseされるべき"
        joined_logs2 = "\n".join(handler2.records)
        assert "status_code=None" in joined_logs2, "属性の無い例外ではNoneとして安全にログされるべき"
    finally:
        realtime_voice_ai.logger.removeHandler(handler2)
    print("4. 属性を持たない素のExceptionでもクラッシュせず、Noneとして安全にログされる: OK")

    # ----- 5. ログにAPIキー・secretが含まれない -----
    import inspect
    diag_src = inspect.getsource(realtime_voice_ai._log_tts_fallback_error_diag)
    for forbidden in ("OPENAI_API_KEY", "api_key", "settings.OPENAI_API_KEY"):
        assert forbidden not in diag_src, f"診断ヘルパーがAPIキー関連の値をログに含めようとしている: {forbidden}"
    print("5. 診断ヘルパーのソースにAPIキー/secret関連の参照が一切ない: OK")

    # ----- 6. ack/rate-limit両方が同じヘルパーを使っている -----
    ack_src = inspect.getsource(realtime_voice_ai.get_or_generate_ack_fallback_audio)
    rl_src = inspect.getsource(realtime_voice_ai.get_or_generate_rate_limit_fallback_audio)
    assert "_log_tts_fallback_error_diag(" in ack_src, "ack fallbackが新しい診断ヘルパーを使っていない"
    assert "_log_tts_fallback_error_diag(" in rl_src, "rate limit fallbackが新しい診断ヘルパーを使っていない"
    print("6. ack fallback / rate limit fallback の両方が同じ診断ヘルパーを一貫して使用している: OK")

    # ----- 7. 診断ヘルパー自体はraiseしない（呼び出し元がraiseする設計） -----
    diag_sig = inspect.signature(realtime_voice_ai._log_tts_fallback_error_diag)
    assert not inspect.iscoroutinefunction(realtime_voice_ai._log_tts_fallback_error_diag), \
        "診断ヘルパーは同期関数であるべき（awaitやretryを内部に持たない設計の確認）"
    assert "raise" not in inspect.getsource(realtime_voice_ai._log_tts_fallback_error_diag), \
        "診断ヘルパー自体が例外を送出/再送出してはいけない（呼び出し元のtry/exceptがraiseする設計）"
    print("7. 診断ヘルパーは同期・非raiseで、挙動を変えない純粋な診断専用関数である: OK")

    print()
    print("全テストOK: FAST TURN EMERGENCY DIAGNOSTIC（429/502切り分け用診断ログ追加）")


if __name__ == "__main__":
    asyncio.run(main())
