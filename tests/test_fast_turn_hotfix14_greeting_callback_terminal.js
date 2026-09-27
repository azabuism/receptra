'use strict';

// RECEPTRA — FAST TURN HOTFIX 14（2026年9月）
// 「GREETING COMPLETION + CALLBACK TERMINAL TURN」の契約テスト（フロントエンド
// +バックエンド）。
//
// 背景（実機evidence。ユーザー指示より）:
//   CASE A: 初回の第一声がZero-Wait Greeting（事前録音の自己紹介のみ）で
//           再生され、名乗りだけで終わりお客様のお名前を伺わないまま
//           USER_WAIT（silence timer）に入ってしまう。
//   CASE B: request_callback成功後もAIが話し続け、通話が終わらない。
//
// 本HOTFIXの設計（詳細はrealtime-voice-engine.js内のHOTFIX14コメント参照）:
//   CASE A: Zero-Wait Greeting再生完了（onEnded）を新しいzeroWaitEndedPromise
//           で待ち合わせ、再生完了後にstale/話し中でなければ
//           'initial_greeting_zero_wait_followup'という理由でfollow-up
//           response.createを送る。この理由文字列は既存の
//           categorizeResponseReason()のindexOf('initial_greeting')===0判定に
//           自動的に合致し、新しい分類コードを追加せずHOTFIX13の
//           incomplete_ai_turn_continuation安全網をそのまま利用できる。
//           NAME phase instructions側には「既に名乗り終えている場合は
//           繰り返さずお名前の質問だけを続ける」という、店舗名・スタッフ名を
//           一切ハードコードしない一般化された追記のみを行う。
//   CASE B: request_callbackのTool成功時のみcallbackTerminalArmedを立て、
//           次のresponse.doneで最優先分岐としてAI_WORKING/incomplete_ai_turn/
//           通常のsilence timerを一切発火させずpendingCallbackTerminalHangupを
//           立てる。既存のmaybeHangUpAfterSilenceGoodbye()と全く同じ「音声
//           再生完了を検知してから切る」パターンをmaybeHangUpAfterCallbackTerminal()
//           として再利用し、音声途中の切断を防ぐ。失敗時はisToolOutputFailure()
//           によりterminalへ入らず、既存のfallback/handoff挙動を変更しない。
//
// 実行: node tests/test_fast_turn_hotfix14_greeting_callback_terminal.js

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

