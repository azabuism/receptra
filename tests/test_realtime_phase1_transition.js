'use strict';

/**
 * RECEPTRA — Realtime Token Architecture Phase 1（2026年9月）
 * NAME → ROUTING → legacy_full の session.update フェーズ遷移契約テスト
 * （フロントエンド側。ユーザー指定の§12契約テストA〜TのうちI〜Tをカバーする。
 * A〜Hはバックエンド側でtests/smoke_test_realtime_phase1_name.pyが検証済み）。
 *
 * 背景: 実機で「input_tokens≈20,550・cached_ratio≈0.997でもrate_limit_exceeded
 * が発生する」ことが確認され、通話のごく早い段階から毎ターン19,753文字の
 * instructions・8個のtoolsを送り続けている構造自体が支配的要因である可能性が
 * 高いと判断し、Architecture A（同一Realtime session内、session.updateによる
 * Phase別最小化）を採用した。今回はPhase 1（NAME）と、NAME→ROUTINGの
 * session.update境界のみを実装する（Reservation/Callback Phase本実装・
 * Phase 3 PHONE・STT別AI・別Realtime session・PeerConnection再接続は今回
 * 実装しない）。ユーザー承認済み設計（AskUserQuestion）により、ROUTING
 * phaseのack応答完了直後、無条件で既存フル機能（legacy_full）へ自動
 * フォールバックする。
 *
 * 本テストが確認する項目（I〜T。カッコ内は§12のラベル）:
 *   I. NAME first-answer behavior remains（既存のNAME_FIRST_FLOW記録は
 *      Phase1のarm処理を追加しても壊れない）
 *   J. after name, exactly one NAME→ROUTING transition（同じNAME回答に対して
 *      複数のイベントハンドラ経由でarmが呼ばれても、二重にarmされない）
 *   K. session.update is sent exactly once（1回のtransitionにつきdc.send呼び出しは1回）
 *   L. duplicate transition is prevented（既に同じphaseにいる／既に進行中の
 *      場合は送信されない）
 *   M. PeerConnection is not recreated（sendRealtimePhaseSessionUpdate内に
 *      new RTCPeerConnection(の呼び出しが無い。全体でも既存の1箇所のまま）
 *   N. audio track is not recreated（同上・getUserMedia(の呼び出しが無い。
 *      全体でも既存の1箇所のまま）
 *   O. existing ANSWER_WINDOW remains（ANSWER_WINDOW_LIMITS_MS・
 *      startAnswerWindowIfNeededは変更されず、Phase1コードから参照・
 *      書き換えされていない）
 *   P. existing Forced Commit remains（maybeSendShortAnswerCommit/
 *      maybeSendPhoneCommit等は変更されず、Phase1コードから参照・
 *      書き換えされていない）
 *   Q. existing AI SPEAKING PROTECTION remains（releaseAiSpeakingProtection等は
 *      変更されず、Phase1のerrorハンドラhookは独立したブロックである）
 *   R. REALTIME_USAGE_BREAKDOWNマーカーが引き続き存在する（回帰確認）
 *   S. RATE_LIMIT_TOKEN_DELTAマーカーが引き続き存在する（回帰確認）
 *   T. Phase1の4つの新規診断マーカー（REALTIME_PHASE_CONTEXT/
 *      REALTIME_PHASE_TRANSITION/REALTIME_SESSION_UPDATE_SENT/
 *      REALTIME_SESSION_UPDATED）にPII（instructions本文・tools本文・
 *      会話内容）が一切含まれない
 *
 * 実行: node tests/test_realtime_phase1_transition.js
 */

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
    classifyExpectedAnswerType: extractFunctionSource(SRC, 'classifyExpectedAnswerType'),
    pushNameFirstFlowEvent: extractFunctionSource(SRC, 'pushNameFirstFlowEvent'),
    maybeRecordNameFirstAnswer: extractFunctionSource(SRC, 'maybeRecordNameFirstAnswer'),
    armPhaseTransitionAfterResponse: extractFunctionSource(SRC, 'armPhaseTransitionAfterResponse'),
    sendRealtimePhaseSessionUpdate: extractFunctionSource(SRC, 'sendRealtimePhaseSessionUpdate'),
};

function extractConstExpr(src, name) {
    const re = new RegExp('const\\s+' + name + '\\s*=\\s*([^;]+);');
    const m = src.match(re);
    if (!m) throw new Error('const not found in source: ' + name);
    return m[1].trim();
}

// NOISE_DIAG（今回追加・雑音誤検知対策 TASK D）: maybeRecordNameFirstAnswer()の
// NAME分岐が新たに参照する、有効ターン最小継続時間のしきい値定数。未定義だと
// ReferenceErrorになるため、実ソースからそのまま抽出して注入する。
const MIN_VALID_TURN_MS_FOR_NAME_ATTEMPT_EXPR = extractConstExpr(SRC, 'MIN_VALID_TURN_MS_FOR_NAME_ATTEMPT');

// response.done境界の消費ロジック（独立ifブロック。関数ではないためextractBlockで抽出）。
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

