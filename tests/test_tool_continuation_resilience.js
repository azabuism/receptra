'use strict';

// FAST TURN 3.6A — Tool Call継続の耐障害性 regression test suite
//
// 背景: 実機（Mac Safari）で、日時＋人数が揃いcheck_availabilityが発火した後、
// AI音声が二度と再開せず会話が無音のまま停止する症状が報告された。
// check_availability自体のHTTP応答は実測で約172msと高速であることが
// Safari Network タブで確認されており、「Toolが遅い」という単純な説明では
// 説明がつかなかった。
//
// コード監査の結果、handleFunctionCallItem()内でTool結果をRealtimeへ送り返す
// dc.send({type:'conversation.item.create', item:{type:'function_call_output', ...}})
// だけが、このファイル内の他すべてのdc.send()呼び出し（input_audio_buffer.commit系・
// sendResponseCreate・Zero-Wait会話履歴通知）と異なりtry/catchで保護されておらず、
// ここで例外が投げられると関数がその場で中断し、以降のsendResponseCreate()自体が
// 一切呼ばれないまま終わることが判明した（呼び出し元のhandleFunctionCallItem(...)
// .catch(e => logEvent(...))は例外をログに残すだけで、Realtime側には何も
// 送られない）。本テストはこの修正（try/catchで保護し、送信失敗時もなお
// sendResponseCreate()の呼び出しまでは継続する）を、実際の実装コードに対して
// 検証する（tests/test_short_answer_turn.js等と同じ、vm上でソースを直接実行する方式）。
//
// 実行: node tests/test_tool_continuation_resilience.js

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

const FN = {
    handleFunctionCallItem: extractFunctionSource(SRC, 'handleFunctionCallItem', true),
    sendResponseCreate: extractFunctionSource(SRC, 'sendResponseCreate', false),
    isToolOutputFailure: extractFunctionSource(SRC, 'isToolOutputFailure', false),
};

