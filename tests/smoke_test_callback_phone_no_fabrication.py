"""
RECEPTRA — ISSUE A監査後の最小mitigationスモークテスト
callback phone number fabrication mitigation の静的テスト

背景（監査結果の要約。詳細はユーザーへの13項目AUDITレポートを参照）:
gpt-realtime-2.1-miniでの実機テストにおいて、担当者への折り返し用の
電話番号（customer_phone）が客から聞けていない状況で、AIが客に尋ねずに
それらしい電話番号を独自に発話してしまう事象が観測された。監査の結果、
以下が判明した:
  - backend（request_callback_tool/RequestCallbackToolRequest）には
    customer_phoneの由来（客が実際に発話したものかどうか）を検証する
    仕組みが一切無く、文字数制約（1〜20文字）のみで受理される。
  - input_audio_transcriptionが未設定のため、クライアント側には客の
    発話内容の独立した文字起こしが存在しない。
  - 常に全セッションへ注入される_EXAMPLES_TEMPLATE_BASE（話し方の見本、
    「この温度感・テンポをそのまま真似てください」という指示付き）と、
    _build_language_rules_section()内の電話番号読み上げ方針の例に、
    「090-1234-5678」という具体的な電話番号らしい文字列がリテラルで
    埋め込まれていた。これが、AIが未知の電話番号を答える際の
    アンカリング（模倣元）になった可能性が高いという仮説を得た
    （モデル内部の生成過程は直接観測できないため、確証ではなく
    最有力の仮説として扱う）。

このフェーズで実装した最小mitigation（ユーザー指示により
input_audio_transcriptionの導入等の大規模なアーキテクチャ変更は
今回は行わない。deterministicなprovenance保証ではなく、あくまで
mitigationとしての位置づけ）:
  1. 上記のリテラルな電話番号例を、Realtimeへ送られるprompt/例文から
     全て除去し、具体的な数字ではなく「お客様へ尋ねる」という振る舞いを
     教える例に置き換えた（090-1234-5678等の具体的な数字は一切使わない）。
  2. request_callback Tool description・customer_phoneパラメータの
     description・_HUMAN_HANDOFF_TEMPLATEの電話番号確認手順の3箇所に、
     「customer_phoneが分かっていない場合はこの関数を呼び出さず、
     先に客へ尋ねる」「AI側で電話番号を考えて答えたり提案したりする
     ことは絶対にしない」「形式上それらしい番号であっても、それは
     客が実際に伝えた証拠にはならない」という明示的な不変条件を追加した。

このテストは、上記1・2がRealtimeへ送られる実際のprompt文字列に
正しく反映されていることのみを静的に検証する（Realtime API自体への
実際の呼び出しは行わない。gpt-realtime-2.1-miniでの実機再テストは
別途ユーザー側で行う想定）。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_callback_phone.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-callback-phone"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

_FABRICATION_ANCHOR_CANDIDATES = [
    "090-1234-5678",
    "090.1234.5678",
    "09012345678",
]


def _all_realtime_tool_text():
    """_REALTIME_TOOLS全体を1つの検索用文字列へ連結する（description・
    parameters内のdescriptionを含む。JSONへのdump相当でネストした
    description文字列も拾えるようにする）。"""
    import json
    from app.services.realtime_voice_ai import _REALTIME_TOOLS

    return json.dumps(_REALTIME_TOOLS, ensure_ascii=False)


def _test_no_literal_fabrication_anchor_phone_number():
    """1: Realtimeへ送られるprompt/例文材料のどこにも、模倣元になり得る
    具体的な電話番号らしいリテラル文字列（090-1234-5678等）が残って
    いないこと。"""
    from app.services.realtime_voice_ai import (
        _EXAMPLES_TEMPLATE_BASE, _HUMAN_HANDOFF_TEMPLATE, _build_language_rules_section,
    )
    from app.language_registry import REQUIRED_AI_LANGUAGE

    materials = {
        "_EXAMPLES_TEMPLATE_BASE": _EXAMPLES_TEMPLATE_BASE,
        "_HUMAN_HANDOFF_TEMPLATE": _HUMAN_HANDOFF_TEMPLATE,
        "_build_language_rules_section(ja_only)": _build_language_rules_section(
            [REQUIRED_AI_LANGUAGE], None
        ),
        "_build_language_rules_section(ja+en)": _build_language_rules_section(
            [REQUIRED_AI_LANGUAGE, "en"], [REQUIRED_AI_LANGUAGE]
        ),
        "_REALTIME_TOOLS(json)": _all_realtime_tool_text(),
    }

    for name, text in materials.items():
        for anchor in _FABRICATION_ANCHOR_CANDIDATES:
            assert anchor not in text, (
                f"{name} に模倣元になり得るリテラル電話番号 '{anchor}' が残っています"
            )

    print("1. 全prompt材料からリテラルな模倣元電話番号（090-1234-5678等）が除去されていること: OK")


def _test_request_callback_prohibits_fabrication():
    """2: request_callbackのtool description・customer_phoneパラメータの
    descriptionが、AIによる電話番号の生成・提案・代用を明示的に禁止して
    いること。"""
    from app.services.realtime_voice_ai import _REALTIME_TOOLS

    tool = next(t for t in _REALTIME_TOOLS if t.get("name") == "request_callback")
    tool_description = tool["description"]
    phone_param_description = tool["parameters"]["properties"]["customer_phone"]["description"]

    for phrase in ["考えて答えたり", "提案したり", "読み上げたり", "自動的に補完したり", "代用したり"]:
        assert phrase in tool_description, (
            f"request_callback descriptionに禁止行為の文言が見当たりません: {phrase}"
        )
    assert "絶対にしないで" in tool_description
    assert "それらしく見える電話番号であっても" in tool_description
    assert "実際に伝えたことの証拠にはなりません" in tool_description

    assert "AI側で考えた・推測した・それらしく組み立てた番号は絶対に" in phone_param_description

    print("2. request_callback description / customer_phone descriptionがAIによる電話番号生成・提案・代用を明示的に禁止していること: OK")


def _test_missing_phone_requires_asking_before_tool_call():
    """3: customer_phoneが分かっていない場合、request_callbackを呼び出す
    前に客へ尋ねる、という手順が明示されていること（呼び出しをブロック
    する処理は今回意図的に実装しておらず、あくまでprompt上の指示のみで
    あることも合わせて確認する＝mitigationであってdeterministicな
    provenance保証ではないことの裏付け）。"""
    from app.services.realtime_voice_ai import _REALTIME_TOOLS, _HUMAN_HANDOFF_TEMPLATE

    tool = next(t for t in _REALTIME_TOOLS if t.get("name") == "request_callback")
    tool_description = tool["description"]

    assert "customer_phoneがまだ分かっていない" in tool_description
    assert "この関数を呼び出さず、先にお客様へお電話番号を尋ねて" in tool_description

    normalized_handoff = _HUMAN_HANDOFF_TEMPLATE.replace("\n", "").replace(" ", "").replace("　", "")
    assert "電話番号がまだ分かっていない場合は、AI側で番号を考えたり読み上げたり提案したり" in normalized_handoff
    assert "必ずお客様へ尋ねてから先へ進んでください" in normalized_handoff

    print("3. customer_phone不明時は呼び出し前に客へ尋ねる、という手順がprompt上に明示されていること: OK")


def _test_customer_provided_phone_flow_still_documented():
    """回帰: 客が実際に電話番号を伝えた場合の1桁ずつ復唱・確認フロー自体は
    引き続き明確に指示されていること（mitigationが既存の正しい確認手順を
    壊していないことの確認）。"""
    from app.services.realtime_voice_ai import _REALTIME_TOOLS, _HUMAN_HANDOFF_TEMPLATE

    tool = next(t for t in _REALTIME_TOOLS if t.get("name") == "request_callback")
    tool_description = tool["description"]

    assert "電話番号は必ず1桁ずつ読み上げて復唱し" in tool_description
    assert "推測や聞き取れなかった桁の補完は絶対にしないでください" in tool_description
    normalized_handoff = _HUMAN_HANDOFF_TEMPLATE.replace("\n", "")
    assert "必ず1桁ずつ読み上げて復唱し" in normalized_handoff
    assert "推測や聞き取れなかった桁の補完は絶対にしないでください" in normalized_handoff

    print("回帰: 客が実際に電話番号を伝えた場合の1桁ずつ復唱・確認フローは無変更で維持されていること: OK")


def main():
    _test_no_literal_fabrication_anchor_phone_number()
    _test_request_callback_prohibits_fabrication()
    _test_missing_phone_requires_asking_before_tool_call()
    _test_customer_provided_phone_flow_still_documented()
    print("\n=== ALL smoke_test_callback_phone_no_fabrication.py CHECKS PASSED ===")


if __name__ == "__main__":
    main()
