"""
RECEPTRA — PHASE W1 スモークテスト（PUBLIC SHOP PAGE & WEB BOOKING）

背景（Audit Reportで判明した内容の要約）:

Phase W1着手前の監査で、frontend/public/shop.htmlには既に完全に動作する
Web予約フォーム（既存のcreate_reservation()/get_availability()をそのまま
再利用したもの）が実装済みであることが判明した。そのため本フェーズは
「新しい予約エンジンを作る」フェーズではなく、(1) 来店日入力を月間
カレンダーUIへ強化し、(2) 従来「AIチャット/AI通話」ボタンが#booking-card
冒頭にありWeb予約フォームより視覚的に優先されていたCTA階層を、Web予約を
PRIMARY・AI受付をSECONDARYへ反転させ、(3) 監査で発見した3件の既存
セキュリティ/プライバシー課題を是正する、という限定的なスコープで実施した。

監査で発見し、本フェーズで是正した3件（DB migration・新規予約エンジン
不要、いずれも認証追加・レスポンススキーマ縮小のみ）:
  1. GET /api/v1/shops/{shop_id} と GET /api/v1/shops/search が、
     ShopResponse（tenant_id含む）をそのまま公開（未認証）で返しており、
     tenant_idが意図せず漏洩していた（ユーザー様ご自身の要求仕様
     「Publicページへ絶対に出さない」リストの1番目がtenant_idそのもの）。
     → tenant_idを含まないShopPublicResponse/ShopPublicListResponseを新設し、
       この2つの公開エンドポイントのみ切り替えた。owner専用エンドポイント
       （/mine, PATCH, PUT hours等）は元のShopResponseのまま無変更。
  2. GET /api/v1/shops/{shop_id}/tables（テーブル一覧）が完全に未認証・
     テナントチェックなしで、任意のshop_idの内部table_id等を返せた
     （同じファイル内のPOST/DELETEは元々_get_owned_shop()で保護済みだった
     が、GET一覧だけ保護漏れだった）。→ 同じ_get_owned_shop()パターンを
     GETにも適用。shop-manage.htmlの唯一の呼び出し箇所もauthFetch()へ統一。
  3. POST /api/v1/reservations/create と GET .../availability に
     レート制限が一切なかった（realtime_voice.pyには既に同種の簡易レート
     制限があったが、通常のWeb予約経路には適用されていなかった）。
     → realtime_voice.pyと全く同じ設計（プロセス内メモリ、shop_idキーの
     スライディングウィンドウ、新規の有料外部サービス不使用）をそのまま
     踏襲して追加。

検証項目:
A. 公開GETからtenant_idが完全に消えていること（ShopPublicResponse/
   ShopPublicListResponseへの切り替え）
B. owner専用GET /mine は引き続きtenant_idを含むこと（回帰確認）
C. GET /shops/{shop_id}/tables が認証必須化されたこと（未認証401/403、
   他テナント403、正しいオーナー200）
D. POST /reservations/create ・ GET .../availability にレート制限が
   機能すること（429）
E. 既存のゲスト予約フロー（空き確認→予約作成）が引き続き正常に動作する
   こと（バックエンド編集による回帰がないことの確認）
F. reservations_enabled=Falseの店舗では、既存のバックエンド側の拒否
   （400, "この店舗は現在予約を受け付けていません"）が変わらず機能する
   こと（フロントエンドの表示切り替えはUI上の話であり、バックエンドの
   検証ロジック自体には一切手を加えていないことの確認）
G. shop.html構造: Web予約フロー（#booking-web-flow）がAI受付導線
   （#booking-ai-secondary）よりDOM順で先に来ること（PRIMARY/SECONDARY
   階層の反転）
H. shop.html構造: 新規カレンダーUI要素が存在すること
I. shop.html構造: 既存予約フォームの要素id（booking-date/booking-party/
   booking-name等）が保持されていること（既存JS関数からの参照が壊れて
   いないことの確認）
J. shop.html構造: 送信fetchにAbortController/timeoutが追加されたこと
K. shop.html構造: 担当スタッフ一覧構築時にnomination_allowed=falseを
   除外するようになったこと
L. shop-manage.htmlのテーブル一覧取得がauthFetch()に統一されたこと

実行: python3 tests/smoke_test_public_shop_page_web_booking.py
"""

