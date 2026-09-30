"""
RECEPTRA — PHONE CAPTURE DIAGNOSTIC PHASE 1（2026年9月）
input_audio_transcription 診断専用追加の静的契約テスト。

## 背景
「RECEPTRA — CRITICAL PHONE NUMBER HEARING / CAPTURE AUDIT」で、customer_phone
はOpenAI Realtimeモデルの内部音声理解結果がfunction_call引数として直接
生成されるのみで（Option A）、input_audio_transcriptionが未設定のため
独立した文字起こしが一切存在せず、既存の10/11桁guardは「桁数」しか検証
できず「桁の値そのものの正しさ」（例: 08039644468→08039644486のような
同じ桁数だが数字が違う誤認識）を検出する手段がどこにも無いことが判明した。

## 今回実装した内容（Phase 1・診断専用）
1. バックエンド(app/services/realtime_voice_ai.py)のsession_configに、
   OpenAI公式SDK(openai==1.109.1)の型定義
   (openai.types.realtime.realtime_audio_config_input_param.
   RealtimeAudioConfigInputParam.transcription: AudioTranscriptionParam)を
   直接確認した上で、audio.input.transcription = {"model": ...} を追加した
   （環境変数OPENAI_REALTIME_TRANSCRIPTION_DIAGNOSTIC_ENABLEDでいつでも
   無効化できる条件付き追加）。
2. フロントエンド(frontend/public/js/realtime-voice-engine.js)に、
   conversation.item.input_audio_transcription.completed / .failed
   イベントのハンドラを新設し、transcript本文を一切ログに出さない
   PII-freeなPHONE_TRANSCRIPTION_TRACE（turnSeq・phase・文字数・
   digit-likeカウント・タイミング相関のみ）だけをconsole.log/
   pushTimelineEventへ出力する。
3. 既存の request_callback 正常系ログ（従来はJSON.stringify(args)を
   そのままlogEventへ出しておりcustomer_phone/customer_nameが画面ログに
   残っていた既存のPII leak）を、真偽値のみのログへ置換した。

## 本Phase 1で絶対にやっていないこと（実装STOP対象・重要）
- transcriptからcustomer_phoneを生成する処理
- transcriptとrequest_callback引数(customer_phone)の突合・比較
- 突合結果によるCALLBACKのブロック
- 決定論的な電話番号パーサー
- phone capture専用のstate machine
- VAD設定（turn_detection.eagerness等）の変更
- CALLBACK flow・PHONE readbackフォーマット・PHONE confirmation gateの変更

## 本テストの限界（誠実な明記）
本テストは実際のOpenAI Realtime APIを呼び出さない、backendが生成する
session configの静的検証と、frontendソースに対する静的な文字列/構造
検証である。「実機でconversation.item.input_audio_transcription.completed
イベントが実際に届くか」「transcriptの精度」は実機テストでのみ判断可能で
あり、本テストはそれを主張するものではない。

実行: python3 tests/smoke_test_input_audio_transcription_diagnostic.py
"""
import asyncio
import os
import re
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_transcription_diag.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-transcription-diag"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402

_JS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "frontend", "public", "js", "realtime-voice-engine.js",
)

_BLOCK_BOUNDARY_PAUSE_PHRASE = "ブロックとブロックの"
_DIGIT_COUNT_GUARD_PHRASE = "先頭の0を含めて10桁または11桁"
_DIGIT_MAPPING_PHRASE = "4=ヨン"
_CALLBACK_FINAL_PHRASE = "担当者から折り返し連絡しますので、電話を切ってお待ちください。"


class _FakeSecret:
    value = "ek_fake_test_secret"
    expires_at = 9999999999


def _make_fake_client(captured):
    async def fake_create(**kwargs):
        captured["session"] = kwargs.get("session")
        return _FakeSecret()

    fake_client_secrets = MagicMock()
    fake_client_secrets.create = AsyncMock(side_effect=fake_create)
    fake_realtime = MagicMock()
    fake_realtime.client_secrets = fake_client_secrets
    fake_openai_client = MagicMock()
    fake_openai_client.realtime = fake_realtime
    return fake_openai_client


