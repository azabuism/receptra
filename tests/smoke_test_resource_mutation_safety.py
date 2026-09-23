"""
Reservation Intelligence "Resource Mutation Safety — Phase 1" スモークテスト

背景（本フェーズで修正した内容）:

前フェーズ「Capacity Mutation Safety」(commit 35068d6) では、予約側
(Reservation.number_of_people)の変更が、割り当て済みテーブルの容量
(ShopTable.capacity)を超えないことを保証した。

しかし逆方向、すなわちOwnerがTable側のcapacityを既存の有効な予約の
人数より小さく変更してしまうケースは未対応のままだった（例: 6人定員の
テーブルに5名の確定予約が入っている状態から、Ownerがcapacityを4へ自由に
変更できてしまう）。

今回、PUT /api/v1/shops/{shop_id}/tables/{table_id}（update_table()）に、
capacityが減少方向へ変更される場合のみ、そのテーブルに割り当てられた
「今後の有効な予約」の中に新capacityを超える人数の予約がないかを検証する
処理を追加した。

設計方針（監査で確認済みの既存規約の再利用。新しい定義を発明していない）:
- 「今後の有効な予約」の定義は、delete_table()が元から持っていた定義
  （status in (pending, confirmed) かつ reservation_date >= 現在時刻）を
  そのまま _find_future_active_table_reservations() として抽出・再利用。
  delete_table()自身もこのhelperを呼ぶようリファクタリングしたが、判定
  条件・挙動は一切変更していない（下記テストNで確認）。
- 「1予約の人数が新capacityに収まるか」の判定は、前フェーズの
  _validate_table_capacity(table, party_size)をそのまま再利用（新しい
  比較ロジックを重複実装していない）。SimpleNamespaceで新capacityだけを
  持つ軽量オブジェクトを作って渡している。
- capacityが増加・同値の場合は検証不要（既存予約を壊す可能性がないため）。
- 対象は Reservation.table_id == table.id の予約のみ（他テーブル・他店舗
  の予約は一切見ない）。
- 自動的な別テーブルへの再割当・予約の自動キャンセル・人数の自動調整は
  一切行わない。容量不足の場合は単純に400で保存全体を拒否する
  （name等の同時変更も含め、部分更新は一切発生しない）。

テスト設計上の注意: create-reservation Toolにはshop_idごとに
60秒10リクエストのレート制限があるため（app/routers/realtime_voice.py）、
本ファイルはテストセクションごとに新しいshopを作成し、1つのshopへ
リクエストが集中しないようにしている（既存のsmoke_test_*.pyの慣習と
同様、レート制限自体は今回のフェーズと無関係のため変更しない）。

検証項目（Section38-51に対応）:
A. capacity増加はOK
B. capacity同値はOK
C. capacity減少（既存予約と矛盾しない）はOK
D. capacity減少（既存予約と矛盾する）はNG、DBのcapacityは変更されない
E. 境界値（新capacityちょうど＝OK、+1名＝NG）
F. cancelled予約は対象外（矛盾する人数でもcapacity減少OK）
G. 過去の予約は対象外（矛盾する人数でもcapacity減少OK）
H. 複数の未来予約（1件でも超過があれば拒否）
I. 無関係な別テーブルの予約はブロック要因にならない
J. atomicity（capacity検証失敗時、同時変更したnameも一切適用されない）
K. 前フェーズ（Capacity Mutation Safety）のReservation Mutation regression
L. table_id=Noneの予約はTable capacity変更検証に無関係
M. テナント分離（他テナントのTable更新は403のまま）
N. delete_table()のregression（helper抽出後も挙動が変わっていないこと）

実行: python3 tests/smoke_test_resource_mutation_safety.py
"""

import asyncio
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, date, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_resource_mutation.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-resource-mutation"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402

JST = timezone(timedelta(hours=9))
_phone_counter = 0


def _next_weekday(target_weekday: int, weeks_ahead: int = 2):
    today = date.today()
    days_ahead = (target_weekday - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead + 7 * (weeks_ahead - 1))