import asyncio
import os
import re
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(
    tempfile.mkdtemp(), "smoke_w1_public_shop_page.db"
)
os.environ["SECRET_KEY"] = "smoke-test-secret-key-w1-public-shop-page"
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

    # G. Web予約フローがAI受付導線より先（PRIMARY/SECONDARY階層の反転）
    idx_web_flow = shop_html.find('id="booking-web-flow"')
    idx_ai_secondary = shop_html.find('id="booking-ai-secondary"')
    assert idx_web_flow != -1 and idx_ai_secondary != -1, "booking-web-flow/booking-ai-secondaryが見つかりません"
    assert idx_web_flow < idx_ai_secondary, "Web予約フローがAI受付導線より後に配置されています（PRIMARY/SECONDARYが反転していません）"
    print("G. #booking-web-flow が #booking-ai-secondary よりDOM順で先（Web予約がPRIMARY）: OK")

    # H. カレンダーUI要素
    for cal_id in ["booking-calendar", "cal-grid", "cal-prev-month", "cal-next-month", "cal-month-label", "cal-dow"]:
        assert f'id="{cal_id}"' in shop_html, f"カレンダー要素が見つかりません: {cal_id}"
    assert "booking-check-btn" not in shop_html, "旧「空き状況を確認」ボタンが残っています（カレンダー方式では不要）"
    print("H. 月間カレンダーUI要素の存在、旧「空き状況を確認」ボタンの削除: OK")

    # I. 既存予約フォームの要素idが保持されていること
    for existing_id in [
        "booking-date", "booking-party", "booking-service", "booking-staff",
        "booking-name", "booking-phone", "booking-email", "booking-notes",
        "booking-coupon", "booking-submit-btn", "booking-slots-wrap", "booking-slots",
        "booking-form-wrap", "booking-msg",
    ]:
        assert f'id="{existing_id}"' in shop_html, f"既存の予約フォーム要素idが失われています: {existing_id}"
    print("I. 既存予約フォームの要素id（booking-date/booking-party/booking-name等）の保持: OK")

    # J. AbortController/timeoutの追加
    assert "AbortController" in shop_html and "submitController.signal" in shop_html, \
        "予約送信fetchへのAbortController/timeout追加が見つかりません"
    print("J. 予約送信fetchへのAbortController（タイムアウト）追加: OK")

    # K. nomination_allowed フィルタ
    # 実装は「nomination_allowedに言及するコメント」の直後ではなく、実際の
    # if文（"nomination_allowed === false" というコード自体）の直後に
    # continueが来ることを確認する（コメントとコードの両方にこの語が
    # 現れるため、単純な最初の出現位置ではコメント側にヒットしてしまう）。
    assert "nomination_allowed" in shop_html, "担当スタッフ一覧のnomination_allowedフィルタが見つかりません"
    idx_nom = shop_html.find("nomination_allowed === false")
    assert idx_nom != -1, "nomination_allowed===falseの判定コードが見つかりません"
    nearby = shop_html[idx_nom:idx_nom + 60]
    assert "continue" in nearby, "nomination_allowed===falseをスキップする実装が見つかりません"
    print("K. 担当スタッフ一覧構築時のnomination_allowed=falseフィルタ: OK")

    # 既存の重要ロジック（業種別party size表示切替）が無傷であることの軽い回帰確認
    assert "SERVICE_BASED_TYPES" in shop_html and "setServiceBookingMode" in shop_html
    print("  (回帰) 既存の業種別ご予約フォーム切替ロジック（SERVICE_BASED_TYPES）: 無傷 OK")

    # shop-manage.htmlのテーブル一覧取得がauthFetch()化されたこと
    shop_manage_html = open(os.path.join(REPO_ROOT, "frontend", "public", "shop-manage.html"), encoding="utf-8").read()
    m = re.search(r"async function loadTables\(\).*?\n(.*?)\n\s*const tables", shop_manage_html, re.DOTALL)
    assert m, "shop-manage.htmlのloadTables()が見つかりません"
    assert "authFetch(" in m.group(1) and "await fetch(" not in m.group(1), \
        "shop-manage.htmlのテーブル一覧取得がauthFetch()化されていません"
    print("L. shop-manage.html loadTables() の authFetch() 化: OK")


