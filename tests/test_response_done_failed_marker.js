'use strict';

/**
 * RECEPTRA — FAST TURN HOTFIX 5
 * RESPONSE FAILED ROOT-CAUSE CAPTURE
 *
 * 背景: 実機で、AI「承知しました。12時のお時間ですね。空き状況を確認します」
 * まで発話した後に続きが停止した事例が再訂正された。今回はSafari Consoleで
 * "WATCHDOG" を検索しても何も見つからなかった一方、通常Console上に
 * 「[Phase2.6 usage] status: "failed"」が複数回、response_count:3 が
 * 観測された。
 *
 * 監査の結果、以下の2つの重大な事実が判明した:
 *
 * 1. recordUsageEvent()はresponse.statusの文字列（"failed"等）しか記録して
 *    おらず、OpenAI Realtime API公式ドキュメントが定義するresponse.done.
 *    status_details（type/reason/error.type/error.code/error.message）を
 *    完全に捨てていた。
 * 2. pushTimelineEvent()・logEvent()はどちらもDOM（#diagTimeline・
 *    recentEvents配列・eventLogEl）にしか書き込んでおらず、ブラウザの
 *    開発者Console（console.log）には一切出力していなかった。したがって
 *    実機で「Console」を"WATCHDOG"で検索して何も見つからなかったことは、
 *    watchdogが発火しなかった証拠には全くならない（そもそも発火しても
 *    Consoleには出ない設計だった）。
 *
 * 本パッチは、response.doneのstatus=failed/incompleteの場合に限り、
 * status_details/error（実際にAPI schemaへ存在するフィールドのみ）を
 * 追加記録し、かつConsoleでも検索できるようconsole.logへ明示的に出す
 * 診断専用マーカー（RESPONSE_DONE_FAILED）を追加した。あわせて、既存の
 * 2つのWATCHDOGと「response.create while active」警告も同じ理由で
 * console.logを追加した（いずれも診断ログの追加のみで、送信・リトライ・
 * タイムアウト等の挙動は一切変更していない）。
 *
 * 本テストは、実際のRESPONSE_DONE_FAILEDブロックの実ソースをvmサンドボックスで
 * 実行し、以下を確認する:
 *   A. completed responseではマーカーが一切記録されないこと
 *   B. failed responseでは診断専用の1行がpushTimelineEvent・console.logの
 *      両方に、正しいstatus_details/errorフィールドと共に1回だけ記録される
 *      こと
 *   C. response_idの末尾8文字で正しく相関できること
 *   D. tool continuation中であればcall_idの末尾8文字で正しく相関できること
 *   E. status_details自体が欠落していてもcrashせず、フィールドは"null"に
 *      なること
 *   F. 未知・不正な形のschema（response自体が無い等）でもcrashしないこと
 *   G. PII・会話内容・transcriptがpushTimelineEvent/console.logへ一切
 *      渡されないこと
 *   H. 既存のtoolContinuationTraceActive等の状態を一切書き換えない
 *      （読み取り専用）こと
 *   I. dc.send()呼び出し箇所数が変化していないこと（診断ログの追加のみ）
 *   J. 既存のTOOL/PLAIN WATCHDOGおよびRESPONSE_CREATE_WHILE_ACTIVE警告が
 *      引き続き存在し、console.logが追加されていること
 *
 * 実行: node tests/test_response_done_failed_marker.js
 */

const fs = require('fs');
const path = require('path');
const assert = require('assert');
const vm = require('vm');

const SRC_PATH = path.join(__dirname, '..', 'frontend', 'public', 'js', 'realtime-voice-engine.js');
const SRC = fs.readFileSync(SRC_PATH, 'utf8');

let passed = 0, failed = 0;
function test(name, fn) {
    try {
        fn();
        passed++;
        console.log('  ok - ' + name);
    } catch (e) {
        failed++;
        console.log('  FAIL - ' + name);
        console.log('    ' + (e && e.stack ? e.stack.split('\n').slice(0, 6).join('\n    ') : e));
    }
}

console.log('FAST TURN HOTFIX 5 — RESPONSE_DONE_FAILED diagnostic marker tests');
console.log('source: ' + SRC_PATH);
console.log('');

