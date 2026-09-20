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

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from openai import AsyncOpenAI
from sqlalchemy import select
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
        return "（営業時間の登録がありません。ご希望日時はそのまま伺ってください）"
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

# 店舗情報。AIスタッフ設定の有無にかかわらず常に挿入する（Phase1から存在）。
_SHOP_INFO_TEMPLATE = """\
# 店舗の営業時間（曜日ごと）
{hours_block}

# 本日の日付
{today_str}（「明日」「今週土曜」などの相対的な日時表現はこれを基準に解釈してください）\
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

# 現時点での制約。Phase1から変更しない固定文言。常に最後に配置する。
_CONSTRAINTS_TEMPLATE = """\
# 現時点での制約（重要・必ず守ってください）
このバージョンでは、まだ予約データベース・空き状況・メニュー・料金を
参照する機能を持っていません。日時やご希望人数などのヒアリングはしてよい
ですが、実際に予約が取れるかどうかの確定的な回答（「空いています」
「予約完了です」等）はまだしないでください。ヒアリングが終わったら、
「担当の者が確認してご連絡いたします」という趣旨で丁寧に案内してください。
存在しない予約状況やメニュー・料金を想像で答えることは絶対にしないで
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


def _build_greeting_section(s: "AIStaffSettings", shop_name: str) -> str:
    """
    電話に出たときの第一声。店舗オーナーが設定していない場合や空文字の
    場合は、スタッフ名の有無に応じた自然なフォールバック文を使う
    （設定が存在しても greeting が空欄なら「名乗らない」フォールバックに
    なるよう配慮している）。
    """
    greeting = (s.greeting or "").strip()
    if not greeting:
        if s.staff_name:
            greeting = f"お電話ありがとうございます。{shop_name}、AI受付の{s.staff_name}です。"
        else:
            greeting = f"お電話ありがとうございます。{shop_name}でございます。"
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
    Constraintsの間にのみ挿入する。これにより staff_settings が None の
    場合、Phase1と完全にバイト同一のinstructions文字列を生成できる）:
      1. Core Rules（話し方の絶対ルール・言語ルール）        … 常に固定・最上位
      2. Examples（話し方の見本）                            … 常に固定
      3. Shop Information（営業時間・本日の日付）            … 常に固定
      4. AI Staff Personality / Greeting                     … staff_settingsがある場合のみ
      5. Shop Custom Instructions（店舗独自の補助指示）      … 明示的に下位と位置づけ
      6. Constraints（現時点での制約）                       … 常に固定・最後

    staff_settingsがNone、またはPhase2で追加されたフィールドが全て未設定の
    場合は、4・5が完全に省略され、Phase1時点と完全に同一のinstructions
    文字列を生成する（既存店舗の動作に一切影響を与えないための設計）。
    """
    hours_block = await _build_hours_block(db, shop.id)

    sections = [
        _CORE_RULES_TEMPLATE.format(shop_name=shop.name),
        _EXAMPLES_TEMPLATE,
        _SHOP_INFO_TEMPLATE.format(hours_block=hours_block, today_str=_today_str_jst()),
    ]

    if staff_settings is not None:
        personality_section = _build_personality_section(staff_settings)
        if personality_section:
            sections.append(personality_section)

        # 第一声の案内は、AIスタッフ設定のレコード自体が作成されている
        # 店舗にのみ追加する（Phase1のみの店舗はこのセクション自体が
        # 無いことで、instructions文字列を完全に元のままに保つ）。
        sections.append(_build_greeting_section(staff_settings, shop.name))

        if staff_settings.custom_instructions and staff_settings.custom_instructions.strip():
            sections.append(_build_custom_instructions_section(staff_settings.custom_instructions.strip()))

    sections.append(_CONSTRAINTS_TEMPLATE)

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
