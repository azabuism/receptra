"""
RECEPTRA — PHASE O2 スモークテスト（PreOrder DB Foundation）

背景（着手前の監査で確認した内容の要約）:

既存コードベースには Product / MenuItem(注文ではなく単なるメニュー表示) /
Order / Pickup に相当する「事前注文」の概念は存在しなかった（MenuItemは
飲食店の閲覧用メニュー項目であり、注文・数量を保持しない）。よって新規に
PreOrder / PreOrderItem の2テーブルを追加した。

★★★ 最重要原則（本テストが最優先で守ることを検証する）:
Reservation.number_of_people（「何人で来店するか」）と
PreOrderItem.quantity（「何を何個受け取るか」）は完全に別概念。
「ドーナツ40個」がReservation.number_of_people=40として保存されることは
絶対にあってはならない。

本フェーズで新設したもの（Section35想定の報告に対応）:
- app/models/pre_order.py: PreOrder, PreOrderItem, PreOrderStatus,
  PreOrderConfirmationStatus（新規テーブル。alembicは使わず
  Base.metadata.create_all()で自動作成。他のPhase3系モデルと同じ
  確立済みパターン）。
- app/schemas/pre_order.py: PreOrderCreate/PreOrderItemCreate/
  PreOrderResponse/PreOrderItemResponse（純粋なschema foundation。
  Public/Owner APIエンドポイントはまだ実装しない）。
- app/services/pre_orders.py: create_pre_order_with_items()
  （単体テストのためだけの最小限のservice helper。Reservation作成ロジック
  には一切触れない）。
- app/models/shop.py: Shop.pre_orders relationship追加のみ
  （既存のreservations/menu_items/callback_requests relationshipには
  一切変更なし）。
- app/models/owner_notification.py: OwnerNotificationEventType に
  PRE_ORDER_CREATED、OwnerNotificationRelatedEntityType に PRE_ORDER を
  追加（どちらもplain Stringカラムのため、DB migrationリスクはゼロ。
  実際の通知生成ロジックはまだ実装しない。O3で接続予定）。

本フェーズで意図的に作っていないもの:
- Public/Owner向けPreOrder作成・一覧・更新APIエンドポイント（O3で実装）。
- Realtime Voice Tool（Toolは追加しない。FAST TURN不可侵）。
- Product/商品マスター（product_nameはsnapshotとして保持するのみ）。
- 在庫管理・大量注文ルール・lead time・注文締切・価格計算・決済。
- PreOrder作成に伴う実際の通知生成（OwnerNotificationEvent行の作成）。

実行: python3 tests/smoke_test_phase_o2_preorder_foundation.py
"""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(
    tempfile.mkdtemp(), "smoke_o2_preorder_foundation.db"
)
os.environ["SECRET_KEY"] = "smoke-test-secret-key-o2-preorder-foundation"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402
from pydantic import ValidationError  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _test_source_level_protection():
    """Section28/29: FAST TURN関連ファイル・shop-manage.htmlのINDUSTRY_UI_CONFIG
    層に、本フェーズのコミットで変更が入っていないことをソースレベルで確認する
    （このテストファイル自体は変更検知の主目的ではなく、git diffレビューが本来の
    防衛線。ここでは「壊れていたら即座に分かる」補助的な確認として、これらの
    ファイルが今も存在し、PHASE O1の主要マーカーが無傷であることだけを見る）。
    """
    shop_manage_html = open(
        os.path.join(REPO_ROOT, "frontend", "public", "shop-manage.html"), encoding="utf-8"
    ).read()
    assert 'id="table-card"' in shop_manage_html
    assert "const INDUSTRY_UI_CONFIG" in shop_manage_html
    assert "const KNOWN_BUSINESS_TYPES" in shop_manage_html
    print("REGRESSION-0. PHASE O1のINDUSTRY_UI_CONFIG層（shop-manage.html）は無傷: OK")

    engine_js = open(
        os.path.join(REPO_ROOT, "frontend", "public", "js", "realtime-voice-engine.js"), encoding="utf-8"
    ).read()
    assert "toolContinuationTraceActive" in engine_js
    assert engine_js.count("dc.send(JSON.stringify(") == 8, (
        "realtime-voice-engine.jsのdc.send()呼び出し回数が変化しています（FAST TURN不可侵違反の疑い）"
    )
    print("REGRESSION-0b. FAST TURN（realtime-voice-engine.js / T0-T10 trace）は無傷: OK")


