'use strict';

// SHORT_ANSWER Forced Commit（TIME/DATE/PARTY_SIZE専用）— regression test suite
//
// 背景（FAST ANSWER TURN / CUSTOMER-FIRST CONVERSATIONフェーズ）: 予約希望時間が
// 休憩時間等に該当し、AIが「別のお時間は何時をご希望ですか？」と尋ねた後、
// 客が新しい時間を回答してもAIが応答を再開せず会話が停止する症状が実機で
// 報告された。監査の結果、classifyExpectedAnswerType()にはTIME/DATE/
// PARTY_SIZEを尋ねる質問を検出する分類が一切存在せず、これらの質問はすべて
// expectedAnswerType='NONE'に分類され、Answer Window自体が開始しない
// （startAnswerWindowIfNeededが即returnする）ことが判明した。本テストは、
// 新しく追加した'SHORT_ANSWER'分類とmaybeSendQuickAnswerCommit()を検証する。
//
// このテストは、shop-ai-realtime-voice.html内の実際の関数ソースをHTMLファイルから
// 直接抽出し、Node.jsのvmモジュール上で最小限のモックとともに実行することで、
// 「本物の実装コード」に対してアサーションを行う（tests/test_phone_forced_commit.js
// / tests/test_short_choice_turn.js と同じ方式）。
//
// 実行: node tests/test_short_answer_turn.js

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

// Phase P1（Public AI Call）: shop-ai-realtime-voice.htmlのインライン<script>は
// frontend/public/js/realtime-voice-engine.jsへ無改変で外部化された（call.html
// と共有するため）。実装コードの実体はそちらに移ったため、抽出元もそちらへ
// 追従させる（テストの検証対象・手法自体は一切変更していない）。
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
    maybeSendQuickAnswerCommit: extractFunctionSource(SRC, 'maybeSendQuickAnswerCommit'),
    maybeLogQuickAnswerReactionElapsed: extractFunctionSource(SRC, 'maybeLogQuickAnswerReactionElapsed'),
    cancelAnswerWindow: extractFunctionSource(SRC, 'cancelAnswerWindow'),
    startTakeoverTimerIfNeeded: extractFunctionSource(SRC, 'startTakeoverTimerIfNeeded'),
};

const ANSWER_WINDOW_LIMITS_MS_EXPR = extractConstExpr(SRC, 'ANSWER_WINDOW_LIMITS_MS');
const ANSWER_WINDOW_LIMITS_MS = eval('(' + ANSWER_WINDOW_LIMITS_MS_EXPR + ')');

assert.strictEqual(ANSWER_WINDOW_LIMITS_MS.SHORT_ANSWER, 3000, 'SHORT_ANSWER limit must be 3000ms (same as YES_NO/SHORT_CHOICE)');
assert.strictEqual(ANSWER_WINDOW_LIMITS_MS.PHONE, 10000, 'PHONE limit must remain 10000ms (unchanged)');
assert.strictEqual(ANSWER_WINDOW_LIMITS_MS.NAME, 5000, 'NAME limit must remain 5000ms (unchanged)');
assert.strictEqual(ANSWER_WINDOW_LIMITS_MS.YES_NO, 3000, 'YES_NO limit must remain 3000ms (unchanged)');
assert.strictEqual(ANSWER_WINDOW_LIMITS_MS.SHORT_CHOICE, 3000, 'SHORT_CHOICE limit must remain 3000ms (unchanged)');
assert.strictEqual(ANSWER_WINDOW_LIMITS_MS.VISIT_REASON, 30000, 'VISIT_REASON limit must remain 30000ms (unchanged, not shortened to 3s)');

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
        quickAnswerGeneration: 0,
        quickAnswerCommitSentGeneration: null,
        quickAnswerTurnNormalCompletionSeen: false,
        quickAnswerCommitSentAt: null,
        quickAnswerCommitCallGeneration: null,
        quickAnswerCommitPendingEvents: { committed: false, item: false, responseCreated: false, aiAudioStarted: false },
        takeoverTimerId: null,
        takeoverSpeechEpisodeId: 0,
        takeoverPocEnabled: false,
        nameCommitPocEnabled: false, // maybeSendNameCommitPocが参照するため必要（通常の本番店舗を模す）
        debugMode: false,
        shopId: 'normal-production-shop-id-xxxx',
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

console.log('SHORT_ANSWER Forced Commit (TIME/DATE/PARTY_SIZE) regression tests');
console.log('source: ' + ENGINE_JS_PATH);
console.log('');