async def main():
    _test_frontend_structure()

    from app.main import app, lifespan

    async with lifespan(app):
        from app.database import AsyncSessionLocal
        from app.models.shop import Shop
        from app.schemas.shop import ShopPublicResponse, ShopPublicListResponse

        # A/B: スキーマレベルでtenant_idの有無を確認
        assert "tenant_id" not in ShopPublicResponse.model_fields, "ShopPublicResponseにtenant_idが含まれています"
        assert "tenant_id" not in ShopPublicListResponse.model_fields["items"].annotation.__args__[0].model_fields
        print("A(schema). ShopPublicResponse/ShopPublicListResponseにtenant_idフィールドが存在しないこと: OK")

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # ===== セットアップ: オーナーA・オーナーB・店舗 =====
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-w1-a@example.com", "password": "password123",
                "display_name": "W1テストオーナーA",
            })
            assert r.status_code in (200, 201), f"register A failed: {r.status_code} {r.text}"
            owner_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-w1-b@example.com", "password": "password123",
                "display_name": "W1テストオーナーB",
            })
            assert r.status_code in (200, 201), f"register B failed: {r.status_code} {r.text}"
            owner_b = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "W1テスト食堂", "category": "食堂", "address": "東京都新宿区9-9-9",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
            shop_id = r.json()["shop_id"]

            hours_payload = {"hours": [
                {"day_of_week": d, "opening_time": "10:00:00", "closing_time": "20:00:00", "is_closed": False}
                for d in range(7)
            ]}
            r = await client.put(f"/api/v1/shops/{shop_id}/hours", json=hours_payload, headers=owner_a)
            assert r.status_code == 200, f"set hours failed: {r.status_code} {r.text}"

            r = await client.post(f"/api/v1/shops/{shop_id}/tables", json={
                "name": "テーブルA", "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 200, f"create table failed: {r.status_code} {r.text}"

            # ===== A: 公開GETからtenant_idが消えていること =====
            r = await client.get(f"/api/v1/shops/{shop_id}")
            assert r.status_code == 200, f"public get_shop failed: {r.status_code} {r.text}"
            body = r.json()
            assert "tenant_id" not in body, f"GET /shops/{{id}}（公開）にtenant_idが含まれています: {list(body.keys())}"
            assert body["id"] == shop_id and body["name"] == "W1テスト食堂"
            print("A(runtime). GET /shops/{shop_id}（公開）レスポンスにtenant_idが含まれない: OK")

            r = await client.get("/api/v1/shops/search", params={"keyword": "W1テスト食堂"})
            assert r.status_code == 200, f"search failed: {r.status_code} {r.text}"
            search_body = r.json()
            assert search_body["items"], "検索結果が空です"
            for item in search_body["items"]:
                assert "tenant_id" not in item, f"GET /shops/search（公開）のitemsにtenant_idが含まれています: {list(item.keys())}"
            print("A(runtime). GET /shops/search（公開）レスポンスのitemsにtenant_idが含まれない: OK")

            # ===== B: owner専用 /mine は引き続きtenant_idを含む（回帰確認） =====
            r = await client.get("/api/v1/shops/mine", headers=owner_a)
            assert r.status_code == 200, f"GET /shops/mine failed: {r.status_code} {r.text}"
            mine_items = r.json()["items"]
            assert mine_items and "tenant_id" in mine_items[0], \
                "GET /shops/mine（オーナー認証済み）からtenant_idが失われています（回帰）"
            print("B. GET /shops/mine（オーナー認証済み）は引き続きtenant_idを含む（回帰なし）: OK")

            # ===== C: GET /shops/{id}/tables の認証必須化 =====
            r = await client.get(f"/api/v1/shops/{shop_id}/tables")
            assert r.status_code in (401, 403), f"未認証でのテーブル一覧取得が拒否されていません: {r.status_code}"
            print(f"C. GET /shops/{{id}}/tables 未認証アクセス拒否（{r.status_code}）: OK")

            r = await client.get(f"/api/v1/shops/{shop_id}/tables", headers=owner_b)
            assert r.status_code == 403, f"他テナントのオーナーからのテーブル一覧取得が拒否されていません: {r.status_code}"
            print("C. GET /shops/{id}/tables 他テナントのオーナーは403: OK")

            r = await client.get(f"/api/v1/shops/{shop_id}/tables", headers=owner_a)
            assert r.status_code == 200, f"正しいオーナーからのテーブル一覧取得が失敗: {r.status_code} {r.text}"
            assert len(r.json()) == 1 and r.json()[0]["name"] == "テーブルA"
            print("C. GET /shops/{id}/tables 正しいオーナーは200・正しいデータ: OK")

            # ===== E: 既存のゲスト予約フロー（空き確認→予約作成）の回帰確認 =====
            async with AsyncSessionLocal() as session:
                shop_obj = await session.get(Shop, shop_id)
                shop_obj.reservations_enabled = True
                await session.commit()

            target_date = _next_weekday(2, weeks_ahead=2)
            r = await client.get(
                f"/api/v1/reservations/shop/{shop_id}/availability",
                params={"date": target_date.isoformat(), "party_size": 2},
            )
            assert r.status_code == 200, f"availability failed: {r.status_code} {r.text}"
            avail = r.json()
            assert avail["is_open"] is True
            available_slot = next(s["time"] for s in avail["slots"] if s["available"])
            print(f"E. GET .../availability（ゲスト・公開）は引き続き正常動作: OK（空き枠 {available_slot}）")

            r = await client.post("/api/v1/reservations/create", json={
                "shop_id": shop_id,
                "reservation_date": f"{target_date.isoformat()}T{available_slot}:00",
                "guest_name": "山田太郎", "guest_phone": "09012345678",
                "number_of_people": 2,
            })
            assert r.status_code == 200, f"create_reservation failed: {r.status_code} {r.text}"
            assert r.json()["reservation"]["status"] == "confirmed" or r.json()["reservation"]["id"]
            print("E. POST /reservations/create（ゲスト・公開）は引き続き正常動作: OK")

            # ===== F: reservations_enabled=False の既存拒否ロジックは無変更 =====
            r = await client.post("/api/v1/shops/register", json={
                "name": "W1テスト未アクティベート店", "category": "食堂", "address": "東京都新宿区1-1-1",
            }, headers=owner_a)
            assert r.status_code in (200, 201)
            disabled_shop_id = r.json()["shop_id"]
            r = await client.post("/api/v1/reservations/create", json={
                "shop_id": disabled_shop_id,
                "reservation_date": f"{target_date.isoformat()}T12:00:00",
                "guest_name": "山田太郎", "guest_phone": "09012345678",
                "number_of_people": 2,
            })
            assert r.status_code == 400, f"reservations_enabled=Falseの店舗への予約が拒否されていません: {r.status_code}"
            assert "受け付けていません" in r.json()["detail"]
            print("F. reservations_enabled=False の店舗はcreate_reservationで引き続き拒否される（回帰なし）: OK")

            # ===== D: レート制限（専用の別店舗で実施し、他の検証への影響を避ける） =====
            r = await client.post("/api/v1/shops/register", json={
                "name": "W1レート制限テスト店", "category": "食堂", "address": "東京都新宿区2-2-2",
            }, headers=owner_a)
            assert r.status_code in (200, 201)
            rl_shop_id = r.json()["shop_id"]
            r = await client.put(f"/api/v1/shops/{rl_shop_id}/hours", json=hours_payload, headers=owner_a)
            assert r.status_code == 200

            avail_statuses = []
            for _ in range(35):
                resp = await client.get(
                    f"/api/v1/reservations/shop/{rl_shop_id}/availability",
                    params={"date": target_date.isoformat(), "party_size": 2},
                )
                avail_statuses.append(resp.status_code)
            assert 429 in avail_statuses, f"GET .../availability に対するレート制限が機能していません: {set(avail_statuses)}"
            print(f"D. GET .../availability のレート制限が機能（{avail_statuses.count(429)}/35件が429）: OK")

            create_statuses = []
            for _ in range(15):
                resp = await client.post("/api/v1/reservations/create", json={
                    "shop_id": rl_shop_id,
                    "reservation_date": f"{target_date.isoformat()}T12:00:00",
                    "guest_name": "レート制限テスト", "guest_phone": "09000000000",
                    "number_of_people": 2,
                })
                create_statuses.append(resp.status_code)
            assert 429 in create_statuses, f"POST .../create に対するレート制限が機能していません: {set(create_statuses)}"
            print(f"D. POST .../create のレート制限が機能（{create_statuses.count(429)}/15件が429）: OK")


if __name__ == "__main__":
    asyncio.run(main())
    print()
    print("全テストOK: Phase W1（Public Shop Page & Web Booking）— "
          "tenant_id非公開化・テーブル一覧認証必須化・レート制限追加、"
          "既存Web予約フロー無回帰、shop.htmlのカレンダー/CTA階層構造を確認")
