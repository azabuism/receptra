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
from app.language_registry import (
    REQUIRED_AI_LANGUAGE, display_name_for, effective_ai_languages, effective_languages,
)

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

# オーナー管理画面（shop-manage.html）向けの表示名の単一の対応表（一元管理）。
#
# 重要（必ず守ること・谷村様の明示的な指示）: OpenAIは2026年9月時点で、Realtime API
# 用の上記10種類のvoiceについて「男性の声」「女性の声」のような公式な性別分類を
# 一切公開していない（platform.openai.com/docs/guides/text-to-speech および
# developers.openai.com/api/docs/guides/realtime-conversations の両公式ドキュメント
# を確認済み。書かれているのは「marin/cedarは音質面で推奨」という説明のみで、
# 声質・性別についての説明は無い）。そのため、声の名前や印象だけでClaude/実装側が
# 独自に「男性A/女性A」のような性別ラベルを推測して割り当てることはしない
# （事実でないものを事実であるかのようにUIへ表示してしまうため）。
# 代わりに、声のIDそのものを見せても非技術者の店舗オーナーには分かりにくいという
# 課題にだけ対処する、性別を示唆しない中立的な表示名（声A〜声J）を割り当てる。
# 「試聴」ボタンで実際に聴いて選ぶことを前提としたUIにする。
#
# 表示名とvoice IDの対応はこの辞書のみを唯一の情報源とする
# （frontend/public/shop-manage.html 側では絶対にハードコードしない。
# GET /voice-options のレスポンスを通じて必ずこの辞書経由で渡す）。
# 表示順は、音質面で公式に推奨されているmarin/cedarを先頭に、残りは
# REALTIME_VOICESの定義順のまま割り当てている。
REALTIME_VOICE_DISPLAY_LABELS = {
    "marin": "声A",
    "cedar": "声B",
    "alloy": "声C",
    "ash": "声D",
    "ballad": "声E",
    "coral": "声F",
    "echo": "声G",
    "sage": "声H",
    "shimmer": "声I",
    "verse": "声J",
}