function buildSandbox(overrides) {
    const timelineEvents = [];
    const consoleLogs = [];
    const dcSendCalls = [];
    const context = {
        performance: { now: () => Date.now() },
        pushTimelineEvent: (text) => { timelineEvents.push(text); },
        console: { log: (...args) => { consoleLogs.push(args); } },
        callStartedAt: Date.now() - 1234,
        dc: { readyState: 'open', send: (payload) => { dcSendCalls.push(payload); } },
        sendResponseCreate: () => true,
        // NOISE_DIAG（今回追加）: maybeRecordNameFirstAnswer()のNAME分岐が
        // NAME_CAPTURE_MAX_ATTEMPTS到達時に条件付きで呼ぶ関数のモック。
        triggerNoisyEnvironmentGoodbye: () => {},
        callGeneration: 0,
        // HOTFIX（今回追加）: ERROR_PHASE_HOOK_SRC・SESSION_UPDATED_PHASE_BLOCK_SRC
        // をvm.runInContextで直接実行するテスト用に、実ファイルのモジュール
        // スコープ変数debugMode・msgをサンドボックスにも用意しておく
        // （未定義のままだとReferenceErrorがtry/catchに飲み込まれ、テストが
        // 「静かに何も検証していない」状態になってしまうのを防ぐ）。
        debugMode: false,
        msg: {},
    };
    const state = Object.assign({
        expectedAnswerType: 'NONE',
        // DUPLICATE RESPONSE HOTFIX（今回追加）: response.doneのphase-
        // transition consumeブロックがreservation/callbackターゲットに
        // ついてもこれら2フラグを参照するようになったため、既存の
        // sandboxデフォルトにも追加する（本番側のデフォルト値falseと同じ）。
        lastResponseTranscriptWasProcessNarrationOnly: false,
        lastResponseTranscriptWasIncompleteAiTurn: false,
        nameAnswerGeneration: 0,
        nameTurnNormalCompletionSeen: false,
        phoneAnswerGeneration: 0,
        phoneTurnNormalCompletionSeen: false,
        shortChoiceAnswerGeneration: 0,
        shortChoiceTurnNormalCompletionSeen: false,
        quickAnswerGeneration: 0,
        quickAnswerTurnNormalCompletionSeen: false,
        nameFirstFlowState: { hasName: false, hasPurpose: false, hasDate: false, hasTime: false, hasPartySize: false },
        lastAiTranscriptHadDateKeyword: false,
        lastAiTranscriptHadTimeKeyword: false,
        lastAiTranscriptHadPartySizeKeyword: false,
        nameFirstStageLogged: { opening_question: false, name_answer_received: false, purpose_question: false, purpose_answer_received: false, before_first_tool_call: false },
        currentRealtimePhase: 'name',
        realtimePhaseContexts: {
            name: { instructions: 'NAME_PHASE_INSTRUCTIONS_STUB', tools: [] },
            routing: { instructions: 'ROUTING_PHASE_INSTRUCTIONS_STUB', tools: [] },
            legacy_full: { instructions: 'LEGACY_FULL_INSTRUCTIONS_STUB', tools: [{ name: 'check_availability' }] },
        },
        phaseTransitionInProgress: false,
        pendingPhaseTransitionTarget: null,
        pendingPhaseTransitionForceFollowUp: null,
        pendingPhaseTransitionReasonForFollowUp: null,
        phaseTransitionSessionUpdateSentAt: null,
        // HOTFIX（今回追加）: REALTIME_PHASE_TRANSITION_CONFIRMED診断が参照する
        // 実ファイル側のモジュールスコープ変数。既定値はnull（実ファイルと同じ）。
        phaseTransitionInstructionsChars: null,
        phaseTransitionToolCount: null,
        // 会話品質改善フェーズ（2026年9月）追記: maybeRecordNameFirstAnswer()の
        // 無限ループ防止カウンタ（実ファイルと同じ初期値）。
        nameCaptureAttempts: 0,
        NAME_CAPTURE_MAX_ATTEMPTS: 3,
        // NOISE_DIAG（今回追加・雑音誤検知対策 TASK D）: 1ターンにつき1回だけ
        // カウントするためのガードと、雑音判定関連の状態（実ファイルと同じ
        // 初期値）。既定ではlastSpeechStartedAt/lastSpeechStoppedAtは共に
        // nullとし、通常の「音声継続時間による短すぎるターン判定」は発火
        // しない（＝この既存テストファイルの対象外のテストは全て従来どおり
        // 「有効なターン」として扱われる）。雑音判定そのものの詳細な検証は
        // tests/test_noisy_environment_turn_boundary.jsが専任で担当する。
        nameCaptureAttemptCountedThisTurn: false,
        nameCaptureShortTurnRejections: 0,
        lastSpeechStartedAt: null,
        lastSpeechStoppedAt: null,
    }, overrides || {});
    Object.assign(context, state);
    vm.createContext(context);
    vm.runInContext(
        'const MIN_VALID_TURN_MS_FOR_NAME_ATTEMPT = ' + MIN_VALID_TURN_MS_FOR_NAME_ATTEMPT_EXPR + ';\n' +
        Object.values(FN).join('\n\n'),
        context
    );
    return { ctx: context, timelineEvents, consoleLogs, dcSendCalls };
}

let passed = 0;
function test(name, fn) {
    try {
        fn();
        passed++;
        console.log('PASS: ' + name);
    } catch (e) {
        console.error('FAIL: ' + name);
        console.error(e);
        process.exitCode = 1;
    }
}

