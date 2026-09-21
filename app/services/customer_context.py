"""
Outbound AI Phase 4B: Returning Customer Context（本人確認後の最小限のご利用情報）

役割（仕様書Phase4B section3-8を踏襲）:
- OpenAI Realtime      = 会話・「confirmed」の意味判断のみ
- このモジュール（Python） = 本人確認状態の管理(session/shop/candidateへの
  binding・有効期限) と、Customer Contextのprivacy policy（何を返してよいか）
- app.models.customer_memory.CustomerMemory / app.models.reservation.Reservation
  = 過去の事実そのもの

===== 本人確認状態(Verification State)の設計方針（重要・必ず守ること） =====

- Phase4Aまでのfind_customer/check_availability/create_reservation/
  get_shop_infoはすべて完全にステートレスなHTTPエンドポイントであり、
  「1回の通話」を識別するサーバー側の概念がそもそも存在しなかった
  （app.routers.realtime_voice.create_realtime_voice_sessionが返す
  client_secretはOpenAI WebRTC接続の確立にのみ使う短命トークンで、
  通話全体を識別するIDではない）。Phase4Bで初めて、この目的のためだけの
  RECEPTRA独自のopaqueな識別子(voice_session_id)を導入する
  （create_realtime_voice_session側でsecrets.token_urlsafe(24)により発行）。

- 本人確認状態は必ず (shop_id, voice_session_id, candidate_reference) の
  組み合わせでbindingする（仕様書section8）。別タブ・別通話・別shop・
  別candidateへの流用を防ぐため。

- 「AIが候補の氏名を読み上げた」ことと「本人が肯定した」ことは別物であり、
  かつ「本人確認済みかどうか」の最終判断は絶対にAI自身に委ねない
  （仕様書section5-7）。この設計では、AIがconfirm_customer_identityへ
  渡せる引数はconfirmed(true/false)という一つのbooleanのみであり、
  session_id・candidate_referenceはAIの出力JSONには一切含まれず、
  フロントエンド(shop-ai-realtime-voice.html)がfind_customerの結果から
  保持し自動的に転送する。したがってAIは「どのcandidateを確認するか」
  自体には一切関与できず、「はいと言われたかどうか」の意味判断のみを
  担う。この意味判断自体（本当に肯定されたか）を暗号学的に証明する手段は
  無く、これは既存のcreate_reservation等のRealtime Tool全体と共通の
  信頼境界であることに留意（instructions側の明確な指示で運用上担保する。
  SMS OTP等の実在証明はPhase4Aで明示的にスコープ外とされている）。

- get_customer_context はAIから受け取る引数が無い設計にした
  （仕様書section14「理想的にはAI引数はほぼ不要」）。バックエンドは
  session_idのみから対象のCustomerMemoryを特定し、AIがphone/shop_id/
  customer_idを自由に指定して任意の顧客情報を引き出す設計を構造的に
  排除している。

- pending candidate（find_customer後、confirm前の状態）・verified状態
  （confirm成功後の状態）は、いずれもプロセス内メモリ(dict)のみで保持する。
  Railway本番の実行形態を確認した結果（Procfile/railway.json/Dockerfileの
  いずれもuvicornに--workersを指定しておらず、単一ワーカーで稼働）、
  既存のapp.routers.realtime_voice内のレート制限バケット群と全く同じ
  前提（単一プロセス内メモリで安全）が成立するため、Redis等の新規
  インフラは導入しない（仕様書section31）。
  複数worker/複数replica構成に将来変更された場合はこの前提が崩れる
  （本人確認状態がworkerごとに分散し、一貫性が保てなくなる）ため、
  その場合はDB-backedな状態管理への切り替えが必要になる。

- server再起動でこの状態が失われることは許容する（仕様書section31）。
  再起動後は単に「本人確認前」の状態からやり直しになるだけで、
  安全側にフォールバックする（何かを誤って「確認済み」として扱うことは
  無い）フェイルセーフ設計。

- 有効期限: pending candidateは5分（find_customerを呼んでから氏名確認の
  返事を待つのに十分な時間）、verified状態は30分
  （frontend/public/shop-ai-realtime-voice.htmlのMAX_CALL_DURATION_MS
  ＝1通話の強制切断上限と同じ値。通常の通話時間内では絶対に期限切れに
  ならず、通話終了後は速やかに失効する）。
"""

