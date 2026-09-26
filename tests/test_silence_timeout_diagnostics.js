'use strict';

// PHASE O5.6 — Silence Timeout Diagnostics regression tests
//
// 背景: 実機テストで「2時でお願いします」→約30秒沈黙→AI「何名様ですか？」→
// 会話途中で通話終了、という重大症状が報告された。緊急Auditの結果、既存
// （PHASE O5.6以前から存在する）Silence Timeoutの状態機械
// （silenceState: 'idle'→'waiting'→'warned'→'goodbye'）が、'warned'状態へ
// 入った後に正常な会話応答が発生しても解除されない可能性がある、という
// 未確定の仮説が最有力候補として浮上した。
//
// 本フェーズはその挙動を一切変更せず（SILENCE_TIMEOUT_MS/
// SILENCE_WARNING_GRACE_MS/状態遷移条件/endCall()の呼び出し条件は完全に
// 無変更）、実機ログから因果関係を確定させるための診断ログ
// （CALL_END_DIAG/SILENCE_STATE_DIAG/SILENCE_TIMER_*/SILENCE_WARNING_GRACE_*/
// RESPONSE_CREATE_DIAG/RESPONSE_CREATE_CORRELATION/PC_STATE_DIAG等）だけを
// 追加した。
//
// このテストは、追加した診断ログが (a) 実際に正しいタイミング・内容で
// 発火すること、(b) 診断ログの追加によって既存のsilenceState挙動・
// タイマーの発火条件・定数値が一切変わっていないこと、(c) 個人情報を
// 一切含まないこと、を検証する。tests/test_phone_forced_commit.js と
// 同じ方式（vm上でソースを直接実行し、setTimeout/clearTimeoutを手動発火
// 可能なモックに差し替える）を採用する。
//
// 実行: node tests/test_silence_timeout_diagnostics.js

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ENGINE_JS_PATH = path.join(__dirname, '..', 'frontend', 'public', 'js', 'realtime-voice-engine.js');
const SRC = fs.readFileSync(ENGINE_JS_PATH, 'utf8');

function extractFunctionSource(src, fnName, isAsync) {
    const startToken = (isAsync ? 'async function ' : 'function ') + fnName + '(';
    const startIdx = src.indexOf(startToken);
    if (startIdx === -1) throw new Error('function not found in source: ' + fnName);
    const braceStart = src.indexOf('{', startIdx);
    let depth = 0;
    let i = braceStart;
    for (; i < src.length; i++) {
        if (src[i] === '{') depth++;
        else if (src[i] === '}') {
            depth--;
            if (depth === 0) { i++; break; }
        }
    }
    return src.slice(startIdx, i);
}

function extractConstSource(src, constName) {
    const re = new RegExp('const ' + constName + '\\s*=\\s*[^;]+;');
    const m = src.match(re);
    if (!m) throw new Error('const not found in source: ' + constName);
    return m[0];
}

const FN = {
    msSince: extractFunctionSource(SRC, 'msSince', false),
    categorizeResponseReason: extractFunctionSource(SRC, 'categorizeResponseReason', false),
    setSilenceState: extractFunctionSource(SRC, 'setSilenceState', false),
    pushPcStateDiag: extractFunctionSource(SRC, 'pushPcStateDiag', false),
    startSilenceTimerIfNeeded: extractFunctionSource(SRC, 'startSilenceTimerIfNeeded', false),
    resetSilenceTimer: extractFunctionSource(SRC, 'resetSilenceTimer', false),
    triggerSilenceWarning: extractFunctionSource(SRC, 'triggerSilenceWarning', false),
    triggerSilenceFinalGoodbye: extractFunctionSource(SRC, 'triggerSilenceFinalGoodbye', false),
    sendResponseCreate: extractFunctionSource(SRC, 'sendResponseCreate', false),
    isStaleCallEvent: extractFunctionSource(SRC, 'isStaleCallEvent', false),
};

const SILENCE_TIMEOUT_MS_EXPR = extractConstSource(SRC, 'SILENCE_TIMEOUT_MS');
const SILENCE_WARNING_GRACE_MS_EXPR = extractConstSource(SRC, 'SILENCE_WARNING_GRACE_MS');
const SILENCE_WARNING_TEXT_EXPR = extractConstSource(SRC, 'SILENCE_WARNING_TEXT');
const SILENCE_GOODBYE_TEXT_EXPR = extractConstSource(SRC, 'SILENCE_GOODBYE_TEXT');

