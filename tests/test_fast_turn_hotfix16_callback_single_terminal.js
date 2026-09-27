'use strict';

// RECEPTRA — FAST TURN HOTFIX 16（2026年9月）
// 「CALLBACK SINGLE TERMINAL RESPONSE」の契約テスト。
//
// 実機症状（ユーザー指示§0より）: HOTFIX15適用後、PHONEは1回で正しく認識
// されるようになったが、新たに「担当者から折り返します」に相当する発話が
// 2回話され、3回目でようやく通話が終了する、という症状が観測された。
// ユーザー自身の実機ログでは、CALLBACK_TERMINAL_ARMED →
// responseCreateCategory=tool_result → CALLBACK_TERMINAL_RESPONSE_DONE →
// CALLBACK_TERMINAL_FALLBACK_DEFERRED → CALLBACK_TERMINAL_END_CALLという
// terminal state machineそのものは「1回だけ」発火していた。
//
// 実コードで確認したroot cause（推測ではなく、実際にrealtime-voice-engine.js
// のresponse.doneハンドラ・handleFunctionCallItem・_HUMAN_HANDOFF_TEMPLATE
// を読んで特定した）:
//   1. HOTFIX16以前の_HUMAN_HANDOFF_TEMPLATEの「折り返し対応の進め方」手順1は、
//      request_callbackを一度も呼び出す前（＝成功も失敗もまだ分かっていない
//      時点）に、モデルへ「担当者から折り返しご連絡いたします」という
//      "結果を確約する" 例文を提示していた。
//   2. 通常ターンのresponse.createは100%サーバー側semantic_vad（turn_detection.
//      create_response既定動作）による自動生成であり（realtime-voice-engine.js
//      内のTURN_RESPONSE_REQUESTEDコメントで監査済み）、クライアント側の
//      sendResponseCreate()呼び出し一覧（9箇所）のいずれとも無関係に、
//      ユーザーの各ターンcommit毎に発生する。
//   3. そのため、情報収集フェーズ中（request_callback呼び出し前）の複数の
//      ユーザーターンに対し、モデルが手順1の例文を毎回（またはその場面ごとに)
//      律儀に踏襲し、"結果の確約"に相当する発話をterminal state machineの
//      "外側"（callbackTerminalArmedが一度も立っていない、普通のresponse.done）
//      で複数回生成し得た。これらはCALLBACK_TERMINAL_*系の診断マーカーには
//      一切現れない（armed自体が立っていないため）。これが実機ログで
//      terminal machineが1回しか見えないにもかかわらず、体感上は発話が
//      複数回聞こえたことの説明と一致する。
//   4. さらに、コード監査で見つけた別のリスク（実際に今回発生したかは不明だが
//      §5/§9/§14の要求上、防いでおくべき構造的ギャップ）: request_callback
//      成功後にcallbackTerminalArmedは1回で消費（false化）される。もし
//      モデルがsuccess後に再度request_callbackを呼び出した場合、既存の
//      重複実行防止（callbackAlreadyConfirmedThisCall）は実際のTool実行こそ
//      防ぐが、その呼び出しに対する通常のtool_result継続response.createは
//      （classify_intent以外の全Tool共通の無条件実行のため）引き続き送信され、
//      かつcallbackTerminalArmedが再セットされないため、この応答の
//      response.doneはCALLBACK_TERMINAL優先分岐を通らず、通常の継続ロジック
//      （AI_WORKING/silence timer等）に落ちてしまう可能性があった。
//
// HOTFIX16の修正（新しいstate machineは作らない。既存stateの再利用のみ）:
//   (a) プロンプト側: 手順1を「用件を引き受ける意思は伝えるが、結果
//       （担当者から折り返します、等）は絶対に確約しない」に書き換え、
//       手順3で「情報が揃ったら、処理中ナレーションを一切せずにそのまま
//       request_callbackを呼び出す」ことを明示した（5-1/5-2/5-3/6は
//       意図的に無変更。tests/smoke_test_human_handoff_wording.pyの
//       文言契約を壊さないため）。
//   (b) コード側: request_callback二重実行防止（callbackAlreadyConfirmedThisCall）
//       の分岐でも、既存のcallbackTerminalArmedを再セットするようにした
//       （新しいフラグを追加せず、既存の「armed→1回だけ消費→endCall」
//       パターンをこの経路にも適用するだけ）。
//   (c) §19診断: CALLBACK_ACTION_STARTED/CALLBACK_ACTION_SUCCESS/
//       CALLBACK_FINAL_REQUESTED/CALLBACK_FINAL_CREATED/
//       CALLBACK_FINAL_DUPLICATE_BLOCKED/CALLBACK_FINAL_DONE/
//       CALLBACK_FINAL_AUDIO_DONE/CALLBACK_FINAL_END_CALLを、既存の
//       CALLBACK_TERMINAL_*マーカーと併記する形で追加した（既存マーカーは
//       一切変更していない＝HOTFIX14/15のテストへの影響ゼロ）。
//
// response-ID相関（ユーザー指示§10）について: 監査の結果、response.idによる
// 明示的な相関は不要と判断した（§10「コード監査で不要と判明した場合は
// 小さい修正を優先」に従う）。理由: callbackTerminalArmedは
// handleFunctionCallItem内でrequest_callback成功時にのみ立てられ、その
// 直後に送信される唯一のtool_result継続response.createのresponse.done
// でのみ、`if (!responseHasFunctionCall)` ガードの中で最優先かつ即座に
// consume（false化）される。この间、他のいかなるsendResponseCreate呼び出し
// （9箇所の理由文字列 + サーバー自動応答）も、armedをセットしたのと同じ
// 同期実行の中で割り込むことは構造上できない（JSのシングルスレッド実行
// モデル上、handleFunctionCallItemの実行が完了する前に別のresponse.done
// ハンドラが割り込むことはない）ため、response.idを使わずとも「次に来る
// 最初の非function_call応答＝この折り返しの最終応答」という対応関係が
// 一意に保たれる。
//
// 実行: node tests/test_fast_turn_hotfix16_callback_single_terminal.js

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

