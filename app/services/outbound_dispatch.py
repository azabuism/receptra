"""
Outbound AI Phase 1: 予約作成 → Outbound通知ジョブへの受け渡し

app.routers.reservations.create_reservation() から、予約が新規に成立した
直後（DBコミット後）にのみ呼び出される。設計上の絶対要件:

- 非同期・失敗分離（non-blocking / failure-isolated）: この関数がどのような
  理由であれ例外を外へ伝播させてはならない。呼び出し時点で予約は既に正常に
  コミット済みであり、Outbound側の都合（DB一時エラー・想定外の例外等）で
  予約作成のレスポンスやステータスコードに影響を与えることは絶対にあっては
  ならない。
- 冪等性: 同一予約に対して同じ種類の通知ジョブが二重に作られないよう、
  OutboundCallJob.idempotency_key のDB一意制約を最終防衛線とする
  （Reservation.idempotency_keyと同じ設計パターン）。
- セッション分離: 呼び出し元（create_reservation）のDBセッション・ORMオブジェクトは
  一切共有せず、プリミティブな値のみを受け取って専用の新しいDBセッションで完結させる。
  SQLAlchemyのrollbackはセッション内の全オブジェクトをexpireするため、もし
  呼び出し元のセッションを共有していると、ここでの（想定内・想定外いずれかの）
  rollbackが、呼び出し元がこの後 reservation の属性へアクセスする際に
  MissingGreenlet例外を引き起こしうる（実際にsmoke testで再現・確認した挙動）。
  専用セッションにすることで、この関数の内部で何が起きても、呼び出し元の
  reservation/shopオブジェクトの状態には一切影響しない。
- import方法の注意: `from app.database import AsyncSessionLocal` のように
  名前を直接importすると、この行が実行された時点（＝モジュール読み込み時。
  app.main起動時のlifespan()でinit_db()が呼ばれるより前）の値（None）が
  固定でコピーされてしまい、その後init_db()がapp.database.AsyncSessionLocal
  を実際のセッションファクトリに差し替えても、ここで束縛した名前には反映されない
  （app/routers/auth.pyに同じ書き方の未使用importが残っているが、実際に
  呼ばれていないため問題が表面化していないだけ）。そのため必ず
  `import app.database as db_module` した上で `db_module.AsyncSessionLocal`
  として都度参照する（app/deps.pyと同じ安全なパターン）。
"""

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import app.database as db_module
from app.models.outbound_call import OutboundCallJob, OutboundCallCategory, OutboundCallJobStatus

logger = logging.getLogger("receptra.outbound.dispatch")


async def enqueue_reservation_confirmed_call(
    shop_id: str,
    reservation_id: str,
    notification_enabled: bool,
    notification_phone: str | None,
) -> None:
    """予約確定通知のOutboundジョブをキューへ積む。

    通知が無効、または通知先電話番号が未設定の店舗では何もしない（コストゼロ）。
    """
    if not notification_enabled or not notification_phone:
        return

    try:
        async with db_module.AsyncSessionLocal() as db:
            idempotency_key = f"outbound:reservation_confirmed:{reservation_id}"
            job = OutboundCallJob(
                shop_id=shop_id,
                reservation_id=reservation_id,
                call_type="reservation_confirmed",
                category=OutboundCallCategory.TRANSACTIONAL.value,
                to_phone=notification_phone,
                status=OutboundCallJobStatus.PENDING.value,
                idempotency_key=idempotency_key,
            )
            db.add(job)
            try:
                await db.commit()
            except IntegrityError:
                # 同一予約に対する重複enqueue（idempotency_keyのunique index）。
                # 想定内の競合なので握りつぶす（例: リトライやレース条件）。
                await db.rollback()
    except Exception:
        # ここでの失敗は、呼び出し元の予約作成の成功に一切影響してはならない。
        # 専用セッションのため、呼び出し元のセッション状態には一切波及しない。
        logger.exception(
            "Outbound通知ジョブのenqueueに失敗しました（予約作成自体は成功済みのため処理を継続します） "
            "shop_id=%s reservation_id=%s",
            shop_id, reservation_id,
        )


async def enqueue_callback_requested_call(
    shop_id: str,
    callback_request_id: str,
    notification_enabled: bool,
    notification_phone: str | None,
) -> str | None:
    """Human Handoff基盤: 折り返し依頼(CallbackRequest)の担当者向け電話通知の
    Outboundジョブをキューへ積む。enqueue_reservation_confirmed_call()と全く同じ
    設計方針（非同期・失敗分離・専用DBセッション・idempotency_keyのDB一意制約を
    最終防衛線とする）を踏襲する。

    通知が無効、または通知先電話番号が未設定の店舗では何もしない（コストゼロ）。
    reservation_id列は使わない（callback_requested種別のジョブは予約に紐付かない
    ため常にNoneのまま）。callback_request_idとの対応づけは、このジョブのidを
    呼び出し元がCallbackRequest.outbound_call_job_idへ保存することで行う
    （OutboundCallJob側に逆参照カラムを追加すると2テーブル間の循環FKになり、
    店舗削除時のカスケード順序が複雑化するため、意図的に単方向のみにしている）。

    戻り値: enqueueに成功した（または既に同一キーで存在した）OutboundCallJob.id。
    通知が無効/未設定、またはenqueue自体が失敗した場合はNone。
    """
    if not notification_enabled or not notification_phone:
        return None

    try:
        async with db_module.AsyncSessionLocal() as db:
            idempotency_key = f"outbound:callback_requested:{callback_request_id}"
            job = OutboundCallJob(
                shop_id=shop_id,
                reservation_id=None,
                call_type="callback_requested",
                category=OutboundCallCategory.TRANSACTIONAL.value,
                to_phone=notification_phone,
                status=OutboundCallJobStatus.PENDING.value,
                idempotency_key=idempotency_key,
            )
            db.add(job)
            try:
                await db.commit()
            except IntegrityError:
                # 同一CallbackRequestに対する重複enqueue（idempotency_keyのunique index）。
                # 想定内の競合なので握りつぶし、先に成立していたジョブのidを返す。
                await db.rollback()
                result = await db.execute(
                    select(OutboundCallJob).filter(OutboundCallJob.idempotency_key == idempotency_key)
                )
                existing = result.scalar_one_or_none()
                return existing.id if existing else None
            return job.id
    except Exception:
        # ここでの失敗は、呼び出し元（request_callback Tool）のCallbackRequest保存の
        # 成功に一切影響してはならない。専用セッションのため、呼び出し元の
        # セッション状態には一切波及しない。
        logger.exception(
            "Outbound通知ジョブ(callback_requested)のenqueueに失敗しました"
            "（折り返し受付自体は成功済みのため処理を継続します） "
            "shop_id=%s callback_request_id=%s",
            shop_id, callback_request_id,
        )
        return None
