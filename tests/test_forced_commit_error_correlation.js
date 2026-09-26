'use strict';

// PHASE O5.9.1 — Forced Commit Error Correlation（診断専用）regression tests
//
// 背景（O5.9 Auditで確認したギャップ）: SHORT_ANSWER Forced Commit
// （PHASE O5.8）は手動でinput_audio_buffer.commitを送信した後、
// QUICK_ANSWER_COMMIT_SENTを記録する。その後OpenAIから非同期に届く
// 'error'イベントは、既存コードでは「同一callGenerationでさえあれば
// 経過時間を問わず」QUICK_ANSWER_COMMIT_ERRORとして記録されており、
// 「本当に直前のこのcommitに起因するerrorなのか、たまたま同じ通話内の
// 無関係な後発errorなのか」を実ログだけから確実に判別できなかった。
//
// このテストは、frontend/public/js/realtime-voice-engine.js内の実際の
// 関数ソース・および該当if文ブロックのソーステキストを直接抽出し、
// Node.jsのvmモジュール上で最小限のモックとともに実行することで、
// 「本物の実装コード」に対してアサーションを行う（tests/test_short_answer_turn.js
// と同じ方式）。handleDataChannelEvent自体は依存関数が非常に多く全体を
// vm実行できないため、error correlationロジックのif文ブロックだけを
// 単独抽出して実行する（tests/test_tool_continuation_resilience.jsの
// E/F/G等と同じ「ソース窓の直接検証」方式を、実行可能な範囲では
// 実行に格上げしたもの）。
//
// 実行: node tests/test_forced_commit_error_correlation.js

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ENGINE_JS_PATH = path.join(__dirname, '..', 'frontend', 'public', 'js', 'realtime-voice-engine.js');
const SRC = fs.readFileSync(ENGINE_JS_PATH, 'utf8');

