"""
FAST TURN — NOISY ENVIRONMENT / 3-SECOND TURN BOUNDARY: NOISE RECOVERY
instructions スモークテスト

背景: 実機Macテストで、call.htmlのRealtime音声AI「1st turn（要件を聞く）」
場面において、周囲の雑音が多い環境ではユーザー発話終了後もAIが黙ったまま
になり、次の質問へ進まないことがあるという症状が報告された（詳細な監査は
frontend/public/js/realtime-voice-engine.js側のUSER_TURN_3S_FALLBACK/
AI SPEAKING PROTECTIONの設計コメント、および
tests/test_noisy_environment_turn_boundary.jsを参照）。

このJS側の3秒fallbackが手動でinput_audio_buffer.commitを送った後、AIが
実際にどう応答するか（会話全体をやり直すのか、既知の情報を保持したまま
次の未取得項目だけを具体的に尋ねるのか）は、Realtime API自体の制御では
なくsession instructions（app/services/realtime_voice_ai.py）側の責務。
そのため、_FAST_RESERVATION_FLOW_TEMPLATE内、既存の
「直前に尋ねた質問に対応する回答を優先する（ノイズ耐性）（重要）」の直後に、
NOISE RECOVERYの新しいルールを追加した:
  - 聞き取れなかった場合、会話全体をリセットする発話（「もう一度最初から
    お願いします。」）は絶対にしない。
  - 既に分かっている情報（日時・人数・お名前・電話番号・来店理由等）は
    保持し、聞き直さない（既存の「既に分かっている情報を聞き直さない
    （重要）」ルールと同じ方針）。
  - まだ分かっていない項目の中から、次に必要な項目をちょうど1つだけ選び、
    具体的に尋ねる（単に「聞き取れませんでした」だけで発話を終えない）。
ユーザー指定のBAD/GOOD例（日時取得済み・人数不明の場合の
「ありがとうございます。何名様でのご予約でしょうか？」等）をそのまま
instructionsへ反映した。

本テストが検証すること:
1. 新しいルールの文言が_FAST_RESERVATION_FLOW_TEMPLATEに含まれ、会話全体の
   リセットを禁止し、既知情報の保持・単一項目への絞り込みを明示している
   こと。
2. ユーザー指定のBAD例（「もう一度最初からお願いします。」）・GOOD例
   （「ありがとうございます。何名様でのご予約でしょうか？」）がそのまま
   含まれていること。
3. 新しいルールが「直前に尋ねた質問に対応する回答を優先する（ノイズ耐性）
   （重要）」の直後・「最重要：速さのために情報を勝手に作らない」の直前
   という意図した位置に挿入されていること。
4. 既存の「既に分かっている情報を聞き直さない（重要）」「相槌だけで発話を
   終わらせない（重要）」等、隣接する既存ルールの文言が一切変更されて
   いないこと（回帰確認）。
5. build_realtime_instructions()が実際に組み立てるinstructions全体でも、
   既存の全セクション出現順序（Booking Safetyが常に最後という不変条件を
   含む）が壊れておらず、新ルールが実際に含まれていること。
6. 8つのRealtime Tool schemaが一切変更されていないこと（DB/API/Tool
   無変更の確認。JS側のUSER_TURN_3S_FALLBACK/AI SPEAKING PROTECTIONは
   純粋にクライアント側のfallback/mute機構であり、Tool定義には一切
   影響しないはずである、という事実の再確認）。
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_noise_recovery.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-noise-recovery"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx


def _test_new_rule_wording():
    from app.services.realtime_voice_ai import _FAST_RESERVATION_FLOW_TEMPLATE

    normalized = _FAST_RESERVATION_FLOW_TEMPLATE.replace("\n", "")

    assert "NOISE RECOVERY" in normalized
    assert "聞き取れなかった" in normalized or "不明瞭だった" in normalized

    # 会話全体のリセットを絶対に禁止していること（HOTFIX 8 Phase 2で圧縮済み。
    # 具体的な言い回しは変わり得るため、禁止対象フレーズのいずれかの近傍に
    # 「絶対に」の強い禁止表現があることを意味的に検証する）
    reset_ban_phrase = None
    for cand in ("会話そのものを止めたり", "最初からやり直させたり", "会話全体をリセットする"):
        if cand in normalized:
            reset_ban_phrase = cand
            break
    assert reset_ban_phrase is not None, "会話リセット禁止の文言が見つかりません"
    idx_reset_ban = normalized.find(reset_ban_phrase)
    window = normalized[max(0, idx_reset_ban - 50):idx_reset_ban + 250]
    assert "絶対に" in window

    # 既知情報の保持・単一項目への絞り込みが明示されていること（助詞「を」の
    # 有無等、細部の言い回しは変わり得るため「ちょうど1つ」＋「選び」の
    # 両方の存在で意味的に検証する）
    assert "そのまま保持し、聞き直さない" in normalized
    assert "ちょうど1つ" in normalized and "選び" in normalized

    # 「聞き取れませんでした」だけで終わらせないことが明示されていること
    assert "聞き取れませんでした" in normalized
    idx_dake = normalized.find("これだけで発話を終える")
    assert idx_dake != -1

    print("1. NOISE RECOVERYルールの文言（会話リセット禁止・既知情報保持・単一項目への絞り込み）: OK")


def _test_bad_good_examples():
    from app.services.realtime_voice_ai import _FAST_RESERVATION_FLOW_TEMPLATE

    normalized = _FAST_RESERVATION_FLOW_TEMPLATE.replace("\n", "")

    # ユーザー指定のBAD例（禁止の言い回し自体はHOTFIX 8 Phase 2で変わり得るため、
    # BAD例フレーズの近傍に「絶対に」＋禁止表現があることを意味的に検証する）
    assert "もう一度最初からお願いします。」" in normalized
    idx_bad_example = normalized.find("もう一度最初からお願いします。」")
    window = normalized[max(0, idx_bad_example - 100):idx_bad_example + 150]
    assert "絶対に" in window and ("しないでください" in window or "しない" in window)

    # ユーザー指定のGOOD例（日時取得済み・人数不明の場合）。
    # TASK B: 相槌の機械的濫用を防ぐため、先頭の「ありがとうございます。」は
    # 撤廃し、質問のみを直接尋ねる形に変更した。
    assert "何名様でのご予約でしょうか？」" in normalized
    assert "ありがとうございます。何名様でのご予約でしょうか？」" not in normalized, (
        "NOISE RECOVERY例に相槌が機械的に復活しています（TASK B回帰）"
    )

    # 逆パターン（人数取得済み・日時不明）のGOOD例も追加している
    assert "ご希望の日時をもう一度お願いします。」" in normalized

    print("2. ユーザー指定のBAD/GOOD例の反映: OK")


def _test_new_rule_position():
    from app.services.realtime_voice_ai import _FAST_RESERVATION_FLOW_TEMPLATE

    pos_noise_resilience = _FAST_RESERVATION_FLOW_TEMPLATE.find("直前に尋ねた質問に対応する回答を優先する（ノイズ耐性）（重要）")
    pos_noise_recovery = _FAST_RESERVATION_FLOW_TEMPLATE.find("NOISE RECOVERY")
    pos_speed = _FAST_RESERVATION_FLOW_TEMPLATE.find("最重要：速さのために情報を勝手に作らない")

    assert pos_noise_resilience != -1 and pos_noise_recovery != -1 and pos_speed != -1
    assert pos_noise_resilience < pos_noise_recovery < pos_speed, (
        f"NOISE RECOVERYルールの挿入位置が意図した順序ではありません: "
        f"ノイズ耐性={pos_noise_resilience}, NOISE RECOVERY={pos_noise_recovery}, 速さ={pos_speed}"
    )

    print("2. NOISE RECOVERYルールの挿入位置（ノイズ耐性ルールの直後・速さルールの直前）: OK")


def _test_adjacent_existing_rules_unchanged():
    """隣接する既存ルールが今回の追加で壊れていないことの回帰確認。"""
    from app.services.realtime_voice_ai import _FAST_RESERVATION_FLOW_TEMPLATE

    assert "既に分かっている情報を聞き直さない（重要）" in _FAST_RESERVATION_FLOW_TEMPLATE
    assert "既に述べられた項目を、確認のためであっても改めて質問し直す" in _FAST_RESERVATION_FLOW_TEMPLATE

    assert "相槌だけで発話を終わらせない（重要）" in _FAST_RESERVATION_FLOW_TEMPLATE

    assert "直前に尋ねた質問に対応する回答を優先する（ノイズ耐性）（重要）" in _FAST_RESERVATION_FLOW_TEMPLATE
    # 改行位置（テンプレート内の折り返し位置）は圧縮で変わり得るため、
    # 改行を除去した上でフレーズ自体の存在を検証する
    _normalized_for_regression = _FAST_RESERVATION_FLOW_TEMPLATE.replace("\n", "")
    assert "背景の雑音・周囲の話し声・テレビの音・無関係な短い音の断片だけを理由に" in _normalized_for_regression

    assert "最重要：速さのために情報を勝手に作らない" in _FAST_RESERVATION_FLOW_TEMPLATE
    assert "「自然」「正確」「速い」の3つを同時に" in _normalized_for_regression

    print("3. 隣接する既存ルール（既知情報を聞き直さない・相槌・ノイズ耐性・速さ）: 無変更 OK")


def _test_realtime_tools_unchanged():
    from app.services.realtime_voice_ai import _REALTIME_TOOLS

    # Reservation Intelligence Phase E-1でsuggest_available_timesが9個目の
    # Toolとして追加された（ユーザー承認済み。本フェーズのスコープ外の変更）。
    tool_names = [t["name"] for t in _REALTIME_TOOLS]
    assert tool_names == [
        "check_availability", "create_reservation", "get_shop_info", "find_customer",
        "confirm_customer_identity", "get_customer_context", "set_conversation_language",
        "request_callback", "suggest_available_times",
    ], f"9 Tool schemaが想定と異なります: {tool_names}"

    print("4. 9つのRealtime Tool schema: 想定通り OK（本フェーズのDB/API/Tool変更なしの確認）")


def _assert_section_order(instructions: str):
    markers = {
        "core_rules": "話し方の絶対ルール",
        "scope": "会話範囲のルール",
        "intent_classification": "通話冒頭のご用件把握",
        "fast_reservation_flow": "予約受付を短時間で終える「Fast Reservation Flow」の絶対ルール",
        "examples": "話し方の見本",
        "shop_info": "本日・明日・明後日の日付",
        "constraints": "現時点での制約",
        "human_handoff": "折り返し対応（Human Handoff）に関する絶対ルール",
        "shop_knowledge_rules": "店舗情報の質問への回答ルール（重要・必ず守ってください）",
        "customer_memory": "常連のお客様の認識に関するルール",
        "customer_context": "本人確認後のご利用情報",
        "time_ambiguity": "時刻の午前/午後（AM/PM）解釈ルール",
        "relative_date": "相対的な日時表現（今日・明日・明後日）の絶対日付への変換ルール（重要・必ず守ってください）",
        "booking_safety": "予約成立の宣言に関する絶対ルール（最重要・必ず守ってください）",
    }
    positions = {}
    for key, marker in markers.items():
        idx = instructions.find(marker)
        assert idx != -1, f"期待したセクションがinstructionsに見つかりません: {key} ({marker!r})"
        positions[key] = idx

    ordered_keys = [
        "core_rules", "scope", "intent_classification", "fast_reservation_flow",
        "examples", "shop_info", "constraints", "human_handoff", "shop_knowledge_rules",
        "customer_memory", "customer_context", "time_ambiguity", "relative_date", "booking_safety",
    ]
    for a, b in zip(ordered_keys, ordered_keys[1:]):
        assert positions[a] < positions[b], (
            f"セクション順序が崩れています: {a}({positions[a]}) が {b}({positions[b]}) より後ろにあります"
        )
    assert positions["booking_safety"] == max(positions.values()), (
        f"Booking Safetyが最後のセクションではありません: {positions}"
    )

    new_rule_idx = instructions.find("NOISE RECOVERY")
    assert positions["fast_reservation_flow"] < new_rule_idx < positions["examples"], (
        "NOISE RECOVERYルールがFast Reservation Flowセクション内の想定位置にありません"
    )


async def _test_build_instructions_direct(shop_id):
    from app.database import AsyncSessionLocal
    from app.models.shop import Shop
    from app.services.realtime_voice_ai import build_realtime_instructions

    async with AsyncSessionLocal() as session:
        shop = await session.get(Shop, shop_id)
        assert shop is not None
        instructions = await build_realtime_instructions(session, shop, None)
        _assert_section_order(instructions)
        # テンプレート内の改行位置（折り返し）は圧縮で変わり得るため、
        # 改行を除去した上でフレーズ自体の存在を検証する。
        # TASK B: 先頭の「ありがとうございます。」は撤廃済み（上記参照）。
        assert "何名様でのご予約でしょうか？」" in instructions.replace("\n", "")

    print("5. build_realtime_instructions() 全体でのセクション出現順序・新ルールの実挿入位置: OK")


async def main():
    _test_new_rule_wording()
    _test_bad_good_examples()
    _test_new_rule_position()
    _test_adjacent_existing_rules_unchanged()
    _test_realtime_tools_unchanged()

    from app.main import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-noise-recovery-test@example.com",
                "password": "password123",
                # 注意: 店名・オーナー名にテスト対象の検索文字列
                # 「NOISE RECOVERY」を含めない（build_realtime_instructions()の
                # 店舗情報セクションに店名が挿入されるため、含めると
                # instructions.find("NOISE RECOVERY")がFast Reservation Flow
                # セクション内の本来のルール文言より先に店名側にマッチして
                # しまい、セクション順序テストが誤って失敗する）。
                "display_name": "雑音復帰調査テストオーナー",
            })
            assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
            owner_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "雑音復帰調査テスト店", "category": "レストラン",
                "address": "東京都渋谷区5-5-5",
            }, headers=owner_headers)
            assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
            shop_id = r.json()["shop_id"]

        await _test_build_instructions_direct(shop_id)

    print("\n=== ALL smoke_test_noise_recovery_instructions.py CHECKS PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
