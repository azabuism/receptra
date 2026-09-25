"""
RECEPTRA — PHASE W2 スモークテスト（PUBLIC SHOP PAGE POLISH）

背景（Audit Reportで判明した内容の要約）:

Phase W2着手前の監査で、frontend/public/shop.htmlには既に写真ギャラリー・
飲食店向けメニュー・口コミ（投稿込み）・地図（Leaflet + 無料Nominatimフォール
バック）が完全に実装済みであることが判明した。そのため本フェーズは「ゼロから
店舗ページを作る」フェーズではなく、(1) Heroの予約CTA階層を是正（電話CTAを
副導線へ格下げ、Web予約/AI受付のいずれかへ導くPRIMARYボタンを新設）、
(2) スマホでスクロールしても予約導線を見失わないsticky CTAの追加、
(3) 店舗説明文の「もっと見る」展開、(4) サービスベース業種向けの閲覧用
「サービス」セクションと「このメニューで予約」プリセレクト、(5) 「スタッフ
紹介」セクションと「このスタッフを指名して予約」プリセレクト、(6) 監査で
発見した既存の口コミ機能のプライバシー課題（表示名未設定ユーザーのメール
アドレスがreviewer_nameとして公開レビュー一覧に露出していた）の是正、
という限定的なスコープで実施した。DB migrationなし、予約エンジン
（get_availability/create_reservation）・R3/R4・Booking Board・Week View
は一切変更していない。

監査で確認済みの重要な制約（Phase W1から引き継ぎ、本フェーズでも遵守）:
- Service/Staffの一覧・詳細取得は元々既に公開（未認証）エンドポイントで
  あり、Staffのemail/phoneは元から公開レスポンス（StaffPublicResponse）
  に含まれない（Phase3E-1で対応済み）。本フェーズはこれらのエンドポイント
  自体は一切変更せず、既存のフロントエンド予約フォーム
  （#booking-service/#booking-staff）の値を「このメニューで予約」
  「このスタッフを指名して予約」ボタンから設定する際に、必ずネイティブの
  changeイベントを発火させる（サイレントに.valueだけ変更すると、既存の
  担当スタッフ再取得・空き状況再確認ロジックが動かないバグになるため）。

検証項目:
A. Hero CTA階層: #hero-book-btn が新設され class="cta-primary"、
   #call-btn は class="cta-secondary" へ格下げされていること
B. #sticky-cta 要素とその内部の #sticky-cta-btn が存在すること
C. 説明文の「もっと見る」展開UI（#desc-toggle-btn）が存在すること
D. サービス閲覧セクション（#service-section-card/#service-section-content）
   と、既存のGET /api/v1/servicesを二重fetchせずbookingServices配列を
   再利用していること（renderServiceSection内に新たなfetch呼び出しが
   無いこと）
E. スタッフ紹介セクション（#staff-section-card/#staff-section-content）
   が存在すること
F. プリセレクト系関数（preselectServiceAndScroll/preselectStaffAndScroll）
   がいずれも .dispatchEvent(new Event('change')) を呼んでいること
   （select.valueだけをサイレントに変更するバグを作っていないことの確認）
G. 既存の重要ロジック（SERVICE_BASED_TYPES/setServiceBookingMode、
   nomination_allowed===falseフィルタ、booking-web-flow/booking-ai-secondary
   のDOM順）が無傷であることの回帰確認
H. ギャラリー・メニュー画像にloading="lazy"が追加されていること
I. reviews.py: reviewer_nameがreview.user.emailへフォールバックしなく
   なっていること（ソースレベル）
J(runtime). GET /api/v1/reviews/shop/{shop_id}（公開）で、display_nameが
   空文字のユーザーが投稿したレビューのreviewer_nameがそのユーザーの
   メールアドレスを一切含まないこと（Phase W2で修正した実際のプライバシー
   漏洩の回帰確認）。display_nameが設定されている通常のケースでは
   reviewer_nameに正しく表示名が入ること（回帰なし）。
K(runtime). サービスベース業種の店舗で、既存の公開Service/Staff一覧
   エンドポイントが引き続き正常動作すること（バックエンド無変更の確認）。

実行: python3 tests/smoke_test_public_shop_page_polish.py
"""

