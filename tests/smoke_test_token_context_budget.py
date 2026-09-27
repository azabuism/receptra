"""
RECEPTRA — FAST TURN HOTFIX 6: TOKEN / CONTEXT BUDGET REGRESSION

背景: 実機で、Tool継続response（check_availability結果を受けた2回目の
response.create）がOpenAI Realtime API側のrate limit
（error_type=tokens, error_code=Rate_limit_exceeded）でfailedになったことが
直接観測された。Console上ではinput_tokens: 24318（該当response）、および
別responseでinput_tokens: 8672 / 8700程度が確認されている。

監査の結果（実測。文字数はPython側のjson.dumps/len()による直接計測、
Realtime APIの実際のBPEトークン数ではない）:
  - build_realtime_instructions()が組み立てるsession instructionsは、
    メニュー・スタッフ・独自instructions等を一切持たない最小構成の
    新規登録店舗でも23568文字。
  - Realtime session登録している8つのtool schema（tools配列全体、
    実際にsession.updateへ送信されるJSON）は16490文字。
  - この2つの合計は40058文字で、日時・人数を1回で伝えるだけの短い
    ユーザー発話1つに対しても、Tool継続response 1回ごとに再処理される
    静的な土台となっている。
  - tool schemaのうち、check_availability（description 3534文字）と
    create_reservation（description 3309文字）の2つのdescriptionだけで
    16490文字中6843文字（約41%）を占める。ただしこれらの説明文は
    過去のFAST TURN／PHONE Forced Commit／ACK-only防止等の実機不具合の
    修正としてそれぞれ追加された行動指示を含んでおり、証拠なしに削る
    ことは既存の修正済みバグの再発リスクを伴う。
  - check_availabilityのTool出力（CheckAvailabilityResponse）は元々
    available/date/time/party_size/reason_code等の最小限のフィールド
    のみで、全営業時間・全予約・全スタッフ一覧等の不要な大量データは
    一切含まれていないことを確認した（削減の必要なし）。
  - 正確なBPEトークン数（tiktoken）は、このサンドボックス環境の
    egressポリシーがtiktokenのエンコーディングデータ取得先
    （openaipublic.blob.core.windows.net、huggingface.co等）への
    接続をブロックしているため実測できなかった。したがって本テストは
    「文字数」という実測可能な指標のみを対象とし、「4万文字≒2万数千
    トークン」等の対応関係を断定しない。

このテストの目的: 今後system prompt/tool schemaが実測に基づかない
（無自覚な）追加によって無制限に肥大化することを防ぐ回帰ガードを置く。
下記のBASELINE_*は上記の実測値そのもの（勘の数値ではない）。許容増加率
ALLOWED_GROWTH_RATIOは「小さな追加は許すが、大きな肥大化は必ず気づいて
意図的にbaselineを更新させる」ためのもので、10%とした（無条件に0%に
すると軽微な追記のたびに本テストがfailし続けて形骸化するため、実務上
安全側に倒しつつ運用可能な値として選んだ）。将来この閾値を超える正当な
追加を行う場合は、BASELINE_*の値をその時点の実測値に更新し、このコメントに
更新理由・実測日時・commit hashを追記すること（黙って閾値を上げない）。

実行: python3 tests/smoke_test_token_context_budget.py
"""
import asyncio
import json
import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tempfile.mkdtemp(), "smoke_token_budget.db")
os.environ["SECRET_KEY"] = "smoke-test-secret-key-token-budget"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used-because-mocked")
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

import httpx

