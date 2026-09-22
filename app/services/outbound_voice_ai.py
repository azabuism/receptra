"""
Outbound AI Phase 1: 予約確定通知の発話内容組み立て

スコープ（重要）: 「新しい予約が確定しました。{日時}に{人数}名でのご予約です」
という短い要件伝達のみ。変更・キャンセル・リマインダー等の文面は対象外。

テーブル/席・診察室等の「予約リソース」呼称は、本フェーズでは意図的に含めない。
理由: RECEPTRA 予約リソース業種横断化 調査報告書で確認した通り、ShopTable/
table_idはrestaurant/entertainment/other業種専用のfallbackリソースであり、
beauty/medical等はそもそも実体を持たない。したがって「テーブル○番」のような
文言を汎用的に組み込もうとすると、業種によっては実体のない情報を話すか、
app/routers/shop_booking_ai.py に既に存在する「業種を問わず『お席』と言って
しまう」バグと同種の不具合を新たに作り込むことになる。日時・人数・（あれば）
氏名・サービス名だけで要件は十分に伝わるため、リソース呼称は将来
（予約リソース業種横断化を実施するタイミング）まで意図的に見送る。

Inbound（app.services.realtime_voice_ai）との共有方針:
- 業種ごとの「サービス」呼称は、Inboundと全く同じ _SERVICE_TERMINOLOGY_LABELS
  をそのままimportして使う（呼称マッピングを二重管理しない）。
- Outbound固有のセッション生成・音声ストリーミングの配線はこのフェーズでは
  作らない（Vonage非依存のFake providerはテキストの組み立てまでしか使わない）。
  実際にVonage経由でOpenAI Realtime APIへ橋渡しする段になったら、この関数が
  組み立てるメッセージ内容をベースに、Outbound専用のsession instructions
  組み立て関数を別途追加する想定（Inboundのcreate_realtime_session()は
  ブラウザ/WebRTC前提のため直接の使い回しはできない。RECEPTRA Outbound AI
  Phase 基盤設計書 12節参照）。
"""

from app.models.reservation import Reservation
from app.models.shop import Shop
from app.models.callback_request import CallbackRequest
from app.services.realtime_voice_ai import _SERVICE_TERMINOLOGY_LABELS


def _format_reservation_datetime(reservation: Reservation) -> str:
    dt = reservation.reservation_date
    weekday_labels = ["月", "火", "水", "木", "金", "土", "日"]
    weekday = weekday_labels[dt.weekday()]
    return f"{dt.month}月{dt.day}日（{weekday}）{dt.hour}時{dt.minute:02d}分"


def build_reservation_confirmed_message(shop: Shop, reservation: Reservation) -> str:
    """予約確定通知の発話内容（テキスト）を組み立てる。

    引数のreservationは、呼び出し側で service リレーションを事前にロード
    済みであることを前提とする（app.routers.reservations.create_reservation()
    の最終再取得でselectinload(Reservation.service)済みのものをそのまま渡す想定）。
    """
    datetime_str = _format_reservation_datetime(reservation)
    people_str = f"{reservation.number_of_people}名"

    name_part = ""
    if reservation.guest_name:
        name_part = f"{reservation.guest_name}様より、"

    service_part = ""
    service = getattr(reservation, "service", None)
    if service is not None and getattr(service, "name", None):
        term = _SERVICE_TERMINOLOGY_LABELS.get(shop.business_type, "サービス")
        service_part = f"{term}「{service.name}」のご予約で、"

    return (
        f"{shop.name}様、新しいご予約のお知らせです。"
        f"{name_part}{datetime_str}、{people_str}で{service_part}ご予約が確定しました。"
        "内容のご確認をお願いいたします。"
    )


def _format_callback_desired_datetime(callback_request: CallbackRequest) -> str:
    """CallbackRequest.desired_date/desired_timeを発話用の文字列に整形する。
    いずれもAIが把握できた場合のみの参考情報のため、無ければ空文字を返す
    （_format_reservation_datetimeと異なり、reservation_dateのような必須値ではない）。"""
    if not callback_request.desired_date:
        return ""
    weekday_labels = ["月", "火", "水", "木", "金", "土", "日"]
    weekday = weekday_labels[callback_request.desired_date.weekday()]
    date_str = f"{callback_request.desired_date.month}月{callback_request.desired_date.day}日（{weekday}）"
    if callback_request.desired_time:
        t = callback_request.desired_time
        date_str += f"{t.hour}時" + (f"{t.minute:02d}分" if t.minute else "")
    return date_str


def build_callback_requested_message(shop: Shop, callback_request: CallbackRequest) -> str:
    """Human Handoff基盤: 折り返し依頼(CallbackRequest)の担当者向け通知の発話内容
    （テキスト）を組み立てる。build_reservation_confirmed_message()と同じ方針
    （テーブル/席等の呼称は含めない、業種問わず共通の文面）を踏襲する。

    ここで組み立てる文面は、ユーザー（谷村様）が明示的に例示した文面
    （「RECEPTRAで折り返し依頼を受け付けました。お客様：○○様 電話番号：...
    お問い合わせ：... 希望日時：... 人数：... お客様へ折り返しをお願いします。」）
    に準拠する。
    """
    name_part = f"{callback_request.customer_name}様" if callback_request.customer_name else "お名前未確認のお客様"
    phone_part = callback_request.customer_phone or "電話番号未確認"
    inquiry_part = callback_request.inquiry_text or "詳細はRECEPTRA管理画面をご確認ください"

    desired_str = _format_callback_desired_datetime(callback_request)
    desired_part = f"希望日時：{desired_str}。" if desired_str else ""
    party_part = f"人数：{callback_request.party_size}名。" if callback_request.party_size else ""

    return (
        f"{shop.name}様、RECEPTRAで折り返し依頼を受け付けました。"
        f"お客様：{name_part}。電話番号：{phone_part}。"
        f"お問い合わせ：{inquiry_part}。{desired_part}{party_part}"
        "お客様へ折り返しのご連絡をお願いいたします。"
    )
