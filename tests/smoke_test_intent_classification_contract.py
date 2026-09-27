"""
RECEPTRA — FAST TURN HOTFIX 8 PHASE 2 STEP 5: INTENT CLASSIFICATION 契約テスト
（CONTRACT TEST・既存smoke_test_intent_classification.pyを補完）

背景: _INTENT_CLASSIFICATION_TEMPLATEの主要な文言（4分類・5秒目安・
プロトコル文言の非混入・来店理由との混同禁止）は既存の
tests/smoke_test_intent_classification.pyとtests/smoke_test_guidance_
unit_acknowledgement.pyで既に検証されているが、以下の挙動はどちらの
ファイルでも直接検証されていなかったため、HOTFIX 8 Phase 2 Step 5で
本テンプレートを圧縮する前に、意味ベースの契約テストとして追加する:
  1. ご用件把握後は深追いする質問を重ねない（症状等を深掘りしない）
  2. お客様の発話を無理に遮ったり急かしたりしない
  3. 4分類それぞれについて、進め方（既存セクション/既存Toolへの導線）が
     明記されている
  4. 一度把握したご用件は、お客様の発言が変わらない限り判定し直さない

実行: python3 tests/smoke_test_intent_classification_contract.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.realtime_voice_ai import _INTENT_CLASSIFICATION_TEMPLATE


def _normalized():
    return (
        _INTENT_CLASSIFICATION_TEMPLATE.replace("\n", "")
        .replace(" ", "")
        .replace("　", "")
    )


def test_heading_preserved():
    assert "通話冒頭のご用件把握" in _INTENT_CLASSIFICATION_TEMPLATE, (
        "他ファイルのセクション出現順序チェックが依存する見出しが失われています"
    )
    print("1. Intent Classification見出し（他ファイルのセクション順序チェックの目印）: OK")


def test_no_deep_probing_once_intent_known():
    n = _normalized()
    assert "深掘り" in n or "深追い" in n
    assert "重ねない" in n or "重ねず" in n
    print("2. ご用件把握後は深掘り・深追いの質問を重ねない: OK")


def test_does_not_rush_or_interrupt_customer():
    n = _normalized()
    assert "遮った" in n or "遮る" in n
    assert "急かした" in n or "急かす" in n or "急かし" in n
    assert "絶対にしないでください" in n
    print("3. 発話を遮ったり急かしたりすることの絶対禁止: OK")


def test_each_category_has_routing_guidance():
    n = _normalized()
    assert "RESERVATION" in n and "FastReservationFlow" in n.replace(" ", "")
    assert "request_callback" in n and ("HumanHandoff" in n.replace(" ", "") or "折り返し" in n)
    assert "get_shop_info" in n
    assert "二択" in n or "確認質問" in n
    print("4. 4分類それぞれに既存セクション/既存Toolへの導線が明記されている: OK")


def test_does_not_re_judge_intent_unless_customer_changes():
    n = _normalized()
    assert "判定し直さない" in n or "判断をやり直" in n
    assert "発言が変わらない限り" in n or "変えない限り" in n
    print("5. 一度把握したご用件は、発言が変わらない限り判定し直さない: OK")


def main():
    test_heading_preserved()
    test_no_deep_probing_once_intent_known()
    test_does_not_rush_or_interrupt_customer()
    test_each_category_has_routing_guidance()
    test_does_not_re_judge_intent_unless_customer_changes()
    print("\n=== ALL smoke_test_intent_classification_contract.py CHECKS PASSED ===")


if __name__ == "__main__":
    main()
