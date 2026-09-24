"""
RESOURCE LANE V1.1 + PRIVACY CONSISTENCY スモークテスト

背景（本フェーズで実装した内容）:

Booking Board（GET /shop/{shop_id}/board）では既にguest_phoneをマスク済みで
返していた一方、既存の「予約一覧」（GET /shop/{shop_id}、オーナー画面の
「一覧」タブが使用）はguest_phone・guest_emailを常時フル値のまま返しており、
一貫性のないプライバシー露出になっていた（監査で発見）。

本フェーズの変更は「予約一覧」エンドポイントに限定される:

1. ReservationResponseへ追加フィールド guest_phone_masked を追加（既存の
   guest_phoneフィールド自体は型・意味とも一切変更しない、後方互換の追加のみ）。
2. GET /shop/{shop_id}（一覧）だけ、_to_list_response()という専用ヘルパーで
   guest_phone_masked に app.schemas.callback_request.mask_phone_for_list()
   （Board APIと全く同じ規約）の結果を入れ、guest_phone自体はNoneに差し替えて
   返す。これによりフルの電話番号は一覧のレスポンス本文に一切含まれなくなる。
3. _to_response()自体は変更していないため、POST /create（予約作成直後の
   レスポンス）・GET /{reservation_id}（詳細取得＝reveal用。Phase Hで
   オーナー認証・tenant分離を追加済み）・PUT更新など、他のすべての用途は
   従来通りフルのguest_phoneを返し続ける（reveal機能はこの既存エンドポイントを
   そのまま再利用しており、新しいreveal専用APIは作っていない）。
4. guest_emailは今回のスコープ外（Section22の逃げ道通りFollow-up送り）。
   一覧のguest_emailは従来通りフル値のまま変更していない——本テストでも
   guest_emailが変わっていないことを確認する。

検証項目:
A. 一覧（GET /shop/{shop_id}）: guest_phoneは常にNone、guest_phone_maskedが
   "****"+末尾4桁になっている、レスポンス本文にフルの電話番号の数字列が
   一切含まれない
B. 一覧: guest_emailは今回変更していないため従来通りフル値のまま返る
C. 詳細取得（GET /{reservation_id}）: オーナー本人はフルのguest_phoneを
   取得できる（一覧のマスクに影響されない）
D. 詳細取得: 未認証は拒否される
E. 詳細取得: 他tenantのオーナーは403で拒否される
F. mask_phone_for_listの境界ケース（一覧経由）: 電話番号なし(None)の予約は
   guest_phone_maskedもNoneになる
G. 予約作成（POST /create）の直後レスポンスは、一覧とは無関係に従来通り
   フルのguest_phoneを返す（_to_response()自体は無変更であることの確認）
H. ステータスフィルタ付き一覧でも同様にマスクされる（既存のフィルタ機能を
   壊していないことの確認も兼ねる）

実行: python3 tests/smoke_test_reservation_list_privacy.py
"""

import asyncio
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, date, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_reslist_privacy.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-reslist-privacy"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402

JST = timezone(timedelta(hours=9))


def _next_weekday(target_weekday: int, weeks_ahead: int = 2):
    today = date.today()
    days_ahead = (target_weekday - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead + 7 * (weeks_ahead - 1))


