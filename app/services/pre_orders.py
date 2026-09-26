"""
PreOrder作成のservice層。

PHASE O2: create_pre_order_with_items() — model/schema foundationの単体
テストのためだけの最小限のhelper（信頼済みデータ、unit_price/internal_note
込みでそのまま作成する）。

PHASE O3: create_public_pre_order() — Public PreOrder Create APIの共通入口
（Section5）。Web/AI Chat/AI Voice/将来PSTNのすべてが将来この関数を呼ぶ想定。
validate → determine confirmation requirement → create → items → commit →
notification、という一本の流れを1箇所に集約する（router内には書かない）。

PHASE O4: determine_pre_order_confirmation()をO3の「常にowner_confirmation_
required」から、店舗オーナーが設定したPreOrderProduct（商品ごとの自動確定
上限数量・最低リードタイム）に基づく実ルールへ拡張。判断はこのモジュール
だけに集約し、routerやAI側では一切行わない（O3から続く安全原則）。

★重要: Reservation作成ロジック（create_reservation, app/routers/
reservations.py）には一切触れない・呼び出さない。PreOrder作成は
Reservationの作成を一切トリガーしない（Section2の「人数」と「商品数量」の
境界を、構造的にも保証するための設計）。
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.pre_order import (
    PreOrder, PreOrderItem, PreOrderConfirmationStatus, PreOrderStatus, PreOrderProduct,
)
from app.models.shop import Shop
from app.schemas.pre_order import PreOrderCreate, PreOrderPublicCreateRequest, PreOrderItemPublicCreate
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


async def _get_active_products_by_name(db: AsyncSession, shop_id: str) -> Dict[str, PreOrderProduct]:
    """指定shopの有効なPreOrderProductを、name（trim済み・完全一致キー）で
    引けるdictとして返す。PreOrderProduct.nameは(shop_id, name)の一意
    インデックスを持つため、有効な商品同士でキーが衝突することはない
    （Section10: 曖昧な商品名matchingを避けるための前提）。"""
    result = await db.execute(
        select(PreOrderProduct).filter(
            PreOrderProduct.shop_id == shop_id, PreOrderProduct.is_active.is_(True)
        )
    )
    return {p.name: p for p in result.scalars().all()}


def _minutes_until(pickup_at: datetime, basis_now: datetime) -> float:
    return (pickup_at - basis_now).total_seconds() / 60.0


async def _resolve_and_validate_product_id_items(
    db: AsyncSession, shop_id: str, data: PreOrderPublicCreateRequest
) -> Dict[str, PreOrderProduct]:
    """PHASE O5 Section2/19/35: product_idが指定された明細を、product_id単独で
    厳密に解決・検証する。app/routers/reservations.pyのservice_id検証
    （create_reservation()内、Optional[Service]をservice_idで解決する箇所）と
    完全に同じテンプレート:

    - 存在しない場合と、他店舗（shop_idが一致しない）の場合を、同じ汎用
      メッセージ・同じreason_codeの404にする（他店舗の商品IDが「存在する」
      こと自体を外部に漏らさないため。tenant分離のための意図的な設計）。
    - is_active=Falseの場合は400（存在はするが現在は注文不可、という別の
      理由であることを区別する）。

    ★★★ 最重要（O5ユーザー追加指示）: product_idが見つからない場合に
    product_nameへフォールバックして代替商品を探すことは絶対に行わない。
    別商品への誤爆・他店舗商品への意図しない一致を防ぐため、ここで
    見つからなければ即座に注文全体を拒否する（HTTPExceptionをそのまま
    呼び出し元へ伝播させる）。この関数はcreate_public_pre_order()の中で
    determine_pre_order_confirmation()より前に呼ばれ、確定判定に到達する前に
    不正なproduct_idを含む注文を弾く。
    """
    resolved: Dict[str, PreOrderProduct] = {}
    for item in data.items:
        if not item.product_id or item.product_id in resolved:
            continue
        product = await db.get(PreOrderProduct, item.product_id)
        if not product or product.shop_id != shop_id:
            raise _http_error(404, "指定された商品が見つかりません", reason_code="product_unavailable")
        if not product.is_active:
            raise _http_error(400, "この商品は現在ご注文いただけません", reason_code="product_unavailable")
        resolved[item.product_id] = product
    return resolved


async def determine_pre_order_confirmation(
    db: AsyncSession,
    shop_id: str,
    data: PreOrderPublicCreateRequest,
    resolved_products_by_id: Optional[Dict[str, PreOrderProduct]] = None,
) -> str:
    """PreOrderが自動確定(confirmed)できるか、店舗確認が必要
    (owner_confirmation_required)かを決定する、唯一の判定箇所（Section12/23）。

    ★★★ PHASE O4での拡張: 店舗オーナーが設定したPreOrderProductの
    auto_confirm_max_quantity / minimum_lead_time_minutesを根拠として
    初めて自動確定を許可する（Section8）。判定はPreOrder単位のAND条件で、
    全itemsが以下をすべて満たした場合のみconfirmedを返す（Section9: 一部の
    商品だけconfirmedという状態は作らない）:

    1. 商品が特定できる（後述の2経路のいずれか）
    2. quantity <= product.auto_confirm_max_quantity
       （NULLは「自動確定しない」を意味する。Section6）
    3. 受取までのリードタイム(分) >= product.minimum_lead_time_minutes
       （NULLは「自動確定しない」を意味する。Section7）

    一つでも満たさないitemがあれば、注文全体をowner_confirmation_requiredに
    する（大口注文・条件外注文もrejectしない。Section20/28: 「大きな注文だから
    断る」のではなく「店舗確認へ回す」）。

    routerやAI側でこの判断を行ってはならない（この関数を経由しない限り
    confirmedにはならない、という構造そのものが安全装置）。

    ★★★ PHASE O5での拡張: 商品特定の経路をproduct_id優先に変更（Section20）。
    - item.product_idが指定されている場合: resolved_products_by_id（事前に
      _resolve_and_validate_product_id_items()で検証済み）からのみ解決する。
      product_nameによるfallbackは行わない（存在しない場合は安全側で
      owner_confirmation_requiredへ落とさず、そのまま拒否する。呼び出し元
      create_public_pre_order()は必ず事前検証を通しているため通常は
      到達しないが、直接この関数を呼ぶ将来の呼び出し元に対する防御として
      ここでも同じ規約を維持する）。
    - item.product_idが指定されていない場合: 従来通りproduct_nameの
      trim済み完全一致で解決する（O3/O4のAI音声受付等との後方互換）。
    """
    resolved_products_by_id = resolved_products_by_id or {}
    products_by_name = await _get_active_products_by_name(db, shop_id)
    basis_now = _pickup_basis_now()
    lead_minutes = _minutes_until(data.pickup_at, basis_now)

    item: PreOrderItemPublicCreate
    for item in data.items:
        if item.product_id:
            # PHASE O5: product_id経路。product_nameへのfallbackは絶対にしない。
            product = resolved_products_by_id.get(item.product_id)
            if product is None:
                raise _http_error(404, "指定された商品が見つかりません", reason_code="product_unavailable")
        else:
            product = products_by_name.get(item.product_name)
            if product is None:
                # Section31: 商品マスターに無い商品名（AI/自由発話経由の未知商品を
                # 含む）はrejectせず、常にowner_confirmation_requiredにする。
                return PreOrderConfirmationStatus.OWNER_CONFIRMATION_REQUIRED.value

        if product.auto_confirm_max_quantity is None:
            return PreOrderConfirmationStatus.OWNER_CONFIRMATION_REQUIRED.value
        if item.quantity > product.auto_confirm_max_quantity:
            # Section20/28: 上限超過はrejectしない。店舗確認へ回すだけ。
            return PreOrderConfirmationStatus.OWNER_CONFIRMATION_REQUIRED.value
        if product.minimum_lead_time_minutes is None:
            return PreOrderConfirmationStatus.OWNER_CONFIRMATION_REQUIRED.value
        if lead_minutes < product.minimum_lead_time_minutes:
            return PreOrderConfirmationStatus.OWNER_CONFIRMATION_REQUIRED.value

    return PreOrderConfirmationStatus.CONFIRMED.value


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

    # PHASE O4 Section12/13: 店舗単位のpre_order_enabled。グローバルfeature gate
    # (settings.PRE_ORDER_PUBLIC_CREATE_ENABLED、router層でチェック済み)とは別軸で、
    # 店舗オーナー自身が事前注文を受け付けていない場合は拒否する
    # （create_reservation()のreservations_enabledチェックと同じ設計）。
    if not shop.pre_order_enabled:
        raise _http_error(400, "この店舗は現在事前注文を受け付けていません", reason_code="pre_order_not_enabled")

    # Section14: 過去日時は明確に拒否する。Section15/25の指示により、営業時間内
    # かどうか・専用pickup hoursはO4でも検証しない（次フェーズへ分離。監査結果は
    # 最終報告のSection25参照）。
    if data.pickup_at <= _pickup_basis_now():
        raise _http_error(400, "過去の日時で受取予約をすることはできません", reason_code="time_in_past")

    # PHASE O5 Section2/19/35: product_idが指定された明細を、determine_pre_order_
    # confirmation()より前に厳密検証する。存在しない/他店舗/inactiveのいずれかで
    # あれば、確定判定に到達する前に注文全体を安全側でreject する（HTTPException
    # がそのまま呼び出し元へ伝播する）。
    resolved_products_by_id = await _resolve_and_validate_product_id_items(db, shop.id, data)

    confirmation_status = await determine_pre_order_confirmation(
        db, shop.id, data, resolved_products_by_id
    )

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
        if item.product_id:
            # PHASE O5 Section2: product_id経路では、商品名・単価ともに
            # クライアント指定の値を一切信用せず、Backendがresolved_products_by_id
            # で解決済みのPreOrderProductからsnapshotする（product_name改ざん・
            # 価格改ざんの両方をここで無効化する）。
            product = resolved_products_by_id[item.product_id]
            snapshot_product_name = product.name
            snapshot_unit_price = product.price
        else:
            # 既存O3/O4経路（AI音声等）: 従来通りクライアント指定のproduct_name
            # をそのまま保存し、unit_priceは常にNone（Section8の既存挙動を維持）。
            snapshot_product_name = item.product_name
            snapshot_unit_price = None
        pre_order.items.append(
            PreOrderItem(
                product_id=item.product_id,
                product_name=snapshot_product_name,
                quantity=item.quantity,
                variant=item.variant,
                unit_price=snapshot_unit_price,
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
