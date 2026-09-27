"""
RECEPTRA — FAST TURN HOTFIX 10: NAME-FIRST CACHE WARM-UP実験 契約テスト
（CONTRACT TEST）

背景: 「電話に出た直後にお客様のお名前を先に伺い、その後にご用件
（本日はどのようなご用件でしょうか）を尋ねる」よう会話順序を変更した
（_NAME_FIRST_TEMPLATE、build_realtime_instructions()内でScopeの直後・
Intent Classificationの直前に挿入）。これは「Tool継続チェーンの最初の
大きな応答が呼ばれるまでの時間を後ろにずらすと、OpenAI Realtime APIの
プロンプトキャッシュがwarm-upする時間ができ、rate_limit_exceededが
減るかもしれない」という未検証の仮説を実機で検証するための変更であり、
本テストは「rate limit問題が解決した」ことを検証するものではない
（それは実機のusage実測でしか判断できない）。本テストが検証するのは
あくまで、意図した順序変更・重複のない正典化・既存の保護対象セクション
（HOTFIX 8 Phase 2で圧縮した4テンプレート）を再度肥大化させていないこと、
の3点のみ。

実行: python3 tests/smoke_test_name_first_contract.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.realtime_voice_ai import (
    _NAME_FIRST_TEMPLATE,
    _SCOPE_TEMPLATE,
    _INTENT_CLASSIFICATION_TEMPLATE,
    _FAST_RESERVATION_FLOW_TEMPLATE,
    _HUMAN_HANDOFF_TEMPLATE,
    _BOOKING_SAFETY_TEMPLATE,
    _TIME_AMBIGUITY_TEMPLATE,
)


def _normalized(s):
    return s.replace("\n", "").replace(" ", "").replace("　", "")


def test_a_name_first_template_exists_and_asks_name_before_intent():
    n = _normalized(_NAME_FIRST_TEMPLATE)
    assert "お名前" in n
    assert "伺って" in n or "伺い" in n
    assert "ご用件把握" in n, "Intent Classificationセクションへの導線が必要"
    print("A. NAME-FIRSTセクションが存在し、ご用件把握より前にお名前を伺う指示を含む: OK")


def test_b_purpose_question_text_present_exactly_once():
    phrase = "本日はどのようなご用件でしょうか"
    n = _normalized(_NAME_FIRST_TEMPLATE)
    assert n.count(phrase) == 1, (
        "「本日はどのようなご用件でしょうか」はNAME_FIRSTセクション内に厳密に1回だけ"
        "含まれているべき（他セクションでの重複導入を防ぐ。改行による分断を無視する"
        "ため正規化した文字列で比較する）"
    )
    # 他の保護対象テンプレートに同一文言が漏れ出して重複していないことも確認する。
    for tmpl in (
        _INTENT_CLASSIFICATION_TEMPLATE,
        _FAST_RESERVATION_FLOW_TEMPLATE,
        _HUMAN_HANDOFF_TEMPLATE,
        _BOOKING_SAFETY_TEMPLATE,
        _TIME_AMBIGUITY_TEMPLATE,
    ):
        assert phrase not in _normalized(tmpl), "ご用件質問の文言が他セクションに複製されている"
    print("B. ご用件質問「本日はどのようなご用件でしょうか」の文言重複なし: OK")


def test_c_preemption_clause_present():
    n = _normalized(_NAME_FIRST_TEMPLATE)
    assert "自発的に" in n
    assert "優先" in n or "そのまま使って" in n
    assert "聞き直さない" in n or "重ねて聞き直さない" in n
    print("C. お客様が先に情報を話した場合の先取り対応（プリエンプション）が明記されている: OK")


def test_d_fast_reservation_flow_no_longer_lists_name_mid_sequence():
    n = _normalized(_FAST_RESERVATION_FLOW_TEMPLATE)
    # 旧: 「...来店理由→お名前→電話番号...」の並び。名前はNAME-FIRSTへ移設されたため、
    # 基本順序の矢印チェーンからは外れ、代わりに「通話冒頭で既に伺っている」という
    # 参照文になっているべき。
    assert "来店理由→電話番号" in n or "人数→電話番号" in n or "NAME-FIRST" in n, (
        "基本順序の矢印チェーンから「お名前」が外れ、NAME-FIRSTセクションへの参照に"
        "置き換わっているはず"
    )
    assert "通話冒頭" in n and ("NAME-FIRST" in n or "既に伺っている" in n), (
        "お名前は通話冒頭で確認済みである旨の短い参照が必要（重複して書き直さない）"
    )
    print("D. Fast Reservation Flowの基本順序からお名前が外れ、NAME-FIRSTへの短い参照に置換されている: OK")


def test_e_protected_templates_not_relengthened():
    # HOTFIX 8 Phase 2で圧縮した4テンプレートの見出しが変わらず残っており、
    # 意図しない再肥大化（圧縮前水準への逆行）が起きていないことを確認する
    # （絶対値の厳密一致ではなく、明らかな再肥大化が無いことを確認する趣旨。
    # 意図した・理由のある追記まで永久に禁止する趣旨ではない）。
    # 実測値（2026-09-27、HOTFIX 10着手時点。HOTFIX10ではこれら4テンプレートの
    # 本文を一切変更していないため、実測値そのまま + 5%の許容枠を上限とする）:
    # _HUMAN_HANDOFF_TEMPLATE=2791, _BOOKING_SAFETY_TEMPLATE=2077,
    # _TIME_AMBIGUITY_TEMPLATE=2061, _INTENT_CLASSIFICATION_TEMPLATE=1908文字。
    #
    # FAST TURN HOTFIX 15（2026年9月・意図的な追記）: request_callback成功後の
    # 最終案内（5-1/5-2）にお礼の一言を追加し、5-3にPRE_CALLBACK_NARRATION
    # （「最後に...確認しますね」）を最終案内の代わりにしないことを明記した
    # （ユーザー指示§6/§17。実機で「最終案内が一度も再生されないまま通話が
    # 切れる」premature hangoutを修正するための一部）。実測でchar delta=+265
    # （2910→3175）であることを確認済み（測定はtests/test_fast_turn_hotfix15_
    # callback_final_confirmation.js参照）。
    #
    # FAST TURN HOTFIX 16（2026年9月・意図的な追記）: 「折り返し対応の進め方」
    # 手順1・3・4を書き換え、request_callback呼び出し前（＝まだ成功も失敗も
    # 分かっていない時点）に「担当者から折り返しご連絡いたします」等、結果を
    # 確約する表現を絶対に使わないことを明記した。また手順3で、必要情報が
    # 揃った後に「確認します」「少々お待ちください」のような処理中ナレーション
    # を一切話さずrequest_callbackを内部処理として呼び出すことを明記した
    # （ユーザー指示§1/§7/§8/§14。実機で「担当者から折り返します」に相当する
    # 発話が2回生成され、3回目でようやく通話が終了した問題の一次的な原因が、
    # request_callback呼び出し前の通常応答（terminal state machine外）で
    # 旧手順1の確約表現が繰り返し話されていたことだったための修正）。
    # 手順5（5-1/5-2/5-3）・6は一切変更していない（tests/smoke_test_human_
    # handoff_wording.pyの厳密な文言契約テストを壊さないため、意図的に
    # スコープ外とした）。実測でchar delta=+416（3175→3591、測定は
    # tests/test_fast_turn_hotfix16_callback_single_terminal.js参照）。
    # この意図的な増分を許容するため、このテンプレートのみベースラインを
    # HOTFIX16後の実測値（3591）+5%へ更新する（他の3テンプレートはHOTFIX16
    # で一切変更していないため据え置き）。
    baselines_after_phase2 = {
        "_HUMAN_HANDOFF_TEMPLATE": (_HUMAN_HANDOFF_TEMPLATE, round(3591 * 1.05)),
        "_BOOKING_SAFETY_TEMPLATE": (_BOOKING_SAFETY_TEMPLATE, round(2077 * 1.05)),
        "_TIME_AMBIGUITY_TEMPLATE": (_TIME_AMBIGUITY_TEMPLATE, round(2061 * 1.05)),
        "_INTENT_CLASSIFICATION_TEMPLATE": (_INTENT_CLASSIFICATION_TEMPLATE, round(1908 * 1.05)),
    }
    for name, (tmpl, rough_ceiling) in baselines_after_phase2.items():
        assert len(tmpl) <= rough_ceiling, (
            f"{name}がHOTFIX 8 Phase 2圧縮後の水準から明らかに再肥大化している "
            f"(len={len(tmpl)}, ceiling={rough_ceiling})。HOTFIX 10はこれらの"
            "テンプレートを再度肥大化させてはいけない（NAME_FIRSTは独立した"
            "新セクションとして追加し、既存テンプレートは最小限の1文差し替えのみ）。"
        )
    print("E. HOTFIX 8 Phase 2で圧縮した保護対象テンプレートが再肥大化していない: OK")


def test_f_name_first_is_standalone_new_section_not_merged_into_protected_templates():
    # 正典ルールは_NAME_FIRST_TEMPLATE内にのみ存在し、_INTENT_CLASSIFICATION_
    # TEMPLATE自体の本文は変更されていない（このHOTFIXでは触っていない）ことを、
    # HOTFIX 8 Phase 2 Step 5終了時点の既知の見出し文言が保たれているかで確認する。
    assert _INTENT_CLASSIFICATION_TEMPLATE.startswith(
        "# 通話冒頭のご用件把握（Conversation Opening / Intent Classification）"
    ), "_INTENT_CLASSIFICATION_TEMPLATE自体の本文は変更していないはず"
    print("F. _INTENT_CLASSIFICATION_TEMPLATE自体は変更されておらず、NAME_FIRSTは独立した新セクション: OK")


def main():
    test_a_name_first_template_exists_and_asks_name_before_intent()
    test_b_purpose_question_text_present_exactly_once()
    test_c_preemption_clause_present()
    test_d_fast_reservation_flow_no_longer_lists_name_mid_sequence()
    test_e_protected_templates_not_relengthened()
    test_f_name_first_is_standalone_new_section_not_merged_into_protected_templates()
    print("\n=== ALL smoke_test_name_first_contract.py CHECKS PASSED ===")


if __name__ == "__main__":
    main()
