"""
RECEPTRA — PHASE P2 スモークテスト（OWNER PUBLIC AI CALL SETTINGS）

背景:

Phase P2は、Phase P1で商品化したPublic AI Call（frontend/public/call.html?id=
{shop_id}）を、店舗オーナーが自分の管理画面（frontend/public/shop-manage.html）
から公開URL・QRコードを確認し、コピー・保存・SNS/Web設置案内までできるように
した「オーナー向けUI」フェーズである。

Audit（実装前）で判明した重要な事実:
- ON/OFFを制御する既存フィールドShop.ai_phone_reception_enabledと、それを
  取得・更新する既存API（GET/PUT /api/v1/shops/{shop_id}/phone-reception-
  settings）は、共にPhase P2着手前から完成済みで、オーナー認証必須・
  テナント分離チェック済みだった。P2はこれを完全に再利用し、新規バックエンド
  エンドポイント・新規DBカラム・マイグレーションを一切追加していない。
- 公開URL（https://<origin>/call.html?id=<shop_id>）は、shop-manage.html
  自身が既に?id=<shop_id>で開かれていることから、追加のAPI呼び出しなしに
  ブラウザ内だけで組み立てられる。
- QRコードはnpm公開パッケージ qrcode-generator（Kazuhiko Arase, MIT
  license, バージョン2.0.4）のdist/qrcode.jsをそのまま
  frontend/public/js/qrcode-lib.jsとしてベンダー同梱し、完全にオフライン・
  クライアント側のみで生成する（外部QR生成APIへの通信は一切発生しない）。
- frontend/public/js/web-ai-call-card.jsは、既存のid="phone-reception-
  settings-form"の保存/読み込みロジック（loadPhoneReceptionSettings /
  _savePhoneReceptionCore / 「全体を保存」アグリゲータ登録）には一切触れず、
  新しいDOM要素・新しい別ファイルとして完全に追加のみで実装されている。

このテストは、(1) 既存owner-authenticated APIが引き続き安全に振る舞うことの
バックエンド回帰確認、(2) 新規フロントエンドファイル・既存shop-manage.html
編集の構造的な整合性（id無変更・保存アグリゲータ無傷・QRライブラリが完全
オフラインであること等）を確認する。ブラウザでのクリップボードコピー・
QR実描画自体は実機/ブラウザが必要なため自動テストの対象外
（既存smoke_test_public_ai_call.pyと同じ方針）。

検証項目:
OWNER AUTH: 未認証でのPUTは拒否される／他テナントのオーナーは更新できない／
            正しいオーナーは更新できる
TOGGLE: ON／OFF／永続化（GET再取得で反映）／OFF時はPublic Callの/sessionが
        403になる／ON時は200になる（既存ガードの回帰確認、smoke_test_
        public_ai_call.pyのD/Eと同じ方式で再確認）
URL/QR/COPY: 新規追加ファイル・編集箇所の構造検証（下記static checks）
OWNER PAGE REGRESSION: 既存フォームのid・保存アグリゲータ登録が無変更
BACKEND REGRESSION: 本ファイルでは新規追加分のみを検証し、予約エンジン等の
                     全面的な回帰は既存の全smokeテスト群で別途確認する
                     （run_all_tests.py等、既存の実行手順を踏襲）

実行: python3 tests/smoke_test_owner_public_ai_call_settings.py
"""

import asyncio
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(
    tempfile.mkdtemp(), "smoke_owner_public_ai_call_settings.db"
)
os.environ["SECRET_KEY"] = "smoke-test-secret-key-owner-public-ai-call-settings"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _repo_path(*parts):
    return os.path.join(REPO_ROOT, *parts)


# ============================================================
# 構造検証（バックエンド起動なし）: URL / COPY / QR / OWNER PAGE REGRESSION
# ============================================================

def check_no_backend_files_touched():
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"], capture_output=True, text=True, cwd=REPO_ROOT,
    )
    result_staged = subprocess.run(
        ["git", "diff", "--name-only", "--cached", "HEAD"], capture_output=True, text=True, cwd=REPO_ROOT,
    )
    changed = {c for c in (result.stdout.splitlines() + result_staged.stdout.splitlines()) if c}
    backend_changed = [c for c in changed if c.startswith("app/")]
    assert not backend_changed, f"Phase P2でバックエンドファイルが変更されています（想定外）: {backend_changed}"
    print(f"[構造] app/配下は無変更であることをgit差分で確認: OK (変更ファイル: {sorted(changed)})")