function extractFunctionSource(src, fnName, fromIndex) {
    const startToken = 'function ' + fnName + '(';
    const startIdx = src.indexOf(startToken, fromIndex || 0);
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

function extractConstExpr(src, name) {
    const re = new RegExp('const\\s+' + name + '\\s*=\\s*([^;]+);');
    const m = src.match(re);
    if (!m) throw new Error('const not found in source: ' + name);
    return m[1].trim();
}

// 「if (quickAnswerCommitSentAt !== null && ...) { ... }」ブロック全体を、
// anchor文字列（そのifの開始条件式）から始めて、対応する閉じ括弧まで
// ブレースマッチングで正確に抜き出す。handleDataChannelEvent全体を
// vm実行する代わりに、このifブロックだけを単独関数として実行するために使う。
function extractIfBlockSource(src, anchor) {
    const anchorIdx = src.indexOf(anchor);
    if (anchorIdx === -1) throw new Error('if-block anchor not found in source: ' + anchor);
    const braceStart = src.indexOf('{', anchorIdx);
    let depth = 0;
    let i = braceStart;
    for (; i < src.length; i++) {
        if (src[i] === '{') depth++;
        else if (src[i] === '}') {
            depth--;
            if (depth === 0) { i++; break; }
        }
    }
    return src.slice(anchorIdx, i);
}

const FN = {
    isStaleCallEvent: extractFunctionSource(SRC, 'isStaleCallEvent'),
    classifyExpectedAnswerType: extractFunctionSource(SRC, 'classifyExpectedAnswerType'),
    maybeSendQuickAnswerCommit: extractFunctionSource(SRC, 'maybeSendQuickAnswerCommit'),
};

const QUICK_ANSWER_COMMIT_ERROR_CORRELATION_MS_EXPR = extractConstExpr(SRC, 'QUICK_ANSWER_COMMIT_ERROR_CORRELATION_MS');
const QUICK_ANSWER_COMMIT_ERROR_CORRELATION_MS = eval('(' + QUICK_ANSWER_COMMIT_ERROR_CORRELATION_MS_EXPR + ')');

// 実際のhandleDataChannelEvent内の 'error' ハンドラのうち、SHORT_ANSWER
// Forced Commit相関ロジックのifブロックだけを抜き出す。このifは
// 「quickAnswerCommitSentAt !== null && callGeneration === quickAnswerCommitCallGeneration」
// という、実装コードそのままの開始条件式をanchorとして使う。
const errorCorrelationIfSrc = extractIfBlockSource(
    SRC,
    'if (quickAnswerCommitSentAt !== null && callGeneration === quickAnswerCommitCallGeneration) {'
);

assert.ok(errorCorrelationIfSrc.includes('QUICK_ANSWER_COMMIT_ERROR_CORRELATED'),
    'the extracted if-block must be the O5.9.1 correlation block (sanity check on extraction)');
assert.strictEqual(QUICK_ANSWER_COMMIT_ERROR_CORRELATION_MS, 5000,
    'QUICK_ANSWER_COMMIT_ERROR_CORRELATION_MS must be the O5.9.1 initial value (5000ms)');

function buildSandbox(overrides) {
    const events = [];
    const context = {
        performance: { now: () => Date.now() },
        pushTimelineEvent: (text) => { events.push(text); },
        console: console,
    };
    const state = Object.assign({
        callGeneration: 1,
        ended: false,
        expectedAnswerType: 'NONE',
        nameAnswerGeneration: 0,
        nameTurnNormalCompletionSeen: false,
        phoneAnswerGeneration: 0,
        phoneTurnNormalCompletionSeen: false,
        shortChoiceAnswerGeneration: 0,
        shortChoiceTurnNormalCompletionSeen: false,
        quickAnswerGeneration: 0,
        quickAnswerCommitSentGeneration: null,
        quickAnswerTurnNormalCompletionSeen: false,
        quickAnswerCommitSentAt: null,
        quickAnswerCommitCallGeneration: null,
        quickAnswerCommitPendingEvents: { committed: false, item: false, responseCreated: false, aiAudioStarted: false },
        quickAnswerCommitDiagSeq: 0,
        lastQuickAnswerCommitDiag: null,
        debugMode: false,
        shopId: 'normal-production-shop-id-xxxx',
        dc: { readyState: 'open', sent: [], send(payload) { this.sent.push(JSON.parse(payload)); } },
    }, overrides || {});
    Object.assign(context, state);
    vm.createContext(context);
    vm.runInContext(
        'const QUICK_ANSWER_COMMIT_ERROR_CORRELATION_MS = ' + QUICK_ANSWER_COMMIT_ERROR_CORRELATION_MS_EXPR + ';\n' +
        Object.values(FN).join('\n\n') + '\n\n' +
        // 実際のerrorハンドラのifブロックを、msgを受け取る単独関数として
        // ラップする（実ソースをそのまま実行。再実装ではない）。
        'function runErrorCorrelationBlock(msg) {\n' + errorCorrelationIfSrc + '\n}',
        context
    );
    return { ctx: context, events };
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
        console.log('    ' + (e && e.stack ? e.stack.split('\n').slice(0, 4).join('\n    ') : e));
    }
}

console.log('PHASE O5.9.1 — Forced Commit Error Correlation regression tests');
console.log('source: ' + ENGINE_JS_PATH);
console.log('');

// ===== COMMIT-CORRELATION: Forced Commit送信時にseq/time/generationを保持 =====
test('COMMIT-CORRELATION: a successful SHORT_ANSWER Forced Commit records seq/sentAt/epochMs/callGeneration/expectedAnswerType (diagnostic-only)', () => {
    const { ctx, events } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('何名様ですか？')", ctx);
    vm.runInContext('maybeSendQuickAnswerCommit(\'SHORT_ANSWER\', 1)', ctx);
    assert.strictEqual(ctx.dc.sent.length, 1, 'the commit must actually have been sent');
    assert.strictEqual(ctx.quickAnswerCommitDiagSeq, 1, 'diag seq must increment to 1 on first successful commit');
    assert.notStrictEqual(ctx.lastQuickAnswerCommitDiag, null);
    assert.strictEqual(ctx.lastQuickAnswerCommitDiag.seq, 1);
    assert.strictEqual(ctx.lastQuickAnswerCommitDiag.callGeneration, 1);
    assert.strictEqual(ctx.lastQuickAnswerCommitDiag.expectedAnswerType, 'SHORT_ANSWER');
    assert.strictEqual(typeof ctx.lastQuickAnswerCommitDiag.sentAt, 'number');
    assert.strictEqual(typeof ctx.lastQuickAnswerCommitDiag.epochMs, 'number');
    assert.ok(events.some((e) => e.startsWith('QUICK_ANSWER_COMMIT_SENT') && e.includes('seq=1') && e.includes('callGeneration=1') && e.includes('expectedAnswerType=SHORT_ANSWER')),
        'the enriched QUICK_ANSWER_COMMIT_SENT event must include seq/callGeneration/expectedAnswerType');
});

