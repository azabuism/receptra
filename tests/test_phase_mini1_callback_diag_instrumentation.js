'use strict';

// RECEPTRA — PHASE MINI-1 observability-only instrumentation contract tests。
//
// 背景（ユーザー指示より）: ローカル限定 gpt-realtime-2.1-mini A/Bテストの
// 実機検証で、callback（折り返し）terminalフロー終盤に「ブツブツ」という
// 断片的な追加発話が聞こえ、その直後に通話が切れる症状が報告された。
// ユーザーからは明示的に「1500msのtail grace自体が無くなっていると仮定
// しないこと」との指摘があり、下記2つの仮説を実機ログのみで切り分ける
// ためのPII一切無しの観測専用instrumentation（[CALLBACK_DIAG_*]/
// [CALLBACK_UNEXPECTED_*_DURING_TAIL_GRACE] marker群）を追加した。
//   H1: callback terminal（tail grace）中/直後に想定外の追加
//       response/audioイベントが発生している。
//   H2: 意図した唯一のterminal response自身のaudioが壊れている、
//       または末尾に余剰が含まれている。
//
// このファイルは「診断ログを追加したことで、既存のcallback terminal挙動
// （HOTFIX14-19で確立された1回限りのterminal機構・1500ms tail grace・
// endCall()の呼び出しタイミング）が一切変わっていないこと」だけを検証する。
// 実際にH1/H2のどちらが真かはこのテストでは判定しない（次の実機テストで
// ブラウザconsoleのmarkerを人間が確認する）。

const fs = require('fs');
const path = require('path');
const assert = require('assert');
const { spawnSync, execSync } = require('child_process');

const REPO_ROOT = path.join(__dirname, '..');
const ENGINE_REL_PATH = path.join('frontend', 'public', 'js', 'realtime-voice-engine.js');
const ENGINE_JS_PATH = path.join(REPO_ROOT, ENGINE_REL_PATH);
const SRC = fs.readFileSync(ENGINE_JS_PATH, 'utf8');

function headSource() {
    return execSync('git show HEAD:' + ENGINE_REL_PATH.split(path.sep).join('/'), {
        cwd: REPO_ROOT,
        encoding: 'utf8',
    });
}
const HEAD_SRC = headSource();

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

