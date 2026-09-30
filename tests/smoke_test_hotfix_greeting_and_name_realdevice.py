"""
RECEPTRA — TASK B実機回帰 HOTFIX: Greeting崩壊＋名前誤認識の緊急監査
契約テスト（G1-G4 / N1-N4）

背景: TASK B適用後の実機テストで (1) 通話開始時に「よろしくお願いします」
だけで始まる、(2) 店舗名が名乗られない、(3) お客様の名乗りが誤認識される、
という3つの実機不具合が報告された。原因調査の結果:

  - Greeting崩壊（Symptom 1/2）: _build_greeting_section() がモデルに
    「一字一句同じである必要はない」という緩い指示のみを与えており、
    ライブ生成フォールバック経路（Zero-Wait Greeting不成立時）で店舗名を
    含まない一般的な相槌（「よろしくお願いします」等）に短縮されうる
    構造だった。他の経路（_resolve_greeting_text・
    _build_phase_minimal_instructions・create_realtime_session・
    get_or_generate_greeting_audioのfingerprintキャッシュ）は全て店舗名を
    正しく含めており、原因ではないことを確認済み。対策として
    _build_greeting_section() に「店舗名を必ず含め」の明示と、
    「よろしくお願いします」等の一言のみでの終了を明示的に禁止する一文を
    追加した。

  - 名前誤認識（Symptom 3）: _PHASE1_NAME_ROLE_TEMPLATEの「## お名前を
    取得できたら」セクション（受理側）に確信度のゲートが無く、
    「## お名前がまだ分からない場合」セクション（再確認側）の発火条件が
    「名前が全く含まれていない発話」に限定されていたため、雑音等で
    曖昧に聞こえた音声をモデルが「名前が含まれている」と解釈した場合、
    再確認せず受理してしまう構造的な隙間があった。対策として、受理側の
    発火条件に「確信を持って」という確信ゲートを追加し、再確認側の発火
    条件を「聞き取りに確信が持てない発話」まで明示的に広げた。同時に
    「明瞭な名前は毎回聞き返さない」という既存の過剰確認防止の要件も
    壊れないよう明記した。

このテストは、上記の対策が実際のinstructions生成コードに正しく反映
されていることのみを静的に検証する（Realtime API自体への実際の呼び出し
は行わない。実機再テストは別途ユーザー側で行う想定）。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_hotfix_greeting_name.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-hotfix-greeting-name"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

_COMMON_SURNAME_SAMPLES = ["田中", "山田", "佐藤", "鈴木", "Tanaka", "Yamada", "Sato"]


def _test_g1_greeting_instruction_requires_shop_name():
    """G1: Greeting指示（_build_greeting_section）が、店舗名を必ず含める
    ことをモデルに明示的に指示していること。"""
    from app.services.realtime_voice_ai import _build_greeting_section

    text = _build_greeting_section(None, "サンプル居酒屋")
    assert "店舗名を必ず含め" in text, "店舗名を必ず含める、という明示的な指示が見当たりません"
    assert "サンプル居酒屋" in text, "実際の店舗名（挨拶文の中）が含まれていません"
    print("G1. Greeting指示が店舗名を必ず含めるよう明示的に指示していること: OK")


def _test_g2_greeting_forbids_bare_yoroshiku_only():
    """G2: Greeting指示が、「よろしくお願いします」等の一言だけで通話開始の
    第一声を済ませることを明示的に禁止していること（実機で報告された
    Symptom 1の直接対策）。"""
    from app.services.realtime_voice_ai import _build_greeting_section

    text = _build_greeting_section(None, "サンプル居酒屋")
    assert "よろしくお願いします" in text and (
        "済ませないでください" in text or "済ませることは絶対にしないでください" in text
    ), "「よろしくお願いします」等の一言のみでの終了を禁止する明示的な指示が見当たりません"
    print("G2. Greeting指示が「よろしくお願いします」等の一言のみでの終了を明示的に禁止していること: OK")


def _test_g3_greeting_still_asks_name_and_purpose_per_existing_contract():
    """G3: 既存の正式Greeting契約（smoke_test_conversation_quality_greeting_
    phase.py）通り、Greetingが引き続きお名前を尋ねる質問を含んでいること
    （HOTFIXでこの既存契約を壊していないことの回帰確認）。"""
    from app.services.realtime_voice_ai import (
        _build_greeting_section, _GREETING_NAME_QUESTION,
    )

    text = _build_greeting_section(None, "サンプル居酒屋")
    assert _GREETING_NAME_QUESTION in text, "既存のお名前を伺う質問文が見当たりません"
    print("G3. Greetingが既存契約通りお名前を伺う質問を含んでいること: OK")


def _test_g4_greeting_not_replaced_by_routing_continuation():
    """G4: NAME→ROUTINGの継続ロジック（Phase2）がGreeting自体を上書き・
    代替していないこと。ROUTING phaseのinstructionsはinclude_greeting=False
    で組み立てられ、Greetingセクション自体を含まない構造的分離を確認する
    （build_realtime_phase_contexts()参照）。"""
    from app.services.realtime_voice_ai import (
        _build_phase_minimal_instructions, _PHASE2_ROUTING_ROLE_TEMPLATE,
        _PHASE1_NAME_ROLE_TEMPLATE,
    )
    from app.models.shop import Shop
    from app.language_registry import REQUIRED_AI_LANGUAGE

    shop = Shop(name="サンプル居酒屋")
    routing_text = _build_phase_minimal_instructions(
        shop, None, [REQUIRED_AI_LANGUAGE], None,
        _PHASE2_ROUTING_ROLE_TEMPLATE, include_greeting=False,
    )
    name_text = _build_phase_minimal_instructions(
        shop, None, [REQUIRED_AI_LANGUAGE], None,
        _PHASE1_NAME_ROLE_TEMPLATE, include_greeting=True,
    )
    assert "電話に出たときの第一声" not in routing_text, (
        "ROUTING phaseのinstructionsにGreetingセクションが混入しています"
        "（NAME phaseで既に発話済みのはずのGreetingが再度・あるいは誤って"
        "継続ロジックにより生成される経路が無いことを保証できません）"
    )
    assert "電話に出たときの第一声" in name_text, "NAME phaseにはGreetingセクションが含まれている必要があります"
    print("G4. ROUTINGへの継続ロジックがGreetingセクション自体を含まない（Greetingを上書き・代替しない）構造的分離: OK")


def _test_n1_unclear_name_not_guessed_into_common_surname():
    """N1: NAME phase instructions自体に、曖昧な音声から一般的な姓を
    推測して埋める、という許可や具体的な姓のサンプルが含まれていない
    こと。"""
    from app.services.realtime_voice_ai import _PHASE1_NAME_ROLE_TEMPLATE

    for sample in _COMMON_SURNAME_SAMPLES:
        assert sample not in _PHASE1_NAME_ROLE_TEMPLATE, (
            f"NAME phase instructionsに具体的な姓のサンプル '{sample}' が含まれています"
            "（曖昧な音声からの推測埋めを誘発しうるため禁止）"
        )
    print("N1. NAME phase instructionsに一般的な姓のサンプル（田中・山田・佐藤等）のハードコードが無いこと: OK")


def _test_n2_low_confidence_audio_triggers_reconfirmation():
    """N2: 「聞き取りに確信が持てない」場合の発火条件が、名前が全く
    含まれていない発話だけでなく、聞き取りの確信度が低い発話にも明示的に
    広がっていること（Symptom 3の直接対策）。"""
    from app.services.realtime_voice_ai import _PHASE1_NAME_ROLE_TEMPLATE

    assert "聞き取りに確信が持てない発話" in _PHASE1_NAME_ROLE_TEMPLATE, (
        "「名前は聞こえたが確信が持てない」場合も再確認対象に含める、という"
        "発火条件の明示的な拡張が見当たりません"
    )
    assert "恐れ入りますが、お名前をもう一度お願いできますか？" in _PHASE1_NAME_ROLE_TEMPLATE
    print("N2. 聞き取りに確信が持てない発話が明示的に再確認（聞き直し）の対象に含まれていること: OK")


def _test_n3_clear_name_accepted_without_over_reconfirmation():
    """N3: 明瞭に聞き取れた名前については毎回聞き返さない、という過剰確認
    防止の要件（ユーザーの重要な逆要件）が明示されていること。"""
    from app.services.realtime_voice_ai import _PHASE1_NAME_ROLE_TEMPLATE

    assert "毎回聞き返し" in _PHASE1_NAME_ROLE_TEMPLATE, (
        "明瞭な名前を毎回聞き返さない、という過剰確認防止の指示が見当たりません"
    )
    # 受理側セクションにも確信ゲートがあり、無条件の受理ではないことを確認
    assert "確信を持って聞き取れたお名前が含まれていたら" in _PHASE1_NAME_ROLE_TEMPLATE
    print("N3. 明瞭に聞き取れた名前は毎回聞き返さない（過剰確認防止）という要件が明示されていること: OK")


def _test_n4_no_sample_name_in_production_instructions():
    """N4: NAME phase instructions・Greeting instructionsのどちらにも、
    サンプル名・例示名が本番プロンプトに混入していないこと（TASK7の
    既存契約の再確認を、より広い姓のサンプルセットで実施）。"""
    from app.services.realtime_voice_ai import (
        _PHASE1_NAME_ROLE_TEMPLATE, _build_greeting_section,
    )

    greeting_text = _build_greeting_section(None, "サンプル居酒屋")
    for sample in _COMMON_SURNAME_SAMPLES:
        assert sample not in _PHASE1_NAME_ROLE_TEMPLATE, f"NAME instructionsに '{sample}' が含まれています"
        assert sample not in greeting_text, f"Greeting instructionsに '{sample}' が含まれています"
    print("N4. NAME/Greeting instructionsのどちらにもサンプル名・例示名が混入していないこと: OK")


def _test_char_budget_still_holds_with_hotfix_wording():
    """補助: HOTFIXの文言追加後もNAME instructions文字数が既存の3,000文字
    回帰guardに収まっていること（smoke_test_name_no_fabrication.pyの
    test_iと同じ検証をこのファイル単体でも再確認する）。"""
    from app.services.realtime_voice_ai import (
        _build_phase_minimal_instructions, _PHASE1_NAME_ROLE_TEMPLATE,
    )
    from app.language_registry import REQUIRED_AI_LANGUAGE

    class _FakeShop:
        name = "テスト店"

    text = _build_phase_minimal_instructions(
        _FakeShop(), None, [REQUIRED_AI_LANGUAGE], None,
        _PHASE1_NAME_ROLE_TEMPLATE, include_greeting=True,
    )
    assert len(text) < 3000, f"NAME instructions_charsが3,000文字を超えています: {len(text)}"
    print(f"補助. HOTFIX後もNAME instructions_chars={len(text)} < 3000（既存回帰guard内）: OK")


def main():
    _test_g1_greeting_instruction_requires_shop_name()
    _test_g2_greeting_forbids_bare_yoroshiku_only()
    _test_g3_greeting_still_asks_name_and_purpose_per_existing_contract()
    _test_g4_greeting_not_replaced_by_routing_continuation()
    _test_n1_unclear_name_not_guessed_into_common_surname()
    _test_n2_low_confidence_audio_triggers_reconfirmation()
    _test_n3_clear_name_accepted_without_over_reconfirmation()
    _test_n4_no_sample_name_in_production_instructions()
    _test_char_budget_still_holds_with_hotfix_wording()
    print("\n=== ALL smoke_test_hotfix_greeting_and_name_realdevice.py CHECKS PASSED ===")


if __name__ == "__main__":
    main()
