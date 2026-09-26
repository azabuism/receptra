"""
PreOrder作成のservice層。

PHASE O2: create_pre_order_with_items() — model/schema foundationの単体
テストのためだけの最小限のhelper（信頼済みデータ、unit_price/internal_note
込みでそのまま作成する）。

PHASE O3: create_public_pre_order() — Public PreOrder Create APIの共通入口
（Section5）。Web/AI Chat/AI Voice/将来PSTNのすべてが将来この関数を呼ぶ想定。
validate → determine confirmation requirement → create → items → commit →
notification、という一本の流れを1箇所に集約する（router内には書かない）。

★重要: Reservation作成ロジック（create_reservation, app/routers/
reservations.py）には一切触れない・呼び出さない。PreOrder作成は
Reservationの作成を一切トリガーしない（Section2の「人数」と「商品数量」の
境界を、構造的にも保証するための設計）。
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.pre_order import PreOrder, PreOrderItem, PreOrderConfirmationStatus, PreOrderStatus
from app.models.shop import Shop
from app.schemas.pre_order import PreOrderCreate, PreOrderPublicCreateRequest
from app.services.owner_notifications import notify_pre_order_created


async def create_pre_order_with_items(session: AsyncSession, data: PreOrderCreate) -> PreOrder:
    """PreOrderCreate（Pydanticバリデーション済み）から、PreOrderとその配下の
    PreOrderItem群を1トランザクションで作成する。

    「商品40個だから40行作る」ようなことはせず、data.items配列の要素数
    どおりの明細行だけを作る（Section25）。"""
    pre_order = PreOrder(
        shop_id=data.shop_id,
        customer_name=data.customer_name,
        customer_phone=data.customer_phone,
        customer_email=data.customer_email,
        pickup_at=data.pickup_at,
        customer_note=data.customer_note,
        internal_note=data.internal_note,
    )
    for item in data.items:
        pre_order.items.append(
            PreOrderItem(
                product_name=item.product_name,
                quantity=item.quantity,
                variant=item.variant,
                unit_price=item.unit_price,
                item_note=item.item_note,
            )
        )
    session.add(pre_order)
    await session.commit()
    await session.refresh(pre_order)
    return pre_order


# ============================================================
# PHASE O3: Pre-Order Backend Acceptance Engine
# ============================================================

# app/routers/reservations.pyの_JSTと全く同じ基準（JST-local-naive）。
# 既存コードベースの規約（app/services/realtime_voice_ai.pyも独自にJST定数を
# 持つ）に合わせ、モジュールごとに同じ小さな定数を持つ（他モジュールの
# private関数をimportして結合を増やさない）。
_JST = timezone(timedelta(hours=9))


def _pickup_basis_now() -> datetime:
    """pickup_at（JST-local-naive）と同じ基準の「現在時刻」を返す。
    app/routers/reservations.py の _reservation_basis_now() と同じ考え方
    （Section14: 過去日時判定にdatetime.utcnow()を直接使わない）。"""
    return datetime.now(_JST).replace(tzinfo=None)


def _http_error(status_code: int, detail: str, reason_code: Optional[str] = None) -> HTTPException:
    """app/routers/reservations.py の _http_error() と同じпаターン
    （.reason_code属性を追加し、将来AI/Web側が文字列一致に頼らず機械可読な
    理由で判定できるようにする）。"""
    exc = HTTPException(status_code=status_code, detail=detail)
    exc.reason_code = reason_code
    return exc


def determine_pre_order_confirmation(data: PreOrderPublicCreateRequest) -> str:
    """PreOrderが自動確定(confirmed)できるか、店舗確認が必要
    (owner_confirmation_required)かを決定する、唯一の判定箇所（Section12）。

    ★★★ Section2/13/32の安全原則: Product master・quantity threshold・
    lead time・inventory・shop settingsのいずれもまだ存在しない（O3時点）ため、
    根拠のない自動確定は絶対に行わない。常にOWNER_CONFIRMATION_REQUIREDを
    返す。将来O4以降でこれらの根拠が揃った場合にのみ、この関数の中身を
    拡張してCONFIRMEDを返す分岐を追加すること。routerやAI側でこの判断を
    行ってはならない（この関数を経由しない限りconfirmedにはならない、という
    構造そのものが安全装置）。
    """
    return PreOrderConfirmationStatus.OWNER_CONFIRMATION_REQUIRED.value


async def get_pre_order_by_idempotency_key(db: AsyncSession, idempotency_key: str) -> Optional[PreOrder]:
    """app/routers/reservations.py の _get_reservation_by_idempotency_key() と
    同じ役割。指定したidempotency_keyを持つ既存PreOrderを1件取得する。"""
    result = await db.execute(
        select(PreOrder).options(selectinload(PreOrder.items)).filter(PreOrder.idempotency_key == idempotency_key)
    )
    return result.scalar_one_or_none()


def _item_summary_tuples(pre_order: PreOrder) -> List[Tuple[str, int]]:
    return [(item.product_name, item.quantity) for item in pre_order.items]


async def create_public_pre_order(db: AsyncSession, data: PreOrderPublicCreateRequest) -> PreOrder:
    """Public PreOrder Create APIの共通入口（Section5）。

    流れ: idempotency事前チェック → shop検証 → pickup_at過去日時チェック →
    confirmation判定 → PreOrder+Items作成 → commit（IntegrityError時は
    idempotency競合として再取得） → 通知生成（失敗分離・注文はrollbackしない）。

    ★価格・internal_noteの安全性（Section8/9）: PreOrderPublicCreateRequest/
    PreOrderItemPublicCreateにはそもそもunit_price/internal_noteフィールドが
    存在しない（呼び出し元が仮に辞書をこじ開けて渡そうとしても、Pydantic
    schemaの時点でextra="forbid"により拒否済み）。本関数もunit_price=None・
    internal_note=Noneを常に明示的に指定し、Publicリクエストの値をそのまま
    使うことは構造上あり得ない。

    ★Web/AI Chat/AI Voice/将来PSTNの複数経路から呼ばれる想定のため、
    HTTPException以外の呼び出し元（将来のRealtime Tool等）はこの関数が
    投げるHTTPExceptionの.reason_code属性を見て適切に処理すること
    （app.routers.reservationsの既存パターンと同じ）。
    """
    # Phase3B/Section19と同じ冪等性チェック: 既に同一キーで成立済みのPreOrderが
    # あれば、新たに作成せずそれをそのまま返す（再検証は行わない）。
    if data.idempotency_key:
        existing = await get_pre_order_by_idempotency_key(db, data.idempotency_key)
        if existing:
            return existing

    shop = await db.get(Shop, data.shop_id)
    if not shop or not shop.is_active:
        raise _http_error(404, "指定された店舗が見つかりません", reason_code="temporarily_unavailable")

    # Section14: 過去日時は明確に拒否する。Section15の指示により、営業時間内
    # かどうかはO3では検証しない（pickup hoursはO4で設計予定）。
    if data.pickup_at <= _pickup_basis_now():
        raise _http_error(400, "過去の日時で受取予約をすることはできません", reason_code="time_in_past")

    confirmation_status = determine_pre_order_confirmation(data)

    pre_order = PreOrder(
        shop_id=data.shop_id,
        customer_name=data.customer_name,
        customer_phone=data.customer_phone,
        customer_email=data.customer_email,
        pickup_at=data.pickup_at,
        customer_note=data.customer_note,
        internal_note=None,  # Section9: Public requestからinternal_noteを受け取らない
        status=PreOrderStatus.PENDING.value,
        confirmation_status=confirmation_status,
        idempotency_key=data.idempotency_key,
    )
    for item in data.items:
        pre_order.items.append(
            PreOrderItem(
                product_name=item.product_name,
                quantity=item.quantity,
                variant=item.variant,
                unit_price=None,  # Section8: Public requestからunit_priceを受け取らない
            )
        )
    db.add(pre_order)

    try:
        await db.commit()
    except IntegrityError:
        # Section19/44: 同一idempotency_keyでの同時多重INSERT。Reservationの
        # create_reservation()と全く同じ最終防衛線（DB一意インデックス）。
        await db.rollback()
        if data.idempotency_key:
            existing = await get_pre_order_by_idempotency_key(db, data.idempotency_key)
            if existing:
                return existing
        raise _http_error(500, "事前注文の受付に失敗しました。時間をおいて再度お試しください", reason_code="temporarily_unavailable")

    result = await db.execute(
        select(PreOrder).options(selectinload(PreOrder.items)).filter(PreOrder.id == pre_order.id)
    )
    pre_order = result.scalar_one()

    # Section23/25: commit成功後にのみ通知を生成する。notify_pre_order_created()
    # 自身が専用DBセッション・失敗分離を保証しているため、ここでの例外送出や
    # このdbセッションへの影響は一切発生しない（PreOrderはrollbackされない）。
    await notify_pre_order_created(
        shop_id=pre_order.shop_id,
        pre_order_id=pre_order.id,
        customer_name=pre_order.customer_name,
        pickup_at=pre_order.pickup_at,
        items=_item_summary_tuples(pre_order),
        confirmation_status=pre_order.confirmation_status,
    )

    return pre_order
