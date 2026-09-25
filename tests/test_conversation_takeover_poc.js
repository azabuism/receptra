'use strict';

// Conversation Takeover Observation PoC — regression test suite
//
// このテストは、shop-ai-realtime-voice.html内の実際の関数ソースをHTMLファイルから
// 直接抽出し、Node.jsのvmモジュール上で最小限のモック（dc.send/setTimeout/
// pushTimelineEvent等）とともに実行することで、「本物の実装コード」に対して
// アサーションを行う（手書きの再実装に対してテストするのではない）。
//
// 対象: startTakeoverTimerIfNeeded / cancelTakeoverTimer /
//       maybeSendTakeoverCommitPoc / maybeLogTakeoverReactionElapsed
//       および、これらと衝突しないことを検証するためのNAME PoC側の関数群
//       （maybeSendNameCommitPoc等）。
//
// 実行: node tests/test_conversation_takeover_poc.js

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

// Phase P1（Public AI Call）: shop-ai-realtime-voice.htmlのインライン<script>は
// frontend/public/js/realtime-voice-engine.jsへ無改変で外部化された（call.html
// と共有するため）。実装コードの実体はそちらに移ったため、抽出元もそちらへ
// 追従させる（テストの検証対象・手法自体は一切変更していない）。
// html（HTMLシェル自体）も、一部テストがマーカー文字列の存在確認に参照している
// ため、引き続き読み込んでおく。
const HTML_PATH = path.join(__dirname, '..', 'frontend', 'public', 'shop-ai-realtime-voice.html');
const html = fs.readFileSync(HTML_PATH, 'utf8');
const ENGINE_JS_PATH = path.join(__dirname, '..', 'frontend', 'public', 'js', 'realtime-voice-engine.js');
const SRC = fs.readFileSync(ENGINE_JS_PATH, 'utf8');

// ---- 抽出ヘルパー（実装コードそのものを取り出す。書き写さない） ----

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

// 実装から直接抽出する対象一式
const FN = {
    isStaleCallEvent: extractFunctionSource(SRC, 'isStaleCallEvent'),
    classifyExpectedAnswerType: extractFunctionSource(SRC, 'classifyExpectedAnswerType'),
    startAnswerWindowIfNeeded: extractFunctionSource(SRC, 'startAnswerWindowIfNeeded'),
    maybeSendNameCommitPoc: extractFunctionSource(SRC, 'maybeSendNameCommitPoc'),
    maybeLogPocReactionElapsed: extractFunctionSource(SRC, 'maybeLogPocReactionElapsed'),
    cancelAnswerWindow: extractFunctionSource(SRC, 'cancelAnswerWindow'),
    startTakeoverTimerIfNeeded: extractFunctionSource(SRC, 'startTakeoverTimerIfNeeded'),
    cancelTakeoverTimer: extractFunctionSource(SRC, 'cancelTakeoverTimer'),
    maybeSendTakeoverCommitPoc: extractFunctionSource(SRC, 'maybeSendTakeoverCommitPoc'),
    maybeLogTakeoverReactionElapsed: extractFunctionSource(SRC, 'maybeLogTakeoverReactionElapsed'),
};

const NAME_COMMIT_POC_SHOP_ID_EXPR = extractConstExpr(SRC, 'NAME_COMMIT_POC_SHOP_ID');
const NAME_COMMIT_POC_SHOP_ID = eval(NAME_COMMIT_POC_SHOP_ID_EXPR);
const nameCommitPocEnabledExpr = extractConstExpr(SRC, 'nameCommitPocEnabled');
const takeoverPocEnabledExpr = extractConstExpr(SRC, 'takeoverPocEnabled');
const ANSWER_WINDOW_LIMITS_MS_EXPR = extractConstExpr(SRC, 'ANSWER_WINDOW_LIMITS_MS');
const TAKEOVER_TIMER_MS_EXPR = extractConstExpr(SRC, 'TAKEOVER_TIMER_MS');

