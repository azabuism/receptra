'use strict';

// RECEPTRA — FAST TURN HOTFIX 18（2026年9月）
// 「CALLBACK FINAL AUDIO TAIL ONLY」の契約テスト。
//
// 実機症状（ユーザー指示§0より）: HOTFIX17適用後、「折り返しのご連絡いたし
// ますか？」という不要な再確認は解消された（維持・回帰させない）。しかし
// 最後の「担当者から折り返しお電話いたします。ありがとうございました。」が
// 「担当者から折り返しお電話いたし・・・」の途中で通話が切れてしまう。
//
// 実コードで確認したroot cause（推測ではなく、実際にrealtime-voice-engine.js
// のmaybeHangUpAfterCallbackTerminal()・cleanupConnection()を読んで特定した）:
//   output_audio_buffer.stopped（Realtime APIのサーバー側イベント）は
//   サーバー側の送出バッファが空になったことを示すのみで、ブラウザ側の
//   WebRTC受信/再生パイプラインに残っている音声が実際にスピーカーから
//   鳴り終わったことを保証しない。HOTFIX17までは、このイベントを検知した
//   直後にendCall()→cleanupConnection()内でpc.close()と<audio>要素の除去を
//   行っており、残っていた末尾の音声（「ありがとうございました」相当）が
//   強制的に破棄され得た（cleanupConnection()直上の既存コメント「ブチッ」
//   調査が、まさにこのクラスの問題を過去に調査していたことも確認済み）。
//
// HOTFIX18の修正（新しいstate machineは作らない。既存のpendingフラグ消費
// パターンを維持したまま、endCall()の直前にbounded tail graceだけ挟む）:
//   maybeHangUpAfterCallbackTerminal()内で、CALLBACK_FINAL_AUDIO_DONEの後、
//   即endCall()する代わりに、CALLBACK_FINAL_AUDIO_TAIL_GRACE_MS(=1500ms)の
//   setTimeoutを1つだけ挟んでからendCall()する。新しいresponse.create・
//   final再生成・callback promptの変更は一切行っていない（realtime_voice_
//   ai.pyはHOTFIX18で無変更）。maybeHangUpAfterSilenceGoodbye（無言タイム
//   アウトのclosing発話）には一切触れていない。

const fs = require('fs');
const path = require('path');
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

const MAYBE_HANGUP_CALLBACK_TERMINAL_FN = extractFunctionSource(SRC, 'maybeHangUpAfterCallbackTerminal', false);
const CANCEL_TAIL_GRACE_FN = extractFunctionSource(SRC, 'cancelCallbackFinalTailGrace', false);
const CLEANUP_CONNECTION_FN = extractFunctionSource(SRC, 'cleanupConnection', false);
const END_CALL_FN = extractFunctionSource(SRC, 'endCall', false);
const GATE_SNIPPET = extractGateSnippet(SRC);
const CALLBACK_BRANCH_BODY = extractElseIfBody(SRC, "item.name === 'request_callback'");

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

function runPythonTest(relPath) {
    const result = spawnSync('python3', [path.join(__dirname, '..', relPath)], { encoding: 'utf8', cwd: path.join(__dirname, '..') });
    return { status: result.status, stdout: result.stdout || '', stderr: result.stderr || '' };
}

console.log('FAST TURN HOTFIX 18 — CALLBACK FINAL AUDIO TAIL ONLY contract tests');
console.log('source: ' + ENGINE_JS_PATH);
console.log('');

// ============================================================
// A-B: callback success → terminal armed / final response max 1（回帰）
// ============================================================

test('A) [回帰] request_callback成功時のみcallbackAlreadyConfirmedThisCall/callbackTerminalArmedがtrueになる（失敗時はtail graceに一切到達しない構造）', () => {
    assert.ok(CALLBACK_BRANCH_BODY.includes('if (!isToolOutputFailure(output)) {'));
    const successBlock = CALLBACK_BRANCH_BODY.slice(CALLBACK_BRANCH_BODY.indexOf('if (!isToolOutputFailure(output)) {'));
    assert.ok(successBlock.includes('callbackAlreadyConfirmedThisCall = true;'));
    assert.ok(successBlock.includes('callbackTerminalArmed = true;'));
});

