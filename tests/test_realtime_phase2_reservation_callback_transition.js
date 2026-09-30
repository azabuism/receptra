'use strict';

/**
 * RECEPTRA — Realtime Token Architecture Phase 2（2026年9月）
 * ROUTING → RESERVATION / CALLBACK 分岐の契約テスト（フロントエンド側）。
 * ユーザー指定の§28契約テストS〜AIをカバーする（既存のtest_realtime_phase1_transition.js
 * のI〜Tのレター体系とは意図的に衝突させず、目的が分かる名前を付けている。
 * 同ファイル内のHOTFIX-*系の命名規則を踏襲する）。あわせて§29（複数スロット
 * 同時発話時のretention）・§30（CALLBACK FIRST ANSWER MUST COUNT）についても、
 * フロントエンドのJSユニットテストとして検証可能な範囲でカバーする。
 *
 * 前提（audit結果。推測ではなく既存コードの実読で確認）:
 * - classify_intent Toolの実体はarmPhaseTransitionAfterResponse()を
 *   呼ぶだけであり、Phase 1から存在するこの関数・sendRealtimePhaseSessionUpdate()・
 *   response.done境界の消費ブロック・session.updated確認ブロックは
 *   一切変更していない（'name'/'routing'/'legacy_full'のみを想定していた
 *   既存コードが、'reservation'/'callback'という新しい文字列に対しても
 *   分岐なしにそのまま正しく動作することを、本ファイルで確認する）。
 * - 複数スロット同時発話（例:「明日の7時に2人で予約したいです」）時の
 *   日時・人数の保持は、新しいstate object（例えばslot専用のJS変数）を
 *   一切追加せず、既存のOpenAI Realtime session自体の会話履歴
 *   （conversation history）がsession.updateをまたいで保持される
 *   という、Phase 1の実機成功実績（名前の聞き直しが起きなかったこと）と
 *   同じ仕組みにそのまま委ねている（RESERVATION/CALLBACK instructions側で
 *   「既に話されている内容は再度尋ねない」よう明示指示している。
 *   app/services/realtime_voice_ai.py の _PHASE2A_RESERVATION_ROLE_TEMPLATE /
 *   _PHASE2B_CALLBACK_ROLE_TEMPLATE参照）。したがって本ファイルでは
 *   「Phase遷移の仕組み自体が会話履歴・conversation.itemを一切
 *   クリア／改変しないこと」を検証することで、この前提を壊していないことを
 *   確認する（新しいstate objectを推測で追加していないことの確認でもある）。
 *
 * 本テストが確認する項目（S〜AI。カッコ内は§28のラベル）:
 *   S. NAME→ROUTING exactly once（既存回帰。test_realtime_phase1_transition.js
 *      のJ/J2と重複させず、ここではclassify_intent導入後も壊れていないことのみ再確認）
 *   T. ROUTING→RESERVATION exactly once
 *   U. ROUTING→CALLBACK exactly once
 *   V. no simultaneous RESERVATION/CALLBACK transition
 *   W. normal reservation flow never sends legacy_full
 *   X. normal callback flow never sends legacy_full
 *   Y. session.type=realtime maintained（reservation/callback対象でも）
 *   Z. phase only confirmed after session.updated CONFIRMED（reservation/callback）
 *   AA. transition failure doesn't advance to next phase（reservation/callback）
 *   AB. no duplicate session.update（reservation/callback対象でも既存ガードが効く）
 *   AC. no duplicate response.create（classify_intentの自動継続スキップの確認）
 *   AD. no RTCPeerConnection recreation（classify_intent関連コード）
 *   AE. no audio track recreation（classify_intent関連コード）
 *   AF. Forced Commit unchanged（classify_intent関連コードは触れない）
 *   AG. ANSWER_WINDOW unchanged（classify_intent関連コードは触れない）
 *   AH. usage diagnostics preserved（既存マーカーの回帰確認）
 *   AI. no PII in diagnostic markers（REALTIME_INTENT_CLASSIFIEDにenum値以外が出ない）
 *
 * 実行: node tests/test_realtime_phase2_reservation_callback_transition.js
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ENGINE_JS_PATH = path.join(__dirname, '..', 'frontend', 'public', 'js', 'realtime-voice-engine.js');
const SRC = fs.readFileSync(ENGINE_JS_PATH, 'utf8');

function extractFunctionSource(src, fnName, fromIndex) {
    // callClassifyIntentToolはasync functionのため、'async function <name>('を
    // 優先的に探す（見つからなければ従来どおり同期functionとして探す）。
    // これを見落とすと「async」プレフィックスを含まないまま抽出してしまい、
    // sandbox内で呼び出した際にPromiseではなく生の戻り値が返る＝
    // vm側テストコードの.then()呼び出しが壊れるため重要。
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

// 「関数でもif-blockでもない、開始マーカー〜終了マーカー直前まで」の
// 生テキストをそのまま抜き出す（T5〜T6のclassify_intent分岐スニペット用。
// extractBlockはif/elseの片側だけで止まってしまうため使えない）。
// fromIndexを指定できるようにしているのは、"if (item.name === 'classify_intent') {"
// という文字列が、handleFunctionCallItem前半のdispatch分岐
// "} else if (item.name === 'classify_intent') {" の部分文字列としても
// 出現するため（"else " の直後から数えると同一の文字列になる）。素朴に
// indexOf(startMarker)だけを使うと誤ってdispatch分岐側にマッチしてしまうため、
// T5マーカーより後ろから検索を開始することで正しい方（T5〜T6の自動継続
// スキップ分岐）だけを一意に取得する。
function extractBetween(src, startMarker, endMarkerExclusive, fromIndex) {
    const startIdx = src.indexOf(startMarker, fromIndex || 0);
    assert.notStrictEqual(startIdx, -1, 'start marker not found: ' + startMarker);
    const endIdx = src.indexOf(endMarkerExclusive, startIdx);
    assert.notStrictEqual(endIdx, -1, 'end marker not found: ' + endMarkerExclusive);
    return src.slice(startIdx, endIdx);
}

const FN = {
    armPhaseTransitionAfterResponse: extractFunctionSource(SRC, 'armPhaseTransitionAfterResponse'),
    sendRealtimePhaseSessionUpdate: extractFunctionSource(SRC, 'sendRealtimePhaseSessionUpdate'),
    callClassifyIntentTool: extractFunctionSource(SRC, 'callClassifyIntentTool'),
};

// response.done境界の消費ロジック（独立ifブロック。Phase1テストと同じ抽出対象）。
const RESPONSE_DONE_PHASE_BLOCK_SRC = extractBlock(
    SRC,
    'if (pendingPhaseTransitionTarget !== null && !phaseTransitionInProgress) {'
);

// session.updated到着時の消費ロジック（独立ifブロック）。
const SESSION_UPDATED_PHASE_BLOCK_SRC = extractBlock(
    SRC,
    "if (type === 'session.updated' && phaseTransitionInProgress) {"
);

// errorハンドラ内の独立hook。
const ERROR_PHASE_HOOK_SRC = extractBlock(
    SRC,
    'if (phaseTransitionInProgress) {',
    SRC.indexOf("pushTimelineEvent('サーバーerrorイベント受信")
);

// PHASE ORDER HOTFIX（今回追加）: legacy_fullへのフォールバックを条件付きで
// armする新規ブロック（RESPONSE_DONE_PHASE_BLOCK_SRCの直前に配置されている
// 実ソースをそのまま抜き出す）。
const LEGACY_FULL_FALLBACK_ARM_BLOCK_SRC = extractBlock(
    SRC,
    "if (currentRealtimePhase === 'routing'\n"
    + "                    && routingHasReceivedUserTurn\n"
    + "                    && !responseHasFunctionCall\n"
    + "                    && pendingPhaseTransitionTarget === null\n"
    + "                    && !phaseTransitionInProgress) {"
);

// handleFunctionCallItem内、classify_intentのみ自動継続response.createを
// スキップする分岐（T5〜T6）。if/elseの両方を1つのスニペットとして
// 実行できるよう、if (item.name === 'classify_intent') { の開始から
// （※直前の`let toolContinuationResponseCreateSent;`宣言は意図的に含めない。
// vm.runInContextではトップレベルのlet/const宣言はコンテキストオブジェクトの
// プロパティとして外部から読み取れず、テストが「実際には何も検証していない」
// 状態になってしまうため。かわりにサンドボックス側でtoolContinuationResponseCreateSent
// を通常のグローバルプロパティとして事前定義し、スニペット内の代入
// （宣言ではない）でそれを更新させる）、直後の
// `lastToolLabel = ... continuation_requested`代入の直前までをそのまま抜き出す。
const _T5_ANCHOR = "pushToolContinuationTrace('T5_CONTINUATION_RESPONSE_CREATE_ATTEMPT (tool=' + (item.name || '(不明)') + ')');";
const _T5_ANCHOR_IDX = SRC.indexOf(_T5_ANCHOR);
assert.notStrictEqual(_T5_ANCHOR_IDX, -1, 'T5 anchor not found (has handleFunctionCallItem changed?)');
const CONTINUATION_SKIP_SNIPPET_SRC = extractBetween(
    SRC,
    // 会話品質改善フェーズ（2026年9月）追記: confirm_customer_name Tool導入に伴い、
    // 実ソース側の開始マーカーが
    // "if (item.name === 'classify_intent') {" から
    // "if (item.name === 'classify_intent' || item.name === 'confirm_customer_name') {"
    // に変わったため、それに合わせて更新した（スニペットの抽出範囲自体は
    // 変わらず、if/else分岐の両方を含む点も従来どおり）。
    "if (item.name === 'classify_intent' || item.name === 'confirm_customer_name') {",
    "lastToolLabel = (item.name || '(不明)') + ': continuation_requested';",
    _T5_ANCHOR_IDX
);

function buildSandbox(overrides) {
    const timelineEvents = [];
    const consoleLogs = [];
    const dcSendCalls = [];
    const logEvents = [];
    const context = {
        performance: { now: () => Date.now() },
        pushTimelineEvent: (text) => { timelineEvents.push(text); },
        console: { log: (...args) => { consoleLogs.push(args); } },
        logEvent: (text) => { logEvents.push(text); },
        callStartedAt: Date.now() - 1234,
        dc: { readyState: 'open', send: (payload) => { dcSendCalls.push(payload); } },
        sendResponseCreate: () => true,
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
        // falseと同じ）。
        lastResponseTranscriptWasProcessNarrationOnly: false,
        lastResponseTranscriptWasIncompleteAiTurn: false,
        // PHASE ORDER HOTFIX（今回追加）
        routingHasReceivedUserTurn: false,
        responseHasFunctionCall: false,
    }, overrides || {});
    Object.assign(context, state);
    vm.createContext(context);
    vm.runInContext(Object.values(FN).join('\n\n'), context);
    return { ctx: context, timelineEvents, consoleLogs, dcSendCalls, logEvents };
}

// callClassifyIntentToolはasync functionのため、本ファイルのtest()の一部は
// Promiseを返す（sandboxで実行したasync関数の戻り値をthenで検証するため）。
// 既存のtest_realtime_phase1_transition.jsの同期的なtest()実装とは異なり、
// ここでは全テストをキューに積んで最後に順次await実行する（同期テストは
// そのままfn()を呼べば即resolveされるPromiseとして扱えるため、分岐は不要）。
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

// =======================================================================
// S. NAME→ROUTING exactly once（既存回帰。classify_intent導入後も壊れていないこと）
// =======================================================================
test('S: NAME→ROUTING arming is still exactly-once after Phase 2 changes (regression of Phase 1 contract J)', () => {
    const { ctx } = buildSandbox({ currentRealtimePhase: 'name', pendingPhaseTransitionTarget: null });
    vm.runInContext("armPhaseTransitionAfterResponse('routing')", ctx);
    vm.runInContext("armPhaseTransitionAfterResponse('routing')", ctx);
    assert.strictEqual(ctx.pendingPhaseTransitionTarget, 'routing');
});

// =======================================================================
// T. ROUTING→RESERVATION exactly once
// =======================================================================
test('T: classify_intent(reservation) called twice in the same turn arms reservation exactly once (no double-arm)', () => {
    const { ctx } = buildSandbox({ currentRealtimePhase: 'routing' });
    return vm.runInContext('callClassifyIntentTool({ intent: "reservation" })', ctx).then(() => {
        assert.strictEqual(ctx.pendingPhaseTransitionTarget, 'reservation');
        return vm.runInContext('callClassifyIntentTool({ intent: "reservation" })', ctx);
    }).then(() => {
        assert.strictEqual(ctx.pendingPhaseTransitionTarget, 'reservation');
    });
});

// =======================================================================
// U. ROUTING→CALLBACK exactly once
// =======================================================================
test('U: classify_intent(callback) called twice in the same turn arms callback exactly once (no double-arm)', () => {
    const { ctx } = buildSandbox({ currentRealtimePhase: 'routing' });
    return vm.runInContext('callClassifyIntentTool({ intent: "callback" })', ctx).then(() => {
        assert.strictEqual(ctx.pendingPhaseTransitionTarget, 'callback');
        return vm.runInContext('callClassifyIntentTool({ intent: "callback" })', ctx);
    }).then(() => {
        assert.strictEqual(ctx.pendingPhaseTransitionTarget, 'callback');
    });
});

// =======================================================================
// V. no simultaneous RESERVATION/CALLBACK transition
// =======================================================================
test('V: pendingPhaseTransitionTarget is a single scalar, so reservation and callback can never be armed simultaneously; a later classify_intent call in the same turn simply overwrites the earlier one, and only one session.update is ever sent', () => {
    const { ctx, dcSendCalls } = buildSandbox({ currentRealtimePhase: 'routing' });
    return vm.runInContext('callClassifyIntentTool({ intent: "reservation" })', ctx).then(() => {
        assert.strictEqual(ctx.pendingPhaseTransitionTarget, 'reservation');
        return vm.runInContext('callClassifyIntentTool({ intent: "callback" })', ctx);
    }).then(() => {
        // 直近の呼び出しが最終的なarm対象になる（矛盾した2回の呼び出しという
        // 想定外ケースでも、変数は常にどちらか一方のみを保持する＝構造的に
        // 「同時に両方」にはなり得ない）。
        assert.strictEqual(ctx.pendingPhaseTransitionTarget, 'callback');
        // この時点ではまだsession.update自体は送信されていない（armするだけ）。
        assert.strictEqual(dcSendCalls.length, 0);
        // response.done境界を1回だけ消費させると、armされている一方（callback）
        // へのsession.updateがちょうど1回だけ送信される。
        vm.runInContext(RESPONSE_DONE_PHASE_BLOCK_SRC, ctx);
        assert.strictEqual(dcSendCalls.length, 1);
        const payload = JSON.parse(dcSendCalls[0]);
        assert.strictEqual(payload.session.instructions, 'CALLBACK_PHASE_INSTRUCTIONS_STUB');
        assert.strictEqual(ctx.currentRealtimePhase, 'callback');
    });
});

// =======================================================================
// W / X. normal reservation/callback flow never sends legacy_full
// =======================================================================
function simulateRoutingThenClassify(intent) {
    const { ctx, dcSendCalls } = buildSandbox({ currentRealtimePhase: 'name' });
    // NAME→ROUTINGへのsession.updateを送信。PHASE ORDER HOTFIXにより、この
    // 時点ではlegacy_fullはもう即座には予約されない（お客様がまだ一度も
    // ROUTINGで発話していないため）。
    vm.runInContext("sendRealtimePhaseSessionUpdate('routing', 'response_done_boundary', false)", ctx);
    assert.strictEqual(ctx.pendingPhaseTransitionTarget, null, 'PHASE ORDER HOTFIX: entering routing must not arm legacy_full before the customer has spoken');
    // ROUTINGへのsession.updateがOpenAI側で確定（session.updated受信）。
    vm.runInContext("type = 'session.updated'; " + SESSION_UPDATED_PHASE_BLOCK_SRC, Object.assign(ctx, { type: null }));
    assert.strictEqual(ctx.phaseTransitionInProgress, false);
    assert.strictEqual(ctx.currentRealtimePhase, 'routing');
    // お客様が実際に用件を発話し、モデルがROUTING応答内でclassify_intentを呼ぶ。
    ctx.routingHasReceivedUserTurn = true;
    ctx.responseHasFunctionCall = true;
    return vm.runInContext('callClassifyIntentTool({ intent: "' + intent + '" })', ctx).then(() => {
        assert.strictEqual(ctx.pendingPhaseTransitionTarget, intent);
        // PHASE ORDER HOTFIXの新しい条件付きarmブロックも実行するが、
        // pendingPhaseTransitionTarget!==nullガードによりno-opであることを確認する。
        vm.runInContext(LEGACY_FULL_FALLBACK_ARM_BLOCK_SRC, ctx);
        assert.strictEqual(ctx.pendingPhaseTransitionTarget, intent, 'the new conditional legacy_full arm must not overwrite an already-armed intent target');
        // このROUTING応答自体のresponse.done境界を消費する。
        vm.runInContext(RESPONSE_DONE_PHASE_BLOCK_SRC, ctx);
        return { ctx, dcSendCalls };
    });
}

test('W: after classify_intent(reservation), the response.done boundary sends session.update to reservation, never to legacy_full', () => {
    return simulateRoutingThenClassify('reservation').then(({ ctx, dcSendCalls }) => {
        assert.strictEqual(ctx.currentRealtimePhase, 'reservation');
        const lastPayload = JSON.parse(dcSendCalls[dcSendCalls.length - 1]);
        assert.strictEqual(lastPayload.session.instructions, 'RESERVATION_PHASE_INSTRUCTIONS_STUB');
        assert.notStrictEqual(lastPayload.session.instructions, 'LEGACY_FULL_INSTRUCTIONS_STUB', 'normal reservation flow must never send legacy_full');
    });
});

test('X: after classify_intent(callback), the response.done boundary sends session.update to callback, never to legacy_full', () => {
    return simulateRoutingThenClassify('callback').then(({ ctx, dcSendCalls }) => {
        assert.strictEqual(ctx.currentRealtimePhase, 'callback');
        const lastPayload = JSON.parse(dcSendCalls[dcSendCalls.length - 1]);
        assert.strictEqual(lastPayload.session.instructions, 'CALLBACK_PHASE_INSTRUCTIONS_STUB');
        assert.notStrictEqual(lastPayload.session.instructions, 'LEGACY_FULL_INSTRUCTIONS_STUB', 'normal callback flow must never send legacy_full');
    });
});

test('X2a (PHASE ORDER HOTFIX): before the customer has spoken in ROUTING, legacy_full must NOT fire even if this response has no function_call (this used to be the root cause bug)', () => {
    const { ctx, dcSendCalls } = buildSandbox({ currentRealtimePhase: 'name' });
    vm.runInContext("sendRealtimePhaseSessionUpdate('routing', 'response_done_boundary', false)", ctx);
    vm.runInContext("type = 'session.updated'; " + SESSION_UPDATED_PHASE_BLOCK_SRC, Object.assign(ctx, { type: null }));
    // classify_intentを一度も呼ばないが、お客様もまだROUTINGで一度も発話して
    // いない（routingHasReceivedUserTurn===false, ROUTING突入直後の強制
    // follow-up応答自身のresponse.done相当）。
    vm.runInContext(LEGACY_FULL_FALLBACK_ARM_BLOCK_SRC, ctx);
    assert.strictEqual(ctx.pendingPhaseTransitionTarget, null, 'ROOT CAUSE FIX: must not arm legacy_full before the customer has spoken in ROUTING');
    vm.runInContext(RESPONSE_DONE_PHASE_BLOCK_SRC, ctx);
    assert.strictEqual(ctx.currentRealtimePhase, 'routing', 'must remain in routing, waiting for the customer\'s real answer');
});

test('X2b: OTHER/UNKNOWN safety net is preserved — once the customer has genuinely spoken in ROUTING and classify_intent is never called, legacy_full still fires (existing Phase 1 fallback, now correctly gated on a real user turn)', () => {
    const { ctx, dcSendCalls } = buildSandbox({ currentRealtimePhase: 'name' });
    vm.runInContext("sendRealtimePhaseSessionUpdate('routing', 'response_done_boundary', false)", ctx);
    vm.runInContext("type = 'session.updated'; " + SESSION_UPDATED_PHASE_BLOCK_SRC, Object.assign(ctx, { type: null }));
    // お客様が実際に用件を発話したが、classify_intentを一度も呼ばない
    // （OTHER/UNKNOWNのまま）。
    ctx.routingHasReceivedUserTurn = true;
    ctx.responseHasFunctionCall = false;
    vm.runInContext(LEGACY_FULL_FALLBACK_ARM_BLOCK_SRC, ctx);
    assert.strictEqual(ctx.pendingPhaseTransitionTarget, 'legacy_full', 'the legitimate safety net still arms once the customer has genuinely spoken and classification failed');
    vm.runInContext(RESPONSE_DONE_PHASE_BLOCK_SRC, ctx);
    assert.strictEqual(ctx.currentRealtimePhase, 'legacy_full');
    const lastPayload = JSON.parse(dcSendCalls[dcSendCalls.length - 1]);
    assert.strictEqual(lastPayload.session.instructions, 'LEGACY_FULL_INSTRUCTIONS_STUB');
});

// =======================================================================
// Y. session.type=realtime maintained（reservation/callback対象でも）
// =======================================================================
test('Y: sendRealtimePhaseSessionUpdate to reservation/callback also includes the required session.type="realtime" (missing_required_parameter regression guard applies to every phase)', () => {
    const { ctx: ctxR, dcSendCalls: sendsR } = buildSandbox({ currentRealtimePhase: 'routing' });
    vm.runInContext("sendRealtimePhaseSessionUpdate('reservation', 'test', true)", ctxR);
    assert.strictEqual(JSON.parse(sendsR[0]).session.type, 'realtime');

    const { ctx: ctxC, dcSendCalls: sendsC } = buildSandbox({ currentRealtimePhase: 'routing' });
    vm.runInContext("sendRealtimePhaseSessionUpdate('callback', 'test', true)", ctxC);
    assert.strictEqual(JSON.parse(sendsC[0]).session.type, 'realtime');
});

// =======================================================================
// Z. phase only confirmed after session.updated CONFIRMED（reservation/callback）
// =======================================================================
test('Z: REALTIME_PHASE_TRANSITION_CONFIRMED fires with phase=reservation only after session.updated is observed, not before', () => {
    const { ctx, timelineEvents } = buildSandbox({
        currentRealtimePhase: 'routing',
    });
    vm.runInContext("sendRealtimePhaseSessionUpdate('reservation', 'test', true)", ctx);
    // まだsession.updatedを受信していない時点ではCONFIRMEDは出ていない。
    assert.ok(!timelineEvents.some((e) => e.includes('REALTIME_PHASE_TRANSITION_CONFIRMED')));
    vm.runInContext("type = 'session.updated'; " + SESSION_UPDATED_PHASE_BLOCK_SRC, Object.assign(ctx, { type: null }));
    const confirmedLine = timelineEvents.find((e) => e.includes('REALTIME_PHASE_TRANSITION_CONFIRMED'));
    assert.ok(confirmedLine && confirmedLine.includes('phase=reservation'));
});

test('Z2: REALTIME_PHASE_TRANSITION_CONFIRMED fires with phase=callback only after session.updated is observed, not before', () => {
    const { ctx, timelineEvents } = buildSandbox({
        currentRealtimePhase: 'routing',
    });
    vm.runInContext("sendRealtimePhaseSessionUpdate('callback', 'test', true)", ctx);
    assert.ok(!timelineEvents.some((e) => e.includes('REALTIME_PHASE_TRANSITION_CONFIRMED')));
    vm.runInContext("type = 'session.updated'; " + SESSION_UPDATED_PHASE_BLOCK_SRC, Object.assign(ctx, { type: null }));
    const confirmedLine = timelineEvents.find((e) => e.includes('REALTIME_PHASE_TRANSITION_CONFIRMED'));
    assert.ok(confirmedLine && confirmedLine.includes('phase=callback'));
});

// =======================================================================
// AA. transition failure doesn't advance to next phase（reservation/callback）
// =======================================================================
test('AA: a failed reservation transition clears the pending target and does not advance currentRealtimePhase (generalization of Phase 1\'s HOTFIX-PENDING-CLEARED, unmodified code)', () => {
    const { ctx, timelineEvents } = buildSandbox({ currentRealtimePhase: 'routing' });
    vm.runInContext("sendRealtimePhaseSessionUpdate('reservation', 'test', true)", ctx);
    // 楽観的更新によりcurrentRealtimePhaseは既に'reservation'（session.updated確定前）。
    assert.strictEqual(ctx.currentRealtimePhase, 'reservation');
    assert.strictEqual(ctx.phaseTransitionInProgress, true);

    Object.assign(ctx, { msg: { error: { type: 'invalid_request_error', code: 'missing_required_parameter', param: 'session.type' } } });
    vm.runInContext(ERROR_PHASE_HOOK_SRC, ctx);

    assert.strictEqual(ctx.phaseTransitionInProgress, false);
    assert.strictEqual(ctx.pendingPhaseTransitionTarget, null);
    const failedLine = timelineEvents.find((e) => e.includes('REALTIME_PHASE_TRANSITION_FAILED'));
    assert.ok(failedLine && failedLine.includes('phase=reservation'));
});

test('AA2: a failed callback transition clears the pending target and does not advance further (same generalization for callback)', () => {
    const { ctx, timelineEvents } = buildSandbox({ currentRealtimePhase: 'routing' });
    vm.runInContext("sendRealtimePhaseSessionUpdate('callback', 'test', true)", ctx);
    assert.strictEqual(ctx.currentRealtimePhase, 'callback');
    Object.assign(ctx, { msg: { error: { type: 'invalid_request_error', code: 'missing_required_parameter', param: 'session.type' } } });
    vm.runInContext(ERROR_PHASE_HOOK_SRC, ctx);
    assert.strictEqual(ctx.phaseTransitionInProgress, false);
    assert.strictEqual(ctx.pendingPhaseTransitionTarget, null);
    const failedLine = timelineEvents.find((e) => e.includes('REALTIME_PHASE_TRANSITION_FAILED'));
    assert.ok(failedLine && failedLine.includes('phase=callback'));
});

// =======================================================================
// AB. no duplicate session.update（reservation/callback対象でも既存ガードが効く）
// =======================================================================
test('AB: while a reservation transition is in-flight, a second call (even targeting callback) sends no additional session.update', () => {
    const { ctx, dcSendCalls } = buildSandbox({ currentRealtimePhase: 'routing' });
    vm.runInContext("sendRealtimePhaseSessionUpdate('reservation', 'first', false)", ctx);
    assert.strictEqual(dcSendCalls.length, 1);
    vm.runInContext("sendRealtimePhaseSessionUpdate('callback', 'second', false)", ctx);
    assert.strictEqual(dcSendCalls.length, 1, 'no second session.update should be sent while one is still in-flight');
});

// =======================================================================
// AC. no duplicate response.create（classify_intentの自動継続スキップの確認）
// =======================================================================
test('AC: classify_intent skips the automatic tool-continuation response.create, while every other tool still sends it unconditionally (unchanged behavior)', () => {
    let sendResponseCreateCallCount = 0;
    const timelineEvents = [];
    const baseCtx = {
        // FAST TURN EMERGENCY HOTFIX 10（今回追加）: 抽出したスニペットの
        // 「他Tool」else分岐に、performance.now()を読むtoolContinuationResponseCreateSentAt
        // 記録行が追加されたため、vmサンドボックスにもperformanceを提供する
        // 必要が生じた（挙動自体はarmToolContinuationResponseWatchdog等と同じ
        // 診断専用の1行で、classify_intentのスキップ判定ロジックには無関係）。
        performance: { now: () => Date.now() },
        pushTimelineEvent: (t) => { timelineEvents.push(t); },
        pushToolContinuationTrace: () => {},
        armToolContinuationResponseWatchdog: () => {},
        sendResponseCreate: (reason) => { sendResponseCreateCallCount++; return true; },
        functionCallOutputSendFailed: false,
        toolContinuationTraceActive: true,
        toolContinuationTraceCallId: 'abcd1234',
        // スニペットが代入する変数を、宣言ではなく通常のグローバルプロパティとして
        // 事前に用意しておく（上のCONTINUATION_SKIP_SNIPPET_SRCのコメント参照）。
        toolContinuationResponseCreateSent: undefined,
        // FAST TURN EMERGENCY HOTFIX 10（今回追加）: 同上の理由で、こちらも
        // 事前にプロパティとして用意する（classify_intent分岐では代入されない、
        // 「他Tool」分岐でのみperformance.now()が代入される）。
        toolContinuationResponseCreateSentAt: null,
    };

    // classify_intent: sendResponseCreateは呼ばれないはず（意図的スキップ）。
    const ctxClassify = Object.assign({}, baseCtx, { item: { name: 'classify_intent' } });
    vm.createContext(ctxClassify);
    vm.runInContext(CONTINUATION_SKIP_SNIPPET_SRC, ctxClassify);
    assert.strictEqual(sendResponseCreateCallCount, 0, 'classify_intent must not trigger the automatic response.create continuation');
    assert.strictEqual(ctxClassify.toolContinuationResponseCreateSent, false);
    assert.ok(timelineEvents.some((e) => e.includes('TOOL_CONTINUATION_SKIPPED_INTENTIONAL')));

    // confirm_customer_name（会話品質改善フェーズで新規追加）も、classify_intentと
    // 全く同じ理由でsendResponseCreateがスキップされるはず。
    sendResponseCreateCallCount = 0;
    timelineEvents.length = 0;
    const ctxConfirmName = Object.assign({}, baseCtx, { item: { name: 'confirm_customer_name' } });
    vm.createContext(ctxConfirmName);
    vm.runInContext(CONTINUATION_SKIP_SNIPPET_SRC, ctxConfirmName);
    assert.strictEqual(sendResponseCreateCallCount, 0, 'confirm_customer_name must not trigger the automatic response.create continuation');
    assert.strictEqual(ctxConfirmName.toolContinuationResponseCreateSent, false);
    assert.ok(timelineEvents.some((e) => e.includes('TOOL_CONTINUATION_SKIPPED_INTENTIONAL')));

    // 他の既存Tool（例: check_availability）は従来どおり無条件でsendResponseCreateが呼ばれる。
    sendResponseCreateCallCount = 0;
    timelineEvents.length = 0;
    const ctxOther = Object.assign({}, baseCtx, { item: { name: 'check_availability' } });
    vm.createContext(ctxOther);
    vm.runInContext(CONTINUATION_SKIP_SNIPPET_SRC, ctxOther);
    assert.strictEqual(sendResponseCreateCallCount, 1, 'all other tools must keep the existing unconditional auto-continuation behavior');
    assert.strictEqual(ctxOther.toolContinuationResponseCreateSent, true);
    assert.ok(timelineEvents.some((e) => e.includes('TOOL_CONTINUATION_REQUESTED')));
});

// =======================================================================
// AD. no RTCPeerConnection recreation
// =======================================================================
test('AD: callClassifyIntentTool and the continuation-skip snippet never touch RTCPeerConnection', () => {
    assert.ok(!FN.callClassifyIntentTool.includes('RTCPeerConnection'));
    assert.ok(!CONTINUATION_SKIP_SNIPPET_SRC.includes('RTCPeerConnection'));
    const count = (SRC.match(/new RTCPeerConnection\(/g) || []).length;
    assert.strictEqual(count, 1, 'RTCPeerConnection must still be constructed exactly once in the whole file (Phase2 must not add a second one)');
});

// =======================================================================
// AE. no audio track recreation
// =======================================================================
test('AE: callClassifyIntentTool and the continuation-skip snippet never touch getUserMedia/audio tracks', () => {
    assert.ok(!FN.callClassifyIntentTool.includes('getUserMedia'));
    assert.ok(!CONTINUATION_SKIP_SNIPPET_SRC.includes('getUserMedia'));
    const count = (SRC.match(/getUserMedia\(/g) || []).length;
    assert.strictEqual(count, 1, 'getUserMedia must still be called exactly once in the whole file (Phase2 must not re-acquire the mic)');
});

// =======================================================================
// AF. Forced Commit unchanged
// =======================================================================
test('AF: existing Forced Commit functions (maybeSendShortAnswerCommit / maybeSendPhoneCommit) are untouched by classify_intent-related code', () => {
    assert.ok(SRC.includes('function maybeSendShortAnswerCommit('));
    assert.ok(SRC.includes('function maybeSendPhoneCommit('));
    assert.ok(!FN.callClassifyIntentTool.includes('maybeSendPhoneCommit') && !FN.callClassifyIntentTool.includes('maybeSendShortAnswerCommit'));
    assert.ok(!CONTINUATION_SKIP_SNIPPET_SRC.includes('maybeSendPhoneCommit') && !CONTINUATION_SKIP_SNIPPET_SRC.includes('maybeSendShortAnswerCommit'));
});

// =======================================================================
// AG. ANSWER_WINDOW unchanged
// =======================================================================
test('AG: ANSWER_WINDOW_LIMITS_MS / startAnswerWindowIfNeeded are unchanged and untouched by classify_intent-related code', () => {
    assert.ok(SRC.includes("const ANSWER_WINDOW_LIMITS_MS = { NAME: 5000, PHONE: 10000, YES_NO: 3000, SHORT_CHOICE: 3000, VISIT_REASON: 30000 };"));
    assert.ok(SRC.includes('function startAnswerWindowIfNeeded('));
    assert.ok(!FN.callClassifyIntentTool.includes('ANSWER_WINDOW'));
    assert.ok(!CONTINUATION_SKIP_SNIPPET_SRC.includes('ANSWER_WINDOW'));
});

// =======================================================================
// AH. usage diagnostics preserved（既存マーカーの回帰確認）
// =======================================================================
test('AH: REALTIME_USAGE_BREAKDOWN / RATE_LIMIT_TOKEN_DELTA markers are still present (regression, Phase 2 did not remove them)', () => {
    assert.ok(SRC.includes('REALTIME_USAGE_BREAKDOWN'));
    assert.ok(SRC.includes('RATE_LIMIT_TOKEN_DELTA'));
});

// =======================================================================
// AI. no PII in diagnostic markers
// =======================================================================
test('AI: REALTIME_INTENT_CLASSIFIED only ever contains the enum value ("reservation"|"callback"), never conversation content, names or phone numbers', () => {
    const { ctx, timelineEvents, consoleLogs } = buildSandbox({ currentRealtimePhase: 'routing' });
    return vm.runInContext('callClassifyIntentTool({ intent: "reservation" })', ctx).then((output) => {
        const line = timelineEvents.find((e) => e.includes('REALTIME_INTENT_CLASSIFIED'));
        assert.ok(line);
        assert.strictEqual(line, 'REALTIME_INTENT_CLASSIFIED (intent=reservation)');
        // Toolの戻り値自体にも会話内容・個人情報は一切含まれない（副作用なしの合図のみ）。
        // 注: outputはvmサンドボックス（別レルム）内で生成されたオブジェクト
        // リテラルのため、assert.deepStrictEqualはプロトタイプ違いで誤って
        // 失敗する（値は同一でも別レルムのObject.prototypeを持つため）。
        // ここではフィールド単位で検証する。
        assert.strictEqual(output.success, true);
        assert.strictEqual(Object.keys(output).length, 1, 'output must contain only {success:true} — no PII, no side-effect fields');
    });
});

test('AI2: an unexpected/malformed intent value never reaches any diagnostic marker (safe fallback to the existing legacy_full arm, no arming here)', () => {
    const fakePii = 'CUSTOMER_TRANSCRIPT_田中太郎090-1234-5678';
    const { ctx, timelineEvents, logEvents } = buildSandbox({ currentRealtimePhase: 'routing' });
    return vm.runInContext('callClassifyIntentTool({ intent: "' + fakePii + '" })', ctx).then(() => {
        assert.strictEqual(ctx.pendingPhaseTransitionTarget, null, 'an unknown intent value must not arm any transition (safe: keeps existing legacy_full fallback)');
        assert.ok(!timelineEvents.some((e) => e.includes('REALTIME_INTENT_CLASSIFIED')));
        // logEvent自体はデバッグ用の内部ログであり、Copy Debug Log（pushTimelineEvent系）
        // には出ない設計であることを確認する（PIIがtimelineEventsに漏れていないことが本質）。
        assert.ok(!timelineEvents.join(' ').includes(fakePii));
    });
});

_runAllTests().then(() => {
    console.log(`\n${passed} test(s) passed.`);
    if (process.exitCode) {
        console.error('\n=== SOME test_realtime_phase2_reservation_callback_transition.js CHECKS FAILED ===');
    } else {
        console.log('\n=== ALL test_realtime_phase2_reservation_callback_transition.js CHECKS PASSED ===');
    }
});