// =======================================================================
// I. NAME first-answer behavior remains
// =======================================================================
test('I: 会話品質改善フェーズ（2026年9月）更新: existing NAME-FIRST-FLOW hasName/name_answer_received diagnostics are unaffected, but maybeRecordNameFirstAnswer() alone no longer arms the ROUTING transition (that is now confirm_customer_name Tool\'s job)', () => {
    // 根本対策: 「NAME質問への回答ターンが完了した」というイベント発火だけを
    // 根拠にROUTINGへarmしていた旧ロジックは、ユーザーが名前ではない発話を
    // した場合でも同じイベントが発火するため、実機で「名前を取得せずに
    // ROUTINGへ進む」バグの直接原因だった。診断用のhasName/name_answer_received
    // 記録自体は既存どおり変更しない。
    const { ctx, timelineEvents } = buildSandbox({ expectedAnswerType: 'NAME' });
    vm.runInContext('maybeRecordNameFirstAnswer()', ctx);
    assert.strictEqual(ctx.nameFirstFlowState.hasName, true);
    assert.ok(timelineEvents.some((e) => e.includes('stage=name_answer_received')));
    // 新契約: この呼び出し単独ではROUTINGへarmしない（1回目のため安全網の
    // NAME_CAPTURE_MAX_ATTEMPTSにもまだ達していない）。
    assert.strictEqual(ctx.pendingPhaseTransitionTarget, null);
    assert.strictEqual(ctx.nameCaptureAttempts, 1);
});

// =======================================================================
// J. after name, exactly one NAME→ROUTING transition
// =======================================================================
test('J: 会話品質改善フェーズ（2026年9月）更新・雑音誤検知対策 TASK Dで修正: calling maybeRecordNameFirstAnswer twice WITHOUT an intervening physical turn boundary (speech_started) must NOT double-count (root-cause fix; no bare arm); armPhaseTransitionAfterResponse is what actually arms', () => {
    // 根本バグ修正の経緯（TASK D・実測で確認）: 呼び出し元はinput_audio_buffer.
    // committed / conversation.item.created / input_audio_buffer.speech_stoppedの
    // 3箇所あり、通常の1回の物理的なユーザーターンでこの3つ全てが発火する。
    // 修正前は、この関数自体にターンの重複排除ガードが無かったため、1物理
    // ターンにつきnameCaptureAttemptsが最大3ずつ進んでしまう深刻なバグが
    // あった（TASK D STEP1監査で実測により確認）。修正後は、実ファイル側の
    // input_audio_buffer.speech_startedハンドラでnameCaptureAttemptCountedThisTurn
    // をfalseへリセットする設計とし、この関数自体は「そのフラグがまだfalseの
    // 場合のみ1回だけカウントする」よう変更した。本テストはその場合分けの
    // うち「フラグをリセットしないまま同じ関数を2回呼ぶ」＝同一物理ターン内で
    // 複数イベントハンドラから呼ばれるケースを再現し、2回目が加算されない
    // ことを確認する（別の物理ターンとして正しく2回進むケースはJ4で確認）。
    const { ctx } = buildSandbox({ expectedAnswerType: 'NAME' });
    vm.runInContext('maybeRecordNameFirstAnswer()', ctx);
    vm.runInContext('maybeRecordNameFirstAnswer()', ctx);
    assert.strictEqual(ctx.pendingPhaseTransitionTarget, null, 'still under NAME_CAPTURE_MAX_ATTEMPTS, must not have armed yet');
    assert.strictEqual(ctx.nameCaptureAttempts, 1, 'the second call within the same physical turn (no speech_started reset in between) must NOT double-count (root-cause fix)');
    // confirm_customer_name Tool相当の直接armは、既存どおり正しく機能する。
    vm.runInContext("armPhaseTransitionAfterResponse('routing')", ctx);
    assert.strictEqual(ctx.pendingPhaseTransitionTarget, 'routing');
    // 直後にもう一度armしても状態は変化しない（既存の多重arm防止ガード）。
    vm.runInContext("armPhaseTransitionAfterResponse('routing')", ctx);
    assert.strictEqual(ctx.pendingPhaseTransitionTarget, 'routing');
});

test('J6: 雑音誤検知対策 TASK D新規（根本バグの直接回帰防止テスト）: 3 calls to maybeRecordNameFirstAnswer() for the SAME physical turn (simulating committed + conversation.item.created + speech_stopped all firing for one turn, real or noise-triggered) still only count as 1 attempt', () => {
    const { ctx } = buildSandbox({ expectedAnswerType: 'NAME' });
    vm.runInContext('maybeRecordNameFirstAnswer()', ctx); // input_audio_buffer.committed相当
    vm.runInContext('maybeRecordNameFirstAnswer()', ctx); // conversation.item.created相当
    vm.runInContext('maybeRecordNameFirstAnswer()', ctx); // input_audio_buffer.speech_stopped相当
    assert.strictEqual(ctx.nameCaptureAttempts, 1, 'all 3 same-turn call sites together must count as exactly 1 attempt, not 3');
    assert.strictEqual(ctx.pendingPhaseTransitionTarget, null, 'must not reach NAME_CAPTURE_MAX_ATTEMPTS=3 from a single physical turn');
});

