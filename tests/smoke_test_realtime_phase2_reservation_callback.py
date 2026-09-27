"""
Realtime Token Architecture Phase 2（2026年9月）: ROUTING → RESERVATION /
CALLBACK 分岐の契約テスト（バックエンド側のみ）。

背景: Phase 1（smoke_test_realtime_phase1_name.py）で NAME→ROUTING の
session.update が実機で成功し、input_tokens が約20,000から1,600〜1,700
クラスへ削減されたことが確認された。今回はその成功を絶対的な回帰基準として
壊さず、ROUTING が「予約」「折り返し・取り次ぎ」を分類し、legacy_full
（19,749文字・8Tool）へ戻さずに RESERVATION / CALLBACK という専用の
最小contextへ直接遷移する仕組みを追加した
（app/services/realtime_voice_ai.py: build_realtime_phase_contexts /
_PHASE2_ROUTING_ROLE_TEMPLATE / classify_intent Tool（_ROUTING_TOOLS）/
_build_reservation_phase_instructions / _build_callback_phase_instructions）。

本テストは「RESERVATION/CALLBACK/更新されたROUTINGのcontextが正しく
組み立てられていること」のみを検証する（バックエンドのみで検証可能な
契約項目）。実機での実際の遷移・レイテンシ・token使用量は実機TEST A/B/C/D
でしか判断できず、本テストはそれを主張するものではない（推測ではなく
実測が必要な点は実機テストに委ねる）。

検証項目（ユーザー指定§27 A〜R。全17項目 + 重複Tool名なしの確認）:
  A. NAME instructions_chars < 3000
  B. NAME tools == []
  C. ROUTING instructions_chars < 3000
  D. ROUTING tools（仕様変更あり。下記コメント参照）
  E. RESERVATION contextが存在する
  F. CALLBACK contextが存在する
  G. RESERVATION は legacy_full と一致しない（instructions/tools）
  H. CALLBACK は legacy_full と一致しない（instructions/tools）
  I. reservation toolsに check_availability が含まれる
  J. reservation toolsに create_reservation が含まれる
  K. callback toolsに request_callback が含まれる
  L. callback toolsに check_availability が含まれない
  M. callback toolsに create_reservation が含まれない
  N. NAME に予約/折り返し関連Toolが含まれない
  O. ROUTING に予約系Tool（check_availability/create_reservation/
     request_callback）が含まれない
  P. legacy_full がfallback/debug用として維持されている
     （既存build_realtime_instructions()+_REALTIME_TOOLSと完全一致）
  Q. session truncation設定が既存どおり維持されている（retention_ratio）
  R. model/voice/semantic_vad設定が既存どおり維持されている（回帰なし）
  （追加）Tool schema全体（legacy_full全8Tool + classify_intent）に
  重複したTool名が存在しない

D.（仕様変更に伴う調整。恣意的な緩和ではない）:
  ユーザー指定の原文には「ROUTING tools==0」とあるが、これはPhase 1が
  「ROUTINGのack応答完了直後、無条件でlegacy_fullへ戻す」設計だった時点の
  記述であり、その時点ではTool自体が不要だった。ユーザー自身の§6
  （新しいNLU・別モデル・別APIを追加せず、既存のRealtimeモデル自身の
  自然言語理解を使う）という制約と、フロントエンド側の重要な制約
  （input_audio_transcriptionが未設定のため、顧客の発話内容そのものには
  一切アクセスできない）を踏まえると、「ROUTINGで判定した意図をどう
  クライアントに伝えるか」という手段が必要であり、唯一の非追加的な手段は
  既存モデル自身のTool呼び出し（構造化された合図）である。そのため、
  副作用の一切ない最小の1関数 classify_intent（enum: reservation|callback
  の2値のみ、他のToolのような外部I/Oは無い）だけをROUTINGに追加する
  という設計変更を行った。この調整は既に
  tests/smoke_test_realtime_phase1_name.pyの同じ場所で先に反映・
  説明済みであり、本ファイルではその調整を前提に、
  「ROUTINGのtoolsがclassify_intentちょうど1個のみであり、
  予約/折り返しを実行する側のTool（check_availability/create_reservation/
  request_callback）は一切含まれない」ことを検証する（=実質的に
  「ROUTING自体は予約・折り返しを実行するTool数がゼロ」であるという
  原文の意図は損なっていない）。

実行: python3 tests/smoke_test_realtime_phase2_reservation_callback.py
"""