test('B) [回帰] final response最大1回のterminal機構（callbackTerminalArmedのconsume）はHOTFIX16/17から無変更', () => {
    const r = runNodeTest('test_fast_turn_hotfix16_callback_single_terminal.js');
    assert.strictEqual(r.status, 0, 'HOTFIX16 targeted test must pass unchanged:\n' + r.stdout + r.stderr);
});

// ============================================================
// C-D: response.done → 即endCallしない / fallback deferred維持
// ============================================================

test('C) [HOTFIX15契約維持] response.doneのcallbackTerminalArmed分岐（GATE_SNIPPET）自体はendCall()を直接呼ばない（pendingCallbackTerminalHangup=trueにするだけ）', () => {
    const armedBranch = GATE_SNIPPET.slice(
        GATE_SNIPPET.indexOf('if (callbackTerminalArmed) {'),
        GATE_SNIPPET.indexOf('} else if (deferSilenceTimerForToolContinuationRateLimit)')
    );
    assert.ok(armedBranch.includes('pendingCallbackTerminalHangup = true;'));
    assert.ok(!armedBranch.includes('endCall('), 'response.done handler must not call endCall() directly for CALLBACK terminal (HOTFIX15 regression guard)');
});

test('D) [回帰] response.doneの10秒遅延fallback（CALLBACK_TERMINAL_FALLBACK_DEFERRED）は維持されている', () => {
    assert.ok(SRC.includes("CALLBACK_TERMINAL_FALLBACK_DEFERRED (delayMs=10000)"));
    assert.ok(SRC.includes("maybeHangUpAfterCallbackTerminal(callGeneration, 'response_done_fallback_deferred');"));
});

// ============================================================
// E-G: output_audio_buffer.stopped → tail grace → endCall（今回の本体修正）
// ============================================================

test('E) [今回の修正の核心] maybeHangUpAfterCallbackTerminal()内で、CALLBACK_FINAL_AUDIO_DONEのログ直後にendCall(が即座に呼ばれていない（setTimeoutの中でのみ呼ばれる）', () => {
    const afterAudioDone = MAYBE_HANGUP_CALLBACK_TERMINAL_FN.slice(
        MAYBE_HANGUP_CALLBACK_TERMINAL_FN.indexOf("CALLBACK_FINAL_AUDIO_DONE (source=")
    );
    const setTimeoutIdx = afterAudioDone.indexOf('setTimeout(');
    const endCallIdx = afterAudioDone.indexOf("endCall('お電話ありがとうございました。'");
    assert.notStrictEqual(setTimeoutIdx, -1, 'a setTimeout must be present after CALLBACK_FINAL_AUDIO_DONE');
    assert.notStrictEqual(endCallIdx, -1, 'endCall must still be called eventually');
    assert.ok(setTimeoutIdx < endCallIdx, 'setTimeout(...) must appear before endCall(...) in source (endCall must be inside the timer callback, not called synchronously first)');
});

test('F) [tail grace開始] output_audio_buffer.stopped経由でCALLBACK_FINAL_TAIL_GRACE_STARTEDマーカーが記録される（grace_msを含む）', () => {
    assert.ok(MAYBE_HANGUP_CALLBACK_TERMINAL_FN.includes("CALLBACK_FINAL_TAIL_GRACE_STARTED (grace_ms="));
    assert.ok(MAYBE_HANGUP_CALLBACK_TERMINAL_FN.includes('CALLBACK_FINAL_AUDIO_TAIL_GRACE_MS'));
});

