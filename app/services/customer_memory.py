"""
Outbound AI Phase 4A: Customer Memory Foundation（店舗単位の顧客識別ロジック）

役割分担（仕様書Phase 4A section5を踏襲）:
- OpenAI Realtime      = 会話・判断
- このモジュール（Python） = lookup / upsertのbusiness logic / privacy control
- app.models.customer_memory.CustomerMemory = 恒久的な記憶（DB）

呼び出し元:
- app.routers.realtime_voice.find_customer_tool（読み取り専用のlookup。
  本人確認前にAIへ渡してよい最小限の情報だけを返す＝Privacy Gate）
- app.routers.realtime_voice.create_reservation_tool（予約成立後にのみ、
  安全な冪等upsertを行う。Web予約・チャット予約・管理画面予約からは
  一切呼ばれない＝既存のcreate_reservation()本体には一切手を加えない）

設計方針（重要・必ず守ること）:
- 「電話番号 = 本人確定」とは扱わない。同一電話番号で氏名が異なる場合、
  既存レコードを上書きせず、別レコードとして共存させる（家族共用電話・
  会社代表番号等を想定。仕様書section13）。
- 保存するのは display_name / normalized_phone / 初回・最終利用日時 /
  来店回数 / 直近予約への参照のみ。詳細プロフィール・AIによる推測・
  医療情報・会話全文は一切保存しない（仕様書section9-11）。
- 予約リトライ・function_call replayで同じCustomer Memoryが何件も
  作られたり、visit_countが二重加算されたりしないよう、reservation.id
  そのものを冪等性の根拠とする（同じreservation_idに対する2回目以降の
  upsert呼び出しは何もしない）。
- DB一意制約 ux_customer_memories_shop_phone_name
  (shop_id, normalized_phone, normalized_name_key) を最終防衛線とし、
  同時多重予約による競合はIntegrityErrorをcatchして安全に吸収する
  （Reservation.idempotency_keyと同じ設計パターン）。
- セッション分離: 呼び出し元（create_reservation_tool）のDBセッション・
  ORMオブジェクトを共有せず、プリミティブな値のみを受け取って専用の
  新しいDBセッションで完結させる（app.services.outbound_dispatchと
  同じ理由・同じパターン。呼び出し元のrollbackに巻き込まれない、
  呼び出し元のreservation/shopオブジェクトの状態に影響を与えない）。
- この処理のどのような失敗も、予約作成の成功可否に一切影響してはならない
  （例外は必ずこのモジュール内でログに残した上で握りつぶす）。
"""

import logging
import re
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import app.database as db_module
from app.models.customer_memory import CustomerMemory
from app.schemas.shop import normalize_jp_phone_national

logger = logging.getLogger("receptra.customer_memory")


def _normalize_name_key(name: Optional[str]) -> str:
    """
    氏名の重複排除用キー。app.routers.realtime_voice._normalize_name_for_dedup
    と同じ考え方（前後の空白除去・連続する空白の1つへの整理のみ）を踏襲するが、
    routerからserviceへの逆方向import（循環import）を避けるため、あえて
    この場所に独立して実装する（意図的な重複。処理内容は完全に同一）。
    漢字⇔カナ変換・読み仮名推測等の推測的な正規化は一切行わない。
    """
    if not name:
        return ""
    collapsed = re.sub(r"[ 　\t\r\n]+", " ", name)
    return collapsed.strip()


async def find_customer_candidate(shop_id: str, phone: str) -> Optional[str]:
    """
    Privacy Gate: 本人確認前にAIへ渡してよい最小限の情報（候補の表示名のみ）を
    返す読み取り専用のlookup。電話番号が正規化できない、該当が無い、DB障害等
    どの場合も例外を外へ伝播させず、Noneを返す（呼び出し元は安全側にフォール
    バックしてnot_found扱いとする）。

    同一電話番号に複数の異なる氏名が既に記録されている場合（家族共用電話等）は、
    どれが「正しい」候補かを推測せず、最も直近に利用された記録の氏名を候補として
    返す（間違っていても、この後の「○○様でよろしいですか」という会話上の確認で
    お客様自身が訂正できるため、実害は無い設計）。
    """
    normalized_phone = normalize_jp_phone_national(phone)
    if normalized_phone is None:
        return None

    try:
        async with db_module.AsyncSessionLocal() as db:
            result = await db.execute(
                select(CustomerMemory)
                .filter(
                    CustomerMemory.shop_id == shop_id,
                    CustomerMemory.normalized_phone == normalized_phone,
                )
                .order_by(CustomerMemory.last_seen_at.desc())
                .limit(1)
            )
            row = result.scalars().first()
            return row.display_name if row else None
    except Exception:
        logger.exception(
            "Customer Memory lookupに失敗しました（安全側でnot_found扱いにします） shop_id=%s",
            shop_id,
        )
        return None