function buildSandbox(overrides) {
    const events = [];
    const timers = new Map();
    let nextTimerId = 1;

    const context = {
        console: console,
        performance: { now: () => Date.now() },
    };
    const state = Object.assign({
        pushTimelineEvent: (text) => { events.push(text); },
        logEvent: () => {},
        updateAudioDiagnosticsPanel: () => {},
        // silenceState機械そのもの
        silenceState: 'idle',
        silenceTimerId: null,
        silenceWarningTimerId: null,
        pendingSilenceGoodbyeHangup: false,
        // O5.6診断専用状態
        silenceStateEnteredAt: null,
        silenceTimerArmedAt: null,
        silenceWarningGraceArmedAt: null,
        lastResponseCreateReason: null,
        responseCreateDiagSeq: 0,
        lastResponseReasonCategoryForDiag: 'unknown',
        // 発話タイミング（既存変数の再利用）
        lastSpeechStartedAt: null,
        lastSpeechStoppedAt: null,
        responseState: 'idle',
        aiAudioOutputActive: false,
        // PC/DC/mic（pushPcStateDiag用）
        pc: { connectionState: 'connected', iceConnectionState: 'connected' },
        dc: { readyState: 'open', sent: [], send(payload) { this.sent.push(JSON.parse(payload)); } },
        localStream: null,
        // isStaleCallEvent用
        ended: false,
        callGeneration: 1,
    }, overrides || {});
    Object.assign(context, state);

    context.setTimeout = (fn, delay) => {
        const id = nextTimerId++;
        timers.set(id, { fn, delay });
        return id;
    };
    context.clearTimeout = (id) => { timers.delete(id); };

    vm.createContext(context);
    vm.runInContext(
        SILENCE_TIMEOUT_MS_EXPR + '\n' +
        SILENCE_WARNING_GRACE_MS_EXPR + '\n' +
        SILENCE_WARNING_TEXT_EXPR + '\n' +
        SILENCE_GOODBYE_TEXT_EXPR + '\n' +
        Object.values(FN).join('\n\n'),
        context
    );

    return {
        ctx: context,
        events,
        fireTimer: (id) => { const t = timers.get(id); timers.delete(id); if (t) t.fn(); },
        timerDelay: (id) => { const t = timers.get(id); return t ? t.delay : null; },
        timerCount: () => timers.size,
        activeTimerIds: () => Array.from(timers.keys()),
    };
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
        console.log('    ' + (e && e.stack ? e.stack.split('\n').slice(0, 6).join('\n    ') : e));
    }
}

console.log('PHASE O5.6 Silence Timeout Diagnostics regression tests');
console.log('source: ' + ENGINE_JS_PATH);
console.log('');

// ===== TIMER-30S / GRACE-8S: 定数値が一切変更されていないこと =====

test('TIMER-30S: SILENCE_TIMEOUT_MS is unchanged at 30000', () => {
    // eslint-disable-next-line no-eval
    const value = eval('(' + SILENCE_TIMEOUT_MS_EXPR.replace(/^const SILENCE_TIMEOUT_MS\s*=\s*/, '').replace(/;\s*$/, '') + ')');
    assert.strictEqual(value, 30000);
});

test('GRACE-8S: SILENCE_WARNING_GRACE_MS is unchanged at 8000', () => {
    // eslint-disable-next-line no-eval
    const value = eval('(' + SILENCE_WARNING_GRACE_MS_EXPR.replace(/^const SILENCE_WARNING_GRACE_MS\s*=\s*/, '').replace(/;\s*$/, '') + ')');
    assert.strictEqual(value, 8000);
});

// ===== STATE-DIAG: silenceState遷移のたびにSILENCE_STATE_DIAGが記録される =====