// A) 時刻質問の検出 -> SHORT_ANSWER
// 実際にapp/services/realtime_voice_ai.py（check_availability/create_reservation
// のTool説明文、time_in_past/outside_business_hoursの案内方針）で使われている
// 文言を確認した上でテストケース化している（憶測の例文ではない）。
test('A: classify) TIME question phrasing (verified against realtime_voice_ai.py wording) -> SHORT_ANSWER', () => {
    const examples = [
        '本日の18時はすでに過ぎております。別のお時間をご希望ですか？', // time_in_past（check_availability/create_reservation）
        'あいにくその曜日は11時から22時までの営業となっております。その時間帯でご都合のよいお時間はございますか？', // outside_business_hours
        '何時をご希望ですか？',
        'ご希望のお時間をお願いします',
    ];
    for (const transcript of examples) {
        const { ctx } = buildSandbox({});
        vm.runInContext('classifyExpectedAnswerType(' + JSON.stringify(transcript) + ')', ctx);
        assert.strictEqual(ctx.expectedAnswerType, 'SHORT_ANSWER', 'expected SHORT_ANSWER for: ' + transcript);
    }
});

// 人数質問の検出
test('A2: classify) PARTY_SIZE question phrasing -> SHORT_ANSWER', () => {
    const examples = ['何名様ですか？', '何名様でご利用ですか？'];
    for (const transcript of examples) {
        const { ctx } = buildSandbox({});
        vm.runInContext('classifyExpectedAnswerType(' + JSON.stringify(transcript) + ')', ctx);
        assert.strictEqual(ctx.expectedAnswerType, 'SHORT_ANSWER', 'expected SHORT_ANSWER for: ' + transcript);
    }
});

// 日付質問の検出
test('A3: classify) DATE question phrasing -> SHORT_ANSWER', () => {
    const examples = ['いつをご希望ですか？', '何日をご希望ですか？'];
    for (const transcript of examples) {
        const { ctx } = buildSandbox({});
        vm.runInContext('classifyExpectedAnswerType(' + JSON.stringify(transcript) + ')', ctx);
        assert.strictEqual(ctx.expectedAnswerType, 'SHORT_ANSWER', 'expected SHORT_ANSWER for: ' + transcript);
    }
});

// B) 正常VADが即確定すれば即進む（3秒まで待たない = タイマー自体はキャンセル可能）
test('B: normal VAD completing quickly cancels the SHORT_ANSWER window before it fires', () => {
    const { ctx, timerCount } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('別のお時間は何時がよろしいですか？')", ctx);
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    assert.strictEqual(timerCount(), 1);
    vm.runInContext("cancelAnswerWindow('speech_stopped_normally')", ctx);
    assert.strictEqual(timerCount(), 0, 'normal completion should cancel the pending SHORT_ANSWER window immediately');
});

// C) 正常完了なし -> 最大3秒でForced Commit
test('C: forced commit fires at the 3s ceiling when no normal completion was seen', () => {
    const { ctx, fireTimer, timerDelay } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('別のお時間は何時がよろしいですか？')", ctx);
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    const timerId = ctx.answerWindowTimerId;
    assert.strictEqual(timerDelay(timerId), 3000);
    fireTimer(timerId);
    assert.strictEqual(ctx.dc.sent.length, 1);
    assert.deepStrictEqual(ctx.dc.sent[0], { type: 'input_audio_buffer.commit' });
});

// D) 二重commit防止
test('D: at most one manual commit per SHORT_ANSWER generation', () => {
    const { ctx, fireTimer } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('何名様ですか？')", ctx);
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    fireTimer(ctx.answerWindowTimerId);
    assert.strictEqual(ctx.dc.sent.length, 1);
    vm.runInContext('maybeSendQuickAnswerCommit("SHORT_ANSWER", 1)', ctx);
    assert.strictEqual(ctx.dc.sent.length, 1, 'must not send a second commit for the same generation');
    assert.ok(!ctx.dc.sent.some(m => m.type === 'response.create'));
});

// E) PHONEは10秒のまま（回帰）
test('E: PHONE question still gets the unchanged 10000ms ceiling after the SHORT_ANSWER addition', () => {
    const { ctx, timerDelay } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('お電話番号をお願いします')", ctx);
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    assert.strictEqual(timerDelay(ctx.answerWindowTimerId), 10000);
});

// F) NAMEは既存挙動のまま（回帰）
test('F: NAME question still classifies as NAME and keeps its 5000ms ceiling (unaffected)', () => {
    const { ctx, timerDelay } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('お名前を教えてください')", ctx);
    assert.strictEqual(ctx.expectedAnswerType, 'NAME');
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    assert.strictEqual(timerDelay(ctx.answerWindowTimerId), 5000);
});

// G) YES_NOは既存3秒のまま（回帰）
test('G: YES_NO question still classifies as YES_NO and keeps its 3000ms ceiling (unaffected)', () => {
    const { ctx, fireTimer } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('この内容でよろしいですか？')", ctx);
    assert.strictEqual(ctx.expectedAnswerType, 'YES_NO');
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    fireTimer(ctx.answerWindowTimerId);
    assert.strictEqual(ctx.dc.sent.length, 1, 'Short Choice forced commit for YES_NO must still fire');
});