assert.strictEqual(NAME_COMMIT_POC_SHOP_ID, '65932cb5-97db-460e-b9d6-0471fce23d88');

// gatingの式（nameCommitPocEnabled/takeoverPocEnabled）が全く同じ安全な
// パターン（debugMode && shopId === PoC店舗ID）であることを構造的に確認する。
// これによりTakeover PoCが誤って別の条件（緩い条件）で有効化されるリスクを
// ソースレベルで検出する。
assert.strictEqual(
    nameCommitPocEnabledExpr.replace(/\s+/g, ''),
    'debugMode&&shopId===NAME_COMMIT_POC_SHOP_ID',
    'NAME PoCのgating式が想定と異なる（テスト前提が崩れている可能性）'
);
assert.strictEqual(
    takeoverPocEnabledExpr.replace(/\s+/g, ''),
    'debugMode&&shopId===NAME_COMMIT_POC_SHOP_ID',
    'Takeover PoCのgating式がNAME PoCと同一の安全なパターンになっていない'
);

// ---- サンドボックス構築ヘルパー ----
// setTimeout/clearTimeoutは実時間を待たず、テストコードから明示的に
// 発火/確認できるフェイク実装に差し替える。

function buildSandbox(overrides) {
    const events = [];
    const timers = new Map();
    let nextTimerId = 1;

    const state = Object.assign({
        // ---- 通話・世代管理（既存の仕組みをそのまま再現） ----
        callGeneration: 1,
        ended: false,

        // ---- Expected Answer Window関連 ----
        expectedAnswerType: 'NONE',
        answerWindowTimerId: null,
        answerWindowType: null,

        // ---- NAME Forced Commit Observation PoC関連 ----
        nameAnswerGeneration: 0,
        pocCommitSentGeneration: null,
        nameTurnNormalCompletionSeen: false,
        pocCommitSentAt: null,
        pocCommitCallGeneration: null,
        pocCommitPendingEvents: { committed: false, item: false, responseCreated: false, aiAudioStarted: false },

        // ---- Conversation Takeover Observation PoC関連 ----
        takeoverSpeechEpisodeId: 0,
        takeoverTimerId: null,
        takeoverCommitSentEpisode: null,
        takeoverTurnNormalCompletionSeen: false,
        takeoverCommitSentAt: null,
        takeoverCommitCallGeneration: null,
        takeoverCommitPendingEvents: { committed: false, item: false, responseCreated: false, aiAudioStarted: false },

        // ---- gating（テストごとに指定するshopId/debugModeから決まる） ----
        debugMode: false,
        shopId: 'normal-shop-id-xxxx',
        NAME_COMMIT_POC_SHOP_ID: NAME_COMMIT_POC_SHOP_ID,

        // ---- datachannel モック ----
        dc: { readyState: 'open', sent: [], send(payload) { this.sent.push(JSON.parse(payload)); } },
    }, overrides || {});

    const sandbox = {
        performance: { now: () => Date.now() },
        setTimeout: (fn, delay) => {
            const id = nextTimerId++;
            timers.set(id, { fn, delay });
            return id;
        },
        clearTimeout: (id) => { timers.delete(id); },
        pushTimelineEvent: (text) => { events.push(text); },
        console: console,
    };
    Object.assign(sandbox, state);

    vm.createContext(sandbox);

    // gating consts（実装式そのものをsandbox内で評価する）
    vm.runInContext(
        'const NAME_COMMIT_POC_SHOP_ID_ = ' + NAME_COMMIT_POC_SHOP_ID_EXPR + ';\n' +
        'var nameCommitPocEnabled = ' + nameCommitPocEnabledExpr + ';\n' +
        'var takeoverPocEnabled = ' + takeoverPocEnabledExpr + ';\n' +
        'const ANSWER_WINDOW_LIMITS_MS = ' + ANSWER_WINDOW_LIMITS_MS_EXPR + ';\n' +
        'const TAKEOVER_TIMER_MS = ' + TAKEOVER_TIMER_MS_EXPR + ';\n',
        sandbox
    );

    // 実装関数そのものをsandbox内で定義する
    vm.runInContext(Object.values(FN).join('\n\n'), sandbox);

    return { sandbox, events, timers, fireTimer: (id) => { const t = timers.get(id); if (t) { timers.delete(id); vm.runInContext('(' + t.fn.toString() + ')()', sandbox); } } };
}

