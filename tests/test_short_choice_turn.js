'use strict';

// Short Choice 3-Second Turn — regression test suite
//
// 「2択質問（YES_NO / SHORT_CHOICE）は最大約3秒を目安に回答ターンを成立させる」
// 機能の回帰テスト。NAME Forced Commit Observation PoC / Conversation Takeover
// Observation PoCのテスト（test_conversation_takeover_poc.js）と同じ方式で、
// shop-ai-realtime-voice.html内の実際の関数ソースを直接抽出し、Node.jsのvm
// モジュール上で最小限のモックとともに実行する。
//
// 重要: この機能はPoC店舗限定ではなく、全店舗（本番）に適用される（ユーザーの
// 明示的な確認済み）。そのため gating の有無そのものはテストしない
// （意図的に無い）。安全パターン（1世代につきcommitは最大1回・正常完了レース
// ガード・response.create不送信・非fatalエラー処理・他機構との二重発火防止）を
// 検証する。
//
// 実行: node tests/test_short_choice_turn.js

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const HTML_PATH = path.join(__dirname, '..', 'frontend', 'public', 'shop-ai-realtime-voice.html');
const html = fs.readFileSync(HTML_PATH, 'utf8');

const scriptMatch = html.match(/<script>([\s\S]*?)<\/script>/);
if (!scriptMatch) throw new Error('inline <script> block not found in shop-ai-realtime-voice.html');
const SRC = scriptMatch[1];

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

const FN = {
    isStaleCallEvent: extractFunctionSource(SRC, 'isStaleCallEvent'),
    classifyExpectedAnswerType: extractFunctionSource(SRC, 'classifyExpectedAnswerType'),
    startAnswerWindowIfNeeded: extractFunctionSource(SRC, 'startAnswerWindowIfNeeded'),
    maybeSendNameCommitPoc: extractFunctionSource(SRC, 'maybeSendNameCommitPoc'),
    maybeLogPocReactionElapsed: extractFunctionSource(SRC, 'maybeLogPocReactionElapsed'),
    maybeSendShortAnswerCommit: extractFunctionSource(SRC, 'maybeSendShortAnswerCommit'),
    maybeLogShortAnswerReactionElapsed: extractFunctionSource(SRC, 'maybeLogShortAnswerReactionElapsed'),
    // PHONE Forced Commit（PHONE NUMBER TURN FIXフェーズで追加）: startAnswerWindowIfNeeded()の
    // タイマー期限切れハンドラが直接呼び出すため、抽出しないとReferenceErrorになる
    // （maybeSendNameCommitPoc/maybeSendShortAnswerCommitと同じ理由）。
    maybeSendPhoneCommit: extractFunctionSource(SRC, 'maybeSendPhoneCommit'),
    maybeLogPhoneReactionElapsed: extractFunctionSource(SRC, 'maybeLogPhoneReactionElapsed'),
    cancelAnswerWindow: extractFunctionSource(SRC, 'cancelAnswerWindow'),
    startTakeoverTimerIfNeeded: extractFunctionSource(SRC, 'startTakeoverTimerIfNeeded'),
};

const ANSWER_WINDOW_LIMITS_MS_EXPR = extractConstExpr(SRC, 'ANSWER_WINDOW_LIMITS_MS');
const ANSWER_WINDOW_LIMITS_MS = eval('(' + ANSWER_WINDOW_LIMITS_MS_EXPR + ')');

assert.strictEqual(ANSWER_WINDOW_LIMITS_MS.SHORT_CHOICE, 3000, 'SHORT_CHOICE limit should be 3000ms');
assert.strictEqual(ANSWER_WINDOW_LIMITS_MS.YES_NO, 3000, 'YES_NO limit should now be 3000ms (unified with SHORT_CHOICE)');
assert.strictEqual(ANSWER_WINDOW_LIMITS_MS.NAME, 5000, 'NAME limit must remain 5000ms (unchanged)');
assert.strictEqual(ANSWER_WINDOW_LIMITS_MS.VISIT_REASON, 30000, 'VISIT_REASON limit must remain 30000ms (unchanged)');

