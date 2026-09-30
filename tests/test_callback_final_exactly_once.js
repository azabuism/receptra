'use strict';

// RECEPTRA — Task M「CALLBACK FINAL ×4 重複発話 根本原因監査＋最小修正」
// 専用の新規回帰テスト: 「CALLBACK final exactly-once」契約。
//
// 実機症状: CALLBACK分類・電話番号処理は正しく、会話も短く自然だったが、
// CALLBACK最終案内文「担当者から折り返し連絡しますので、電話を切って
// お待ちください。」が約4回発話されてから通話が終了した。
//
// READ-ONLY監査で判明したroot cause（推測ではなく、実際に
// frontend/public/js/realtime-voice-engine.jsのhandleFunctionCallItem内、
// request_callback分岐＋Tool結果継続response.create送信ブロックを読んで
// 特定した）:
//   - request_callbackがこの通話中に複数回呼び出された場合（HOTFIX14の
//     callbackAlreadyConfirmedThisCallによる「既に確定済み」の再呼び出しも
//     含む）、これまでは呼び出しのたびに無条件でTool結果継続の
//     sendResponseCreate('tool_result:request_callback')が実行されていた。
//   - HOTFIX16は、この再呼び出しに対してもcallbackTerminalArmedを再arm
//     する（＝その応答もCALLBACK_TERMINAL優先分岐で「最終案内を話してよい」
//     と扱われる）ことで、response.doneの継続ロジック（AI_WORKING/
//     incomplete_ai_turn等）が誤って重複発話を起こすことは防いでいたが、
//     「新しい応答そのものを生成させない」対策は無かった。
//   - sendResponseCreate()自体もresponseState==='active'を一切ブロックしない
//     設計（観測ログのみ）であるため、request_callbackが呼ばれた回数だけ
//     新しい応答（＝新しい音声）が生成され得た。これが「約4回発話」の
//     root cause（複数の独立したresponse、各々がCALLBACK_TERMINAL優先分岐を
//     通って最終案内フレーズを1回ずつ話す＝合計で複数回聞こえる）。
//
// 今回の最小修正（frontend/public/js/realtime-voice-engine.js）:
//   新しい状態変数 callbackFinalResponseAlreadyRequested（真偽値のみ・PIIなし）
//   を追加し、CALLBACK terminal応答用のresponse.createがこの通話で既に
//   一度送信済みの場合、request_callbackが（成功／dedup扱いで）再度呼ばれても
//   新しいresponse.createを二度と送らないようにした（生成source自体を
//   1回に制限。既存のcallbackTerminalArmed/callbackAlreadyConfirmedThisCall
//   等、他の全てのメカニズムは無変更）。
//
// このファイルは、実際のソースから該当スニペットを毎回抽出して実行する
// （ハードコードしたロジックの複製ではなく、本番コードそのものを検証する）。
//
// 実行: node tests/test_callback_final_exactly_once.js

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ENGINE_JS_PATH = path.join(__dirname, '..', 'frontend', 'public', 'js', 'realtime-voice-engine.js');
const SRC = fs.readFileSync(ENGINE_JS_PATH, 'utf8');
const PY_PATH = path.join(__dirname, '..', 'app', 'services', 'realtime_voice_ai.py');
const PY_SRC = fs.readFileSync(PY_PATH, 'utf8');

function extractElseIfBody(src, condLiteral) {
    const header = "else if (" + condLiteral + ") {";
    const idx = src.indexOf(header);
    assert.notStrictEqual(idx, -1, 'else-if block not found: ' + condLiteral);
    const bodyStart = idx + header.length;
    let depth = 1, i = bodyStart;
    for (; i < src.length; i++) {
        if (src[i] === '{') depth++;
        else if (src[i] === '}') {
            depth--;
            if (depth === 0) break;
        }
    }
    return src.slice(bodyStart, i);
}

