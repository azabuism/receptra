'use strict';

// RECEPTRA — FAST TURN EMERGENCY HOTFIX 12（2026年9月）
// 「AIが『確認します』等の処理宣言のみで発話を終えた場合、ユーザーの相槌
// （「はい」等）を待たずに自動で処理を続ける」契約テスト（フロントエンド側）。
//
// 背景（実機症状）: AIが「内容を確認しますので少々お待ちください。」と
// 発話した後、function_callを伴わずにそのターンが完了すると、既存コードは
// これを「本当の質問への回答待ち」（USER_ANSWER_WAIT）と一切区別せず、
// サーバーのturn_detectionが新しいユーザー発話を検知するまで次の処理が
// 始まらなかった（＝ユーザーが「はい」等と言わない限り会話が進まない）。
//
// 本HOTFIXは、AIの発話が既存のapp/services/realtime_voice_ai.py側で
// 既に明示的に禁止されている「内部処理を実況するだけの発話」語彙
// （整理します/確認します/確認いたします/確認してみます/調べます/
// お待ちください）に一致し、かつ既存のclassifyExpectedAnswerType()が
// 本当の質問（PHONE/NAME/YES_NO/SHORT_CHOICE/VISIT_REASON/SHORT_ANSWER）
// のいずれにも分類しなかった場合に限り、AI_WORKING（AI自身が処理中）と
// みなし、ユーザーの発話を待たず1回だけ継続のresponse.createを送る。
//
// 本テストが確認する項目:
//   1. classifyExpectedAnswerType(): 「確認します」系の発話でtype='NONE'の
//      ままAI_WORKING判定フラグが立つ
//   2. classifyExpectedAnswerType(): 本当の質問ではtype判定が優先され、
//      AI_WORKING判定フラグは立たない（既存のFIRST ANSWER MUST COUNT分類を
//      壊さない）
//   3. response.doneのsilence timeoutゲート: AI_WORKING判定時、
//      新規のresponse.create('ai_working_continuation')が1回だけ送られ、
//      silence timerは即座に開始されない
//   4. ループ防止: 継続応答自体が再びAI_WORKING判定だった場合、2回目の
//      継続は送らず、通常のsilence timerへ安全側にフォールバックする
//   5. 本当の質問（AI_WORKING判定でない）では従来どおりsilence timerを
//      即座に開始する（回帰）
//   6. Tool Call中（responseHasFunctionCall=true）ではゲート自体が
//      一切評価されない（回帰・Tool継続チェーンとの非干渉）
//   7. HOTFIX 10のrate limit fallback defer flagが優先される（HOTFIX 10/11
//      との共存・回帰）
//   8. 安全網タイムアウト: response.createdが届かなかった場合に備え、
//      一定時間後に強制的にsilence timerを開始する（idempotent）
//   9. categorizeResponseReason('ai_working_continuation') が正しく分類される
//   10. dc.send/sendResponseCreate回帰: 新規raw dc.send経路を増やしていない
//
// 実行: node tests/test_fast_turn_hotfix12_ai_working_continuation.js

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ENGINE_JS_PATH = path.join(__dirname, '..', 'frontend', 'public', 'js', 'realtime-voice-engine.js');
const SRC = fs.readFileSync(ENGINE_JS_PATH, 'utf8');

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
const CATEGORIZE_FN = extractFunctionSource(SRC, 'categorizeResponseReason', false);
const GATE_SNIPPET = extractGateSnippet(SRC);

// ===== classifyExpectedAnswerType のテスト用サンドボックス =====

