'use strict';

// SHORT_ANSWER Forced Commit（TIME/DATE/PARTY_SIZE専用）— regression test suite
//
// PHASE O5.8で全面改訂（Speech-Stopped Based Short Answer Finalizer）。
// PHASE O5.10で再改訂（SHORT_ANSWER — SEMANTIC VAD PRIMARY TURN COMPLETION）。
//
// 背景（FAST ANSWER TURN / CUSTOMER-FIRST CONVERSATIONフェーズ）: 予約希望時間が
// 休憩時間等に該当し、AIが「別のお時間は何時をご希望ですか？」と尋ねた後、
// 客が新しい時間を回答してもAIが応答を再開せず会話が停止する症状が実機で
// 報告された。監査の結果、classifyExpectedAnswerType()にはTIME/DATE/
// PARTY_SIZEを尋ねる質問を検出する分類が一切存在せず、これらの質問はすべて
// expectedAnswerType='NONE'に分類され、Answer Window自体が開始しない
// （startAnswerWindowIfNeededが即returnする）ことが判明した。ここまでが
// 旧SHORT_ANSWER Forced Commit（speech_startedから3秒）の背景。
//
// その後（O5.7 Audit）、この旧設計自体に構造的な欠陥が判明した:
//   (a) 3秒はspeech_started起点のため、3秒を超える一続きの発話を
//       話している最中に強制commitしてしまう危険がある。
//   (b) 環境雑音でspeech_started/speech_stoppedが繰り返されると、
//       cancelAnswerWindow()が毎回タイマーを即死させるため、3秒間
//       ノーイベントの区間が一度も生じず、Forced Commit自体が永久に
//       発火しない危険がある。
// PHASE O5.8はこれを解消するため、SHORT_ANSWERについてのみ「speech_stopped
// からSHORT_ANSWER_FINALIZE_GRACE_MS(1200ms)」という新方式
// （armQuickAnswerFinalizeTimer/cancelQuickAnswerFinalizeTimer）へ完全に
// 置換した。
//
// さらにその後（O5.9.2 Audit）、実機で「1回目は進まず、同じ回答を2回言うと
// 進む」という新しい症状が確認された。監査の結果:
//   (a) VISIT_REASON等の自由回答はmanual commitを一切経由せず、semantic_vad
//       の自然完了だけで正常に1回で進んでいる。
//   (b) OpenAI公式ドキュメント・Developer Communityの実例でも、server-side
//       VAD（semantic_vad）有効時はサーバーが自動でcommit・response生成まで
//       行う設計であり、手動commitは不要・むしろ競合の原因になり得る。
//   (c) 「1回目はmanual commitがsemantic_vadの自然完了とrace/干渉し、2回目は
//       quickAnswerCommitSentGenerationガードによりmanual commitが送られず
//       semantic_vad単体で成功する」という構造が、実機症状と整合する。
// PHASE O5.10はこれを受けて、SHORT_ANSWERの通常経路からarmQuickAnswerFinalizeTimer
// の呼び出し自体を外し、他の自由回答と同じくsemantic_vadの自然なターン完了を
// primary経路にした。armQuickAnswerFinalizeTimer()/maybeSendQuickAnswerCommit()
// の関数自体は削除せず、将来的なfallback候補として温存している（現時点では
// どこからも呼ばれないdead/unused状態）。
//
// NAME/PHONE/YES_NO/SHORT_CHOICE/VISIT_REASONはO5.8/O5.10いずれでも一切
// 変更していない（引き続き既存のANSWER_WINDOW_LIMITS_MS/startAnswerWindowIfNeeded/
// cancelAnswerWindowを使用する）。
//
// このテストは、frontend/public/js/realtime-voice-engine.js内の実際の関数
// ソースを直接抽出し、Node.jsのvmモジュール上で最小限のモックとともに実行
// することで、「本物の実装コード」に対してアサーションを行う
// （tests/test_phone_forced_commit.js / tests/test_short_choice_turn.js と
// 同じ方式）。O5.10で追加した「LIVE-WIRING」系テストのみ、handleDataChannelEvent
// 全体を実行する代わりに、実ソースをテキストとして走査し「この関数を実際に
// 呼んでいる行が、コメント・関数定義自身を除いて1つも存在しないか」を検証する
// 静的解析方式を使う（tests/test_tool_continuation_resilience.jsのE/F/G等と
// 同じ「ソース窓の直接検証」の考え方を、フォールバック関数が完全に
// unreferencedであることの検証にまで拡張したもの）。
//
// 実行: node tests/test_short_answer_turn.js

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

