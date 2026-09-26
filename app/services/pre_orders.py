"""
PHASE O2: PreOrder作成の最小限のservice helper。

まだPublic/Owner APIエンドポイントは実装しない（仕様書Section20）。本関数は
model/schema foundationの単体テストが、PreOrderとその配下の複数
PreOrderItemを1トランザクションで安全に作成できることを確認するためだけに
存在する。O3でAPIを実装する際、このhelperをそのまま（または拡張して）
利用する想定。

★重要: Reservation作成ロジック（create_reservation, app/routers/
reservations.py）には一切触れない・呼び出さない。PreOrder作成は
Reservationの作成を一切トリガーしない（Section2の「人数」と「商品数量」の
境界を、構造的にも保証するための設計）。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pre_order import PreOrder, PreOrderItem
from app.schemas.pre_order import PreOrderCreate


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
