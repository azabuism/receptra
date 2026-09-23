'use strict';

// PHONE Forced Commit（電話番号ターン専用の安全上限）— regression test suite
//
// 背景（PHONE NUMBER TURN FIXフェーズ）: 「最後に、ご連絡先のお電話番号を
// お願いします」とAIが尋ねた後、お客様が電話番号を話したにもかかわらずAIが
// 応答を再開しないまま会話が停止する症状が実機で報告された。監査の結果、
// PHONE用のExpected Answer Window自体（ANSWER_WINDOW_LIMITS_MS.PHONE=10000ms・
// classifyExpectedAnswerType()によるPHONE判定・startAnswerWindowIfNeeded()に
// よるタイマー開始）はすでに存在していたが、そのタイマーが期限切れになった
// 際に実際に手動commitを送信する関数がNAME/YES_NO/SHORT_CHOICE分にしか
// 存在せず、PHONEだけが「タイムボックス超過をログに記録するだけで、その後は
// 何もせず通常のsemantic_vadに委ねる」という未実装のまま放置されていたことが
// 判明した。本テストは、実機で安全性が確認済みのNAME Forced Commit
// Observation PoC・Short Choice 3-Second Turnと全く同じ安全パターンで実装した
// maybeSendPhoneCommit()を検証する。
//
// このテストは、shop-ai-realtime-voice.html内の実際の関数ソースをHTMLファイルから
// 直接抽出し、Node.jsのvmモジュール上で最小限のモック（dc.send/setTimeout/
// pushTimelineEvent等）とともに実行することで、「本物の実装コード」に対して
// アサーションを行う（手書きの再実装に対してテストするのではない）。
// tests/test_short_choice_turn.js / tests/test_conversation_takeover_poc.js と
// 同じ方式。
//
// 実行: node tests/test_phone_forced_commit.js

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
    maybeSendPhoneCommit: extractFunctionSource(SRC, 'maybeSendPhoneCommit'),
    maybeLogPhoneReactionElapsed: extractFunctionSource(SRC, 'maybeLogPhoneReactionElapsed'),
    // SHORT_ANSWER Forced Commit（FAST ANSWER TURNフェーズで追加）:
    // startAnswerWindowIfNeeded()のタイマー期限切れハンドラが直接呼び出すため、
    // 抽出しないとReferenceErrorになる（maybeSendPhoneCommit等と同じ理由）。
    maybeSendQuickAnswerCommit: extractFunctionSource(SRC, 'maybeSendQuickAnswerCommit'),
    maybeLogQuickAnswerReactionElapsed: extractFunctionSource(SRC, 'maybeLogQuickAnswerReactionElapsed'),
    cancelAnswerWindow: extractFunctionSource(SRC, 'cancelAnswerWindow'),
    startTakeoverTimerIfNeeded: extractFunctionSource(SRC, 'startTakeoverTimerIfNeeded'),
};

const ANSWER_WINDOW_LIMITS_MS_EXPR = extractConstExpr(SRC, 'ANSWER_WINDOW_LIMITS_MS');
const ANSWER_WINDOW_LIMITS_MS = eval('(' + ANSWER_WINDOW_LIMITS_MS_EXPR + ')');

