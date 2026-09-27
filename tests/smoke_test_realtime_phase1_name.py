"""
Realtime Token Architecture Phase 1（2026年9月）: NAME / ROUTING / legacy_full
の3つのsession context契約テスト（バックエンド側のみ）。

背景: 実機で「input_tokens=20,550・cached_ratio=0.997（ほぼ全キャッシュ）
でもrate_limit_exceededが発生し、remainingが18,565→1,406まで激減する」
ことが確認され、instructions圧縮（HOTFIX8-10）・session.truncation
（retention_ratio=0.8）のいずれでも解消しなかった。監査の結果、通話の
ごく早い段階（NAME/ご用件を尋ねる程度の1〜2ターン目）で、
build_realtime_instructions()の全15セクション（約19,753文字）+
_REALTIME_TOOLS全8個（約14,277文字）という固定payloadを毎ターン送って
いること自体が支配的な要因である可能性が高いと判断し、採用アーキテクチャA
（同一Realtime session内、session.updateによるPhase別最小化）に基づき、
create_realtime_session()の初回セッション作成自体をPhase "name"の最小
instructions・tools=[]で開始するよう変更した
（app/services/realtime_voice_ai.py: build_realtime_phase_contexts /
create_realtime_session）。

重要: 本テストは「Phase 1/2/legacy_fullのcontextが正しく組み立てられ、
初回セッション作成に正しく使われること」のみを検証する。session.updateが
実機で実際にOpenAI Realtime API側の挙動を変えるか、rate limitへ効果が
あるかは実機のREALTIME_USAGE_BREAKDOWN/RATE_LIMIT_TOKEN_DELTA診断マーカー
での実測でしか判断できず、本テストはその効果を主張するものではない。

検証項目（ユーザー指定の§12契約テストのうちバックエンドで検証可能な項目）:
A. 初回セッションのsession_configには全8Tool(_REALTIME_TOOLS)が含まれない
B. Phase1のtool数は0
C. Phase1のinstructionsに_FAST_RESERVATION_FLOW_TEMPLATE全文が含まれない
D. Phase1のinstructionsに_BOOKING_SAFETY_TEMPLATE全文が含まれない
E. Phase1のinstructionsに_TIME_AMBIGUITY_TEMPLATE全文が含まれない
F. Phase1のinstructionsに_HUMAN_HANDOFF_TEMPLATE全文が含まれない
G. Phase1のinstructionsに最小限の安全ルール（_MINIMAL_EARLY_HANDOFF_SAFETY_TEMPLATE）が含まれる
H. Phase1のinstructionsがお名前を尋ねる内容を含む
（追加）Routingフェーズも同様にtool数0・大きなテンプレートを含まないことを確認
（追加）legacy_fullフェーズは既存のbuild_realtime_instructions()+_REALTIME_TOOLSと完全一致（回帰確認）
（追加）Phase1インストラクションの実測文字数を報告し、19,753charsから大幅に削減されていることを確認
（追加）create_voice_preview_session()にはrealtime_phase_contextsを混入させない（回帰確認）

実行: python3 tests/smoke_test_realtime_phase1_name.py
"""