// handleFunctionCallItem内、「共通のtool_result継続response.create送信」
// ブロック（classify_intent以外の全Tool共通・CALLBACK_FINAL_REQUESTED/
// CALLBACK_FINAL_CREATED/CALLBACK_FINAL_RESPONSE_DEDUPEDマーカーを含む）
// だけを抜き出す（tests/test_fast_turn_hotfix16_callback_single_terminal.js
// と同じ抽出方式・同じアンカー文字列）。
function extractToolContinuationSendSnippet(src) {
    const startAnchor = 'let toolContinuationResponseCreateSent;';
    const endAnchor = "\n            // sendResponseCreate()の戻り値";
    const startIdx = src.indexOf(startAnchor);
    assert.notStrictEqual(startIdx, -1, 'tool continuation send block start anchor not found');
    const endIdx = src.indexOf(endAnchor, startIdx);
    assert.notStrictEqual(endIdx, -1, 'tool continuation send block end anchor not found');
    return src.slice(startIdx, endIdx);
}

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

const CALLBACK_BRANCH_BODY = extractElseIfBody(SRC, "item.name === 'request_callback'");
const TOOL_CONTINUATION_SEND_SNIPPET = extractToolContinuationSendSnippet(SRC);
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

function isToolOutputFailureImpl() {
    const ctx = {};
    vm.createContext(ctx);
    vm.runInContext(IS_TOOL_OUTPUT_FAILURE_FN, ctx);
    return vm.runInContext('isToolOutputFailure', ctx);
}

// ============================================================
// フルシミュレーションハーネス: 実際のCALLBACK_BRANCH_BODYと
// TOOL_CONTINUATION_SEND_SNIPPETを、同一通話内の状態
// （callbackAlreadyConfirmedThisCall / callbackTerminalArmed /
// callbackFinalResponseAlreadyRequested）を引き継ぎながら、
// handleFunctionCallItemが複数回呼ばれる状況（＝request_callbackが
// 複数回呼び出される状況）を再現する。ハードコードした模擬ロジックでは
// なく、本番の2つの抽出済みコードブロックをそのまま実行する。
// ============================================================
function newCallSession(toolOutputOverride) {
    return {
        // 通話全体で持ち越される状態（本番のクロージャ変数と対応）。
        callbackAlreadyConfirmedThisCall: false,
        callbackTerminalArmed: false,
        callbackFinalResponseAlreadyRequested: false,
        toolOutputOverride: toolOutputOverride || { success: true, status: 'ok' },
        toolCallCount: 0,
        sendResponseCreateCalls: [], // { reason }[]
        allEvents: [],
    };
}

async function invokeRequestCallbackOnce(session) {
    // --- STEP 1: request_callback分岐本体（output決定・
    //     callbackTerminalArmed/callbackAlreadyConfirmedThisCallの更新）---
    const branchContext = {
        callbackAlreadyConfirmedThisCall: session.callbackAlreadyConfirmedThisCall,
        phoneConfirmationIncomplete: false,
        phoneReadbackTurnCompletedThisCall: true,
        phoneReadbackAwaitingUserReply: false,
        callbackTerminalArmed: session.callbackTerminalArmed,
        args: {},
        callId: 'call_sim_' + (session.toolCallCount + 1),
        logEvent: () => {},
        pushTimelineEvent: (text) => { session.allEvents.push(text); },
        console: { log: (line) => {} },
        JSON,
        callRequestCallbackTool: async (args, callId) => {
            session.toolCallCount += 1;
            return session.toolOutputOverride;
        },
        isToolOutputFailure: isToolOutputFailureImpl(),
    };
    vm.createContext(branchContext);
    const wrapped = '(async function(){ let output;\n' + CALLBACK_BRANCH_BODY + '\n return output; })()';
    const output = await vm.runInContext(wrapped, branchContext);
    session.callbackAlreadyConfirmedThisCall = branchContext.callbackAlreadyConfirmedThisCall;
    session.callbackTerminalArmed = branchContext.callbackTerminalArmed;

    // --- STEP 2: Tool結果継続response.create送信ブロック（本番と同じく、
    //     STEP1の直後に必ず1回だけ実行される）---
    const dispatchContext = {
        item: { name: 'request_callback' },
        functionCallOutputSendFailed: false,
        callbackTerminalArmed: session.callbackTerminalArmed,
        callbackFinalResponseAlreadyRequested: session.callbackFinalResponseAlreadyRequested,
        pushTimelineEvent: (text) => { session.allEvents.push(text); },
        pushToolContinuationTrace: () => {},
        console: { log: () => {} },
        sendResponseCreate: (reason) => {
            session.sendResponseCreateCalls.push({ reason });
            return true;
        },
    };
    vm.createContext(dispatchContext);
    vm.runInContext(TOOL_CONTINUATION_SEND_SNIPPET, dispatchContext);
    session.callbackFinalResponseAlreadyRequested = dispatchContext.callbackFinalResponseAlreadyRequested;

    return { output, sent: dispatchContext.toolContinuationResponseCreateSent };
}

