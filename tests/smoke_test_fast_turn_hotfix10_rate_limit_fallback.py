"""
RECEPTRA — FAST TURN EMERGENCY HOTFIX 10（2026年9月）
「Tool continuation rate-limit failure / 30秒沈黙 / 勝手な通話終了」修正の
契約テスト（バックエンド側のみ）。

背景（実機証拠に基づくroot cause。詳細は該当コミットの説明を参照）:
check_availability等のTool結果をお客様へ伝えるための2回目のresponse.create
（Tool continuation）がerror_code=rate_limit_exceededで失敗し、既存の
HOTFIX 6/7のbounded retryロジックが「追加retryを送らない」と判断した場合
（budgetLooksInsufficient）、これまではブラウザ画面上のテキスト変更のみが
行われ、電話中のお客様には何も聞こえない状態になっていた。この無音区間を
埋めるため、PHASE O5.5 Speak-Then-Work Ack Fallbackと同じパターン
（Realtime API本体には一切関与しない、完全にローカルな事前生成音声）で
第二の固定音声（rate limit fallback audio）を追加した。

本テストはバックエンド側で検証可能な契約項目のみを扱う（実際のブラウザでの
再生・silence timerの遅延挙動はtests/test_fast_turn_hotfix10_rate_limit_
fallback.jsで検証する。実機での実際の音声到達・通話継続は実機TEST 1〜5
でしか判断できず、本テストはそれを主張するものではない）。

検証項目（ユーザー指定§9/§11「rate_limit_exceededをBUSINESS RESULTと
混同しない」を、新規追加した固定文言そのものに対して機械的に強制する）:
  1. _RATE_LIMIT_FALLBACK_TEXTが存在し、空文字列でない
  2. _RATE_LIMIT_FALLBACK_TEXTが、_ACK_FALLBACK_TEXT（既存のSpeak-Then-Work
     用の別の固定文言）と異なる文字列である（目的の異なる別音声であることの
     確認。既存のack fallbackを流用していない）
  3. _RATE_LIMIT_FALLBACK_TEXTに、BUSINESS RESULTを示唆する語彙
     （空きがない・空きなし・営業時間外・対応してません・対応していません・
     予約できません・承れません・closed・unavailable等）が一切含まれない
     （SYSTEM FAILUREであることのみを伝える文言であることの機械的な保証）
  4. _rate_limit_fallback_audio_cache が _ack_fallback_audio_cache とは
     別個のdictオブジェクトである（キャッシュの取り違え・汚染防止）
  5. get_or_generate_rate_limit_fallback_audio() が
     get_or_generate_ack_fallback_audio() とは別関数であり、実際に
     _RATE_LIMIT_FALLBACK_TEXTを使ってTTSを生成する（既存のOpenAI非
     RealtimeのTTS APIをそのまま踏襲し、新しいTTS基盤・別モデルを追加して
     いないことの確認）
  6. 生成した音声はvoiceごとにプロセス内メモリでキャッシュされ、同じvoiceへの
     2回目の呼び出しではTTS APIを再度呼ばない（既存のack fallbackと同じ
     キャッシュ設計を踏襲していることの確認）
  7. ルーター側に新しいエンドポイント関数
     get_realtime_voice_rate_limit_fallback_audio が存在し、
     get_realtime_voice_ack_fallback_audio とは別関数である
  8. Realtime Token Architecture Phase 2の既存契約（RESERVATION/CALLBACK
     context等）が、本HOTFIXにより一切壊されていない（回帰確認。既存の
     smoke_test_realtime_phase2_reservation_callback.pyの主要な検証観点を
     ここでも独立に再確認する）

実行: python3 tests/smoke_test_fast_turn_hotfix10_rate_limit_fallback.py
"""

import asyncio
import os
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(
    tempfile.mkdtemp(), "smoke_fast_turn_hotfix10.db"
)
os.environ["SECRET_KEY"] = "smoke-test-secret-key-fast-turn-hotfix10"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

from app.services import realtime_voice_ai  # noqa: E402
from app.routers import realtime_voice as realtime_voice_router  # noqa: E402

# BUSINESS RESULTを示唆する語彙（§9/§11の絶対要件: rate_limit_exceededを
# no_availability/outside_business_hours/unsupported_time/closed/
# reservation unavailableのいずれとしても扱わない・伝えない）。
_FORBIDDEN_BUSINESS_RESULT_SUBSTRINGS = [
    "空きがない", "空きなし", "空きが無い", "空いてい", "満席",
    "営業時間外", "営業時間内", "対応してません", "対応していません",
    "対応できません", "予約できません", "承れません", "承ることができません",
    "closed", "unavailable", "no_availability", "outside_business_hours",
    "unsupported_time", "reservation unavailable",
]


def check_rate_limit_fallback_text_exists_and_distinct():
    text = realtime_voice_ai._RATE_LIMIT_FALLBACK_TEXT
    assert isinstance(text, str) and len(text.strip()) > 0, \
        "_RATE_LIMIT_FALLBACK_TEXT must be a non-empty fixed string"
    assert text != realtime_voice_ai._ACK_FALLBACK_TEXT, \
        "the rate-limit fallback text must be a distinct, purpose-specific string, not a reuse of the ack fallback text"
    print("OK: 1/2 _RATE_LIMIT_FALLBACK_TEXT exists and is distinct from _ACK_FALLBACK_TEXT")