test('COMMIT-CORRELATION: diag seq increments across separate SHORT_ANSWER generations (re-ask scenario)', () => {
    const { ctx } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('本日の18時はすでに過ぎております。別のお時間をご希望ですか？')", ctx);
    vm.runInContext('maybeSendQuickAnswerCommit(\'SHORT_ANSWER\', 1)', ctx);
    assert.strictEqual(ctx.quickAnswerCommitDiagSeq, 1);
    vm.runInContext("classifyExpectedAnswerType('あいにくその曜日は11時から22時までの営業となっております。その時間帯でご都合のよいお時間はございますか？')", ctx);
    vm.runInContext('maybeSendQuickAnswerCommit(\'SHORT_ANSWER\', 1)', ctx);
    assert.strictEqual(ctx.quickAnswerCommitDiagSeq, 2, 'a second, independent SHORT_ANSWER re-ask must bump the diag seq again');
    assert.strictEqual(ctx.lastQuickAnswerCommitDiag.seq, 2);
});

// ===== NEARBY-ERROR: 同generation・window内error -> QUICK_ANSWER_COMMIT_ERROR_CORRELATED =====
test('NEARBY-ERROR: an error on the same callGeneration, well inside the correlation window, is marked CORRELATED', () => {
    const { ctx, events } = buildSandbox({
        callGeneration: 7,
        quickAnswerCommitSentAt: 1000,
        quickAnswerCommitCallGeneration: 7,
        lastQuickAnswerCommitDiag: { seq: 3, sentAt: 1000, epochMs: 1700000000000, callGeneration: 7, expectedAnswerType: 'SHORT_ANSWER' },
    });
    // performance.now()はDate.now()にマップしているため、Date.nowを固定して
    // 「commit送信(1000)から500ms後」を再現する。
    const realNow = Date.now;
    Date.now = () => 1500;
    try {
        vm.runInContext('runErrorCorrelationBlock({ error: { type: "invalid_request_error", code: "input_audio_buffer_commit_empty", message: "the buffer has 0ms of audio" } })', ctx);
    } finally {
        Date.now = realNow;
    }
    assert.ok(events.some((e) => e.startsWith('QUICK_ANSWER_COMMIT_ERROR (')), 'the existing, unmodified QUICK_ANSWER_COMMIT_ERROR must still fire (never hidden)');
    const correlated = events.find((e) => e.startsWith('QUICK_ANSWER_COMMIT_ERROR_CORRELATED'));
    assert.ok(correlated, 'QUICK_ANSWER_COMMIT_ERROR_CORRELATED must fire for a nearby, same-generation error');
    assert.ok(correlated.includes('seq=3'));
    assert.ok(correlated.includes('elapsedSinceCommitMs=500'));
    assert.ok(correlated.includes('callGeneration=7'));
    assert.ok(correlated.includes('errorType=invalid_request_error'));
    assert.ok(correlated.includes('errorCode=input_audio_buffer_commit_empty'));
    assert.ok(!correlated.includes('the buffer has 0ms of audio'), 'the raw error message text must never be included in the correlated event (PII/verbosity safety)');
});

test('NEARBY-ERROR: exactly at the correlation window boundary (=5000ms) is still CORRELATED (inclusive boundary)', () => {
    const { ctx, events } = buildSandbox({
        callGeneration: 2,
        quickAnswerCommitSentAt: 0,
        quickAnswerCommitCallGeneration: 2,
        lastQuickAnswerCommitDiag: { seq: 1, sentAt: 0, epochMs: 1700000000000, callGeneration: 2, expectedAnswerType: 'SHORT_ANSWER' },
    });
    const realNow = Date.now;
    Date.now = () => QUICK_ANSWER_COMMIT_ERROR_CORRELATION_MS; // ちょうど境界値
    try {
        vm.runInContext('runErrorCorrelationBlock({ error: { type: "invalid_request_error", code: "some_code" } })', ctx);
    } finally {
        Date.now = realNow;
    }
    assert.ok(events.some((e) => e.startsWith('QUICK_ANSWER_COMMIT_ERROR_CORRELATED')), 'the window boundary itself must be treated as correlated (<=), not excluded');
});