test('J3: 会話品質改善フェーズ（2026年9月）新規（根本バグの直接再現・回帰防止）: a non-name utterance (turn completes while expectedAnswerType===NAME, e.g. customer said something else) does NOT by itself arm ROUTING before the safety-net threshold', () => {
    // 実機で観測されたバグの直接シナリオ: 「予約したいです」等、お名前を
    // 含まない発話でも input_audio_buffer.committed 等のターン完了イベントは
    // 同じく発火する。confirm_customer_nameが呼ばれていない限り、この
    // イベント単独ではROUTINGへ進んではならない。
    const { ctx } = buildSandbox({ expectedAnswerType: 'NAME' });
    vm.runInContext('maybeRecordNameFirstAnswer()', ctx);
    assert.strictEqual(ctx.currentRealtimePhase, 'name');
    assert.strictEqual(ctx.pendingPhaseTransitionTarget, null);
});

test('J4: 会話品質改善フェーズ（2026年9月）新規（無限ループ防止・最小安全案）・雑音誤検知対策 TASK Dで更新: after NAME_CAPTURE_MAX_ATTEMPTS DISTINCT PHYSICAL TURN-completions without confirm_customer_name, maybeRecordNameFirstAnswer() forces the ROUTING transition as a safety net', () => {
    // TASK D修正後の契約: 1回の安全網カウントは1回の「物理的なターン」
    // （input_audio_buffer.speech_startedを境界とする）につき最大1回。
    // ここでは実ファイル側のspeech_startedハンドラが行うのと同じリセット
    // （nameCaptureAttemptCountedThisTurn = false）を各呼び出しの直前で
    // シミュレートし、3回の「別々の物理ターン」で安全網に到達することを
    // 確認する（同一ターン内での多重カウントをさせないことはJ・J6が担当）。
    const { ctx, timelineEvents } = buildSandbox({ expectedAnswerType: 'NAME', NAME_CAPTURE_MAX_ATTEMPTS: 3 });
    ctx.nameCaptureAttemptCountedThisTurn = false; // 1ターン目の開始（speech_started相当）
    vm.runInContext('maybeRecordNameFirstAnswer()', ctx);
    assert.strictEqual(ctx.pendingPhaseTransitionTarget, null);
    assert.strictEqual(ctx.nameCaptureAttempts, 1);
    ctx.nameCaptureAttemptCountedThisTurn = false; // 2ターン目の開始（speech_started相当）
    vm.runInContext('maybeRecordNameFirstAnswer()', ctx);
    assert.strictEqual(ctx.pendingPhaseTransitionTarget, null);
    assert.strictEqual(ctx.nameCaptureAttempts, 2);
    ctx.nameCaptureAttemptCountedThisTurn = false; // 3ターン目の開始（speech_started相当）
    vm.runInContext('maybeRecordNameFirstAnswer()', ctx);
    assert.strictEqual(ctx.pendingPhaseTransitionTarget, 'routing');
    assert.strictEqual(ctx.nameCaptureAttempts, 3);
    assert.ok(timelineEvents.some((e) => e.includes('NAME_CAPTURE_MAX_ATTEMPTS_REACHED')));
});

test('J5: 会話品質改善フェーズ（2026年9月）新規: the safety-net counter only increments while still in the NAME phase (does not fire once already transitioned)', () => {
    const { ctx } = buildSandbox({ expectedAnswerType: 'NAME', currentRealtimePhase: 'routing' });
    vm.runInContext('maybeRecordNameFirstAnswer()', ctx);
    assert.strictEqual(ctx.nameCaptureAttempts, 0, 'counter must not increment once the phase has already moved on from name');
    assert.strictEqual(ctx.pendingPhaseTransitionTarget, null);
});

test('J2: arming is a no-op once already in the target phase or a transition is in progress', () => {
    const { ctx: ctxAlreadyThere } = buildSandbox({ currentRealtimePhase: 'routing' });
    vm.runInContext("armPhaseTransitionAfterResponse('routing')", ctxAlreadyThere);
    assert.strictEqual(ctxAlreadyThere.pendingPhaseTransitionTarget, null);

    const { ctx: ctxInProgress } = buildSandbox({ phaseTransitionInProgress: true });
    vm.runInContext("armPhaseTransitionAfterResponse('routing')", ctxInProgress);
    assert.strictEqual(ctxInProgress.pendingPhaseTransitionTarget, null);
});