// response.doneハンドラ内の silence-timeout ゲート全体を抽出する。
// HOTFIX14でcallbackTerminalArmedの分岐が先頭に追加されたため、シグネチャを
// それに合わせて更新している（HOTFIX13時点のextractGateSnippetとは開始文字列
// が異なる）。
function extractGateSnippet(src) {
    const sig = 'if (!responseHasFunctionCall) {\n                    if (callbackTerminalArmed) {';
    const idx = src.indexOf(sig);
    assert.notStrictEqual(idx, -1, 'silence-timeout gate (with callbackTerminalArmed branch) not found — has response.done been restructured?');
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

// handleFunctionCallItem内の `else if (item.name === 'request_callback') { ... }`
// ブロックの「中身」だけ（ヘッダ行と閉じ括弧を除く）を抜き出す。
function extractElseIfBody(src, condLiteral) {
    const header = "else if (" + condLiteral + ") {";
    const idx = src.indexOf(header);
    assert.notStrictEqual(idx, -1, 'else-if block not found: ' + condLiteral);
    const braceStart = idx + header.length - 1; // points at the '{'
    let depth = 0, i = braceStart, bodyStart = -1;
    for (; i < src.length; i++) {
        if (src[i] === '{') {
            depth++;
            if (bodyStart === -1) bodyStart = i + 1;
        } else if (src[i] === '}') {
            depth--;
            if (depth === 0) break;
        }
    }
    return src.slice(bodyStart, i);
}

const GATE_SNIPPET = extractGateSnippet(SRC);
const CALLBACK_BRANCH_BODY = extractElseIfBody(SRC, "item.name === 'request_callback'");
const MAYBE_HANGUP_CALLBACK_TERMINAL_FN = extractFunctionSource(SRC, 'maybeHangUpAfterCallbackTerminal', false);
const MAYBE_SEND_INITIAL_GREETING_FN = extractFunctionSource(SRC, 'maybeSendInitialGreeting', true);
const IS_TOOL_OUTPUT_FAILURE_FN = extractFunctionSource(SRC, 'isToolOutputFailure', false);
const CATEGORIZE_REASON_FN = extractFunctionSource(SRC, 'categorizeResponseReason', false);

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
async function testAsync(name, fn) {
    try {
        await fn();
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

console.log('FAST TURN HOTFIX 14 — GREETING COMPLETION + CALLBACK TERMINAL TURN contract tests');
console.log('source: ' + ENGINE_JS_PATH);
console.log('');

// ============================================================
// CASE A: initial greeting completion (Zero-Wait follow-up)
// ============================================================

function buildGreetingSandbox(overrides) {
    const events = [];
    const logs = [];
    const consoleLogs = [];
    const sendResponseCreateCalls = [];
    const startSilenceTimerCalls = [];
    const dcSendCalls = [];
    const context = {
        initialGreetingSent: false,
        userSpokeBeforeGreeting: false,
        greetingSource: null,
        dc: { readyState: 'open', send: (s) => { dcSendCalls.push(s); } },
        callGeneration: 1,
        zeroWaitEnabled: true,
        zeroWaitState: 'ended',
        zeroWaitGreetingText: 'AI受付の○○です。',
        zeroWaitEndedPromise: Promise.resolve(),
        userVadState: 'idle',
        greetingTiming: {},
        waitForZeroWaitOutcome: async () => overrides && overrides.__zeroWaitOutcome !== undefined ? overrides.__zeroWaitOutcome : 'success',
        isStaleCallEvent: (gen) => !!(overrides && overrides.__stale),
        pushTimelineEvent: (text) => { events.push(text); },
        logEvent: (text) => { logs.push(text); },
        updateAudioDiagnosticsPanel: () => {},
        sendResponseCreate: (reason) => {
            sendResponseCreateCalls.push(reason);
            return overrides && overrides.__sendResponseCreateReturns === false ? false : true;
        },
        startSilenceTimerIfNeeded: (gen, reason) => { startSilenceTimerCalls.push({ gen, reason }); },
        logGreetingLatenciesIfReady: () => {},
        console: { log: (line) => { consoleLogs.push(line); } },
        JSON,
        performance: { now: () => Date.now() },
    };
    Object.assign(context, overrides || {});
    vm.createContext(context);
    vm.runInContext(MAYBE_SEND_INITIAL_GREETING_FN, context);
    return { context, events, logs, consoleLogs, sendResponseCreateCalls, startSilenceTimerCalls, dcSendCalls };
}

(async () => {

await testAsync('A) Zero-Wait成功（自己紹介のみ再生完了）→ stale/話し中でなければfollow-up response.createを1回だけ送る', async () => {
    const built = buildGreetingSandbox({});
    await vm.runInContext('maybeSendInitialGreeting', built.context)();
    assert.deepStrictEqual(built.sendResponseCreateCalls, ['initial_greeting_zero_wait_followup']);
    assert.ok(built.events.some((e) => e.indexOf('INITIAL_GREETING_RECOVERY_SENT') === 0));
    assert.ok(built.consoleLogs.some((l) => l === '[INITIAL_GREETING_RECOVERY_SENT]'));
});

await testAsync('A2) follow-upの理由文字列は既存categorizeResponseReason()により自動的にgreetingカテゴリへ分類される（新規分類コード不要・一般化されている）', () => {
    const ctx = { console };
    vm.createContext(ctx);
    vm.runInContext(CATEGORIZE_REASON_FN, ctx);
    const category = vm.runInContext('categorizeResponseReason', ctx)('initial_greeting_zero_wait_followup');
    assert.strictEqual(category, 'greeting');
});

await testAsync('B) 名乗りだけで発話終了しても、店舗名/AIスタッフ名に依存しない一般ロジックで検出される（transcript内容に一切依存しないfollow-up送信経路）', async () => {
    // Zero-Wait経路自体がtranscriptの文字列一致(endsWith等)を一切見ておらず、
    // 「Zero-Wait Greetingが再生完了した」という事実のみをトリガーにしている
    // ことを、店舗名・AIスタッフ名を変えたケースで確認する（値そのものは
    // ロジックの分岐に一切使われないため、異なる値でも同じ結果になるはず）。
    const built1 = buildGreetingSandbox({ zeroWaitGreetingText: '天ぷらデモ、AI受付の佐藤です。' });
    await vm.runInContext('maybeSendInitialGreeting', built1.context)();
    const built2 = buildGreetingSandbox({ zeroWaitGreetingText: 'すし処あかり、受付担当の鈴木です。' });
    await vm.runInContext('maybeSendInitialGreeting', built2.context)();
    assert.deepStrictEqual(built1.sendResponseCreateCalls, ['initial_greeting_zero_wait_followup']);
    assert.deepStrictEqual(built2.sendResponseCreateCalls, ['initial_greeting_zero_wait_followup']);
});

await testAsync('C) stale call（既に次の通話が始まっている）ならfollow-upを送らない', async () => {
    const built = buildGreetingSandbox({ __stale: true });
    await vm.runInContext('maybeSendInitialGreeting', built.context)();
    assert.deepStrictEqual(built.sendResponseCreateCalls, []);
    assert.ok(built.events.some((e) => e.indexOf('ZERO_WAIT_NAME_FOLLOWUP_SKIPPED (reason=stale_call)') === 0));
});

await testAsync('D) 再生完了時点で既にお客様が話し始めている（userVadState=speech）ならfollow-upを送らない（二重応答防止）', async () => {
    const built = buildGreetingSandbox({ userVadState: 'speech' });
    await vm.runInContext('maybeSendInitialGreeting', built.context)();
    assert.deepStrictEqual(built.sendResponseCreateCalls, []);
    assert.ok(built.events.some((e) => e.indexOf('ZERO_WAIT_NAME_FOLLOWUP_SKIPPED (reason=user_already_speaking)') === 0));
});

await testAsync('E) follow-up成功時にstartSilenceTimerIfNeededは直接呼ばれない（silence timerは後続のresponse.done側に一本化）', async () => {
    const built = buildGreetingSandbox({});
    await vm.runInContext('maybeSendInitialGreeting', built.context)();
    assert.strictEqual(built.startSilenceTimerCalls.length, 0);
});

await testAsync('F) follow-up送信自体が失敗（dc未接続等でsendResponseCreateがfalse）した場合のみ、安全網としてsilence timerを開始する', async () => {
    const built = buildGreetingSandbox({ __sendResponseCreateReturns: false });
    await vm.runInContext('maybeSendInitialGreeting', built.context)();
    assert.ok(built.events.some((e) => e.indexOf('ZERO_WAIT_NAME_FOLLOWUP_SEND_FAILED') === 0));
    assert.strictEqual(built.startSilenceTimerCalls.length, 1);
    assert.strictEqual(built.startSilenceTimerCalls[0].reason, 'zero_wait_greeting_ended_followup_send_failed');
});

await testAsync('G) 回帰: Zero-Wait対象外/失敗/タイムアウトの経路は変更されていない（従来通りinitial_greeting(_fallback)を送る）', async () => {
    const builtFailure = buildGreetingSandbox({ __zeroWaitOutcome: 'failure' });
    await vm.runInContext('maybeSendInitialGreeting', builtFailure.context)();
    assert.deepStrictEqual(builtFailure.sendResponseCreateCalls, ['initial_greeting_fallback']);

    const builtNotApplicable = buildGreetingSandbox({ __zeroWaitOutcome: 'not_applicable', zeroWaitEnabled: false });
    await vm.runInContext('maybeSendInitialGreeting', builtNotApplicable.context)();
    assert.deepStrictEqual(builtNotApplicable.sendResponseCreateCalls, ['initial_greeting']);
});

test('H) 構造確認: Zero-Wait再生終了ハンドラ(onEnded)はもはや直接silence timerを開始しない（旧・実機バグの直接原因の除去）', () => {
    const startZeroWaitFn = extractFunctionSource(SRC, 'startZeroWaitGreeting', false);
    const onEndedStart = startZeroWaitFn.indexOf('const onEnded = () => {');
    assert.notStrictEqual(onEndedStart, -1);
    const onEndedEnd = startZeroWaitFn.indexOf('zeroWaitAudioEl.addEventListener(\'playing\'', onEndedStart);
    const onEndedSrc = startZeroWaitFn.slice(onEndedStart, onEndedEnd);
    assert.ok(!onEndedSrc.includes("startSilenceTimerIfNeeded(myGeneration, 'zero_wait_greeting_ended')"),
        'onEnded must no longer directly arm the silence timer on greeting-only playback completion (that was CASE A\'s root cause)');
    assert.ok(onEndedSrc.includes('if (zeroWaitEndedResolve) {'), 'onEnded must resolve the new zeroWaitEndedPromise unconditionally (outside the stale-UI-update guard)');
});

test('I) 構造確認: resetZeroWaitCallState()は通話ごとにzeroWaitEndedPromiseを作り直す（zeroWaitOutcomePromiseと同じ規律）', () => {
    const resetFn = extractFunctionSource(SRC, 'resetZeroWaitCallState', false);
    assert.ok(resetFn.includes('zeroWaitEndedPromise = new Promise((resolve) => { zeroWaitEndedResolve = resolve; });'));
});

test('J) バックエンド契約: NAME phase instructionsは「既に名乗りを話し終えている場合は繰り返さずお名前の質問だけ続ける」ことを一般化された形（店舗名/AIスタッフ名をハードコードしない）で指示している', () => {
    const startIdx = PY_SRC.indexOf('_PHASE1_NAME_ROLE_TEMPLATE = ');
    const endIdx = PY_SRC.indexOf('_PHASE2_ROUTING_ROLE_TEMPLATE');
    assert.notStrictEqual(startIdx, -1);
    assert.notStrictEqual(endIdx, -1);
    const template = PY_SRC.slice(startIdx, endIdx);
    assert.ok(template.includes('すでに名乗り'), 'must add guidance for the already-spoken-greeting case');
    assert.ok(template.includes('繰り返さず'), 'must instruct not to repeat the already-spoken self-introduction');
    // 禁止された実装方法（exact-phrase-matchによる固定文言比較）がフロント/バック
    // いずれにも存在しないことを確認する。
    assert.ok(!/佐藤です。?"\)/.test(SRC), 'frontend must not hardcode a fixed staff name for exact-phrase matching');
    assert.ok(!SRC.includes('.endsWith(\'佐藤です'), 'frontend must not use endsWith() exact-phrase matching against a hardcoded staff name (explicitly forbidden by spec)');
});

test('K) 回帰: NAME phase instructionsは引き続き「名乗り+お名前質問を同一発話で完結させる」ことを要求している（HOTFIX13の既存契約を壊していない）', () => {
    const startIdx = PY_SRC.indexOf('_PHASE1_NAME_ROLE_TEMPLATE = ');
    const endIdx = PY_SRC.indexOf('_PHASE2_ROUTING_ROLE_TEMPLATE');
    const template = PY_SRC.slice(startIdx, endIdx);
    assert.ok(template.includes('同じ1回の発話'));
    assert.ok(template.includes('名乗りだけで発話を終えて'));
});

test('L) PRIVACY: INITIAL_GREETING_RECOVERY_SENTマーカーはtranscript本文・店舗名・AIスタッフ名を一切含まない固定文言である', () => {
    const idx = SRC.indexOf("pushTimelineEvent('INITIAL_GREETING_RECOVERY_SENT");
    assert.notStrictEqual(idx, -1);
    const line = SRC.slice(idx, SRC.indexOf(')', idx) + 2);
    assert.ok(line.includes('INITIAL_GREETING_RECOVERY_SENT (reason=zero_wait_greeting_ended)'));
});

test('M) bounded: follow-up経路は1呼び出しにつきsendResponseCreateを最大1回しか呼ばない（ループ構文を含まない）', () => {
    // maybeSendInitialGreeting全体の中で、zero_wait_greeting_ended系follow-up
    // 送信経路にwhile/forのループ構文が使われていないことを確認する
    // （無限リトライ禁止の構造的裏付け）。
    assert.ok(!/while\s*\(/.test(MAYBE_SEND_INITIAL_GREETING_FN));
    const followUpCount = (MAYBE_SEND_INITIAL_GREETING_FN.match(/sendResponseCreate\('initial_greeting_zero_wait_followup'\)/g) || []).length;
    assert.strictEqual(followUpCount, 1, 'must be exactly one call site inside maybeSendInitialGreeting');
});

// ============================================================
// CASE B: request_callback success → terminal state
// ============================================================

function buildCallbackBranchSandbox(overrides) {
    const events = [];
    const logs = [];
    const consoleLogs = [];
    const toolCalls = [];
    const context = {
        callbackAlreadyConfirmedThisCall: false,
        callbackTerminalArmed: false,
        args: {},
        callId: 'call_abc123',
        logEvent: (text) => { logs.push(text); },
        pushTimelineEvent: (text) => { events.push(text); },
        console: { log: (line) => { consoleLogs.push(line); } },
        JSON,
        callRequestCallbackTool: async (args, callId) => {
            toolCalls.push({ args, callId });
            return (overrides && overrides.__toolOutput) || { success: true, status: 'ok' };
        },
        isToolOutputFailure: (() => {
            const ctx2 = {};
            vm.createContext(ctx2);
            vm.runInContext(IS_TOOL_OUTPUT_FAILURE_FN, ctx2);
            return vm.runInContext('isToolOutputFailure', ctx2);
        })(),
    };
    Object.assign(context, overrides || {});
    vm.createContext(context);
    return { context, events, logs, consoleLogs, toolCalls };
}

async function runCallbackBranch(overrides) {
    const built = buildCallbackBranchSandbox(overrides);
    const wrapped = '(async function(){ let output;\n' + CALLBACK_BRANCH_BODY + '\n return output; })()';
    const output = await vm.runInContext(wrapped, built.context);
    return { ...built, output };
}

await testAsync('N) request_callback成功時: callbackTerminalArmed / callbackAlreadyConfirmedThisCallが立ち、CALLBACK_TERMINAL_ARMEDマーカーが記録される', async () => {
    const r = await runCallbackBranch({ __toolOutput: { success: true, status: 'created' } });
    assert.strictEqual(r.context.callbackTerminalArmed, true);
    assert.strictEqual(r.context.callbackAlreadyConfirmedThisCall, true);
    assert.ok(r.events.some((e) => e === 'CALLBACK_TERMINAL_ARMED'));
    assert.ok(r.consoleLogs.some((l) => l === '[CALLBACK_TERMINAL_ARMED]'));
    assert.deepStrictEqual(r.output, { success: true, status: 'created' });
});

await testAsync('O) request_callback失敗時: terminal状態に入らず、既存のToolエラー出力がそのまま返る（成功したかのように扱わない）', async () => {
    const r = await runCallbackBranch({ __toolOutput: { success: false, status: 'error', reason_code: 'temporarily_unavailable' } });
    assert.strictEqual(r.context.callbackTerminalArmed, false, 'callback failure must never arm terminal state');
    assert.strictEqual(r.context.callbackAlreadyConfirmedThisCall, false);
    assert.deepStrictEqual(r.output, { success: false, status: 'error', reason_code: 'temporarily_unavailable' });
    assert.ok(!r.events.some((e) => e === 'CALLBACK_TERMINAL_ARMED'));
});

await testAsync('P) 二重実行防止: この通話で既に成功済みなら、実際のTool呼び出しを再度行わず確認済みresultを返す（重複折り返し作成防止）', async () => {
    const r = await runCallbackBranch({ callbackAlreadyConfirmedThisCall: true });
    assert.strictEqual(r.toolCalls.length, 0, 'must not invoke callRequestCallbackTool again once already confirmed this call');
    // 注: r.outputはvmサンドボックス内(別realm)で生成されたオブジェクト
    // リテラルのため、deepStrictEqualはObject.prototypeの実体が異なり失敗する
    // （構造は同一）。個々のプロパティ値で比較する。
    assert.strictEqual(r.output.success, true);
    assert.strictEqual(r.output.status, 'already_confirmed');
    assert.ok(r.events.some((e) => e === 'CALLBACK_REQUEST_DEDUPED (reason=already_confirmed)'));
});

function buildGateContext(overrides) {
    const events = [];
    const logs = [];
    const sendResponseCreateCalls = [];
    const startSilenceTimerCalls = [];
    const timeouts = [];
    const endCallCalls = [];
    const context = {
        responseHasFunctionCall: false,
        callbackTerminalArmed: false,
        pendingCallbackTerminalHangup: false,
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
        endCall: (text, reason) => { endCallCalls.push({ text, reason }); },
        isStaleCallEvent: () => !!(overrides && overrides.__stale),
    };
    Object.assign(context, overrides || {});
    vm.createContext(context);
    return { context, events, logs, sendResponseCreateCalls, startSilenceTimerCalls, timeouts, endCallCalls };
}

function runGate(overrides) {
    const built = buildGateContext(overrides);
    vm.runInContext(GATE_SNIPPET, built.context);
    return built;
}

test('Q) callbackTerminalArmed=true: 最優先で消費され、AI_WORKING/incomplete/通常silence timerのいずれも発火しない', () => {
    const r = runGate({
        callbackTerminalArmed: true,
        lastResponseTranscriptWasProcessNarrationOnly: true, // これがtrueでも上書きされるはず
    });
    assert.strictEqual(r.sendResponseCreateCalls.length, 0, 'must never continue the conversation once callback terminal is armed');
    assert.strictEqual(r.startSilenceTimerCalls.length, 0, 'must not start the normal silence timer either');
    assert.strictEqual(r.context.callbackTerminalArmed, false, 'must be consumed (one-shot)');
    assert.strictEqual(r.context.pendingCallbackTerminalHangup, true);
    assert.ok(r.events.some((e) => e === 'CALLBACK_TERMINAL_RESPONSE_DONE'));
});

test('Q2) callbackTerminalArmed=trueはincomplete_ai_turn判定より優先される', () => {
    const r = runGate({ callbackTerminalArmed: true, lastResponseTranscriptWasIncompleteAiTurn: true });
    assert.strictEqual(r.sendResponseCreateCalls.length, 0);
    assert.strictEqual(r.context.pendingCallbackTerminalHangup, true);
});

test('R) callbackTerminalArmedはHOTFIX10のrate-limit deferよりも先に評価される（最優先分岐として実装されている）', () => {
    const r = runGate({ callbackTerminalArmed: true, deferSilenceTimerForToolContinuationRateLimit: true });
    assert.ok(r.events.some((e) => e === 'CALLBACK_TERMINAL_RESPONSE_DONE'));
    assert.ok(!r.events.some((e) => e.indexOf('SILENCE_TIMER_START_DEFERRED') === 0));
});

test('S) 構造確認: callbackTerminalArmedの分岐は既存の `if (!responseHasFunctionCall)` ガード内にあり、Tool呼び出し中（responseHasFunctionCall=true）はこのターン自体で評価されない', () => {
    const r = runGate({ responseHasFunctionCall: true, callbackTerminalArmed: true });
    assert.strictEqual(r.sendResponseCreateCalls.length, 0);
    assert.strictEqual(r.context.callbackTerminalArmed, true, 'must remain armed for the next response.done (the closing-utterance turn), not consumed while a tool call is still in flight');
    assert.strictEqual(r.context.pendingCallbackTerminalHangup, false);
});

test('T) 回帰: callbackTerminalArmed=falseの通常ターンはHOTFIX12/13の既存分岐（AI_WORKING/incomplete/通常silence timer）が変更なく動作する', () => {
    const rWorking = runGate({ lastResponseTranscriptWasProcessNarrationOnly: true });
    assert.deepStrictEqual(rWorking.sendResponseCreateCalls, ['ai_working_continuation']);

    const rIncomplete = runGate({ lastResponseTranscriptWasIncompleteAiTurn: true });
    assert.deepStrictEqual(rIncomplete.sendResponseCreateCalls, ['incomplete_ai_turn_continuation']);

    const rNormal = runGate({});
    assert.deepStrictEqual(rNormal.startSilenceTimerCalls, [{ gen: 1, reason: 'response_done_no_function_call' }]);
});

test('U) maybeHangUpAfterCallbackTerminal: pendingフラグを消費し、staleでなければbounded tail grace経由でendCall()を1回だけ呼ぶ', () => {
    const events = [];
    const logs = [];
    const endCallCalls = [];
    const timeouts = [];
    const context = {
        pendingCallbackTerminalHangup: true,
        // FAST TURN HOTFIX 18（今回更新）: maybeHangUpAfterCallbackTerminal()が
        // 新しく参照するようになったモジュールレベルのtail grace state/setTimeout。
        callbackFinalTailGraceTimerId: null,
        CALLBACK_FINAL_AUDIO_TAIL_GRACE_MS: 1500,
        setTimeout: (fn, ms) => { const id = timeouts.length; timeouts.push({ fn, ms, fired: false }); return id; },
        isStaleCallEvent: () => false,
        pushTimelineEvent: (t) => events.push(t),
        console: { log: (l) => logs.push(l) },
        endCall: (text, reason) => endCallCalls.push({ text, reason }),
    };
    vm.createContext(context);
    vm.runInContext(MAYBE_HANGUP_CALLBACK_TERMINAL_FN, context);
    vm.runInContext('maybeHangUpAfterCallbackTerminal', context)(1, 'ai_audio_stopped');
    assert.strictEqual(context.pendingCallbackTerminalHangup, false);
    // HOTFIX18: endCall()はbounded tail grace（setTimeout）を挟んでから呼ばれる
    // ため、この時点ではまだ呼ばれていない。
    assert.strictEqual(endCallCalls.length, 0, 'HOTFIX18: endCall must not fire synchronously before the tail grace timer');
    assert.strictEqual(timeouts.length, 1, 'exactly one tail grace timer must be scheduled');
    for (const t of timeouts) { if (!t.fired) { t.fired = true; t.fn(); } }
    assert.strictEqual(endCallCalls.length, 1);
    assert.strictEqual(endCallCalls[0].reason, 'callback_terminal');
    assert.ok(events.some((e) => e.indexOf('CALLBACK_TERMINAL_END_CALL_REQUESTED') === 0));
    assert.ok(logs.some((l) => l === '[CALLBACK_TERMINAL_END_CALL]'));

    // 2回目の呼び出し（フラグ消費済み）は何もしない
    vm.runInContext('maybeHangUpAfterCallbackTerminal', context)(1, 'response_done_fallback');
    assert.strictEqual(endCallCalls.length, 1, 'must not hang up twice');
});

test('U2) maybeHangUpAfterCallbackTerminal: pendingフラグが立っていなければ何もしない（no-op）', () => {
    const endCallCalls = [];
    const context = {
        pendingCallbackTerminalHangup: false,
        isStaleCallEvent: () => false,
        pushTimelineEvent: () => {},
        console: { log: () => {} },
        endCall: (text, reason) => endCallCalls.push({ text, reason }),
    };
    vm.createContext(context);
    vm.runInContext(MAYBE_HANGUP_CALLBACK_TERMINAL_FN, context);
    vm.runInContext('maybeHangUpAfterCallbackTerminal', context)(1, 'ai_audio_stopped');
    assert.strictEqual(endCallCalls.length, 0);
});

test('U3) maybeHangUpAfterCallbackTerminal: stale generationならフラグは消費するがendCall()は呼ばない（音声完了検知後・通話跨ぎの誤発火防止）', () => {
    const endCallCalls = [];
    const context = {
        pendingCallbackTerminalHangup: true,
        isStaleCallEvent: () => true,
        pushTimelineEvent: () => {},
        console: { log: () => {} },
        endCall: (text, reason) => endCallCalls.push({ text, reason }),
    };
    vm.createContext(context);
    vm.runInContext(MAYBE_HANGUP_CALLBACK_TERMINAL_FN, context);
    vm.runInContext('maybeHangUpAfterCallbackTerminal', context)(1, 'ai_audio_stopped');
    assert.strictEqual(context.pendingCallbackTerminalHangup, false);
    assert.strictEqual(endCallCalls.length, 0);
});

test('V) 構造確認: maybeHangUpAfterCallbackTerminal()の主経路(output_audio_buffer.stopped)は既存のmaybeHangUpAfterSilenceGoodbye()と同じ箇所から即時に呼ばれている（音声再生完了を待ってから切るという既存パターンの再利用・HOTFIX15でも不変）', () => {
    const stoppedIdx = SRC.indexOf("maybeHangUpAfterSilenceGoodbye(callGeneration, 'ai_audio_stopped');");
    const stoppedCallbackIdx = SRC.indexOf("maybeHangUpAfterCallbackTerminal(callGeneration, 'ai_audio_stopped');", stoppedIdx);
    assert.notStrictEqual(stoppedIdx, -1);
    assert.notStrictEqual(stoppedCallbackIdx, -1);
    assert.ok(stoppedCallbackIdx - stoppedIdx < 400, 'must be hooked in immediately after the existing silence-goodbye hangup call (output_audio_buffer.stopped handler)');
});

test('V2) FAST TURN HOTFIX 15: response.doneのcallback terminalフォールバックは、もはや同じ同期実行内で即座には呼ばれない（HOTFIX14のroot cause: 主経路(output_audio_buffer.stopped)がこのresponse.done自身の音声再生完了を待つ機会が一度も無いまま、直後の安全網が即時にendCall()してしまっていた）。silence-goodbye側のフォールバックは意図的に変更していない（ユーザー指示§14のスコープ外）。', () => {
    const fallbackIdx = SRC.indexOf("maybeHangUpAfterSilenceGoodbye(callGeneration, 'response_done_fallback');");
    assert.notStrictEqual(fallbackIdx, -1, 'silence-goodbye response.done fallback must remain untouched (out of scope)');

    // HOTFIX14の「即時・無条件」呼び出しは、もはやソースに存在しないこと
    // （存在していればHOTFIX15のroot causeが再発する）。
    assert.ok(!SRC.includes("maybeHangUpAfterCallbackTerminal(callGeneration, 'response_done_fallback');"),
        'the old unconditional, same-tick fallback call must no longer exist verbatim (that was HOTFIX14\'s premature-hangup root cause)');

    // 新しい実装: setTimeoutで遅延された呼び出しに置き換わっていること。
    const deferredIdx = SRC.indexOf("maybeHangUpAfterCallbackTerminal(callGeneration, 'response_done_fallback_deferred');");
    assert.notStrictEqual(deferredIdx, -1, 'deferred fallback call site not found');
    const setTimeoutIdx = SRC.lastIndexOf('setTimeout(', deferredIdx);
    assert.notStrictEqual(setTimeoutIdx, -1);
    assert.ok(deferredIdx - setTimeoutIdx < 200, 'the callback-terminal fallback call must be wrapped in setTimeout (deferred), not called synchronously');
    const delayMatch = SRC.slice(deferredIdx, deferredIdx + 400).match(/\},\s*(\d+)\)/);
    assert.ok(delayMatch, 'could not find the setTimeout delay value near the deferred fallback call');
    assert.strictEqual(Number(delayMatch[1]), 10000, 'must reuse the existing 10000ms bounded-fallback convention used elsewhere in this file (AI_WORKING_CONTINUATION etc.), not an invented value');

    assert.ok(fallbackIdx < deferredIdx, 'silence-goodbye fallback call must remain textually before the (now-deferred) callback-terminal fallback, i.e. the surrounding structure was not reordered');
});

test('W) 構造確認: startCall()の通話ごとリセットブロックがcallbackTerminalArmed/pendingCallbackTerminalHangup/callbackAlreadyConfirmedThisCallを初期化している', () => {
    const resetIdx = SRC.indexOf('callbackTerminalArmed = false;\n            pendingCallbackTerminalHangup = false;\n            callbackAlreadyConfirmedThisCall = false;');
    assert.notStrictEqual(resetIdx, -1, 'per-call reset block for HOTFIX14 callback terminal state not found');
});

test('X) バックエンド契約: _HUMAN_HANDOFF_TEMPLATEは折り返し成功メッセージ(5-1/5-2)の直後に、追加の質問・確認・ご案内をせず会話を終えるよう明示的に指示している', () => {
    const idx = PY_SRC.indexOf('_HUMAN_HANDOFF_TEMPLATE = """');
    assert.notStrictEqual(idx, -1);
    const endIdx = PY_SRC.indexOf('\n"""', idx);
    const template = PY_SRC.slice(idx, endIdx);
    assert.ok(template.includes('5-3'), 'must add an explicit closing instruction after the success wording (5-1/5-2)');
    assert.ok(template.includes('用件は完結します'));
    assert.ok(/続けて「ほかにご用件はありますか」等の追加の質問・\s*確認・ご案内は行わず/.test(template),
        'must explicitly forbid follow-up questions/offers as a NEGATIVE instruction (quoting the forbidden phrase only as an example not to say)');
});

test('X2) バックエンド契約: 5-3の追記はsuccessがfalseの場合の案内（6.）より前に置かれ、失敗時の既存フォールバック文言には一切影響しない', () => {
    const idx = PY_SRC.indexOf('5-3. 5-1または5-2');
    const failureIdx = PY_SRC.indexOf('6. successがfalseの場合');
    assert.notStrictEqual(idx, -1);
    assert.notStrictEqual(failureIdx, -1);
    assert.ok(idx < failureIdx, '5-3 (success-only closing instruction) must precede the failure-path instruction, never merge with it');
});

test('Y) 回帰: dc.send(JSON.stringify(...))の生呼び出し箇所数は変更されていない（新しいresponse.create経路も既存のsendResponseCreate()一元化ラッパー経由）', () => {
    const sendCount = (SRC.match(/dc\.send\(JSON\.stringify\(/g) || []).length;
    // HOTFIX13時点で10箇所（sendResponseCreate内部の1箇所＋conversation.item.create系等の既存箇所）。
    // HOTFIX14はresponse.create経路を新設していないため、この総数は変わらない。
    assert.strictEqual(sendCount, 10, 'HOTFIX14 must not add a new raw dc.send call site for response.create (it must reuse sendResponseCreate())');
});

test('Y2) 新しいsendResponseCreate呼び出し箇所は1つだけ追加されている（initial_greeting_zero_wait_followup）', () => {
    const count = (SRC.match(/sendResponseCreate\('initial_greeting_zero_wait_followup'\)/g) || []).length;
    assert.strictEqual(count, 1);
});

test('Z) PRIVACY: 新規診断マーカー(CALLBACK_TERMINAL_ARMED/CALLBACK_TERMINAL_RESPONSE_DONE/CALLBACK_TERMINAL_END_CALL/CALLBACK_REQUEST_DEDUPED/INITIAL_GREETING_RECOVERY_SENT)は固定文言のみで、顧客名・電話番号・予約内容・transcript本文を一切含まない', () => {
    const markers = [
        "pushTimelineEvent('CALLBACK_TERMINAL_ARMED');",
        "console.log('[CALLBACK_TERMINAL_ARMED]');",
        "pushTimelineEvent('CALLBACK_TERMINAL_RESPONSE_DONE');",
        "console.log('[CALLBACK_TERMINAL_RESPONSE_DONE]');",
        "console.log('[CALLBACK_TERMINAL_END_CALL]');",
        "pushTimelineEvent('CALLBACK_REQUEST_DEDUPED (reason=already_confirmed)');",
        "pushTimelineEvent('INITIAL_GREETING_RECOVERY_SENT (reason=zero_wait_greeting_ended)');",
        "console.log('[INITIAL_GREETING_RECOVERY_SENT]');",
    ];
    for (const m of markers) {
        assert.ok(SRC.includes(m), 'expected diagnostic marker call site missing: ' + m);
    }
});

// ============================================================
// 回帰スイート（サブプロセス実行）
// ============================================================

test('AA) 回帰スイート: HOTFIX 10/11/12/13 の対象テストが全て通る', () => {
    for (const relPath of [
        'test_fast_turn_hotfix10_rate_limit_fallback.js',
        'test_fast_turn_hotfix11_normal_conversation_rate_limit_fallback.js',
        'test_fast_turn_hotfix12_ai_working_continuation.js',
        'test_fast_turn_hotfix13_incomplete_ai_turn.js',
    ]) {
        const r = runNodeTest(relPath);
        assert.strictEqual(r.status, 0, relPath + ' must pass:\n' + r.stdout + r.stderr);
    }
});

test('AB) 回帰スイート: FIRST ANSWER MUST COUNT / PHONE Forced Commit / NAME-FIRST FLOW が全て通る', () => {
    for (const relPath of [
        'test_first_answer_must_count.js',
        'test_phone_forced_commit.js',
        'test_name_first_flow.js',
    ]) {
        const r = runNodeTest(relPath);
        assert.strictEqual(r.status, 0, relPath + ' must pass:\n' + r.stdout + r.stderr);
    }
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
process.exit(failed > 0 ? 1 : 0);

})();
