"""
RECEPTRA — TASK B: 「ご用件を2回聞く」問題／「ありがとうございますを連呼する」
問題 専用回帰テスト

背景（実機での再現報告）:
問題A: お客様が名前とご用件を1つの発話でまとめて話しても、AIがPhase 1
       （お名前確認）→ Phase 2（ご用件の振り分け）へ遷移した直後に、
       「本日はどのようなご用件でしょうか？」等を重ねて尋ねてしまうことが
       あった。根本原因はfrontend/public/js/realtime-voice-engine.js側の
       forceFollowUpForTransitionヒューリスティック（Phase 1→2遷移時、
       直前のAI発話がVISIT_REASON的な語を含んでいない限り、新しいお客様の
       発話が無くても強制的にフォローアップ応答を生成させる仕組み）と、
       Phase 2 instructions側がその「フォローアップ応答＝新しい発話が無い
       継続応答」というケースを明示的に扱っていなかったことの組み合わせ。
       この回帰テストでは、JS側のこの仕組み（forceFollowUpForTransition・
       PLAYBACK_AWARE_MAX_WAIT_MS等）には一切手を入れず、Phase 1/Phase 2の
       instructions文言のみを最小変更で修正したことを検証する。

問題B: 「ありがとうございます」を、情報を1つ受け取るたびに機械的に付ける
       決まり文句として使ってしまう（かつ、それを別の固定相槌
       ―「承知しました」「かしこまりました」―に単純に置き換えるだけでは
       同じ問題が残る）。

検証項目（ユーザー指定のCASE/TESTの最小セット）:
  B1: 名前のみ回答 → 用件を尋ねてよい
  B2: 名前＋用件を同時に回答 → 用件を再度尋ねない
  B3: 用件のみ回答（名前不明） → 名前だけを尋ね、用件は再度尋ねない
      （「お名前とご用件をお聞かせください」のような、既知の用件を含む
      複合質問を新たに生成しない）
  B4: 名前＋用件＋日時＋人数を同時に回答 → いずれも聞き直さない
  B5: 用件が曖昧な場合 → 確認質問（二択等）は依然として許可される
      （「聞き直さない」ルールが行き過ぎて、必要な確認質問まで
      封じていないことの確認）
  T1: 「ありがとうございます」等の相槌を毎回使うことを義務付ける指示が
      どこにも無いこと
  T2: 名前を答えた直後に相槌が義務ではないこと
  T3: 日時を答えた直後に相槌が義務ではないこと
  T4: 人数を答えた直後に相槌が義務ではないこと
  T5: CALLBACK最終案内の直後に相槌が追加されないこと（既存のHOTFIX19の
      仕組みを再確認するのみで、変更は加えない）
  T6: 「ありがとうございます」自体が全面禁止されていないこと（自然な場面
      での使用は許可されている）
  T7: 「承知しました」「かしこまりました」等への単純な機械的置き換えが
      新たな義務として指示されていないこと

実行: python3 tests/smoke_test_no_duplicate_question_and_ack_overuse.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(
    tempfile.mkdtemp(), "smoke_no_dup_question_ack.db"
)
os.environ["SECRET_KEY"] = "smoke-test-secret-key-no-dup-question-ack"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")


def _test_b1_name_only_may_ask_purpose():
    from app.services.realtime_voice_ai import _PHASE1_NAME_ROLE_TEMPLATE

    tmpl = _PHASE1_NAME_ROLE_TEMPLATE
    assert "ご用件が未確認なら「本日はどのようなご用件でしょうか？」と続けてください" in tmpl.replace("\n", ""), (
        "B1: 名前のみ回答した場合に用件を尋ねる指示が見つかりません"
    )
    print("B1. 名前のみ回答 → 用件を尋ねてよい（NAME phase instructions）: OK")


def _test_b2_name_and_purpose_together_no_reask():
    from app.services.realtime_voice_ai import _PHASE1_NAME_ROLE_TEMPLATE, _PHASE2_ROUTING_ROLE_TEMPLATE

    name_tmpl = _PHASE1_NAME_ROLE_TEMPLATE.replace("\n", "")
    assert "ご用件を既に話されていた場合は聞き直さず" in name_tmpl, (
        "B2: NAME phaseに「用件を既に話されていた場合は聞き直さない」指示が見つかりません"
    )

    routing_tmpl = _PHASE2_ROUTING_ROLE_TEMPLATE.replace("\n", "")
    # Phase 1→2の遷移がJS側のforceFollowUpForTransitionにより「新しいお客様の
    # 発話が無い継続応答」として生成される場合でも、既に話された用件を会話
    # 履歴全体から再確認し、聞き直さずにclassify_intentを呼び出す指示が
    # 必須（TASK Bの根本対策）。
    assert "新しい発言への応答ではなく" in routing_tmpl and "会話全体を確認してください" in routing_tmpl, (
        "B2: ROUTING phaseに「新しい発話が無い継続応答でも会話全体を確認する」指示が見つかりません"
    )
    assert "ご用件を再度尋ねることなくすぐに classify_intent ツールを呼び出してください" in routing_tmpl, (
        "B2: ROUTING phaseに「用件を再度尋ねずにclassify_intentを呼ぶ」指示が見つかりません"
    )
    assert "新しいお客様の発言を待たずに行ってください" in routing_tmpl, (
        "B2: 既に用件が話されている場合、新しい発話を待たずclassify_intentを呼ぶ指示が見つかりません"
    )
    print("B2. 名前＋用件を同時に回答 → 用件を再度尋ねない（ROUTING phase forced-continuation対応）: OK")


def _test_b3_purpose_only_ask_name_only():
    from app.services.realtime_voice_ai import _PHASE1_NAME_ROLE_TEMPLATE

    tmpl = _PHASE1_NAME_ROLE_TEMPLATE.replace("\n", "")
    idx = tmpl.find("お名前がまだ分からない場合")
    assert idx != -1, "B3: 「お名前がまだ分からない場合」セクションが見つかりません"
    nearby = tmpl[idx:idx + 300]
    assert "ご用件が話されていれば覚えておき" in nearby, (
        "B3: 用件を覚えておく（保持する）指示が見つかりません"
    )
    assert "名乗りは繰り返さず「お名前を教えていただけますか？」と" in nearby, (
        "B3: 名前だけを尋ねる指示が見つかりません"
    )
    # 「お名前とご用件をお聞かせください」のような、既知の用件を再度含める
    # 複合質問を新たに指示していないことを確認する。
    assert "お名前とご用件をお聞かせください" not in tmpl, (
        "B3: 既知の用件を含む複合質問（お名前とご用件を...）が指示されています（回帰）"
    )
    print("B3. 用件のみ回答（名前不明） → 名前だけを尋ね、用件は再度尋ねない: OK")


def _test_b4_all_together_no_reask_any():
    from app.services.realtime_voice_ai import _FAST_RESERVATION_FLOW_TEMPLATE

    tmpl = _FAST_RESERVATION_FLOW_TEMPLATE.replace("\n", "")
    idx = tmpl.find("既に分かっている情報を聞き直さない")
    assert idx != -1, "B4: 「既に分かっている情報を聞き直さない」ルールが見つかりません"
    nearby = tmpl[idx:idx + 300]
    for item in ["日時", "人数", "お名前", "電話番号"]:
        assert item in nearby, f"B4: 聞き直し禁止の対象項目に{item}が明記されていません"
    assert "改めて質問し直すことは絶対にしないでください" in nearby, (
        "B4: 聞き直しを絶対に禁止する強い表現が見つかりません"
    )
    print("B4. 名前＋用件＋日時＋人数を同時に回答 → いずれも聞き直さない: OK")


def _test_b5_ambiguous_purpose_confirmation_still_allowed():
    from app.services.realtime_voice_ai import _PHASE2_ROUTING_ROLE_TEMPLATE

    tmpl = _PHASE2_ROUTING_ROLE_TEMPLATE.replace("\n", "")
    idx = tmpl.find("まだご用件がはっきりしない場合")
    assert idx != -1, "B5: 「まだご用件がはっきりしない場合」セクションが見つかりません"
    nearby = tmpl[idx:idx + 300]
    assert "二択程度の短い確認質問をしてください" in nearby, (
        "B5: 曖昧な場合の確認質問（二択等）が許可されていません（聞き直さないルールの過剰適用の疑い）"
    )
    print("B5. 用件が曖昧な場合 → 確認質問は依然として許可される: OK")


def _collect_ack_relevant_templates():
    from app.services.realtime_voice_ai import (
        _PHASE1_NAME_ROLE_TEMPLATE,
        _PHASE2_ROUTING_ROLE_TEMPLATE,
        _PHASE2A_RESERVATION_ROLE_TEMPLATE,
        _PHASE2B_CALLBACK_ROLE_TEMPLATE,
        _INTENT_CLASSIFICATION_TEMPLATE,
        _FAST_RESERVATION_FLOW_TEMPLATE,
        _HUMAN_HANDOFF_TEMPLATE,
    )
    return {
        "name": _PHASE1_NAME_ROLE_TEMPLATE,
        "routing": _PHASE2_ROUTING_ROLE_TEMPLATE,
        "reservation_role": _PHASE2A_RESERVATION_ROLE_TEMPLATE,
        "callback_role": _PHASE2B_CALLBACK_ROLE_TEMPLATE,
        "intent_classification": _INTENT_CLASSIFICATION_TEMPLATE,
        "fast_reservation_flow": _FAST_RESERVATION_FLOW_TEMPLATE,
        "human_handoff": _HUMAN_HANDOFF_TEMPLATE,
    }


_MANDATORY_ACK_REGEXES = None


def _forbidden_mandatory_ack_regexes():
    global _MANDATORY_ACK_REGEXES
    if _MANDATORY_ACK_REGEXES is None:
        import re

        _MANDATORY_ACK_REGEXES = [
            re.compile(r"毎回[^\n]{0,15}(ありがとうございます|承知しました|かしこまりました)"),
            re.compile(r"必ず[^\n]{0,15}(ありがとうございます|承知しました|かしこまりました)と(言|話|伝え)"),
            re.compile(r"(ありがとうございます|承知しました|かしこまりました)を毎回"),
            re.compile(r"情報を(1つ|１つ|一つ)答えるたびに[^\n]{0,20}(ありがとうございます|承知しました|かしこまりました)"),
        ]
    return _MANDATORY_ACK_REGEXES


def _test_t1_no_mandatory_ack_every_turn():
    templates = _collect_ack_relevant_templates()
    combined = "\n".join(templates.values())
    for regex in _forbidden_mandatory_ack_regexes():
        assert not regex.search(combined), (
            f"T1: 相槌を毎回義務付ける禁止パターンに一致する文言が見つかりました: {regex.pattern}"
        )
    print("T1. 相槌を毎回使うことを義務付ける指示がどこにも無い: OK")


def _test_t2_no_mandatory_ack_after_name():
    from app.services.realtime_voice_ai import _PHASE1_NAME_ROLE_TEMPLATE

    tmpl = _PHASE1_NAME_ROLE_TEMPLATE.replace("\n", "")
    idx = tmpl.find("お名前を取得できたら")
    assert idx != -1
    nearby = tmpl[idx:idx + 400]
    # 「○○様ですね。」という短い受け止めの例はあってよいが、相槌語自体
    # （ありがとうございます）を必須の応答として指示していないことを確認。
    assert "必ず「ありがとうございます」と" not in nearby
    assert "必ず「承知しました」と" not in nearby
    print("T2. 名前回答直後に相槌が義務ではない: OK")


def _test_t3_no_mandatory_ack_after_datetime():
    from app.services.realtime_voice_ai import _FAST_RESERVATION_FLOW_TEMPLATE

    tmpl = _FAST_RESERVATION_FLOW_TEMPLATE.replace("\n", "")
    idx = tmpl.find("相槌だけで発話を終わらせない")
    nearby = tmpl[idx:idx + 700]
    # 良い例が相槌無しで要約＋質問/Tool呼び出しへ直行していることを確認
    assert "明日12時、2名様ですね。空き状況を確認します。」" in nearby
    assert "相槌が無くても自然です" in nearby
    print("T3. 日時回答直後に相槌が義務ではない: OK")


def _test_t4_no_mandatory_ack_after_party_size():
    from app.services.realtime_voice_ai import _FAST_RESERVATION_FLOW_TEMPLATE

    tmpl = _FAST_RESERVATION_FLOW_TEMPLATE.replace("\n", "")
    idx = tmpl.find("何名様でのご予約でしょうか？")
    assert idx != -1
    # NOISE RECOVERY例（人数を尋ねる場面）が相槌無しになっていることを確認
    nearby = tmpl[max(0, idx - 60):idx + 20]
    assert "ありがとうございます。何名様でのご予約でしょうか？" not in nearby
    print("T4. 人数を尋ねる場面で相槌が機械的に付いていない（NOISE RECOVERY例）: OK")


def _test_t5_no_ack_after_callback_terminal():
    from app.services.realtime_voice_ai import _HUMAN_HANDOFF_TEMPLATE

    tmpl = _HUMAN_HANDOFF_TEMPLATE
    assert "担当者から折り返し連絡しますので、電話を切ってお待ちください。" in tmpl, (
        "T5: CALLBACK最終文言そのものが変更されています（保護対象・回帰）"
    )
    assert "この最終案内の直前に「承知しました。」「かしこまりました。」等の独立した余計なACK応答を新規に発生させないでください" in tmpl.replace("\n", ""), (
        "T5: CALLBACK最終案内の直前に余計なACKを発生させない既存の禁止（HOTFIX19）が見つかりません"
    )
    print("T5. CALLBACK最終案内の直後（直前）に相槌が追加されない（HOTFIX19保持）: OK")


def _test_t6_ack_not_fully_banned():
    templates = _collect_ack_relevant_templates()
    combined = "\n".join(templates.values())
    assert "ありがとうございます" in combined, (
        "T6: 「ありがとうございます」という表現自体が完全に排除されています（全面禁止の疑い）"
    )
    assert "本当に自然な場面でのみ使ってください" in combined.replace("\n", "") or \
        "本当に自然な場面であれば" in combined.replace("\n", ""), (
        "T6: 自然な場面での相槌使用を許可する文言が見つかりません"
    )
    print("T6. 「ありがとうございます」自体は全面禁止されていない（自然な使用は許可）: OK")


def _test_t7_no_simple_substitution_to_fixed_ack():
    from app.services.realtime_voice_ai import _FAST_RESERVATION_FLOW_TEMPLATE

    tmpl = _FAST_RESERVATION_FLOW_TEMPLATE.replace("\n", "")
    assert "単純な置き換えも同様に避けてください" in tmpl, (
        "T7: 「承知しました」「かしこまりました」等への単純な置き換え禁止の文言が見つかりません"
    )
    idx = tmpl.find("単純な置き換えも同様に避けてください")
    nearby = tmpl[max(0, idx - 300):idx]
    assert "承知しました" in nearby and "かしこまりました" in nearby, (
        "T7: 単純な置き換え禁止の対象として「承知しました」「かしこまりました」が明記されていません"
    )
    print("T7. 別の固定相槌への単純な機械的置き換えが新たな義務として指示されていない: OK")


def _test_protected_areas_untouched():
    """
    STEP 17: 保護対象の簡易確認（詳細な回帰は full JS/Python regression 側で行う。
    ここではTASK Bの変更対象と保護対象が重ならないことをテンプレート文字列
    レベルで最低限確認する）。
    """
    from app.services.realtime_voice_ai import (
        _HUMAN_HANDOFF_TEMPLATE,
        _NAME_TOOLS,
        _ROUTING_TOOLS,
        _REALTIME_TOOLS,
    )

    assert "担当者から折り返し連絡しますので、電話を切ってお待ちください。" in _HUMAN_HANDOFF_TEMPLATE
    assert _NAME_TOOLS[0]["name"] == "confirm_customer_name"
    assert _NAME_TOOLS[0]["parameters"] == {"type": "object", "properties": {}, "required": []}
    assert _ROUTING_TOOLS[0]["name"] == "classify_intent"
    assert _ROUTING_TOOLS[0]["parameters"]["properties"]["intent"]["enum"] == ["reservation", "callback"]
    assert len(_REALTIME_TOOLS) == 9
    print("保護対象（CALLBACK最終文言・confirm_customer_name/classify_intent Tool schema・"
          "Tool数）: 無変更 OK")


def main():
    _test_b1_name_only_may_ask_purpose()
    _test_b2_name_and_purpose_together_no_reask()
    _test_b3_purpose_only_ask_name_only()
    _test_b4_all_together_no_reask_any()
    _test_b5_ambiguous_purpose_confirmation_still_allowed()
    _test_t1_no_mandatory_ack_every_turn()
    _test_t2_no_mandatory_ack_after_name()
    _test_t3_no_mandatory_ack_after_datetime()
    _test_t4_no_mandatory_ack_after_party_size()
    _test_t5_no_ack_after_callback_terminal()
    _test_t6_ack_not_fully_banned()
    _test_t7_no_simple_substitution_to_fixed_ack()
    _test_protected_areas_untouched()
    print()
    print("全テストOK: TASK B（ご用件の重複質問防止／ありがとうございます濫用防止）"
          "— B1-B5・T1-T7・保護対象の最小確認")


if __name__ == "__main__":
    main()
