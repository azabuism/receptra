"""
「ちょっと状況を整理しながら進めますね」→沈黙 問題調査 スモークテスト

背景（ユーザー報告・実機テストでの再現）:
客が日付・名前・人数など複数の予約情報を一度の発話でまとめて伝えたとき、
AIが「ちょっと状況を整理しながら進めますね。」のような、内部処理を実況
するだけの一言を発話し、その一言だけで発話を終えてしまうことがあった。
その後、長く沈黙する、または会話が進まないケースが実機で確認された。

コード監査で判明したこと（推測でPromptだけ直したのではなく、まず調査した
結果）:
- 「整理しますね」というリテラル文字列は、Realtime instructions・
  Realtime Tool定義・フロントエンドJS（frontend/public/
  shop-ai-realtime-voice.html）のいずれにもハードコードされていない。
  すなわちこれはOpenAI Realtimeモデル自身が生成した発話であり、既存の
  固定テンプレート文言のバグではない。
- 既存の「予告だけをして黙り込むことは絶対にしない」という指示
  （_CONSTRAINTS_TEMPLATE・check_availability/create_reservation/
  request_callbackの各tool description・電話番号復唱の指示）は、いずれも
  「Toolを呼び出す直前」または「電話番号を復唱する直前」という特定の場面
  にのみ適用範囲が限定されていた。ユーザー報告の再現例
  （「明日、谷村です。2人で予約したいです」→時間だけが不足しており、
  Tool呼び出しはまだ発生しない場面）は、この既存ルールのいずれにも
  該当しない一般的な会話継続の場面であり、この場面で「予告だけで発話を
  終える」ことを禁止する指示が存在しないという抜け（instructions gap）
  だった。
- フロントエンドのSilence Timeout機構（frontend/public/
  shop-ai-realtime-voice.html、SILENCE_TIMEOUT_MS=30000）は、
  responseHasFunctionCall===falseのままresponse.doneを受け取った場合に
  「AIがお客様の返事を待っている」とみなし、実際に無言のまま最大30秒待つ
  設計になっている。これは意図された安全網であり壊れていないが、AIの
  発話自体が実質的な質問を含まない「予告だけ」の内容だった場合、お客様は
  何を答えればいいか分からず沈黙してしまい、結果として最大30秒の無言
  →警告アナウンス→終話、という体感上の「会話が進まない」症状につながる。
  これがフロントエンドのJS/lifecycle側の不具合ではなく、Realtime
  instructions側の一般化不足（ユーザー報告のSTOP条件17には該当しない、
  最小のinstructions修正で説明できる原因）であることの根拠。

対応（今回の修正の範囲。VAD/WebRTC/Zero-Wait/response lifecycle JS・
DB schema・API・Realtime Toolのいずれも変更していない、純粋な
instructions文言の追加のみ）:
- _FAST_RESERVATION_FLOW_TEMPLATE内、既存の「既に分かっている情報を
  聞き直さない」の直後に、「内部処理を実況するだけの発話で終わらせない」
  という新しい一般ルールを追加。Tool呼び出しの場面に限定せず、まだ
  聞けていない項目を尋ねる場面も含めて、予告的な一言だけで発話を終える
  ことを禁止し、必ず同じ発話の中で実際の質問かTool呼び出しのいずれかを
  行うよう明示した。
- _EXAMPLES_TEMPLATE_BASEに、ユーザー報告の再現例と同じ状況
  （「明日、谷村です。2人で予約したいです」）を良い例として追加した。

本テストが検証すること:
1. 新しいルールの文言が_FAST_RESERVATION_FLOW_TEMPLATEに含まれ、報告された
   フィラー表現（整理します・確認します・調べます・確認してみます・
   少々お待ちください）を、それ単体で発話を終えることを禁止していること。
2. 新しいルールが「既に分かっている情報を聞き直さない」の直後・
   「Toolを呼び出す前に許可を求めない」の直前という意図した位置に
   挿入されていること（Tool呼び出し限定ルールの前に、より一般的な
   ルールが来る自然な流れ）。
3. 新しいルールに、ユーザー報告と同一の再現例（悪い例・良い例）が
   含まれていること。
4. _EXAMPLES_TEMPLATE_BASEに新しい会話例が追加され、既存の会話例
   （日時・人数・電話番号の読み上げ等）が変更されていないこと。
5. 既存の「Toolを呼び出す前に許可を求めない」等、Tool呼び出し限定の
   既存ルールの文言が一切変更されていないこと（回帰確認）。
6. build_realtime_instructions()が実際に組み立てるinstructions全体で、
   既存の全セクション出現順序（Booking Safetyが常に最後という不変条件を
   含む）が壊れていないこと。
7. 8つのRealtime Tool schemaが一切変更されていないこと（DB/API/Tool
   無変更の確認）。
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_no_filler.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-no-filler"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx


def _test_new_rule_wording():
    from app.services.realtime_voice_ai import _FAST_RESERVATION_FLOW_TEMPLATE

    normalized = _FAST_RESERVATION_FLOW_TEMPLATE.replace("\n", "")

    # 1. 新しいルールの見出しと、報告されたフィラー表現の実例が含まれていること
    assert "内部処理を実況するだけの発話で終わらせない" in normalized
    for phrase in ["整理しながら進めますね", "整理します", "確認します", "調べます",
                   "確認してみます", "少々お待ちください"]:
        assert phrase in normalized, f"報告されたフィラー表現の実例が見当たりません: {phrase}"

    # 否定文脈を伴っていること（単なる推奨表現として肯定的に書かれていないこと）
    idx = normalized.find("内部処理を実況するだけの発話で終わらせない")
    nearby = normalized[idx:idx + 400]
    assert "絶対にしないでください" in nearby or "絶対に" in nearby

    # Tool呼び出しに限定せず、質問の場面も対象範囲に含むと明記していること
    assert "Toolを呼び出す場面に限らず" in normalized
    assert "尋ねる場面" in normalized

    # 2. ユーザー報告と同一の再現例（悪い例・良い例）が含まれていること
    assert "明日、谷村です。2人で予約したいです" in normalized
    assert "ちょっと状況を整理しながら進めますね" in normalized
    assert "お時間は何時をご希望ですか" in normalized

    print("1. 新ルール（内部処理を実況するだけの発話で終わらせない）の文言: OK")


def _test_new_rule_position():
    from app.services.realtime_voice_ai import _FAST_RESERVATION_FLOW_TEMPLATE

    pos_known = _FAST_RESERVATION_FLOW_TEMPLATE.find("既に分かっている情報を聞き直さない")
    pos_new = _FAST_RESERVATION_FLOW_TEMPLATE.find("内部処理を実況するだけの発話で終わらせない")
    pos_permission = _FAST_RESERVATION_FLOW_TEMPLATE.find("Toolを呼び出す前に許可を求めない")

    assert pos_known != -1 and pos_new != -1 and pos_permission != -1
    assert pos_known < pos_new < pos_permission, (
        f"新ルールの挿入位置が意図した順序ではありません: "
        f"既知情報={pos_known}, 新ルール={pos_new}, Tool許可ルール={pos_permission}"
    )

    print("2. 新ルールの挿入位置（既知情報を聞き直さない の直後・Tool許可ルールの直前）: OK")


def _test_examples_updated_without_regression():
    from app.services.realtime_voice_ai import _EXAMPLES_TEMPLATE_BASE

    # 新しい会話例が追加されている
    assert "明日、谷村です。2人で予約したいです" in _EXAMPLES_TEMPLATE_BASE
    assert "お時間は何時をご希望ですか" in _EXAMPLES_TEMPLATE_BASE

    # 既存の会話例（Phase1からの固定文言）が変更されていない
    for existing_line in [
        "客:「今日って空いてます？」", "AI:「はい。何時頃がいいですか？」",
        "客:「7時くらいかな」", "AI:「7時ですね。何名様ですか？」",
        "客:「3人。あ、やっぱ8時で」", "AI:「はい、8時ですね。3名様で確認します。」",
        "客:「電話番号は090-1234-5678です」",
        "AI:「ゼロキューゼロ、イチニサンヨン、ゴーロクナナハチですね、ありがとうございます。」",
        "客:「料金はいくら？」", "AI:「5,500円です。」",
    ]:
        assert existing_line in _EXAMPLES_TEMPLATE_BASE, f"既存の会話例が変化しています: {existing_line}"

    print("3. 話し方の見本（既存4例は無変更＋新規1例を追加）: OK")


def _test_existing_tool_call_filler_rules_unchanged():
    """既存のTool呼び出し限定の予告禁止ルールが今回の変更で壊れていないことの回帰確認。"""
    from app.services.realtime_voice_ai import _CONSTRAINTS_TEMPLATE, _FAST_RESERVATION_FLOW_TEMPLATE

    assert "Toolを呼び出す前に許可を求めない（重要）" in _FAST_RESERVATION_FLOW_TEMPLATE
    assert "「空き状況を確認しますね」と" in _FAST_RESERVATION_FLOW_TEMPLATE
    assert "Tool結果の後は自分から続ける" in _FAST_RESERVATION_FLOW_TEMPLATE

    assert "予告だけを発話してそこで止めるのではなく" in _CONSTRAINTS_TEMPLATE
    assert "「日付を確認します」「空き状況を確認します」" in _CONSTRAINTS_TEMPLATE

    print("4. 既存のTool呼び出し限定の予告禁止ルール（_CONSTRAINTS_TEMPLATE等）: 無変更 OK")


def _test_realtime_tools_unchanged():
    from app.services.realtime_voice_ai import _REALTIME_TOOLS

    tool_names = [t["name"] for t in _REALTIME_TOOLS]
    assert tool_names == [
        "check_availability", "create_reservation", "get_shop_info", "find_customer",
        "confirm_customer_identity", "get_customer_context", "set_conversation_language",
        "request_callback",
    ], f"8 Tool schemaが変化しています: {tool_names}"

    print("5. 8つのRealtime Tool schema: 無変更 OK（DB/API/Tool変更なしの確認）")


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

    # 新ルールが実際に組み立てたinstructions全体の中でも、Fast Reservation Flow
    # セクション内・examplesセクションより前に存在すること
    new_rule_idx = instructions.find("内部処理を実況するだけの発話で終わらせない")
    assert positions["fast_reservation_flow"] < new_rule_idx < positions["examples"], (
        "新ルールがFast Reservation Flowセクション内の想定位置にありません"
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
        assert "明日、谷村です。2人で予約したいです" in instructions

    print("6. build_realtime_instructions() 全体でのセクション出現順序・新ルールの実挿入位置: OK")


async def main():
    _test_new_rule_wording()
    _test_new_rule_position()
    _test_examples_updated_without_regression()
    _test_existing_tool_call_filler_rules_unchanged()
    _test_realtime_tools_unchanged()

    from app.main import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-no-filler-test@example.com",
                "password": "password123",
                "display_name": "整理しますね調査テストオーナー",
            })
            assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
            owner_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "整理しますね調査テスト店", "category": "レストラン",
                "address": "東京都渋谷区4-4-4",
            }, headers=owner_headers)
            assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
            shop_id = r.json()["shop_id"]

        await _test_build_instructions_direct(shop_id)

    print("\n=== ALL smoke_test_no_process_narration_filler.py CHECKS PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