test('STATE-DIAG: idle -> waiting transition is recorded with correct from/to', () => {
    const { ctx, events, timerCount, timerDelay } = buildSandbox({});
    vm.runInContext('startSilenceTimerIfNeeded(1, "ai_waiting_for_user")', ctx);
    assert.strictEqual(ctx.silenceState, 'waiting', 'silenceState must actually become waiting (behavior unchanged)');
    assert.strictEqual(timerCount(), 1, 'exactly one silence timer must be armed');
    assert.strictEqual(timerDelay(ctx.silenceTimerId), 30000, 'the armed timer must still use the unmodified 30000ms duration');
    assert.ok(events.some((e) => e.includes('SILENCE_STATE_DIAG') && e.includes('from=idle') && e.includes('to=waiting')));
    assert.ok(events.some((e) => e.includes('SILENCE_TIMER_ARMED') && e.includes('durationMs=30000')));
});

test('STATE-DIAG: waiting -> warned -> goodbye sequence is fully recorded, matching the leading hypothesis path', () => {
    const { ctx, events, fireTimer } = buildSandbox({});
    vm.runInContext('startSilenceTimerIfNeeded(1, "ai_waiting_for_user")', ctx);
    fireTimer(ctx.silenceTimerId); // 30秒timer発火 -> triggerSilenceWarning
    assert.strictEqual(ctx.silenceState, 'warned');
    assert.ok(events.some((e) => e.includes('SILENCE_TIMER_FIRED') && e.includes('expectedDurationMs=30000')));
    assert.ok(events.some((e) => e.includes('SILENCE_STATE_DIAG') && e.includes('from=waiting') && e.includes('to=warned')));
    assert.ok(events.some((e) => e.includes('SILENCE_WARNING_GRACE_ARMED') && e.includes('durationMs=8000')));

    fireTimer(ctx.silenceWarningTimerId); // 8秒grace発火 -> triggerSilenceFinalGoodbye
    assert.strictEqual(ctx.silenceState, 'goodbye');
    assert.strictEqual(ctx.pendingSilenceGoodbyeHangup, true, 'existing goodbye-hangup behavior must be unaffected');
    assert.ok(events.some((e) => e.includes('SILENCE_WARNING_GRACE_FIRED') && e.includes('expectedDurationMs=8000')));
    assert.ok(events.some((e) => e.includes('SILENCE_STATE_DIAG') && e.includes('from=warned') && e.includes('to=goodbye')));
});

test('STATE-DIAG: waiting -> idle (normal user speech) is recorded and cancels both timers', () => {
    const { ctx, events, timerCount } = buildSandbox({});
    vm.runInContext('startSilenceTimerIfNeeded(1, "ai_waiting_for_user")', ctx);
    vm.runInContext('resetSilenceTimer("user_speech_started")', ctx);
    assert.strictEqual(ctx.silenceState, 'idle');
    assert.strictEqual(timerCount(), 0, 'the 30s timer must actually be cancelled (existing behavior)');
    assert.ok(events.some((e) => e.includes('SILENCE_STATE_DIAG') && e.includes('from=waiting') && e.includes('to=idle')));
    assert.ok(events.some((e) => e.includes('SILENCE_TIMER_CANCELLED')));
});

test('DIAG-NO-BEHAVIOR-CHANGE: setSilenceState assigns the exact same value as before, with no side effects beyond logging', () => {
    const { ctx, events } = buildSandbox({ silenceState: 'idle' });
    // 同じ状態への再代入（既存のresetSilenceTimer()がidle中でも無条件にidleを
    // 再代入する既存仕様）ではSILENCE_STATE_DIAGを出さない（診断ノイズ低減の
    // ためのガードであり、silenceStateへの代入自体は必ず行われる）。
    vm.runInContext('setSilenceState("idle", "no_op")', ctx);
    assert.strictEqual(ctx.silenceState, 'idle');
    assert.strictEqual(events.filter((e) => e.includes('SILENCE_STATE_DIAG')).length, 0,
        'no-op reassignment to the same state must not emit a transition log (this is intentional and does not change behavior)');
});

// ===== 重要: 'warned'状態のまま8秒grace timerがキャンセルされない場合、その事実がログに残る =====

