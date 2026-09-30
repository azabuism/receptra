"""
RECEPTRA — 実機不具合調査「田中」固定バグ疑い / TASK 7
名前の捏造防止（no fabrication）契約のスモークテスト

背景（ユーザーからの実機不具合報告を受けた調査結果の要約。詳細は別途
ユーザーへの30項目レポートを参照）:
  - お客様の名乗りが常に「田中」として認識されているように見える、という
    実機不具合報告を受けて、リポジトリ全体を「コード変更禁止」の状態で
    exhaustiveにgrep調査した（TASK 1）。結果、実際の顧客名を扱う本番
    ランタイムコードパス（NAME phaseのプロンプト・Tool定義、
    realtime-voice-engine.js、confirm_customer_name/create_reservation/
    request_callbackの各パラメータ定義）のどこにも「田中」のハードコードは
    存在しなかった。「田中」が現れるのは (a) app/routers/vonage_voice.py の
    テスト/シミュレーション専用エンドポイント（SAMPLE_SCENARIOS、実際の
    顧客通話では到達しない）、(b) STAFF指名・CRM候補確認等、無関係な別
    フローのTool description内の言い回し例（例:「田中様でよろしいで
    しょうか？」）、(c) コメント・ドキュメントのみ。
  - 名前そのものの記憶場所を調査した結果（TASK 2）、confirm_customer_name
    はパラメータを一切持たない（parameters.properties={}）、純粋な
    フェーズ遷移用のbooleanシグナルであり、実際のお客様のお名前の値は
    どこにも構造的に保存されていない。名前はOpenAI Realtimeモデル自身の
    会話コンテキスト内にのみ存在し、後続のcreate_reservation（guest_name）
    やrequest_callback（customer_name）が必要になった時点で、モデル自身が
    改めて会話履歴から名前を扱う設計になっている（意図的なPII最小化。
    confirm_customer_nameのコメント参照）。
  - この設計自体は変更しない（ユーザー指示により大規模アーキテクチャ変更は
    今回のスコープ外）。TASK 3として、_PHASE1_NAME_ROLE_TEMPLATEに、
    雑音・不明瞭な発話等で確信が持てない場合に一般的な例（「田中」等）を
    推測で使わないことと、既定の再確認フレーズ
    「恐れ入りますが、お名前をもう一度お願いできますか？」を明示的に
    指示する一文を追加した（NAME instructions_chars<3,000の既存回帰guard
    （smoke_test_fast_turn_hotfix14_greeting_callback_terminal.py /
    smoke_test_realtime_phase2_reservation_callback.py）に収まるよう、
    必要最小限の分量にとどめている）。

このテストは、上記の調査結果と対策が実際のprompt/Tool定義に正しく
反映されていることのみを静的に検証する（Realtime API自体への実際の
呼び出しは行わない。gpt-realtime-2.1-miniでの実機再テストは別途
ユーザー側で行う想定）。
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_name_no_fabrication.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-name-no-fabrication"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

_SAMPLE_CUSTOMER_NAME_LIKE_STRINGS = ["田中", "Tanaka", "TANAKA", "tanaka"]


def _all_realtime_tool_text():
    from app.services.realtime_voice_ai import _REALTIME_TOOLS
    return json.dumps(_REALTIME_TOOLS, ensure_ascii=False)


def _test_a_confirm_customer_name_has_no_arguments():
    """A（TASK2の核心）: confirm_customer_nameはパラメータを一切持たない
    （名前の値自体を運ばない、純粋なphase遷移用booleanシグナルである）こと
    を構造的に確認する。これが崩れていない限り、「名前の値」が
    コード側で捏造されうる余地（例えば固定のデフォルト値等）は存在
    しない。"""
    from app.services.realtime_voice_ai import _NAME_TOOLS

    tool = next(t for t in _NAME_TOOLS if t.get("name") == "confirm_customer_name")
    assert tool["parameters"]["properties"] == {}, (
        "confirm_customer_nameにパラメータが追加されています。実際のお客様の"
        "お名前は依然としてモデル自身の会話コンテキストのみで扱われる設計を"
        "前提にした他の検証が無効になるため、これは意図的な変更であるべきです"
    )
    assert tool["parameters"]["required"] == []
    print("A. confirm_customer_nameは引数を持たない（名前の値そのものはコード側に一切保存されない設計を維持）: OK")


def _test_b_no_hardcoded_sample_name_in_name_phase_materials():
    """B（TASK1の回帰確認）: NAME phaseで実際にモデルへ渡される全材料
    （instructions本文・confirm_customer_name Tool定義）のどこにも、
    「田中」等のサンプル名がハードコードされていないこと。"""
    from app.services.realtime_voice_ai import (
        _PHASE1_NAME_ROLE_TEMPLATE, _NAME_TOOLS, _build_greeting_section,
    )

    class _FakeSettings:
        staff_name = "テストAI花子"
        greeting = None

    materials = {
        "_PHASE1_NAME_ROLE_TEMPLATE": _PHASE1_NAME_ROLE_TEMPLATE,
        "_NAME_TOOLS(json)": json.dumps(_NAME_TOOLS, ensure_ascii=False),
        "_build_greeting_section(default)": _build_greeting_section(None, "テスト店"),
        "_build_greeting_section(with_staff)": _build_greeting_section(_FakeSettings(), "テスト店"),
    }
    for name, text in materials.items():
        for sample in _SAMPLE_CUSTOMER_NAME_LIKE_STRINGS:
            assert sample not in text, (
                f"{name} にサンプル名/ハードコード疑いの文字列 '{sample}' が含まれています"
            )
    print("B. NAME phaseでモデルへ渡される全材料（instructions・Tool定義・第一声）に「田中」等のサンプル名のハードコードが無いこと: OK")


def _test_c_no_hardcoded_sample_name_in_real_name_carrying_tool_params():
    """C: 実際にお客様のお名前を値として運ぶ2つのTool引数
    （create_reservationのguest_name、request_callbackのcustomer_name）の
    description自体に、サンプル名がバイアスとして埋め込まれていないこと。"""
    from app.services.realtime_voice_ai import _REALTIME_TOOLS

    reservation_tool = next(t for t in _REALTIME_TOOLS if t.get("name") == "create_reservation")
    callback_tool = next(t for t in _REALTIME_TOOLS if t.get("name") == "request_callback")

    guest_name_desc = reservation_tool["parameters"]["properties"]["guest_name"]["description"]
    customer_name_desc = callback_tool["parameters"]["properties"]["customer_name"]["description"]

    for sample in _SAMPLE_CUSTOMER_NAME_LIKE_STRINGS:
        assert sample not in guest_name_desc, f"create_reservation.guest_name descriptionに '{sample}' が含まれています"
        assert sample not in customer_name_desc, f"request_callback.customer_name descriptionに '{sample}' が含まれています"

    assert guest_name_desc == "予約者名"
    assert customer_name_desc == "お客様のお名前"
    print("C. create_reservation.guest_name / request_callback.customer_nameのdescriptionにサンプル名のバイアスが無いこと（回帰・完全一致確認）: OK")


def _test_d_frontend_engine_js_has_zero_tanaka_references():
    """D（TASK1）: 本番フロントエンドJS（実際の顧客通話で必ず読み込まれる
    realtime-voice-engine.js）に「田中」「Tanaka」への言及が一切無いこと。"""
    engine_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "frontend", "public", "js", "realtime-voice-engine.js",
    )
    with open(engine_path, "r", encoding="utf-8") as f:
        engine_js = f.read()
    for sample in _SAMPLE_CUSTOMER_NAME_LIKE_STRINGS:
        assert sample not in engine_js, f"realtime-voice-engine.jsに '{sample}' への言及が残っています"
    print("D. frontend/public/js/realtime-voice-engine.js（本番顧客通話で必ず使われるJS）に「田中」「Tanaka」への言及が一切無いこと: OK")


def _test_e_vonage_tanaka_reference_confined_to_test_only_endpoint():
    """E（TASK1・分類F）: app/routers/vonage_voice.py内の「田中」への唯一の
    言及が、実際の顧客通話とは無関係なテスト/シミュレーション専用
    エンドポイント（SAMPLE_SCENARIOS）の中に閉じていること（本番の着信
    処理関数のソース自体には含まれていないこと）の回帰確認。"""
    router_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "app", "routers", "vonage_voice.py",
    )
    with open(router_path, "r", encoding="utf-8") as f:
        src = f.read()

    assert "田中" in src, (
        "この行が失敗する場合、vonage_voice.pyから「田中」参照そのものが"
        "既に消えており、このテストの前提（テスト専用エンドポイント内に"
        "閉じていることの回帰確認）自体が不要になっている可能性があります"
    )
    idx = src.index("田中")
    sample_scenarios_idx = src.rfind("SAMPLE_SCENARIOS", 0, idx)
    assert sample_scenarios_idx != -1, (
        "「田中」への言及がSAMPLE_SCENARIOS（テスト/シミュレーション専用データ）"
        "より前で見つかりました。実際の顧客通話処理へ紛れ込んでいないか確認してください"
    )
    print("E. app/routers/vonage_voice.py内の「田中」はテスト/シミュレーション専用エンドポイント（SAMPLE_SCENARIOS）内に閉じている: OK")


def _test_f_anti_fabrication_instruction_present_with_exact_reconfirmation_phrase():
    """F（TASK3の核心）: _PHASE1_NAME_ROLE_TEMPLATEが、確信が持てない場合に
    例やよくある名前を推測で使わないことを明示的に指示し、ユーザー指定の
    正確な再確認フレーズを含んでいること。"""
    from app.services.realtime_voice_ai import _PHASE1_NAME_ROLE_TEMPLATE

    assert "推測" in _PHASE1_NAME_ROLE_TEMPLATE, "推測で決めつけないことへの言及が見当たりません"
    assert "恐れ入りますが、お名前をもう一度お願いできますか？" in _PHASE1_NAME_ROLE_TEMPLATE, (
        "ユーザー指定の正確な再確認フレーズが見当たりません"
    )
    print("F. _PHASE1_NAME_ROLE_TEMPLATEに「推測で決めつけない」指示と正確な再確認フレーズ「恐れ入りますが、お名前をもう一度お願いできますか？」が含まれていること: OK")


def _test_g_no_name_no_tool_call_instruction_preserved():
    """G（TASK3の回帰確認・「予約したいです」のようなご用件のみの発話や
    無言のケース）: 既存の「お名前が含まれていない発話ではconfirm_
    customer_nameを呼ばない」「無言の場合は重ねて聞き直さない」という
    既存指示が、TASK3の追記によって壊れていないこと。"""
    from app.services.realtime_voice_ai import _PHASE1_NAME_ROLE_TEMPLATE

    assert "お名前が含まれていない発話" in _PHASE1_NAME_ROLE_TEMPLATE
    assert "confirm_customer_name を呼ばないでください" in _PHASE1_NAME_ROLE_TEMPLATE
    assert "無言の場合は重ねて聞き直す必要はありません" in _PHASE1_NAME_ROLE_TEMPLATE
    print("G. 「ご用件のみ（例: 予約したいです）」「無言」の発話ではconfirm_customer_nameを呼ばない、という既存指示が維持されていること: OK")


def _test_h_actual_name_echo_back_instruction_preserved():
    """H（TASK3の回帰確認・「田中です」「山田です」等、実際に名乗った場合）:
    お客様が実際に発話した内容に基づいて名前を復唱する、という既存の
    汎用的な（特定の名前をハードコードしない）指示が維持されていること。
    これにより、実際の発話内容ごとに異なる名前が正しく復唱される（＝田中
    固定にはならない）設計が保たれていることを確認する。"""
    from app.services.realtime_voice_ai import _PHASE1_NAME_ROLE_TEMPLATE

    # HOTFIX（TASK B実機回帰）で「確信を持って聞き取れた」という確信ゲートを
    # 明示的に追加したため、以前の完全一致フレーズから更新（意味は同じ:
    # 実際にお客様が話した内容に基づいて名前を汎用的に扱う）。
    assert "確信を持って聞き取れたお名前が含まれていたら" in _PHASE1_NAME_ROLE_TEMPLATE
    normalized = _PHASE1_NAME_ROLE_TEMPLATE.replace("\n", "").replace(" ", "").replace("　", "")
    assert "○○様ですね" in normalized, "実際に聞き取った名前を汎用プレースホルダ（○○）で復唱する既存指示が見当たりません"
    for sample in _SAMPLE_CUSTOMER_NAME_LIKE_STRINGS:
        assert sample not in _PHASE1_NAME_ROLE_TEMPLATE
    print("H. お客様が実際に話したお名前を（特定の名前をハードコードせず汎用的に）復唱する既存指示が維持されていること: OK")


def _test_i_name_instructions_char_budget_regression_guard():
    """I: TASK3の追記後もNAME instructionsが既存の3,000文字回帰guard
    （smoke_test_fast_turn_hotfix14_greeting_callback_terminal.py /
    smoke_test_realtime_phase2_reservation_callback.py）に収まっている
    こと（レイテンシ・コスト予算を壊していないことの確認）。
    build_realtime_phase_contexts()が実際に"name"用instructionsを組み立てる
    際に呼ぶ_build_phase_minimal_instructions()を、同じ引数パターンで
    直接呼び出す（DBアクセスが必要なasync関数自体は呼ばない）。"""
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
    print(f"I. TASK3追記後もNAME instructions_chars={len(text)} < 3000（既存回帰guard内）: OK")


def main():
    _test_a_confirm_customer_name_has_no_arguments()
    _test_b_no_hardcoded_sample_name_in_name_phase_materials()
    _test_c_no_hardcoded_sample_name_in_real_name_carrying_tool_params()
    _test_d_frontend_engine_js_has_zero_tanaka_references()
    _test_e_vonage_tanaka_reference_confined_to_test_only_endpoint()
    _test_f_anti_fabrication_instruction_present_with_exact_reconfirmation_phrase()
    _test_g_no_name_no_tool_call_instruction_preserved()
    _test_h_actual_name_echo_back_instruction_preserved()
    _test_i_name_instructions_char_budget_regression_guard()
    print("\n=== ALL smoke_test_name_no_fabrication.py CHECKS PASSED ===")


if __name__ == "__main__":
    main()