test('G) [tail grace完了→endCall] タイマーcallback内でCALLBACK_FINAL_TAIL_GRACE_COMPLETEDの後にendCall()が呼ばれる', () => {
    const timerBodyStart = MAYBE_HANGUP_CALLBACK_TERMINAL_FN.indexOf('callbackFinalTailGraceTimerId = setTimeout(() => {');
    assert.notStrictEqual(timerBodyStart, -1);
    const timerBody = MAYBE_HANGUP_CALLBACK_TERMINAL_FN.slice(timerBodyStart);
    const completedIdx = timerBody.indexOf('CALLBACK_FINAL_TAIL_GRACE_COMPLETED');
    const endCallIdx = timerBody.indexOf("endCall('お電話ありがとうございました。'");
    assert.notStrictEqual(completedIdx, -1);
    assert.notStrictEqual(endCallIdx, -1);
    assert.ok(completedIdx < endCallIdx, 'CALLBACK_FINAL_TAIL_GRACE_COMPLETED must be logged before endCall() inside the timer callback');
});

// ============================================================
// H: endCall最大1回（既存のendCall()自体のガードで保証）
// ============================================================

test('H) [回帰] endCall()自身のended二重防止ガード（if (ended) return;）はHOTFIX18で変更されていない', () => {
    assert.ok(END_CALL_FN.includes('if (ended) return;'));
});

// ============================================================
// I-J: race protection（重複イベント・fallbackとの競合）
// ============================================================

test('I) [race防止] maybeHangUpAfterCallbackTerminal()の先頭はpendingCallbackTerminalHangupの即時ガード＋同期的な消費（false化）のままであり、重複したoutput_audio_buffer.stoppedがtail graceタイマーを二重生成しない', () => {
    const trimmed = MAYBE_HANGUP_CALLBACK_TERMINAL_FN.replace(/^function maybeHangUpAfterCallbackTerminal\([^)]*\)\s*\{\s*/, '');
    assert.ok(trimmed.startsWith('if (!pendingCallbackTerminalHangup) return;'), 'entry guard must remain the very first statement');
    const guardIdx = MAYBE_HANGUP_CALLBACK_TERMINAL_FN.indexOf('if (!pendingCallbackTerminalHangup) return;');
    const consumeIdx = MAYBE_HANGUP_CALLBACK_TERMINAL_FN.indexOf('pendingCallbackTerminalHangup = false;');
    const setTimeoutIdx = MAYBE_HANGUP_CALLBACK_TERMINAL_FN.indexOf('callbackFinalTailGraceTimerId = setTimeout(');
    assert.ok(guardIdx < consumeIdx && consumeIdx < setTimeoutIdx, 'the flag must be consumed synchronously before any timer is created');
});

test('J) [race防止] 既存のtail graceタイマーが残っている場合は新しいタイマーを作る前に必ずclearTimeoutする（防御的多重防止）', () => {
    const clearIdx = MAYBE_HANGUP_CALLBACK_TERMINAL_FN.indexOf('if (callbackFinalTailGraceTimerId !== null) {');
    const setTimeoutIdx = MAYBE_HANGUP_CALLBACK_TERMINAL_FN.indexOf('callbackFinalTailGraceTimerId = setTimeout(');
    assert.notStrictEqual(clearIdx, -1);
    assert.ok(clearIdx < setTimeoutIdx);
});

// ============================================================
// K-L: manual hangup / connection close時の安全cleanup
// ============================================================

test('K) [manual end / L) connection close 安全cleanup] cleanupConnection()（手動終了・接続断・beforeunloadの全経路で呼ばれる共通関数）が待機中のtail graceタイマーをcancelCallbackFinalTailGrace()経由で破棄する', () => {
    assert.ok(CLEANUP_CONNECTION_FN.includes('cancelCallbackFinalTailGrace('));
});

