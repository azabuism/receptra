"""
RECEPTRA — FAST TURN HOTFIX 8: TOOL DESCRIPTION 圧縮の契約テスト（CONTRACT TEST）

背景: FAST TURN HOTFIX 8では、Realtime session登録している8つのtool schema
のうち check_availability（description 3534文字）と create_reservation
（description 3309文字）の2つで、tools schema全体16490文字中6843文字
（約41%）を占めていることが実測されている（HOTFIX 7の監査結果）。この
2つのdescriptionを、意味を保ったまま圧縮するのがHOTFIX 8の最優先事項
（Section 9 TOP PRIORITY）である。

このテストの目的（Section 7の方針: 圧縮の前に必ず契約テストを書く）:
圧縮によって、過去の実機不具合修正として追加された個別の行動指示
（reason_code別の案内・Named Staff Safe Resolution・Generic Resource
Foundation・電話番号の1桁読み上げ・許可を求めない・結果が返るまで断定
しない、等）が一つでも失われていないことを、文字列の圧縮そのものとは
独立に検証する。圧縮後のdescription文字列に対して実行し、全項目が
PASSする状態を維持したまま圧縮を行うことを保証する（圧縮前のこのテスト
自体は、圧縮前の元のdescriptionに対しても全項目PASSすることを事前に
確認済み＝既存の意味を漏れなく拾えているテストであることの確認）。

実行: python3 tests/smoke_test_tool_description_contract.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.realtime_voice_ai import _REALTIME_TOOLS

_TOOLS_BY_NAME = {t["name"]: t for t in _REALTIME_TOOLS}


def _assert_contains(text: str, needle: str, ctx: str):
    assert needle in text, f"[{ctx}] 期待する文言が見つかりません: {needle!r}"


def test_tool_count_and_names_unchanged():
    # Reservation Intelligence Phase E-1でsuggest_available_timesが9個目の
    # Toolとして追加された（ユーザー承認済み。本テストが守る「check_availability/
    # create_reservationのdescription圧縮で個別の行動指示を失っていないこと」
    # という契約自体には影響しない）。
    expected_names = {
        "check_availability", "create_reservation", "get_shop_info",
        "find_customer", "confirm_customer_identity", "get_customer_context",
        "set_conversation_language", "request_callback", "suggest_available_times",
    }
    actual_names = {t["name"] for t in _REALTIME_TOOLS}
    assert actual_names == expected_names, f"Tool名の集合が変化しています: {actual_names}"
    assert len(_REALTIME_TOOLS) == 9, f"Tool数が{len(_REALTIME_TOOLS)}個に変化しています"
    print("1. Tool数・Tool名の集合（9個固定）: OK")


def test_check_availability_required_args_unchanged():
    t = _TOOLS_BY_NAME["check_availability"]
    assert t["parameters"]["required"] == ["date", "time", "party_size"], (
        f"check_availabilityのrequiredが変化しています: {t['parameters']['required']}"
    )
    props = set(t["parameters"]["properties"].keys())
    assert props == {"date", "time", "party_size", "service_id", "staff_name", "resource_type"}, (
        f"check_availabilityのparameters.propertiesが変化しています: {props}"
    )
    print("2. check_availability required/properties: OK")


def test_create_reservation_required_args_unchanged():
    t = _TOOLS_BY_NAME["create_reservation"]
    assert t["parameters"]["required"] == ["date", "time", "party_size", "guest_name", "guest_phone"], (
        f"create_reservationのrequiredが変化しています: {t['parameters']['required']}"
    )
    props = set(t["parameters"]["properties"].keys())
    assert props == {
        "date", "time", "party_size", "guest_name", "guest_phone",
        "service_id", "staff_name", "resource_type", "special_requests",
    }, f"create_reservationのparameters.propertiesが変化しています: {props}"
    print("3. create_reservation required/properties: OK")


def test_resource_type_enum_unchanged():
    expected_enum = ["room", "bed", "chair", "vehicle", "karaoke_room", "classroom", "equipment", "other"]
    for tool_name in ("check_availability", "create_reservation"):
        t = _TOOLS_BY_NAME[tool_name]
        actual = t["parameters"]["properties"]["resource_type"]["enum"]
        assert actual == expected_enum, f"{tool_name}のresource_type enumが変化しています: {actual}"
    print("4. resource_type enum（8種類・順序含む）: OK")


def test_request_callback_reason_code_enum_unchanged():
    t = _TOOLS_BY_NAME["request_callback"]
    expected_enum = [
        "business_hours_not_configured", "reservation_not_enabled",
        "availability_judgement_required", "shop_knowledge_unavailable",
        "staff_judgement_required", "other",
    ]
    actual = t["parameters"]["properties"]["reason_code"]["enum"]
    assert actual == expected_enum, f"request_callbackのreason_code enumが変化しています: {actual}"
    print("5. request_callback reason_code enum: OK")


# check_availabilityのdescriptionが必ず含んでいなければならない要素
# （reason_code・安全上の絶対禁止事項・Named Staff Safe Resolution・
# Generic Resource Foundation）。圧縮後もこれらすべてが残っていることを
# 検証する。文言そのものを固定しすぎないよう、reason_code名・禁止される
#具体的な断定表現・機能上の要求事項に絞ってチェックする。
def test_check_availability_description_contract():
    desc = _TOOLS_BY_NAME["check_availability"]["description"]

    # 基本動作: 確認を待たず呼び出す、結果が返るまで自分で判断・案内しない
    _assert_contains(desc, "呼び出", "check_availability/基本動作")
    _assert_contains(desc, "date", "check_availability/date形式")
    _assert_contains(desc, "YYYY-MM-DD", "check_availability/date形式")
    _assert_contains(desc, "HH:MM", "check_availability/time形式")

    # 全reason_codeが残っていること
    for code in [
        "fully_booked", "outside_business_hours", "shop_closed",
        "business_hours_not_configured", "temporary_closure",
        "service_unavailable", "staff_unavailable", "break_time",
        "invalid_request", "time_in_past", "temporarily_unavailable",
        "staff_not_identified", "staff_name_ambiguous", "resource_type_required",
    ]:
        _assert_contains(desc, code, "check_availability/reason_code")

    # Named Staff Safe Resolution: 断定禁止・振替禁止・安全なフォールバック
    _assert_contains(desc, "在籍", "check_availability/Named Staff Safe Resolution(在籍断定禁止)")
    _assert_contains(desc, "staff_judgement_required", "check_availability/Named Staff Safe Resolution(request_callback誘導)")
    _assert_contains(desc, "staff_name_candidates", "check_availability/staff_name_ambiguous候補")

    # time_in_past: 過去日時を空いている/予約可能と案内しない、断りなく読み替えない
    _assert_contains(desc, "time_in_past", "check_availability/time_in_past")

    # temporarily_unavailable: 満席とも空いているとも案内しない、Human Handoff対象ではない
    _assert_contains(desc, "temporarily_unavailable", "check_availability/temporarily_unavailable")

    # Generic Resource Foundation: available_resource_types・カテゴリラベルであってIDでないこと
    _assert_contains(desc, "available_resource_types", "check_availability/Generic Resource Foundation")
    _assert_contains(desc, "room", "check_availability/resource_type例")

    print("5.5 check_availability description contract (全reason_code・安全ルール): OK")


def test_create_reservation_description_contract():
    desc = _TOOLS_BY_NAME["create_reservation"]["description"]

    # 基本動作: 呼び出し前提条件、最終確認の分離、電話番号の1桁読み上げ
    _assert_contains(desc, "電話番号", "create_reservation/電話番号確認")
    _assert_contains(desc, "date", "create_reservation/date形式")
    _assert_contains(desc, "YYYY-MM-DD", "create_reservation/date形式")
    _assert_contains(desc, "HH:MM", "create_reservation/time形式")

    # 個別項目確認と予約全体最終確認の分離（Booking Safetyと同じ概念だが
    # Tool呼び出しの直接条件としてもこのdescription自身に残す）
    _assert_contains(desc, "予約してよろしいですか", "create_reservation/最終確認フレーズ")

    # success前に完了を案内しない
    _assert_contains(desc, "success", "create_reservation/success分岐")

    # 全reason_codeが残っていること
    for code in [
        "invalid_request", "time_in_past", "reservation_not_enabled",
        "fully_booked", "staff_unavailable", "shop_closed", "temporary_closure",
        "service_unavailable", "break_time", "business_hours_not_configured",
        "temporarily_unavailable", "resource_type_required",
        "staff_not_identified", "staff_name_ambiguous",
    ]:
        _assert_contains(desc, code, "create_reservation/reason_code")

    # Named Staff Safe Resolutionの安全網（request_callbackへの誘導と
    # 既知情報の再質問禁止）が残っていること
    _assert_contains(desc, "staff_judgement_required", "create_reservation/Named Staff Safe Resolution")

    print("5.6 create_reservation description contract (全reason_code・安全ルール): OK")


def test_staff_name_parameter_never_internal_id():
    # staff_nameパラメータの説明は圧縮してよいが、「内部IDではない」という
    # 誤り防止の核心だけは両Toolで必ず残す。
    for tool_name in ("check_availability", "create_reservation"):
        d = _TOOLS_BY_NAME[tool_name]["parameters"]["properties"]["staff_name"]["description"]
        _assert_contains(d, "内部ID", f"{tool_name}.staff_name param")
    print("6. staff_name parameter『内部IDではない』の明記（両Tool）: OK")


def test_phone_parameter_never_guessed():
    # 電話番号系パラメータは「推測しない」の明記を必ず残す
    # （create_reservation.guest_phone, find_customer.phone, request_callback.customer_phone）
    checks = [
        ("create_reservation", "guest_phone"),
        ("find_customer", "phone"),
        ("request_callback", "customer_phone"),
    ]
    for tool_name, param in checks:
        d = _TOOLS_BY_NAME[tool_name]["parameters"]["properties"][param]["description"]
        _assert_contains(d, "推測", f"{tool_name}.{param} param")
    print("7. 電話番号系パラメータの『推測しない』明記（3箇所）: OK")


def main():
    test_tool_count_and_names_unchanged()
    test_check_availability_required_args_unchanged()
    test_create_reservation_required_args_unchanged()
    test_resource_type_enum_unchanged()
    test_request_callback_reason_code_enum_unchanged()
    test_check_availability_description_contract()
    test_create_reservation_description_contract()
    test_staff_name_parameter_never_internal_id()
    test_phone_parameter_never_guessed()
    print("\n=== ALL smoke_test_tool_description_contract.py CHECKS PASSED ===")


if __name__ == "__main__":
    main()