test('HYPOTHESIS-CHECK: if a normal conversational response.created arrives while warned, SILENCE_WARNING_GRACE is NOT cancelled (existing behavior unchanged; diagnostics reveal it)', () => {
    const { ctx, events, fireTimer, timerCount } = buildSandbox({});
    vm.runInContext('startSilenceTimerIfNeeded(1, "ai_waiting_for_user")', ctx);
    fireTimer(ctx.silenceTimerId); // -> warned, 8s grace armed
    assert.strictEqual(ctx.silenceState, 'warned');
    const graceTimerId = ctx.silenceWarningTimerId;
    assert.notStrictEqual(graceTimerId, null);

    // 正常な会話応答が発生した場合、response.createdハンドラのガードは
    // silenceState === 'waiting' のときのみ resetSilenceTimer() を呼ぶ
    // （本フェーズでは変更していない既存条件）。'warned'中はresetされない
    // ため、8秒grace timerは生き続ける。
    // ここではその既存条件そのものを再現する（resetSilenceTimer呼び出しを
    // 意図的に行わない）。
    assert.strictEqual(timerCount(), 1, 'the 8s grace timer must still be armed (unchanged pre-existing behavior)');

    fireTimer(graceTimerId);
    assert.strictEqual(ctx.silenceState, 'goodbye', 'without a cancellation, the existing code proceeds to goodbye exactly as before O5.6');
    assert.ok(events.some((e) => e.includes('SILENCE_WARNING_GRACE_FIRED')),
        'the diagnostic must surface this exact sequence for real-log confirmation, without altering it');
});

// ===== CALL-END-DIAG相当: sendResponseCreate/response.createの相関ログ =====

test('sendResponseCreate records RESPONSE_CREATE_DIAG with an incrementing local sequence and a correct category, without adding new dc.send payload fields', () => {
    const { ctx, events } = buildSandbox({});
    const ok = vm.runInContext('sendResponseCreate("silence_warning", "test instructions")', ctx);
    assert.strictEqual(ok, true);
    assert.strictEqual(ctx.dc.sent.length, 1);
    assert.deepStrictEqual(Object.keys(ctx.dc.sent[0]).sort(), ['response', 'type'],
        'the wire payload sent to the OpenAI Realtime API must still only contain type/response (no new unsupported fields)');
    assert.strictEqual(ctx.responseCreateDiagSeq, 1);
    assert.strictEqual(ctx.lastResponseCreateReason, 'silence_warning');
    assert.ok(events.some((e) => e.includes('RESPONSE_CREATE_DIAG') && e.includes('seq=1') && e.includes('category=silence_warning')));
});

test('categorizeResponseReason: silence_warning / silence_final_goodbye / tool_result / greeting / unknown / normal_conversation buckets', () => {
    const { ctx } = buildSandbox({});
    const cat = (r) => vm.runInContext('categorizeResponseReason(' + JSON.stringify(r) + ')', ctx);
    assert.strictEqual(cat('silence_warning'), 'silence_warning');
    assert.strictEqual(cat('silence_final_goodbye'), 'silence_goodbye');
    assert.strictEqual(cat('tool_result:check_availability'), 'tool_result');
    assert.strictEqual(cat('initial_greeting'), 'greeting');
    assert.strictEqual(cat('initial_greeting_fallback'), 'greeting');
    assert.strictEqual(cat('something_else'), 'unknown');
    assert.strictEqual(vm.runInContext('categorizeResponseReason(null)', ctx), 'normal_conversation');
});

// ===== SPEECH-DIAG: speech_started/speech_stoppedの経過時間を保持できる（既存変数の再利用） =====

test('SPEECH-DIAG: msSince() correctly reports elapsed time since lastSpeechStartedAt / lastSpeechStoppedAt, and null when unset', () => {
    const { ctx } = buildSandbox({});
    assert.strictEqual(vm.runInContext('msSince(lastSpeechStartedAt)', ctx), null, 'must be null before any speech_started has been observed');
    const t0 = Date.now();
    ctx.lastSpeechStartedAt = t0;
    const elapsed = vm.runInContext('msSince(lastSpeechStartedAt)', ctx);
    assert.ok(typeof elapsed === 'number' && elapsed >= 0, 'must return a non-negative numeric elapsed ms once speech_started has a timestamp');
});

// ===== PC-DIAG: connection state情報を取得できる =====

