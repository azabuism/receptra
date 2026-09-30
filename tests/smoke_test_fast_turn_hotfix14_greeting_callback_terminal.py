"""
RECEPTRA — FAST TURN HOTFIX 14（2026年9月）
「GREETING COMPLETION + CALLBACK TERMINAL TURN」バックエンド契約テスト。

ユーザー指示§19: バックエンドのinstructions変更（NAME phase / CALLBACK・
legacy_fullが共有する_HUMAN_HANDOFF_TEMPLATE）は、日本語の細かな言い回しの
変更に脆くならないよう、リテラル文字列一致ではなく「意味・振る舞いの契約」
として検証すること。本ファイルはbuild_realtime_phase_contexts()が実際に
組み立てるinstructions文字列（=モデルへ実際に送られる内容）を対象に、以下を
検証する（tests/test_fast_turn_hotfix14_greeting_callback_terminal.jsの
フロントエンド側契約テストと対をなす）。

検証項目:
  A. NAME instructionsは「名乗り+お名前質問を同じ発話で完結させる」
     （HOTFIX13の既存契約）ことを引き続き要求している。
  B. NAME instructionsは新たに「既に名乗りを話し終えている場合は繰り返さず、
     お名前の質問だけを続ける」（Zero-Wait Greeting follow-up対応）ことを
     要求している。
  C. 上記の役割説明部分（店舗名・AIスタッフ名を含む第一声本文とは独立した
     セクション）は、店舗名・AIスタッフ名（staff_name）を変えても
     一字一句同一である（店舗/スタッフ名をハードコードしていないことの
     直接的な証拠）。
  D. 実際に生成される第一声本文（greetingセクション）自体は、AIスタッフ設定
     （staff_name）ごとに正しく異なる（=A/Bの検証がテストの不備で
     「常にどのみち同じ」になっていないことの前提確認）。
  E. NAME instructions_charsは既存のPhase1回帰guard（<3000文字）を
     引き続き満たす。
  F. CALLBACK instructionsは「折り返し成功メッセージの直後にこの用件は
     完結し、追加の質問・確認・ご案内をせず会話を終える」ことを要求している
     （店舗名に依存しない一般的な指示であることも確認）。
  G. legacy_full instructionsにも同じF.の指示が含まれる（_HUMAN_HANDOFF_
     TEMPLATEがCALLBACK/legacy_fullの両方で共有されているため）。
  H. ROUTING/RESERVATION instructionsはHOTFIX14により変更されていない
     （文字数が本テスト実行時点のPhase2回帰guardの範囲内に収まっている）。
  I. CALLBACK/legacy_fullの新規追記は、ユーザー指示で明示的に禁止された
     「これから言うべきでない」追加要求（「ほかにご用件はありますか」等を
     新たに話せと指示するもの）を一切含まない
     （既存の「保証できない表現の禁止」セクションと同じ形式＝禁止例として
     引用符内に登場するだけで、実際に言うべき内容としては出現しない）。

実行: python3 tests/smoke_test_fast_turn_hotfix14_greeting_callback_terminal.py
"""

import asyncio
import os
import re
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_hotfix14.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-hotfix14"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402