async def main():
    from app.main import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # ===== セットアップ: Tenant A / Tenant B（架空のテストオーナー） =====
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-reslist-privacy-a@example.com", "password": "password123",
                "display_name": "予約一覧プライバシーテストオーナーA",
            })
            assert r.status_code in (200, 201), f"register A failed: {r.status_code} {r.text}"
            owner_a = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-reslist-privacy-b@example.com", "password": "password123",
                "display_name": "予約一覧プライバシーテストオーナーB",
            })
            assert r.status_code in (200, 201), f"register B failed: {r.status_code} {r.text}"
            owner_b = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "プライバシーテスト居酒屋", "category": "居酒屋", "address": "東京都渋谷区1-1-1",
            }, headers=owner_a)
            assert r.status_code in (200, 201), f"create shop_a failed: {r.status_code} {r.text}"
            shop_a = r.json()["shop_id"]

            hours_payload = {"hours": [
                {"day_of_week": d, "opening_time": "18:00:00", "closing_time": "23:00:00", "is_closed": False, "closes_next_day": False}
                for d in range(7)
            ]}
            r = await client.put(f"/api/v1/shops/{shop_a}/hours", json=hours_payload, headers=owner_a)
            assert r.status_code == 200, f"set hours(shop_a) failed: {r.status_code} {r.text}"

            from app.database import AsyncSessionLocal
            from app.models.shop import Shop
            from app.models.reservation import Reservation
            from sqlalchemy import select

            async def _enable_reservations(shop_id: str):
                async with AsyncSessionLocal() as session:
                    shop_obj = await session.get(Shop, shop_id)
                    shop_obj.reservations_enabled = True
                    await session.commit()

            await _enable_reservations(shop_a)

            mon = _next_weekday(0, weeks_ahead=2)

            async def _insert_reservation(shop_id, dt, guest_name="太郎", guest_phone="09012345678", guest_email="taro@example.com", status="confirmed", number_of_people=2):
                async with AsyncSessionLocal() as session:
                    res = Reservation(
                        id=str(uuid.uuid4()), shop_id=shop_id,
                        guest_name=guest_name, guest_phone=guest_phone, guest_email=guest_email,
                        reservation_date=dt, duration_minutes=60,
                        number_of_people=number_of_people, status=status,
                        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                    )
                    session.add(res)
                    await session.commit()
                    return res.id

            # ===== A/B. 一覧: フル電話番号は含まれず、マスク済みのみ。emailは従来通り =====
            res_id_1 = await _insert_reservation(
                shop_a, datetime(mon.year, mon.month, mon.day, 19, 0),
                guest_name="山田太郎", guest_phone="09012345678", guest_email="yamada@example.com",
            )
            r = await client.get(f"/api/v1/reservations/shop/{shop_a}", headers=owner_a)
            assert r.status_code == 200, f"list failed: {r.status_code} {r.text}"
            body = r.json()
            assert body["total"] == 1, f"expected 1 item: {body}"
            item = body["items"][0]
            assert item["id"] == res_id_1
            assert item["guest_phone"] is None, f"guest_phone must be None in list: {item}"
            assert item["guest_phone_masked"] == "****5678", f"guest_phone_masked mismatch: {item}"
            assert "09012345678" not in r.text, "フルの電話番号が一覧レスポンスに含まれてはならない"
            print("A. 一覧: guest_phoneはNone・guest_phone_maskedのみマスク済みで返る: OK")

            assert item["guest_email"] == "yamada@example.com", f"guest_email should be unchanged (out of scope this phase): {item}"
            print("B. 一覧: guest_emailは今回のスコープ外のため従来通りフル値: OK")

            await _clear(AsyncSessionLocal, Reservation, shop_a)

            # ===== C/D/E. 詳細取得（reveal） =====
            res_id_2 = await _insert_reservation(
                shop_a, datetime(mon.year, mon.month, mon.day, 20, 0),
                guest_name="佐藤花子", guest_phone="08087654321",
            )

            r = await client.get(f"/api/v1/reservations/{res_id_2}", headers=owner_a)
            assert r.status_code == 200, f"detail(owner_a) failed: {r.status_code} {r.text}"
            detail = r.json()
            assert detail["guest_phone"] == "08087654321", f"detail should return full phone: {detail}"
            print("C. 詳細取得: オーナー本人はフルの電話番号を取得できる: OK")

            r = await client.get(f"/api/v1/reservations/{res_id_2}")
            assert r.status_code in (401, 403), f"unauthenticated detail should be denied: {r.status_code} {r.text}"
            print("D. 詳細取得: 未認証は拒否される: OK")

            r = await client.get(f"/api/v1/reservations/{res_id_2}", headers=owner_b)
            assert r.status_code == 403, f"cross-tenant detail should be 403: {r.status_code} {r.text}"
            print("E. 詳細取得: 他tenantのオーナーは403で拒否される: OK")

            await _clear(AsyncSessionLocal, Reservation, shop_a)

            # ===== F. mask_phone_for_listの境界ケース: 電話番号なし =====
            res_id_3 = await _insert_reservation(
                shop_a, datetime(mon.year, mon.month, mon.day, 19, 30),
                guest_name="電話番号未登録", guest_phone=None, guest_email=None,
            )
            r = await client.get(f"/api/v1/reservations/shop/{shop_a}", headers=owner_a)
            assert r.status_code == 200
            item = next(x for x in r.json()["items"] if x["id"] == res_id_3)
            assert item["guest_phone"] is None
            assert item["guest_phone_masked"] is None, f"phoneがNoneならmaskedもNoneのはず: {item}"
            print("F. 一覧: 電話番号未登録(None)の予約はguest_phone_maskedもNone: OK")

            await _clear(AsyncSessionLocal, Reservation, shop_a)

            # ===== G. 予約作成直後のレスポンスは従来通りフルの電話番号 =====
            r = await client.post("/api/v1/reservations/create", json={
                "shop_id": shop_a,
                "reservation_date": datetime(mon.year, mon.month, mon.day, 19, 0).isoformat(),
                "number_of_people": 2,
                "guest_name": "作成直後確認太郎",
                "guest_phone": "07011112222",
            })
            assert r.status_code in (200, 201), f"create failed: {r.status_code} {r.text}"
            created = r.json()
            assert created["reservation"]["guest_phone"] == "07011112222", (
                f"作成直後レスポンスは一覧とは無関係に従来通りフルの電話番号を返すはず: {created}"
            )
            print("G. 予約作成直後レスポンスは_to_response()無変更のため従来通りフル電話番号: OK")

            await _clear(AsyncSessionLocal, Reservation, shop_a)

            # ===== H. ステータスフィルタ付き一覧でもマスクされる =====
            res_id_4 = await _insert_reservation(
                shop_a, datetime(mon.year, mon.month, mon.day, 21, 0),
                guest_name="キャンセル太郎", guest_phone="09099998888", status="cancelled",
            )
            r = await client.get(f"/api/v1/reservations/shop/{shop_a}", params={"status": "cancelled"}, headers=owner_a)
            assert r.status_code == 200
            body = r.json()
            assert body["total"] == 1 and body["items"][0]["id"] == res_id_4
            assert body["items"][0]["guest_phone"] is None
            assert body["items"][0]["guest_phone_masked"] == "****8888"
            print("H. ステータスフィルタ付き一覧でも同様にマスクされる: OK")

            await _clear(AsyncSessionLocal, Reservation, shop_a)

            print("\nALL RESERVATION LIST PRIVACY CHECKS PASSED")


async def _clear(AsyncSessionLocal, Reservation, shop_id):
    from sqlalchemy import select
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Reservation).filter(Reservation.shop_id == shop_id))
        for res in result.scalars().all():
            await session.delete(res)
        await session.commit()


if __name__ == "__main__":
    asyncio.run(main())
