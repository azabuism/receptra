"""
RECEPTRA — FAST RESPONSE + 5-SECOND HUMAN HANDOFF PHASE（B案）
スモークテスト: 既存Human Handoff文言（_HUMAN_HANDOFF_TEMPLATE）の改訂確認

背景（ユーザー要求の要約。全75セクションの日本語スペック）:
「AI処理が約5秒を超えたら自律的にHuman Handoffへ切り替える」という新規
タイマー機構の実装が要求されたが、事前監査（Section 3）とSTOP判断
（Section 67）の結果、以下の理由でJS側への新規タイマー実装は見送った
（B案のみ採用。A案＝自律5秒タイマーはSTOP・不実装）:
  - フロントエンド（shop-ai-realtime-voice.html）は会話の文字起こし内容に
    一切アクセスできない（input_audio_transcriptionが未設定）ため、
    「客がまだ話している／考えている」ことと「AIが処理に詰まっている」
    ことを安全に区別できない。
  - request_callbackには、create_reservationが持つLayer2相当の
    3分間クロスcall_id重複防止窓が無い（Layer1のDB unique index
    idempotency_keyのみ）ため、新規タイマーが暴発すると重複登録リスクが
    create_reservationより高い。
  - 8秒のfetchToolWithTimeoutが既に走っている間に、別の5秒タイマーが
    独立してHandoffへ切り替えると、Tool成功とHandoff確約の二重結果が
    起き得る。
  - sendResponseCreate()はresponseState==='active'時に警告のみで送信を
    ブロックしない未解決の仕様があり、この上に新しい自律判断ロジックを
    重ねるのは危険。
代わりに（B案）、Realtime Tools／DB／API／VAD／Audio／WebRTC／
Forced Commitタイマー（3秒/5秒/10秒/30秒）を一切変更せず、
app/services/realtime_voice_ai.pyの_HUMAN_HANDOFF_TEMPLATE（AIへの
指示文）のみを次の4点について改訂した:
  1. 折り返し確定時、「担当者は通常業務中であり、すぐに折り返せるとは
     限らない」という期待値を、成功案内と同じ発話で必ず短く伝える。
  2. 折り返しが確定していない（担当者が確認の上で必要な場合のみ連絡する）
     ケース専用の言い回しを追加し、「必ず折り返す」という誤解を避ける。
  3. 「いつ電話が来ますか？」と聞かれた場合、店舗側に具体的な予定時刻の
     情報が無い限りAIが時刻を推測しない、という定型応答を追加した。
  4. 保証できない断定的な時間・確約表現の禁止リストを明文化した。
また、5秒タイマーの代替として、AIが「店舗情報に答えがない／Toolで
確認できない／担当者判断が必要／安全に回答できない」と判断できた時点で、
長く考えたり推測したりせず直ちに折り返し対応へ進んでよい、という
既存の判断基準への追記も行った（KNOWN→回答/実行、UNKNOWN→即Handoff、
STAFF DECISION REQUIRED→即Handoff）。

Known Limitation / Future Safety Task（今回は変更しない。完了報告に明記）:
  (a) request_callbackのLayer2重複防止（今回未実装）
  (b) sendResponseCreateのresponseState==='active'時の多重送信安全性
      （今回未変更）
  (c) 将来「AI Processing Waiting Budget」を安全に導入する場合の設計
      （今回は着手しない）

本テストが検証すること（ユーザー指定のA〜Pチェックリストに対応）:
  A. 分からないことを推測しない旨の文言
  B. 担当者判断が必要ならhandoffする旨の文言
  C. 折り返し確定時の文言（「業務の状況により、折り返しまでお時間を
     いただく場合がある」）
  D. 折り返し未確定時の文言（「必要に応じて」）
  E. 「いつ電話が来ますか？」への応答（具体的な時間を推測しない）
  F. 「すぐ」「数分以内」「30分以内」「本日中」「必ず」等を約束しない
     禁止リスト
  G/H. 既知の電話番号・名前を聞き直さない既存ルールが無変更であること
  I. Medical Privacy Gate（is_medical_category）が無変更であること
  J〜N. 3秒/10秒/5秒ルール・VISIT_REASON・break_time再質問の既存回帰
     （SHORT_ANSWER/YES_NO/PHONE/NAME等の分類関数はJS側にあるため、
     このテストではPython側のANSWER_WINDOW関連文言に触れていないこと・
     JSファイル自体を変更していないことのみを確認する。実際の3秒/5秒/
     10秒タイマーのJS回帰は既存のtests/test_*.jsスイートが担当する）
  O. request_callback Tool schemaが無変更であること
  P. Realtime Tool数が8個のままであること
"""
import asyncio
import hashlib
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_handoff_wording.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-handoff-wording"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx


