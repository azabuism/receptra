"""
RECEPTRA — TASK B HOTFIX 2
実機「ご用件を何度も聞く」ループの根本修正・prompt content contract test

根本原因（詳細はユーザーへの最終報告を参照。JS側のevent-order検証は
tests/test_hotfix2_routing_legacy_full_forced_continuation.jsを参照）:
  ROUTING phaseでモデルがclassify_intentを呼ばずに応答を終えた場合
  （tool_choiceは明示設定されておらずデフォルト'auto'のため、モデルが
  Tool呼び出し無しのテキスト応答のみで終える余地が常に存在する）、既存の
  「OTHER/UNKNOWN安全網」が無条件に発火し、forceFollowUp=trueにより新しい
  お客様の発話を一切待たずにlegacy_fullへ強制継続する
  （REALTIME_PHASE_TRANSITION_JS側で確認済み）。この強制継続はlegacy_full
  の_INTENT_CLASSIFICATION_TEMPLATEから開始するが、このテンプレートには
  TASK Bで_PHASE2_ROUTING_ROLE_TEMPLATEにのみ追加された「強制継続でも
  会話全体を確認し、既に話された用件を再度尋ねない」という対策が無かった。
  これが実機で観測された「AIがまた『ご用件』を聞く」の直接原因である。

このテストは、_INTENT_CLASSIFICATION_TEMPLATE（legacy_fullが使用する）に
同種の対策が実際に追加されていること、既存のROUTING側対策・4分類・
曖昧時のclarification許可・「一度把握したご用件は判定し直さない」契約が
壊れていないことを静的に検証する。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(
    tempfile.mkdtemp(), "smoke_hotfix2_routing_no_repeat.db"
)
os.environ["SECRET_KEY"] = "smoke-test-secret-key-hotfix2-routing-no-repeat"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")


def _test_r4_legacy_full_has_forced_continuation_instruction():
    """R4（今回のHOTFIX2の核心）: legacy_fullが使用する
    _INTENT_CLASSIFICATION_TEMPLATEに、「強制継続（新しいお客様の発話を
    待たずに生成された発話）であっても会話全体を確認し、既に話された
    ご用件を再度尋ねない」という対策が実際に追加されていること。"""
    from app.services.realtime_voice_ai import _INTENT_CLASSIFICATION_TEMPLATE

    assert "新しい発言への応答でない場合" in _INTENT_CLASSIFICATION_TEMPLATE, (
        "R4: legacy_fullのIntent Classificationセクションに、強制継続時の"
        "会話全体確認・再質問防止の対策が見当たりません"
    )
    assert "会話全体を確認して" in _INTENT_CLASSIFICATION_TEMPLATE
    assert "同じ質問を重ねず" in _INTENT_CLASSIFICATION_TEMPLATE
    print("R4. legacy_full（_INTENT_CLASSIFICATION_TEMPLATE）に、強制継続時でも既に話された用件を再度尋ねない対策が追加されていること: OK")


def _test_r4b_new_section_precedes_four_category_section():
    """R4補助: 新しく追加したセクションが「## 4つの分類」より前（＝最優先の
    確認事項として）に配置されていること（ROUTING側の「## 最優先で必ず
    確認すること」と同じ構造上の位置づけであることの確認）。"""
    from app.services.realtime_voice_ai import _INTENT_CLASSIFICATION_TEMPLATE

    idx_new = _INTENT_CLASSIFICATION_TEMPLATE.find("新しい発言への応答でない場合")
    idx_categories = _INTENT_CLASSIFICATION_TEMPLATE.find("## 4つの分類")
    assert idx_new != -1 and idx_categories != -1
    assert idx_new < idx_categories, (
        "R4補助: 強制継続対策のセクションが「## 4つの分類」より後ろに配置されています"
        "（最優先の確認事項として機能しない可能性があります）"
    )
    print("R4補助. 強制継続対策セクションが「## 4つの分類」より前（最優先）に配置されていること: OK")


def _test_r5_ambiguous_purpose_clarification_still_allowed():
    """R5（既存契約の回帰）: 曖昧な用件の場合、二択程度の確認質問を行う、
    という既存の「## まだご用件がはっきりしない場合」に相当する契約が
    壊れていないこと。"""
    from app.services.realtime_voice_ai import _INTENT_CLASSIFICATION_TEMPLATE

    # 実際のソース上の折り返し位置に合わせ、改行を跨がない範囲で照合する
    # （「まだご用件がはっきりし」の直後に改行が入り「ない場合は、」と続く。
    # 「二択程度の短い確認」の直後に改行が入り「質問をしてください。」と続く。
    # いずれも本HOTFIX2で変更していない既存文言であり、テスト側の想定文字列
    # を実文言の折り返しに合わせて修正したもの）。
    assert "まだご用件がはっきりし" in _INTENT_CLASSIFICATION_TEMPLATE
    assert "ない場合は、" in _INTENT_CLASSIFICATION_TEMPLATE
    assert "二択程度の短い確認" in _INTENT_CLASSIFICATION_TEMPLATE
    print("R5. 曖昧な用件の場合の確認質問（二択程度のclarification）が引き続き許可されていること: OK")


def _test_r6_clarification_can_still_proceed_to_classification():
    """R6（既存契約の回帰）: OTHER/UNCLEARの確認質問の後、4つの分類・
    それぞれの進め方（classify_intentへの案内を含む既存フロー）へ進める
    構造が維持されていること（新しいセクションが分類ロジック自体を
    妨げていないことの確認）。"""
    from app.services.realtime_voice_ai import _INTENT_CLASSIFICATION_TEMPLATE

    assert "## それぞれの分類での進め方" in _INTENT_CLASSIFICATION_TEMPLATE
    assert "RESERVATION: このすぐ後の" in _INTENT_CLASSIFICATION_TEMPLATE
    assert "CALLBACK / HUMAN_HANDOFF: 必ず既存のrequest_callbackツール" in _INTENT_CLASSIFICATION_TEMPLATE
    print("R6. clarification後に4分類・既存フロー（RESERVATION/CALLBACK等）へ進める構造が維持されていること: OK")


def _test_r9_ack_suppression_contract_untouched():
    """R9（TASK B保護確認）: 「ありがとうございます」を毎ターン強制する
    義務が今回のHOTFIX2でも追加されていないこと（_INTENT_CLASSIFICATION_
    TEMPLATE自体の既存の「自然な相槌で会話に入る」セクションの回帰確認）。"""
    from app.services.realtime_voice_ai import _INTENT_CLASSIFICATION_TEMPLATE

    assert "これは毎回付ける" in _INTENT_CLASSIFICATION_TEMPLATE and "決まり文句ではありません" in _INTENT_CLASSIFICATION_TEMPLATE, (
        "R9: 「ありがとうございます」等の相槌が毎回の決まり文句ではない、という既存の指示が見当たりません"
    )
    print("R9. 「ありがとうございます」等の相槌を毎回の決まり文句にしない既存契約が維持されていること: OK")


def _test_r10_name_and_greeting_untouched():
    """R10（保護対象確認）: 今回のHOTFIX2はGreeting・NAME phaseの
    いずれにも触れていないこと（該当する定数・関数が、既存の正式契約
    テストで検証済みの状態のままであることの簡易確認）。"""
    from app.services.realtime_voice_ai import (
        _PHASE1_NAME_ROLE_TEMPLATE, _build_greeting_section,
    )

    # HOTFIX2の直前（NAME実機誤認識調査時点）に追加した確信ゲート関連の
    # 文言が、今回のROUTINGループ調査でも変わらず存在すること（無変更の
    # 簡易確認。詳細な回帰は既存のsmoke_test_name_no_fabrication.py /
    # smoke_test_conversation_quality_greeting_phase.py /
    # smoke_test_hotfix_greeting_and_name_realdevice.py /
    # smoke_test_hotfix2_name_recognition_architecture_audit.pyに委ねる）。
    assert "確信を持って聞き取れたお名前が含まれていたら" in _PHASE1_NAME_ROLE_TEMPLATE
    assert "店舗名を必ず含め" in _build_greeting_section(None, "サンプル店")
    print("R10. Greeting・NAME phaseの既存契約が今回のROUTINGループ修正で変更されていないこと（簡易確認、詳細は専用テストに委ねる）: OK")


def _test_callback_final_phrase_untouched():
    """保護対象: CALLBACK正式最終文言が一字一句無変更であること。"""
    from app.services.realtime_voice_ai import _HUMAN_HANDOFF_TEMPLATE

    assert "担当者から折り返し連絡しますので、電話を切ってお待ちください。" in _HUMAN_HANDOFF_TEMPLATE
    print("補助. CALLBACK正式最終文言が無変更であること: OK")


def main():
    _test_r4_legacy_full_has_forced_continuation_instruction()
    _test_r4b_new_section_precedes_four_category_section()
    _test_r5_ambiguous_purpose_clarification_still_allowed()
    _test_r6_clarification_can_still_proceed_to_classification()
    _test_r9_ack_suppression_contract_untouched()
    _test_r10_name_and_greeting_untouched()
    _test_callback_final_phrase_untouched()
    print("\n=== ALL smoke_test_hotfix2_routing_no_repeat_purpose_question.py CHECKS PASSED ===")


if __name__ == "__main__":
    main()