# 実測値（2026-09-26、commit c98efbe時点、最小構成の新規登録店舗）。
# 勘の数値ではなく、tests/smoke_test_token_context_budget.py作成時に
# 本テストと同じ手順（build_realtime_instructions() / _REALTIME_TOOLS を
# json.dumpsしてlen()）で直接計測した値そのもの。
#
# FAST TURN HOTFIX 7更新（2026-09-26、このコミット時点）: 実機で
# Tool継続responseがOpenAI側のtoken rate limit（Rate_limit_exceeded、
# limit=40000, remaining=14711）でfailedになったことが確認されたのを受け、
# static context（instructions + tools schema）の重複監査を行った。
# _RELATIVE_DATE_TEMPLATEの「この確認と『予約全体の最終確認』は別物です」
# セクションが、直前の_TIME_AMBIGUITY_TEMPLATEの同趣旨の説明を全文に近い
# 形で繰り返していたため、意味を保ったまま短い相互参照へ圧縮した（安全性は
# tests/smoke_test_relative_date_conversion.pyの既存アサーションで担保
# 済み）。この変更によりinstructionsは23568→23549文字（-19文字）へ減少
# した。tools schema側はこの時点では変更していない（check_availability/
# create_reservationのdescriptionはreason_code別の固有ロジックであり、
# 過去の実機バグ修正に直結する内容のため、安全に削れる重複が見つからず、
# 今回は見送った。詳細はFAST TURN HOTFIX 7の最終報告を参照）。
#
# FAST TURN HOTFIX 8更新（2026-09-26、commit 447488a時点からの変更）:
# 実機で「check_availability後に30秒近く無音になり通話が切断される」
# 症状が継続していることが確認されたのを受け、HOTFIX 7では見送った
# tool schema（特にcheck_availability/create_reservationのdescription、
# tools schema全体16490文字中6843文字＝約41%を占める）の圧縮に踏み込んだ
# （Section 9 TOP PRIORITY）。
# 圧縮方針: reason_code別の案内内容・Named Staff Safe Resolution・
# Generic Resource Foundationの安全ルールは一切削らず（全項目を
# tests/smoke_test_tool_description_contract.pyで契約テスト化した上で
# 圧縮前後ともPASSすることを確認済み）、(1)「Toolを呼び出す前に許可を
# 求めない」という、_FAST_RESERVATION_FLOW_TEMPLATE/_CONSTRAINTS_TEMPLATE
# 側に既に存在する指示の全文再掲を1行の参照に短縮、(2) 長い説明文を
# 「reason_code=意味→対応」という密な箇条書き形式に変換、
# (3) create_reservationのstaff_name/resource_type関連の説明を
# 「check_availabilityと同じ」という参照に置き換え（両descriptionとも
# 同じtools配列の中で毎回一緒にモデルへ渡されるため、内容が完全に重複して
# いた分は実質的に無駄なtoken消費だった）、(4) staff_name/resource_type
# パラメータの説明文（両Toolに一字一句同一の文言が重複していた）を短縮、
# という4種類の圧縮のみを行った。文言の言い換え・要約であり、reason_code・
# 禁止表現・エスカレーション先（request_callback等）はすべて維持している。
# 実測: tools schema 16490→14277文字（-2213文字、-13.4%）。
# 内訳: check_availability description 3534→2312（-1222）、
# create_reservation description 3309→2535（-774）、
# staff_name/resource_typeパラメータ（両Tool計4箇所） 計-217。
# instructions側（system prompt本体）は今回のcommitでは変更していない
# （FAST_RESERVATION_FLOW/BOOKING_SAFETY/TIME_AMBIGUITY等の圧縮は、
# 実機検証済みの安全ルールを壊すリスクが高く、本フェーズで新設した
# 契約テストの範囲を超えるため見送った。詳細はFAST TURN HOTFIX 8の
# 最終報告を参照）。
#
# FAST TURN HOTFIX 8 PHASE 2更新（2026-09-27、commit 14ecbeb時点からの変更、
# Step 1: _FAST_RESERVATION_FLOW_TEMPLATE圧縮）: 実機で、Phase 1の
# tool schema圧縮後もinput_tokensが23015→変わらずrate_limit_exceededが
# 継続することが確認されたのを受け、instructions本体（system prompt）
# 側の圧縮に着手した。最初の対象は、最小構成の店舗でinstructions全体
# 23545文字中5212文字（22.1%）を占め、単独最大セクションだった
# _FAST_RESERVATION_FLOW_TEMPLATEとした。
# 圧縮方針: 各ルールが禁止する具体的な行動・許可する行動・悪い例／良い例は
# 一切削らず（多slot保持・相槌のみでの発話終了禁止・内部処理実況のみでの
# 発話終了禁止・Tool呼び出し前の許可待ち禁止・PHASE O5.5の一言先出し・
# party_size_guidance差し込み・メニュー詳細の任意化・来店理由の活用・
# 電話番号は原則最後・Tool結果後の自発継続・ノイズ耐性・NOISE RECOVERY・
# 速さより正確性優先、の全項目を保持）、重複した接続詞・言い換えの冗長な
# 説明文・重複気味だった例示を圧縮した。安全性は
# tests/smoke_test_guidance_unit_acknowledgement.py、
# tests/smoke_test_no_process_narration_filler.py、
# tests/smoke_test_noise_recovery_instructions.py、
# tests/smoke_test_relative_date_conversion.pyの既存アサーション
# （このコミットで、テンプレート内の折り返し改行位置や助詞等の些末な
# 言い回しに依存していた一部の完全一致チェックを、同じ意味を保ったまま
# 出現位置ベースの意味的チェックへ更新した上で）で全項目PASSすることを
# 確認済み。
# 実測: _FAST_RESERVATION_FLOW_TEMPLATE（raw, party_size_guidance未展開）
# 5175→2975文字（-2200文字、-42.5%）。instructions全体（最小構成の
# 新規登録店舗、build_realtime_instructions()の実測）23549→21337文字
# （-2212文字、-9.4%）。tools schema側はこのcommitでは変更していない
# （Phase 1のcommit 14ecbebから14277文字のまま）。
# 残る大型セクション（HUMAN_HANDOFF/BOOKING_SAFETY/TIME_AMBIGUITY/
# INTENT_CLASSIFICATION）はHOTFIX 8 Phase 2の後続ステップで段階的に
# 圧縮予定（詳細はFAST TURN HOTFIX 8 Phase 2の最終報告を参照）。
#
# FAST TURN HOTFIX 8 PHASE 2更新（2026-09-27、Step 1の次のコミット、
# Step 2: _HUMAN_HANDOFF_TEMPLATE圧縮）: 最小構成の店舗でinstructions中
# 14.1%（3330文字）を占めていた_HUMAN_HANDOFF_TEMPLATEを圧縮した。
# この節は「折り返し確定/未確定時の言い回し」「『いつ電話が来ますか』への
# 定型応答」「保証できない表現の禁止リスト」等、お客様に直接読み上げる
# 台本に近い一言一句が複数の契約テストで文字通り検証されているため、
# それらの顧客向けフレーズ自体は一切パラフレーズせず、重複する接続詞・
# 前置きの説明文・電話番号読み上げ手順の中の既存ルール（Fast Reservation
# Flow／create_reservationの電話番号確認ルール）との重複説明のみを圧縮
# した（削減率がStep 1より小さいのは、圧縮対象の大半が顧客向け台本の
# 文言そのものではなく説明文だったため）。
# 安全性はtests/smoke_test_human_handoff_wording.py（A〜P全項目）、
# tests/smoke_test_relative_date_conversion.py、
# tests/smoke_test_short_choice_and_hours_callback.pyの既存アサーションが
# 一切の変更なしで全項目PASSすることを確認済み（顧客向け台本フレーズを
# 一言一句保持したため、テスト自体の書き換えは不要だった）。
# 実測: _HUMAN_HANDOFF_TEMPLATE 3330→2791文字（-539文字、-16.2%）。
# instructions全体（最小構成の新規登録店舗）21337→20798文字
# （-539文字、-2.5%）。tools schemaは変更なし（14277文字のまま）。
# 残るBOOKING_SAFETY/TIME_AMBIGUITY/INTENT_CLASSIFICATIONはHOTFIX 8
# Phase 2の後続ステップで段階的に圧縮予定。
BASELINE_INSTRUCTIONS_CHARS = 20798
BASELINE_TOOLS_JSON_CHARS = 14277
ALLOWED_GROWTH_RATIO = 1.10  # 10%までの増加は許容し、それを超えたら気づけるようにする