test('K2) cancelCallbackFinalTailGrace()自体はclearTimeout+null化+診断ログのみを行い、endCall()やresponse.create等の副作用を持たない', () => {
    assert.ok(CANCEL_TAIL_GRACE_FN.includes('clearTimeout(callbackFinalTailGraceTimerId)'));
    assert.ok(CANCEL_TAIL_GRACE_FN.includes('callbackFinalTailGraceTimerId = null;'));
    assert.ok(!CANCEL_TAIL_GRACE_FN.includes('endCall('));
    assert.ok(!CANCEL_TAIL_GRACE_FN.includes('sendResponseCreate('));
});

test('L2) [新しい通話開始時の防御的cleanup] startCall()の状態リセット箇所でもcancelCallbackFinalTailGrace(\'new_call_setup\')が呼ばれる（前回通話のタイマー持ち越し防止）', () => {
    assert.ok(SRC.includes("cancelCallbackFinalTailGrace('new_call_setup');"));
});

// ============================================================
// M-N: callback failure時はtail grace/success announcementなし
// ============================================================

test('M) [failure時tail graceなし] 実際にrequest_callbackを呼び出す経路（else節、callRequestCallbackTool()実行後）では、callbackTerminalArmedはisToolOutputFailure(output)がfalseの場合のみ設定され、failure時はpendingCallbackTerminalHangup/tail grace自体に一切到達しない構造', () => {
    // CALLBACK_BRANCH_BODY内には2つの`callbackTerminalArmed = true;`が存在する:
    // (1) 既に成功済みの場合の再実行dedup分岐（callbackAlreadyConfirmedThisCall
    //     ガード内、HOTFIX16で追加）— ここは「過去に成功済み」が前提のため
    //     armedを再セットしても安全（実際のTool再実行はしない）。
    // (2) 今回実際にcallRequestCallbackTool()を呼び出した後、isToolOutput
    //     Failure(output)がfalseの場合のみ（＝今回のfailure時にはtail grace/
    //     endCallへ一切到達しない構造）。
    // ここで確認すべきは(2)、すなわち実際にTool呼び出しを行う"else"節の中で、
    // isToolOutputFailureチェックより前にarmedがセットされていないこと。
    const toolCallIdx = CALLBACK_BRANCH_BODY.indexOf('output = await callRequestCallbackTool(args || {}, callId);');
    assert.notStrictEqual(toolCallIdx, -1);
    const ifIdx = CALLBACK_BRANCH_BODY.indexOf('if (!isToolOutputFailure(output)) {', toolCallIdx);
    assert.notStrictEqual(ifIdx, -1);
    const betweenToolCallAndCheck = CALLBACK_BRANCH_BODY.slice(toolCallIdx, ifIdx);
    assert.ok(!betweenToolCallAndCheck.includes('callbackTerminalArmed = true;'), 'callbackTerminalArmed must not be set before the success check in the real invocation path');
});

test('N) [failure時success announcementなし・回帰] request_callbackツールのdescriptionはsuccess=falseの場合に折り返すと案内しないことを引き続き明示している（HOTFIX18ではprompt無変更）', () => {
    const idx = PY_SRC.indexOf('"name": "request_callback"');
    assert.notStrictEqual(idx, -1);
    const descIdx = PY_SRC.indexOf('"description": (', idx);
    const endIdx = PY_SRC.indexOf('),\n        "parameters"', descIdx);
    const desc = PY_SRC.slice(descIdx, endIdx);
    assert.ok(desc.includes('successがfalseの場合は'));
    assert.ok(desc.includes('担当者から折り返す、とは案内せず'));
});

// ============================================================
// O-P: request_callback最大1回・callback final最大1回（回帰）
// ============================================================

test('O) [回帰] request_callbackの二重実行防止（callbackAlreadyConfirmedThisCallによるdedup）は無変更', () => {
    assert.ok(CALLBACK_BRANCH_BODY.includes('if (callbackAlreadyConfirmedThisCall) {'));
    assert.ok(CALLBACK_BRANCH_BODY.includes("status: 'already_confirmed'"));
});

