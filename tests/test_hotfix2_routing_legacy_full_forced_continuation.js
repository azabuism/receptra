'use strict';

/**
 * RECEPTRA — TASK B HOTFIX 2（2026年9月）
 * 実機「ご用件を何度も聞く」ループの根本修正・behavioral regression test
 * （フロントエンド側イベント順序）。
 *
 * 根本原因調査の結論（詳細はユーザーへの最終報告を参照）:
 *   ROUTING phaseでモデルがclassify_intentを呼ばずに応答を終えた場合
 *   （tool_choiceは明示設定されておらずデフォルト'auto'のため、モデルが
 *   Tool呼び出し無しのテキスト応答のみで終える余地が常に存在する）、
 *   既存の「OTHER/UNKNOWN安全網」（sendRealtimePhaseSessionUpdate()内で
 *   'routing'遷移と同時に無条件でpendingPhaseTransitionTarget='legacy_full'
 *   を予約する仕組み）が、次のresponse.done境界で無条件に発火する。この時
 *   forceFollowUpForTransitionは常にtrue（targetPhaseForTransition!=='routing'
 *   の分岐）になるため、新しいお客様の発話を一切待たずに
 *   sendResponseCreate('phase_transition_to_legacy_full')が即座に呼ばれ、
 *   legacy_fullのinstructions（_INTENT_CLASSIFICATION_TEMPLATE等、TASK Bの
 *   forced-continuation対策が適用されていなかった別系統の古いテンプレート）
 *   がゼロから「ご用件把握」を開始してしまう。これが実機で観測された
 *   「AIがまた『ご用件』を聞く」の直接の技術的原因である。
 *
 * このファイルはJS側のイベント順序・状態遷移メカニズムのみを検証する
 * （実際にモデルがclassify_intentを呼ぶかどうかというLLMの挙動自体は
 * 決定的にテストできないため、静的検証の対象外。Python側の
 * tests/smoke_test_hotfix2_routing_no_repeat_purpose_question.pyが
 * legacy_full instructions自体に「強制継続でも会話全体を確認し、既に
 * 話された用件を再度尋ねない」という対策文言が実際に追加されていることを
 * 検証する。両者を合わせて「なぜ起きたか（このファイル）」と「何を直した
 * か（Python側）」の両方をproduction event/orderに沿って検証する）。
 *
 * 既存のtests/test_realtime_phase2_reservation_callback_transition.jsの
 * ハーネス（vm.runInContextで実ソースから関数を抜き出して実行）をそのまま
 * 踏襲する（新しいテスト用モックフレームワークは追加しない）。
 *
 * 実行: node tests/test_hotfix2_routing_legacy_full_forced_continuation.js
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
            else if (src[i] === '}') {
                depth--;
                if (depth === 0) { i++; break; }
            }
        }
        return src.slice(startIdx, i);
    }
    const startToken = 'function ' + fnName + '(';
    startIdx = src.indexOf(startToken, fromIndex || 0);
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

function extractBlock(src, signature, fromIndex) {
    const idx = src.indexOf(signature, fromIndex || 0);
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

const FN = {
    armPhaseTransitionAfterResponse: extractFunctionSource(SRC, 'armPhaseTransitionAfterResponse'),
    sendRealtimePhaseSessionUpdate: extractFunctionSource(SRC, 'sendRealtimePhaseSessionUpdate'),
    callClassifyIntentTool: extractFunctionSource(SRC, 'callClassifyIntentTool'),
};

const RESPONSE_DONE_PHASE_BLOCK_SRC = extractBlock(
    SRC,
    'if (pendingPhaseTransitionTarget !== null && !phaseTransitionInProgress) {'
);

// PHASE ORDER HOTFIX（今回追加）: legacy_fullへのフォールバックを条件付きで
// armする新規ブロック（ROUTINGでrouting HasReceivedUserTurn===trueかつ
// responseHasFunctionCall===falseの場合のみpendingPhaseTransitionTarget=
// 'legacy_full'をセットする）。RESPONSE_DONE_PHASE_BLOCK_SRCの直前に配置
// されている、実ソースそのものを抜き出す（新しいモック実装は書かない）。
const LEGACY_FULL_FALLBACK_ARM_BLOCK_SRC = extractBlock(
    SRC,
    "if (currentRealtimePhase === 'routing'\n"
    + "                    && routingHasReceivedUserTurn\n"
    + "                    && !responseHasFunctionCall\n"
    + "                    && pendingPhaseTransitionTarget === null\n"
    + "                    && !phaseTransitionInProgress) {"
);

const SESSION_UPDATED_PHASE_BLOCK_SRC = extractBlock(
    SRC,
    "if (type === 'session.updated' && phaseTransitionInProgress) {"
);

function buildSandbox(overrides) {
    const timelineEvents = [];
    const consoleLogs = [];
    const dcSendCalls = [];
    const sendResponseCreateCalls = [];
    const logEvents = [];
    const context = {
        performance: { now: () => Date.now() },
        pushTimelineEvent: (text) => { timelineEvents.push(text); },
        console: { log: (...args) => { consoleLogs.push(args); } },
        logEvent: (text) => { logEvents.push(text); },
        callStartedAt: Date.now() - 1234,
        dc: { readyState: 'open', send: (payload) => { dcSendCalls.push(payload); } },
        sendResponseCreate: (reason) => { sendResponseCreateCalls.push(reason); return true; },
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
        // PHASE ORDER HOTFIX（今回追加）: ROUTINGでお客様の実際の新しい発話を
        // 受け取ったかどうかのstate（デフォルトfalse=まだ受け取っていない）。
        routingHasReceivedUserTurn: false,
        // このテストハーネスでは実際のfunction_call処理までは再現しないため、
        // 「classify_intentが呼ばれたかどうか」をテスト側で明示的に模擬する
        // （デフォルトfalse=Tool呼び出し無しでテキストのみ応答が完了した想定）。
        responseHasFunctionCall: false,
    }, overrides || {});
    Object.assign(context, state);
    vm.createContext(context);
    vm.runInContext(Object.values(FN).join('\n\n'), context);
    return { ctx: context, timelineEvents, consoleLogs, dcSendCalls, sendResponseCreateCalls, logEvents };
}

let passed = 0;
const _queuedTests = [];
function test(name, fn) {
    _queuedTests.push({ name, fn });
}

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

// このヘルパーは、NAME完了→ROUTINGへの遷移を、
// テストX2と同じ手順（session.updated到着→response.done境界の消費）で
// 完了させる。戻り値のctxはROUTING phaseに正しく入った状態。
function enterRouting() {
    const { ctx, dcSendCalls, sendResponseCreateCalls } = buildSandbox({ currentRealtimePhase: 'name' });
    vm.runInContext("sendRealtimePhaseSessionUpdate('routing', 'response_done_boundary', false)", ctx);
    // PHASE ORDER HOTFIX（今回・root cause fix）: ROUTINGへ入った時点では、
    // お客様がまだ一度も新しい発話をしていないため、legacy_fullの安全網は
    // もう即座にはarmされない（旧設計の'legacy_full'即時予約はこのラウンドで
    // 廃止した）。routingHasReceivedUserTurnもfalseのまま。
    assert.strictEqual(ctx.pendingPhaseTransitionTarget, null, 'ROOT CAUSE FIX: entering routing must NOT arm the legacy_full safety net until the customer has actually spoken');
    assert.strictEqual(ctx.routingHasReceivedUserTurn, false, 'entering routing resets routingHasReceivedUserTurn to false');
    vm.runInContext("type = 'session.updated'; " + SESSION_UPDATED_PHASE_BLOCK_SRC, Object.assign(ctx, { type: null }));
    assert.strictEqual(ctx.currentRealtimePhase, 'routing');
    return { ctx, dcSendCalls, sendResponseCreateCalls };
}

// PHASE ORDER HOTFIX（今回追加）: ROUTING在中に実際のお客様の発話が
// input_audio_buffer.committedとしてcommitされたことを模擬する
// （production側の実際の配線はEdit3参照。ここではそのイベントが起きた
// 結果としての状態変化のみを模擬する。新しいモック実装ではなく、
// 実ソースのLEGACY_FULL_FALLBACK_ARM_BLOCK_SRC/RESPONSE_DONE_PHASE_BLOCK_SRC
// をそのまま実行するための前提状態を整えるだけ）。
function simulateGenuineRoutingUserTurn(ctx, hasFunctionCall) {
    ctx.routingHasReceivedUserTurn = true;
    ctx.responseHasFunctionCall = !!hasFunctionCall;
}

// =======================================================================
// R1: NAME完了 → ROUTINGへ正常遷移（既存回帰）
// =======================================================================
test('R1: NAME completion arms exactly one ROUTING transition, and it completes normally', () => {
    const { ctx } = enterRouting();
    assert.strictEqual(ctx.currentRealtimePhase, 'routing');
});

// =======================================================================
// R2: ROUTINGで明確な予約用件 → classify_intent(reservation)が
//     response.done"より前"に届けば、legacy_fullへは絶対に落ちない
// =======================================================================
test('R2: classify_intent(reservation) called on the customer\'s real purpose turn reaches reservation directly — legacy_full is never armed, let alone overwritten', () => {
    const { ctx, dcSendCalls } = enterRouting();
    // ユーザーが実際に発話（PHASE ORDER HOTFIXにより、この時点まで
    // legacy_fullはまだarmされていない）→ classify_intentのfunction_callが
    // response.doneより先に処理される（実際のOpenAI Realtime APIでも
    // function_call系イベントはresponse.doneに先行する）。
    simulateGenuineRoutingUserTurn(ctx, true);
    return vm.runInContext('callClassifyIntentTool({ intent: "reservation" })', ctx).then(() => {
        assert.strictEqual(ctx.pendingPhaseTransitionTarget, 'reservation', 'classify_intent arms reservation directly (nothing to overwrite, since legacy_full is no longer armed eagerly)');
        // PHASE ORDER HOTFIXの新しい条件付きarmブロックも実行するが、
        // pendingPhaseTransitionTarget!==nullガードによりno-opであることを確認する。
        vm.runInContext(LEGACY_FULL_FALLBACK_ARM_BLOCK_SRC, ctx);
        assert.strictEqual(ctx.pendingPhaseTransitionTarget, 'reservation', 'the new conditional legacy_full arm must not overwrite an already-armed reservation target');
        vm.runInContext(RESPONSE_DONE_PHASE_BLOCK_SRC, ctx);
        assert.strictEqual(ctx.currentRealtimePhase, 'reservation');
        const lastPayload = JSON.parse(dcSendCalls[dcSendCalls.length - 1]);
        assert.strictEqual(lastPayload.session.instructions, 'RESERVATION_PHASE_INSTRUCTIONS_STUB');
    });
});

// =======================================================================
// R3: 同様にcallback
// =======================================================================
test('R3: classify_intent(callback) called on the customer\'s real purpose turn reaches callback directly — legacy_full is never armed', () => {
    const { ctx, dcSendCalls } = enterRouting();
    simulateGenuineRoutingUserTurn(ctx, true);
    return vm.runInContext('callClassifyIntentTool({ intent: "callback" })', ctx).then(() => {
        assert.strictEqual(ctx.pendingPhaseTransitionTarget, 'callback');
        vm.runInContext(LEGACY_FULL_FALLBACK_ARM_BLOCK_SRC, ctx);
        assert.strictEqual(ctx.pendingPhaseTransitionTarget, 'callback', 'the new conditional legacy_full arm must not overwrite an already-armed callback target');
        vm.runInContext(RESPONSE_DONE_PHASE_BLOCK_SRC, ctx);
        assert.strictEqual(ctx.currentRealtimePhase, 'callback');
        const lastPayload = JSON.parse(dcSendCalls[dcSendCalls.length - 1]);
        assert.strictEqual(lastPayload.session.instructions, 'CALLBACK_PHASE_INSTRUCTIONS_STUB');
    });
});

// =======================================================================
// R4（PHASE ORDER HOTFIXで更新・最重要回帰テスト）: 旧設計では、ROUTINGへ
// 入った直後（お客様がまだ一度も発話していない段階）でもlegacy_fullの
// 安全網が即座に発火してしまっていた（これがPHASE ORDER HOTFIXで特定・
// 修正した根本原因そのもの）。このテストはまずその「まだ発話していない
// 段階では絶対に落ちない」ことを検証し（R4a）、続けて「お客様が実際に
// 発話し、それでもclassify_intentが呼ばれなかった場合にのみ」正しく
// legacy_fullへフォールバックすることを検証する（R4b・既存の安全網の
// 意図そのものが壊れていないことの確認）。
// =======================================================================
test('R4a (ROOT CAUSE FIX regression): before the customer has spoken in ROUTING, the legacy_full fallback must NOT fire even at response.done — no forced response.create without a real user utterance', () => {
    const { ctx, sendResponseCreateCalls } = enterRouting();
    // ROUTING突入直後の強制follow-up応答（「本日はどのようなご用件でしょうか？」）
    // 自身のresponse.done相当。routingHasReceivedUserTurnはまだfalseのまま
    // （お客様はまだ一言も発話していない）。
    assert.strictEqual(ctx.routingHasReceivedUserTurn, false);
    vm.runInContext(LEGACY_FULL_FALLBACK_ARM_BLOCK_SRC, ctx);
    assert.strictEqual(ctx.pendingPhaseTransitionTarget, null, 'ROOT CAUSE FIX: must NOT arm legacy_full before the customer has actually spoken in ROUTING');
    vm.runInContext(RESPONSE_DONE_PHASE_BLOCK_SRC, ctx);
    assert.strictEqual(ctx.currentRealtimePhase, 'routing', 'must remain in routing, waiting for the customer\'s real answer');
    assert.strictEqual(sendResponseCreateCalls.length, 0, 'ROOT CAUSE FIX: no forced response.create may fire before the customer has answered the purpose question');
});

test('R4b (legitimate safety net preserved): once the customer has genuinely spoken in ROUTING and the response still did not classify (no function_call), legacy_full fallback fires exactly as before (forceFollowUp=true, one forced response.create)', () => {
    const { ctx, sendResponseCreateCalls } = enterRouting();
    // お客様が実際に用件を発話したが、モデルがclassify_intentを呼ばずに
    // テキストのみで応答を終えた（tool_choiceが'auto'のため構造的にあり得る）。
    simulateGenuineRoutingUserTurn(ctx, false);
    vm.runInContext(LEGACY_FULL_FALLBACK_ARM_BLOCK_SRC, ctx);
    assert.strictEqual(ctx.pendingPhaseTransitionTarget, 'legacy_full', 'the legitimate OTHER/UNKNOWN safety net must still work once the customer has genuinely spoken and classification failed');
    vm.runInContext(RESPONSE_DONE_PHASE_BLOCK_SRC, ctx);
    assert.strictEqual(ctx.currentRealtimePhase, 'legacy_full', 'must fall back to legacy_full when classify_intent was never called for the customer\'s real answer');
    assert.strictEqual(ctx.pendingPhaseTransitionForceFollowUp, true, 'the legacy_full safety net still forces forceFollowUp=true, unchanged from before this HOTFIX');
    vm.runInContext("type = 'session.updated'; " + SESSION_UPDATED_PHASE_BLOCK_SRC, Object.assign(ctx, { type: null }));
    assert.strictEqual(sendResponseCreateCalls.length, 1, 'exactly one forced response.create fires once the safety net is legitimately armed');
    assert.strictEqual(sendResponseCreateCalls[0], 'phase_transition_to_legacy_full');
});

// =======================================================================
// R7/R8: 1ユーザーターンに不要な複数response.createが発生しない／
// Tool continuationによって「ご用件」質問が再生成されない
// （classify_intent自体の自動継続スキップの既存契約の回帰確認。
// 別ファイルtest_realtime_phase2_reservation_callback_transition.jsの
// ACと同じ検証だが、HOTFIX2の文脈で独立して再確認する）。
// =======================================================================
test('R7/R8: classify_intent success path sends exactly one session.update (to reservation/callback) and the automatic tool-continuation response.create is skipped for classify_intent, so no extra "ご用件" response is generated by that mechanism', () => {
    const { ctx, dcSendCalls, sendResponseCreateCalls } = enterRouting();
    // enterRouting()自体が既にNAME→ROUTINGのsession.updateを1回送信している
    // ため、ここではそれ以降（reservationへの遷移1回分）の増分のみを数える。
    const sessionUpdateCountBeforeClassify = dcSendCalls.filter((p) => JSON.parse(p).type === 'session.update').length;
    return vm.runInContext('callClassifyIntentTool({ intent: "reservation" })', ctx).then(() => {
        vm.runInContext(RESPONSE_DONE_PHASE_BLOCK_SRC, ctx);
        // reservationへの遷移そのものはsession.update 1回のみ（dc.send）の増分。
        const sessionUpdateSends = dcSendCalls.filter((p) => JSON.parse(p).type === 'session.update');
        assert.strictEqual(sessionUpdateSends.length - sessionUpdateCountBeforeClassify, 1, 'exactly one ADDITIONAL session.update for the reservation transition (on top of the routing entry itself)');
        // classify_intent success時、session.updated受信まではforceFollowUpの
        // 状態のみで、まだsendResponseCreateは呼ばれていない（この時点では
        // ユーザーの新しい発話を待つのが正しい設計）。
        assert.strictEqual(sendResponseCreateCalls.length, 0, 'no forced response.create yet before session.updated for the reservation transition arrives');
    });
});

_runAllTests().then(() => {
    console.log('\n' + passed + ' test(s) passed.');
    if (process.exitCode) {
        console.log('\n=== SOME test_hotfix2_routing_legacy_full_forced_continuation.js CHECKS FAILED ===');
    } else {
        console.log('\n=== ALL test_hotfix2_routing_legacy_full_forced_continuation.js CHECKS PASSED ===');
    }
});