// H) SHORT_CHOICEは既存3秒のまま（回帰）
test('H: SHORT_CHOICE question still classifies as SHORT_CHOICE (unaffected)', () => {
    const { ctx } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('午前ですか、午後ですか？')", ctx);
    assert.strictEqual(ctx.expectedAnswerType, 'SHORT_CHOICE');
});

// I) VISIT_REASONは3秒にしない（30秒のまま。SHORT_ANSWERへ誤分類しない）
test('I: VISIT_REASON question is NOT reclassified as SHORT_ANSWER (stays 30000ms, long free-form answers preserved)', () => {
    const { ctx, timerDelay } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('ご来店の目的を教えてください')", ctx);
    assert.strictEqual(ctx.expectedAnswerType, 'VISIT_REASON');
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    assert.strictEqual(timerDelay(ctx.answerWindowTimerId), 30000);
});

// J) 自由回答「どのようなご用件でしょうか？」は3秒にしない（NONEのまま。既存挙動保持）
test('J: generic free-talk question ("どのようなご用件でしょうか？") is NOT classified as SHORT_ANSWER', () => {
    const { ctx } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('どのようなご用件でしょうか？')", ctx);
    assert.notStrictEqual(ctx.expectedAnswerType, 'SHORT_ANSWER');
});

// K) 休憩時間→別時間→3時: 停止しないこと（コアシナリオ）のtimer/commitレベル確認
test('K: core scenario) break_time re-ask ("別のお時間をご希望ですか？") gets a working SHORT_ANSWER safety net', () => {
    const { ctx, fireTimer } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('その時間は休憩時間のため、別のお時間をご希望ですか？')", ctx);
    assert.strictEqual(ctx.expectedAnswerType, 'SHORT_ANSWER');
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    fireTimer(ctx.answerWindowTimerId);
    assert.strictEqual(ctx.dc.sent.length, 1, 'the previously-unprotected break_time re-ask must now get a forced commit if VAD stalls');
});

// L) 営業時間外/満席/過去時刻の再提案も同じ仕組みで保護される
test('L: outside_business_hours / fully_booked / time_in_past re-asks also classify as SHORT_ANSWER', () => {
    const examples = [
        '本日は満席のため、別のお時間をご希望ですか？',
        'その時間は営業時間外のため、別のお時間をご希望ですか？',
        '本日の18時はすでに過ぎております。別のお時間をご希望ですか？', // 実際のtime_in_past文言
    ];
    for (const transcript of examples) {
        const { ctx } = buildSandbox({});
        vm.runInContext('classifyExpectedAnswerType(' + JSON.stringify(transcript) + ')', ctx);
        assert.strictEqual(ctx.expectedAnswerType, 'SHORT_ANSWER', 'expected SHORT_ANSWER for: ' + transcript);
    }
});

// M) 複数回の再提案それぞれが独立した世代としてForced Commitの機会を持つ
test('M: repeated re-asks (e.g. break_time then fully_booked) each get their own forced-commit generation', () => {
    const { ctx, fireTimer } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('その時間は休憩時間のため、別のお時間をご希望ですか？')", ctx);
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    fireTimer(ctx.answerWindowTimerId);
    assert.strictEqual(ctx.dc.sent.length, 1);
    assert.strictEqual(ctx.quickAnswerGeneration, 1);
    ctx.answerWindowTimerId = null; // 実装ではAnswer Window終了時にnullへ戻る
    vm.runInContext("classifyExpectedAnswerType('その時間は満席のため、別のお時間をご希望ですか？')", ctx);
    assert.strictEqual(ctx.quickAnswerGeneration, 2, 'a second re-ask should bump the generation, like Short Choice');
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    fireTimer(ctx.answerWindowTimerId);
    assert.strictEqual(ctx.dc.sent.length, 2, 'the second re-ask should get its own forced commit');
});