test('PC-DIAG: pushPcStateDiag records connectionState/iceConnectionState/dcReadyState/micTrackReadyState', () => {
    const { ctx, events } = buildSandbox({
        pc: { connectionState: 'disconnected', iceConnectionState: 'disconnected' },
        dc: { readyState: 'open' },
        localStream: { getAudioTracks: () => [{ readyState: 'live' }] },
    });
    vm.runInContext('pushPcStateDiag("test_label")', ctx);
    assert.ok(events.some((e) => e.includes('PC_STATE_DIAG')
        && e.includes('label=test_label')
        && e.includes('connectionState=disconnected')
        && e.includes('iceConnectionState=disconnected')
        && e.includes('dcReadyState=open')
        && e.includes('micTrackReadyState=live')));
});

test('PC-DIAG: pushPcStateDiag never throws even when pc/dc/localStream are all null (must not break call teardown)', () => {
    const { ctx, events } = buildSandbox({ pc: null, dc: null, localStream: null });
    assert.doesNotThrow(() => {
        vm.runInContext('pushPcStateDiag("null_case")', ctx);
    });
    assert.ok(events.some((e) => e.includes('PC_STATE_DIAG') && e.includes('connectionState=null') && e.includes('micTrackReadyState=null')));
});

test('CALL-END-DIAG wiring: endCall() builds the CALL_END_DIAG event inside a try/catch, before cleanupConnection()', () => {
    const idx = SRC.indexOf('function endCall(reason, source)');
    assert.notStrictEqual(idx, -1, 'endCall not found');
    const window = SRC.slice(idx, idx + 4400);
    const diagIdx = window.indexOf("pushTimelineEvent('CALL_END_DIAG");
    // 実際の呼び出し（cleanupConnection('endCall(...)')）を探す。診断ブロック
    // 直前のコメント中に「cleanupConnection()でpc/dc/localStreamがnull化される
    // 前に」という説明文があり、そこに含まれる"cleanupConnection("という
    // 部分文字列を誤って実際の呼び出しと取り違えないよう、diagIdx以降のみを
    // 検索する。
    const cleanupIdx = window.indexOf("cleanupConnection('endCall(", diagIdx);
    assert.ok(diagIdx !== -1, 'CALL_END_DIAG must be emitted from within endCall()');
    assert.ok(cleanupIdx !== -1 && diagIdx < cleanupIdx,
        'CALL_END_DIAG must be captured before cleanupConnection() nulls out pc/dc/localStream');
    // すべてのフィールドが仕様どおり含まれていること
    const fields = [
        'elapsedFromCallStartMs', 'callGeneration=', 'source=', 'silenceState=',
        'pendingSilenceGoodbyeHangup=', 'pcConnectionState=', 'iceConnectionState=',
        'dcReadyState=', 'micTrackReadyState=', 'elapsedSinceLastSpeechStartedMs=',
        'elapsedSinceLastSpeechStoppedMs=', 'lastResponseReason=', 'lastFunctionCallName=',
        'lastFunctionCallElapsedMs=',
    ];
    fields.forEach((f) => {
        assert.ok(window.includes(f), 'CALL_END_DIAG must include field: ' + f);
    });
    // try/catchで包まれていること
    const tryIdx = window.lastIndexOf('try {', diagIdx);
    assert.ok(tryIdx !== -1 && tryIdx < diagIdx, 'CALL_END_DIAG must be wrapped in try/catch so it can never break call teardown');
});

// ===== PRIVACY: 診断ログに個人情報・機密情報が含まれないこと =====

test('PRIVACY: CALL_END_DIAG embeds only a reasonPresent boolean, never the literal reason/UI text', () => {
    const idx = SRC.indexOf("pushTimelineEvent('CALL_END_DIAG");
    assert.notStrictEqual(idx, -1);
    const block = SRC.slice(idx, idx + 900);
    assert.ok(block.includes('reasonPresent=') && block.includes('!!reason'), 'must use a boolean presence flag, not the literal reason text');
    assert.ok(!block.includes("+ reason +") && !block.includes('+ reason)'), 'must never concatenate the raw reason string (may contain customer-facing UI text) into the log');
});