// setTimeoutのコールバックはクロージャ（myEpisodeId/myGeneration等を捕捉）を
// 持つため、vm.runInContext経由でtoString()した関数を再実行するのではなく、
// sandbox内に登録された実の関数オブジェクトを直接呼び出せるようにする。
function buildSandbox2(overrides) {
    const timers = new Map();
    let nextTimerId = 1;
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
        answerWindowTimerId: null,
        answerWindowType: null,
        nameAnswerGeneration: 0,
        pocCommitSentGeneration: null,
        nameTurnNormalCompletionSeen: false,
        pocCommitSentAt: null,
        pocCommitCallGeneration: null,
        pocCommitPendingEvents: { committed: false, item: false, responseCreated: false, aiAudioStarted: false },
        takeoverSpeechEpisodeId: 0,
        takeoverTimerId: null,
        takeoverCommitSentEpisode: null,
        takeoverTurnNormalCompletionSeen: false,
        takeoverCommitSentAt: null,
        takeoverCommitCallGeneration: null,
        takeoverCommitPendingEvents: { committed: false, item: false, responseCreated: false, aiAudioStarted: false },
        debugMode: false,
        shopId: 'normal-shop-id-xxxx',
        dc: { readyState: 'open', sent: [], send(payload) { this.sent.push(JSON.parse(payload)); } },
    }, overrides || {});
    Object.assign(context, state);
    context.setTimeout = (fn, delay) => {
        const id = nextTimerId++;
        timers.set(id, fn);
        return id;
    };
    context.clearTimeout = (id) => { timers.delete(id); };

    vm.createContext(context);
    vm.runInContext(
        'const NAME_COMMIT_POC_SHOP_ID = ' + NAME_COMMIT_POC_SHOP_ID_EXPR + ';\n' +
        'var nameCommitPocEnabled = ' + nameCommitPocEnabledExpr + ';\n' +
        'var takeoverPocEnabled = ' + takeoverPocEnabledExpr + ';\n' +
        'const ANSWER_WINDOW_LIMITS_MS = ' + ANSWER_WINDOW_LIMITS_MS_EXPR + ';\n' +
        'const TAKEOVER_TIMER_MS = ' + TAKEOVER_TIMER_MS_EXPR + ';\n' +
        Object.values(FN).join('\n\n'),
        context
    );

    return {
        ctx: context,
        events,
        fireTimer: (id) => { const fn = timers.get(id); timers.delete(id); if (fn) fn(); },
        timerCount: () => timers.size,
    };
}

// ---- テストランナー ----
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

console.log('Conversation Takeover Observation PoC regression tests');
console.log('source: ' + ENGINE_JS_PATH);
console.log('');

// 1. 通常店舗ではTakeover PoCが完全に無効（debug=1であっても）
test('1) normal shop + debug=1 -> takeoverPocEnabled=false, timer never starts', () => {
    const { ctx, events } = buildSandbox2({ debugMode: true, shopId: 'some-other-real-shop-id' });
    assert.strictEqual(ctx.takeoverPocEnabled, false);
    vm.runInContext('startTakeoverTimerIfNeeded(1)', ctx);
    assert.strictEqual(ctx.takeoverTimerId, null);
    assert.strictEqual(ctx.takeoverSpeechEpisodeId, 0);
    assert.ok(!events.some(e => e.startsWith('TAKEOVER_')), 'no TAKEOVER_* events should be logged on a normal shop');
});

