"""
RECEPTRA — DUPLICATE RESPONSE HOTFIX / CALLBACK誤分類対策
契約テスト（R1〜R6 / C1〜C10）

背景: 実機RESPONSE_ORIGIN診断ログにより、(A) NAME→ROUTING/ROUTING→
RESERVATION双方でserver auto response + phase_transition follow-upの
2重生成が起きていたこと、(B) 「担当者から折り返し連絡がほしい」という
明確なCALLBACK意図の発話が誤ってphase_transition_to_reservationへ
分類されたことが確認された。

このテストは、上記2つの問題に対する対策
（frontend/public/js/realtime-voice-engine.jsのforceFollowUpForTransition
一般化、app/services/realtime_voice_ai.pyのclassify_intent Tool
description強化）が正しく反映されており、かつユーザー指示で明示的に
保護対象とされた既存機構（routingHasReceivedUserTurn・legacy_full
フォールバック・classify_intentの構造化Tool呼び出し）を壊していないことを
静的に検証する（Realtime APIへの実際の呼び出しは行わない。実機再テストは
別途ユーザー側で行う想定）。

R1-R6: ROUTING phaseの契約
C1-C10: CALLBACK意図分類・CALLBACK terminalの契約
"""
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(
    tempfile.mkdtemp(), "smoke_duplicate_response_hotfix_r_c.db"
)
os.environ["SECRET_KEY"] = "smoke-test-secret-key-duplicate-response-hotfix"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

_ENGINE_JS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "frontend", "public", "js", "realtime-voice-engine.js",
)


def _read_engine_js():
    with open(_ENGINE_JS_PATH, "r", encoding="utf-8") as f:
        return f.read()


# ============================== R1-R6 ==============================

def _test_r1_no_immediate_legacy_full_arm_on_routing_entry():
    """R1: ROUTING突入直後（routingHasReceivedUserTurn===false）の段階では
    legacy_fullを即座にarmしない。sendRealtimePhaseSessionUpdate側で
    targetPhase==='routing'のときroutingHasReceivedUserTurn=falseへ
    戻すことと、legacy_full arm条件自体がroutingHasReceivedUserTurnを
    必須としていることの両方を確認する（PHASE ORDER HOTFIX回帰確認）。"""
    js = _read_engine_js()
    assert "if (targetPhase === 'routing') {" in js
    reset_idx = js.index("if (targetPhase === 'routing') {")
    reset_block = js[reset_idx:reset_idx + 200]
    assert "routingHasReceivedUserTurn = false;" in reset_block, (
        "ROUTING遷移時にroutingHasReceivedUserTurnをfalseへ戻す既存ロジックが見当たりません"
    )
    arm_signature = (
        "if (currentRealtimePhase === 'routing'\n"
        "                    && routingHasReceivedUserTurn\n"
        "                    && !responseHasFunctionCall\n"
        "                    && pendingPhaseTransitionTarget === null\n"
        "                    && !phaseTransitionInProgress) {"
    )
    assert arm_signature in js, "legacy_full arm条件がroutingHasReceivedUserTurnを必須としていません"
    print("R1. ROUTING突入直後はlegacy_fullを即座にarmしない（routingHasReceivedUserTurnゲート維持）: OK")


def _test_r2_routing_receives_user_purpose():
    """R2: ROUTING在中の実際のユーザー発話commit（USER_AUDIO_BUFFER_COMMITTED）
    受信時にのみroutingHasReceivedUserTurn=trueとなる（AI自身の強制follow-up
    応答からは発火しない）契約が維持されている。"""
    js = _read_engine_js()
    assert "if (currentRealtimePhase === 'routing') {\n" in js
    idx = js.index("routingHasReceivedUserTurn = true;")
    preceding = js[max(0, idx - 600):idx]
    assert "USER_AUDIO_BUFFER_COMMITTED" in preceding, (
        "routingHasReceivedUserTurn=trueがUSER_AUDIO_BUFFER_COMMITTEDコンテキスト外で設定されています"
    )
    print("R2. ROUTING在中の実際のユーザー発話受信でroutingHasReceivedUserTurn=trueになる契約が維持: OK")


