"""
RECEPTRA — NAME実機誤認識 HOTFIX 2
「谷村です」→「田村様ですね」誤確定の根本調査（架空調査／根本原因監査）

このテストは「谷村」対「田村」という特定の名字の問題を修正するものでは
ない。ユーザーの明示的な指示により、特定名字のハードコード・特別扱い・
辞書登録は一切行っていない（N2で、そのような特別扱いが存在しないことを
逆に検証する）。

本HOTFIXの調査結論（詳細はユーザーへの最終報告を参照）:
  - RECEPTRAのRealtime session（app/services/realtime_voice_ai.py
    create_realtime_session()）はaudio.input.transcriptionを一切
    設定していない。そのためOpenAI Realtime APIのconversation.item.
    input_audio_transcription.*イベントは発生せず、お客様の発話内容に
    対する、モデルの発話生成そのものとは独立したテキスト・confidence・
    logprob相当の情報は、backend/frontendのどこにも存在しない。
  - speech_started/speech_stoppedイベントにもconfidence相当の
    フィールドは存在しない（audio_start_ms/audio_end_ms/item_id/
    event_idのみ）。
  - confirm_customer_nameは意図的にarg-zero（parameters.properties=
    {}）であり、モデルが実際に「何と聞き取ったか」を構造化データとして
    外部化する経路も存在しない。
  - 結果として、_PHASE1_NAME_ROLE_TEMPLATEの「確信ゲート」（確信を持って
    聞き取れた場合のみ受理し、確信が持てない場合は聞き返す）は、実データに
    基づくdeterministicな判定ではなく、モデルへの自然言語指示のみに
    依拠するself-reportedな確信度である。モデル自身が音声を（実際には
    誤って）高い確信度で認識してしまった場合（今回の「谷村→田村」の
    ような、モデル自身にとっては曖昧ではない誤認識）、この自然言語による
    確信ゲートは原理的に発火し得ない。
  - この限界は、instructions文言の調整だけでは解消できない現行
    アーキテクチャの構造的な限界であり、根本的な改善にはinput_audio_
    transcriptionの有効化・Tool schema変更・全員必須の復唱確認導入等の
    比較的大きな変更が必要と判断した（ユーザーへの最終報告で選択肢A〜Eを
    比較のうえ提示し、実装はユーザーの承認を待つ。本テストの時点では
    それらは実装していない）。

このテストは、上記の監査結果が実際のコードに正しく反映されていること
（および、対症療法的な特定名字ハードコードが行われていないこと）のみを
静的に検証する。
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(
    tempfile.mkdtemp(), "smoke_hotfix2_name_arch_audit.db"
)
os.environ["SECRET_KEY"] = "smoke-test-secret-key-hotfix2-name-arch-audit"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

# 今回の実機報告に登場した名字。今後もこのテストにこれらの文字列を追加で
# 特別扱いしないこと自体がN2の検証対象（=対症療法の逆説的なガード）。
_INCIDENT_NAMES = ["谷村", "田村"]
_OTHER_COMMON_SURNAME_SAMPLES = ["田中", "山田", "佐藤", "鈴木"]


def _test_n1_low_confidence_name_still_not_auto_confirmed():
    """N1（既存契約の回帰）: 聞き取りに確信が持てない発話ではconfirm_
    customer_nameを呼ばず、既定の再確認フレーズで聞き直す、という既存
    instructionsが、本HOTFIXでのコメント追記後も破壊されていないこと。"""
    from app.services.realtime_voice_ai import _PHASE1_NAME_ROLE_TEMPLATE

    assert "聞き取りに確信が持てない発話" in _PHASE1_NAME_ROLE_TEMPLATE
    assert "confirm_customer_name を呼ばないでください" in _PHASE1_NAME_ROLE_TEMPLATE
    assert "恐れ入りますが、お名前をもう一度お願いできますか？" in _PHASE1_NAME_ROLE_TEMPLATE
    print("N1. 聞き取りに確信が持てない発話では引き続きconfirm_customer_nameを呼ばず再確認する契約が維持されていること: OK")


def _test_n2_no_special_case_dictionary_for_incident_names():
    """N2（対症療法禁止の検証）: 「谷村」「田村」を含め、特定の名字を
    NAME phase instructions・Greeting・confirm_customer_name Tool定義の
    どこにも特別扱い（サンプル・例示・辞書登録）として追加していないこと。
    これは今回のユーザー指示（特定名字だけを直すタスクにしない）の直接的な
    回帰ガードである。"""
    from app.services.realtime_voice_ai import (
        _PHASE1_NAME_ROLE_TEMPLATE, _NAME_TOOLS, _build_greeting_section,
    )

    materials = {
        "_PHASE1_NAME_ROLE_TEMPLATE": _PHASE1_NAME_ROLE_TEMPLATE,
        "_NAME_TOOLS(json)": json.dumps(_NAME_TOOLS, ensure_ascii=False),
        "_build_greeting_section": _build_greeting_section(None, "サンプル店"),
    }
    for name, text in materials.items():
        for sample in _INCIDENT_NAMES + _OTHER_COMMON_SURNAME_SAMPLES:
            assert sample not in text, (
                f"{name} に特定の名字 '{sample}' が特別扱い（ハードコード）として含まれています"
                "（対症療法禁止の指示に反する可能性があります）"
            )
    print("N2. 「谷村」「田村」等の特定名字がNAME/Greeting/Tool定義のどこにも特別扱いとして追加されていないこと: OK")


def _test_n3_no_deterministic_confidence_source_currently_exists():
    """N3（構造監査・現状確認・PHONE CAPTURE DIAGNOSTIC PHASE 1後に更新）:
    PHONE CAPTURE DIAGNOSTIC PHASE 1（2026年9月）で、session_config
    （create_realtime_session()）へaudio.input.transcriptionがPII-freeな
    診断専用の並行経路として追加された（HOTFIX2調査時点からのアーキテク
    チャ変更。本テストはそれ自体を禁止するものではなく、「追加された
    transcriptionが、このNAME確信ゲート・confirm_customer_name・
    customer_phoneのいずれの既存の意思決定ロジックにも接続されていない」
    ことを引き続き構造的に保証する（regression guard）。この接続が
    将来追加された場合はこのテストが失敗することで変更が検知される。"""
    import inspect
    from app.services import realtime_voice_ai

    session_src = inspect.getsource(realtime_voice_ai.create_realtime_session)
    assert '"transcription"' in session_src, (
        "PHONE CAPTURE DIAGNOSTIC PHASE 1で追加されたはずのaudio.input."
        "transcription設定がcreate_realtime_session()に見当たりません"
        "（本テストの前提が変化しています。テストの更新が必要です）"
    )

    engine_js_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "frontend", "public", "js", "realtime-voice-engine.js",
    )
    with open(engine_js_path, encoding="utf-8") as f:
        engine_js_src = f.read()
    start = engine_js_src.index(
        "} else if (type === 'conversation.item.input_audio_transcription.completed'"
    )
    end = engine_js_src.index("} else if (type === 'response.created') {", start)
    transcription_handler_block = engine_js_src[start:end]
    # 説明コメント（否定形での言及。例:「customer_phone等には使わない」）を
    # 誤検知しないよう、行頭が`//`のコメント専用行を除いた実コードのみを
    # 検査対象にする（smoke_test_input_audio_transcription_diagnostic.py
    # のTEST E/F/Gと同じ手法）。
    transcription_handler_code_only = "\n".join(
        line for line in transcription_handler_block.split("\n")
        if not line.strip().startswith("//")
    )

    # transcriptionハンドラがconfirm_customer_name/customer_phone等、
    # いずれの既存の意思決定ロジックにも一切接続していないことを実測する
    # （transcript本文を読み取って使う経路が無いことの直接確認）。
    assert "confirm_customer_name" not in transcription_handler_code_only, (
        "transcriptionハンドラがconfirm_customer_nameへ関与している疑いが"
        "あります（NAME確信ゲートとの接続禁止に抵触する可能性）"
    )
    assert "customer_phone" not in transcription_handler_code_only, (
        "transcriptionハンドラがcustomer_phoneへ関与している疑いがあります"
    )
    print("N3. audio.input.transcriptionはPHONE CAPTURE DIAGNOSTIC PHASE 1により"
          "診断専用の並行経路として存在するが、NAME確信ゲート・"
          "confirm_customer_name・customer_phoneのいずれにも接続されて"
          "いないことを構造的に確認: OK")


def _test_n4_confidence_gate_limitation_honestly_documented_in_code():
    """N4（コード上誤魔化さない）: 「確信ゲート」がdeterministicな裏付け
    データに基づくものではなく、モデルへの自然言語指示のみに依拠する
    self-reportedな確信度であるという限界が、ソースコード上に明示的に
    文書化されていること（HOTFIX2で追記したコメントの回帰確認）。"""
    with open(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "app", "services", "realtime_voice_ai.py",
        ),
        encoding="utf-8",
    ) as f:
        src = f.read()

    assert "self-reportedな確信度" in src or "self-reported" in src, (
        "確信ゲートがLLM自己申告のみに依拠するものであるという限界の明示的な"
        "文書化が見当たりません"
    )
    assert "NAME確信ゲート・confirm_customer_name・customer_phone等、いずれの" in src, (
        "PHONE CAPTURE DIAGNOSTIC PHASE 1で追加されたaudio.input.transcriptionが"
        "NAME確信ゲート等の既存の意思決定ロジックに接続されていない、という"
        "事実の明示的な文書化が見当たりません"
    )
    print("N4. 「確信ゲート」がLLM自己申告のみに依拠する（deterministicな裏付けが無い）という限界がコード上に明示的に文書化されていること: OK")


def _test_n5_reconfirmation_does_not_drop_already_stated_purpose():
    """N5（既存契約の回帰）: 名前の再確認（聞き直し）が発生しても、同一
    発話内で既に話されたご用件を失わない（覚えておく）、という既存の
    instructionsが維持されていること。"""
    from app.services.realtime_voice_ai import _PHASE1_NAME_ROLE_TEMPLATE

    tmpl = _PHASE1_NAME_ROLE_TEMPLATE.replace("\n", "")
    idx = tmpl.find("お名前がまだ分からない場合")
    assert idx != -1
    nearby = tmpl[idx:idx + 300]
    assert "ご用件が話されていれば覚えておき" in nearby, (
        "N5: 名前の再確認が必要な場合でも、既に話されたご用件を覚えておく指示が見つかりません"
    )
    print("N5. 名前の再確認（聞き直し）が発生しても、既に話されたご用件を失わない契約が維持されていること: OK")


def _test_n6_clear_name_first_answer_still_counts():
    """N6（既存契約の回帰）: 明瞭に聞き取れた名前は、HOTFIX2のコメント
    追記後も引き続き毎回聞き返されない（過剰確認防止・first answer must
    countの精神）という既存のHOTFIX1追記が維持されていること。"""
    from app.services.realtime_voice_ai import _PHASE1_NAME_ROLE_TEMPLATE

    assert "毎回聞き返し" in _PHASE1_NAME_ROLE_TEMPLATE, (
        "N6: 明瞭な名前を毎回聞き返さない、という過剰確認防止の指示が見つかりません"
    )
    assert "確信を持って聞き取れたお名前が含まれていたら" in _PHASE1_NAME_ROLE_TEMPLATE, (
        "N6: 受理側セクションの確信ゲート自体（HOTFIX1）が消えています"
    )
    print("N6. 明瞭な名前のfirst-answer-must-count（毎回聞き返さない）契約が維持されていること: OK")


def _test_confirm_customer_name_still_arg_zero_and_no_app_side_verification():
    """補助（E/F/G確認の回帰）: confirm_customer_nameが引き続きarg-zero
    であり（意図的なPII最小化設計）、アプリ側に名前の正誤を検証する
    構造的な仕組みが今回のHOTFIX2でも追加されていないこと（＝対症療法的な
    検証ロジックを勝手に実装していないことの確認）。"""
    from app.services.realtime_voice_ai import _NAME_TOOLS

    tool = next(t for t in _NAME_TOOLS if t.get("name") == "confirm_customer_name")
    assert tool["parameters"]["properties"] == {}
    assert tool["parameters"]["required"] == []
    print("補助. confirm_customer_nameは引き続きarg-zero（名前の値も検証ロジックもコード側に追加していないことの確認）: OK")


def _test_char_budget_still_holds():
    """補助: HOTFIX2のコメント追記（Pythonコメントのみ・モデルへの
    instructions文字列には一切含まれない）後も、NAME instructions文字数が
    既存の3,000文字回帰guardに収まっていること。"""
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
    print(f"補助. HOTFIX2後もNAME instructions_chars={len(text)} < 3000（既存回帰guard内、コメントはinstructions文字列に含まれないことも確認）: OK")


def main():
    _test_n1_low_confidence_name_still_not_auto_confirmed()
    _test_n2_no_special_case_dictionary_for_incident_names()
    _test_n3_no_deterministic_confidence_source_currently_exists()
    _test_n4_confidence_gate_limitation_honestly_documented_in_code()
    _test_n5_reconfirmation_does_not_drop_already_stated_purpose()
    _test_n6_clear_name_first_answer_still_counts()
    _test_confirm_customer_name_still_arg_zero_and_no_app_side_verification()
    _test_char_budget_still_holds()
    print("\n=== ALL smoke_test_hotfix2_name_recognition_architecture_audit.py CHECKS PASSED ===")


if __name__ == "__main__":
    main()
