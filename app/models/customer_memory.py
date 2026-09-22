"""
Outbound AI Phase 4A: Customer Memory Foundation（店舗単位の「常連認識」基盤）

設計方針（重要・必ず守ること）:
- 既存の Customer モデル（app/models/customer.py）は tenant_id 単位・email必須
  （unique(tenant_id, email)）の「マイページ会員アカウント」であり、Realtime Voice
  経由のゲスト予約（emailを持たないことが大半）や、店舗単位で顧客を隔離したいという
  Phase 4Aの要件のどちらにもそのままでは適合しない（調査結果。既存Customer.emailは
  nullable=Falseのため、電話のみの相手には作成不能）。そのため既存Customerを拡張・
  再利用せず、本ファイルで新規かつ最小限のテーブルを追加する（Base.metadata.create_all()
  で自動作成される新規テーブル。既存customersテーブル・customersルーター/スキーマには
  一切手を加えない）。
- 既存 VoiceCallLog（app/models/voice_call_log.py）は旧Vonage IVR用の「通話内容の
  要約テキスト」を蓄積するための tenant_id 単位（nullable）のログであり、目的
  （業務電話のトリアージ）も粒度（tenant単位・自由記述summary）もPhase 4Aが必要と
  する「店舗単位・構造化された最小限の顧客識別情報」とは異なるため、混同せず流用しない。
- shop_id 単位で完全に隔離する（Tenant内の複数店舗間で顧客記憶を共有しない）。
  同一tenant内の店舗Aと店舗Bが同じ電話番号を持つ客を記録しても、互いに参照できない
  設計にするため、あえて tenant_id 列を持たない（shop_idからtenant_idは
  Shop経由で辿れるため、tenant単位の集計が将来必要になっても列追加で対応可能）。
- 「電話番号 = 本人確定」という前提を置かない（同じ電話番号を持つ別人が実在しうる：
  家族共用電話・会社代表番号等）。そのため一意制約は
  (shop_id, normalized_phone, normalized_name_key) の3列複合とし、同じ電話番号でも
  氏名が異なれば別レコードとして共存できるようにする（田中太郎と田中花子が同じ
  電話番号を使うケースを、どちらかを上書きすることなく両方とも記録できる）。
- 保存する情報はPhase 4Aで実際に使う最小限のみ（display_name / 正規化済み電話番号 /
  初回・最終利用日時 / 来店回数 / 直近の予約への参照）。詳細な人物プロフィール・
  AIによる性格評価や属性推測・医療情報・会話全文/録音全文は一切保存しない
  （仕様書Phase 4A section 9〜11の禁止事項）。
- Reservation側のスキーマ・relationshipには一切変更を加えない
  （Reservation.customer_id は既存Customer専用のまま touch しない。本モデルから
  Reservationへは last_reservation_id で一方向に参照するのみ）。
- FK/cascade設計（Phase 1のProduction PostgreSQL cascade事故を踏まえた対応）:
  - shop_id は ondelete="CASCADE" とし、Shop削除時にDBレベルで確実に
    connected_memoriesごと削除されるようにする（ORM側のcascade="all,
    delete-orphan"と二重の防御にする）。
  - last_reservation_id は ondelete="SET NULL" とする。Reservationは現状
    Shop削除によるcascade以外で個別にhard-deleteされることはない
    （調査で確認済み。cancel_reservation()はstatus変更のみ）が、Phase 1で
    「ORM cascadeの削除順序をSQLAlchemyの back_populates 明示に頼る」設計が
    複雑化した教訓を踏まえ、ここではDBのFK制約自体にON DELETE SET NULLを
    持たせることで、ORMの削除順序に一切依存せず安全側に倒す（Reservation側の
    モデル定義に新しいrelationshipを追加する必要がなく、既存Reservationへの
    影響をゼロにできる利点もある）。
"""

import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, ForeignKey, Index, Integer
from sqlalchemy.orm import relationship
from app.database import Base


class CustomerMemory(Base):
    """店舗単位の最小限の顧客記憶（Phase 4A MVP）"""

    __tablename__ = "customer_memories"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    shop_id = Column(String(36), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)

    # 日本の電話番号を "0"始まり・数字のみに正規化した文字列
    # （app.schemas.shop.normalize_jp_phone_national参照。090-.../+8190.../
    # 81-90-...等の表記ゆれを吸収した後の値のみを保存する）。
    normalized_phone = Column(String(20), nullable=False, index=True)

    # 氏名の重複排除用キー（空白正規化のみ。漢字/カナ変換等の推測は行わない。
    # app.services.customer_memory._normalize_name_key参照）。表示には使わない。
    normalized_name_key = Column(String(255), nullable=False)

    # 実際に予約時に伺った氏名（表示・確認用）。
    display_name = Column(String(255), nullable=False)

    # 既存予約にemailが存在する場合のみ補完（Realtime Voice経由では現状常にNone）。
    email = Column(String(255), nullable=True)

    first_seen_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    visit_count = Column(Integer, nullable=False, default=1)

    # Phase 5A: 直近の会話で実際に使われていた言語コード（例: "en"）。
    # あくまで「次回そのままAIが使ってよい」という強い指示ではなく、次回接客時の
    # ソフトなヒントに過ぎない（app.services.realtime_voice_ai参照。これだけを根拠に
    # 国籍・民族を推測してはならない。話しかけられた言語を記録するのみ）。
    # 予約作成時、その会話で使われていた言語が判明した場合のみ書き込む
    # （None のままなら「記録なし」であり、日本語を意味するわけではない）。
    last_conversation_language = Column(String(10), nullable=True)

    # 直近この顧客記憶に結び付いた予約（監査・将来拡張用。Reservation側には
    # 対応するrelationshipを追加しない＝Reservationへの影響ゼロ）。
    last_reservation_id = Column(
        String(36), ForeignKey("reservations.id", ondelete="SET NULL"), nullable=True, index=True
    )

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    shop = relationship("Shop", back_populates="customer_memories")
    last_reservation = relationship("Reservation")

    __table_args__ = (
        # 冪等性の最終防衛線（Phase3Bのidempotency_key設計と同じ思想）。
        # 同一店舗・同一正規化電話番号・同一正規化氏名の組み合わせを一意とする。
        # 電話番号だけの一意制約にしないのは、家族共用電話等で同じ番号を
        # 複数人が使うケースを表現できなくするため（仕様書section22で明示的に禁止）。
        Index(
            "ux_customer_memories_shop_phone_name",
            "shop_id", "normalized_phone", "normalized_name_key",
            unique=True,
        ),
    )

    def __repr__(self):
        return f"<CustomerMemory(id={self.id}, shop_id={self.shop_id}, visit_count={self.visit_count})>"