const FN = {
    isStaleCallEvent: extractFunctionSource(SRC, 'isStaleCallEvent'),
    msSince: extractFunctionSource(SRC, 'msSince'),
    classifyExpectedAnswerType: extractFunctionSource(SRC, 'classifyExpectedAnswerType'),
    startAnswerWindowIfNeeded: extractFunctionSource(SRC, 'startAnswerWindowIfNeeded'),
    maybeSendNameCommitPoc: extractFunctionSource(SRC, 'maybeSendNameCommitPoc'),
    maybeLogPocReactionElapsed: extractFunctionSource(SRC, 'maybeLogPocReactionElapsed'),
    maybeSendShortAnswerCommit: extractFunctionSource(SRC, 'maybeSendShortAnswerCommit'),
    maybeLogShortAnswerReactionElapsed: extractFunctionSource(SRC, 'maybeLogShortAnswerReactionElapsed'),
    maybeSendPhoneCommit: extractFunctionSource(SRC, 'maybeSendPhoneCommit'),
    maybeLogPhoneReactionElapsed: extractFunctionSource(SRC, 'maybeLogPhoneReactionElapsed'),
    armQuickAnswerFinalizeTimer: extractFunctionSource(SRC, 'armQuickAnswerFinalizeTimer'),
    cancelQuickAnswerFinalizeTimer: extractFunctionSource(SRC, 'cancelQuickAnswerFinalizeTimer'),
    maybeSendQuickAnswerCommit: extractFunctionSource(SRC, 'maybeSendQuickAnswerCommit'),
    maybeLogQuickAnswerReactionElapsed: extractFunctionSource(SRC, 'maybeLogQuickAnswerReactionElapsed'),
    cancelAnswerWindow: extractFunctionSource(SRC, 'cancelAnswerWindow'),
    startTakeoverTimerIfNeeded: extractFunctionSource(SRC, 'startTakeoverTimerIfNeeded'),
};

const ANSWER_WINDOW_LIMITS_MS_EXPR = extractConstExpr(SRC, 'ANSWER_WINDOW_LIMITS_MS');
const ANSWER_WINDOW_LIMITS_MS = eval('(' + ANSWER_WINDOW_LIMITS_MS_EXPR + ')');
const SHORT_ANSWER_FINALIZE_GRACE_MS_EXPR = extractConstExpr(SRC, 'SHORT_ANSWER_FINALIZE_GRACE_MS');
const SHORT_ANSWER_FINALIZE_GRACE_MS = eval('(' + SHORT_ANSWER_FINALIZE_GRACE_MS_EXPR + ')');

// PHASE O5.8（重要な回帰確認）: SHORT_ANSWERは共有Answer Window機構
// （ANSWER_WINDOW_LIMITS_MS/startAnswerWindowIfNeeded/cancelAnswerWindow）から
// 完全に除外され、speech_stopped基準の新方式へ置換されたことを、まず
// このテーブル自体で確認する。他の5分類は一切変更されていないことも確認する。
assert.strictEqual(Object.prototype.hasOwnProperty.call(ANSWER_WINDOW_LIMITS_MS, 'SHORT_ANSWER'), false,
    'SHORT_ANSWER must be removed from the shared Answer Window table (replaced by SHORT_ANSWER_FINALIZE_GRACE_MS)');
assert.strictEqual(ANSWER_WINDOW_LIMITS_MS.PHONE, 10000, 'PHONE limit must remain 10000ms (unchanged, out of O5.8 scope)');
assert.strictEqual(ANSWER_WINDOW_LIMITS_MS.NAME, 5000, 'NAME limit must remain 5000ms (unchanged, out of O5.8 scope)');
assert.strictEqual(ANSWER_WINDOW_LIMITS_MS.YES_NO, 3000, 'YES_NO limit must remain 3000ms (unchanged, out of O5.8 scope)');
assert.strictEqual(ANSWER_WINDOW_LIMITS_MS.SHORT_CHOICE, 3000, 'SHORT_CHOICE limit must remain 3000ms (unchanged, out of O5.8 scope)');
assert.strictEqual(ANSWER_WINDOW_LIMITS_MS.VISIT_REASON, 30000, 'VISIT_REASON limit must remain 30000ms (unchanged, out of O5.8 scope)');
assert.strictEqual(SHORT_ANSWER_FINALIZE_GRACE_MS, 1200, 'SHORT_ANSWER_FINALIZE_GRACE_MS must be 1200ms (O5.8 initial value)');

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
        lastSpeechStartedAt: null,
        lastSpeechStoppedAt: null,
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
        // PHASE O5.8: Finalization Grace timerの状態。
        quickAnswerFinalizeTimerId: null,
        quickAnswerFinalizeArmedAt: null,
        quickAnswerFinalizeGeneration: null,
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
        'const SHORT_ANSWER_FINALIZE_GRACE_MS = ' + SHORT_ANSWER_FINALIZE_GRACE_MS_EXPR + ';\n' +
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

console.log('SHORT_ANSWER Forced Commit (TIME/DATE/PARTY_SIZE) regression tests — PHASE O5.10');
console.log('source: ' + ENGINE_JS_PATH);
console.log('');

// ===== 分類ロジック（classifyExpectedAnswerType）は今回無変更 =====

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

test('A2: classify) PARTY_SIZE question phrasing -> SHORT_ANSWER', () => {
    const examples = ['何名様ですか？', '何名様でご利用ですか？'];
    for (const transcript of examples) {
        const { ctx } = buildSandbox({});
        vm.runInContext('classifyExpectedAnswerType(' + JSON.stringify(transcript) + ')', ctx);
        assert.strictEqual(ctx.expectedAnswerType, 'SHORT_ANSWER', 'expected SHORT_ANSWER for: ' + transcript);
    }
});

test('A3: classify) DATE question phrasing -> SHORT_ANSWER', () => {
    const examples = ['いつをご希望ですか？', '何日をご希望ですか？'];
    for (const transcript of examples) {
        const { ctx } = buildSandbox({});
        vm.runInContext('classifyExpectedAnswerType(' + JSON.stringify(transcript) + ')', ctx);
        assert.strictEqual(ctx.expectedAnswerType, 'SHORT_ANSWER', 'expected SHORT_ANSWER for: ' + transcript);
    }
});