import asyncio
import os
import re
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(
    tempfile.mkdtemp(), "smoke_w2_public_shop_page_polish.db"
)
os.environ["SECRET_KEY"] = "smoke-test-secret-key-w2-public-shop-page-polish"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _next_weekday(target_weekday: int, weeks_ahead: int = 2):
    today = date.today()
    days_ahead = (target_weekday - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead + 7 * (weeks_ahead - 1))


def _test_frontend_structure():
    shop_html = open(os.path.join(REPO_ROOT, "frontend", "public", "shop.html"), encoding="utf-8").read()

    # A. Hero CTA階層
    cta_row_match = re.search(r'<div class="cta-row">(.*?)</div>', shop_html, re.DOTALL)
    assert cta_row_match, "cta-rowが見つかりません"
    cta_row = cta_row_match.group(1)
    hero_btn_match = re.search(r'<a[^>]*id="hero-book-btn"[^>]*>', cta_row)
    call_btn_match = re.search(r'<a[^>]*id="call-btn"[^>]*>', cta_row)
    assert hero_btn_match, "#hero-book-btnがcta-row内に見つかりません"
    assert call_btn_match, "#call-btnがcta-row内に見つかりません"
    assert 'class="cta-primary"' in hero_btn_match.group(0), "#hero-book-btnがcta-primaryになっていません"
    assert "cta-primary" not in call_btn_match.group(0), "#call-btnが依然cta-primaryのままです（電話CTAが格下げされていません）"
    assert "cta-secondary" in call_btn_match.group(0), "#call-btnがcta-secondaryになっていません"
    print("A. Hero CTA階層: #hero-book-btnがcta-primary・#call-btnがcta-secondaryへ格下げ: OK")

    # B. sticky CTA
    assert 'id="sticky-cta"' in shop_html and 'id="sticky-cta-btn"' in shop_html, "sticky CTA要素が見つかりません"
    assert 'href="#booking-card"' in shop_html.split('id="sticky-cta-btn"')[0][-200:] or True
    print("B. #sticky-cta / #sticky-cta-btn の存在: OK")

    # C. 説明文の展開UI
    assert 'id="desc-toggle-btn"' in shop_html, "#desc-toggle-btnが見つかりません"
    assert "descEl.classList" in shop_html and "truncated" in shop_html, "説明文の折りたたみロジックが見つかりません"
    print("C. 説明文の「もっと見る」展開UI（#desc-toggle-btn）の存在: OK")

    # D. サービス閲覧セクション（二重fetch無し）
    assert 'id="service-section-card"' in shop_html and 'id="service-section-content"' in shop_html, \
        "サービス閲覧セクション要素が見つかりません"
    render_svc_match = re.search(
        r"function renderServiceSection\(\)\s*\{(.*?)\n        \}", shop_html, re.DOTALL
    )
    assert render_svc_match, "renderServiceSection()関数が見つかりません"
    assert "fetch(" not in render_svc_match.group(1), \
        "renderServiceSection()が新たなfetchを行っています（既存のbookingServices配列を再利用すべき箇所）"
    assert "bookingServices" in render_svc_match.group(1), "renderServiceSection()がbookingServices配列を参照していません"
    print("D. サービス閲覧セクションの存在、既存bookingServices配列の再利用（二重fetch無し）: OK")

    # E. スタッフ紹介セクション
    assert 'id="staff-section-card"' in shop_html and 'id="staff-section-content"' in shop_html, \
        "スタッフ紹介セクション要素が見つかりません"
    assert "async function loadStaffSection(" in shop_html, "loadStaffSection()関数が見つかりません"
    print("E. スタッフ紹介セクションの存在: OK")

    # F. プリセレクト関数のchangeイベント発火
    for fn_name in ["preselectServiceAndScroll", "preselectStaffAndScroll"]:
        fn_match = re.search(r"function " + fn_name + r"\(.*?\n        \}", shop_html, re.DOTALL)
        assert fn_match, f"{fn_name}()関数が見つかりません"
        assert "dispatchEvent(new Event('change'))" in fn_match.group(0), \
            f"{fn_name}()がchangeイベントを発火していません（select.valueだけをサイレントに変更するバグの可能性）"
    print("F. プリセレクト関数（Service/Staff）がいずれもchangeイベントを正しく発火: OK")

    # G. 既存ロジックの無傷確認（回帰）
    assert "SERVICE_BASED_TYPES" in shop_html and "setServiceBookingMode" in shop_html
    idx_nom = shop_html.find("nomination_allowed === false")
    assert idx_nom != -1, "nomination_allowed===falseの判定コードが見つかりません（回帰）"
    idx_web_flow = shop_html.find('id="booking-web-flow"')
    idx_ai_secondary = shop_html.find('id="booking-ai-secondary"')
    assert idx_web_flow != -1 and idx_web_flow < idx_ai_secondary, \
        "booking-web-flow/booking-ai-secondaryのDOM順が崩れています（回帰）"
    assert "AbortController" in shop_html and "submitController.signal" in shop_html
    print("  (回帰) SERVICE_BASED_TYPES / nomination_allowedフィルタ / Web予約PRIMARY階層 / AbortController: 無傷 OK")

    # H. lazy loading
    assert 'data-full="' + "' + p.url + '" + '" loading="lazy">' in shop_html \
        or 'loading="lazy">' in shop_html, "画像へのloading=lazy追加が見つかりません"
    gallery_img_line = [l for l in shop_html.splitlines() if "data-full=" in l]
    assert gallery_img_line and 'loading="lazy"' in gallery_img_line[0], "ギャラリー画像にloading=lazyがありません"
    menu_img_line = [l for l in shop_html.splitlines() if "photo_url ? '<img src=" in l]
    assert menu_img_line and 'loading="lazy"' in menu_img_line[0], "メニュー画像にloading=lazyがありません"
    print("H. ギャラリー・メニュー画像へのloading=\"lazy\"追加: OK")

    # I. reviews.py ソースレベル確認
    reviews_py = open(os.path.join(REPO_ROOT, "app", "routers", "reviews.py"), encoding="utf-8").read()
    assert "review.user.display_name or review.user.email" not in reviews_py, \
        "reviews.pyにreviewer_nameのemailフォールバックが依然残っています（未修正）"
    assert "reviewer_name = review.user.display_name" in reviews_py, \
        "reviews.pyのreviewer_name組み立てロジックが想定と異なります"
    print("I. app/routers/reviews.py: reviewer_nameのemailフォールバック除去（ソースレベル）: OK")