def _test_schema_validation():
    """Section6/10/11/22: quantity/product_name/pickup_at/customer_*の
    バリデーションを、DBやHTTPを経由せずPydanticスキーマ単体で検証する。"""
    from app.schemas.pre_order import PreOrderCreate, PreOrderItemCreate

    base_kwargs = dict(
        shop_id="shop-x",
        customer_name="山田太郎",
        customer_phone="08011112222",
        pickup_at="2026-09-27T12:00:00",
    )

    # 6. quantity 0 rejected
    try:
        PreOrderCreate(**base_kwargs, items=[{"product_name": "幕の内弁当", "quantity": 0}])
        assert False, "quantity=0が誤って許可されました"
    except ValidationError:
        pass
    print("6. quantity=0のPreOrderItemCreateはValidationErrorで拒否される: OK")

    # 7. negative rejected
    try:
        PreOrderCreate(**base_kwargs, items=[{"product_name": "幕の内弁当", "quantity": -1}])
        assert False, "quantity=-1が誤って許可されました"
    except ValidationError:
        pass
    print("7. quantity=負数のPreOrderItemCreateはValidationErrorで拒否される: OK")

    # 8. empty product_name rejected
    try:
        PreOrderCreate(**base_kwargs, items=[{"product_name": "", "quantity": 1}])
        assert False, "product_name=''が誤って許可されました"
    except ValidationError:
        pass
    print("8. product_name=''のPreOrderItemCreateはValidationErrorで拒否される: OK")

    # 9. pickup_at required
    kwargs_no_pickup = {k: v for k, v in base_kwargs.items() if k != "pickup_at"}
    try:
        PreOrderCreate(**kwargs_no_pickup, items=[{"product_name": "幕の内弁当", "quantity": 1}])
        assert False, "pickup_at省略が誤って許可されました"
    except ValidationError:
        pass
    print("9. pickup_at省略はValidationErrorで拒否される（必須項目）: OK")

    # 10. customer required (name / phone どちらも)
    kwargs_no_name = {k: v for k, v in base_kwargs.items() if k != "customer_name"}
    try:
        PreOrderCreate(**kwargs_no_name, items=[{"product_name": "幕の内弁当", "quantity": 1}])
        assert False, "customer_name省略が誤って許可されました"
    except ValidationError:
        pass
    kwargs_empty_name = {**{k: v for k, v in base_kwargs.items() if k != "customer_name"}, "customer_name": ""}
    try:
        PreOrderCreate(**kwargs_empty_name, items=[{"product_name": "幕の内弁当", "quantity": 1}])
        assert False, "customer_name=''が誤って許可されました"
    except ValidationError:
        pass
    kwargs_no_phone = {k: v for k, v in base_kwargs.items() if k != "customer_phone"}
    try:
        PreOrderCreate(**kwargs_no_phone, items=[{"product_name": "幕の内弁当", "quantity": 1}])
        assert False, "customer_phone省略が誤って許可されました"
    except ValidationError:
        pass
    print("10. customer_name/customer_phoneの省略・空文字はValidationErrorで拒否される: OK")

    # items空配列も拒否（Section25関連の安全策）
    try:
        PreOrderCreate(**base_kwargs, items=[])
        assert False, "items=[]が誤って許可されました"
    except ValidationError:
        pass
    print("10b. items=[]（商品明細ゼロ件）はValidationErrorで拒否される: OK")

    # 4/5. quantity 1 / quantity 40 は正常に受理される
    valid = PreOrderCreate(**base_kwargs, items=[
        {"product_name": "幕の内弁当", "quantity": 1},
        {"product_name": "チョコドーナツ", "quantity": 40},
    ])
    assert valid.items[0].quantity == 1
    assert valid.items[1].quantity == 40
    print("4/5. quantity=1およびquantity=40は正常に受理される: OK")

    # 11. unit_price: nullable・正常値・マイナス値は拒否
    item_ok = PreOrderItemCreate(product_name="誕生日ケーキ", quantity=1, unit_price=None)
    assert item_ok.unit_price is None
    item_ok2 = PreOrderItemCreate(product_name="誕生日ケーキ", quantity=1, unit_price=3500)
    assert item_ok2.unit_price == 3500
    try:
        PreOrderItemCreate(product_name="誕生日ケーキ", quantity=1, unit_price=-100)
        assert False, "unit_price=-100が誤って許可されました"
    except ValidationError:
        pass
    print("11. unit_priceはnullable・非負整数のみ許可（NULL/正常値OK、マイナス値NG）: OK")


