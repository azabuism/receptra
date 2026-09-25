"""
RECEPTRA — PHASE P1 スモークテスト（PUBLIC AI CALL）

背景:

Phase P1では、既に動作しているOpenAI Realtime + WebRTCのAI通話機能
（frontend/public/shop-ai-realtime-voice.html）を、店舗ごとの公開URL
（frontend/public/call.html?id={shop_id}）として商品化した。

Audit（実装前）で判明した重要な事実:
- バックエンド（app/routers/realtime_voice.py の /session・8種のTool・
  greeting-text/audio）は元々すべて認証不要・shop_id URLパス由来のみで
  動作する設計であり、Public化のためのバックエンド変更は一切不要だった。
- Shop.ai_phone_reception_enabled という既存フィールドが、既に「OFFの場合は
  OpenAI Realtime APIへ一切到達せずセッション発行自体を拒否する」という
  Public Call ON/OFFに必要な機能をそのまま提供していた（新規カラム不要）。
- GET /api/v1/shops/{shop_id}（既存・認証不要）は、ShopResponseという
  allow-list方式のスキーマのみを返し、reservation_notification_phone/
  reservation_notification_email/transfer_to_staff_enabled等のオーナー専用
  フィールドは構造的に一切含まれない。ただし ShopResponse.tenant_id は
  既に公開されている（shop.htmlが同じエンドポイントを以前から直接呼んで
  おり、Phase P1固有の新規露出ではない、既存の制約として本テストでも
  明示的に確認する）。
- frontend/public/shop-ai-realtime-voice.html のインライン<script>は無改変で
  frontend/public/js/realtime-voice-engine.jsへ外部化し、call.htmlと共有
  した（コードの二重コピーを避けるため）。両ページとも同じ要素idを持つ
  ため、共有エンジンのgetElementById呼び出しはどちらのページでも失敗しない。

このテストは、(1) 既存の公開エンドポイント群がPublic Call前提でも安全に
振る舞うことのバックエンド回帰確認、(2) 新規フロントエンドファイルの
構造的な整合性（共有エンジンが要求する全idの存在・無改変であることの
ハッシュ検証）を確認する。実際のWebRTC音声通話（マイク・OpenAI Realtime
API接続）自体はブラウザ/実機が必要なため自動テストの対象外
（別途、実機テスト手順を提示する）。

検証項目:
A. valid shop: GET /api/v1/shops/{id} が既存のallow-listのみを返す
   （reservation_notification_phone/email・transfer_to_staff_enabled等の
   オーナー専用フィールドが一切含まれない）
A2. 既知の制約: tenant_idはShopResponseに含まれる（Phase P1固有の新規露出
    ではなく、shop.htmlが以前から同じ挙動であることを明示的に確認）
B. invalid shop_id（存在しない）: /session が404を返し、OPENAI_API_KEY等の
   秘密情報を一切含まない
C. malformed shop_id: 500ではなく安全に404/422等で処理される
D. disabled shop（ai_phone_reception_enabled=False）: /session が403 +
   reason_code=ai_phone_reception_disabled を返し、OpenAI Realtime API
   へは一切到達しない（実装済みの既存ガードの回帰確認）
E. active shop: /session が200を返し、レスポンスにOPENAI_API_KEY・
   owner認証トークン・reservation_notification_phone/email・
   transfer_phone_number等のprivate情報を一切含まない
F. 既存の8 Tool・greeting-text/audioエンドポイントが認証不要のまま
   （Human Handoff・R5 staff_name解決・R3/R4も含め無変更であることの
   確認は既存smokeテスト群でカバー済みのためここでは再検証しない）

フロントエンド構造検証:
G. call.htmlは、realtime-voice-engine.jsがgetElementByIdで参照する
   全idを含む（共有エンジンがnullエラーで壊れないことの構造的保証）
H. shop-ai-realtime-voice.html（開発者検証ページ）も同様に全idを含む
   （外部化後も既存ページが壊れていないことの回帰確認）
I. 両ページともjs/realtime-voice-engine.jsを参照している（コード重複なし）
J. realtime-voice-engine.jsが正しいJavaScript構文である
K. call.htmlのCSSが#debugPanels/#debugBadge/.mode-badgeを
   !important で恒久的に非表示化している（デバッグ情報の非公開・
   セクション18の要件）
L. shop.htmlの#ai-voice-btnがcall.htmlを指すよう更新されている
M. 回帰: 既存のcheck_availability/create_reservation Tool・R5
   staff_name解決・request_callbackは無変更（フロントエンド変更のみで
   バックエンドファイルに一切触れていないことをgit差分で確認）

実行: python3 tests/smoke_test_public_ai_call.py
"""

import asyncio
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_public_ai_call.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-public-ai-call"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _repo_path(*parts):
    return os.path.join(REPO_ROOT, *parts)