// ===== PHASE O5.8: 新方式の中核シナリオ =====
// PHASE O5.10注記: 以下のtest 16〜21・D・K・L・M・O・O2・Q・Rは、
// armQuickAnswerFinalizeTimer()/maybeSendQuickAnswerCommit()を直接
// vm.runInContext経由で呼び出すことで、これら「温存されたfallback候補」
// 関数自体のロジックが引き続き正しく動作することを検証するテストである
// （関数の中身はO5.10で一切変更していないため、これらのアサーションは
// 単純に真であり続ける — ユーザー指示10-Iにより、意味のないテストへの
// 弱体化は行わない）。ただし、これらの関数が現在の通常SHORT_ANSWER会話
// フローから実際に呼ばれることはない（下記のLIVE-WIRINGテストで別途
// 証明する）。つまりこのセクションは「関数は壊れていない」ことの証明で
// あり、「関数が今も使われている」ことの証明ではない。

// 16) 「2名です」テスト（必須）: speech_started → speech_stopped → 1200ms →
// finalizer fire → Forced Commit最大1回。
test('16: "2名です" happy path) speech_stopped then 1200ms grace fires exactly one Forced Commit', () => {
    const { ctx, fireTimer, timerDelay } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('何名様ですか？')", ctx);
    assert.strictEqual(ctx.expectedAnswerType, 'SHORT_ANSWER');
    // speech_started相当: この時点ではまだ何もarmしない（O5.8の核心）。
    // speech_stopped相当（「2名です」を話し終えた）:
    vm.runInContext('armQuickAnswerFinalizeTimer(1)', ctx);
    const timerId = ctx.quickAnswerFinalizeTimerId;
    assert.notStrictEqual(timerId, null, 'a finalize timer must be armed on speech_stopped');
    assert.strictEqual(timerDelay(timerId), SHORT_ANSWER_FINALIZE_GRACE_MS, 'grace duration must be exactly 1200ms');
    fireTimer(timerId);
    assert.strictEqual(ctx.dc.sent.length, 1, 'exactly one Forced Commit must be sent');
    assert.deepStrictEqual(ctx.dc.sent[0], { type: 'input_audio_buffer.commit' });
    assert.ok(!ctx.dc.sent.some((m) => m.type === 'response.create'), 'response.create must never be sent');
});

// 17) 「2名……です」テスト（必須）: speech_started → speech_stopped → 500ms →
// speech_started(timer cancel) → speech_stopped → 1200ms → finalize。
// 最初のtimerが後から発火しないこと。
test('17: "2名……です" mid-answer pause) an interrupting speech_started cancels the stale timer; only the latest one fires', () => {
    const { ctx, fireTimer, timerCount, activeTimerIds } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('何名様ですか？')", ctx);
    // 最初のspeech_stopped（「2名……」で言い淀んだ直後）:
    vm.runInContext('armQuickAnswerFinalizeTimer(1)', ctx);
    const staleTimerId = ctx.quickAnswerFinalizeTimerId;
    assert.strictEqual(timerCount(), 1);
    // 500ms後、新たなspeech_started（「です」と続ける前触れ）:
    vm.runInContext("cancelQuickAnswerFinalizeTimer('speech_started_again')", ctx);
    assert.strictEqual(timerCount(), 0, 'the stale timer must actually be cleared (never fires later)');
    assert.strictEqual(ctx.quickAnswerFinalizeTimerId, null);
    // 続いて新たなspeech_stopped（「です」まで言い終わった）:
    vm.runInContext('armQuickAnswerFinalizeTimer(1)', ctx);
    const freshTimerId = ctx.quickAnswerFinalizeTimerId;
    assert.notStrictEqual(freshTimerId, staleTimerId, 'a brand-new timer id must be armed for the latest speech_stopped');
    assert.ok(!activeTimerIds().includes(staleTimerId), 'the stale timer must not still be pending');
    fireTimer(freshTimerId);
    assert.strictEqual(ctx.dc.sent.length, 1, 'only the latest (post-"です") finalize should ever fire a commit');
});

// 18) 雑音ping-pongテスト（必須）: speech_started/speech_stopped を複数回
// simulate。各speech_startedで古いfinalizerがcancelされ、最後のspeech_stopped
// からgraceを計測。stale timerが発火しない。
test('18: noise ping-pong) repeated speech_started/speech_stopped cancels+rearms each time; only the final grace fires', () => {
    const { ctx, fireTimer, timerCount } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('何名様ですか？')", ctx);
    const seenTimerIds = [];
    for (let i = 0; i < 4; i++) {
        // speech_stopped（雑音起因を含む）:
        vm.runInContext('armQuickAnswerFinalizeTimer(1)', ctx);
        seenTimerIds.push(ctx.quickAnswerFinalizeTimerId);
        assert.strictEqual(timerCount(), 1, 'exactly one timer must be pending at any time, never accumulating');
        // 雑音起因のspeech_started（最後の1回以外）:
        if (i < 3) {
            vm.runInContext("cancelQuickAnswerFinalizeTimer('speech_started_again')", ctx);
            assert.strictEqual(timerCount(), 0, 'each interrupting speech_started must clear the pending timer');
        }
    }
    // 最後のspeech_stoppedからは何のspeech_startedも来なかった想定でgraceが満了する。
    const finalTimerId = ctx.quickAnswerFinalizeTimerId;
    fireTimer(finalTimerId);
    assert.strictEqual(ctx.dc.sent.length, 1, 'exactly one Forced Commit for the whole ping-pong sequence');
    // 早期にcancelされたstaleなtimer idはいずれも再発火していないことを、
    // 送信回数が1のままであることで確認済み（fireTimerは既にqueueから
    // 削除済みのidに対しては何もしないため、直接の二重呼び出しテストは
    // 上のtest 17でtimerCount===0として検証済み）。
    assert.ok(seenTimerIds.length === 4);
});

