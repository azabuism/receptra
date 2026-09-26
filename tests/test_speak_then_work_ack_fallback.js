'use strict';

// PHASE O5.5 — Speak-Then-Work Ack-Fallback regression tests
//
// 背景: 実機電話テストで「今から30分後」のような、check_availability等
// Backend確認が必要な発話の後、AIが（Tool呼び出し自体は行われているにも
// かかわらず）無言になる症状が確認された。コード監査の結果、OpenAI
// Realtimeの1応答はTool呼び出しを含む場合function_call専用（音声ゼロ）と
// なることが実機ログ上のコメント（FAST TURN 3.4）で既に確認されており、
// instructionsの強化だけでは解決できないと判断した（Audit判定: B）。
//
// 本Tier2実装は、既存のRealtime response lifecycle（sendResponseCreate/
// dc.send/T0-T10/責任分界）に一切新しいdc.send()呼び出しを追加せず、
// ローカルに事前生成・プリロード済みの固定文言音声を、
// response.output_item.done(function_call)確定と同時に、AI自身がまだ
// 一度も話していない場合にのみ再生することで「無言」を埋める。
//
// このテストは、その安全網 (`maybeSendSpeakThenWorkAckFallback`) と
// 既存の通話世代ガード (`isStaleCallEvent`) を、tests/test_tool_continuation_
// resilience.js と同じ方式（vm上でソースを直接実行）で検証する。
//
// 実行: node tests/test_speak_then_work_ack_fallback.js

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
    maybeSendSpeakThenWorkAckFallback: extractFunctionSource(SRC, 'maybeSendSpeakThenWorkAckFallback', false),
    isStaleCallEvent: extractFunctionSource(SRC, 'isStaleCallEvent', false),
    formatElapsedSinceSpeechStopped: extractFunctionSource(SRC, 'formatElapsedSinceSpeechStopped', false),
};

