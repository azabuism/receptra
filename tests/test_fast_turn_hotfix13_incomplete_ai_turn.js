'use strict';

// RECEPTRA — FAST TURN HOTFIX 13（2026年9月）
// 「AIが次の処理・質問へ進む途中の発話だけをしてresponseを終了し、本来まだ
// ユーザーの回答待ちではないのにUSER_WAIT/silence timeoutに入る」問題の契約
// テスト（フロントエンド側）。
//
// 背景（実機evidence）:
//   CASE A: AI「AI電話の佐藤です。」で名乗りだけ終わり、お名前を尋ねずに
//           発話が完了する。
//   CASE B: AI「続いてお電話番号の確認ですが…」で話題を切り出しただけで
//           実際の質問（「お電話番号をお願いします」等）を言い切らない。
//   CASE D: 上記いずれの場合も、既存のsilence timeout機構
//           （SILENCE_TIMEOUT_MS=30000）がAIの発話を「本当の質問への回答
//           待ち」と区別せず起動してしまい、silence_warningが誤って発火する
//           （実機ログのinput_tokens≈6,426/6,480がその証拠）。
//
// 本HOTFIXは、HOTFIX12（AI_WORKING継続）と全く同じ設計・同じ
// sendResponseCreate()一元化ラッパーを再利用し、「話題は切り出したが実際の
// 質問を言い切らずに発話を終えた」ケース（incomplete lead-in）を
// classifyExpectedAnswerType()の末尾で追加検出し、ユーザーの相槌を待たず
// 1回だけ継続response.createを送る。
//
// 本テストが確認する項目（ユーザー指示§14のA〜Tに対応):
//   A. 「AI電話の佐藤です。」は完全な質問として扱われない
//   B. 初回greetingは名乗り+お名前質問が同一ターンで完了する契約
//      （バックエンドのNAME phase instructions文言＋フロントエンドの
//      greeting分類ロジックの両面から検証）
//   C. 「続いてお電話番号の確認ですが…」はPHONE質問として誤認識されない
//      （type自体はPHONEのままだが、incomplete判定でincompleteAiTurn=trueになる）
//   D. 「お電話番号をお願いします。」はPHONEとして認識され、complete扱い
//   E. 「何名様ですか？」は引き続きSHORT_ANSWER
//   F. 「何時をご希望ですか？」は引き続きSHORT_ANSWER
//   G. 「お名前をお願いします。」は引き続きNAME
//   H. 「確認します。少々お待ちください。」は引き続きAI_WORKING
//   I. AI_WORKING継続とincomplete-turn継続は同じresponse.doneで二重発火しない
//   J. incomplete継続はbounded（無限ループにならない）
//   K. rate limit deferが優先される
//   L. Tool継続中はincomplete継続が誤発火しない（responseHasFunctionCall=trueで
//      ゲート自体が評価されない）
//   M. phase transition時にresponse.createが二重送信されない（構造確認）
//   N. silence_warning/silence_goodbyeは無傷
//   O. 既存FIRST ANSWER MUST COUNTテストが全て通る
//   P. 既存PHONE Forced Commitテストが全て通る
//   Q. 既存NAME-first-flowテストが全て通る
//   R. dc.send/response.create呼び出し箇所数が意図せず増えていない
//   S. 診断マーカーにPIIが含まれない
//   T. HOTFIX 10/11/12の対象テストが全て通る
//
// 実行: node tests/test_fast_turn_hotfix13_incomplete_ai_turn.js

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');
const { spawnSync } = require('child_process');

const ENGINE_JS_PATH = path.join(__dirname, '..', 'frontend', 'public', 'js', 'realtime-voice-engine.js');
const SRC = fs.readFileSync(ENGINE_JS_PATH, 'utf8');
const PY_PATH = path.join(__dirname, '..', 'app', 'services', 'realtime_voice_ai.py');
const PY_SRC = fs.readFileSync(PY_PATH, 'utf8');

