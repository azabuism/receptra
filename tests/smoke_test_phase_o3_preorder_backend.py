"""
RECEPTRA — PHASE O3 スモークテスト（Pre-Order Backend Acceptance Engine）

FINAL PRINCIPLE（本テストが最優先で守ることを検証する）:
RECEPTRAは「注文を聞いた」だけで「作れます」とは絶対に約束しない。
Product master・quantity threshold・lead time・inventory・shop settingsの
いずれもまだ存在しない（O3時点）ため、Public Create APIで作成される
PreOrderは常に confirmation_status = owner_confirmation_required で
あることを、本テストの中心的な検証項目とする。

本フェーズで新設したもの:
- app/services/pre_orders.py: create_public_pre_order() /
  determine_pre_order_confirmation()（常にowner_confirmation_requiredを
  返す）/ get_pre_order_by_idempotency_key()
- app/routers/pre_orders.py: POST /api/v1/pre-orders/create（Public、
  グローバルfeature gate + rate limit付き）、GET /api/v1/pre-orders/{id}
  （Owner詳細）、PATCH /api/v1/pre-orders/{id}/confirmation（Owner確認/
  却下）、GET /api/v1/shops/{shop_id}/pre-orders（Owner一覧）
- app/services/owner_notifications.py: notify_pre_order_created()
- app/config.py: PRE_ORDER_PUBLIC_CREATE_ENABLED（デフォルトFalse）
- app/models/pre_order.py: idempotency_key列追加、confirmation_status
  のDBデフォルトをconfirmed→owner_confirmation_requiredへ変更
- app/main.py: pre_orders.idempotency_keyカラム/一意インデックスの
  ALTER追加、pre_orders_router/shop_pre_orders_routerの登録

検証項目（仕様書Section40-48に対応）:
GATE.  Public Create APIのグローバルfeature gate（デフォルトFalse /
       閉じている場合は404）
A.     単一商品×20（1商品明細行のみ、40行に分割されない）
B.     複数商品（明細行数が商品種類数と一致する）
C.     customer_note保持・internal_note非露出・confirmation_status
SEC.   価格注入・internal_note上書き・状態偽装の拒否（extra="forbid"）
PAST.  過去日時の拒否
TRIM.  customer_name/product_nameのtrim・空文字拒否
MIN.   商品明細ゼロ件の拒否
DUP.   idempotency_keyによる冪等性（同一キー2回送信で1件のみ）
DIST.  同一顧客・同一受取時刻でも商品が違えば別注文として扱う
TXN.   バリデーション失敗時に部分的な行が一切作られない
NOTIF. OwnerNotificationEvent生成（正しいtype/priority、PII非含有）
NOTIF-FAIL. 通知生成失敗時もPreOrder作成自体はロールバックされない
OWNER. Owner詳細/一覧/確認/却下のtenant分離・状態遷移制御

実行: python3 tests/smoke_test_phase_o3_preorder_backend.py
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(
    tempfile.mkdtemp(), "smoke_o3_preorder_backend.db"
)
os.environ["SECRET_KEY"] = "smoke-test-secret-key-o3-preorder-backend"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")
# Section11/49のグローバルfeature gateは、テストの大半（機能検証）のために
# 明示的に有効化する。GATEセクションでは、この値をプロセス内でcache済みの
# Settingsシングルトンごと一時的にFalseへ書き換えて、デフォルト無効の挙動を
# 別途検証する（app.config.get_settings()は@lru_cacheのため、環境変数だけを
# 後から変えても効かない。設定オブジェクトそのものを直接書き換える）。
os.environ["PRE_ORDER_PUBLIC_CREATE_ENABLED"] = "true"

import httpx  # noqa: E402

JST = timezone(timedelta(hours=9))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _future_pickup(hours: int = 3) -> str:
    return (datetime.now(JST).replace(tzinfo=None) + timedelta(hours=hours)).isoformat()


def _test_source_level_protection():
    """FAST TURN・PHASE O1のUI層に本フェーズで一切触れていないことをソース
    レベルで確認する（git diffレビューが本来の防衛線。ここでは補助確認）。"""
    shop_manage_html = open(
        os.path.join(REPO_ROOT, "frontend", "public", "shop-manage.html"), encoding="utf-8"
    ).read()
    assert "const INDUSTRY_UI_CONFIG" in shop_manage_html
    assert "事前注文する" not in shop_manage_html
    print("REGRESSION-0. shop-manage.htmlのO1 UI層は無傷、PreOrder設定UIは未追加: OK")

    shop_html = open(os.path.join(REPO_ROOT, "frontend", "public", "shop.html"), encoding="utf-8").read()
    assert "事前注文する" not in shop_html
    print("REGRESSION-0b. shop.htmlに事前注文ボタンは未追加（O5範囲外）: OK")

    engine_js = open(
        os.path.join(REPO_ROOT, "frontend", "public", "js", "realtime-voice-engine.js"), encoding="utf-8"
    ).read()
    assert "toolContinuationTraceActive" in engine_js
    assert engine_js.count("dc.send(JSON.stringify(") == 8
    print("REGRESSION-0c. FAST TURN 3.6B T0-T10 trace・Tool数は無傷: OK")


async def main():
    from app.main import app, lifespan
    from app.config import get_settings

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # ===== セットアップ: Tenant A（テスト用店舗）=====
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-o3-a@example.com", "password": "password123",
                "display_name": "PHASE O3テストオーナーA",
            })
            assert r.status_code in (200, 201), f"register A failed: {r.status_code} {r.text}"
            owner_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "PHASE O3テスト洋菓子店", "category": "その他", "address": "東京都渋谷区1-1-1",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create shop_a failed: {r.status_code} {r.text}"
            shop_a = r.json()["shop_id"]

            # ===== セットアップ: Tenant B（tenant分離テスト用の別店舗）=====
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-o3-b@example.com", "password": "password123",
                "display_name": "PHASE O3テストオーナーB",
            })
            assert r.status_code in (200, 201), f"register B failed: {r.status_code} {r.text}"
            owner_b = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "PHASE O3テスト別店舗", "category": "その他", "address": "東京都新宿区2-2-2",
            }, headers=owner_b)
            assert r.status_code in (200, 201), f"create shop_b failed: {r.status_code} {r.text}"
            shop_b = r.json()["shop_id"]

            print("SETUP. Tenant A/Bの店舗を作成: OK")

            # ============================================================
            # GATE: グローバルfeature gate（Section11/49）
            # ============================================================
            with open(os.path.join(REPO_ROOT, "app", "config.py"), encoding="utf-8") as f:
                config_src = f.read()
            assert "PRE_ORDER_PUBLIC_CREATE_ENABLED: bool = False" in config_src
            print("GATE-0. app/config.pyのコード上のデフォルトはFalse（本番でも無効）: OK")

            settings = get_settings()
            assert settings.PRE_ORDER_PUBLIC_CREATE_ENABLED is True  # このテストプロセスではtrueに設定済み
            settings.PRE_ORDER_PUBLIC_CREATE_ENABLED = False
            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "ゲート閉鎖テスト", "customer_phone": "090-0000-0000",
                "pickup_at": _future_pickup(), "items": [{"product_name": "テスト商品", "quantity": 1}],
            })
            assert r.status_code == 404, f"gate closed should be 404: {r.status_code} {r.text}"
            settings.PRE_ORDER_PUBLIC_CREATE_ENABLED = True
            print("GATE-1. feature gateが閉じている間はPublic Create APIが404で無効化される: OK")

            # ============================================================
            # CASE A: 単一商品×20
            # ============================================================
            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "山田太郎", "customer_phone": "090-1111-2222",
                "customer_email": "yamada@example.com",
                "pickup_at": _future_pickup(), "items": [{"product_name": "ドーナツ", "quantity": 20}],
                "customer_note": "誕生日プレート希望",
            })
            assert r.status_code == 200, f"CASE A failed: {r.status_code} {r.text}"
            body = r.json()
            assert body["success"] is True
            assert len(body["items"]) == 1, f"1商品明細行のみのはず: {body['items']}"
            assert body["items"][0]["quantity"] == 20, "ドーナツ40個ではなくquantity=20の1行であるべき"
            assert body["items"][0]["product_name"] == "ドーナツ"
            assert body["items"][0]["unit_price"] is None, "Publicレスポンスのunit_priceは常にNone"
            assert body["confirmation_status"] == "owner_confirmation_required"
            assert body["customer_message_code"] == "OWNER_CONFIRMATION_REQUIRED"
            assert body["status"] == "pending"
            assert "internal_note" not in body, "Publicレスポンスにinternal_noteを含めてはいけない"
            assert "tenant_id" not in body, "Publicレスポンスにtenant_idを含めてはいけない"
            case_a_id = body["pre_order_id"]
            print("CASE-A. 単一商品×20 → 1商品明細行(quantity=20)・owner_confirmation_required: OK")

            # ============================================================
            # CASE B: 複数商品
            # ============================================================
            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "鈴木花子", "customer_phone": "090-3333-4444",
                "pickup_at": _future_pickup(),
                "items": [
                    {"product_name": "ショートケーキ", "quantity": 3, "variant": "苺"},
                    {"product_name": "チョコレートケーキ", "quantity": 2},
                ],
            })
            assert r.status_code == 200, f"CASE B failed: {r.status_code} {r.text}"
            body = r.json()
            assert len(body["items"]) == 2, f"商品明細は2行のはず（40行等に分割されない）: {body['items']}"
            names = {item["product_name"] for item in body["items"]}
            assert names == {"ショートケーキ", "チョコレートケーキ"}
            print("CASE-B. 複数商品 → 明細行数が商品種類数(2)と一致: OK")

            # ============================================================
            # CASE C: customer_note保持・internal_note非露出
            # ============================================================
            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "佐藤次郎", "customer_phone": "090-5555-6666",
                "pickup_at": _future_pickup(),
                "items": [{"product_name": "アレルギー対応パン", "quantity": 1}],
                "customer_note": "卵アレルギーがあります。除去対応可能か確認をお願いします",
            })
            assert r.status_code == 200, f"CASE C failed: {r.status_code} {r.text}"
            case_c_public = r.json()
            case_c_id = case_c_public["pre_order_id"]
            assert "customer_note" not in case_c_public, (
                "Public Create Responseのschema自体にcustomer_noteフィールドは無い"
            )
            # Owner詳細で確認（customer_noteは保持されているが、backendが安全性を
            # 判定したりはしない = 常にowner_confirmation_requiredのまま）
            r = await client.get(f"/api/v1/pre-orders/{case_c_id}", headers=owner_a)
            assert r.status_code == 200
            detail = r.json()
            assert detail["customer_note"] == "卵アレルギーがあります。除去対応可能か確認をお願いします"
            assert detail["internal_note"] is None
            assert detail["confirmation_status"] == "owner_confirmation_required"
            print("CASE-C. customer_noteはOwner詳細に保持・特別対応は自動判断せずowner_confirmation_required: OK")

            # ============================================================
            # SEC: 価格注入・internal_note上書き・状態偽装の拒否
            # ============================================================
            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "不正1", "customer_phone": "090-0000-0001",
                "pickup_at": _future_pickup(),
                "items": [{"product_name": "商品", "quantity": 1, "unit_price": 1}],
            })
            assert r.status_code == 422, f"unit_price注入は拒否されるべき: {r.status_code} {r.text}"
            print("SEC-1. item.unit_priceを送ると422で拒否される: OK")

            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "不正2", "customer_phone": "090-0000-0002",
                "pickup_at": _future_pickup(),
                "items": [{"product_name": "商品", "quantity": 1}],
                "internal_note": "勝手に内部メモを書き込む",
            })
            assert r.status_code == 422, f"internal_note注入は拒否されるべき: {r.status_code} {r.text}"
            print("SEC-2. internal_noteを送ると422で拒否される: OK")

            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "不正3", "customer_phone": "090-0000-0003",
                "pickup_at": _future_pickup(),
                "items": [{"product_name": "商品", "quantity": 1}],
                "status": "confirmed",
            })
            assert r.status_code == 422, f"status偽装は拒否されるべき: {r.status_code} {r.text}"
            print("SEC-3. statusを送ると422で拒否される: OK")

            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "不正4", "customer_phone": "090-0000-0004",
                "pickup_at": _future_pickup(),
                "items": [{"product_name": "商品", "quantity": 1}],
                "confirmation_status": "confirmed",
            })
            assert r.status_code == 422, f"confirmation_status偽装は拒否されるべき: {r.status_code} {r.text}"
            print("SEC-4. confirmation_statusを送ると422で拒否される: OK")

            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "不正5", "customer_phone": "090-0000-0005",
                "pickup_at": _future_pickup(),
                "items": [{"product_name": "商品", "quantity": 1}],
                "tenant_id": "attacker-tenant",
            })
            assert r.status_code == 422, f"tenant_id注入は拒否されるべき: {r.status_code} {r.text}"
            print("SEC-5. tenant_idを送ると422で拒否される: OK")

            # ============================================================
            # PAST: 過去日時の拒否
            # ============================================================
            past = (datetime.now(JST).replace(tzinfo=None) - timedelta(hours=1)).isoformat()
            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "過去日時", "customer_phone": "090-0000-0006",
                "pickup_at": past, "items": [{"product_name": "商品", "quantity": 1}],
            })
            assert r.status_code == 400, f"過去日時は拒否されるべき: {r.status_code} {r.text}"
            assert r.json().get("detail") is not None
            print("PAST-1. 過去のpickup_atは400で拒否される: OK")

            # ============================================================
            # TRIM: customer_name/product_nameのtrim・空文字拒否
            # ============================================================
            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "   ", "customer_phone": "090-0000-0007",
                "pickup_at": _future_pickup(), "items": [{"product_name": "商品", "quantity": 1}],
            })
            assert r.status_code == 422, f"空白のみのcustomer_nameは拒否されるべき: {r.status_code} {r.text}"
            print("TRIM-1. 空白のみのcustomer_nameは422で拒否される: OK")

            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "  田中一郎  ", "customer_phone": "090-0000-0008",
                "pickup_at": _future_pickup(), "items": [{"product_name": "  マドレーヌ  ", "quantity": 5}],
            })
            assert r.status_code == 200, f"trim後は正常作成されるべき: {r.status_code} {r.text}"
            trimmed_body = r.json()
            assert trimmed_body["items"][0]["product_name"] == "マドレーヌ", "product_nameはtrimされるべき"
            r = await client.get(f"/api/v1/pre-orders/{trimmed_body['pre_order_id']}", headers=owner_a)
            assert r.json()["customer_name"] == "田中一郎", "customer_nameはtrimされて保存されるべき"
            print("TRIM-2. customer_name/product_nameは前後の空白がtrimされて保存される: OK")

            # ============================================================
            # MIN: 商品明細ゼロ件の拒否
            # ============================================================
            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "明細ゼロ", "customer_phone": "090-0000-0009",
                "pickup_at": _future_pickup(), "items": [],
            })
            assert r.status_code == 422, f"商品明細ゼロ件は拒否されるべき: {r.status_code} {r.text}"
            print("MIN-1. items=[]（商品明細ゼロ件）は422で拒否される: OK")

            # ============================================================
            # TXN: バリデーション失敗時、部分的な行が一切作られない
            # ============================================================
            r = await client.get(f"/api/v1/shops/{shop_a}/pre-orders", headers=owner_a)
            total_before = r.json()["total"]
            r = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "部分失敗テスト", "customer_phone": "090-0000-0010",
                "pickup_at": _future_pickup(),
                "items": [
                    {"product_name": "有効な商品", "quantity": 1},
                    {"product_name": "無効な商品", "quantity": 0},  # gt=0違反
                ],
            })
            assert r.status_code == 422, f"1商品でもバリデーション違反があれば全体が拒否されるべき: {r.status_code}"
            r = await client.get(f"/api/v1/shops/{shop_a}/pre-orders", headers=owner_a)
            total_after = r.json()["total"]
            assert total_after == total_before, "バリデーション失敗時はPreOrderが一切作られてはいけない"
            print("TXN-1. 商品明細の一部が不正な場合、PreOrder自体が全く作成されない: OK")

            # ============================================================
            # DUP: idempotency_keyによる冪等性
            # ============================================================
            dup_key = "test-idempotency-key-001"
            payload = {
                "shop_id": shop_a, "customer_name": "冪等性テスト", "customer_phone": "090-7777-8888",
                "pickup_at": _future_pickup(), "items": [{"product_name": "食パン", "quantity": 2}],
                "idempotency_key": dup_key,
            }
            r1 = await client.post("/api/v1/pre-orders/create", json=payload)
            assert r1.status_code == 200, f"1回目の送信は成功するはず: {r1.status_code} {r1.text}"
            r2 = await client.post("/api/v1/pre-orders/create", json=payload)
            assert r2.status_code == 200, f"2回目の送信も200で返るはず（新規作成ではなく既存を返す）: {r2.status_code}"
            assert r1.json()["pre_order_id"] == r2.json()["pre_order_id"], "同一idempotency_keyは同一PreOrderを返すべき"

            r = await client.get(f"/api/v1/shops/{shop_a}/pre-orders", headers=owner_a)
            matching = [item for item in r.json()["items"] if item["customer_phone"] == "090-7777-8888"]
            assert len(matching) == 1, f"同一idempotency_keyの2回送信でPreOrderは1件のみのはず: {len(matching)}件"
            print("DUP-1. 同一idempotency_keyでの2回送信 → PreOrderは1件のみ（2回目は既存を返す）: OK")

            # ============================================================
            # DIST: 同一顧客・同一受取時刻でも商品が違えば別注文
            # ============================================================
            same_pickup = _future_pickup(hours=5)
            r1 = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "同時刻別注文太郎", "customer_phone": "090-9999-0000",
                "pickup_at": same_pickup, "items": [{"product_name": "クロワッサン", "quantity": 3}],
            })
            r2 = await client.post("/api/v1/pre-orders/create", json={
                "shop_id": shop_a, "customer_name": "同時刻別注文太郎", "customer_phone": "090-9999-0000",
                "pickup_at": same_pickup, "items": [{"product_name": "バゲット", "quantity": 1}],
            })
            assert r1.status_code == 200 and r2.status_code == 200
            assert r1.json()["pre_order_id"] != r2.json()["pre_order_id"], (
                "同一顧客・同一受取時刻でも商品内容が違えば別のPreOrderとして扱うべき（Section18-20）"
            )
            print("DIST-1. 同一顧客・同一受取時刻でも商品が異なれば別注文として2件作成される: OK")

            # ============================================================
            # NOTIF: OwnerNotificationEvent生成
            # ============================================================
            r = await client.get(f"/api/v1/shops/{shop_a}/notifications?limit=200", headers=owner_a)
            assert r.status_code == 200
            events = r.json()["items"]
            pre_order_events = [e for e in events if e["event_type"] == "PRE_ORDER_CREATED"]
            assert len(pre_order_events) >= 1, "PRE_ORDER_CREATEDイベントが生成されているはず"
            case_a_event = next((e for e in pre_order_events if e["related_entity_id"] == case_a_id), None)
            assert case_a_event is not None, "CASE Aで作成したPreOrderに対応する通知イベントが見つからない"
            assert case_a_event["priority"] == "ACTION_REQUIRED", (
                "owner_confirmation_required状態の場合はACTION_REQUIRED優先度であるべき"
            )
            assert case_a_event["related_entity_type"] == "pre_order"
            # PII非含有チェック: 電話番号・メールアドレスの生テキストが通知本文に含まれないこと
            assert "090-1111-2222" not in case_a_event["message"], "通知メッセージに電話番号を含めてはいけない"
            assert "yamada@example.com" not in case_a_event["message"], "通知メッセージにメールアドレスを含めてはいけない"
            print("NOTIF-1. PRE_ORDER_CREATED通知が正しいtype/priorityで生成され、PIIを含まない: OK")

            # ============================================================
            # NOTIF-FAIL: 通知生成失敗時もPreOrder作成自体はロールバックされない
            # ============================================================
            # notify_pre_order_created()自体を差し替えるのではなく、その内部で
            # 使われるdb_module.AsyncSessionLocal()自体がDBセッション取得に
            # 失敗するケースを模擬する。これにより、notify_pre_order_created()
            # 自身のtry/except（app/services/owner_notifications.py）が実際に
            # 例外を握りつぶし、create_public_pre_order()側へは一切伝播しない、
            # という実運用時の失敗分離の経路をそのまま検証できる
            # （関数まるごとの差し替えは、notify側のtry/exceptを迂回してしまい
            # 実際の失敗モードを正しく再現しないため避ける）。
            import app.database as real_db_module

            class _SelectiveBoomSessionLocal:
                """1回目の呼び出し（このリクエスト自身のget_db依存）はそのまま
                本物のセッションを返し、2回目以降の呼び出し（notify_pre_order_created()
                が独自に開く専用セッション）だけを失敗させる。"""

                def __init__(self, original):
                    self._original = original
                    self._calls = 0

                def __call__(self, *args, **kwargs):
                    self._calls += 1
                    if self._calls == 1:
                        return self._original(*args, **kwargs)
                    raise RuntimeError("通知生成の疑似障害（テスト専用・DBセッション取得失敗を模擬）")

            original_session_local = real_db_module.AsyncSessionLocal
            real_db_module.AsyncSessionLocal = _SelectiveBoomSessionLocal(original_session_local)
            try:
                r = await client.post("/api/v1/pre-orders/create", json={
                    "shop_id": shop_a, "customer_name": "通知失敗テスト", "customer_phone": "090-1234-0000",
                    "pickup_at": _future_pickup(), "items": [{"product_name": "商品", "quantity": 1}],
                })
                assert r.status_code == 200, (
                    f"通知生成が失敗してもPreOrder作成自体は成功として返るべき: {r.status_code} {r.text}"
                )
                failed_notif_id = r.json()["pre_order_id"]
            finally:
                real_db_module.AsyncSessionLocal = original_session_local

            # PreOrder自体は実際にDBへ commit 済みであることも確認する
            r = await client.get(f"/api/v1/pre-orders/{failed_notif_id}", headers=owner_a)
            assert r.status_code == 200, "通知生成失敗後もPreOrderは実際にDBへ保存されているべき"
            print("NOTIF-FAIL-1. 通知生成のDBセッション取得が失敗してもPreOrder作成はロールバックされず成功する: OK")

            # ============================================================
            # OWNER: tenant分離・状態遷移制御
            # ============================================================
            r = await client.get(f"/api/v1/pre-orders/{case_a_id}")
            assert r.status_code in (401, 403), f"未認証でのOwner詳細取得は拒否されるべき: {r.status_code}"
            print("OWNER-SEC-1. 未認証でのOwner詳細取得は401/403で拒否される: OK")

            r = await client.get(f"/api/v1/pre-orders/{case_a_id}", headers=owner_b)
            assert r.status_code == 403, f"他tenantのオーナーによる詳細取得は403で拒否されるべき: {r.status_code}"
            print("OWNER-SEC-2. 他tenant(B)のオーナーはtenant Aの事前注文を閲覧できない: OK")

            r = await client.patch(
                f"/api/v1/pre-orders/{case_a_id}/confirmation", json={"confirmation_status": "confirmed"},
                headers=owner_b,
            )
            assert r.status_code == 403, f"他tenantによる確認更新は403で拒否されるべき: {r.status_code}"
            print("OWNER-SEC-3. 他tenant(B)のオーナーはtenant Aの事前注文を確認/却下できない: OK")

            r = await client.get(f"/api/v1/shops/{shop_a}/pre-orders", headers=owner_b)
            assert r.status_code == 403, f"他tenantによる一覧取得は403で拒否されるべき: {r.status_code}"
            print("OWNER-SEC-4. 他tenant(B)のオーナーはtenant Aの事前注文一覧を取得できない: OK")

            r = await client.get(f"/api/v1/pre-orders/{case_a_id}", headers=owner_a)
            assert r.status_code == 200
            assert r.json()["confirmation_status"] == "owner_confirmation_required"
            print("OWNER-SEC-5. 正しいtenant(A)のオーナーは自店舗の事前注文を閲覧できる: OK")

            # CONFIRM: owner_confirmation_required → confirmed
            r = await client.patch(
                f"/api/v1/pre-orders/{case_a_id}/confirmation", json={"confirmation_status": "confirmed"},
                headers=owner_a,
            )
            assert r.status_code == 200, f"確認は成功するはず: {r.status_code} {r.text}"
            confirmed_body = r.json()
            assert confirmed_body["confirmation_status"] == "confirmed"
            assert confirmed_body["status"] == "confirmed", "confirm時にstatusもconfirmedへ進む設計"
            print("CONFIRM-1. owner_confirmation_required → confirmedへの遷移が成功する: OK")

            # 再度confirm/rejectしようとすると拒否される（状態遷移制御）
            r = await client.patch(
                f"/api/v1/pre-orders/{case_a_id}/confirmation", json={"confirmation_status": "rejected"},
                headers=owner_a,
            )
            assert r.status_code == 400, f"確認済みへの再遷移は拒否されるべき: {r.status_code} {r.text}"
            print("CONFIRM-2. 既にconfirmed済みの事前注文への再遷移は400で拒否される: OK")

            # confirmation_status="owner_confirmation_required"への遷移はschemaレベルで拒否
            r = await client.patch(
                f"/api/v1/pre-orders/{case_c_id}/confirmation",
                json={"confirmation_status": "owner_confirmation_required"}, headers=owner_a,
            )
            assert r.status_code == 422, f"owner_confirmation_requiredへの明示的な遷移は422で拒否されるべき: {r.status_code}"
            print("CONFIRM-3. confirmation_status=owner_confirmation_requiredへの遷移リクエストは422で拒否される: OK")

            # REJECT: owner_confirmation_required → rejected
            r = await client.patch(
                f"/api/v1/pre-orders/{case_c_id}/confirmation", json={"confirmation_status": "rejected"},
                headers=owner_a,
            )
            assert r.status_code == 200, f"却下は成功するはず: {r.status_code} {r.text}"
            rejected_body = r.json()
            assert rejected_body["confirmation_status"] == "rejected"
            assert rejected_body["status"] == "cancelled", "reject時にstatusもcancelledへ進む設計"
            print("REJECT-1. owner_confirmation_required → rejectedへの遷移が成功し、statusもcancelledになる: OK")

            # ============================================================
            # LIST: Owner一覧のフィルタ・集計
            # ============================================================
            r = await client.get(f"/api/v1/shops/{shop_a}/pre-orders?limit=200", headers=owner_a)
            assert r.status_code == 200
            listed = r.json()
            case_a_row = next(item for item in listed["items"] if item["id"] == case_a_id)
            assert case_a_row["item_count"] == 1
            assert case_a_row["total_quantity"] == 20, "CASE Aはquantity=20の1明細なのでtotal_quantity=20"
            print("LIST-1. Owner一覧のitem_count/total_quantityが正しく集計される: OK")

            r = await client.get(f"/api/v1/shops/{shop_a}/pre-orders?status=confirmed", headers=owner_a)
            assert r.status_code == 200
            assert all(item["status"] == "confirmed" for item in r.json()["items"])
            assert any(item["id"] == case_a_id for item in r.json()["items"])
            print("LIST-2. status絞込が正しく機能する: OK")

            print("\n✅ PHASE O3 スモークテスト: 全項目 OK")


if __name__ == "__main__":
    _test_source_level_protection()
    asyncio.run(main())