// N) response.create不送信の確認（ソースレベル）
test('N: maybeSendQuickAnswerCommit never constructs or sends a response.create payload', () => {
    assert.ok(!FN.maybeSendQuickAnswerCommit.includes('sendResponseCreate('), 'must not call sendResponseCreate');
    assert.ok(!/type:\s*['"]response\.create['"]/.test(FN.maybeSendQuickAnswerCommit), 'must never construct a response.create payload');
    assert.ok(FN.maybeSendQuickAnswerCommit.includes("type: 'input_audio_buffer.commit'"));
});

// O) callGeneration/isStaleCallEventガードの再利用
test('O: stale callGeneration aborts the SHORT_ANSWER forced commit (isStaleCallEvent guard reused)', () => {
    const { ctx, fireTimer } = buildSandbox({ callGeneration: 1 });
    vm.runInContext("classifyExpectedAnswerType('何名様ですか？')", ctx);
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    const timerId = ctx.answerWindowTimerId;
    ctx.callGeneration = 2; // 新しい通話が始まった
    fireTimer(timerId);
    assert.strictEqual(ctx.dc.sent.length, 0);
});

// P) Conversation Takeoverとの非競合: SHORT_ANSWERターン中はTakeoverタイマーが開始しない
test('P: Conversation Takeover timer never starts while expectedAnswerType is SHORT_ANSWER', () => {
    const { ctx } = buildSandbox({ takeoverPocEnabled: true, debugMode: true, shopId: '65932cb5-97db-460e-b9d6-0471fce23d88' });
    ctx.expectedAnswerType = 'SHORT_ANSWER';
    vm.runInContext('startTakeoverTimerIfNeeded(1)', ctx);
    assert.strictEqual(ctx.takeoverTimerId, null, 'takeover timer must not start while expectedAnswerType=SHORT_ANSWER');
});

// Q) 全店舗適用の確認: gatingを一切行わない
test('Q: maybeSendQuickAnswerCommit performs no shop_id/debug gating (applies to all production shops)', () => {
    assert.ok(!FN.maybeSendQuickAnswerCommit.includes('shopId'), 'must not gate by shopId');
    assert.ok(!FN.maybeSendQuickAnswerCommit.includes('debugMode'), 'must not gate by debugMode');
    assert.ok(FN.maybeSendNameCommitPoc.includes('nameCommitPocEnabled'), 'NAME PoC gating must remain unchanged');
});

// R) datachannel未オープン/send例外は非fatal
test('R: robustness) datachannel not open / send exception are non-fatal', () => {
    const { ctx: ctxClosed, fireTimer: fireClosed } = buildSandbox({ dc: { readyState: 'closed', sent: [], send() { throw new Error('should not be called'); } } });
    vm.runInContext("classifyExpectedAnswerType('何名様ですか？')", ctxClosed);
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctxClosed);
    assert.doesNotThrow(() => fireClosed(ctxClosed.answerWindowTimerId));

    const { ctx: ctxThrow, fireTimer: fireThrow, events } = buildSandbox({ dc: { readyState: 'open', sent: [], send() { throw new Error('simulated send failure'); } } });
    vm.runInContext("classifyExpectedAnswerType('何名様ですか？')", ctxThrow);
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctxThrow);
    assert.doesNotThrow(() => fireThrow(ctxThrow.answerWindowTimerId));
    assert.ok(events.some(e => e.startsWith('QUICK_ANSWER_COMMIT_ERROR')));
});

// S) 優先順位確認: PHONE/NAME/YES_NO/SHORT_CHOICEがSHORT_ANSWERに誤分類されない
test('S: priority) PHONE/NAME/YES_NO/SHORT_CHOICE phrasing is never misclassified as SHORT_ANSWER', () => {
    const cases = [
        ['お電話番号をお願いします', 'PHONE'],
        ['お名前を教えてください', 'NAME'],
        ['この内容でよろしいですか？', 'YES_NO'],
        ['午前ですか、午後ですか？', 'SHORT_CHOICE'],
    ];
    for (const [transcript, expected] of cases) {
        const { ctx } = buildSandbox({});
        vm.runInContext('classifyExpectedAnswerType(' + JSON.stringify(transcript) + ')', ctx);
        assert.strictEqual(ctx.expectedAnswerType, expected, 'expected ' + expected + ' for: ' + transcript);
    }
});

// T) 複数情報発話後の回帰: multi-slot発話（本テストではclassify自体はAI側発話にのみ
// 依存するため、既存分類ロジックが今回の追加で壊れていないことを再確認する）
test('T regression: existing NAME/PHONE/Short Choice classification and forced commits still work after the SHORT_ANSWER addition', () => {
    const { ctx: ctxName, fireTimer: fireName } = buildSandbox({ dc: { readyState: 'open', sent: [], send(p) { this.sent.push(JSON.parse(p)); } } });
    vm.runInContext("classifyExpectedAnswerType('お名前を教えてください')", ctxName);
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctxName);
    fireName(ctxName.answerWindowTimerId);
    assert.strictEqual(ctxName.dc.sent.length, 0, 'NAME PoC gating unchanged (normal shop): no commit sent');

    const { ctx: ctxPhone, fireTimer: firePhone } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('お電話番号をお願いします')", ctxPhone);
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctxPhone);
    firePhone(ctxPhone.answerWindowTimerId);
    assert.strictEqual(ctxPhone.dc.sent.length, 1, 'PHONE forced commit still fires');
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
if (failed > 0) process.exit(1);