test('P) [回帰] callback final（closing発話）最大1回の機構はHOTFIX16と同じ構造のまま（callbackTerminalArmed=falseの出現箇所が増えていない）', () => {
    const armedFalseCount = (SRC.match(/callbackTerminalArmed = false;/g) || []).length;
    // 内訳: (1) let宣言時の初期値、(2) response.doneのterminal分岐でのconsume、
    // (3) 新しい通話開始時のリセット = 3箇所が正しいベースライン（HOTFIX16/17と不変）。
    assert.strictEqual(armedFalseCount, 3, 'callbackTerminalArmed = false; should appear exactly 3 times (let declaration + terminal consume + new-call reset), unchanged from HOTFIX16/17');
});

// ============================================================
// Q-S: AI_WORKING/incomplete continuation/silence warning復活なし（回帰）
// ============================================================

test('Q) [回帰] silence-timeoutゲートでcallbackTerminalArmedが最優先分岐のまま（AI_WORKING/incomplete continuation/通常silence timerより先にチェックされる）', () => {
    const armedIdx = GATE_SNIPPET.indexOf('if (callbackTerminalArmed) {');
    const aiWorkingIdx = GATE_SNIPPET.indexOf('lastResponseTranscriptWasProcessNarrationOnly');
    const incompleteIdx = GATE_SNIPPET.indexOf('lastResponseTranscriptWasIncompleteAiTurn');
    assert.notStrictEqual(armedIdx, -1);
    assert.ok(armedIdx < aiWorkingIdx || aiWorkingIdx === -1);
    assert.ok(armedIdx < incompleteIdx || incompleteIdx === -1);
});

test('R) [回帰] callbackTerminalArmed分岐に入った場合、AI_WORKING_CONTINUATION_SENT/INCOMPLETE_AI_TURN_CONTINUATION_SENTは送出されない（elseif連鎖のため相互排他のまま）', () => {
    const armedBranch = GATE_SNIPPET.slice(
        GATE_SNIPPET.indexOf('if (callbackTerminalArmed) {'),
        GATE_SNIPPET.indexOf('} else if (deferSilenceTimerForToolContinuationRateLimit)')
    );
    assert.ok(!armedBranch.includes('AI_WORKING_CONTINUATION_SENT'));
    assert.ok(!armedBranch.includes('INCOMPLETE_AI_TURN_CONTINUATION_SENT'));
});

test('S) [回帰] callbackTerminalArmed分岐はstartSilenceTimerIfNeeded()を呼ばない（通常のsilence timerが復活しない）', () => {
    const armedBranch = GATE_SNIPPET.slice(
        GATE_SNIPPET.indexOf('if (callbackTerminalArmed) {'),
        GATE_SNIPPET.indexOf('} else if (deferSilenceTimerForToolContinuationRateLimit)')
    );
    assert.ok(!armedBranch.includes('startSilenceTimerIfNeeded'));
});

// ============================================================
// T: phase transition復活なし（HOTFIX18はphase transitionに一切触れていない）
// ============================================================

test('T) [スコープ確認] 今回追加したtail grace関連コード（maybeHangUpAfterCallbackTerminal/cancelCallbackFinalTailGrace）はcurrentRealtimePhase/phaseTransitionInProgress等のphase transition state を一切参照していない', () => {
    assert.ok(!MAYBE_HANGUP_CALLBACK_TERMINAL_FN.includes('currentRealtimePhase'));
    assert.ok(!MAYBE_HANGUP_CALLBACK_TERMINAL_FN.includes('phaseTransitionInProgress'));
    assert.ok(!CANCEL_TAIL_GRACE_FN.includes('currentRealtimePhase'));
    assert.ok(!CANCEL_TAIL_GRACE_FN.includes('phaseTransitionInProgress'));
});

// ============================================================
// U: HOTFIX17 no-reconfirmation維持（回帰）
// ============================================================

test('U) [回帰] HOTFIX17契約テスト（callback再確認禁止＋Zero-Wait診断）は全て無変更で通る', () => {
    const r = runNodeTest('test_fast_turn_hotfix17_callback_no_reconfirm_zero_wait_audio.js');
    assert.strictEqual(r.status, 0, 'HOTFIX17 targeted test must pass unchanged:\n' + r.stdout + r.stderr);
});