def check_new_files_exist_and_valid():
    qr_lib_path = _repo_path("frontend", "public", "js", "qrcode-lib.js")
    card_js_path = _repo_path("frontend", "public", "js", "web-ai-call-card.js")
    manage_path = _repo_path("frontend", "public", "shop-manage.html")

    for p in (qr_lib_path, card_js_path, manage_path):
        assert os.path.isfile(p), f"必須ファイルが見つかりません: {p}"

    for p, label in ((qr_lib_path, "qrcode-lib.js"), (card_js_path, "web-ai-call-card.js")):
        result = subprocess.run(["node", "--check", p], capture_output=True, text=True)
        assert result.returncode == 0, f"{label}の構文エラー: {result.stderr}"
    print("[構造] qrcode-lib.js / web-ai-call-card.js は正しいJavaScript構文: OK")

    with open(qr_lib_path, encoding="utf-8") as f:
        qr_lib_js = f.read()
    assert "MIT" in qr_lib_js, "qrcode-lib.jsにMITライセンス表記が見つかりません"
    forbidden_calls = ("fetch(", "XMLHttpRequest", "new Image(", "WebSocket(")
    for token in forbidden_calls:
        assert token not in qr_lib_js, f"qrcode-lib.jsにネットワーク通信の疑いがある呼び出しがあります: {token}"
    print("[QR] qrcode-lib.jsはMITライセンス表記あり・ネットワーク呼び出しなし（完全オフライン）: OK")

    with open(card_js_path, encoding="utf-8") as f:
        card_js = f.read()
    for token in ("fetch(", "XMLHttpRequest", "authFetch("):
        assert token not in card_js, (
            f"web-ai-call-card.jsに新しいバックエンドAPI呼び出しの疑いがある記述があります: {token}"
        )
    print("[URL/QR] web-ai-call-card.jsは新規API呼び出しゼロ（クライアント側のみで完結）: OK")

    # URL: shop_idはブラウザのURLパラメータ(?id=)からのみ取得し、公開URLは
    # window.location.origin + '/call.html?id=' + encodeURIComponent(shopId) で
    # 組み立てられている（オーナーがshop_idを手入力する余地がない）。
    assert "params.get('id')" in card_js
    assert "/call.html?id=' + encodeURIComponent(shopId)" in card_js
    print("[URL] 公開URLはログイン中の店舗のshop_idから自動生成（手入力の余地なし）: OK")

    # URL/QR: tenant_id・JWT・トークン・秘密情報の類が、このファイルの中に
    # 一切登場しないことを確認する（QR/URLに秘密情報が絶対に混入しない設計の
    # 構造的な保証）。
    forbidden_terms = (
        "tenant_id", "access_token", "owner_token", "api_key", "API_KEY",
        "client_secret", "reservation_notification_phone", "reservation_notification_email",
    )
    for term in forbidden_terms:
        assert term not in card_js, f"web-ai-call-card.jsに秘密情報らしき識別子が含まれています: {term}"
    print("[QR/URL 安全性] web-ai-call-card.jsにtenant_id/トークン等の秘密情報関連識別子は一切なし: OK")

    # QRはpublicUrl（またはsource付きの同URL）以外をaddDataしていないことを
    # 構造的に確認する（qr.addData の呼び出し箇所が1つだけで、引数がpublicUrl）。
    add_data_calls = re.findall(r"\.addData\(([^)]*)\)", card_js)
    assert add_data_calls == ["publicUrl"], (
        f"qr.addData()に渡している値がpublicUrl以外です（秘密情報混入のリスク）: {add_data_calls}"
    )
    print("[QR 安全性] QRはpublicUrlのみをエンコード（他の値は一切addDataされない）: OK")

    # 長いURLでもレイアウトが壊れないこと（word-break指定・QR svgのレスポンシブ化）。
    with open(manage_path, encoding="utf-8") as f:
        manage_html = f.read()
    assert "word-break: break-all" in manage_html
    assert "webcall-action-btn { min-height: 44px" in manage_html
    print("[モバイル] 長いURLのword-break指定・タップ領域44px以上のCSSクラスを確認: OK")