def _next_phone():
    global _phone_counter
    _phone_counter += 1
    return f"090{_phone_counter:08d}"


async def main():
    from app.main import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-resource-a@example.com", "password": "password123",
                "display_name": "Resourceテストオーナー",
            })
            assert r.status_code in (200, 201), f"register A failed: {r.status_code} {r.text}"
            owner_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

            from app.database import AsyncSessionLocal
            from app.models.shop import Shop, ShopTable
            from app.models.reservation import Reservation
            from sqlalchemy import select

            target_date = _next_weekday(2, weeks_ahead=2)

            async def _new_shop(name: str):
                """テストセクションごとに新しいshopを作る（create-reservation
                Toolのshop_idごとレート制限(60秒10リクエスト)を回避するため）。"""
                r = await client.post("/api/v1/shops/register", json={
                    "name": name, "category": "食堂", "address": "東京都渋谷区9-9-9",
                }, headers=owner_a)
                assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
                shop_id = r.json()["shop_id"]

                hours_payload = {"hours": [
                    {"day_of_week": d, "opening_time": "10:00:00", "closing_time": "22:00:00", "is_closed": False}
                    for d in range(7)
                ]}
                r = await client.put(f"/api/v1/shops/{shop_id}/hours", json=hours_payload, headers=owner_a)
                assert r.status_code == 200, f"set hours failed: {r.status_code} {r.text}"

                async with AsyncSessionLocal() as session:
                    shop_obj = await session.get(Shop, shop_id)
                    shop_obj.reservations_enabled = True
                    shop_obj.reservation_duration_minutes = 60
                    await session.commit()
                return shop_id

            async def _new_table(shop_id: str, capacity: int, name: str = "テストテーブル"):
                r = await client.post(f"/api/v1/shops/{shop_id}/tables", json={
                    "name": name, "capacity": capacity,
                }, headers=owner_a)
                assert r.status_code == 200, f"create table failed: {r.status_code} {r.text}"
                return r.json()["id"]

            async def _reserve_on_table(shop_id: str, table_id: str, party_size: int, time_str: str):
                r = await client.post(
                    f"/api/v1/shops/{shop_id}/realtime-voice/tools/create-reservation",
                    json={
                        "date": target_date.isoformat(), "time": time_str, "party_size": party_size,
                        "guest_name": "Resourceテスト客", "guest_phone": _next_phone(),
                        "call_id": "smoke-resource-" + str(uuid.uuid4()),
                    },
                )
                assert r.status_code == 200 and r.json()["success"] is True, f"予約作成失敗: {r.status_code} {r.text}"
                reservation_id = r.json()["reservation_id"]
                async with AsyncSessionLocal() as session:
                    res = await session.get(Reservation, reservation_id)
                    assert res.table_id == table_id, f"想定外のテーブル割当: {res.table_id} != {table_id}"
                return reservation_id

            async def _get_table(table_id: str):
                async with AsyncSessionLocal() as session:
                    return await session.get(ShopTable, table_id)

            # ===== A. capacity増加はOK =====
            shop_a = await _new_shop("ResourceテストA食堂")
            table_id = await _new_table(shop_a, 4)
            await _reserve_on_table(shop_a, table_id, 4, "13:00")
            r = await client.put(f"/api/v1/shops/{shop_a}/tables/{table_id}", json={
                "capacity": 6,
            }, headers=owner_a)
            assert r.status_code == 200, f"capacity増加(4->6)が失敗: {r.status_code} {r.text}"
            assert r.json()["capacity"] == 6
            print("A. capacity増加(4->6)はOK: OK")

            # ===== B. capacity同値はOK =====
            r = await client.put(f"/api/v1/shops/{shop_a}/tables/{table_id}", json={
                "capacity": 6,
            }, headers=owner_a)
            assert r.status_code == 200, f"capacity同値(6->6)が失敗: {r.status_code} {r.text}"
            print("B. capacity同値(6->6)はOK: OK")

            # ===== C. capacity減少（既存予約と矛盾しない）はOK =====
            # 現在capacity=6、既存予約=4名。6->4はOKのはず。
            r = await client.put(f"/api/v1/shops/{shop_a}/tables/{table_id}", json={
                "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 200, f"capacity減少(6->4,予約4名)が失敗: {r.status_code} {r.text}"
            assert r.json()["capacity"] == 4
            print("C. capacity減少（既存予約と矛盾しない: 予約4名->new capacity4）はOK: OK")

            # ===== D.【最重要】capacity減少（既存予約と矛盾する）はNG =====
            shop_d = await _new_shop("ResourceテストD食堂")
            table_id = await _new_table(shop_d, 6)
            await _reserve_on_table(shop_d, table_id, 5, "14:00")
            r = await client.put(f"/api/v1/shops/{shop_d}/tables/{table_id}", json={
                "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 400, (
                f"【最重要】未来5名予約があるのにcapacity(6->4)への変更が誤って成立: {r.status_code} {r.text}"
            )
            t = await _get_table(table_id)
            assert t.capacity == 6, f"【最重要】拒否されたはずのcapacityがDB上変更されてしまっている: {t.capacity}"
            print("D.【最重要】capacity減少（既存予約(5名)と矛盾）はNG、DBのcapacityは変更されない: OK")

            # ===== E. 境界値 =====
            shop_e = await _new_shop("ResourceテストE食堂")
            table_id = await _new_table(shop_e, 6)
            await _reserve_on_table(shop_e, table_id, 4, "14:00")
            r = await client.put(f"/api/v1/shops/{shop_e}/tables/{table_id}", json={
                "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 200, f"境界値(予約4名, new capacity4)が失敗: {r.status_code} {r.text}"

            table_id2 = await _new_table(shop_e, 6, "テーブル2")
            await _reserve_on_table(shop_e, table_id2, 5, "17:00")
            r = await client.put(f"/api/v1/shops/{shop_e}/tables/{table_id2}", json={
                "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 400, f"境界値(予約5名, new capacity4)が誤って成立: {r.status_code} {r.text}"
            print("E. 境界値（ちょうどはOK、1名超過はNG）: OK")

            # ===== F. cancelled予約は対象外 =====
            shop_f = await _new_shop("ResourceテストF食堂")
            table_id = await _new_table(shop_f, 6)
            cancelled_id = await _reserve_on_table(shop_f, table_id, 5, "14:00")
            r = await client.put(f"/api/v1/reservations/{cancelled_id}", json={
                "status": "cancelled",
            }, headers=owner_a)
            assert r.status_code == 200, f"予約キャンセル失敗: {r.status_code} {r.text}"
            r = await client.put(f"/api/v1/shops/{shop_f}/tables/{table_id}", json={
                "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 200, (
                f"cancelled予約(5名)がブロック要因になってしまい、capacity減少(6->4)が失敗: {r.status_code} {r.text}"
            )
            print("F. cancelled予約は今後の有効な予約の対象外（capacity減少OK）: OK")

            # ===== G. 過去の予約は対象外 =====
            shop_g = await _new_shop("ResourceテストG食堂")
            table_id = await _new_table(shop_g, 6)
            past_id = str(uuid.uuid4())
            async with AsyncSessionLocal() as session:
                past_dt = datetime.utcnow() - timedelta(days=1)
                past_res = Reservation(
                    id=past_id, shop_id=shop_g, table_id=table_id, staff_id=None, service_id=None,
                    guest_name="過去客", guest_phone=_next_phone(),
                    reservation_date=past_dt, duration_minutes=60, number_of_people=5,
                    status="confirmed", reservation_source="manual",
                    created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                )
                session.add(past_res)
                await session.commit()
            r = await client.put(f"/api/v1/shops/{shop_g}/tables/{table_id}", json={
                "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 200, (
                f"過去の予約(5名)がブロック要因になってしまい、capacity減少(6->4)が失敗: {r.status_code} {r.text}"
            )
            print("G. 過去の予約は今後の有効な予約の対象外（capacity減少OK）: OK")

            # ===== H. 複数の未来予約 =====
            shop_h = await _new_shop("ResourceテストH食堂")
            table_id = await _new_table(shop_h, 6)
            await _reserve_on_table(shop_h, table_id, 2, "11:00")
            await _reserve_on_table(shop_h, table_id, 3, "14:00")
            await _reserve_on_table(shop_h, table_id, 4, "17:00")
            r = await client.put(f"/api/v1/shops/{shop_h}/tables/{table_id}", json={
                "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 200, f"複数未来予約(2,3,4名, new capacity4)が失敗: {r.status_code} {r.text}"

            table_id2 = await _new_table(shop_h, 6, "テーブル2")
            await _reserve_on_table(shop_h, table_id2, 2, "11:00")
            await _reserve_on_table(shop_h, table_id2, 3, "14:00")
            await _reserve_on_table(shop_h, table_id2, 5, "17:00")
            r = await client.put(f"/api/v1/shops/{shop_h}/tables/{table_id2}", json={
                "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 400, f"複数未来予約(2,3,5名, new capacity4)が誤って成立: {r.status_code} {r.text}"
            print("H. 複数の未来予約（1件でも超過があれば拒否）: OK")

            # ===== I. 無関係な別テーブルの予約はブロック要因にならない =====
            # テーブルAのcapacityをテーブルBより小さくしておく
            # （_find_available_table()の候補ソート順は(capacity, id)昇順のため、
            # 同capacityだとUUIDの大小という非決定的な要素で割当先が変わって
            # しまい、テストが不安定になる。capacityを意図的にずらして
            # 「最初の予約(2名)は必ずテーブルAに入る」ことを決定的にする）。
            shop_i = await _new_shop("ResourceテストI食堂")
            table_a = await _new_table(shop_i, 6, "テーブルA")
            table_b = await _new_table(shop_i, 8, "テーブルB")
            await _reserve_on_table(shop_i, table_a, 2, "11:00")
            # table_aは11:00に埋まっているため、同時刻の別予約はtable_bへ入るはず
            r = await client.post(
                f"/api/v1/shops/{shop_i}/realtime-voice/tools/create-reservation",
                json={
                    "date": target_date.isoformat(), "time": "11:00", "party_size": 5,
                    "guest_name": "Resourceテスト客B", "guest_phone": _next_phone(),
                    "call_id": "smoke-resource-" + str(uuid.uuid4()),
                },
            )
            assert r.status_code == 200 and r.json()["success"] is True, f"table_bへの予約作成失敗: {r.status_code} {r.text}"
            async with AsyncSessionLocal() as session:
                res_b = await session.get(Reservation, r.json()["reservation_id"])
                assert res_b.table_id == table_b, f"table_bへの割当想定が崩れている: {res_b.table_id}"

            r = await client.put(f"/api/v1/shops/{shop_i}/tables/{table_a}", json={
                "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 200, (
                f"別テーブル(B)の5名予約がテーブルAのcapacity変更をブロックしてしまった: {r.status_code} {r.text}"
            )
            print("I. 無関係な別テーブルの予約はブロック要因にならない: OK")

            # ===== J. atomicity =====
            shop_j = await _new_shop("ResourceテストJ食堂")
            table_id = await _new_table(shop_j, 6, "テーブルA")
            await _reserve_on_table(shop_j, table_id, 5, "14:00")
            r = await client.put(f"/api/v1/shops/{shop_j}/tables/{table_id}", json={
                "name": "VIP A",
                "capacity": 4,
            }, headers=owner_a)
            assert r.status_code == 400, f"capacity減少+name同時変更が誤って成立: {r.status_code} {r.text}"
            t = await _get_table(table_id)
            assert t.capacity == 6, f"capacityが変更されてしまっている: {t.capacity}"
            assert t.name == "テーブルA", (
                f"【重要】capacity検証失敗時にnameだけ部分的に適用されてしまった（atomicity違反）: {t.name}"
            )
            print("J. capacity検証失敗時、同時変更したnameも一切適用されない（atomicity）: OK")

            # ===== K. 前フェーズ（Capacity Mutation Safety）のReservation Mutation regression =====
            shop_k = await _new_shop("ResourceテストK食堂")
            table_id = await _new_table(shop_k, 4)
            reservation_id = await _reserve_on_table(shop_k, table_id, 2, "13:00")
            r = await client.put(f"/api/v1/reservations/{reservation_id}", json={
                "number_of_people": 5,
            }, headers=owner_a)
            assert r.status_code == 400, f"前フェーズのregression: 2->5への変更が誤って成立: {r.status_code} {r.text}"
            async with AsyncSessionLocal() as session:
                res = await session.get(Reservation, reservation_id)
                assert res.number_of_people == 2
            print("K. 前フェーズ(Capacity Mutation Safety)のReservation Mutation regression: OK")

            # ===== L. table_id=Noneの予約は無関係 =====
            shop_l = await _new_shop("ResourceテストL食堂")
            table_id = await _new_table(shop_l, 6)
            no_table_dt = datetime.combine(target_date, datetime.min.time().replace(hour=11, minute=0))
            async with AsyncSessionLocal() as session:
                no_table_res = Reservation(
                    id=str(uuid.uuid4()), shop_id=shop_l, table_id=None, staff_id=None, service_id=None,
                    guest_name="テーブルなし客", guest_phone=_next_phone(),
                    reservation_date=no_table_dt, duration_minutes=60, number_of_people=999,
                    status="confirmed", reservation_source="manual",
                    created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                )
                session.add(no_table_res)
                await session.commit()
            r = await client.put(f"/api/v1/shops/{shop_l}/tables/{table_id}", json={
                "capacity": 2,
            }, headers=owner_a)
            assert r.status_code == 200, (
                f"table_id=Noneの予約(999名)がTable capacity変更をブロックしてしまった: {r.status_code} {r.text}"
            )
            print("L. table_id=Noneの予約はTable capacity変更検証に無関係: OK")

            # ===== M. テナント分離 =====
            shop_m = await _new_shop("ResourceテストM食堂")
            table_id = await _new_table(shop_m, 6)
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-resource-b@example.com", "password": "password123",
                "display_name": "Resourceテスト他テナントオーナー",
            })
            assert r.status_code in (200, 201)
            owner_b = {"Authorization": f"Bearer {r.json()['access_token']}"}
            r = await client.put(f"/api/v1/shops/{shop_m}/tables/{table_id}", json={
                "capacity": 2,
            }, headers=owner_b)
            assert r.status_code == 403, f"他テナントのオーナーがTableを更新できてしまった: {r.status_code} {r.text}"
            print("M. テナント分離（他テナントはTable更新不可のまま）: OK")

            # ===== N. delete_table()のregression =====
            shop_n = await _new_shop("ResourceテストN食堂")
            table_id = await _new_table(shop_n, 6)
            await _reserve_on_table(shop_n, table_id, 3, "14:00")
            r = await client.delete(f"/api/v1/shops/{shop_n}/tables/{table_id}", headers=owner_a)
            assert r.status_code == 400, (
                f"未来の有効な予約があるTableのdeleteが誤って成立してしまった（delete_table regression）: "
                f"{r.status_code} {r.text}"
            )

            # cancelledにすれば削除できることも確認（delete_table()の既存挙動regression）
            async with AsyncSessionLocal() as session:
                result = await session.execute(select(Reservation).filter(Reservation.table_id == table_id))
                for res in result.scalars().all():
                    res.status = "cancelled"
                await session.commit()
            r = await client.delete(f"/api/v1/shops/{shop_n}/tables/{table_id}", headers=owner_a)
            assert r.status_code == 200, f"cancelled後もdeleteできない（delete_table regression）: {r.status_code} {r.text}"
            print("N. delete_table()のregression（未来有効予約でブロック・cancelled後は削除可）: OK")

            print("\n=== Resource Mutation Safety スモークテスト: 全項目OK ===")


asyncio.run(main())
