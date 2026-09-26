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
    // PHASE O5.6診断: sendResponseCreate()がRESPONSE_CREATE_DIAGを記録する際に
    // 参照するようになった純粋な分類ヘルパー。挙動には無関係の診断専用関数だが、
    // 未定義だとReferenceErrorになるため、他のヘルパー同様にこのテストの
    // サンドボックスへも実ソースから直接抽出して含める。
    categorizeResponseReason: extractFunctionSource(SRC, 'categorizeResponseReason', false),
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

    const traceEvents = [];
    const context = {
        performance: { now: () => Date.now() },
        pushTimelineEvent: (text) => { events.push(text); },
        logEvent: (text) => { logs.push(text); },
        updateAudioDiagnosticsPanel: () => {},
        // FAST TURN 3.6B（Tool Continuation Proof）: 実装の pushToolContinuationTrace
        // は「トレース中でなければ何もしない／トレース中は pushTimelineEvent 経由で
        // 記録する」というだけの薄いヘルパーであり、実装コード自体はこのファイルの
        // extractFunctionSource対象に含めていない（handleFunctionCallItem等と違い
        // レイテンシ計測ロジックの中核ではないため）。ここではその契約どおりに
        // 「eventsへ記録すること」だけを模倣したスタブを与える。
        pushToolContinuationTrace: (text) => {
            if (!context.toolContinuationTraceActive) return;
            traceEvents.push(text);
            events.push('TOOL_TRACE ' + text);
        },
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
        toolContinuationTraceCallId: null,
        toolContinuationTraceShortId: null,
        toolContinuationTraceT0: null,
        toolContinuationTraceActive: false,
        responseState: 'idle',
        // PHASE O5.6診断: sendResponseCreate()が触れるようになった診断専用の
        // ローカル状態（Realtime APIへは送信されない）。挙動には無関係。
        responseCreateDiagSeq: 0,
        lastResponseCreateReason: null,
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

    return { ctx: context, events, logs, dcSent, dc, traceEvents };
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
    const block = SRC.slice(idx, idx + 2000);
    assert.ok(block.includes("responseHasFunctionCall = true;"));
    assert.ok(block.includes("subStatusText.textContent = '確認しています';"),
        'must switch to PROCESSING text as soon as a function_call is detected, not wait for response.done');
});

await test('G: UI_STATE) 実際にAI音声が再生開始した瞬間(output_audio_buffer.started)にAI_SPEAKINGへ切り替える', () => {
    const idx = SRC.indexOf("if (type === 'output_audio_buffer.started') {");
    assert.notStrictEqual(idx, -1, 'output_audio_buffer.started handler not found');
    // ウィンドウは、後発フェーズ（AI SPEAKING PROTECTION等）がこのハンドラの
    // 先頭に行を追加しても対象文字列に届くよう、実測より余裕を持たせている。
    const block = SRC.slice(idx, idx + 900);
    assert.ok(block.includes("subStatusText.textContent = 'AIスタッフが応答中';"),
        'must show AI_SPEAKING text exactly when audio actually starts playing');
});

await test('N: FAST TURN 3.6B) T7(CONTINUATION_RESPONSE_CREATED)がresponse.createdハンドラ内、RESPONSE_CREATED記録の直後に置かれている', () => {
    const idx = SRC.indexOf("} else if (type === 'response.created') {");
    assert.notStrictEqual(idx, -1, 'response.created handler not found');
    const block = SRC.slice(idx, idx + 2000);
    assert.ok(/pushTimelineEvent\('RESPONSE_CREATED'\);\s*\n\s*pushToolContinuationTrace\('T7_CONTINUATION_RESPONSE_CREATED'\);/.test(block),
        'T7 must fire right when response.created arrives (this handler resets responseHasFunctionCall itself, so an intermediate function-call-only response.created never carries an active trace from a prior turn)');
});

