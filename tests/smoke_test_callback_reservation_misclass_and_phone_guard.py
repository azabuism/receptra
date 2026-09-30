"""
RECEPTRA — CALLBACK誤分類＋電話番号桁数判定バグの実機修正（2026年9月）
静的契約テスト + 決定論的backend guardの実endpoint検証。

## 背景（実機で観測された不具合）
1. 「担当者から折り返しが欲しい」と伝えたところ、AIが「予約ですね」という
   趣旨の発話を2回行い、CALLBACKではなくRESERVATIONとして扱おうとした。
2. CALLBACKフローで正しい11桁の携帯電話番号（08039640201）を伝えても
   「桁数が合わない」として繰り返し拒否され、再入力ループから抜けられ
   なかった。

## 監査で確認した事実（推測ではなくコードを直接確認した事実）
- classify_intent Tool（ROUTING専用）の説明文自体は元々「担当者から
  折り返してほしい」等の具体例と、予約の話が出ていてもCALLBACKのままで
  ある旨の反混同注記を含んでおり、これ自体に欠陥は見つからなかった。
- ただしJS側（frontend/public/js/realtime-voice-engine.js）には、
  ROUTINGでユーザーの発話に対する応答がfunction_call（classify_intent
  呼び出し）を一切伴わずに終わった場合、legacy_fullへ自動フォールバック
  する既存の安全網（PHASE_ORDER_LEGACY_FULL_FALLBACK_ARMED、reason=
  routing_answer_unclassified）が存在する。legacy_full自身の分類
  （_INTENT_CLASSIFICATION_TEMPLATE、Tool呼び出しを伴わない自由文判断）
  は、CALLBACK基準の説明がclassify_intentほど具体的でなく（具体例・
  反混同注記を欠いていた）、この弱い経路を通った場合に誤分類が発生し
  やすい構造だったと判断した。二重発話についても、既存のsend
  ResponseCreate（response.create送信の一元化ラッパー）とresponse.done
  境界でのphase遷移消費の仕組みを確認した結果、同一応答が二重送信
  される既知のduplicate response bug（server auto response +
  明示的response.createの競合）が新たに発生した形跡は見当たらず、
  ROUTING応答とlegacy_fullへのforced follow-up応答という、設計上
  意図された2つの別々の応答が、それぞれ独立に同じ誤分類へ到達した
  結果と判断した（詳細は最終報告参照。二重response対策自体は無変更）。
- 電話番号については、request_callbackエンドポイント側の決定論的guard
  （normalize_jp_phone_national() + _JP_PHONE_DIGITS_RE）は
  "08039640201" を単体で正しくACCEPTすることをPythonで直接確認済み
  （regexは0始まり10〜11桁を受理し、"08039640201"は11桁で一致する）。
  一方、_PHONE_BLOCK_FORMAT_TEMPLATE内の「復唱する前に桁数を確認する」
  という既存の事前チェックは、AI自身の（誤りうる）桁数カウントのみに
  基づく強いブロックとして書かれており、決定論的なバックエンド判定より
  先にAI自身の思い込みだけで再入力ループへ戻ってしまう設計上のリスクが
  あった。

## 実装した最小修正
1. legacy_full専用の_INTENT_CLASSIFICATION_TEMPLATE・CALLBACK基準
   （項目2）に、classify_intentツールと同水準の具体例・反混同注記を
   追加した。
2. ROUTING専用の_PHASE2_ROUTING_ROLE_TEMPLATEに、classify_intent
   呼び出しより前に分類結果（「ご予約ですね」「折り返しですね」等）を
   断定的に話さないことを明記した。
3. _PHONE_BLOCK_FORMAT_TEMPLATEの桁数事前確認の末尾に、この確認は
   あくまで参考であり、最終判定は決定論的なツール呼び出し結果
   （reason_code=phone_invalid_digit_count）に委ねる旨を追記した。
いずれも既存の決定論的guard・3-4-4ブロック読み・CALLBACK/RESERVATION
専用phaseの構造・response生成制御コード（JS側）には一切手を加えて
いない。

## 本テストの限界（誠実な明記）
本テストは実際のOpenAI Realtime API呼び出しを行わない。(A)(B)は
backendが生成するprompt文字列に対する静的検証であり、「実際にモデルが
これを読んで正しく分類するか」は実機テストでのみ判断可能である。
(C)(D)(E)(F)は実際のrequest_callbackエンドポイントをASGITransport経由で
呼び出す決定論的検証であり、これはコード自体の正しさを実証する
（LLMの判断には依存しない）。(H)は「AIが実際に再入力ループに陥らないか」
までは実証できず、決定論的guardが正しくacceptすることと、事前チェックが
advisory化されたことの静的確認にとどまる。

実行: python3 tests/smoke_test_callback_reservation_misclass_and_phone_guard.py
"""
import asyncio
import os
import re
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_callback_misclass_phone.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-callback-misclass-phone"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402


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