function buildClassifySandbox(overrides) {
    const events = [];
    const context = {
        expectedAnswerType: 'NONE',
        lastResponseTranscriptWasProcessNarrationOnly: false,
        // AI_WORKING_NARRATION_ONLY_REはclassifyExpectedAnswerType()の外側
        // （囲むIIFEのトップレベル）で宣言される定数のため、関数本体だけを
        // 抽出したこのサンドボックスでは別途用意する必要がある。値は
        // ソース側の定義と完全に同じものをここでも直接参照する（コピーの
        // ズレを防ぐため、SRCから正規表現リテラルの文字列自体を抽出して使う）。
        AI_WORKING_NARRATION_ONLY_RE: (() => {
            const m = SRC.match(/const AI_WORKING_NARRATION_ONLY_RE = (\/.*\/);/);
            assert.ok(m, 'AI_WORKING_NARRATION_ONLY_RE constant not found in source');
            // eslint-disable-next-line no-eval
            return eval(m[1]);
        })(),
        // FAST TURN EMERGENCY HOTFIX 13（今回追加）: COMPLETE_QUESTION_ENDING_REも
        // 同じ理由（関数の外側で宣言される定数）で、同じ方法でソースから抽出して
        // 用意する必要がある（抽出しないとReferenceErrorになる）。
        COMPLETE_QUESTION_ENDING_RE: (() => {
            const m = SRC.match(/const COMPLETE_QUESTION_ENDING_RE = (\/.*\/);/);
            assert.ok(m, 'COMPLETE_QUESTION_ENDING_RE constant not found in source');
            // eslint-disable-next-line no-eval
            return eval(m[1]);
        })(),
        lastResponseTranscriptWasIncompleteAiTurn: false,
        // classify()がgreeting応答かどうかを判定するために参照する。既存の
        // HOTFIX12テスト項目はいずれも通話冒頭の第一声応答を扱わないため
        // 'unknown'のままでよい（HOTFIX13の新規テストが別途'greeting'で
        // 上書きして検証する）。
        lastResponseReasonCategoryForDiag: 'unknown',
        pushTimelineEvent: (text) => { events.push(text); },
        // classify内部で参照される既存の周辺状態（挙動には使わないが
        // ReferenceErrorを避けるために必要な最小限のみ用意する）。
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

console.log('FAST TURN EMERGENCY HOTFIX 12 — AI_WORKING self-continuation contract tests');
console.log('source: ' + ENGINE_JS_PATH);
console.log('');

// ===== 1: classifyExpectedAnswerType — AI_WORKING narration検出 =====

test('1) 「内容を確認しますので少々お待ちください。」→ type=NONEのままAI_WORKING判定フラグが立つ', () => {
    const { context } = buildClassifySandbox({});
    vm.runInContext('classifyExpectedAnswerType', context)('内容を確認しますので少々お待ちください。');
    assert.strictEqual(context.expectedAnswerType, 'NONE');
    assert.strictEqual(context.lastResponseTranscriptWasProcessNarrationOnly, true);
});

test('1) 「空き状況を確認しますね」→ AI_WORKING判定フラグが立つ', () => {
    const { context } = buildClassifySandbox({});
    vm.runInContext('classifyExpectedAnswerType', context)('空き状況を確認しますね');
    assert.strictEqual(context.lastResponseTranscriptWasProcessNarrationOnly, true);
});

test('1) 「はい、19時ですね。空きを確認します。」（PHASE O5.5推奨の一言）→ type=NONEのままAI_WORKING判定フラグが立つ（実際にfunction_callを伴うかは呼び出し側=response.doneゲートが判断）', () => {
    const { context } = buildClassifySandbox({});
    vm.runInContext('classifyExpectedAnswerType', context)('はい、19時ですね。空きを確認します。');
    assert.strictEqual(context.expectedAnswerType, 'NONE');
    assert.strictEqual(context.lastResponseTranscriptWasProcessNarrationOnly, true);
});

// ===== 2: 本当の質問では発火しない（既存分類の優先） =====

test('2) 「何名様ですか？」→ type=SHORT_ANSWER、AI_WORKING判定フラグは立たない', () => {
    const { context } = buildClassifySandbox({});
    vm.runInContext('classifyExpectedAnswerType', context)('何名様ですか？');
    assert.strictEqual(context.expectedAnswerType, 'SHORT_ANSWER');
    assert.strictEqual(context.lastResponseTranscriptWasProcessNarrationOnly, false);
});

test('2) 「お名前をお願いします。」→ type=NAME、AI_WORKING判定フラグは立たない', () => {
    const { context } = buildClassifySandbox({});
    vm.runInContext('classifyExpectedAnswerType', context)('お名前をお願いします。');
    assert.strictEqual(context.expectedAnswerType, 'NAME');
    assert.strictEqual(context.lastResponseTranscriptWasProcessNarrationOnly, false);
});

test('2) 「電話番号を確認します」（予告のみ・PHONE語を含むがNAME/PHONE質問ではない）→ typeはPHONEとして分類され、AI_WORKING判定フラグは立たない（既存優先順位どおりPHONE判定が勝つ）', () => {
    const { context } = buildClassifySandbox({});
    vm.runInContext('classifyExpectedAnswerType', context)('電話番号を確認します');
    assert.strictEqual(context.expectedAnswerType, 'PHONE');
    assert.strictEqual(context.lastResponseTranscriptWasProcessNarrationOnly, false);
});

test('2) 「19時でしたらご案内できます。お名前をお願いします。」（完了報告+実質問）→ type=NAME、AI_WORKING判定フラグは立たない', () => {
    const { context } = buildClassifySandbox({});
    vm.runInContext('classifyExpectedAnswerType', context)('19時でしたらご案内できます。お名前をお願いします。');
    assert.strictEqual(context.expectedAnswerType, 'NAME');
    assert.strictEqual(context.lastResponseTranscriptWasProcessNarrationOnly, false);
});

test('2) 単なる相槌「ありがとうございます。」→ AI_WORKING判定フラグは立たない（Tool呼び出しを含意しない一般的な相槌までは拾わない）', () => {
    const { context } = buildClassifySandbox({});
    vm.runInContext('classifyExpectedAnswerType', context)('ありがとうございます。');
    assert.strictEqual(context.lastResponseTranscriptWasProcessNarrationOnly, false);
});

// ===== 9: categorizeResponseReason =====

test('9) categorizeResponseReason("ai_working_continuation") === "ai_working_continuation"', () => {
    const context = { };
    vm.createContext(context);
    vm.runInContext(CATEGORIZE_FN, context);
    const result = vm.runInContext('categorizeResponseReason', context)('ai_working_continuation');
    assert.strictEqual(result, 'ai_working_continuation');
});

test('9) 回帰: categorizeResponseReasonの既存カテゴリは無変更', () => {
    const context = { };
    vm.createContext(context);
    vm.runInContext(CATEGORIZE_FN, context);
    const fn = vm.runInContext('categorizeResponseReason', context);
    assert.strictEqual(fn(null), 'normal_conversation');
    assert.strictEqual(fn('silence_warning'), 'silence_warning');
    assert.strictEqual(fn('silence_final_goodbye'), 'silence_goodbye');
    assert.strictEqual(fn('tool_result:check_availability'), 'tool_result');
    assert.strictEqual(fn('initial_greeting'), 'greeting');
    assert.strictEqual(fn('tool_continuation_rate_limit_retry'), 'unknown');
});

// ===== 3〜8: response.done silence-timeoutゲートの実行テスト =====

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
        lastResponseReasonCategoryForDiag: 'normal_conversation',
        deferSilenceTimerForAiWorkingContinuation: false,
        // FAST TURN EMERGENCY HOTFIX 13（今回追加）: GATE_SNIPPETが新しく参照する
        // INCOMPLETE AI TURN側の状態。このHOTFIX12専用テストではデフォルトfalseの
        // まま（HOTFIX13自身のテストファイルで別途trueにして検証する）。
        lastResponseTranscriptWasIncompleteAiTurn: false,
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

test('3) AI_WORKING判定（responseHasFunctionCall=false, narration=true, category=normal_conversation）: 継続response.createが1回だけ送られる', () => {
    const { sendResponseCreateCalls } = runGate({ lastResponseTranscriptWasProcessNarrationOnly: true, lastResponseReasonCategoryForDiag: 'normal_conversation' });
    assert.deepStrictEqual(sendResponseCreateCalls, ['ai_working_continuation']);
});

test('3) AI_WORKING判定: silence timerは即座に開始されず、defer flagが立つ', () => {
    const { context, startSilenceTimerCalls } = runGate({ lastResponseTranscriptWasProcessNarrationOnly: true, lastResponseReasonCategoryForDiag: 'normal_conversation' });
    assert.strictEqual(context.deferSilenceTimerForAiWorkingContinuation, true);
    assert.strictEqual(startSilenceTimerCalls.length, 0);
});

test('3) AI_WORKING判定: 専用の診断マーカーが記録される', () => {
    const { events, logs } = runGate({ lastResponseTranscriptWasProcessNarrationOnly: true, lastResponseReasonCategoryForDiag: 'normal_conversation' });
    assert.ok(events.some((e) => e.indexOf('AI_WORKING_CONTINUATION_SENT') === 0));
    assert.ok(logs.some((l) => l.indexOf('[AI_WORKING_CONTINUATION_SENT') === 0));
});

test('4) ループ防止: この応答自体が既にai_working_continuationの結果であり、再びAI_WORKING判定の場合、2回目の継続は送らない', () => {
    const { sendResponseCreateCalls, startSilenceTimerCalls, events, context } = runGate({
        lastResponseTranscriptWasProcessNarrationOnly: true,
        lastResponseReasonCategoryForDiag: 'ai_working_continuation',
    });
    assert.strictEqual(sendResponseCreateCalls.length, 0, 'must never send a second ai_working_continuation (infinite loop forbidden)');
    assert.strictEqual(startSilenceTimerCalls.length, 1, 'must fall back to normal silence supervision instead of looping');
    assert.strictEqual(startSilenceTimerCalls[0].reason, 'response_done_no_function_call');
    assert.strictEqual(context.deferSilenceTimerForAiWorkingContinuation, false);
    assert.ok(events.some((e) => e.indexOf('AI_WORKING_CONTINUATION_LOOP_PREVENTED') === 0));
});

test('5) 回帰) 本当の質問（AI_WORKING判定でない）では従来どおりsilence timerを即座に開始する', () => {
    const { sendResponseCreateCalls, startSilenceTimerCalls } = runGate({
        lastResponseTranscriptWasProcessNarrationOnly: false,
        lastResponseReasonCategoryForDiag: 'normal_conversation',
    });
    assert.strictEqual(sendResponseCreateCalls.length, 0);
    assert.deepStrictEqual(startSilenceTimerCalls, [{ gen: 1, reason: 'response_done_no_function_call' }]);
});

test('6) 回帰) responseHasFunctionCall=true（Tool Call中）ではゲート自体が一切評価されない', () => {
    const { sendResponseCreateCalls, startSilenceTimerCalls, context } = runGate({
        responseHasFunctionCall: true,
        lastResponseTranscriptWasProcessNarrationOnly: true,
        lastResponseReasonCategoryForDiag: 'normal_conversation',
    });
    assert.strictEqual(sendResponseCreateCalls.length, 0);
    assert.strictEqual(startSilenceTimerCalls.length, 0);
    assert.strictEqual(context.deferSilenceTimerForAiWorkingContinuation, false);
});

test('7) 回帰) HOTFIX 10のrate limit fallback deferが優先される（HOTFIX10/11との共存）', () => {
    const { sendResponseCreateCalls, startSilenceTimerCalls, events } = runGate({
        deferSilenceTimerForToolContinuationRateLimit: true,
        lastResponseTranscriptWasProcessNarrationOnly: true,
        lastResponseReasonCategoryForDiag: 'normal_conversation',
    });
    assert.strictEqual(sendResponseCreateCalls.length, 0, 'HOTFIX 12 must never send ai_working_continuation while a HOTFIX 10 rate-limit fallback is already pending');
    assert.strictEqual(startSilenceTimerCalls.length, 0);
    assert.ok(events.some((e) => e.indexOf('SILENCE_TIMER_START_DEFERRED (reason=tool_continuation_rate_limit_fallback_pending)') === 0));
});

test('8) 安全網タイムアウト: 10秒後、まだdefer中ならstartSilenceTimerIfNeededを強制的に呼ぶ', () => {
    const { context, timeouts, startSilenceTimerCalls } = runGate({ lastResponseTranscriptWasProcessNarrationOnly: true, lastResponseReasonCategoryForDiag: 'normal_conversation' });
    assert.strictEqual(timeouts.length, 1);
    assert.strictEqual(timeouts[0].ms, 10000);
    assert.strictEqual(startSilenceTimerCalls.length, 0, 'must not fire before the timeout runs');
    timeouts[0].fn(); // 安全網タイムアウトを手動発火
    assert.strictEqual(context.deferSilenceTimerForAiWorkingContinuation, false);
    assert.deepStrictEqual(startSilenceTimerCalls, [{ gen: 1, reason: 'ai_working_continuation_safety_timeout' }]);
});

test('8) 安全網タイムアウト: 既にdeferが解除済みなら何もしない（idempotent）', () => {
    const { context, timeouts, startSilenceTimerCalls } = runGate({ lastResponseTranscriptWasProcessNarrationOnly: true, lastResponseReasonCategoryForDiag: 'normal_conversation' });
    context.deferSilenceTimerForAiWorkingContinuation = false; // 継続応答が正常に完了し、既に別経路で解除された状況を模す
    timeouts[0].fn();
    assert.strictEqual(startSilenceTimerCalls.length, 0, 'the safety timeout must be a no-op once the defer was already released');
});

// ===== 10: dc.send / sendResponseCreate 回帰 =====

test('10) DC-SEND回帰: 本HOTFIXは新規raw dc.send経路を追加していない（既存のsendResponseCreate一元化ラッパーを再利用するのみ）', () => {
    const sendCount = (SRC.match(/dc\.send\(JSON\.stringify\(/g) || []).length;
    assert.strictEqual(sendCount, 10, 'HOTFIX 12 must route its new response.create through the existing sendResponseCreate() wrapper, not a new raw dc.send call site');
});

test('10) 新しいsendResponseCreate呼び出し箇所は1つだけ追加されている', () => {
    assert.ok(SRC.includes("sendResponseCreate('ai_working_continuation');"));
    const count = (SRC.match(/sendResponseCreate\('ai_working_continuation'\)/g) || []).length;
    assert.strictEqual(count, 1, 'must be sent from exactly one call site (no duplication)');
});

test('10) 回帰: 既存6箇所のsendResponseCreate呼び出しは引き続き存在する', () => {
    assert.ok(SRC.includes("sendResponseCreate('silence_warning', SILENCE_WARNING_TEXT);"));
    assert.ok(SRC.includes("sendResponseCreate('silence_final_goodbye', SILENCE_GOODBYE_TEXT);"));
    assert.ok(SRC.includes("sendResponseCreate('tool_continuation_rate_limit_retry');"));
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
process.exit(failed > 0 ? 1 : 0);