function buildSandbox(overrides) {
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
        shortChoiceAnswerGeneration: 0,
        shortChoiceCommitSentGeneration: null,
        shortChoiceTurnNormalCompletionSeen: false,
        shortAnswerCommitSentAt: null,
        shortAnswerCommitCallGeneration: null,
        shortAnswerCommitPendingEvents: { committed: false, item: false, responseCreated: false, aiAudioStarted: false },
        // PHONE Forced Commit（PHONE NUMBER TURN FIXフェーズで追加。このテストの
        // 対象ではないが、startAnswerWindowIfNeeded()経由で参照されるため必要）
        phoneAnswerGeneration: 0,
        phoneCommitSentGeneration: null,
        phoneTurnNormalCompletionSeen: false,
        phoneCommitSentAt: null,
        phoneCommitCallGeneration: null,
        phoneCommitPendingEvents: { committed: false, item: false, responseCreated: false, aiAudioStarted: false },
        takeoverTimerId: null,
        takeoverSpeechEpisodeId: 0,
        takeoverPocEnabled: false, // このテストではTakeover PoC自体の有効性は問わない
        debugMode: false,
        shopId: 'normal-production-shop-id-xxxx', // 通常の本番店舗（PoC店舗ではない）
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
        'const ANSWER_WINDOW_LIMITS_MS = ' + ANSWER_WINDOW_LIMITS_MS_EXPR + ';\n' +
        Object.values(FN).join('\n\n'),
        context
    );

    return {
        ctx: context,
        events,
        fireTimer: (id) => { const fn = timers.get(id); timers.delete(id); if (fn) fn(); },
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
        console.log('    ' + (e && e.stack ? e.stack.split('\n').slice(0, 4).join('\n    ') : e));
    }
}

console.log('Short Choice 3-Second Turn regression tests');
console.log('source: ' + HTML_PATH);
console.log('');

// 分類テスト: ユーザー提示の4つの代表例が正しくSHORT_CHOICEに分類されること
test('classify) AM/PM, today/tomorrow, reservation/callback, yes/no choice questions -> SHORT_CHOICE', () => {
    const examples = [
        '午前ですか、午後ですか？',
        'はい、いいえのどちらですか？',
        '今日ですか、明日ですか？',
        '予約ですか、折り返しですか？',
    ];
    for (const transcript of examples) {
        const { ctx } = buildSandbox({});
        vm.runInContext('classifyExpectedAnswerType(' + JSON.stringify(transcript) + ')', ctx);
        assert.strictEqual(ctx.expectedAnswerType, 'SHORT_CHOICE', 'expected SHORT_CHOICE for: ' + transcript);
    }
});

test('classify) free-form questions are NOT classified as SHORT_CHOICE (CASE 6)', () => {
    const freeForm = [
        'ご希望の日時を教えてください',
        '来店理由を教えてください',
        'お問い合わせ内容を教えてください',
    ];
    for (const transcript of freeForm) {
        const { ctx } = buildSandbox({});
        vm.runInContext('classifyExpectedAnswerType(' + JSON.stringify(transcript) + ')', ctx);
        assert.notStrictEqual(ctx.expectedAnswerType, 'SHORT_CHOICE', 'must not classify as SHORT_CHOICE: ' + transcript);
    }
});

test('classify) existing confirmation-style YES_NO phrasing still classifies as YES_NO, not SHORT_CHOICE', () => {
    const { ctx } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('この内容でよろしいですか？')", ctx);
    assert.strictEqual(ctx.expectedAnswerType, 'YES_NO');
});

test('classify) NAME/PHONE classification takes priority over accidental SHORT_CHOICE-shaped phrasing', () => {
    const { ctx } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('お名前を教えていただけますか？')", ctx);
    assert.strictEqual(ctx.expectedAnswerType, 'NAME');
});

