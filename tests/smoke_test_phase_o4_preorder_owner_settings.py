"""
RECEPTRA — PHASE O4 スモークテスト（Pre-Order Owner Settings & Product Rules）

FINAL PRINCIPLE（本テストが最優先で守ることを検証する）:
RECEPTRAは「大きな注文だから断る」のではなく、安全に自動確定できる注文は
すぐ確定し、判断できない注文は店舗確認へ回す。この判断基準（何個まで・
何時間前まで）は店舗オーナー自身がPreOrderProductとして設定でき、その設定を
根拠にBackendのdetermine_pre_order_confirmation()だけが判断する。

本フェーズで新設したもの:
- app/models/pre_order.py: PreOrderProduct（商品マスター。shop_id+nameで
  一意、auto_confirm_max_quantity/minimum_lead_time_minutesはNULL=「自動確定
  しない」という安全側デフォルト）。
- app/models/shop.py: Shop.pre_order_enabled（デフォルトFalse）。
- app/routers/pre_order_products.py: Owner認証済みCRUD
  （GET/POST /api/v1/shops/{shop_id}/pre-order-products、
  PATCH/DELETE .../pre-order-products/{id}）。
- app/schemas/shop.py: ShopUpdateRequest/ShopResponse/ShopPublicResponseへ
  pre_order_enabledを追加（reservations_enabled/staff_schedule_enabledと
  同じbulk PATCH /api/v1/shops/{shop_id}経由の更新規約）。
- app/services/pre_orders.py: determine_pre_order_confirmation()を
  「常にowner_confirmation_required」から実ルールエンジンへ拡張。
  create_public_pre_order()にshop.pre_order_enabledチェックを追加
  （false時は400 pre_order_not_enabled）。

検証項目（仕様書Section39に対応）:
GATE.   Shop.pre_order_enabledのデフォルトFalseと、false時の事前注文拒否
CRUD.   商品のcreate/update/deactivate/delete・tenant分離
FIELD.  price Integer/nullable、quantity上限NULL、lead time NULL
AUTO.   max=12 qty=12 confirmed / qty=13 owner_confirmation_required
LEAD.   lead time充足 confirmed / 不足 owner_confirmation_required
UNKNOWN 商品マスターに無い商品名 → owner_confirmation_required（rejectしない）
INACTIVE 無効化された商品 → owner_confirmation_required
MULTI.  複数商品全て条件クリア→confirmed／1つでも条件外→owner_confirmation_required
LARGE.  上限超過の大口注文もreject せず owner_confirmation_required
NOTIF.  confirmed時はNORMAL優先度、owner_confirmation_required時はACTION_REQUIRED
REG.    O3の冪等性・O1 UI・FAST TURNが無傷であることの確認

実行: python3 tests/smoke_test_phase_o4_preorder_owner_settings.py
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(
    tempfile.mkdtemp(), "smoke_o4_preorder_owner_settings.db"
)
os.environ["SECRET_KEY"] = "smoke-test-secret-key-o4-preorder-owner-settings"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")
os.environ["PRE_ORDER_PUBLIC_CREATE_ENABLED"] = "true"

import httpx  # noqa: E402

JST = timezone(timedelta(hours=9))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _future_pickup(hours: float = 3) -> str:
    return (datetime.now(JST).replace(tzinfo=None) + timedelta(hours=hours)).isoformat()


def _reset_public_create_rate_limit(shop_id: str) -> None:
    """app/routers/pre_orders.pyのPublic Create APIレート制限
    （プロセス内メモリ・shop_idキー、60秒あたり最大10リクエスト）は、
    本番の不正利用対策としては正しい挙動だが、本テストファイルは
    同一shop_idに対して短時間で10件を超えるPublic Create呼び出しを
    行う（AUTO/LEAD/MULTI/LARGE/NOTIFの境界値検証のため）。これは
    製品側のバグではなくテスト側の呼び出し密度の問題なので、各検証
    セクションの直前でこのテスト専用shopのバケットだけをクリアする
    （本番のレート制限ロジック自体は一切変更しない）。"""
    from app.routers.pre_orders import _public_pre_order_create_requests
    _public_pre_order_create_requests.pop(shop_id, None)


def _test_source_level_protection():
    shop_manage_html = open(
        os.path.join(REPO_ROOT, "frontend", "public", "shop-manage.html"), encoding="utf-8"
    ).read()
    assert "const INDUSTRY_UI_CONFIG" in shop_manage_html
    print("REGRESSION-0. shop-manage.htmlのO1 INDUSTRY_UI_CONFIG層は無傷: OK")

    engine_js = open(
        os.path.join(REPO_ROOT, "frontend", "public", "js", "realtime-voice-engine.js"), encoding="utf-8"
    ).read()
    assert "toolContinuationTraceActive" in engine_js
    assert engine_js.count("dc.send(JSON.stringify(") == 8
    print("REGRESSION-0b. FAST TURN 3.6B T0-T10 trace・Tool数は無傷: OK")

    assert 'id="pre-order-enabled-toggle"' in shop_manage_html
    assert 'id="pre-order-products-section"' in shop_manage_html
    assert 'id="pre-order-product-form"' in shop_manage_html
    assert "pre_order_enabled: nextValue" in shop_manage_html
    print("UI-0. shop-manage.htmlにPHASE O4の事前注文Owner UI（トグル・商品CRUD）が追加されている: OK")


async def main():
    from app.main import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # ===== セットアップ =====
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-o4-a@example.com", "password": "password123",
                "display_name": "PHASE O4テストオーナーA",
            })
            assert r.status_code in (200, 201), f"register A failed: {r.status_code} {r.text}"
            owner_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "PHASE O4テストベーカリー", "category": "その他", "address": "東京都渋谷区1-1-1",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create shop_a failed: {r.status_code} {r.text}"
            shop_a = r.json()["shop_id"]

            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-o4-b@example.com", "password": "password123",
                "display_name": "PHASE O4テストオーナーB",
            })
            assert r.status_code in (200, 201), f"register B failed: {r.status_code} {r.text}"
            owner_b = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "PHASE O4テスト別店舗", "category": "その他", "address": "東京都新宿区2-2-2",
            }, headers=owner_b)
            assert r.status_code in (200, 201), f"create shop_b failed: {r.status_code} {r.text}"
            shop_b = r.json()["shop_id"]
            print("SETUP. Tenant A/Bの店舗を作成: OK")

            # ============================================================
            # GATE: Shop.pre_order_enabledのデフォルトFalse
            # ============================================================
            r = await client.get(f"/api/v1/shops/{shop_a}")
            assert r.status_code == 200
            assert r.json()["pre_order_enabled"] is False, "新規店舗のpre_order_enabledはデフォルトFalseであるべき"
            print("GATE-0. 新規店舗のpre_order_enabledはデフォルトFalse: OK")

            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "ゲート閉鎖テスト", "customer_phone": "090-0000-0000",
                "pickup_at": _future_pickup(), "items": [{"product_name": "テスト商品", "quantity": 1}],
            })
            assert r.status_code == 400, f"pre_order_enabled=false時は400のはず: {r.status_code} {r.text}"
            assert r.json()["detail"] == "この店舗は現在事前注文を受け付けていません"
            print("GATE-1. pre_order_enabled=falseの店舗はPublic Create APIで400拒否される: OK")

            r = await client.patch(f"/api/v1/shops/{shop_a}", json={"pre_order_enabled": True}, headers=owner_a)
            assert r.status_code == 200 and r.json()["pre_order_enabled"] is True
            print("GATE-2. オーナーがpre_order_enabledをONにできる（bulk PATCH /shops/{id}経由）: OK")

            # ============================================================
            # CRUD: 商品のcreate/update/deactivate/delete・tenant分離
            # ============================================================
            r = await client.post(f"/api/v1/shops/{shop_a}/pre-order-products", json={
                "shop_id": shop_a, "name": "ドーナツ", "price": 150,
                "auto_confirm_max_quantity": 12, "minimum_lead_time_minutes": 120,
            }, headers=owner_a)
            assert r.status_code == 200, f"商品作成に失敗: {r.status_code} {r.text}"
            donut = r.json()
            assert donut["is_active"] is True
            assert donut["price"] == 150
            print("CRUD-1. 商品作成（ドーナツ, max=12, lead=120分）: OK")

            # tenant分離: owner_bはshop_aの商品一覧/作成/更新/削除ができない
            r = await client.get(f"/api/v1/shops/{shop_a}/pre-order-products", headers=owner_b)
            assert r.status_code == 403
            r = await client.post(f"/api/v1/shops/{shop_a}/pre-order-products", json={
                "shop_id": shop_a, "name": "不正商品",
            }, headers=owner_b)
            assert r.status_code == 403
            r = await client.patch(
                f"/api/v1/shops/{shop_a}/pre-order-products/{donut['id']}", json={"price": 1}, headers=owner_b
            )
            assert r.status_code == 403
            r = await client.delete(f"/api/v1/shops/{shop_a}/pre-order-products/{donut['id']}", headers=owner_b)
            assert r.status_code == 403
            print("CRUD-2. 他tenant(B)は商品のCRUDに一切アクセスできない: OK")

            # 同名商品の重複作成は409
            r = await client.post(f"/api/v1/shops/{shop_a}/pre-order-products", json={
                "shop_id": shop_a, "name": "ドーナツ",
            }, headers=owner_a)
            assert r.status_code == 409, f"同名商品は409のはず: {r.status_code}"
            print("CRUD-3. 同一店舗内で同名商品の重複作成は409で拒否される: OK")

            # FIELD: priceはInteger/nullable、上限/lead timeは省略可能(NULL)
            r = await client.post(f"/api/v1/shops/{shop_a}/pre-order-products", json={
                "shop_id": shop_a, "name": "特製オードブル",
            }, headers=owner_a)
            assert r.status_code == 200
            unconfigured = r.json()
            assert unconfigured["price"] is None
            assert unconfigured["auto_confirm_max_quantity"] is None
            assert unconfigured["minimum_lead_time_minutes"] is None
            print("FIELD-1. price/auto_confirm_max_quantity/minimum_lead_time_minutesは省略時NULL: OK")

            # update: 一部フィールドのみ変更（exclude_unset）
            r = await client.patch(
                f"/api/v1/shops/{shop_a}/pre-order-products/{unconfigured['id']}",
                json={"price": 500}, headers=owner_a,
            )
            assert r.status_code == 200 and r.json()["price"] == 500
            assert r.json()["name"] == "特製オードブル", "指定しなかったフィールドは変更されないはず"
            print("CRUD-4. PATCH部分更新は指定フィールドのみ変更する: OK")

            # deactivate
            r = await client.patch(
                f"/api/v1/shops/{shop_a}/pre-order-products/{unconfigured['id']}",
                json={"is_active": False}, headers=owner_a,
            )
            assert r.status_code == 200 and r.json()["is_active"] is False
            print("CRUD-5. is_active=falseで商品を無効化できる: OK")

            # delete（ハードデリート）
            r = await client.delete(
                f"/api/v1/shops/{shop_a}/pre-order-products/{unconfigured['id']}", headers=owner_a
            )
            assert r.status_code == 200
            r = await client.get(f"/api/v1/shops/{shop_a}/pre-order-products", headers=owner_a)
            ids = [p["id"] for p in r.json()]
            assert unconfigured["id"] not in ids, "削除した商品は一覧から消えるはず"
            print("CRUD-6. DELETEで商品がハードデリートされる: OK")

            # ============================================================
            # AUTO: max=12 qty=12 confirmed / qty=13 owner_confirmation_required
            # ============================================================
            _reset_public_create_rate_limit(shop_a)
            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "自動確定テスト12個", "customer_phone": "090-1111-0001",
                "pickup_at": _future_pickup(hours=3), "items": [{"product_name": "ドーナツ", "quantity": 12}],
            })
            assert r.status_code == 200, f"AUTO qty=12 failed: {r.status_code} {r.text}"
            assert r.json()["confirmation_status"] == "confirmed", "max=12・qty=12・lead十分 → confirmedのはず"
            print("AUTO-1. max=12 qty=12（境界値, lead十分）→ confirmed: OK")

            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "自動確定テスト13個", "customer_phone": "090-1111-0002",
                "pickup_at": _future_pickup(hours=3), "items": [{"product_name": "ドーナツ", "quantity": 13}],
            })
            assert r.status_code == 200
            assert r.json()["confirmation_status"] == "owner_confirmation_required", "qty=13（上限超過） → owner確認のはず"
            print("AUTO-2. max=12 qty=13（上限超過）→ owner_confirmation_required（rejectされない）: OK")

            # ============================================================
            # LEAD: lead time充足/不足
            # ============================================================
            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "リードタイム充足", "customer_phone": "090-1111-0003",
                "pickup_at": _future_pickup(hours=2.5), "items": [{"product_name": "ドーナツ", "quantity": 5}],
            })
            assert r.status_code == 200
            assert r.json()["confirmation_status"] == "confirmed", "lead=120分必要・150分後受取 → confirmedのはず"
            print("LEAD-1. 必要リードタイム(120分)を満たす(150分後受取) → confirmed: OK")

            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "リードタイム不足", "customer_phone": "090-1111-0004",
                "pickup_at": _future_pickup(hours=0.5), "items": [{"product_name": "ドーナツ", "quantity": 5}],
            })
            assert r.status_code == 200
            assert r.json()["confirmation_status"] == "owner_confirmation_required", "30分後受取（lead不足） → owner確認のはず"
            print("LEAD-2. 必要リードタイム(120分)を満たさない(30分後受取) → owner_confirmation_required: OK")

            # ============================================================
            # UNKNOWN / INACTIVE
            # ============================================================
            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "未知の商品", "customer_phone": "090-1111-0005",
                "pickup_at": _future_pickup(hours=3), "items": [{"product_name": "特製オードブル（存在しない）", "quantity": 1}],
            })
            assert r.status_code == 200, "未知の商品はrejectされず受け付けられるはず"
            assert r.json()["confirmation_status"] == "owner_confirmation_required"
            print("UNKNOWN-1. 商品マスターに無い商品名 → reject せず owner_confirmation_required: OK")

            r = await client.post(f"/api/v1/shops/{shop_a}/pre-order-products", json={
                "shop_id": shop_a, "name": "無効化商品", "auto_confirm_max_quantity": 100,
                "minimum_lead_time_minutes": 0,
            }, headers=owner_a)
            inactive_product = r.json()
            r = await client.patch(
                f"/api/v1/shops/{shop_a}/pre-order-products/{inactive_product['id']}",
                json={"is_active": False}, headers=owner_a,
            )
            assert r.status_code == 200
            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "無効化商品注文", "customer_phone": "090-1111-0006",
                "pickup_at": _future_pickup(hours=3), "items": [{"product_name": "無効化商品", "quantity": 1}],
            })
            assert r.status_code == 200
            assert r.json()["confirmation_status"] == "owner_confirmation_required", "無効化された商品は未知の商品と同様に扱われるべき"
            print("INACTIVE-1. is_active=falseの商品は未知の商品と同様owner_confirmation_requiredになる: OK")

            # ============================================================
            # MULTI: 複数商品
            # ============================================================
            _reset_public_create_rate_limit(shop_a)
            r = await client.post(f"/api/v1/shops/{shop_a}/pre-order-products", json={
                "shop_id": shop_a, "name": "食パン", "auto_confirm_max_quantity": 20,
                "minimum_lead_time_minutes": 60,
            }, headers=owner_a)
            assert r.status_code == 200

            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "複数商品全て条件クリア", "customer_phone": "090-1111-0007",
                "pickup_at": _future_pickup(hours=3),
                "items": [
                    {"product_name": "ドーナツ", "quantity": 10},
                    {"product_name": "食パン", "quantity": 15},
                ],
            })
            assert r.status_code == 200
            assert r.json()["confirmation_status"] == "confirmed", "全item条件クリア → confirmedのはず"
            print("MULTI-1. 複数商品全てが条件を満たす → confirmed: OK")

            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "複数商品一部条件外", "customer_phone": "090-1111-0008",
                "pickup_at": _future_pickup(hours=3),
                "items": [
                    {"product_name": "ドーナツ", "quantity": 10},   # OK
                    {"product_name": "食パン", "quantity": 25},     # NG (max=20超過)
                ],
            })
            assert r.status_code == 200
            assert r.json()["confirmation_status"] == "owner_confirmation_required", (
                "1品でも条件外なら注文全体がowner_confirmation_requiredのはず（一部だけconfirmedにしない）"
            )
            print("MULTI-2. 複数商品のうち1つでも条件外 → 注文全体がowner_confirmation_required: OK")

            # ============================================================
            # LARGE: 上限超過の大口注文もrejectしない（既にAUTO-2で確認済みだが、
            # 明確に「400等でrejectされていないこと」を再確認する）
            # ============================================================
            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "大口注文", "customer_phone": "090-1111-0009",
                "pickup_at": _future_pickup(hours=3), "items": [{"product_name": "ドーナツ", "quantity": 100}],
            })
            assert r.status_code == 200, "大口注文であってもreject(4xx)されてはいけない"
            assert r.json()["confirmation_status"] == "owner_confirmation_required"
            print("LARGE-1. 上限を大幅に超える注文(100個)もreject されず owner_confirmation_requiredで受理される: OK")

            # ============================================================
            # NOTIF: confirmed時はNORMAL優先度、owner確認時はACTION_REQUIRED
            # ============================================================
            _reset_public_create_rate_limit(shop_a)
            confirmed_id = None
            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "通知優先度テスト確定", "customer_phone": "090-2222-0001",
                "pickup_at": _future_pickup(hours=3), "items": [{"product_name": "ドーナツ", "quantity": 3}],
            })
            assert r.status_code == 200 and r.json()["confirmation_status"] == "confirmed"
            confirmed_id = r.json()["pre_order_id"]

            r = await client.get(f"/api/v1/shops/{shop_a}/notifications?limit=200", headers=owner_a)
            events = r.json()["items"]
            confirmed_event = next(e for e in events if e["related_entity_id"] == confirmed_id)
            assert confirmed_event["priority"] == "NORMAL", "自動確定時の通知はNORMAL優先度のはず"
            print("NOTIF-1. 自動確定(confirmed)時の通知priorityはNORMAL: OK")

            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "通知優先度テスト要確認", "customer_phone": "090-2222-0002",
                "pickup_at": _future_pickup(hours=3), "items": [{"product_name": "未登録商品X", "quantity": 1}],
            })
            assert r.status_code == 200 and r.json()["confirmation_status"] == "owner_confirmation_required"
            required_id = r.json()["pre_order_id"]

            r = await client.get(f"/api/v1/shops/{shop_a}/notifications?limit=200", headers=owner_a)
            events = r.json()["items"]
            required_event = next(e for e in events if e["related_entity_id"] == required_id)
            assert required_event["priority"] == "ACTION_REQUIRED", "owner確認時の通知はACTION_REQUIRED優先度のはず"
            print("NOTIF-2. owner_confirmation_required時の通知priorityはACTION_REQUIRED: OK")

            # ============================================================
            # REG: O3の冪等性が無傷であることの確認
            # ============================================================
            _reset_public_create_rate_limit(shop_a)
            dup_key = "o4-regression-idempotency-key"
            payload = {
                "shop_id": shop_a, "customer_name": "O3冪等性回帰確認", "customer_phone": "090-3333-0001",
                "pickup_at": _future_pickup(hours=3), "items": [{"product_name": "ドーナツ", "quantity": 1}],
                "idempotency_key": dup_key,
            }
            r1 = await client.post("/api/v1/pre-orders/create", json=payload)
            r2 = await client.post("/api/v1/pre-orders/create", json=payload)
            assert r1.status_code == 200 and r2.status_code == 200
            assert r1.json()["pre_order_id"] == r2.json()["pre_order_id"], "O3のidempotency_key機構が無傷であるはず"
            print("REG-1. O3のidempotency_key冪等性はO4のルールエンジン変更後も無傷: OK")

            print("\n✅ PHASE O4 スモークテスト: 全項目 OK")


if __name__ == "__main__":
    _test_source_level_protection()
    asyncio.run(main())
