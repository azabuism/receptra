"""
RECEPTRA — CALLBACK FLOW CONTAMINATION AUDIT（2026年9月）
再現テスト: 「担当者から折り返し連絡がほしいです」のようにCALLBACK/
HUMAN_HANDOFF意図が明確な会話で、RESERVATION専用の質問（人数・来店希望
日時・メニュー）やRESERVATION専用のTool呼び出し（check_availability/
create_reservation/find_customer）が発生しないことを検証する。

## 背景（実機で観測された不具合の要約）
実機のCALLBACK意図が明確な通話で、AIが「人数」（RESERVATION専用の質問）を
尋ねてしまい、通話終了時のログはphase=legacy_full、また
tool_result:find_customerがtool_result:request_callbackの直前に観測された。

## 監査で確認した事実（推測ではなくコードを直接確認した事実。詳細は
最終報告参照）
1. 専用のCALLBACK phase（_build_callback_phase_instructions() /
   _CALLBACK_TOOL_NAMES）は、Fast Reservation Flow・Customer Memory
   Rules・check_availability/create_reservation/find_customerの
   いずれも含まない。構造的に「人数」質問もfind_customer呼び出しも
   発生し得ない（本ファイルのTEST 1〜5で再確認する）。
2. legacy_full（build_realtime_instructions()）は「なんでもこなす
   フォールバックphase」であり、_FAST_RESERVATION_FLOW_TEMPLATE
   （人数案内を含む）と_CUSTOMER_MEMORY_RULES_TEMPLATE（電話番号を
   聞いたら無条件でfind_customerを呼ぶ指示）を、ご用件が実際に
   RESERVATIONかどうかの条件なしに常に含んでいた。これがtemplate
   contaminationの直接の原因であり、実機ログのphase=legacy_full /
   tool_result:find_customerという観測結果と整合する。
3. JS側のPhase遷移制御フロー（response.doneの遷移消費ブロック、
   sendRealtimePhaseSessionUpdate、tool_result継続のsendResponseCreate）
   を確認した結果、classify_intentで一度CALLBACKと確定した後に
   legacy_fullへ後から上書きされる経路は無い
   （pendingPhaseTransitionTarget === nullの条件により保護されている）。
   また、find_customerのtool_result継続処理自体もphaseに依存せず
   sendResponseCreate()を呼ぶだけであり、continuation側のcontrol flow
   自体に追加のRESERVATION誘導ロジックは無い。
   → 以上より、根本原因は「単純なtemplate/instruction content
   contamination」であり、大規模なcontrol-flow変更は不要と判断した。

## 実装した最小修正（本ファイルで検証する内容そのもの）
_FAST_RESERVATION_FLOW_TEMPLATEと_CUSTOMER_MEMORY_RULES_TEMPLATEの冒頭に、
「ご用件がCALLBACK/HUMAN_HANDOFFであると判断できている場合は、この
セクションの内容を一切適用しない」という明示的な適用条件（exclusivity
guard）を追加した。この2つのtemplateは、legacy_fullとRESERVATION phase
の2箇所でのみ使われており（RESERVATION phaseでは元々ご用件が
RESERVATIONと確定済みのため、このguardは常にtrueの無害な分岐となる）、
PHONE関連のtemplate・normalize_jp_phone_national・request_callbackの
電話番号桁数guard・JS側のphase遷移/tool_result継続control-flowには
一切手を加えていない。

## 本テストの限界（誠実な明記。「測って推測しない」の原則に基づく）
本テスト（および既存のsmoke_test_*.py群）はいずれも実際のOpenAI Realtime
API呼び出しを行わない、backendが生成するprompt文字列・Tool定義に対する
静的検証である。「実際にモデルがこのprompt/Toolを見て人数を尋ねなく
なるか」という会話レベルの再現・実証は実機テストでのみ判断可能であり、
本テストはそれを主張するものではない。本テストが検証するのは、
(a) 専用CALLBACK phaseが構造的にRESERVATION専用の質問/Toolを一切
発生させ得ないこと、および (b) legacy_fullフォールバックに入った場合でも
今回追加したexclusivity guardの文言が実際に生成されるinstructions文字列
に含まれていること、の2点である。

実行: python3 tests/smoke_test_callback_no_reservation_contamination.py
"""
import asyncio
import os
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_callback_contamination.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-callback-contamination"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402