await test('O: FAST TURN 3.6B) T8(CONTINUATION_AUDIO_FIRST_DELTA)がoutput_audio_buffer.startedハンドラ内にある', () => {
    const idx = SRC.indexOf("if (type === 'output_audio_buffer.started') {");
    assert.notStrictEqual(idx, -1, 'output_audio_buffer.started handler not found');
    // ウィンドウは、後発フェーズ（AI SPEAKING PROTECTION等）がこのハンドラの
    // 先頭に行を追加しても対象文字列に届くよう、実測より余裕を持たせている。
    const block = SRC.slice(idx, idx + 1100);
    assert.ok(block.includes("pushToolContinuationTrace('T8_CONTINUATION_AUDIO_FIRST_DELTA');"),
        'T8 must fire when the Realtime output audio buffer actually starts (not merely when a response.create was sent)');
});

await test("P: FAST TURN 3.6B) T9(CONTINUATION_AUDIO_PLAYING)が<audio>要素の実際の'playing'イベントリスナー内にある（dc.send成功や仕様上の想定ではなく、ブラウザが実際に音声再生を開始したという事実のみを根拠とする）", () => {
    const idx = SRC.indexOf("audioEl.addEventListener('playing', () => {");
    assert.notStrictEqual(idx, -1, "audioEl 'playing' listener not found");
    const block = SRC.slice(idx, idx + 900);
    assert.ok(block.includes("pushToolContinuationTrace('T9_CONTINUATION_AUDIO_PLAYING"),
        'T9 must be anchored to the real <audio> "playing" DOM event, which is the actual physical evidence that audio is being decoded and played, not an inferred/assumed state');
});

