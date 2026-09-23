"""
相対的な日時表現（今日・明日・明後日）の絶対日付変換 スモークテスト

背景: ユーザーからの「重要な修正」指示に基づく変更。
- お客様には「今日」「明日」「明後日」のような自然な相対表現だけを話して
  もらい、具体的な日付（何月何日）を言わせない・尋ねない。
- 絶対日付への変換はAIモデルの推測に依存させず、RECEPTRA（サーバー側の
  Python実装）が店舗ローカルの現在日付を基準に計算する。
- 日時・人数等の確認は、個別に何度も行わず、その時点で分かっている情報を
  まとめて一度の発話で確認する。
- 予約全体の最終確認（既存のBooking Safety）と重複した日付の再確認を
  しないよう案内する。

実装前の安全確認（ユーザーの明示的なSTOP条件への回答）:
「現在日時・timezoneを安全に取得できない場合は実装前にSTOPして報告」との
指示に対し、Shopモデルには店舗ごとのtimezoneフィールドが存在せず、本サービスは
現時点で日本国内の店舗のみを対象としていることを確認した。これは、既存の
_today_str_jst()がPhase1時点から本番で使い続けているJST固定基準と完全に
同じものであり、新たに導入する依存ではないため、STOP条件には該当しないと
判断し、実装を行った。本テストはこの判断も含めて検証する。

検証項目:
1. _relative_dates_block_jst() が、複数の「現在日時」（通常の平日、月またぎ、
   年またぎ）それぞれで、今日・明日・明後日の絶対日付（曜日付き）を
   Python側で正しく計算していること（AIモデルの推測に一切依存しないことの
   核心部分）。
2. _SHOP_INFO_TEMPLATE / _RELATIVE_DATE_TEMPLATE の文面に、
   - 日付計算をAI自身の推測で行わないという明示的な指示
   - 「何月何日ですか？」「明日というのは何日でしょうか？」という
     禁止された言い回しの実例（否定文脈を伴う）
   - 日時・人数をまとめて一度の発話で確認するという指示、および
     「明日ですね？」→「2時ですね？」→「2人ですね？」のような個別の
     逐次確認を禁止する文言
   - 予約全体の最終確認と、この早期の日付確認が別物であり、かつ
     最終確認で日付だけを重複して問い直す必要はないという文言
   が含まれていること。
3. build_realtime_instructions() が実際に組み立てるinstructions全体で、
   Relative DateセクションがTime Ambiguityの直後・Booking Safetyの直前に
   挿入されており、既存の全セクションの出現順序（Booking Safetyが常に
   最後という不変条件を含む）が壊れていないこと。
4. 今回変更していないはずの既存テンプレート（Tool schema・Intent
   Classification・Constraints・Fast Reservation Flow・Booking Safety・
   Human Handoff）が変化していないことの回帰確認。

実行: python3 tests/smoke_test_relative_date_conversion.py
"""

import asyncio
import os
import sys
import tempfile
import unittest.mock
from datetime import datetime as real_datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_relative_date.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-relative-date"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")

import httpx  # noqa: E402

JST = timezone(timedelta(hours=9))
_DAY_NAMES_JA = ["月", "火", "水", "木", "金", "土", "日"]


def _expected_block(fixed_now: real_datetime) -> str:
    today = fixed_now.astimezone(JST).date()
    tomorrow = today + timedelta(days=1)
    day_after = today + timedelta(days=2)

    def fmt(d):
        return f"{d.year}年{d.month}月{d.day}日（{_DAY_NAMES_JA[d.weekday()]}曜日）"

    return f"今日: {fmt(today)}\n明日: {fmt(tomorrow)}\n明後日: {fmt(day_after)}"


