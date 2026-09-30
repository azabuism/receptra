'use strict';

// RECEPTRA — FAST TURN HOTFIX 15（2026年9月）
// 「CALLBACK FINAL CONFIRMATION BEFORE HANGUP」の契約テスト。
//
// 実機症状（ユーザー指示より）: request_callback成功後、AIが
// 「最後に担当者の折り返し手配を確認しますね。」（PRE_CALLBACK_NARRATION、
// 最終案内ではない）を話した直後に通話が切れてしまい、本来の最終案内
// （5-1/5-2の成功案内・お礼）が一度も再生されない。
//
// 実コードで確認したroot cause（推測ではなく、realtime-voice-engine.js
// 6289-6433行目のresponse.doneハンドラを実際に読んで特定した）:
//   1. callbackTerminalArmedは、request_callback成功時にhandleFunctionCallItem
//      内で非同期に（response.output_item.doneトリガーで、対応する
//      response.doneを待たず）立てられる。
//   2. その後のtool_result継続response（＝本来の最終案内であるべき応答）の
//      response.doneハンドラ内で、`if (!responseHasFunctionCall) { if
//      (callbackTerminalArmed) { ... pendingCallbackTerminalHangup = true;
//      } }` という最優先分岐により、"この同じresponse.doneハンドラの実行の
//      中で" 初めてpendingCallbackTerminalHangup=trueになる。
//   3. HOTFIX14はこの直後（同じresponse.doneハンドラの実行の続き、同じ
//      同期呼び出しスタックの中）で、無条件に
//      maybeHangUpAfterCallbackTerminal(callGeneration,
//      'response_done_fallback') を呼んでいた。output_audio_buffer.stopped
//      は通常response.doneより先に来る（本ファイル内の既存コメント）ため、
//      この折り返しclosing応答自身のstoppedイベントはこの時点でまだ発生
//      し得ず、結果として「安全網」であるはずのこの呼び出しが、間を置かず
//      必ず先にpendingCallbackTerminalHangupを消費してendCall()してしまい、
//      本来の主経路（output_audio_buffer.stopped、＝この応答自身の音声
//      再生完了を検知する経路）が一度も勝てない構造になっていた。
//      つまり、この折り返しclosing応答が実際に何を話す内容だったとしても
//      （「最後に確認しますね」であっても、正しい成功案内であっても）、
//      その音声が再生され切る前に必ず通話が切れる、という決定的な
//      （タイミング依存ではない）バグだった。
//
// HOTFIX15の修正（最小変更・新しいstate machineは作らない）:
//   response.doneのcallback terminalフォールバック呼び出しを、本ファイル内の
//   既存の他の安全網（AI_WORKING_CONTINUATION/incomplete_ai_turn_continuationの
//   10秒setTimeout安全網）と全く同じ「有界(10秒)setTimeoutで遅延」パターンに
//   変更しただけ。主経路(output_audio_buffer.stopped)が正常に先に発火すれば
//   pendingCallbackTerminalHangupは既にfalseになっているため、この遅延
//   フォールバックは既存のno-opガードにより自動的に何もしない
//   （二重endCallの心配はない）。主経路が万一発火しなかった場合にのみ、
//   10秒後にこの遅延フォールバックが実際にendCall()する（無期限に通話を
//   保持したままにはしない＝ユーザー指示§12）。
// silence-goodbye側の同種フォールバックは意図的に一切変更していない
// （ユーザー指示§14のスコープ外・既存の実機確認済み挙動を保護する）。
//
// 実行: node tests/test_fast_turn_hotfix15_callback_final_confirmation.js

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