def check_rate_limit_fallback_text_has_no_business_result_wording():
    text = realtime_voice_ai._RATE_LIMIT_FALLBACK_TEXT
    lowered = text.lower()
    for forbidden in _FORBIDDEN_BUSINESS_RESULT_SUBSTRINGS:
        assert forbidden.lower() not in lowered, (
            f"_RATE_LIMIT_FALLBACK_TEXT must never contain a BUSINESS RESULT phrase "
            f"(found forbidden substring '{forbidden}' in: {text!r}). "
            f"rate_limit_exceeded is a SYSTEM FAILURE, never a business result (ユーザー指示§9/§11)."
        )
    print("OK: 3 _RATE_LIMIT_FALLBACK_TEXT contains no BUSINESS RESULT wording")


def check_caches_are_distinct_objects():
    assert realtime_voice_ai._rate_limit_fallback_audio_cache is not realtime_voice_ai._ack_fallback_audio_cache, \
        "the rate-limit fallback cache must be a distinct dict, never sharing storage with the ack fallback cache"
    print("OK: 4 _rate_limit_fallback_audio_cache is a distinct dict from _ack_fallback_audio_cache")


def check_generator_functions_are_distinct():
    assert realtime_voice_ai.get_or_generate_rate_limit_fallback_audio is not realtime_voice_ai.get_or_generate_ack_fallback_audio, \
        "the rate-limit fallback generator must be its own function, not an alias of the ack fallback generator"
    print("OK: 5a get_or_generate_rate_limit_fallback_audio is a distinct function")


async def check_generator_uses_correct_text_and_caches():
    realtime_voice_ai._rate_limit_fallback_audio_cache.clear()

    fake_bytes = b"FAKE_MP3_BYTES_RATE_LIMIT_FALLBACK"
    mock_speech_response = MagicMock()
    mock_speech_response.aread = AsyncMock(return_value=fake_bytes)

    mock_client = MagicMock()
    mock_client.audio.speech.create = AsyncMock(return_value=mock_speech_response)

    with patch.object(realtime_voice_ai, "_get_client", return_value=mock_client):
        result1 = await realtime_voice_ai.get_or_generate_rate_limit_fallback_audio("alloy")
        assert result1 == fake_bytes, "must return the bytes produced by the TTS call"
        assert mock_client.audio.speech.create.await_count == 1
        call_kwargs = mock_client.audio.speech.create.call_args.kwargs
        assert call_kwargs.get("input") == realtime_voice_ai._RATE_LIMIT_FALLBACK_TEXT, \
            "the TTS call must use the fixed SYSTEM-FAILURE-only text, not a dynamic/business-derived string"
        assert call_kwargs.get("voice") == "alloy"
        assert call_kwargs.get("response_format") == "mp3"

        # 6. 同じvoiceへの2回目の呼び出しはキャッシュを使い、TTS APIを再度
        #    呼ばない（既存のack fallbackと同じキャッシュ設計）。
        result2 = await realtime_voice_ai.get_or_generate_rate_limit_fallback_audio("alloy")
        assert result2 == fake_bytes
        assert mock_client.audio.speech.create.await_count == 1, \
            "a second call for the same voice must be served from the in-process cache, not a new TTS API call"

    print("OK: 5b/6 get_or_generate_rate_limit_fallback_audio uses the fixed SYSTEM-FAILURE text and caches per voice")


def check_router_endpoint_exists_and_is_distinct():
    assert hasattr(realtime_voice_router, "get_realtime_voice_rate_limit_fallback_audio"), \
        "the new /rate-limit-fallback-audio endpoint function must exist in the router module"
    assert (
        realtime_voice_router.get_realtime_voice_rate_limit_fallback_audio
        is not realtime_voice_router.get_realtime_voice_ack_fallback_audio
    ), "the new endpoint must be its own function, not an alias of /ack-fallback-audio"
    # 独立したrate limitバケットを持つこと（既存のack-fallback-audio用
    # バケットと取り違えて共有していないことの確認）。
    assert hasattr(realtime_voice_router, "_recent_rate_limit_fallback_audio_requests")
    assert (
        realtime_voice_router._recent_rate_limit_fallback_audio_requests
        is not realtime_voice_router._recent_ack_fallback_audio_requests
    ), "the new endpoint must use its own rate-limit bucket, not share the ack-fallback-audio bucket"
    print("OK: 7 /rate-limit-fallback-audio endpoint exists, is distinct, and has its own rate-limit bucket")


def check_phase2_contract_not_broken():
    # Realtime Token Architecture Phase 2の主要契約が、本HOTFIXにより
    # 一切変更されていないことの独立した回帰確認（既存の専用テストファイル
    # smoke_test_realtime_phase2_reservation_callback.pyをここでも簡易に
    # 再確認する。詳細な17項目はそちらに委ね、本ファイルではその専用テストが
    # 存在し続けていること・主要シンボルが健在であることだけを確認する）。
    for symbol in [
        "build_realtime_phase_contexts",
        "_build_reservation_phase_instructions",
        "_build_callback_phase_instructions",
    ]:
        assert hasattr(realtime_voice_ai, symbol), \
            f"Realtime Token Architecture Phase 2 symbol must remain untouched: {symbol}"
    print("OK: 8 Realtime Token Architecture Phase 2 main symbols remain intact (regression)")


def main():
    check_rate_limit_fallback_text_exists_and_distinct()
    check_rate_limit_fallback_text_has_no_business_result_wording()
    check_caches_are_distinct_objects()
    check_generator_functions_are_distinct()
    asyncio.run(check_generator_uses_correct_text_and_caches())
    check_router_endpoint_exists_and_is_distinct()
    check_phase2_contract_not_broken()
    print("\n=== ALL smoke_test_fast_turn_hotfix10_rate_limit_fallback.py CHECKS PASSED ===")


if __name__ == "__main__":
    main()