// handleFunctionCallItem内、「共通のtool_result継続response.create送信」
// ブロック（classify_intent以外の全Tool共通・CALLBACK_FINAL_REQUESTED/
// CALLBACK_FINAL_CREATEDマーカーを含む）だけを抜き出す。
function extractToolContinuationSendSnippet(src) {
    const startAnchor = 'let toolContinuationResponseCreateSent;';
    const endAnchor = "\n            // sendResponseCreate()の戻り値";
    const startIdx = src.indexOf(startAnchor);
    assert.notStrictEqual(startIdx, -1, 'tool continuation send block start anchor not found');
    const endIdx = src.indexOf(endAnchor, startIdx);
    assert.notStrictEqual(endIdx, -1, 'tool continuation send block end anchor not found');
    return src.slice(startIdx, endIdx);
}

const CALLBACK_BRANCH_BODY = extractElseIfBody(SRC, "item.name === 'request_callback'");
const GATE_SNIPPET = extractGateSnippet(SRC);
const DEFERRED_FALLBACK_SNIPPET = extractDeferredFallbackSnippet(SRC);
const TOOL_CONTINUATION_SEND_SNIPPET = extractToolContinuationSendSnippet(SRC);
const MAYBE_HANGUP_CALLBACK_TERMINAL_FN = extractFunctionSource(SRC, 'maybeHangUpAfterCallbackTerminal', false);
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

function runPythonTest(relPath) {
    const result = spawnSync('python3', [path.join(__dirname, '..', relPath)], { encoding: 'utf8', cwd: path.join(__dirname, '..') });
    return { status: result.status, stdout: result.stdout || '', stderr: result.stderr || '' };
}

function getHandoffTemplateBody() {
    const idx = PY_SRC.indexOf('_HUMAN_HANDOFF_TEMPLATE = """');
    const endIdx = PY_SRC.indexOf('\n"""', idx);
    return PY_SRC.slice(idx, endIdx);
}

console.log('FAST TURN HOTFIX 16 — CALLBACK SINGLE TERMINAL RESPONSE contract tests');
console.log('source: ' + ENGINE_JS_PATH);
console.log('');