// ===== OLD-ERROR: window外 -> correlated eventなし（既存QUICK_ANSWER_COMMIT_ERRORは維持） =====
test('OLD-ERROR: an error just outside the correlation window is NOT marked CORRELATED, but the base error event still fires', () => {
    const { ctx, events } = buildSandbox({
        callGeneration: 4,
        quickAnswerCommitSentAt: 0,
        quickAnswerCommitCallGeneration: 4,
        lastQuickAnswerCommitDiag: { seq: 9, sentAt: 0, epochMs: 1700000000000, callGeneration: 4, expectedAnswerType: 'SHORT_ANSWER' },
    });
    const realNow = Date.now;
    Date.now = () => QUICK_ANSWER_COMMIT_ERROR_CORRELATION_MS + 1; // 境界を1ms超過
    try {
        vm.runInContext('runErrorCorrelationBlock({ error: { type: "server_error", code: "unrelated_later_error" } })', ctx);
    } finally {
        Date.now = realNow;
    }
    assert.ok(events.some((e) => e.startsWith('QUICK_ANSWER_COMMIT_ERROR (')), 'the existing QUICK_ANSWER_COMMIT_ERROR must not be hidden even when out of the correlation window (user instruction 6)');
    assert.ok(!events.some((e) => e.startsWith('QUICK_ANSWER_COMMIT_ERROR_CORRELATED')), 'an error outside the correlation window must not be labeled as correlated (must not overclaim causation)');
});

// ===== STALE-CALL: 別callGeneration -> correlated eventなし =====
test('STALE-CALL: an error whose callGeneration differs from the commit is NOT marked CORRELATED (and the base error event is also gated the same way)', () => {
    const { ctx, events } = buildSandbox({
        callGeneration: 99, // 現在の通話世代（別の通話）
        quickAnswerCommitSentAt: 0,
        quickAnswerCommitCallGeneration: 5, // commit送信時の世代（既に古い）
        lastQuickAnswerCommitDiag: { seq: 2, sentAt: 0, epochMs: 1700000000000, callGeneration: 5, expectedAnswerType: 'SHORT_ANSWER' },
    });
    const realNow = Date.now;
    Date.now = () => 100;
    try {
        vm.runInContext('runErrorCorrelationBlock({ error: { type: "server_error", code: "irrelevant" } })', ctx);
    } finally {
        Date.now = realNow;
    }
    assert.ok(!events.some((e) => e.startsWith('QUICK_ANSWER_COMMIT_ERROR')), 'a stale-generation error must not be attributed to a previous call at all (existing gating, unchanged)');
    assert.ok(!events.some((e) => e.startsWith('QUICK_ANSWER_COMMIT_ERROR_CORRELATED')));
});

// ===== NO-COMMIT: commitなしerror -> correlated eventなし =====
test('NO-COMMIT: an error with no prior SHORT_ANSWER Forced Commit at all produces no correlation event of either kind', () => {
    const { ctx, events } = buildSandbox({
        callGeneration: 1,
        quickAnswerCommitSentAt: null, // 一度もcommitを送っていない
        quickAnswerCommitCallGeneration: null,
        lastQuickAnswerCommitDiag: null,
    });
    vm.runInContext('runErrorCorrelationBlock({ error: { type: "server_error", code: "unrelated" } })', ctx);
    assert.ok(!events.some((e) => e.startsWith('QUICK_ANSWER_COMMIT_ERROR')), 'no commit was ever sent, so no SHORT_ANSWER commit error attribution of any kind should occur');
});