function extractFunctionSource(src, fnName, isAsync) {
    const startToken = (isAsync ? 'async function ' : 'function ') + fnName + '(';
    const startIdx = src.indexOf(startToken);
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

function extractGateSnippet(src) {
    const sig = "if (!responseHasFunctionCall) {\n                    if (deferSilenceTimerForToolContinuationRateLimit) {";
    const idx = src.indexOf(sig);
    assert.notStrictEqual(idx, -1, 'silence-timeout gate not found (has response.done been restructured?)');
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

const CLASSIFY_FN = extractFunctionSource(SRC, 'classifyExpectedAnswerType', false);
const GATE_SNIPPET = extractGateSnippet(SRC);

function buildClassifySandbox(overrides) {
    const events = [];
    const context = {
        expectedAnswerType: 'NONE',
        lastResponseTranscriptWasProcessNarrationOnly: false,
        lastResponseTranscriptWasIncompleteAiTurn: false,
        lastResponseReasonCategoryForDiag: 'unknown',
        AI_WORKING_NARRATION_ONLY_RE: (() => {
            const m = SRC.match(/const AI_WORKING_NARRATION_ONLY_RE = (\/.*\/);/);
            assert.ok(m, 'AI_WORKING_NARRATION_ONLY_RE constant not found in source');
            // eslint-disable-next-line no-eval
            return eval(m[1]);
        })(),
        COMPLETE_QUESTION_ENDING_RE: (() => {
            const m = SRC.match(/const COMPLETE_QUESTION_ENDING_RE = (\/.*\/);/);
            assert.ok(m, 'COMPLETE_QUESTION_ENDING_RE constant not found in source');
            // eslint-disable-next-line no-eval
            return eval(m[1]);
        })(),
        pushTimelineEvent: (text) => { events.push(text); },
        nameAnswerGeneration: 0,
        nameTurnNormalCompletionSeen: false,
        pushNameFirstFlowEvent: () => {},
        phoneAnswerGeneration: 0,
        phoneTurnNormalCompletionSeen: false,
        shortChoiceAnswerGeneration: 0,
        shortChoiceTurnNormalCompletionSeen: false,
        quickAnswerGeneration: 0,
        quickAnswerTurnNormalCompletionSeen: false,
        lastAiTranscriptHadDateKeyword: false,
        lastAiTranscriptHadTimeKeyword: false,
        lastAiTranscriptHadPartySizeKeyword: false,
    };
    Object.assign(context, overrides || {});
    vm.createContext(context);
    vm.runInContext(CLASSIFY_FN, context);
    return { context, events };
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
        console.log('    ' + (e && e.stack ? e.stack.split('\n').slice(0, 8).join('\n    ') : e));
    }
}

function runNodeTest(relPath) {
    const result = spawnSync(process.execPath, [path.join(__dirname, relPath)], { encoding: 'utf8' });
    return { status: result.status, stdout: result.stdout || '', stderr: result.stderr || '' };
}

console.log('FAST TURN HOTFIX 13 — INCOMPLETE AI TURN / USER_WAIT contract tests');
console.log('source: ' + ENGINE_JS_PATH);
console.log('');

// ===== A: 「AI電話の佐藤です。」は完全な質問として扱われない =====

test('A) 「AI電話の佐藤です。」→ type=NONE・greeting応答扱いならincompleteAiTurn=true', () => {
    const { context } = buildClassifySandbox({ lastResponseReasonCategoryForDiag: 'greeting' });
    vm.runInContext('classifyExpectedAnswerType', context)('AI電話の佐藤です。');
    assert.strictEqual(context.expectedAnswerType, 'NONE', '名乗りのみでは既存のNAME/PHONE等いずれの型にも一致しないはず');
    assert.strictEqual(context.lastResponseTranscriptWasIncompleteAiTurn, true,
        '名乗りだけで終わるgreeting応答はincomplete（不完全なAIターン）として検出されなければならない');
});

test('A2) greeting応答でなければ（通常のNONE型応答）incompleteAiTurn判定はfalseのまま（既存挙動保持）', () => {
    const { context } = buildClassifySandbox({ lastResponseReasonCategoryForDiag: 'normal_conversation' });
    vm.runInContext('classifyExpectedAnswerType', context)('AI電話の佐藤です。');
    assert.strictEqual(context.lastResponseTranscriptWasIncompleteAiTurn, false,
        'greeting応答以外でtype=NONEの発話にincomplete判定を広げると、既存の「単なる相槌」等を誤検出するリスクがあるため、対象をgreeting応答に限定する');
});

// ===== B: 初回greetingの契約（名乗り+お名前質問が同一ターン） =====

test('B) 名乗り+お名前質問が同一発話で完了していれば、type=NAMEとなりincomplete扱いされない', () => {
    const { context } = buildClassifySandbox({ lastResponseReasonCategoryForDiag: 'greeting' });
    vm.runInContext('classifyExpectedAnswerType', context)('お電話ありがとうございます。AI受付の佐藤です。お名前を教えていただけますか？');
    assert.strictEqual(context.expectedAnswerType, 'NAME');
    assert.strictEqual(context.lastResponseTranscriptWasIncompleteAiTurn, false,
        '名乗り+お名前質問が同一ターンで完了していれば、既存のNAME判定が勝ち、incomplete扱いにはならない');
});

test('B2) バックエンド契約: NAME phase instructionsが「名乗りだけで発話を終える」ことを明示的に禁止し、名乗り+お名前質問を同一発話で行うよう指示している', () => {
    assert.ok(PY_SRC.includes('_PHASE1_NAME_ROLE_TEMPLATE'), 'NAME phase role template missing');
    const startIdx = PY_SRC.indexOf('_PHASE1_NAME_ROLE_TEMPLATE = ');
    const endIdx = PY_SRC.indexOf('_PHASE2_ROUTING_ROLE_TEMPLATE');
    assert.notStrictEqual(startIdx, -1);
    assert.notStrictEqual(endIdx, -1);
    const template = PY_SRC.slice(startIdx, endIdx);
    assert.ok(template.includes('同じ1回の発話'), 'must explicitly require combining greeting + name question in the same single utterance');
    assert.ok(template.includes('名乗りだけで発話を終えて'), 'must explicitly forbid ending the turn with the self-introduction alone');
    assert.ok(!/佐藤/.test(template.replace(/例:「[^」]*」/g, '')) || template.includes('○○'),
        'the example should use a generic placeholder (○○) rather than hardcoding a fixed staff name outside of illustrative examples');
});

// ===== C: 「続いてお電話番号の確認ですが…」はPHONE質問として誤認識されない =====

test('C) 「続いてお電話番号の確認ですが…」→ type=PHONEのままだが、質問を言い切っていないためincompleteAiTurn=true', () => {
    const { context } = buildClassifySandbox({});
    vm.runInContext('classifyExpectedAnswerType', context)('続いてお電話番号の確認ですが…');
    assert.strictEqual(context.expectedAnswerType, 'PHONE', '既存優先順位どおり「電話番号」語でPHONEに分類される（type分類自体は変更しない）');
    assert.strictEqual(context.lastResponseTranscriptWasIncompleteAiTurn, true,
        '質問を言い切っていない（末尾が「ですか/でしょうか/ますか/ください/お願いします」等で終わっていない）ため、incomplete lead-inとして検出されなければならない');
});

test('C2) 「電話番号を確認します」（HOTFIX12で確認済みの予告のみ）もPHONE型のままincomplete扱いになる', () => {
    const { context } = buildClassifySandbox({});
    vm.runInContext('classifyExpectedAnswerType', context)('電話番号を確認します');
    assert.strictEqual(context.expectedAnswerType, 'PHONE');
    assert.strictEqual(context.lastResponseTranscriptWasProcessNarrationOnly, false,
        'HOTFIX12の既存アサーション（narrationフラグはtype!==NONEでは常にfalse）は一切変更しない');
    assert.strictEqual(context.lastResponseTranscriptWasIncompleteAiTurn, true,
        'narration経由ではなくincomplete_ai_turn経由で同じ「自動継続」の結果に至る');
});

// ===== D: 「お電話番号をお願いします。」はPHONEとして認識され、complete扱い =====

test('D) 「お電話番号をお願いします。」→ type=PHONE、incompleteAiTurn=false（完全な質問）', () => {
    const { context } = buildClassifySandbox({});
    vm.runInContext('classifyExpectedAnswerType', context)('お電話番号をお願いします。');
    assert.strictEqual(context.expectedAnswerType, 'PHONE');
    assert.strictEqual(context.lastResponseTranscriptWasIncompleteAiTurn, false);
});

// ===== E/F/G: 既存分類の回帰 =====

test('E) 「何名様ですか？」→ 引き続きSHORT_ANSWER、incompleteAiTurn=false', () => {
    const { context } = buildClassifySandbox({});
    vm.runInContext('classifyExpectedAnswerType', context)('何名様ですか？');
    assert.strictEqual(context.expectedAnswerType, 'SHORT_ANSWER');
    assert.strictEqual(context.lastResponseTranscriptWasIncompleteAiTurn, false);
});

test('F) 「何時をご希望ですか？」→ 引き続きSHORT_ANSWER、incompleteAiTurn=false', () => {
    const { context } = buildClassifySandbox({});
    vm.runInContext('classifyExpectedAnswerType', context)('何時をご希望ですか？');
    assert.strictEqual(context.expectedAnswerType, 'SHORT_ANSWER');
    assert.strictEqual(context.lastResponseTranscriptWasIncompleteAiTurn, false);
});

test('G) 「お名前をお願いします。」→ 引き続きNAME、incompleteAiTurn=false', () => {
    const { context } = buildClassifySandbox({});
    vm.runInContext('classifyExpectedAnswerType', context)('お名前をお願いします。');
    assert.strictEqual(context.expectedAnswerType, 'NAME');
    assert.strictEqual(context.lastResponseTranscriptWasIncompleteAiTurn, false);
});

// ===== H: AI_WORKING判定は引き続き機能する（HOTFIX12回帰） =====

test('H) 「確認します。少々お待ちください。」→ 引き続きAI_WORKING（narrationOnly=true）、incompleteAiTurnはfalse（排他）', () => {
    const { context } = buildClassifySandbox({});
    vm.runInContext('classifyExpectedAnswerType', context)('確認します。少々お待ちください。');
    assert.strictEqual(context.expectedAnswerType, 'NONE');
    assert.strictEqual(context.lastResponseTranscriptWasProcessNarrationOnly, true);
    assert.strictEqual(context.lastResponseTranscriptWasIncompleteAiTurn, false,
        'AI_WORKING（narration）判定とincomplete判定は排他でなければならない（同じresponse.doneで二重発火しないための前提）');
});

// ===== I/J/K/L: response.done silence-timeoutゲートの実行テスト =====

function buildGateContext(overrides) {
    const events = [];
    const logs = [];
    const sendResponseCreateCalls = [];
    const startSilenceTimerCalls = [];
    const timeouts = [];
    const context = {
        responseHasFunctionCall: false,
        deferSilenceTimerForToolContinuationRateLimit: false,
        lastResponseTranscriptWasProcessNarrationOnly: false,
        lastResponseTranscriptWasIncompleteAiTurn: false,
        lastResponseReasonCategoryForDiag: 'normal_conversation',
        deferSilenceTimerForAiWorkingContinuation: false,
        deferSilenceTimerForIncompleteAiTurnContinuation: false,
        callGeneration: 1,
        pushTimelineEvent: (text) => { events.push(text); },
        console: { log: (line) => { logs.push(line); } },
        sendResponseCreate: (reason) => { sendResponseCreateCalls.push(reason); return true; },
        startSilenceTimerIfNeeded: (gen, reason) => { startSilenceTimerCalls.push({ gen, reason }); },
        setTimeout: (fn, ms) => { const id = timeouts.length; timeouts.push({ fn, ms }); return id; },
    };
    Object.assign(context, overrides || {});
    vm.createContext(context);
    return { context, events, logs, sendResponseCreateCalls, startSilenceTimerCalls, timeouts };
}

function runGate(overrides) {
    const built = buildGateContext(overrides);
    vm.runInContext(GATE_SNIPPET, built.context);
    return built;
}

test('I) incomplete判定（responseHasFunctionCall=false, incompleteAiTurn=true）: 継続response.createが1回だけ送られる', () => {
    const { sendResponseCreateCalls } = runGate({ lastResponseTranscriptWasIncompleteAiTurn: true, lastResponseReasonCategoryForDiag: 'normal_conversation' });
    assert.deepStrictEqual(sendResponseCreateCalls, ['incomplete_ai_turn_continuation']);
});

test('I2) 二重発火防止: narrationとincompleteAiTurnが（あり得ないはずだが）両方trueでも、継続は1回しか送られない（narration側が優先＝if/else-ifの排他構造）', () => {
    const { sendResponseCreateCalls } = runGate({
        lastResponseTranscriptWasProcessNarrationOnly: true,
        lastResponseTranscriptWasIncompleteAiTurn: true,
        lastResponseReasonCategoryForDiag: 'normal_conversation',
    });
    assert.strictEqual(sendResponseCreateCalls.length, 1, 'must never send both ai_working_continuation and incomplete_ai_turn_continuation for the same response.done');
    assert.deepStrictEqual(sendResponseCreateCalls, ['ai_working_continuation']);
});

test('I3) silence timerは即座に開始されず、incomplete専用のdefer flagが立つ', () => {
    const { context, startSilenceTimerCalls } = runGate({ lastResponseTranscriptWasIncompleteAiTurn: true, lastResponseReasonCategoryForDiag: 'normal_conversation' });
    assert.strictEqual(context.deferSilenceTimerForIncompleteAiTurnContinuation, true);
    assert.strictEqual(startSilenceTimerCalls.length, 0);
});

test('I4) 専用の診断マーカーが記録される', () => {
    const { events, logs } = runGate({ lastResponseTranscriptWasIncompleteAiTurn: true, lastResponseReasonCategoryForDiag: 'normal_conversation' });
    assert.ok(events.some((e) => e.indexOf('INCOMPLETE_AI_TURN_CONTINUATION_SENT') === 0));
    assert.ok(logs.some((l) => l.indexOf('[INCOMPLETE_AI_TURN_CONTINUATION_SENT') === 0));
});

test('J) ループ防止: この応答自体が既にincomplete_ai_turn_continuationの結果であり、再びincomplete判定の場合、2回目の継続は送らない', () => {
    const { sendResponseCreateCalls, startSilenceTimerCalls, events, context } = runGate({
        lastResponseTranscriptWasIncompleteAiTurn: true,
        lastResponseReasonCategoryForDiag: 'incomplete_ai_turn_continuation',
    });
    assert.strictEqual(sendResponseCreateCalls.length, 0, 'must never send a second incomplete_ai_turn_continuation (infinite loop forbidden)');
    assert.strictEqual(startSilenceTimerCalls.length, 1, 'must fall back to normal silence supervision instead of looping');
    assert.strictEqual(startSilenceTimerCalls[0].reason, 'response_done_no_function_call');
    assert.strictEqual(context.deferSilenceTimerForIncompleteAiTurnContinuation, false);
    assert.ok(events.some((e) => e.indexOf('INCOMPLETE_AI_TURN_CONTINUATION_LOOP_PREVENTED') === 0));
});

test('J2) ループ防止（機構をまたぐ）: 直前がai_working_continuationの結果で、今回incomplete判定になった場合も2回目の継続は送らない（1 response chainあたり合計最大1回の束縛）', () => {
    const { sendResponseCreateCalls, startSilenceTimerCalls, context } = runGate({
        lastResponseTranscriptWasIncompleteAiTurn: true,
        lastResponseReasonCategoryForDiag: 'ai_working_continuation',
    });
    assert.strictEqual(sendResponseCreateCalls.length, 0, 'a continuation of either kind must not immediately chain into a continuation of the other kind');
    assert.strictEqual(startSilenceTimerCalls.length, 1);
    assert.strictEqual(context.deferSilenceTimerForIncompleteAiTurnContinuation, false);
});

test('J3) 安全網タイムアウト: 10秒後、まだdefer中ならstartSilenceTimerIfNeededを強制的に呼ぶ', () => {
    const { context, timeouts, startSilenceTimerCalls } = runGate({ lastResponseTranscriptWasIncompleteAiTurn: true, lastResponseReasonCategoryForDiag: 'normal_conversation' });
    assert.strictEqual(timeouts.length, 1);
    assert.strictEqual(timeouts[0].ms, 10000);
    assert.strictEqual(startSilenceTimerCalls.length, 0, 'must not fire before the timeout runs');
    timeouts[0].fn();
    assert.strictEqual(context.deferSilenceTimerForIncompleteAiTurnContinuation, false);
    assert.deepStrictEqual(startSilenceTimerCalls, [{ gen: 1, reason: 'incomplete_ai_turn_continuation_safety_timeout' }]);
});

test('J4) 安全網タイムアウト: 既にdeferが解除済みなら何もしない（idempotent）', () => {
    const { context, timeouts, startSilenceTimerCalls } = runGate({ lastResponseTranscriptWasIncompleteAiTurn: true, lastResponseReasonCategoryForDiag: 'normal_conversation' });
    context.deferSilenceTimerForIncompleteAiTurnContinuation = false;
    timeouts[0].fn();
    assert.strictEqual(startSilenceTimerCalls.length, 0, 'the safety timeout must be a no-op once the defer was already released');
});

test('K) 回帰) HOTFIX 10のrate limit fallback deferが最優先される（incomplete判定より優先）', () => {
    const { sendResponseCreateCalls, startSilenceTimerCalls, events } = runGate({
        deferSilenceTimerForToolContinuationRateLimit: true,
        lastResponseTranscriptWasIncompleteAiTurn: true,
        lastResponseReasonCategoryForDiag: 'normal_conversation',
    });
    assert.strictEqual(sendResponseCreateCalls.length, 0, 'must never send incomplete_ai_turn_continuation while a HOTFIX 10 rate-limit fallback is already pending');
    assert.strictEqual(startSilenceTimerCalls.length, 0);
    assert.ok(events.some((e) => e.indexOf('SILENCE_TIMER_START_DEFERRED (reason=tool_continuation_rate_limit_fallback_pending)') === 0));
});

test('L) 回帰) responseHasFunctionCall=true（Tool Call中）ではゲート自体が一切評価されない（incomplete継続も誤発火しない）', () => {
    const { sendResponseCreateCalls, startSilenceTimerCalls, context } = runGate({
        responseHasFunctionCall: true,
        lastResponseTranscriptWasIncompleteAiTurn: true,
        lastResponseReasonCategoryForDiag: 'normal_conversation',
    });
    assert.strictEqual(sendResponseCreateCalls.length, 0);
    assert.strictEqual(startSilenceTimerCalls.length, 0);
    assert.strictEqual(context.deferSilenceTimerForIncompleteAiTurnContinuation, false);
});

test('L2) 回帰) 本当の質問（narrationでもincompleteでもない）では従来どおりsilence timerを即座に開始する', () => {
    const { sendResponseCreateCalls, startSilenceTimerCalls } = runGate({
        lastResponseTranscriptWasProcessNarrationOnly: false,
        lastResponseTranscriptWasIncompleteAiTurn: false,
        lastResponseReasonCategoryForDiag: 'normal_conversation',
    });
    assert.strictEqual(sendResponseCreateCalls.length, 0);
    assert.deepStrictEqual(startSilenceTimerCalls, [{ gen: 1, reason: 'response_done_no_function_call' }]);
});

// ===== M: phase transition時にresponse.createが二重送信されない（構造確認） =====

test('M) 構造確認: phase transitionブロックはsilence-timeoutゲートより前（response.doneハンドラ内の別ブロック）にあり、ゲート自体を書き換えていない', () => {
    const respDoneIdx = SRC.indexOf("} else if (type === 'response.done') {");
    const phaseTransitionIdx = SRC.indexOf('pendingPhaseTransitionTarget !== null && !phaseTransitionInProgress', respDoneIdx);
    const gateIdx = SRC.indexOf('if (!responseHasFunctionCall) {', respDoneIdx);
    assert.notStrictEqual(phaseTransitionIdx, -1, 'phase transition block not found in response.done handler');
    assert.notStrictEqual(gateIdx, -1);
    assert.ok(phaseTransitionIdx < gateIdx, 'phase transition must remain structurally independent of (and precede) the silence-timeout gate this HOTFIX extends');
});

// ===== N: silence_warning/silence_goodbyeは無傷 =====

test('N) 回帰: silence_warning/silence_final_goodbyeの送信箇所・テキスト定数は変更されていない', () => {
    assert.ok(SRC.includes("sendResponseCreate('silence_warning', SILENCE_WARNING_TEXT);"));
    assert.ok(SRC.includes("sendResponseCreate('silence_final_goodbye', SILENCE_GOODBYE_TEXT);"));
    assert.ok(SRC.includes("const SILENCE_TIMEOUT_MS = 30000"));
    assert.ok(SRC.includes("const SILENCE_WARNING_GRACE_MS = 8000"));
});

// ===== O/P/Q/T: 既存テストスイートの回帰（サブプロセス実行） =====

test('O) 回帰スイート: test_first_answer_must_count.js が全て通る', () => {
    const r = runNodeTest('test_first_answer_must_count.js');
    assert.strictEqual(r.status, 0, 'FIRST ANSWER MUST COUNT regression suite must pass:\n' + r.stdout + r.stderr);
});

test('P) 回帰スイート: test_phone_forced_commit.js が全て通る', () => {
    const r = runNodeTest('test_phone_forced_commit.js');
    assert.strictEqual(r.status, 0, 'PHONE Forced Commit regression suite must pass:\n' + r.stdout + r.stderr);
});

test('Q) 回帰スイート: test_name_first_flow.js が全て通る', () => {
    const r = runNodeTest('test_name_first_flow.js');
    assert.strictEqual(r.status, 0, 'NAME-FIRST FLOW regression suite must pass:\n' + r.stdout + r.stderr);
});

test('T) 回帰スイート: HOTFIX 10/11/12の対象テストが全て通る', () => {
    for (const relPath of [
        'test_fast_turn_hotfix10_rate_limit_fallback.js',
        'test_fast_turn_hotfix11_normal_conversation_rate_limit_fallback.js',
        'test_fast_turn_hotfix12_ai_working_continuation.js',
    ]) {
        const r = runNodeTest(relPath);
        assert.strictEqual(r.status, 0, relPath + ' must pass:\n' + r.stdout + r.stderr);
    }
});

// ===== R: dc.send/response.create呼び出し箇所数の回帰 =====

test('R) DC-SEND回帰: 本HOTFIXは新規raw dc.send経路を追加していない（既存のsendResponseCreate一元化ラッパーを再利用するのみ）', () => {
    const sendCount = (SRC.match(/dc\.send\(JSON\.stringify\(/g) || []).length;
    assert.strictEqual(sendCount, 10, 'HOTFIX 13 must route its new response.create through the existing sendResponseCreate() wrapper, not a new raw dc.send call site');
});

test('R2) 新しいsendResponseCreate呼び出し箇所は1つだけ追加されている', () => {
    assert.ok(SRC.includes("sendResponseCreate('incomplete_ai_turn_continuation');"));
    const count = (SRC.match(/sendResponseCreate\('incomplete_ai_turn_continuation'\)/g) || []).length;
    assert.strictEqual(count, 1, 'must be sent from exactly one call site (no duplication)');
});

test('R3) 回帰: 既存7箇所のsendResponseCreate呼び出しは引き続き存在する', () => {
    assert.ok(SRC.includes("sendResponseCreate('silence_warning', SILENCE_WARNING_TEXT);"));
    assert.ok(SRC.includes("sendResponseCreate('silence_final_goodbye', SILENCE_GOODBYE_TEXT);"));
    assert.ok(SRC.includes("sendResponseCreate('tool_continuation_rate_limit_retry');"));
    assert.ok(SRC.includes("sendResponseCreate('ai_working_continuation');"));
});

// ===== S: 診断マーカーにPIIが含まれない =====

test('S) PRIVACY: AI_TURN_CLASSIFIEDマーカーはtranscript本文を一切含まず、type/narrationOnly/incompleteAiTurnの3値のみを記録する', () => {
    const { context, events } = buildClassifySandbox({ lastResponseReasonCategoryForDiag: 'greeting' });
    const secretTranscript = 'AI電話の佐藤です。（客の個人情報は含まない例文だが念のためtranscript本文が漏れていないことを確認する）';
    vm.runInContext('classifyExpectedAnswerType', context)(secretTranscript);
    const diagEvents = events.filter((e) => e.startsWith('AI_TURN_CLASSIFIED'));
    assert.ok(diagEvents.length >= 1, 'AI_TURN_CLASSIFIED must be recorded');
    for (const e of diagEvents) {
        assert.ok(!e.includes(secretTranscript), 'AI_TURN_CLASSIFIED must never embed the raw AI transcript');
        assert.ok(/^AI_TURN_CLASSIFIED \(type=[A-Z_]+, narrationOnly=(true|false), incompleteAiTurn=(true|false)\)$/.test(e),
            'AI_TURN_CLASSIFIED must only ever carry type=/narrationOnly=/incompleteAiTurn= bare values');
    }
});

test('S2) PRIVACY: INCOMPLETE_AI_TURN_CONTINUATION_SENTマーカーはカテゴリ名のみを記録し、transcriptを含まない', () => {
    const { events } = runGate({ lastResponseTranscriptWasIncompleteAiTurn: true, lastResponseReasonCategoryForDiag: 'normal_conversation' });
    const line = events.find((e) => e.indexOf('INCOMPLETE_AI_TURN_CONTINUATION_SENT') === 0);
    assert.ok(line);
    assert.ok(/^INCOMPLETE_AI_TURN_CONTINUATION_SENT \(previousCategory=[a-z_]+\)$/.test(line));
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
process.exit(failed > 0 ? 1 : 0);
