'use strict';

/**
 * RECEPTRA — FAST TURN HOTFIX 4
 * ACK-ONLY / CONFIRMATION-ONLY TURN 調査（実機再現の訂正を受けて）
 *
 * 背景: 実機で「明日、二人で12時に予約したいです」に対しAIが
 * 「承知しました。12時のお時間ですね」まで発話し、その後何も質問せず
 * 完全に停止した。ユーザー本人の訂正により、これは「最初の発話が届かない」
 * 症状ではなく、user speech→model response→audio playbackまでは成功した
 * 上での「responseの内容が確認だけで終了した」症状であることが判明した。
 *
 * 監査の結果:
 *   - check_availabilityはdate/time/party_sizeの3つだけで呼び出し可能で、
 *     Tool定義自身が「確認を待たず必ずこの関数を呼び出してください」と
 *     明記している。「明日、二人で12時に予約したいです」はこの3つ全てを
 *     含むため、モデルが「承知しました。12時のお時間ですね」を
 *     Tool呼び出し前の一言として発話した直後にcheck_availabilityを呼び、
 *     その後の空き状況案内＋次の質問（Tool結果を受けた2回目のresponse）が
 *     一度も生成されなかった可能性がある。
 *   - 監査で、T6（response.create送信成功=dc.send成功）からT7
 *     （response.created受信）までの間に、現状いかなるタイムアウト・
 *     フォールバックも存在しないことを確認した（前フェーズで発見した
 *     「committed後にresponse.createdが一切届かない」構造的リスクの、
 *     Tool継続チェーン版）。
 *   - 同じ構造的リスクは、Tool呼び出しを伴わない通常ターン（committed→
 *     response.created）側にも存在する（前フェーズのFAST TURN HOTFIX 3で
 *     既に発見済みだが、そちらは修正せず観測のみに留めていた）。
 *
 * 今回も推測でタイマー短縮・response.create再送信等の挙動変更は行わない
 * （証拠不十分）。かわりに、この2つの空白（Tool継続側・通常ターン側）を
 * 次の実機テストで確実に可視化するための診断専用watchdogを追加した。
 * watchdogは一切送信を行わず、タイムアウト時にタイムラインへ1行記録する
 * だけである。
 *
 * 本テストは、実際のarm/cancel関数の実ソースをvmサンドボックスで実行し、
 * (1) 正常系（T7/TURN_RESPONSE_CREATEDが先に届く）ではwatchdogが黙って
 *     解除され、何も記録されないこと
 * (2) 異常系（タイムアウトまでに届かない）でのみ、診断専用の1行が記録され、
 *     dc.send等の送信は一切発生しないこと
 * (3) 通話終了・ターンリセット時にwatchdogがリークしないこと
 * を実行ベースで確認する。
 *
 * 実行: node tests/test_response_watchdogs.js
 */

const fs = require('fs');
const path = require('path');
const assert = require('assert');
const vm = require('vm');

const SRC_PATH = path.join(__dirname, '..', 'frontend', 'public', 'js', 'realtime-voice-engine.js');
const SRC = fs.readFileSync(SRC_PATH, 'utf8');

let passed = 0, failed = 0;
function test(name, fn) {
    try {
        fn();
        passed++;
        console.log('  ok - ' + name);
    } catch (e) {
        failed++;
        console.log('  FAIL - ' + name);
        console.log('    ' + (e && e.stack ? e.stack.split('\n').slice(0, 6).join('\n    ') : e));
    }
}

console.log('FAST TURN HOTFIX 4 — TOOL CONTINUATION / PLAIN TURN RESPONSE WATCHDOG tests');
console.log('source: ' + SRC_PATH);
console.log('');

function extractFunction(src, signature) {
    const idx = src.indexOf(signature);
    assert.notStrictEqual(idx, -1, 'signature not found: ' + signature);
    let depth = 0, i = idx, started = false;
    for (; i < src.length; i++) {
        if (src[i] === '{') { depth++; started = true; }
        else if (src[i] === '}') {
            depth--;
            if (started && depth === 0) { i++; break; }
        }
    }
    return src.slice(idx, i);
}

