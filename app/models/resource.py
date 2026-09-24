"""
Resource (予約リソース) モデル — Generic Resource Foundation Phase R1

飲食店以外の業種（美容室のチェア、整体のベッド、ホテルの部屋、レンタカーの
車両、カラオケの部屋、教室など）が持つ「WHAT / WHERE」（予約に使う設備・場所）
を表現する、業種を問わない汎用エンティティ。

★★★ 設計方針（Generic Resource Architecture Audit / Phase R1 Audit Reportに
基づく確定仕様。詳細な検討過程・却下した代替案は各Auditのやり取りを参照）:

- 既存の ShopTable（飲食店のテーブル・席）には一切触れない。ShopTableは
  今後も飲食店専用のリソース次元として独立に存在し続ける（Option B）。
  ResourceとShopTableが同じ店舗で二重管理されることを防ぐ運用上の判断
  （どちらを使うかは店舗のbusiness_type/categoryで一意に決まる想定）は
  API/フロントエンド側の責務であり、本モデル自体には持たせない。

- Phase R1では「安全なデータ基盤」のみを作る。Reservationモデルへの
  resource_id列の追加、availability engine（check_availability/
  create_reservation/get_availability）との統合、Realtime Tool・Booking
  Board・Week Viewへの統合は、いずれも意図的にこのフェーズでは行わない
  （将来のPhase R2以降で段階的に追加する）。そのため現時点でResourceに
  紐づくReservationは存在せず、削除時の「今後の有効な予約」チェックは
  まだ実装しない（将来Reservation Integration Phaseで、
  shop_tables.py の _find_future_active_table_reservations() と同型の
  ヘルパーを追加する想定）。

- resource_type はDB enumにしない（将来の種別追加でmigrationを要求
  しないため）。DB列としてはString(50)の自由文字列で持ち、許可される
  値の検証はAPI層（app/schemas/resource.pyのPydanticスキーマ）で行う
  （ShopCategory/BusinessTypeと同じ「Python層でのみ列挙、DB列は
  plain String」という既存規約に合わせている）。

- capacity は nullable。ShopTable.capacityとは異なり、Resourceは
  車両・設備等「収容人数」という概念が意味を持たない種別も対象にするため、
  NOT NULL制約を機械的に採用しない。NULLは「収容人数の概念がこの
  リソースには適用されない」という意味であり、0や無制限を意味しない。
  Phase R1では予約判定に一切使われないため、この列の値がどうであれ
  現時点での実害はない。

- attributes はJSON nullable列として保持するが、Phase R1では
  Create/Update/Responseいずれのスキーマにも含めない（DBレベルの
  foundationのみ）。Shop.features・Reservation.reservation_detailsと
  同じ「柔軟なJSON列、EAVテーブルは作らない」という既存規約を将来の
  拡張時にも踏襲する前提の列だが、Owner UIへ生のJSON入力をさせない
  という明示的な指示（Section22）を最も安全に満たすため、API層での
  使用は完全に見送る。

- name の一意制約は設けない（同一店舗内に同名リソースが複数存在する
  ケースを禁止しない。ShopTableも同様に一意制約を持たない）。
"""

from datetime import datetime
import uuid

from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey, Index, Integer, JSON
from sqlalchemy.orm import relationship
from app.database import Base


class Resource(Base):
    """予約リソース（部屋・ベッド・椅子・車両・カラオケルーム・教室・設備など）"""

    __tablename__ = "resources"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    shop_id = Column(String(36), ForeignKey("shops.id"), nullable=False, index=True)

    name = Column(String(100), nullable=False)  # 例: 個室A、施術ベッド1、カラオケルームB
    # 種別。DB enumにしない（Section7/22参照）。許可値の検証はapp/schemas/resource.py側。
    resource_type = Column(String(50), nullable=False)
    # 収容可能人数。nullable（車両・設備等では概念自体が無意味なため。Section9参照）。
    capacity = Column(Integer, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)  # False = 一時的に使用不可
    display_order = Column(Integer, default=0, nullable=False)

    # 将来の属性拡張用（喫煙可否・車両クラス等）。Phase R1ではAPIスキーマに
    # 一切含めず、DBレベルのfoundationのみ（Section10/11/22参照）。
    attributes = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    shop = relationship("Shop", foreign_keys=[shop_id])

    __table_args__ = (
        Index("ix_resources_shop", "shop_id"),
    )

    def __repr__(self):
        return f"<Resource(id={self.id}, shop_id={self.shop_id}, name={self.name}, type={self.resource_type})>"
