'use strict';

/**
 * RECEPTRA — FAST TURN HOTFIX 10: NAME-FIRST FLOW 診断マーカーのテスト
 *
 * 背景: 通話冒頭でお名前→ご用件（本日はどのようなご用件でしょうか？）の
 * 順に伺うNAME-FIRST実験のため、
 *   - classifyExpectedAnswerType()のVISIT_REASON判定に「ご用件」を追加
 *     （新しいtype文字列は追加していない。既存のANSWER_WINDOW_LIMITS_MS/
 *     Forced Commit系のいずれもVISIT_REASONを個別参照しないため、この拡張で
 *     新しい強制commit経路は生まれない）
 *   - PIIを一切含まないNAME_FIRST_FLOW診断マーカー（pushNameFirstFlowEvent/
 *     maybeRecordNameFirstAnswer）を新規追加した
 * を、frontend/public/js/realtime-voice-engine.js内の実ソースを直接抽出し、
 * Node.jsのvmモジュール上で最小限のモックとともに実行して検証する
 * （tests/test_short_answer_turn.js等と同じ方式）。
 *
 * 本テストが確認する項目:
 *   A. 「本日はどのようなご用件でしょうか？」がVISIT_REASONに分類される
 *   B. 「ご来店の目的を教えてください」等、既存のVISIT_REASON検出語は
 *      引き続きVISIT_REASONに分類される（回帰確認）
 *   C. NAME/PHONE/YES_NO/SHORT_CHOICE/SHORT_ANSWERの優先順位・判定結果は
 *      一切変わっていない（回帰確認）
 *   D. 通話中最初のNAME質問でopening_questionが1回だけ記録される
 *   E. 通話中最初のVISIT_REASON質問でpurpose_questionが1回だけ記録される
 *   F. speech_stopped相当のイベントでNAME回答後にhasName=true・
 *      name_answer_receivedが記録される
 *   G. speech_stopped相当のイベントでVISIT_REASON回答後にhasPurpose=true・
 *      purpose_answer_receivedが記録される
 *   H. SHORT_ANSWER（時間質問）の回答後、hasTime=trueのみ立ち、
 *      hasDate/hasPartySizeは立たない（キーワード別の軽量推定）
 *   I. 最初のfunction_call確定時にbefore_first_tool_callが1回だけ記録される
 *   J. NAME_FIRST_FLOWイベント本文にPII（AIの発話全文=transcript本文）が
 *      含まれない（type文字列・bool・数値のみ）
 *   K. 新しい通話ごとに全state（nameFirstFlowState/nameFirstStageLogged等）が
 *      リセットされることをソースレベルで確認する（startCall()のリセット
 *      ブロックに含まれているか）
 *   L. HOTFIX 9の診断マーカー（REALTIME_USAGE_BREAKDOWN/RATE_LIMIT_TOKEN_
 *      DELTA）の文字列が引き続きソース中に存在する（今回のHOTFIXで削除・
 *      改変していないことの回帰確認）
 *
 * 実行: node tests/test_name_first_flow.js
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

const FN = {
    classifyExpectedAnswerType: extractFunctionSource(SRC, 'classifyExpectedAnswerType'),
    pushNameFirstFlowEvent: extractFunctionSource(SRC, 'pushNameFirstFlowEvent'),
    maybeRecordNameFirstAnswer: extractFunctionSource(SRC, 'maybeRecordNameFirstAnswer'),
    // Realtime Token Architecture Phase 1（今回追加）: maybeRecordNameFirstAnswer()の
    // NAME分岐がこの関数を呼ぶようになったため、未定義のReferenceErrorを防ぐために
    // 実ソースからそのまま抽出して含める（このファイル自体のテスト対象はNAME-FIRST
    // FLOW診断のままで、Phase1遷移そのものの詳細な検証はtests/test_realtime_phase1_
    // transition.jsが専任で担当する）。
    armPhaseTransitionAfterResponse: extractFunctionSource(SRC, 'armPhaseTransitionAfterResponse'),
};

function buildSandbox(overrides) {
    const events = [];
    const context = {
        performance: { now: () => Date.now() },
        pushTimelineEvent: (text) => { events.push(text); },
        console: console,
        callStartedAt: Date.now() - 1234,
    };
    const state = Object.assign({
        expectedAnswerType: 'NONE',
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
        // Realtime Token Architecture Phase 1（今回追加）: armPhaseTransitionAfterResponse()
        // が参照する最小限の状態（このファイルではarmされたかどうかの副作用を
        // 気にしない。詳細はtests/test_realtime_phase1_transition.jsが担当）。
        currentRealtimePhase: 'name',
        pendingPhaseTransitionTarget: null,
        phaseTransitionInProgress: false,
    }, overrides || {});
    Object.assign(context, state);
    vm.createContext(context);
    vm.runInContext(Object.values(FN).join('\n\n'), context);
    return { ctx: context, events };
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

// A) 新しいご用件質問がVISIT_REASONに分類される
test('A: "本日はどのようなご用件でしょうか？" classifies as VISIT_REASON', () => {
    const { ctx } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('本日はどのようなご用件でしょうか？')", ctx);
    assert.strictEqual(ctx.expectedAnswerType, 'VISIT_REASON');
});

// B) 既存のVISIT_REASON検出語は引き続きVISIT_REASON（回帰）
test('B: existing VISIT_REASON phrasing still classifies as VISIT_REASON (regression)', () => {
    const { ctx } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('ご来店の目的を教えてください')", ctx);
    assert.strictEqual(ctx.expectedAnswerType, 'VISIT_REASON');
});

// C) 他の分類の優先順位・判定結果は変わらない（回帰）
test('C: NAME/PHONE/YES_NO/SHORT_CHOICE/SHORT_ANSWER classification unaffected (regression)', () => {
    const cases = [
        ["お電話番号を教えてください", 'PHONE'],
        ["お名前を教えてください", 'NAME'],
        ["この内容でよろしいですか？", 'YES_NO'],
        ["午前ですか、午後ですか？", 'SHORT_CHOICE'],
        ["何名様でのご予約でしょうか？", 'SHORT_ANSWER'],
    ];
    for (const [transcript, expected] of cases) {
        const { ctx } = buildSandbox({});
        vm.runInContext('classifyExpectedAnswerType(' + JSON.stringify(transcript) + ')', ctx);
        assert.strictEqual(ctx.expectedAnswerType, expected, `"${transcript}" should classify as ${expected}, got ${ctx.expectedAnswerType}`);
    }
});

// D) 通話中最初のNAME質問でopening_questionが1回だけ記録される
test('D: first NAME question logs opening_question exactly once', () => {
    const { ctx, events } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('お名前を教えてください')", ctx);
    vm.runInContext("classifyExpectedAnswerType('恐れ入りますが、お名前をもう一度お願いします')", ctx);
    const openingEvents = events.filter(e => e.includes('NAME_FIRST_FLOW') && e.includes('stage=opening_question'));
    assert.strictEqual(openingEvents.length, 1, 'opening_question must be logged exactly once per call');
});

// E) 通話中最初のVISIT_REASON質問でpurpose_questionが1回だけ記録される
test('E: first VISIT_REASON question logs purpose_question exactly once', () => {
    const { ctx, events } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('本日はどのようなご用件でしょうか？')", ctx);
    vm.runInContext("classifyExpectedAnswerType('ご来店の目的を教えてください')", ctx);
    const purposeEvents = events.filter(e => e.includes('NAME_FIRST_FLOW') && e.includes('stage=purpose_question'));
    assert.strictEqual(purposeEvents.length, 1, 'purpose_question must be logged exactly once per call');
});

// F) NAME回答後にhasName=true・name_answer_receivedが記録される
test('F: answering the NAME question sets hasName=true and logs name_answer_received', () => {
    const { ctx, events } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('お名前を教えてください')", ctx);
    vm.runInContext('maybeRecordNameFirstAnswer()', ctx);
    assert.strictEqual(ctx.nameFirstFlowState.hasName, true);
    assert.ok(events.some(e => e.includes('stage=name_answer_received')));
});

// G) VISIT_REASON回答後にhasPurpose=true・purpose_answer_receivedが記録される
test('G: answering the purpose question sets hasPurpose=true and logs purpose_answer_received', () => {
    const { ctx, events } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('本日はどのようなご用件でしょうか？')", ctx);
    vm.runInContext('maybeRecordNameFirstAnswer()', ctx);
    assert.strictEqual(ctx.nameFirstFlowState.hasPurpose, true);
    assert.ok(events.some(e => e.includes('stage=purpose_answer_received')));
});

// H) SHORT_ANSWER（時間質問）の回答後、hasTimeのみ立つ
test('H: answering a time-only SHORT_ANSWER question sets hasTime only (not hasDate/hasPartySize)', () => {
    const { ctx } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('何時をご希望ですか？')", ctx);
    vm.runInContext('maybeRecordNameFirstAnswer()', ctx);
    assert.strictEqual(ctx.nameFirstFlowState.hasTime, true);
    assert.strictEqual(ctx.nameFirstFlowState.hasDate, false);
    assert.strictEqual(ctx.nameFirstFlowState.hasPartySize, false);
});

// I) 最初のfunction_call確定時にbefore_first_tool_callが1回だけ記録される
test('I: before_first_tool_call can be logged exactly once per call via pushNameFirstFlowEvent guard', () => {
    const { ctx, events } = buildSandbox({});
    vm.runInContext("pushNameFirstFlowEvent('before_first_tool_call')", ctx);
    vm.runInContext("pushNameFirstFlowEvent('before_first_tool_call')", ctx);
    const toolCallEvents = events.filter(e => e.includes('stage=before_first_tool_call'));
    assert.strictEqual(toolCallEvents.length, 1);
});

// J) NAME_FIRST_FLOWイベントにPII（transcript本文）が含まれない
test('J: NAME_FIRST_FLOW events never contain raw transcript text (PII-free)', () => {
    const { ctx, events } = buildSandbox({});
    vm.runInContext("classifyExpectedAnswerType('お名前を教えてください、田中様でよろしいですか')", ctx);
    vm.runInContext('maybeRecordNameFirstAnswer()', ctx);
    vm.runInContext("classifyExpectedAnswerType('本日はどのようなご用件でしょうか？')", ctx);
    vm.runInContext('maybeRecordNameFirstAnswer()', ctx);
    const nameFirstEvents = events.filter(e => e.includes('NAME_FIRST_FLOW'));
    assert.ok(nameFirstEvents.length > 0);
    for (const e of nameFirstEvents) {
        assert.ok(!e.includes('田中'), 'NAME_FIRST_FLOW must never leak transcript content');
        // 許可されたフィールド名以外の自由記述が紛れ込んでいないことも軽く確認する。
        assert.ok(/stage=\w+, msSinceCallStart=(\d+|null), expectedAnswerType=\w+, hasName=(true|false), hasPurpose=(true|false), hasDate=(true|false), hasTime=(true|false), hasPartySize=(true|false)/.test(e),
            'NAME_FIRST_FLOW event must match the exact PII-free field schema: ' + e);
    }
});

// K) 新しい通話ごとの状態リセットがstartCall()のリセットブロックに含まれる（ソース確認）
test('K: per-call reset block includes NAME-FIRST FLOW state (source-level check)', () => {
    const startCallIdx = SRC.indexOf('async function startCall()');
    assert.notStrictEqual(startCallIdx, -1, 'startCall() not found');
    const resetAnchorIdx = SRC.indexOf('nameFirstFlowState = {', startCallIdx);
    assert.notStrictEqual(resetAnchorIdx, -1, 'nameFirstFlowState reset not found inside/after startCall()');
    const nextFnIdx = SRC.indexOf('\n        function cleanupConnection', startCallIdx);
    assert.ok(resetAnchorIdx < nextFnIdx, 'nameFirstFlowState reset must be inside startCall(), before cleanupConnection() definition');
});

// L) HOTFIX 9の診断マーカーが引き続き存在する（削除・改変していないことの回帰確認）
test('L: HOTFIX 9 diagnostic markers (REALTIME_USAGE_BREAKDOWN / RATE_LIMIT_TOKEN_DELTA) are untouched', () => {
    assert.ok(SRC.includes('REALTIME_USAGE_BREAKDOWN'));
    assert.ok(SRC.includes('RATE_LIMIT_TOKEN_DELTA'));
});

console.log(`\n${passed} test(s) passed.`);
if (process.exitCode) {
    console.error('\n=== SOME test_name_first_flow.js CHECKS FAILED ===');
} else {
    console.log('\n=== ALL test_name_first_flow.js CHECKS PASSED ===');
}