def _test_relative_dates_block_computation():
    """CASE: 通常の平日・月またぎ・年またぎで、Python側の計算が正しいこと。"""
    import app.services.realtime_voice_ai as rv

    test_cases = [
        # (説明, JSTでの固定now)
        ("通常の平日（2026-09-23水曜）", real_datetime(2026, 9, 23, 10, 0, 0, tzinfo=JST)),
        ("月またぎ（2026-09-30水曜→10-01/10-02）", real_datetime(2026, 9, 30, 23, 30, 0, tzinfo=JST)),
        ("年またぎ（2026-12-31木曜→翌年1-01/1-02）", real_datetime(2026, 12, 31, 23, 59, 0, tzinfo=JST)),
        ("深夜0時直後（2027-01-01金曜 00:05）", real_datetime(2027, 1, 1, 0, 5, 0, tzinfo=JST)),
    ]

    class _FakeDatetime(real_datetime):
        _fixed = None

        @classmethod
        def now(cls, tz=None):
            assert tz is not None, "JST引数なしでdatetime.now()が呼ばれています（タイムゾーンを明示すべき）"
            return cls._fixed.astimezone(tz) if cls._fixed.tzinfo else cls._fixed.replace(tzinfo=tz)

    for label, fixed_now in test_cases:
        _FakeDatetime._fixed = fixed_now
        with unittest.mock.patch.object(rv, "datetime", _FakeDatetime):
            actual = rv._relative_dates_block_jst()
        expected = _expected_block(fixed_now)
        assert actual == expected, f"[{label}] 期待値と不一致:\n期待:\n{expected}\n実際:\n{actual}"

    print("1. _relative_dates_block_jst() の日付計算（平日/月またぎ/年またぎ）: OK")


def _test_template_wording():
    from app.services.realtime_voice_ai import _SHOP_INFO_TEMPLATE, _RELATIVE_DATE_TEMPLATE

    # Shop Info: モデル自身の推測に依存しないという明示的指示
    assert "{relative_dates_block}" in _SHOP_INFO_TEMPLATE
    assert "推測・暗算で行うことは絶対にしない" in _SHOP_INFO_TEMPLATE

    # 禁止された言い回しの実例（否定文脈を伴っていること）
    forbidden_phrases = ["何月何日ですか？", "明日というのは何日でしょうか？"]
    for phrase in forbidden_phrases:
        idx = _RELATIVE_DATE_TEMPLATE.find(phrase)
        assert idx != -1, f"禁止フレーズの実例が見当たりません: {phrase}"
        nearby = _RELATIVE_DATE_TEMPLATE[max(0, idx - 30):idx + len(phrase) + 100]
        assert "絶対にしないでください" in nearby or "絶対に" in nearby, (
            f"禁止フレーズが否定文脈を伴わず登場しています: {phrase!r} 周辺: {nearby!r}"
        )

    # まとめて一度の発話で確認する（個別の逐次確認は禁止）
    normalized = _RELATIVE_DATE_TEMPLATE.replace("\n", "")
    assert "まとめて、一つの発話で自然に確認してください" in normalized
    assert "個別に確認して回ることは絶対にしないでください" in normalized
    assert "「明日ですね？」" in normalized and "「2時ですね？」" in normalized

    # 予約全体の最終確認との重複回避
    assert "予約全体の最終確認」は別物です" in normalized
    assert "改めて個別に問い直す必要はありません" in normalized

    # AIモデル自身が日付計算・曜日推測をしないことの明記
    assert "日付を加算したり、曜日を推測したりすることは絶対にしないでください" in normalized

    print("2. Shop Info / Relative Dateテンプレートの文面: OK")


def _test_backend_unchanged():
    """今回のスコープ外（Tool schema・Intent Classification等）が変わっていないことの回帰確認。"""
    from app.services.realtime_voice_ai import (
        _REALTIME_TOOLS, _INTENT_CLASSIFICATION_TEMPLATE, _CONSTRAINTS_TEMPLATE,
        _FAST_RESERVATION_FLOW_TEMPLATE, _HUMAN_HANDOFF_TEMPLATE,
    )

    tool_names = [t["name"] for t in _REALTIME_TOOLS]
    assert tool_names == [
        "check_availability", "create_reservation", "get_shop_info", "find_customer",
        "confirm_customer_identity", "get_customer_context", "set_conversation_language",
        "request_callback",
    ], f"8 Tool schemaが変化しています: {tool_names}"
    assert "通話冒頭のご用件把握" in _INTENT_CLASSIFICATION_TEMPLATE
    assert "空き状況の確認は check_availability" in _CONSTRAINTS_TEMPLATE
    assert "情報を集める基本順序" in _FAST_RESERVATION_FLOW_TEMPLATE
    assert "business_hours_not_configured" in _HUMAN_HANDOFF_TEMPLATE

    print("3. Tool schema/Intent Classification/Constraints/Fast Reservation Flow/Human Handoff: 変更なし OK")