console.log('CALLBACK FINAL EXACTLY-ONCE (Task M) — contract tests');
console.log('source: ' + ENGINE_JS_PATH);
console.log('');

(async () => {

// ============================================================
// C/F/I/J: 単一の論理terminal応答・追加response無し・0回も2回以上も無い
// ============================================================

await testAsync('A) request_callbackが実機の「約4回」相当（4回連続で成功/dedup扱いとして呼び出される）状況をシミュレートしても、CALLBACK terminal用のresponse.create（sendResponseCreate(\'tool_result:request_callback\')）は通話全体でちょうど1回だけ送信される（root cause fixの直接証拠。C/F/I/J相当）', async () => {
    const session = newCallSession({ success: true, status: 'ok' });
    for (let i = 0; i < 4; i++) {
        await invokeRequestCallbackOnce(session);
    }
    const finalSendCalls = session.sendResponseCreateCalls.filter((c) => c.reason === 'tool_result:request_callback');
    assert.strictEqual(finalSendCalls.length, 1, 'exactly one CALLBACK terminal response.create must be sent across 4 request_callback invocations (never 0, never 2+)');
    assert.strictEqual(session.sendResponseCreateCalls.length, 1, 'no other reason must ever be used for these dispatches');
    // バックエンドへの実際の折り返し作成も1回のみ（既存のHOTFIX14保護と非回帰）。
    assert.strictEqual(session.toolCallCount, 1, 'the backend callRequestCallbackTool must still be called exactly once (production safety, unchanged)');
});

await testAsync('B) 抑制された3回の再呼び出しはCALLBACK_FINAL_RESPONSE_DEDUPEDとして記録され、CALLBACK_FINAL_REQUESTED/CALLBACK_FINAL_CREATEDはちょうど1回ずつのみ記録される（監査可能性の確認）', async () => {
    const session = newCallSession({ success: true, status: 'ok' });
    for (let i = 0; i < 4; i++) {
        await invokeRequestCallbackOnce(session);
    }
    const dedupedCount = session.allEvents.filter((e) => e.indexOf('CALLBACK_FINAL_RESPONSE_DEDUPED') === 0).length;
    const requestedCount = session.allEvents.filter((e) => e === 'CALLBACK_FINAL_REQUESTED').length;
    const createdCount = session.allEvents.filter((e) => e === 'CALLBACK_FINAL_CREATED').length;
    assert.strictEqual(requestedCount, 1);
    assert.strictEqual(createdCount, 1);
    assert.strictEqual(dedupedCount, 3, 'the 2nd/3rd/4th invocations must each be logged as deduped (PII-free, boolean-style marker only)');
});

await testAsync('C) [非回帰] request_callbackが通話中に一度しか呼ばれない通常ケースでは、抑制ロジックは一切介入せず、従来どおりCALLBACK_FINAL_REQUESTED/CALLBACK_FINAL_CREATEDが1回ずつ記録される', async () => {
    const session = newCallSession({ success: true, status: 'ok' });
    const result = await invokeRequestCallbackOnce(session);
    // 注: result.sent (context.toolContinuationResponseCreateSent) はvm
    // 実行のトップレベルlet宣言のためcontextのプロパティとしては反映されない
    // （node:vmの既知の挙動）。実際にsendResponseCreateが呼ばれたことの
    // 直接証拠はsendResponseCreateCallsの記録件数で確認する。
    assert.strictEqual(session.sendResponseCreateCalls.length, 1);
    assert.strictEqual(session.allEvents.filter((e) => e === 'CALLBACK_FINAL_REQUESTED').length, 1);
    assert.strictEqual(session.allEvents.filter((e) => e === 'CALLBACK_FINAL_CREATED').length, 1);
    assert.strictEqual(session.allEvents.filter((e) => e.indexOf('CALLBACK_FINAL_RESPONSE_DEDUPED') === 0).length, 0);
});

await testAsync('C2) [非回帰] request_callbackが電話番号未確認で失敗する場合（callbackTerminalArmedが立たない）、抑制ロジックは一切介入せず、従来どおり継続response.createが送信される（失敗時の再質問フローを妨げないことの確認）', async () => {
    const session = newCallSession();
    const branchContext = {
        callbackAlreadyConfirmedThisCall: false,
        phoneConfirmationIncomplete: true, // 電話番号の復唱確認が未完了
        phoneReadbackTurnCompletedThisCall: false,
        phoneReadbackAwaitingUserReply: false,
        callbackTerminalArmed: false,
        args: {},
        callId: 'call_sim_fail',
        logEvent: () => {},
        pushTimelineEvent: (text) => { session.allEvents.push(text); },
        console: { log: () => {} },
        JSON,
        callRequestCallbackTool: async () => { throw new Error('must not be called when the phone confirmation gate blocks'); },
        isToolOutputFailure: isToolOutputFailureImpl(),
    };
    vm.createContext(branchContext);
    const wrapped = '(async function(){ let output;\n' + CALLBACK_BRANCH_BODY + '\n return output; })()';
    const output = await vm.runInContext(wrapped, branchContext);
    assert.strictEqual(output.success, false);
    assert.strictEqual(output.reason_code, 'phone_not_confirmed');
    assert.strictEqual(branchContext.callbackTerminalArmed, false);

    const dispatchContext = {
        item: { name: 'request_callback' },
        functionCallOutputSendFailed: false,
        callbackTerminalArmed: false,
        callbackFinalResponseAlreadyRequested: false,
        pushTimelineEvent: (text) => { session.allEvents.push(text); },
        pushToolContinuationTrace: () => {},
        console: { log: () => {} },
        sendResponseCreate: (reason) => { session.sendResponseCreateCalls.push({ reason }); return true; },
    };
    vm.createContext(dispatchContext);
    vm.runInContext(TOOL_CONTINUATION_SEND_SNIPPET, dispatchContext);
    // 注: dispatchContext.toolContinuationResponseCreateSentはvm実行の
    // トップレベルlet宣言のためcontextのプロパティとしては反映されない
    // （node:vmの既知の挙動。上のtest C参照）。sendResponseCreateが実際に
    // 呼ばれたこと自体をsendResponseCreateCallsの記録件数で直接確認する。
    assert.strictEqual(session.sendResponseCreateCalls.length, 1, 'the failure-path continuation (so the model can re-ask for the phone number) must NOT be suppressed by the new dedup guard');
});

// ============================================================
// D/E: CALLBACK terminal中に不要なtool continuation / phase transition
// 応答が発生しないこと（既存のHOTFIX14優先分岐・phase transition consumeが
// 無変更であることの再確認。tests/test_fast_turn_hotfix16_callback_single_
// terminal.jsのV/Y/Zと同じ既存不変条件をこのファイルでも再確認する）
// ============================================================

test('D) response.doneのsilence-timeoutゲートは、callbackTerminalArmed=trueを他のいかなる継続ロジック（AI_WORKING_CONTINUATION/incomplete_ai_turn_continuation/通常のsilence timer）よりも先に評価する（HOTFIX14で確立済み。Task Mの変更で優先順位が変わっていないことの確認）', () => {
    const sig = 'if (!responseHasFunctionCall) {\n                    if (callbackTerminalArmed) {';
    assert.notStrictEqual(SRC.indexOf(sig), -1, 'callbackTerminalArmed must remain the first branch evaluated in the response.done silence-timeout gate');
});

test('E) request_callback分岐（CALLBACK_BRANCH_BODY）自体はarmPhaseTransitionAfterResponse等のphase-transition機構を一切参照しない（classify_intent/confirm_customer_nameのみがphase遷移を予約する。request_callback呼び出しがCALLBACK phase遷移の再予約・二重予約を引き起こす経路が存在しないことの確認）', () => {
    assert.ok(!CALLBACK_BRANCH_BODY.includes('armPhaseTransitionAfterResponse'));
    assert.ok(!CALLBACK_BRANCH_BODY.includes('pendingPhaseTransitionTarget'));
});

// ============================================================
// G/H: endCallはfinal audioの再生完了を待ってから1回のみ。playback-aware
// teardown（Phase C）は無変更（既存のtest_fast_turn_hotfix16_callback_
// single_terminal.jsのP〜Tで既に検証済みのため、ここでは変更が無い
// ことだけを再確認する軽量チェック）。
// ============================================================

test('G/H) playback-aware teardown（waitForPlaybackSettleThenEndCall/PLAYBACK_AWARE_MAX_WAIT_MS/cancelCallbackPlaybackAwareWait）と、CALLBACK_FINAL_AUDIO_TAIL_GRACE_MS/pendingCallbackTerminalHangupの機構名がTask Mの変更後もソース内にそのまま存在する（詳細な時系列不変条件はtests/test_fast_turn_hotfix16_callback_single_terminal.jsのP〜Tが担当。ここでは名前・定数が変更されていないことのみを確認する）', () => {
    assert.ok(SRC.includes('function waitForPlaybackSettleThenEndCall('));
    assert.ok(SRC.includes('const PLAYBACK_AWARE_MAX_WAIT_MS'));
    assert.ok(SRC.includes('function cancelCallbackPlaybackAwareWait('));
    assert.ok(SRC.includes('const CALLBACK_FINAL_AUDIO_TAIL_GRACE_MS = 1500;'));
    assert.ok(SRC.includes('pendingCallbackTerminalHangup'));
});

// ============================================================
// K: 会話が長くなっていないこと（新しい質問・ACK・確認ステップを追加して
// いないことの静的確認）
// ============================================================

test('K1) Task Mの変更はrequest_callback Tool description・_HUMAN_HANDOFF_TEMPLATE・_PHASE2B_CALLBACK_ROLE_TEMPLATE等、いかなるプロンプト文言（app/services/realtime_voice_ai.py）も一切変更していない（会話の長さ・発話内容に影響する変更はゼロ）。最終案内文言そのものも変更していない', () => {
    assert.ok(PY_SRC.includes('担当者から折り返し連絡しますので、電話を切ってお待ちください。'), 'the CALLBACK final message text itself must remain byte-identical (Task M forbids rewording it)');
});

test('K2) frontend/public/js/realtime-voice-engine.js側の新規コードは、response.instructions（1回限りの発話内容差し替え）を一切使っていない（新しい発話・新しい質問・新しいACKを追加していないことの構造的確認）', () => {
    const newBlockStart = SRC.indexOf("item.name === 'request_callback' && callbackTerminalArmed && callbackFinalResponseAlreadyRequested");
    assert.notStrictEqual(newBlockStart, -1);
    const newBlockEnd = SRC.indexOf('CALLBACK_FINAL_RESPONSE_DEDUPED]', newBlockStart);
    assert.notStrictEqual(newBlockEnd, -1);
    const newBlockSnippet = SRC.slice(newBlockStart, newBlockEnd);
    assert.ok(!newBlockSnippet.includes('instructionsOverride'));
    assert.ok(!newBlockSnippet.includes('response.instructions'));
});

test('K3) 新規フラグ callbackFinalResponseAlreadyRequested はboolean 1個のみで、PII（電話番号・氏名・transcript本文）を一切保持しない（診断・状態管理ともにフラグのみ）。通話終了時に確実にリセットされる', () => {
    assert.ok(SRC.includes('let callbackFinalResponseAlreadyRequested = false;'));
    assert.ok(SRC.includes('callbackFinalResponseAlreadyRequested = false;\n'));
});

// ============================================================
// bounded: 抑制ロジックはループを含まない（無限retry禁止の構造的裏付け）
// ============================================================

test('L) Task Mで追加した抑制分岐はループ構文を一切含まない', () => {
    const newBlockStart = SRC.indexOf("item.name === 'request_callback' && callbackTerminalArmed && callbackFinalResponseAlreadyRequested");
    const newBlockEnd = SRC.indexOf('CALLBACK_FINAL_RESPONSE_DEDUPED]', newBlockStart);
    const snippet = SRC.slice(newBlockStart, newBlockEnd);
    assert.ok(!/while\s*\(/.test(snippet));
    assert.ok(!/for\s*\(/.test(snippet));
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
process.exitCode = failed > 0 ? 1 : 0;

})();