import asyncio
import os
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_realtime_phase1.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-realtime-phase1"
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
                "email": "owner-realtime-phase1@example.com",
                "password": "password123",
                "display_name": "Phase1テストオーナー",
            })
            assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
            owner_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "Phase1テスト店", "category": "レストラン",
                "address": "東京都渋谷区4-4-4",
            }, headers=owner_headers)
            assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
            shop_id = r.json()["shop_id"]

            async with AsyncSessionLocal() as db_session:
                shop = await db_session.get(Shop, shop_id)
                assert shop is not None

                # ===== 初回セッション作成（create_realtime_session）を検証 =====
                captured = {}
                fake_client = _make_fake_client(captured)
                with patch("app.services.realtime_voice_ai._get_client", return_value=fake_client):
                    result = await realtime_voice_ai.create_realtime_session(db_session, shop)
                session_config = captured.get("session") or {}

                # A. 初回セッションのtoolsに全8Toolが含まれない
                sent_tools = session_config.get("tools", None)
                assert sent_tools == [], (
                    f"初回セッション作成時点でtoolsが空リストではありません（Phase1最小化が効いていません）: {sent_tools}"
                )
                print("A. 初回セッションのsession_configには全8Toolが含まれない（tools=[]）: OK")

                # B. Phase1 tool数は0
                phase_contexts = result.get("realtime_phase_contexts") or {}
                assert "name" in phase_contexts and "routing" in phase_contexts and "legacy_full" in phase_contexts, (
                    f"realtime_phase_contextsにname/routing/legacy_fullが揃っていません: {list(phase_contexts.keys())}"
                )
                name_ctx = phase_contexts["name"]
                assert name_ctx["tools"] == [], f"Phase1のtool数が0ではありません: {name_ctx['tools']}"
                print("B. Phase1のtool数は0: OK")

                name_instructions = name_ctx["instructions"]

                # C〜F. 大きな既存テンプレート全文が含まれていないこと
                assert realtime_voice_ai._FAST_RESERVATION_FLOW_TEMPLATE.split("{party_size_guidance}")[0][:50] not in name_instructions, (
                    "Phase1 instructionsにFAST_RESERVATION_FLOW全文の一部が含まれています"
                )
                print("C. Phase1のinstructionsにFAST_RESERVATION_FLOW全文が含まれない: OK")

                assert realtime_voice_ai._BOOKING_SAFETY_TEMPLATE not in name_instructions, (
                    "Phase1 instructionsにBOOKING_SAFETY全文が含まれています"
                )
                print("D. Phase1のinstructionsにBOOKING_SAFETY全文が含まれない: OK")

                assert realtime_voice_ai._TIME_AMBIGUITY_TEMPLATE not in name_instructions, (
                    "Phase1 instructionsにTIME_AMBIGUITY全文が含まれています"
                )
                print("E. Phase1のinstructionsにTIME_AMBIGUITY全文が含まれない: OK")

                assert realtime_voice_ai._HUMAN_HANDOFF_TEMPLATE not in name_instructions, (
                    "Phase1 instructionsにHUMAN_HANDOFF全文が含まれています"
                )
                print("F. Phase1のinstructionsにHUMAN_HANDOFF全文が含まれない: OK")

                # G. 最小限の安全ルールが含まれる
                assert realtime_voice_ai._MINIMAL_EARLY_HANDOFF_SAFETY_TEMPLATE in name_instructions, (
                    "Phase1 instructionsに最小限の安全ルール（MINIMAL_EARLY_HANDOFF_SAFETY）が含まれていません"
                )
                print("G. Phase1のinstructionsに最小限の安全ルールが含まれる: OK")

                # H. お名前を尋ねる内容を含む
                assert "お名前" in name_instructions, "Phase1 instructionsにお名前を尋ねる内容が含まれていません"
                print("H. Phase1のinstructionsがお名前を尋ねる内容を含む: OK")

                # ===== Routingフェーズの検証（tool数・大きなテンプレートを含まない） =====
                # Realtime Token Architecture Phase 2（今回の仕様変更に伴う更新）:
                # 以前は「ROUTINGのtool数は常に0」だったが、これはPhase 1が
                # 「ROUTINGのack応答完了直後、無条件でlegacy_fullへ自動フォール
                # バックする」設計だったためであり、tool自体が一切不要だった。
                # 今回のPhase 2はROUTING自身が「予約」「折り返し」を実際に
                # 判定してRESERVATION/CALLBACKへ直接分岐する設計に変わった
                # ため、判定結果をクライアントへ伝えるための副作用の無い最小限の
                # 1関数（classify_intent）だけをROUTINGへ追加した。したがって
                # 「tools==[]」から「tools内にclassify_intentのみ（他の7つの
                # 予約/折り返し関連Toolは含まれない）」へ更新する。これは仕様
                # そのものの変更であり、テストを通すための恣意的な緩和ではない
                # （tests/smoke_test_realtime_phase2_reservation_callback.pyの
                # 契約テストF/Gで、classify_intent以外のToolがROUTINGに一切
                # 含まれないことを別途検証する）。
                routing_ctx = phase_contexts["routing"]
                routing_tool_names = [t["name"] for t in routing_ctx["tools"]]
                assert routing_tool_names == ["classify_intent"], (
                    f"RoutingのtoolsがPhase2仕様（classify_intentのみ）と一致しません: {routing_tool_names}"
                )
                routing_instructions = routing_ctx["instructions"]
                for big_template_name in [
                    "_FAST_RESERVATION_FLOW_TEMPLATE", "_BOOKING_SAFETY_TEMPLATE",
                    "_TIME_AMBIGUITY_TEMPLATE", "_HUMAN_HANDOFF_TEMPLATE",
                    "_SHOP_KNOWLEDGE_RULES_TEMPLATE", "_CUSTOMER_CONTEXT_RULES_TEMPLATE",
                ]:
                    big_template = getattr(realtime_voice_ai, big_template_name)
                    assert big_template not in routing_instructions, (
                        f"Routing instructionsに{big_template_name}全文が含まれています"
                    )
                print("Routing: tools=[classify_intent]のみ、既存の大きなテンプレート全文を含まない: OK")

                # ===== legacy_fullフェーズは既存build_realtime_instructions()と完全一致（回帰確認） =====
                legacy_ctx = phase_contexts["legacy_full"]
                assert legacy_ctx["tools"] == realtime_voice_ai._REALTIME_TOOLS, (
                    "legacy_fullのtoolsが既存の_REALTIME_TOOLSと一致していません"
                )
                staff_settings = await realtime_voice_ai._get_staff_settings(db_session, shop.id)
                expected_legacy_instructions = await realtime_voice_ai.build_realtime_instructions(
                    db_session, shop, staff_settings
                )
                assert legacy_ctx["instructions"] == expected_legacy_instructions, (
                    "legacy_fullのinstructionsが既存build_realtime_instructions()の出力と一致していません"
                    "（Reservation/Callback Phaseへの無条件フォールバックが既存フローと完全に同一ではありません）"
                )
                print("legacy_full: 既存build_realtime_instructions()+_REALTIME_TOOLSと完全一致（回帰確認）: OK")

                # 実測文字数の報告（推測ではなく実測。19,753charsからの削減率を記録）
                baseline_chars = 19753
                reduction_pct = round((1 - len(name_instructions) / baseline_chars) * 100, 1)
                print(
                    f"[MEASURED] Phase1 instructions_chars={len(name_instructions)} "
                    f"(旧baseline={baseline_chars}比 -{reduction_pct}%), "
                    f"Routing instructions_chars={len(routing_instructions)}, "
                    f"legacy_full instructions_chars={len(legacy_ctx['instructions'])}"
                )
                assert len(name_instructions) < 5000, (
                    f"Phase1 instructionsが5,000文字を超えています（想定より大きい可能性）: {len(name_instructions)}"
                )

                # ===== create_voice_preview_session()にはrealtime_phase_contextsを混入させない（回帰確認） =====
                captured_preview = {}
                fake_client_preview = _make_fake_client(captured_preview)
                with patch("app.services.realtime_voice_ai._get_client", return_value=fake_client_preview):
                    preview_result = await realtime_voice_ai.create_voice_preview_session("marin")
                assert "realtime_phase_contexts" not in preview_result, (
                    "voice-preview用セッションの戻り値にrealtime_phase_contextsが混入しています"
                )
                preview_session_config = captured_preview.get("session") or {}
                assert "tools" not in preview_session_config, (
                    "voice-preview用セッションにtoolsが混入しています"
                )
                print("voice-preview用セッションにはrealtime_phase_contextsもtoolsも混入していない（回帰確認）: OK")

            print("\n=== ALL smoke_test_realtime_phase1_name.py CHECKS PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
