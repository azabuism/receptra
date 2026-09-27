'use strict';

// RECEPTRA — FAST TURN EMERGENCY HOTFIX 11（2026年9月・root cause fix）
// 「Tool継続だけでなくnormal_conversation応答でもrate_limit_exceededが発生し、
// HOTFIX 10のfallback機構がtoolContinuationTraceActiveにのみgateされていた
// ため一切カバーされていなかった」問題の契約テスト（フロントエンド側）。
//
// 背景（実機証拠。ユーザー提供の実機ログに基づく）:
//   RATE_LIMITS_UPDATED (limit=40000, remaining=445, reset_seconds≈59.3)
//   RATE_LIMIT_TOKEN_DELTA (previousRemaining=37072, currentRemaining=445,
//     consumedSinceLastUpdate=36627, lastObservedResponseInputTokens=20625)
//   RESPONSE_DONE_FAILED error_code=rate_limit_exceeded
//     responseCreateCategory=normal_conversation
// が2回連続で観測され、その後silence_warning→silence_goodbye→通話終了と
// 進んでしまった（MODEL FAILUREがUSER SILENCEと誤認されていたroot cause。
// ユーザー指示§11）。HOTFIX 10のfallback音声＋silence timer遅延機構は
// toolContinuationTraceActive && toolContinuationTraceCallId にのみ
// gateされていたため、この経路を一切カバーしていなかった。
//
// 本HOTFIX 11の修正方針（ユーザー指示§10の絶対禁止事項を厳守）:
//   - 新しいresponse.create再送信は一切追加しない
//   - availabilityの捏造・ビジネス結果の断定は一切しない
//   - 既存のHOTFIX 10のfallback音声再生＋silence timer遅延の仕組み
//     （playToolContinuationRateLimitFallback / deferSilenceTimerFor
//     ToolContinuationRateLimit）を、call_idが存在しないケース向けに
//     response.idベースの合成ガードキーで流用するだけ（新しい音声・
//     新しいTTS・新しいstate変数は追加しない）
//   - silence_warning/silence_goodbye自身の応答が失敗するケースは
//     今回の実機証拠に含まれないため、意図的にスコープ外のまま
//     （証拠のない変更はしない）
//
// 本テストが確認する項目:
//   1. normal_conversationカテゴリのrate_limit_exceeded失敗で、新しい
//      response.create再送信を一切行わず、既存fallback機構が起動する
//   2. 起動時のガードキーがtoolContinuationTraceCallIdではなく、
//      response.idベースの合成キーである（Tool継続側のonce-per-call_id
//      ガードと衝突しない）
//   3. 異なるresponse.id（異なる失敗）は異なるガードキーになる
//      （1回目の失敗が2回目の再生をブロックしない）
//   4. silence_warning/silence_goodbyeカテゴリでは新分岐が発火しない
//      （意図的なスコープ外の回帰確認）
//   5. rate_limit_exceeded以外のerror_codeでは何も起きない（既存動作）
//   6. 既存のTool継続分岐（budgetLooksInsufficient / RETRY_ALSO_FAILED）は
//      本HOTFIXにより一切変更されていない（回帰）
//   7. dc.send呼び出し箇所数は無変更（新規Realtime制御イベントを追加していない）
//
// 実行: node tests/test_fast_turn_hotfix11_normal_conversation_rate_limit_fallback.js

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ENGINE_JS_PATH = path.join(__dirname, '..', 'frontend', 'public', 'js', 'realtime-voice-engine.js');
const SRC = fs.readFileSync(ENGINE_JS_PATH, 'utf8');

// response.doneハンドラ内の「rate_limit_exceeded判定〜fallback起動」ブロック
// （const respStatus = ... から、対応するif (respStatus==='failed'||'incomplete')
// ブロックの終端まで）を、行番号に依存せず、シグネチャの対応する中括弧の
// 深さを実際に数えて抽出する（既存test_fast_turn_hotfix10_...jsのextractBlock
// と同じ手法。中身が変わっても壊れにくい）。
function extractRateLimitHandlingSnippet(src) {
    const startSig = 'const respStatus = msg.response && msg.response.status;';
    const startIdx = src.indexOf(startSig);
    assert.notStrictEqual(startIdx, -1, 'response.done rate-limit handling block start not found (has response.done been restructured?)');
    const ifSig = "if (respStatus === 'failed' || respStatus === 'incomplete') {";
    const ifIdx = src.indexOf(ifSig, startIdx);
    assert.notStrictEqual(ifIdx, -1, 'the failed/incomplete guard not found after respStatus declaration');
    let depth = 0, i = ifIdx, started = false;
    for (; i < src.length; i++) {
        if (src[i] === '{') { depth++; started = true; }
        else if (src[i] === '}') {
            depth--;
            if (started && depth === 0) { i++; break; }
        }
    }
    return src.slice(startIdx, i);
}

