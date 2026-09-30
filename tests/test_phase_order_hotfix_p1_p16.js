'use strict';

/**
 * RECEPTRA — PHASE ORDER HOTFIX（実装編）
 * NAME確認 → ROUTING用件質問 → classify_intent の順序を保証する修正の
 * behavioral regression test（P1〜P16。ユーザー指定のSTEP11チェックリスト）。
 *
 * 根本原因（詳細は本ラウンドの最終報告を参照）:
 *   sendRealtimePhaseSessionUpdate('routing', ...)が送信された「瞬間」に、
 *   まだお客様が一度も発話していないにもかかわらず
 *   pendingPhaseTransitionTarget='legacy_full'を無条件に予約していた。
 *   ROUTING突入直後の強制follow-up応答（「本日はどのようなご用件でしょうか？」）
 *   自身のresponse.doneでこの予約が消費されてしまい、お客様の回答を一度も
 *   受け取らないままlegacy_fullへ脱出していた。
 *
 * 修正（選択肢A）: legacy_fullへのフォールバックの予約タイミングを、
 * ROUTING突入時点から「ROUTINGでお客様の実際の新しい発話を受け取り
 * （routingHasReceivedUserTurn===true）、かつその応答がclassify_intentを
 * 呼ばずに終わった場合」へ遅らせた。新規state（routingHasReceivedUserTurn、
 * boolean 1個のみ）を追加し、既存のresponse.done消費ガード・
 * armPhaseTransitionAfterResponse・sendRealtimePhaseSessionUpdateの本体は
 * 変更していない。
 *
 * 本ファイルは、既存の3ファイル
 * （test_hotfix2_routing_legacy_full_forced_continuation.js の R1〜R4b,
 *   test_realtime_phase1_transition.js の HOTFIX-PENDING-CLEARED,
 *   test_realtime_phase2_reservation_callback_transition.js の W/X/X2a/X2b）
 * で既に検証済みの項目（P3・P4・P10・P11相当）を重複させず、それら3ファイル
 * ではまだ直接カバーされていなかった項目（P12・P14の実配線確認）と、
 * production側のLIVE-WIRING（input_audio_buffer.committedハンドラが実際に
 * routingHasReceivedUserTurnをtrueにする配線、新しい通話開始時のreset配線）
 * のみを新規に追加する。P1・P2・P5・P6・P7・P8・P9・P13・P15・P16は
 * prompt文言を一切変更していないため（本ラウンドのSTEP10で禁止）、既存の
 * smoke_test_hotfix3_name_reconfirm_loop_and_correction.py（NAME correction）・
 * smoke_test_human_handoff_wording.py 等のCALLBACK終話文言テスト・
 * classify_intent Tool schema自体（app/services/realtime_voice_ai.py、
 * 今回無変更）でカバー済みであることを、最終報告側で個別に確認する。
 *
 * 実行: node tests/test_phase_order_hotfix_p1_p16.js
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ENGINE_JS_PATH = path.join(__dirname, '..', 'frontend', 'public', 'js', 'realtime-voice-engine.js');
const SRC = fs.readFileSync(ENGINE_JS_PATH, 'utf8');

function extractFunctionSource(src, fnName, fromIndex) {
    const asyncToken = 'async function ' + fnName + '(';
    let startIdx = src.indexOf(asyncToken, fromIndex || 0);
    if (startIdx !== -1) {
        const braceStart = src.indexOf('{', startIdx);
        let depth = 0, i = braceStart;
        for (; i < src.length; i++) {
            if (src[i] === '{') depth++;
            else if (src[i] === '}') { depth--; if (depth === 0) { i++; break; } }
        }
        return src.slice(startIdx, i);
    }
    const startToken = 'function ' + fnName + '(';
    startIdx = src.indexOf(startToken, fromIndex || 0);
    if (startIdx === -1) throw new Error('function not found in source: ' + fnName);
    const braceStart = src.indexOf('{', startIdx);
    let depth = 0, i = braceStart;
    for (; i < src.length; i++) {
        if (src[i] === '{') depth++;
        else if (src[i] === '}') { depth--; if (depth === 0) { i++; break; } }
    }
    return src.slice(startIdx, i);
}

function extractBlock(src, signature, fromIndex) {
    const idx = src.indexOf(signature, fromIndex || 0);
    assert.notStrictEqual(idx, -1, 'signature not found: ' + signature);
    let depth = 0, i = idx, started = false;
    for (; i < src.length; i++) {
        if (src[i] === '{') { depth++; started = true; }
        else if (src[i] === '}') { depth--; if (started && depth === 0) { i++; break; } }
    }
    return src.slice(idx, i);
}

const FN = {
    armPhaseTransitionAfterResponse: extractFunctionSource(SRC, 'armPhaseTransitionAfterResponse'),
    sendRealtimePhaseSessionUpdate: extractFunctionSource(SRC, 'sendRealtimePhaseSessionUpdate'),
    callClassifyIntentTool: extractFunctionSource(SRC, 'callClassifyIntentTool'),
};

const RESPONSE_DONE_PHASE_BLOCK_SRC = extractBlock(
    SRC,
    'if (pendingPhaseTransitionTarget !== null && !phaseTransitionInProgress) {'
);
const SESSION_UPDATED_PHASE_BLOCK_SRC = extractBlock(
    SRC,
    "if (type === 'session.updated' && phaseTransitionInProgress) {"
);
const LEGACY_FULL_FALLBACK_ARM_BLOCK_SRC = extractBlock(
    SRC,
    "if (currentRealtimePhase === 'routing'\n"
    + "                    && routingHasReceivedUserTurn\n"
    + "                    && !responseHasFunctionCall\n"
    + "                    && pendingPhaseTransitionTarget === null\n"
    + "                    && !phaseTransitionInProgress) {"
);
// input_audio_buffer.committedハンドラ全体（LIVE-WIRING検証用）。
// このハンドラはlastSpeechStoppedAt等、多数の既存モジュールスコープ変数に
// 依存しているため、他のLIVE-WIRINGテスト（test_noisy_environment_turn_
// boundary.jsのJ2等）と同じく、vm実行ではなく固定オフセット窓の静的文字列
// 照合で配線を確認する（依存関係を全て模擬するより安全で、既存の確立された
// パターンに従う）。
const USER_AUDIO_COMMITTED_ANCHOR = "} else if (type === 'input_audio_buffer.committed') {";

function buildSandbox(overrides) {
    const timelineEvents = [];
    const consoleLogs = [];
    const dcSendCalls = [];
    const sendResponseCreateCalls = [];
    const pushTurnLatencyTraceCalls = [];
    const armPlainTurnResponseWatchdogCalls = [];
    const context = {
        performance: { now: () => Date.now() },
        pushTimelineEvent: (text) => { timelineEvents.push(text); },
        console: { log: (...args) => { consoleLogs.push(args); } },
        callStartedAt: Date.now() - 1234,
        dc: { readyState: 'open', send: (payload) => { dcSendCalls.push(payload); } },
        sendResponseCreate: (reason) => { sendResponseCreateCalls.push(reason); return true; },
        pushTurnLatencyTrace: (...args) => { pushTurnLatencyTraceCalls.push(args); },
        armPlainTurnResponseWatchdog: (...args) => { armPlainTurnResponseWatchdogCalls.push(args); },
        turnLatencyTraceId: null,
        debugMode: false,
        msg: {},
    };
    const state = Object.assign({
        currentRealtimePhase: 'routing',
        realtimePhaseContexts: {
            name: { instructions: 'NAME_PHASE_INSTRUCTIONS_STUB', tools: [] },
            routing: { instructions: 'ROUTING_PHASE_INSTRUCTIONS_STUB', tools: [{ name: 'classify_intent' }] },
            reservation: { instructions: 'RESERVATION_PHASE_INSTRUCTIONS_STUB', tools: [{ name: 'check_availability' }, { name: 'create_reservation' }] },
            callback: { instructions: 'CALLBACK_PHASE_INSTRUCTIONS_STUB', tools: [{ name: 'request_callback' }] },
            legacy_full: { instructions: 'LEGACY_FULL_INSTRUCTIONS_STUB', tools: [{ name: 'check_availability' }] },
        },
        phaseTransitionInProgress: false,
        pendingPhaseTransitionTarget: null,
        pendingPhaseTransitionForceFollowUp: null,
        pendingPhaseTransitionReasonForFollowUp: null,
        phaseTransitionSessionUpdateSentAt: null,
        phaseTransitionInstructionsChars: null,
        phaseTransitionToolCount: null,
        expectedAnswerType: 'NONE',
        // DUPLICATE RESPONSE HOTFIX（今回追加）: response.doneの
        // phase-transition consumeブロックが、reservation/callback
        // ターゲットについてもlastResponseTranscriptWasProcessNarrationOnly/
        // lastResponseTranscriptWasIncompleteAiTurnを参照するようになった
        // ため、既存のsandboxデフォルトにも追加する（本番側のデフォルト値
        // falseと同じ。参照追加のみで、これらのフラグ自体の計算ロジックは
        // このテストファイルの対象外＝classifyExpectedAnswerType()側で
        // 既に別途カバーされている）。
        lastResponseTranscriptWasProcessNarrationOnly: false,
        lastResponseTranscriptWasIncompleteAiTurn: false,
        routingHasReceivedUserTurn: false,
        responseHasFunctionCall: false,
    }, overrides || {});
    Object.assign(context, state);
    vm.createContext(context);
    vm.runInContext(Object.values(FN).join('\n\n'), context);
    return { ctx: context, timelineEvents, consoleLogs, dcSendCalls, sendResponseCreateCalls, pushTurnLatencyTraceCalls };
}

let passed = 0;
const _queuedTests = [];
function test(name, fn) { _queuedTests.push({ name, fn }); }

async function _runAllTests() {
    for (const { name, fn } of _queuedTests) {
        try {
            await fn();
            passed++;
            console.log('PASS: ' + name);
        } catch (e) {
            console.error('FAIL: ' + name);
            console.error(e);
            process.exitCode = 1;
        }
    }
}

// =======================================================================
// P3 (最重要・再確認): NAME→ROUTING開始時点ではlegacy_full fallbackが
// armされていない。
// （test_hotfix2_routing_legacy_full_forced_continuation.js R1・
//   test_realtime_phase2_reservation_callback_transition.js X2aと同趣旨。
//   本ファイルでも独立に再確認する。）
// =======================================================================
test('P3: entering ROUTING does not arm the legacy_full fallback', () => {
    const { ctx } = buildSandbox({ currentRealtimePhase: 'name' });
    vm.runInContext("sendRealtimePhaseSessionUpdate('routing', 'response_done_boundary', false)", ctx);
    assert.strictEqual(ctx.pendingPhaseTransitionTarget, null);
    assert.strictEqual(ctx.routingHasReceivedUserTurn, false);
});

// =======================================================================
// P4: ROUTING開始後、ユーザーの新規発話前にはlegacy_fullへ遷移できない
// （強制follow-up応答自身のresponse.doneを含む）。
// =======================================================================
test('P4: no transition to legacy_full is possible before a genuine new user utterance is received in ROUTING, even across the forced follow-up response', () => {
    const { ctx, sendResponseCreateCalls } = buildSandbox({ currentRealtimePhase: 'name' });
    vm.runInContext("sendRealtimePhaseSessionUpdate('routing', 'response_done_boundary', true)", ctx);
    vm.runInContext("type = 'session.updated'; " + SESSION_UPDATED_PHASE_BLOCK_SRC, Object.assign(ctx, { type: null }));
    // forceFollowUp=trueのため、ここで強制follow-up応答（PURPOSE質問）が
    // 1回だけ生成される。まだお客様は一言も発話していない。
    assert.strictEqual(sendResponseCreateCalls.length, 1);
    assert.strictEqual(ctx.routingHasReceivedUserTurn, false, 'the forced follow-up response itself must not count as a genuine user turn');
    // この強制follow-up応答自身のresponse.doneに到達しても、legacy_fullへは
    // 絶対に落ちない。
    vm.runInContext(LEGACY_FULL_FALLBACK_ARM_BLOCK_SRC, ctx);
    assert.strictEqual(ctx.pendingPhaseTransitionTarget, null);
    vm.runInContext(RESPONSE_DONE_PHASE_BLOCK_SRC, ctx);
    assert.strictEqual(ctx.currentRealtimePhase, 'routing', 'must still be waiting in routing for the customer\'s real answer');
});

// =======================================================================
// P10 / P11: classify_intent成功時はlegacy_fullへ行かない。本当に分類
// 不能な場合のみ既存のlegacy_full fallbackが生きている（安全網の削除禁止）。
// （test_realtime_phase2_reservation_callback_transition.js W/X/X2bと同趣旨。
//   本ファイルでも独立に再確認する。）
// =======================================================================
test('P10: once classify_intent succeeds on the customer\'s real answer, legacy_full is never armed', () => {
    const { ctx } = buildSandbox({ currentRealtimePhase: 'routing', routingHasReceivedUserTurn: true, responseHasFunctionCall: true });
    return vm.runInContext('callClassifyIntentTool({ intent: "callback" })', ctx).then(() => {
        vm.runInContext(LEGACY_FULL_FALLBACK_ARM_BLOCK_SRC, ctx);
        assert.strictEqual(ctx.pendingPhaseTransitionTarget, 'callback', 'must remain armed to callback, not be overwritten by the fallback arm block');
        vm.runInContext(RESPONSE_DONE_PHASE_BLOCK_SRC, ctx);
        assert.strictEqual(ctx.currentRealtimePhase, 'callback');
    });
});

test('P11: the real OTHER/UNKNOWN fallback is preserved — once the customer has genuinely spoken and classification truly failed, legacy_full still fires', () => {
    const { ctx } = buildSandbox({ currentRealtimePhase: 'routing', routingHasReceivedUserTurn: true, responseHasFunctionCall: false });
    vm.runInContext(LEGACY_FULL_FALLBACK_ARM_BLOCK_SRC, ctx);
    assert.strictEqual(ctx.pendingPhaseTransitionTarget, 'legacy_full');
    vm.runInContext(RESPONSE_DONE_PHASE_BLOCK_SRC, ctx);
    assert.strictEqual(ctx.currentRealtimePhase, 'legacy_full', 'the safety net itself must not be deleted — it must still work once genuinely warranted');
});

// =======================================================================
// P12: 1回のPURPOSE回答（classify_intent成功）に対して、不要な複数
// response.createを生成しない（正しい継続follow-upはちょうど1回のみ）。
// =======================================================================
test('P12: classify_intent success generates exactly one follow-up response.create for the reservation/callback transition itself (no duplicate/extra response.create for the same user turn)', () => {
    const { ctx, sendResponseCreateCalls } = buildSandbox({ currentRealtimePhase: 'routing', routingHasReceivedUserTurn: true, responseHasFunctionCall: true });
    return vm.runInContext('callClassifyIntentTool({ intent: "reservation" })', ctx).then(() => {
        vm.runInContext(LEGACY_FULL_FALLBACK_ARM_BLOCK_SRC, ctx);
        vm.runInContext(RESPONSE_DONE_PHASE_BLOCK_SRC, ctx);
        assert.strictEqual(sendResponseCreateCalls.length, 0, 'no response.create yet before session.updated for the reservation transition arrives');
        vm.runInContext("type = 'session.updated'; " + SESSION_UPDATED_PHASE_BLOCK_SRC, Object.assign(ctx, { type: null }));
        assert.strictEqual(sendResponseCreateCalls.length, 1, 'exactly one follow-up response.create for the reservation transition (forceFollowUp=true is unconditional for non-routing targets, unchanged)');
    });
});

// =======================================================================
// P14: 次の通話でrouting stateがreset される（LIVE-WIRING）。
// =======================================================================
test('P14: LIVE-WIRING) the new-call reset block resets routingHasReceivedUserTurn (no stale state leaking into the next call)', () => {
    const anchor = 'nameCaptureShortTurnRejections = 0;';
    const first = SRC.indexOf(anchor);
    assert.notStrictEqual(first, -1);
    const second = SRC.indexOf(anchor, first + 1);
    assert.notStrictEqual(second, -1, 'expected two occurrences: the let-declaration and the new-call reset block');
    const block = SRC.slice(second, second + 900);
    assert.ok(block.includes('routingHasReceivedUserTurn = false;'), 'the new-call reset block must reset routingHasReceivedUserTurn for every new call');
});

// =======================================================================
// LIVE-WIRING（production配線の確認）: input_audio_buffer.committedハンドラが
// 実際にROUTING在中でroutingHasReceivedUserTurnをtrueにしていること。
// このファイル以外のテストは、この配線自体を手作業で模擬しているだけなので、
// 実ソースのハンドラそのものをここで実行して確認する。
// =======================================================================
test('LIVE-WIRING: input_audio_buffer.committed sets routingHasReceivedUserTurn=true only while in ROUTING phase (static wiring check)', () => {
    const idx = SRC.indexOf(USER_AUDIO_COMMITTED_ANCHOR);
    assert.notStrictEqual(idx, -1);
    // 実測: PHASE_ORDER_ROUTING_USER_TURN_RECEIVEDマーカー本体までのオフセットが
    // 956文字のため、ウィンドウを900→1300へ拡張（node -eでの実測+余裕分）。
    const block = SRC.slice(idx, idx + 1300);
    assert.ok(block.includes("pushTimelineEvent('USER_AUDIO_BUFFER_COMMITTED');"), 'existing marker must still be the first thing recorded');
    assert.ok(
        /if\s*\(currentRealtimePhase === 'routing'\)\s*\{\s*routingHasReceivedUserTurn = true;/.test(block),
        'the handler must set routingHasReceivedUserTurn=true only when currentRealtimePhase is routing'
    );
});

test('LIVE-WIRING: the routingHasReceivedUserTurn wiring is placed before the pre-existing FAST TURN HOTFIX 3 turn-latency tracing (does not disturb existing diagnostics ordering)', () => {
    const idx = SRC.indexOf(USER_AUDIO_COMMITTED_ANCHOR);
    assert.notStrictEqual(idx, -1);
    const routingWireIdx = SRC.indexOf('routingHasReceivedUserTurn = true;', idx);
    const turnCommittedIdx = SRC.indexOf("pushTurnLatencyTrace('TURN_COMMITTED')", idx);
    assert.notStrictEqual(routingWireIdx, -1);
    assert.notStrictEqual(turnCommittedIdx, -1);
    assert.ok(routingWireIdx < turnCommittedIdx, 'the new wiring must not reorder or remove the existing TURN_COMMITTED tracing');
});

// =======================================================================
// PII: 新規診断マーカーがPIIを一切含まないことの確認（フォールバック
// enable/arm・ユーザー発話受信の各マーカーとも、固定文言のみ）。
// =======================================================================
test('PII: new PHASE_ORDER_ROUTING_USER_TURN_RECEIVED marker is a fixed literal string with no interpolated transcript/name/phone content (static source check)', () => {
    const idx = SRC.indexOf(USER_AUDIO_COMMITTED_ANCHOR);
    assert.notStrictEqual(idx, -1);
    const block = SRC.slice(idx, idx + 1300);
    assert.ok(block.includes("'PHASE_ORDER_ROUTING_USER_TURN_RECEIVED (phase=routing)'"),
        'the marker must be a fixed string literal (no string concatenation of transcript/name/phone content)');
});

test('PII: new PHASE_ORDER_LEGACY_FULL_FALLBACK_ARMED marker contains only a fixed reason label, never transcript content', () => {
    const { ctx: ctx2, timelineEvents: te2 } = buildSandbox({ currentRealtimePhase: 'routing', routingHasReceivedUserTurn: true, responseHasFunctionCall: false });
    vm.runInContext(LEGACY_FULL_FALLBACK_ARM_BLOCK_SRC, ctx2);
    const fallbackLine = te2.find((e) => e.includes('PHASE_ORDER_LEGACY_FULL_FALLBACK_ARMED'));
    assert.ok(fallbackLine);
    assert.strictEqual(fallbackLine, 'PHASE_ORDER_LEGACY_FULL_FALLBACK_ARMED (reason=routing_answer_unclassified)');
});

_runAllTests().then(() => {
    if (process.exitCode !== 1) {
        console.log('\n' + passed + ' test(s) passed.');
        console.log('\n=== ALL test_phase_order_hotfix_p1_p16.js CHECKS PASSED ===');
    } else {
        console.error('\nSOME test_phase_order_hotfix_p1_p16.js CHECKS FAILED');
    }
});
