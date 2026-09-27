'use strict';

// RECEPTRA — FAST TURN EMERGENCY HOTFIX 10（2026年9月）
// 「Tool continuation rate-limit failure / 30秒沈黙 / 勝手な通話終了」修正の
// 契約テスト（フロントエンド側）。
//
// 背景（実機証拠。ユーザー提供の実機ログと完全に一致する形で監査済み。詳細は
// 該当コミットの説明を参照）: check_availability等のTool結果をお客様へ伝える
// ための2回目のresponse.create（Tool continuation）がerror_code=
// rate_limit_exceededで失敗し、既存のHOTFIX 6/7のbounded retryロジックが
// 「追加retryを送らない」と判断した場合（budgetLooksInsufficient）、これまでは
// ブラウザ画面上のテキスト変更のみが行われ、電話中のお客様には何も聞こえない
// 状態になっていた。その直後、function_callを含まない完了扱いのresponse.done
// として既存のsilence timeout（30秒→8秒→終話）が無条件に開始されてしまい、
// 無関係な「お声が確認できません」案内のあとに通話が終了していた
// （実機ログのSILENCE_TIMEOUT_MS=30000と完全一致）。
//
// 本テストが確認する項目（ユーザー指定§16 A〜Tのうち、本ファイルの対象範囲。
// H〜K・L〜N・P等の一部は既存のPhase1/Phase2テストファイルで既に回帰確認済み
// のため、ここでは重複させず対象外とする）:
//   D. rate limit + insufficient budget → 30秒沈黙waitへ突入しない
//      （silence timer開始がローカル安全網音声の再生完了まで遅延される）
//   E. rate-limit失敗単体では自動hangupしない（endCall()が新規コードパスに
//      一切追加されていない・SILENCE_WARNING_TEXT/SILENCE_GOODBYE_TEXT/
//      SILENCE_TIMEOUT_MS/SILENCE_WARNING_GRACE_MSは無変更）
//   F/G. retry storm防止・既存の最大1回retryガードを尊重（新しい
//      「retry自体も失敗」分岐が追加のsendResponseCreateを一切呼ばない）
//   T. latency markerにPIIが含まれない（tool名の固定文字列＋数値msのみ）
//   その他: 安全網音声のonce-per-call_idガード・play失敗時のフォールバック・
//      配線確認（startCall/response.doneハンドラ）・新規Realtime制御イベント
//      （dc.send）を追加していないことの回帰確認。
//
// 実行: node tests/test_fast_turn_hotfix10_rate_limit_fallback.js

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