import logging
import secrets
import time as time_module
from typing import Optional

from sqlalchemy import select

import app.database as db_module
from app.models.customer_memory import CustomerMemory
from app.models.reservation import Reservation, ReservationStatus
from app.models.service import Service
from app.models.shop import Shop
from app.models.staff import Staff, StaffService
from app.taxonomy import LEGACY_CATEGORY_MAP, resolve_business_type

logger = logging.getLogger("receptra.customer_context")

_CANDIDATE_PENDING_TTL_SECONDS = 300  # 5分。find_customerからconfirmまでの猶予。
_VERIFIED_STATE_TTL_SECONDS = 1800  # 30分。MAX_CALL_DURATION_MS（1通話の上限）と同じ値。

# key: voice_session_id
_pending_candidates: dict[str, dict] = {}
_verified_sessions: dict[str, dict] = {}


def _purge_expired() -> None:
    now = time_module.monotonic()
    expired_pending = [
        k for k, v in _pending_candidates.items()
        if now - v["created_at"] > _CANDIDATE_PENDING_TTL_SECONDS
    ]
    for k in expired_pending:
        _pending_candidates.pop(k, None)

    expired_verified = [
        k for k, v in _verified_sessions.items()
        if now - v["verified_at"] > _VERIFIED_STATE_TTL_SECONDS
    ]
    for k in expired_verified:
        _verified_sessions.pop(k, None)


def issue_candidate(
    shop_id: str, voice_session_id: str, customer_memory_id: str, display_name: str
) -> str:
    """
    find_customerがcandidate_foundを返す際に呼ぶ。この通話(voice_session_id)
    に対して1件だけpending candidateを登録し、confirm_customer_identity専用の
    opaqueなcandidate_referenceを発行する。同じ通話内で複数回find_customerが
    呼ばれた場合（AIが電話番号を聞き直した等）は、最新の呼び出しが以前の
    pending candidateを上書きする（一度に確認対象になるのは常に1件のみ）。
    """
    _purge_expired()
    candidate_reference = secrets.token_urlsafe(24)
    _pending_candidates[voice_session_id] = {
        "shop_id": shop_id,
        "candidate_reference": candidate_reference,
        "customer_memory_id": customer_memory_id,
        "display_name": display_name,
        "created_at": time_module.monotonic(),
    }
    return candidate_reference


def confirm_candidate(
    shop_id: str, voice_session_id: str, candidate_reference: str, confirmed: bool
) -> str:
    """
    confirm_customer_identity Toolの中核ロジック。戻り値のstatus:
    - "verified"             : 本人確認成功。以後get_customer_contextが使える。
    - "rejected"              : confirmed=falseで呼ばれた（正常系。お客様が否定した）。
    - "no_pending_candidate"  : このshop_id・voice_session_idに対して
                                 pending candidateが存在しない（find_customerを
                                 呼んでいない／既に消費済み／有効期限切れ／
                                 別店舗のcandidateだった、のいずれか。
                                 存在しない理由を外部から区別できないよう、
                                 すべて同じ値にまとめる）。
    - "reference_mismatch"    : 正しいshop_id・voice_session_idだが、
                                 candidate_referenceが一致しない
                                 （改ざん・別candidateのreference流用の疑い）。

    重要: 呼び出し結果に関わらず、該当voice_session_idのpending candidateは
    この呼び出しで必ず消費（削除）する。同じ試行を繰り返して総当たりされる
    ことを防ぐため（1回のfind_customerにつき、confirmの試行機会は1回のみ）。
    """
    _purge_expired()
    pending = _pending_candidates.get(voice_session_id)

    if pending is None or pending["shop_id"] != shop_id:
        # 別店舗からの呼び出しの場合も、存在有無を漏らさず同じ結果を返す。
        return "no_pending_candidate"

    if pending["candidate_reference"] != candidate_reference:
        _pending_candidates.pop(voice_session_id, None)
        return "reference_mismatch"

    _pending_candidates.pop(voice_session_id, None)

    if not confirmed:
        return "rejected"

    _verified_sessions[voice_session_id] = {
        "shop_id": shop_id,
        "customer_memory_id": pending["customer_memory_id"],
        "verified_at": time_module.monotonic(),
    }
    return "verified"