def check_owner_page_regression():
    manage_path = _repo_path("frontend", "public", "shop-manage.html")
    with open(manage_path, encoding="utf-8") as f:
        html = f.read()

    # 既存フォーム・既存id群が一切変更されていないことの構造確認。
    required_ids = (
        'id="phone-reception-settings-form"',
        'id="ai-phone-reception-enabled"',
        'id="transfer-to-staff-enabled"',
        'id="transfer-phone-number"',
        'id="transfer-no-answer-fallback"',
        'id="phone-reception-settings-save-btn"',
        'id="phone-reception-settings-msg"',
    )
    for needle in required_ids:
        assert needle in html, f"既存の必須idが失われています（回帰）: {needle}"
    print("[回帰] 既存の電話受付設定フォームのid群は無変更: OK")

    # 既存の保存関数・load関数のシグネチャが無変更であること。
    for needle in (
        "async function loadPhoneReceptionSettings()",
        "async function _savePhoneReceptionCore()",
        "document.getElementById('phone-reception-settings-form').addEventListener('submit'",
    ):
        assert needle in html, f"既存の保存/読み込みロジックが変更されています（回帰）: {needle}"
    print("[回帰] loadPhoneReceptionSettings / _savePhoneReceptionCore / submitハンドラは無変更: OK")

    # 「全体を保存」アグリゲータに引き続き登録されていること。
    assert "{ label: 'AI電話受付設定', run: _savePhoneReceptionCore }" in html, (
        "「全体を保存」アグリゲータからAI電話受付設定が失われています（回帰）"
    )
    print("[回帰] 「全体を保存」アグリゲータへの登録は無変更: OK")

    # 新しい追加ブロックが、既存フォームの外（閉じタグの後）に置かれていること
    # （同じformの中で新しいsubmit系ボタンが誤って送信をトリガーしないための
    # 構造保証）。
    form_start = html.index('<form id="phone-reception-settings-form">')
    form_end = html.index("</form>", form_start)
    new_block_start = html.index('id="web-ai-call-block"')
    assert new_block_start > form_end, (
        "新しいWeb AI受付ブロックが既存フォームの内側に配置されています（想定外の構造）"
    )
    print("[構造] 新規追加ブロックは既存フォームの外側に配置（送信誤爆リスクなし）: OK")

    # 新規追加ボタンはすべてtype="button"であり、既存formのsubmitを誤爆しないこと。
    block_end = html.index("</div>\n        </div>", new_block_start)
    new_block_html = html[new_block_start:block_end]
    submit_like = re.findall(r'<button[^>]*id="web-ai-call[^"]*"[^>]*>', new_block_html)
    assert len(submit_like) >= 5, f"新規追加ボタンの数が想定より少ないです: {len(submit_like)}"
    for btn_tag in submit_like:
        assert 'type="button"' in btn_tag, f"新規追加ボタンがtype=\"button\"ではありません（誤送信リスク）: {btn_tag}"
    print(f"[構造] 新規追加ボタン({len(submit_like)}個)はすべてtype=\"button\"（既存formの誤送信リスクなし）: OK")

    # 新しいscriptタグは既存メインscriptの後に配置され、既存script終了タグ以前の
    # コードには一切依存しない外部ファイルであること。
    assert '<script src="js/qrcode-lib.js"></script>' in html
    assert '<script src="js/web-ai-call-card.js"></script>' in html
    print("[構造] qrcode-lib.js / web-ai-call-card.js は正しく読み込まれている: OK")


# ============================================================
# バックエンド回帰: OWNER AUTH / TOGGLE
# ============================================================

