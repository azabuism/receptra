"""
RECEPTRA — TASK B HOTFIX 3
実機「名前の確認を連呼する／訂正を受け付けない」修正・契約テスト

背景: 実機で、お名前確認後に「○○様ですね」に相当する確認が繰り返される
（連呼）症状と、お客様からの名前訂正（例: AIが聞き間違えた名前と違う名前を
名乗り直す）が反映されない症状が報告された。

根本原因の調査（コードを直接確認）:
  confirm_customer_name Toolが呼ばれると、その応答（response.done）の
  境界でNAME→ROUTINGへのphase遷移が即座に消費される。この遷移は
  お客様の実際の次の発話を待たずに行われるため、お客様が名前の訂正を
  発話しても、それは常にNAME phaseの指示ではなく既にROUTING phaseの
  指示のもとで解釈される（frontend/public/js/realtime-voice-engine.js
  のresponse.done処理・sendRealtimePhaseSessionUpdate参照）。
  また、来店目的（VISIT_REASON）を同じ発話内で尋ねなかった場合
  （classifyExpectedAnswerType()がVISIT_REASON以外と判定した場合）、
  forceFollowUpForTransitionが無条件でtrueとなり、お客様の新しい発話を
  待たずに追加のresponse.createがROUTING phaseの指示のもとで強制される
  （このJS側の挙動自体は意図的な既存設計であり、今回変更していない）。

  しかしROUTING phase（_PHASE2_ROUTING_ROLE_TEMPLATE）には、直前で
  確認したはずのお名前を再確認・復唱しない、および、お客様からの
  訂正があった場合にそれをどう扱うか、という指示が一切無かった。
  この構造的な隙間が、実機で観測された「名前の確認を連呼する」症状と
  「訂正が反映されない」症状の直接の原因であると判断した。

対策: _PHASE2_ROUTING_ROLE_TEMPLATE の冒頭（「## 最優先で必ず確認する
こと」の直前）に、(1) 訂正の申し出がない限りお名前を重ねて確認・復唱
しないこと、(2) お客様が先ほどと異なるお名前を名乗った場合は訂正として
一度だけ短く受け止め、すぐご用件把握へ進むこと、の2点を明示する新規
セクションを追加した。特定の姓（谷村・田村等）へのハードコード・
専用の例外処理は一切行っていない（一般的なプレースホルダー「○○様」の
みを使用）。

このテストは、上記の対策が実際のinstructions生成コードに正しく反映
されていること、および既存の保護対象（NAME phase・CALLBACK文言・
TASK B HOTFIX 2のご用件二重質問防止・文字数回帰guard）が壊れていない
ことを静的に検証する。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(
    tempfile.mkdtemp(), "smoke_hotfix3_name_reconfirm.db"
)
os.environ["SECRET_KEY"] = "smoke-test-secret-key-hotfix3-name-reconfirm"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

_COMMON_SURNAME_SAMPLES = ["田中", "山田", "佐藤", "鈴木", "田村", "谷村", "Tanaka", "Yamada"]


def _test_1_no_repeat_confirmation_instruction_present():
    """1: ROUTING指示に、訂正の申し出がない限りお名前を重ねて確認・復唱
    しない、という明示的な指示が含まれていること（連呼防止）。"""
    from app.services.realtime_voice_ai import _PHASE2_ROUTING_ROLE_TEMPLATE

    assert "お名前は確認済みです" in _PHASE2_ROUTING_ROLE_TEMPLATE
    assert "重ねて確認・復唱しないでください" in _PHASE2_ROUTING_ROLE_TEMPLATE, (
        "1: ROUTING指示に、お名前を重ねて確認・復唱しないという明示的な指示が見当たりません"
    )
    print("1. ROUTING指示に、訂正がない限りお名前を重ねて確認・復唱しない指示が含まれていること: OK")


def _test_2_correction_handling_instruction_present():
    """2: お客様からの名前訂正（先ほどと異なるお名前を名乗った場合）を
    一度だけ短く受け止めてすぐ次へ進む、という明示的な指示が含まれている
    こと。"""
    from app.services.realtime_voice_ai import _PHASE2_ROUTING_ROLE_TEMPLATE

    assert "異なるお名前を名乗った場合は訂正とみなし" in _PHASE2_ROUTING_ROLE_TEMPLATE
    assert "失礼しました。○○様ですね。" in _PHASE2_ROUTING_ROLE_TEMPLATE
    assert "一度だけ短く受け止め" in _PHASE2_ROUTING_ROLE_TEMPLATE
    assert "すぐ本来のご用件把握へ進んでください" in _PHASE2_ROUTING_ROLE_TEMPLATE
    print("2. お客様からの名前訂正を一度だけ受け止めてすぐ次へ進む指示が含まれていること: OK")


def _test_3_new_section_precedes_existing_priority_section():
    """3: 新規セクションが既存の「## 最優先で必ず確認すること」より前に
    配置されていること（最優先の確認事項として機能する構造上の位置づけ）。"""
    from app.services.realtime_voice_ai import _PHASE2_ROUTING_ROLE_TEMPLATE

    idx_new = _PHASE2_ROUTING_ROLE_TEMPLATE.find("お名前は確認済みです")
    idx_existing = _PHASE2_ROUTING_ROLE_TEMPLATE.find("## 最優先で必ず確認すること")
    assert idx_new != -1 and idx_existing != -1
    assert idx_new < idx_existing, (
        "3: 新規セクションが既存の「## 最優先で必ず確認すること」より後ろに配置されています"
    )
    print("3. 新規セクションが既存の最優先確認事項セクションより前に配置されていること: OK")


def _test_4_no_surname_hardcoding_anywhere():
    """4: 特定の姓（谷村・田村等）へのハードコード・専用例外処理が一切
    無いこと（ユーザーの明示的な禁止事項の回帰確認）。プレースホルダーは
    「○○」のみを使用していること。"""
    from app.services.realtime_voice_ai import (
        _PHASE1_NAME_ROLE_TEMPLATE, _PHASE2_ROUTING_ROLE_TEMPLATE,
    )

    for sample in _COMMON_SURNAME_SAMPLES:
        assert sample not in _PHASE1_NAME_ROLE_TEMPLATE, f"NAME instructionsに '{sample}' が含まれています"
        assert sample not in _PHASE2_ROUTING_ROLE_TEMPLATE, f"ROUTING instructionsに '{sample}' が含まれています"
    print("4. NAME/ROUTING instructionsのどちらにも特定の姓のハードコードが無いこと（プレースホルダーのみ）: OK")


def _test_5_char_budget_still_holds():
    """5: 追加後もROUTING instructions_chars・NAME instructions_charsが
    既存の3,000文字回帰guard（smoke_test_realtime_phase2_reservation_
    callback.pyのA/C）に収まっていること。"""
    from app.services.realtime_voice_ai import (
        _build_phase_minimal_instructions, _PHASE1_NAME_ROLE_TEMPLATE,
        _PHASE2_ROUTING_ROLE_TEMPLATE,
    )
    from app.language_registry import REQUIRED_AI_LANGUAGE

    class _FakeShop:
        name = "テスト店"

    name_text = _build_phase_minimal_instructions(
        _FakeShop(), None, [REQUIRED_AI_LANGUAGE], None,
        _PHASE1_NAME_ROLE_TEMPLATE, include_greeting=True,
    )
    routing_text = _build_phase_minimal_instructions(
        _FakeShop(), None, [REQUIRED_AI_LANGUAGE], None,
        _PHASE2_ROUTING_ROLE_TEMPLATE, include_greeting=False,
    )
    assert len(name_text) < 3000, f"NAME instructions_charsが3,000文字を超えています: {len(name_text)}"
    assert len(routing_text) < 3000, f"ROUTING instructions_charsが3,000文字を超えています: {len(routing_text)}"
    print(
        f"5. 追加後もNAME instructions_chars={len(name_text)}・"
        f"ROUTING instructions_chars={len(routing_text)}、どちらも既存の3,000文字回帰guard内: OK"
    )


def _test_6_name_phase_template_untouched():
    """6: 今回のHOTFIX3はNAME phase自体（_PHASE1_NAME_ROLE_TEMPLATE）を
    変更していないこと（既存の確信度ゲート等の文言がそのまま維持されて
    いることの簡易確認）。"""
    from app.services.realtime_voice_ai import _PHASE1_NAME_ROLE_TEMPLATE

    assert "確信を持って聞き取れたお名前が含まれていたら" in _PHASE1_NAME_ROLE_TEMPLATE
    assert "毎回聞き返し" in _PHASE1_NAME_ROLE_TEMPLATE
    print("6. NAME phase自体（_PHASE1_NAME_ROLE_TEMPLATE）が今回変更されていないこと（簡易確認）: OK")


def _test_7_routing_task_b_hotfix2_contract_untouched():
    """7: TASK B HOTFIX 2（ご用件の二重質問防止）の既存対策がそのまま
    維持されていること（回帰確認）。"""
    from app.services.realtime_voice_ai import _PHASE2_ROUTING_ROLE_TEMPLATE

    assert "この発話が、お客様の新しい発言への応答ではなく" in _PHASE2_ROUTING_ROLE_TEMPLATE
    assert "再度尋ねることなくすぐに classify_intent ツールを呼び出してください" in _PHASE2_ROUTING_ROLE_TEMPLATE
    print("7. TASK B HOTFIX 2（ご用件の二重質問防止）の既存対策がそのまま維持されていること: OK")


def _test_8_intent_classification_template_untouched():
    """8: legacy_fullが使う_INTENT_CLASSIFICATION_TEMPLATEは、本HOTFIX3の
    スコープ（ROUTING phaseのNAME再確認ループ防止）では変更していないこと。

    【2026-09-30更新】RECEPTRA — CALLBACK誤分類＋電話番号桁数判定バグの実機
    修正にて、このテンプレートのCALLBACK分類基準（項目2）に、classify_intent
    Toolと同水準の具体例・反混同注記を意図的に追加した（実機で「担当者から
    折り返しが欲しい」がRESERVATIONへ誤分類される不具合の対策。詳細は
    tests/smoke_test_callback_reservation_misclass_and_phone_guard.py参照）。
    これは本HOTFIX3タスクのスコープ外の変更だが、別タスクとして正当な理由が
    あるため、この長さ固定tripwire自体を新しい実測値（2132→2228）へ更新する
    （黙って更新したものではなく、この理由コメントとともに更新）。
    """
    from app.services.realtime_voice_ai import _INTENT_CLASSIFICATION_TEMPLATE

    assert len(_INTENT_CLASSIFICATION_TEMPLATE) == 2228, (
        f"_INTENT_CLASSIFICATION_TEMPLATEの文字数が2026-09-30更新時点（2228）から"
        f"変化しています: {len(_INTENT_CLASSIFICATION_TEMPLATE)}"
    )
    # CALLBACK誤分類対策として追加した具体例・反混同注記が実際に含まれている
    # ことも確認する（今回の意図した変更であることの積極的な確認）。
    assert "担当者から折り返してほしい" in _INTENT_CLASSIFICATION_TEMPLATE
    assert "予約の話が出ていても常にCALLBACKです" in _INTENT_CLASSIFICATION_TEMPLATE
    print("8. legacy_fullの_INTENT_CLASSIFICATION_TEMPLATEは、CALLBACK誤分類対策"
          "（2026-09-30・意図した変更）以外は変更されていないこと: OK")


def _test_callback_final_phrase_untouched():
    """補助: CALLBACK正式最終文言が一字一句無変更であること。"""
    from app.services.realtime_voice_ai import _HUMAN_HANDOFF_TEMPLATE

    assert "担当者から折り返し連絡しますので、電話を切ってお待ちください。" in _HUMAN_HANDOFF_TEMPLATE
    print("補助. CALLBACK正式最終文言が無変更であること: OK")


def main():
    _test_1_no_repeat_confirmation_instruction_present()
    _test_2_correction_handling_instruction_present()
    _test_3_new_section_precedes_existing_priority_section()
    _test_4_no_surname_hardcoding_anywhere()
    _test_5_char_budget_still_holds()
    _test_6_name_phase_template_untouched()
    _test_7_routing_task_b_hotfix2_contract_untouched()
    _test_8_intent_classification_template_untouched()
    _test_callback_final_phrase_untouched()
    print("\n=== ALL smoke_test_hotfix3_name_reconfirm_loop_and_correction.py CHECKS PASSED ===")


if __name__ == "__main__":
    main()