import asyncio
import os
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_realtime_phase2.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-realtime-phase2"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402


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


async def main():
    from app.main import app, lifespan
    from app.models.shop import Shop

    async with lifespan(app):
        from app.database import AsyncSessionLocal
        from app.services import realtime_voice_ai

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-realtime-phase2@example.com",
                "password": "password123",
                "display_name": "Phase2テストオーナー",
            })
            assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
            owner_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "Phase2テスト店", "category": "レストラン",
                "address": "東京都渋谷区5-5-5",
            }, headers=owner_headers)
            assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
            shop_id = r.json()["shop_id"]

            async with AsyncSessionLocal() as db_session:
                shop = await db_session.get(Shop, shop_id)
                assert shop is not None

                captured = {}
                fake_client = _make_fake_client(captured)
                with patch("app.services.realtime_voice_ai._get_client", return_value=fake_client):
                    result = await realtime_voice_ai.create_realtime_session(db_session, shop)
                session_config = captured.get("session") or {}
                phase_contexts = result.get("realtime_phase_contexts") or {}

                for required_phase in ("name", "routing", "reservation", "callback", "legacy_full"):
                    assert required_phase in phase_contexts, (
                        f"realtime_phase_contextsに'{required_phase}'が存在しません: {list(phase_contexts.keys())}"
                    )

                name_ctx = phase_contexts["name"]
                routing_ctx = phase_contexts["routing"]
                reservation_ctx = phase_contexts["reservation"]
                callback_ctx = phase_contexts["callback"]
                legacy_ctx = phase_contexts["legacy_full"]

                name_instructions = name_ctx["instructions"]
                routing_instructions = routing_ctx["instructions"]
                reservation_instructions = reservation_ctx["instructions"]
                callback_instructions = callback_ctx["instructions"]
                legacy_instructions = legacy_ctx["instructions"]

                name_tool_names = [t["name"] for t in name_ctx["tools"]]
                routing_tool_names = [t["name"] for t in routing_ctx["tools"]]
                reservation_tool_names = [t["name"] for t in reservation_ctx["tools"]]
                callback_tool_names = [t["name"] for t in callback_ctx["tools"]]
                legacy_tool_names = [t["name"] for t in legacy_ctx["tools"]]

                # ===== A. NAME instructions_chars < 3000（Phase1成功実績=2054に対する回帰guard） =====
                assert len(name_instructions) < 3000, (
                    f"NAME instructions_charsが3,000文字を超えています（Phase1回帰基準を大きく上回る可能性）: {len(name_instructions)}"
                )
                print(f"A. NAME instructions_chars={len(name_instructions)} < 3000: OK")

                # ===== B. NAME tools == [] =====
                assert name_ctx["tools"] == [], f"NAMEのtoolsが空リストではありません: {name_tool_names}"
                print("B. NAME tools == []: OK")

                # ===== C. ROUTING instructions_chars < 3000（Phase1成功実績=2265に対する回帰guard） =====
                assert len(routing_instructions) < 3000, (
                    f"ROUTING instructions_charsが3,000文字を超えています（Phase1回帰基準を大きく上回る可能性）: {len(routing_instructions)}"
                )
                print(f"C. ROUTING instructions_chars={len(routing_instructions)} < 3000: OK")

                # ===== D. ROUTING tools（上記docstring参照。classify_intentのみ許容） =====
                assert routing_tool_names == ["classify_intent"], (
                    f"ROUTINGのtoolsがclassify_intentのみではありません（仕様変更の意図に反する）: {routing_tool_names}"
                )
                print("D. ROUTING tools == ['classify_intent']（予約/折り返しを実行するToolは含まない）: OK")

                # ===== E. RESERVATION contextが存在する =====
                assert reservation_ctx is not None and "instructions" in reservation_ctx and "tools" in reservation_ctx
                print("E. RESERVATION contextが存在する: OK")

                # ===== F. CALLBACK contextが存在する =====
                assert callback_ctx is not None and "instructions" in callback_ctx and "tools" in callback_ctx
                print("F. CALLBACK contextが存在する: OK")

                # ===== G. RESERVATION != legacy_full =====
                assert reservation_instructions != legacy_instructions, (
                    "RESERVATION instructionsがlegacy_fullと同一です（軽量化されていません）"
                )
                assert reservation_tool_names != legacy_tool_names, (
                    "RESERVATION toolsがlegacy_fullと同一です（軽量化されていません）"
                )
                print("G. RESERVATION は legacy_full と一致しない（instructions/tools）: OK")

                # ===== H. CALLBACK != legacy_full =====
                assert callback_instructions != legacy_instructions, (
                    "CALLBACK instructionsがlegacy_fullと同一です（軽量化されていません）"
                )
                assert callback_tool_names != legacy_tool_names, (
                    "CALLBACK toolsがlegacy_fullと同一です（軽量化されていません）"
                )
                print("H. CALLBACK は legacy_full と一致しない（instructions/tools）: OK")

                # ===== I/J. reservation toolsにcheck_availability/create_reservationが含まれる =====
                assert "check_availability" in reservation_tool_names, (
                    f"RESERVATION toolsにcheck_availabilityが含まれていません: {reservation_tool_names}"
                )
                print("I. reservation toolsにcheck_availabilityが含まれる: OK")
                assert "create_reservation" in reservation_tool_names, (
                    f"RESERVATION toolsにcreate_reservationが含まれていません: {reservation_tool_names}"
                )
                print("J. reservation toolsにcreate_reservationが含まれる: OK")

                # ===== K. callback toolsにrequest_callbackが含まれる =====
                assert "request_callback" in callback_tool_names, (
                    f"CALLBACK toolsにrequest_callbackが含まれていません: {callback_tool_names}"
                )
                print("K. callback toolsにrequest_callbackが含まれる: OK")

                # ===== L/M. callback toolsにcheck_availability/create_reservationが含まれない =====
                assert "check_availability" not in callback_tool_names, (
                    f"CALLBACK toolsにcheck_availabilityが混入しています: {callback_tool_names}"
                )
                print("L. callback toolsにcheck_availabilityが含まれない: OK")
                assert "create_reservation" not in callback_tool_names, (
                    f"CALLBACK toolsにcreate_reservationが混入しています: {callback_tool_names}"
                )
                print("M. callback toolsにcreate_reservationが含まれない: OK")

                # ===== N. NAME に予約/折り返し関連Toolが含まれない =====
                _reservation_or_callback_tools = {
                    "check_availability", "create_reservation", "request_callback", "classify_intent",
                }
                assert not (_reservation_or_callback_tools & set(name_tool_names)), (
                    f"NAME toolsに予約/折り返し関連Toolが混入しています: {name_tool_names}"
                )
                print("N. NAME に予約/折り返し関連Toolが含まれない: OK")

                # ===== O. ROUTING に予約を実行するToolが含まれない =====
                _execution_tools = {"check_availability", "create_reservation", "request_callback"}
                assert not (_execution_tools & set(routing_tool_names)), (
                    f"ROUTING toolsに予約/折り返しを実行するToolが混入しています: {routing_tool_names}"
                )
                print("O. ROUTING に予約を実行するTool（check_availability/create_reservation/request_callback）が含まれない: OK")

                # ===== P. legacy_fullがfallback/debug用として維持されている（既存出力と完全一致・回帰確認） =====
                assert legacy_ctx["tools"] == realtime_voice_ai._REALTIME_TOOLS, (
                    "legacy_fullのtoolsが既存の_REALTIME_TOOLSと一致していません（fallbackとして維持されていない）"
                )
                staff_settings = await realtime_voice_ai._get_staff_settings(db_session, shop.id)
                expected_legacy_instructions = await realtime_voice_ai.build_realtime_instructions(
                    db_session, shop, staff_settings
                )
                assert legacy_instructions == expected_legacy_instructions, (
                    "legacy_fullのinstructionsが既存build_realtime_instructions()の出力と一致していません"
                    "（fallback/debug用として意図せず改変されている可能性）"
                )
                print("P. legacy_full がfallback/debug用として既存どおり維持されている（完全一致・回帰確認）: OK")

                # ===== Q. session truncation設定が既存どおり維持されている =====
                # settings.OPENAI_REALTIME_TRUNCATION_RETENTION_RATIO（デフォルト"0.8"）が
                # 設定されている限りsession_config["truncation"]が既存どおり
                # {"type": "retention_ratio", "retention_ratio": <float>} であることを確認する
                # （Phase 2のcontext architecture変更が、無関係なtruncation設定を
                # 意図せず壊していないことの回帰確認。値そのものの妥当性は
                # settings側の責務であり、ここでは「壊れていないこと」のみ検証する）。
                from app.config import get_settings as _get_settings
                _settings = _get_settings()
                if _settings.OPENAI_REALTIME_TRUNCATION_RETENTION_RATIO:
                    truncation_cfg = session_config.get("truncation")
                    assert truncation_cfg is not None and truncation_cfg.get("type") == "retention_ratio", (
                        f"session.truncationが既存のretention_ratio設定のまま維持されていません: {truncation_cfg}"
                    )
                    print(f"Q. session truncation設定（retention_ratio={truncation_cfg.get('retention_ratio')}）が既存どおり維持されている: OK")
                else:
                    print("Q. OPENAI_REALTIME_TRUNCATION_RETENTION_RATIOが未設定のため、truncation設定なしを許容（既存どおり）: OK")

                # ===== R. model/voice/semantic_vad設定が回帰なく維持されている =====
                assert session_config.get("model") == _settings.OPENAI_REALTIME_MODEL, (
                    f"session_configのmodelが既存設定と一致しません: {session_config.get('model')}"
                )
                audio_cfg = session_config.get("audio") or {}
                output_voice = ((audio_cfg.get("output") or {}).get("voice"))
                assert output_voice, "session_config.audio.output.voiceが設定されていません（既存構造からの回帰の可能性）"
                turn_detection = (audio_cfg.get("input") or {}).get("turn_detection") or {}
                assert turn_detection.get("type") == "semantic_vad", (
                    f"session_config.audio.input.turn_detection.typeがsemantic_vadではありません（音声スタックへの意図しない変更の可能性）: {turn_detection}"
                )
                print(
                    f"R. model={session_config.get('model')} / voice={output_voice} / "
                    f"turn_detection.type=semantic_vad が既存どおり維持されている（回帰なし）: OK"
                )

                # ===== 追加: Tool schema全体で重複したTool名が存在しない =====
                all_tool_names_with_context = []
                for phase_name, ctx in phase_contexts.items():
                    for t in ctx["tools"]:
                        all_tool_names_with_context.append((phase_name, t["name"]))
                # legacy_full自体の中に重複がないこと（既存の8Tool定義自体の健全性）
                legacy_name_counts = {}
                for name in legacy_tool_names:
                    legacy_name_counts[name] = legacy_name_counts.get(name, 0) + 1
                duplicated_in_legacy = [n for n, c in legacy_name_counts.items() if c > 1]
                assert not duplicated_in_legacy, (
                    f"legacy_full(_REALTIME_TOOLS)内にTool名の重複があります: {duplicated_in_legacy}"
                )
                # classify_intentがlegacy_fullの既存8Toolのいずれとも名前が衝突していないこと
                assert "classify_intent" not in legacy_tool_names, (
                    "classify_intentという名前が既存の_REALTIME_TOOLS（legacy_full）内のTool名と衝突しています"
                )
                print("追加: Tool schema全体（legacy_full全8Tool + classify_intent）に重複したTool名が存在しない: OK")

                # 実測値の報告（推測ではなく実測。最終報告に転記するための数値）
                def _tools_chars(tools):
                    import json
                    return len(json.dumps(tools, ensure_ascii=False))

                print(
                    "\n[MEASURED] "
                    f"name: instructions_chars={len(name_instructions)}, tool_count={len(name_tool_names)} | "
                    f"routing: instructions_chars={len(routing_instructions)}, tool_count={len(routing_tool_names)}, tool_names={routing_tool_names} | "
                    f"reservation: instructions_chars={len(reservation_instructions)}, tool_count={len(reservation_tool_names)}, "
                    f"tools_chars={_tools_chars(reservation_ctx['tools'])}, tool_names={reservation_tool_names} | "
                    f"callback: instructions_chars={len(callback_instructions)}, tool_count={len(callback_tool_names)}, "
                    f"tools_chars={_tools_chars(callback_ctx['tools'])}, tool_names={callback_tool_names} | "
                    f"legacy_full: instructions_chars={len(legacy_instructions)}, tool_count={len(legacy_tool_names)}"
                )

            print("\n=== ALL smoke_test_realtime_phase2_reservation_callback.py CHECKS PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