async def main():
    check_no_backend_files_touched()
    check_new_files_exist_and_valid()
    check_owner_page_regression()

    from app.main import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            from app.database import AsyncSessionLocal
            from app.models.shop import Shop

            # ---- オーナーA（正当な店舗オーナー）----
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-p2-a@example.com", "password": "password123",
                "display_name": "P2テストオーナーA",
            })
            assert r.status_code in (200, 201), f"register(A) failed: {r.status_code} {r.text}"
            owner_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "P2テストサロンA", "category": "ヘアサロン", "address": "東京都渋谷区1-1-1",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create shop(A) failed: {r.status_code} {r.text}"
            shop_id = r.json()["shop_id"]

            # ---- オーナーB（別テナント、無関係の店舗オーナー）----
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-p2-b@example.com", "password": "password123",
                "display_name": "P2テストオーナーB",
            })
            assert r.status_code in (200, 201), f"register(B) failed: {r.status_code} {r.text}"
            owner_b = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "P2テストサロンB", "category": "ネイルサロン", "address": "東京都目黒区2-2-2",
            }, headers=owner_b)
            assert r.status_code in (200, 201), f"create shop(B) failed: {r.status_code} {r.text}"

            settings_url = f"/api/v1/shops/{shop_id}/phone-reception-settings"

            # ===== OWNER AUTH: 未認証でのPUTは拒否される =====
            r = await client.put(settings_url, json={"ai_phone_reception_enabled": False})
            assert r.status_code in (401, 403), f"未認証PUTが想定外のステータス: {r.status_code} {r.text}"
            print(f"OWNER AUTH: 未認証でのPUTは拒否される（{r.status_code}）: OK")

            # ===== OWNER AUTH: 他テナント（オーナーB）は店舗Aを更新できない =====
            r = await client.put(settings_url, json={"ai_phone_reception_enabled": False}, headers=owner_b)
            assert r.status_code == 403, f"他テナントによるPUTが403以外: {r.status_code} {r.text}"
            print("OWNER AUTH: 他テナント(オーナーB)は店舗Aの設定を更新できない（403）: OK")

            # ===== OWNER AUTH: 正しいオーナー(A)は更新できる =====
            r = await client.get(settings_url, headers=owner_a)
            assert r.status_code == 200, f"正しいオーナーによるGETが失敗: {r.status_code} {r.text}"
            assert r.json()["ai_phone_reception_enabled"] is True, "初期状態はON(既定値)であるはず"
            print("OWNER AUTH: 正しいオーナー(A)はGETできる（既定値ON）: OK")

            # ===== TOGGLE: OFFにできる・永続化される =====
            r = await client.put(settings_url, json={"ai_phone_reception_enabled": False}, headers=owner_a)
            assert r.status_code == 200, f"OFFへのPUTが失敗: {r.status_code} {r.text}"
            assert r.json()["ai_phone_reception_enabled"] is False
            r = await client.get(settings_url, headers=owner_a)
            assert r.json()["ai_phone_reception_enabled"] is False, "OFFがGETで再取得しても永続化されていない"
            print("TOGGLE: OFFに更新・GETで再取得しても永続化されている: OK")

            # ===== TOGGLE: OFF状態でPublic Call(/session)は403になる（既存ガードの回帰確認）=====
            r = await client.post(f"/api/v1/shops/{shop_id}/realtime-voice/session")
            assert r.status_code == 403, f"OFF状態で/sessionが403以外: {r.status_code} {r.text}"
            assert r.json().get("reason_code") == "ai_phone_reception_disabled"
            print("TOGGLE: OFF状態でPublic Call(/realtime-voice/session)は403（回帰なし）: OK")

            # ===== TOGGLE: ONにできる・永続化される =====
            r = await client.put(settings_url, json={"ai_phone_reception_enabled": True}, headers=owner_a)
            assert r.status_code == 200, f"ONへのPUTが失敗: {r.status_code} {r.text}"
            assert r.json()["ai_phone_reception_enabled"] is True
            r = await client.get(settings_url, headers=owner_a)
            assert r.json()["ai_phone_reception_enabled"] is True, "ONがGETで再取得しても永続化されていない"
            print("TOGGLE: ONに更新・GETで再取得しても永続化されている: OK")

            # ===== TOGGLE: ON状態でPublic Call(/session)は200になる（既存ガードの回帰確認）=====
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
            assert r.status_code == 200, f"ON状態で/sessionが失敗: {r.status_code} {r.text}"
            assert "sk-test-not-used-because-mocked" not in r.text
            print("TOGGLE: ON状態でPublic Call(/realtime-voice/session)は200・秘密情報漏洩なし（回帰なし）: OK")

            # ===== TOGGLE: 取り次ぎ設定を巻き込まず、ai_phone_reception_enabledだけが
            #                独立して更新できる（部分更新=exclude_unsetの回帰確認）=====
            r = await client.put(settings_url, json={"ai_phone_reception_enabled": False}, headers=owner_a)
            assert r.status_code == 200
            assert r.json()["transfer_to_staff_enabled"] is False, "取り次ぎ設定が意図せず変化しています"
            print("TOGGLE: ai_phone_reception_enabledの単独更新は取り次ぎ設定を巻き込まない（部分更新の回帰なし）: OK")

    print()
    print("全テストOK: Phase P2（Owner Public AI Call Settings）— 既存owner-authenticated "
          "APIの安全性回帰・新規フロントエンドファイルの構造的整合性を確認")


if __name__ == "__main__":
    asyncio.run(main())