function buildSandbox() {
    const armToolSrc = extractFunction(SRC, 'function armToolContinuationResponseWatchdog(forCallId) {');
    const cancelToolSrc = extractFunction(SRC, 'function cancelToolContinuationResponseWatchdog(reason) {');
    const armPlainSrc = extractFunction(SRC, 'function armPlainTurnResponseWatchdog(forTurnId) {');
    const cancelPlainSrc = extractFunction(SRC, 'function cancelPlainTurnResponseWatchdog(reason) {');
    const pushToolTraceSrc = extractFunction(SRC, 'function pushToolContinuationTrace(label) {');
    const pushTurnTraceSrc = extractFunction(SRC, 'function pushTurnLatencyTrace(marker, extraNote) {');

    const events = [];
    const pendingTimers = new Map();
    let nextTimerId = 1;
    let clockMs = 0;

    const TOOL_CONTINUATION_RESPONSE_WATCHDOG_MS = 8000;
    const PLAIN_TURN_RESPONSE_WATCHDOG_MS = 8000;

    const sandbox = {
        // tool continuation trace state
        toolContinuationTraceActive: false,
        toolContinuationTraceCallId: null,
        toolContinuationTraceT0: 0,
        toolContinuationTraceShortId: null,
        toolContinuationWatchdogTimerId: null,
        toolContinuationWatchdogForCallId: null,
        TOOL_CONTINUATION_RESPONSE_WATCHDOG_MS,

        // plain turn trace state
        turnLatencyTraceId: null,
        turnLatencyTraceMarkersSeen: {},
        plainTurnResponseWatchdogTimerId: null,
        plainTurnResponseWatchdogForTurnId: null,
        PLAIN_TURN_RESPONSE_WATCHDOG_MS,

        // shared diagnostic context fields referenced by pushTurnLatencyTrace
        expectedAnswerType: 'NONE',
        responseState: 'idle',
        turnLatencyTraceT0: 0,
        turnLatencyTraceLastAt: 0,

        dc: { readyState: 'open', send: (payload) => { events.push({ type: 'dc.send', payload: JSON.parse(payload) }); } },
        pushTimelineEvent: (msg) => { events.push({ type: 'timeline', msg }); },
        performance: { now: () => clockMs },
        setTimeout: (cb) => {
            const id = nextTimerId++;
            pendingTimers.set(id, cb);
            return id;
        },
        clearTimeout: (id) => { pendingTimers.delete(id); },
        console,
    };
    vm.createContext(sandbox);
    vm.runInContext(
        [pushToolTraceSrc, pushTurnTraceSrc, armToolSrc, cancelToolSrc, armPlainSrc, cancelPlainSrc].join('\n\n'),
        sandbox
    );

    return {
        sandbox,
        events,
        advanceClock: (ms) => { clockMs += ms; },
        // 最後にarmされたタイマー（このテストではarmは1回ずつしか呼ばないため、
        // 常に最新のpendingを1つfireすれば十分）。
        fireLatestTimer: () => {
            const ids = [...pendingTimers.keys()];
            const id = ids[ids.length - 1];
            const cb = pendingTimers.get(id);
            pendingTimers.delete(id);
            cb();
        },
        pendingTimerCount: () => pendingTimers.size,
        timelineTexts: () => events.filter(e => e.type === 'timeline').map(e => e.msg),
        dcSends: () => events.filter(e => e.type === 'dc.send'),
    };
}

// =======================================================================
// TOOL CONTINUATION RESPONSE WATCHDOG
// =======================================================================

test('TOOL WATCHDOG) 異常系: T6送信後、T7(response.created)が一度も届かないままタイムアウトすると、診断専用の1行が記録され、dc.sendは一切発生しない', () => {
    const { sandbox, fireLatestTimer, dcSends, timelineTexts } = buildSandbox();

    sandbox.toolContinuationTraceActive = true;
    sandbox.toolContinuationTraceCallId = 'call_abc123';
    sandbox.armToolContinuationResponseWatchdog('call_abc123');

    // T7が一度も来ないままタイムアウト時刻に到達
    fireLatestTimer();

    assert.strictEqual(dcSends().length, 0, 'the watchdog must never call dc.send under any circumstance');
    const lines = timelineTexts().filter(t => t.includes('TOOL_CONTINUATION_WATCHDOG_NO_RESPONSE_CREATED_AFTER_T6'));
    assert.strictEqual(lines.length, 1, 'exactly one diagnostic line must be recorded when T7 never arrives');
    assert.ok(lines[0].includes('diagnostic_only_no_action_taken=true'), 'the log line must explicitly self-document that no action was taken');
});