// 2. PoC店舗だがdebug=1が無い場合は無効
test('2) PoC shop without debug=1 -> takeoverPocEnabled=false, timer never starts', () => {
    const { ctx, events } = buildSandbox2({ debugMode: false, shopId: NAME_COMMIT_POC_SHOP_ID });
    assert.strictEqual(ctx.takeoverPocEnabled, false);
    vm.runInContext('startTakeoverTimerIfNeeded(1)', ctx);
    assert.strictEqual(ctx.takeoverTimerId, null);
    assert.ok(!events.some(e => e.startsWith('TAKEOVER_')));
});

// 3. PoC店舗 + debug=1 の両方が揃った場合のみ有効
test('3) PoC shop + debug=1 -> takeoverPocEnabled=true, timer starts on speech_started equivalent', () => {
    const { ctx, events, timerCount } = buildSandbox2({ debugMode: true, shopId: NAME_COMMIT_POC_SHOP_ID });
    assert.strictEqual(ctx.takeoverPocEnabled, true);
    vm.runInContext('startTakeoverTimerIfNeeded(1)', ctx);
    assert.strictEqual(ctx.takeoverSpeechEpisodeId, 1);
    assert.strictEqual(timerCount(), 1);
    assert.ok(events.some(e => e.startsWith('TAKEOVER_ELIGIBLE')));
    assert.ok(events.some(e => e.startsWith('TAKEOVER_TIMER_STARTED')));
});

// 4. NAME PoCの既存動作が保持されている（NAME世代のcommitロジックは不変）
test('4) NAME PoC still commits exactly once per NAME generation, unaffected by Takeover code', () => {
    const { ctx, events } = buildSandbox2({ debugMode: true, shopId: NAME_COMMIT_POC_SHOP_ID });
    vm.runInContext("classifyExpectedAnswerType('お名前を教えてください')", ctx);
    assert.strictEqual(ctx.expectedAnswerType, 'NAME');
    assert.strictEqual(ctx.nameAnswerGeneration, 1);
    vm.runInContext('maybeSendNameCommitPoc("NAME", 1)', ctx);
    assert.strictEqual(ctx.dc.sent.length, 1);
    assert.deepStrictEqual(ctx.dc.sent[0], { type: 'input_audio_buffer.commit' });
    assert.strictEqual(ctx.pocCommitSentGeneration, 1);
    // 同一世代で2回目を試みても再送信されない
    vm.runInContext('maybeSendNameCommitPoc("NAME", 1)', ctx);
    assert.strictEqual(ctx.dc.sent.length, 1, 'NAME PoC must not double-commit within the same generation');
});

// 5. NAME PoCの処理中でも、TakeoverはNAME/PHONE/YES_NO期待時にタイマーを開始しない
//    （二重commitの構造的防止）
test('5) no double-commit risk: takeover timer never starts while expectedAnswerType is NAME/PHONE/YES_NO', () => {
    const { ctx, timerCount } = buildSandbox2({ debugMode: true, shopId: NAME_COMMIT_POC_SHOP_ID });
    for (const type of ['NAME', 'PHONE', 'YES_NO']) {
        ctx.expectedAnswerType = type;
        vm.runInContext('startTakeoverTimerIfNeeded(1)', ctx);
        assert.strictEqual(timerCount(), 0, 'takeover timer must not start for expectedAnswerType=' + type);
        assert.strictEqual(ctx.takeoverTimerId, null);
    }
});

// 6. VISIT_REASON/NONEの場合はTakeoverタイマーが正しく開始する
test('6) takeover timer starts normally for VISIT_REASON and NONE (unprompted long speech)', () => {
    for (const type of ['VISIT_REASON', 'NONE']) {
        const { ctx, timerCount } = buildSandbox2({ debugMode: true, shopId: NAME_COMMIT_POC_SHOP_ID });
        ctx.expectedAnswerType = type;
        vm.runInContext('startTakeoverTimerIfNeeded(1)', ctx);
        assert.strictEqual(timerCount(), 1, 'takeover timer should start for expectedAnswerType=' + type);
    }
});

