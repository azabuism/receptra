"""
Phase N2: LINE通知配信のバックグラウンドワーカー

app.services.outbound_call_worker と全く同じ設計思想を踏襲する
(Redis/Celery等の新規インフラは導入しない。app.main.py の lifespan() から
asyncio.create_task() で起動する単純なポーリングループ)。

【N1との完全分離について】
このワーカーは OwnerNotificationEvent テーブルを「読み取るだけ」で、
一切書き込まない（read_atも含め、N1側のどの列も変更しない）。書き込むのは
LineNotificationDelivery テーブルのみ。app/routers/reservations.py,
app/routers/realtime_voice.py, app/services/owner_notifications.py,
app/models/owner_notification.py には一切変更を加えていない
（呼び出しフックの追加すら行っていない）。これにより「N1=Event生成、
N2=Delivery」という分離を、コードの構造そのもので保証する。

配信判定ロジック:
1. OwnerNotificationEvent のうち、LineNotificationDelivery行がまだ存在しない
   ものだけを対象にする（一意制約が最終防衛線）。
2. Shop -> ShopLineNotificationSetting を見て、enabledかつ該当event_typeの
   トグルがONでなければ即座にSKIPPEDとして確定する（再スキャンし続けない）。
3. Shop -> Tenant -> OwnerLineConnection が存在し、revoked_atがNoneでなければ
   配信対象。無ければSKIPPED。
4. 配信本文は OwnerNotificationEvent.title / .message をそのまま使う
   （N1で既にPIIを含まないことが監査・テスト済みのテンプレートであるため、
   ここで新たな文面ロジックを作らずそのまま再利用するのが最も安全）。
   本文には認証必須の固定ディープリンク（お知らせ一覧へのURL）のみを付加する
   （related_entity_id・LINE userId・トークン・秘密情報は一切含めない）。
5. 配信失敗はFAILEDとして記録するのみで、Reservation/CallbackRequest/
   OwnerNotificationEventには一切影響しない。リトライは行わない
   （spec上「リトライ可能な設計」は推奨だが必須ではなく、本フェーズは
   シンプルな単純化を優先。次回ポーリング時に再送はしない
   =重複防止の一意制約と矛盾しないよう、1回試行してPENDING->SENT/FAILEDで確定する）。
"""

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import app.database as db_module
from app.config import get_settings
from app.models.owner_notification import OwnerNotificationEvent, OwnerNotificationEventType
from app.models.line_notification import (
    LineNotificationDelivery,
    LineNotificationDeliveryStatus,
    OwnerLineConnection,
    ShopLineNotificationSetting,
)
from app.models.shop import Shop
from app.services.line_message_provider import get_line_message_provider

logger = logging.getLogger("receptra.line.worker")

# オーナー向け画面への固定ディープリンク。認証必須（未ログインならログイン画面へ
# リダイレクトされる）。related_entity_id等は含めない
# （既存のお知らせ一覧UIが個別項目への直接ジャンプに対応していないため）。
_NOTICES_DEEP_LINK_BASE = "https://receptra.bariyon.com/shop-manage.html"


async def _build_message_text(event: OwnerNotificationEvent) -> str:
    link = f"{_NOTICES_DEEP_LINK_BASE}?id={event.shop_id}#notices"
    return f"{event.title}\n{event.message}\n\n{link}"