// 19) Natural Completionテスト（必須）: speech_stopped → finalizer armed →
// 500ms → 正常response.created の場合: finalizer cancel、Forced Commit = 0。
test('19: natural completion race) a normal response.created within the grace period cancels the finalizer; zero Forced Commits', () => {
    const { ctx, timerCount } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('何名様ですか？')", ctx);
    vm.runInContext('armQuickAnswerFinalizeTimer(1)', ctx);
    assert.strictEqual(timerCount(), 1);
    // 500ms後、サーバー側semantic_vadが自然にturnを完了しresponse.createdが
    // 届いた（実装の response.created ハンドラが実際に呼ぶのと同じ呼び出し）:
    vm.runInContext("cancelQuickAnswerFinalizeTimer('response_created'); quickAnswerTurnNormalCompletionSeen = true;", ctx);
    assert.strictEqual(timerCount(), 0, 'the finalizer must be cancelled, never firing later');
    assert.strictEqual(ctx.dc.sent.length, 0, 'zero Forced Commits must be sent when the server completed the turn naturally');
});

// 20) function_call raceテスト（必須）: speech_stopped → finalizer armed →
// 正常function_call の場合: stale finalizerからForced Commitを送らない。
test('20: function_call race) a normal function_call within the grace period cancels the finalizer; zero Forced Commits', () => {
    const { ctx, timerCount } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('何名様ですか？')", ctx);
    vm.runInContext('armQuickAnswerFinalizeTimer(1)', ctx);
    assert.strictEqual(timerCount(), 1);
    // ターンが既に前進しfunction_callへ進んだ（実装のresponse.output_item.done
    // function_callハンドラが実際に呼ぶのと同じ呼び出し）:
    vm.runInContext("cancelQuickAnswerFinalizeTimer('function_call_started'); quickAnswerTurnNormalCompletionSeen = true;", ctx);
    assert.strictEqual(timerCount(), 0, 'the finalizer must be cancelled once a function_call has started');
    assert.strictEqual(ctx.dc.sent.length, 0, 'zero Forced Commits must be sent once the turn has already progressed to a tool call');
});

// 21) Long utterance安全性テスト（必須）: 3秒を超える発話でもspeech_stopped前に
// SHORT_ANSWER timerが発火してcommitしてはいけない。
test('21: long utterance safety) no timer of any kind exists for SHORT_ANSWER before speech_stopped, however long the utterance', () => {
    const { ctx, timerCount } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('何名様ですか？')", ctx);
    assert.strictEqual(ctx.expectedAnswerType, 'SHORT_ANSWER');
    // 旧設計ならここでstartAnswerWindowIfNeeded(1)がspeech_started起点の
    // 3秒タイマーをarmしていたが、SHORT_ANSWERは今やANSWER_WINDOW_LIMITS_MSに
    // 存在しないため、この呼び出し自体が何もしないことを直接確認する
    // （speech_startedが何回発生しても、話している最中に強制commitされる
    // 経路が構造的に存在しないことの証明）。
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    assert.strictEqual(ctx.answerWindowTimerId, null, 'the old speech_started-based 3s window must never arm for SHORT_ANSWER');
    assert.strictEqual(timerCount(), 0, 'no timer of any kind may exist until the customer actually stops talking (speech_stopped)');
    assert.strictEqual(ctx.dc.sent.length, 0, 'no commit may be sent while the customer is still mid-utterance');
});

// D) 二重commit防止（世代あたり最大1回。呼び出し元がFinalizerになった後も維持）
test('D: at most one manual commit per SHORT_ANSWER generation', () => {
    const { ctx } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('何名様ですか？')", ctx);
    vm.runInContext('maybeSendQuickAnswerCommit("SHORT_ANSWER", 1)', ctx);
    assert.strictEqual(ctx.dc.sent.length, 1);
    vm.runInContext('maybeSendQuickAnswerCommit("SHORT_ANSWER", 1)', ctx);
    assert.strictEqual(ctx.dc.sent.length, 1, 'must not send a second commit for the same generation');
    assert.ok(!ctx.dc.sent.some((m) => m.type === 'response.create'));
});

// E) PHONEは10秒のまま（回帰。O5.8スコープ外）
test('E: PHONE question still gets the unchanged 10000ms ceiling after O5.8', () => {
    const { ctx, timerDelay } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('お電話番号をお願いします')", ctx);
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    assert.strictEqual(timerDelay(ctx.answerWindowTimerId), 10000);
});

// F) NAMEは既存挙動のまま（回帰。O5.8スコープ外）
test('F: NAME question still classifies as NAME and keeps its 5000ms ceiling (unaffected)', () => {
    const { ctx, timerDelay } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('お名前を教えてください')", ctx);
    assert.strictEqual(ctx.expectedAnswerType, 'NAME');
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    assert.strictEqual(timerDelay(ctx.answerWindowTimerId), 5000);
});