test('U2) [回帰] smoke_test_human_handoff_wording.pyは無変更（realtime_voice_ai.pyはHOTFIX18で一切変更していない）', () => {
    const r = runPythonTest('tests/smoke_test_human_handoff_wording.py');
    assert.strictEqual(r.status, 0, r.stdout + r.stderr);
});

// ============================================================
// V-Z: PHONE/DATE/TIME/PARTY_SIZE/NAME回帰なし
// ============================================================

test('V) [回帰] PHONE Forced Commit（test_phone_forced_commit.js）', () => {
    const r = runNodeTest('test_phone_forced_commit.js');
    assert.strictEqual(r.status, 0, r.stdout + r.stderr);
});

test('W-X-Y) [回帰] DATE/TIME/PARTY_SIZEはSHORT_ANSWER/SHORT_CHOICE Forced Commit機構で共通に扱われており（test_short_answer_turn.js/test_short_choice_turn.js）、HOTFIX18はこれらに一切触れていない', () => {
    for (const relPath of ['test_short_answer_turn.js', 'test_short_choice_turn.js']) {
        const r = runNodeTest(relPath);
        assert.strictEqual(r.status, 0, relPath + ':\n' + r.stdout + r.stderr);
    }
});

test('Z) [回帰] NAME-FIRST FLOW（test_name_first_flow.js）', () => {
    const r = runNodeTest('test_name_first_flow.js');
    assert.strictEqual(r.status, 0, r.stdout + r.stderr);
});

// ============================================================
// AA: FIRST ANSWER MUST COUNT維持（回帰）
// ============================================================

test('AA) [回帰] FIRST ANSWER MUST COUNT（test_first_answer_must_count.js / test_first_answer_must_count_fix.js）', () => {
    for (const relPath of ['test_first_answer_must_count.js', 'test_first_answer_must_count_fix.js']) {
        const r = runNodeTest(relPath);
        assert.strictEqual(r.status, 0, relPath + ':\n' + r.stdout + r.stderr);
    }
});

// ============================================================
// 追加測定: sendResponseCreate/dc.send不変・setTimeout+1の妥当性
// ============================================================

test('MEASURE) [§20/§21] sendResponseCreate(/dc.send(JSON.stringify(はHOTFIX17基準から不変（25/10）、setTimeout(は+1（19→20、tail graceタイマー1個分のみ）', () => {
    const sendResponseCreateCount = (SRC.match(/sendResponseCreate\(/g) || []).length;
    const dcSendCount = (SRC.match(/dc\.send\(JSON\.stringify\(/g) || []).length;
    const setTimeoutCount = (SRC.match(/setTimeout\(/g) || []).length;
    assert.strictEqual(sendResponseCreateCount, 25, 'sendResponseCreate( count must remain 25 (no new response generation added)');
    assert.strictEqual(dcSendCount, 10, 'dc.send(JSON.stringify( count must remain 10');
    assert.strictEqual(setTimeoutCount, 20, 'setTimeout( count must be exactly +1 from the HOTFIX17 baseline of 19 (the new tail grace timer only)');
});

test('MEASURE2) [§22/§23] realtime_voice_ai.py（prompt/tool description）はHOTFIX18で一切変更されていない', () => {
    const { execSync } = require('child_process');
    let diffNames = '';
    try {
        diffNames = execSync('git diff --name-only HEAD -- app/services/realtime_voice_ai.py', { cwd: path.join(__dirname, '..'), encoding: 'utf8' });
    } catch (e) {
        diffNames = '';
    }
    assert.strictEqual(diffNames.trim(), '', 'app/services/realtime_voice_ai.py must show no uncommitted diff for HOTFIX18 (prompt-change-禁止 per §22)');
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
if (failed > 0) process.exit(1);