# 音質面でOpenAI公式に推奨されているvoice（表示名にも小さく添える）。
REALTIME_VOICES_RECOMMENDED = {"marin", "cedar"}

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
            "来店希望日時・人数など必要な情報がお客様から得られたら、確認を待たず"
            "必ずこの関数を呼び出してください。この結果が返る前に、空いているか"
            "どうかを自分で判断したり、お客様に案内したりしないでください。\n"
            "重要（許可を求めない）: 「空き時間を確認しますね」「確認します」"
            "「少々お待ちください」のような予告を発話する場合、その予告だけを"
            "話して発話を終えないでください。この予告は質問ではないため、"
            "お客様の「はい」「お願いします」といった返事や許可を待つ必要は"
            "一切ありません。予告をしたら、同じ発話の流れの中でそのままこの関数を"
            "呼び出してください。「確認してもよろしいですか？」のように、"
            "呼び出してよいかどうかをお客様に尋ねることも絶対にしないでください。"
            "予告だけをして黙り込むことも絶対にしないでください。\n"
            "dateは必ずYYYY-MM-DD形式、timeは必ずHH:MM形式（24時間表記）で指定してください。\n"
            "美容院・クリニックなどサービス単位の予約でサービス指定がある場合は"
            "service_idを、スタッフ指名がある場合はstaff_idを指定してください"
            "（どちらも該当する場合のみ。省略可）。\n"
            "戻り値のavailableがtrueの場合、空いている旨と、まだ聞けていない"
            "次の項目（お名前・電話番号など）への質問を、一つの発話の中で"
            "続けて行ってください（例:「13時でお取りできます。お名前をお願い"
            "します。」）。空いていることだけを伝えて発話を終え、お客様からの"
            "「お願いします」等の返事を待ってから次の質問をする、という進め方は"
            "しないでください。\n"
            "戻り値のavailableがfalseの場合、reason_codeを見て案内してください。"
            "fully_booked=満席、outside_business_hours=営業時間外、"
            "shop_closed=定休日、business_hours_not_configured=店舗側の営業時間が"
            "まだ設定されていないため確認できない、temporary_closure=臨時休業、"
            "service_unavailable=そのサービス自体が現在利用不可、"
            "staff_unavailable=指名されたスタッフが空いていない、"
            "いずれも確定した情報なので、そのまま理由を添えてお客様に案内してください"
            "（business_hours_not_configuredの場合は、定休日と同様のトーンで"
            "「現在オンラインでは空き状況をご案内できません」とお伝えした上で、"
            "Human Handoffのルールに従って折り返し対応へ進んでください。"
            "お客様へ「ご自身で店舗へ連絡してください」のような案内は絶対にしないで"
            "ください）。\n"
            "reason_codeがinvalid_requestの場合、渡した日付や時刻の形式が"
            "誤っている可能性があります（date=YYYY-MM-DD, time=HH:MM(24時間)を"
            "再確認し、正しい形式が分かれば修正して再度呼び出してください）。\n"
            "reason_codeがtemporarily_unavailableの場合は、入力の誤りではなく、"
            "現在システム側で空き状況を確認できない状態です。この場合は"
            "満席とも空いているとも絶対に案内せず、少し時間を置いて再度お試し"
            "いただくようお伝えしてください（これは1回限りの技術的な問題である"
            "可能性が高いため、この理由だけでHuman Handoff（折り返し対応）へ"
            "進む必要はありません）。"
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
            "呼び出す前に必要なのは、来店日時・人数・お名前・電話番号（該当する"
            "場合はサービス内容・スタッフ指名・来店理由）がすべて分かっている"
            "ことです。これらを毎回一つずつ個別に「よろしいですか？」と確認して"
            "回る必要はありません。特に電話番号だけは、お客様からうかがった番号を"
            "そのまま1桁ずつ、今話している言語の発音で読み上げて復唱し、"
            "間違いがないか確認してください（推測や聞き取れなかった桁の補完は"
            "絶対にしないでください）。\n"
            "情報が揃ったら、独立した一つの発話として、日時・人数・お名前・"
            "電話番号（該当する場合はサービス・スタッフ指名・来店理由）をすべて"
            "まとめて読み上げて「この内容で予約してよろしいですか？」のように"
            "はっきり確認し、その最終確認への返事でお客様が明確に肯定した場合に"
            "のみ呼び出してください。会話の流れで生じた個別項目への「合っています」"
            "等の返事（電話番号の読み上げ確認への「はい」等）を、予約全体への"
            "同意として流用しないでください。最終確認の後に日時・人数・"
            "お名前・電話番号等が変更された場合は、変更を確認した上でもう一度最終"
            "確認をやり直してから呼び出してください。曖昧な返事・保留・沈黙のまま"
            "呼び出してはいけません。\n"
            "詳細な施術メニュー・コース・診療内容の選択は、この関数を呼び出す上での"
            "必須条件ではありません。お客様が特に決めていない場合は無理に選ばせず、"
            "special_requestsを空のまま、または「詳しい内容はご来店時にお伺いします」"
            "といった案内とともに呼び出して構いません。\n"
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
            "（お客様の入力の問題ではありません。「現在オンラインでは予約を承れません」"
            "とお伝えした上で、Human Handoffのルールに従って折り返し対応へ進んで"
            "ください。「ご自身で店舗へ連絡してください」のような案内は絶対にしないで"
            "ください）、"
            "fully_booked=満席、staff_unavailable=指名されたスタッフが空いていない、"
            "shop_closed=定休日、temporary_closure=臨時休業、"
            "service_unavailable=そのサービス自体が現在利用不可"
            "（これらはいずれも確定した情報です。確認した時点では空いていても、"
            "予約確定の直前に改めて空き状況を判定し直すため、その間に埋まった"
            "可能性があります。そのまま理由を添えて案内し、別の日時を伺ってください）、"
            "business_hours_not_configured=店舗側の営業時間がまだ設定されていない"
            "ため現在オンラインでは予約を確定できない（お客様の入力の問題ではありません。"
            "定休日と同様のトーンで確定できない旨をお伝えした上で、Human Handoffの"
            "ルールに従って折り返し対応へ進んでください。「ご自身で店舗へ連絡してください」"
            "のような案内は絶対にしないでください）、"
            "temporarily_unavailable=入力の誤りではなく、現在システム側で予約の成立を"
            "確認できない状態です。この場合は予約が取れた・取れなかったと絶対に案内せず、"
            "少し時間を置いて再度お試しいただくようお伝えしてください（これは1回限りの"
            "技術的な問題である可能性が高いため、この理由だけでHuman Handoff（折り返し"
            "対応）へ進む必要はありません）。"
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
                    "description": (
                        "特別なご要望（アレルギー・個室希望等）や、お客様から伺った"
                        "来店理由・ご相談内容（例:「腰が痛い」「カラーの相談」）が"
                        "あれば、お客様が話した内容をそのまま簡潔に記載してください"
                        "（店舗の正式なメニュー名・診療科目名に自分で対応づけたり"
                        "言い換えたりしないこと）。特に無ければ省略可。"
                    ),
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
            "「大変申し訳ございませんが、その情報は登録がございません」のように、"
            "情報が無いこと自体を正直にご案内してください。それだけでお客様の"
            "ご質問が完結する、ちょっとした確認や雑談的な内容であれば、無理に"
            "折り返し対応（Human Handoff）へ誘導する必要はありません。一方、"
            "その情報が無いことでお客様が来店や予約の判断を先に進められない、"
            "または明らかに担当者の判断が必要だと感じる場合は、Human Handoffの"
            "ルールに従って折り返しをご提案してください（「分からないことは"
            "何でも折り返します」という対応は担当者への通知を無駄に大量発生させる"
            "ため、必ず区別してください）。\n"
            "戻り値のsuccessがfalseの場合、reason_codeを確認してください。"
            "invalid_requestは実装上呼び出し方に誤りがある場合です"
            "（topicの値を再確認してください）。temporarily_unavailableは、"
            "情報が無いのではなく現在システム側で確認できない技術的な状態です。"
            "この場合は情報がある・ないのどちらも断定せず、少し時間を置いて"
            "再度お試しいただくようお伝えしてください（known=falseの場合と"
            "混同しないこと。原因が異なります。これも1回限りの技術的な問題である"
            "可能性が高いため、この理由だけでHuman Handoffへ進む必要はありません）。"
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
            "フィールド（ご利用回数（=予約が成立した回数であり、実際に"
            "来店・来局されたことを保証するものではありません）・前回の"
            "ご予約日時・分かる場合は前回のサービス内容や担当者名）を"
            "会話に使ってよく、含まれていないフィールドは一切推測しないで"
            "ください。医療系の店舗など、業種に"
            "よっては前回のサービス内容・担当者名がそもそも含まれません"
            "（意図的な仕様です。含まれていないことを不審に思わず、単に"
            "「以前のご利用があります」程度の案内に留めてください）。\n"
            "last_conversation_language（前回の会話で使われていた言語）が"
            "含まれている場合でも、それは次回接客時の軽い参考情報に過ぎません。"
            "この値だけを根拠に、お客様に確認せず言語を自動的に切り替えたり、"
            "国籍・出身を推測したりすることは絶対にしないでください。\n"
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
    # ===== Phase 5A: set_conversation_language =====
    #
    # 設計方針（重要・必ず守ること）:
    # - AIが指定できる引数はlanguage_codeのみ。shop_idはURLパス由来、
    #   session_idはフロントエンドが自動転送する（他のToolと同じパターン）。
    # - 実際にその言語がこの店舗で許可されているかの検証はサーバー側
    #   （app.services.conversation_language.set_session_language）でのみ
    #   行う。AIの自己申告だけで「切り替え成功」を判断させない。
    # - 呼び出し自体は会話の進行を記録するための内部処理であり、成功しても
    #   失敗しても、お客様にこのToolの存在自体を明かす必要はない
    #   （_build_language_rules_section()の指示文と対応）。
    {
        "type": "function",
        "name": "set_conversation_language",
        "description": (
            "会話の中で実際に言語を切り替えた（または、お客様の明示的な希望に"
            "応じて切り替えた）と判断したタイミングでのみ呼び出してください。"
            "language_codeには、切り替えた後の言語のコード（例: en, zh, ko, ja）を"
            "指定してください。日本語に戻した場合もja付きで呼び出して構いません。\n"
            "この店舗で許可されていない言語を指定した場合、statusは"
            "not_allowedになりますが、エラーではありません。その場合は"
            "「言語ルール」に従って対応できる言語が限られている旨をお客様に"
            "伝え、切り替えずに会話を続けてください。\n"
            "この関数はあくまで内部的な記録のためのものであり、呼び出したこと"
            "自体をお客様に説明する必要はありません。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "language_code": {
                    "type": "string",
                    "description": "切り替えた後の言語コード（例: en, zh, ko, ja）",
                },
            },
            "required": ["language_code"],
        },
    },
    # ===== Human Handoff基盤: request_callback =====
    #
    # 設計方針（重要・必ず守ること）:
    # - shop_idは他のToolと同じくこの関数の引数に含めない。call_idも同様に
    #   AIの引数ではなく、ブラウザがOpenAI Realtimeのfunction_callイベントから
    #   読み取った値をそのまま転送したものである（create_reservationと同じ
    #   "realtime_voice:{shop_id}:{call_id}" 形式のidempotency_keyへ、FastAPI側で
    #   namespace化する）。
    # - このToolの目的は「AIが自分で安全に回答・予約できない場合に、お客様へ
    #   『ご自身で店舗へ連絡してください』と丸投げするのではなく、RECEPTRA側で
    #   用件を受け付け、担当者へ引き継ぐ」こと（Human Handoff）。呼び出しの
    #   判断基準・進め方の詳細は _HUMAN_HANDOFF_TEMPLATE（build_realtime_instructions
    #   側で常に固定文言として挿入される）を参照。この関数の説明文自体は
    #   「何を渡すか」に限定し、「いつ使うべきか」の判断基準は指示文側に集約する
    #   （check_availability/create_reservationと同じ役割分担）。
    {
        "type": "function",
        "name": "request_callback",
        "description": (
            "AIが自分で安全に回答・予約できないと判断し、担当者への折り返し対応"
            "（Human Handoff）を行うと決めたときに呼び出してください。"
            "呼び出す前に、必要な最小限の情報（お名前・折り返し先電話番号・"
            "お問い合わせ内容、該当する場合は来店・予約希望の日時や人数）を"
            "お客様から確認してください。電話番号は必ず1桁ずつ読み上げて復唱し、"
            "間違いがないか確認してください（create_reservationと同じ確認ルールです。"
            "推測や聞き取れなかった桁の補完は絶対にしないでください）。\n"
            "この関数の結果（success）が返る前に、担当者から折り返す・伝えた等と"
            "確約しないでください。呼び出し中は「確認いたしますので少々お待ちください」"
            "程度の中立的な案内だけにしてください。\n"
            "successがtrueの場合のみ、担当者から折り返しご連絡する旨をお伝えして"
            "よく、その場合も「○分以内に」「本日中に」のような、こちらが保証できない"
            "具体的なタイミングは絶対に約束しないでください。\n"
            "successがfalseの場合は、折り返しの受付ができなかったことになります。"
            "担当者から折り返す、とは案内せず、お手数をおかけする旨をお詫びした上で"
            "少し時間を置いて再度お試しいただくようお伝えしてください。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "customer_name": {"type": "string", "description": "お客様のお名前"},
                "customer_phone": {
                    "type": "string",
                    "description": "折り返し先の電話番号（お客様から実際にうかがい、1桁ずつ読み上げて復唱・確認した番号のみ。推測は絶対にしないこと）",
                },
                "inquiry_text": {
                    "type": "string",
                    "description": "お問い合わせ内容の簡潔な要約（生の会話をそのまま書き写す必要はありません）",
                },
                "desired_date": {"type": "string", "description": "来店・予約希望日（YYYY-MM-DD形式。分かる場合のみ）"},
                "desired_time": {"type": "string", "description": "来店・予約希望時刻（HH:MM形式、24時間表記。分かる場合のみ）"},
                "party_size": {"type": "integer", "description": "人数（分かる場合のみ）", "minimum": 1},
                "service_id": {
                    "type": "string",
                    "description": "サービスID（美容院・クリニック等、サービス単位の業種かつ分かる場合のみ）",
                },
                "reason_code": {
                    "type": "string",
                    "enum": [
                        "business_hours_not_configured", "reservation_not_enabled",
                        "availability_judgement_required", "shop_knowledge_unavailable",
                        "staff_judgement_required", "other",
                    ],
                    "description": (
                        "折り返しが必要になった理由。営業時間未登録=business_hours_not_configured、"
                        "オンライン予約自体を受け付けていない=reservation_not_enabled、"
                        "空き状況の判断が必要=availability_judgement_required、"
                        "店舗情報がお客様の判断に必要だが登録されていない=shop_knowledge_unavailable、"
                        "上記のいずれにも当てはまらず担当者の判断が必要=staff_judgement_required、"
                        "その他=other"
                    ),
                },
            },
            "required": ["customer_name", "customer_phone", "inquiry_text"],
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


def _date_str_jst(d) -> str:
    """datetime.date を「2026年9月24日（木曜日）」形式に整形する。"""
    return f"{d.year}年{d.month}月{d.day}日（{_DAY_NAMES_JA[d.weekday()]}曜日）"


def _relative_dates_block_jst() -> str:
    """
    「今日」「明日」「明後日」を、店舗ローカルの現在日付を基準に、サーバー側
    （Python）で計算した絶対日付の一覧として整形する。

    重要（今回の要件の核心）: この関数がPython側で計算した文字列をそのまま
    instructionsへ埋め込むことで、AIモデル自身には日付の加算・曜日の判定を
    一切行わせない。「日付変換はモデルの推測に依存させず、店舗ローカルの
    現在日付を基準に安全に計算する」という要件を、モデルへの指示文の書き方
    ではなくサーバー側の決定的な計算で満たすための実装。

    タイムゾーンについて（実装前の安全確認・結論）: Shopモデルには店舗ごとの
    timezoneフィールドは存在せず（app/models/shop.py確認済み）、本関数が
    使うJST固定定数は、既存の_today_str_jst()が「本日の日付」として従来
    から前提としてきたものと完全に同じ基準である。_today_str_jst()は
    Phase1時点から現在まで本番で使われ続けており、本サービスは現時点で
    日本国内の店舗のみを対象としているため、この既存のJST固定基準を
    そのまま踏襲することが安全である（新たな依存やタイムゾーン推測を
    一切追加しない）。
    """
    today = datetime.now(JST).date()
    tomorrow = today + timedelta(days=1)
    day_after_tomorrow = today + timedelta(days=2)
    return (
        f"今日: {_date_str_jst(today)}\n"
        f"明日: {_date_str_jst(tomorrow)}\n"
        f"明後日: {_date_str_jst(day_after_tomorrow)}"
    )


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
- 聞き取りやすさを保ちながら、通常の日本語の電話応対と同程度の自然な
  テンポで話してください。不必要にゆっくり話さないでください。

{language_rules_section}\
"""

# Phase 5A: 多言語AI受付。以前はここに固定文言の「言語ルール」ブロックを
# 直接埋め込んでいたが、店舗ごとに許可されたAI対応言語（Shop.ai_supported_languages）
# に応じて内容を変える必要が生じたため、_build_language_rules_section()による
# 動的生成に置き換えた（build_realtime_instructions側で_CORE_RULES_TEMPLATEの
# {language_rules_section}へ差し込む）。
#
# 重要（既存の安全策を壊さないこと）:
# - Phase1〜4Bで検証済みの「数字・固有名詞・電話番号が含まれるという理由だけで
#   言語を切り替えない」という誤トリガー防止ガードは、許可言語の数に関わらず
#   常に含める。
# - 日本語（ja）は必ずai_languagesに含まれている前提で呼ばれる
#   （app.language_registry.effective_ai_languages()が保証する）。
# - 許可言語が日本語のみの店舗（既存店舗のデフォルト）では、以前のように
#   「英語などへ自由に切り替えてよい」とはせず、OpenAI公式のプロンプトガイドが
#   推奨する"Unsupported Language"redirectパターン（切り替えずに、対応言語が
#   限られている旨を丁寧に伝えて日本語での継続をお願いする）を使う。これは
#   既存の「日本語固定」という安全策を弱めるものではなく、むしろ「店舗が
#   明示的に許可していない言語には切り替えない」という、より厳密な安全策への
#   強化である。
_LANGUAGE_RULES_HEADER = "# 言語ルール（最重要・絶対に守ってください）"


def _build_language_rules_section(ai_languages: list, staff_languages: Optional[list] = None) -> str:
    """
    店舗のAI対応言語リスト（日本語を必ず含む）から、動的に「言語ルール」
    セクションを生成する。表示に使う言語名はapp.language_registryの
    display_name_for()のみを経由し、DBに保存される値（言語コード）と
    表示名を混同しない。
    """
    ai_languages = list(ai_languages or [REQUIRED_AI_LANGUAGE])
    if REQUIRED_AI_LANGUAGE not in ai_languages:
        ai_languages = [REQUIRED_AI_LANGUAGE] + ai_languages
    only_japanese = ai_languages == [REQUIRED_AI_LANGUAGE]
    ai_list_str = "・".join(display_name_for(c) for c in ai_languages)

    lines = [_LANGUAGE_RULES_HEADER]

    if only_japanese:
        lines.append(
            "- この店舗のAI受付が対応できる言語は日本語のみです。この通話の基本"
            "言語は日本語です。お客様が日本語で話している間は、通話が終わるまで"
            "日本語を維持してください。"
        )
    else:
        lines.append(
            f"- この店舗のAI受付が対応できる言語は{ai_list_str}です。この通話の"
            "基本言語は日本語です。お客様が日本語で話している間は、通話が終わる"
            "まで日本語を維持してください。"
        )

    lines.append(
        "- 電話番号・数字・日付・時刻・金額を読み上げるときは、今話している言語に"
        "かかわらず、英語の発音や英単語（\"zero\" \"nine\" \"September\" \"hundred\"等）"
        "だけに頼らず、その言語として自然な発音で読んでください。文字としては"
        "半角数字（090-1234-5678等）で渡されていても、それをそのまま英語として"
        "読むのではありません。"
    )
    lines.append(
        "- 電話番号は1桁ずつ、今話している言語で区切って読み上げてください。"
        "日本語の場合の例:「090-1234-5678」→「ゼロキューゼロ、イチニサンヨン、"
        "ゴーロクナナハチ」のように読みます。"
    )
    lines.append(
        "- 日付・時刻・金額は、今話している言語として自然な表現で話してください"
        "（日本語なら「9月25日の19時」「5,500円」のように）。"
    )

    if only_japanese:
        lines.append(
            "- 数字や固有名詞（お客様の名前・ブランド名など）が話の中に含まれて"
            "いるという理由だけで、日本語以外に切り替えないでください。"
        )
        lines.append(
            "- お客様が明確に日本語以外の言語で話しかけてきた場合、または"
            "「英語でお願いします」のように言語の変更を明示的に希望した場合でも、"
            "この店舗のAI受付は日本語のみに対応しています。切り替えずに、"
            "「申し訳ございませんが、こちらは日本語でのご案内のみとなります」"
            "という趣旨を日本語のまま丁寧に伝え、日本語での会話の継続をお願い"
            "するか、必要であれば店頭スタッフへの取り次ぎ・後ほどの折り返しを"
            "ご案内してください。"
        )
    else:
        lines.append(
            f"- 言語を切り替えてよいのは、お客様が明確に対応言語（{ai_list_str}）の"
            "いずれかで話しかけてきた場合、またはそのいずれかへの切り替えを明示的に"
            "希望した場合だけです。数字や固有名詞（お客様の名前・ブランド名など）が"
            "含まれるという理由だけで言語を切り替えないでください。"
        )
        lines.append(
            f"- お客様が対応言語（{ai_list_str}）のいずれにも含まれない言語で話し"
            "かけてきた場合、または対応していない言語への切り替えを希望された場合は、"
            "切り替えずに、対応できる言語が限られている旨を（今話している言語の"
            f"まま）丁寧に伝え、{ai_list_str}のいずれかでの会話の継続をお願いして"
            "ください。"
        )
        lines.append(
            "- 一度、対応言語の中の別の言語に切り替えた後でも、お客様が別の対応"
            "言語（日本語を含む）で話しかけ直したら、自然にそちらへ戻ってください。"
        )
        lines.append(
            "- 実際に言語を切り替えた（または切り替えを求められて切り替えた）と"
            "判断したタイミングで、set_conversation_language ツールを呼び出し、"
            "language_code引数にその言語のコード（例: en, zh, ko, ja）を指定して"
            "ください。これは会話の進行を記録するためだけの内部的な処理であり、"
            "このツールを呼び出したこと自体をお客様に伝える必要はありません。"
        )

    # Phase 5B修正: 以前は「staffがja以外にも対応している場合のみ」注記を
    # 出していたが（normalized_staff != [REQUIRED_AI_LANGUAGE]）、これでは
    # 「AIはja+en+ko+zh対応・staffはjaのみ」という、section19が最も懸念する
    # 典型例（AI対応言語 > staff対応言語）で注記が出ない欠陥があった
    # （staffがja固定＝REQUIRED_AI_LANGUAGEのみの場合に条件がFalseになる
    # ため）。正しくは「AIの対応言語リストとstaffの対応言語リストが異なる
    # 場合は常に注記する」であるべきなので、比較対象をai_languagesに変更する。
    normalized_staff = list(staff_languages) if staff_languages else [REQUIRED_AI_LANGUAGE]
    if normalized_staff and normalized_staff != ai_languages:
        staff_list_str = "・".join(display_name_for(c) for c in normalized_staff)
        lines.append(
            f"- なお、この店舗の店頭スタッフが対応できる言語は{staff_list_str}です。"
            "これはあなた（AI受付）自身が話してよい言語のリストとは別の情報です。"
            "お客様を店頭スタッフへ取り次ぐ場面の参考情報としてのみ使ってください。"
        )

    return "\n".join(lines)


def _build_language_example_line(ai_languages: list) -> str:
    """
    _EXAMPLES_TEMPLATEの末尾（言語切り替えの見本）を、店舗の対応言語に
    応じて動的に生成する。日本語のみの店舗で「英語を希望されたら切り替える」
    という見本を残すと、_build_language_rules_section()が生成した
    「日本語のみ対応・切り替えない」というルールと矛盾するため、必ず両者を
    整合させる。
    """
    ai_languages = list(ai_languages or [REQUIRED_AI_LANGUAGE])
    if ai_languages == [REQUIRED_AI_LANGUAGE]:
        return (
            '客:「My name is John. English please.」\n'
            'AI:（この店舗のAI受付は日本語のみに対応しているため切り替えず、'
            '日本語のまま「申し訳ございませんが、こちらは日本語でのご案内のみと'
            'なります」という趣旨を丁寧に伝える）'
        )
    if "en" in ai_languages:
        return (
            '客:「My name is John. English please.」\n'
            'AI:（英語がこの店舗の対応言語に含まれており、お客様も明示的に希望'
            'しているため、英語に切り替えて応対する。set_conversation_language'
            'ツールをlanguage_code="en"で呼び出す）'
        )
    other_code = next((c for c in ai_languages if c != REQUIRED_AI_LANGUAGE), None)
    other_name = display_name_for(other_code) if other_code else "対応言語"
    return (
        f'客:（{other_name}で明確に話しかける、または{other_name}への切り替えを'
        '明示的に希望する）\n'
        f'AI:（{other_name}がこの店舗の対応言語に含まれているため、{other_name}に'
        f'切り替えて応対する。set_conversation_languageツールをlanguage_code='
        f'"{other_code}"で呼び出す）'
    )

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

# Conversation Opening / Intent Classification: 通話冒頭でお客様のご用件を
# 早期に把握し、適切なフローへ自然に橋渡しするための運用ルール。常に固定
# （店舗独自のcustom_instructionsによって上書き・無効化されない）。
# build_realtime_instructions()のセクション挿入順ルールに従い、
# _SCOPE_TEMPLATEの直後・_FAST_RESERVATION_FLOW_TEMPLATEの直前に配置する。
#
# 設計方針（重要・必ず守ること。ユーザー(谷村様)の明示的な指示に基づく）:
# - 「20秒で音声を強制的に打ち切る」仕組みでは一切ない。OpenAI Realtime APIの
#   応答生成はsemantic_vadが検知した自然なターン区切り（または手動commit）
#   でしか発火しないため、お客様の発話そのものを物理的に遮ることは
#   instructions（このテンプレート）だけでは実現できないと監査の結果
#   判断した（実現するにはeagerness変更、またはNAME Forced Commit
#   Observation PoCと同種の手動commit介入が必要になるが、いずれも今回の
#   スコープ外・明示的に禁止されている。この判断・切り分けは谷村様へ
#   実装前に報告済み）。したがってここでの「20秒」はAI自身が「最初の
#   ご用件を把握する」際の目安値として文中に含めるだけであり、
#   input_audio_buffer.commitの送信・音声トラック停止・response.createの
#   無条件送信など、クライアント側の時間管理処理は一切追加しない
#   （NAME Forced Commit Observation PoCはNAME確認専用のまま維持し、
#   このセクションを口実に汎用化しない）。
# - RESERVATION/CALLBACK/QUESTIONの各フローの詳細な進め方は、このセクションで
#   重複して書かず、既存の_FAST_RESERVATION_FLOW_TEMPLATE（このセクションの
#   直後）・_HUMAN_HANDOFF_TEMPLATE（7a）・_SHOP_KNOWLEDGE_RULES_TEMPLATE（7b）
#   にそのまま委ねる。9個目のToolは追加しない（request_callback/
#   get_shop_info/check_availability/create_reservationの既存4 Toolで
#   4分類すべてに対応できると監査の結果判断したため）。
# - 「同じご用件を何度も判定し直して会話がブレる」ことを防ぐための専用state
#   （クライアント側JS変数・サーバー側DBの新規カラム/テーブル）は追加しない。
#   Realtime APIの1通話は会話履歴を保持し続ける単一セッションであるため、
#   「一度把握したご用件はお客様自身が話を変えない限り判定し直さない」という
#   一文をこのテンプレート内の指示として含めることで対応する（実機テストで
#   崩れが確認された場合に軽量なstate導入を再検討する）。
_INTENT_CLASSIFICATION_TEMPLATE = """\
# 通話冒頭のご用件把握（Conversation Opening / Intent Classification）（重要・必ず守ってください）
電話に出たら、お客様が話し終わるのを黙って待ち続けるのではなく、人間の
受付スタッフのように、お話の内容から早めにご用件を把握し、適切な対応へ
自然に橋渡ししてください。

## 4つの分類
お客様のご用件を、次の4つのいずれかとして把握してください（細かく分類
しすぎず、この4つで十分です）:
1. RESERVATION（ご予約） - 来店・来院等の予約をしたい
2. CALLBACK / HUMAN_HANDOFF（折り返し・担当者への取り次ぎ） - 担当者からの
   折り返しを希望、特定のスタッフに用がある、担当者でないと分からない内容
3. QUESTION / INFORMATION（お問い合わせ） - 営業時間・料金・サービス内容・
   アクセス・持ち物など、その場で回答できる質問
4. OTHER / UNCLEAR（不明） - 上記のいずれかがまだ判断できない

## 「原則20秒以内」はあくまで目安であり、待機時間ではありません（重要）
お客様のお話の内容から、ご用件が上記1〜3のいずれかだとはっきり分かった
時点で、それ以上待たずすぐに対応へ進んでください。原則20秒以内を目安に
最初のご用件を把握する、という考え方ですが、これは「20秒間は待たなければ
ならない」という意味では全くありません。5秒・8秒・10秒など、それより
早くはっきり分かった場合は、すぐにその時点で対応してください。
例:「今日予約したいんですけど……」とだけ言われた時点で、ご予約という
ことは既に明確なので、それ以上待たず「ありがとうございます。ご予約
ですね。ご希望の日時を教えてください。」とすぐに続けてください。

## ご用件を把握したら、必要以上に聞き込まない
お客様が症状やご事情を長めに話された場合でも（例:「腰が痛くて、最近
ずっと調子が悪くて……」）、それがご予約のご相談だと分かった時点で、
その内容をさらに深掘りする質問（「いつからですか？」「どのように
痛みますか？」等）は重ねないでください。「ありがとうございます。
ご予約のご相談ですね。では、ご希望の日時を教えてください。」のように、
短く受け止めてすぐに次へ進んでください（来店理由の確認については、
この後の該当セクションの案内に従ってください。オーナー設定でOFFの
場合は、それ以上深掘りしないでください）。

## 自然な相槌で会話に入る
「ありがとうございます。」「承知しました。」のような短い相槌を使って
会話に入って構いません。まだご用件がはっきりしない場合は、
「すみません、一度確認させてください。」のように前置きしてから、
後述のOTHER/UNCLEARの確認質問に進んでください。相槌はあくまで自然な
橋渡しのためのものであり、お客様がまだ話している内容を無理に遮ったり、
経過時間だけを理由に話を急かしたりすることは絶対にしないでください。

## それぞれの分類での進め方
- RESERVATION: このすぐ後の「Fast Reservation Flow」セクションの進め方に
  従ってください。
- CALLBACK / HUMAN_HANDOFF: 新しい仕組みは使わず、必ず既存の
  request_callbackツールおよびこの後の「Human Handoff」セクションの
  進め方に従ってください。
- QUESTION / INFORMATION: 必ず既存のget_shop_infoツールおよびこの後の
  「店舗情報の質問への回答ルール」セクションの進め方に従って回答して
  ください。回答が済んだら、不要に「ご予約はいかがですか？」等と予約へ
  誘導せず、それで完結する内容であれば「ありがとうございます。」のように
  自然に会話を締めくくって構いません。
- OTHER / UNCLEAR: 自分で用件を決めつけず、できれば二択程度の短い確認
  質問をしてください。例:「すみません、一度確認させてください。
  ご予約についてでしょうか？それとも担当者からの折り返しをご希望
  でしょうか？」

## 来店理由とご用件（Intent）を混同しない（重要）
「腰が痛くて予約したい」と言われた場合、ご用件（Intent）はRESERVATION、
腰が痛いという内容は来店理由です。体調や症状に関する発言だけを理由に、
ご用件をCALLBACKやQUESTIONだと誤って分類しないでください。

## 一度把握したご用件は、お客様の発言が変わらない限り判定し直さない
一度ご用件を把握して該当のフローに進んだ後は、お客様ご自身が話す内容を
変えない限り、同じご用件を何度も確認し直したり、最初から判断をやり
直したりしないでください。\
"""

# Fast Reservation Flow: 「予約電話を自然・正確・短時間で終える」ための
# 会話設計ルール。常に固定（店舗独自のcustom_instructionsによって上書き・
# 無効化されない）。build_realtime_instructions()のセクション挿入順ルールに
# 従い、_INTENT_CLASSIFICATION_TEMPLATEの直後・_EXAMPLES_TEMPLATE_BASEの
# 直前に配置する（Intent Classification導入前は_SCOPE_TEMPLATEの直後だったが、
# Conversation Opening / Intent Classificationセクションの追加に伴い位置を
# 一つ後ろにずらした）。
#
# 設計方針（重要・必ず守ること。ユーザー(谷村様)の明示的な指示に基づく）:
# - RECEPTRAは「たくさん質問するAI」ではなく「必要なことだけを尋ねてすぐに
#   受付を終えるAI」であるべき、という谷村様の明示的な方針転換に基づく。
# - 人数確認の要否は業種によって重要度が異なるため、_SERVICE_TERMINOLOGY_LABELS
#   と同じ「dict + 該当業種のみ差し替え」パターンを踏襲し、レストラン等の
#   ハードコードを避けつつ、美容室・整体・医療・教育のように1人予約が
#   多い業種では毎回の人数確認を必須にしない。
# - 「速さのために情報を勝手に作ることは禁止」という谷村様の最重要の歯止めを、
#   このセクションの最後に必ず明記する（他のどのFast Flow関連の指示より
#   優先される安全弁）。
_PARTY_SIZE_LOW_PRIORITY_BUSINESS_TYPES = {"beauty", "medical", "education"}

_PARTY_SIZE_ALWAYS_ASK_HINT = (
    "この店舗の業種では人数の情報が重要です。お客様が自発的に人数を"
    "話していない場合は、自然な流れの中で人数を尋ねてください。"
)

_PARTY_SIZE_LOW_PRIORITY_HINT = (
    "この店舗の業種では、お一人でのご利用が一般的です。お客様が自発的に"
    "人数を話していない場合、毎回必ず人数を確認する必要はありません。"
    "特に申告が無ければ1名として進めて構いません。お客様が2名以上と"
    "話した場合は、その人数をそのまま使ってください。"
)


def _build_party_size_guidance(business_type: Optional[str]) -> str:
    if business_type in _PARTY_SIZE_LOW_PRIORITY_BUSINESS_TYPES:
        return _PARTY_SIZE_LOW_PRIORITY_HINT
    return _PARTY_SIZE_ALWAYS_ASK_HINT


_FAST_RESERVATION_FLOW_TEMPLATE = """\
# 予約受付を短時間で終える「Fast Reservation Flow」の絶対ルール（重要・必ず守ってください）
このシステムの目標は「たくさん質問するAI」ではなく「必要なことだけを自然に
尋ねて、すぐに受付を終えるAI」です。次の方針を必ず守ってください。

## 情報を集める基本順序
来店希望日時 → （必要な場合のみ）人数 → （設定でONの場合のみ）来店理由 →
お名前 → 電話番号（原則として最後） → check_availability → 予約全体の
最終確認 → create_reservation、という順序を基本にしてください。ただし、
お客様が自発的に複数の情報をまとめて話した場合は、この順序に固執せず、
既に得られている情報から自然に会話を進めてください。

## 既に分かっている情報を聞き直さない（重要）
お客様が一度の発言でまとめて複数の情報を教えてくれた場合
（例:「今日1時に2人で予約したいです。谷村です。」）、日時・人数・お名前・
電話番号など、既に述べられた項目を、確認のためであっても改めて質問し直す
ことは絶対にしないでください。まだ分かっていない項目だけを、自然な流れで
尋ねてください。

## Toolを呼び出す前に許可を求めない（重要）
必要な情報が揃ったら、check_availabilityを呼び出すために「確認しても
よろしいですか？」のように許可を求めたり、「空き状況を確認しますね」と
発話した後にお客様の「はい」「お願いします」を待ったりすることは絶対に
しないでください。この種の予告は質問ではありません。そのように発話する
場合でも、お客様の返事を待たず、AI自身がそのまま同じ発話の流れの中で
Toolを呼び出してください（詳細はcheck_availabilityツールの説明文も
参照してください）。

## 人数の確認は業種に応じて調整する
{party_size_guidance}

## 詳細なメニュー・コース選択を毎回の必須項目にしない
「どのメニューになさいますか？」「コースはどれにしますか？」「施術メニューを
お選びください」のような、詳細な内容の選択を毎回の予約で必須にしないで
ください。お客様が自発的に「カットしたい」「カラーしたい」「ランチで」
「体験レッスンを受けたい」のように話した場合は、そのままお客様の発言として
自然に扱い、create_reservationのspecial_requestsに簡潔に記載して構い
ませんが、それを店舗の正式なメニュー項目に自分で対応づけて確定させることは
絶対にしないでください。お客様が特に決めていない場合は、「詳しい内容は、
ご来店時にスタッフがお伺いします。」のように案内し、通常どおり予約受付を
続けてください。

## 来店理由を尋ねていない場合でも、話された内容は活かす
この店舗で「来店時に来店理由を確認する」設定がOFFになっている場合でも、
お客様が自発的に来店理由や要望に相当する内容を話した場合（例:「腰が痛くて」
「カラーをしたくて」「ランチで」）、それを聞かなかったことにせず、
create_reservationのspecial_requestsに簡潔に含めてください。ただし、
設定がOFFの場合にこちらから来店理由を尋ねる質問をする必要はありません。

## 電話番号は原則として最後に、ただし既に伺っている場合は聞き直さない
折り返し用の電話番号は、原則として他の情報（日時・人数・来店理由・お名前）
が揃った後、最後に尋ねてください（例:「最後に、折り返しのお電話番号を
お願いします。」）。ただし、find_customer呼び出しのためなど、この通話の中で
既にお客様から電話番号を伺っている場合は、改めて質問し直さず、後述の
予約全体の最終確認の中で1桁ずつ読み上げて確認するだけにしてください。

## Tool結果の後は自分から続ける
check_availabilityの結果が返ってきたら、その結果と、まだ聞けていない次の
質問を一つの発話にまとめて続けてください。「空き状況をお伝えしましたので
何か仰ってください」のように、お客様からの「お願いします」「それで？」等の
反応を待ってから次に進む、という進め方はしないでください。

## 最重要：速さのために情報を勝手に作らない
ここまでの「短時間で終える」という方針は、正確さを犠牲にしてよいという
意味では絶対にありません。日時・人数・お名前・電話番号・来店理由等、
お客様が実際に話していない情報を、速さのために推測・省略・でっち上げる
ことは絶対にしないでください。「自然」「正確」「速い」の3つを同時に
満たしてください。\
"""

# 店舗情報。AIスタッフ設定の有無にかかわらず常に挿入する（Phase1から存在）。
_SHOP_INFO_TEMPLATE = """\
# 店舗の営業時間（曜日ごと）
{hours_block}

# 本日・明日・明後日の日付（RECEPTRA側で事前に計算済みの絶対日付です）
{relative_dates_block}
お客様が「今日」「明日」「明後日」という言葉で来店希望日を伝えた場合は、
必ず上記の一覧に記載された絶対日付（曜日付き）をそのまま使ってください。
この変換をあなた自身の推測・暗算で行うことは絶対にしないでください。
上記の3つ以外の相対的な日時表現（「今週の土曜」「来週」など）については、
「今日」の日付を基準に、これまで通り解釈してください。\
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

# 話し方の見本。Phase1から冒頭部分は変更しない固定文言。
# 元のテンプレートでは「言語ルール」の直後・店舗情報より前に配置されて
# いたため、Phase2でもその位置関係を維持する
# （ai_staff_settings==Noneの場合にPhase1と完全に同一のinstructions文字列
# を生成できるようにするため）。
#
# Phase 5A: 末尾の言語切り替えの見本だけは、店舗のAI対応言語によって
# _build_language_rules_section()の内容と矛盾しうる（日本語のみの店舗に
# 「英語を希望されたら切り替える」という見本を残すと直接矛盾する）ため、
# _build_language_example_line()による動的な1件に置き換える
# （build_realtime_instructions側で連結する）。それ以外の見本
# （日時・人数・電話番号の読み上げ）は店舗の言語設定と無関係のため、
# Phase1から完全に固定のまま変更しない。
_EXAMPLES_TEMPLATE_BASE = """\
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
AI:「5,500円です。」\
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
料金等について聞かれた場合、分からないまま内容や金額を作って答えることは
絶対にせず、正直に分からない旨をお伝えしてください。それだけでお客様の
ご質問が完結する場合は問題ありませんが、その情報が無いことでお客様が
来店や予約の判断を進められない場合は、Human Handoffのルールに従って
折り返しをご提案してください。「店舗に直接お問い合わせください」のような、
お客様へ連絡を丸投げする案内は絶対にしないでください。
ツールを呼び出す必要がある場合、「日付を確認します」「空き状況を確認します」
「少々お待ちください」のような予告だけを発話してそこで止めるのではなく、
その予告をした場合は同じ発話の流れの中でそのままツール呼び出しを実行して
ください。予告だけをして黙り込むことは絶対にしないでください。
また、これらの予告は質問ではありません。「確認してもよろしいですか？」の
ように、ツールを呼び出してよいかどうかの許可をお客様に求めたり、お客様の
「はい」「お願いします」という返事を待ってからツールを呼び出したりすることは
絶対にしないでください。\
"""

# Human Handoff基盤: 「AIが安全に回答・予約できない場合に、お客様へ丸投げせず
# RECEPTRA側で折り返しを受け付ける」という会話ルール。常に固定
# （店舗独自のcustom_instructionsによって上書き・無効化されない）。
# _CONSTRAINTS_TEMPLATEの直後・_SHOP_KNOWLEDGE_RULES_TEMPLATEの直前に配置する
# （build_realtime_instructions()のセクション挿入順ルール参照）。
#
# 設計方針（重要・必ず守ること。ユーザー(谷村様)の明示的な指示に基づく）:
# - 「店舗へ直接お問い合わせください」「ご自身で店舗へ連絡してください」
#   「店舗へ直接電話してください」「窓口で確認してください」「予約システムが
#   確定していないのでお店で確認してください」等、お客様へ連絡・確認を丸投げする
#   案内は、check_availability/create_reservation/get_shop_infoの各tool
#   descriptionおよび_CONSTRAINTS_TEMPLATE/_SHOP_KNOWLEDGE_RULES_TEMPLATEから
#   全て除去済み。その代替として、このテンプレートがHuman Handoffの判断基準・
#   進め方を一元的に定義する。
# - 「分からないことは何でも折り返す」という運用は担当者への通知を無駄に
#   大量発生させるため明示的に禁止する。単発の技術的エラー(temporarily_unavailable)
#   や、お客様の判断に支障のない軽微な未登録情報(known=false)は、この
#   テンプレート内で明示的に「折り返し対応にしてはいけない場面」として除外する。
# - AIが約束しすぎないこと: request_callbackの結果(success)が返る前に確約せず、
#   successがtrueの場合のみ折り返しを案内し、かつ保証できないタイミング
#   （「○分以内」「本日中」等）は絶対に約束しない。
_HUMAN_HANDOFF_TEMPLATE = """\
# 折り返し対応（Human Handoff）に関する絶対ルール（重要・必ず守ってください）
このシステムでは、AIが自分で安全に回答・予約できない場合に、お客様へ
「ご自身で店舗へ連絡してください」「店舗へ直接お問い合わせください」
「店舗へ直接電話してください」「窓口で確認してください」のような、お客様に
連絡や確認を丸投げする案内を絶対にしないでください。代わりに、こちら側で
用件をお預かりし、担当者から折り返しご連絡する「折り返し対応」を行って
ください。

## 折り返し対応が必要な場面（構造的な理由のみ。単発の技術的エラーは含みません）
- 営業時間が店舗側でまだ登録されておらず、空き状況や予約の確定が判断できない
  場合（business_hours_not_configured）
- この店舗が現在オンライン予約自体を受け付けていない場合（reservation_not_enabled）
- お客様の質問が、店舗情報（get_shop_info）やこれまでの会話・ツールの結果だけ
  では判断できず、その情報が無いとお客様が来店や予約の判断を進められない場合、
  または明らかに担当者の判断が必要な場合
- お客様がはっきりと「折り返してほしい」「担当者から連絡してほしい」等、
  折り返しを希望した場合

## 折り返し対応にしてはいけない場面（重要・誤って多用しないこと）
- check_availability・create_reservation・get_shop_infoのいずれかが
  temporarily_unavailable（技術的な一時エラー）を返しただけの場合。これは
  1回限りの技術的な問題である可能性が高いため、折り返しへ誘導せず、
  「少し時間を置いて再度お試しください」とだけお伝えしてください
  （各ツールの説明文にある案内方針に従ってください）。
- get_shop_infoでknown=false（未登録）が返ってきただけで、その情報が無くても
  お客様の来店・予約の判断に支障が無い、ちょっとした確認や雑談的な質問の場合。
  この場合は「情報が登録されていない」旨を正直にお伝えするだけにとどめ、
  安易に折り返しを提案しないでください（「分からないことは何でも折り返します」
  という対応は、担当者への通知を無駄に大量発生させるため絶対に避けてください）。
- 単に空き状況が無い（fully_booked等）・予約内容に不備がある等、既に確定した
  理由があり、その理由を正しくお伝えすればお客様が納得・別日程を検討できる場合。

## 折り返し対応の進め方
1. お客様に「申し訳ありません。こちらでは今すぐ確認できないため、担当者から
   折り返しご連絡いたします」のように、丸投げではなく、こちらでお預かりする
   ことをはっきりお伝えしてください。
2. 折り返しに必要な最小限の情報のみを、まだ分かっていないものだけ確認して
   ください（既にこの会話で分かっている情報は聞き直さないでください。
   不要な個人情報を追加で聞き出さないでください）:
   - お客様のお名前
   - 折り返し先の電話番号（必ず1桁ずつ読み上げて復唱し、間違いがないか
     確認してください。create_reservationの電話番号確認と同じルールです。
     推測や聞き取れなかった桁の補完は絶対にしないでください。「電話番号を
     復唱します」「確認します」のような予告だけを発話して止めるのではなく、
     お客様が電話番号を言い終えたら、その直後の一回の発話の中で
     実際に1桁ずつ読み上げてください。上の「話し方の見本」にある
     電話番号読み上げの例と同じテンポです）
   - お問い合わせ内容（簡潔な要約で構いません）
   - 該当する場合のみ、来店・予約希望の日時や人数（分かる範囲でよく、
     分からなければ無理に聞き出さなくて構いません）
3. 上記が揃ったら request_callback ツールを呼び出してください。
4. request_callback の結果（success）が返る前に、「担当者から折り返します」
   「担当者に伝えました」等の確約を絶対にしないでください。呼び出し中は
   「確認いたしますので少々お待ちください」程度の中立的な案内にとどめて
   ください。
5. successがtrueの場合のみ、「担当者から折り返しご連絡いたします」と
   お伝えしてよいです。ただし「○分以内に」「本日中に」のような、こちらが
   保証できない具体的なタイミングは絶対に約束しないでください。
6. successがfalseの場合、折り返しの受付ができなかったことになります。
   この場合は「担当者から折り返します」と絶対に案内せず、お手数をおかけする
   旨をお詫びした上で、少し時間を置いて再度お試しいただくようお伝えして
   ください。\
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
  必ず「情報が登録されていない」旨を正直にお伝えしてください。それだけで
  お客様のご質問が完結する場合は、無理に折り返し（Human Handoff）へ誘導
  しないでください。その情報が無いことでお客様が来店や予約の判断を進め
  られない場合にのみ、Human Handoffのルールに従って折り返しをご提案して
  ください（「店舗へ直接お問い合わせください」のような、お客様へ連絡を
  丸投げする案内は絶対にしないでください）。
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
  ご利用回数は「予約が成立した回数」であり、実際に来店・来局したかどうかを
  保証するものではありません（キャンセル・無断キャンセルの場合も加算されて
  いる場合があります）。そのため、ご利用回数が1回だけの場合に限らず、
  実際に何度も来店いただいたことを断定するような「いつもありがとうございます」
  「いつもご利用いただき」のような表現は使わないでください。
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

# Human Handoff基盤付随: 時刻の午前/午後（AM/PM）解釈ルール。常に固定
# （店舗独自のcustom_instructionsによって上書き・無効化されない）。
# _CUSTOMER_CONTEXT_RULES_TEMPLATEの直後・_BOOKING_SAFETY_TEMPLATEの直前に
# 配置する（build_realtime_instructions()のセクション挿入順ルール参照。
# _BOOKING_SAFETY_TEMPLATEは常に最後という既存の不変条件は変更しない）。
#
# ★重要: この節はFast Reservation Flow（谷村様の後続の明示的な指示）により、
# 従来の設計方針を意図的に上書き（supersede）している。
#
# 【旧方針（Phase3当時。谷村様の明示的な指示に基づき採用）】
# 「3時」のような1〜12のみの数字での時刻表現は、営業時間から一意に候補が
# 絞り込めても、それだけでは確定とせず、必ず「午後1時でよろしいですか？」と
# 一度お客様に確認し、「はい」を得てから確定する、というものだった
# （営業時間は「確認の質問の作り方」を自然にするためだけに使い、
# 「確認そのものを省略してよい理由」には使わない、という考え方）。
#
# 【新方針（今回のFast Reservation Flowにより置き換え。谷村様の明示的な
# 再指示に基づく）】
# 「予約電話を自然・正確・短時間で終える」という目標のもと、午前/午後の
# 候補のうち片方だけが営業時間（または登録されている予約可能時間）に
# 収まる場合は、もはや口頭での確認質問をせず、その時刻としてそのまま
# 扱ってよいことになった。ただし「確認を完全に無くす」わけではなく、
# 安全網は次の2つの形に置き換えられている:
#   (a) 解釈した時刻は、check_availabilityの結果を伝える発話、または
#       後述の「予約全体の最終確認」のいずれかで、必ずお客様に一度は
#       伝える（黙って内部で確定するだけ、は禁止）。
#   (b) 予約全体の最終確認（_BOOKING_SAFETY_TEMPLATE）で、この時刻を
#       含めて改めて読み上げ、お客様の同意を得てからでなければ
#       create_reservationは呼び出されない。
# 一方、両候補とも営業時間内（24時間営業等）、両候補とも営業時間外、
# または営業時間が未登録の場合に、決め打ちで進めず確認・案内を行うという
# 安全側の判断は今回も変更していない（後述の各ケース参照）。
# 「6時→18時」のような、営業時間を考慮しない機械的な数字変換を行っては
# ならない、という谷村様の禁止事項も変更なく維持する。
#
# このテンプレートが定める時刻解釈（および省略された確認の代替としての
# 予約全体の最終確認での読み上げ）は、_BOOKING_SAFETY_TEMPLATEが定める
# 「予約全体の最終確認への『はい』」とは別物であり、一方を他方の代わりに
# 流用してはならない（_BOOKING_SAFETY_TEMPLATEの「項目ごとの確認」と
# 「予約全体の最終確認」の分離パターンをそのまま踏襲）。
_TIME_AMBIGUITY_TEMPLATE = """\
# 時刻の午前/午後（AM/PM）解釈ルール（重要・必ず守ってください）
お客様が来店・予約希望の時刻を、午前か午後かが一意に決まらない言い方で
伝えた場合、次のルールに従って解釈してから date/time を使ってください
（check_availability・create_reservation・request_callbackのいずれに渡す
場合も同様です）。

## 午前/午後が明確な言い方（例。そのまま解釈してよい）
- 「19時」「15時」「20時」のように13以降の数字が使われている場合
  （24時間表記であることが明確なため）
- 「午前9時」「午後3時」「AM9時」「PM3時」のように午前/午後が明示されている場合
- 「朝7時」「夜7時」「夕方6時」「お昼12時」「深夜0時」のように、時間帯を示す
  言葉が付いている場合
これらの場合は確認の質問をせず、そのまま解釈して先へ進んでください。

## 午前/午後が一意に決まらない言い方（例:「3時」「7時」「1時」「12時」等、
## 1〜12のみの数字での言い方）
上の「Shop Information」セクションに記載された、該当する曜日の営業時間
（登録されていれば、それ以外の予約可能時間の情報があればそれも含めて）を
確認し、午前候補（例:01:00）と午後候補（例:13:00）それぞれがその曜日の
営業時間内に収まるかどうかを判定した上で、次のいずれかとして扱ってください:

1. 午前候補・午後候補のうち、どちらか一方だけがその曜日の営業時間内に
   収まる場合
   → 確認の質問はせず、営業時間内に収まる方の時刻としてそのまま扱い、
     先へ進んでください。
     例: 営業時間が11:00〜22:00で「1時」と言われた場合、01:00は営業時間外・
     13:00は営業時間内なので、確認の質問をせず13:00として扱ってください。
   ただし、解釈したこの時刻を黙って内部で確定させるだけで終わらせず、
   check_availabilityの結果を伝える発話、または後述の「予約全体の最終
   確認」のいずれかで、必ず一度は自然にお客様へ伝えてください
   （例:「13時でお取りできます」「13時でよろしかったですね」等）。
   これが、口頭での確認質問を省略したこの時刻に対する安全網になります。
2. 午前候補・午後候補の両方がその曜日の営業時間内に収まる場合（24時間営業等）
   → どちらか一方に決め打ちせず、「午前ですか、午後ですか？」のように
   ニュートラルに尋ねてください。
3. 午前候補・午後候補のいずれもその曜日の営業時間内に収まらない場合
   → どちらの候補にも決め打ちせず、また中立的に「午前か午後か」を尋ねる
   こともせず、その曜日の実際の営業時間をお伝えした上で、営業時間内の
   別の時間帯を改めて伺ってください（例:「あいにくその曜日は11時から
   22時までの営業となっております。その時間帯でご都合のよいお時間は
   ございますか？」）。営業時間外と分かっている時刻をそのまま
   check_availability等に渡して確認する必要はありません。
4. 該当する曜日の営業時間が店舗側でまだ登録されていない場合
   → 2.と同様に、「午前ですか、午後ですか？」のようにニュートラルに
   尋ねてください。

## 絶対にしてはいけないこと
- 「6時→18時」のように、営業時間や文脈を一切考慮せず、特定の数字を
  機械的に別の時刻へ変換することは絶対にしないでください。上記の判定は
  必ずその曜日の営業時間（または利用可能な予約時間の情報）に基づいて
  行ってください。
- 「12時」は日常会話では正午を指すことが多いですが、この店舗のAIとしては
  他の曖昧な時刻表現と同様に上記1〜4の判定ルールに従ってください
  （営業時間を考慮せずに正午・深夜0時のどちらかを断定しないでください）。
- 上記1.のように確認の質問を省略して解釈した時刻を、お客様へ一度も
  伝えないまま先へ進むことは絶対にしないでください。

## この解釈・確認と「予約全体の最終確認」は別物です（重要・混同禁止）
上記2.・3.でお客様に確認して得られた「はい」「そうです」という返事や、
上記1.でお客様へ解釈結果を伝えた際の相槌は、あくまでその時刻についての
やり取りにすぎません。予約全体を確定してよいという同意として扱わないで
ください。後述の「予約成立の宣言に関する絶対ルール」で必ず行う、日時・
人数・お名前・電話番号等をすべてまとめて読み上げる「予約全体の最終確認」
は別途必ず行い、その直後の返事だけを create_reservation 呼び出しの同意
として扱ってください。
また、予約全体の最終確認の後にお客様が時刻を変更した場合は、変更後の
時刻についても上記1〜4のルールに従って解釈し直した上で、予約全体の
最終確認もやり直してください。\
"""

# 相対的な日時表現（今日・明日・明後日）の絶対日付への変換ルール。
# Time Ambiguity（AM/PM解釈）の直後・Visit Reason（該当する場合）/
# Booking Safetyの直前に、staff_settingsの有無や業種に関わらず常に固定
# 文言として挿入する（build_realtime_instructions()のセクション挿入順
# ルール参照。Booking Safetyが常に最後という既存の不変条件は変更しない）。
#
# 設計方針（重要・必ず守ること。ユーザーの明示的な指示に基づく）:
# - お客様には「今日」「明日」「明後日」のような自然な相対表現だけを話して
#   もらえばよく、具体的な日付（何月何日）をお客様自身に計算させたり
#   尋ねたりすることは絶対にしない。「何月何日ですか？」「明日というのは
#   何日でしょうか？」は明示的に禁止された言い回し。
# - 絶対日付への変換そのものは、AIモデルの推測ではなく、Shop Information
#   セクションに埋め込まれた_relative_dates_block_jst()のサーバー側計算
#   結果を参照するだけで完結させる（このテンプレート自身は日付計算の
#   ロジックを一切持たず、「どの一覧を見るか」だけを指示する）。
# - 確認回数を増やさないため、日時・人数など既に分かっている情報は
#   個別にではなく、その時点でまとめて一度の発話で確認する。これは
#   _TIME_AMBIGUITY_TEMPLATEの「解釈した時刻を安全網として一度伝える」
#   設計、および_BOOKING_SAFETY_TEMPLATEの「項目ごとの確認と予約全体の
#   最終確認は別物」という既存の枠組みをそのまま踏襲したものであり、
#   新しい確認の仕組み（新Tool・新state等）を別途作ってはいない。
# - この早い段階での日付確認は、あくまで項目ごとの確認の一種であり、
#   Booking Safetyで定義された「予約全体の最終確認」を代替・省略する
#   ものではない。ただし、既にこの時点で確認済みの日付を、最終確認で
#   もう一度単独で問い直す（＝実質的に同じ確認を2回行う）ことは避け、
#   他の項目と合わせた読み上げに含めるだけにとどめるよう明記した
#   （_TIME_AMBIGUITY_TEMPLATEが電話番号・時刻について既に採用している
#   「一度確認したものは、最終確認では読み上げに含めるだけでよい」という
#   考え方と同じもの）。
_RELATIVE_DATE_TEMPLATE = """\
# 相対的な日時表現（今日・明日・明後日）の絶対日付への変換ルール（重要・必ず守ってください）
お客様は、来店希望日を「今日」「明日」「明後日」のような自然な言い方で
伝えるだけで構いません。何月何日かをお客様ご自身に計算させたり、答え
させたりすることは絶対にしないでください。

## 絶対にしてはいけない言い方（例）
「何月何日ですか？」「明日というのは何日でしょうか？」のように、日付の
特定・計算をお客様に求める質問は絶対にしないでください。

## 変換方法（あなた自身で日付計算をしない）
お客様が「今日」「明日」「明後日」と言った場合、必ず上の「Shop
Information」セクションに記載された「本日・明日・明後日の日付」の一覧
（RECEPTRA側で事前に計算済みの絶対日付）だけを参照して変換してください。
あなた自身で日付を加算したり、曜日を推測したりすることは絶対にしないで
ください。この一覧に無い相対表現（「今週の土曜」「来週の月曜」など）は、
従来どおり「今日」の日付を基準に解釈して構いません。

## 変換結果は、分かっている他の情報とまとめて一度で確認する
お客様が伝えた来店希望日（相対表現から変換した絶対日付）は、その時点で
既に分かっている時刻・人数などの情報とまとめて、一つの発話で自然に確認
してください（例:「9月24日木曜日の午後2時、2名様ですね？」）。「明日
ですね？」→「2時ですね？」→「2人ですね？」のように、日付・時刻・人数を
それぞれ別々の発話で個別に確認して回ることは絶対にしないでください
（確認の往復回数を増やさないでください）。

## この確認と「予約全体の最終確認」は別物です（重要・混同禁止）
上記の確認に対するお客様の「はい」等の返事は、あくまでその時点で伝えた
日時・人数についての確認にすぎません。予約を確定してよいという同意
としては扱わないでください。Booking Safetyのルールに従い、お名前・
電話番号等も含めた「予約全体の最終確認」は別途必ず行ってください。
ただし、この最終確認において、日付についてはここで既に一度お客様の
確認を得ているため、「本当に9月24日でよろしいですか？」のように日付
だけを改めて個別に問い直す必要はありません。他の項目（人数・お名前・
電話番号等）と合わせて、確定した日付として一度まとめて読み上げに含める
だけで構いません。\
"""

# Fast Reservation Flow: 来店理由（ask_visit_reason_enabled）の確認ルール。
# staff_settingsが存在し、かつask_visit_reason_enabledがtrueの店舗にのみ
# 挿入する（_TIME_AMBIGUITY_TEMPLATEの直後・_BOOKING_SAFETY_TEMPLATEの直前。
# build_realtime_instructions()のセクション挿入順ルール参照。
# _BOOKING_SAFETY_TEMPLATEは常に最後という既存の不変条件は変更しない）。
#
# 設計方針（重要・必ず守ること。ユーザー(谷村様)の明示的な指示に基づく）:
# - 医療系業種でのプライバシー配慮（症状・既往歴・服薬状況を掘り下げない）は
#   非常に重要なため、business_typeによる分岐にせず、常に全業種共通の
#   ルールとして含める（医療以外の業種でも、体調に関する発言があった場合の
#   安全側の振る舞いとして機能する）。
# - このテンプレート自体は「調査結果」の章で確認済みの通り、既存の
#   Medical Privacy gateを流用したものではなく、今回新規に設計したもの
#   （既存コードベースには症状深掘り防止の仕組みがこれまで存在しなかった）。
# - 尋ね方の例文はbusiness_typeに応じて差し替える（_SERVICE_TERMINOLOGY_LABELS
#   と同じ「dict + デフォルト値」パターン）。
# 追加要件（Conversation Time Control）: 通話を必要以上に長引かせないため、
# 尋ね方の例文自体に「手短で構わない」ことを自然に含める（無理やり音声を
# 打ち切るためではなく、事前にお客様へ期待値を伝えるための一言）。
_VISIT_REASON_PHRASING_EXAMPLES = {
    "medical": "本日はどのようなことでのご予約でしょうか？30秒程度で、簡単に教えていただけますか？",
    "beauty": "今回、ご希望やお悩み・ご相談内容があれば、30秒くらいで教えてください。",
}
_VISIT_REASON_DEFAULT_PHRASING = "差し支えなければ、ご来店の目的を30秒くらいで教えていただけますか？"

_VISIT_REASON_TEMPLATE = """\
# 来店理由の確認について（この店舗ではオーナー設定によりONになっています。重要・必ず守ってください）
この店舗のオーナー設定により、予約受付の際にお客様へ来店・来院の目的を
一言確認することが有効になっています。ただし、これは詳細な施術・診療内容や
メニューの選択を求めるものでは全くありません。

## 尋ね方
まだ来店理由が分かっていない場合に限り、会話の自然な流れの中で一度だけ
尋ねてください。例:「{example_question}」
この店舗の業種に合わせて自然な言い回しに変えて構いませんが、詳しい症状や
具体的な施術内容まで踏み込んで尋ねないでください。

## 回答時間の目安を伝える（今回追加・重要）
上記の尋ね方の例のように、「30秒くらいで」「手短に」等、長い説明が不要で
あることを一言添えてください。これは音声を強制的に打ち切るためのものでは
なく、お客様に手短な返答で十分だという期待値を事前に伝えるための一言です。
お客様がまだ話している最中であれば、経過時間だけを理由に話を遮ったり
急かしたりすることは絶対にしないでください（時間はあくまで目安です）。

## 回答が得られたら手短に受け止めて次へ進む（今回追加・重要）
来店理由の内容が一通り確認できたら、「ありがとうございます。内容は
承りました。」のように短く受け止め、すぐに次の予約項目（日時・人数・
お名前・電話番号等、まだ確認できていない項目）の確認に進んでください。
「具体的にはどのような点でお悩みですか？」「いつ頃からですか？」のような
追加の深掘り質問を重ねて会話を必要以上に長引かせないでください。

## 既に話されている場合は聞き直さない
お客様が予約の依頼と同時に、またはそれより前の発言で、来店理由に相当する
内容を既に話している場合（例:「腰が痛いので予約したいです」「カラーを
したくて」「ランチで」）、この設定がONであっても改めて「来店理由を教えて
ください」と聞き直すことは絶対にしないでください。既に話された内容を
そのまま使ってください。

## 断られた場合は予約を止めない（重要）
お客様が「直接スタッフに話したいです」「来店してから話します」「特に
決まっていません」のように、来店理由を話したくない・まだ決めていない
様子を示した場合、無理に聞き出そうとせず、「承知しました。詳しい内容は
ご来店時にスタッフがお伺いします。」のように自然に受け止めてください。
来店理由を必須条件にせず、通常どおり予約受付を続けてください。来店理由が
分からないことを理由に予約を止めたり、create_reservationの呼び出しを
妨げたりすることは絶対にしないでください。

## 医療・症状に関するプライバシー配慮（非常に重要・必ず守ってください）
お客様が「腰が痛いです」「目がかゆいです」のように症状や体調に触れた場合、
それは予約の受付に必要な範囲の情報として「承知しました。」のように短く
受け止めるだけにとどめてください。そこから「いつからですか？」「どのように
痛みますか？」「持病はありますか？」「お薬は飲んでいますか？」のように、
症状の詳細・既往歴・服薬状況などを掘り下げて尋ねることは絶対にしないで
ください。あなたは医療従事者ではなく、診断や問診を行う役割ではありません。
予約の受付に必要な最小限の一言を超えて、体調や症状について深追いしない
でください。

## 得られた来店理由の使い方
確認できた来店理由（お客様が自発的に話した場合を含む）は、create_reservation
ツールのspecial_requests引数に、お客様が話した内容をそのまま簡潔に含めて
ください。店舗の正式なメニュー・診療科目名などに自分で対応づけたり、
言い換えて確定させたりしないでください。\
"""


def _build_visit_reason_section(business_type: Optional[str]) -> str:
    example_question = _VISIT_REASON_PHRASING_EXAMPLES.get(business_type, _VISIT_REASON_DEFAULT_PHRASING)
    return _VISIT_REASON_TEMPLATE.format(example_question=example_question)


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
- create_reservation ツールを呼び出す前に必要なのは、来店希望日時、人数、
  お名前、電話番号（該当する場合はサービス内容・スタッフ指名・来店理由）が
  すべて分かっていることです。これらを一つずつ個別に「日時はこれで
  よろしいですか？」「お名前は○○様でよろしいですか？」のように確認して
  回る必要はありません。かわりに、後述の「予約全体の最終確認」として、
  これらをすべてまとめて一度だけ読み上げて確認してください（Fast
  Reservation Flowの方針。詳細は該当セクション参照）。
- 例外として電話番号だけは、聞き取り間違いを防ぐため、必ず1桁ずつ読み上げて
  復唱してください。この復唱は、予約全体の最終確認の読み上げの中で行っても、
  それより前の独立した発話として行っても構いません。
- 電話番号は、お客様からうかがった内容を必ず1桁ずつ、今話している言語の発音で
  読み上げて復唱し、お客様に間違いがないか確認してから先に進んでください。
  電話番号を推測したり、聞き取れなかった桁を適当に補ったりすることは絶対に
  しないでください。
- 「電話番号を確認します」「電話番号を確認いたしますね」のように、次に何をする
  かをぼかした予告だけを発話してそこで黙り込むことは絶対にしないでください。
  必ず次のどちらか一方を、同じ発話の中で明確に行ってください:
  (A) まだこの通話でお客様から電話番号を伺っていない場合
      → 予告せず、「お電話番号を教えていただけますか？」のように、はっきりと
      電話番号を尋ねる質問をしてください。
  (B) この通話の中で（find_customer呼び出し時など）お客様から既に電話番号を
      伺っている場合、またはお客様が今まさに電話番号を言い終えた場合
      → 「確認します」等の予告だけで止めず、同じ発話の中でその番号を実際に
      1桁ずつ読み上げて「ゼロ・キュウ・ゼロ…でよろしいですか？」のように
      復唱確認してください（上の「話し方の見本」の電話番号読み上げの例と
      同じテンポです）。改めて聞き直す必要はありません。

# 「項目ごとの確認」と「予約全体の最終確認」は別物です（重要・混同禁止）
- 会話の自然な流れの中で、電話番号・日時・人数・お名前・サービス・スタッフ
  指名など個別の項目について「合っていますか？」というやり取りが結果的に
  生じた場合でも、その「はい」「合っています」「そうです」等の返事は、
  その項目単体が正しいことの確認にすぎません。予約を確定してよいという
  同意として扱ってはいけません（上記のとおり、これらを毎回個別に確認して
  回ること自体は必須ではありません）。
- 必要な情報（日時・人数・お名前・電話番号、該当する場合はサービス・
  スタッフ指名・来店理由）がすべて揃ったら、それとは独立した一つの発話として、
  それらをすべてまとめて読み上げ、「この内容で予約してよろしいですか？」の
  ようにはっきりと尋ねてください。この「予約全体の最終確認」の直後に
  お客様が返した返事だけを、create_reservation 呼び出しの同意として
  扱ってください。個別項目確認への
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
  空いている・予約できたと推測することは絶対にしないでください。
- ツールの結果が success:true で予約が成立した後は、「ありがとうございます。
  予約をお取りしました。」のように短くお伝えするだけにとどめてください。
  詳しい内容の補足が必要な場合は「詳しい内容は、ご来店時にスタッフが
  お伺いします。」「詳しい内容については、担当者から折り返しご連絡します。」
  程度を続けて構いませんが、それ以上、予約成立後に新たな質問（メニューの
  詳細・追加のご要望等）を重ねて尋ねることは避け、通話を自然に締めくくって
  ください。\
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
        "ルールを優先してください。特に、この店舗で許可された対応言語の範囲を"
        "守ること・電話番号や日付や金額を今話している言語で自然に読み上げること・"
        "存在しない予約状況を答えないことは、この補助的な指示によって"
        "変更・無効化することはできません。\n"
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
      2a. Conversation Opening / Intent Classification（通話冒頭のご用件把握） … 常に固定
      2b. Fast Reservation Flow（予約受付を短時間で終える会話設計ルール） … 常に固定
      3. Examples（話し方の見本）                            … 常に固定
      4. Shop Information（営業時間・本日の日付）            … 常に固定
      4b. Service Terminology（Phase3H: 業種別の「サービス」呼称） … 該当業種のみ
      5a. AI Staff Personality                               … staff_settingsがある場合のみ
      5b. 電話に出たときの第一声（Greeting）                 … Phase3Cより常に付与
      6. Shop Custom Instructions（店舗独自の補助指示）      … 明示的に下位と位置づけ
      7. Constraints（現時点での制約）                       … 常に固定
      7a. Human Handoff（Human Handoff基盤: 折り返し対応の判断基準・進め方） … 常に固定
      7b. Shop Knowledge Rules（Phase3D: get_shop_info運用ルール） … 常に固定
      7c. Customer Memory Rules（Phase4A: find_customer運用ルール） … 常に固定
      7d. Customer Context Rules（Phase4B: confirm_customer_identity /
          get_customer_context運用ルール） … 常に固定
      7e. Time Ambiguity（Human Handoff基盤付随: 時刻の午前/午後解釈ルール） … 常に固定
      7e2. Relative Date（相対的な日時表現「今日/明日/明後日」の絶対日付
          変換ルール） … 常に固定
      7f. Visit Reason（Fast Reservation Flow: 来店理由の確認ルール） …
          staff_settings.ask_visit_reason_enabledがtrueの場合のみ
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

    Phase 5A追記: Core Rules内の「言語ルール」セクションと、Examples末尾の
    言語切り替えの見本は、shop.ai_supported_languages（未設定の既存店舗は
    日本語のみとして扱う。app.language_registry.effective_ai_languages参照）
    に応じて動的に生成する。日本語は必ず含まれる前提で常に先頭に来る。

    Human Handoff基盤追記: 7a（_HUMAN_HANDOFF_TEMPLATE）はConstraintsの直後・
    Shop Knowledge Rulesの直前に、7e（_TIME_AMBIGUITY_TEMPLATE）はCustomer
    Context Rulesの直後・Booking Safetyの直前に、それぞれ常に固定文言として
    挿入する（店舗独自のcustom_instructionsによる上書き・無効化はできない。
    他の常時固定セクションと同じ位置づけ）。Booking Safetyが常に最後という
    既存の不変条件は変更しない。

    相対日付変換ルール追記（谷村様の明示的な指示に基づく）:
    - 7e2（_RELATIVE_DATE_TEMPLATE）はTime Ambiguityの直後・Visit Reason
      （該当する場合）またはBooking Safetyの直前に、staff_settingsの
      有無や業種に関わらず常に固定文言として挿入する。
    - 「今日」「明日」「明後日」の絶対日付（曜日付き）自体は、このテンプレート
      ではなくShop Informationセクション
      （_SHOP_INFO_TEMPLATE + _relative_dates_block_jst()）側で、
      Python（サーバー側）が店舗ローカルの現在日付（JST固定。Shopモデルに
      店舗ごとのtimezoneフィールドは存在せず、本サービスは現時点で日本
      国内の店舗のみを対象としているため、既存の_today_str_jst()と同じ
      JST基準をそのまま踏襲する）を基準に計算する。AIモデル自身による
      日付の加算・曜日の推測には一切依存しない。
    - 新規Tool・新規DBスキーマ・新規state（クライアント側/サーバー側とも）
      は一切追加していない（既存のShop InformationセクションとBooking
      Safety/Time Ambiguityの既存の「項目ごとの確認 と 予約全体の最終確認は
      別物」という枠組みを、日付にもそのまま適用しただけの、prompt/
      instructions側のみの変更）。

    Fast Reservation Flow追記:
    - 2b（_FAST_RESERVATION_FLOW_TEMPLATE）はIntent Classificationの直後・
      Examplesの直前に、staff_settingsの有無に関わらず常に固定文言として
      挿入する。人数確認の要否は_build_party_size_guidance(shop.business_type)
      で業種ごとに差し替える（テンプレート自体は1つだけで、業種分岐はこの
      関数内のdictのみで完結させ、instructions生成処理自体を業種で
      ハードコード分岐させない）。
    - 7f（_VISIT_REASON_TEMPLATE、_build_visit_reason_sectionで組み立て）は、
      staff_settingsが存在し、かつask_visit_reason_enabledがtrueの場合にのみ、
      Time Ambiguityの直後・Booking Safetyの直前に挿入する。OFFの場合や
      staff_settingsが存在しない場合（レコード未作成＝デフォルトOFF）は
      一切挿入しない（既存店舗の挙動は変更されない）。

    Conversation Opening / Intent Classification追記（谷村様の明示的な指示に
    基づく）:
    - 2a（_INTENT_CLASSIFICATION_TEMPLATE）はScopeの直後・Fast Reservation
      Flowの直前に、staff_settingsの有無や業種に関わらず常に固定文言として
      挿入する。RESERVATION/CALLBACK/HUMAN_HANDOFF/QUESTION/INFORMATION/
      OTHER・UNCLEARの4分類と、通話冒頭でのご用件把握のタイミング・進め方を
      定めるだけで、各分類の詳細な進め方自体はFast Reservation Flow・
      Human Handoff・Shop Knowledge Rulesの各既存セクションにそのまま委ねる
      （重複して書き直さない）。
    - 「20秒」はAI自身の判断目安としてのみ文中に含め、client側の強制的な
      音声打ち切り・commit送信等は一切実装していない（NAME Forced Commit
      Observation PoCとは無関係・別スコープ）。新規Toolの追加、新規の
      Intent保持用state（クライアント側変数・サーバー側DB）の追加も、
      いずれも行っていない（監査の結果、既存4Toolと会話履歴のみで十分と
      判断したため）。
    """
    hours_block = await _build_hours_block(db, shop.id)

    ai_languages = effective_ai_languages(shop.ai_supported_languages)
    staff_languages = effective_languages(shop.staff_supported_languages)

    sections = [
        _CORE_RULES_TEMPLATE.format(
            shop_name=shop.name,
            language_rules_section=_build_language_rules_section(ai_languages, staff_languages),
        ),
        _SCOPE_TEMPLATE,
        _INTENT_CLASSIFICATION_TEMPLATE,
        _FAST_RESERVATION_FLOW_TEMPLATE.format(
            party_size_guidance=_build_party_size_guidance(shop.business_type)
        ),
        _EXAMPLES_TEMPLATE_BASE + "\n" + _build_language_example_line(ai_languages),
        _SHOP_INFO_TEMPLATE.format(hours_block=hours_block, relative_dates_block=_relative_dates_block_jst()),
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
    sections.append(_HUMAN_HANDOFF_TEMPLATE)
    sections.append(_SHOP_KNOWLEDGE_RULES_TEMPLATE)
    sections.append(_CUSTOMER_MEMORY_RULES_TEMPLATE)
    sections.append(_CUSTOMER_CONTEXT_RULES_TEMPLATE)
    sections.append(_TIME_AMBIGUITY_TEMPLATE)
    sections.append(_RELATIVE_DATE_TEMPLATE)

    # Fast Reservation Flow: 来店理由確認はオーナー設定がONの店舗のみ挿入する
    # （デフォルトOFF・staff_settings未作成の店舗は従来通り一切触れない）。
    if staff_settings is not None and staff_settings.ask_visit_reason_enabled:
        sections.append(_build_visit_reason_section(shop.business_type))

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