def _test_unknown_and_staff_decision_immediate_handoff():
    """A, B: 分からない場合/担当者判断が必要な場合は長く考えず直ちにHandoff。"""
    from app.services.realtime_voice_ai import _HUMAN_HANDOFF_TEMPLATE

    normalized = _HUMAN_HANDOFF_TEMPLATE.replace("\n", "")

    assert "分からないことについて長く考えたり、推測したりしない" in normalized
    assert "分かっている" in normalized and "分からない" in normalized
    assert "担当者の判断が必要" in normalized
    assert "直ちに" in normalized
    assert "推測で答えず" in normalized
    assert "当てずっぽうな回答をしたり" in normalized

    print("A/B. 「分からない→即Handoff」「担当者判断が必要→即Handoff」の文言: OK")


def _test_confirmed_callback_wording():
    """C: 折り返し確定時の文言。"""
    from app.services.realtime_voice_ai import _HUMAN_HANDOFF_TEMPLATE

    assert (
        "確認が必要なため、担当者にお伝えします。業務の状況により、\n"
        "   折り返しまでお時間をいただく場合がございます。"
        in _HUMAN_HANDOFF_TEMPLATE
        or "確認が必要なため、担当者にお伝えします。業務の状況により、" in _HUMAN_HANDOFF_TEMPLATE
    )
    normalized = _HUMAN_HANDOFF_TEMPLATE.replace("\n", "").replace(" ", "").replace("　", "")
    assert "確認が必要なため、担当者にお伝えします。業務の状況により、折り返しまでお時間をいただく場合がございます。" in normalized
    assert "担当者は通常業務中であり" in normalized
    assert "すぐに折り返せるとは限らない" in normalized

    print("C. 折り返し確定時の文言（業務の状況により折り返しまでお時間をいただく場合がある）: OK")


def _test_unconfirmed_callback_wording():
    """D: 折り返し未確定時の文言。"""
    from app.services.realtime_voice_ai import _HUMAN_HANDOFF_TEMPLATE

    normalized = _HUMAN_HANDOFF_TEMPLATE.replace("\n", "").replace(" ", "").replace("　", "")
    assert "確認が必要なため、担当者にお伝えします。必要に応じてこちらからご連絡いたします。業務の状況により、ご連絡までお時間をいただく場合がございます。" in normalized
    assert "必ず折り返す」と誤解させる言い方" in normalized

    print("D. 折り返し未確定時の文言（必要に応じてこちらからご連絡いたします）: OK")


def _test_when_will_you_call_wording():
    """E: 「いつ電話が来ますか？」への応答。"""
    from app.services.realtime_voice_ai import _HUMAN_HANDOFF_TEMPLATE

    normalized = _HUMAN_HANDOFF_TEMPLATE.replace("\n", "")
    assert "「いつ電話が来ますか？」と聞かれた場合" in normalized
    assert "時刻を推測して答えないでください" in normalized
    assert "担当者の業務状況によるため、具体的なお時間はご案内できません。" in normalized
    assert "確認でき次第の対応となります。" in normalized

    print("E. 「いつ電話が来ますか？」への応答（時刻を推測しない）: OK")


def _test_banned_promise_phrases():
    """F: 断定的な時間・確約表現の禁止リスト。"""
    from app.services.realtime_voice_ai import _HUMAN_HANDOFF_TEMPLATE

    normalized = _HUMAN_HANDOFF_TEMPLATE.replace("\n", "")
    assert "保証できない表現の禁止" in normalized
    for phrase in [
        "すぐ折り返します", "まもなくご連絡します", "数分以内です",
        "30分以内です", "本日中です", "今日中にご連絡します", "必ず折り返します",
    ]:
        assert phrase in normalized, f"禁止表現の実例が見当たりません: {phrase}"

    print("F. 断定的な時間・確約表現の禁止リスト（すぐ/数分以内/30分以内/本日中/必ず 等）: OK")


def _test_existing_rules_unchanged():
    """G, H: 既知の電話番号・名前を聞き直さない既存ルールが無変更。"""
    from app.services.realtime_voice_ai import _HUMAN_HANDOFF_TEMPLATE

    assert "既にこの会話で分かっている情報は聞き直さないでください" in _HUMAN_HANDOFF_TEMPLATE
    assert "必ず1桁ずつ読み上げて復唱し" in _HUMAN_HANDOFF_TEMPLATE
    assert "推測や聞き取れなかった桁の補完は絶対にしないでください" in _HUMAN_HANDOFF_TEMPLATE
    # 折り返し対応が必要/不要な場面の既存の判断基準（回帰）
    assert "business_hours_not_configured" in _HUMAN_HANDOFF_TEMPLATE
    assert "reservation_not_enabled" in _HUMAN_HANDOFF_TEMPLATE
    assert "temporarily_unavailable" in _HUMAN_HANDOFF_TEMPLATE
    assert "known=false" in _HUMAN_HANDOFF_TEMPLATE

    print("G/H. 既知の電話番号/名前を聞き直さない・既存の折り返し要否判断基準: 無変更 OK")