async def main():
    _test_source_level_protection()
    _test_schema_validation()

    from app.main import app, lifespan

    async with lifespan(app):
        from app.database import AsyncSessionLocal
        from app.models.shop import Shop
        from app.models.reservation import Reservation
        from app.models.pre_order import PreOrder, PreOrderItem, PreOrderStatus, PreOrderConfirmationStatus
        from app.models.owner_notification import OwnerNotificationEventType, OwnerNotificationRelatedEntityType
        from app.schemas.pre_order import PreOrderCreate, PreOrderResponse
        from app.services.pre_orders import create_pre_order_with_items
        from sqlalchemy import select, func

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # ===== セットアップ: オーナー・店舗（飲食店） =====
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-o2-a@example.com", "password": "password123",
                "display_name": "O2テストオーナーA",
            })
            assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
            owner_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "O2テスト店舗A（弁当・ドーナツ店）", "category": "レストラン", "address": "東京都渋谷区1-1-1",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
            shop_a = r.json()["shop_id"]

            # ===== 通常のReservation（人数ベース）を1件作成しておく =====
            # このReservationのnumber_of_peopleが、後続のPreOrder作成によって
            # 一切変化しないことを14番で確認する。
            import datetime as _dt
            future_date = (_dt.date.today() + _dt.timedelta(days=10)).isoformat()

            hours_payload = {"hours": [
                {"day_of_week": d, "opening_time": "00:00:00", "closing_time": "23:30:00", "is_closed": False}
                for d in range(7)
            ]}
            r = await client.put(f"/api/v1/shops/{shop_a}/hours", json=hours_payload, headers=owner_a)
            assert r.status_code == 200, f"set hours failed: {r.status_code} {r.text}"

            async with AsyncSessionLocal() as session:
                shop_obj_setup = await session.get(Shop, shop_a)
                shop_obj_setup.reservations_enabled = True
                await session.commit()

            r = await client.post("/api/v1/reservations/create", json={
                "shop_id": shop_a, "reservation_date": f"{future_date}T18:00:00",
                "number_of_people": 4, "guest_name": "予約太郎", "guest_phone": "08099998888",
            })
            assert r.status_code in (200, 201), f"reservation create failed: {r.status_code} {r.text}"
            reservation_id = r.json()["reservation_id"]

            async with AsyncSessionLocal() as session:
                reservation_count_before = (await session.execute(
                    select(func.count()).select_from(Reservation)
                )).scalar_one()
                res_obj = await session.get(Reservation, reservation_id)
                assert res_obj.number_of_people == 4

            # ===== CASE A: 幕の内弁当×20（単一商品） =====
            pickup_a = f"{(_dt.date.today() + _dt.timedelta(days=1)).isoformat()}T12:00:00"
            data_a = PreOrderCreate(
                shop_id=shop_a, customer_name="注文太郎", customer_phone="08011112222",
                pickup_at=pickup_a,
                items=[{"product_name": "幕の内弁当", "quantity": 20}],
            )
            async with AsyncSessionLocal() as session:
                pre_order_a = await create_pre_order_with_items(session, data_a)
                pre_order_a_id = pre_order_a.id
            print("1/2. CASE A（幕の内弁当×20、単一商品）のPreOrder作成: OK")

            # ===== CASE B: チョコドーナツ×20 + シュガードーナツ×20（複数商品） =====
            pickup_b = f"{(_dt.date.today() + _dt.timedelta(days=2)).isoformat()}T10:00:00"
            data_b = PreOrderCreate(
                shop_id=shop_a, customer_name="注文花子", customer_phone="08033334444",
                pickup_at=pickup_b,
                items=[
                    {"product_name": "チョコドーナツ", "quantity": 20},
                    {"product_name": "シュガードーナツ", "quantity": 20},
                ],
            )
            async with AsyncSessionLocal() as session:
                pre_order_b = await create_pre_order_with_items(session, data_b)
                pre_order_b_id = pre_order_b.id

            async with AsyncSessionLocal() as session:
                items_b = (await session.execute(
                    select(PreOrderItem).filter(PreOrderItem.pre_order_id == pre_order_b_id)
                    .order_by(PreOrderItem.created_at)
                )).scalars().all()
                # 13. 商品ごとの数量が独立して保持されている（合算・混同されない）
                assert len(items_b) == 2, f"PreOrderItemが2件ではありません: {len(items_b)}"
                by_name = {i.product_name: i.quantity for i in items_b}
                assert by_name == {"チョコドーナツ": 20, "シュガードーナツ": 20}, by_name
                # 25. 「40個だから40行」になっていないこと（あくまで商品単位で2行）
                total_items_for_pre_order_b = len(items_b)
                assert total_items_for_pre_order_b == 2, (
                    f"複数商品の数量が誤って40行に展開されています: {total_items_for_pre_order_b}"
                )
            print("3. CASE B（チョコドーナツ×20+シュガードーナツ×20、複数商品）のPreOrder作成: OK")
            print("13. 商品ごとの数量が独立して保持され、合算・混同されない（チョコ20/シュガー20が別行）: OK")
            print("25(重要). 「40個だから40行」にはならず、商品単位で2行だけが作られる: OK")

            # ===== CASE C: 誕生日ケーキ×1 + customer_note =====
            pickup_c = f"{(_dt.date.today() + _dt.timedelta(days=3)).isoformat()}T15:00:00"
            data_c = PreOrderCreate(
                shop_id=shop_a, customer_name="注文次郎", customer_phone="08055556666",
                pickup_at=pickup_c,
                items=[{"product_name": "誕生日ケーキ", "quantity": 1, "unit_price": 3500}],
                customer_note="Happy Birthday ○○",
                internal_note="常連客。前回はチョコレートケーキを希望。",
            )
            async with AsyncSessionLocal() as session:
                pre_order_c = await create_pre_order_with_items(session, data_c)
                pre_order_c_id = pre_order_c.id
            print("CASE C. 誕生日ケーキ×1 + customer_note + internal_noteのPreOrder作成: OK")

            # ===== 11. shop relation: Shop.pre_orders / PreOrder.shop_id =====
            async with AsyncSessionLocal() as session:
                shop_obj = await session.get(Shop, shop_a)
                await session.refresh(shop_obj, attribute_names=["pre_orders"])
                pre_order_ids_via_shop = {po.id for po in shop_obj.pre_orders}
                assert {pre_order_a_id, pre_order_b_id, pre_order_c_id} <= pre_order_ids_via_shop, (
                    "Shop.pre_orders relationship経由で全PreOrderが取得できません"
                )
            print("11. shop relation: Shop.pre_orders relationship経由で作成した全PreOrderを取得できる: OK")

            # ===== 12. item relation: PreOrder.items / PreOrderItem.pre_order =====
            async with AsyncSessionLocal() as session:
                po_a = await session.get(PreOrder, pre_order_a_id)
                await session.refresh(po_a, attribute_names=["items"])
                assert len(po_a.items) == 1 and po_a.items[0].product_name == "幕の内弁当"
                assert po_a.items[0].pre_order_id == pre_order_a_id
            print("12. item relation: PreOrder.items / PreOrderItem.pre_order_idが正しく対応している: OK")

            # ===== 14. Reservation.number_of_peopleが一切変化していない =====
            async with AsyncSessionLocal() as session:
                res_obj_after = await session.get(Reservation, reservation_id)
                assert res_obj_after.number_of_people == 4, (
                    f"PreOrder作成後にReservation.number_of_peopleが変化しました: {res_obj_after.number_of_people}"
                )
            print("14. PreOrder作成後もReservation.number_of_peopleは4のまま変化しない（最重要原則）: OK")

            # ===== 15. PreOrder作成がReservation行を新規作成していない =====
            async with AsyncSessionLocal() as session:
                reservation_count_after = (await session.execute(
                    select(func.count()).select_from(Reservation)
                )).scalar_one()
                assert reservation_count_after == reservation_count_before, (
                    f"PreOrder作成によってReservation行数が変化しました: "
                    f"{reservation_count_before} -> {reservation_count_after}"
                )
            print(f"15. PreOrder作成（3件・商品明細4行）はReservation行を1件も新規作成しない"
                  f"（Reservation総数は{reservation_count_before}件のまま）: OK")

            # ===== 16. internal_note separation =====
            async with AsyncSessionLocal() as session:
                po_c = await session.get(PreOrder, pre_order_c_id)
                await session.refresh(po_c, attribute_names=["items"])
                # DBには保存されている
                assert po_c.internal_note == "常連客。前回はチョコレートケーキを希望。"
                assert po_c.customer_note == "Happy Birthday ○○"
                # だがPublic向けレスポンススキーマには含まれない
                response = PreOrderResponse.model_validate(po_c)
                response_dict = response.model_dump()
                assert "internal_note" not in response_dict, (
                    "PreOrderResponseにinternal_noteが含まれています（プライバシー/業務情報の漏洩）"
                )
                assert response_dict["customer_note"] == "Happy Birthday ○○"
            print("16. internal_noteはDBには保存されるがPreOrderResponse（Public/Owner向け）には含まれない: OK")

            # ===== JST-naive datetime handling =====
            async with AsyncSessionLocal() as session:
                po_a_check = await session.get(PreOrder, pre_order_a_id)
                assert po_a_check.pickup_at.tzinfo is None, (
                    "pickup_atがtimezone-awareで保存されています（JST-local-naive規約からの逸脱）"
                )
                assert po_a_check.pickup_at.hour == 12, (
                    f"pickup_atの時刻が保存時と異なります（意図しないtimezone変換の疑い）: {po_a_check.pickup_at}"
                )
            print("JST. pickup_atはtimezone-awareに変換されず、JST-local-naiveのまま指定通り保存される"
                  "（Reservation.reservation_dateと同じ基準。新しいtimezone architectureは作っていない）: OK")

            # ===== cascade delete: PreOrder削除でPreOrderItemも削除される =====
            async with AsyncSessionLocal() as session:
                po_b_for_delete = await session.get(PreOrder, pre_order_b_id)
                await session.delete(po_b_for_delete)
                await session.commit()
            async with AsyncSessionLocal() as session:
                remaining_items = (await session.execute(
                    select(func.count()).select_from(PreOrderItem)
                    .filter(PreOrderItem.pre_order_id == pre_order_b_id)
                )).scalar_one()
                assert remaining_items == 0, "PreOrder削除後もPreOrderItemが残存しています（cascade設定漏れ）"
            print("CASCADE. PreOrder削除時にPreOrderItemもcascade削除される（孤立行が残らない）: OK")

            # ===== Owner Notification基盤への追加（enum値のみ・実際の生成は未実装） =====
            assert OwnerNotificationEventType.PRE_ORDER_CREATED.value == "PRE_ORDER_CREATED"
            assert OwnerNotificationRelatedEntityType.PRE_ORDER.value == "pre_order"
            # 既存値が変化していないこと（回帰）
            assert OwnerNotificationEventType.RESERVATION_CREATED.value == "RESERVATION_CREATED"
            assert OwnerNotificationEventType.OWNER_ACTION_REQUIRED.value == "OWNER_ACTION_REQUIRED"
            assert OwnerNotificationRelatedEntityType.RESERVATION.value == "reservation"
            assert OwnerNotificationRelatedEntityType.CALLBACK_REQUEST.value == "callback_request"
            print("NOTIF. OwnerNotificationEventType.PRE_ORDER_CREATED / "
                  "OwnerNotificationRelatedEntityType.PRE_ORDERが追加され、既存値は無傷（回帰なし）: OK")

            # ===== デフォルト値の確認 =====
            async with AsyncSessionLocal() as session:
                po_a_defaults = await session.get(PreOrder, pre_order_a_id)
                assert po_a_defaults.status == PreOrderStatus.PENDING.value
                assert po_a_defaults.confirmation_status == PreOrderConfirmationStatus.CONFIRMED.value
            print("DEFAULT. 新規PreOrderのデフォルトはstatus=pending, confirmation_status=confirmed: OK")

            # ===== (回帰) 既存Reservation/Shop APIは無傷 =====
            r = await client.get(f"/api/v1/reservations/{reservation_id}", headers=owner_a)
            assert r.status_code == 200, "(回帰) Reservation詳細取得に問題があります"
            r = await client.get(f"/api/v1/shops/{shop_a}", headers=owner_a)
            assert r.status_code == 200, "(回帰) Shop詳細取得に問題があります"
            print("(回帰) 既存Reservation/Shop APIは無傷: OK")

    print()
    print("全テストOK: PHASE O2（PreOrder DB Foundation）— PreOrder/PreOrderItemの作成・"
          "バリデーション・relationship・cascade削除・Reservationとの完全独立性・"
          "internal_note分離・JST-naive日時規約・OwnerNotification foundation追加を確認")


if __name__ == "__main__":
    asyncio.run(main())
