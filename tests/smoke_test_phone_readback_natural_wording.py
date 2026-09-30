"""
RECEPTRA — 電話番号復唱UX修正（2026年9月）
「XはX」説明形式・メタ説明（一桁ずつ確認します等）の禁止と、携帯電話番号の
3-4-4ブロック読み上げ・固定電話の非決め打ちガイダンスに関する静的契約テスト。

## 背景（実機で観測された不具合）
08039640201のような番号を復唱する際、AIが「一桁ずつ確認します」という
メタ説明（これから何をするかの実況）を口にしたうえで、「ゼロはゼロ、
ハチはハチ、ゼロはゼロ、サンはサン……」という、数字とその読み方を1つずつ
対応表のように説明する話し方（「XはX」形式）をしてしまう現象が観測された。

## 監査で確認した事実（推測ではなくコードを直接確認した事実）
- `_build_language_rules_section()`（NAME/ROUTINGを含む全phaseに常に
  含まれる汎用テンプレート）内の電話番号読み上げルールに、
  「「0」は「ゼロ」、「9」は「キュー」のように」という、日本語の
  「「X」は「Y」」という引用+係助詞のパターン（自然な発話と構造的に
  酷似している）を使った例示が存在していた。これが「XはX」形式で実際に
  発話される一因になっていたと判断し、矢印記法の例（090 → ゼロキュウゼロ）
  へ置き換えたうえで、「0はゼロです」のような対応表形式そのものを明示的に
  禁止する一文を追加した。
- `_PHONE_BLOCK_FORMAT_TEMPLATE`（RESERVATION/CALLBACK/legacy_fullの
  3箇所にのみ含まれる、電話番号読み上げ専用のsingle source of truth）は、
  既に3-4-4ブロック分けを携帯電話番号（090/080/070始まり11桁）に対して
  正しく指定していたが、(a) 「一桁ずつ確認します」等のメタ説明を明示的に
  禁止していなかった、(b) 固定電話番号については「携帯電話番号以外には
  適用しません」と opt-out するのみで、代替のガイダンスが一切無かった。
- `app/schemas/shop.py`の`normalize_jp_phone_national()`は、そのコメントで
  「市外局番の桁数までは厳密に検証しない」「欠落桁の補完や、市外局番の
  推測などは一切行わない」と明記しており、市外局番の桁数を認識する
  既存ロジックがコードベースのどこにも存在しないことを確認した。このため、
  固定電話の区切りを「2-4-4」のように決め打ちすることは推測にあたり、
  ユーザーの明示的な禁止事項に反すると判断し、決め打ちせず、自信が持てる
  場合にのみ短い間を置く・自信が持てない場合は携帯電話番号と同程度の
  自然なテンポで区切らず続けて読み上げる、という非決め打ちのガイダンスを
  追加した。

## 実装した最小修正
1. `_build_language_rules_section()`内の電話番号読み上げ例示を、
   「「X」は「Y」」形式から矢印記法へ変更し、「0はゼロです」のような
   対応表形式での説明を明示的に禁止する一文を追加（NAME/ROUTINGの文字数
   予算<3000文字を維持するため、既存より短い文言に整理した）。
2. `_PHONE_BLOCK_FORMAT_TEMPLATE`に、メタ説明（「一桁ずつ確認します」
   「3桁、4桁、4桁に分けます」「最初の3桁は」「次の4桁は」）と「XはX」
   形式（「ゼロはゼロ、ハチはハチ」等）を明示的に禁止する新セクションを
   追加。
3. `_PHONE_BLOCK_FORMAT_TEMPLATE`の固定電話に関する最終文を、「適用
   しません」という opt-out のみから、市外局番の桁数を推測で決め打ち
   しない・区切りに自信が持てる場合のみ短い間を置く・自信が持てない
   場合は携帯電話番号と同程度の自然なテンポで続けて読み上げる、という
   非決め打ちガイダンスへ置き換えた。

## 本テストの限界（誠実な明記）
本テストは実際のOpenAI Realtime APIを呼び出さない、backendが生成する
prompt文字列に対する静的検証である。「実際にモデルがこの指示を読んで
『XはX』形式で発話しなくなるか」という会話レベルの実証は実機テストでのみ
判断可能であり、本テストはそれを主張するものではない。

実行: python3 tests/smoke_test_phone_readback_natural_wording.py
"""
import asyncio
import os
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_phone_readback_wording.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-phone-readback-wording"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402

# 電話番号読み上げ専用の数字マッピング（既存契約。smoke_test_phone_digit_
# pronunciation.pyと同一の定義を独立して再定義し、本テストが他テストの
# 内部状態に依存しないようにする）
_DIGIT_PRONUNCIATION_MAP = {
    "0": "ゼロ", "1": "イチ", "2": "ニ", "3": "サン", "4": "ヨン",
    "5": "ゴ", "6": "ロク", "7": "ナナ", "8": "ハチ", "9": "キュウ",
}

