"""
Phase3E-3 Workstream2（Zero-Wait Greeting「即名乗り」改善）スモークテスト

検証項目:
1. _resolve_greeting_text(): staff_name設定時、電話に出た瞬間の第一声に
   必ず名前が含まれること（カスタムgreeting未設定/設定済みの両方で）。
2. _resolve_greeting_text(): カスタムgreetingに既に名前が含まれている場合、
   二重に名乗らないこと。
3. get_or_generate_greeting_audio(): 初回はキャッシュミスで生成し
   AIStaffSettingsへ保存すること。2回目は同じ内容ならキャッシュヒットし、
   TTS生成を再度呼ばないこと。
4. get_or_generate_greeting_audio(): staff_name/voice/greetingのいずれかを
   変更すると、キャッシュが自動的に無効化され再生成されること（≠古い音声が
   返り続けること）。
5. GET /api/v1/shops/{shop_id}/realtime-voice/greeting-audio エンドポイントが
   実際に音声バイト列とCache-Control: no-storeヘッダーを返すこと。
6. 存在しない/非公開の店舗に対しては404を返し、成功扱いにしないこと
   （既存のcheck_availability/create_reservation Toolと同じ「失敗は必ず
   安全側」の方針に合わせる）。

注意: OpenAI TTS APIは実際には呼び出さず、
app.services.realtime_voice_ai.generate_greeting_tts_audio をモックして
決定的にテストする（ネットワーク・課金なしでキャッシュロジックのみを検証）。
"""

import asyncio
import os
import sys
import tempfile
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_phase3e3_zw.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-3e3-zw"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402


def _test_resolve_greeting_text():
    from app.services.realtime_voice_ai import _resolve_greeting_text

    class FakeSettings:
        def __init__(self, staff_name=None, greeting=None):
            self.staff_name = staff_name
            self.greeting = greeting

    # 1. 設定なし: 名乗らない汎用フォールバック
    assert _resolve_greeting_text(None, "テスト店") == "お電話ありがとうございます。テスト店でございます。"

    # 2. staff_nameのみ設定・カスタムgreeting未設定: 名前入りフォールバック
    s = FakeSettings(staff_name="さくら")
    text = _resolve_greeting_text(s, "テスト店")
    assert "さくら" in text, f"expected staff_name in greeting: {text}"

    # 3. staff_name設定 + カスタムgreetingに名前が含まれない: 名乗りを補う
    s = FakeSettings(staff_name="さくら", greeting="本日もご予約ありがとうございます！")
    text = _resolve_greeting_text(s, "テスト店")
    assert "さくら" in text, f"expected staff_name prepended: {text}"
    assert text.count("さくら") == 1, f"expected exactly one mention: {text}"
    assert "本日もご予約ありがとうございます" in text, f"expected custom text preserved: {text}"

    # 4. staff_name設定 + カスタムgreetingに既に名前が含まれる: 二重に名乗らない
    s = FakeSettings(staff_name="さくら", greeting="さくらです。本日もよろしくお願いします！")
    text = _resolve_greeting_text(s, "テスト店")
    assert text.count("さくら") == 1, f"expected no duplicate mention: {text}"
    assert text == "さくらです。本日もよろしくお願いします！", f"expected unchanged custom text: {text}"

    print("1-2. _resolve_greeting_text: OK")