async def _process_one_event(db, event: OwnerNotificationEvent) -> None:
    """1件のイベントについてLineNotificationDelivery行を確定させる。
    例外はこの関数の外に伝播させない（1件の異常が他のイベント処理を止めないため）。
    OwnerNotificationEvent自体は一切書き換えない。"""

    delivery = LineNotificationDelivery(
        notification_event_id=event.id,
        status=LineNotificationDeliveryStatus.PENDING.value,
    )
    db.add(delivery)
    try:
        await db.commit()
    except IntegrityError:
        # 既に他プロセス/前回のポーリングで処理済み（一意制約による重複防止）。
        await db.rollback()
        return

    try:
        shop = await db.get(Shop, event.shop_id)
        if shop is None:
            delivery.status = LineNotificationDeliveryStatus.SKIPPED.value
            delivery.error_category = "shop_not_found"
            await db.commit()
            return

        setting_result = await db.execute(
            select(ShopLineNotificationSetting).filter(ShopLineNotificationSetting.shop_id == shop.id)
        )
        setting = setting_result.scalar_one_or_none()

        # 設定行が存在しない店舗は「LINE通知は一度も設定されていない」状態
        # =未設定時のデフォルト方針(action_requiredのみON、reservationはOFF)を適用する。
        if setting is None:
            eligible = event.event_type == OwnerNotificationEventType.OWNER_ACTION_REQUIRED.value
        elif not setting.enabled:
            eligible = False
        elif event.event_type == OwnerNotificationEventType.OWNER_ACTION_REQUIRED.value:
            eligible = setting.action_required_enabled
        elif event.event_type == OwnerNotificationEventType.RESERVATION_CREATED.value:
            eligible = setting.reservation_enabled
        else:
            eligible = False

        if not eligible:
            delivery.status = LineNotificationDeliveryStatus.SKIPPED.value
            delivery.error_category = "notification_disabled"
            await db.commit()
            return

        conn_result = await db.execute(
            select(OwnerLineConnection).filter(
                OwnerLineConnection.tenant_id == shop.tenant_id,
                OwnerLineConnection.revoked_at.is_(None),
            )
        )
        connection = conn_result.scalar_one_or_none()
        if connection is None:
            delivery.status = LineNotificationDeliveryStatus.SKIPPED.value
            delivery.error_category = "not_connected"
            await db.commit()
            return

        message_text = await _build_message_text(event)
        provider = get_line_message_provider()
        result = await provider.push_message(connection.line_user_id, message_text)

        if result.success:
            delivery.status = LineNotificationDeliveryStatus.SENT.value
            delivery.error_category = None
        else:
            delivery.status = LineNotificationDeliveryStatus.FAILED.value
            delivery.error_category = result.error_category or "unknown"
        await db.commit()
    except Exception:
        logger.exception(
            "LINE通知配信処理中に予期しない例外が発生しました event_id=%s "
            "（OwnerNotificationEvent本体には一切影響しません）",
            event.id,
        )
        try:
            delivery.status = LineNotificationDeliveryStatus.FAILED.value
            delivery.error_category = "worker_exception"
            await db.commit()
        except Exception:
            await db.rollback()


async def _poll_once() -> int:
    """1回分のポーリング。処理したイベント件数を返す。

    app.database.AsyncSessionLocal は init_db() 実行後に差し替えられる
    モジュール属性のため、db_module.AsyncSessionLocal として都度参照する
    （app.services.outbound_dispatch / owner_notifications と同じ理由）。
    """
    processed = 0
    async with db_module.AsyncSessionLocal() as db:
        stmt = (
            select(OwnerNotificationEvent)
            .outerjoin(
                LineNotificationDelivery,
                LineNotificationDelivery.notification_event_id == OwnerNotificationEvent.id,
            )
            .filter(LineNotificationDelivery.id.is_(None))
            .order_by(OwnerNotificationEvent.created_at.asc())
            .limit(20)
        )
        result = await db.execute(stmt)
        events = result.scalars().all()

        for event in events:
            try:
                await _process_one_event(db, event)
                processed += 1
            except Exception:
                logger.exception("LINE通知ワーカーのイベント処理で予期しない例外が発生しました event_id=%s", event.id)
                try:
                    await db.rollback()
                except Exception:
                    pass
    return processed


async def run_line_notification_worker_loop() -> None:
    """app.main.py の lifespan() から起動される常駐ループ。
    app.services.outbound_call_worker.run_outbound_worker_loop と同じ構造。"""
    import asyncio

    settings = get_settings()
    if not settings.LINE_NOTIFICATION_WORKER_ENABLED:
        logger.info("LINE_NOTIFICATION_WORKER_ENABLED=Falseのため、LINE通知ワーカーは起動しません")
        return

    logger.info(
        "LINE通知ワーカーを起動しました interval=%ds",
        settings.LINE_NOTIFICATION_WORKER_POLL_INTERVAL_SECONDS,
    )
    try:
        while True:
            try:
                await _poll_once()
            except Exception:
                logger.exception("LINE通知ワーカーのポーリングで予期しない例外が発生しました")
            await asyncio.sleep(settings.LINE_NOTIFICATION_WORKER_POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        logger.info("LINE通知ワーカーを停止しました")
        raise