# PHONE FIX FREEZE 回帰確認用: Task D/E で確定させた文言のうち、
# 存在してはいけないもの（修正前の矛盾した例文）と、存在すべきもの
# （修正後の一貫した3-4-4ブロック方式）を固定文字列で確認する。
_PHONE_OLD_CONTRADICTING_EXAMPLE = "ゼロ・キュウ・ゼロ"
_PHONE_NEW_CONSISTENT_EXAMPLE = "ゼロキュウゼロ"
_PHONE_DIGIT_COUNT_GUARD_PHRASE = "先頭の0を含めて10桁または11桁"

# CALLBACK最終案内文言（一字一句変更禁止・絶対保護対象）
_CALLBACK_FINAL_PHRASE = "担当者から折り返し連絡しますので、電話を切ってお待ちください。"

# 今回追加したexclusivity guardの中核フレーズ（存在確認用）
_FAST_RESERVATION_FLOW_GUARD_PHRASE = "このFast Reservation Flowセクション全体"
_CUSTOMER_MEMORY_RULES_GUARD_PHRASE = "このセクション（find_customerツールに関するルール）は、ご用件がRESERVATION"

# RESERVATION専用のセクション・強制的な質問を示すキーワード（CALLBACK
# phaseのinstructionsに一切出現してはならない）。
# 注意: bare「人数」「create_reservation」は_HUMAN_HANDOFF_TEMPLATE自身が
# 元々含む正当な文言（例:「該当する場合のみ、来店・予約希望の日時や人数
# （分かる範囲でよく、分からなければ無理に聞き出さなくて構いません）」
# という任意項目としての言及、「create_reservationの電話番号確認と同じ
# ルールです」という比較のための言及）にも一致してしまい誤検知するため、
# それらと区別できる、実際にFast Reservation Flow/Customer Memory Rulesの
# 中身そのものが混入したことを示す固有の文言のみを対象にする。
_RESERVATION_ONLY_KEYWORDS = [
    # Fast Reservation Flowセクションの見出しそのもの
    "「Fast Reservation Flow」の絶対ルール",
    # Customer Memory Rulesセクションの見出しそのもの
    "常連のお客様の認識に関するルール",
    # 人数を積極的に尋ねるよう指示するFast Reservation Flow固有のヒント文言
    "自然な流れの中で人数を尋ねてください",
    "何名様でのご予約でしょうか",
    # check_availabilityというTool名そのもの（Human Handoffセクションには
    # 一度も登場しない。真にCALLBACK phaseで呼び出し可能なToolはrequest_callback
    # のみであることの独立確認）
    "check_availability",
]


class _FakeSecret:
    value = "ek_fake_test_secret"
    expires_at = 9999999999


def _make_fake_client(captured):
    async def fake_create(**kwargs):
        captured["session"] = kwargs.get("session")
        return _FakeSecret()

    fake_client_secrets = MagicMock()
    fake_client_secrets.create = AsyncMock(side_effect=fake_create)
    fake_realtime = MagicMock()
    fake_realtime.client_secrets = fake_client_secrets
    fake_openai_client = MagicMock()
    fake_openai_client.realtime = fake_realtime
    return fake_openai_client