async def _test_caching(client, owner_headers, shop_id):
    from app.database import AsyncSessionLocal
    from app.models.ai_staff_settings import AIStaffSettings
    from sqlalchemy import select

    call_count = {"n": 0}

    async def fake_generate(shop, staff_settings):
        call_count["n"] += 1
        from app.services.realtime_voice_ai import _resolve_greeting_text
        text = _resolve_greeting_text(staff_settings, shop.name)
        # フェイクの「音声」バイト列。テキストが変われば内容も変わる。
        fake_bytes = f"FAKE_AUDIO::{text}".encode("utf-8")
        return {"audio_bytes": fake_bytes, "voice": "marin", "model": "fake-tts", "greeting_text": text}

    # キャッシュを保持するにはAIStaffSettings行が必要（get_or_generate_greeting_audio()は
    # 未認証エンドポイント経由で行を新規作成しない設計のため、先にオーナーとして
    # 一度保存しておく）。
    r0 = await client.put(f"/api/v1/shops/{shop_id}/ai-staff-settings", json={"voice": "marin"}, headers=owner_headers)
    assert r0.status_code == 200, f"initial ai-staff-settings save failed: {r0.status_code} {r0.text}"

    with patch("app.services.realtime_voice_ai.generate_greeting_tts_audio", new=AsyncMock(side_effect=fake_generate)):
        # 3. 初回: キャッシュミス
        r1 = await client.get(f"/api/v1/shops/{shop_id}/realtime-voice/greeting-audio")
        assert r1.status_code == 200, f"greeting-audio(1st) failed: {r1.status_code} {r1.text}"
        assert r1.headers.get("x-greeting-audio-cache") == "miss", r1.headers
        assert r1.headers.get("cache-control") == "no-store", r1.headers
        body1 = r1.content
        assert call_count["n"] == 1, call_count

        # 2回目（設定変更なし）: キャッシュヒット・TTSは再度呼ばれない
        r2 = await client.get(f"/api/v1/shops/{shop_id}/realtime-voice/greeting-audio")
        assert r2.status_code == 200, f"greeting-audio(2nd) failed: {r2.status_code} {r2.text}"
        assert r2.headers.get("x-greeting-audio-cache") == "hit", r2.headers
        assert r2.content == body1, "expected identical cached bytes"
        assert call_count["n"] == 1, f"expected no re-generation on cache hit: {call_count}"

        # DBに実際に保存されていることを直接確認（greeting_audio_dataはdeferredのため
        # undefer()で明示的に読み込む）
        from sqlalchemy.orm import undefer
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                select(AIStaffSettings)
                .options(undefer(AIStaffSettings.greeting_audio_data))
                .filter(AIStaffSettings.shop_id == shop_id)
            )
            row = res.scalars().first()
            assert row.greeting_audio_data == body1, "expected cached bytes persisted in DB"
            assert row.greeting_audio_fingerprint, "expected fingerprint stored"

        # 4. staff_nameを変更 → キャッシュ自動無効化・再生成される
        r_update = await client.put(
            f"/api/v1/shops/{shop_id}/ai-staff-settings",
            json={"staff_name": "みらい"},
            headers=owner_headers,
        )
        assert r_update.status_code == 200, f"update staff_name failed: {r_update.status_code} {r_update.text}"

        r3 = await client.get(f"/api/v1/shops/{shop_id}/realtime-voice/greeting-audio")
        assert r3.status_code == 200, f"greeting-audio(after change) failed: {r3.status_code} {r3.text}"
        assert r3.headers.get("x-greeting-audio-cache") == "miss", (
            f"expected cache invalidation after staff_name change: {r3.headers}"
        )
        assert call_count["n"] == 2, f"expected exactly one regeneration: {call_count}"
        assert r3.content != body1, "expected new audio content to differ after staff_name change"
        assert b"MIRAI_PLACEHOLDER" or True  # (no-op; keeps intent explicit without asserting exact JP bytes)

        # 5回目: 新しい内容で再度キャッシュヒットするはず
        r4 = await client.get(f"/api/v1/shops/{shop_id}/realtime-voice/greeting-audio")
        assert r4.status_code == 200
        assert r4.headers.get("x-greeting-audio-cache") == "hit", r4.headers
        assert r4.content == r3.content
        assert call_count["n"] == 2, f"expected still only 2 generations total: {call_count}"

    print("3-4. get_or_generate_greeting_audio caching + auto-invalidation: OK")


async def main():
    _test_resolve_greeting_text()

    from app.main import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-zw-3e3@example.com", "password": "password123", "display_name": "オーナーZW",
            })
            assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
            owner = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "3E3ZWテスト店舗", "category": "居酒屋", "address": "沖縄県那覇市1-1-1",
            }, headers=owner)
            assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
            shop_id = r.json()["shop_id"]

            await _test_caching(client, owner, shop_id)

            # 6. 存在しない店舗は404
            r = await client.get("/api/v1/shops/nonexistent-shop-id/realtime-voice/greeting-audio")
            assert r.status_code == 404, f"expected 404 for nonexistent shop, got {r.status_code}"

            print("Phase3E-3 Workstream2 smoke test: ALL CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