function buildSandbox(overrides) {
    const events = [];
    const playCalls = [];

    const ackFallbackAudioEl = {
        currentTime: 0,
        play() {
            playCalls.push({ atCurrentTime: this.currentTime });
            if (overrides && overrides.playRejects) {
                return Promise.reject(new Error('simulated play() rejection'));
            }
            return Promise.resolve();
        },
    };

    const context = {
        performance: { now: () => Date.now() },
        pushTimelineEvent: (text) => { events.push(text); },
        console: console,
    };
    const state = Object.assign({
        // maybeSendSpeakThenWorkAckFallbackが参照する状態
        SPEAK_THEN_WORK_ACK_TOOLS: new Set(['check_availability']),
        aiAudioOutputActive: false,
        ackFallbackState: 'ready',
        ackFallbackAudioEl,
        processedToolCallIds: new Set(),
        // formatElapsedSinceSpeechStoppedが参照する状態（Section9計測。
        // nullのままなら'?'を返すだけで、再生判定ロジックには影響しない）。
        lastUserSpeechStoppedAt: null,
        // isStaleCallEventが参照する状態
        ended: false,
        callGeneration: 1,
    }, overrides || {});
    delete state.playRejects;
    Object.assign(context, state);

    vm.createContext(context);
    vm.runInContext(Object.values(FN).join('\n\n'), context);

    return { ctx: context, events, playCalls, ackFallbackAudioEl };
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

console.log('PHASE O5.5 Speak-Then-Work Ack-Fallback regression tests');
console.log('source: ' + ENGINE_JS_PATH);
console.log('');

(async () => {

await test('ACK-AVAILABILITY: 対象Tool(check_availability)・AI未発話・プリロード済みなら安全網音声を再生する', () => {
    const { ctx, events, playCalls } = buildSandbox({});
    vm.runInContext(
        'maybeSendSpeakThenWorkAckFallback({ call_id: "call_1", name: "check_availability" }, callGeneration)',
        ctx
    );
    assert.strictEqual(playCalls.length, 1, 'play() must be called exactly once');
    assert.ok(events.some((e) => e.includes('SPEAK_THEN_WORK_ACK_FALLBACK_PLAY_REQUESTED') && e.includes('check_availability')));
});

await test('対象外Tool(create_reservation)では安全網音声を再生しない（Section6/15: 副作用のあるToolは今回のスコープ外）', () => {
    const { ctx, events, playCalls } = buildSandbox({});
    vm.runInContext(
        'maybeSendSpeakThenWorkAckFallback({ call_id: "call_2", name: "create_reservation" }, callGeneration)',
        ctx
    );
    assert.strictEqual(playCalls.length, 0, 'create_reservation must never trigger the ack fallback (deferred out of scope for O5.5)');
    assert.strictEqual(events.length, 0, 'no timeline event at all should fire for an out-of-scope tool (early return)');
});

await test('AIが既に発話中(aiAudioOutputActive=true)の場合は二重acknowledgementを避けて再生しない', () => {
    const { ctx, events, playCalls } = buildSandbox({ aiAudioOutputActive: true });
    vm.runInContext(
        'maybeSendSpeakThenWorkAckFallback({ call_id: "call_3", name: "check_availability" }, callGeneration)',
        ctx
    );
    assert.strictEqual(playCalls.length, 0);
    assert.ok(events.some((e) => e.includes('SPEAK_THEN_WORK_ACK_SKIPPED') && e.includes('reason=ai_already_speaking')));
});

await test('プリロード未完了/失敗(ackFallbackState!==ready)の場合は安全に何もせずスキップする（fatalにしない）', () => {
    const { ctx, events, playCalls } = buildSandbox({ ackFallbackState: 'preload_failed' });
    vm.runInContext(
        'maybeSendSpeakThenWorkAckFallback({ call_id: "call_4", name: "check_availability" }, callGeneration)',
        ctx
    );
    assert.strictEqual(playCalls.length, 0);
    assert.ok(events.some((e) => e.includes('SPEAK_THEN_WORK_ACK_SKIPPED') && e.includes('fallback_not_ready:preload_failed')));
});

await test('BARGE-IN/通話世代ガード: 呼び出し時点のcallGenerationが既に古い(isStaleCallEvent=true)場合は再生しない', () => {
    // 通話1(myGeneration=1)のfunction_callが非同期に遅延して届いた時点で、
    // 既に通話2(callGeneration=2)が始まっている（前の通話が終了・新しい
    // 通話が発信された）ケースを模倣する。この安全網音声が、既に終了した
    // 古い通話のために新しい通話中に鳴ってしまうことを防ぐ。
    const { ctx, events, playCalls } = buildSandbox({ callGeneration: 2 });
    const myGenerationFromOldCall = 1;
    vm.runInContext(
        'maybeSendSpeakThenWorkAckFallback({ call_id: "call_5", name: "check_availability" }, ' + myGenerationFromOldCall + ')',
        ctx
    );
    assert.strictEqual(playCalls.length, 0, 'a stale (previous-generation) function_call must never trigger playback in the new call');
    assert.strictEqual(events.length, 0, 'isStaleCallEvent guards silently (no timeline noise for an already-superseded call)');
});

await test('BARGE-IN/fatal終了ガード: ended=trueの場合は同一世代でも再生しない', () => {
    const { ctx, playCalls } = buildSandbox({ ended: true });
    vm.runInContext(
        'maybeSendSpeakThenWorkAckFallback({ call_id: "call_6", name: "check_availability" }, callGeneration)',
        ctx
    );
    assert.strictEqual(playCalls.length, 0, 'once the call has fatally ended, no further audio must be triggered');
});

await test('TOOL-ONCE相当（二重再生防止）: 同じcall_idが既にprocessedToolCallIds済みなら再生しない', () => {
    const processedToolCallIds = new Set(['call_dup']);
    const { ctx, events, playCalls } = buildSandbox({ processedToolCallIds });
    vm.runInContext(
        'maybeSendSpeakThenWorkAckFallback({ call_id: "call_dup", name: "check_availability" }, callGeneration)',
        ctx
    );
    assert.strictEqual(playCalls.length, 0, 'a call_id already processed by handleFunctionCallItem must not re-trigger the fallback (duplicate function_call event)');
    assert.ok(events.some((e) => e.includes('reason=duplicate_call_id')));
});

await test('itemがnull、またはcall_idが無い場合は例外を投げず何もしない', () => {
    const { ctx: ctx1, playCalls: playCalls1 } = buildSandbox({});
    assert.doesNotThrow(() => {
        vm.runInContext('maybeSendSpeakThenWorkAckFallback(null, callGeneration)', ctx1);
    });
    assert.strictEqual(playCalls1.length, 0);

    const { ctx: ctx2, playCalls: playCalls2 } = buildSandbox({});
    assert.doesNotThrow(() => {
        vm.runInContext('maybeSendSpeakThenWorkAckFallback({ name: "check_availability" }, callGeneration)', ctx2);
    });
    assert.strictEqual(playCalls2.length, 1, 'call_id is only used for the dedup guard; its absence must not block legitimate playback');
});

await test('play()がrejectしても例外が外へ伝播しない（音声再生失敗が通話自体を止めない）', () => {
    const { ctx } = buildSandbox({ playRejects: true });
    assert.doesNotThrow(() => {
        vm.runInContext(
            'maybeSendSpeakThenWorkAckFallback({ call_id: "call_7", name: "check_availability" }, callGeneration)',
            ctx
        );
    });
});

await test('DC-SEND: 本フェーズの実装はdc.send(JSON.stringify(の出現数を増やしていない（新規Realtime制御イベントを追加していない）。基準値は8ではなく9（後発の別フェーズ NOISY ENVIRONMENT / 3-SECOND TURN BOUNDARY がmaybeSendUserTurnFallbackCommitを正当に追加したため。本フェーズ自体は引き続き追加していない）。', () => {
    const sendCount = (SRC.match(/dc\.send\(JSON\.stringify\(/g) || []).length;
    assert.strictEqual(sendCount, 9, 'Speak-Then-Work Ack-Fallback must not add any new dc.send() call site (it bypasses the Realtime response lifecycle entirely); the current baseline of 9 reflects a later, unrelated phase adding USER_TURN_3S_FALLBACK, not this one');
});

await test('GREETING-REG: Zero-Wait Greeting関連コードの主要シンボルが変更されず残っている', () => {
    ['ZERO_WAIT_AUDIO_URL', 'startZeroWaitGreeting', 'preloadZeroWaitGreetingAudio', 'waitForZeroWaitOutcome'].forEach((sym) => {
        assert.ok(SRC.includes(sym), 'Zero-Wait Greeting symbol must remain untouched: ' + sym);
    });
});

await test('配線確認: startCall()内でresetAckFallbackCallState()が呼ばれている（前回通話の再生位置を持ち越さない）', () => {
    const idx = SRC.indexOf('async function startCall');
    assert.notStrictEqual(idx, -1, 'startCall not found');
    // startCall本体はかなり長いため、次のトップレベル関数定義に到達するまでの
    // 範囲ではなく、十分広いウィンドウで探索する（後発フェーズの状態リセット
    // 追加分だけ実際の出現位置が後ろへ移動することがあるため、余裕を持たせた
    // 固定長を使う。実測位置固定ではなく、実測より十分大きい値を使うことで
    // 将来の追加リセット行にもある程度耐えられるようにする）。
    const window = SRC.slice(idx, idx + 11000);
    assert.ok(window.includes('resetAckFallbackCallState();'), 'startCall() must reset the ack-fallback playback position for each new call');
});

await test('配線確認: response.output_item.done(function_call)ハンドラがmaybeSendSpeakThenWorkAckFallbackを呼んでいる', () => {
    const idx = SRC.indexOf("type === 'response.output_item.done' && msg.item && msg.item.type === 'function_call'");
    assert.notStrictEqual(idx, -1, 'function_call output_item.done handler not found');
    const block = SRC.slice(idx, idx + 2200);
    assert.ok(block.includes('maybeSendSpeakThenWorkAckFallback(msg.item, callGeneration);'),
        'the ack-fallback decision must be wired into the function_call detection handler, using the shared callGeneration counter');
    // 呼び出し順序: 安全網の再生判定は、Tool本体の非同期処理
    // (handleFunctionCallItem)より先に評価される（最速でレイテンシを埋める
    // ため。handleFunctionCallItemは内部でtry/catch保護されawaitされない
    // ため、順序を入れ替えても実処理の完了タイミングには影響しない）。
    const ackIdx = block.indexOf('maybeSendSpeakThenWorkAckFallback(msg.item, callGeneration);');
    const toolIdx = block.indexOf('handleFunctionCallItem(msg.item)');
    assert.ok(ackIdx !== -1 && toolIdx !== -1 && ackIdx < toolIdx,
        'the ack-fallback call should be evaluated before kicking off handleFunctionCallItem, for lowest latency to first audio');
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
process.exit(failed > 0 ? 1 : 0);

})();