// 7. Tool Calling / Fast Reservation Flow / Intent Classification /
//    Medical Privacy / Silence Timeout / Zero-Wait など、今回変更していない
//    領域のソースコードが変更されていないことを確認する（構造的回帰チェック）。
test('7) unrelated production mechanisms are textually unchanged (no accidental edits)', () => {
    const mustContain = [
        'SILENCE_TIMEOUT_MS = 30000',
        "silenceState = 'idle'; // 'idle' | 'waiting' | 'warned' | 'goodbye'",
        'function handleFunctionCallItem',
    ];
    for (const needle of mustContain) {
        const inHtml = html.includes(needle) || SRC.includes(needle);
        assert.ok(inHtml, 'expected unchanged marker not found: ' + needle);
    }
});

test('7b) Intent Classification / Fast Reservation Flow templates unaffected (realtime_voice_ai.py, not this file)', () => {
    const pyPath = path.join(__dirname, '..', 'app', 'services', 'realtime_voice_ai.py');
    const py = fs.readFileSync(pyPath, 'utf8');
    assert.ok(py.includes('_INTENT_CLASSIFICATION_TEMPLATE'), 'Intent Classification template missing from realtime_voice_ai.py');
    assert.ok(py.includes('_FAST_RESERVATION_FLOW_TEMPLATE'), 'Fast Reservation Flow template missing from realtime_voice_ai.py');
});

// 8. Visit Reason ON/OFF自体のロジック（ask_visit_reason_enabled）はrealtime_voice_ai.py側の
//    responsibility。ここではVISIT_REASON分類が壊れていないことのみ確認。
test('8) VISIT_REASON classification via classifyExpectedAnswerType is preserved', () => {
    const { ctx } = buildSandbox2({});
    vm.runInContext("classifyExpectedAnswerType('本日はどのようなことでのご予約でしょうか')", ctx);
    assert.strictEqual(ctx.expectedAnswerType, 'VISIT_REASON');
});