test('CASE 7) NAME timer limit remains 5000ms and unaffected by Short Choice changes', () => {
    const { ctx, timerCount } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('お名前をお願いします')", ctx);
    assert.strictEqual(ctx.expectedAnswerType, 'NAME');
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    assert.strictEqual(timerCount(), 1);
    // NAME限定のPoC gatingは維持されている（通常店舗ではmaybeSendNameCommitPocは何もしない）
});

test('CASE 8) VISIT_REASON timer limit remains 30000ms (observation-only, unaffected)', () => {
    const { ctx } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('ご来店の目的を教えてください')", ctx);
    assert.strictEqual(ctx.expectedAnswerType, 'VISIT_REASON');
});

// CASE 3: 通常VADが先に成立 -> manual commitしない
test('CASE 1/3) normal VAD completing before the 3s window fires prevents any forced commit', () => {
    const { ctx, fireTimer } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('午前と午後、どちらですか？')", ctx);
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    const timerId = ctx.answerWindowTimerId;
    // 正常なターン終了（speech_stopped等）が先に届いたと仮定
    ctx.shortChoiceTurnNormalCompletionSeen = true;
    fireTimer(timerId);
    assert.strictEqual(ctx.dc.sent.length, 0, 'no forced commit should be sent once normal completion was observed');
});

// CASE 2: 通常VADが来ない -> 約3秒で安全なTurn Completion
test('CASE 2) forced commit fires after the window expires when no normal completion was seen', () => {
    const { ctx, fireTimer } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('午前と午後、どちらですか？')", ctx);
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    const timerId = ctx.answerWindowTimerId;
    fireTimer(timerId);
    assert.strictEqual(ctx.dc.sent.length, 1);
    assert.deepStrictEqual(ctx.dc.sent[0], { type: 'input_audio_buffer.commit' });
});

// CASE 4: manual commitが先 -> 遅延speech_stoppedで二重commitしない
test('CASE 4) at most one manual commit per generation even if called again afterwards', () => {
    const { ctx, fireTimer } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('今日ですか、明日ですか？')", ctx);
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    const timerId = ctx.answerWindowTimerId;
    fireTimer(timerId);
    assert.strictEqual(ctx.dc.sent.length, 1);
    // 遅れて到着したspeech_stopped相当の処理（正常完了フラグは立つが、既に
    // commitSentGenerationが一致しているため再送信はされない）
    vm.runInContext('maybeSendShortAnswerCommit("SHORT_CHOICE", 1)', ctx);
    assert.strictEqual(ctx.dc.sent.length, 1, 'must not send a second commit for the same generation');
    // response.create は一度も送られていない
    assert.ok(!ctx.dc.sent.some(m => m.type === 'response.create'));
});

// CASE 5: YES/NOでも同じ
test('CASE 5) YES_NO gets the same 3-second forced-commit treatment as SHORT_CHOICE', () => {
    const { ctx, fireTimer } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('この内容でよろしいですか？')", ctx);
    assert.strictEqual(ctx.expectedAnswerType, 'YES_NO');
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    const timerId = ctx.answerWindowTimerId;
    fireTimer(timerId);
    assert.strictEqual(ctx.dc.sent.length, 1);
    assert.deepStrictEqual(ctx.dc.sent[0], { type: 'input_audio_buffer.commit' });
});

// 新しい世代（次の2択質問）では改めて1回だけ送信できる
test('new generation) a second short-choice question after the first gets its own forced commit', () => {
    const { ctx, fireTimer } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('午前と午後、どちらですか？')", ctx);
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    fireTimer(ctx.answerWindowTimerId);
    assert.strictEqual(ctx.dc.sent.length, 1);
    // 2問目（別の2択質問）
    ctx.answerWindowTimerId = null; // 実装ではAnswer Window終了時にnullへ戻る
    vm.runInContext("classifyExpectedAnswerType('今日ですか、明日ですか？')", ctx);
    assert.strictEqual(ctx.shortChoiceAnswerGeneration, 2);
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    fireTimer(ctx.answerWindowTimerId);
    assert.strictEqual(ctx.dc.sent.length, 2, 'the second short-choice question should get its own forced commit');
});