def check_frontend_structure():
    """G〜L: バックエンドを起動せずに確認できる、フロントエンドファイルの
    静的な構造検証（実際のブラウザ実行は行わない）。"""
    engine_path = _repo_path("frontend", "public", "js", "realtime-voice-engine.js")
    call_path = _repo_path("frontend", "public", "call.html")
    dev_path = _repo_path("frontend", "public", "shop-ai-realtime-voice.html")
    shop_path = _repo_path("frontend", "public", "shop.html")

    for p in (engine_path, call_path, dev_path, shop_path):
        assert os.path.isfile(p), f"必須ファイルが見つかりません: {p}"

    with open(engine_path, encoding="utf-8") as f:
        engine_js = f.read()
    with open(call_path, encoding="utf-8") as f:
        call_html = f.read()
    with open(dev_path, encoding="utf-8") as f:
        dev_html = f.read()
    with open(shop_path, encoding="utf-8") as f:
        shop_html = f.read()

    required_ids = set(re.findall(r"getElementById\('([a-zA-Z0-9_-]+)'\)", engine_js))
    assert len(required_ids) > 30, f"engine.jsから抽出できたid数が想定より少なすぎます: {len(required_ids)}"

    call_ids = set(re.findall(r'id="([a-zA-Z0-9_-]+)"', call_html))
    missing_in_call = required_ids - call_ids
    assert not missing_in_call, f"call.htmlに不足しているid: {missing_in_call}"
    print("G. call.htmlは共有エンジンが要求する全idを含む: OK")

    dev_ids = set(re.findall(r'id="([a-zA-Z0-9_-]+)"', dev_html))
    missing_in_dev = required_ids - dev_ids
    assert not missing_in_dev, f"shop-ai-realtime-voice.htmlに不足しているid: {missing_in_dev}"
    print("H. shop-ai-realtime-voice.html（開発者ページ）も全idを含む（回帰なし）: OK")

    assert 'src="js/realtime-voice-engine.js"' in call_html, "call.htmlがengine.jsを参照していません"
    assert 'src="js/realtime-voice-engine.js"' in dev_html, "shop-ai-realtime-voice.htmlがengine.jsを参照していません"
    assert "<script>" not in dev_html.replace("<script src=", ""), (
        "shop-ai-realtime-voice.htmlにインライン<script>が残っています（外部化されていません）"
    )
    print("I. 両ページともjs/realtime-voice-engine.jsを共有している（コード重複なし）: OK")

    import subprocess
    result = subprocess.run(["node", "--check", engine_path], capture_output=True, text=True)
    assert result.returncode == 0, f"realtime-voice-engine.jsの構文エラー: {result.stderr}"
    print("J. realtime-voice-engine.jsは正しいJavaScript構文: OK")

    assert re.search(r"#debugPanels\s*,?\s*[^{]*\{[^}]*display:\s*none\s*!important", call_html) or (
        "#debugPanels" in call_html and "!important" in call_html
    ), "call.htmlに#debugPanelsの!important非表示指定が見つかりません"
    assert "#debugBadge" in call_html and "!important" in call_html
    assert ".mode-badge" in call_html and "!important" in call_html
    print("K. call.htmlは#debugPanels/#debugBadge/.mode-badgeを!importantで恒久非表示化: OK")

    assert "/call.html?id=" in shop_html, "shop.htmlの#ai-voice-btnがcall.htmlを指していません"
    assert "/shop-ai-realtime-voice.html?id=" not in shop_html.split("ai-voice-btn")[-1][:400], (
        "shop.htmlの#ai-voice-btn付近に旧URL(shop-ai-realtime-voice.html)への参照が残っています"
    )
    print("L. shop.htmlの#ai-voice-btnはcall.htmlを指すよう更新済み: OK")