function extractBlock(src, signature, fromIndex) {
    const idx = src.indexOf(signature, fromIndex || 0);
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

const FN = {
    playToolContinuationRateLimitFallback: extractFunctionSource(SRC, 'playToolContinuationRateLimitFallback', false),
    isStaleCallEvent: extractFunctionSource(SRC, 'isStaleCallEvent', false),
    emitFastTurnToolLatencySummary: extractFunctionSource(SRC, 'emitFastTurnToolLatencySummary', false),
};

function buildFallbackSandbox(overrides) {
    const events = [];
    const playCalls = [];
    const endedListeners = [];

    const rateLimitFallbackAudioEl = {
        currentTime: 0,
        addEventListener(type, fn) {
            if (type === 'ended') endedListeners.push(fn);
        },
        removeEventListener() {},
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
        console: { log: () => {} },
    };
    const state = Object.assign({
        rateLimitFallbackState: 'ready',
        rateLimitFallbackAudioEl,
        toolContinuationRateLimitFallbackPlayedForCallId: null,
        ended: false,
        callGeneration: 1,
    }, overrides || {});
    delete state.playRejects;
    Object.assign(context, state);

    vm.createContext(context);
    vm.runInContext([FN.isStaleCallEvent, FN.playToolContinuationRateLimitFallback].join('\n\n'), context);

    return { ctx: context, events, playCalls, fireEnded: () => endedListeners.forEach((fn) => fn()) };
}

function buildLatencySandbox(overrides) {
    const events = [];
    const logs = [];
    const context = {
        performance: { now: () => context.__now },
        pushTimelineEvent: (text) => { events.push(text); },
        console: { log: (line) => { logs.push(line); } },
    };
    context.__now = (overrides && overrides.__now) || 0;
    const state = Object.assign({
        toolCallStartedAtForLatency: null,
        turnLatencyToolDurationMs: null,
        toolContinuationResponseCreateSentAt: null,
    }, overrides || {});
    delete state.__now;
    Object.assign(context, state);
    vm.createContext(context);
    vm.runInContext(FN.emitFastTurnToolLatencySummary, context);
    return { ctx: context, events, logs };
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

console.log('FAST TURN EMERGENCY HOTFIX 10 — rate-limit fallback contract tests');
console.log('source: ' + ENGINE_JS_PATH);
console.log('');

(async () => {

// ===== playToolContinuationRateLimitFallback =====

await test('D/正常系: 再生可能な状態なら安全網音声を再生し、"ended"発火でonEndedOrUnavailableを1回だけ呼ぶ', () => {
    const { ctx, events, playCalls, fireEnded } = buildFallbackSandbox({});
    let endedCalls = 0;
    const played = vm.runInContext('playToolContinuationRateLimitFallback', ctx)(
        'call_1', 1, () => { endedCalls++; }
    );
    assert.strictEqual(played, true, 'must report that playback was actually attempted');
    assert.strictEqual(playCalls.length, 1);
    assert.strictEqual(endedCalls, 0, 'callback must not fire before the audio actually ends');
    fireEnded();
    assert.strictEqual(endedCalls, 1, 'callback must fire exactly once when the audio ends');
    assert.ok(events.some((e) => e.includes('TOOL_CONTINUATION_RATE_LIMIT_FALLBACK_PLAY_REQUESTED')));
    assert.ok(events.some((e) => e.includes('TOOL_CONTINUATION_RATE_LIMIT_FALLBACK_AUDIO_ENDED')));
});

await test('once-per-call_id: 同一call_idへの2回目の呼び出しは再生せず、即座にonEndedOrUnavailableを呼ぶ（無音のまま放置しない）', () => {
    const { ctx, playCalls } = buildFallbackSandbox({});
    const fn = vm.runInContext('playToolContinuationRateLimitFallback', ctx);
    let firstCbCalls = 0, secondCbCalls = 0;
    fn('call_dup', 1, () => { firstCbCalls++; });
    assert.strictEqual(playCalls.length, 1);
    fn('call_dup', 1, () => { secondCbCalls++; });
    assert.strictEqual(playCalls.length, 1, 'must not play a second time for the same call_id');
    assert.strictEqual(secondCbCalls, 1, 'even when skipped, the callback must still fire immediately so the caller never waits forever');
});

await test('プリロード未完了/失敗時は再生せず、即座にonEndedOrUnavailableを呼ぶ（silence timerを無期限に遅延させない）', () => {
    const { ctx, playCalls } = buildFallbackSandbox({ rateLimitFallbackState: 'preload_failed' });
    let cbCalls = 0;
    const played = vm.runInContext('playToolContinuationRateLimitFallback', ctx)('call_2', 1, () => { cbCalls++; });
    assert.strictEqual(played, false);
    assert.strictEqual(playCalls.length, 0);
    assert.strictEqual(cbCalls, 1, 'the caller must be released immediately when the fallback cannot play');
});

await test('通話世代ガード: 呼び出し時点で既に古い世代(isStaleCallEvent=true)の場合は再生せず、即座にコールバックを呼ぶ', () => {
    const { ctx, playCalls } = buildFallbackSandbox({ callGeneration: 2 });
    let cbCalls = 0;
    const played = vm.runInContext('playToolContinuationRateLimitFallback', ctx)('call_3', 1, () => { cbCalls++; });
    assert.strictEqual(played, false);
    assert.strictEqual(playCalls.length, 0);
    assert.strictEqual(cbCalls, 1);
});

await test('call_idが無い場合は再生せず、即座にコールバックを呼ぶ', () => {
    const { ctx, playCalls } = buildFallbackSandbox({});
    let cbCalls = 0;
    const played = vm.runInContext('playToolContinuationRateLimitFallback', ctx)(null, 1, () => { cbCalls++; });
    assert.strictEqual(played, false);
    assert.strictEqual(playCalls.length, 0);
    assert.strictEqual(cbCalls, 1);
});

await test('play()がrejectしても例外が外へ伝播せず、コールバックが必ず呼ばれる（通話自体を止めない）', async () => {
    const { ctx } = buildFallbackSandbox({ playRejects: true });
    let cbCalls = 0;
    assert.doesNotThrow(() => {
        vm.runInContext('playToolContinuationRateLimitFallback', ctx)('call_4', 1, () => { cbCalls++; });
    });
    // play()のPromise rejectionはマイクロタスクなので1tick待つ。
    await Promise.resolve().then(() => Promise.resolve());
    assert.strictEqual(cbCalls, 1, 'play() rejection must still release the caller exactly once');
});

// ===== emitFastTurnToolLatencySummary（§15 latency summary） =====

await test('§15: 4つのdelta（backend_ms/result_to_response_create_ms/response_create_to_first_audio_ms/total_ms）を正しく計算する', () => {
    // T1(tool call start)=1000, backend_ms=500（T2=1500）,
    // T6(response.create sent)=2000, T8(first audio, "now")=2300
    const { ctx, events, logs } = buildLatencySandbox({
        toolCallStartedAtForLatency: 1000,
        turnLatencyToolDurationMs: 500,
        toolContinuationResponseCreateSentAt: 2000,
        __now: 2300,
    });
    vm.runInContext('emitFastTurnToolLatencySummary("check_availability")', ctx);
    const line = events.find((e) => e.indexOf('FAST_TURN_TOOL_LATENCY') === 0);
    assert.ok(line, 'must emit exactly one FAST_TURN_TOOL_LATENCY line');
    assert.ok(line.includes('backend_ms=500'), 'backend_ms must equal turnLatencyToolDurationMs (T1->T2) unchanged: ' + line);
    assert.ok(line.includes('result_to_response_create_ms=500'), 'result(T2=1500)->continuation-request(T6=2000) must be 500ms: ' + line);
    assert.ok(line.includes('response_create_to_first_audio_ms=300'), 'continuation-request(T6=2000)->first-audio(now=2300) must be 300ms: ' + line);
    assert.ok(line.includes('total_ms=1300'), 'total (T1=1000 -> now=2300) must be 1300ms: ' + line);
    assert.strictEqual(logs.length, 1, 'must also console.log exactly once (existing RESPONSE_DONE_FAILED marker convention)');
});

await test('§15/T: latency markerにPIIが含まれない（tool名の固定文字列＋数値msのみ）', () => {
    const { ctx, events } = buildLatencySandbox({
        toolCallStartedAtForLatency: 0,
        turnLatencyToolDurationMs: 120,
        toolContinuationResponseCreateSentAt: 200,
        __now: 400,
    });
    vm.runInContext('emitFastTurnToolLatencySummary("check_availability")', ctx);
    const line = events.find((e) => e.indexOf('FAST_TURN_TOOL_LATENCY') === 0);
    // フィールド名を除去した後、残るのは"check_availability"という固定Tool名と
    // 数値・記号のみであるべき（自由テキスト・会話内容・電話番号等が一切
    // 混入していないことを機械的に確認する）。
    const stripped = line
        .replace('FAST_TURN_TOOL_LATENCY (tool=check_availability', '')
        .replace(/backend_ms=(null|\d+)/, '')
        .replace(/result_to_response_create_ms=(null|\d+)/, '')
        .replace(/response_create_to_first_audio_ms=\d+/, '')
        .replace(/total_ms=\d+/, '');
    assert.strictEqual(stripped.replace(/[,\s()]/g, ''), '', 'no free-text/PII must remain in the marker beyond the known fixed fields: "' + stripped + '"');
});

await test('必要な時刻が未確定（toolContinuationResponseCreateSentAtがnull）の場合は何も出力しない（誤ったサマリを出さない）', () => {
    const { ctx, events, logs } = buildLatencySandbox({
        toolCallStartedAtForLatency: 1000,
        turnLatencyToolDurationMs: 500,
        toolContinuationResponseCreateSentAt: null,
        __now: 2300,
    });
    vm.runInContext('emitFastTurnToolLatencySummary("check_availability")', ctx);
    assert.strictEqual(events.length, 0);
    assert.strictEqual(logs.length, 0);
});

await test('1回出力したら次回まで再送信しない（toolContinuationResponseCreateSentAtをnullへ戻す）', () => {
    const { ctx, events } = buildLatencySandbox({
        toolCallStartedAtForLatency: 1000,
        turnLatencyToolDurationMs: 500,
        toolContinuationResponseCreateSentAt: 2000,
        __now: 2300,
    });
    vm.runInContext('emitFastTurnToolLatencySummary("check_availability")', ctx);
    assert.strictEqual(events.length, 1);
    vm.runInContext('emitFastTurnToolLatencySummary("check_availability")', ctx);
    assert.strictEqual(events.length, 1, 'a second call with no new T6 timestamp must not emit a duplicate/stale summary');
});

// ===== 構造/回帰チェック（silence timeout・retry storm防止・配線） =====

await test('E/回帰: SILENCE_TIMEOUT_MS/SILENCE_WARNING_GRACE_MSの値は無変更（30000ms/8000ms）', () => {
    const timeoutMatch = SRC.match(/const SILENCE_TIMEOUT_MS = (\d+);/);
    const graceMatch = SRC.match(/const SILENCE_WARNING_GRACE_MS = (\d+);/);
    assert.ok(timeoutMatch && graceMatch, 'silence timeout constants not found');
    assert.strictEqual(Number(timeoutMatch[1]), 30000, 'SILENCE_TIMEOUT_MS must remain unchanged (ユーザー指示: 秒数変更は禁止)');
    assert.strictEqual(Number(graceMatch[1]), 8000, 'SILENCE_WARNING_GRACE_MS must remain unchanged');
});

await test('E/回帰: SILENCE_WARNING_TEXT/SILENCE_GOODBYE_TEXTの文言は無変更', () => {
    assert.ok(SRC.includes("const SILENCE_WARNING_TEXT = 'お声が確認できないため、このままですとお電話を終了します。';"));
    assert.ok(SRC.includes("const SILENCE_GOODBYE_TEXT = 'お電話を終了させていただきます。ありがとうございました。';"));
});

await test('D: budgetLooksInsufficient分岐は追加のsendResponseCreate()を一切呼ばない（新規Realtime応答を試みない）', () => {
    const block = extractBlock(SRC, 'if (budgetLooksInsufficient) {');
    assert.ok(!block.includes('sendResponseCreate('), 'the skip branch must never attempt a new response.create; local fallback audio only');
    assert.ok(block.includes('rateLimitFallbackNeededForCallId = toolContinuationTraceCallId;'), 'the skip branch must arm the local fallback audio');
});

await test('F/G: 「retryも失敗」分岐(RETRY_ALSO_FAILED)は追加のsendResponseCreate()を一切呼ばない（無限retry禁止）', () => {
    // else-if全体をブロックとして抜き出すため、else if (...) { の行から探す。
    const idx = SRC.indexOf('} else if (respStatus === \'failed\' && isRateLimitedForRetry && toolContinuationTraceActive\n                            && toolContinuationTraceCallId\n                            && toolContinuationRateLimitRetryUsedForCallId === toolContinuationTraceCallId) {');
    assert.notStrictEqual(idx, -1, 'RETRY_ALSO_FAILED else-if branch not found (has the rate-limit handling been restructured?)');
    // ブロックの終わりを、この直後に続く「if (rateLimitFallbackNeededForCallId !== null) {」の
    // 直前までとする（else-ifブロック自体は単純な{...}なので、開始"{"から対応する"}"までを取る）。
    const braceStart = SRC.indexOf('{', idx + '} else if ('.length - 1);
    let depth = 0, i = braceStart;
    for (; i < SRC.length; i++) {
        if (SRC[i] === '{') depth++;
        else if (SRC[i] === '}') { depth--; if (depth === 0) { i++; break; } }
    }
    const block = SRC.slice(braceStart, i);
    assert.ok(!block.includes('sendResponseCreate('), 'the "retry already failed again" branch must never send another response.create (retry storm forbidden)');
    assert.ok(block.includes('rateLimitFallbackNeededForCallId = toolContinuationTraceCallId;'));
});

await test('E: HOTFIX10で新規追加したrate-limit fallback関連コードにendCall(の呼び出しが一切無い（rate_limit_exceeded単体では通話を終了しない）', () => {
    const fallbackMechanismBlock = extractFunctionSource(SRC, 'playToolContinuationRateLimitFallback', false);
    assert.ok(!fallbackMechanismBlock.includes('endCall('), 'the local fallback-audio mechanism itself must never end the call directly');
});

await test('配線確認: response.doneの一般的なsilence timer開始箇所がdeferSilenceTimerForToolContinuationRateLimitを尊重する', () => {
    const block = extractBlock(SRC, 'if (!responseHasFunctionCall) {\n                    if (deferSilenceTimerForToolContinuationRateLimit) {');
    assert.ok(block.includes("pushTimelineEvent('SILENCE_TIMER_START_DEFERRED"));
    assert.ok(block.includes('startSilenceTimerIfNeeded(callGeneration'));
});

await test('配線確認: startCall()内でresetRateLimitFallbackCallState()が呼ばれている（前回通話の状態を持ち越さない）', () => {
    const idx = SRC.indexOf('async function startCall');
    assert.notStrictEqual(idx, -1, 'startCall not found');
    const window = SRC.slice(idx, idx + 14000);
    assert.ok(window.includes('resetRateLimitFallbackCallState();'), 'startCall() must reset the rate-limit fallback playback position/guards for each new call');
});

await test('配線確認: preloadRateLimitFallbackAudio()がページ読み込み時に呼ばれている', () => {
    assert.ok(SRC.includes('preloadRateLimitFallbackAudio();'));
});

await test('once-per-call_idガード自体がplayToolContinuationRateLimitFallback内に実装されている', () => {
    const fn = extractFunctionSource(SRC, 'playToolContinuationRateLimitFallback', false);
    assert.ok(fn.includes('toolContinuationRateLimitFallbackPlayedForCallId === callIdForGuard'));
    assert.ok(fn.includes('toolContinuationRateLimitFallbackPlayedForCallId = callIdForGuard;'));
});

await test('エンドポイント整合性: フロントエンドのURLとバックエンドのルートパスが一致している', () => {
    assert.ok(SRC.includes("'/realtime-voice/rate-limit-fallback-audio'"), 'frontend URL must reference the new endpoint path');
    const routerPath = path.join(__dirname, '..', 'app', 'routers', 'realtime_voice.py');
    const routerSrc = fs.readFileSync(routerPath, 'utf8');
    assert.ok(routerSrc.includes('@router.get("/rate-limit-fallback-audio")'), 'backend router must expose the matching path');
});

await test('DC-SEND回帰: 本HOTFIXは新規Realtime制御イベント(dc.send)を1つも追加していない（ローカル音声のみで対処するという設計方針の確認）', () => {
    const sendCount = (SRC.match(/dc\.send\(JSON\.stringify\(/g) || []).length;
    assert.strictEqual(sendCount, 10, 'FAST TURN EMERGENCY HOTFIX 10 must not add any new dc.send() call site; it only adds a local <audio> fallback and defers an existing local timer, exactly like PHASE O5.5 before it (baseline 10 established by test_speak_then_work_ack_fallback.js)');
});

await test('既存のbounded retry（budget充分時）は従来どおり1回のsendResponseCreateを維持している（回帰）', () => {
    assert.ok(SRC.includes("sendResponseCreate('tool_continuation_rate_limit_retry');"), 'the existing HOTFIX 6/7 bounded retry call must remain intact and unique');
    const count = (SRC.match(/sendResponseCreate\('tool_continuation_rate_limit_retry'\)/g) || []).length;
    assert.strictEqual(count, 1, 'the bounded retry must still be sent from exactly one call site (no duplication introduced)');
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
process.exit(failed > 0 ? 1 : 0);

})();
