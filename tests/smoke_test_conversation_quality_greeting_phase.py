"""
RECEPTRA — Realtime会話品質改善フェーズ（初回挨拶・二重自己紹介・冗長発話削減）
契約テスト（STEP 6: A〜J）

背景: 実機で観測された4つの問題（1. 初回挨拶に店舗名・AIスタッフ名が
正しく含まれるか、2. AIが自己紹介を二重に行う、3. お客様の名前を最初に
聞かない、4. 冗長な発話）のうち、1〜3の根本対策として、
_resolve_greeting_text()の第一声に常にお客様のお名前を伺う質問を含めるよう
改修し（挨拶とお名前確認を1つの発話に統合）、それに伴いFAST TURN HOTFIX14の
Zero-Wait follow-up発話（＝二重自己紹介の温床）を、会話履歴への注入が成功した
場合は送らないよう変更した（frontend/public/js/realtime-voice-engine.jsの
maybeSendInitialGreeting()参照）。

本テストは、この変更が仕様どおりであることと、既存の保護対象（Tool数・
Feature1/Feature3・冗長発話を防ぐ既存の仕組み）を壊していないことを検証する。
JS側（Zero-Wait follow-up省略ロジックそのもの）はtests/test_fast_turn_hotfix14_
greeting_callback_terminal.jsで別途検証済みのため、本ファイルはバックエンド
（instructions/Tool定義）側の契約のみを対象とする。

実行: python3 tests/smoke_test_conversation_quality_greeting_phase.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.realtime_voice_ai import (
    _resolve_greeting_text,
    _build_greeting_section,
    _GREETING_NAME_QUESTION,
    _PHASE1_NAME_ROLE_TEMPLATE,
    _PHASE2_ROUTING_ROLE_TEMPLATE,
    _PHASE2A_RESERVATION_ROLE_TEMPLATE,
    _PHASE2B_CALLBACK_ROLE_TEMPLATE,
    _REALTIME_TOOLS,
)


class _FakeSettings:
    def __init__(self, staff_name=None, greeting=None):
        self.staff_name = staff_name
        self.greeting = greeting


def test_a_greeting_structure_from_fixture_not_hardcoded():
    shop_name = "ミニA/Bテスト店舗"
    staff_name = "テストAI花子"
    s = _FakeSettings(staff_name=staff_name)
    text = _resolve_greeting_text(s, shop_name)
    assert shop_name in text, f"fixture shop_name must appear verbatim: {text}"
    assert staff_name in text, f"fixture staff_name must appear verbatim: {text}"
    assert text.startswith("お電話ありがとうございます。"), f"unexpected opening: {text}"
    assert _GREETING_NAME_QUESTION in text, f"name question must be included: {text}"
    idx_shop = text.index(shop_name)
    idx_question = text.index(_GREETING_NAME_QUESTION)
    assert idx_shop < idx_question, f"shop/staff introduction must precede the name question: {text}"
    section = _build_greeting_section(s, shop_name)
    assert text in section, "the resolved greeting text must be embedded verbatim in the instructions section"
    print("A) greeting structure from fixture: OK")


def test_a2_ai_name_unset_fallback_never_fabricates():
    shop_name = "テスト店"
    text_no_settings = _resolve_greeting_text(None, shop_name)
    assert "AI受付の" not in text_no_settings, f"must not fabricate an AI staff name: {text_no_settings}"
    assert shop_name in text_no_settings
    assert _GREETING_NAME_QUESTION in text_no_settings

    s = _FakeSettings(staff_name=None, greeting=None)
    text_settings_no_name = _resolve_greeting_text(s, shop_name)
    assert "AI受付の" not in text_settings_no_name, f"must not fabricate an AI staff name: {text_settings_no_name}"
    assert _GREETING_NAME_QUESTION in text_settings_no_name
    print("A2) AI name unset fallback (no fabrication): OK")


def test_b_double_self_intro_prevention_wording():
    assert "すでに名乗り" in _PHASE1_NAME_ROLE_TEMPLATE
    assert "繰り返さず" in _PHASE1_NAME_ROLE_TEMPLATE
    assert "名乗り・お名前を伺う質問のいずれも" in _PHASE1_NAME_ROLE_TEMPLATE
    print("B) double self-intro prevention wording: OK")


def test_c_name_first_ordering_in_greeting():
    text = _resolve_greeting_text(_FakeSettings(staff_name="さくら"), "テスト店")
    assert "お名前" in text
    assert "ご用件" not in text, f"the first utterance must ask for the name, not the reason for calling: {text}"
    assert "ご用件" in _PHASE2_ROUTING_ROLE_TEMPLATE, "the visit-reason question belongs to the ROUTING phase, not the greeting"
    print("C) name-first ordering in greeting: OK")


def _normalized(s):
    return s.replace("\n", "").replace(" ", "").replace("\u3000", "")


def test_d_no_reask_when_name_already_known():
    for name, tmpl in [
        ("RESERVATION", _PHASE2A_RESERVATION_ROLE_TEMPLATE),
        ("CALLBACK", _PHASE2B_CALLBACK_ROLE_TEMPLATE),
    ]:
        norm = _normalized(tmpl)
        assert "お名前は既に伺っています" in norm, f"{name} phase must state the name is already known: {tmpl}"
        assert "改めてお名前を尋ねないでください" in norm, f"{name} phase must instruct not to re-ask the name: {tmpl}"
    print("D) no re-ask when name already known: OK")


def test_e_no_mandatory_ack_every_turn():
    combined = "\n".join([
        _PHASE1_NAME_ROLE_TEMPLATE,
        _PHASE2_ROUTING_ROLE_TEMPLATE,
        _PHASE2A_RESERVATION_ROLE_TEMPLATE,
        _PHASE2B_CALLBACK_ROLE_TEMPLATE,
    ])
    forbidden_patterns = [
        r"毎回[^\n]{0,15}(ありがとうございます|承知しました|かしこまりました)",
        r"必ず[^\n]{0,15}(ありがとうございます|承知しました|かしこまりました)と(言|話|伝え)",
        r"(ありがとうございます|承知しました|かしこまりました)を毎回",
    ]
    for pat in forbidden_patterns:
        assert not re.search(pat, combined), f"found a mandatory-every-turn ack instruction matching {pat!r}"
    print("E) no instruction mandates a bare ack every turn: OK")


def test_f_no_mandatory_bare_narration():
    combined = "\n".join([
        _PHASE1_NAME_ROLE_TEMPLATE,
        _PHASE2_ROUTING_ROLE_TEMPLATE,
        _PHASE2A_RESERVATION_ROLE_TEMPLATE,
        _PHASE2B_CALLBACK_ROLE_TEMPLATE,
    ])
    assert "少々お待ちくださいとだけ" not in combined
    assert not re.search(r"少々お待ちください[^\n]{0,10}のみ(で終|話)", combined)
    print("F) no instruction mandates bare process-narration alone: OK")


def test_g_realtime_tool_count_and_schemas_unchanged():
    names = [t["name"] for t in _REALTIME_TOOLS]
    assert len(names) == 9, f"expected exactly 9 tools, found {len(names)}: {names}"
    expected = {
        "check_availability", "create_reservation", "get_shop_info",
        "find_customer", "confirm_customer_identity", "get_customer_context",
        "set_conversation_language", "request_callback", "suggest_available_times",
    }
    assert set(names) == expected, f"tool set changed: {set(names)} != {expected}"
    print("G) Realtime tool count (9) and names unchanged: OK")


def test_h_feature1_preserved():
    by_name = {t["name"]: t for t in _REALTIME_TOOLS}
    desc = by_name["check_availability"]["description"]
    assert "business_hours_not_configured" in desc or "営業時間" in desc, "Feature 1 business-hours guidance must remain in check_availability's description"
    print("H) Feature 1 (check_availability business-hours guidance) preserved: OK")


def test_i_feature3_preserved():
    names = [t["name"] for t in _REALTIME_TOOLS]
    assert "suggest_available_times" in names, "Feature 3 tool must remain registered"
    # 実際のハンドラ・共有ヘルパーはapp/routers/realtime_voice.py
    # （suggest_available_times_tool）およびapp/routers/reservations.py
    # （_compute_day_availability）に存在する（realtime_voice_ai.pyは
    # instructions/Tool定義のみを持つモジュールのため、ここでは対象外）。
    from app.routers import realtime_voice as rv_router
    from app.routers import reservations as res_router
    assert hasattr(rv_router, "suggest_available_times_tool"), "Feature 3's handler suggest_available_times_tool must still exist"
    assert hasattr(res_router, "_compute_day_availability"), "Feature 3's shared helper _compute_day_availability must still exist"
    print("I) Feature 3 (suggest_available_times) preserved: OK")


def test_j_speak_then_work_ack_tools_unchanged_reference():
    js_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "frontend", "public", "js", "realtime-voice-engine.js")
    with open(js_path, "r", encoding="utf-8") as f:
        js_src = f.read()
    assert "const SPEAK_THEN_WORK_ACK_TOOLS = new Set(['check_availability']);" in js_src, \
        "SPEAK_THEN_WORK_ACK_TOOLS must remain exactly {check_availability}"
    print("J) SPEAK_THEN_WORK_ACK_TOOLS unchanged (check_availability only): OK")


def main():
    test_a_greeting_structure_from_fixture_not_hardcoded()
    test_a2_ai_name_unset_fallback_never_fabricates()
    test_b_double_self_intro_prevention_wording()
    test_c_name_first_ordering_in_greeting()
    test_d_no_reask_when_name_already_known()
    test_e_no_mandatory_ack_every_turn()
    test_f_no_mandatory_bare_narration()
    test_g_realtime_tool_count_and_schemas_unchanged()
    test_h_feature1_preserved()
    test_i_feature3_preserved()
    test_j_speak_then_work_ack_tools_unchanged_reference()
    print("Realtime会話品質改善フェーズ STEP6 契約テスト: ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