const SNIPPET = extractRateLimitHandlingSnippet(SRC);

function buildContext(overrides) {
    const events = [];
    const logs = [];
    const sendResponseCreateCalls = [];
    const playCalls = [];
    const startSilenceTimerCalls = [];
    const timeouts = [];

    const context = {
        msg: {
            response: {
                status: 'failed',
                status_details: { type: 'failed', reason: 'rate_limit_exceeded', error: { type: 'tokens', code: 'rate_limit_exceeded', message: 'Rate limit reached' } },
                id: 'resp_normalconv_0001',
            },
        },
        responseState: 'active',
        updateAudioDiagnosticsPanel: () => {},
        pushTimelineEvent: (text) => { events.push(text); },
        console: { log: (line) => { logs.push(line); } },
        debugMode: false,
        toolContinuationTraceActive: false,
        toolContinuationTraceCallId: null,
        turnLatencyTraceId: null,
        lastResponseReasonCategoryForDiag: 'normal_conversation',
        toolContinuationRateLimitRetryUsedForCallId: null,
        lastKnownRateLimitRemainingTokens: 445,
        lastObservedResponseInputTokens: 20625,
        subStatusText: { textContent: '' },
        sendResponseCreate: (reason) => { sendResponseCreateCalls.push(reason); return true; },
        deferSilenceTimerForToolContinuationRateLimit: false,
        callGeneration: 1,
        startSilenceTimerIfNeeded: (gen, reason) => { startSilenceTimerCalls.push({ gen, reason }); },
        playToolContinuationRateLimitFallback: (callIdForGuard, myGeneration, cb) => {
            playCalls.push({ callIdForGuard, myGeneration });
            // HOTFIX10の実装同様、この場ではcbを呼ばない（実機のonEndedを模す）。
        },
        setTimeout: (fn, ms) => { timeouts.push({ fn, ms }); return 0; },
    };
    Object.assign(context, overrides || {});
    vm.createContext(context);
    return { context, events, logs, sendResponseCreateCalls, playCalls, startSilenceTimerCalls, timeouts };
}

function run(overrides) {
    const built = buildContext(overrides);
    vm.runInContext(SNIPPET, built.context);
    return built;
}

let passed = 0, failed = 0;
function test(name, fn) {
    try {
        fn();
        passed++;
        console.log('  ok - ' + name);
    } catch (e) {
        failed++;
        console.log('  FAIL - ' + name);
        console.log('    ' + (e && e.stack ? e.stack.split('\n').slice(0, 8).join('\n    ') : e));
    }
}

console.log('FAST TURN EMERGENCY HOTFIX 11 — normal_conversation rate-limit fallback contract tests');
console.log('source: ' + ENGINE_JS_PATH);
console.log('');

// ===== 1〜3: normal_conversationカテゴリでの新分岐 =====

test('1) normal_conversationカテゴリのrate_limit_exceeded失敗: 追加のsendResponseCreateは一切呼ばれない（新規retry禁止・ユーザー指示§10）', () => {
    const { sendResponseCreateCalls } = run({});
    assert.strictEqual(sendResponseCreateCalls.length, 0, 'HOTFIX 11 must never send a new response.create for a normal_conversation rate-limit failure');
});

test('1) normal_conversationカテゴリのrate_limit_exceeded失敗: 既存fallback機構(playToolContinuationRateLimitFallback)が起動する', () => {
    const { playCalls } = run({});
    assert.strictEqual(playCalls.length, 1, 'the existing local fallback-audio mechanism must be triggered exactly once');
});