// 回帰防止（最重要）: このdc.send()が再びtry/catchの外に出されることを防ぐ、
// ソーステキストレベルでの単純なガード。handleFunctionCallItem内で
// function_call_outputを送るdc.send()呼び出しの直前にtryが、直後にcatchが
// 存在することを確認する。
assert.ok(
    /try\s*\{\s*dc\.send\(JSON\.stringify\(\{\s*type:\s*'conversation\.item\.create'/.test(FN.handleFunctionCallItem),
    'the function_call_output dc.send() must be wrapped in try/catch (regression guard for the silent-stall bug)'
);
assert.ok(
    FN.handleFunctionCallItem.includes('functionCallOutputSendFailed = true'),
    'a failed function_call_output send must be tracked, not silently swallowed'
);

function buildSandbox(overrides) {
    const events = [];
    const logs = [];
    const dcSent = [];

    const dc = Object.assign({
        readyState: 'open',
        send(payload) {
            const parsed = JSON.parse(payload);
            dcSent.push(parsed);
            if (this.__throwOn && this.__throwOn(parsed)) {
                throw new Error('simulated dc.send failure');
            }
        },
    }, (overrides && overrides.dc) || {});

    const context = {
        performance: { now: () => Date.now() },
        pushTimelineEvent: (text) => { events.push(text); },
        logEvent: (text) => { logs.push(text); },
        updateAudioDiagnosticsPanel: () => {},
        console: console,
        JSON: JSON,
    };
    const state = Object.assign({
        dc,
        processedToolCallIds: new Set(),
        lastToolLabel: null,
        lastToolFetchOutcome: null,
        toolCallStartedAtForLatency: null,
        turnLatencyToolOccurred: false,
        turnLatencyToolName: null,
        turnLatencyToolDurationMs: null,
        responseState: 'idle',
        // check_availability成功のcanned response（PIIなし）。
        callCheckAvailabilityTool: async () => ({
            available: true,
            date: '2026-09-27',
            time: '14:00',
            party_size: 2,
            reason_code: null,
        }),
        callCreateReservationTool: async () => ({ success: true }),
        callGetShopInfoTool: async () => ({}),
        callFindCustomerTool: async () => ({}),
        callConfirmCustomerIdentityTool: async () => ({}),
        callGetCustomerContextTool: async () => ({}),
        callSetConversationLanguageTool: async () => ({}),
        callRequestCallbackTool: async () => ({}),
    }, overrides || {});
    delete state.dc;
    Object.assign(context, state);
    context.dc = dc;

    vm.createContext(context);
    vm.runInContext(Object.values(FN).join('\n\n'), context);

    return { ctx: context, events, logs, dcSent, dc };
}

let passed = 0, failed = 0;
function test(name, fn) {
    const p = Promise.resolve().then(fn);
    return p.then(() => {
        passed++;
        console.log('  ok - ' + name);
    }).catch((e) => {
        failed++;
        console.log('  FAIL - ' + name);
        console.log('    ' + (e && e.stack ? e.stack.split('\n').slice(0, 6).join('\n    ') : e));
    });
}

console.log('Tool Call継続の耐障害性 (FAST TURN 3.6A) regression tests');
console.log('source: ' + ENGINE_JS_PATH);
console.log('');

(async () => {

await test('A: happy path) check_availability success -> function_call_output送信 -> response.create送信まですべて到達する', async () => {
    const { ctx, events, dcSent } = buildSandbox({});
    await vm.runInContext(
        'handleFunctionCallItem({ call_id: "call_1", name: "check_availability", arguments: "{}" })',
        ctx
    );
    assert.ok(events.includes('TOOL_CALL_RECEIVED (tool=check_availability)'));
    assert.ok(events.includes('TOOL_FETCH_STARTED (tool=check_availability)'));
    assert.ok(events.includes('TOOL_FETCH_SUCCESS (tool=check_availability)'));
    assert.ok(events.includes('TOOL_OUTPUT_SENT (tool=check_availability)'), 'function_call_output send must be recorded as sent on the happy path');
    assert.ok(events.includes('TOOL_CONTINUATION_REQUESTED (tool=check_availability)'));
    assert.ok(!events.some((e) => e.includes('TOOL_OUTPUT_SEND_FAILED')), 'no failure marker expected on the happy path');
    // dc.sendは function_call_output の conversation.item.create → response.create の順で2回呼ばれる
    assert.strictEqual(dcSent.length, 2);
    assert.strictEqual(dcSent[0].type, 'conversation.item.create');
    assert.strictEqual(dcSent[0].item.type, 'function_call_output');
    assert.strictEqual(dcSent[1].type, 'response.create');
});

await test('B: CRITICAL regression) function_call_outputのdc.send()が例外を投げても、会話が無音のまま停止せず、response.createが送信される', async () => {
    const { ctx, events, dcSent, logs } = buildSandbox({
        dc: {
            __throwOn: (parsed) => parsed.type === 'conversation.item.create',
        },
    });
    // 例外がhandleFunctionCallItem自体の外へ伝播しない（＝呼び出し元の
    // .catch()に頼らずとも安全）ことも確認する。
    await vm.runInContext(
        'handleFunctionCallItem({ call_id: "call_2", name: "check_availability", arguments: "{}" })',
        ctx
    );
    assert.ok(events.includes('TOOL_OUTPUT_SEND_FAILED (tool=check_availability): simulated dc.send failure'),
        'the failed send must be recorded, not silently swallowed');
    assert.ok(!events.includes('TOOL_OUTPUT_SENT (tool=check_availability)'),
        'must not claim success when the send actually threw');
    // 最重要: function_call_outputの送信に失敗しても、sendResponseCreate()の
    // 呼び出しまでは必ず到達する（＝この修正前は、ここで関数が中断し
    // TOOL_CONTINUATION_REQUESTEDにすら到達しなかった）。
    assert.ok(events.includes('TOOL_CONTINUATION_REQUESTED (tool=check_availability)'),
        'must still proceed to request a continuation response after a failed output send');
    // response.create自体はdcがまだopenであれば実際に送信される
    // （conversation.item.createだけが失敗し、response.createは別送信のため）。
    assert.strictEqual(dcSent.length, 2, 'response.create must still be attempted as a separate send');
    assert.strictEqual(dcSent[1].type, 'response.create');
    assert.ok(logs.some((l) => l.includes('dc.send')), 'a human-readable failure log must be recorded');
});

await test('C: dcが完全に閉じている場合、function_call_output送信もresponse.create送信も安全にno-opする（例外を投げない）', async () => {
    const { ctx, events } = buildSandbox({
        dc: { readyState: 'closed', send() { throw new Error('InvalidStateError'); } },
    });
    await vm.runInContext(
        'handleFunctionCallItem({ call_id: "call_3", name: "check_availability", arguments: "{}" })',
        ctx
    );
    assert.ok(events.includes('TOOL_OUTPUT_SEND_FAILED (tool=check_availability): InvalidStateError'));
    assert.ok(events.includes('RESPONSE_CREATE_SKIPPED (dc未接続, reason=tool_result:check_availability:output_send_failed)'),
        'sendResponseCreate must itself safely skip when dc is not open, without throwing');
    assert.ok(events.includes('TOOL_CONTINUATION_REQUESTED (tool=check_availability)'));
});

await test('D: 二重実行防止) 同じcall_idは2回処理されない（既存動作の維持）', async () => {
    const { ctx, events } = buildSandbox({});
    await vm.runInContext('handleFunctionCallItem({ call_id: "call_dup", name: "check_availability", arguments: "{}" })', ctx);
    const firstCount = events.filter((e) => e === 'TOOL_CALL_RECEIVED (tool=check_availability)').length;
    await vm.runInContext('handleFunctionCallItem({ call_id: "call_dup", name: "check_availability", arguments: "{}" })', ctx);
    const secondCount = events.filter((e) => e === 'TOOL_CALL_RECEIVED (tool=check_availability)').length;
    assert.strictEqual(firstCount, 1);
    assert.strictEqual(secondCount, 1, 'a repeated call_id must not be processed twice');
});

await test('E: UI_STATE) function_callを含む応答のresponse.doneでは「待機中」へ戻さず「確認しています」を維持する', () => {
    // handleDataChannelEvent自体は依存関数が非常に多いため、既存テスト群
    // （例: 「must not call sendResponseCreate」等）と同じ方式で、実装コードの
    // 該当ブロックをソーステキストとして直接検証する（実行はしない）。
    const idx = SRC.indexOf("} else if (type === 'response.done') {");
    assert.notStrictEqual(idx, -1, 'response.done handler not found');
    const block = SRC.slice(idx, idx + 1200);
    assert.ok(/if\s*\(responseHasFunctionCall\)\s*\{\s*subStatusText\.textContent\s*=\s*'確認しています';/.test(block),
        'response.done must keep "確認しています" (PROCESSING) when this response contained a function_call');
    assert.ok(/\}\s*else\s*\{\s*subStatusText\.textContent\s*=\s*'待機中（お話しください）';/.test(block),
        'response.done must only fall back to "待機中" (LISTENING) when the response had no function_call');
});

await test('F: UI_STATE) function_call検出時点で即座に「確認しています」(PROCESSING)へ切り替える', () => {
    const idx = SRC.indexOf("type === 'response.output_item.done' && msg.item && msg.item.type === 'function_call'");
    assert.notStrictEqual(idx, -1, 'function_call output_item.done handler not found');
    const block = SRC.slice(idx, idx + 1200);
    assert.ok(block.includes("responseHasFunctionCall = true;"));
    assert.ok(block.includes("subStatusText.textContent = '確認しています';"),
        'must switch to PROCESSING text as soon as a function_call is detected, not wait for response.done');
});

await test('G: UI_STATE) 実際にAI音声が再生開始した瞬間(output_audio_buffer.started)にAI_SPEAKINGへ切り替える', () => {
    const idx = SRC.indexOf("if (type === 'output_audio_buffer.started') {");
    assert.notStrictEqual(idx, -1, 'output_audio_buffer.started handler not found');
    const block = SRC.slice(idx, idx + 500);
    assert.ok(block.includes("subStatusText.textContent = 'AIスタッフが応答中';"),
        'must show AI_SPEAKING text exactly when audio actually starts playing');
});

await test('H: dc.send()呼び出し箇所は増えていない（新規Realtime制御イベントを追加していない、既存箇所のtry/catch化のみ）', () => {
    // FAST TURN 3.6Aの修正は、既存のfunction_call_output送信をtry/catchで
    // 保護したこととUI文言変更のみであり、新しいdc.send()呼び出し箇所や
    // 新しい種類のRealtime制御イベント（response.cancel等）は一切追加して
    // いないことをソースレベルで確認する。
    const sendCount = (SRC.match(/dc\.send\(JSON\.stringify\(/g) || []).length;
    assert.strictEqual(sendCount, 8, 'expected exactly 8 actual dc.send(JSON.stringify(...)) call sites (unchanged from before this fix; comments mentioning dc.send() do not count)');
    assert.ok(!SRC.includes("type: 'response.cancel'"), 'must not introduce response.cancel');
    assert.ok(!SRC.includes("type: 'conversation.item.truncate'") || SRC.includes('conversation.item.truncated'),
        'must not send a new conversation.item.truncate control event');
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
process.exit(failed > 0 ? 1 : 0);

})();