FORBIDDEN_TO_SAY = [
    "少々お待ちください",
    "ほかにご用件はありますか",
    "予約について",
    "何かございましたら",
    "お名前を",
    "お電話番号を",
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


def _extract_new_closing_instruction(template: str) -> str:
    """_HUMAN_HANDOFF_TEMPLATE内の、成功案内後の「用件は完結し追加の質問・
    確認・ご案内をしない」旨を定めたクロージング指示のサブセクション（RECEPTRA
    CALLBACK最終案内文の固定タスクで5-3から5-1へ改番）だけを抜き出す
    （禁止フレーズ検査を、既存の他セクション（例えば「保証できない表現の
    禁止」セクションが「すぐ折り返します」等を引用しているのは今回の対象外）
    に誤爆させないため）。"""
    start = template.index("5-1. ")
    end = template.index("\n6. successがfalseの場合")
    return template[start:end]


async def main():
    from app.main import app, lifespan
    from app.models.shop import Shop

    async with lifespan(app):
        from app.database import AsyncSessionLocal
        from app.services import realtime_voice_ai

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-hotfix14@example.com",
                "password": "password123",
                "display_name": "HOTFIX14テストオーナー",
            })
            assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
            owner_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

            shop_ids = {}
            for key, shop_name in (
                ("default", "HOTFIX14テスト店（既定）"),
                ("staff_a", "HOTFIX14テスト店（天ぷらデモ）"),
                ("staff_b", "HOTFIX14テスト店（すし処あかり）"),
            ):
                r = await client.post("/api/v1/shops/register", json={
                    "name": shop_name, "category": "レストラン",
                    "address": "東京都渋谷区5-5-5",
                }, headers=owner_headers)
                assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
                shop_ids[key] = r.json()["shop_id"]

            # staff_a/staff_bにはそれぞれ異なるAIスタッフ名を設定し、
            # 「役割説明部分は店舗名・スタッフ名に依存しない」ことを検証する
            # 際の対照群とする。
            r = await client.put(
                f"/api/v1/shops/{shop_ids['staff_a']}/ai-staff-settings",
                json={"staff_name": "佐藤"},
                headers=owner_headers,
            )
            assert r.status_code in (200, 201), f"put ai-staff-settings(a) failed: {r.status_code} {r.text}"

            r = await client.put(
                f"/api/v1/shops/{shop_ids['staff_b']}/ai-staff-settings",
                json={"staff_name": "鈴木"},
                headers=owner_headers,
            )
            assert r.status_code in (200, 201), f"put ai-staff-settings(b) failed: {r.status_code} {r.text}"

            phase_contexts_by_shop = {}
            async with AsyncSessionLocal() as db_session:
                for key, shop_id in shop_ids.items():
                    shop = await db_session.get(Shop, shop_id)
                    assert shop is not None
                    captured = {}
                    fake_client = _make_fake_client(captured)
                    with patch("app.services.realtime_voice_ai._get_client", return_value=fake_client):
                        result = await realtime_voice_ai.create_realtime_session(db_session, shop)
                    phase_contexts_by_shop[key] = result.get("realtime_phase_contexts") or {}

            name_instructions = {k: v["name"]["instructions"] for k, v in phase_contexts_by_shop.items()}
            callback_instructions = {k: v["callback"]["instructions"] for k, v in phase_contexts_by_shop.items()}
            legacy_instructions = {k: v["legacy_full"]["instructions"] for k, v in phase_contexts_by_shop.items()}
            routing_instructions = {k: v["routing"]["instructions"] for k, v in phase_contexts_by_shop.items()}
            reservation_instructions = {k: v["reservation"]["instructions"] for k, v in phase_contexts_by_shop.items()}

            # ===== A. 同一発話での名乗り+お名前質問完結（HOTFIX13契約の回帰） =====
            for key, text in name_instructions.items():
                assert "同じ1回の発話" in text, f"[{key}] 同一発話での完結要求が見つかりません"
                assert "名乗りだけで発話を終えて" in text, f"[{key}] 名乗りのみでの終了禁止が見つかりません"
            print("A. NAME instructionsは引き続き「名乗り+お名前質問を同一発話で完結」を要求している: OK")

            # ===== B. 既に名乗り済みの場合は繰り返さない（HOTFIX14新規） =====
            for key, text in name_instructions.items():
                assert "すでに名乗り" in text, f"[{key}] 既出greeting対応の追記が見つかりません"
                assert "繰り返さず" in text, f"[{key}] 「繰り返さず」という指示が見つかりません"
            print("B. NAME instructionsは新たに「既に名乗り済みなら繰り返さずお名前だけ聞く」ことを要求している: OK")

            # ===== C. 役割説明部分は店舗名/スタッフ名に非依存（ハードコード無し） =====
            def _role_section(text: str) -> str:
                idx = text.index("# あなたの今の役割（Phase 1")
                return text[idx:]

            role_default = _role_section(name_instructions["default"])
            role_a = _role_section(name_instructions["staff_a"])
            role_b = _role_section(name_instructions["staff_b"])
            assert role_default == role_a == role_b, (
                "NAME instructionsの役割説明部分が店舗/スタッフ名によって変化しています"
                "（店舗名・スタッフ名をハードコードしていないという要件に反する可能性）"
            )
            assert "佐藤" not in role_a and "鈴木" not in role_b, (
                "役割説明部分にスタッフ名がハードコードされています"
            )
            print("C. NAME instructionsの役割説明部分は店舗名・AIスタッフ名を変えても一字一句同一（ハードコード無し）: OK")

            # ===== D. 第一声本文（greetingセクション）はスタッフ名ごとに正しく異なる =====
            # （C.の「同一」判定がテスト不備で無意味になっていないことの前提確認）
            def _greeting_section(text: str) -> str:
                role_idx = text.index("# あなたの今の役割（Phase 1")
                return text[:role_idx]

            greeting_a = _greeting_section(name_instructions["staff_a"])
            greeting_b = _greeting_section(name_instructions["staff_b"])
            assert greeting_a != greeting_b, "AIスタッフ名を変えても第一声本文が変化していません（テスト前提が崩れています）"
            assert "佐藤" in greeting_a or "佐藤" in name_instructions["staff_a"]
            assert "鈴木" in greeting_b or "鈴木" in name_instructions["staff_b"]
            print("D. 第一声本文（greetingセクション）はAIスタッフ設定ごとに正しく異なる（テスト前提の健全性確認）: OK")

            # ===== E. NAME instructions_chars回帰guard =====
            for key, text in name_instructions.items():
                assert len(text) < 3000, f"[{key}] NAME instructions_charsが3,000文字を超えています: {len(text)}"
            print(f"E. NAME instructions_chars（例: default={len(name_instructions['default'])}）< 3000: OK")

            # ===== F/G. CALLBACK・legacy_fullの終話指示（_HUMAN_HANDOFF_TEMPLATE共有） =====
            for label, instructions_map in (("CALLBACK", callback_instructions), ("legacy_full", legacy_instructions)):
                for key, text in instructions_map.items():
                    assert "用件は完結します" in text, f"[{label}/{key}] 用件完結の明示が見つかりません"
                    assert re.search(r"追加の質問・\s*確認・ご案内は行わず", text), (
                        f"[{label}/{key}] 追加の質問/確認/案内を行わない旨の指示が見つかりません"
                    )
            print("F/G. CALLBACK・legacy_full instructionsはいずれも「成功メッセージ後は完結し追加の質問をしない」ことを要求している: OK")

            # ===== H. ROUTING/RESERVATIONはHOTFIX14で変更されていない（回帰guard） =====
            for key in shop_ids:
                assert len(routing_instructions[key]) < 3000, f"[{key}] ROUTING instructions_charsが想定より大きい: {len(routing_instructions[key])}"
                assert len(reservation_instructions[key]) < 15000, f"[{key}] RESERVATION instructions_charsが想定より大きい: {len(reservation_instructions[key])}"
            print("H. ROUTING/RESERVATION instructionsはHOTFIX14による変更の影響を受けていない（回帰guard範囲内）: OK")

            # ===== I. 新規追記が「言うべきでない」追加要求を含んでいない =====
            # 注: 既存の「保証できない表現の禁止」セクション等と同じ形式で、
            # 禁止フレーズを「「フレーズ」等の...は行わず」という否定文の中で
            # 引用符付きの例として挙げること自体は許容する（それが本来の
            # 目的であり、実際にそのフレーズを話せという指示ではないため）。
            # 単純な部分文字列検査ではなく、「フレーズが引用符に包まれ、かつ
            # 直後に否定（行わず/しないでください等）が続く」形になっている
            # ことまで確認し、万一フレーズが肯定文脈（＝実際に話せという
            # 指示）で登場したら検知できるようにする。
            NEGATION_MARKERS = ("行わず", "しないでください", "絶対にしない", "禁止")
            for label, instructions_map in (("CALLBACK", callback_instructions), ("legacy_full", legacy_instructions)):
                for key, text in instructions_map.items():
                    idx = text.index("折り返し対応（Human Handoff）に関する絶対ルール")
                    handoff_section = text[idx:]
                    new_section = _extract_new_closing_instruction(handoff_section)
                    for forbidden in FORBIDDEN_TO_SAY:
                        pos = new_section.find(forbidden)
                        if pos == -1:
                            continue
                        window = new_section[max(0, pos - 5): pos + len(forbidden) + 40]
                        assert "「" in window and "」" in window, (
                            f"[{label}/{key}] 禁止フレーズ{forbidden!r}が引用符で囲まれた「例」の形になっていません: {window!r}"
                        )
                        assert any(marker in window for marker in NEGATION_MARKERS), (
                            f"[{label}/{key}] 禁止フレーズ{forbidden!r}の近くに否定表現が見つからず、"
                            f"実際に話せという指示になっている疑いがあります: {window!r}"
                        )
            print("I. CALLBACK/legacy_fullの新規追記(5-3)は、禁止フレーズを「言わないでください」という否定文の例としてのみ引用しており、実際に話せという指示にはなっていない: OK")

            print()
            print("全テストOK: FAST TURN HOTFIX 14 バックエンド契約テスト（NAME/CALLBACK・legacy_full instructions）")


if __name__ == "__main__":
    asyncio.run(main())