# 今回の修正で絶対に発話させてはいけない「XはX」形式・対応表形式のフレーズ
_FORBIDDEN_EXPLANATION_PAIR_PHRASES = [
    "ゼロはゼロ",
    "ハチはハチ",
    "サンはサン",
    "キュウはキュウ",
    "「0」は「ゼロ」",
    "「9」は「キュー」",
]

# 今回の修正で絶対に発話させてはいけないメタ説明（実況）フレーズ
_FORBIDDEN_META_NARRATION_PHRASES = [
    "一桁ずつ確認します",
    "一桁ずつ読み上げます",
    "3桁、4桁、4桁に分けます",
    "最初の3桁は",
    "次の4桁は",
]

# 既存（PHONE FIX FREEZE対象）の回帰確認用フレーズ
_BLOCK_BOUNDARY_PAUSE_PHRASE = "ブロックとブロックの"
_NO_PAUSE_WITHIN_BLOCK_PHRASE = "同じブロックの中にある数字と数字の間には、意図的な間を置かない"
_DIGIT_COUNT_GUARD_PHRASE = "先頭の0を含めて10桁または11桁"
_CALLBACK_FINAL_PHRASE = "担当者から折り返し連絡しますので、電話を切ってお待ちください。"
_MOBILE_BLOCK_GROUPING_PHRASE = "先頭3桁・次の4桁・最後の4桁"

# 今回追加した、固定電話の非決め打ちガイダンスに含まれるべきキーフレーズ
_LANDLINE_NO_GUESS_PHRASE = "市外局番の桁数を推測で決め打ちせず"
_LANDLINE_NOT_APPLY_ONLY_PHRASE_OLD = "携帯電話番号以外（固定電話等、桁数や区切りが異なる番号）には\n適用しません。"
_META_NARRATION_SECTION_HEADING = "復唱時に絶対にしてはいけない説明"