assert.strictEqual(ANSWER_WINDOW_LIMITS_MS.PHONE, 10000, 'PHONE limit must be 10000ms (~10 second safety ceiling)');
assert.strictEqual(ANSWER_WINDOW_LIMITS_MS.NAME, 5000, 'NAME limit must remain 5000ms (unchanged)');
assert.strictEqual(ANSWER_WINDOW_LIMITS_MS.YES_NO, 3000, 'YES_NO limit must remain 3000ms (unchanged)');
assert.strictEqual(ANSWER_WINDOW_LIMITS_MS.SHORT_CHOICE, 3000, 'SHORT_CHOICE limit must remain 3000ms (unchanged)');
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
        phoneAnswerGeneration: 0,
        phoneCommitSentGeneration: null,
        phoneTurnNormalCompletionSeen: false,
        phoneCommitSentAt: null,
        phoneCommitCallGeneration: null,
        phoneCommitPendingEvents: { committed: false, item: false, responseCreated: false, aiAudioStarted: false },
        // SHORT_ANSWER Forced Commit（FAST ANSWER TURNフェーズで追加。このテストの
        // 対象ではないが、startAnswerWindowIfNeeded()経由で参照されるため必要）
        quickAnswerGeneration: 0,
        quickAnswerCommitSentGeneration: null,
        quickAnswerTurnNormalCompletionSeen: false,
        quickAnswerCommitSentAt: null,
        quickAnswerCommitCallGeneration: null,
        quickAnswerCommitPendingEvents: { committed: false, item: false, responseCreated: false, aiAudioStarted: false },
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
        timers.set(id, { fn, delay });
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
        console.log('    ' + (e && e.stack ? e.stack.split('\n').slice(0, 4).join('\n    ') : e));
    }
}

console.log('PHONE Forced Commit regression tests');
console.log('source: ' + HTML_PATH);
console.log('');

// A) classify) 電話番号を尋ねる代表的な言い回し -> PHONE
test('A: classify) phone-number question phrasing -> PHONE', () => {
    const examples = ['最後に、ご連絡先のお電話番号をお願いします', 'お電話番号を教えてください', '電話番号は何番ですか？'];
    for (const transcript of examples) {
        const { ctx } = buildSandbox({});
        vm.runInContext('classifyExpectedAnswerType(' + JSON.stringify(transcript) + ')', ctx);
        assert.strictEqual(ctx.expectedAnswerType, 'PHONE', 'expected PHONE for: ' + transcript);
    }
});

// B) タイマー起点はユーザー発話開始、上限は10秒（安全上限であり待ち時間ではない）
test('B: PHONE Answer Window timer uses the existing 10000ms ceiling', () => {
    const { ctx, timerCount, timerDelay } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('お電話番号をお願いします')", ctx);
    assert.strictEqual(ctx.expectedAnswerType, 'PHONE');
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx); // 起点: ユーザーspeech_started相当
    assert.strictEqual(timerCount(), 1);
    assert.strictEqual(timerDelay(ctx.answerWindowTimerId), 10000);
});

// C) 正常なsemantic_vad完了が10秒より先に来れば、何も送信しない（NORMAL VAD FIRST）
test('C: normal VAD completion before the 10s ceiling prevents any forced commit', () => {
    const { ctx, fireTimer } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('お電話番号をお願いします')", ctx);
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    const timerId = ctx.answerWindowTimerId;
    // 正常なターン終了（speech_stopped/committed/item_created相当）が先に届いた
    ctx.phoneTurnNormalCompletionSeen = true;
    fireTimer(timerId);
    assert.strictEqual(ctx.dc.sent.length, 0, 'no forced commit should be sent once normal completion was observed');
});

// D) 正常完了が来ない場合、10秒上限でForced Commitが実際に発火する
test('D: forced commit fires at the 10s ceiling when no normal completion was seen', () => {
    const { ctx, fireTimer } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('お電話番号をお願いします')", ctx);
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    const timerId = ctx.answerWindowTimerId;
    fireTimer(timerId);
    assert.strictEqual(ctx.dc.sent.length, 1);
    assert.deepStrictEqual(ctx.dc.sent[0], { type: 'input_audio_buffer.commit' });
});

// E) 二重commit防止（同一世代につき最大1回）
test('E: at most one manual commit per PHONE generation even if called again afterwards', () => {
    const { ctx, fireTimer } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('お電話番号をお願いします')", ctx);
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    const timerId = ctx.answerWindowTimerId;
    fireTimer(timerId);
    assert.strictEqual(ctx.dc.sent.length, 1);
    // 遅れて到着した正常完了相当の処理（フラグは立つが、既にcommitSentGenerationが
    // 一致しているため再送信はされない）
    vm.runInContext('maybeSendPhoneCommit("PHONE", 1)', ctx);
    assert.strictEqual(ctx.dc.sent.length, 1, 'must not send a second commit for the same generation');
    assert.ok(!ctx.dc.sent.some(m => m.type === 'response.create'));
});