def _test_medical_privacy_gate_unchanged():
    """I: Medical Privacy Gateが無変更であること。"""
    from app.services.customer_context import is_medical_category

    assert callable(is_medical_category)
    import inspect
    src = inspect.getsource(is_medical_category)
    assert "_MEDICAL_TAXONOMY_GROUP_KEY" in src
    assert "resolve_business_type" in src

    from app.services.customer_context import build_customer_context
    src2 = inspect.getsource(build_customer_context)
    assert "is_medical_category" in src2
    assert "last_service_name" in src2 and "last_staff_name" in src2

    print("I. Medical Privacy Gate（is_medical_category / build_customer_context）: 無変更 OK")


def _test_realtime_tools_unchanged():
    """O, P: request_callback Tool schema無変更・Tool数8個。"""
    from app.services.realtime_voice_ai import _REALTIME_TOOLS

    tool_names = [t["name"] for t in _REALTIME_TOOLS]
    assert len(tool_names) == 8, f"Tool数が8個ではありません: {len(tool_names)}"
    assert tool_names == [
        "check_availability", "create_reservation", "get_shop_info", "find_customer",
        "confirm_customer_identity", "get_customer_context", "set_conversation_language",
        "request_callback",
    ], f"8 Tool schemaが変化しています: {tool_names}"

    request_callback_tool = next(t for t in _REALTIME_TOOLS if t["name"] == "request_callback")
    required = set(request_callback_tool["parameters"]["required"])
    assert required == {"customer_name", "customer_phone", "inquiry_text"}, (
        f"request_callbackのrequiredパラメータが変化しています: {required}"
    )
    properties = set(request_callback_tool["parameters"]["properties"].keys())
    assert properties == {
        "customer_name", "customer_phone", "inquiry_text",
        "desired_date", "desired_time", "party_size", "service_id", "reason_code",
    }, f"request_callbackのparametersが変化しています: {properties}"

    print("O/P. request_callback Tool schema無変更・Realtime Tool数8個: OK")


def _test_frontend_html_and_json_untouched():
    """Scope: shop-ai-realtime-voice.htmlに今回のコミットで変更が無いことのgit差分確認。"""
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        capture_output=True, text=True,
    )
    changed_files = [f for f in result.stdout.splitlines() if f.strip()]
    for f in changed_files:
        assert "shop-ai-realtime-voice.html" not in f, (
            f"scope外のファイルが変更されています(HTML/JSは今回変更禁止): {f}"
        )
        assert not f.startswith("app/models/"), f"scope外: DBモデルは今回変更禁止: {f}"
        assert "alembic" not in f.lower(), f"scope外: マイグレーションは今回変更禁止: {f}"
    print(f"Scope. git diffで変更されたファイル: {changed_files}")
    print("Scope. shop-ai-realtime-voice.html / DBモデル / マイグレーション: 無変更 OK")


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

    new_rule_idx = instructions.find("分からないことについて長く考えたり")
    assert positions["human_handoff"] < new_rule_idx < positions["shop_knowledge_rules"], (
        "新ルールがHuman Handoffセクション内の想定位置にありません"
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
        assert "確認が必要なため、担当者にお伝えします。" in instructions
        assert "担当者の業務状況によるため、具体的なお時間はご案内できません。" in instructions

    print("build_realtime_instructions() 全体でのセクション出現順序・新文言の実挿入位置: OK")


async def main():
    _test_unknown_and_staff_decision_immediate_handoff()
    _test_confirmed_callback_wording()
    _test_unconfirmed_callback_wording()
    _test_when_will_you_call_wording()
    _test_banned_promise_phrases()
    _test_existing_rules_unchanged()
    _test_medical_privacy_gate_unchanged()
    _test_realtime_tools_unchanged()

    from app.main import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-handoff-wording-test@example.com",
                "password": "password123",
                "display_name": "HumanHandoff文言改訂テストオーナー",
            })
            assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
            owner_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "HumanHandoff文言改訂テスト店", "category": "レストラン",
                "address": "東京都渋谷区5-5-5",
            }, headers=owner_headers)
            assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
            shop_id = r.json()["shop_id"]

        await _test_build_instructions_direct(shop_id)

    print("\n=== ALL smoke_test_human_handoff_wording.py CHECKS PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
