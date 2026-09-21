"""
OpenAI Realtime API（ブラウザ ⇔ WebRTC 直結）用のセッション構築ロジック

設計方針:
- 音声そのものはブラウザとOpenAIの間をWebRTCで直接流れ、RECEPTRAのサーバーは
  一切経由しない。サーバー側の役割は「店舗情報から会話のシステムプロンプトを
  組み立て、OpenAI公式が現在推奨している短命のephemeralトークン(client secret)
  を発行してブラウザへ渡す」ことだけに限定する。
  OPENAI_API_KEY自体はここでサーバー側にのみ保持し、ブラウザには一切渡さない。
- Phase1のスコープでは Function Calling・メニュー/料金/予約DBの参照は行わず、
  店舗名と営業時間のみをシステムプロンプトに含める簡易版とする。
  既存の shop_booking_ai.py（チャット予約）・voice_ai.py（電話AI/店舗ページ
  チャット・従来型音声）は一切変更しない、完全に別レイヤーの実装。
- OpenAI Realtime APIのモデル名・イベント名・セッション設定の項目名は
  この数ヶ月でも変更されているため、実装時点（2026年9月）で
  openai公式Pythonライブラリ(openai==1.109.1)に実際に存在する
  `client.realtime.client_secrets.create()` の型定義を確認した上で実装している。
"""

import hashlib
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.orm import undefer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.shop import Shop, ShopHours
from app.models.ai_staff_settings import AIStaffSettings

logger = logging.getLogger("receptra.realtime_voice_ai")

JST = timezone(timedelta(hours=9))
_DAY_NAMES_JA = ["月", "火", "水", "木", "金", "土", "日"]

# Realtime API用voice一覧（2026年9月時点、openai==1.109.1のSDK型定義
# RealtimeAudioConfigOutputParam.voice で確認済み）。
#
# 重要: 将来的にOpenAI側でvoiceが増減する可能性があるため、「永遠にこの
# 10種類しか存在しない」という前提で実装してはいけない。SDK/API仕様が
# 更新された際は、この一覧を再確認して更新すること。
#
# 注意: これはRealtime API専用のvoice一覧である。テキスト読み上げ用の
# /audio/speech エンドポイントのvoice一覧（nova/onyx/fable等を含む、
# 別物）とは混同しないこと。
REALTIME_VOICES = [
    "alloy", "ash", "ballad", "coral", "echo",
    "sage", "shimmer", "verse", "marin", "cedar",
]

# voice試聴用の短い固定セリフ。実際の接客instructionsは使わず、
# 「このvoiceがどんな声か」だけを確認できれば十分なため、店舗情報等は
# 一切含まない最小限のinstructionsにする。
_VOICE_PREVIEW_INSTRUCTIONS = (
    "あなたは飲食店の音声AI受付です。接続直後に、これから話す一文だけを"
    "自然な日本語の話し言葉で一度だけ話してください。それ以外は何も話さないで"
    "ください。\n"
    "「お電話ありがとうございます。本日はどのようなご用件でしょうか？」"
)

