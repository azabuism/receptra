"""
RECEPTRA — JAPANESE PHONE DIGIT PRONUNCIATION HOTFIX（2026年9月）
電話番号の数字読み固定（4=ヨン・7=ナナ・9=キュウ）の静的契約テスト。

## 背景（実機で観測された不具合）
Task E（JAPAN PHONE NUMBER LENGTH GUARD）・Task D（電話番号復唱テンポ
HOTFIX）が適用された状態での実機テストで、080-3964-4468という番号の
桁数・3-4-4グルーピング・ブロック境界の間の置き方はいずれも正常だったが、
「4」が「ヨン」ではなく「シ」と発音される箇所が観測された。

## 監査で確認した事実（推測ではなくコードを直接確認した事実）
既存のprompt materialを全数調査した結果、電話番号の桁ごとの読み方を
明示的に固定する指示（「4」「7」「9」等、複数の読み方が存在する数字を
一方に固定する指示）はどこにも存在しなかった。存在していたのは:
  - `_build_language_rules_section()`（`_CORE_RULES_TEMPLATE`の一部。
    NAME/ROUTINGを含む全phaseに常に含まれる）内の一般的な数字読み上げ
    ルールのみで、「0」「9」の例を1つずつ示すのみ（4・7・9の曖昧さには
    一切触れていない）。
  - `_PHONE_BLOCK_FORMAT_TEMPLATE`（RESERVATION/CALLBACK/legacy_fullの
    3箇所にのみ含まれる、電話番号読み上げ専用のsingle source of
    truth）も、3-4-4のブロック分け・ブロック境界の間の置き方・桁数
    整合性は規定していたが、桁ごとの発音（「4」を「ヨン」と読むか
    「シ」と読むか等）までは規定していなかった。
このため、モデルが「4」を「シ」、「7」を「シチ」、「9」を「ク」という
別の妥当な日本語の読み方を選択する余地が残っており、これが実機で観測
された発音揺れの原因と判断した。

## 実装した最小修正
`_PHONE_BLOCK_FORMAT_TEMPLATE`（電話番号読み上げの既存single source of
truth）に、電話番号を復唱する場面に限定した数字ごとの読み方固定
（0=ゼロ、1=イチ、2=ニ、3=サン、4=ヨン、5=ゴ、6=ロク、7=ナナ、8=ハチ、
9=キュウ。特に4/7/9はシ・シチ・クを明示的に禁止）を追記した。
`_build_language_rules_section()`（NAME/ROUTINGを含む全phaseに影響する
汎用テンプレート）には一切手を加えていない（電話番号以外の数字読み上げに
このルールをグローバル適用しないという要求、およびNAME/ROUTINGの
文字数予算（<3000文字）を保護するため）。

## 本テストの限界（誠実な明記）
本テストは実際のOpenAI Realtime APIを呼び出さない、backendが生成する
prompt文字列に対する静的検証である。「実際にモデルがこの指示を読んで
4をシと発音しなくなるか」という会話レベルの実証は実機テストでのみ
判断可能であり、本テストはそれを主張するものではない。

実行: python3 tests/smoke_test_phone_digit_pronunciation.py
"""
import asyncio
import os
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_phone_digit_pron.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-phone-digit-pron"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402

# 電話番号読み上げ専用の数字マッピング（新設。今回の契約の中核）
_DIGIT_PRONUNCIATION_MAP = {
    "0": "ゼロ", "1": "イチ", "2": "ニ", "3": "サン", "4": "ヨン",
    "5": "ゴ", "6": "ロク", "7": "ナナ", "8": "ハチ", "9": "キュウ",
}

# 禁止すべき紛らわしい読み方の明示的禁止フレーズ
_FORBIDDEN_READING_PHRASES = [
    "「シ」とは読まない",
    "「シチ」とは読まない",
    "「ク」とは読まない",
]

# 既存（PHONE FIX FREEZE対象）の回帰確認用フレーズ
_BLOCK_BOUNDARY_PAUSE_PHRASE = "ブロックとブロックの"
_NO_PAUSE_WITHIN_BLOCK_PHRASE = "同じブロックの中にある数字と数字の間には、意図的な間を置かない"
_DIGIT_COUNT_GUARD_PHRASE = "先頭の0を含めて10桁または11桁"
_CALLBACK_FINAL_PHRASE = "担当者から折り返し連絡しますので、電話を切ってお待ちください。"

# 実例: 080-3964-4468 の各ブロックの期待される読み（digit-by-digit）
_EXPECTED_080 = "ゼロハチゼロ"
_EXPECTED_3964 = "サンキュウロクヨン"
_EXPECTED_4468 = "ヨンヨンロクハチ"


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


def _digits_to_reading(digits: str) -> str:
    return "".join(_DIGIT_PRONUNCIATION_MAP[d] for d in digits)