// ===== PRIVACY: transcript/audio/tokenを保存しない =====
test('PRIVACY: EXPECTED_ANSWER_DIAG never includes the AI transcript text itself, only the classified type', () => {
    const { ctx, events } = buildSandbox({});
    const secretTranscript = '本日はお電話ありがとうございます。何名様ですか？（客の個人情報は含まない例文）';
    vm.runInContext('classifyExpectedAnswerType(' + JSON.stringify(secretTranscript) + ')', ctx);
    const diagEvents = events.filter((e) => e.startsWith('EXPECTED_ANSWER_DIAG'));
    assert.ok(diagEvents.length >= 1, 'EXPECTED_ANSWER_DIAG must be recorded unconditionally on every classification call');
    for (const e of diagEvents) {
        assert.ok(!e.includes(secretTranscript), 'EXPECTED_ANSWER_DIAG must never embed the raw AI transcript');
        assert.ok(/^EXPECTED_ANSWER_DIAG \(type=[A-Z_]+\)$/.test(e), 'EXPECTED_ANSWER_DIAG must only ever carry a bare type= value');
    }
});

test('PRIVACY: QUICK_ANSWER_COMMIT_ERROR_CORRELATED never includes the raw error message, only type/code/seq/elapsed/generation', () => {
    const { ctx, events } = buildSandbox({
        callGeneration: 1,
        quickAnswerCommitSentAt: 0,
        quickAnswerCommitCallGeneration: 1,
        lastQuickAnswerCommitDiag: { seq: 1, sentAt: 0, epochMs: 1700000000000, callGeneration: 1, expectedAnswerType: 'SHORT_ANSWER' },
    });
    const realNow = Date.now;
    Date.now = () => 10;
    try {
        vm.runInContext('runErrorCorrelationBlock({ error: { type: "invalid_request_error", code: "buffer_too_small", message: "極めて長い診断メッセージにPIIやtranscriptが紛れ込んでいないことを確認するためのダミー文字列".repeat(5) } })', ctx);
    } finally {
        Date.now = realNow;
    }
    const correlated = events.find((e) => e.startsWith('QUICK_ANSWER_COMMIT_ERROR_CORRELATED'));
    assert.ok(correlated);
    assert.ok(!correlated.includes('message='), 'QUICK_ANSWER_COMMIT_ERROR_CORRELATED must never include an error message field at all');
    assert.ok(/^QUICK_ANSWER_COMMIT_ERROR_CORRELATED \(seq=\d+, elapsedSinceCommitMs=\d+, callGeneration=\d+, errorType=[^,]*, errorCode=[^)]*\)$/.test(correlated),
        'the correlated event must have exactly the specified minimal field set (seq/elapsedSinceCommitMs/callGeneration/errorType/errorCode)');
});

// ===== NO-BEHAVIOR-CHANGE: Realtime送信イベント数・内容が変更されていない =====
test('NO-BEHAVIOR-CHANGE: dc.send(JSON.stringify( call-site count is unchanged at 8 (O5.9.1 adds diagnostics only, no new Realtime sends)', () => {
    const sendCount = (SRC.match(/dc\.send\(JSON\.stringify\(/g) || []).length;
    assert.strictEqual(sendCount, 8, 'O5.9.1 must not add any new dc.send() call site (diagnostics-only phase)');
});

test('NO-BEHAVIOR-CHANGE: maybeSendQuickAnswerCommit still sends exactly one input_audio_buffer.commit and never a response.create', () => {
    const { ctx } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('何名様ですか？')", ctx);
    vm.runInContext('maybeSendQuickAnswerCommit(\'SHORT_ANSWER\', 1)', ctx);
    assert.strictEqual(ctx.dc.sent.length, 1);
    assert.deepStrictEqual(ctx.dc.sent[0], { type: 'input_audio_buffer.commit' });
});

test('NO-BEHAVIOR-CHANGE: the error-correlation block itself never calls dc.send (diagnostics only, read-only observation)', () => {
    assert.ok(!errorCorrelationIfSrc.includes('dc.send'), 'the correlation logic must be pure observation/logging and must never send anything to Realtime');
    assert.ok(!errorCorrelationIfSrc.includes('input_audio_buffer.commit'), 'must not re-trigger a commit');
    assert.ok(!errorCorrelationIfSrc.includes('response.create'), 'must not add a response.create');
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
if (failed > 0) process.exit(1);