test('TOOL WATCHDOG) 正常系: T7(response.created)が届いてcancelされた場合、タイムアウトしても何も記録されない', () => {
    const { sandbox, fireLatestTimer, timelineTexts } = buildSandbox();

    sandbox.toolContinuationTraceActive = true;
    sandbox.toolContinuationTraceCallId = 'call_xyz789';
    sandbox.armToolContinuationResponseWatchdog('call_xyz789');

    // T7相当: 正常にresponse.createdが届き、キャンセルされる
    sandbox.cancelToolContinuationResponseWatchdog('t7_response_created_arrived');

    // タイマー自体は既にclearTimeout済みのはずなので、fireできない（pending無し）ことも確認
    assert.throws(() => fireLatestTimer(), 'no pending timer should remain after cancel');
    assert.strictEqual(timelineTexts().filter(t => t.includes('TOOL_CONTINUATION_WATCHDOG')).length, 0,
        'no watchdog diagnostic line should ever appear when the response arrived normally');
});

test('TOOL WATCHDOG) 別のcall_idのトレースに切り替わっていた場合、古いwatchdogは誤発火しない（call_id相関ガード）', () => {
    const { sandbox, fireLatestTimer, timelineTexts } = buildSandbox();

    sandbox.toolContinuationTraceActive = true;
    sandbox.toolContinuationTraceCallId = 'call_old';
    sandbox.armToolContinuationResponseWatchdog('call_old');

    // 新しいTool呼び出しが始まり、call_idが切り替わった（旧watchdogはre-armされずそのまま）
    sandbox.toolContinuationTraceCallId = 'call_new';

    fireLatestTimer();

    assert.strictEqual(timelineTexts().filter(t => t.includes('TOOL_CONTINUATION_WATCHDOG')).length, 0,
        'a watchdog armed for an old call_id must not fire a false positive once the trace has moved on to a different call_id');
});

// =======================================================================
// PLAIN TURN RESPONSE WATCHDOG
// =======================================================================

test('PLAIN WATCHDOG) 異常系: TURN_RESPONSE_REQUESTED後、TURN_RESPONSE_CREATEDが一度も記録されないままタイムアウトすると、診断専用の1行が記録され、dc.sendは一切発生しない', () => {
    const { sandbox, fireLatestTimer, dcSends, timelineTexts } = buildSandbox();

    sandbox.turnLatencyTraceId = 'T1';
    sandbox.turnLatencyTraceMarkersSeen = {}; // TURN_RESPONSE_CREATEDはまだ記録されていない
    sandbox.armPlainTurnResponseWatchdog('T1');

    fireLatestTimer();

    assert.strictEqual(dcSends().length, 0, 'the watchdog must never call dc.send under any circumstance');
    const lines = timelineTexts().filter(t => t.includes('PLAIN_TURN_WATCHDOG_NO_RESPONSE_CREATED_AFTER_COMMIT'));
    assert.strictEqual(lines.length, 1, 'exactly one diagnostic line must be recorded when TURN_RESPONSE_CREATED never arrives');
    assert.ok(lines[0].includes('diagnostic_only_no_action_taken=true'));
});

test('PLAIN WATCHDOG) 正常系: TURN_RESPONSE_CREATEDが先に記録され、watchdogがcancelされた場合、タイムアウトしても何も記録されない', () => {
    const { sandbox, fireLatestTimer, timelineTexts } = buildSandbox();

    sandbox.turnLatencyTraceId = 'T2';
    sandbox.armPlainTurnResponseWatchdog('T2');

    // response.createdハンドラ相当: markerを立ててからcancelする
    sandbox.turnLatencyTraceMarkersSeen['TURN_RESPONSE_CREATED'] = true;
    sandbox.cancelPlainTurnResponseWatchdog('turn_response_created_arrived');

    assert.throws(() => fireLatestTimer(), 'no pending timer should remain after cancel');
    assert.strictEqual(timelineTexts().filter(t => t.includes('PLAIN_TURN_WATCHDOG')).length, 0);
});

test('PLAIN WATCHDOG) ターンが別のturnIdへ切り替わっていた場合（前ターンがabandonedになった等）、古いwatchdogは誤発火しない', () => {
    const { sandbox, fireLatestTimer, timelineTexts } = buildSandbox();

    sandbox.turnLatencyTraceId = 'T3';
    sandbox.armPlainTurnResponseWatchdog('T3');

    // 新しいターンが始まり、turnIdが切り替わった
    sandbox.turnLatencyTraceId = 'T4';
    sandbox.turnLatencyTraceMarkersSeen = {};

    fireLatestTimer();

    assert.strictEqual(timelineTexts().filter(t => t.includes('PLAIN_TURN_WATCHDOG')).length, 0,
        'a watchdog armed for an old turnId must not fire once the trace has moved on to a new turn');
});

