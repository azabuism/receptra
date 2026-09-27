"""
Realtime Token Architecture（2026年9月）: session.truncation 契約テスト

背景: 実機で「cached_ratio 99.2%でもrate_limit_exceededが発生する」ことが
確認され、instructionsの圧縮（HOTFIX 8-10）だけでは対処できない「通話が
進むにつれて肥大化する会話履歴そのもの」への対策として、OpenAI公式Python
SDK (openai==1.109.1)のRealtimeSessionCreateRequest型定義に存在する
truncationフィールド（デフォルト"auto"）を、明示的に"retention_ratio"戦略へ
設定するようにした（app/config.pyのOPENAI_REALTIME_TRUNCATION_RETENTION_RATIO、
app/services/realtime_voice_ai.pyのcreate_realtime_session()）。

重要: 本テストは「truncation設定が正しく送信されること」のみを検証する。
これがOpenAI Realtime API側で実際にinput_tokens・rate limitを改善するか
どうかは実機のREALTIME_USAGE_BREAKDOWN/RATE_LIMIT_TOKEN_DELTA診断マーカーでの
実測でしか判断できず、本テストはその効果を主張するものではない。

検証項目:
A. デフォルト設定（0.8）で、実際に送信されるsession_configに
   truncation={"type": "retention_ratio", "retention_ratio": 0.8}が
   含まれること。
B. 環境変数を空文字にすると、truncationキー自体が送信されないこと
   （既存のreasoning.effortと同じ「空文字で無効化」パターン）。
C. 不正な値（数値に変換できない文字列）の場合、truncationキーを送信せず、
   例外も投げずにフォールバックすること（安全側に倒れる）。
D. 範囲外の値（0以下、1超）の場合もtruncationキーを送信しないこと。
E. instructions/toolsという既存の送信内容が今回の変更で失われていないこと
   （回帰確認）。
F. voice-preview用セッション（create_voice_preview_session）には
   truncation設定を一切追加していないこと（試聴セッションは予約会話を
   行わないため対象外、という既存方針の回帰確認）。

実行: python3 tests/smoke_test_realtime_truncation.py
"""

import asyncio
import os
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_realtime_truncation.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-realtime-truncation"
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


async def _create_session_with_ratio_setting(db_session, shop, ratio_value):
    """settingsのOPENAI_REALTIME_TRUNCATION_RETENTION_RATIOを一時的に差し替えてcreate_realtime_session()を直接呼ぶ。"""
    from app.services import realtime_voice_ai
    from app.config import get_settings

    settings = get_settings()  # lru_cache済みシングルトン。再代入ではなく属性を直接差し替える。
    captured = {}
    fake_client = _make_fake_client(captured)
    original = settings.OPENAI_REALTIME_TRUNCATION_RETENTION_RATIO
    settings.OPENAI_REALTIME_TRUNCATION_RETENTION_RATIO = ratio_value
    try:
        with patch("app.services.realtime_voice_ai._get_client", return_value=fake_client):
            await realtime_voice_ai.create_realtime_session(db_session, shop)
    finally:
        settings.OPENAI_REALTIME_TRUNCATION_RETENTION_RATIO = original
    return captured.get("session") or {}


async def main():
    from app.main import app, lifespan
    from app.models.shop import Shop

    async with lifespan(app):
        from app.database import AsyncSessionLocal
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-realtime-truncation@example.com",
                "password": "password123",
                "display_name": "Truncationテストオーナー",
            })
            assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
            owner_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "Truncationテスト店", "category": "レストラン",
                "address": "東京都渋谷区3-3-3",
            }, headers=owner_headers)
            assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
            shop_id = r.json()["shop_id"]

            async with AsyncSessionLocal() as db_session:
                shop = await db_session.get(Shop, shop_id)
                assert shop is not None

                # A. デフォルト0.8でtruncationが正しく送信される
                session_config = await _create_session_with_ratio_setting(db_session, shop, "0.8")
                assert session_config.get("truncation") == {"type": "retention_ratio", "retention_ratio": 0.8}, (
                    f"デフォルト設定でtruncationが期待通りに送信されていません: {session_config.get('truncation')}"
                )
                print("A. デフォルト設定(0.8)でtruncation={type: retention_ratio, retention_ratio: 0.8}が送信される: OK")

                # B. 空文字で無効化
                session_config = await _create_session_with_ratio_setting(db_session, shop, "")
                assert "truncation" not in session_config, (
                    f"空文字設定時にtruncationキーが送信されています: {session_config.get('truncation')}"
                )
                print("B. 環境変数を空文字にするとtruncationキー自体が送信されない: OK")

                # C. 不正な値（数値変換不可）は安全にフォールバック
                session_config = await _create_session_with_ratio_setting(db_session, shop, "not-a-number")
                assert "truncation" not in session_config, (
                    f"不正な値でもtruncationキーが送信されています: {session_config.get('truncation')}"
                )
                print("C. 不正な値（数値変換不可）は例外を投げずtruncationキーを送信しない: OK")

                # D. 範囲外の値は送信しない
                for bad_value in ["0", "-0.5", "1.5", "2"]:
                    session_config = await _create_session_with_ratio_setting(db_session, shop, bad_value)
                    assert "truncation" not in session_config, (
                        f"範囲外の値({bad_value})でもtruncationキーが送信されています: {session_config.get('truncation')}"
                    )
                print("D. 範囲外の値（0以下・1超）はtruncationキーを送信しない: OK")

                # E. 既存の送信内容（instructions/tools）が失われていない
                session_config = await _create_session_with_ratio_setting(db_session, shop, "0.8")
                assert "通話冒頭のご用件把握" in session_config.get("instructions", ""), (
                    "既存のIntent Classificationセクションが送信instructionsから失われています"
                )
                sent_tool_names = [t["name"] for t in session_config.get("tools", [])]
                assert sent_tool_names == [
                    "check_availability", "create_reservation", "get_shop_info", "find_customer",
                    "confirm_customer_identity", "get_customer_context", "set_conversation_language",
                    "request_callback",
                ], f"既存8Toolの送信内容が変化しています: {sent_tool_names}"
                print("E. instructions/toolsという既存の送信内容は今回の変更で失われていない: OK")

                # F. voice-preview用セッションにはtruncationを追加していない
                from app.services import realtime_voice_ai as rva_module
                captured_preview = {}
                fake_client_preview = _make_fake_client(captured_preview)
                with patch("app.services.realtime_voice_ai._get_client", return_value=fake_client_preview):
                    await rva_module.create_voice_preview_session("marin")
                preview_session_config = captured_preview.get("session") or {}
                assert "truncation" not in preview_session_config, (
                    f"voice-preview用セッションにtruncationが混入しています: {preview_session_config.get('truncation')}"
                )
                print("F. voice-preview用セッション(create_voice_preview_session)にはtruncationを追加していない: OK")

            print("\n=== ALL smoke_test_realtime_truncation.py CHECKS PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