# ===== Realtime Voice AI Tool Calling（Phase3A: check_availability / Phase3B: create_reservation） =====
#
# 設計方針（重要・必ず守ること）:
# - ここで宣言するのは「AIが呼び出せる関数の名前・説明・引数スキーマ」のみ。
#   実際の空き状況の判定は一切ここで行わず、必ず
#   POST /api/v1/shops/{shop_id}/realtime-voice/tools/check-availability
#   （FastAPI側、Reservation DB / ShopHours / ShopClosureを参照）の結果を
#   唯一の正とする。このtoolsの説明文自体が「結果を見るまで自分で
#   空き状況を判断・回答しない」ようAIに指示する役割を持つ。
# - shop_id はこの関数の引数に含めない。実際にどの店舗かはRECEPTRAサーバー側
#   （セッションを発行した店舗）で決まっており、AIに他店舗のshop_idを
#   自由に指定させる余地を作らない。
# - 型定義は openai==1.109.1 の
#   openai.types.realtime.realtime_function_tool_param.RealtimeFunctionToolParam
#   （type/name/description/parameters のフラットな構造）を実装時に確認して
#   準拠している。ただし実際にOpenAI側からどのイベント（
#   response.output_item.done の item.type=="function_call" 等）でどう
#   送られてくるかは、SDKの型定義だけでなく実APIでの観測結果を最終的な
#   根拠とする（Phase2.5と同じ方針）。
_REALTIME_TOOLS = [
    {
        "type": "function",
        "name": "check_availability",
        "description": (
            "お客様から来店予約の空き状況（指定した日時に予約できるかどうか）を"
            "尋ねられたときに、必ずこの関数を呼び出してください。この結果が返る前に、"
            "空いているかどうかを自分で判断したり、お客様に案内したりしないでください。\n"
            "dateは必ずYYYY-MM-DD形式、timeは必ずHH:MM形式（24時間表記）で指定してください。\n"
            "美容院・クリニックなどサービス単位の予約でサービス指定がある場合は"
            "service_idを、スタッフ指名がある場合はstaff_idを指定してください"
            "（どちらも該当する場合のみ。省略可）。\n"
            "戻り値のavailableがfalseの場合、reason_codeを見て案内してください。"
            "fully_booked=満席、outside_business_hours=営業時間外、"
            "shop_closed=定休日、business_hours_not_configured=店舗側の営業時間が"
            "まだ設定されていないため確認できない、temporary_closure=臨時休業、"
            "service_unavailable=そのサービス自体が現在利用不可、"
            "staff_unavailable=指名されたスタッフが空いていない、"
            "いずれも確定した情報なので、そのまま理由を添えてお客様に案内してください"
            "（business_hours_not_configuredの場合は、定休日と同様のトーンで"
            "「現在オンラインでは空き状況をご案内できないため、お手数ですが"
            "店舗へ直接お問い合わせください」のように案内してください）。\n"
            "reason_codeがinvalid_requestの場合、渡した日付や時刻の形式が"
            "誤っている可能性があります（date=YYYY-MM-DD, time=HH:MM(24時間)を"
            "再確認し、正しい形式が分かれば修正して再度呼び出してください）。\n"
            "reason_codeがtemporarily_unavailableの場合は、入力の誤りではなく、"
            "現在システム側で空き状況を確認できない状態です。この場合は"
            "満席とも空いているとも絶対に案内せず、少し時間を置いて再度お試し"
            "いただくか、店舗へ直接お問い合わせいただくようお伝えしてください。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "日付（YYYY-MM-DD形式）"},
                "time": {"type": "string", "description": "時刻（HH:MM形式、24時間表記）"},
                "party_size": {"type": "integer", "description": "人数", "minimum": 1},
                "service_id": {
                    "type": "string",
                    "description": "サービスID（美容院・クリニック等、サービス単位で予約する業種かつサービス指定がある場合のみ）",
                },
                "staff_id": {
                    "type": "string",
                    "description": "スタッフID（スタッフ指名がある場合のみ）",
                },
            },
            "required": ["date", "time", "party_size"],
        },
    },
    # ===== Phase3B: create_reservation =====
    #
    # 設計方針（重要・必ず守ること）:
    # - check_availabilityと同じく、shop_idも予約作成の実処理も一切ここでは行わない。
    #   必ず POST /api/v1/shops/{shop_id}/realtime-voice/tools/create-reservation
    #   （FastAPI側、既存のcreate_reservation()をそのまま呼び出す）の結果を唯一の正とする。
    # - Realtime Voiceはブラウザ⇔OpenAI直結のWebRTCで、RECEPTRAサーバーは音声も会話内容も
    #   経由しない。そのため既存のshop_booking_ai.py（チャット予約）が使っている
    #   「AIの発話をシステム側の確定文言に後から差し替える」仕組み（override_last_assistant_message）
    #   はこの経路では技術的に使えない（一度声に出した発話を後から書き換えることはできない）。
    #   したがって「Tool結果が返るまで予約成立を宣言しない」という安全性は、この説明文と
    #   build_realtime_instructions()側の_BOOKING_SAFETY_TEMPLATEという二重の指示（挙動レベルの
    #   安全策であり、Phase3Aのtemporarily_unavailable運用と同種の設計）でのみ担保している。
    #   一方、実際にDBへ予約が書き込まれるかどうか（＝真の予約成立）は、AIが何を話したかに
    #   一切左右されず、必ずバックエンドのcreate_reservation()成功の有無だけで決まる。
    # - call_idはこのToolのparametersに含めない。AIの出力JSONにはcall_idを一切含めさせず、
    #   ブラウザがOpenAI Realtime APIのfunction_callイベント(response.output_item.done の
    #   item.call_id)から直接読み取った値のみを、RECEPTRA側のToolエンドポイントへの
    #   リクエストボディに追加して転送する。RECEPTRA側でこれを
    #   "realtime_voice:{shop_id}:{call_id}" にnamespace化した上でDBの一意インデックスによる
    #   冪等性キーとして使う（同一call_idでの二重予約作成をDBレベルで確実に防ぐため）。
    {
        "type": "function",
        "name": "create_reservation",
        "description": (
            "お客様が実際に来店予約を確定したいときにのみ呼び出してください。"
            "呼び出す前に必ず、来店日時・人数・お名前・電話番号（該当する場合は"
            "サービス内容・スタッフ指名）を一つずつお客様と確認し、特に電話番号は"
            "お客様からうかがった番号をそのまま1桁ずつ日本語で読み上げて復唱し、"
            "間違いがないか確認してください（推測や聞き取れなかった桁の補完は"
            "絶対にしないでください）。\n"
            "その上で、電話番号や日時など個別項目への「合っています」等の返事とは"
            "別の、独立した発話として、日時・人数・お名前・電話番号をすべてまとめて"
            "読み上げて「この内容で予約してよろしいですか？」のようにはっきり確認し、"
            "その最終確認への返事でお客様が明確に肯定した場合にのみ呼び出してください。"
            "個別項目の確認への返事（電話番号の読み上げ確認への「はい」等）を、"
            "予約全体への同意として流用しないでください。最終確認の後に日時・人数・"
            "お名前・電話番号等が変更された場合は、変更を確認した上でもう一度最終"
            "確認をやり直してから呼び出してください。曖昧な返事・保留・沈黙のまま"
            "呼び出してはいけません。\n"
            "この関数の結果（success）が返る前に、予約が取れた・完了した・確定した等と"
            "一切案内しないでください。呼び出し中は「予約状況を確認して確定しますので、"
            "少々お待ちください」程度の中立的な案内だけにしてください。\n"
            "dateは必ずYYYY-MM-DD形式、timeは必ずHH:MM形式（24時間表記）で指定してください。"
            "美容院・クリニックなどサービス単位の予約でサービス指定がある場合はservice_idを、"
            "スタッフ指名がある場合はstaff_idを指定してください（該当する場合のみ。省略可）。\n"
            "戻り値のsuccessがtrueの場合のみ予約が成立したとご案内してよく、その際は必ず"
            "戻り値に含まれる日時・人数と完全に一致する内容だけを使ってください。\n"
            "successがfalseの場合、reason_codeを見て案内してください。"
            "invalid_request=入力形式に誤りの可能性（date/timeの形式等を再確認し、"
            "正しい形式が分かれば修正して再度呼び出してください）、"
            "reservation_not_enabled=この店舗は現在オンライン予約自体を受け付けていない"
            "（お客様の入力の問題ではないため、お店へ直接お問い合わせいただくようご案内）、"
            "fully_booked=満席、staff_unavailable=指名されたスタッフが空いていない、"
            "shop_closed=定休日、temporary_closure=臨時休業、"
            "service_unavailable=そのサービス自体が現在利用不可"
            "（これらはいずれも確定した情報です。確認した時点では空いていても、"
            "予約確定の直前に改めて空き状況を判定し直すため、その間に埋まった"
            "可能性があります。そのまま理由を添えて案内し、別の日時を伺ってください）、"
            "business_hours_not_configured=店舗側の営業時間がまだ設定されていない"
            "ため現在オンラインでは予約を確定できない（お客様の入力の問題ではない"
            "ため、定休日と同様のトーンで、お店へ直接お問い合わせいただくようご案内"
            "してください）、"
            "temporarily_unavailable=入力の誤りではなく、現在システム側で予約の成立を"
            "確認できない状態です。この場合は予約が取れた・取れなかったと絶対に案内せず、"
            "少し時間を置いて再度お試しいただくか、店舗へ直接お問い合わせいただくよう"
            "お伝えしてください。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "来店日（YYYY-MM-DD形式）"},
                "time": {"type": "string", "description": "来店時刻（HH:MM形式、24時間表記）"},
                "party_size": {"type": "integer", "description": "人数", "minimum": 1},
                "guest_name": {"type": "string", "description": "予約者名"},
                "guest_phone": {
                    "type": "string",
                    "description": "連絡先電話番号（お客様から実際にうかがい、1桁ずつ読み上げて復唱・確認した番号のみ。推測は絶対にしないこと）",
                },
                "service_id": {
                    "type": "string",
                    "description": "サービスID（美容院・クリニック等、サービス単位で予約する業種かつサービス指定がある場合のみ）",
                },
                "staff_id": {
                    "type": "string",
                    "description": "スタッフID（スタッフ指名がある場合のみ）",
                },
                "special_requests": {
                    "type": "string",
                    "description": "特別なご要望（アレルギー・個室希望等、あれば）",
                },
            },
            "required": ["date", "time", "party_size", "guest_name", "guest_phone"],
        },
    },
    # ===== Phase3D: get_shop_info =====
    #
    # 設計方針（重要・必ず守ること）:
    # - 駐車場・支払い方法・設備・来店前案内・キャンセルポリシー・FAQ等、
    #   店舗情報に関する質問を受けたら、必ずこの関数を呼び出してDBに登録された
    #   情報のみで回答すること。これらの情報について、この関数を呼ばずに
    #   自分の知識や推測で回答することは絶対に禁止する（ハルシネーション防止が
    #   このToolの存在意義そのもの）。
    # - shop_idはこの関数の引数に含めない。check_availability/create_reservationと
    #   同じく、実際にどの店舗かはRECEPTRAサーバー側（セッションを発行した店舗）で
    #   決まる。
    # - 一度に全店舗情報をまとめて取得することはできない設計とし、topicで
    #   1トピックずつ問い合わせる（Phase3D仕様書 section49「Tool結果は
    #   必要最小限に留める」に基づく）。
    # - 戻り値のknownがfalseの場合は「その情報がDBに登録されていない」ことを
    #   意味し、successがfalseの場合とは明確に区別する（前者は正常に問い合わせた
    #   結果、情報が無いだけ。後者は技術的な失敗）。この区別自体がPhase3Dの
    #   アンチハルシネーション設計の中核（shop_knowledge.pyのモデル設計コメント
    #   および仕様書section12参照）。
    {
        "type": "function",
        "name": "get_shop_info",
        "description": (
            "駐車場・支払い方法・Wi-Fi・バリアフリー/車椅子対応・お子様連れ可否・"
            "喫煙可否・ペット可否・設備全般・来店前の持ち物や注意事項・"
            "キャンセルや遅刻のルール・その他店舗独自のよくある質問（FAQ）について"
            "尋ねられたときは、必ずこの関数を呼び出し、その結果に含まれる情報だけを"
            "使って回答してください。この関数を呼ばずに自分の知識や推測でこれらの"
            "話題に回答することは絶対にしないでください。\n"
            "topicには次のいずれか一つだけを指定してください（一度に一つのtopicしか"
            "問い合わせられません。複数の話題を尋ねられた場合は、必要な回数だけ"
            "繰り返し呼び出してください）:\n"
            "parking=駐車場, payment=支払い方法（現金/クレジットカード/デビットカード/"
            "QRコード決済/電子マネー。data内にcredit_brands/qr_brands/emoney_brandsが"
            "含まれる場合、各ブランド名をキーとした個別の対応状況(yes=対応/no=非対応/"
            "conditional=条件付き/null=未登録)が入っています。あるブランドの値がnull"
            "（またはそのブランド名自体がキーに存在しない）場合は、そのブランド固有の"
            "対応可否は登録されていないという意味であり、対応・非対応のどちらとも"
            "絶対に断定しないでください）, wifi=Wi-Fi, accessibility=バリアフリー・"
            "車椅子対応, children=お子様連れ可否, smoking=喫煙可否（喫煙可/禁煙/"
            "分煙等の条件付き）, pets=ペット可否, facilities=上記wifi/accessibility/"
            "children/smoking/petsに加え個室・エレベーターの有無をまとめて確認したい"
            "場合, pre_visit=来店前にご案内すべき持ち物・注意事項・到着時間の目安, "
            "cancellation=キャンセル・遅刻に関するポリシー, faq=店舗独自のよくある"
            "質問と回答（上記のいずれにも当てはまらない質問はまずfaqで検索して"
            "ください。queryに検索したい内容の短いキーワードを日本語で指定すると、"
            "関連するFAQを検索して返します。queryを省略した場合は登録されている"
            "FAQの一部が返ります）。\n"
            "戻り値のsuccessがtrueの場合、knownを必ず確認してください。"
            "known=trueの場合のみdataに実際の情報が入っています。dataに含まれない"
            "項目・値については絶対に推測で補わないでください。"
            "known=falseの場合、その情報はまだ店舗側で登録されていません。"
            "この場合は絶対に「ある」「ない」を断定せず、"
            "「大変申し訳ございませんが、その情報は登録がなく、店舗に直接"
            "お問い合わせください」のように、情報が無いこと自体を正直にご案内し、"
            "お手数をおかけする形になる旨をお詫びしてください。\n"
            "戻り値のsuccessがfalseの場合、reason_codeを確認してください。"
            "invalid_requestは実装上呼び出し方に誤りがある場合です"
            "（topicの値を再確認してください）。temporarily_unavailableは、"
            "情報が無いのではなく現在システム側で確認できない技術的な状態です。"
            "この場合は情報がある・ないのどちらも断定せず、少し時間を置いて"
            "再度お試しいただくか、店舗へ直接お問い合わせいただくようお伝え"
            "してください（known=falseの場合と混同しないこと。原因が異なります）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "enum": [
                        "parking", "payment", "wifi", "accessibility", "children",
                        "smoking", "pets", "facilities", "pre_visit", "cancellation", "faq",
                    ],
                    "description": "問い合わせたい店舗情報の種類（一度に一つだけ指定）",
                },
                "query": {
                    "type": "string",
                    "description": "FAQを検索するためのキーワード（topic=faqの場合のみ使用。それ以外のtopicでは不要・無視されます）",
                },
            },
            "required": ["topic"],
        },
    },
    # ===== Outbound AI Phase 4A: find_customer =====
    #
    # 設計方針（重要・必ず守ること）:
    # - shop_idはこの関数の引数に含めない。check_availability等と同じく、
    #   実際にどの店舗かはRECEPTRAサーバー側（セッションを発行した店舗）で決まる。
    # - この関数の結果に含まれるのは「候補の氏名」だけ（見つからない場合は
    #   その旨のみ）。来店回数・前回利用日・過去の予約内容等は一切含まれない
    #   （FastAPI側のPrivacy Gate設計。app.schemas.reservation.
    #   FindCustomerToolResponseのdocstring参照）。この関数の説明文自体が、
    #   AIに「見つかった＝本人確定」と誤解させないための役割を持つ。
    {
        "type": "function",
        "name": "find_customer",
        "description": (
            "お客様から電話番号を伺ったら、予約の受付を進める前に必ずこの関数を"
            "呼び出してください（来店予約の会話で電話番号を伺うタイミングであれば"
            "いつでも構いません）。\n"
            "戻り値のstatusがnot_foundの場合は、初めてのお客様として通常どおり"
            "受付を続けてください（何も案内しなくてよい）。\n"
            "戻り値のstatusがcandidate_foundの場合、以前ご利用いただいた可能性が"
            "あるお客様が見つかっています。ただし電話番号の一致だけでは本人確定"
            "ではありません（家族共用の電話等の可能性があるため）。candidate_display_name"
            "を使って「以前ご利用いただいた可能性があります。○○様でよろしいですか？」"
            "のように、必ず一度確認してください。お客様が肯定した場合のみ、"
            "「ありがとうございます」「またのご利用ありがとうございます」のように"
            "自然にお伝えして構いません。お客様が否定した場合や、別の名前を"
            "名乗った場合は、その候補の情報を一切使わず、新規のお客様として"
            "通常どおり受付を続けてください。\n"
            "重要: この関数の結果には、以前の来店日・利用内容・来店回数等の"
            "詳細情報は一切含まれていません。本人確認が済む前はもちろん、"
            "済んだ後であっても、この関数の結果に含まれていない情報を"
            "推測して話すことは絶対にしないでください。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "description": "お客様から実際に伺った電話番号（推測は絶対にしないこと）",
                },
            },
            "required": ["phone"],
        },
    },
    # ===== Outbound AI Phase 4B: confirm_customer_identity =====
    #
    # 設計方針（重要・必ず守ること）:
    # - AIが渡せる引数はconfirmed(boolean)のみ。「どの候補を確認するか」を
    #   示すsession_id・candidate_referenceはこのparametersに一切含めない
    #   （フロントエンドがfind_customerの結果から保持し、AIに見せずに
    #   自動転送する。app.schemas.reservation.ConfirmCustomerIdentityToolRequest
    #   のdocstring参照）。AIが担うのは「お客様が肯定したかどうか」という
    #   意味判断のみであり、本人確認状態そのものの真偽はFastAPI側
    #   （app.services.customer_context）が判定する。
    {
        "type": "function",
        "name": "confirm_customer_identity",
        "description": (
            "find_customer の結果が candidate_found だった場合に、候補の氏名を"
            "お客様に確認した結果を報告するために呼び出してください。\n"
            "お客様が候補の氏名（例:「田中様でよろしいでしょうか？」）に対して"
            "明確に肯定した場合は confirmed=true で呼び出してください。\n"
            "お客様が否定した場合、別の名前を名乗った場合、または返事が曖昧・"
            "無言の場合は confirmed=false で呼び出してください。\n"
            "find_customer を呼んでいない場合、または結果が not_found だった"
            "場合は、この関数を呼び出さないでください（呼び出しても成功"
            "しません）。\n"
            "戻り値のstatusがverifiedの場合のみ、get_customer_context ツールが"
            "使えるようになります。verified以外（rejected/no_pending_candidate/"
            "reference_mismatch/temporarily_unavailable）の場合は、以前の"
            "利用について一切触れず、新規のお客様として通常どおり受付を"
            "続けてください。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "confirmed": {
                    "type": "boolean",
                    "description": "お客様が候補の氏名を明確に肯定した場合のみtrue。それ以外は必ずfalse。",
                },
            },
            "required": ["confirmed"],
        },
    },
    # ===== Outbound AI Phase 4B: get_customer_context =====
    #
    # 設計方針（重要・必ず守ること）:
    # - 引数は無し（parametersが空のobject）。対象の特定はバックエンド側の
    #   本人確認状態(voice_session_id)のみで行い、AIがphone/shop_id/
    #   customer_id等を指定して任意の顧客情報を引き出せる設計を構造的に
    #   排除する（仕様書section7・14）。
    # - 戻り値に含まれるのは仕様書section9-12で許可された最小限の項目のみ。
    #   医療系業種ではサービス名・担当者名が意図的に含まれない
    #   （app.services.customer_context.build_customer_context参照）。
    {
        "type": "function",
        "name": "get_customer_context",
        "description": (
            "confirm_customer_identity の結果が verified になった場合にのみ、"
            "必要であれば呼び出してください。引数は不要です。\n"
            "本人確認が済んでいない状態でこの関数を呼び出しても、有効な情報は"
            "返ってきません（statusがnot_verifiedになります）。\n"
            "戻り値のstatusがcontext_availableの場合のみ、含まれている"
            "フィールド（来店回数・前回のご利用日時・分かる場合は前回の"
            "サービス内容や担当者名）を会話に使ってよく、含まれていない"
            "フィールドは一切推測しないでください。医療系の店舗など、業種に"
            "よっては前回のサービス内容・担当者名がそもそも含まれません"
            "（意図的な仕様です。含まれていないことを不審に思わず、単に"
            "「以前のご利用があります」程度の案内に留めてください）。\n"
            "statusがno_contextの場合は、本人確認はできたものの参照できる"
            "利用履歴が無いという意味です。新規のお客様と同様に通常どおり"
            "受付を続けてください。"
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]

_client: Optional[AsyncOpenAI] = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


async def _build_hours_block(db: AsyncSession, shop_id: str) -> str:
    """
    店舗の営業時間をシステムプロンプト埋め込み用のテキストに整形する。
    shop_booking_ai.py の同名処理と内容は同じだが、既存ファイルには手を
    加えない方針のため、ここに独立して実装している（意図的な重複）。
    """
    result = await db.execute(select(ShopHours).where(ShopHours.shop_id == shop_id))
    rows = {h.day_of_week: h for h in result.scalars().all()}
    if not rows:
        # Phase3B.1: 以前は「ご希望日時はそのまま伺ってください」としており、
        # create_reservation()側の旧挙動（hours未設定時はチェックをスキップして
        # 予約を成立させてしまう）と辻褄を合わせていたが、その旧挙動自体を廃止した
        # （business_hours_not_configuredとして予約不可に統一）ため、AIへの案内も
        # 実態に合わせて修正する。
        return "（営業時間がまだ設定されていないため、現在オンラインでは予約を確定できません。日時を伺った上で、確定できない旨を簡潔にご案内してください）"
    lines = []
    for day in range(7):
        h = rows.get(day)
        if not h or h.is_closed:
            lines.append(f"{_DAY_NAMES_JA[day]}曜: 定休日")
        else:
            opening = h.opening_time.strftime("%H:%M")
            closing = h.closing_time.strftime("%H:%M")
            lines.append(f"{_DAY_NAMES_JA[day]}曜: {opening}〜{closing}")
    return "\n".join(lines)


def _today_str_jst() -> str:
    now = datetime.now(JST)
    return f"{now.year}年{now.month}月{now.day}日（{_DAY_NAMES_JA[now.weekday()]}曜日）"


# ============================================================
# instructions組み立て方針（Phase2）
# ============================================================
# 店舗オーナーがAIスタッフ設定（AIStaffSettings）を変更しても、以下の
# 優先順位を絶対に崩さないこと:
#
#   1. _CORE_RULES_TEMPLATE（Phase1の話し方の絶対ルール・言語ルール）
#   2. _SHOP_INFO_TEMPLATE（営業時間・本日の日付）
#   3. 店舗のAIスタッフ設定（性格・第一声など、instructionsとして表現）
#   4. 店舗オーナーの自由入力(custom_instructions) ※常に最下位・補助的
#   5. _EXAMPLES_AND_CONSTRAINTS_TEMPLATE（Phase1の話し方の見本・制約）
#
# 1と5はAIStaffSettingsの値によって書き換えられることは一切ない
# （build_realtime_instructions内で常に固定文言として挿入する）。
# custom_instructionsは _build_custom_instructions_section() で
# 「上位ルールと矛盾する場合は上位ルールを優先する」という位置付けを
# 明示した上で末尾近くに挿入するのみで、システムルールそのものを
# 差し替えることはできない。
#
# ai_staff_settingsが存在しない（None）店舗については、2〜4のうち
# 3・4を完全に省略し、Phase1時点と全く同じinstructions文字列を生成する
# （既存店舗への影響ゼロを保証するための設計）。

_CORE_RULES_TEMPLATE = """\
あなたは飲食店・サロン等の予約管理システム「RECEPTRA」の音声AI受付です。
店舗「{shop_name}」の音声通話に、実際の受付スタッフのように応対してください。

# 話し方の絶対ルール
- マニュアルを読み上げるような丁寧すぎる接客敬語ではなく、実際の店員が
  電話口で話すような、自然でくだけたテンポの話し言葉にしてください。
- 1回の発話は1〜2文、できるだけ短く話してください。長い前置きや繰り返しの
  お礼は不要です。
- 一度に複数のことを質問しないでください。人間の受付スタッフのように、
  一つずつ確認してください。
  悪い例:「お問い合わせいただきありがとうございます。ご予約について
  承知いたしました。それではご希望のお日にちとお時間についてお伺いさせて
  いただいてもよろしいでしょうか？」
  良い例:「はい。ご希望の日時はいつですか？」
- お客様が話し始めたら、あなたの発話は途中でも止めてください。
- お客様が言い直した場合（例:「3人、いや4人です」）は、最後に言った内容を
  正として自然に応じてください。聞き返して確認しても構いません。

# 言語ルール（最重要・絶対に守ってください）
- この通話の基本言語は日本語です。お客様が日本語で話している間は、
  通話が終わるまで日本語を維持してください。通話の途中で勝手に英語や
  他の言語に切り替えることは絶対にしないでください。
- 電話番号・数字・日付・時刻・金額を読み上げるときも、英語の発音や
  英単語（"zero" "nine" "September" "hundred" 等）を使わないでください。
  文字としては半角数字（090-1234-5678等）で渡されていても、それを
  英語として読むのではなく、日本語の発音として自然に読んでください。
- 電話番号は1桁ずつ、日本語で区切って読み上げてください。
  例:「090-1234-5678」→「ゼロキューゼロ、イチニサンヨン、
  ゴーロクナナハチ」のように読みます。
- 日付・時刻は「9月25日の19時」「9月25日の夜7時」のように自然な日本語
  で話してください。「September twenty-fifth」のような英語表現は
  禁止です。
- 金額は「5,500円」を「ごせんごひゃくえん」のように、日本語の金額表現
  として話してください。
- 言語を切り替えてよいのは、お客様が明確に英語（または他の言語）で
  話しかけてきた場合、または「英語でお願いします」のように明示的に
  言語の変更を希望した場合だけです。数字や固有名詞が含まれるという
  理由だけで言語を切り替えないでください。
- 一度英語などに切り替えた後でも、お客様が日本語で話しかけ直したら、
  自然に日本語へ戻ってください。\
"""

# Phase3B追加要件: 通話コスト削減・AI受付としての役割逸脱防止のための会話範囲ルール。
# 既存のPhase1話し方ルール・Phase2人格・Phase3安全ルールを上書きするものではなく、
# 「受付業務としてどこまでの話題に応じるか」を定義する追加ルールとして、Core Rulesの
# 直後・常に固定で挿入する（staff_settings/custom_instructionsの影響を受けない）。
# ユーザー指摘の通り、既存テンプレートを長文化させないよう独立した短いブロックにする。
_SCOPE_TEMPLATE = """\
# 会話範囲のルール（通話コスト管理・役割逸脱防止のため必ず守ってください）
あなたの役割は店舗受付業務（予約・予約に必要な確認、店舗情報・営業時間、料金や
メニュー等の案内可能な範囲、アクセス、来店に関する質問、店舗への問い合わせ、
折り返しが必要な内容の整理）です。来店・予約・店舗サービス・営業時間・駐車場・
アクセス・子供連れ・支払い方法・店舗設備・施術可否など、店舗利用に少しでも関係し
得る質問は通常どおり受付業務として対応してください。判断に迷う場合も業務内として
扱ってください。
一方、ニュース・政治・芸能・ゲーム・しりとり・長い世間話・AI自身について・人生相談
など店舗と無関係な話題には長く付き合わないでください。一度だけ短く自然に受け流し
（毎回同じ言い回しでなくて構いません）、すぐに受付業務の話に戻してください。その際、
不必要にToolを呼び出したり、店舗と無関係な情報を詳しく説明したりしないでください。\
"""

# 店舗情報。AIスタッフ設定の有無にかかわらず常に挿入する（Phase1から存在）。
_SHOP_INFO_TEMPLATE = """\
# 店舗の営業時間（曜日ごと）
{hours_block}

# 本日の日付
{today_str}（「明日」「今週土曜」などの相対的な日時表現はこれを基準に解釈してください）\
"""

# Phase3H Workstream A: 業種（Shop.business_type）に応じて、AIが会話の中で
# 「サービス」をどう呼ぶかだけを伝える最小限のセクション。
#
# 設計方針（重要）:
# - Realtime instructionsへ業種ごとの長い文章を大量に詰め込むことはせず、
#   1行の呼び方だけを差し替える最小限のmetadataにとどめる。
# - Core Rules・Scope・Examples・Constraints・Shop Knowledge Rules・
#   Booking Safetyなど、E2Eで検証済みの既存固定テンプレートは一切変更しない。
#   このセクションは、build_realtime_instructions()のdocstringが示す
#   「Phase2の新セクションはShopInfoとConstraintsの間にのみ挿入する」という
#   既存の挿入位置ルールに従い、その間にのみ追加する。
# - service_id・create_reservation等のtool/パラメータ名は一切変更しない。
#   あくまで会話中の「呼び方」だけを伝える。
# - frontend/public/shop-manage.html の SERVICE_LABEL_META と同じ考え方・
#   同じキー（business_type）を使った、意図的に重複させた別のmetadataである
#   （フロントとバックエンドでファイル・言語が異なるため）。表記を変える場合は
#   両方を更新すること。
# - Serviceの概念を使わない業種（restaurant/entertainment/other等）では、
#   このセクション自体を挿入しない（該当しない店舗のinstructionsを
#   不必要に長くしないため）。
_SERVICE_TERMINOLOGY_LABELS = {
    "beauty": "施術メニュー",
    "medical": "診療内容",
    "education": "コース・レッスン",
    "fitness": "コース・メニュー",
    "hotel": "宿泊プラン",
}

_SERVICE_TERMINOLOGY_TEMPLATE = """\
# この店舗での呼び方
この店舗の業種では、「サービス」のことを「{service_term}」と呼びます。
お客様との会話の中で呼びかけるときは、「サービス」ではなく「{service_term}」と
いう言葉を自然に使ってください（例:「ご希望の{service_term}を教えてください」）。
ツールの呼び出し自体（service_id等のパラメータ名）はこれまで通りで構いません。\
"""

# 話し方の見本。Phase1から変更しない固定文言。
# 元のテンプレートでは「言語ルール」の直後・店舗情報より前に配置されて
# いたため、Phase2でもその位置関係を維持する
# （ai_staff_settings==Noneの場合にPhase1と完全に同一のinstructions文字列
# を生成できるようにするため）。
_EXAMPLES_TEMPLATE = """\
# 話し方の見本（この温度感・テンポをそのまま真似てください）
客:「今日って空いてます？」
AI:「はい。何時頃がいいですか？」
客:「7時くらいかな」
AI:「7時ですね。何名様ですか？」
客:「3人。あ、やっぱ8時で」
AI:「はい、8時ですね。3名様で確認します。」
客:「電話番号は090-1234-5678です」
AI:「ゼロキューゼロ、イチニサンヨン、ゴーロクナナハチですね、ありがとうございます。」
客:「料金はいくら？」
AI:「5,500円です。」
客:「My name is John. English please.」
AI:（ここでは明示的に英語を希望しているため、英語に切り替えて応対する）\
"""

# 現時点での制約。Phase3Bで内容を更新（Phase1時点の「予約機能はまだ無い」という
# 記述は、Phase3A(check_availability)・Phase3B(create_reservation)の導入により
# 事実と異なるものになったため、正確な内容に書き換えた。常に最後から2番目に配置し、
# 直後に_BOOKING_SAFETY_TEMPLATEを続ける）。
_CONSTRAINTS_TEMPLATE = """\
# 現時点での制約（重要・必ず守ってください）
空き状況の確認は check_availability ツールの結果を、予約の確定は
create_reservation ツールの結果を、それぞれ受け取った場合にのみ行ってください。
ツールの結果を待たずに、自分の判断で空き状況や予約の成立を答えることは
絶対にしないでください。存在しない空き状況・予約状況・メニュー・料金を
想像で答えることも絶対にしないでください。まだ提供していないメニュー詳細・
料金等について聞かれた場合は「詳しくは店舗に直接お問い合わせください」と
ご案内してください。\
"""

# Phase3D: Shop Knowledge & FAQ に関する回答ルール。常に固定
# （店舗独自のcustom_instructionsによって上書き・無効化されない）。
# _CONSTRAINTS_TEMPLATE（推測回答の全般的な禁止）の直前に配置し、
# get_shop_infoツール固有の運用ルール（topicの使い分け・known判定・
# 構造化データとFAQの優先順位）を補強する。
_SHOP_KNOWLEDGE_RULES_TEMPLATE = """\
# 店舗情報の質問への回答ルール（重要・必ず守ってください）
駐車場・支払い方法・Wi-Fi・バリアフリー/車椅子対応・お子様連れ・喫煙可否・
ペット可否・設備・来店前の持ち物や注意事項・キャンセルや遅刻のルール・
その他店舗独自のよくある質問については、必ず get_shop_info ツールを呼び出し、
その結果だけを根拠に回答してください。呼び出す前に自分の知識や一般論、
他店舗の情報から推測して回答することは絶対にしないでください。
- 駐車場・支払い方法・Wi-Fi・バリアフリー・お子様連れ・喫煙・ペット・設備・
  来店前案内・キャンセルポリシーのように、あらかじめ決まった項目として
  尋ねられた場合は、該当するtopic（parking/payment/wifi/accessibility/
  children/smoking/pets/facilities/pre_visit/cancellation）を使ってください。
- 上記のどれにも当てはまらない、店舗独自の質問（例:「子供用の椅子はある？」
  「セットメニューはある？」等、構造化データに項目が無い質問）は、
  topic=faqでqueryにキーワードを指定して検索してください。
- 同じ会話の中で複数の話題（例: 駐車場と支払い方法の両方）を尋ねられた場合は、
  一度のtoolで済ませようとせず、topicごとに必要な回数だけ呼び出してください。
- known=falseが返ってきた場合は、その情報が「無い」のではなく「まだ店舗側で
  登録されていない」ことを意味します。この場合に「駐車場はありません」
  「カードは使えません」のように断定することは絶対にしないでください。
  必ず「情報が登録されていない」旨を正直にお伝えし、店舗へ直接お問い合わせ
  いただくようご案内してください。
- dataに含まれていない項目・値は、それがどれだけもっともらしく思えても
  絶対に推測で補わないでください。\
"""

# Outbound AI Phase 4A: Customer Memory（常連認識）に関する運用ルール。常に固定
# （店舗独自のcustom_instructionsによって上書き・無効化されない）。
# get_shop_infoと同様、find_customerツール固有の振る舞いをここで補強する。
# 重要: ここには顧客の実データ（氏名・電話番号・来店履歴等）は一切書き込まない。
# あくまで「find_customerというツールをどう使うか」という一般的な行動指針のみ
# （仕様書Phase 4A section5「巨大なRealtime instructionsへ顧客履歴を埋め込む
# 設計は禁止」を踏まえた設計）。
_CUSTOMER_MEMORY_RULES_TEMPLATE = """\
# 常連のお客様の認識に関するルール（重要・必ず守ってください）
- お客様から電話番号を伺ったタイミングで find_customer ツールを呼び出し、
  その結果を見るまで、そのお客様が以前利用したことがあるかどうかを自分で
  判断・発言しないでください。
- find_customer の結果が candidate_found の場合でも、電話番号の一致だけを
  根拠に「常連ですね」「いつもありがとうございます」のように断定的・機械的に
  発言しないでください。必ず候補の氏名を挙げて「○○様でよろしいですか？」と
  一度確認し、お客様が肯定した場合にのみ、自然な範囲で「またのご利用
  ありがとうございます」等とお伝えください。
- find_customer の結果に含まれていない情報（前回の来店日・利用内容・来店回数・
  住所等）を、本人確認の前後を問わず、推測や既知の情報であるかのように
  話すことは絶対にしないでください。
- お客様が候補の氏名を否定した場合や、find_customer の結果がnot_foundの場合は、
  以前の利用について一切触れず、初めてのお客様として通常どおり受付を
  続けてください。\
"""

# Outbound AI Phase 4B: Customer Context（本人確認後の最小限の利用）に関する
# 運用ルール。常に固定（店舗独自のcustom_instructionsによって上書き・
# 無効化されない）。_CUSTOMER_MEMORY_RULES_TEMPLATE（Phase4A: find_customerの
# 運用ルール）の直後に配置する。
# 重要: ここにも顧客の実データは一切書き込まない。あくまで
# confirm_customer_identity / get_customer_context という2つのツールを
# どう使うかという一般的な行動指針のみ（仕様書Phase4B section18準拠。
# 「既存instructionsを巨大化させない・短く明確に」という要求に従い、
# 医療/非医療の分岐等は一切書かない＝バックエンド側の構造で保証されて
# いるため、AIへは「含まれていない項目を推測しない」の一文だけで十分）。
_CUSTOMER_CONTEXT_RULES_TEMPLATE = """\
# 本人確認後のご利用情報（Customer Context）に関するルール（重要・必ず守ってください）
- find_customer で候補が見つかり、お客様が候補の氏名を肯定したら、必ず
  confirm_customer_identity ツールを confirmed=true で呼び出してください。
  お客様が否定した場合や別の名前を名乗った場合は confirmed=false で呼び出し、
  以降その候補の情報は一切使わないでください。
- confirm_customer_identity の呼び出し結果がverifiedになるまで、
  get_customer_context ツールを呼び出したり、以前のご利用内容を口にしたり
  しないでください。
- get_customer_context は引数なしで呼び出せます。戻り値に含まれている
  フィールドだけを使ってください。含まれていない項目（前回のサービス内容・
  担当者名など）は「情報が無い」という意味であり、絶対に推測や一般論で
  補わないでください。
- get_customer_context の結果は、会話上自然に必要な場合にのみ使ってください。
  毎回「以前のご利用履歴があります」のように機械的に触れる必要はありません。
  特に来店回数が1回だけの場合に「いつもありがとうございます」のような
  複数回利用を前提にした表現は絶対に使わないでください。
- 「前回と同じでお願いします」のようにお客様から言われた場合、
  get_customer_context の結果に前回のサービス内容が含まれていれば、
  それを候補として会話を進めて構いません。ただしその場合も、日時の空き
  状況確認・予約全体の最終確認を経て create_reservation を呼び出すまでは
  予約を確定しないでください（この手順を Customer Context によって
  省略することはできません）。
- get_customer_context の結果に含まれる前回の担当者について、その担当者を
  自動的に今回の予約へ割り当てないでください。あくまで会話上の参考情報
  としてのみ使い、実際の指名はお客様の意思を改めて確認してください。\
"""

# Phase3B: 予約成立の宣言に関する安全ルール。常に最後に配置する固定文言
# （店舗独自のcustom_instructionsによって上書き・無効化されない）。
#
# 重要な設計上の制約（調査結果）: Realtime Voiceはブラウザ⇔OpenAI直結のWebRTCで
# あり、RECEPTRAサーバーは音声・会話内容を一切経由しない。そのためチャット予約
# (shop_booking_ai.py)が使っている「AIの発話を後からシステム側の確定文言に
# 差し替える」仕組み(override_last_assistant_message)はここでは技術的に使えない
# （一度声に出した発話を後から書き換えることはできない）。したがってこの
# テンプレートおよびcreate_reservationツールの説明文による指示が、AIに誤った
# 予約成立を宣言させないための唯一の防御線となる（Phase3Aのtemporarily_unavailable
# 運用で実証済みの「tool descriptionによる挙動制御は高い信頼性で機能する」という
# 知見に基づく設計だが、100%の技術的保証ではないことに留意）。なお、AIが仮に
# 先走って発話してしまった場合でも、DBへの実際の書き込みはcreate_reservation()の
# 成功可否だけで決まるため、システム上の実際の予約状態が誤ることはない。
_BOOKING_SAFETY_TEMPLATE = """\
# 予約成立の宣言に関する絶対ルール（最重要・必ず守ってください）
- create_reservation ツールを呼び出す前に、必ず次のすべてを一つずつお客様と
  確認してください: 来店希望日時、人数、お名前、電話番号
  （該当する場合はサービス内容・スタッフ指名）。
- 電話番号は、お客様からうかがった内容を必ず1桁ずつ日本語で読み上げて復唱し、
  お客様に間違いがないか確認してから先に進んでください。電話番号を推測したり、
  聞き取れなかった桁を適当に補ったりすることは絶対にしないでください。

# 「項目ごとの確認」と「予約全体の最終確認」は別物です（重要・混同禁止）
- 電話番号・日時・人数・お名前・サービス・スタッフ指名など、個別の項目について
  「合っていますか？」と尋ねたときの「はい」「合っています」「そうです」等の
  返事は、その項目単体が正しいことの確認にすぎません。予約を確定してよいという
  同意として扱ってはいけません。
- 個別項目の確認がすべて終わったら、それとは別の独立した発話として、日時・人数・
  お名前・電話番号（該当する場合はサービス・スタッフ指名）をすべてまとめて
  読み上げ、「この内容で予約してよろしいですか？」のようにはっきりと尋ねて
  ください。この「予約全体の最終確認」の直後にお客様が返した返事だけを、
  create_reservation 呼び出しの同意として扱ってください。個別項目確認への
  返事を後から予約全体の同意として流用することは絶対にしないでください。
- 予約全体の最終確認をした後でも、お客様が日時・人数・お名前・電話番号・
  サービス・スタッフ指名のいずれかを変更・訂正した場合、それ以前の同意は
  無効になります（変更前の内容のまま予約してはいけません）。変更内容を確認
  した上で（日時が変わった場合は check_availability で空き状況も再確認した
  上で）、もう一度予約全体をまとめ直し、改めて最終確認をやり直してください。
- 予約全体の最終確認への返事が「うーん」「たぶん」「ちょっと待って」
  「どうしようかな」のように曖昧な場合は、create_reservation を呼び出さず、
  もう一度はっきりと確認し直してください。
- 「やめます」「予約しません」「ちょっと考えます」等、否定・保留の返事の
  場合は、create_reservation を絶対に呼び出さないでください。
- 上記のとおり、予約全体の最終確認に対する明確な肯定が得られた場合にのみ
  create_reservation ツールを呼び出してください。曖昧な返事や沈黙のまま
  呼び出してはいけません。
- create_reservation ツールを呼び出してから結果が返るまでの間は、
  「予約状況を確認して確定しますので、少々お待ちください」程度の
  中立的な案内だけにとどめてください。
- ツールの結果で success が true になるまでは、次のような予約成立を意味する
  発話を絶対にしないでください:
  「予約できました」「ご予約完了です」「お取りしました」「承りました」
  「予約確定しました」等。
- ツールの結果が success:true で返ってきたら、その結果に含まれる日時・人数と
  完全に一致する内容だけを使ってお客様にご案内してください。
- ツールの結果が success:false の場合、reason_code に応じて安全に案内してください
  （create_reservation ツールの説明文にある案内方針に従ってください）。
  空いている・予約できたと推測することは絶対にしないでください。\
"""


def _build_personality_section(s: "AIStaffSettings") -> str:
    """
    AIStaffSettingsの値をRealtime APIのinstructions（自然文）に変換する。
    重要: politeness_level/brightness/energy_levelはRealtime APIの
    独立したパラメータではなく、あくまでこの関数がテキストへ変換する
    「値」に過ぎない。この関数の出力そのものがinstructionsの一部になる。
    """
    lines = []
    if s.staff_name:
        lines.append(f"- あなたの名前は「{s.staff_name}」です。お客様に名乗る際はこの名前を使ってください。")

    politeness_map = {
        "formal": "丁寧な敬語を基本にしつつ、堅苦しくなりすぎない自然な話し方にしてください。",
        "standard": "標準的な接客敬語で、親しみやすさも感じられる話し方にしてください。",
        "casual": "敬語は保ちつつも、カジュアルで親しみやすい、くだけたトーンで話してください。",
    }
    if s.politeness_level in politeness_map:
        lines.append(f"- {politeness_map[s.politeness_level]}")

    brightness_map = {
        "bright": "声のトーンは明るく、はきはきとした印象で話してください。",
        "neutral": "声のトーンは自然体で、落ち着きと親しみやすさのバランスを意識してください。",
        "calm": "声のトーンは落ち着いていて、ゆったりと安心感のある話し方にしてください。",
    }
    if s.brightness in brightness_map:
        lines.append(f"- {brightness_map[s.brightness]}")

    energy_map = {
        "high": "テンションはやや高めに、元気で活気のある応対を心がけてください。",
        "medium": "テンションは中庸に、落ち着きつつも活気を感じさせる応対を心がけてください。",
        "low": "テンションは控えめに、静かで丁寧な応対を心がけてください。",
    }
    if s.energy_level in energy_map:
        lines.append(f"- {energy_map[s.energy_level]}")

    if not lines:
        return ""
    return "# AIスタッフの人物設定\n" + "\n".join(lines)


def _resolve_greeting_text(s: Optional["AIStaffSettings"], shop_name: str) -> str:
    """
    実際に話す（または読み上げる）べき第一声の文字列そのものを解決する。
    店舗オーナーが設定していない場合や空文字の場合は、スタッフ名の有無に
    応じた自然なフォールバック文を使う（設定が存在しても greeting が
    空欄なら「名乗らない」フォールバックになるよう配慮している）。

    Phase3C.1追記: この関数は_build_greeting_section（Realtime instructions
    への埋め込み用）と、generate_greeting_tts_audio（Zero-Wait Greeting用の
    事前生成TTS音声）の両方から共通で呼ばれる。両者が違う文言を使うと
    「事前生成音声とAIの認識している第一声が食い違う」事態になるため、
    第一声の実際の文言はこの関数の一箇所だけで決定する。

    Phase3E-3追記（Zero-Wait Greeting「即名乗り」改善）: staff_nameが
    設定されているにもかかわらず、店舗オーナーが入力したカスタムgreeting
    文言にその名前が含まれていない場合、電話に出た瞬間（＝この文言の冒頭）
    で名乗れていないことになる。これを防ぐため、カスタムgreetingが設定
    されていても、staff_nameが設定済みかつその文言に名前が含まれていない
    場合は、短い名乗りを文頭に補う。カスタムgreetingに既に名前が含まれて
    いる場合は補わない（二重に名乗ることを避ける。単純な部分一致判定に
    留め、過剰な自然言語処理は行わない）。
    """
    greeting = ""
    staff_name = None
    if s is not None:
        greeting = (s.greeting or "").strip()
        staff_name = (s.staff_name or "").strip() or None
    if not greeting:
        if staff_name:
            return f"お電話ありがとうございます。{shop_name}、AI受付の{staff_name}です。"
        return f"お電話ありがとうございます。{shop_name}でございます。"
    if staff_name and staff_name not in greeting:
        return f"{shop_name}、AI受付の{staff_name}です。{greeting}"
    return greeting


def _build_greeting_section(s: Optional["AIStaffSettings"], shop_name: str) -> str:
    """
    電話に出たときの第一声をRealtime instructionsへ埋め込む形に整形する。

    Phase3C追記: この関数はAIスタッフ設定(staff_settings)の有無に関わらず、
    全店舗のinstructionsに常時含める（build_realtime_instructions側の
    呼び出し箇所を参照）。理由: フロントエンドの第一声用response.createは
    response-level instructionsを付与しない設計（session側のinstructionsを
    そのまま使わせるため）にしたため、AIスタッフ設定が無い店舗であっても
    「通話開始時にまず自分から話し始める」という指示自体はsession
    instructions側に常に存在している必要がある。s=Noneの場合は
    「名乗らない」フォールバック文のみを使う（Phase1のみの店舗の人格・
    名乗りに関する既存の沈黙を破らないため）。
    """
    greeting = _resolve_greeting_text(s, shop_name)
    return (
        "# 電話に出たときの第一声\n"
        "通話が始まったら、まず最初に次のような内容を自然に話してください"
        "（一字一句同じである必要はありませんが、内容・トーンは維持してください）。\n"
        f"「{greeting}」"
    )


def _build_custom_instructions_section(custom_instructions: str) -> str:
    """
    店舗オーナーの自由入力を、システムルール・言語ルールより下位の
    補助的な指示として明示的に位置づけた上でinstructionsへ挿入する。
    重要: この関数の出力はユーザー入力をそのまま最上位instructionsとして
    扱うものではなく、必ず「矛盾する場合は上位ルールを優先する」という
    枠組みの中に埋め込む。
    """
    return (
        "# 店舗独自の補助的な接客指示（下位ルール・矛盾時は上位ルールを優先）\n"
        "以下は店舗オーナーが設定した、この店舗独自の補助的な接客スタイルの"
        "指示です。上記の「話し方の絶対ルール」「言語ルール（最重要）」および"
        "「現時点での制約」と矛盾する内容が含まれていた場合は、必ず上記の"
        "ルールを優先してください。特に、日本語を維持すること・電話番号や"
        "日付や金額を日本語で読み上げること・存在しない予約状況を答えないこと"
        "は、この補助的な指示によって変更・無効化することはできません。\n"
        "---\n"
        f"{custom_instructions}\n"
        "---"
    )


async def build_realtime_instructions(
    db: AsyncSession, shop: Shop, staff_settings: Optional["AIStaffSettings"] = None
) -> str:
    """
    Realtime AIのシステムプロンプト(instructions)を組み立てる。

    連結順序（Phase1の元テンプレートにおける並び順 Core→Examples→ShopInfo→
    Constraints をそのまま維持し、Phase2の新セクションはShopInfoと
    Constraintsの間にのみ挿入する。Phase3B追加要件のScopeはCoreの直後に挿入）:
      1. Core Rules（話し方の絶対ルール・言語ルール）        … 常に固定・最上位
      2. Scope（Phase3B: 受付業務の会話範囲ルール）          … 常に固定
      3. Examples（話し方の見本）                            … 常に固定
      4. Shop Information（営業時間・本日の日付）            … 常に固定
      4b. Service Terminology（Phase3H: 業種別の「サービス」呼称） … 該当業種のみ
      5a. AI Staff Personality                               … staff_settingsがある場合のみ
      5b. 電話に出たときの第一声（Greeting）                 … Phase3Cより常に付与
      6. Shop Custom Instructions（店舗独自の補助指示）      … 明示的に下位と位置づけ
      7. Constraints（現時点での制約）                       … 常に固定
      7b. Shop Knowledge Rules（Phase3D: get_shop_info運用ルール） … 常に固定
      7c. Customer Memory Rules（Phase4A: find_customer運用ルール） … 常に固定
      7d. Customer Context Rules（Phase4B: confirm_customer_identity /
          get_customer_context運用ルール） … 常に固定
      8. Booking Safety（Phase3B: 予約成立宣言の絶対ルール） … 常に固定・最後

    staff_settingsがNone、またはPhase2で追加されたフィールドが全て未設定の
    場合は、5a・6が省略される。なお、Phase3A/3Bの導入以降は7の内容自体が
    Phase1と異なり（check_availability/create_reservationの存在を前提とした
    内容に更新済み）、2・8も常に付与されるため、「staff_settingsがNoneならPhase1と
    完全にバイト同一」という以前の不変条件はPhase3A時点で既に崩れている
    （toolsを常時有効化した時点でPhase1とは別物であるため、この崩れ自体は
    Phase3Bで新たに生じたものではない）。

    Phase3C追記: 5b（電話に出たときの第一声）は、staff_settingsの有無に
    関わらず常に付与するよう変更した。理由は_build_greeting_section()の
    docstringを参照（フロントエンドの第一声用response.createが
    response-level instructionsを付与しない設計になったため、
    「通話開始時に自分から話し始める」という指示自体をsession instructions
    側に常時含める必要があるため）。
    """
    hours_block = await _build_hours_block(db, shop.id)

    sections = [
        _CORE_RULES_TEMPLATE.format(shop_name=shop.name),
        _SCOPE_TEMPLATE,
        _EXAMPLES_TEMPLATE,
        _SHOP_INFO_TEMPLATE.format(hours_block=hours_block, today_str=_today_str_jst()),
    ]

    # Phase3H Workstream A: 業種に応じた「サービス」の呼び方（該当する業種のみ）。
    service_term = _SERVICE_TERMINOLOGY_LABELS.get(shop.business_type)
    if service_term:
        sections.append(_SERVICE_TERMINOLOGY_TEMPLATE.format(service_term=service_term))

    if staff_settings is not None:
        personality_section = _build_personality_section(staff_settings)
        if personality_section:
            sections.append(personality_section)

    # Phase3C: 第一声の案内は、AIスタッフ設定の有無に関わらず常に追加する。
    # 理由: フロントエンドが通話開始直後に送る第一声用response.createは
    # response-level instructionsを付与しないため（付与するとOpenAI Realtime
    # APIの仕様によりsession instructions全体がそのresponseに限り上書きされて
    # しまい、AIスタッフ設定のgreeting/人格が第一声に反映されなくなることが
    # 本番E2E（Test F）で確認された）、「通話開始時にまず自分から話し始める」
    # という指示自体をsession instructions側に常時含める必要がある。
    # staff_settingsがNoneの場合は_build_greeting_section内部の
    # 「名乗らない」フォールバック文のみが使われ、Phase1のみの店舗の人格・
    # 名乗りに関する既存の沈黙（personality_section等）は変更しない。
    sections.append(_build_greeting_section(staff_settings, shop.name))

    if staff_settings is not None:
        if staff_settings.custom_instructions and staff_settings.custom_instructions.strip():
            sections.append(_build_custom_instructions_section(staff_settings.custom_instructions.strip()))

    sections.append(_CONSTRAINTS_TEMPLATE)
    sections.append(_SHOP_KNOWLEDGE_RULES_TEMPLATE)
    sections.append(_CUSTOMER_MEMORY_RULES_TEMPLATE)
    sections.append(_CUSTOMER_CONTEXT_RULES_TEMPLATE)
    sections.append(_BOOKING_SAFETY_TEMPLATE)

    return "\n\n".join(sections)


async def _get_staff_settings(db: AsyncSession, shop_id: str) -> Optional[AIStaffSettings]:
    result = await db.execute(select(AIStaffSettings).where(AIStaffSettings.shop_id == shop_id))
    return result.scalars().first()


async def create_realtime_session(db: AsyncSession, shop: Shop) -> dict:
    """
    ブラウザ用の短命ephemeralトークン(client secret)を発行する。

    ブラウザはこのトークンをBearerトークンとしてOpenAIのWebRTCエンドポイントに
    直接接続する（RECEPTRAのサーバーは音声そのものを中継しない）。
    """
    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY が設定されていません")

    staff_settings = await _get_staff_settings(db, shop.id)
    instructions = await build_realtime_instructions(db, shop, staff_settings)

    # Phase2: 店舗オーナーがAIスタッフ設定でvoiceを指定していればそちらを
    # 使用し、未設定（レコード自体が無い、またはvoiceが空）の場合は
    # Phase1と同じくapp.config.Settings.OPENAI_REALTIME_VOICEにフォールバック
    # する。voiceの値自体はスキーマ層(app.schemas.ai_staff_settings)で
    # REALTIME_VOICESに含まれるものだけが保存されるよう検証済みのため、
    # ここでの再検証は行わない。
    voice = (staff_settings.voice if staff_settings and staff_settings.voice else settings.OPENAI_REALTIME_VOICE)

    # 会話品質の調査結果（2026年9月）を踏まえた明示設定。
    # 以前はvoice/turn_detectionを未指定にしており、OpenAI側の暗黙の
    # デフォルトに委ねていたことが「日本語がカタコト」「テンポが不自然」
    # という報告の一因と考えられたため、公式ドキュメントの推奨に沿って
    # 明示的に指定する。
    #
    # 重要: この2項目は、インストール済みのopenaiパッケージ(1.109.1)が
    # 生成する型定義(RealtimeAudioConfigOutputParam/InputParam)で
    # session.audio.output.voice / session.audio.input.turn_detection という
    # ネスト構造であることを直接確認した上で、その構造で送っている
    # （以前の実装案ではセッション直下にvoice/turn_detectionを置いていたが、
    # それは誤りだったため、実装時に修正した）。
    session_config: dict = {
        "type": "realtime",
        "model": settings.OPENAI_REALTIME_MODEL,
        "instructions": instructions,
        "audio": {
            "output": {
                # Phase2: 店舗のAIスタッフ設定で選ばれたvoice（未設定時は
                # OpenAI公式のWebRTC接続ガイド・TTSガイドがgpt-realtime-2.1
                # との組み合わせで推奨しているデフォルト音声にフォールバック）。
                "voice": voice,
            },
            "input": {
                # 現行デフォルトのsemantic_vad
                # （無音時間ではなく発話内容の区切りで判定）を明示化。
                # eagernessは今後の実機テストでチューニングする前提の初期値。
                "turn_detection": {
                    "type": "semantic_vad",
                    "eagerness": settings.OPENAI_REALTIME_VAD_EAGERNESS,
                },
            },
        },
    }

    # Phase3A: check_availability Tool Callingを有効化。
    # session.tools はopenai==1.109.1のSDK型定義(RealtimeSessionCreateRequest.tools /
    # RealtimeToolsConfig)でセッション直下のフラットな配列であることを確認済み。
    # voice-preview用セッション(create_voice_preview_session)には意図的に含めない
    # （試聴は声質確認のみが目的で、予約関連の会話を行わないため）。
    session_config["tools"] = _REALTIME_TOOLS

    # reasoning（推論の深さ）: gpt-realtime-2.1のモデルページには
    # 「configurable reasoning effortに対応し、上げるほどレイテンシと
    # トークン使用量が増える」と記載があるが、インストール済みの
    # openai==1.109.1が生成する型定義には現時点でこのフィールドが
    # 一切存在しない（実装時にopenai/types/realtime配下を直接確認して
    # 確認済み）。つまり実際のAPIが本当にこのフィールドを受け付けるかは
    # 未検証。本番同等環境でしか実キーによる検証ができないため、
    # 送信自体は試すが、無効な場合に備えて空文字にすればこのフィールド
    # ごと送らないようにしてあり、デプロイ後にセッション発行が失敗する
    # 場合はまずこの値を空にして切り分けられるようにしている。
    if settings.OPENAI_REALTIME_REASONING_EFFORT:
        session_config["reasoning"] = {
            "effort": settings.OPENAI_REALTIME_REASONING_EFFORT,
        }

    client = _get_client()
    secret = await client.realtime.client_secrets.create(
        expires_after={
            "anchor": "created_at",
            "seconds": settings.REALTIME_CLIENT_SECRET_TTL_SECONDS,
        },
        session=session_config,
    )

    logger.info(
        "Realtimeセッションを発行 shop_id=%s model=%s voice=%s vad_eagerness=%s "
        "reasoning_effort=%s has_staff_settings=%s expires_at=%s",
        shop.id,
        settings.OPENAI_REALTIME_MODEL,
        voice,
        settings.OPENAI_REALTIME_VAD_EAGERNESS,
        settings.OPENAI_REALTIME_REASONING_EFFORT,
        staff_settings is not None,
        secret.expires_at,
    )

    return {
        "client_secret": secret.value,
        "expires_at": secret.expires_at,
        "model": settings.OPENAI_REALTIME_MODEL,
    }


async def create_voice_preview_session(voice: str) -> dict:
    """
    店舗管理画面の「voice試聴」機能用の、短命ephemeralトークンを発行する。

    設計方針:
    - 通常の通話セッション(create_realtime_session)とは完全に独立した、
      最小限のセッションを発行する。店舗情報・言語ルール等は一切含めない
      （試聴の目的はvoiceの声質確認のみのため）。
    - OPENAI_API_KEY自体はここでもサーバー側にのみ保持し、ブラウザには
      ephemeralなclient_secretのみを渡す（通常セッションと同じ方式）。
    - ブラウザ側は、この関数が返すclient_secretを使って通常の通話画面と
      同様にWebRTC接続を行うが、マイク入力(getUserMedia)は行わず、無音の
      ローカルトラックのみを送信し、接続後すぐに応答をトリガーして
      音声を再生したら数秒で切断する想定（試聴のためにマイク許可を
      求める必要をなくすため）。
    - voice自体の値の妥当性チェックはAPI層(app.schemas.ai_staff_settings.
      VoicePreviewRequest)で REALTIME_VOICES に対して行われている前提で、
      ここでは再チェックのみ行う（直接この関数が呼ばれる場合に備えて）。
    """
    if voice not in REALTIME_VOICES:
        raise ValueError(f"不正なvoiceです: {voice}")

    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY が設定されていません")

    session_config: dict = {
        "type": "realtime",
        "model": settings.OPENAI_REALTIME_MODEL,
        "instructions": _VOICE_PREVIEW_INSTRUCTIONS,
        "audio": {
            "output": {
                "voice": voice,
            },
            "input": {
                # 試聴では実際のマイク入力を送らないため、VADはお客様の発話を
                # 検知する目的では機能しないが、セッション設定としては
                # 通常セッションと同じ形式で明示しておく。
                "turn_detection": {
                    "type": "semantic_vad",
                    "eagerness": settings.OPENAI_REALTIME_VAD_EAGERNESS,
                },
            },
        },
    }

    client = _get_client()
    # 試聴用トークンは通常セッションより短命でよいため、上限30秒とする
    # （数秒の再生が終わればブラウザ側で即座に切断する想定）。
    secret = await client.realtime.client_secrets.create(
        expires_after={"anchor": "created_at", "seconds": 30},
        session=session_config,
    )

    logger.info("voice試聴セッションを発行 voice=%s model=%s", voice, settings.OPENAI_REALTIME_MODEL)

    return {
        "client_secret": secret.value,
        "expires_at": secret.expires_at,
        "model": settings.OPENAI_REALTIME_MODEL,
        "voice": voice,
    }


async def generate_greeting_tts_audio(shop: Shop, staff_settings: Optional["AIStaffSettings"]) -> dict:
    """
    Phase3C.1 PoC: Zero-Wait Greeting用に、店舗の第一声をテキスト読み上げ
    (POST /v1/audio/speech 相当)で事前生成する。

    重要な設計上の制約:
    - これは通話中の会話生成(Realtime API)とは完全に別物。第一声の文字列
      そのものは_resolve_greeting_text()で解決し、Realtime instructions側
      （_build_greeting_section）と完全に同じ文言を使う（食い違い防止）。
    - この関数自体はバイト列を返すだけで、DBにもファイルシステムにも一切
      保存しない（Railwayのコンテナファイルシステムへ実行時に書き込むと、
      デプロイ毎に消えるため、そのような永続化はしない）。呼び出し元が
      保存を担う。
      - お客様向けZero-Wait Greeting（get_or_generate_greeting_audio()）は
        AIStaffSettings.greeting_audio_dataへDB保存し、以後はキャッシュを
        再利用する（Phase3E-3）。
      - app/routers/ai_staff_settings.py の POST /greeting-audio
        （オーナー認証必須の試聴専用エンドポイント）は、その場で生成した
        音声をそのまま返すだけで一切保存しない（オーナーが今の設定で
        どう聞こえるかその場で確認するための機能であり、Zero-Wait用
        キャッシュとは別物）。
    - voiceはRealtime API用のvoice設定(AIStaffSettings.voice)をそのまま
      流用する。2026年9月時点のopenai SDK(1.109.1)の型定義を確認したところ、
      /v1/audio/speech の voice パラメータは alloy/ash/ballad/coral/echo/
      sage/shimmer/verse/marin/cedar という、REALTIME_VOICESと完全に同一の
      10種類になっている（以前は別々のvoice一覧だったが、現時点では
      両APIが同じvoice名を受け付ける）。ただし「同じ名前を受け付ける」ことは
      「音質が完全に同一」であることを保証しない点に注意
      （基盤モデルが異なるため）。
    """
    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY が設定されていません")

    greeting_text = _resolve_greeting_text(staff_settings, shop.name)
    voice = (staff_settings.voice if staff_settings and staff_settings.voice else settings.OPENAI_REALTIME_VOICE)

    client = _get_client()
    resp = await client.audio.speech.create(
        model=settings.OPENAI_TTS_MODEL,
        voice=voice,
        input=greeting_text,
        response_format="mp3",
    )
    audio_bytes = await resp.aread()

    logger.info(
        "Zero-Wait Greeting音声を生成 shop_id=%s voice=%s model=%s bytes=%d greeting_len=%d",
        shop.id, voice, settings.OPENAI_TTS_MODEL, len(audio_bytes), len(greeting_text),
    )

    return {
        "audio_bytes": audio_bytes,
        "voice": voice,
        "model": settings.OPENAI_TTS_MODEL,
        "greeting_text": greeting_text,
    }


async def get_effective_greeting_text(db: AsyncSession, shop: Shop) -> str:
    """
    Phase3C.1: Zero-Wait Greeting用に、通話ページ（お客様向け・認証不要）が
    「今この店舗のRealtime instructions／事前生成音声がどちらも前提としている
    第一声の文字列」を取得するための公開ヘルパー。

    重要:
    - _resolve_greeting_text()を唯一の正として使う（build_realtime_instructions
      が組み立てるinstructions、generate_greeting_tts_audio()が生成する音声の
      両方と、常に完全一致する）。
    - 認証不要のroutersからも安全に呼べるよう、DBアクセス(_get_staff_settings)
      までこの関数の中に閉じ込める。第一声の文言はお客様に電話で話しかける
      内容そのものであり秘匿情報ではないため、公開エンドポイントから返しても
      情報漏洩にはならない（既存の/realtime-voice/sessionが認証不要なのと
      同じ扱い）。
    - Zero-Wait Greeting成功時にRealtimeの会話履歴へ
      「この文言は既に話した」と伝える(conversation.item.create)ためだけに
      使う。事前生成音声ファイル自体の内容と完全に一致している保証は、
      両者が同じ_resolve_greeting_text()を経由していることに依存する。
      Phase3E-3以降、音声はget_or_generate_greeting_audio()経由でDBに
      キャッシュ・自動再生成されるため（下記参照）、この文言との食い違いは
      発生しない（両方とも同じ_resolve_greeting_text()の同時点の出力を使う）。
    """
    staff_settings = await _get_staff_settings(db, shop.id)
    return _resolve_greeting_text(staff_settings, shop.name)


def _compute_greeting_audio_fingerprint(voice: str, greeting_text: str) -> str:
    """
    Phase3E-3: Zero-Wait Greeting音声のキャッシュキー。
    実際にTTSへ渡す(voice, greeting_text)の組から一意のハッシュ値を計算する。
    staff_name/voice/greetingのいずれかが変わればgreeting_text/voiceの
    どちらかが変わり、結果としてこのフィンガープリントも変わるため、
    3つの設定値を個別に追跡する必要がない（_resolve_greeting_text()の
    出力さえ変われば自動的にキャッシュミスになる）。
    """
    raw = f"{voice}\x00{greeting_text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def get_or_generate_greeting_audio(db: AsyncSession, shop: Shop) -> dict:
    """
    Phase3E-3: Zero-Wait Greeting音声のキャッシュ取得・必要なら自動再生成。

    Phase3C.1のPoCでは、事前生成音声を開発者が手動でfrontend/public/配下に
    配置する運用だったため、staff_name/voice/greetingを変更しても音声だけが
    古いまま残り続ける既知の問題があった。この関数はその代替であり、
    AIStaffSettings.greeting_audio_* カラム（DB。Railwayのコンテナローカル
    ファイルシステムのように再デプロイで消えることはない）に生成済み音声を
    キャッシュし、現在の設定から計算したフィンガープリントと保存済みの
    フィンガープリントを比較することで、変更を検知して自動的に再生成する。

    - 一致（キャッシュヒット）: DBに保存済みの音声バイト列をそのまま返す。
      OpenAI TTSへのリクエストは発生しない（高速・低コスト）。
    - 不一致 or 未生成（キャッシュミス）: generate_greeting_tts_audio()で
      その場で生成し、AIStaffSettingsの行が存在する場合はそこへ保存してから
      返す。AIStaffSettingsの行自体が存在しない店舗（一度もAIスタッフ設定を
      保存したことがない店舗）は、キャッシュを保持する行が無いため毎回生成
      する（対象はZero-Wait Greeting有効店舗のみで、通常は事前に設定済み
      であるため実運用上は稀なケース）。ここで新規に行を作成することは
      あえて行わない（未認証の公開エンドポイント経由でDB行を作成する
      副作用を避けるため）。

    キャッシュミス時のみOpenAI TTSの応答時間（数百ms〜数秒）がかかるが、
    これはページ読み込み時のプリロード中に発生するため、通話開始クリックから
    音声再生開始までのZero-Wait本来のレイテンシ（~15-40ms、キャッシュヒット
    時のDB読み出しのみ）には影響しない。

    実装メモ: greeting_audio_dataはdeferred（通常のinstructions組み立て等では
    毎回この大きなバイト列を読み込まないようにするため）なので、_get_staff_settings
    共通ヘルパーではなく、この関数専用にundefer()を指定したクエリを使う。
    """
    result = await db.execute(
        select(AIStaffSettings)
        .options(undefer(AIStaffSettings.greeting_audio_data))
        .where(AIStaffSettings.shop_id == shop.id)
    )
    staff_settings = result.scalars().first()
    greeting_text = _resolve_greeting_text(staff_settings, shop.name)
    settings = get_settings()
    voice = (staff_settings.voice if staff_settings and staff_settings.voice else settings.OPENAI_REALTIME_VOICE)
    fingerprint = _compute_greeting_audio_fingerprint(voice, greeting_text)

    if (
        staff_settings is not None
        and staff_settings.greeting_audio_fingerprint == fingerprint
        and staff_settings.greeting_audio_data
    ):
        return {
            "audio_bytes": staff_settings.greeting_audio_data,
            "content_type": staff_settings.greeting_audio_content_type or "audio/mpeg",
            "voice": voice,
            "cache": "hit",
        }

    result = await generate_greeting_tts_audio(shop, staff_settings)
    audio_bytes = result["audio_bytes"]

    if staff_settings is not None:
        staff_settings.greeting_audio_data = audio_bytes
        staff_settings.greeting_audio_content_type = "audio/mpeg"
        staff_settings.greeting_audio_fingerprint = fingerprint
        staff_settings.greeting_audio_generated_at = datetime.utcnow()
        await db.commit()
        logger.info(
            "Zero-Wait Greeting音声のキャッシュを更新 shop_id=%s fingerprint=%s",
            shop.id, fingerprint[:12],
        )

    return {
        "audio_bytes": audio_bytes,
        "content_type": "audio/mpeg",
        "voice": voice,
        "cache": "miss",
    }