// =======================================================================
// REGRESSION: 新規追加が既存の送信ロジックに一切触れていないこと
// =======================================================================

test('REGRESSION) dc.send()呼び出し箇所は今回も増えていない（両watchdogは診断ログのみで、Realtime APIへの新規送信を一切追加していない。既存9箇所のまま）', () => {
    const actualCallLines = SRC.split('\n').filter(line => line.trim().startsWith('dc.send('));
    assert.strictEqual(actualCallLines.length, 9, 'dc.send() call-site count must remain 9 — FAST TURN HOTFIX 4 adds zero new Realtime API sends (diagnostic-only)');
});

test('LIVE-WIRING) armToolContinuationResponseWatchdogはT6送信成功時（else節）にのみ呼ばれ、送信スキップ時には呼ばれない', () => {
    const idx = SRC.indexOf("pushToolContinuationTrace('T6_' + (toolContinuationResponseCreateSent");
    assert.notStrictEqual(idx, -1);
    // 実測: armToolContinuationResponseWatchdog呼び出しはアンカーから約590文字
    // 後ろにあるため、ウィンドウを700→900へ拡張（測定値+余裕分）。
    const block = SRC.slice(idx, idx + 900);
    assert.ok(block.includes('} else {') && block.includes('armToolContinuationResponseWatchdog(toolContinuationTraceCallId)'),
        'the watchdog must be armed only in the branch where response.create was actually sent (dc.send succeeded)');
});

test('LIVE-WIRING) cancelToolContinuationResponseWatchdogがT7(response.created)・T10(response.done)・T_ERRORの各箇所で呼ばれている', () => {
    assert.ok(/pushToolContinuationTrace\('T7_CONTINUATION_RESPONSE_CREATED'\);\s*\n[\s\S]{0,300}cancelToolContinuationResponseWatchdog/.test(SRC), 'T7 must cancel the watchdog');
    assert.ok(/pushToolContinuationTrace\('T10_CONTINUATION_RESPONSE_DONE[\s\S]{0,300}cancelToolContinuationResponseWatchdog/.test(SRC), 'T10 must cancel the watchdog');
    assert.ok(/T_ERROR_REALTIME_ERROR[\s\S]{0,300}cancelToolContinuationResponseWatchdog/.test(SRC), 'the realtime error handler must cancel the watchdog');
});

test('LIVE-WIRING) armPlainTurnResponseWatchdogがTURN_RESPONSE_REQUESTED記録の直後にあり、cancelPlainTurnResponseWatchdogがTURN_RESPONSE_CREATED記録の直後にある', () => {
    const armIdx = SRC.indexOf("pushTurnLatencyTrace('TURN_RESPONSE_REQUESTED'");
    assert.notStrictEqual(armIdx, -1);
    const armBlock = SRC.slice(armIdx, armIdx + 400);
    assert.ok(armBlock.includes('armPlainTurnResponseWatchdog(turnLatencyTraceId)'));

    const cancelIdx = SRC.indexOf("pushTurnLatencyTrace('TURN_RESPONSE_CREATED');");
    assert.notStrictEqual(cancelIdx, -1);
    const cancelBlock = SRC.slice(cancelIdx, cancelIdx + 300);
    assert.ok(cancelBlock.includes('cancelPlainTurnResponseWatchdog'));
});

test('REGRESSION) 両watchdogのタイムアウト処理は setTimeout のコールバック内でのみ pushTimelineEvent/pushToolContinuationTrace/pushTurnLatencyTrace を呼び、response.create/commit等のRealtime制御イベントを一切送信しない（ソース上の直接確認）', () => {
    const armToolSrc = extractFunction(SRC, 'function armToolContinuationResponseWatchdog(forCallId) {');
    const armPlainSrc = extractFunction(SRC, 'function armPlainTurnResponseWatchdog(forTurnId) {');
    assert.ok(!armToolSrc.includes('dc.send') && !armToolSrc.includes('sendResponseCreate'));
    assert.ok(!armPlainSrc.includes('dc.send') && !armPlainSrc.includes('sendResponseCreate'));
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
if (failed > 0) process.exit(1);