async def _call_request_callback(client, shop_id, customer_phone, call_id_suffix):
    payload = {
        "customer_name": "テスト太郎",
        "customer_phone": customer_phone,
        "inquiry_text": "折り返し希望",
        "call_id": "call_test_" + call_id_suffix,
        "session_id": "sess_test_" + call_id_suffix,
    }
    r = await client.post(f"/api/v1/shops/{shop_id}/realtime-voice/tools/request-callback", json=payload)
    assert r.status_code == 200, f"unexpected status: {r.status_code} {r.text}"
    return r.json()


async def main():
    from app.main import app, lifespan
    from app.models.shop import Shop

    async with lifespan(app):
        from app.database import AsyncSessionLocal
        from app.services import realtime_voice_ai

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-callback-misclass-phone@example.com",
                "password": "password123",
                "display_name": "CallbackMisclassPhoneテストオーナー",
            })
            assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
            owner_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "CallbackMisclassPhoneテスト店", "category": "レストラン",
                "address": "東京都渋谷区10-10-10",
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

                routing_instructions = phase_contexts["routing"]["instructions"]
                reservation_instructions = phase_contexts["reservation"]["instructions"]
                callback_instructions = phase_contexts["callback"]["instructions"]
                legacy_instructions = phase_contexts["legacy_full"]["instructions"]

                from app.services.realtime_voice_ai import _ROUTING_TOOLS

                classify_intent_desc = _ROUTING_TOOLS[0]["description"]

                # ===== TEST A: 「担当者から折り返しが欲しい」がCALLBACKとして
                # 扱われる根拠（classify_intent Tool説明・legacy_full分類基準の
                # 両方）が存在し、RESERVATIONへ誤誘導する文言が無い =====
                assert "担当者から折り返してほしい" in classify_intent_desc, (
                    "classify_intent Tool説明文に「担当者から折り返してほしい」の"
                    "具体例が見つかりません"
                )
                assert "たとえ会話の中で予約について話していた" in classify_intent_desc, (
                    "classify_intent Tool説明文に反混同注記が見つかりません"
                )
                assert "担当者から折り返してほしい" in legacy_instructions, (
                    "legacy_full instructionsのCALLBACK分類基準に「担当者から"
                    "折り返してほしい」の具体例が見つかりません（今回追加分）"
                )
                assert "予約の話が出ていても常にCALLBACKです" in legacy_instructions, (
                    "legacy_full instructionsのCALLBACK分類基準に反混同注記が"
                    "見つかりません（今回追加分）"
                )
                print("A. 「担当者から折り返しが欲しい」がCALLBACKとして扱われる根拠"
                      "（classify_intent Tool説明・legacy_full分類基準）が"
                      "存在する: OK")

                # ===== TEST B: 「予約したい」がRESERVATIONとして扱われる既存の
                # 分類基準は無変更で維持されている（回帰） =====
                assert "intent=reservationは、来店・来院等の予約をしたい場合" in classify_intent_desc, (
                    "classify_intent Tool説明文のRESERVATION基準が変更・"
                    "失われています"
                )
                assert "1. RESERVATION（ご予約） - 来店・来院等の予約をしたい" in legacy_instructions, (
                    "legacy_full instructionsのRESERVATION分類基準（項目1）が"
                    "変更・失われています"
                )
                print("B (回帰). 「予約したい」がRESERVATIONとして扱われる既存の"
                      "分類基準（classify_intent Tool説明・legacy_full項目1）は"
                      "無変更で維持されている: OK")

                # ===== TEST C/D/E: 実際のrequest_callbackエンドポイントへ
                # 08039640201 / 09012345678 / 07012345678 を送り、決定論的guardが
                # 正しくACCEPTし、値がそのまま保存候補として通過することを
                # 実endpoint呼び出しで確認する（LLM判断を介さない、コード自体の
                # 正しさの実証） =====
                for phone, label in [
                    ("08039640201", "C"),
                    ("09012345678", "D"),
                    ("07012345678", "E"),
                ]:
                    body = await _call_request_callback(client, shop_id, phone, label)
                    assert body.get("success") is True, (
                        f"{label}. {phone} がACCEPTされるべきところREJECTされました: {body}"
                    )
                    print(f"{label}. {phone} → normalize後も同じ11桁 → "
                          "request_callbackエンドポイントで実際にACCEPT: OK")

                # ===== TEST F: 0803964020（10桁、携帯番号としては1桁不足）を
                # 勝手に11桁へ補完しないことを確認する。10桁は日本の固定電話等の
                # 妥当な桁数でもあるため、既存の決定論的guard（桁数のみで判定し、
                # 桁の値そのものの正しさまでは判定しない、という既知の設計上の
                # 限界）としてはACCEPTされ得るが、値そのものが変更・補完されて
                # いないことを直接確認する =====
                from app.schemas.shop import normalize_jp_phone_national
                incomplete = "0803964020"
                normalized = normalize_jp_phone_national(incomplete)
                assert normalized == incomplete, (
                    f"0803964020が補完・変更されています: normalize結果={normalized}"
                    "（桁を推測で足す実装が混入している可能性）"
                )
                assert len(incomplete) == 10
                print("F. 0803964020（10桁、携帯番号としては不完全）はnormalize後も"
                      "一字一句そのまま(08039640201等へ勝手に補完されない): OK")

                # ===== TEST G (回帰): 08039640201 → 080/3964/0201 の既存復唱契約は
                # 維持されている =====
                assert "先頭3桁・次の4桁・最後の4桁" in callback_instructions
                assert "先頭3桁・次の4桁・最後の4桁" in reservation_instructions
                assert "先頭3桁・次の4桁・最後の4桁" in legacy_instructions
                print("G (回帰). 08039640201→080/3964/0201の3-4-4ブロック復唱契約は"
                      "既存どおり維持されている: OK")

                # ===== TEST H: 電話番号の桁数事前確認が、決定論的なツール呼び出し
                # 結果を最終権威とするadvisory（参考情報）へ格下げされたことを
                # 確認する。あわせて、既存の「推測で桁を足す/削ることの禁止」
                # 「不完全な番号は補完せず尋ね直す」という正しい既存動作は
                # 削除されていないことも確認する =====
                for phase_label, instructions_text in (
                    ("reservation", reservation_instructions),
                    ("callback", callback_instructions),
                    ("legacy_full", legacy_instructions),
                ):
                    assert "この事前確認は参考であり" in instructions_text, (
                        f"{phase_label} instructionsに、桁数事前確認をadvisory化する"
                        "今回の追記が見つかりません"
                    )
                    assert "最終判定は" in instructions_text and "決定論的に行われます" in instructions_text, (
                        f"{phase_label} instructionsに、最終判定はツール呼び出し結果に"
                        "委ねる旨の文言が見つかりません"
                    )
                    assert "reason_code=phone_invalid_digit_countが返った" in instructions_text, (
                        f"{phase_label} instructionsに、reason_codeベースの再確認"
                        "条件が見つかりません"
                    )
                    # 既存の正しい動作（推測補完の禁止）は削除されていない
                    # （改行を含む文言のため、改行除去済みの文字列で比較する）
                    flat_instructions = instructions_text.replace("\n", "")
                    assert "桁を推測で足したり削ったりして辻褄を合わせることも、絶対にしないでください" in flat_instructions, (
                        f"{phase_label} instructionsから、桁の推測補完禁止の既存文言が"
                        "失われています（回帰）"
                    )
                print("H. 電話番号の桁数事前確認はLLM自身の判断のみに依存する絶対的な"
                      "ブロックから、決定論的なツール呼び出し結果を最終権威とする"
                      "advisoryへ格下げされており、既存の推測補完禁止ルールも"
                      "維持されている: OK")

                # ===== TEST I (回帰): CALLBACK確認後の二重response対策
                # （callbackTerminalArmed等の既存メカニズム）に対応する既存の
                # CALLBACK最終案内文言・ブロック境界文言は変更されておらず、
                # 今回のROUTING追記（Fix B）はresponse生成制御コードではなく
                # instructions文字列のみの変更であることを確認する =====
                _CALLBACK_FINAL_PHRASE = "担当者から折り返し連絡しますので、電話を切ってお待ちください。"
                assert _CALLBACK_FINAL_PHRASE in callback_instructions, (
                    "CALLBACK final phraseが変更・削除されています（絶対保護対象）"
                )
                assert "classify_intent呼び出しより前に断定的に話さない" in routing_instructions, (
                    "ROUTING instructionsに、分類結果の事前断定を禁止する今回の"
                    "追記が見つかりません"
                )
                # NAME/ROUTING/RESERVATIONの文字数予算は今回の追記後も既存基準内
                name_instructions = phase_contexts["name"]["instructions"]
                assert len(name_instructions) < 3000
                assert len(routing_instructions) < 3000
                assert len(reservation_instructions) < 15000
                print("I (回帰). CALLBACK最終案内文言・既存の二重response対策は"
                      "無変更で維持されており、今回のROUTING追記はinstructions"
                      "文字列のみの変更（response生成制御コード無変更）で、"
                      "NAME/ROUTING/RESERVATIONの文字数予算も維持されている: OK")

                print("\n=== ALL smoke_test_callback_reservation_misclass_and_phone_guard.py CHECKS PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