test('PRIVACY: no diagnostic event name or field references customer name/phone/reservation/transcript/audio/secret/token', () => {
    const forbidden = ['customerName', 'phoneNumber', 'shopSecret', 'ephemeralKey', 'fullTranscript', 'transcriptText'];
    const diagBlockStart = SRC.indexOf('PHASE O5.6: Silence Timeout Diagnostics');
    assert.notStrictEqual(diagBlockStart, -1);
    forbidden.forEach((sym) => {
        assert.ok(!SRC.includes(sym), 'engine source must never introduce a symbol suggesting PII storage: ' + sym);
    });
});

test('PRIVACY: RESPONSE_CREATE_CORRELATION only logs the fixed reason identifier (tool name / fixed literal), never free-form transcript text', () => {
    const idx = SRC.indexOf('RESPONSE_CREATE_CORRELATION (category=');
    assert.notStrictEqual(idx, -1);
    const block = SRC.slice(Math.max(0, idx - 400), idx + 400);
    assert.ok(block.includes('consumedReasonForDiag'), 'must correlate via the internal reason identifier, not raw transcript');
});

// ===== FAST-TURN / T0-T10: 既存トレースの主要シンボルが変更されず残っている =====

test('FAST-TURN/T0-T10: existing tool continuation trace symbols remain untouched', () => {
    ['toolContinuationTraceT0', 'toolContinuationTraceActive', 'pushToolContinuationTrace', 'T0_FUNCTION_CALL_RECEIVED', 'T7_CONTINUATION_RESPONSE_CREATED'].forEach((sym) => {
        assert.ok(SRC.includes(sym), 'FAST TURN / T0-T10 symbol must remain untouched: ' + sym);
    });
});

test('DC-SEND: O5.6 diagnostics add zero new dc.send() call sites (pure client-side observation only). Baseline is 9, not 8, because a later, independent phase (NOISY ENVIRONMENT / 3-SECOND TURN BOUNDARY) legitimately added one new call site (maybeSendUserTurnFallbackCommit); O5.6 itself still adds none.', () => {
    const sendCount = (SRC.match(/dc\.send\(JSON\.stringify\(/g) || []).length;
    assert.strictEqual(sendCount, 9, 'O5.6 must not add any new dc.send() call site; the current baseline of 9 reflects a later, unrelated phase adding USER_TURN_3S_FALLBACK, not this one');
});

test('O5.5 regression symbol check: ack-fallback symbols remain untouched by O5.6', () => {
    ['maybeSendSpeakThenWorkAckFallback', 'SPEAK_THEN_WORK_ACK_TOOLS', 'resetAckFallbackCallState'].forEach((sym) => {
        assert.ok(SRC.includes(sym), 'O5.5 symbol must remain untouched: ' + sym);
    });
});

test('GREETING regression symbol check: Zero-Wait Greeting symbols remain untouched by O5.6', () => {
    ['ZERO_WAIT_AUDIO_URL', 'startZeroWaitGreeting', 'preloadZeroWaitGreetingAudio'].forEach((sym) => {
        assert.ok(SRC.includes(sym), 'Zero-Wait Greeting symbol must remain untouched: ' + sym);
    });
});

test('debug=1 gating: the new window.error/unhandledrejection diagnostic listeners are gated by debugMode', () => {
    const idx = SRC.indexOf("window.addEventListener('error'");
    assert.notStrictEqual(idx, -1, 'error listener must exist');
    const before = SRC.slice(Math.max(0, idx - 300), idx);
    assert.ok(before.includes('if (debugMode)'), 'the error/unhandledrejection listeners must only be registered when debugMode is true');
});

test('startCall() resets all new O5.6 diagnostic-only variables for every new call (no cross-call leakage)', () => {
    const idx = SRC.indexOf('async function startCall');
    assert.notStrictEqual(idx, -1);
    const window = SRC.slice(idx, idx + 9000);
    ['silenceStateEnteredAt = null;', 'silenceTimerArmedAt = null;', 'silenceWarningGraceArmedAt = null;',
        'lastResponseCreateReason = null;', 'responseCreateDiagSeq = 0;', 'lastResponseReasonCategoryForDiag = \'unknown\';',
        'lastFunctionCallNameForDiag = null;', 'lastFunctionCallStartedAtForDiag = null;'].forEach((snippet) => {
        assert.ok(window.includes(snippet), 'startCall() must reset diagnostic variable: ' + snippet);
    });
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
process.exit(failed > 0 ? 1 : 0);
