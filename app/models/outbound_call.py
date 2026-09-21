"""
Outbound AI Phase 1: 予約確定通知の架電ジョブ・ログ

このフェーズのスコープは「新しい予約が確定した際に、店舗の設定した通知先電話番号へ
AIが短く要件を伝える」1機能のみ（変更・キャンセル・リマインダー・臨時休業・
販促・休眠顧客への架電などは対象外。将来それらを追加する場合も、本モデルの
category（transactional/promotional）による構造的な分離を前提とする）。

Vonage側は本フェーズ時点で審査中のため、実際の電話発信は一切行わない
（app.services.outbound_call_provider.FakeOutboundCallProvider のみを使用）。
ここで設計しているのは、実際の発信部分を差し替えるだけで済むよう、
キュー・リトライ・冪等性・ログの「配管」を先に整えておくことが目的。

設計方針（重要）:
- reservations.py の create_reservation() から呼ばれる enqueue 処理は、
  予約の成立そのものには一切影響してはならない（非同期・失敗分離）。
  そのため OutboundCallJob の作成は予約のコミットとは別トランザクションで行い、
  例外はすべて呼び出し側（app.services.outbound_dispatch）で握りつぶす。
- 冪等性キーは、Reservation.idempotency_key で確立された設計
  （"realtime_voice:{shop_id}:{call_id}" 形式・DBのunique indexを最終防衛線とする）
  を踏襲する。ここでは "outbound:{call_type}:{reservation_id}" とし、
  同一予約に対して同じ種類の通知ジョブが二重に作られることをDBレベルで防ぐ。
- OutboundCallJob は「これから処理する/処理中の」キューであり、
  OutboundCallLog は「実際に何が起きたか」の恒久的な監査ログ。
  ジョブは完了後も削除せず残すが、オーナー向け表示や分析はログの方を使う想定。
- 個人情報（電話番号）はログにマスク済みの形でのみ保持する
  （app/routers/shops.py の既存のマスキング方針
  "****" + 下4桁 を踏襲。フルの番号はOutboundCallJob.to_phoneにのみ、
  処理に必要な期間だけ保持する）。
"""

import uuid
from datetime import datetime
import enum

from sqlalchemy import Column, String, DateTime, ForeignKey, Index, Text, Integer
from app.database import Base


class OutboundCallCategory(str, enum.Enum):
    """Transactional（予約に付随する事務連絡） / Promotional（販促・再来店促進等）の
    構造的な分離。Phase 1では TRANSACTIONAL のみを実際に使用する。
    Promotionalの実装は本フェーズのスコープ外だが、後から同じテーブル・同じ
    ワーカーに販促系ジョブを混在させて「気づいたら販促電話も自動発信していた」
    という事故を防ぐため、値そのものは最初から用意しておく。"""
    TRANSACTIONAL = "transactional"
    PROMOTIONAL = "promotional"


class OutboundCallJobStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class OutboundCallJob(Base):
    """Outbound架電のキュー（1件のジョブ = 1回の通知タスク）"""

    __tablename__ = "outbound_call_jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    shop_id = Column(String(36), ForeignKey("shops.id"), nullable=False, index=True)
    reservation_id = Column(String(36), ForeignKey("reservations.id"), nullable=True, index=True)

    # 例: "reservation_confirmed"（Phase 1で唯一実装する種類）。
    # 将来 "reservation_changed" 等を追加する際もこのカラムで区別する想定。
    call_type = Column(String(50), nullable=False, default="reservation_confirmed")

    # Transactional / Promotional の構造的分離（docstring参照）。Phase 1は常にtransactional。
    category = Column(String(20), nullable=False, default=OutboundCallCategory.TRANSACTIONAL.value)

    # 発信先（数字のみに正規化済み）。Shop.reservation_notification_phoneの
    # enqueue時点でのスナップショット。ジョブ作成後にオーナーが番号を変更しても、
    # 既にキューにあるジョブの宛先は変わらない（一貫性のため意図的な設計）。
    to_phone = Column(String(20), nullable=False)

    status = Column(String(20), nullable=False, default=OutboundCallJobStatus.PENDING.value, index=True)

    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)

    # 直近の失敗理由（エラーの種類・分類のみ。個人情報や生の例外メッセージは
    # 記録しない。app.services.outbound_call_worker参照）。
    last_error_category = Column(String(50), nullable=True)

    last_attempted_at = Column(DateTime, nullable=True)
    # リトライのバックオフ用。この時刻以降にワーカーが拾う。
    next_attempt_at = Column(DateTime, nullable=True)

    # "outbound:{call_type}:{reservation_id}" 形式。DBのunique indexで
    # 同一予約×同一種類の二重ジョブ作成を最終防衛する（docstring参照）。
    idempotency_key = Column(String(200), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ux_outbound_call_jobs_idempotency_key", "idempotency_key", unique=True),
        Index("ix_outbound_call_jobs_status_next_attempt", "status", "next_attempt_at"),
    )

    def __repr__(self):
        return f"<OutboundCallJob(id={self.id}, shop_id={self.shop_id}, status={self.status})>"


class OutboundCallLog(Base):
    """Outbound架電の恒久的な監査ログ（ジョブが完了/失敗するたびに1行追加）。

    オーナー向けの「通知履歴」表示は本フェーズでは実装しないが、将来の
    表示・分析のためのデータはこの時点から蓄積しておく。"""

    __tablename__ = "outbound_call_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    job_id = Column(String(36), ForeignKey("outbound_call_jobs.id"), nullable=True, index=True)
    shop_id = Column(String(36), ForeignKey("shops.id"), nullable=False, index=True)
    reservation_id = Column(String(36), ForeignKey("reservations.id"), nullable=True, index=True)

    call_type = Column(String(50), nullable=False)
    category = Column(String(20), nullable=False, default=OutboundCallCategory.TRANSACTIONAL.value)

    # "fake"（Phase 1で唯一使用）。将来 "vonage" 等が入る想定。
    provider = Column(String(30), nullable=False, default="fake")
    # 将来、実プロバイダーの通話ID等を保持する（Phase 1では常にNULL）。
    provider_call_id = Column(String(100), nullable=True)

    # 例: "simulated_success"（Fake providerでの模擬成功）、"failed"、"skipped"。
    result_status = Column(String(30), nullable=False)

    # 電話番号は必ずマスク済み（"****1234"のような形）でのみ保持する。生の番号は
    # このログには一切書き込まない（app/routers/shops.pyの既存マスキング方針を踏襲）。
    to_phone_masked = Column(String(20), nullable=True)

    # 実際にAIが伝える内容として組み立てたテキスト（架電シミュレーションの記録用）。
    # 個人を特定しうる情報（氏名・番号そのもの）を含みうるため、将来オーナー向けに
    # 表示する際は取り扱いに注意する（本フェーズではオーナー向け表示自体は未実装）。
    message_text = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_outbound_call_logs_shop_created", "shop_id", "created_at"),
    )

    def __repr__(self):
        return f"<OutboundCallLog(id={self.id}, shop_id={self.shop_id}, result_status={self.result_status})>"
