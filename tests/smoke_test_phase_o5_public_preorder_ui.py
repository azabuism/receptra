"""
RECEPTRA — PHASE O5 スモークテスト（Public Web Pre-Order UI & 厳密な商品紐付け）

FINAL PRINCIPLE（本テストが最優先で守ることを検証する）:
RECEPTRAの事前注文は「自動確定できない注文を拒否するシステム」ではない。
自動確定できるものは即確定し、それ以外は店舗確認へ安全に渡す。大量注文・
リードタイム不足・複数商品の一部条件外を理由に注文自体を拒否してはならない。
一方、偽物product_id・他店舗product_id・inactive商品・disabled shopなど、
注文対象そのものが無効なケースは安全に拒否する。この区別をBackend/
Frontend/Testsの全てで維持する。

本フェーズで新設・変更したもの:
- app/models/pre_order.py: PreOrderItem.product_id追加
  （nullable・ondelete指定なし・relationship無し。後方互換のためのみ）
- app/schemas/pre_order.py: PreOrderItemPublicCreate.product_id追加
  （product_id指定時はproduct_nameへのフォールバックを一切行わない）
- app/services/pre_orders.py: _resolve_and_validate_product_id_items()新設
  （fake/cross-tenant/inactiveなproduct_idを安全にreject）、
  determine_pre_order_confirmation()をproduct_id優先の2経路対応へ拡張
- app/routers/pre_order_products.py: GET .../pre-order-products/public
  （Public商品一覧。Owner専用ルールは非公開）
- app/schemas/pre_order_product.py: PreOrderProductPublicResponse新設
- app/main.py: pre_order_items.product_idカラム/インデックスのALTER追加
- frontend/public/shop.html: Public Pre-Order UI（#pre-order-card）追加

検証項目（仕様書Section26に対応）:
PRODUCT-ID. valid/fake/cross-tenant/inactive product_id、product_name
            へのフォールバック禁止
SNAPSHOT.   注文後の商品名/価格変更・削除が過去注文の表示に影響しない
PRICE.      price>0/price==0/price==NULL・価格改ざん不可
GATE.       pre_order_enabled=False・存在しない店舗・
            PRE_ORDER_PUBLIC_CREATE_ENABLED=False
AUTO/LARGE/LEAD/MULTI. product_id経路でのルールエンジンの継続動作
NOTIF.      confirmed→NORMAL、owner_confirmation_required→ACTION_REQUIRED
UI.         Public Pre-Order UIの主要要素の存在（ソースレベル確認）

実行: python3 tests/smoke_test_phase_o5_public_preorder_ui.py
"""

import asyncio
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(
    tempfile.mkdtemp(), "smoke_o5_public_preorder_ui.db"
)
os.environ["SECRET_KEY"] = "smoke-test-secret-key-o5-public-preorder-ui"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")
os.environ["PRE_ORDER_PUBLIC_CREATE_ENABLED"] = "true"

import httpx  # noqa: E402

JST = timezone(timedelta(hours=9))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _future_pickup(hours: float = 3) -> str:
    return (datetime.now(JST).replace(tzinfo=None) + timedelta(hours=hours)).isoformat()


def _reset_public_create_rate_limit(shop_id: str) -> None:
    """app/routers/pre_orders.pyのPublic Create APIレート制限（プロセス内メモリ・
    shop_idキー、60秒あたり最大10リクエスト）は、本テストが同一shop_idへ短時間で
    多数のPublic Create呼び出しを行うために各検証セクション前でクリアする
    （tests/smoke_test_phase_o4_preorder_owner_settings.pyと全く同じ規約。
    本番のレート制限ロジック自体は一切変更しない）。"""
    from app.routers.pre_orders import _public_pre_order_create_requests
    _public_pre_order_create_requests.pop(shop_id, None)