async def find_customer_candidate_record(shop_id: str, phone: str) -> Optional[dict]:
    """
    Outbound AI Phase 4B追加: find_customer_candidate()と全く同じ検索ロジックだが、
    display_nameだけでなく内部id（CustomerMemory.id）も返す。

    重要: この関数の戻り値（id含む）はAIへ絶対に渡してはならない。呼び出し元
    （app.routers.realtime_voice.find_customer_tool）は、このidを
    app.services.customer_context.issue_candidate()へ渡してサーバー側の
    本人確認状態（Phase4B）に紐付けるためだけに使い、レスポンススキーマ
    （FindCustomerToolResponse）にはdisplay_nameしか含めない設計を維持する。

    find_customer_candidate()自体は変更しない（Phase4Aで検証済みの挙動に
    一切手を加えないため）。両関数は同じ検索条件を意図的に重複させている。

    戻り値はORMオブジェクトではなく、必要な値だけを持つ軽量なdictにする
    （DBセッションをasync withブロックの外へ持ち出さないため）。
    """
    normalized_phone = normalize_jp_phone_national(phone)
    if normalized_phone is None:
        return None

    try:
        async with db_module.AsyncSessionLocal() as db:
            result = await db.execute(
                select(CustomerMemory)
                .filter(
                    CustomerMemory.shop_id == shop_id,
                    CustomerMemory.normalized_phone == normalized_phone,
                )
                .order_by(CustomerMemory.last_seen_at.desc())
                .limit(1)
            )
            row = result.scalars().first()
            if row is None:
                return None
            return {"id": row.id, "display_name": row.display_name}
    except Exception:
        logger.exception(
            "Customer Memory lookup(record)に失敗しました（安全側でnot_found扱いにします） shop_id=%s",
            shop_id,
        )
        return None


async def upsert_customer_memory_for_reservation(
    shop_id: str,
    reservation_id: str,
    guest_name: Optional[str],
    guest_phone: Optional[str],
) -> None:
    """
    予約成立後にのみ呼び出す、安全な冪等upsert。

    - guest_name/guest_phoneのどちらか、または電話番号の正規化に失敗した場合は
      何もしない（Customer Memoryの主キー材料が揃わないため、無理に作成しない）。
    - 既にこのreservation_idに対して処理済みの場合は何もしない（Realtime Voiceの
      retry/replayでvisit_countが二重加算されることを防ぐ）。
    - 同一(shop_id, normalized_phone, normalized_name_key)の既存レコードが
      あれば来店回数・最終利用日時・直近予約への参照のみ更新する。名前が
      異なる場合は既存レコードを上書きせず、新規レコードとして追加する
      （仕様書section13の「田中太郎→田中花子で勝手に上書きしない」要件）。
    """
    if not guest_name or not guest_phone:
        return
    normalized_phone = normalize_jp_phone_national(guest_phone)
    if normalized_phone is None:
        return
    normalized_name_key = _normalize_name_key(guest_name)
    if not normalized_name_key:
        return
    display_name = guest_name.strip()

    try:
        async with db_module.AsyncSessionLocal() as db:
            already = await db.execute(
                select(CustomerMemory.id)
                .filter(CustomerMemory.last_reservation_id == reservation_id)
                .limit(1)
            )
            if already.scalar_one_or_none() is not None:
                # 既にこのreservationに対してCustomer Memoryを記録済み
                # （retry/replayによる二重呼び出し）。何もしない。
                return

            await _upsert_once(
                db, shop_id, normalized_phone, normalized_name_key, display_name, reservation_id
            )
    except Exception:
        logger.exception(
            "Customer Memoryのupsertに失敗しました（予約作成自体は成功済みのため処理を継続します） "
            "shop_id=%s reservation_id=%s",
            shop_id, reservation_id,
        )


async def _upsert_once(
    db, shop_id: str, normalized_phone: str, normalized_name_key: str,
    display_name: str, reservation_id: str,
) -> None:
    now = datetime.utcnow()
    result = await db.execute(
        select(CustomerMemory).filter(
            CustomerMemory.shop_id == shop_id,
            CustomerMemory.normalized_phone == normalized_phone,
            CustomerMemory.normalized_name_key == normalized_name_key,
        )
    )
    row = result.scalar_one_or_none()

    if row is not None:
        row.last_seen_at = now
        row.visit_count = (row.visit_count or 0) + 1
        row.last_reservation_id = reservation_id
        row.updated_at = now
        await db.commit()
        return

    row = CustomerMemory(
        id=str(uuid.uuid4()),
        shop_id=shop_id,
        normalized_phone=normalized_phone,
        normalized_name_key=normalized_name_key,
        display_name=display_name,
        first_seen_at=now,
        last_seen_at=now,
        visit_count=1,
        last_reservation_id=reservation_id,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        # 同時多重予約（同じ電話番号・同じ氏名）によるUNIQUE制約競合。
        # 既に別トランザクションが同じCustomerMemory行を作成済みのため、
        # rollbackして更新側に回る（Reservation.idempotency_keyと同じ思想）。
        await db.rollback()
        result = await db.execute(
            select(CustomerMemory).filter(
                CustomerMemory.shop_id == shop_id,
                CustomerMemory.normalized_phone == normalized_phone,
                CustomerMemory.normalized_name_key == normalized_name_key,
            )
        )
        row = result.scalar_one_or_none()
        if row is not None:
            row.last_seen_at = now
            row.visit_count = (row.visit_count or 0) + 1
            row.last_reservation_id = reservation_id
            row.updated_at = now
            await db.commit()