def _test_r3_classify_intent_flows_via_structured_tool():
    """R3: classify_intentはarmPhaseTransitionAfterResponse()を呼ぶのみで、
    新しいJSキーワード/if-else分類ロジックは追加されていない（構造化Tool
    呼び出しのみで意図を受け取る既存設計の維持）。"""
    js = _read_engine_js()
    fn_idx = js.index("async function callClassifyIntentTool(args) {")
    fn_end = js.index("\n        }\n", fn_idx)
    fn_body = js[fn_idx:fn_end]
    assert "armPhaseTransitionAfterResponse(intent)" in fn_body
    assert "intent === 'reservation' || intent === 'callback'" in fn_body
    # 新規キーワードマッチング（indexOf等による文字列判定）を追加していないこと
    forbidden_patterns = ["includes('折り返し'", "indexOf('折り返し'", ".match(/折り返し/"]
    for pat in forbidden_patterns:
        assert pat not in fn_body, f"callClassifyIntentTool内に禁止されたキーワード判定が見つかりました: {pat}"
    print("R3. classify_intentは構造化Tool呼び出し（intent enum）のみでarmする既存設計を維持（新規キーワード判定なし）: OK")


def _test_r4_no_response_competition_on_purpose_turn():
    """R4: PURPOSEターンでも（routing→reservation/callback遷移でも）、
    server auto responseが既に次フェーズの質問を言い切っていればforceFollowUp
    はfalseとなり、同一ターンでの競合応答は発生しない
    （D1/D2と同一ロジックの再確認。テストファイルはtest_duplicate_response_
    hotfix_d1_d7.jsに分離済みのため、ここではロジックの存在のみ確認）。"""
    js = _read_engine_js()
    assert "alreadyCompletedInlineForTransition" in js
    assert "targetPhaseForTransition === 'reservation' && expectedAnswerType === 'SHORT_ANSWER'" in js
    assert "targetPhaseForTransition === 'callback' && expectedAnswerType === 'PHONE'" in js
    print("R4. PURPOSEターンでのserver-auto+phase-transition競合防止ロジックが存在する: OK")


def _test_r5_no_legacy_full_on_successful_classify():
    """R5: classify_intent呼び出しに成功した場合（pendingPhaseTransitionTarget
    が既にreservation/callbackにセット済み）、legacy_full armブロックは
    上書きしない（pendingPhaseTransitionTarget === nullガードによる安全側
    設計の確認）。"""
    js = _read_engine_js()
    arm_signature = "pendingPhaseTransitionTarget === null\n                    && !phaseTransitionInProgress) {"
    assert arm_signature in js, "legacy_full arm条件にpendingPhaseTransitionTarget===nullガードが見当たりません"
    print("R5. classify_intent成功時（pendingPhaseTransitionTarget設定済み）はlegacy_fullで上書きしない: OK")


def _test_r6_real_legacy_full_fallback_preserved():
    """R6: 本当に分類できなかった場合（responseHasFunctionCall===false）の
    legacy_fullフォールバック自体は削除されていない。"""
    js = _read_engine_js()
    assert "!responseHasFunctionCall" in js
    assert "pendingPhaseTransitionTarget = 'legacy_full';" in js
    print("R6. 真に分類不能な場合のlegacy_fullフォールバックは削除されず維持されている: OK")


# ============================== C1-C10 ==============================

_CALLBACK_EXAMPLE_PHRASES = [
    "担当者から折り返してほしい",
    "担当者から電話してほしい",
    "担当者と話したい",
    "人と話したい",
    "スタッフから連絡がほしい",
]


def _get_classify_intent_tool_description():
    from app.services.realtime_voice_ai import _ROUTING_TOOLS

    tools = [t for t in _ROUTING_TOOLS if t.get("name") == "classify_intent"]
    assert len(tools) == 1, "classify_intent Toolが_ROUTING_TOOLSに存在しません"
    return tools[0]["description"]


def _test_c1_to_c5_callback_example_phrases_present():
    """C1-C5: ユーザー指定の5つのCALLBACK例文フレーズが、classify_intent
    Toolのdescription内にintent=callbackの例として明示されている。"""
    desc = _get_classify_intent_tool_description()
    for i, phrase in enumerate(_CALLBACK_EXAMPLE_PHRASES, start=1):
        assert phrase in desc, f"C{i}: CALLBACK例文フレーズが見当たりません: {phrase}"
        print(f"C{i}. classify_intent descriptionにCALLBACK例文フレーズが含まれる（{phrase}）: OK")