test('1) normal_conversationカテゴリのrate_limit_exceeded失敗: BUSINESS RESULTではなくSYSTEM FAILUREの表示に切り替わる', () => {
    const { context } = run({});
    assert.strictEqual(context.subStatusText.textContent, '混み合っています。少々お待ちください');
});

test('1) normal_conversationカテゴリのrate_limit_exceeded失敗: silence timerが即座に開始されず、defer flagが立つ（MODEL FAILUREをUSER SILENCEと誤認しない・ユーザー指示§11）', () => {
    const { context, startSilenceTimerCalls } = run({});
    assert.strictEqual(context.deferSilenceTimerForToolContinuationRateLimit, true, 'the silence timer must be deferred until the fallback audio actually finishes, not armed immediately on a model/API failure');
    assert.strictEqual(startSilenceTimerCalls.length, 0, 'startSilenceTimerIfNeeded must not be called synchronously here (it is called later, from the fallback-audio-ended callback)');
});

test('1) normal_conversationカテゴリのrate_limit_exceeded失敗: 専用の診断マーカーが記録される', () => {
    const { events, logs } = run({});
    assert.ok(events.some((e) => e.indexOf('NORMAL_CONVERSATION_RATE_LIMIT_FALLBACK_TRIGGERED') === 0));
    assert.ok(logs.some((l) => l.indexOf('[NORMAL_CONVERSATION_RATE_LIMIT_FALLBACK_TRIGGERED') === 0));
});

test('2) 起動時のガードキーはtoolContinuationTraceCallIdではなく、response.idベースの合成キーである（Tool継続側のonce-per-call_idガードと衝突しない）', () => {
    const { playCalls } = run({});
    assert.strictEqual(playCalls.length, 1);
    const guardKey = playCalls[0].callIdForGuard;
    assert.ok(guardKey, 'guard key must not be null/empty (playToolContinuationRateLimitFallback would otherwise skip playback entirely)');
    assert.ok(String(guardKey).indexOf('normal_conv_resp_') === 0, 'guard key must be namespaced distinctly from real tool call_ids: ' + guardKey);
    const expectedRespIdTail = String('resp_normalconv_0001').slice(-8);
    assert.ok(String(guardKey).indexOf(expectedRespIdTail) !== -1, 'guard key must be derived from this response\'s own id (last 8 chars, same slicing as the existing respIdTail diagnostic), not a fixed/shared constant: ' + guardKey);
});

test('3) 異なるresponse.id（異なる失敗）は異なるガードキーになる（1回目の失敗が2回目の再生を永久にブロックしない）', () => {
    const first = run({ msg: { response: { status: 'failed', status_details: { type: 'failed', reason: 'rate_limit_exceeded', error: { type: 'tokens', code: 'rate_limit_exceeded' } }, id: 'resp_AAAAAAAA' } } });
    const second = run({ msg: { response: { status: 'failed', status_details: { type: 'failed', reason: 'rate_limit_exceeded', error: { type: 'tokens', code: 'rate_limit_exceeded' } }, id: 'resp_BBBBBBBB' } } });
    assert.strictEqual(first.playCalls.length, 1);
    assert.strictEqual(second.playCalls.length, 1);
    assert.notStrictEqual(first.playCalls[0].callIdForGuard, second.playCalls[0].callIdForGuard,
        'two different failed responses must get two different guard keys, so a second, later normal_conversation failure in the same call is not silently skipped as "already played"');
});

// ===== 4: 意図的なスコープ外（silence_warning/silence_goodbye） =====

test('4) silence_warningカテゴリでは新分岐が発火しない（今回の実機証拠に含まれないため意図的にスコープ外）', () => {
    const { playCalls, sendResponseCreateCalls, events, context } = run({ lastResponseReasonCategoryForDiag: 'silence_warning' });
    assert.strictEqual(playCalls.length, 0);
    assert.strictEqual(sendResponseCreateCalls.length, 0);
    assert.ok(!events.some((e) => e.indexOf('NORMAL_CONVERSATION_RATE_LIMIT_FALLBACK_TRIGGERED') === 0));
    assert.strictEqual(context.deferSilenceTimerForToolContinuationRateLimit, false);
});