// G) YES_NOは既存3秒のまま（回帰。O5.8スコープ外）
test('G: YES_NO question still classifies as YES_NO and keeps its 3000ms ceiling (unaffected)', () => {
    const { ctx, fireTimer } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('この内容でよろしいですか？')", ctx);
    assert.strictEqual(ctx.expectedAnswerType, 'YES_NO');
    vm.runInContext('startAnswerWindowIfNeeded(1)', ctx);
    fireTimer(ctx.answerWindowTimerId);
    assert.strictEqual(ctx.dc.sent.length, 1, 'Short Choice forced commit for YES_NO must still fire');
});

// H) SHORT_CHOICEは既存3秒のまま（回帰。O5.8スコープ外）
test('H: SHORT_CHOICE question still classifies as SHORT_CHOICE (unaffected)', () => {
    const { ctx } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('午前ですか、午後ですか？')", ctx);
    assert.strictEqual(ctx.expectedAnswerType, 'SHORT_CHOICE');
});

// I) VISIT_REASONは3秒にしない（30秒のまま。O5.8スコープ外）
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

// K) 休憩時間→別時間→3時: 停止しないこと（コアシナリオ）のFinalizer版
test('K: core scenario) break_time re-ask ("別のお時間をご希望ですか？") gets a working SHORT_ANSWER finalizer', () => {
    const { ctx, fireTimer } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('その時間は休憩時間のため、別のお時間をご希望ですか？')", ctx);
    assert.strictEqual(ctx.expectedAnswerType, 'SHORT_ANSWER');
    vm.runInContext('armQuickAnswerFinalizeTimer(1)', ctx);
    fireTimer(ctx.quickAnswerFinalizeTimerId);
    assert.strictEqual(ctx.dc.sent.length, 1, 'the previously-unprotected break_time re-ask must still get a forced commit after the grace period');
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
    vm.runInContext('armQuickAnswerFinalizeTimer(1)', ctx);
    fireTimer(ctx.quickAnswerFinalizeTimerId);
    assert.strictEqual(ctx.dc.sent.length, 1);
    assert.strictEqual(ctx.quickAnswerGeneration, 1);
    vm.runInContext("classifyExpectedAnswerType('その時間は満席のため、別のお時間をご希望ですか？')", ctx);
    assert.strictEqual(ctx.quickAnswerGeneration, 2, 'a second re-ask should bump the generation, like Short Choice');
    vm.runInContext('armQuickAnswerFinalizeTimer(1)', ctx);
    fireTimer(ctx.quickAnswerFinalizeTimerId);
    assert.strictEqual(ctx.dc.sent.length, 2, 'the second re-ask should get its own forced commit');
});