// response.doneハンドラ内の silence-timeout ゲート全体（callbackTerminalArmed
// 分岐を含む）を抽出する。HOTFIX14テストと同じシグネチャ・同じ抽出関数。
function extractGateSnippet(src) {
    const sig = 'if (!responseHasFunctionCall) {\n                    if (callbackTerminalArmed) {';
    const idx = src.indexOf(sig);
    assert.notStrictEqual(idx, -1, 'silence-timeout gate (with callbackTerminalArmed branch) not found');
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

// HOTFIX15で新設した「response.doneのcallback terminalフォールバックを
// 遅延させるブロック」だけを抜き出す（if (pendingCallbackTerminalHangup) {
// ... setTimeout(...) ... } の全体）。
function extractDeferredFallbackSnippet(src) {
    const anchor = "if (pendingCallbackTerminalHangup) {\n                    pushTimelineEvent('CALLBACK_TERMINAL_FALLBACK_DEFERRED";
    const idx = src.indexOf(anchor);
    assert.notStrictEqual(idx, -1, 'HOTFIX15 deferred fallback block not found — has response.done been restructured?');
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

function extractElseIfBody(src, condLiteral) {
    const header = "else if (" + condLiteral + ") {";
    const idx = src.indexOf(header);
    assert.notStrictEqual(idx, -1, 'else-if block not found: ' + condLiteral);
    const braceStart = idx + header.length - 1;
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
const DEFERRED_FALLBACK_SNIPPET = extractDeferredFallbackSnippet(SRC);
const CALLBACK_BRANCH_BODY = extractElseIfBody(SRC, "item.name === 'request_callback'");
const MAYBE_HANGUP_CALLBACK_TERMINAL_FN = extractFunctionSource(SRC, 'maybeHangUpAfterCallbackTerminal', false);
// PHASE C（今回追加）: tail grace完了後の実際のendCall()呼び出しは
// waitForPlaybackSettleThenEndCall()へ委譲されるようになったため、このVM
// コンテキストでtail graceタイマーを発火させるテスト群が解決できるよう
// 併せて抽出しておく（cancelCallbackPlaybackAwareWaitも再入防止分岐で
// 参照される）。
const WAIT_FOR_PLAYBACK_SETTLE_FN = extractFunctionSource(SRC, 'waitForPlaybackSettleThenEndCall', false);
const CANCEL_PLAYBACK_AWARE_WAIT_FN = extractFunctionSource(SRC, 'cancelCallbackPlaybackAwareWait', false);
const IS_TOOL_OUTPUT_FAILURE_FN = extractFunctionSource(SRC, 'isToolOutputFailure', false);

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

console.log('FAST TURN HOTFIX 15 — CALLBACK FINAL CONFIRMATION BEFORE HANGUP contract tests');
console.log('source: ' + ENGINE_JS_PATH);
console.log('');

(async () => {

// ============================================================
// 統合シミュレーション: 「response.done → (gate) → deferred fallback」を
// 実際にこの順で連結して実行し、実機タイムラインを再現する。
// ============================================================

function buildIntegrationContext(overrides) {
    const events = [];
    const consoleLogs = [];
    const endCallCalls = [];
    const scheduled = []; // { fn, ms }
    const context = {
        responseHasFunctionCall: false,
        callbackTerminalArmed: false,
        pendingCallbackTerminalHangup: false,
        // FAST TURN HOTFIX 18（今回更新）: maybeHangUpAfterCallbackTerminal()
        // が新しく参照するようになったモジュールレベルのtail grace state。
        callbackFinalTailGraceTimerId: null,
        CALLBACK_FINAL_AUDIO_TAIL_GRACE_MS: 1500,
        // PHASE C（今回追加）: waitForPlaybackSettleThenEndCall()/
        // cancelCallbackPlaybackAwareWait()が参照するモジュールレベルstate。
        // remoteAudioElはnullのままにし、fail-open経路（audio_element_absent
        // → 即proceedToEndCall）を通す。waiting/stalled(confirm-window方式)
        // イベントを使った実際の待機挙動は専用のtests/test_phase_c_playback_aware_teardown.js
        // 側でカバーする。
        callbackPlaybackAwareWaitTimerId: null,
        callbackPlaybackAwareListenersCleanup: null,
        PLAYBACK_AWARE_MAX_WAIT_MS: 2500,
        PLAYBACK_AWARE_SETTLE_MARGIN_MS: 300,
        PLAYBACK_AWARE_SETTLE_CONFIRM_MS: 400,
        remoteAudioEl: null,
        callbackDiagTerminalResponseId: null,
        callbackDiagCurrentResponseId: null,
        callbackDiagUnexpectedResponseDuringGrace: false,
        callbackDiagUnexpectedAudioDuringGrace: false,
        callbackDiagCaptureAudioState: () => 'audioElPresent=false',
        responseState: 'done',
        aiAudioOutputActive: false,
        performance: { now: () => Date.now() },
        deferSilenceTimerForToolContinuationRateLimit: false,
        lastResponseTranscriptWasProcessNarrationOnly: false,
        lastResponseTranscriptWasIncompleteAiTurn: false,
        lastResponseReasonCategoryForDiag: 'normal_conversation',
        deferSilenceTimerForAiWorkingContinuation: false,
        deferSilenceTimerForIncompleteAiTurnContinuation: false,
        callGeneration: 1,
        pushTimelineEvent: (text) => { events.push(text); },
        console: { log: (line) => { consoleLogs.push(line); } },
        sendResponseCreate: () => true,
        startSilenceTimerIfNeeded: () => {},
        setTimeout: (fn, ms) => { const id = scheduled.length; scheduled.push({ fn, ms, fired: false }); return id; },
        clearTimeout: (id) => { if (typeof id === 'number' && scheduled[id]) scheduled[id].fired = true; },
        endCall: (text, reason) => { endCallCalls.push({ text, reason }); },
        isStaleCallEvent: () => !!(overrides && overrides.__stale),
    };
    Object.assign(context, overrides || {});
    vm.createContext(context);
    // maybeHangUpAfterCallbackTerminal自体をこのコンテキストへ注入する
    // （GATE_SNIPPET/DEFERRED_FALLBACK_SNIPPETの両方から参照されるため）。
    vm.runInContext(MAYBE_HANGUP_CALLBACK_TERMINAL_FN, context);
    // PHASE C（今回追加）: tail grace完了後に委譲される新関数群も同じ
    // コンテキストへ注入する。
    vm.runInContext(WAIT_FOR_PLAYBACK_SETTLE_FN, context);
    vm.runInContext(CANCEL_PLAYBACK_AWARE_WAIT_FN, context);
    return { context, events, consoleLogs, endCallCalls, scheduled };
}

function fireAllScheduled(built) {
    // テスト内の「時間経過」を模倣する: 積まれたsetTimeoutを全て実行する。
    for (const s of built.scheduled) {
        if (!s.fired) { s.fired = true; s.fn(); }
    }
}

test('1) [最重要/root cause再現防止] callbackTerminalArmed成功直後のresponse.done処理そのものの中では、endCall()が同期的に呼ばれない（HOTFIX14はここで即endCall()していた＝実機の即時切断のroot cause）', () => {
    const built = buildIntegrationContext({ callbackTerminalArmed: true });
    vm.runInContext(GATE_SNIPPET, built.context);
    assert.strictEqual(built.context.pendingCallbackTerminalHangup, true, 'gate must still arm the pending hangup flag (unchanged from HOTFIX14)');
    // ここまでの時点（GATE_SNIPPET実行直後）でendCall()が呼ばれていたら、
    // HOTFIX14のroot causeが再発している。
    assert.strictEqual(built.endCallCalls.length, 0, 'endCall must NOT fire synchronously as part of arming — this is the exact HOTFIX14 bug');

    // 続けて同じresponse.doneハンドラの残り（HOTFIX15のdeferred fallback
    // ブロック）を実行しても、まだendCall()は呼ばれない（setTimeoutへ
    // 積まれるだけ）。
    vm.runInContext(DEFERRED_FALLBACK_SNIPPET, built.context);
    assert.strictEqual(built.endCallCalls.length, 0, 'endCall must not fire synchronously even after the deferred-fallback block runs — it must only be scheduled');
    assert.strictEqual(built.scheduled.length, 1, 'exactly one fallback must be scheduled');
    assert.strictEqual(built.scheduled[0].ms, 10000, 'must reuse the existing 10000ms bounded-fallback convention');
    assert.ok(built.events.some((e) => e === 'CALLBACK_TERMINAL_FALLBACK_DEFERRED (delayMs=10000)'));
});

test('2) 主経路(output_audio_buffer.stopped)がsetTimeoutより先に発火すれば、その場でendCall()し、後で発火する遅延フォールバックは何もしない(no-op)', () => {
    const built = buildIntegrationContext({ callbackTerminalArmed: true });
    vm.runInContext(GATE_SNIPPET, built.context);
    vm.runInContext(DEFERRED_FALLBACK_SNIPPET, built.context);
    assert.strictEqual(built.endCallCalls.length, 0);

    // 主経路: この応答自身の音声再生完了(output_audio_buffer.stopped)を模擬。
    vm.runInContext('maybeHangUpAfterCallbackTerminal', built.context)(1, 'ai_audio_stopped');
    // FAST TURN HOTFIX 18（今回更新）: endCall()の前にbounded tail grace
    // （setTimeout）が挟まるようになったため、この時点ではまだ呼ばれない。
    assert.strictEqual(built.endCallCalls.length, 0, 'HOTFIX18: endCall must not fire synchronously before the tail grace timer');

    // 時間経過後、遅延フォールバック「と」新しいtail graceタイマーの両方が
    // 発火する（fireAllScheduledは新たにscheduledへ積まれたタイマーも
    // 拾って発火する）。それでもendCall()は1回だけ。
    fireAllScheduled(built);
    assert.strictEqual(built.endCallCalls.length, 1, 'primary path must end the call once its own audio finishes (after its tail grace), deferred fallback must not double it');
    assert.strictEqual(built.endCallCalls[0].reason, 'callback_terminal');
});

test('3) 主経路が(既知のOpenAI側イベント遅延/欠落バグにより)全く発火しなかった場合でも、10秒後の遅延フォールバックが確実にendCall()する（無期限に通話を保持しない＝ユーザー指示§12）', () => {
    const built = buildIntegrationContext({ callbackTerminalArmed: true });
    vm.runInContext(GATE_SNIPPET, built.context);
    vm.runInContext(DEFERRED_FALLBACK_SNIPPET, built.context);
    assert.strictEqual(built.endCallCalls.length, 0, 'must not have ended the call yet (bounded wait, not indefinite)');

    fireAllScheduled(built);
    assert.strictEqual(built.endCallCalls.length, 1, 'the bounded (10s) fallback must eventually end the call even if output_audio_buffer.stopped never arrives');
    assert.strictEqual(built.endCallCalls[0].reason, 'callback_terminal');
});

test('4) レース: 遅延フォールバックが先に発火し、その直後に主経路イベントが届いても、二重にendCall()しない', () => {
    const built = buildIntegrationContext({ callbackTerminalArmed: true });
    vm.runInContext(GATE_SNIPPET, built.context);
    vm.runInContext(DEFERRED_FALLBACK_SNIPPET, built.context);

    fireAllScheduled(built); // 遅延フォールバックが先に発火
    assert.strictEqual(built.endCallCalls.length, 1);

    // 直後に(遅延して)主経路イベントが届いたと仮定しても、無視される。
    vm.runInContext('maybeHangUpAfterCallbackTerminal', built.context)(1, 'ai_audio_stopped');
    assert.strictEqual(built.endCallCalls.length, 1, 'endCall must fire at most once regardless of arrival order');
});

test('5) callbackTerminalArmedがfalse(通常ターン)の場合、遅延フォールバックブロックは何もスケジュールしない(既存の通常ターンへの影響ゼロ)', () => {
    const built = buildIntegrationContext({});
    vm.runInContext(GATE_SNIPPET, built.context);
    assert.strictEqual(built.context.pendingCallbackTerminalHangup, false);
    vm.runInContext(DEFERRED_FALLBACK_SNIPPET, built.context);
    assert.strictEqual(built.scheduled.length, 0, 'must not schedule any timer when this response.done was not the callback-terminal response');
    assert.strictEqual(built.endCallCalls.length, 0);
});

test('6) PRE_CALLBACK_NARRATION（function_callを伴う応答, responseHasFunctionCall=true）の段階では、callbackTerminalArmedが立っていてもgateは評価されず、遅延フォールバックも一切スケジュールされない（Tool往復中に切らない＝ユーザー指示§5）', () => {
    const built = buildIntegrationContext({ responseHasFunctionCall: true, callbackTerminalArmed: true });
    vm.runInContext(GATE_SNIPPET, built.context);
    assert.strictEqual(built.context.pendingCallbackTerminalHangup, false, 'must remain false while a function_call is still part of this response');
    vm.runInContext(DEFERRED_FALLBACK_SNIPPET, built.context);
    assert.strictEqual(built.scheduled.length, 0);
    assert.strictEqual(built.endCallCalls.length, 0);
});

test('7) stale generation（次の通話が既に始まっている）なら、遅延フォールバック発火時もendCall()しない', () => {
    const built = buildIntegrationContext({ callbackTerminalArmed: true, __stale: true });
    vm.runInContext(GATE_SNIPPET, built.context);
    vm.runInContext(DEFERRED_FALLBACK_SNIPPET, built.context);
    fireAllScheduled(built);
    assert.strictEqual(built.endCallCalls.length, 0, 'a stale (superseded) call must never be hung up by a leftover deferred timer from a previous call');
});

// ============================================================
// request_callback branch: 実行回数・response.create送信回数の再確認
// ============================================================

function buildCallbackBranchSandbox(overrides) {
    const events = [];
    const consoleLogs = [];
    const toolCalls = [];
    const sendResponseCreateCalls = [];
    const context = {
        callbackAlreadyConfirmedThisCall: false,
        // SMART INTERRUPTION / NOISE RESILIENCE HOTFIX（今回追加）: request_callback
        // 分岐がphoneConfirmationIncompleteを参照するようになったため、既存
        // sandboxのデフォルトにも追加する（本番側のデフォルト値falseと同じ）。
        phoneConfirmationIncomplete: false,
        phoneReadbackTurnCompletedThisCall: true,
        phoneReadbackAwaitingUserReply: false,
        callbackTerminalArmed: false,
        args: {},
        callId: 'call_abc123',
        logEvent: () => {},
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
    return { context, events, consoleLogs, toolCalls, sendResponseCreateCalls };
}

async function runCallbackBranch(overrides) {
    const built = buildCallbackBranchSandbox(overrides);
    const wrapped = '(async function(){ let output;\n' + CALLBACK_BRANCH_BODY + '\n return output; })()';
    const output = await vm.runInContext(wrapped, built.context);
    return { ...built, output };
}

await testAsync('8) [ユーザー指示§13・最重要] request_callback成功後に同じ通話内でモデルが再度request_callbackを呼んでも、実際のTool（callRequestCallbackTool）は二度と実行されない', async () => {
    const first = await runCallbackBranch({});
    assert.strictEqual(first.toolCalls.length, 1);
    assert.strictEqual(first.context.callbackAlreadyConfirmedThisCall, true);

    // 2回目の呼び出し（同一コンテキストの続き＝同一通話を模擬）。
    const wrapped = '(async function(){ let output;\n' + CALLBACK_BRANCH_BODY + '\n return output; })()';
    const secondOutput = await vm.runInContext(wrapped, first.context);
    assert.strictEqual(first.toolCalls.length, 1, 'callRequestCallbackTool must still have been called exactly once total across both invocations');
    assert.strictEqual(secondOutput.status, 'already_confirmed');
});

test('9) request_callback成功時、tool継続response.createは(classify_intent以外の全Toolと同じ)既存の一元化ラッパー呼び出し1回のみで送られる（新しいdc.send経路を増やさない）', () => {
    const sendCount = (CALLBACK_BRANCH_BODY.match(/sendResponseCreate\(/g) || []).length;
    // request_callback分岐自体はsendResponseCreate()を直接呼ばない
    // （呼び出し元のhandleFunctionCallItem共通処理の1箇所のみで送信される）。
    assert.strictEqual(sendCount, 0, 'the request_callback branch itself must not add its own response.create call site — it must rely on the single shared continuation call');
});

// ============================================================
// バックエンド契約: 最終案内は単一の固定文言のみで完結し（お礼等の追加
// 発話は今回明示的に禁止された）、5-1がPRE_CALLBACK_NARRATIONを最終案内
// の代替として認めていないこと
//
// 【RECEPTRA『CALLBACK最終案内文の固定』タスクによる意図的な契約変更】
// このファイルのテスト10は元々「5-1/5-2の成功案内の例文にお礼(ありがとう
// ございました)が含まれていること」を要求していたが、実機テストで最終
// 案内の文言が発話ごとに揺れる問題が確認されたため、successがtrueの場合
// の最終案内は「担当者から折り返し連絡しますので、電話を切ってお待ち
// ください。」の1文に固定され、「お電話ありがとうございました」等の
// お礼を含む追加の一言は、この最終案内の直後に付け加えることを明示的に
// 禁止する方針へ変更された。テスト10はこの新方針を検証する内容へ更新する。
// ============================================================

test('10) バックエンド契約: successがtrueの場合の最終案内は単一の固定文言のみであり、「ありがとうございました」等のお礼を含む追加の一言をこの最終案内の直後に付け加えることを明示的に禁止している（RECEPTRA『CALLBACK最終案内文の固定』タスク＝旧・お礼を含む例文方針からの意図的な変更）', () => {
    const idx = PY_SRC.indexOf('_HUMAN_HANDOFF_TEMPLATE = """');
    const endIdx = PY_SRC.indexOf('\n"""', idx);
    const template = PY_SRC.slice(idx, endIdx);
    const s5 = template.slice(template.indexOf('5. successがtrueになったら'), template.indexOf('6. successがfalseの場合'));
    assert.ok(s5.includes('「担当者から折り返し連絡しますので、電話を切ってお待ちください。」'),
        'the single official fixed final phrase must be present verbatim');
    assert.ok(/「ありがとうございました」[^\n]*付け加えず/.test(s5),
        'must explicitly forbid adding a trailing thanks phrase after the official final phrase');
});

test('11) バックエンド契約: 5-1が、PRE_CALLBACK_NARRATION的な処理中の言い回し（「最後に...確認しますね」）を最終案内の代わりとして認めていないことを明示している', () => {
    const idx = PY_SRC.indexOf('_HUMAN_HANDOFF_TEMPLATE = """');
    const endIdx = PY_SRC.indexOf('\n"""', idx);
    const template = PY_SRC.slice(idx, endIdx);
    const s51 = template.slice(template.indexOf('5-1.'), template.indexOf('6. successがfalseの場合'));
    assert.ok(s51.includes('確認しますね'), 'must explicitly name the observed premature-narration phrasing as insufficient');
    assert.ok(s51.includes('この最終案内の代わりにはなりません'));
});

test('12) 回帰: NAME/ROUTING/RESERVATION用instructions構築関数は_HUMAN_HANDOFF_TEMPLATEを一切参照していない（HOTFIX15の文言変更によるchar deltaはCALLBACK/legacy_fullのみに限定される＝ユーザー指示§22）', () => {
    const nameRoutineStart = PY_SRC.indexOf('def _build_phase_minimal_instructions(');
    assert.notStrictEqual(nameRoutineStart, -1, 'NAME/ROUTING minimal builder (shared by Phase 1/2) not found (has it been renamed?)');
    const nextDefIdx = PY_SRC.indexOf('\ndef _build_reservation_phase_instructions(', nameRoutineStart);
    assert.notStrictEqual(nextDefIdx, -1);
    // 実際の「使用」(sections配列の要素・.append引数)だけを見る。docstring/
    // コメント内の説明的な言及（例: 「_HUMAN_HANDOFF_TEMPLATE全文2,791文字の
    // 代わり」）は実際のコード上の参照ではないため誤検知しないようにする。
    const USAGE_PATTERNS = ['_HUMAN_HANDOFF_TEMPLATE,', '_HUMAN_HANDOFF_TEMPLATE)', 'append(_HUMAN_HANDOFF_TEMPLATE'];
    const usesHandoffTemplate = (body) => USAGE_PATTERNS.some((p) => body.includes(p));

    const fnBody = PY_SRC.slice(nameRoutineStart, nextDefIdx);
    assert.ok(!usesHandoffTemplate(fnBody), 'NAME/ROUTING must not actually use _HUMAN_HANDOFF_TEMPLATE (a docstring mention referencing its char count is fine)');

    const reservationStart = nextDefIdx + 1;
    const callbackDefIdx = PY_SRC.indexOf('\ndef _build_callback_phase_instructions(', reservationStart);
    assert.notStrictEqual(callbackDefIdx, -1);
    const reservationBody = PY_SRC.slice(reservationStart, callbackDefIdx);
    assert.ok(!usesHandoffTemplate(reservationBody), 'RESERVATION must not actually use _HUMAN_HANDOFF_TEMPLATE (per its own docstring)');

    const nextAfterCallbackIdx = PY_SRC.indexOf('\ndef _select_realtime_tools(', callbackDefIdx);
    assert.notStrictEqual(nextAfterCallbackIdx, -1);
    const callbackBody = PY_SRC.slice(callbackDefIdx, nextAfterCallbackIdx);
    assert.ok(callbackBody.includes('_HUMAN_HANDOFF_TEMPLATE'), 'CALLBACK must include _HUMAN_HANDOFF_TEMPLATE (this is the phase HOTFIX15 targets)');
});

// ============================================================
// PII-free診断マーカー
// ============================================================

test('13) PRIVACY: 新規診断マーカー(CALLBACK_TERMINAL_FALLBACK_DEFERRED)は固定文言のみで、顧客名・電話番号・予約内容・transcript本文を一切含まない', () => {
    const markers = [
        "pushTimelineEvent('CALLBACK_TERMINAL_FALLBACK_DEFERRED (delayMs=10000)');",
        "console.log('[CALLBACK_TERMINAL_FALLBACK_DEFERRED]');",
    ];
    for (const m of markers) {
        assert.ok(SRC.includes(m), 'expected diagnostic marker call site missing: ' + m);
    }
});

test('14) bounded: HOTFIX15の変更はループ構文を一切含まない（無限retry禁止の構造的裏付け）', () => {
    assert.ok(!/while\s*\(/.test(DEFERRED_FALLBACK_SNIPPET));
    assert.ok(!/for\s*\(/.test(DEFERRED_FALLBACK_SNIPPET));
    const setTimeoutCount = (DEFERRED_FALLBACK_SNIPPET.match(/setTimeout\(/g) || []).length;
    assert.strictEqual(setTimeoutCount, 1, 'must schedule exactly one bounded fallback, not a repeating timer');
});

// ============================================================
// 回帰スイート（サブプロセス実行）
// ============================================================

test('15) 回帰スイート: HOTFIX14（GREETING COMPLETION + CALLBACK TERMINAL、V/V2改訂含む）が全て通る', () => {
    const r = runNodeTest('test_fast_turn_hotfix14_greeting_callback_terminal.js');
    assert.strictEqual(r.status, 0, 'test_fast_turn_hotfix14_greeting_callback_terminal.js must pass:\n' + r.stdout + r.stderr);
});

test('16) 回帰スイート: HOTFIX 10/11/12/13 の対象テストが全て通る', () => {
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

test('17) 回帰スイート: FIRST ANSWER MUST COUNT / PHONE Forced Commit / NAME-FIRST FLOW が全て通る', () => {
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