// =======================================================================
// K. session.update is sent exactly once
// =======================================================================
test('K: sendRealtimePhaseSessionUpdate sends exactly one session.update over dc for a single transition', () => {
    const { ctx, dcSendCalls } = buildSandbox({});
    vm.runInContext("sendRealtimePhaseSessionUpdate('routing', 'test_reason', false)", ctx);
    assert.strictEqual(dcSendCalls.length, 1);
    const payload = JSON.parse(dcSendCalls[0]);
    assert.strictEqual(payload.type, 'session.update');
    // HOTFIX（今回追加・root cause回帰防止・§14）: OpenAI Realtime API SDK型定義
    // （RealtimeSessionCreateRequestParam.type: Required[Literal["realtime"]]、
    // SessionUpdateEventParam.session: Required[Session]がsession作成時と
    // 同一スキーマを使う）で確認済みの通り、session.updateのsessionオブジェクトは
    // type: 'realtime'が必須。この欠落が実機で観測されたmissing_required_parameter
    // の直接の原因だったため、実際にdc.send(JSON.stringify(...))へ渡される
    // 最終objectをJSON.parseした結果に対して明示的にassertする
    // （ソーステキストに'type'という文字列が含まれているかではなく、実際の
    // 送信payloadの形を検証する。§14の要求どおり）。
    assert.strictEqual(payload.session.type, 'realtime', 'session.update session object must include the required type:"realtime" discriminator (missing_required_parameter root cause)');
    assert.strictEqual(payload.session.instructions, 'ROUTING_PHASE_INSTRUCTIONS_STUB');
    assert.deepStrictEqual(payload.session.tools, []);
    assert.strictEqual(ctx.currentRealtimePhase, 'routing');
    assert.strictEqual(ctx.phaseTransitionInProgress, true);
    assert.strictEqual(ctx.phaseTransitionInstructionsChars, 'ROUTING_PHASE_INSTRUCTIONS_STUB'.length);
    assert.strictEqual(ctx.phaseTransitionToolCount, 0);
});

test('K3: sendRealtimePhaseSessionUpdate to legacy_full also includes session.type="realtime" (schema requirement applies to every phase, not just routing)', () => {
    const { ctx, dcSendCalls } = buildSandbox({ currentRealtimePhase: 'routing' });
    vm.runInContext("sendRealtimePhaseSessionUpdate('legacy_full', 'test_reason', true)", ctx);
    assert.strictEqual(dcSendCalls.length, 1);
    const payload = JSON.parse(dcSendCalls[0]);
    assert.strictEqual(payload.session.type, 'realtime');
    assert.strictEqual(payload.session.instructions, 'LEGACY_FULL_INSTRUCTIONS_STUB');
    assert.strictEqual(payload.session.tools.length, 1);
});

test('K2: a second call while a transition is already in-flight sends no additional session.update (no duplicate dc.send)', () => {
    const { ctx, dcSendCalls } = buildSandbox({});
    vm.runInContext("sendRealtimePhaseSessionUpdate('routing', 'first', false)", ctx);
    assert.strictEqual(dcSendCalls.length, 1);
    // まだsession.updatedを受信していない（phaseTransitionInProgress===true）間に
    // 同じ・別のtargetへ向けてもう一度呼んでも、送信は増えない。
    vm.runInContext("sendRealtimePhaseSessionUpdate('legacy_full', 'second', true)", ctx);
    assert.strictEqual(dcSendCalls.length, 1, 'no second session.update should be sent while one is still in-flight');
});

// =======================================================================
// L. duplicate transition is prevented
// =======================================================================
test('L: sendRealtimePhaseSessionUpdate is a no-op when already in the target phase', () => {
    const { ctx, dcSendCalls } = buildSandbox({ currentRealtimePhase: 'routing' });
    const result = vm.runInContext("sendRealtimePhaseSessionUpdate('routing', 'noop_reason', false)", ctx);
    assert.strictEqual(result, false);
    assert.strictEqual(dcSendCalls.length, 0);
});

test('L2: sendRealtimePhaseSessionUpdate is a no-op when dc is not open', () => {
    const { ctx, dcSendCalls } = buildSandbox({ dc: { readyState: 'connecting', send: () => { throw new Error('must not be called'); } } });
    const result = vm.runInContext("sendRealtimePhaseSessionUpdate('routing', 'dc_not_open', false)", ctx);
    assert.strictEqual(result, false);
    assert.strictEqual(dcSendCalls.length, 0);
});