async def main():
    from app.main import app, lifespan
    from app.models.shop import Shop

    async with lifespan(app):
        from app.database import AsyncSessionLocal
        from app.services import realtime_voice_ai

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-callback-contamination@example.com",
                "password": "password123",
                "display_name": "CallbackContaminationテストオーナー",
            })
            assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
            owner_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "Callback Contaminationテスト店", "category": "レストラン",
                "address": "東京都渋谷区6-6-6",
            }, headers=owner_headers)
            assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
            shop_id = r.json()["shop_id"]

            async with AsyncSessionLocal() as db_session:
                shop = await db_session.get(Shop, shop_id)
                assert shop is not None

                captured = {}
                fake_client = _make_fake_client(captured)
                with patch("app.services.realtime_voice_ai._get_client", return_value=fake_client):
                    result = await realtime_voice_ai.create_realtime_session(db_session, shop)
                phase_contexts = result.get("realtime_phase_contexts") or {}

                callback_ctx = phase_contexts["callback"]
                reservation_ctx = phase_contexts["reservation"]
                legacy_ctx = phase_contexts["legacy_full"]

                callback_instructions = callback_ctx["instructions"]
                reservation_instructions = reservation_ctx["instructions"]
                legacy_instructions = legacy_ctx["instructions"]

                callback_tool_names = [t["name"] for t in callback_ctx["tools"]]
                reservation_tool_names = [t["name"] for t in reservation_ctx["tools"]]

                # ===== TEST 1: 専用CALLBACK phaseのtoolsはrequest_callbackのみ
                # （find_customer/check_availability/create_reservationを含まない） =====
                assert callback_tool_names == ["request_callback"], (
                    f"CALLBACK phaseのtoolsがrequest_callbackのみではありません: {callback_tool_names}"
                )
                assert realtime_voice_ai._CALLBACK_TOOL_NAMES == ["request_callback"], (
                    "_CALLBACK_TOOL_NAMES定数が変更されています（CALLBACK phaseの"
                    "Tool制限という既存の安全設計が変わった可能性）: "
                    f"{realtime_voice_ai._CALLBACK_TOOL_NAMES}"
                )
                print("TEST 1. CALLBACK phase toolsはrequest_callbackのみ（find_customer等を含まない）: OK")

                # ===== TEST 2: 専用CALLBACK phaseのinstructionsにRESERVATION専用の
                # キーワード（人数・何名・check_availability・create_reservation）が
                # 一切出現しない =====
                found_keywords = [kw for kw in _RESERVATION_ONLY_KEYWORDS if kw in callback_instructions]
                assert not found_keywords, (
                    f"CALLBACK phase instructionsにRESERVATION専用キーワードが混入しています: {found_keywords}"
                )
                print("TEST 2. CALLBACK phase instructionsにFast Reservation Flow/"
                      "Customer Memory Rulesセクション自体や、人数を積極的に尋ねる"
                      "指示、check_availabilityというTool名が一切出現しない: OK")

                # ===== TEST 3: legacy_fullに今回追加したexclusivity guardの文言が
                # 実際に含まれている（修正が実際にRealtimeへ送られるprompt文字列へ
                # 反映されていることの直接証拠） =====
                assert _FAST_RESERVATION_FLOW_GUARD_PHRASE in legacy_instructions, (
                    "legacy_full instructionsにFast Reservation Flowのexclusivity guardが"
                    "含まれていません（修正が反映されていない可能性）"
                )
                assert _CUSTOMER_MEMORY_RULES_GUARD_PHRASE in legacy_instructions, (
                    "legacy_full instructionsにCustomer Memory Rulesのexclusivity guardが"
                    "含まれていません（修正が反映されていない可能性）"
                )
                print("TEST 3. legacy_full instructionsに新設のexclusivity guard文言"
                      "（Fast Reservation Flow / Customer Memory Rules）が含まれている: OK")

                # ===== TEST 4: RESERVATION phaseは今回の修正による退行が無い
                # （guardはRESERVATION phaseでは常にtrueの無害な分岐であり、
                # 既存の人数案内・find_customer/check_availability/create_reservation
                # は引き続き全て存在する） =====
                assert "check_availability" in reservation_tool_names, (
                    f"RESERVATION toolsからcheck_availabilityが失われています: {reservation_tool_names}"
                )
                assert "create_reservation" in reservation_tool_names, (
                    f"RESERVATION toolsからcreate_reservationが失われています: {reservation_tool_names}"
                )
                assert "find_customer" in reservation_tool_names, (
                    f"RESERVATION toolsからfind_customerが失われています: {reservation_tool_names}"
                )
                assert "人数" in reservation_instructions, (
                    "RESERVATION instructionsから人数に関する案内が失われています"
                    "（exclusivity guard追加によりFast Reservation Flow本体の内容を"
                    "意図せず削除してしまった可能性）"
                )
                assert _FAST_RESERVATION_FLOW_GUARD_PHRASE in reservation_instructions, (
                    "RESERVATION instructionsにもexclusivity guardの文言自体は含まれる"
                    "はず（同一templateを共有しているため）が見つかりません"
                )
                print("TEST 4. RESERVATION phaseは既存の人数案内・"
                      "find_customer/check_availability/create_reservationを"
                      "全て維持している（退行なし）: OK")

                # ===== TEST 5: request_callback は CALLBACK phase で引き続き
                # 正常に宣言されている（機能を削除していないことの確認） =====
                assert "request_callback" in callback_tool_names, (
                    "CALLBACK phase toolsからrequest_callbackが失われています"
                    "（既存の必要情報・機能を削除してはいけないという指示に反する）"
                )
                print("TEST 5. CALLBACK phaseでrequest_callbackが引き続き正常に宣言されている: OK")

                # ===== TEST 6: CALLBACK最終案内文言が一字一句変更されていない =====
                assert _CALLBACK_FINAL_PHRASE in callback_instructions, (
                    "CALLBACK final phraseがCALLBACK phase instructionsから"
                    "見つかりません（絶対保護対象の文言が変更・削除された可能性）"
                )
                print("TEST 6. CALLBACK最終案内文言が一字一句変更されずに存在している: OK")

                # ===== TEST 7 (PHONE FIX FREEZE 回帰確認): 今回のCALLBACK修正が
                # PHONE関連のtemplateに一切触れていないことを、reservation/callback/
                # legacy_fullの全てで確認する =====
                for phase_label, instructions_text in (
                    ("reservation", reservation_instructions),
                    ("callback", callback_instructions),
                    ("legacy_full", legacy_instructions),
                ):
                    assert _PHONE_NEW_CONSISTENT_EXAMPLE in instructions_text, (
                        f"{phase_label} instructionsにPHONE修正後の一貫した例文"
                        f"（{_PHONE_NEW_CONSISTENT_EXAMPLE}）が見つかりません（PHONE FIX FREEZE違反の可能性）"
                    )
                    # 「ゼロ・キュウ・ゼロ」という文字列自体は、_PHONE_BLOCK_FORMAT_TEMPLATE
                    # 内で「このように間を空けて読んではいけない」という意図的な
                    # NG例として1回だけ登場する（Task Dで確定させた正しい設計。
                    # 直後に対比として正しい例「ゼロキュウゼロ」が続く）。Task Dの
                    # 不具合は、これとは別に_HUMAN_HANDOFF_TEMPLATE/_BOOKING_SAFETY_TEMPLATE
                    # 側の具体的な会話例で「ゼロ・キュウ・ゼロ」が「正しい例」として
                    # ハードコードされ、_PHONE_BLOCK_FORMAT_TEMPLATEの指示と矛盾して
                    # いたことだった。そのため「一切出現しない」ではなく「意図的な
                    # NG例1回分だけに限られている（＝他のセクションに再混入して
                    # いない）」ことを回帰確認する。
                    old_example_count = instructions_text.count(_PHONE_OLD_CONTRADICTING_EXAMPLE)
                    assert old_example_count == 1, (
                        f"{phase_label} instructionsの「{_PHONE_OLD_CONTRADICTING_EXAMPLE}」の"
                        f"出現回数が想定の1回（_PHONE_BLOCK_FORMAT_TEMPLATE内の意図的なNG例のみ）"
                        f"と一致しません（実際={old_example_count}）。0回ならNG例の説明ごと"
                        "消えた可能性、2回以上ならHuman Handoff/Booking Safety側に矛盾した"
                        "例文が再混入した可能性（PHONE FIX FREEZE違反）があります。"
                    )
                    assert _PHONE_DIGIT_COUNT_GUARD_PHRASE in instructions_text, (
                        f"{phase_label} instructionsにPHONE桁数整合性guardの文言が"
                        "見つかりません（PHONE FIX FREEZE違反の可能性）"
                    )
                print("TEST 7. PHONE FIX FREEZE: reservation/callback/legacy_full全てで"
                      "PHONE関連template（復唱テンポ・矛盾例文の不在・桁数guard）が"
                      "既存どおり維持されている（回帰なし）: OK")

                print("\n=== ALL smoke_test_callback_no_reservation_contamination.py CHECKS PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