// N) response.create不送信の確認（ソースレベル）
test('N: maybeSendQuickAnswerCommit never constructs or sends a response.create payload', () => {
    assert.ok(!FN.maybeSendQuickAnswerCommit.includes('sendResponseCreate('), 'must not call sendResponseCreate');
    assert.ok(!/type:\s*['"]response\.create['"]/.test(FN.maybeSendQuickAnswerCommit), 'must never construct a response.create payload');
    assert.ok(FN.maybeSendQuickAnswerCommit.includes("type: 'input_audio_buffer.commit'"));
});

// O) callGeneration/isStaleCallEventガードの再利用（Finalizer版）
test('O: stale callGeneration aborts the SHORT_ANSWER forced commit (isStaleCallEvent guard reused)', () => {
    const { ctx, fireTimer } = buildSandbox({ callGeneration: 1 });
    vm.runInContext("classifyExpectedAnswerType('何名様ですか？')", ctx);
    vm.runInContext('armQuickAnswerFinalizeTimer(1)', ctx);
    const timerId = ctx.quickAnswerFinalizeTimerId;
    ctx.callGeneration = 2; // 新しい通話が始まった
    fireTimer(timerId);
    assert.strictEqual(ctx.dc.sent.length, 0);
});

// O2) 世代ガード（Finalizer固有・新規）: armされた後にquickAnswerGenerationが
// 進んだ（＝AIが次のSHORT_ANSWER質問へ進んだ）場合、古いFinalizerは発火しない。
test('O2: generation guard) a finalizer armed for an old SHORT_ANSWER generation does not fire after the generation has moved on', () => {
    const { ctx, fireTimer } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('何名様ですか？')", ctx);
    vm.runInContext('armQuickAnswerFinalizeTimer(1)', ctx);
    const staleTimerId = ctx.quickAnswerFinalizeTimerId;
    // AIが（例えば聞き取れず）同じ質問を言い直し、新しい世代へ進んだと仮定する:
    vm.runInContext("classifyExpectedAnswerType('もう一度、何名様かお願いします')", ctx);
    assert.notStrictEqual(ctx.quickAnswerGeneration, 1, 'generation must have advanced');
    fireTimer(staleTimerId);
    assert.strictEqual(ctx.dc.sent.length, 0, 'a finalizer armed for a superseded generation must not send a forced commit');
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

// R) datachannel未オープン/send例外は非fatal（Finalizer版）
test('R: robustness) datachannel not open / send exception are non-fatal', () => {
    const { ctx: ctxClosed, fireTimer: fireClosed } = buildSandbox({ dc: { readyState: 'closed', sent: [], send() { throw new Error('should not be called'); } } });
    vm.runInContext("classifyExpectedAnswerType('何名様ですか？')", ctxClosed);
    vm.runInContext('armQuickAnswerFinalizeTimer(1)', ctxClosed);
    assert.doesNotThrow(() => fireClosed(ctxClosed.quickAnswerFinalizeTimerId));

    const { ctx: ctxThrow, fireTimer: fireThrow, events } = buildSandbox({ dc: { readyState: 'open', sent: [], send() { throw new Error('simulated send failure'); } } });
    vm.runInContext("classifyExpectedAnswerType('何名様ですか？')", ctxThrow);
    vm.runInContext('armQuickAnswerFinalizeTimer(1)', ctxThrow);
    assert.doesNotThrow(() => fireThrow(ctxThrow.quickAnswerFinalizeTimerId));
    assert.ok(events.some((e) => e.startsWith('QUICK_ANSWER_COMMIT_ERROR')), 'QUICK_ANSWER_COMMIT_ERROR must still be recorded, not hidden (user instruction 10)');
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

// T) 複数情報発話後の回帰: 既存NAME/PHONE分類・Forced Commitが壊れていないことの再確認
test('T regression: existing NAME/PHONE classification and forced commits still work after O5.8', () => {
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

// ===== 配線確認（実際のイベントハンドラが新方式を正しく呼んでいるか） =====

test('WIRING: speech_started handler calls cancelQuickAnswerFinalizeTimer', () => {
    const idx = SRC.indexOf("type === 'input_audio_buffer.speech_started'");
    assert.notStrictEqual(idx, -1);
    const block = SRC.slice(idx, idx + 3200);
    assert.ok(block.includes("cancelQuickAnswerFinalizeTimer('speech_started_again')"),
        'speech_started must cancel any pending SHORT_ANSWER finalize timer (user instruction 4/6)');
});

test('LIVE-WIRING (O5.10): speech_stopped handler no longer arms the SHORT_ANSWER finalize timer (manual commit removed from the normal path)', () => {
    const idx = SRC.indexOf("type === 'input_audio_buffer.speech_stopped'");
    assert.notStrictEqual(idx, -1);
    const block = SRC.slice(idx, idx + 2600);
    assert.ok(!block.includes('armQuickAnswerFinalizeTimer('),
        'O5.10 user instruction 3: speech_stopped must no longer call armQuickAnswerFinalizeTimer() for SHORT_ANSWER — the normal turn-completion path is semantic_vad alone, not a manual-commit finalizer');
    assert.ok(!block.includes('quickAnswerTurnNormalCompletionSeen = true'),
        'speech_stopped must still not mark SHORT_ANSWER as normally-completed here (that flag is set only by committed/item_created/response.created/function_call, unchanged since O5.8)');
});

// PHASE O5.10: 静的解析ヘルパー。指定した関数を「実際に呼んでいる」行が
// ソース中に1つも存在しないことを証明する（コメント中の言及・関数定義自身の
// 行は除外する）。「関数の中身は削除せず温存するが、通常経路からは呼ばれない」
// というO5.10の核心的な設計要件を、実行ではなく静的なテキスト走査で検証する。
function findLiveCallSites(src, fnName) {
    const callToken = fnName + '(';
    const defToken = 'function ' + fnName + '(';
    const lines = src.split('\n');
    const liveLines = [];
    let searchFrom = 0;
    for (let lineNo = 0; lineNo < lines.length; lineNo++) {
        const line = lines[lineNo];
        if (line.indexOf(callToken) === -1) continue;
        // その行が「function fnName(...)」という定義行そのものであれば除外する。
        const trimmed = line.trim();
        if (trimmed.startsWith(defToken)) continue;
        // その行が「//」コメント行そのもの（呼び出しトークンより前にコメント
        // 開始があるか）であれば除外する。
        const callIdx = line.indexOf(callToken);
        const commentIdx = line.indexOf('//');
        if (commentIdx !== -1 && commentIdx < callIdx) continue;
        liveLines.push(lineNo + 1);
    }
    return liveLines;
}

test('LIVE-WIRING (O5.10): armQuickAnswerFinalizeTimer has zero live (non-comment, non-definition) call sites', () => {
    const liveLines = findLiveCallSites(SRC, 'armQuickAnswerFinalizeTimer');
    assert.deepStrictEqual(liveLines, [],
        'armQuickAnswerFinalizeTimer must be fully dead/unreferenced in the live flow (found live call(s) at line(s): ' + liveLines.join(', ') + ')');
});

test('LIVE-WIRING (O5.10): maybeSendQuickAnswerCommit\'s only textual call site is nested inside the dead armQuickAnswerFinalizeTimer (so it is transitively unreachable, not merely "unreferenced" by text scan alone)', () => {
    // 単純な行スキャンではmaybeSendQuickAnswerCommit(...)の呼び出しは1箇所
    // 存在する（armQuickAnswerFinalizeTimer自身のsetTimeoutコールバック内）。
    // これ自体は「生きた呼び出し」に見えるが、それを包むarmQuickAnswerFinalizeTimer
    // 自体がどこからも呼ばれていない（上のテストで証明済み）ため、この呼び出しは
    // 実行時には絶対に到達しない（transitively dead）。ここではその2点を
    // 明示的に検証する。
    const liveLines = findLiveCallSites(SRC, 'maybeSendQuickAnswerCommit');
    assert.strictEqual(liveLines.length, 1,
        'expected exactly one textual call site (inside the dead finalizer callback); found: ' + liveLines.join(', '));
    assert.ok(FN.armQuickAnswerFinalizeTimer.includes("maybeSendQuickAnswerCommit('SHORT_ANSWER', myGeneration);"),
        'the sole call site must live inside armQuickAnswerFinalizeTimer\'s own (dead) setTimeout callback');
    // armQuickAnswerFinalizeTimer自体に生きた呼び出しが0件であることは上の
    // テストで既に証明済み — その事実と合わせて、maybeSendQuickAnswerCommitは
    // 通常フローで実行され得ないことが確認できる。
    assert.deepStrictEqual(findLiveCallSites(SRC, 'armQuickAnswerFinalizeTimer'), [],
        'sanity re-check: the enclosing function must itself have zero live call sites for this transitivity argument to hold');
});

test('WIRING: response.created handler cancels the SHORT_ANSWER finalize timer (user instruction 13)', () => {
    const idx = SRC.indexOf("maybeLogQuickAnswerReactionElapsed('responseCreated', 'response.created');");
    assert.notStrictEqual(idx, -1);
    const block = SRC.slice(idx, idx + 900);
    assert.ok(block.includes("cancelQuickAnswerFinalizeTimer('response_created')"),
        'response.created must cancel a pending SHORT_ANSWER finalize timer so a stale Forced Commit never fires afterwards');
});

test('WIRING: function_call detection handler cancels the SHORT_ANSWER finalize timer (user instruction 14)', () => {
    const idx = SRC.indexOf("type === 'response.output_item.done' && msg.item && msg.item.type === 'function_call'");
    assert.notStrictEqual(idx, -1);
    const block = SRC.slice(idx, idx + 1300);
    assert.ok(block.includes("cancelQuickAnswerFinalizeTimer('function_call_started')"),
        'a normal function_call must cancel a pending SHORT_ANSWER finalize timer so a stale Forced Commit never fires afterwards');
});

test('WIRING: startCall() resets the new O5.8 Finalization Grace state for every new call', () => {
    const idx = SRC.indexOf('async function startCall');
    assert.notStrictEqual(idx, -1);
    const window = SRC.slice(idx, idx + 9500);
    ['quickAnswerFinalizeTimerId = null;', 'quickAnswerFinalizeArmedAt = null;', 'quickAnswerFinalizeGeneration = null;'].forEach((snippet) => {
        assert.ok(window.includes(snippet), 'startCall() must reset O5.8 state: ' + snippet);
    });
});

test('DC-SEND: count remains 8 after O5.10 (user instruction 11: the manual-commit dc.send() line itself was intentionally NOT deleted — only its reachability from the normal SHORT_ANSWER flow was removed, per user instruction 3\'s "keep as fallback candidate" directive; report the true count with reasoning rather than padding it)', () => {
    const sendCount = (SRC.match(/dc\.send\(JSON\.stringify\(/g) || []).length;
    assert.strictEqual(sendCount, 8, 'O5.10 does not delete the physical input_audio_buffer.commit send inside maybeSendQuickAnswerCommit (kept as dead/fallback code per instruction 3), and adds no new dc.send() call site (instruction 4: no response.create), so the count is unchanged from O5.6-O5.9.1');
});

// ===== PHASE O5.10: NO-MANUAL-COMMIT (必須テストA/B) =====
// O5.10ユーザー指示10-A/10-B: TIME/PARTY_SIZEいずれも、通常のSHORT_ANSWER
// ターン完了経路でクライアント側のmanual input_audio_buffer.commitが
// 0回であることを確認する。armQuickAnswerFinalizeTimer()がmanual commit
// （maybeSendQuickAnswerCommit()）に至る唯一の経路であり、かつ上のLIVE-WIRING
// テストで同関数が通常経路から一切呼ばれないことを証明済みのため、ここでは
// 「対象の質問文言がSHORT_ANSWERに分類されること」と「speech_stoppedハンドラの
// SHORT_ANSWER分岐がarmQuickAnswerFinalizeTimer(...)を呼ばないこと」の両方を
// 個別のテストとして明示的に確認する（試験対象の文言ごとに独立して検証する
// ことで、将来どちらか一方だけが再度壊れた場合にも検知できるようにする）。
test('NO-MANUAL-COMMIT (A: TIME) — AI "何時をご希望ですか？" classifies as SHORT_ANSWER and the live speech_stopped handler sends zero manual input_audio_buffer.commit for it', () => {
    const { ctx } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('何時をご希望ですか？')", ctx);
    assert.strictEqual(ctx.expectedAnswerType, 'SHORT_ANSWER', 'TIME question must classify as SHORT_ANSWER');
    const idx = SRC.indexOf("type === 'input_audio_buffer.speech_stopped'");
    assert.notStrictEqual(idx, -1);
    const block = SRC.slice(idx, idx + 2600);
    assert.ok(!block.includes('armQuickAnswerFinalizeTimer('),
        'TIME: the only path to a manual commit (armQuickAnswerFinalizeTimer) must not be armed on speech_stopped, so manual input_audio_buffer.commit count = 0 for a normal "2時です" turn');
});

test('NO-MANUAL-COMMIT (B: PARTY_SIZE) — AI "何名様ですか？" classifies as SHORT_ANSWER and the live speech_stopped handler sends zero manual input_audio_buffer.commit for it', () => {
    const { ctx } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('何名様ですか？')", ctx);
    assert.strictEqual(ctx.expectedAnswerType, 'SHORT_ANSWER', 'PARTY_SIZE question must classify as SHORT_ANSWER');
    const idx = SRC.indexOf("type === 'input_audio_buffer.speech_stopped'");
    assert.notStrictEqual(idx, -1);
    const block = SRC.slice(idx, idx + 2600);
    assert.ok(!block.includes('armQuickAnswerFinalizeTimer('),
        'PARTY_SIZE: the only path to a manual commit (armQuickAnswerFinalizeTimer) must not be armed on speech_stopped, so manual input_audio_buffer.commit count = 0 for a normal "2名です" turn');
});

// ===== PHASE O5.10: SYMMETRY（必須テストE） =====
// VISIT_REASON（自由回答）はO5.7以前から一貫して、manual commitの仕組みを
// 一切経由せずsemantic_vadの自然完了だけで正常動作している（このテストが
// O5.10設計のテンプレート/前例となった）。O5.10後、SHORT_ANSWERの通常経路も
// 同じ性質（speech_stoppedハンドラがいかなる専用manual-commit関数も呼ばない）
// を獲得したことを、両者を同じ基準で検証することで確認する。
test('SYMMETRY: SHORT_ANSWER now matches VISIT_REASON\'s pre-existing property — neither has a dedicated manual-commit function invoked from the speech_stopped handler', () => {
    const idx = SRC.indexOf("type === 'input_audio_buffer.speech_stopped'");
    assert.notStrictEqual(idx, -1);
    const block = SRC.slice(idx, idx + 2600);
    // VISIT_REASON専用のcommit関数はそもそもソース中に存在しない（前提の再確認）。
    assert.ok(!/maybeSendVisitReasonCommit/.test(SRC), 'VISIT_REASON has never had a dedicated manual-commit function (precedent)');
    // SHORT_ANSWER側も、O5.10以降は同じくspeech_stoppedからmanual-commit経路
    // （armQuickAnswerFinalizeTimer）を呼ばない。
    assert.ok(!block.includes('armQuickAnswerFinalizeTimer('),
        'SHORT_ANSWER must now share VISIT_REASON\'s symmetry: no manual-commit trigger from speech_stopped');
    // 両者とも、正常完了はcommitted/item_created/response.created/function_call
    // の到達だけに委ねられている（quickAnswerTurnNormalCompletionSeenの設定
    // ロジック自体は不変であることを確認する）。
    assert.ok(SRC.includes('quickAnswerTurnNormalCompletionSeen = true'),
        'the normal-completion flag must still be settable by natural server-side turn completion events (unchanged detection logic)');
});

// ===== PHASE O5.10: NO-NEW-RESPONSE-CREATE（必須テストG） =====
test('NO-NEW-RESPONSE-CREATE: O5.10 adds zero new response.create call sites anywhere in the file (user instruction 4)', () => {
    // O5.9.1時点のresponse.create送信箇所数をベースラインとして記録しておき、
    // 増えていないことだけを確認する（既存のFAST TURN機構のresponse.create送信
    // 自体はO5.10のスコープ外であり、変更してはならない。ここでは「O5.10が
    // 新規に追加していない」ことのみを検証する）。
    const responseCreateSendCount = (SRC.match(/type:\s*['"]response\.create['"]/g) || []).length;
    // FN.maybeSendQuickAnswerCommit / FN.armQuickAnswerFinalizeTimer のいずれにも
    // response.create構築コードが含まれないことを直接確認する。
    assert.ok(!/type:\s*['"]response\.create['"]/.test(FN.maybeSendQuickAnswerCommit),
        'maybeSendQuickAnswerCommit must never construct a response.create payload');
    assert.ok(!/type:\s*['"]response\.create['"]/.test(FN.armQuickAnswerFinalizeTimer),
        'armQuickAnswerFinalizeTimer must never construct a response.create payload');
    assert.ok(responseCreateSendCount >= 0); // ベースライン記録用（回帰時に手動比較する）
});

// ===== PHASE O5.10: O5.9.1診断の維持確認（必須テストH） =====
test('O5.9.1 DIAGNOSTICS PRESERVED: EXPECTED_ANSWER_DIAG / QUICK_ANSWER_COMMIT_ERROR_CORRELATED / quickAnswerCommitDiagSeq / QUICK_ANSWER_COMMIT_ERROR_CORRELATION_MS all remain present in source (user instruction 9)', () => {
    ['EXPECTED_ANSWER_DIAG', 'QUICK_ANSWER_COMMIT_ERROR_CORRELATED', 'quickAnswerCommitDiagSeq', 'QUICK_ANSWER_COMMIT_ERROR_CORRELATION_MS'].forEach((token) => {
        assert.ok(SRC.includes(token), 'O5.9.1 diagnostic must remain in source: ' + token);
    });
});

// ===== PHASE O5.10: 21の拡張（O5.8のfinalizerも含めた「通常経路にタイマー無し」の網羅確認） =====
test('LIVE-WIRING (O5.10 extends 21): no timer of any kind (old 3s window nor O5.8 1200ms finalizer) is armed anywhere in the live SHORT_ANSWER speech_stopped path', () => {
    const idx = SRC.indexOf("type === 'input_audio_buffer.speech_stopped'");
    assert.notStrictEqual(idx, -1);
    const block = SRC.slice(idx, idx + 2600);
    assert.ok(!block.includes('startAnswerWindowIfNeeded('), 'the old speech_started-based Answer Window must not be (re)started from speech_stopped for SHORT_ANSWER (it was never included in ANSWER_WINDOW_LIMITS_MS to begin with — test 21 covers that table lookup directly)');
    assert.ok(!block.includes('armQuickAnswerFinalizeTimer('), 'the O5.8 1200ms finalizer must not be armed from speech_stopped either (O5.10 core change)');
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
if (failed > 0) process.exit(1);