// CASE 9 / 二重発火防止: Conversation TakeoverはSHORT_CHOICE中に競合しない
test('CASE 9 / no double-trigger) Conversation Takeover timer never starts while expectedAnswerType is SHORT_CHOICE or YES_NO', () => {
    for (const type of ['SHORT_CHOICE', 'YES_NO']) {
        const { ctx } = buildSandbox({ takeoverPocEnabled: true, debugMode: true, shopId: '65932cb5-97db-460e-b9d6-0471fce23d88' });
        ctx.expectedAnswerType = type;
        vm.runInContext('startTakeoverTimerIfNeeded(1)', ctx);
        assert.strictEqual(ctx.takeoverTimerId, null, 'takeover timer must not start for expectedAnswerType=' + type);
    }
});

// datachannel not open -> non-fatal skip
test('robustness) datachannel not open -> commit is skipped without throwing', () => {
    const { ctx, fireTimer } = buildSandbox({ dc: { readyState: 'closed', sent: [], send() { throw new Error('should not be called'); } } });
    vm.runInContext("classifyExpectedAnswerType('午前ですか、午後ですか？')", ctx);
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    const timerId = ctx.answerWindowTimerId;
    assert.doesNotThrow(() => fireTimer(timerId));
});

// send()例外は非fatal
test('robustness) send() exceptions are caught and non-fatal (no endCall/cleanupConnection reference)', () => {
    assert.ok(!FN.maybeSendShortAnswerCommit.includes('endCall('));
    assert.ok(!FN.maybeSendShortAnswerCommit.includes('cleanupConnection('));
    assert.ok(FN.maybeSendShortAnswerCommit.includes('try {') && FN.maybeSendShortAnswerCommit.includes('catch (e)'));
    assert.ok(!/type:\s*['"]response\.create['"]/.test(FN.maybeSendShortAnswerCommit), 'must never construct a response.create payload');

    const { ctx, fireTimer, events } = buildSandbox({
        dc: { readyState: 'open', sent: [], send() { throw new Error('simulated send failure'); } },
    });
    vm.runInContext("classifyExpectedAnswerType('午前ですか、午後ですか？')", ctx);
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    const timerId = ctx.answerWindowTimerId;
    assert.doesNotThrow(() => fireTimer(timerId));
    assert.ok(events.some(e => e.startsWith('SHORT_ANSWER_COMMIT_ERROR')));
});

// stale call generation guard
test('robustness) stale callGeneration aborts the forced commit (existing isStaleCallEvent guard reused)', () => {
    const { ctx, fireTimer } = buildSandbox({ callGeneration: 1 });
    vm.runInContext("classifyExpectedAnswerType('午前ですか、午後ですか？')", ctx);
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    const timerId = ctx.answerWindowTimerId;
    ctx.callGeneration = 2; // 新しい通話が始まった
    fireTimer(timerId);
    assert.strictEqual(ctx.dc.sent.length, 0);
});

// 全店舗適用の確認: このPoCと異なりgatingが無いこと（構造的確認）
test('scope) maybeSendShortAnswerCommit performs no shop_id/debug gating (applies to all production shops)', () => {
    assert.ok(!FN.maybeSendShortAnswerCommit.includes('shopId'), 'must not gate by shopId');
    assert.ok(!FN.maybeSendShortAnswerCommit.includes('debugMode'), 'must not gate by debugMode');
    // NAME PoC自体のgatingは維持されていること（対照確認）
    assert.ok(FN.maybeSendNameCommitPoc.includes('nameCommitPocEnabled'), 'NAME PoC gating must remain unchanged');
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
if (failed > 0) process.exit(1);