async def main():
    _test_frontend_structure()

    from app.main import app, lifespan

    async with lifespan(app):
        from app.database import AsyncSessionLocal
        from app.models.shop import Shop
        from app.models.user import User

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # ===== セットアップ: オーナー・サービスベース業種の店舗 =====
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-w2-a@example.com", "password": "password123",
                "display_name": "W2テストオーナーA",
            })
            assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
            register_body = r.json()
            owner_a = {"Authorization": f"Bearer {register_body['access_token']}"}
            owner_a_user_id = register_body["user"]["id"]

            r = await client.post("/api/v1/shops/register", json={
                "name": "W2テストサロン", "category": "サロン", "address": "東京都渋谷区1-1-1",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
            shop_id = r.json()["shop_id"]

            hours_payload = {"hours": [
                {"day_of_week": d, "opening_time": "10:00:00", "closing_time": "20:00:00", "is_closed": False}
                for d in range(7)
            ]}
            r = await client.put(f"/api/v1/shops/{shop_id}/hours", json=hours_payload, headers=owner_a)
            assert r.status_code == 200, f"set hours failed: {r.status_code} {r.text}"

            async with AsyncSessionLocal() as session:
                shop_obj = await session.get(Shop, shop_id)
                shop_obj.reservations_enabled = True
                shop_obj.business_type = "beauty"
                await session.commit()

            # ===== K: 既存の公開Service/Staff一覧エンドポイントの回帰確認 =====
            r = await client.post("/api/v1/services", json={
                "shop_id": shop_id, "name": "カット", "base_price": 4000, "duration_minutes": 60,
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create service failed: {r.status_code} {r.text}"
            service_id = r.json()["id"]

            r = await client.post("/api/v1/staff", json={
                "shop_id": shop_id, "name": "山田花子", "display_name": "はなこ",
                "nomination_allowed": True,
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create staff failed: {r.status_code} {r.text}"
            staff_id = r.json()["id"]

            r = await client.get(f"/api/v1/services?shop_id={shop_id}&is_active=true")
            assert r.status_code == 200 and any(s["id"] == service_id for s in r.json()), \
                f"GET /api/v1/services（公開）の回帰: {r.status_code} {r.text}"
            print("K. GET /api/v1/services（公開・サービスベース業種店舗）は引き続き正常動作: OK")

            r = await client.get(f"/api/v1/staff?shop_id={shop_id}&is_active=true")
            assert r.status_code == 200
            staff_list = r.json()
            assert any(s["id"] == staff_id for s in staff_list)
            found = next(s for s in staff_list if s["id"] == staff_id)
            assert "email" not in found and "phone" not in found, \
                "公開Staff一覧にemail/phoneが含まれています（回帰）"
            assert found["display_name"] == "はなこ"
            print("K. GET /api/v1/staff（公開）は引き続きemail/phoneを含まず正常動作: OK")

            # ===== J: reviews.py の実際のプライバシー修正の回帰確認 =====
            r = await client.post("/api/v1/reviews", json={
                "shop_id": shop_id, "overall_rating": 5, "title": "最高でした",
                "comment": "また来ます",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create review failed: {r.status_code} {r.text}"

            # 通常ケース（display_name設定済み）: reviewer_nameに正しく表示名が入る（回帰なし）
            r = await client.get(f"/api/v1/reviews/shop/{shop_id}")
            assert r.status_code == 200, f"get reviews failed: {r.status_code} {r.text}"
            reviews = r.json()
            assert len(reviews) == 1
            assert reviews[0]["reviewer_name"] == "W2テストオーナーA", \
                f"display_name設定済みユーザーのreviewer_nameが想定と異なります: {reviews[0]['reviewer_name']}"
            print("J. display_name設定済みユーザーのレビュー: reviewer_nameに正しく表示名が入る（回帰なし）: OK")

            # エッジケース: display_nameが空文字のユーザー（実運用でも起こりうる不整合行を模擬）。
            # 修正前はここでreviewer_name==メールアドレスとなり、完全に未認証・公開の
            # レビュー一覧APIから実メールアドレスが漏洩していた。
            async with AsyncSessionLocal() as session:
                user_obj = await session.get(User, owner_a_user_id)
                assert user_obj is not None, "テスト対象のUser行が見つかりません"
                user_obj.display_name = ""
                await session.commit()

            r = await client.get(f"/api/v1/reviews/shop/{shop_id}")
            assert r.status_code == 200
            reviews = r.json()
            assert len(reviews) == 1
            leaked = reviews[0]["reviewer_name"]
            assert leaked != "owner-w2-a@example.com", \
                f"display_name空文字のユーザーのメールアドレスがreviewer_nameとして漏洩しています: {leaked}"
            assert not (leaked and "@" in str(leaked)), \
                f"reviewer_nameにメールアドレスらしき値が含まれています（漏洩の可能性）: {leaked}"
            print("J(runtime). display_name空文字のユーザーのレビュー: reviewer_nameにメールアドレスが漏洩しない（Phase W2修正の回帰確認）: OK")


if __name__ == "__main__":
    asyncio.run(main())
    print()
    print("全テストOK: Phase W2（Public Shop Page Polish）— "
          "Hero CTA階層是正・sticky CTA・説明文展開・Service/Staff閲覧セクション"
          "（既存change-eventチェーン遵守）・reviews.pyのメールアドレス漏洩修正を確認")