test('4) silence_goodbyeカテゴリでは新分岐が発火しない（今回の実機証拠に含まれないため意図的にスコープ外）', () => {
    const { playCalls, sendResponseCreateCalls } = run({ lastResponseReasonCategoryForDiag: 'silence_goodbye' });
    assert.strictEqual(playCalls.length, 0);
    assert.strictEqual(sendResponseCreateCalls.length, 0);
});

// ===== 5: rate_limit_exceeded以外は無変更 =====

test('5) error_codeがrate_limit_exceeded以外の場合、新分岐・既存分岐のいずれも発火しない（既存動作の維持）', () => {
    const { playCalls, sendResponseCreateCalls, context } = run({
        msg: { response: { status: 'failed', status_details: { type: 'failed', reason: 'server_error', error: { type: 'server_error', code: 'internal_error' } }, id: 'resp_other' } },
    });
    assert.strictEqual(playCalls.length, 0);
    assert.strictEqual(sendResponseCreateCalls.length, 0);
    assert.strictEqual(context.deferSilenceTimerForToolContinuationRateLimit, false);
});

// ===== 6: 既存のTool継続分岐の回帰確認 =====

test('6) 回帰) Tool継続中・budget不足の既存分岐は本HOTFIXで変更されていない（ガードキーは引き続きtoolContinuationTraceCallIdそのもの）', () => {
    const { playCalls, sendResponseCreateCalls } = run({
        toolContinuationTraceActive: true,
        toolContinuationTraceCallId: 'call_abc123',
        lastResponseReasonCategoryForDiag: 'tool_result',
        lastKnownRateLimitRemainingTokens: 445,
        lastObservedResponseInputTokens: 20625,
    });
    assert.strictEqual(sendResponseCreateCalls.length, 0, 'budgetLooksInsufficient must still skip the retry (unchanged)');
    assert.strictEqual(playCalls.length, 1);
    assert.strictEqual(playCalls[0].callIdForGuard, 'call_abc123', 'the tool-continuation branch must still use the real call_id as its guard key, unaffected by HOTFIX 11');
});

test('6) 回帰) Tool継続中・budget充分な場合は引き続き1回だけretryのresponse.createを送る（既存動作の維持）', () => {
    const { playCalls, sendResponseCreateCalls } = run({
        toolContinuationTraceActive: true,
        toolContinuationTraceCallId: 'call_xyz789',
        lastResponseReasonCategoryForDiag: 'tool_result',
        lastKnownRateLimitRemainingTokens: 30000,
        lastObservedResponseInputTokens: 5000,
    });
    assert.deepStrictEqual(sendResponseCreateCalls, ['tool_continuation_rate_limit_retry']);
    assert.strictEqual(playCalls.length, 0, 'fallback audio must not play when a retry was sent instead');
});

test('6) 回帰) Tool継続retryが既に消費済みで再度失敗した場合（RETRY_ALSO_FAILED）も本HOTFIXで変更されていない', () => {
    const { playCalls, sendResponseCreateCalls } = run({
        toolContinuationTraceActive: true,
        toolContinuationTraceCallId: 'call_retry_done',
        toolContinuationRateLimitRetryUsedForCallId: 'call_retry_done',
        lastResponseReasonCategoryForDiag: 'tool_result',
    });
    assert.strictEqual(sendResponseCreateCalls.length, 0, 'must never send a second retry (infinite retry forbidden)');
    assert.strictEqual(playCalls.length, 1);
    assert.strictEqual(playCalls[0].callIdForGuard, 'call_retry_done');
});

// ===== 7: dc.send回帰 =====

test('7) DC-SEND回帰: 本HOTFIXは新規Realtime制御イベント(dc.send)を1つも追加していない', () => {
    const sendCount = (SRC.match(/dc\.send\(JSON\.stringify\(/g) || []).length;
    assert.strictEqual(sendCount, 10, 'FAST TURN EMERGENCY HOTFIX 11 must not add any new dc.send() call site; it only reuses the existing local <audio> fallback mechanism with a different guard key');
});

test('7) 回帰: sendResponseCreate(\'tool_continuation_rate_limit_retry\')は引き続き唯一の呼び出し箇所である', () => {
    const count = (SRC.match(/sendResponseCreate\('tool_continuation_rate_limit_retry'\)/g) || []).length;
    assert.strictEqual(count, 1);
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
process.exit(failed > 0 ? 1 : 0);