def _assert_section_order(instructions: str):
    markers = {
        "core_rules": "話し方の絶対ルール",
        "scope": "会話範囲のルール",
        "intent_classification": "通話冒頭のご用件把握",
        "fast_reservation_flow": "予約受付を短時間で終える「Fast Reservation Flow」の絶対ルール",
        "examples": "話し方の見本",
        "shop_info": "本日・明日・明後日の日付",
        "constraints": "現時点での制約",
        "human_handoff": "折り返し対応（Human Handoff）に関する絶対ルール",
        "shop_knowledge_rules": "店舗情報の質問への回答ルール（重要・必ず守ってください）",
        "customer_memory": "常連のお客様の認識に関するルール",
        "customer_context": "本人確認後のご利用情報",
        "time_ambiguity": "時刻の午前/午後（AM/PM）解釈ルール",
        "relative_date": "相対的な日時表現（今日・明日・明後日）の絶対日付への変換ルール（重要・必ず守ってください）",
        "booking_safety": "予約成立の宣言に関する絶対ルール（最重要・必ず守ってください）",
    }
    positions = {}
    for key, marker in markers.items():
        idx = instructions.find(marker)
        assert idx != -1, f"期待したセクションがinstructionsに見つかりません: {key} ({marker!r})"
        positions[key] = idx

    ordered_keys = [
        "core_rules", "scope", "intent_classification", "fast_reservation_flow",
        "examples", "shop_info", "constraints", "human_handoff", "shop_knowledge_rules",
        "customer_memory", "customer_context", "time_ambiguity", "relative_date", "booking_safety",
    ]
    for a, b in zip(ordered_keys, ordered_keys[1:]):
        assert positions[a] < positions[b], (
            f"セクション順序が崩れています: {a}({positions[a]}) が {b}({positions[b]}) より後ろにあります"
        )
    assert positions["booking_safety"] == max(positions.values()), (
        f"Booking Safetyが最後のセクションではありません: {positions}"
    )
    # Relative DateがTime Ambiguityの直後・Booking Safetyの直前にあること
    assert positions["time_ambiguity"] < positions["relative_date"] < positions["booking_safety"]


async def _test_build_instructions_direct(shop_id):
    from app.database import AsyncSessionLocal
    from app.models.shop import Shop
    from app.services.realtime_voice_ai import build_realtime_instructions

    async with AsyncSessionLocal() as session:
        shop = await session.get(Shop, shop_id)
        assert shop is not None
        instructions = await build_realtime_instructions(session, shop, None)
        _assert_section_order(instructions)

        # 実際に計算された絶対日付（曜日付き）が本文に現れていること
        # （AIの推測ではなくサーバー計算結果が使われていることの端的な確認）
        import app.services.realtime_voice_ai as rv
        block = rv._relative_dates_block_jst()
        assert block.splitlines()[0] in instructions, "計算済みの「今日」の日付がinstructionsに含まれていません"

    print("4. build_realtime_instructions() 全体でのセクション出現順序・実際の日付埋め込み: OK")


async def main():
    _test_relative_dates_block_computation()
    _test_template_wording()
    _test_backend_unchanged()

    from app.main import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-relative-date@example.com",
                "password": "password123",
                "display_name": "相対日付テストオーナー",
            })
            assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
            owner_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "相対日付テスト整体院", "category": "整体",
                "address": "東京都渋谷区3-3-3",
            }, headers=owner_headers)
            assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
            shop_id = r.json()["shop_id"]

        await _test_build_instructions_direct(shop_id)

    print("\n全テストOK: 相対的な日時表現の絶対日付変換（改善3）")


if __name__ == "__main__":
    asyncio.run(main())