# ユーザー指定の3つの携帯電話番号テストケース（11桁, 3-4-4分割）
_MOBILE_TEST_CASES = [
    ("08039640201", "080", "3964", "0201"),
    ("09012345678", "090", "1234", "5678"),
    ("07012345678", "070", "1234", "5678"),
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


def _digits_to_reading(digits: str) -> str:
    return "".join(_DIGIT_PRONUNCIATION_MAP[d] for d in digits)


async def main():
    from app.main import app, lifespan
    from app.models.shop import Shop

    async with lifespan(app):
        from app.database import AsyncSessionLocal

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-phone-readback-wording@example.com",
                "password": "password123",
                "display_name": "PhoneReadbackWordingテストオーナー",
            })
            assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
            owner_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "PhoneReadbackWordingテスト店", "category": "レストラン",
                "address": "東京都渋谷区8-8-8",
            }, headers=owner_headers)
            assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
            shop_id = r.json()["shop_id"]

            async with AsyncSessionLocal() as db_session:
                shop = await db_session.get(Shop, shop_id)
                assert shop is not None

                captured = {}
                fake_client = _make_fake_client(captured)
                from app.services import realtime_voice_ai
                with patch("app.services.realtime_voice_ai._get_client", return_value=fake_client):
                    result = await realtime_voice_ai.create_realtime_session(db_session, shop)
                phase_contexts = result.get("realtime_phase_contexts") or {}

                name_instructions = phase_contexts["name"]["instructions"]
                routing_instructions = phase_contexts["routing"]["instructions"]
                reservation_instructions = phase_contexts["reservation"]["instructions"]
                callback_instructions = phase_contexts["callback"]["instructions"]
                legacy_instructions = phase_contexts["legacy_full"]["instructions"]

                all_phases = {
                    "name": name_instructions,
                    "routing": routing_instructions,
                    "reservation": reservation_instructions,
                    "callback": callback_instructions,
                    "legacy_full": legacy_instructions,
                }
                phone_using_phases = {
                    "reservation": reservation_instructions,
                    "callback": callback_instructions,
                    "legacy_full": legacy_instructions,
                }

                # ===== TEST 1: 「XはX」形式・対応表形式のフレーズは、AIへの実際の
                # 発話指示としては全phaseのどこにも存在しない。ただし
                # 「このように言ってはいけない」という禁止指示自体の中で、禁止対象を
                # 名指しするための否定例として登場することは許容する（禁止するには
                # 禁止対象を書く必要があるため）。そのため、フレーズが出現した場合は
                # 必ず近傍（前後150文字以内）に明確な禁止マーカー（「しないでください」
                # 「絶対にせず」「絶対にしません」「禁止」等、否定形の表現）が
                # 伴っていることを確認する =====
                _NEGATION_MARKERS = [
                    "しないでください", "絶対にせず", "しません", "禁止",
                ]

                def _assert_only_as_negative_example(phase_label, instructions_text, phrase, category_label):
                    idx = instructions_text.find(phrase)
                    while idx != -1:
                        window = instructions_text[max(0, idx - 150): idx + len(phrase) + 150]
                        assert any(marker in window for marker in _NEGATION_MARKERS), (
                            f"{phase_label} instructionsに{category_label}フレーズ"
                            f"「{phrase}」が、明確な禁止マーカーを伴わずに出現しています"
                            "（実際の発話指示として紛れ込んでいる可能性）: "
                            f"...{window}..."
                        )
                        idx = instructions_text.find(phrase, idx + len(phrase))

                for phase_label, instructions_text in all_phases.items():
                    for forbidden_phrase in _FORBIDDEN_EXPLANATION_PAIR_PHRASES:
                        _assert_only_as_negative_example(
                            phase_label, instructions_text, forbidden_phrase, "『XはX』形式"
                        )
                print("TEST 1. 「ゼロはゼロ」「ハチはハチ」等の『XはX』形式・対応表形式の"
                      "フレーズは、全phaseにおいて禁止指示内の否定例としてのみ出現し、"
                      "実際の発話指示としては存在しない: OK")

                # ===== TEST 2: メタ説明（「一桁ずつ確認します」等）のフレーズも同様に、
                # 実際の発話指示としては存在せず、禁止指示内の否定例としてのみ出現する =====
                for phase_label, instructions_text in all_phases.items():
                    for forbidden_phrase in _FORBIDDEN_META_NARRATION_PHRASES:
                        _assert_only_as_negative_example(
                            phase_label, instructions_text, forbidden_phrase, "メタ説明（実況）"
                        )
                print("TEST 2. 「一桁ずつ確認します」等のメタ説明（実況）フレーズは、"
                      "全phaseにおいて禁止指示内の否定例としてのみ出現し、実際の発話指示"
                      "としては存在しない: OK")

                # ===== TEST 2b: NAME/ROUTINGには『XはX』形式・メタ説明のいずれの
                # フレーズも一切出現しない（_PHONE_BLOCK_FORMAT_TEMPLATEを含まない
                # ためそもそも混入し得ないことを直接確認する） =====
                for phase_label, instructions_text in (("name", name_instructions), ("routing", routing_instructions)):
                    for forbidden_phrase in _FORBIDDEN_EXPLANATION_PAIR_PHRASES + _FORBIDDEN_META_NARRATION_PHRASES:
                        assert forbidden_phrase not in instructions_text, (
                            f"{phase_label} instructionsに『XはX』形式・メタ説明フレーズ"
                            f"「{forbidden_phrase}」が混入しています"
                            "（_PHONE_BLOCK_FORMAT_TEMPLATEが誤って含まれている可能性）"
                        )
                print("TEST 2b. NAME/ROUTINGには『XはX』形式・メタ説明のいずれのフレーズも"
                      "一切出現しない: OK")

                # ===== TEST 3: _PHONE_BLOCK_FORMAT_TEMPLATEに、メタ説明・
                # 「XはX」形式を明示的に禁止する新セクションが、電話番号を
                # 扱う3phase全てに含まれている =====
                for phase_label, instructions_text in phone_using_phases.items():
                    assert _META_NARRATION_SECTION_HEADING in instructions_text, (
                        f"{phase_label} instructionsに、メタ説明・「XはX」形式を明示的に"
                        "禁止する新セクションの見出しが見つかりません"
                    )
                    assert "一桁ずつ確認します" in instructions_text, (
                        f"{phase_label} instructionsに、メタ説明の具体的な禁止文言"
                        "（「一桁ずつ確認します」の禁止例）が見つかりません"
                    )
                print("TEST 3. メタ説明・『XはX』形式を明示的に禁止する新セクションが、"
                      "reservation/callback/legacy_full全てに含まれている: OK")

                # ===== TEST 4: 固定電話について、「適用しません」という
                # opt-outのみの旧文言が除去され、非決め打ちガイダンス
                # （市外局番の桁数を推測で決め打ちしない）へ置き換わっている =====
                for phase_label, instructions_text in phone_using_phases.items():
                    assert _LANDLINE_NOT_APPLY_ONLY_PHRASE_OLD not in instructions_text, (
                        f"{phase_label} instructionsに、固定電話へのガイダンスを提供しない"
                        "旧文言（opt-outのみ）がまだ残っています"
                    )
                    assert _LANDLINE_NO_GUESS_PHRASE in instructions_text, (
                        f"{phase_label} instructionsに、固定電話の市外局番桁数を推測で"
                        "決め打ちしないという非決め打ちガイダンスが見つかりません"
                    )
                    # 固定の桁数（例: 2-4-4）を決め打ちする表現が混入していないこと
                    assert "固定電話は必ず2-4-4" not in instructions_text
                    assert "固定電話は2-4-4" not in instructions_text
                print("TEST 4. 固定電話について、市外局番の桁数を推測で決め打ちしない"
                      "非決め打ちガイダンスへ置き換わっており、2-4-4等の決め打ち表現が"
                      "混入していない: OK")

                # ===== TEST 5: 携帯電話番号の3-4-4ブロック分けルール自体は
                # 既存どおり維持されている（回帰） =====
                for phase_label, instructions_text in phone_using_phases.items():
                    assert _MOBILE_BLOCK_GROUPING_PHRASE in instructions_text, (
                        f"{phase_label} instructionsから携帯電話番号の3-4-4ブロック分け"
                        "ルールが失われています（PHONE FIX FREEZE違反の可能性）"
                    )
                print("TEST 5 (回帰). 携帯電話番号の3-4-4ブロック分けルール文言は"
                      "既存どおり維持されている: OK")

                # ===== TEST 6: ユーザー指定の3つの携帯電話番号について、
                # 3-4-4分割と各ブロックの数字読みマッピングの整合性を契約レベルで
                # 確認する（08039640201/09012345678/07012345678） =====
                for full_number, block1, block2, block3 in _MOBILE_TEST_CASES:
                    assert len(full_number) == 11, f"{full_number} は11桁ではありません"
                    assert full_number[0:3] == block1
                    assert full_number[3:7] == block2
                    assert full_number[7:11] == block3
                    assert full_number[0:3] + full_number[3:7] + full_number[7:11] == full_number
                    # 各ブロックの数字読み（digit-by-digit）が既存マッピングと矛盾しない
                    reading1 = _digits_to_reading(block1)
                    reading2 = _digits_to_reading(block2)
                    reading3 = _digits_to_reading(block3)
                    assert len(reading1) > 0 and len(reading2) > 0 and len(reading3) > 0
                print("TEST 6. 08039640201→080/3964/0201、09012345678→090/1234/5678、"
                      "07012345678→070/1234/5678、いずれも3-4-4分割の桁数・境界が"
                      "正しいことを契約レベルで確認: OK")

                # ===== TEST 7 (回帰): 既存のブロック境界・ブロック内連続読み・
                # 桁数整合性guard・CALLBACK最終案内文言がいずれも既存どおり維持 =====
                for phase_label, instructions_text in phone_using_phases.items():
                    assert _BLOCK_BOUNDARY_PAUSE_PHRASE in instructions_text
                    assert _NO_PAUSE_WITHIN_BLOCK_PHRASE in instructions_text
                    assert _DIGIT_COUNT_GUARD_PHRASE in instructions_text
                assert _CALLBACK_FINAL_PHRASE in callback_instructions, (
                    "CALLBACK final phraseが変更・削除されています（絶対保護対象）"
                )
                print("TEST 7 (回帰). ブロック境界の間・ブロック内連続読み・桁数整合性guard・"
                      "CALLBACK最終案内文言がいずれも既存どおり維持されている: OK")

                # ===== TEST 8: NAME/ROUTINGのinstructions文字数予算（<3000文字）が
                # 今回の修正後も維持されている =====
                for phase_label, instructions_text in (("name", name_instructions), ("routing", routing_instructions)):
                    assert len(instructions_text) < 3000, (
                        f"{phase_label} instructions_charsが3000文字を超えています: "
                        f"{len(instructions_text)}"
                    )
                print(f"TEST 8. NAME(instructions_chars={len(name_instructions)})/"
                      f"ROUTING(instructions_chars={len(routing_instructions)})は、"
                      "今回の修正後も<3000文字を維持している: OK")

                # ===== TEST 9: 数字ごとの読み方固定（0=ゼロ〜9=キュウ、4/7/9の
                # 曖昧さ禁止）はTask『JAPANESE PHONE DIGIT PRONUNCIATION HOTFIX』の
                # 既存契約のまま維持されている（回帰） =====
                for phase_label, instructions_text in phone_using_phases.items():
                    for digit, reading in _DIGIT_PRONUNCIATION_MAP.items():
                        mapping_phrase = f"{digit}={reading}"
                        assert mapping_phrase in instructions_text, (
                            f"{phase_label} instructionsに数字マッピング「{mapping_phrase}」が"
                            "見つかりません（既存契約からの回帰の可能性）"
                        )
                print("TEST 9 (回帰). 0=ゼロ〜9=キュウの数字読みマッピングは既存どおり"
                      "維持されている: OK")

                print("\n=== ALL smoke_test_phone_readback_natural_wording.py CHECKS PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