// F) 手動commit後、response.createは絶対に追加送信しない（ソースレベルで確認）
test('F: maybeSendPhoneCommit never constructs or sends a response.create payload', () => {
    assert.ok(!FN.maybeSendPhoneCommit.includes('sendResponseCreate('), 'must not call sendResponseCreate');
    assert.ok(!/type:\s*['"]response\.create['"]/.test(FN.maybeSendPhoneCommit), 'must never construct a response.create payload');
    assert.ok(FN.maybeSendPhoneCommit.includes("type: 'input_audio_buffer.commit'"));
});

// G) 通話終了後（ended=true）はcommitを送信しない（isStaleCallEventの再利用）
test('G: stale call event (ended=true) aborts the forced commit', () => {
    const { ctx, fireTimer } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('お電話番号をお願いします')", ctx);
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    const timerId = ctx.answerWindowTimerId;
    ctx.ended = true; // 通話終了
    fireTimer(timerId);
    assert.strictEqual(ctx.dc.sent.length, 0);
});

// H) 通話をまたいだ場合（callGeneration不一致）もstaleとしてcommitを送信しない
test('H: stale callGeneration (call ended and a new one started) aborts the forced commit', () => {
    const { ctx, fireTimer } = buildSandbox({ callGeneration: 1 });
    vm.runInContext("classifyExpectedAnswerType('お電話番号をお願いします')", ctx);
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    const timerId = ctx.answerWindowTimerId;
    ctx.callGeneration = 2; // 新しい通話が始まった
    fireTimer(timerId);
    assert.strictEqual(ctx.dc.sent.length, 0);
});

// I) datachannel未オープン -> 非fatalでスキップ
test('I: datachannel not open -> commit is skipped without throwing', () => {
    const { ctx, fireTimer } = buildSandbox({ dc: { readyState: 'closed', sent: [], send() { throw new Error('should not be called'); } } });
    vm.runInContext("classifyExpectedAnswerType('お電話番号をお願いします')", ctx);
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    const timerId = ctx.answerWindowTimerId;
    assert.doesNotThrow(() => fireTimer(timerId));
    assert.strictEqual(ctx.dc.sent.length, 0);
});

// J) send()例外は非fatal（endCall/cleanupConnectionを呼ばない）
test('J: send() exceptions are caught and non-fatal (no endCall/cleanupConnection reference)', () => {
    assert.ok(!FN.maybeSendPhoneCommit.includes('endCall('));
    assert.ok(!FN.maybeSendPhoneCommit.includes('cleanupConnection('));
    assert.ok(FN.maybeSendPhoneCommit.includes('try {') && FN.maybeSendPhoneCommit.includes('catch (e)'));

    const { ctx, fireTimer, events } = buildSandbox({
        dc: { readyState: 'open', sent: [], send() { throw new Error('simulated send failure'); } },
    });
    vm.runInContext("classifyExpectedAnswerType('お電話番号をお願いします')", ctx);
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    const timerId = ctx.answerWindowTimerId;
    assert.doesNotThrow(() => fireTimer(timerId));
    assert.ok(events.some(e => e.startsWith('PHONE_COMMIT_ERROR')));
});

// K) PHONE以外のanswerTypeではmaybeSendPhoneCommitは何もしない（型ガード）
test('K: maybeSendPhoneCommit is a no-op for non-PHONE answerType', () => {
    const { ctx } = buildSandbox({});
    vm.runInContext('maybeSendPhoneCommit("YES_NO", 1)', ctx);
    assert.strictEqual(ctx.dc.sent.length, 0);
    vm.runInContext('maybeSendPhoneCommit("SHORT_CHOICE", 1)', ctx);
    assert.strictEqual(ctx.dc.sent.length, 0);
    vm.runInContext('maybeSendPhoneCommit("NAME", 1)', ctx);
    assert.strictEqual(ctx.dc.sent.length, 0);
});

// L) 新しいPHONE世代（電話番号の聞き直し）では改めて1回だけForced Commitできる
test('L: a re-asked PHONE question (new generation) gets its own forced commit', () => {
    const { ctx, fireTimer } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('お電話番号をお願いします')", ctx);
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    fireTimer(ctx.answerWindowTimerId);
    assert.strictEqual(ctx.dc.sent.length, 1);
    assert.strictEqual(ctx.phoneAnswerGeneration, 1);
    // 何らかの理由でAIが電話番号を再度尋ねた（型がNAME等へ変わってからPHONEへ戻る想定）
    vm.runInContext("classifyExpectedAnswerType('お名前を教えてください')", ctx);
    ctx.answerWindowTimerId = null; // 実装ではAnswer Window終了時にnullへ戻る
    vm.runInContext("classifyExpectedAnswerType('もう一度お電話番号をお願いします')", ctx);
    assert.strictEqual(ctx.phoneAnswerGeneration, 2, 'a fresh PHONE classification should bump the generation');
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    fireTimer(ctx.answerWindowTimerId);
    assert.strictEqual(ctx.dc.sent.length, 2, 'the re-asked PHONE question should get its own forced commit');
});

// M) Conversation Takeoverとの非競合: PHONEターン中はTakeoverタイマーが開始しない
test('M: Conversation Takeover timer never starts while expectedAnswerType is PHONE', () => {
    const { ctx } = buildSandbox({ takeoverPocEnabled: true, debugMode: true, shopId: '65932cb5-97db-460e-b9d6-0471fce23d88' });
    ctx.expectedAnswerType = 'PHONE';
    vm.runInContext('startTakeoverTimerIfNeeded(1)', ctx);
    assert.strictEqual(ctx.takeoverTimerId, null, 'takeover timer must not start while expectedAnswerType=PHONE');
});

// N) 全店舗適用の確認: NAME PoCのようなgatingを一切行わないこと（構造的確認）
test('N: maybeSendPhoneCommit performs no shop_id/debug gating (applies to all production shops, like Short Choice)', () => {
    assert.ok(!FN.maybeSendPhoneCommit.includes('shopId'), 'must not gate by shopId');
    assert.ok(!FN.maybeSendPhoneCommit.includes('debugMode'), 'must not gate by debugMode');
    // NAME PoC自体のgatingは今回の変更で緩められていないこと（対照確認）
    assert.ok(FN.maybeSendNameCommitPoc.includes('nameCommitPocEnabled'), 'NAME PoC gating must remain unchanged');
});

// O) 既存NAME/Short Choiceの世代・関数が今回の追加で壊れていない（回帰）
test('O regression: existing NAME/Short Choice forced-commit behavior is unaffected by the PHONE addition', () => {
    const { ctx, fireTimer } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('お名前を教えてください')", ctx);
    assert.strictEqual(ctx.expectedAnswerType, 'NAME');
    vm.runInContext("classifyExpectedAnswerType('午前ですか、午後ですか？')", ctx);
    assert.strictEqual(ctx.expectedAnswerType, 'SHORT_CHOICE');
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    fireTimer(ctx.answerWindowTimerId);
    assert.strictEqual(ctx.dc.sent.length, 1, 'Short Choice forced commit must still fire after the PHONE addition');
    assert.deepStrictEqual(ctx.dc.sent[0], { type: 'input_audio_buffer.commit' });
});

// P) PHONE_TURN_DETECTEDタイムラインイベントが記録される（診断用マーカー）
test('P: PHONE_TURN_DETECTED timeline marker is recorded when a PHONE question is classified', () => {
    const { ctx, events } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('お電話番号をお願いします')", ctx);
    assert.ok(events.some(e => e.startsWith('PHONE_TURN_DETECTED')), 'expected a PHONE_TURN_DETECTED timeline event');
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
if (failed > 0) process.exit(1);