async def main():
    from app.main import app, lifespan
    from app.models.shop import Shop

    async with lifespan(app):
        from app.database import AsyncSessionLocal
        from app.services import realtime_voice_ai

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-phone-digit-pron@example.com",
                "password": "password123",
                "display_name": "PhoneDigitPronテストオーナー",
            })
            assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
            owner_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "PhoneDigitPronテスト店", "category": "レストラン",
                "address": "東京都渋谷区7-7-7",
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

                name_instructions = phase_contexts["name"]["instructions"]
                routing_instructions = phase_contexts["routing"]["instructions"]
                reservation_instructions = phase_contexts["reservation"]["instructions"]
                callback_instructions = phase_contexts["callback"]["instructions"]
                legacy_instructions = phase_contexts["legacy_full"]["instructions"]

                phone_using_phases = {
                    "reservation": reservation_instructions,
                    "callback": callback_instructions,
                    "legacy_full": legacy_instructions,
                }

                # ===== TEST 1: 数字ごとの読み方マッピング(0〜9)が
                # _PHONE_BLOCK_FORMAT_TEMPLATE経由でreservation/callback/
                # legacy_fullの全てに含まれている =====
                for phase_label, instructions_text in phone_using_phases.items():
                    for digit, reading in _DIGIT_PRONUNCIATION_MAP.items():
                        mapping_phrase = f"{digit}={reading}"
                        assert mapping_phrase in instructions_text, (
                            f"{phase_label} instructionsに数字マッピング「{mapping_phrase}」が"
                            "見つかりません（digit pronunciation hotfixが反映されていない可能性）"
                        )
                print("TEST 1. reservation/callback/legacy_full全てに0〜9の数字読みマッピング"
                      "（0=ゼロ〜9=キュウ）が含まれている: OK")

                # ===== TEST 2: 4/7/9の紛らわしい読み方（シ/シチ/ク）の
                # 明示的禁止フレーズが含まれている =====
                for phase_label, instructions_text in phone_using_phases.items():
                    for forbidden_phrase in _FORBIDDEN_READING_PHRASES:
                        assert forbidden_phrase in instructions_text, (
                            f"{phase_label} instructionsに禁止フレーズ「{forbidden_phrase}」が"
                            "見つかりません"
                        )
                print("TEST 2. 4=シ・7=シチ・9=クという紛らわしい読み方の明示的禁止が"
                      "reservation/callback/legacy_full全てに含まれている: OK")

                # ===== TEST 3: このルールが電話番号読み上げの場面に限定される旨の
                # 明示的なスコープ限定文言が含まれている（時刻・人数・日付等への
                # グローバル適用を防ぐ） =====
                for phase_label, instructions_text in phone_using_phases.items():
                    assert "電話番号以外の数字を読み上げる場面には適用せず" in instructions_text, (
                        f"{phase_label} instructionsに、電話番号以外への非適用を明示する"
                        "文言が見つかりません（他の数字読み上げへ意図せずグローバル適用される"
                        "リスクがあります）"
                    )
                print("TEST 3. 数字読み固定ルールが電話番号読み上げの場面にのみ限定される旨が"
                      "明記されている（時刻・人数・日付等への誤適用防止）: OK")

                # ===== TEST 4: NAME/ROUTINGには今回の追記が一切混入していない
                # （_build_language_rules_section()は変更していないため当然だが、
                # 直接確認する） =====
                for phase_label, instructions_text in (("name", name_instructions), ("routing", routing_instructions)):
                    assert "4=ヨン" not in instructions_text, (
                        f"{phase_label} instructionsに電話番号専用の数字読みマッピングが"
                        "混入しています（NAME/ROUTINGの文字数予算を圧迫するリスク）"
                    )
                    assert len(instructions_text) < 3000, (
                        f"{phase_label} instructions_charsが3000文字を超えています: "
                        f"{len(instructions_text)}"
                    )
                print(f"TEST 4. NAME(instructions_chars={len(name_instructions)})/"
                      f"ROUTING(instructions_chars={len(routing_instructions)})に"
                      "digit pronunciation hotfixが混入しておらず、いずれも<3000文字を維持: OK")

                # ===== TEST 5: 実例 080-3964-4468 の各ブロックの期待される読みが、
                # 今回追加したマッピングをそのまま1桁ずつ適用した結果と一致する
                # （契約レベルでの実例確認。実際にモデルがこう発音するかは実機依存） =====
                assert _digits_to_reading("080") == _EXPECTED_080 == "ゼロハチゼロ"
                assert _digits_to_reading("3964") == _EXPECTED_3964 == "サンキュウロクヨン"
                assert _digits_to_reading("4468") == _EXPECTED_4468 == "ヨンヨンロクハチ"
                print("TEST 5. 080-3964-4468の期待される読み（ゼロハチゼロ／サンキュウロクヨン／"
                      "ヨンヨンロクハチ）が、新設した数字マッピングの定義と一致する: OK")

                # ===== TEST 6 (回帰): 3-4-4ブロック分け・ブロック内連続読み・
                # 桁数整合性guard・CALLBACK最終案内文言がいずれも既存どおり維持 =====
                for phase_label, instructions_text in phone_using_phases.items():
                    assert _BLOCK_BOUNDARY_PAUSE_PHRASE in instructions_text, (
                        f"{phase_label} instructionsから3-4-4ブロック境界の間の指示が"
                        "失われています（PHONE FIX FREEZE違反の可能性）"
                    )
                    assert _NO_PAUSE_WITHIN_BLOCK_PHRASE in instructions_text, (
                        f"{phase_label} instructionsからブロック内連続読みの指示が"
                        "失われています（PHONE FIX FREEZE違反の可能性）"
                    )
                    assert _DIGIT_COUNT_GUARD_PHRASE in instructions_text, (
                        f"{phase_label} instructionsから桁数整合性guardが失われています"
                        "（PHONE FIX FREEZE違反の可能性）"
                    )
                assert _CALLBACK_FINAL_PHRASE in callback_instructions, (
                    "CALLBACK final phraseが変更・削除されています（絶対保護対象）"
                )
                print("TEST 6 (回帰). 3-4-4ブロック分け・ブロック内連続読み・桁数整合性guard・"
                      "CALLBACK最終案内文言がいずれも既存どおり維持されている: OK")

                print("\n=== ALL smoke_test_phone_digit_pronunciation.py CHECKS PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