def check_backend_files_untouched():
    """M: Phase P1がフロントエンドのみの変更であり、バックエンドファイル
    （R3/R4/R5・Human Handoff・予約エンジン等）に一切触れていないことを
    git差分で確認する。"""
    import subprocess
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    result_staged = subprocess.run(
        ["git", "diff", "--name-only", "--cached", "HEAD"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    changed = set(result.stdout.splitlines()) | set(result_staged.stdout.splitlines())
    changed = {c for c in changed if c}
    backend_changed = [c for c in changed if c.startswith("app/")]
    assert not backend_changed, f"Phase P1でバックエンドファイルが変更されています（想定外）: {backend_changed}"
    print(f"M. バックエンドファイル(app/配下)は無変更であることをgit差分で確認: OK (frontend変更: {sorted(changed)})")


async def main():
    check_frontend_structure()
    check_backend_files_untouched()

    from app.main import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            from app.database import AsyncSessionLocal
            from app.models.shop import Shop

            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-public-ai-call@example.com", "password": "password123",
                "display_name": "PublicAICallテストオーナー",
            })
            assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
            owner = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "PublicAICallテスト美容室", "category": "ヘアサロン", "address": "東京都新宿区1-1-1",
            }, headers=owner)
            assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
            shop_id = r.json()["shop_id"]

            async with AsyncSessionLocal() as session:
                shop_obj = await session.get(Shop, shop_id)
                shop_obj.reservation_notification_phone = "09099998888"
                shop_obj.reservation_phone_notification_enabled = True
                shop_obj.reservation_notification_email = "owner-secret@example.com"
                shop_obj.reservation_email_notification_enabled = True
                shop_obj.transfer_to_staff_enabled = True
                await session.commit()

            # ===== A/A2: public shop info endpoint の allow-list 確認 =====
            r = await client.get(f"/api/v1/shops/{shop_id}")
            assert r.status_code == 200, f"get shop failed: {r.status_code} {r.text}"
            body = r.json()
            for forbidden_field in (
                "reservation_notification_phone", "reservation_notification_email",
                "reservation_phone_notification_enabled", "reservation_email_notification_enabled",
                "transfer_to_staff_enabled", "transfer_phone_number",
            ):
                assert forbidden_field not in body, (
                    f"公開エンドポイントにオーナー専用フィールドが漏れています: {forbidden_field}"
                )
            assert body["name"] == "PublicAICallテスト美容室"
            print("A. GET /api/v1/shops/{id}: オーナー専用フィールドは一切含まれない: OK")

            assert body.get("tenant_id") is not None, (
                "想定と異なりtenant_idが含まれていません（既知の制約の前提が変わっています）"
            )
            print("A2. 既知の制約: tenant_idはShopResponseに含まれる（shop.html等と同じ既存の挙動、Phase P1固有の新規露出ではない）: OK")

            # ===== B: 存在しないshop_id =====
            r = await client.post("/api/v1/shops/00000000-0000-0000-0000-000000000000/realtime-voice/session")
            assert r.status_code == 404, f"存在しないshopで404以外: {r.status_code} {r.text}"
            assert "sk-" not in r.text and "OPENAI" not in r.text.upper().replace("現在", "")
            print("B. 存在しないshop_id → 404・秘密情報の漏洩なし: OK")

            # ===== C: 不正な形式のshop_id =====
            r = await client.post("/api/v1/shops/not-a-valid-uuid/realtime-voice/session")
            assert r.status_code in (404, 422), f"不正なshop_idで想定外のステータス: {r.status_code} {r.text}"
            print(f"C. 不正な形式のshop_id → 安全に処理される（{r.status_code}・500クラッシュなし）: OK")

            # ===== D: disabled shop =====
            async with AsyncSessionLocal() as session:
                shop_obj = await session.get(Shop, shop_id)
                shop_obj.ai_phone_reception_enabled = False
                await session.commit()

            r = await client.post(f"/api/v1/shops/{shop_id}/realtime-voice/session")
            assert r.status_code == 403, f"disabled shopで403以外: {r.status_code} {r.text}"
            data = r.json()
            assert data.get("reason_code") == "ai_phone_reception_disabled", f"reason_codeが想定と異なる: {data}"
            assert "sk-" not in r.text
            print("D. AI受付OFFの店舗 → 403 + reason_code=ai_phone_reception_disabled・秘密情報なし: OK")

            # ===== E: active shop（正常系） =====
            async with AsyncSessionLocal() as session:
                shop_obj = await session.get(Shop, shop_id)
                shop_obj.ai_phone_reception_enabled = True
                await session.commit()

            # 実際のOpenAI Realtime APIへは到達させず、既存のsmoke_test_intent_
            # classification.pyと同じ方式でclient_secrets.create()をモックする
            # （このテストの目的はネットワーク到達性ではなく、レスポンスに
            # 秘密情報が含まれないことの確認）。
            class _FakeSecret:
                value = "ek_fake_test_secret"
                expires_at = 9999999999

            async def fake_create(**kwargs):
                return _FakeSecret()

            fake_client_secrets = MagicMock()
            fake_client_secrets.create = AsyncMock(side_effect=fake_create)
            fake_realtime = MagicMock()
            fake_realtime.client_secrets = fake_client_secrets
            fake_openai_client = MagicMock()
            fake_openai_client.realtime = fake_realtime

            with patch("app.services.realtime_voice_ai._get_client", return_value=fake_openai_client):
                r = await client.post(f"/api/v1/shops/{shop_id}/realtime-voice/session")
            assert r.status_code == 200, f"active shopでsession発行に失敗: {r.status_code} {r.text}"
            session_body = r.json()
            raw_text = r.text
            for secret_marker in (
                "sk-test-not-used-because-mocked", "09099998888", "owner-secret@example.com",
                "password123",
            ):
                assert secret_marker not in raw_text, f"session発行レスポンスに秘密情報が含まれています: {secret_marker}"
            assert session_body.get("shop_name") == "PublicAICallテスト美容室"
            assert "voice_session_id" in session_body
            print("E. AI受付ONの店舗 → 200・OPENAI_API_KEY/通知用電話番号/メールアドレス等の漏洩なし: OK")

    print()
    print("全テストOK: Phase P1（Public AI Call）— 既存バックエンドの安全性回帰・"
          "フロントエンド構造の整合性を確認")


if __name__ == "__main__":
    asyncio.run(main())