def _extract_transcription_handler_block(js_source: str) -> str:
    """conversation.item.input_audio_transcription.*ハンドラのブロックだけを
    切り出す（他の既存ブロックを誤って検査対象にしないため）。"""
    start_marker = "} else if (type === 'conversation.item.input_audio_transcription.completed'"
    end_marker = "} else if (type === 'response.created') {"
    start = js_source.index(start_marker)
    end = js_source.index(end_marker, start)
    return js_source[start:end]


async def main():
    from app.config import get_settings
    from app.main import app, lifespan
    from app.models.shop import Shop

    async with lifespan(app):
        from app.database import AsyncSessionLocal
        from app.services import realtime_voice_ai

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-transcription-diag@example.com",
                "password": "password123",
                "display_name": "TranscriptionDiagテストオーナー",
            })
            assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
            owner_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "TranscriptionDiagテスト店", "category": "レストラン",
                "address": "東京都渋谷区8-8-8",
            }, headers=owner_headers)
            assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
            shop_id = r.json()["shop_id"]

            async with AsyncSessionLocal() as db_session:
                shop = await db_session.get(Shop, shop_id)
                assert shop is not None

                settings = get_settings()

                captured = {}
                fake_client = _make_fake_client(captured)
                with patch("app.services.realtime_voice_ai._get_client", return_value=fake_client):
                    result = await realtime_voice_ai.create_realtime_session(db_session, shop)
                session = captured.get("session")
                assert session is not None, "session_configがclient_secrets.create()へ渡されていません"
                phase_contexts = result.get("realtime_phase_contexts") or {}

                # ===== TEST A: session.audio.input.transcriptionが存在する =====
                assert "audio" in session and "input" in session["audio"], (
                    "session_configにaudio.inputが存在しません（既存構造が壊れている可能性）"
                )
                assert settings.OPENAI_REALTIME_TRANSCRIPTION_DIAGNOSTIC_ENABLED is True, (
                    "デフォルトでOPENAI_REALTIME_TRANSCRIPTION_DIAGNOSTIC_ENABLEDがTrueで"
                    "はありません（Phase 1の既定は有効であるべき）"
                )
                assert "transcription" in session["audio"]["input"], (
                    "session.audio.input.transcriptionが設定されていません"
                )
                print("TEST A. session.audio.input.transcriptionが実際のsession_configに"
                      "存在する: OK")

                # ===== TEST B: 正しいtranscription modelが使われている
                # （OpenAI公式SDK openai==1.109.1 の AudioTranscriptionParam
                # 型定義で確認済みの選択肢の1つであることを確認） =====
                transcription_cfg = session["audio"]["input"]["transcription"]
                valid_models = {"whisper-1", "gpt-4o-transcribe-latest", "gpt-4o-mini-transcribe", "gpt-4o-transcribe"}
                assert transcription_cfg.get("model") in valid_models, (
                    f"transcription.modelが不正: {transcription_cfg.get('model')!r} "
                    f"(有効な選択肢: {valid_models})"
                )
                assert transcription_cfg.get("model") == settings.OPENAI_REALTIME_TRANSCRIPTION_MODEL
                # turn_detection/voice等、既存のaudio config構造がそのまま維持されている
                assert session["audio"]["input"]["turn_detection"]["type"] == "semantic_vad", (
                    "turn_detectionが変更されています（REALTIME CONTROL FREEZE違反の可能性）"
                )
                assert "voice" in session["audio"]["output"], "audio.output.voiceが失われています"
                print(f"TEST B. transcription.model={transcription_cfg.get('model')!r} が有効な"
                      "選択肢であり、既存のturn_detection/voice設定は変更されていない: OK")

                # ===== TEST C/D: フロントエンドにcompleted/failed両方の
                # イベントハンドラが存在する =====
                with open(_JS_PATH, "r", encoding="utf-8") as f:
                    js_source = f.read()
                assert "conversation.item.input_audio_transcription.completed" in js_source, (
                    "completed eventのハンドラがJS側に見つかりません"
                )
                assert "conversation.item.input_audio_transcription.failed" in js_source, (
                    "failed eventのハンドラがJS側に見つかりません"
                    "（OpenAI公式SDK型定義でfailed eventの存在を確認済みのため必須）"
                )
                handler_block = _extract_transcription_handler_block(js_source)
                assert "isFailed" in handler_block and "completed" in handler_block, (
                    "completed/failed両方を区別するロジックがハンドラ内に見当たりません"
                )
                print("TEST C/D. completed eventとfailed eventの両方のハンドラがJS側に"
                      "存在する: OK")

                # 以降のPII/使用箇所チェックは「実際に実行されるコード」のみを対象と
                # したいため、説明コメント行（例: 「ここでcustomer_phoneには使わない」
                # という否定形の説明）を誤検知しないよう、行頭が`//`のコメント専用行を
                # 除去したコードのみのブロックを別途作る（説明コメント中に
                # customer_phone/JSON.stringify(msg)等の禁止事項の説明として単語が
                # 出てくること自体は問題ではなく、実コードとして書かれているかどうかを
                # 検査したいため）。
                code_only_lines = [
                    line for line in handler_block.split("\n")
                    if not line.strip().startswith("//")
                ]
                code_only_block = "\n".join(code_only_lines)

                # ===== TEST E: transcript本文をconsole.log/logEvent/
                # pushTimelineEventへ直接出していない（PII厳禁・最重要） =====
                forbidden_patterns = [
                    r"console\.log\(\s*transcript\b",
                    r"logEvent\(\s*transcript\b",
                    r"pushTimelineEvent\(\s*transcript\b",
                    r"JSON\.stringify\(\s*msg\s*\)",
                    r"console\.log\([^)]*\+\s*transcript\s*\+",
                ]
                for pattern in forbidden_patterns:
                    assert not re.search(pattern, code_only_block), (
                        f"PII漏洩の疑いがあるパターンがハンドラ内の実コードに見つかりました: {pattern}"
                    )
                # 実際にログへ渡している変数がtraceLine（統計値のみで構成された文字列）
                # であり、生のtranscriptそのものではないことを確認
                assert "console.log('[' + traceLine + ']')" in code_only_block, (
                    "console.logへ渡している内容がtraceLine（PII-freeな統計値文字列）"
                    "ではない可能性があります"
                )
                assert "pushTimelineEvent(traceLine)" in code_only_block
                # traceLine自体の組み立てにtranscript本文が連結されていないことも確認
                # （transcriptCharLength/digitLikeCount等の"数値化された結果"のみが
                # 使われているべきで、transcript変数そのものが文字列結合されていては
                # ならない）
                trace_line_build = code_only_block[code_only_block.index("const traceLine ="):code_only_block.index("console.log('[' + traceLine")]
                assert "+ transcript " not in trace_line_build and "+transcript " not in trace_line_build, (
                    "traceLineの組み立てにtranscript本文が直接連結されている疑いがあります"
                )
                print("TEST E. transcript本文がconsole.log/logEvent/pushTimelineEventへ"
                      "一切出力されていない（PII-free）: OK")

                # ===== TEST F: transcription結果をcustomer_phoneへ代入していない =====
                assert "customer_phone" not in code_only_block, (
                    "transcriptionハンドラ内でcustomer_phoneが参照されています"
                    "（Phase 1の禁止事項＝transcript→customer_phone生成に抵触する疑い）"
                )
                print("TEST F. transcriptionハンドラがcustomer_phoneを一切参照・代入して"
                      "いない: OK")

                # ===== TEST G: transcription結果をrequest_callback gate
                # （phoneConfirmationIncomplete等）へ使用していない =====
                gate_vars = [
                    "phoneConfirmationIncomplete", "phoneReadbackTurnCompletedThisCall",
                    "phoneReadbackAwaitingUserReply", "callbackAlreadyConfirmedThisCall",
                ]
                for gate_var in gate_vars:
                    assert gate_var not in code_only_block, (
                        f"transcriptionハンドラ内でPHONE confirmation gate変数「{gate_var}」が"
                        "参照されています（Phase 1の禁止事項に抵触する疑い）"
                    )
                assert "request_callback" not in code_only_block or "customer_phone" not in code_only_block, (
                    "transcriptionハンドラがrequest_callback関連の判定へ関与している疑いがあります"
                )
                print("TEST G. transcriptionハンドラがPHONE confirmation gate・"
                      "request_callback判定のいずれにも一切使用されていない: OK")

                # ===== TEST H/I: transcription失敗時にendCall/phase変更をしない =====
                dangerous_calls = [
                    "endCall(", "armPhaseTransitionAfterResponse(",
                    "currentRealtimePhase =", "response.cancel", "showErrorBanner(",
                ]
                for call in dangerous_calls:
                    assert call not in code_only_block, (
                        f"transcriptionハンドラ内に危険な副作用呼び出し「{call}」が"
                        "見つかりました（failed時も非致命的であるべき、という要件に抵触）"
                    )
                print("TEST H/I. transcriptionハンドラにendCall/phase変更/response.cancel/"
                      "error banner表示等の副作用が一切存在しない（構造的に非致命的）: OK")

                # ===== TEST J/K: 既存PHONE length guard・digit pronunciation
                # mappingが維持されている（PHONE FIX FREEZE回帰確認） =====
                reservation_instructions = phase_contexts["reservation"]["instructions"]
                callback_instructions = phase_contexts["callback"]["instructions"]
                legacy_instructions = phase_contexts["legacy_full"]["instructions"]
                for phase_label, instructions_text in (
                    ("reservation", reservation_instructions),
                    ("callback", callback_instructions),
                    ("legacy_full", legacy_instructions),
                ):
                    assert _DIGIT_COUNT_GUARD_PHRASE in instructions_text, (
                        f"{phase_label} instructionsから桁数整合性guardが失われています"
                    )
                    assert _BLOCK_BOUNDARY_PAUSE_PHRASE in instructions_text, (
                        f"{phase_label} instructionsから3-4-4ブロック境界の指示が失われています"
                    )
                    assert _DIGIT_MAPPING_PHRASE in instructions_text, (
                        f"{phase_label} instructionsから数字読みマッピングが失われています"
                    )
                print("TEST J/K. 既存のPHONE 10/11桁guard・3-4-4ブロック境界・数字読み"
                      "マッピングがいずれも維持されている: OK")

                # ===== TEST L: CALLBACK exclusivity guard維持
                # （CALLBACK phaseのtoolsがrequest_callbackのみ） =====
                callback_tool_names = sorted(t["name"] for t in phase_contexts["callback"]["tools"])
                assert callback_tool_names == ["request_callback"], (
                    f"CALLBACK phaseのtoolsがrequest_callbackのみではありません: {callback_tool_names}"
                )
                print("TEST L. CALLBACK phaseのtoolsがrequest_callbackのみに限定されたまま"
                      "維持されている: OK")

                # ===== TEST M: CALLBACK final phrase維持 =====
                assert _CALLBACK_FINAL_PHRASE in callback_instructions, (
                    "CALLBACK final phraseが変更・削除されています（絶対保護対象）"
                )
                print("TEST M. CALLBACK final phraseが変更されていない: OK")

                # ===== TEST N/O: NAME/ROUTING instructions <3000文字、かつ
                # 今回の変更が混入していない =====
                name_instructions = phase_contexts["name"]["instructions"]
                routing_instructions = phase_contexts["routing"]["instructions"]
                for phase_label, instructions_text in (("name", name_instructions), ("routing", routing_instructions)):
                    assert len(instructions_text) < 3000, (
                        f"{phase_label} instructions_charsが3000文字を超えています: "
                        f"{len(instructions_text)}"
                    )
                    assert _DIGIT_COUNT_GUARD_PHRASE not in instructions_text
                print(f"TEST N/O. NAME(instructions_chars={len(name_instructions)})/"
                      f"ROUTING(instructions_chars={len(routing_instructions)})が"
                      "<3000文字を維持し、今回の変更が混入していない: OK")

                # ===== TEST P (追加回帰): request_callback正常系ログの
                # 既存PII leak（JSON.stringify(args)そのままログ）が是正されている =====
                assert "logEvent('Tool呼び出し受信: request_callback ' + JSON.stringify(args))" not in js_source, (
                    "request_callback正常系のPII leak（JSON.stringify(args)の直接ログ出力）が"
                    "まだ残っています"
                )
                assert "hasCustomerPhone=" in js_source and "hasCustomerName=" in js_source, (
                    "request_callbackログがPII-freeな真偽値ログへ置換されていません"
                )
                print("TEST P (追加回帰). request_callback正常系ログの既存PII leak"
                      "（customer_phone/customer_nameの平文JSON出力）が是正されている: OK")

                print("\n=== ALL smoke_test_input_audio_transcription_diagnostic.py CHECKS PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