def _test_tools_schema_budget():
    from app.services.realtime_voice_ai import _REALTIME_TOOLS

    tools_json = json.dumps(_REALTIME_TOOLS, ensure_ascii=False)
    actual = len(tools_json)
    limit = int(BASELINE_TOOLS_JSON_CHARS * ALLOWED_GROWTH_RATIO)
    assert actual <= limit, (
        f"tools schema (JSON) が{actual}文字に増加しています（baseline={BASELINE_TOOLS_JSON_CHARS}文字、"
        f"許容上限={limit}文字）。意図した追加であれば、BASELINE_TOOLS_JSON_CHARSを実測値に更新し、"
        f"更新理由・実測日時・commit hashをこのファイルのコメントに追記してください。"
    )
    # tool数自体が意図せず増減していないかも合わせて確認（8つ固定）。
    assert len(_REALTIME_TOOLS) == 8, (
        f"Realtime Toolの数が{len(_REALTIME_TOOLS)}個に変化しています（想定は8個）。"
        f"意図した追加/削除であれば、このテスト自体の期待値も更新してください。"
    )
    print(f"1. tools schema (JSON, 全{len(_REALTIME_TOOLS)}Tool): {actual}文字 "
          f"(baseline={BASELINE_TOOLS_JSON_CHARS}, 上限={limit}): OK")