def _test_source_level_protection():
    shop_manage_html = open(
        os.path.join(REPO_ROOT, "frontend", "public", "shop-manage.html"), encoding="utf-8"
    ).read()
    assert "const INDUSTRY_UI_CONFIG" in shop_manage_html
    assert 'id="pre-order-enabled-toggle"' in shop_manage_html
    assert 'id="pre-order-product-form"' in shop_manage_html
    print("REGRESSION-0. shop-manage.htmlのO1 UI層・O4事前注文Owner UIは無傷: OK")

    engine_js = open(
        os.path.join(REPO_ROOT, "frontend", "public", "js", "realtime-voice-engine.js"), encoding="utf-8"
    ).read()
    assert "toolContinuationTraceActive" in engine_js
    assert engine_js.count("dc.send(JSON.stringify(") == 8
    print("REGRESSION-0b. FAST TURN 3.6B T0-T10 trace・Tool数は無傷: OK")

    shop_html = open(os.path.join(REPO_ROOT, "frontend", "public", "shop.html"), encoding="utf-8").read()

    assert 'id="pre-order-card"' in shop_html
    assert 'id="pre-order-products-list"' in shop_html
    print("UI-0. shop.htmlにPHASE O5のPublic Pre-Order UIカードが追加されている: OK")

    assert 'class="pre-order-qty-input"' in shop_html
    qty_input_line = next(
        line for line in shop_html.splitlines() if 'class="pre-order-qty-input"' in line
    )
    # Section9: 数量入力欄にmax属性を絶対に付けない（自動確定上限を理由に
    # 入力そのものを不可能にしない）。
    assert "max=" not in qty_input_line, "数量inputにmax属性が付いている（Section9違反）"
    assert 'min="0"' in qty_input_line
    print("UI-1. 数量入力欄にmax属性が無い（上限超過の注文自体は入力可能）: OK")

    assert 'id="pre-order-next-btn" disabled' in shop_html
    print("UI-2. 最低1商品選択するまで次へ進めない（初期状態disabled）: OK")

    assert 'id="pre-order-pickup-date"' in shop_html and 'id="pre-order-pickup-time"' in shop_html
    print("UI-3. 受取希望日時の入力欄が存在する: OK")

    assert 'id="pre-order-name"' in shop_html and 'id="pre-order-phone"' in shop_html
    print("UI-4. 氏名・電話番号の入力欄が存在する: OK")

    assert 'id="pre-order-confirm-wrap"' in shop_html and 'id="pre-order-confirm-summary"' in shop_html
    print("UI-5. 送信前の注文内容確認画面が存在する: OK")

    assert "btn.disabled = true;" in shop_html and 'pre-order-submit-btn' in shop_html
    print("UI-6. 送信ボタンの二重送信防止（disabled化）が実装されている: OK")

    assert "confirmation_status === 'confirmed'" in shop_html
    assert "'success'" in shop_html
    assert "'pending'" in shop_html
    assert "店舗で内容を確認後、確定となります" in shop_html
    print("UI-7. confirmed/owner_confirmation_requiredが別クラス・別文言で明確に区別されている: OK")

    assert ".booking-msg.pending" in shop_html
    print("UI-8. owner_confirmation_required専用のCSS（.booking-msg.pending）が存在する: OK")

    assert "if (shop.pre_order_enabled) {" in shop_html
    assert "loadPreOrderProducts(shop.id)" in shop_html
    print("UI-9. shop.pre_order_enabled=falseの店舗ではPre-Order UIを読み込まない: OK")

    assert "商品情報が更新されました" in shop_html
    print("UI-10. 商品情報が古い場合の再読み込み案内文言が存在する: OK")