def get_verified_customer_memory_id(shop_id: str, voice_session_id: str) -> Optional[str]:
    """
    get_customer_context Toolの本人確認チェック。verified状態が存在し、
    shop_idが一致する場合のみCustomerMemory.idを返す。それ以外
    （未確認・別店舗・有効期限切れ）は全てNone（呼び出し元はnot_verified
    として安全側に倒す）。
    """
    _purge_expired()
    verified = _verified_sessions.get(voice_session_id)
    if verified is None or verified["shop_id"] != shop_id:
        return None
    return verified["customer_memory_id"]


_INVALID_LAST_RESERVATION_STATUSES = (
    ReservationStatus.CANCELLED.value,
    ReservationStatus.NO_SHOW.value,
)


_MEDICAL_TAXONOMY_GROUP_KEY = "medical"  # app/taxonomy.py の医療グループの key と同じ値


def is_medical_category(shop: Shop) -> bool:
    """
    医療系業種かどうかを判定する唯一の場所（仕様書Phase4B section12
    「一箇所にルールを集約し、巨大なif文を各所に散らさない」への対応）。

    重要な修正履歴（Production E2E中に発見・即修正）:
    当初は app/models/shop.py の BusinessType/ShopCategory Enum
    （値="clinic"）とのstring比較で実装していたが、これらのEnumは
    taxonomy体系（app/taxonomy.py）導入前の廃止済み定数であり、
    実際のオーナー向け店舗登録UI（frontend/public/owner.html）は
    taxonomyのJSON（/api/v1/taxonomy）から選ばせた日本語カテゴリー名を
    そのままShop.categoryに保存し、Shop.business_typeには
    app.taxonomy.resolve_business_type()が返すtaxonomyグループkey
    （医療系なら"medical"）を保存する（app/routers/shops.py参照）。
    つまり旧Enumとの比較では実運用中の医療系店舗を一件も検出できず
    （現行データ経路がそもそも"clinic"という値を生成しない）、
    Section12が禁じる「医療系店舗でのService名漏洩」を防げていなかった。
    E2E本番検証（【E2E-TEMP】P4BShopC, category="一般クリニック"相当）で
    実際にlast_service_nameが返ってしまうことを確認し、直ちに本実装へ修正。

    現在の実装は、taxonomy.py を単一の情報源として3段階でチェックする
    （安全側に倒すため、いずれか一つでも一致すればTrueとする）:
    1. shop.business_type が taxonomy の "medical" グループkeyと一致する
       （Phase3H以降の通常の新規登録・更新経路）。
    2. shop.category を resolve_business_type() で逆引きした結果が
       "medical" となる（business_typeが何らかの理由で未同期・未設定の
       場合のフォールバック）。
    3. shop.category が taxonomy導入前の旧固定値（例: "CLINIC"）を
       保持したままの未移行データである場合、LEGACY_CATEGORY_MAP経由で
       医療系と判定する。
    """
    if shop.business_type == _MEDICAL_TAXONOMY_GROUP_KEY:
        return True
    if resolve_business_type(shop.category) == _MEDICAL_TAXONOMY_GROUP_KEY:
        return True
    legacy_entry = LEGACY_CATEGORY_MAP.get(shop.category or "")
    if legacy_entry and legacy_entry[0] == _MEDICAL_TAXONOMY_GROUP_KEY:
        return True
    return False