async def _test_instructions_budget():
    from app.main import app, lifespan

    async with lifespan(app):
        # AsyncSessionLocal はlifespan起動時に初めて実体が設定されるモジュール
        # 変数のため、import自体もlifespan開始後に行う（開始前にimportすると
        # Noneのままの参照を束縛してしまう）。
        from app.database import AsyncSessionLocal
        from app.models.shop import Shop
        from app.services.realtime_voice_ai import build_realtime_instructions
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/v1/auth/register", json={
                "email": "owner-token-budget-test@example.com",
                "password": "password123",
                "display_name": "トークン予算監視テストオーナー",
            })
            assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
            owner_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await client.post("/api/v1/shops/register", json={
                "name": "トークン予算監視テスト店", "category": "レストラン",
                "address": "東京都渋谷区9-9-9",
            }, headers=owner_headers)
            assert r.status_code in (200, 201), f"create shop failed: {r.status_code} {r.text}"
            shop_id = r.json()["shop_id"]

            async with AsyncSessionLocal() as session:
                shop = await session.get(Shop, shop_id)
                assert shop is not None
                instructions = await build_realtime_instructions(session, shop, None)

    actual = len(instructions)
    limit = int(BASELINE_INSTRUCTIONS_CHARS * ALLOWED_GROWTH_RATIO)
    assert actual <= limit, (
        f"session instructionsが{actual}文字に増加しています（baseline={BASELINE_INSTRUCTIONS_CHARS}文字、"
        f"許容上限={limit}文字、最小構成の新規登録店舗基準）。意図した追加であれば、"
        f"BASELINE_INSTRUCTIONS_CHARSを実測値に更新し、更新理由・実測日時・commit hashを"
        f"このファイルのコメントに追記してください。"
    )
    print(f"2. session instructions（最小構成の新規登録店舗）: {actual}文字 "
          f"(baseline={BASELINE_INSTRUCTIONS_CHARS}, 上限={limit}): OK")


def _test_check_availability_output_stays_minimal():
    """
    check_availabilityのTool出力（CheckAvailabilityResponse）が、全営業時間・
    全予約・全スタッフ一覧等の不要な大量データを含まない最小限のフィールド
    構成のままであることを確認する（Tool継続response側のinput token膨張源に
    なっていないことの回帰確認）。
    """
    from app.schemas.reservation import CheckAvailabilityResponse

    expected_fields = {
        "available", "date", "time", "party_size", "reason_code",
        "available_resource_types", "staff_name_candidates",
    }
    actual_fields = set(CheckAvailabilityResponse.model_fields.keys())
    assert actual_fields == expected_fields, (
        f"CheckAvailabilityResponseのフィールド構成が変化しています: {actual_fields}\n"
        f"（想定: {expected_fields}）。新しいフィールドが大量データ（全営業時間・全予約・"
        f"全スタッフ一覧等）を含む場合、Tool継続responseのinput token増加源になり得るため、"
        f"追加時は本テストの期待値更新と合わせて必要性を再検討してください。"
    )

    # 典型的な成功レスポンス・失敗レスポンスそれぞれのJSONサイズが小さいままであることも確認する。
    sample_success = CheckAvailabilityResponse(
        available=True, date="2026-09-27", time="12:00", party_size=2, reason_code=None,
    )
    sample_failure = CheckAvailabilityResponse(
        available=False, date="2026-09-27", time="12:00", party_size=2,
        reason_code="fully_booked",
    )
    success_json_len = len(sample_success.model_dump_json())
    failure_json_len = len(sample_failure.model_dump_json())
    # 実測: 上記フィールド構成での典型値は200文字未満。大量データが混入すれば
    # 数千文字規模になるはずなので、500文字を安全側の上限とする。
    assert success_json_len < 500, f"check_availability成功レスポンスが{success_json_len}文字に肥大化しています"
    assert failure_json_len < 500, f"check_availability失敗レスポンスが{failure_json_len}文字に肥大化しています"
    print(f"3. check_availability Tool出力の最小構成: success={success_json_len}文字, "
          f"failure={failure_json_len}文字（いずれも500文字未満）: OK")


async def main():
    _test_tools_schema_budget()
    await _test_instructions_budget()
    _test_check_availability_output_stays_minimal()
    print("\n=== ALL smoke_test_token_context_budget.py CHECKS PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