function extractBlock(src, signature) {
    const idx = src.indexOf(signature);
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

// 実ソースのif-blockそのもの（respStatusは事前に変数として存在する前提で
// 実コードのままの形）を抽出する。実コード中の一意な開始シグネチャで固定する。
const FAILED_BLOCK_SRC = extractBlock(
    SRC,
    "if (respStatus === 'failed' || respStatus === 'incomplete') {"
);

function run(overrides) {
    const consoleLogs = [];
    const timelineEvents = [];
    const sandbox = {
        respStatus: null,
        msg: { response: null },
        toolContinuationTraceActive: false,
        toolContinuationTraceCallId: null,
        turnLatencyTraceId: null,
        lastResponseReasonCategoryForDiag: 'unknown',
        debugMode: false,
        pushTimelineEvent: (t) => timelineEvents.push(t),
        console: { log: (...args) => consoleLogs.push(args) },
    };
    Object.assign(sandbox, overrides);
    vm.createContext(sandbox);
    vm.runInContext(FAILED_BLOCK_SRC, sandbox);
    return { timelineEvents, consoleLogs, sandbox };
}

// =======================================================================
// A. completed response
// =======================================================================
test('A) status=completedではRESPONSE_DONE_FAILEDは一切記録されない（timeline・console両方）', () => {
    const { timelineEvents, consoleLogs } = run({
        respStatus: 'completed',
        msg: { response: { id: 'resp_completed_1', status: 'completed' } },
    });
    assert.strictEqual(timelineEvents.length, 0);
    assert.strictEqual(consoleLogs.length, 0);
});

// =======================================================================
// B. failed response — full status_details
// =======================================================================
test('B) status=failedかつstatus_details/errorが揃っている場合、timeline・console双方に1回だけ正しい内容で記録される', () => {
    const { timelineEvents, consoleLogs } = run({
        respStatus: 'failed',
        msg: {
            response: {
                id: 'resp_abcdef123456',
                status: 'failed',
                status_details: {
                    type: 'failed',
                    error: { type: 'server_error', code: 'internal_error', message: 'something went wrong (should never be logged outside debugMode)' },
                },
            },
        },
    });

    const lines = timelineEvents.filter(t => t.includes('RESPONSE_DONE_FAILED'));
    assert.strictEqual(lines.length, 1, 'exactly one RESPONSE_DONE_FAILED line must be pushed to the timeline');
    assert.ok(lines[0].includes('status=failed'));
    assert.ok(lines[0].includes('status_details_type=failed'));
    assert.ok(lines[0].includes('error_type=server_error'));
    assert.ok(lines[0].includes('error_code=internal_error'));
    assert.ok(!lines[0].includes('something went wrong'), 'error.message must never appear in the timeline line');

    const consoleLines = consoleLogs.filter(args => String(args[0]).includes('RESPONSE_DONE_FAILED'));
    assert.strictEqual(consoleLines.length, 1, 'exactly one console.log call for RESPONSE_DONE_FAILED (Safari Console-searchable)');
    assert.ok(!String(consoleLines[0][0]).includes('something went wrong'));
});

test('B2) status=incompleteでも同様にRESPONSE_DONE_FAILEDが記録される（reasonフィールド）', () => {
    const { timelineEvents } = run({
        respStatus: 'incomplete',
        msg: {
            response: {
                id: 'resp_incomplete_1',
                status: 'incomplete',
                status_details: { type: 'incomplete', reason: 'max_output_tokens' },
            },
        },
    });
    const lines = timelineEvents.filter(t => t.includes('RESPONSE_DONE_FAILED'));
    assert.strictEqual(lines.length, 1);
    assert.ok(lines[0].includes('status=incomplete'));
    assert.ok(lines[0].includes('status_details_reason=max_output_tokens'));
});

// =======================================================================
// C. response_id correlation
// =======================================================================
test('C) response.idの末尾8文字がresponse_id_tailとして正しく記録される', () => {
    const { timelineEvents } = run({
        respStatus: 'failed',
        msg: { response: { id: 'resp_0123456789abcdef', status: 'failed' } },
    });
    const line = timelineEvents.find(t => t.includes('RESPONSE_DONE_FAILED'));
    assert.ok(line.includes('response_id_tail=' + '0123456789abcdef'.slice(-8)));
});

// =======================================================================
// D. call_id correlation (tool continuation)
// =======================================================================
test('D) tool continuation中であれば、toolContinuationTraceCallIdの末尾8文字でcall_id相関できる', () => {
    const { timelineEvents } = run({
        respStatus: 'failed',
        msg: { response: { id: 'resp_x', status: 'failed' } },
        toolContinuationTraceActive: true,
        toolContinuationTraceCallId: 'call_9988776655443322',
    });
    const line = timelineEvents.find(t => t.includes('RESPONSE_DONE_FAILED'));
    assert.ok(line.includes('toolContinuationActive=true'));
    assert.ok(line.includes('toolCallIdTail=' + 'call_9988776655443322'.slice(-8)));
});

test('D2) tool continuation中でなければtoolCallIdTail=nullかつtoolContinuationActive=falseとなる', () => {
    const { timelineEvents } = run({
        respStatus: 'failed',
        msg: { response: { id: 'resp_y', status: 'failed' } },
    });
    const line = timelineEvents.find(t => t.includes('RESPONSE_DONE_FAILED'));
    assert.ok(line.includes('toolContinuationActive=false'));
    assert.ok(line.includes('toolCallIdTail=null'));
});

// =======================================================================
// E. status_details missing entirely
// =======================================================================
test('E) status_detailsが存在しない場合でもcrashせず、各フィールドはnullとして記録される', () => {
    const { timelineEvents } = run({
        respStatus: 'failed',
        msg: { response: { id: 'resp_no_details', status: 'failed' } },
    });
    const line = timelineEvents.find(t => t.includes('RESPONSE_DONE_FAILED'));
    assert.ok(line);
    assert.ok(line.includes('status_details_type=null'));
    assert.ok(line.includes('status_details_reason=null'));
    assert.ok(line.includes('error_type=null'));
    assert.ok(line.includes('error_code=null'));
});

// =======================================================================
// F. unknown / malformed schema
// =======================================================================
test('F) msg.response自体が存在しない不正な形でもcrashしない', () => {
    assert.doesNotThrow(() => {
        run({ respStatus: 'failed', msg: {} });
    });
});

test('F2) status_detailsが期待しない型（文字列）でもcrashしない', () => {
    assert.doesNotThrow(() => {
        run({
            respStatus: 'failed',
            msg: { response: { id: 'resp_weird', status: 'failed', status_details: 'unexpected_string_shape' } },
        });
    });
});

test('F3) errorが期待しない型（文字列）でもcrashしない', () => {
    assert.doesNotThrow(() => {
        run({
            respStatus: 'failed',
            msg: { response: { id: 'resp_weird2', status: 'failed', status_details: { error: 'unexpected_string_error' } } },
        });
    });
});

// =======================================================================
// G. PII / transcript never passed to logger
// =======================================================================
test('G) response.output内にtranscript相当のPIIが含まれていても、timeline/consoleへは一切渡らない', () => {
    const { timelineEvents, consoleLogs } = run({
        respStatus: 'failed',
        msg: {
            response: {
                id: 'resp_pii',
                status: 'failed',
                status_details: { error: { type: 'server_error', code: 'x', message: '電話番号090-1234-5678の田中様の予約' } },
                output: [{ type: 'message', content: [{ transcript: '電話番号090-1234-5678の田中様の予約' }] }],
            },
        },
    });
    const allTimelineText = timelineEvents.join(' ');
    const allConsoleText = consoleLogs.map(args => args.join(' ')).join(' ');
    assert.ok(!allTimelineText.includes('090-1234-5678'));
    assert.ok(!allTimelineText.includes('田中'));
    assert.ok(!allConsoleText.includes('090-1234-5678'));
    assert.ok(!allConsoleText.includes('田中'));
});

test('G2) debugMode=falseの場合、error.messageはconsole.logへも一切出力されない', () => {
    const { consoleLogs } = run({
        respStatus: 'failed',
        debugMode: false,
        msg: { response: { id: 'resp_dbg_off', status: 'failed', status_details: { error: { type: 'server_error', message: 'SECRET_MESSAGE_CONTENT' } } } },
    });
    const allConsoleText = consoleLogs.map(args => args.join(' ')).join(' ');
    assert.ok(!allConsoleText.includes('SECRET_MESSAGE_CONTENT'));
});

test('G3) debugMode=trueの場合のみ、error.messageが専用のDEBUG_ONLYラベル付きでconsole.logに出る（timelineには出ない）', () => {
    const { consoleLogs, timelineEvents } = run({
        respStatus: 'failed',
        debugMode: true,
        msg: { response: { id: 'resp_dbg_on', status: 'failed', status_details: { error: { type: 'server_error', message: 'DEBUG_VISIBLE_MESSAGE' } } } },
    });
    const debugLine = consoleLogs.find(args => String(args[0]).includes('RESPONSE_DONE_FAILED_ERROR_MESSAGE_DEBUG_ONLY'));
    assert.ok(debugLine, 'debugMode=true must emit the debug-only console line');
    assert.ok(debugLine.includes('DEBUG_VISIBLE_MESSAGE'));
    assert.ok(!timelineEvents.join(' ').includes('DEBUG_VISIBLE_MESSAGE'), 'error.message must never reach the timeline/Copy Debug Log even in debugMode');
});

// =======================================================================
// H. read-only — does not mutate existing T0-T10 / turn trace state
// =======================================================================
test('H) RESPONSE_DONE_FAILEDブロックはtoolContinuationTrace*・turnLatencyTrace*等の既存状態を一切書き換えない（読み取り専用）', () => {
    // 実際の代入文はこのコードベースの慣習上 "name = value" のように前後に
    // 空白を伴う（"turnLatencyTraceId=" のようにログ文字列の一部としてのみ
    // 空白無しで出現する箇所と区別するため \s+ を要求する）。
    assert.ok(!/\btoolContinuationTraceActive\s+=\s+(?!=)/.test(FAILED_BLOCK_SRC), 'must not assign to toolContinuationTraceActive');
    assert.ok(!/\btoolContinuationTraceCallId\s+=\s+(?!=)/.test(FAILED_BLOCK_SRC), 'must not assign to toolContinuationTraceCallId');
    assert.ok(!/\bturnLatencyTraceId\s+=\s+(?!=)/.test(FAILED_BLOCK_SRC), 'must not assign to turnLatencyTraceId');
    assert.ok(!FAILED_BLOCK_SRC.includes('dc.send'), 'must never call dc.send');
    assert.ok(!FAILED_BLOCK_SRC.includes('sendResponseCreate'), 'must never call sendResponseCreate');
});

// =======================================================================
// I. dc.send count unchanged
// =======================================================================
test('I) dc.send()呼び出し箇所数は今回も増えていない（診断ログ追加のみ、既存9箇所のまま）', () => {
    const actualCallLines = SRC.split('\n').filter(line => line.trim().startsWith('dc.send('));
    assert.strictEqual(actualCallLines.length, 9, 'dc.send() call-site count must remain 9 — FAST TURN HOTFIX 5 adds zero new Realtime API sends (diagnostic-only)');
});

// =======================================================================
// J. existing watchdogs / active-response warning still present with console.log added
// =======================================================================
test('J) 既存のTOOL_CONTINUATION_WATCHDOG発火箇所にconsole.logが追加されている', () => {
    const idx = SRC.indexOf('TOOL_CONTINUATION_WATCHDOG_NO_RESPONSE_CREATED_AFTER_T6 (waitedMs=');
    assert.notStrictEqual(idx, -1);
    // 実測: アンカーからconsole.log呼び出しまで733文字（間に長い日本語コメントが
    // あるため）。余裕を見て900文字とする。
    const block = SRC.slice(idx, idx + 900);
    assert.ok(block.includes("console.log('[TOOL_CONTINUATION_WATCHDOG_NO_RESPONSE_CREATED_AFTER_T6]"));
});

test('J2) 既存のPLAIN_TURN_WATCHDOG発火箇所にconsole.logが追加されている', () => {
    const idx = SRC.indexOf('PLAIN_TURN_WATCHDOG_NO_RESPONSE_CREATED_AFTER_COMMIT (turnId=');
    assert.notStrictEqual(idx, -1);
    const block = SRC.slice(idx, idx + 600);
    assert.ok(block.includes("console.log('[PLAIN_TURN_WATCHDOG_NO_RESPONSE_CREATED_AFTER_COMMIT]"));
});

test('J3) 既存の「response.create while active」警告箇所にconsole.logが追加されている（重要な原因候補の可視化）', () => {
    const idx = SRC.indexOf("RESPONSE_CREATE多重送信の可能性");
    assert.notStrictEqual(idx, -1);
    // 実測: アンカーからconsole.log呼び出しまで693文字。余裕を見て850文字とする。
    const block = SRC.slice(idx, idx + 850);
    assert.ok(block.includes("console.log('[RESPONSE_CREATE_WHILE_ACTIVE]"));
});

test('J4) 既存のTOOL/PLAIN WATCHDOGのarm/cancel関数定義自体は今回変更されていない（dc.send/sendResponseCreateを含まない）', () => {
    const armToolSrc = extractBlock(SRC, 'function armToolContinuationResponseWatchdog(forCallId) {');
    const armPlainSrc = extractBlock(SRC, 'function armPlainTurnResponseWatchdog(forTurnId) {');
    assert.ok(!armToolSrc.includes('dc.send') && !armToolSrc.includes('sendResponseCreate'));
    assert.ok(!armPlainSrc.includes('dc.send') && !armPlainSrc.includes('sendResponseCreate'));
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
if (failed > 0) process.exit(1);