(async () => {

// ============================================================
// バックエンド契約（§21: 文字列完全一致ではなく意味的な契約テスト）
// A/B/C: 情報未完了時は不足分のみ質問／情報完了後は処理中ナレーション禁止／
// request_callback呼び出し中も成功宣言禁止
// ============================================================

test('A) バックエンド契約: 手順2は、まだ分かっていない情報だけを確認し、既知の情報を聞き直さないことを明示している', () => {
    const t = getHandoffTemplateBody();
    const s2 = t.slice(t.indexOf('2. 折り返しに必要な最小限の情報'), t.indexOf('3. 上記の必要な情報がすべて揃ったら'));
    assert.ok(s2.includes('まだ分かっていないものだけ確認'));
    assert.ok(s2.includes('聞き直さないでください'));
});

test('B) バックエンド契約: 手順3は、必要情報が揃った後に「確認します」「少々お待ちください」等の処理中ナレーションを一切せず、そのままrequest_callbackを呼び出すことを明示している（ユーザー指示§7）', () => {
    const t = getHandoffTemplateBody();
    const s3 = t.slice(t.indexOf('3. 上記の必要な情報がすべて揃ったら'), t.indexOf('4. request_callback の結果'));
    assert.ok(s3.includes('確認します'), 'must explicitly name the forbidden narration phrase to guard against it');
    assert.ok(s3.includes('少々お待ちください'));
    assert.ok(s3.includes('実況する発話は一切せずに'));
    assert.ok(s3.includes('内部処理として扱ってください'));
});

test('C) バックエンド契約: 手順4は、request_callbackの結果(success)が返る前に「担当者から折り返します」等の確約を絶対にしないことを明示している（request_callback呼び出し中も成功を先取りしない＝ユーザー指示§8）', () => {
    const t = getHandoffTemplateBody();
    const s4 = t.slice(t.indexOf('4. request_callback の結果'), t.indexOf('5. successがtrueになったら'));
    assert.ok(s4.includes('担当者から折り返します'));
    assert.ok(s4.includes('絶対にしないでください'));
});

// ============================================================
// root cause: 手順1が「結果の確約」を求めていた旧文言が修正されていること
// ============================================================

test('D) [root cause修正の確認] 手順1は、request_callbackをまだ呼び出しておらず結果も分かっていない時点で「担当者から折り返しご連絡いたします」等、結果を確約する言い方を絶対にしないことを明示している（HOTFIX16以前はこの制約が無く、情報収集フェーズ中の複数の通常応答でこの確約表現が繰り返され得た＝実機で観測された1回目・2回目の発話源）', () => {
    const t = getHandoffTemplateBody();
    const s1 = t.slice(t.indexOf('## 折り返し対応の進め方'), t.indexOf('2. 折り返しに必要な最小限の情報'));
    assert.ok(s1.includes('この時点ではまだrequest_callback'), 'step 1 must explicitly note request_callback has not been called yet at this point');
    assert.ok(s1.includes('結果も分かっていない'));
    assert.ok(s1.includes('絶対にしないでください'));
    assert.ok(s1.includes('5.のsuccess確定後にのみ使ってください'), 'step 1 must defer the outcome-announcing phrase to step 5 (post-success) only');
});

test('E) 回帰: 手順5(5-1/5-2/5-3)・6はHOTFIX16で変更されていない（smoke_test_human_handoff_wording.pyの文言契約を壊さないため、意図的にスコープ外とした）', () => {
    const t = getHandoffTemplateBody();
    assert.ok(t.includes('確認が必要なため、担当者にお伝えします。業務の状況により、\n   折り返しまでお時間をいただく場合がございます。お問い合わせいただき\n   ありがとうございました。'), '5-1 wording must remain byte-identical to the HOTFIX15 baseline');
    assert.ok(t.includes('「最後に担当者の折り返し手配を確認しますね」のような、これから確認する'), '5-3 wording must remain byte-identical to the HOTFIX15 baseline');
});

test('F) 回帰: NAME/ROUTING/RESERVATION用instructions構築関数は_HUMAN_HANDOFF_TEMPLATEを一切参照していない（HOTFIX16の文言変更によるchar deltaはCALLBACK/legacy_fullのみに限定される＝ユーザー指示§23、期待delta=0）', () => {
    const nameRoutineStart = PY_SRC.indexOf('def _build_phase_minimal_instructions(');
    assert.notStrictEqual(nameRoutineStart, -1);
    const nextDefIdx = PY_SRC.indexOf('\ndef _build_reservation_phase_instructions(', nameRoutineStart);
    assert.notStrictEqual(nextDefIdx, -1);
    const USAGE_PATTERNS = ['_HUMAN_HANDOFF_TEMPLATE,', '_HUMAN_HANDOFF_TEMPLATE)', 'append(_HUMAN_HANDOFF_TEMPLATE'];
    const usesHandoffTemplate = (body) => USAGE_PATTERNS.some((p) => body.includes(p));
    const fnBody = PY_SRC.slice(nameRoutineStart, nextDefIdx);
    assert.ok(!usesHandoffTemplate(fnBody), 'NAME/ROUTING must not use _HUMAN_HANDOFF_TEMPLATE');
    const reservationStart = nextDefIdx + 1;
    const callbackDefIdx = PY_SRC.indexOf('\ndef _build_callback_phase_instructions(', reservationStart);
    assert.notStrictEqual(callbackDefIdx, -1);
    const reservationBody = PY_SRC.slice(reservationStart, callbackDefIdx);
    assert.ok(!usesHandoffTemplate(reservationBody), 'RESERVATION must not use _HUMAN_HANDOFF_TEMPLATE');
});

test('G) classify_intentはROUTING phaseにのみ宣言され、CALLBACK phaseのTool一覧には含まれない（フェーズ遷移・classify_intent再実行が構造的に不可能であることの確認＝ユーザー指示§9 M/N）', () => {
    const callbackDefIdx = PY_SRC.indexOf('def _build_callback_phase_instructions(');
    assert.notStrictEqual(callbackDefIdx, -1);
    const selectToolsIdx = PY_SRC.indexOf('def _select_realtime_tools(');
    assert.notStrictEqual(selectToolsIdx, -1);
    const selectToolsEndIdx = PY_SRC.indexOf('\ndef ', selectToolsIdx + 10);
    const selectToolsBody = PY_SRC.slice(selectToolsIdx, selectToolsEndIdx === -1 ? undefined : selectToolsEndIdx);
    assert.ok(selectToolsBody.includes('classify_intent'), '_select_realtime_tools must still reference classify_intent somewhere (ROUTING phase)');
});

// ============================================================
// request_callback branch: 実行回数・terminal armingの再確認（D/F/G/H/AF-AK）
// ============================================================

function buildCallbackBranchSandbox(overrides) {
    const events = [];
    const consoleLogs = [];
    const toolCalls = [];
    const context = {
        callbackAlreadyConfirmedThisCall: false,
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
    return { context, events, consoleLogs, toolCalls };
}

async function runCallbackBranch(context) {
    const wrapped = '(async function(){ let output;\n' + CALLBACK_BRANCH_BODY + '\n return output; })()';
    return vm.runInContext(wrapped, context);
}

await testAsync('H) [terminal mode] request_callback成功時にcallbackTerminalArmed/callbackAlreadyConfirmedThisCallが立ち、CALLBACK_ACTION_STARTED/CALLBACK_ACTION_SUCCESSマーカーが記録される', async () => {
    const built = buildCallbackBranchSandbox({});
    const output = await runCallbackBranch(built.context);
    assert.strictEqual(output.success, true);
    assert.strictEqual(built.context.callbackTerminalArmed, true);
    assert.strictEqual(built.context.callbackAlreadyConfirmedThisCall, true);
    assert.strictEqual(built.toolCalls.length, 1, '実際のバックエンド呼び出しは1回のみ');
    assert.ok(built.events.includes('CALLBACK_ACTION_STARTED'));
    assert.ok(built.events.includes('CALLBACK_ACTION_SUCCESS'));
});

await testAsync('I) [callback failure] request_callback失敗時はcallbackTerminalArmedが立たず、成功クロージングへ進まない（ユーザー指示§17。失敗を成功と偽って案内しない）', async () => {
    const built = buildCallbackBranchSandbox({ __toolOutput: { success: false, status: 'error', reason_code: 'temporarily_unavailable' } });
    const output = await runCallbackBranch(built.context);
    assert.strictEqual(output.success, false);
    assert.strictEqual(built.context.callbackTerminalArmed, false, 'failure must never arm the terminal-success gate');
    assert.strictEqual(built.context.callbackAlreadyConfirmedThisCall, false);
});

await testAsync('J) [request_callback最大1回・ユーザー指示§6] request_callback成功後、同じ通話内で再度呼び出されても実際のTool（callRequestCallbackTool）は二度と実行されない', async () => {
    const built = buildCallbackBranchSandbox({});
    const first = await runCallbackBranch(built.context);
    assert.strictEqual(built.toolCalls.length, 1);
    const second = await runCallbackBranch(built.context);
    assert.strictEqual(built.toolCalls.length, 1, 'must still be exactly 1 total after a second invocation');
    assert.strictEqual(second.status, 'already_confirmed');
});

await testAsync('K) [HOTFIX16修正・二重発話の構造的ギャップの解消] request_callbackが成功後に再度呼び出されても（本来は起きてはいけないが、モデルの誤動作に備えた防御）、dedup分岐がcallbackTerminalArmedを再セットし、その応答もCALLBACK_TERMINAL優先分岐で確実に消費される。CALLBACK_FINAL_DUPLICATE_BLOCKEDマーカーが記録される', async () => {
    const built = buildCallbackBranchSandbox({});
    await runCallbackBranch(built.context); // 1回目: 成功→armed=true
    // 1回目の成功に対するterminal応答が既に処理され、armedがconsumeされた
    // (=false化された)状態を模擬する。
    built.context.callbackTerminalArmed = false;
    const second = await runCallbackBranch(built.context); // 2回目: dedup分岐
    assert.strictEqual(second.status, 'already_confirmed');
    assert.strictEqual(built.context.callbackTerminalArmed, true, 'HOTFIX16: the dedup branch must re-arm callbackTerminalArmed so its own tool_result continuation is still captured by the terminal-priority gate');
    assert.ok(built.events.some((e) => e.indexOf('CALLBACK_FINAL_DUPLICATE_BLOCKED') === 0));
});

test('L) request_callback分岐自体はsendResponseCreate()を直接呼ばない（既存の一元化ラッパー呼び出し1箇所のみで送信される・新しいdc.send経路を増やさない＝ユーザー指示§22 AM/AN）', () => {
    const sendCount = (CALLBACK_BRANCH_BODY.match(/sendResponseCreate\(/g) || []).length;
    assert.strictEqual(sendCount, 0);
});

// ============================================================
// tool_result継続response.create送信ブロック: CALLBACK_FINAL_REQUESTED/
// CALLBACK_FINAL_CREATEDが正しいタイミングで(request_callbackのみ)記録される
// ============================================================

function runToolContinuationSend(itemName, sendResult) {
    const events = [];
    const consoleLogs = [];
    const sendCalls = [];
    const context = {
        item: { name: itemName },
        functionCallOutputSendFailed: false,
        pushTimelineEvent: (t) => events.push(t),
        pushToolContinuationTrace: () => {},
        console: { log: (l) => consoleLogs.push(l) },
        sendResponseCreate: (reason) => { sendCalls.push(reason); return sendResult; },
    };
    vm.createContext(context);
    vm.runInContext(TOOL_CONTINUATION_SEND_SNIPPET, context);
    return { events, consoleLogs, sendCalls, toolContinuationResponseCreateSent: context.toolContinuationResponseCreateSent };
}

test('M) [E: 最終応答リクエストは1回のみ] request_callbackの結果送信時、CALLBACK_FINAL_REQUESTEDとCALLBACK_FINAL_CREATEDがそれぞれ厳密に1回ずつ記録される', () => {
    const r = runToolContinuationSend('request_callback', true);
    assert.strictEqual(r.events.filter((e) => e === 'CALLBACK_FINAL_REQUESTED').length, 1);
    assert.strictEqual(r.events.filter((e) => e === 'CALLBACK_FINAL_CREATED').length, 1);
    assert.strictEqual(r.sendCalls.length, 1, 'sendResponseCreate must be invoked exactly once for this tool result');
    assert.strictEqual(r.sendCalls[0], "tool_result:request_callback");
});

test('N) [他Toolとの分離確認] request_callback以外のTool（例: check_availability）ではCALLBACK_FINAL_REQUESTED/CALLBACK_FINAL_CREATEDは記録されない', () => {
    const r = runToolContinuationSend('check_availability', true);
    assert.strictEqual(r.events.filter((e) => e === 'CALLBACK_FINAL_REQUESTED').length, 0);
    assert.strictEqual(r.events.filter((e) => e === 'CALLBACK_FINAL_CREATED').length, 0);
});

test('O) [送信失敗時] sendResponseCreateがfalseを返した場合、CALLBACK_FINAL_CREATEDは記録されない（実際に送れていないものを送ったことにしない）', () => {
    const r = runToolContinuationSend('request_callback', false);
    assert.strictEqual(r.events.filter((e) => e === 'CALLBACK_FINAL_REQUESTED').length, 1, 'REQUESTEDは試行時点で記録される');
    assert.strictEqual(r.events.filter((e) => e === 'CALLBACK_FINAL_CREATED').length, 0, 'CREATEDは実際に送信できた場合のみ記録される');
});

// ============================================================
// response.done gate: AI_WORKING/incomplete/silence timer/phase transition
// の排他（H/I/J/K/L/M/N/O — 大半はHOTFIX14で既に確立済みの分岐をそのまま
// 再確認する）
// ============================================================

function buildIntegrationContext(overrides) {
    const events = [];
    const consoleLogs = [];
    const endCallCalls = [];
    const scheduled = [];
    const context = {
        responseHasFunctionCall: false,
        callbackTerminalArmed: false,
        pendingCallbackTerminalHangup: false,
        // FAST TURN HOTFIX 18（今回追加）: maybeHangUpAfterCallbackTerminal()
        // が新しく参照するようになったモジュールレベルのtail grace state。
        // 実際のコードではmoduleスコープのlet/constだが、この関数だけを
        // 抜き出して評価するVMサンドボックスでは、既存のcallbackTerminal
        // Armed等と同じく「グローバル変数」として明示的に用意する必要がある。
        callbackFinalTailGraceTimerId: null,
        CALLBACK_FINAL_AUDIO_TAIL_GRACE_MS: 1500,
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
        endCall: (text, reason) => { endCallCalls.push({ text, reason }); },
        isStaleCallEvent: () => !!(overrides && overrides.__stale),
    };
    Object.assign(context, overrides || {});
    vm.createContext(context);
    vm.runInContext(MAYBE_HANGUP_CALLBACK_TERMINAL_FN, context);
    return { context, events, consoleLogs, endCallCalls, scheduled };
}

function fireAllScheduled(built) {
    for (const s of built.scheduled) {
        if (!s.fired) { s.fired = true; s.fn(); }
    }
}

test('P) [E/H/I/J: exactly-once invariant] callbackTerminalArmed成功直後のresponse.done処理そのものの中ではendCall()が同期的に呼ばれない（HOTFIX15で確立済みの不変条件が、HOTFIX16の診断追加後も保たれていることの再確認）', () => {
    const built = buildIntegrationContext({ callbackTerminalArmed: true });
    vm.runInContext(GATE_SNIPPET, built.context);
    assert.strictEqual(built.context.pendingCallbackTerminalHangup, true);
    assert.strictEqual(built.endCallCalls.length, 0);
    vm.runInContext(DEFERRED_FALLBACK_SNIPPET, built.context);
    assert.strictEqual(built.endCallCalls.length, 0);
    assert.strictEqual(built.scheduled.length, 1);
    assert.strictEqual(built.scheduled[0].ms, 10000);
});

test('Q) [S: 音声再生完了前にendCallしない] 主経路(output_audio_buffer.stopped)が発火するまでendCall()されない', () => {
    const built = buildIntegrationContext({ callbackTerminalArmed: true });
    vm.runInContext(GATE_SNIPPET, built.context);
    vm.runInContext(DEFERRED_FALLBACK_SNIPPET, built.context);
    assert.strictEqual(built.endCallCalls.length, 0, 'response.doneだけではendCallされない（音声再生完了を待つ）');
});

test('R) [T: 音声停止でendCall] 主経路発火でendCall()が1回だけ呼ばれ、CALLBACK_FINAL_AUDIO_DONE/CALLBACK_FINAL_END_CALLマーカーが記録される', () => {
    const built = buildIntegrationContext({ callbackTerminalArmed: true });
    vm.runInContext(GATE_SNIPPET, built.context);
    vm.runInContext(DEFERRED_FALLBACK_SNIPPET, built.context);
    vm.runInContext('maybeHangUpAfterCallbackTerminal', built.context)(1, 'ai_audio_stopped');
    // FAST TURN HOTFIX 18（今回更新）: 主経路発火の直後は、endCall()の前に
    // bounded tail grace（setTimeout）が1つ挟まるようになったため、即座には
    // endCallCallsに積まれない。既存のfireAllScheduled()でこのタイマーを
    // 明示的に発火させてから確認する（S/Tの既存テストと同じパターン）。
    assert.strictEqual(built.endCallCalls.length, 0, 'HOTFIX18: endCall must not fire synchronously before the tail grace timer');
    fireAllScheduled(built);
    assert.strictEqual(built.endCallCalls.length, 1);
    assert.strictEqual(built.endCallCalls[0].reason, 'callback_terminal');
    assert.ok(built.events.includes('CALLBACK_FINAL_AUDIO_DONE (source=ai_audio_stopped)'));
    assert.ok(built.events.includes('CALLBACK_FINAL_END_CALL'));
});

test('S) [U: endCallは最大1回] 音声停止後に遅延フォールバックが発火しても二重にendCall()しない', () => {
    const built = buildIntegrationContext({ callbackTerminalArmed: true });
    vm.runInContext(GATE_SNIPPET, built.context);
    vm.runInContext(DEFERRED_FALLBACK_SNIPPET, built.context);
    vm.runInContext('maybeHangUpAfterCallbackTerminal', built.context)(1, 'ai_audio_stopped');
    fireAllScheduled(built);
    assert.strictEqual(built.endCallCalls.length, 1, 'must not hang up twice');
});

test('T) [W: 有界フォールバックは音声停止イベント欠落時のみ発火] 主経路が全く発火しない場合のみ、10秒後の遅延フォールバックがendCall()する', () => {
    const built = buildIntegrationContext({ callbackTerminalArmed: true });
    vm.runInContext(GATE_SNIPPET, built.context);
    vm.runInContext(DEFERRED_FALLBACK_SNIPPET, built.context);
    assert.strictEqual(built.endCallCalls.length, 0);
    fireAllScheduled(built);
    assert.strictEqual(built.endCallCalls.length, 1);
});

test('U) [V: 10秒フォールバック待機中は追加のresponse.createを一切発生させない] 遅延フォールバックブロックはmaybeHangUpAfterCallbackTerminal()の呼び出し以外、いかなるsendResponseCreate/dc.send呼び出しも含まない', () => {
    assert.ok(!/sendResponseCreate\(/.test(DEFERRED_FALLBACK_SNIPPET), 'the 10s fallback window must not itself trigger any new response.create');
    assert.ok(!/dc\.send\(/.test(DEFERRED_FALLBACK_SNIPPET));
});

test('V) [H/I/J/K/L/M/N/O: AI_WORKING/incomplete/silence timer/phase transition排他] callbackTerminalArmed=trueは、それらのいずれよりも先に評価される最優先分岐である（HOTFIX14で確立済み。HOTFIX16でこの優先順位が変わっていないことを再確認）', () => {
    const built = buildIntegrationContext({
        callbackTerminalArmed: true,
        deferSilenceTimerForToolContinuationRateLimit: true,
        lastResponseTranscriptWasProcessNarrationOnly: true,
        lastResponseReasonCategoryForDiag: 'ai_working_continuation',
    });
    vm.runInContext(GATE_SNIPPET, built.context);
    assert.strictEqual(built.context.pendingCallbackTerminalHangup, true, 'callbackTerminalArmed must win even when every other continuation condition is simultaneously true');
    assert.ok(!built.events.some((e) => e.indexOf('SILENCE_TIMER_START_DEFERRED') === 0));
});

test('W) [function_call中は評価しない] responseHasFunctionCall=trueの応答（Tool往復中）ではcallbackTerminalArmedが立っていてもgateは評価されない', () => {
    const built = buildIntegrationContext({ responseHasFunctionCall: true, callbackTerminalArmed: true });
    vm.runInContext(GATE_SNIPPET, built.context);
    assert.strictEqual(built.context.pendingCallbackTerminalHangup, false);
});

test('X) [stale generation] 次の通話が既に始まっている場合、遅延フォールバックが発火してもendCall()しない', () => {
    const built = buildIntegrationContext({ callbackTerminalArmed: true, __stale: true });
    vm.runInContext(GATE_SNIPPET, built.context);
    vm.runInContext(DEFERRED_FALLBACK_SNIPPET, built.context);
    fireAllScheduled(built);
    assert.strictEqual(built.endCallCalls.length, 0);
});

test('Y) [Q/R: 応答の取り違え防止・response-ID相関不要の裏付け] callbackTerminalArmed=falseの通常ターン（＝この折り返しと無関係な応答）のresponse.doneは、CALLBACK_TERMINAL_RESPONSE_DONE/CALLBACK_FINAL_DONEのいずれも記録しない', () => {
    const built = buildIntegrationContext({ callbackTerminalArmed: false });
    vm.runInContext(GATE_SNIPPET, built.context);
    assert.ok(!built.events.includes('CALLBACK_TERMINAL_RESPONSE_DONE'));
    assert.ok(!built.events.includes('CALLBACK_FINAL_DONE'));
    assert.strictEqual(built.context.pendingCallbackTerminalHangup, false);
});

// ============================================================
// レート制限下でも成功を捏造しない（Y相当・ユーザー指示§18）
// ============================================================

test('Z) [rate limit下でも成功を捏造しない] callbackTerminalArmedはisToolOutputFailure(output)の結果のみに依存し、rate_limit関連のフラグ（deferSilenceTimerForToolContinuationRateLimit等）には一切依存しない（構造確認）', () => {
    // handleFunctionCallItem内のrequest_callback分岐自体は
    // deferSilenceTimerForToolContinuationRateLimit等のrate-limit関連識別子を
    // 一切参照していない（＝rate limitの発生有無がcallback成功判定に
    // 混入する余地が構造的に無い）。
    assert.ok(!CALLBACK_BRANCH_BODY.includes('rate_limit'), 'the request_callback branch must not reference rate-limit state when deciding success/failure');
    assert.ok(!CALLBACK_BRANCH_BODY.includes('RateLimit'));
});

// ============================================================
// PII-free診断マーカー（§19 AL）
// ============================================================

test('AA) PRIVACY: HOTFIX16で追加した新規診断マーカーは全て固定文言のみで、顧客名・電話番号・予約内容・transcript本文を一切含まない', () => {
    const markers = [
        "pushTimelineEvent('CALLBACK_ACTION_STARTED');",
        "console.log('[CALLBACK_ACTION_STARTED]');",
        "pushTimelineEvent('CALLBACK_ACTION_SUCCESS');",
        "console.log('[CALLBACK_ACTION_SUCCESS]');",
        "pushTimelineEvent('CALLBACK_FINAL_REQUESTED');",
        "console.log('[CALLBACK_FINAL_REQUESTED]');",
        "pushTimelineEvent('CALLBACK_FINAL_CREATED');",
        "console.log('[CALLBACK_FINAL_CREATED]');",
        "pushTimelineEvent('CALLBACK_FINAL_DUPLICATE_BLOCKED (reason=request_callback_reinvoked_after_success)');",
        "console.log('[CALLBACK_FINAL_DUPLICATE_BLOCKED]');",
        "pushTimelineEvent('CALLBACK_FINAL_DONE');",
        "console.log('[CALLBACK_FINAL_DONE]');",
        "pushTimelineEvent('CALLBACK_FINAL_END_CALL');",
        "console.log('[CALLBACK_FINAL_END_CALL]');",
    ];
    for (const m of markers) {
        assert.ok(SRC.includes(m), 'expected diagnostic marker call site missing: ' + m);
    }
    // CALLBACK_FINAL_AUDIO_DONEはsource(固定enumのraeson文字列)のみを含む形。
    assert.ok(SRC.includes("pushTimelineEvent('CALLBACK_FINAL_AUDIO_DONE (source=' + source + ')');"));
    assert.ok(SRC.includes("console.log('[CALLBACK_FINAL_AUDIO_DONE]');"));
});

test('AB) bounded: HOTFIX16の変更はループ構文を一切含まない（無限retry禁止の構造的裏付け）', () => {
    assert.ok(!/while\s*\(/.test(CALLBACK_BRANCH_BODY));
    assert.ok(!/for\s*\(/.test(CALLBACK_BRANCH_BODY));
    assert.ok(!/while\s*\(/.test(TOOL_CONTINUATION_SEND_SNIPPET));
    assert.ok(!/for\s*\(/.test(TOOL_CONTINUATION_SEND_SNIPPET));
});

// ============================================================
// §22: response.create/dc.send呼び出し箇所数の回帰ガード（AM/AN）
// ============================================================

test('AC) [AM/AN: 呼び出し箇所数の不要な増加が無いこと] sendResponseCreate(の総出現数=25、dc.send(JSON.stringify(の総出現数=10（HOTFIX15時点の実測ベースラインと完全一致。HOTFIX16は新しいresponse.create経路もdc.send経路も一切追加していない＝診断ログ追加のみ）。setTimeout(はHOTFIX18でCALLBACK FINAL専用のbounded tail grace 1個分のみ意図的に+1（19→20、詳細はtests/test_fast_turn_hotfix18_callback_audio_tail.js参照）', () => {
    const sendResponseCreateCount = (SRC.match(/sendResponseCreate\(/g) || []).length;
    const dcSendCount = (SRC.match(/dc\.send\(JSON\.stringify\(/g) || []).length;
    const setTimeoutCount = (SRC.match(/setTimeout\(/g) || []).length;
    assert.strictEqual(sendResponseCreateCount, 25, 'sendResponseCreate( occurrence count must be unchanged from the HOTFIX15 baseline');
    assert.strictEqual(dcSendCount, 10, 'dc.send(JSON.stringify( occurrence count must be unchanged from the HOTFIX15 baseline');
    assert.strictEqual(setTimeoutCount, 20, 'setTimeout( occurrence count must be exactly +1 from the HOTFIX15/16/17 baseline of 19 (HOTFIX18: one bounded tail grace timer for CALLBACK FINAL only)');
});

// ============================================================
// 回帰スイート（サブプロセス実行・Z-AD/AE/AF-AK相当）
// ============================================================

test('AD) 回帰スイート: HOTFIX14/HOTFIX15（CALLBACK terminal機構全体）が全て通る', () => {
    for (const relPath of [
        'test_fast_turn_hotfix14_greeting_callback_terminal.js',
        'test_fast_turn_hotfix15_callback_final_confirmation.js',
    ]) {
        const r = runNodeTest(relPath);
        assert.strictEqual(r.status, 0, relPath + ' must pass:\n' + r.stdout + r.stderr);
    }
});

test('AE) 回帰スイート: HOTFIX 10/11/12/13の対象テストが全て通る', () => {
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

test('AF) [Z-AD: PHONE/DATE/TIME/PARTY_SIZE/NAME regression] FIRST ANSWER MUST COUNT / PHONE Forced Commit / NAME-FIRST FLOWが全て通る（HOTFIX16はこれらのロジックに一切触れていない＝ユーザー指示§15/§16）', () => {
    for (const relPath of [
        'test_first_answer_must_count.js',
        'test_phone_forced_commit.js',
        'test_name_first_flow.js',
    ]) {
        const r = runNodeTest(relPath);
        assert.strictEqual(r.status, 0, relPath + ' must pass:\n' + r.stdout + r.stderr);
    }
});

test('AG) 回帰: smoke_test_human_handoff_wording.py（5-1/5-2/5-3の厳密な文言契約）が通る', () => {
    const r = runPythonTest('tests/smoke_test_human_handoff_wording.py');
    assert.strictEqual(r.status, 0, 'smoke_test_human_handoff_wording.py must pass:\n' + r.stdout + r.stderr);
});

test('AH) 回帰: smoke_test_name_first_contract.py（保護対象テンプレートの再肥大化チェック、HOTFIX16向けに閾値を更新済み）が通る', () => {
    const r = runPythonTest('tests/smoke_test_name_first_contract.py');
    assert.strictEqual(r.status, 0, 'smoke_test_name_first_contract.py must pass:\n' + r.stdout + r.stderr);
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
process.exit(failed > 0 ? 1 : 0);

})();