async def main():
    from app.main import app, lifespan
    from app.config import get_settings

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # ===== セットアップ =====
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-o5-a@example.com", "password": "password123",
                "display_name": "PHASE O5テストオーナーA",
            })
            assert r.status_code in (200, 201), f"register A failed: {r.status_code} {r.text}"
            owner_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "PHASE O5テストベーカリー", "category": "その他", "address": "東京都渋谷区1-1-1",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create shop_a failed: {r.status_code} {r.text}"
            shop_a = r.json()["shop_id"]

            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-o5-b@example.com", "password": "password123",
                "display_name": "PHASE O5テストオーナーB",
            })
            assert r.status_code in (200, 201), f"register B failed: {r.status_code} {r.text}"
            owner_b = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "PHASE O5テスト別店舗", "category": "その他", "address": "東京都新宿区2-2-2",
            }, headers=owner_b)
            assert r.status_code in (200, 201), f"create shop_b failed: {r.status_code} {r.text}"
            shop_b = r.json()["shop_id"]
            print("SETUP. Tenant A/Bの店舗を作成: OK")

            # ============================================================
            # GATE: pre_order_enabled=False / 存在しない店舗
            # ============================================================
            r = await client.get(f"/api/v1/shops/{shop_a}/pre-order-products/public")
            assert r.status_code == 404, f"pre_order_enabled=false時は404のはず: {r.status_code}"
            print("GATE-1. pre_order_enabled=falseの店舗はPublic商品一覧APIで404: OK")

            r = await client.get(f"/api/v1/shops/{uuid.uuid4()}/pre-order-products/public")
            assert r.status_code == 404
            print("GATE-2. 存在しない店舗も同じ404（店舗の存在有無を区別しない）: OK")

            r = await client.patch(f"/api/v1/shops/{shop_a}", json={"pre_order_enabled": True}, headers=owner_a)
            assert r.status_code == 200 and r.json()["pre_order_enabled"] is True
            print("GATE-3. オーナーがpre_order_enabledをONにできる: OK")

            # ===== 商品マスターのセットアップ =====
            async def _create_product(shop_id, headers, **fields):
                payload = {"shop_id": shop_id, **fields}
                resp = await client.post(f"/api/v1/shops/{shop_id}/pre-order-products", json=payload, headers=headers)
                assert resp.status_code == 200, f"商品作成に失敗: {resp.status_code} {resp.text}"
                return resp.json()

            donut = await _create_product(
                shop_a, owner_a, name="ドーナツ", price=150,
                auto_confirm_max_quantity=12, minimum_lead_time_minutes=120,
            )
            bread = await _create_product(
                shop_a, owner_a, name="食パン", price=200,
                auto_confirm_max_quantity=20, minimum_lead_time_minutes=60,
            )
            free_item = await _create_product(shop_a, owner_a, name="無料引換券", price=0)
            unset_price_item = await _create_product(shop_a, owner_a, name="特製ケーキ")
            same_name_product = await _create_product(shop_a, owner_a, name="同名チェック商品", price=300)
            snap_product = await _create_product(shop_a, owner_a, name="スナップショット商品", price=500)
            other_shop_product = await _create_product(shop_b, owner_b, name="別店舗の商品", price=100)
            print("SETUP-2. 商品マスター一式を作成: OK")

            # ============================================================
            # GATE-4: Public商品一覧はis_active=Trueのみ・内部フィールド非公開
            # ============================================================
            r = await client.get(f"/api/v1/shops/{shop_a}/pre-order-products/public")
            assert r.status_code == 200, f"Public商品一覧の取得に失敗: {r.status_code} {r.text}"
            public_products = r.json()
            names = {p["name"] for p in public_products}
            assert "ドーナツ" in names and "別店舗の商品" not in names
            sample = next(p for p in public_products if p["name"] == "ドーナツ")
            assert set(sample.keys()) == {"id", "name", "description", "price", "display_order"}, (
                f"Public商品一覧に内部フィールドが混入している: {sample.keys()}"
            )
            print("GATE-4. Public商品一覧は自店舗のis_active商品のみ・最小フィールドのみ返す: OK")

            inactive_for_list = await _create_product(shop_a, owner_a, name="一覧非表示商品")
            r = await client.patch(
                f"/api/v1/shops/{shop_a}/pre-order-products/{inactive_for_list['id']}",
                json={"is_active": False}, headers=owner_a,
            )
            assert r.status_code == 200
            r = await client.get(f"/api/v1/shops/{shop_a}/pre-order-products/public")
            listed_ids = {p["id"] for p in r.json()}
            assert inactive_for_list["id"] not in listed_ids
            print("GATE-5. is_active=falseの商品はPublic商品一覧に表示されない: OK")

            # ============================================================
            # GATE-6: グローバルfeature gate（O3/O4から継続）
            # ============================================================
            settings = get_settings()
            settings.PRE_ORDER_PUBLIC_CREATE_ENABLED = False
            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "ゲート閉鎖テスト", "customer_phone": "090-0000-0000",
                "pickup_at": _future_pickup(), "items": [{"product_id": donut["id"], "product_name": donut["name"], "quantity": 1}],
            })
            assert r.status_code == 404, f"gate closed should be 404: {r.status_code} {r.text}"
            settings.PRE_ORDER_PUBLIC_CREATE_ENABLED = True
            print("GATE-6. PRE_ORDER_PUBLIC_CREATE_ENABLED=False時の既存gateはO5でも維持される: OK")

            # ============================================================
            # PRODUCT-ID: valid / fake / cross-tenant / inactive
            # ============================================================
            _reset_public_create_rate_limit(shop_a)
            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "有効なproduct_id", "customer_phone": "090-1000-0001",
                "pickup_at": _future_pickup(hours=3),
                "items": [{"product_id": donut["id"], "product_name": donut["name"], "quantity": 12}],
            })
            assert r.status_code == 200, f"valid product_id failed: {r.status_code} {r.text}"
            body = r.json()
            assert body["confirmation_status"] == "confirmed"
            assert body["items"][0]["product_id"] == donut["id"]
            assert body["items"][0]["product_name"] == "ドーナツ"
            assert body["items"][0]["unit_price"] == 150
            print("PRODUCT-ID-1. 有効なproduct_idでの注文が成立し、product_id/名前/単価が正しくsnapshotされる: OK")

            fake_id = str(uuid.uuid4())
            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "偽物product_id", "customer_phone": "090-1000-0002",
                "pickup_at": _future_pickup(hours=3),
                "items": [{"product_id": fake_id, "product_name": "ドーナツ", "quantity": 1}],
            })
            assert r.status_code == 404, f"fake product_id should be 404: {r.status_code} {r.text}"
            fake_detail = r.json()["detail"]
            print("PRODUCT-ID-2. 存在しないproduct_idは404で拒否される: OK")

            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "他店舗product_id", "customer_phone": "090-1000-0003",
                "pickup_at": _future_pickup(hours=3),
                "items": [{"product_id": other_shop_product["id"], "product_name": other_shop_product["name"], "quantity": 1}],
            })
            assert r.status_code == 404, f"cross-tenant product_id should be 404: {r.status_code} {r.text}"
            assert r.json()["detail"] == fake_detail, "存在しない場合と他店舗の場合で異なるメッセージを返すと、商品IDの存在をtenant跨ぎで漏らしてしまう"
            print("PRODUCT-ID-3. 他店舗のproduct_idは404で拒否される（存在しない場合と同一メッセージ・tenant漏洩なし）: OK")

            inactive_product = await _create_product(shop_a, owner_a, name="無効化商品O5")
            r = await client.patch(
                f"/api/v1/shops/{shop_a}/pre-order-products/{inactive_product['id']}",
                json={"is_active": False}, headers=owner_a,
            )
            assert r.status_code == 200
            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "inactive商品", "customer_phone": "090-1000-0004",
                "pickup_at": _future_pickup(hours=3),
                "items": [{"product_id": inactive_product["id"], "product_name": inactive_product["name"], "quantity": 1}],
            })
            assert r.status_code == 400, f"inactive product_id should be 400: {r.status_code} {r.text}"
            assert r.json()["detail"] != fake_detail, "inactiveと存在しない場合は別の理由として区別されるべき"
            print("PRODUCT-ID-4. inactiveなproduct_idは400で拒否される（存在しない場合とは別メッセージ）: OK")

            # ★最重要: product_idが見つからない場合にproduct_nameへフォールバックしない
            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "フォールバック禁止テスト", "customer_phone": "090-1000-0005",
                "pickup_at": _future_pickup(hours=3),
                "items": [{"product_id": fake_id, "product_name": same_name_product["name"], "quantity": 1}],
            })
            assert r.status_code == 404, (
                "product_idが不正な場合、実在するproduct_nameと一致していてもフォールバックしてはならない"
            )
            print("PRODUCT-ID-5. product_id不正時、一致するproduct_nameが実在してもフォールバックせず拒否される: OK")

            # ============================================================
            # PRICE: price>0 / price==0 / price==NULL / 価格改ざん不可
            # ============================================================
            _reset_public_create_rate_limit(shop_a)
            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "価格0円商品", "customer_phone": "090-2000-0001",
                "pickup_at": _future_pickup(hours=3),
                "items": [{"product_id": free_item["id"], "product_name": free_item["name"], "quantity": 1}],
            })
            assert r.status_code == 200
            assert r.json()["items"][0]["unit_price"] == 0, "price=0はNoneに丸められず0のまま返るべき"
            print("PRICE-1. price=0の商品はunit_price=0としてsnapshotされる（NULLと区別）: OK")

            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "価格未設定商品", "customer_phone": "090-2000-0002",
                "pickup_at": _future_pickup(hours=3),
                "items": [{"product_id": unset_price_item["id"], "product_name": unset_price_item["name"], "quantity": 1}],
            })
            assert r.status_code == 200
            assert r.json()["items"][0]["unit_price"] is None, "price未設定商品はunit_price=Noneのまま返るべき"
            print("PRICE-2. price未設定の商品はunit_price=Noneのままsnapshotされる: OK")

            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "価格改ざん試行", "customer_phone": "090-2000-0003",
                "pickup_at": _future_pickup(hours=3),
                "items": [{"product_id": donut["id"], "product_name": donut["name"], "quantity": 1, "unit_price": 1}],
            })
            assert r.status_code == 422, "unit_priceフィールドはextra=forbidにより422で拒否されるはず"
            print("PRICE-3. itemにunit_priceを送ると422で拒否される（価格注入不可）: OK")

            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "商品名改ざん試行", "customer_phone": "090-2000-0004",
                "pickup_at": _future_pickup(hours=3),
                "items": [{"product_id": donut["id"], "product_name": "改ざんされた商品名", "quantity": 1}],
            })
            assert r.status_code == 200
            assert r.json()["items"][0]["product_name"] == "ドーナツ", (
                "product_id指定時、クライアントが送ったproduct_nameは無視され、正式名称で上書きされるべき"
            )
            print("PRICE-4. product_id指定時、クライアント送信のproduct_nameは無視され正式名称でsnapshotされる: OK")

            # ============================================================
            # SNAPSHOT: 商品の変更・削除が過去注文の表示に影響しない
            # ============================================================
            _reset_public_create_rate_limit(shop_a)
            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "スナップショットテスト", "customer_phone": "090-3000-0001",
                "pickup_at": _future_pickup(hours=3),
                "items": [{"product_id": snap_product["id"], "product_name": snap_product["name"], "quantity": 2}],
            })
            assert r.status_code == 200
            snap_pre_order_id = r.json()["pre_order_id"]
            assert r.json()["items"][0]["product_name"] == "スナップショット商品"
            assert r.json()["items"][0]["unit_price"] == 500

            r = await client.patch(
                f"/api/v1/shops/{shop_a}/pre-order-products/{snap_product['id']}",
                json={"name": "変更後の名前", "price": 999}, headers=owner_a,
            )
            assert r.status_code == 200

            r = await client.get(f"/api/v1/pre-orders/{snap_pre_order_id}", headers=owner_a)
            assert r.status_code == 200
            item = r.json()["items"][0]
            assert item["product_name"] == "スナップショット商品", "商品名変更後も過去注文のsnapshotは変化しないはず"
            assert item["unit_price"] == 500, "価格変更後も過去注文のsnapshotは変化しないはず"
            print("SNAPSHOT-1. 商品の名前/価格変更後も過去注文のsnapshot表示は変化しない: OK")

            r = await client.delete(f"/api/v1/shops/{shop_a}/pre-order-products/{snap_product['id']}", headers=owner_a)
            assert r.status_code == 200, "商品削除自体は成功するはず（CASCADE禁止＝過去注文には影響しないだけで、削除操作自体は可能）"

            r = await client.get(f"/api/v1/pre-orders/{snap_pre_order_id}", headers=owner_a)
            assert r.status_code == 200, "参照先商品が削除されても過去注文の取得はエラーにならないはず"
            item = r.json()["items"][0]
            assert item["product_name"] == "スナップショット商品"
            assert item["unit_price"] == 500
            assert item["quantity"] == 2
            print("SNAPSHOT-2. 商品削除後も過去注文（商品名/単価/数量）は一切変化せず取得できる（CASCADE削除されない）: OK")

            # ============================================================
            # AUTO/LARGE/LEAD/MULTI: product_id経路でのルールエンジン
            # ============================================================
            _reset_public_create_rate_limit(shop_a)
            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "AUTO-ID境界値", "customer_phone": "090-4000-0001",
                "pickup_at": _future_pickup(hours=3),
                "items": [{"product_id": donut["id"], "product_name": donut["name"], "quantity": 12}],
            })
            assert r.status_code == 200 and r.json()["confirmation_status"] == "confirmed"
            print("AUTO. product_id経路: max=12・qty=12・lead十分 → confirmed: OK")

            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "LARGE-ID大口注文", "customer_phone": "090-4000-0002",
                "pickup_at": _future_pickup(hours=3),
                "items": [{"product_id": donut["id"], "product_name": donut["name"], "quantity": 200}],
            })
            assert r.status_code == 200, "大口注文であってもreject(4xx)されてはいけない"
            assert r.json()["confirmation_status"] == "owner_confirmation_required"
            print("LARGE. product_id経路: 上限を大幅に超える注文(200個)もreject されず owner_confirmation_requiredで受理: OK")

            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "LEAD-IDリードタイム不足", "customer_phone": "090-4000-0003",
                "pickup_at": _future_pickup(hours=0.5),
                "items": [{"product_id": donut["id"], "product_name": donut["name"], "quantity": 3}],
            })
            assert r.status_code == 200
            assert r.json()["confirmation_status"] == "owner_confirmation_required"
            print("LEAD. product_id経路: リードタイム不足(30分後受取)もreject されず owner_confirmation_requiredで受理: OK")

            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "MULTI-ID一部条件外", "customer_phone": "090-4000-0004",
                "pickup_at": _future_pickup(hours=3),
                "items": [
                    {"product_id": donut["id"], "product_name": donut["name"], "quantity": 5},         # OK
                    {"product_id": bread["id"], "product_name": bread["name"], "quantity": 25},        # NG (max=20超過)
                ],
            })
            assert r.status_code == 200
            assert r.json()["confirmation_status"] == "owner_confirmation_required", (
                "複数商品のうち1つでも条件外なら注文全体がowner_confirmation_requiredのはず"
            )
            print("MULTI-ID-1. product_id経路のみの複数商品、1つでも条件外なら注文全体がowner_confirmation_required: OK")

            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "MULTI-ID混在経路", "customer_phone": "090-4000-0005",
                "pickup_at": _future_pickup(hours=3),
                "items": [
                    {"product_id": donut["id"], "product_name": donut["name"], "quantity": 5},  # product_id経路・条件クリア
                    {"product_name": "AI音声由来の未知商品"},  # product_name経路・未知商品
                ],
            })
            # product_nameのみのitemはquantity省略のためschema上422になる点に注意し、
            # quantityを補って再送する。
            assert r.status_code == 422
            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "MULTI-ID混在経路", "customer_phone": "090-4000-0005",
                "pickup_at": _future_pickup(hours=3),
                "items": [
                    {"product_id": donut["id"], "product_name": donut["name"], "quantity": 5},
                    {"product_name": "AI音声由来の未知商品", "quantity": 1},
                ],
            })
            assert r.status_code == 200
            assert r.json()["confirmation_status"] == "owner_confirmation_required", (
                "product_id経路とproduct_name経路が混在する注文でも、1つでも条件外なら全体がowner_confirmation_requiredのはず"
            )
            print("MULTI-ID-2. product_id経路とproduct_name経路が同一注文に混在しても、判定ロジックは正しく機能する: OK")

            # ============================================================
            # NOTIF: confirmed→NORMAL、owner_confirmation_required→ACTION_REQUIRED
            # ============================================================
            _reset_public_create_rate_limit(shop_a)
            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "NOTIF-ID確定", "customer_phone": "090-5000-0001",
                "pickup_at": _future_pickup(hours=3),
                "items": [{"product_id": donut["id"], "product_name": donut["name"], "quantity": 3}],
            })
            assert r.status_code == 200 and r.json()["confirmation_status"] == "confirmed"
            confirmed_id = r.json()["pre_order_id"]

            r = await client.get(f"/api/v1/shops/{shop_a}/notifications?limit=200", headers=owner_a)
            events = r.json()["items"]
            confirmed_event = next(e for e in events if e["related_entity_id"] == confirmed_id)
            assert confirmed_event["priority"] == "NORMAL"
            print("NOTIF-ID-1. product_id経路のconfirmed注文の通知priorityはNORMAL: OK")

            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "NOTIF-ID要確認", "customer_phone": "090-5000-0002",
                "pickup_at": _future_pickup(hours=3),
                "items": [{"product_id": donut["id"], "product_name": donut["name"], "quantity": 200}],
            })
            assert r.status_code == 200 and r.json()["confirmation_status"] == "owner_confirmation_required"
            required_id = r.json()["pre_order_id"]

            r = await client.get(f"/api/v1/shops/{shop_a}/notifications?limit=200", headers=owner_a)
            events = r.json()["items"]
            required_event = next(e for e in events if e["related_entity_id"] == required_id)
            assert required_event["priority"] == "ACTION_REQUIRED"
            print("NOTIF-ID-2. product_id経路のowner_confirmation_required注文の通知priorityはACTION_REQUIRED: OK")

            print("\n✅ PHASE O5 スモークテスト: 全項目 OK")


if __name__ == "__main__":
    _test_source_level_protection()
    asyncio.run(main())
