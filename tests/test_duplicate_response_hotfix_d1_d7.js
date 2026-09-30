'use strict';

/**
 * RECEPTRA — DUPLICATE RESPONSE HOTFIX 契約テスト（D1〜D7）
 *
 * 対象: response.done境界のphase-transition消費ブロック
 * （"if (pendingPhaseTransitionTarget !== null && !phaseTransitionInProgress) {"）
 * が、server auto response（既存のcreate_response=true既定によりOpenAI側が
 * 自動生成する応答）自身が既に次フェーズの質問まで言い切っていた場合に、
 * 不要な2本目のresponse.create（forceFollowUpForTransition=trueによる
 * phase_transition_to_*）を送らないことを確認する。
 *
 * D1/D2は実際のforceFollowUpForTransition計算ロジック（新規ロジック）を
 * vm.runInContextで実行して検証する。D3〜D7はGreeting/NAME instructionsの
 * 静的契約（Pythonテスト側と対をなす、JS側から見た回帰確認）として、
 * ソースファイルのテキストのみを検証する（新しいNLU・regex等は一切
 * 追加しない静的アサーションのみ）。
 *
 * 実行: node tests/test_duplicate_response_hotfix_d1_d7.js
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ENGINE_JS_PATH = path.join(__dirname, '..', 'frontend', 'public', 'js', 'realtime-voice-engine.js');
const SRC = fs.readFileSync(ENGINE_JS_PATH, 'utf8');

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

// response.done境界のphase-transition消費ロジック（DUPLICATE RESPONSE
// HOTFIXが変更した本体そのもの）。
const RESPONSE_DONE_PHASE_BLOCK_SRC = extractBlock(
    SRC,
    'if (pendingPhaseTransitionTarget !== null && !phaseTransitionInProgress) {'
);

function buildSandbox(overrides) {
    const sessionUpdateCalls = [];
    const context = {
        pushTimelineEvent: () => {},
        console: { log: () => {} },
        sendRealtimePhaseSessionUpdate: (targetPhase, reason, forceFollowUp) => {
            sessionUpdateCalls.push({ targetPhase, reason, forceFollowUp });
        },
    };
    const state = Object.assign({
        phaseTransitionInProgress: false,
        pendingPhaseTransitionTarget: null,
        expectedAnswerType: 'NONE',
        lastResponseTranscriptWasProcessNarrationOnly: false,
        lastResponseTranscriptWasIncompleteAiTurn: false,
    }, overrides || {});
    Object.assign(context, state);
    vm.createContext(context);
    vm.runInContext(RESPONSE_DONE_PHASE_BLOCK_SRC, context);
    return { ctx: context, sessionUpdateCalls };
}

let passed = 0;
function test(name, fn) {
    try {
        fn();
        passed++;
        console.log('OK: ' + name);
    } catch (e) {
        console.error('FAIL: ' + name);
        console.error(e);
        process.exitCode = 1;
    }
}

// ---- D1: RESERVATION target — server auto responseが既にSHORT_ANSWER
//      （日時/人数等）を尋ね切っていた場合、不要なphase_transition follow-up
//      応答を送らない（forceFollowUp=false）。----
test('D1. reservation: server auto responseがSHORT_ANSWERを尋ね済みならforceFollowUp=false', () => {
    const { sessionUpdateCalls } = buildSandbox({
        pendingPhaseTransitionTarget: 'reservation',
        expectedAnswerType: 'SHORT_ANSWER',
        lastResponseTranscriptWasProcessNarrationOnly: false,
        lastResponseTranscriptWasIncompleteAiTurn: false,
    });
    assert.strictEqual(sessionUpdateCalls.length, 1);
    assert.strictEqual(sessionUpdateCalls[0].targetPhase, 'reservation');
    assert.strictEqual(sessionUpdateCalls[0].forceFollowUp, false);
});

// ---- D2: CALLBACK target — server auto responseが既にPHONE（折り返し先
//      電話番号の質問）を尋ね切っていた場合、同様にforceFollowUp=false。
//      D1・D2どちらも「1回のsendRealtimePhaseSessionUpdate呼び出ししか
//      発生しない」ことを確認することで、同一ユーザーターン内で競合する
//      2本目のAI応答が生成されないことを保証する。----
test('D2. callback: server auto responseがPHONEを尋ね済みならforceFollowUp=false（競合response無し）', () => {
    const { sessionUpdateCalls } = buildSandbox({
        pendingPhaseTransitionTarget: 'callback',
        expectedAnswerType: 'PHONE',
        lastResponseTranscriptWasProcessNarrationOnly: false,
        lastResponseTranscriptWasIncompleteAiTurn: false,
    });
    assert.strictEqual(sessionUpdateCalls.length, 1);
    assert.strictEqual(sessionUpdateCalls[0].targetPhase, 'callback');
    assert.strictEqual(sessionUpdateCalls[0].forceFollowUp, false);
});

// ---- 安全側回帰（既存挙動の非破壊確認）: narration-onlyまたは
//      incompleteの場合は、たとえtypeが一致していてもforceFollowUp=true
//      のまま（「言い切れていない」とみなす安全側の挙動を壊していない）。----
test('D1補助. reservation: narration-onlyの場合はSHORT_ANSWERでもforceFollowUp=true（安全側維持）', () => {
    const { sessionUpdateCalls } = buildSandbox({
        pendingPhaseTransitionTarget: 'reservation',
        expectedAnswerType: 'SHORT_ANSWER',
        lastResponseTranscriptWasProcessNarrationOnly: true,
        lastResponseTranscriptWasIncompleteAiTurn: false,
    });
    assert.strictEqual(sessionUpdateCalls[0].forceFollowUp, true);
});

test('D2補助. callback: incompleteの場合はPHONEでもforceFollowUp=true（安全側維持）', () => {
    const { sessionUpdateCalls } = buildSandbox({
        pendingPhaseTransitionTarget: 'callback',
        expectedAnswerType: 'PHONE',
        lastResponseTranscriptWasProcessNarrationOnly: false,
        lastResponseTranscriptWasIncompleteAiTurn: true,
    });
    assert.strictEqual(sessionUpdateCalls[0].forceFollowUp, true);
});

test('D1/D2補助. legacy_fullは常にforceFollowUp=true（無条件・最適化対象外・既存契約維持）', () => {
    const { sessionUpdateCalls } = buildSandbox({
        pendingPhaseTransitionTarget: 'legacy_full',
        expectedAnswerType: 'SHORT_ANSWER',
        lastResponseTranscriptWasProcessNarrationOnly: false,
        lastResponseTranscriptWasIncompleteAiTurn: false,
    });
    assert.strictEqual(sessionUpdateCalls[0].forceFollowUp, true);
});

test('routing既存回帰. VISIT_REASON尋ね済みならforceFollowUp=false（旧実装からの既存挙動）', () => {
    const { sessionUpdateCalls } = buildSandbox({
        pendingPhaseTransitionTarget: 'routing',
        expectedAnswerType: 'VISIT_REASON',
        lastResponseTranscriptWasProcessNarrationOnly: false,
        lastResponseTranscriptWasIncompleteAiTurn: false,
    });
    assert.strictEqual(sessionUpdateCalls[0].forceFollowUp, false);
});

// ---- D3/D4: Greeting exactly-once（店舗名は通話開始時に一度だけ）。
//      _build_greeting_section()に「既に話し終えている場合は繰り返さない」
//      旨の指示が含まれていることをテキストとして確認する（店舗名を
//      ハードコードしていないことも確認）。----
const PY_SRC_PATH = path.join(__dirname, '..', 'app', 'services', 'realtime_voice_ai.py');
const PY_SRC = fs.readFileSync(PY_SRC_PATH, 'utf8');

test('D3/D4. _build_greeting_sectionに「既に話し終えている場合は繰り返さない」指示が含まれる', () => {
    const idx = PY_SRC.indexOf('def _build_greeting_section(');
    assert.notStrictEqual(idx, -1, '_build_greeting_section not found');
    const braceIdx = PY_SRC.indexOf('return (', idx);
    const endIdx = PY_SRC.indexOf('\n\n\ndef ', braceIdx);
    const fnBody = PY_SRC.slice(idx, endIdx);
    assert.ok(fnBody.includes('話し終えている場合'), '既に話し終えている場合の言及が見当たりません');
    assert.ok(fnBody.includes('繰り返さないでください'), '繰り返さない、という明示的な禁止指示が見当たりません');
    // ハードコード禁止（特定店舗名を直接書いていないこと）
    assert.ok(!fnBody.includes('サンプル居酒屋'), 'テスト用店舗名がハードコードされています');
});

// ---- D5/D6/D7: NAME phase instructions（_PHASE1_NAME_ROLE_TEMPLATE）が
//      今回のDUPLICATE RESPONSE HOTFIXで変更されていない（ユーザー指示
//      STEP11: 既存のcontinuation機構等は証明なしに変更しないの通り、
//      今回のNAME template自体は変更対象外）ことを確認する。----
test('D5/D6/D7. _PHASE1_NAME_ROLE_TEMPLATEは今回変更されていない（名乗り+お名前質問の一体化契約を維持）', () => {
    const idx = PY_SRC.indexOf('_PHASE1_NAME_ROLE_TEMPLATE = """');
    assert.notStrictEqual(idx, -1, '_PHASE1_NAME_ROLE_TEMPLATE not found');
    const endIdx = PY_SRC.indexOf('"""', idx + '_PHASE1_NAME_ROLE_TEMPLATE = """'.length);
    const templateBody = PY_SRC.slice(idx, endIdx);
    // D6: NAME確認（confirm_customer_name）の既存契約が維持されている
    assert.ok(templateBody.includes('confirm_customer_name'), 'confirm_customer_name Tool呼び出し契約が見当たりません');
    // D7: ご用件（PURPOSE）を同一発話で尋ねる既存契約が維持されている
    assert.ok(templateBody.includes('本日はどのようなご用件でしょうか'), 'ご用件を尋ねる既存の質問文が見当たりません');
});

console.log('\n' + passed + ' tests passed.');
if (process.exitCode) {
    console.error('SOME TESTS FAILED');
} else {
    console.log('ALL D1-D7 TESTS PASSED');
}
