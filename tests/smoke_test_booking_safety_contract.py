"""
RECEPTRA — FAST TURN HOTFIX 8 PHASE 2 STEP 3: BOOKING SAFETY 契約テスト（CONTRACT TEST）

背景: _BOOKING_SAFETY_TEMPLATE（予約成立の宣言に関する絶対ルール）は、
最小構成の店舗でinstructions全体の11.3%（2670文字）を占める大型セクション
だが、これまで文字列としての内容を直接検証する契約テストが存在しなかった
（他ファイルは見出し文字列「予約成立の宣言に関する絶対ルール（最重要・
必ず守ってください）」をセクション出現順序の目印として使っているだけで、
本文の内容は一切検証していない）。

HOTFIX 8 Phase 2 Step 3として本テンプレートを圧縮するにあたり、方針
（Section 8: 圧縮前に必ず契約テストを書く）に従い、本テンプレートが実際に
守っている挙動をすべて洗い出した上で、圧縮の前後どちらでも全項目PASSする
意味ベースの契約テストとして先に用意する。

検証する挙動（本文を読んで洗い出したもの）:
  1. create_reservation呼び出し前の項目は毎回個別確認せず、まとめて一度の
     「予約全体の最終確認」で確認する（Fast Reservation Flowの方針と整合）。
  2. 例外として電話番号だけは必ず1桁ずつ読み上げて復唱する。
  3. 電話番号の推測・聞き取れなかった桁の補完は絶対にしない。
  4. 「確認します」等の予告だけで発話を終えず、(A)未取得なら質問する
     (B)取得済み/直後ならその場で1桁ずつ読み上げて復唱する、のいずれかを
     同じ発話内で必ず行う。
  5. 個別項目への「はい」等の返事は、予約確定の同意としては扱わない
     （項目ごとの確認と予約全体の最終確認は別物）。
  6. 予約全体の最終確認への明確な肯定のみが、create_reservation呼び出しの
     同意として扱われる。
  7. 最終確認後に客が内容を変更・訂正した場合、それ以前の同意は無効になり、
     （日時変更時はcheck_availabilityで再確認の上）最終確認をやり直す。
  8. 曖昧な返事（うーん/たぶん等）ではcreate_reservationを呼び出さず、
     もう一度明確に確認し直す。
  9. 否定・保留の返事（やめます/予約しません等）ではcreate_reservationを
     絶対に呼び出さない。
  10. 呼び出し中は中立的な案内のみで、成立を意味する断定表現は絶対にしない
      （「予約できました」「ご予約完了です」「お取りしました」「承りました」
      「予約確定しました」等はsuccess:trueが返るまで禁止）。
  11. success:trueの場合、返ってきた内容（日時・人数）と完全に一致する
      内容のみを案内する。
  12. success:falseの場合、reason_codeに応じて安全に案内し、空いている・
      予約できたと推測しない。
  13. 成立後は短く伝えるにとどめ、新たな質問を重ねずに通話を自然に締める。

実行: python3 tests/smoke_test_booking_safety_contract.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.realtime_voice_ai import _BOOKING_SAFETY_TEMPLATE


def _normalized():
    # 改行・半角/全角スペースを除去した正規化テキスト。行の折り返し位置や
    # インデントの空白は圧縮で変わり得るため、意味的な内容チェックは
    # これらに依存しない形で行う。
    return (
        _BOOKING_SAFETY_TEMPLATE.replace("\n", "")
        .replace(" ", "")
        .replace("　", "")
    )


def test_heading_and_section_is_last_marker_preserved():
    assert "予約成立の宣言に関する絶対ルール（最重要・必ず守ってください）" in _BOOKING_SAFETY_TEMPLATE, (
        "他ファイルのセクション出現順序チェックが依存する見出しが失われています"
    )
    print("1. Booking Safety見出し（他ファイルのセクション順序チェックの目印）: OK")


def test_combined_final_confirmation_instead_of_per_field():
    n = _normalized()
    assert "個別に" in n and ("確認" in n)
    assert "予約全体の最終確認" in n
    assert "FastReservationFlow" in n, "Fast Reservation Flowへのクロスリファレンスが見当たりません"
    print("2. 個別逐次確認ではなく『予約全体の最終確認』でまとめて確認する方針: OK")


def test_phone_digit_by_digit_readback():
    n = _normalized()
    assert "1桁ずつ読み上げて" in n and "復唱" in n, "電話番号の1桁ずつ読み上げルールが見当たりません"
    assert "推測" in n and ("補っ" in n or "補完" in n), "電話番号の推測・補完禁止が見当たりません"
    print("3. 電話番号の1桁ずつ読み上げ・推測/補完の絶対禁止: OK")


def test_phone_no_announce_only_then_silence():
    n = _normalized()
    assert "予告だけを発話してそこで黙り込むことは絶対にしないでください" in n or (
        "予告" in n and "絶対にしないでください" in n
    ), "『予告だけで止まる』ことの禁止が見当たりません"
    # (A) 未取得なら質問する
    assert "電話番号を教えていただけますか" in n or "電話番号を尋ねる質問をしてください" in n
    # (B) 取得済みならその場で読み上げる
    assert "実際に" in n and "1桁ずつ読み上げて" in n
    print("4. 電話番号確認: 予告だけで止めず(A)質問または(B)即時読み上げを必ず行う: OK")


def test_per_field_reply_is_not_reservation_consent():
    n = _normalized()
    assert "項目ごとの確認" in n and "予約全体の最終確認" in n
    assert "同意として扱ってはいけません" in n or "同意として扱ってください" in n, (
        "個別項目確認と予約全体同意の区別ルールが見当たりません"
    )
    assert "個別項目確認への" in n and "流用する" in n and (
        "絶対にしないでください" in n
    ), "個別項目確認への返事を予約同意として流用することの禁止が見当たりません"
    print("5/6. 個別項目への返事は予約同意にならない・最終確認への明確な肯定のみが同意: OK")


def test_consent_invalidated_on_later_change():
    n = _normalized()
    assert "変更・訂正した場合" in n or ("変更" in n and "訂正" in n)
    assert "無効になります" in n
    assert "check_availability" in n, "日時変更時のcheck_availability再確認への言及が見当たりません"
    assert "最終確認をやり直" in n or "改めて最終確認" in n
    print("7. 最終確認後の変更・訂正で同意が無効になり、最終確認をやり直す: OK")


def test_ambiguous_and_negative_replies_block_reservation():
    n = _normalized()
    assert "曖昧な場合" in n
    assert "create_reservation" in n and "呼び出さず" in n
    for phrase in ["やめます", "予約しません"]:
        assert phrase in n, f"否定・保留の返事の実例が見当たりません: {phrase}"
    assert "絶対に呼び出さないでください" in n
    print("8/9. 曖昧な返事では再確認、否定・保留の返事ではcreate_reservationを絶対に呼び出さない: OK")


def test_no_premature_success_announcement():
    n = _normalized()
    assert "success" in n and "true" in n
    banned_success_phrases = [
        "予約できました", "ご予約完了です", "お取りしました", "承りました", "予約確定しました",
    ]
    for phrase in banned_success_phrases:
        assert phrase in n, f"禁止される予約成立断定表現の実例が見当たりません: {phrase}"
    idx = n.find("success")
    # success:trueより前でこれらの表現が使われてはいけない、という文脈自体が
    # 「絶対にしないでください」という否定表現とセットで存在することを確認する。
    assert "絶対にしないでください" in n
    print("10. success:trueが返るまで予約成立を意味する断定表現を絶対にしない: OK")


def test_success_true_uses_only_returned_content():
    n = _normalized()
    assert "success" in n and ("true" in n)
    assert "完全に一致する内容だけ" in n or "完全に一致する内容" in n
    print("11. success:true後は返ってきた内容と完全に一致する内容のみ案内: OK")


def test_success_false_uses_reason_code_no_guessing():
    n = _normalized()
    assert "success" in n and "false" in n
    assert "reason_code" in n
    assert "推測することは絶対にしないでください" in n or (
        "推測する" in n and "絶対に" in n
    )
    print("12. success:falseはreason_codeに応じて案内し、空いている/予約できたと推測しない: OK")


def test_closing_after_success_is_short_and_ends_call():
    n = _normalized()
    assert "短く" in n
    assert "通話を自然に締めくくって" in n or "自然に締めくくって" in n
    print("13. 成立後は短く伝え、新たな質問を重ねず通話を自然に締めくくる: OK")


def main():
    test_heading_and_section_is_last_marker_preserved()
    test_combined_final_confirmation_instead_of_per_field()
    test_phone_digit_by_digit_readback()
    test_phone_no_announce_only_then_silence()
    test_per_field_reply_is_not_reservation_consent()
    test_consent_invalidated_on_later_change()
    test_ambiguous_and_negative_replies_block_reservation()
    test_no_premature_success_announcement()
    test_success_true_uses_only_returned_content()
    test_success_false_uses_reason_code_no_guessing()
    test_closing_after_success_is_short_and_ends_call()
    print("\n=== ALL smoke_test_booking_safety_contract.py CHECKS PASSED ===")


if __name__ == "__main__":
    main()