def _test_c6_callback_phrases_never_map_to_reservation():
    """C6: 5つのCALLBACK例文フレーズが、たとえ会話の中で予約について話して
    いたとしても、この時点の希望としてはintent=callbackであると明示されている
    （予約への誤混同を禁止する文言）。"""
    desc = _get_classify_intent_tool_description()
    assert "たとえ会話の中で予約について話していた" in desc
    assert "としても、この時点の希望としてはintent=callback" in desc
    assert "混同しないでください" in desc
    print("C6. CALLBACK例文は予約の話題と混同せずintent=callbackとする契約が明記されている: OK")


def _test_c7_classify_intent_arms_callback_phase_transition():
    """C7: intent=callbackがarg-zero経由（値そのものをコード側でハードコード
    判定せず）でarmPhaseTransitionAfterResponse('callback')経由のCALLBACK
    phase遷移につながる既存経路が維持されている。"""
    js = _read_engine_js()
    fn_idx = js.index("async function callClassifyIntentTool(args) {")
    fn_end = js.index("\n        }\n", fn_idx)
    fn_body = js[fn_idx:fn_end]
    assert "armPhaseTransitionAfterResponse(intent)" in fn_body
    print("C7. classify_intent(intent=callback)がCALLBACK phase遷移をarmする既存経路が維持されている: OK")


def _test_c8_request_callback_human_handoff_preserved_impl():
    """C8: 既存のrequest_callback（Human Handoff/CALLBACK terminal）フローが
    削除・改変されていない（Tool定義の存在確認）。"""
    import importlib
    rv = importlib.import_module("app.services.realtime_voice_ai")
    src = rv.__file__
    with open(src, "r", encoding="utf-8") as f:
        py_src = f.read()
    assert '"name": "request_callback"' in py_src, "request_callback Tool定義が見当たりません"
    print("C8. request_callback（Human Handoff/CALLBACK terminal）Tool定義が維持されている: OK")


def _test_c9_callback_terminal_closing_line_verbatim():
    """C9: CALLBACK terminalの正式closing line
    「担当者から折り返し連絡しますので、電話を切ってお待ちください。」が
    一字一句変更されていない。"""
    import importlib
    rv = importlib.import_module("app.services.realtime_voice_ai")
    with open(rv.__file__, "r", encoding="utf-8") as f:
        py_src = f.read()
    assert "担当者から折り返し連絡しますので、電話を切ってお待ちください。" in py_src, (
        "CALLBACK terminalの正式closing lineが見当たりません（一字一句変更されている可能性）"
    )
    print("C9. CALLBACK terminalの正式closing lineが一字一句維持されている: OK")


def _test_c10_reservation_contract_excludes_bare_callback_request():
    """C10: 「担当者から折り返してほしい」等の折り返し希望単独の発言は、
    reservation descriptionの定義（来店・来院等の予約、来店希望日時や空き
    状況の確認）には該当せず、reservationとcallbackの記述が明確に分離
    されていることを確認する。"""
    desc = _get_classify_intent_tool_description()
    reservation_idx = desc.index("intent=reservationは")
    callback_idx = desc.index("intent=callbackは")
    reservation_desc = desc[reservation_idx:callback_idx]
    assert "折り返し" not in reservation_desc, (
        "reservation側の説明に折り返し関連の記述が混入しています（境界が曖昧になっています）"
    )
    assert "来店" in reservation_desc or "予約" in reservation_desc
    print("C10. reservation契約は来店・予約関連のみに限定され、折り返し単独の希望とは明確に分離されている: OK")


def main():
    _test_r1_no_immediate_legacy_full_arm_on_routing_entry()
    _test_r2_routing_receives_user_purpose()
    _test_r3_classify_intent_flows_via_structured_tool()
    _test_r4_no_response_competition_on_purpose_turn()
    _test_r5_no_legacy_full_on_successful_classify()
    _test_r6_real_legacy_full_fallback_preserved()
    _test_c1_to_c5_callback_example_phrases_present()
    _test_c6_callback_phrases_never_map_to_reservation()
    _test_c7_classify_intent_arms_callback_phase_transition()
    _test_c8_request_callback_human_handoff_preserved_impl()
    _test_c9_callback_terminal_closing_line_verbatim()
    _test_c10_reservation_contract_excludes_bare_callback_request()
    print("\n=== ALL R1-R6 / C1-C10 CHECKS PASSED ===")


if __name__ == "__main__":
    main()