// 9. Medical Privacy: このPoCはユーザー発話内容に一切アクセスしない
//    （transcriptを引数に取る関数が無いことをソースレベルで確認）。
test('9) Takeover functions never reference user speech content/transcript', () => {
    for (const [name, src] of [
        ['startTakeoverTimerIfNeeded', FN.startTakeoverTimerIfNeeded],
        ['cancelTakeoverTimer', FN.cancelTakeoverTimer],
        ['maybeSendTakeoverCommitPoc', FN.maybeSendTakeoverCommitPoc],
        ['maybeLogTakeoverReactionElapsed', FN.maybeLogTakeoverReactionElapsed],
    ]) {
        assert.ok(!/transcript/i.test(src), name + ' must not reference transcript content');
        // コメント上で「response.createは送らない」と説明する記述自体は許容する
        // （それこそが今回の安全設計の核心のため）。ここでは実際に
        // 'response.create' というtype文字列を組み立てて送信するコードが
        // 存在しないことだけを確認する。
        assert.ok(!/type:\s*['"]response\.create['"]/.test(src), name + ' must never construct a response.create payload');
    }
});

// 10. Silence Timeout preserved (independent mechanism, untouched consts)
test('10) Silence Timeout constants untouched', () => {
    assert.ok(SRC.includes('const SILENCE_TIMEOUT_MS = 30000;'));
    assert.ok(SRC.includes('const SILENCE_WARNING_GRACE_MS = 8000;'));
});

// 11. Zero-Wait untouched marker check
test('11) Zero-Wait greeting mechanism markers untouched', () => {
    assert.ok(SRC.includes('zeroWaitEnabled'));
    assert.ok(SRC.includes('zeroWaitState'));
});

// 12. callGeneration/ended staleness guard is honored by the takeover timer callback
test('12) stale call event guard: expired call generation aborts timer expiry handling', () => {
    const { ctx, fireTimer, events } = buildSandbox2({ debugMode: true, shopId: NAME_COMMIT_POC_SHOP_ID });
    vm.runInContext('startTakeoverTimerIfNeeded(1)', ctx);
    const timerId = ctx.takeoverTimerId;
    // 通話が終了した（ended=true）後にタイマーが発火した場合を模擬する
    ctx.ended = true;
    fireTimer(timerId);
    assert.ok(!events.some(e => e.startsWith('TAKEOVER_TIMER_EXPIRED')), 'expired timer must be a no-op once the call has ended');
    assert.strictEqual(ctx.dc.sent.length, 0);
});

test('12b) stale call event guard: mismatched callGeneration aborts timer expiry handling', () => {
    const { ctx, fireTimer, events } = buildSandbox2({ debugMode: true, shopId: NAME_COMMIT_POC_SHOP_ID, callGeneration: 1 });
    vm.runInContext('startTakeoverTimerIfNeeded(1)', ctx);
    const timerId = ctx.takeoverTimerId;
    ctx.callGeneration = 2; // 新しい通話が始まった
    fireTimer(timerId);
    assert.ok(!events.some(e => e.startsWith('TAKEOVER_TIMER_EXPIRED')));
    assert.strictEqual(ctx.dc.sent.length, 0);
});

// 13/16. no double response / no double commit: 手動commitは1エピソードにつき最大1回
test('13/16) at most one manual commit per speech episode, and response.create is never sent', () => {
    const { ctx, fireTimer } = buildSandbox2({ debugMode: true, shopId: NAME_COMMIT_POC_SHOP_ID });
    vm.runInContext('startTakeoverTimerIfNeeded(1)', ctx);
    const timerId = ctx.takeoverTimerId;
    fireTimer(timerId);
    assert.strictEqual(ctx.dc.sent.length, 1);
    assert.deepStrictEqual(ctx.dc.sent[0], { type: 'input_audio_buffer.commit' });
    // 直接もう一度呼んでも二重送信しない（最終確認ガード）
    vm.runInContext('maybeSendTakeoverCommitPoc(' + ctx.takeoverSpeechEpisodeId + ', 1)', ctx);
    assert.strictEqual(ctx.dc.sent.length, 1, 'must not send a second commit for the same episode');
    // response.create は一度も送られていない
    assert.ok(!ctx.dc.sent.some(m => m.type === 'response.create'));
});

// 14. race condition: 正常なターン終了が先に来た場合、commitは送信されない
test('14) race guard: a normal turn completion right before expiry cancels the forced commit', () => {
    const { ctx, fireTimer } = buildSandbox2({ debugMode: true, shopId: NAME_COMMIT_POC_SHOP_ID });
    vm.runInContext('startTakeoverTimerIfNeeded(1)', ctx);
    const timerId = ctx.takeoverTimerId;
    // タイマー期限直前に正常完了イベント（speech_stopped相当）が届いたと仮定
    ctx.takeoverTurnNormalCompletionSeen = true;
    fireTimer(timerId);
    assert.strictEqual(ctx.dc.sent.length, 0, 'forced commit must be skipped once normal completion has been observed');
});

// 15. datachannel not open -> non-fatal skip, no throw
test('15) datachannel not open -> commit is skipped without throwing', () => {
    const { ctx, fireTimer } = buildSandbox2({ debugMode: true, shopId: NAME_COMMIT_POC_SHOP_ID, dc: { readyState: 'closed', sent: [], send() { throw new Error('should not be called'); } } });
    vm.runInContext('startTakeoverTimerIfNeeded(1)', ctx);
    const timerId = ctx.takeoverTimerId;
    assert.doesNotThrow(() => fireTimer(timerId));
});

// 17. errors must not unilaterally end the call: send()の例外はcatchされ、
//     endCall/cleanupConnectionへの参照が全く無いことをソースレベルで確認
test('17) send() exceptions are caught and non-fatal (no endCall/cleanupConnection reference)', () => {
    for (const [name, src] of Object.entries({
        maybeSendTakeoverCommitPoc: FN.maybeSendTakeoverCommitPoc,
        maybeLogTakeoverReactionElapsed: FN.maybeLogTakeoverReactionElapsed,
        startTakeoverTimerIfNeeded: FN.startTakeoverTimerIfNeeded,
        cancelTakeoverTimer: FN.cancelTakeoverTimer,
    })) {
        assert.ok(!src.includes('endCall('), name + ' must never call endCall()');
        assert.ok(!src.includes('cleanupConnection('), name + ' must never call cleanupConnection()');
    }
    assert.ok(FN.maybeSendTakeoverCommitPoc.includes('try {') && FN.maybeSendTakeoverCommitPoc.includes('catch (e)'),
        'maybeSendTakeoverCommitPoc must wrap dc.send in try/catch');

    const { ctx, fireTimer, events } = buildSandbox2({
        debugMode: true, shopId: NAME_COMMIT_POC_SHOP_ID,
        dc: { readyState: 'open', sent: [], send() { throw new Error('simulated send failure'); } },
    });
    vm.runInContext('startTakeoverTimerIfNeeded(1)', ctx);
    const timerId = ctx.takeoverTimerId;
    assert.doesNotThrow(() => fireTimer(timerId));
    assert.ok(events.some(e => e.startsWith('TAKEOVER_ERROR')));
});

// 18. no impact on other tenants: gatingはshopIdの完全一致のみで判定される
//     （部分一致・大文字小文字無視等の緩い判定になっていないこと）
test('18) shopId gating is an exact, case-sensitive match (no accidental match for other tenants)', () => {
    const nearMiss = NAME_COMMIT_POC_SHOP_ID.toUpperCase();
    const { ctx } = buildSandbox2({ debugMode: true, shopId: nearMiss });
    assert.notStrictEqual(nearMiss, NAME_COMMIT_POC_SHOP_ID);
    assert.strictEqual(ctx.takeoverPocEnabled, false, 'case-different shopId must not enable the PoC');

    const prefixOnly = NAME_COMMIT_POC_SHOP_ID.slice(0, 10);
    const { ctx: ctx2 } = buildSandbox2({ debugMode: true, shopId: prefixOnly });
    assert.strictEqual(ctx2.takeoverPocEnabled, false, 'prefix match must not enable the PoC');
});

// 追加: startCall()のリセットブロックにTakeover PoCの状態変数が
// 含まれていることをソースレベルで確認する（通話をまたいだ状態持ち越し防止）。
test('extra) startCall() resets all Takeover PoC state variables', () => {
    const startCallSrc = extractFunctionSource(SRC, 'startCall');
    for (const varName of [
        'takeoverSpeechEpisodeId = 0',
        'takeoverTimerId',
        'takeoverCommitSentEpisode = null',
        'takeoverTurnNormalCompletionSeen = false',
        'takeoverCommitSentAt = null',
        'takeoverCommitCallGeneration = null',
        'takeoverCommitPendingEvents = {',
    ]) {
        assert.ok(startCallSrc.includes(varName), 'startCall() is missing reset for: ' + varName);
    }
});

// 追加: cancelTakeoverTimerがspeech_stoppedハンドラから呼ばれていることを
// ソースレベルで確認する（正常終了時にタイマーが残らないこと）。
test('extra) speech_stopped handler cancels the takeover timer', () => {
    const idx = SRC.indexOf("input_audio_buffer.speech_stopped'");
    const chunk = SRC.slice(idx, idx + 1200);
    assert.ok(chunk.includes("cancelTakeoverTimer('speech_stopped_normally')"));
    assert.ok(chunk.includes('takeoverTurnNormalCompletionSeen = true'));
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
if (failed > 0) process.exit(1);