// =======================================================================
// M. PeerConnection is not recreated
// =======================================================================
test('M: sendRealtimePhaseSessionUpdate never touches RTCPeerConnection, and the whole file still creates it exactly once', () => {
    assert.ok(!FN.sendRealtimePhaseSessionUpdate.includes('RTCPeerConnection'));
    assert.ok(!FN.armPhaseTransitionAfterResponse.includes('RTCPeerConnection'));
    const count = (SRC.match(/new RTCPeerConnection\(/g) || []).length;
    assert.strictEqual(count, 1, 'RTCPeerConnection must still be constructed exactly once in the whole file (Phase1 must not add a second one)');
});

// =======================================================================
// N. audio track is not recreated
// =======================================================================
test('N: sendRealtimePhaseSessionUpdate never touches getUserMedia/audio tracks, and the whole file still calls getUserMedia exactly once', () => {
    assert.ok(!FN.sendRealtimePhaseSessionUpdate.includes('getUserMedia'));
    assert.ok(!FN.armPhaseTransitionAfterResponse.includes('getUserMedia'));
    const count = (SRC.match(/getUserMedia\(/g) || []).length;
    assert.strictEqual(count, 1, 'getUserMedia must still be called exactly once in the whole file (Phase1 must not re-acquire the mic)');
});

// =======================================================================
// O. existing ANSWER_WINDOW remains
// =======================================================================
test('O: ANSWER_WINDOW_LIMITS_MS and startAnswerWindowIfNeeded are unchanged and untouched by Phase1 code', () => {
    assert.ok(SRC.includes("const ANSWER_WINDOW_LIMITS_MS = { NAME: 5000, PHONE: 10000, YES_NO: 3000, SHORT_CHOICE: 3000, VISIT_REASON: 30000 };"));
    assert.ok(SRC.includes('function startAnswerWindowIfNeeded('));
    assert.ok(!FN.sendRealtimePhaseSessionUpdate.includes('ANSWER_WINDOW'));
    assert.ok(!FN.armPhaseTransitionAfterResponse.includes('ANSWER_WINDOW'));
    assert.ok(!RESPONSE_DONE_PHASE_BLOCK_SRC.includes('ANSWER_WINDOW'));
});

// =======================================================================
// P. existing Forced Commit remains
// =======================================================================
test('P: existing Forced Commit functions (maybeSendShortAnswerCommit / maybeSendPhoneCommit) are unchanged and untouched by Phase1 code', () => {
    assert.ok(SRC.includes('function maybeSendShortAnswerCommit('));
    assert.ok(SRC.includes('function maybeSendPhoneCommit('));
    assert.ok(!FN.sendRealtimePhaseSessionUpdate.includes('ForcedCommit') && !FN.sendRealtimePhaseSessionUpdate.includes('maybeSendPhoneCommit') && !FN.sendRealtimePhaseSessionUpdate.includes('maybeSendShortAnswerCommit'));
    assert.ok(!RESPONSE_DONE_PHASE_BLOCK_SRC.includes('maybeSendPhoneCommit') && !RESPONSE_DONE_PHASE_BLOCK_SRC.includes('maybeSendShortAnswerCommit'));
});

// =======================================================================
// Q. existing AI SPEAKING PROTECTION remains
// =======================================================================
test('Q: releaseAiSpeakingProtection is unchanged, and the Phase1 error-handler hook is an independent block that does not call it', () => {
    assert.ok(SRC.includes('function releaseAiSpeakingProtection(reason) {'));
    assert.ok(!ERROR_PHASE_HOOK_SRC.includes('releaseAiSpeakingProtection'), 'the Phase1 error hook must not touch AI SPEAKING PROTECTION state');
    assert.ok(ERROR_PHASE_HOOK_SRC.includes('phaseTransitionInProgress = false'));
    // no-retry要件: このブロックはsendRealtimePhaseSessionUpdate/dc.sendを呼ばない（再送しない）。
    assert.ok(!ERROR_PHASE_HOOK_SRC.includes('sendRealtimePhaseSessionUpdate('));
    assert.ok(!ERROR_PHASE_HOOK_SRC.includes('dc.send('));
});

// =======================================================================
// R / S. existing usage/rate-limit diagnostics remain
// =======================================================================
test('R: REALTIME_USAGE_BREAKDOWN marker is still present (regression)', () => {
    assert.ok(SRC.includes('REALTIME_USAGE_BREAKDOWN'));
});

test('S: RATE_LIMIT_TOKEN_DELTA marker is still present (regression)', () => {
    assert.ok(SRC.includes('RATE_LIMIT_TOKEN_DELTA'));
});

// =======================================================================
// T. PII absent from the 4 new Phase1 diagnostic markers
// =======================================================================
test('T1: REALTIME_SESSION_UPDATE_SENT / REALTIME_PHASE_TRANSITION never include instructions or tools content itself (only counts)', () => {
    const { timelineEvents, consoleLogs } = buildSandbox({});
    // instructionsに万一お客様の会話内容らしき文字列が混入していたケースを
    // 想定し、フェイクの「PII」を仕込んでも診断行に一切現れないことを確認する。
    const fakePii = '田中太郎様090-1234-5678';
    const { ctx: ctx2, timelineEvents: events2, consoleLogs: logs2 } = buildSandbox({
        realtimePhaseContexts: {
            name: { instructions: 'NAME_STUB', tools: [] },
            routing: { instructions: 'ROUTING_STUB_' + fakePii, tools: [] },
            legacy_full: { instructions: 'LEGACY_STUB', tools: [{ name: 'check_availability' }] },
        },
    });
    vm.runInContext("sendRealtimePhaseSessionUpdate('routing', 'test', false)", ctx2);
    const allTimelineText = events2.join(' ');
    const allConsoleText = logs2.map((args) => args.join(' ')).join(' ');
    assert.ok(!allTimelineText.includes(fakePii), 'REALTIME_PHASE_TRANSITION/REALTIME_SESSION_UPDATE_SENT must never include instructions content');
    assert.ok(!allConsoleText.includes(fakePii));
    const transitionLine = events2.find((e) => e.includes('REALTIME_PHASE_TRANSITION'));
    const sentLine = events2.find((e) => e.includes('REALTIME_SESSION_UPDATE_SENT'));
    assert.ok(transitionLine && /from=name, to=routing, reason=test, ms_since_call_start=(\d+|null)/.test(transitionLine));
    assert.ok(sentLine && /phase=routing, instructions_chars=\d+, tools_chars=\d+, tool_count=\d+/.test(sentLine));
});

test('T2: REALTIME_SESSION_UPDATED never includes instructions/tools content (only phase name and elapsed ms)', () => {
    const { ctx, timelineEvents, consoleLogs } = buildSandbox({
        phaseTransitionInProgress: true,
        currentRealtimePhase: 'routing',
        phaseTransitionSessionUpdateSentAt: Date.now() - 42,
        pendingPhaseTransitionForceFollowUp: false,
        pendingPhaseTransitionReasonForFollowUp: 'phase_transition_to_routing',
    });
    vm.runInContext("type = 'session.updated'; " + SESSION_UPDATED_PHASE_BLOCK_SRC, Object.assign(ctx, { type: null }));
    const updatedLine = timelineEvents.find((e) => e.includes('REALTIME_SESSION_UPDATED'));
    assert.ok(updatedLine, 'REALTIME_SESSION_UPDATED must be logged');
    assert.ok(/phase=routing, ms_since_update_sent=\d+/.test(updatedLine));
    assert.strictEqual(ctx.phaseTransitionInProgress, false);
});

test('T3: REALTIME_PHASE_CONTEXT-style lines produced by the response.done boundary block never leak instructions content', () => {
    const fakePii = 'CUSTOMER_TRANSCRIPT_SHOULD_NEVER_APPEAR';
    const { ctx, timelineEvents } = buildSandbox({
        pendingPhaseTransitionTarget: 'routing',
        expectedAnswerType: 'NONE',
        realtimePhaseContexts: {
            name: { instructions: 'NAME_STUB', tools: [] },
            routing: { instructions: 'ROUTING_STUB', tools: [] },
            legacy_full: { instructions: 'LEGACY_STUB_' + fakePii, tools: [{ name: 'check_availability' }] },
        },
    });
    vm.runInContext(RESPONSE_DONE_PHASE_BLOCK_SRC, ctx);
    const allTimelineText = timelineEvents.join(' ');
    assert.ok(!allTimelineText.includes(fakePii));
    // NONE!==VISIT_REASON なのでforceFollowUp=trueとなり、routingへ遷移しているはず。
    assert.strictEqual(ctx.currentRealtimePhase, 'routing');
});

// =======================================================================
// HOTFIX（今回追加）: session.update missing_required_parameter root cause
// fix + §9 (legacy_fullを早期送信しない) + §3 (error.param/error.message
// 診断拡張) の契約テスト。既存のI〜Tのレター体系とは意図的に衝突させず、
// 目的が分かる名前を付ける（サマリー上の合意事項どおり）。
// =======================================================================

test('HOTFIX-CONFIRMED: REALTIME_PHASE_TRANSITION_CONFIRMED marker fires on session.updated with correct phase/instructions_chars/tool_count and no PII', () => {
    const fakePii = 'CUSTOMER_NAME_SHOULD_NEVER_APPEAR_090-0000-0000';
    const { ctx, timelineEvents, consoleLogs } = buildSandbox({
        phaseTransitionInProgress: true,
        currentRealtimePhase: 'routing',
        phaseTransitionSessionUpdateSentAt: Date.now() - 17,
        pendingPhaseTransitionForceFollowUp: false,
        pendingPhaseTransitionReasonForFollowUp: 'phase_transition_to_routing',
        // 万一実際のinstructions文字列（fakePii相当）がどこかから紛れ込んでも、
        // このマーカーはinstructions_chars（文字数）・tool_countのみを記録し、
        // 本文自体は一切含めないことを確認する。
        phaseTransitionInstructionsChars: fakePii.length,
        phaseTransitionToolCount: 2,
    });
    vm.runInContext("type = 'session.updated'; " + SESSION_UPDATED_PHASE_BLOCK_SRC, Object.assign(ctx, { type: null }));
    const confirmedLine = timelineEvents.find((e) => e.includes('REALTIME_PHASE_TRANSITION_CONFIRMED'));
    assert.ok(confirmedLine, 'REALTIME_PHASE_TRANSITION_CONFIRMED must be logged once session.updated is observed for an in-flight transition');
    assert.ok(new RegExp('phase=routing, ms_since_call_start=\\d+, instructions_chars=' + fakePii.length + ', tool_count=2').test(confirmedLine));
    assert.ok(!confirmedLine.includes(fakePii), 'the CONFIRMED marker must never include instructions/tools content itself, only counts');
    const allConsoleText = consoleLogs.map((args) => args.join(' ')).join(' ');
    assert.ok(!allConsoleText.includes(fakePii));
    // session.updated消費後は次のtransitionに備えて必ずnullへリセットされる。
    assert.strictEqual(ctx.phaseTransitionInstructionsChars, null);
    assert.strictEqual(ctx.phaseTransitionToolCount, null);
});

test('HOTFIX-FAILED-PARAM: REALTIME_PHASE_TRANSITION_FAILED includes error_param (schema field name; always safe per OpenAI SDK docstring) and never leaks error.message into pushTimelineEvent/Copy Debug Log', () => {
    const fakeMessage = 'Missing required parameter: session.type. Customer said something unrelated here just in case.';
    const { ctx, timelineEvents, consoleLogs } = buildSandbox({
        phaseTransitionInProgress: true,
        currentRealtimePhase: 'routing',
        pendingPhaseTransitionTarget: 'legacy_full',
        debugMode: false,
        msg: { error: { type: 'invalid_request_error', code: 'missing_required_parameter', param: 'session.type', message: fakeMessage } },
    });
    vm.runInContext(ERROR_PHASE_HOOK_SRC, ctx);
    const failedLine = timelineEvents.find((e) => e.includes('REALTIME_PHASE_TRANSITION_FAILED'));
    assert.ok(failedLine, 'REALTIME_PHASE_TRANSITION_FAILED must be logged');
    assert.ok(failedLine.includes('error_param=session.type'), 'error.param (a schema field name, never user content) must appear in the main diagnostic line');
    const allTimelineText = timelineEvents.join(' ');
    const allConsoleText = consoleLogs.map((args) => args.join(' ')).join(' ');
    assert.ok(!allTimelineText.includes(fakeMessage), 'error.message must never reach pushTimelineEvent (Copy Debug Log/#diagTimeline)');
    // debugMode=falseなので、error.messageはconsole.logへも一切出力されない。
    assert.ok(!allConsoleText.includes(fakeMessage), 'error.message must not reach console.log when debugMode is false');
});

test('HOTFIX-FAILED-DEBUG-ONLY: error.message reaches console.log only under the _DEBUG_ONLY marker and only when debugMode=true', () => {
    const fakeMessage = 'Missing required parameter: session.type.';
    const { ctx, consoleLogs } = buildSandbox({
        phaseTransitionInProgress: true,
        currentRealtimePhase: 'routing',
        debugMode: true,
        msg: { error: { type: 'invalid_request_error', code: 'missing_required_parameter', param: 'session.type', message: fakeMessage } },
    });
    vm.runInContext(ERROR_PHASE_HOOK_SRC, ctx);
    const debugOnlyCall = consoleLogs.find((args) => String(args[0]).includes('REALTIME_PHASE_TRANSITION_FAILED_ERROR_MESSAGE_DEBUG_ONLY'));
    assert.ok(debugOnlyCall, 'when debugMode=true, error.message must still be reachable via console.log for real-device debugging, under a clearly _DEBUG_ONLY-suffixed marker');
    assert.ok(debugOnlyCall.includes(fakeMessage));
});

test('HOTFIX-PENDING-CLEARED (§9, updated by PHASE ORDER HOTFIX): a failed phase transition clears ANY stale pendingPhaseTransitionTarget, so a subsequent response.done boundary does not act on state left over from before the failure', () => {
    // PHASE ORDER HOTFIX（今回更新・重要な設計変更）: 旧設計は
    // sendRealtimePhaseSessionUpdate('routing', ...)送信「時点」で無条件に
    // pendingPhaseTransitionTarget='legacy_full'を楽観的にarmしていたが、
    // この即時armはPHASE ORDER HOTFIXの根本原因修正により廃止した（ROUTINGへ
    // 入っただけでlegacy_fullを予約してしまい、お客様がまだ一度も発話して
    // いない段階でlegacy_fullへ脱出してしまう実機バグの直接原因だったため。
    // 詳細は同ラウンドの最終報告を参照）。そのため、この用の「routing送信
    // 時点でlegacy_fullがarmされている」という前提はもう成立しない。
    // しかし、classify_intentによる'reservation'/'callback'のarm、または
    // 新しい条件付きlegacy_full arm等、他の経路でpendingPhaseTransitionTarget
    // が非nullになっている状態で、現在進行中のtransition自体がエラーで
    // 失敗する可能性は今も残っているため、この汎用ガード（ERROR_PHASE_HOOK
    // 内のif (pendingPhaseTransitionTarget !== null) { ...クリア... }、
    // コード自体は今回無変更）の健全性は引き続き検証する。
    const { ctx, timelineEvents } = buildSandbox({ pendingPhaseTransitionTarget: 'legacy_full' });
    vm.runInContext("sendRealtimePhaseSessionUpdate('routing', 'test_reason', false)", ctx);
    assert.strictEqual(ctx.phaseTransitionInProgress, true);

    // routingのsession.updateがOpenAI側でmissing_required_parameterとして拒否される。
    Object.assign(ctx, { msg: { error: { type: 'invalid_request_error', code: 'missing_required_parameter', param: 'session.type' } } });
    vm.runInContext(ERROR_PHASE_HOOK_SRC, ctx);

    assert.strictEqual(ctx.phaseTransitionInProgress, false);
    assert.strictEqual(ctx.pendingPhaseTransitionTarget, null,
        'whatever stale target was pending at the moment of failure must be cleared, otherwise the next response.done boundary could act on it despite this transition never having been confirmed');
    const clearedLine = timelineEvents.find((e) => e.includes('REALTIME_PHASE_TRANSITION_PENDING_CLEARED'));
    assert.ok(clearedLine && clearedLine.includes('cleared_target=legacy_full'));

    // その後response.done境界が来ても、もう何も送信されない（クリア済みのため）。
    const { dcSendCalls: sendCallsAfter } = buildSandbox({});
    Object.assign(ctx, { dc: { readyState: 'open', send: (p) => { sendCallsAfter.push(p); } } });
    vm.runInContext(RESPONSE_DONE_PHASE_BLOCK_SRC, ctx);
    assert.strictEqual(sendCallsAfter.length, 0, 'legacy_full must NOT be sent after routing failed and was never confirmed via session.updated');
});

console.log(`\n${passed} test(s) passed.`);
if (process.exitCode) {
    console.error('\n=== SOME test_realtime_phase1_transition.js CHECKS FAILED ===');
} else {
    console.log('\n=== ALL test_realtime_phase1_transition.js CHECKS PASSED ===');
}