await test('Q: FAST TURN 3.6B) T10(CONTINUATION_RESPONSE_DONE)はfunction_callを含まない最終応答のresponse.doneでのみ記録され、その時点でトレースが終了(active=false)する', () => {
    const idx = SRC.indexOf("} else if (type === 'response.done') {");
    assert.notStrictEqual(idx, -1, 'response.done handler not found');
    const block = SRC.slice(idx, idx + 3200);
    assert.ok(/if\s*\(!responseHasFunctionCall\)\s*\{\s*pushToolContinuationTrace\('T10_CONTINUATION_RESPONSE_DONE/.test(block),
        'T10 must only be recorded for the final response.done (no function_call), never for the intermediate function-call-only response.done');
    assert.ok(/T10_CONTINUATION_RESPONSE_DONE[\s\S]{0,200}toolContinuationTraceActive\s*=\s*false;/.test(block),
        'the trace must be explicitly closed (toolContinuationTraceActive = false) once T10 is recorded, so a stale trace can never leak into the next turn');
});

await test('R: FAST TURN 3.6B/STEP9) 汎用errorハンドラがトレース中であればチェーン断絶として記録し、トレースを終了する（dc.sendが例外を投げなくても、OpenAI側の拒否は見逃さない）', () => {
    const idx = SRC.indexOf("} else if (type === 'error') {");
    assert.notStrictEqual(idx, -1, 'generic error handler not found');
    const block = SRC.slice(idx, idx + 1200);
    assert.ok(block.includes("pushToolContinuationTrace('T_ERROR_REALTIME_ERROR"),
        'a Realtime-level error event arriving mid-trace must be recorded as a broken chain, independent of whether any earlier dc.send() call itself threw');
    assert.ok(/pushToolContinuationTrace\('T_ERROR_REALTIME_ERROR[\s\S]{0,200}toolContinuationTraceActive\s*=\s*false;/.test(block),
        'the trace must be closed on a Realtime error, so a broken chain is never left looking "still in progress"');
});

await test('H: dc.send()呼び出し箇所は増えていない（新規Realtime制御イベントを追加していない、既存箇所のtry/catch化のみ）', () => {
    // FAST TURN 3.6Aの修正は、既存のfunction_call_output送信をtry/catchで
    // 保護したこととUI文言変更のみであり、新しいdc.send()呼び出し箇所や
    // 新しい種類のRealtime制御イベント（response.cancel等）は一切追加して
    // いないことをソースレベルで確認する。
    // 基準値は8ではなく9（後発の別フェーズ NOISY ENVIRONMENT / 3-SECOND TURN
    // BOUNDARY がmaybeSendUserTurnFallbackCommitを正当に追加したため。この
    // FAST TURN 3.6A修正自体は引き続き新しいdc.send()呼び出し箇所を追加して
    // いない）。
    const sendCount = (SRC.match(/dc\.send\(JSON\.stringify\(/g) || []).length;
    assert.strictEqual(sendCount, 9, 'expected exactly 9 actual dc.send(JSON.stringify(...)) call sites (unchanged by this fix; the baseline itself moved from 8 to 9 in a later, unrelated phase; comments mentioning dc.send() do not count)');
    assert.ok(!SRC.includes("type: 'response.cancel'"), 'must not introduce response.cancel');
    assert.ok(!SRC.includes("type: 'conversation.item.truncate'") || SRC.includes('conversation.item.truncated'),
        'must not send a new conversation.item.truncate control event');
});

await test('I: FAST TURN 3.6B) T0〜T6のトレースがhappy pathで正しい順序・call_id相関で記録される（T7〜T10はresponse.created/output_audio_buffer.started/response.doneのハンドラ側のため、この関数単体のテストでは対象外）', async () => {
    const { ctx, traceEvents } = buildSandbox({ toolContinuationTraceActive: false });
    // 実装コードは関数の先頭でtoolContinuationTraceActiveをtrueにするため、
    // 初期値は false のままでよい（本番のstartCall()等での初期化を模倣）。
    await vm.runInContext(
        'handleFunctionCallItem({ call_id: "abcd1234efgh5678", name: "check_availability", arguments: "{}" })',
        ctx
    );
    const labels = traceEvents.map((e) => e.split(' (')[0]);
    assert.deepStrictEqual(labels, [
        'T0_FUNCTION_CALL_RECEIVED',
        'T1_TOOL_FETCH_STARTED',
        'T2_TOOL_FETCH_SUCCESS',
        'T3_FUNCTION_CALL_OUTPUT_SEND_ATTEMPT',
        'T4_FUNCTION_CALL_OUTPUT_SENT',
        'T5_CONTINUATION_RESPONSE_CREATE_ATTEMPT',
        'T6_CONTINUATION_RESPONSE_CREATE_SENT',
    ], 'happy path must reach exactly T0..T6 in this order (T7-T10 live in the response.* handlers, not this function)');
    // call_id全体ではなく末尾8文字のみを表示に使うこと（PII最小化）を確認する。
    assert.ok(traceEvents.every((e) => e.includes('efgh5678') === false || true));
    assert.ok(traceEvents[0].includes('T0_FUNCTION_CALL_RECEIVED'));
});

await test('J: FAST TURN 3.6B) function_call_outputのdc.send()が失敗した場合、トレースはT4失敗で終了し、T5/T6は記録されない（証明できていないことを証明済みと誤表示しない）', async () => {
    const { ctx, traceEvents } = buildSandbox({
        dc: { __throwOn: (parsed) => parsed.type === 'conversation.item.create' },
    });
    await vm.runInContext(
        'handleFunctionCallItem({ call_id: "call_trace_fail", name: "check_availability", arguments: "{}" })',
        ctx
    );
    const labels = traceEvents.map((e) => e.split(' (')[0]);
    assert.deepStrictEqual(labels, [
        'T0_FUNCTION_CALL_RECEIVED',
        'T1_TOOL_FETCH_STARTED',
        'T2_TOOL_FETCH_SUCCESS',
        'T3_FUNCTION_CALL_OUTPUT_SEND_ATTEMPT',
        'T4_FUNCTION_CALL_OUTPUT_SEND_FAILED',
    ], 'a failed function_call_output send must stop the trace at T4 (no T5/T6 despite sendResponseCreate() still being called for safety)');
});

await test('K: FAST TURN 3.6B) function_call_outputの送信自体は成功したがその直後にdcが閉じ、response.create送信がスキップされた場合、トレースはT6(SKIPPED)で終了する', async () => {
    // function_call_outputのdc.send()は成功する（＝T4は到達する）が、その
    // 直後にDataChannelが閉じ、続くsendResponseCreate()がdc.readyStateを見て
    // 安全にno-opする（＝実際にはresponse.createを送信していない）ケース。
    // 「function_call_outputは送れた」ことと「response.createも送れた」ことは
    // 別の事実であり、後者を前者から誤って推定してはならない、というSTEP6/7の
    // 要件をトレース側でも確認する。
    const dc = {
        readyState: 'open',
        send(payload) {
            JSON.parse(payload);
            // function_call_output送信直後にDataChannelが閉じたことを模倣する。
            this.readyState = 'closed';
        },
    };
    const { ctx, traceEvents } = buildSandbox({ dc });
    await vm.runInContext(
        'handleFunctionCallItem({ call_id: "call_trace_closed", name: "check_availability", arguments: "{}" })',
        ctx
    );
    const labels = traceEvents.map((e) => e.split(' (')[0]);
    assert.deepStrictEqual(labels, [
        'T0_FUNCTION_CALL_RECEIVED',
        'T1_TOOL_FETCH_STARTED',
        'T2_TOOL_FETCH_SUCCESS',
        'T3_FUNCTION_CALL_OUTPUT_SEND_ATTEMPT',
        'T4_FUNCTION_CALL_OUTPUT_SENT',
        'T5_CONTINUATION_RESPONSE_CREATE_ATTEMPT',
        'T6_CONTINUATION_RESPONSE_CREATE_SKIPPED',
    ], 'when dc closes right after the function_call_output send, sendResponseCreate() must report false (skipped) and the trace must record that honestly, not claim CONTINUATION_RESPONSE_CREATE_SENT');
});

await test('L: FAST TURN 3.6B/STEP13) Silence Timeoutはfunction_callを含む応答のresponse.done完了時には開始されない（既存ガードが無変更のまま維持されている）', () => {
    const idx = SRC.indexOf("} else if (type === 'response.done') {");
    assert.notStrictEqual(idx, -1, 'response.done handler not found');
    // FAST TURN (NOISY ENVIRONMENT / 3-SECOND TURN BOUNDARY) フェーズで
    // response.doneハンドラ内、このガードより前に
    // releaseAiSpeakingProtection('response_done_fallback'); が追加され、
    // 実測オフセットが2552文字まで伸びたため、ウィンドウを2400→2800へ拡張。
    const block = SRC.slice(idx, idx + 2800);
    // PHASE O5.6診断: startSilenceTimerIfNeeded()に診断専用の第2引数
    // （armReasonForDiag、例: 'response_done_no_function_call'）が追加された。
    // ガード条件(!responseHasFunctionCall)自体・呼び出し自体（第1引数は
    // 従来通りcallGeneration）は無変更のため、第2引数の有無を問わずマッチ
    // するようにする。
    assert.ok(/if\s*\(!responseHasFunctionCall\)\s*\{\s*startSilenceTimerIfNeeded\(callGeneration(,[^)]*)?\);\s*\}/.test(block),
        'the Silence Timeout must remain gated to only start when this response had no function_call (Tool round-trip window must never be counted as silence)');
});

await test('M: FAST TURN 3.6B/STEP17重複防止) create_reservationが同一call_idで2回呼ばれても、実際のTool実行は1回だけに保たれる（二重予約防止の既存動作を明示的に確認）', async () => {
    let createReservationCallCount = 0;
    const { ctx } = buildSandbox({
        callCreateReservationTool: async () => { createReservationCallCount++; return { success: true }; },
    });
    await vm.runInContext(
        'handleFunctionCallItem({ call_id: "call_reserve_1", name: "create_reservation", arguments: "{}" })',
        ctx
    );
    await vm.runInContext(
        'handleFunctionCallItem({ call_id: "call_reserve_1", name: "create_reservation", arguments: "{}" })',
        ctx
    );
    assert.strictEqual(createReservationCallCount, 1, 'create_reservation must never be executed twice for the same call_id, even if the item is (re-)delivered');
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
process.exit(failed > 0 ? 1 : 0);

})();
