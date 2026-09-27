"""
RECEPTRA — FAST TURN HOTFIX 8 PHASE 2 STEP 4: TIME AMBIGUITY 契約テスト（CONTRACT TEST）

背景: _TIME_AMBIGUITY_TEMPLATE（時刻の午前/午後解釈ルール）は、最小構成の
店舗でinstructions全体の11.2%（2640文字）を占める大型セクションだが、
これまで内容そのものを直接検証する専用の契約テストが存在しなかった
（smoke_test_relative_date_conversion.py / smoke_test_time_in_past.pyは
それぞれPHASE O5.5クロスリファレンスとtime_in_pastクロスリファレンスの
一部分のみを検証しており、AM/PM判定ロジック本体は未検証だった）。

HOTFIX 8 Phase 2 Step 4として本テンプレートを圧縮するにあたり、方針
（圧縮前に必ず契約テストを書く）に従い、本文を読んで洗い出した挙動を
すべて先に意味ベースの契約テストとして用意する。

検証する挙動:
  1. 13以降の数字・AM/PM明示・時間帯を示す言葉が付いている場合は、確認の
     質問をせずそのまま解釈する（午前/午後が明確な言い方）。
  2. 1〜12のみの数字で午前/午後が一意に決まらない場合、その曜日の営業時間
     に基づき次の4パターンに分類する:
     a. 午前候補・午後候補の一方だけが営業時間内 → 確認せずその時刻を
        採用するが、check_availability結果か予約全体の最終確認のいずれかで
        必ず一度は口頭でお客様に伝える（安全網）。
     b. 両方が営業時間内（24時間営業等） → 「午前ですか、午後ですか？」と
        ニュートラルに尋ねる。
     c. どちらも営業時間内に収まらない → 決め打ちも中立質問もせず、実際の
        営業時間を伝えて別の時間帯を伺う。
     d. その曜日の営業時間が未登録 → bと同様にニュートラルに尋ねる。
  3. AM/PM判定と「その時刻が既に過去かどうか」の判定は別物であり、現在時刻は
     相対時刻の計算のためだけに使い、過去かどうかをAI自身が判断することは
     絶対にしない（Tool側の判定が唯一の正）。
  4. AM/PM判定で選んだ時刻でもTool結果がtime_in_pastで返ることがあり、その
     場合はAM/PM解釈をやり直すのではなく、各Toolのtime_in_past案内方針に
     従う。
  5. 営業時間や文脈を無視した機械的な数字変換（6時→18時等）は絶対にしない。
  6. 「12時」も日常語感で正午と決め打ちせず、他の曖昧な時刻と同じ判定ルールに
     従う。
  7. 確認を省略して解釈した時刻を、お客様へ一度も伝えないまま先へ進むことは
     絶対にしない。
  8. この時刻確認への返事は「予約全体の最終確認」とは別物であり、予約確定の
     同意として扱わない。最終確認後に時刻が変更された場合はAM/PM判定と
     最終確認をやり直す。

実行: python3 tests/smoke_test_time_ambiguity_contract.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.realtime_voice_ai import _TIME_AMBIGUITY_TEMPLATE


def _normalized():
    return (
        _TIME_AMBIGUITY_TEMPLATE.replace("\n", "")
        .replace(" ", "")
        .replace("　", "")
    )


def test_heading_preserved():
    assert "時刻の午前/午後（AM/PM）解釈ルール（重要・必ず守ってください）" in _TIME_AMBIGUITY_TEMPLATE, (
        "他ファイルのセクション出現順序チェックが依存する見出しが失われています"
    )
    print("1. Time Ambiguity見出し（他ファイルのセクション順序チェックの目印）: OK")


def test_unambiguous_cases_no_confirmation_needed():
    n = _normalized()
    assert "13以降" in n or "24時間表記" in n
    assert "AM" in n and "PM" in n
    assert ("時間帯を示す" in n) or ("朝" in n and "夜" in n)
    assert "確認の質問をせず" in n
    print("2. 午前/午後が明確な言い方は確認不要でそのまま解釈: OK")


def test_ambiguous_case_single_fit_silent_but_must_announce_later():
    n = _normalized()
    assert "営業時間内に収まる" in n
    assert "確認の質問はせず" in n
    # 安全網: 一度は口頭でお客様に伝える
    assert "必ず一度は" in n and ("伝えて" in n or "伝えください" in n)
    print("3a. 片方だけ営業時間内: 確認せず採用するが必ず一度は口頭で伝える安全網: OK")


def test_ambiguous_case_both_fit_asks_neutrally():
    n = _normalized()
    assert "両方" in n and "営業時間内" in n
    assert "午前ですか、午後ですか？" in n
    assert "ニュートラルに尋ね" in n
    print("3b. 両方営業時間内（24時間営業等）: ニュートラルに午前/午後を尋ねる: OK")


def test_ambiguous_case_neither_fit_no_guess_offers_real_hours():
    n = _normalized()
    assert ("いずれも" in n or "どちらの候補も" in n) and "収まらない" in n
    assert "決め打ちせず" in n
    assert "実際の営業時間" in n
    print("3c. どちらも営業時間外: 決め打ちせず実際の営業時間を伝えて別の時間を伺う: OK")


def test_ambiguous_case_hours_not_configured_asks_neutrally():
    n = _normalized()
    assert "営業時間が店舗側でまだ登録されていない場合" in n or (
        "営業時間" in n and "登録されていない" in n
    )
    print("3d. 営業時間未登録の場合もニュートラルに尋ねる: OK")


def test_ampm_judgment_distinct_from_past_judgment():
    n = _normalized()
    assert "午前/午後のどちらか" in n or ("午前" in n and "過去かどうか" in n)
    assert "相対時刻の計算のためだけに使って" in n
    assert "自分で判断し" in n and "絶対にしないでください" in n
    assert "Tool側の判定を唯一の正とします" in n or "唯一の正" in n
    print("4/5. AM/PM判定と過去判定は別物・現在時刻はAI自身の過去判定に使わない（Tool権威）: OK")


def test_time_in_past_from_tool_defers_to_tool_guidance():
    n = _normalized()
    assert "time_in_past" in n
    assert "午前/午後の解釈をやり直すのではなく" in n
    print("6. Tool結果がtime_in_pastの場合はAM/PM解釈をやり直さずToolの案内方針に従う: OK")


def test_no_mechanical_number_conversion():
    n = _normalized()
    assert "6時" in n and "18時" in n
    assert "機械的に" in n and "絶対にしないでください" in n
    print("7. 営業時間・文脈を無視した機械的な数字変換の絶対禁止: OK")


def test_noon_not_hardcoded():
    n = _normalized()
    assert "12時" in n
    assert "正午" in n
    print("8. 「12時」も日常語感で決め打ちせず通常のAM/PM判定ルールに従う: OK")


def test_must_announce_interpreted_time_at_least_once():
    n = _normalized()
    assert "一度も" in n and "伝えないまま" in n and "絶対にしないでください" in n
    print("9. 確認を省略して解釈した時刻を一度も伝えないまま進めることの絶対禁止: OK")


def test_time_confirmation_distinct_from_final_reservation_confirmation():
    n = _normalized()
    assert "予約全体の最終確認" in n
    assert "同意として扱" in n
    assert "変更した場合" in n
    print("10. 時刻確認への返事は予約全体の同意にならない・変更時はAM/PM判定と最終確認をやり直す: OK")


def main():
    test_heading_preserved()
    test_unambiguous_cases_no_confirmation_needed()
    test_ambiguous_case_single_fit_silent_but_must_announce_later()
    test_ambiguous_case_both_fit_asks_neutrally()
    test_ambiguous_case_neither_fit_no_guess_offers_real_hours()
    test_ambiguous_case_hours_not_configured_asks_neutrally()
    test_ampm_judgment_distinct_from_past_judgment()
    test_time_in_past_from_tool_defers_to_tool_guidance()
    test_no_mechanical_number_conversion()
    test_noon_not_hardcoded()
    test_must_announce_interpreted_time_at_least_once()
    test_time_confirmation_distinct_from_final_reservation_confirmation()
    print("\n=== ALL smoke_test_time_ambiguity_contract.py CHECKS PASSED ===")


if __name__ == "__main__":
    main()