async def _is_staff_capable_of_service(db, staff_id: str, service_id: str) -> bool:
    """
    app.routers.reservations._find_available_staff_for_serviceと同じ
    「unmanaged」の考え方を踏襲する: そのサービスに一件もstaff_services
    割り当てが登録されていない店舗では、スタッフ・サービスの紐付け管理を
    していない店舗とみなし、capable=Trueとして扱う（既存のcheck_availability
    の挙動と一致させる。新しい解釈を持ち込まない）。
    割り当てが1件でも存在する店舗でのみ、実際にこのstaff_idがその
    service_idに割り当てられているかを厳密にチェックする。
    """
    any_assignment = await db.execute(
        select(StaffService.id).filter(StaffService.service_id == service_id).limit(1)
    )
    if any_assignment.scalar_one_or_none() is None:
        return True
    result = await db.execute(
        select(StaffService.id).filter(
            StaffService.staff_id == staff_id, StaffService.service_id == service_id
        ).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def build_customer_context(shop: Shop, customer_memory_id: str) -> Optional[dict]:
    """
    本人確認済み(get_verified_customer_memory_idがcustomer_memory_idを
    返した後)にのみ呼ばれる、Customer Context構築の唯一の入り口。

    返す情報は仕様書Phase4B section9で許可されたものに限る:
    display_name / visit_count / last_seen_at / 直近の予約日時 /
    （非医療系業種かつ判明する場合のみ）直近のサービス名・現在も予約可能か・
    直近の担当スタッフ名。

    絶対に含めない情報（section10）: 内部ID（CustomerMemory.id /
    Reservation.id / Service.id / Staff.id）、special_requests（自由記述。
    医療情報等のセンシティブ情報を含みうるため業種を問わず除外）、
    住所・生年月日・決済情報等。

    医療系業種(is_medical_category)では、Service名・Staff名を意図的に
    一切含めない（診療内容自体を示しうるため。section12）。

    「直近の予約」はCustomerMemory.last_reservation_idが指す1件のみを対象と
    する（全履歴を検索しない。section26「非常に小さくする」）。この予約が
    キャンセル・ノーショーだった場合は実際の利用ではないため、日時・
    サービス・スタッフは一切含めない（visit_count・last_seen_atは
    CustomerMemory自体の値であり、この1件の予約の状態に左右されないため
    そのまま返す。section24）。

    visit_countの意味についての重要な注記（section23の調査結果）:
    この値はapp.services.customer_memory._upsert_once()により
    「Realtime Voice経由でcreate_reservationが成功した回数」としてカウント
    されており、その後の予約キャンセル・ノーショーでは減算されない。
    つまり「実際に来店した回数」とは厳密には異なる（「予約成立回数」に近い）。
    この関数はこの値の意味を変更・再解釈せず、そのまま返す
    （仕様書section23「Phase4Bのためだけに過去データの意味を
    mass migrationしない」）。Realtime instructions側でも、この値を
    お客様への案内で言い切る材料にはせず、トーン調整の参考情報として
    のみ使うよう指示している。

    セッション分離: 呼び出し元とDBセッションを共有せず、専用の新しい
    セッションで完結させる（app.services.customer_memoryと同じ設計思想）。
    どのような失敗もNoneを返すのみで、例外を外へ伝播させない。
    """
    try:
        async with db_module.AsyncSessionLocal() as db:
            memory = await db.get(CustomerMemory, customer_memory_id)
            if memory is None or memory.shop_id != shop.id:
                return None

            context: dict = {
                "display_name": memory.display_name,
                "visit_count": memory.visit_count,
                "last_seen_at": memory.last_seen_at,
                "last_reservation_at": None,
                "last_service_name": None,
                "last_service_available_now": None,
                "last_staff_name": None,
            }

            if not memory.last_reservation_id:
                return context

            reservation = await db.get(Reservation, memory.last_reservation_id)
            if reservation is None or reservation.shop_id != shop.id:
                return context
            if reservation.status in _INVALID_LAST_RESERVATION_STATUSES:
                # キャンセル・ノーショーは「実際の利用」として扱わない。
                return context

            context["last_reservation_at"] = reservation.reservation_date

            if is_medical_category(shop):
                # 医療系業種: 「以前の利用あり・最終利用時期・来店回数」のみに
                # 留める。診療内容を示しうるService名・担当Staff名は
                # ここで一律に除外する（section12。判定はis_medical_categoryの
                # 一箇所のみに集約）。
                return context

            if reservation.service_id:
                service = await db.get(Service, reservation.service_id)
                if service is not None and service.shop_id == shop.id:
                    context["last_service_name"] = service.name
                    context["last_service_available_now"] = (service.is_active == "active")

            if reservation.staff_id:
                staff = await db.get(Staff, reservation.staff_id)
                if staff is not None and staff.shop_id == shop.id and staff.is_active == "active":
                    capable = True
                    if reservation.service_id:
                        capable = await _is_staff_capable_of_service(
                            db, staff.id, reservation.service_id
                        )
                    if capable:
                        context["last_staff_name"] = staff.display_name or staff.name

            return context
    except Exception:
        logger.exception(
            "Customer Context構築に失敗しました（安全側でno_context相当にします） "
            "shop_id=%s customer_memory_id=%s",
            shop.id, customer_memory_id,
        )
        return None