// type==='xxx' の if/else-if ブロック本体を抜き出す（handleDataChannelEvent内の
// トップレベル分岐専用。ネストしたif/elseのバランスを見て終端を決める）。
function extractTypeBranchBody(src, condLiteral, opts) {
    opts = opts || {};
    const headerVariants = opts.isIf
        ? ['if (' + condLiteral + ') {']
        : ['else if (' + condLiteral + ') {'];
    let idx = -1, header = null;
    for (const h of headerVariants) {
        idx = src.indexOf(h);
        if (idx !== -1) { header = h; break; }
    }
    assert.notStrictEqual(idx, -1, 'branch not found: ' + condLiteral);
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

function extractElseIfBody(src, condLiteral) {
    const header = 'else if (' + condLiteral + ') {';
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

function countOccurrences(src, literalOrRegex) {
    const re = (literalOrRegex instanceof RegExp)
        ? literalOrRegex
        : new RegExp(literalOrRegex.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g');
    return (src.match(re) || []).length;
}

// 呼び出し（"endCall("等）を、コメント行内の言及と区別して検出する。
// 対象文字列を含む「行」を1行ずつ見て、その行が // で始まるコメント専用行
// でない場合のみ「実際の呼び出しの可能性あり」とみなす（この診断
// instrumentationでは対象トークンは常にコード上の実呼び出しか、行頭
// コメントのどちらかにしか現れない設計にしているため、この単純な判定で
// 十分）。
function hasRealCallOutsideComments(body, token) {
    const lines = body.split('\n');
    for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith('//')) continue;
        if (trimmed.includes(token)) return true;
    }
    return false;
}

const RESPONSE_CREATED_BODY = extractElseIfBody(SRC, "type === 'response.created'");
const RESPONSE_DONE_BODY = extractElseIfBody(SRC, "type === 'response.done'");
const AUDIO_STARTED_BODY = extractTypeBranchBody(SRC, "type === 'output_audio_buffer.started'", { isIf: true });
const AUDIO_STOPPED_BODY = extractElseIfBody(SRC, "type === 'output_audio_buffer.stopped'");
const MAYBE_HANGUP_CALLBACK_TERMINAL_FN = extractFunctionSource(SRC, 'maybeHangUpAfterCallbackTerminal', false);
// PHASE C（今回追加）: tail grace完了後の実際のendCall()呼び出し（および
// その直前のBEFORE_END診断ログ）は、新しいwaitForPlaybackSettleThenEndCall()
// へ委譲されるようになった（1500ms baseline待機の直後に、browser側の
// playback settle待ちフェーズが追加されたため）。このファイルの静的構造
// テスト（A2/J3）はmaybeHangUpAfterCallbackTerminal()自身の中にendCall()が
// あることを前提にしていたため、この新関数も併せて抽出し、委譲後の等価な
// 契約を検証できるようにする。
const WAIT_FOR_PLAYBACK_SETTLE_FN = extractFunctionSource(SRC, 'waitForPlaybackSettleThenEndCall', false);
const REQUEST_CALLBACK_BRANCH_BODY = extractElseIfBody(SRC, "item.name === 'request_callback'");

const HEAD_RESPONSE_CREATED_BODY = extractElseIfBody(HEAD_SRC, "type === 'response.created'");
const HEAD_AUDIO_STARTED_BODY = extractTypeBranchBody(HEAD_SRC, "type === 'output_audio_buffer.started'", { isIf: true });
const HEAD_MAYBE_HANGUP_CALLBACK_TERMINAL_FN = extractFunctionSource(HEAD_SRC, 'maybeHangUpAfterCallbackTerminal', false);

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

console.log('PHASE MINI-1 — observability-only instrumentation contract tests');
console.log('source: ' + ENGINE_JS_PATH);
console.log('');

// ============================================================
// A) 既存のHOTFIX18正常シーケンス（terminal response.done → audio stopped
//    → 1500ms grace → endCall）は今回の診断ログ追加後も不変
// ============================================================

test('A1) [回帰] 既存のHOTFIX18契約テスト（test_fast_turn_hotfix18_callback_audio_tail.js）は診断ログ追加後も全項目そのまま通る', () => {
    const r = runNodeTest('test_fast_turn_hotfix18_callback_audio_tail.js');
    assert.strictEqual(r.status, 0, 'HOTFIX18 targeted test must still pass unchanged:\n' + r.stdout + r.stderr);
});

test('A2) [シーケンス不変（Phase C: playback-aware第2フェーズへの委譲を含めて検証）] maybeHangUpAfterCallbackTerminal()内の出現順序（entry guard→CALLBACK_FINAL_AUDIO_DONE→tail grace start診断→CALLBACK_FINAL_TAIL_GRACE_STARTED→setTimeout→CALLBACK_FINAL_TAIL_GRACE_COMPLETED→waitForPlaybackSettleThenEndCall()への委譲）が壊れておらず、委譲先のwaitForPlaybackSettleThenEndCall()内でbefore-end-call診断→endCallの順序が維持されている', () => {
    const fn = MAYBE_HANGUP_CALLBACK_TERMINAL_FN;
    const guardIdx = fn.indexOf('if (!pendingCallbackTerminalHangup) return;');
    const audioDoneIdx = fn.indexOf("CALLBACK_FINAL_AUDIO_DONE (source=");
    const diagTailStartIdx = fn.indexOf('[CALLBACK_DIAG_TAIL_GRACE_START]');
    const graceStartedIdx = fn.indexOf('CALLBACK_FINAL_TAIL_GRACE_STARTED (grace_ms=');
    const setTimeoutIdx = fn.indexOf('callbackFinalTailGraceTimerId = setTimeout(');
    const graceCompletedIdx = fn.indexOf('CALLBACK_FINAL_TAIL_GRACE_COMPLETED');
    // PHASE C（今回追加）: maybeHangUpAfterCallbackTerminal()自身はもはや
    // endCall()も[CALLBACK_DIAG_BEFORE_END_CALL]も直接含まない。tail grace
    // 完了後は必ずwaitForPlaybackSettleThenEndCall()へ委譲される。
    const delegateIdx = fn.indexOf('waitForPlaybackSettleThenEndCall(myGeneration, source);');
    for (const [label, v] of Object.entries({ guardIdx, audioDoneIdx, diagTailStartIdx, graceStartedIdx, setTimeoutIdx, graceCompletedIdx, delegateIdx })) {
        assert.notStrictEqual(v, -1, label + ' not found');
    }
    assert.ok(guardIdx < audioDoneIdx);
    assert.ok(audioDoneIdx < diagTailStartIdx, '診断ログはCALLBACK_FINAL_AUDIO_DONEより後、grace開始マーカーより前');
    assert.ok(diagTailStartIdx < graceStartedIdx);
    assert.ok(graceStartedIdx < setTimeoutIdx);
    assert.ok(setTimeoutIdx < graceCompletedIdx, 'grace完了マーカーはsetTimeoutコールバック内');
    assert.ok(graceCompletedIdx < delegateIdx, 'grace完了マーカーの後にplayback-aware第2フェーズへ委譲する');
    assert.ok(!fn.includes('[CALLBACK_DIAG_BEFORE_END_CALL]'), 'maybeHangUpAfterCallbackTerminal()自身はもはやBEFORE_END診断を含まない（委譲先へ移動済み）');
    assert.ok(!fn.includes("endCall('お電話ありがとうございました。'"), 'maybeHangUpAfterCallbackTerminal()自身はもはやendCall()を直接呼ばない（委譲先へ移動済み）');

    // 委譲先: waitForPlaybackSettleThenEndCall()の中で、before-end-call診断→
    // endCall()の順序が既存と同じ形で維持されていることを確認する。
    const waitFn = WAIT_FOR_PLAYBACK_SETTLE_FN;
    const diagBeforeEndCallIdx = waitFn.indexOf('[CALLBACK_DIAG_BEFORE_END_CALL]');
    const endCallIdx = waitFn.indexOf("endCall('お電話ありがとうございました。'");
    assert.notStrictEqual(diagBeforeEndCallIdx, -1, '[CALLBACK_DIAG_BEFORE_END_CALL] not found in waitForPlaybackSettleThenEndCall()');
    assert.notStrictEqual(endCallIdx, -1, 'endCall(...) not found in waitForPlaybackSettleThenEndCall()');
    assert.ok(diagBeforeEndCallIdx < endCallIdx, '診断ログはendCall()の直前のまま（委譲先でも順序不変）');
});

test('A3) [tail grace値・タイマー個数不変] CALLBACK_FINAL_AUDIO_TAIL_GRACE_MS=1500、setTimeout(呼び出しはmaybeHangUpAfterCallbackTerminal内で従来通り1箇所のみ', () => {
    assert.ok(SRC.includes('const CALLBACK_FINAL_AUDIO_TAIL_GRACE_MS = 1500;'));
    const setTimeoutCallsInFn = countOccurrences(MAYBE_HANGUP_CALLBACK_TERMINAL_FN, /setTimeout\(/g);
    assert.strictEqual(setTimeoutCallsInFn, 1, 'maybeHangUpAfterCallbackTerminal()内のsetTimeout(呼び出しは1個のまま（診断ログはタイマーを追加しない）');
});

// ============================================================
// B) response.created が tail grace 中に発生した場合、異常marker
//    [CALLBACK_UNEXPECTED_RESPONSE_DURING_TAIL_GRACE] は出るが、
//    既存のendCall挙動は一切変えない
// ============================================================

test('B1) [異常marker追加] response.createdハンドラは、tail grace中（callbackFinalTailGraceTimerId !== null）であれば[CALLBACK_UNEXPECTED_RESPONSE_DURING_TAIL_GRACE]を出す', () => {
    assert.ok(RESPONSE_CREATED_BODY.includes('[CALLBACK_UNEXPECTED_RESPONSE_DURING_TAIL_GRACE]'));
    assert.ok(RESPONSE_CREATED_BODY.includes('CALLBACK_UNEXPECTED_RESPONSE_DURING_TAIL_GRACE (responseId='));
    assert.ok(RESPONSE_CREATED_BODY.includes('callbackFinalTailGraceTimerId !== null'));
});

test('B2) [挙動不変・純粋prepend] response.createdハンドラの診断ブロックはtry/catchで囲われた読み取り専用ログのみで、直後に既存の先頭処理（greetingTiming.firstResponseCreated判定）がそのまま続く（置換ではなく追加のみ）', () => {
    const catchIdx = RESPONSE_CREATED_BODY.indexOf('} catch (diagErr) {}');
    assert.notStrictEqual(catchIdx, -1);
    const afterCatch = RESPONSE_CREATED_BODY.slice(catchIdx + '} catch (diagErr) {}'.length).replace(/^\s+/, '');
    assert.ok(afterCatch.startsWith('if (greetingTiming.firstResponseCreated === null) {'), '診断ブロックの直後は既存の最初の文のまま');
});

test('B3) [endCall挙動不変] response.createdハンドラ本体はendCall(/sendResponseCreate(/生のclearTimeout(呼び出しを一切含まない（診断ログ追加前後で変化なし）', () => {
    assert.ok(!hasRealCallOutsideComments(RESPONSE_CREATED_BODY, 'endCall('), 'response.created handler must not call endCall()');
    assert.ok(!hasRealCallOutsideComments(RESPONSE_CREATED_BODY, 'sendResponseCreate('), 'response.created handler must not call sendResponseCreate()');
    assert.ok(!hasRealCallOutsideComments(RESPONSE_CREATED_BODY, 'clearTimeout('), 'response.created handler must not call clearTimeout() directly');
    // HEAD（診断ログ追加前）でも同様に0件であることを確認し、「今回変化していない」ことを併せて保証する。
    assert.ok(!hasRealCallOutsideComments(HEAD_RESPONSE_CREATED_BODY, 'endCall('));
    assert.ok(!hasRealCallOutsideComments(HEAD_RESPONSE_CREATED_BODY, 'sendResponseCreate('));
});

// ============================================================
// C) output_audio_buffer.started が tail grace 中に発生した場合、
//    異常marker [CALLBACK_UNEXPECTED_AUDIO_DURING_TAIL_GRACE] は
//    出るが、既存のendCall挙動は一切変えない
// ============================================================

test('C1) [異常marker追加] output_audio_buffer.startedハンドラは、tail grace中であれば[CALLBACK_UNEXPECTED_AUDIO_DURING_TAIL_GRACE]を出す', () => {
    assert.ok(AUDIO_STARTED_BODY.includes('[CALLBACK_UNEXPECTED_AUDIO_DURING_TAIL_GRACE]'));
    assert.ok(AUDIO_STARTED_BODY.includes('CALLBACK_UNEXPECTED_AUDIO_DURING_TAIL_GRACE (responseId='));
    assert.ok(AUDIO_STARTED_BODY.includes('callbackFinalTailGraceTimerId !== null'));
});

test('C2) [挙動不変・純粋prepend] output_audio_buffer.startedハンドラの診断ブロックの直後は既存の先頭処理（aiAudioOutputActive = true;）がそのまま続く', () => {
    const catchIdx = AUDIO_STARTED_BODY.indexOf('} catch (diagErr) {}');
    assert.notStrictEqual(catchIdx, -1);
    const afterCatch = AUDIO_STARTED_BODY.slice(catchIdx + '} catch (diagErr) {}'.length).replace(/^\s+/, '');
    assert.ok(afterCatch.startsWith('aiAudioOutputActive = true;'), '診断ブロックの直後は既存の最初の文のまま');
});

test('C3) [endCall挙動不変] output_audio_buffer.startedハンドラ本体はendCall(/sendResponseCreate(を一切含まない（診断ログ追加前後で変化なし）', () => {
    assert.ok(!hasRealCallOutsideComments(AUDIO_STARTED_BODY, 'endCall('));
    assert.ok(!hasRealCallOutsideComments(AUDIO_STARTED_BODY, 'sendResponseCreate('));
    assert.ok(!hasRealCallOutsideComments(HEAD_AUDIO_STARTED_BODY, 'endCall('));
    assert.ok(!hasRealCallOutsideComments(HEAD_AUDIO_STARTED_BODY, 'sendResponseCreate('));
});

test('C4) [output_audio_buffer.stopped側も同様] stoppedハンドラの診断ブロックの直後は既存の先頭処理（aiAudioOutputActive = false;）がそのまま続き、endCall/sendResponseCreateを一切含まない', () => {
    const catchIdx = AUDIO_STOPPED_BODY.indexOf('} catch (diagErr) {}');
    assert.notStrictEqual(catchIdx, -1);
    const afterCatch = AUDIO_STOPPED_BODY.slice(catchIdx + '} catch (diagErr) {}'.length).replace(/^\s+/, '');
    assert.ok(afterCatch.startsWith('aiAudioOutputActive = false;'));
    assert.ok(!hasRealCallOutsideComments(AUDIO_STOPPED_BODY, 'endCall('));
    assert.ok(!hasRealCallOutsideComments(AUDIO_STOPPED_BODY, 'sendResponseCreate('));
});

// ============================================================
// D) sendResponseCreate( の出現回数が今回の変更で増えていない
// ============================================================

test('D) [回帰・最重要] sendResponseCreate(の出現回数はHEAD（診断ログ追加前・保護済みベースライン）と今回の変更後で完全に一致する', () => {
    const before = countOccurrences(HEAD_SRC, 'sendResponseCreate(');
    const after = countOccurrences(SRC, 'sendResponseCreate(');
    console.log('    sendResponseCreate( count: before=' + before + ' after=' + after);
    assert.strictEqual(after, before, 'no new sendResponseCreate( call may be introduced by diagnostics-only instrumentation');
});

// ============================================================
// E) dc.send( の出現回数が今回の変更で増えていない
// ============================================================

test('E) [回帰・最重要] dc.send(の出現回数はHEAD（診断ログ追加前・保護済みベースライン）と今回の変更後で完全に一致する', () => {
    const before = countOccurrences(HEAD_SRC, 'dc.send(');
    const after = countOccurrences(SRC, 'dc.send(');
    console.log('    dc.send( count: before=' + before + ' after=' + after);
    assert.strictEqual(after, before, 'no new dc.send( call may be introduced by diagnostics-only instrumentation');
});

// ============================================================
// F) endCall( の「実際の呼び出し」回数（コメント内言及を除く）が
//    今回の変更で増えていない（endCall(という文字列自体は新規コメントで
//    2箇所増えるが、それは呼び出しではないことをここで明示的に区別する）
// ============================================================

test('F) [回帰] endCall(の実際の呼び出し箇所（行頭//コメントを除く）はHEADと今回の変更後で完全に一致する', () => {
    function realCallLines(src) {
        return src.split('\n').filter((line) => {
            const trimmed = line.trim();
            if (trimmed.startsWith('//')) return false;
            return trimmed.includes('endCall(');
        }).length;
    }
    const before = realCallLines(HEAD_SRC);
    const after = realCallLines(SRC);
    console.log('    endCall( real-call lines: before=' + before + ' after=' + after);
    assert.strictEqual(after, before, 'no new real endCall( invocation may be introduced (comment-only mentions of endCall() are fine and excluded here)');
});

// ============================================================
// G) request_callback function-call検知/tool結果の診断ログはPIIを含まない
// ============================================================

test('G) [PII安全性] request_callbackブランチの新規診断ログ（[CALLBACK_DIAG_FUNCTION_CALL]/[CALLBACK_DIAG_TOOL_RESULT]）はargs/output本文を一切含まない', () => {
    assert.ok(REQUEST_CALLBACK_BRANCH_BODY.includes('[CALLBACK_DIAG_FUNCTION_CALL]'));
    assert.ok(REQUEST_CALLBACK_BRANCH_BODY.includes('[CALLBACK_DIAG_TOOL_RESULT]'));
    // 診断ログのconsole.log行自体にargs/outputの変数参照が含まれていないことを確認する
    // （JSON.stringify(args)やJSON.stringify(output)のような呼び出しが診断marker行に
    // 混入していないか、テキストレベルで検査する）。
    const diagLines = REQUEST_CALLBACK_BRANCH_BODY.split('\n').filter((l) => l.includes('CALLBACK_DIAG_FUNCTION_CALL') || l.includes('CALLBACK_DIAG_TOOL_RESULT'));
    for (const line of diagLines) {
        assert.ok(!line.includes('JSON.stringify(args)'), 'diagnostic marker line must not log tool arguments: ' + line);
        assert.ok(!line.includes('JSON.stringify(output)'), 'diagnostic marker line must not log tool output body: ' + line);
    }
});

// ============================================================
// H) 診断専用変数はcallbackDiag*という一貫した命名で、既存の挙動制御変数
//    （callbackTerminalArmed等）とは完全に分離されている
// ============================================================

test('H) [変数分離] 新規診断専用変数（callbackDiagCurrentResponseId/callbackDiagTerminalResponseId/callbackDiagUnexpectedResponseDuringGrace/callbackDiagUnexpectedAudioDuringGrace）はすべてcallbackDiagプレフィックスを持ち、既存のif分岐条件（if/else if/while/ternaryの条件式）内では一切読まれていない（ログ出力のみに使われている）', () => {
    const diagVarNames = [
        'callbackDiagCurrentResponseId',
        'callbackDiagTerminalResponseId',
        'callbackDiagUnexpectedResponseDuringGrace',
        'callbackDiagUnexpectedAudioDuringGrace',
    ];
    for (const name of diagVarNames) {
        // 宣言が存在すること
        assert.ok(SRC.includes('callbackDiag'.length ? name : name), name + ' must be declared');
        // 既存の分岐条件 `if (<name>` や `if (<name> ` の形で使われていないことを確認する
        // （= 制御フローの条件式としては使われていない、ログ用のif(diagTailGraceActive)等
        // ローカル変数経由の分岐はOKだが、callbackDiag*変数自体を直接if条件に使わない
        // ことを保証する）。
        const ifConditionUsage = new RegExp('if\\s*\\(\\s*' + name + '\\b');
        assert.ok(!ifConditionUsage.test(SRC), name + ' must not be used directly as an if-condition (diagnostics must not drive control flow)');
    }
});

// ============================================================
// I) Issue A/B AUDIT後の追加フェーズ: 終端callback応答のtranscript診断
//    （[CALLBACK_DIAG_TERMINAL_TRANSCRIPT]）はcallback terminal応答
//    確定時のみログされ、本文（transcript本体）は一切保持・出力しない
// ============================================================

const TRANSCRIPT_DONE_BODY = extractElseIfBody(SRC, "type === 'response.output_audio_transcript.done'");

test('I1) [PII安全性] response.output_audio_transcript.doneハンドラ本体はconsole.log(を一切呼ばない（transcript本体は安全な派生値としてのみ一時保持し、この時点ではログ出力しない）', () => {
    assert.ok(!hasRealCallOutsideComments(TRANSCRIPT_DONE_BODY, 'console.log('),
        'output_audio_transcript.done handler must not log anything directly (transcript-derived diagnostics are logged later, only once the response is confirmed to be the callback-terminal one)');
});

test('I2) [PII安全性] transcriptから導出したスナップショットは文字数・真偽値のみを保持し、transcript本文自体（diagTranscript/diagTranscriptTrimmed）をオブジェクトのプロパティ値として代入していない', () => {
    const snapshotAssignIdx = TRANSCRIPT_DONE_BODY.indexOf('callbackDiagLastTranscriptSnapshot = {');
    assert.notStrictEqual(snapshotAssignIdx, -1, 'callbackDiagLastTranscriptSnapshot assignment must exist in this handler');
    const closeIdx = TRANSCRIPT_DONE_BODY.indexOf('};', snapshotAssignIdx);
    assert.notStrictEqual(closeIdx, -1);
    const objectLiteral = TRANSCRIPT_DONE_BODY.slice(snapshotAssignIdx, closeIdx + 2);
    assert.ok(!/:\s*diagTranscript\s*,/.test(objectLiteral), 'must not store the raw transcript string itself: ' + objectLiteral);
    assert.ok(!/:\s*diagTranscriptTrimmed\s*,/.test(objectLiteral), 'must not store the raw (trimmed) transcript string itself: ' + objectLiteral);
    assert.ok(/:\s*diagTranscript\.length\s*,/.test(objectLiteral), 'length field must be derived via .length, not the raw string');
    assert.ok(objectLiteral.includes('endsWithExpectedClosing'), 'must record only a boolean for the closing-suffix check');
    assert.ok(objectLiteral.includes('containsExpectedThankYouEnding'), 'must record only a boolean for the thank-you-phrase check');
});

test('I3) [terminal限定] [CALLBACK_DIAG_TERMINAL_TRANSCRIPT]ログは、response.doneハンドラのcallbackTerminalArmed確定分岐の内側にのみ存在し、それ以外の分岐（非terminal応答）には一切出現しない', () => {
    const armedBranchStart = RESPONSE_DONE_BODY.indexOf('if (callbackTerminalArmed) {');
    assert.notStrictEqual(armedBranchStart, -1, 'callbackTerminalArmed branch must exist in response.done handler');
    let depth = 0, i = RESPONSE_DONE_BODY.indexOf('{', armedBranchStart), bodyStart = -1;
    for (; i < RESPONSE_DONE_BODY.length; i++) {
        if (RESPONSE_DONE_BODY[i] === '{') { depth++; if (bodyStart === -1) bodyStart = i + 1; }
        else if (RESPONSE_DONE_BODY[i] === '}') { depth--; if (depth === 0) break; }
    }
    const armedBranchBody = RESPONSE_DONE_BODY.slice(bodyStart, i);
    const restOfResponseDone = RESPONSE_DONE_BODY.slice(0, armedBranchStart) + RESPONSE_DONE_BODY.slice(i + 1);

    assert.ok(armedBranchBody.includes('[CALLBACK_DIAG_TERMINAL_TRANSCRIPT]'),
        'the terminal-transcript diagnostic must be logged inside the confirmed-terminal branch');
    assert.ok(!restOfResponseDone.includes('[CALLBACK_DIAG_TERMINAL_TRANSCRIPT]'),
        'the terminal-transcript diagnostic must not be logged from any non-terminal branch of response.done');
    assert.ok(!SRC.includes('[CALLBACK_DIAG_TERMINAL_TRANSCRIPT]') || SRC.split('[CALLBACK_DIAG_TERMINAL_TRANSCRIPT]').length - 1 === 1,
        'the terminal-transcript diagnostic marker must be emitted from exactly one call site in the whole file');
});

test('I4) [PII安全性] [CALLBACK_DIAG_TERMINAL_TRANSCRIPT]ログ行自体は、文字数・真偽値・responseIdのみを含み、transcript本文やcustomerの発話内容を一切含まない', () => {
    const logLineIdx = SRC.indexOf("console.log('[CALLBACK_DIAG_TERMINAL_TRANSCRIPT]");
    assert.notStrictEqual(logLineIdx, -1);
    const logStatementEnd = SRC.indexOf(');', logLineIdx) + 2;
    const logStatement = SRC.slice(logLineIdx, logStatementEnd);
    assert.ok(!logStatement.includes('diagTranscript'), 'log statement must not reference the raw transcript variable: ' + logStatement);
    assert.ok(!logStatement.includes('msg.transcript'), 'log statement must not reference msg.transcript directly: ' + logStatement);
    assert.ok(logStatement.includes('.length'));
    assert.ok(logStatement.includes('.endsWithExpectedClosing'));
    assert.ok(logStatement.includes('.containsExpectedThankYouEnding'));
});

// ============================================================
// J) ブラウザ音声要素の技術的な再生状態スナップショット
//    （callbackDiagCaptureAudioState / [CALLBACK_DIAG_AUDIO_STATE_*]）は
//    読み取り専用の診断であり、一切の挙動（endCall/response.create/
//    タイマー等）を左右しない
// ============================================================

const CAPTURE_AUDIO_STATE_FN = extractFunctionSource(SRC, 'callbackDiagCaptureAudioState', false);

test('J1) [挙動不変] callbackDiagCaptureAudioState()はendCall(/sendResponseCreate(/dc.send(/clearTimeout(/setTimeout(のいずれも呼ばない純粋な読み取り専用関数である', () => {
    for (const token of ['endCall(', 'sendResponseCreate(', 'dc.send(', 'clearTimeout(', 'setTimeout(']) {
        assert.ok(!hasRealCallOutsideComments(CAPTURE_AUDIO_STATE_FN, token),
            'callbackDiagCaptureAudioState must not call ' + token);
    }
});

test('J2) [挙動不変] callbackDiagCaptureAudioState()はremoteAudioEl/その内部プロパティへの代入を一切行わない（読み取りのみ）', () => {
    // 代入(=)を含む行のうち、関数内ローカル変数（el/stream/streamActive/trackCount/
    // trackReadyState/trackMuted/audioTracks、いずれもconst/let宣言）への代入以外が
    // 無いことを確認する。remoteAudioElやそのプロパティ（.srcObject等）への代入が
    // 無いことが最重要。
    assert.ok(!/remoteAudioEl\s*=/.test(CAPTURE_AUDIO_STATE_FN), 'must not assign to remoteAudioEl');
    assert.ok(!/\.srcObject\s*=/.test(CAPTURE_AUDIO_STATE_FN), 'must not assign to .srcObject');
    assert.ok(!/\.currentTime\s*=/.test(CAPTURE_AUDIO_STATE_FN), 'must not assign to .currentTime');
});

test('J3) [出現箇所（Phase C: BEFORE_ENDは委譲先のwaitForPlaybackSettleThenEndCall()内）] [CALLBACK_DIAG_AUDIO_STATE_AT_STOP]/[CALLBACK_DIAG_AUDIO_STATE_AT_TAIL_GRACE_START]/[CALLBACK_DIAG_AUDIO_STATE_BEFORE_END]の3マーカーがすべて存在し、それぞれAUDIO_STOPPEDハンドラ・tail grace開始・endCall直前の3箇所にのみ対応する', () => {
    assert.ok(AUDIO_STOPPED_BODY.includes('[CALLBACK_DIAG_AUDIO_STATE_AT_STOP]'));
    const maybeHangupFnAfter = extractFunctionSource(SRC, 'maybeHangUpAfterCallbackTerminal', false);
    assert.ok(maybeHangupFnAfter.includes('[CALLBACK_DIAG_AUDIO_STATE_AT_TAIL_GRACE_START]'));
    // PHASE C（今回追加）: BEFORE_END診断はendCall()そのものと一緒に
    // waitForPlaybackSettleThenEndCall()へ移動した（maybeHangUpAfterCallbackTerminal()
    // 自身はもはやこのマーカーを含まない）。
    assert.ok(!maybeHangupFnAfter.includes('[CALLBACK_DIAG_AUDIO_STATE_BEFORE_END]'), 'maybeHangUpAfterCallbackTerminal()自身はもはやBEFORE_END診断を含まない（委譲先へ移動済み）');
    const waitFnForJ3 = extractFunctionSource(SRC, 'waitForPlaybackSettleThenEndCall', false);
    assert.ok(waitFnForJ3.includes('[CALLBACK_DIAG_AUDIO_STATE_BEFORE_END]'), 'waitForPlaybackSettleThenEndCall()の中にBEFORE_END診断が存在するはず（endCall()の直前）');
    // それぞれ1箇所ずつのみ（ファイル全体で重複出現していない）
    for (const marker of [
        '[CALLBACK_DIAG_AUDIO_STATE_AT_STOP]',
        '[CALLBACK_DIAG_AUDIO_STATE_AT_TAIL_GRACE_START]',
        '[CALLBACK_DIAG_AUDIO_STATE_BEFORE_END]',
    ]) {
        const occurrences = SRC.split(marker).length - 1;
        assert.strictEqual(occurrences, 1, marker + ' must be emitted from exactly one call site, found ' + occurrences);
    }
});

test('J4) [挙動不変] endCall(直前のBEFORE_END診断ブロックは、既存のendCall(呼び出し自体より前に位置し、endCall(の引数・タイミングには一切影響しない（純粋な追加ログのみ）', () => {
    const beforeEndDiagIdx = SRC.indexOf('[CALLBACK_DIAG_AUDIO_STATE_BEFORE_END]');
    const endCallInvocationIdx = SRC.indexOf("endCall('お電話ありがとうございました。', 'callback_terminal');");
    assert.notStrictEqual(beforeEndDiagIdx, -1);
    assert.notStrictEqual(endCallInvocationIdx, -1);
    assert.ok(beforeEndDiagIdx < endCallInvocationIdx, 'the diagnostic snapshot must be logged strictly before the real endCall() invocation, never after or instead of it');
    // endCall(の実引数はHEADと完全一致（診断追加が引数へ影響していないことの直接確認）
    assert.ok(HEAD_SRC.includes("endCall('お電話ありがとうございました。', 'callback_terminal');"),
        'the exact endCall() call signature at the callback-terminal hangup site must be byte-identical to the protected baseline');
});

// ============================================================
// K) 新規診断専用変数・関数も、既存のcallbackDiag*と同じく制御フローの
//    条件式には一切使われていない
// ============================================================

test('K) [変数分離] callbackDiagLastTranscriptSnapshot / callbackDiagCaptureAudioState はいずれも既存の分岐条件（if文の条件式）内で読まれていない（ログ出力の引数としてのみ使われている）', () => {
    assert.ok(!/if\s*\(\s*callbackDiagLastTranscriptSnapshot\b/.test(SRC),
        'callbackDiagLastTranscriptSnapshot must not directly gate an if-condition outside its own diagnostic helper logic');
    assert.ok(!/if\s*\(\s*callbackDiagCaptureAudioState\s*\(/.test(SRC),
        'callbackDiagCaptureAudioState() must never be used as an if-condition');
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
if (failed > 0) process.exit(1);
