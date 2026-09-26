'use strict';

/**
 * RECEPTRA — FAST TURN HOTFIX 3
 * MULTI-SLOT FIRST UTTERANCE SILENCE / SLOW RESPONSE
 *
 * 背景: 実機で「明日、二人で12時に予約したいです。」（DATE/TIME/PARTY_SIZE/
 * 予約intentを含む1発話）を1回話しても「何も返事をしないことがある」、かつ
 * 全般的に「話し終わってからAIの返事が始まるまでが遅い」という不具合が報告
 * された。前フェーズ（0a93404、AI SPEAKING PROTECTION修正）はこの症状を
 * 解決していない前提で、今回はPHASE1で読み取り専用の監査のみを行い、
 * PHASE2としてFULL TURN LATENCY TRACE（TURN_INPUT_START〜TURN_RESPONSE_DONE、
 * turnId/elapsedMs/deltaFromPreviousMs付き）を追加した。挙動（タイマー・
 * 閾値・commit/response.create送信ロジック）は一切変更していない。
 *
 * 監査で判明した重要な事実（このテストが固定するリグレッションガードの根拠）:
 *   - 「明日、二人で12時に予約したいです」はこの通話で最初のユーザー発話
 *     であるため、expectedAnswerTypeはまだ'NONE'のまま（classifyExpectedAnswerType
 *     はAI自身の発話transcriptに対してのみ呼ばれ、ユーザーの最初の発話より
 *     前にはまだ一度も呼ばれていない）。これは前フェーズ（TIME/PARTY_SIZE=
 *     SHORT_ANSWER）とは異なり、USER_TURN_3S_FALLBACK機構のスコープ内である
 *     ことを意味する（前フェーズの「無関係」という結論はSHORT_ANSWER限定であり、
 *     このNONEターンには適用されない）。
 *   - armUserTurnFallbackTimerは、speech_stopped後3秒以内に
 *     input_audio_buffer.committed（またはconversation.item.created/
 *     response.created/function_call）が正常に届けば、
 *     userTurnFallbackNormalCompletionSeen=trueにより手動commitを送らない
 *     （race対策・normal_completion_won_race）。正常な1発話ターンでは、
 *     この経路が「何もしない」ことを実行ベースで確認する。
 *   - response.audio.delta等、1ターン中に何度も発火するイベントでも、
 *     TURN_TRACEの各markerは1ターンにつき1回のみ記録される（重複防止）。
 *   - Tool呼び出しターン（function_callを含む中間応答）では、
 *     TURN_RESPONSE_DONEはresponseHasFunctionCall===trueの間は記録されず、
 *     最終応答まで同一turnIdでトレースが継続する。
 *
 * 本テストは、実際のarmUserTurnFallbackTimer/cancelUserTurnFallbackTimer/
 * maybeSendUserTurnFallbackCommit/isStaleCallEvent/msSinceと、新規追加した
 * startTurnLatencyTrace/pushTurnLatencyTrace/endTurnLatencyTrace/
 * resetTurnLatencyTraceの実ソースをそのままvmサンドボックスで実行し、
 * 「明日、二人で12時に予約したいです」を模した1ターンのイベント列を
 * 実際に流し込んで検証する（static substringだけに頼らない）。
 *
 * 重要な限界（正直に明記する）: クライアントJSはinput_audio_transcriptionを
 * 一切構成していない（0件、監査済み）ため、発話内容そのもの
 * （DATE=明日/TIME=12:00/PARTY_SIZE=2の実際の保持・再質問回避）はクライアント
 * コードからは観測も実行もできない。これは100% OpenAIモデル側（システム
 * プロンプト）の責務であり、app/services/realtime_voice_ai.pyの既存
 * 「既に分かっている情報を聞き直さない（重要）」節（前フェーズ監査済み）が
 * 対応する。本テストが実行検証できるのは、クライアント側の
 * イベント処理・fallbackタイマー・latency trace機構が、この種の1発話ターンを
 * 妨害・重複・停止させないことのみである。実際にAIが何を話すかは実機確認が
 * 必要（実測していない数字・内容を断定しない）。
 *
 * 実行: node tests/test_turn_latency_trace.js
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

console.log('FAST TURN HOTFIX 3 — MULTI-SLOT FIRST UTTERANCE / TURN LATENCY TRACE tests');
console.log('source: ' + SRC_PATH);
console.log('');

function extractFunction(src, signature) {
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

// ---------------------------------------------------------------------
// サンドボックス構築: USER_TURN_3S_FALLBACK一式 + 新規FULL TURN LATENCY
// TRACE一式の実ソースをそのまま実行する。setTimeout/clearTimeoutは
// 3秒待たずにテストできるよう、コールバックを保持するだけの偽実装にする。
// ---------------------------------------------------------------------
function buildSandbox() {
    const armSrc = extractFunction(SRC, 'function armUserTurnFallbackTimer(myGeneration) {');
    const cancelSrc = extractFunction(SRC, 'function cancelUserTurnFallbackTimer(reason) {');
    const maybeSendSrc = extractFunction(SRC, 'function maybeSendUserTurnFallbackCommit(myGeneration, forGeneration) {');
    const isStaleSrc = extractFunction(SRC, 'function isStaleCallEvent(eventGeneration) {');
    const msSinceSrc = extractFunction(SRC, 'function msSince(t) {');
    const startTraceSrc = extractFunction(SRC, 'function startTurnLatencyTrace(myCallGeneration) {');
    const pushTraceSrc = extractFunction(SRC, 'function pushTurnLatencyTrace(marker, extraNote) {');
    const endTraceSrc = extractFunction(SRC, 'function endTurnLatencyTrace(marker, extraNote) {');
    const resetTraceSrc = extractFunction(SRC, 'function resetTurnLatencyTrace(reason) {');

    const events = [];
    const pendingTimers = new Map();
    let nextTimerId = 1;
    let clockMs = 0;

    const sandbox = {
        // ===== USER_TURN_3S_FALLBACK state =====
        expectedAnswerType: 'NONE',
        toolContinuationTraceActive: false,
        USER_TURN_FALLBACK_GRACE_MS: 3000,
        userTurnFallbackGeneration: 0,
        userTurnFallbackTimerId: null,
        userTurnFallbackArmedAt: null,
        userTurnFallbackArmedForGeneration: null,
        userTurnFallbackCommitSentGeneration: null,
        userTurnFallbackNormalCompletionSeen: false,
        userTurnFallbackCommitSentAt: null,
        userTurnFallbackCommitCallGeneration: null,
        ended: false,
        callGeneration: 1,
        dc: { readyState: 'open', send: (payload) => { events.push({ type: 'dc.send', payload: JSON.parse(payload) }); } },

        // ===== FULL TURN LATENCY TRACE state =====
        turnLatencyTraceId: null,
        turnLatencyTraceSeq: 0,
        turnLatencyTraceCallGeneration: null,
        turnLatencyTraceT0: null,
        turnLatencyTraceLastAt: null,
        turnLatencyTraceMarkersSeen: {},
        responseState: 'idle',

        pushTimelineEvent: (msg) => { events.push({ type: 'timeline', msg }); },
        performance: { now: () => clockMs },
        setTimeout: (cb, ms) => {
            const id = nextTimerId++;
            pendingTimers.set(id, { cb, ms });
            return id;
        },
        clearTimeout: (id) => { pendingTimers.delete(id); },
        console,
    };
    vm.createContext(sandbox);
    vm.runInContext(
        [msSinceSrc, isStaleSrc, armSrc, cancelSrc, maybeSendSrc, startTraceSrc, pushTraceSrc, endTraceSrc, resetTraceSrc].join('\n\n'),
        sandbox
    );

    return {
        sandbox,
        events,
        pendingTimers,
        advanceClock: (ms) => { clockMs += ms; },
        fireDueTimers: () => {
            // pendingTimersのうち、armされた時点からmsが経過したものを発火する
            // （本テストでは単純化のため、明示的にfireTimer(id)を呼ぶ形で使う）。
        },
        fireTimer: (id) => {
            const t = pendingTimers.get(id);
            if (!t) return;
            pendingTimers.delete(id);
            t.cb();
        },
        timelineTexts: () => events.filter(e => e.type === 'timeline').map(e => e.msg),
        dcSends: () => events.filter(e => e.type === 'dc.send'),
    };
}

// =======================================================================
// NEW TEST CASE: 「明日、二人で12時に予約したいです」を模した1発話ターン
// =======================================================================

test('NEW TEST CASE) 通話で最初のユーザー発話（多スロット予約発話を想定）では、speech_stopped時点でexpectedAnswerTypeがまだ\'NONE\'であり、USER_TURN_3S_FALLBACKのスコープ内であることの固定（前フェーズのSHORT_ANSWER限定の結論とは異なる新事実）', () => {
    const { sandbox } = buildSandbox();
    assert.strictEqual(sandbox.expectedAnswerType, 'NONE',
        'a first-turn utterance (before any classifyExpectedAnswerType call from an AI question) must still be NONE — this is what puts it in USER_TURN_3S_FALLBACK scope, unlike the previous phase\'s SHORT_ANSWER (TIME/PARTY_SIZE) turns');
});

test('NEW TEST CASE) 正常系: speech_stopped→(3秒以内に)committed→item_created→response.created が届く場合、3秒fallbackはarmされるが手動commitは送信されない（normal_completion_won_race）。予約意図/DATE/TIME/PARTY_SIZEを含む1発話が「Forced Commit待ち」で止まらないことの証拠', () => {
    const { sandbox, fireTimer, dcSends, timelineTexts } = buildSandbox();

    // speech_stopped相当: 3秒fallbackをarmする
    sandbox.armUserTurnFallbackTimer(sandbox.callGeneration);
    assert.notStrictEqual(sandbox.userTurnFallbackTimerId, null, 'fallback timer must be armed for a NONE-type first turn');
    const armedTimerId = sandbox.userTurnFallbackTimerId;

    // committed相当（3秒以内に正常到着）: 既存の committed ハンドラが行う2つの
    // 操作を実行する（本体はハンドラ内に直接書かれているため、その2行を
    // ここで模擬する。armUserTurnFallbackTimer自体の中身は変更していない）。
    sandbox.userTurnFallbackNormalCompletionSeen = true;
    sandbox.cancelUserTurnFallbackTimer('committed');
    assert.strictEqual(sandbox.userTurnFallbackTimerId, null, 'the timer must be cancelled once a normal committed event arrives');

    // 3秒経過相当（実際にはこの時点でタイマーは既にcancelされているため
    // fireできないはずだが、念のため「もしまだ生きていたら」の安全確認として
    // 直接fire相当のmaybeSendを呼び、race-guardが機能することも確認する。
    sandbox.maybeSendUserTurnFallbackCommit(sandbox.callGeneration, 1);

    const sends = dcSends();
    assert.strictEqual(sends.length, 0, 'no manual input_audio_buffer.commit must be sent for a turn that completed normally — the multi-slot utterance must not be double-committed or interfered with');
    assert.ok(timelineTexts().some(t => t.includes('USER_TURN_FALLBACK_COMMIT_SKIPPED') && t.includes('normal_completion_won_race')),
        'the skip must be logged with the normal_completion_won_race reason, proving the race-guard (not silence) is why nothing was sent');
});

test('NEW TEST CASE) 異常系（今回発見した構造的リスク・観測用): committed/item_createdが正常に届いたのにresponse.createdが一切届かない場合、現行の3秒fallbackは「normal_completion_won_race」としてスキップし、手動commitもresponse.createも一切送らない（＝この経路には現状フォールバックが存在しないことの実行ベースでの証明。証拠不十分のため今回は修正しない。診断のみ追加）', () => {
    const { sandbox, dcSends, timelineTexts } = buildSandbox();

    sandbox.armUserTurnFallbackTimer(sandbox.callGeneration);
    // committed/item_createdは正常に届く（=userTurnFallbackNormalCompletionSeen=true）が、
    // その後response.createdが来ないケースを模す。
    sandbox.userTurnFallbackNormalCompletionSeen = true;
    sandbox.cancelUserTurnFallbackTimer('committed');

    // 3秒fallbackが（もし生きていたら）fireした場合の挙動を確認
    sandbox.maybeSendUserTurnFallbackCommit(sandbox.callGeneration, 1);

    assert.strictEqual(dcSends().length, 0,
        'CONFIRMED GAP: when committed/item_created succeed but response.created never arrives, the existing USER_TURN_3S_FALLBACK mechanism does nothing (by design, since it only guards against a missing *commit*, not a missing *response*). This reproduces exactly the "何も返事をしないことがある" symptom class described in CRITICAL QUESTION 2, but this test does NOT prove it is the actual real-device root cause — only that no client-side recovery exists for this specific window today.');
    assert.ok(timelineTexts().some(t => t.includes('normal_completion_won_race')),
        'the skip reason must be logged so real-device logs can show whether this exact window actually occurs');
});

// =======================================================================
// LATENCY TRACE TEST: marker順序・turnId相関・elapsedMs・cleanup・重複防止・PII
// =======================================================================

test('LATENCY TRACE TEST) 1ターン分のイベント列（TURN_INPUT_START〜TURN_RESPONSE_DONE）を模した場合、全markerが同一turnIdで記録され、要求された順序で並ぶ', () => {
    const { sandbox, timelineTexts } = buildSandbox();

    sandbox.startTurnLatencyTrace(sandbox.callGeneration); // TURN_INPUT_START
    sandbox.pushTurnLatencyTrace('TURN_INPUT_STOP');
    sandbox.pushTurnLatencyTrace('TURN_COMMITTED');
    sandbox.pushTurnLatencyTrace('TURN_RESPONSE_REQUESTED', 'source=server_auto_create_response_on_commit');
    sandbox.pushTurnLatencyTrace('TURN_USER_ITEM_CREATED');
    sandbox.pushTurnLatencyTrace('TURN_RESPONSE_CREATED');
    sandbox.pushTurnLatencyTrace('TURN_FIRST_OUTPUT_ITEM');
    sandbox.pushTurnLatencyTrace('TURN_FIRST_AUDIO_DELTA');
    sandbox.pushTurnLatencyTrace('TURN_AUDIO_PLAYBACK_START');
    sandbox.endTurnLatencyTrace('TURN_RESPONSE_DONE', 'status=completed');

    const traceLines = timelineTexts().filter(t => t.startsWith('TURN_TRACE '));
    const expectedOrder = [
        'TURN_INPUT_START', 'TURN_INPUT_STOP', 'TURN_COMMITTED', 'TURN_RESPONSE_REQUESTED',
        'TURN_USER_ITEM_CREATED', 'TURN_RESPONSE_CREATED', 'TURN_FIRST_OUTPUT_ITEM',
        'TURN_FIRST_AUDIO_DELTA', 'TURN_AUDIO_PLAYBACK_START', 'TURN_RESPONSE_DONE',
    ];
    assert.strictEqual(traceLines.length, expectedOrder.length, 'exactly one line per required marker, no extras, no omissions');
    expectedOrder.forEach((marker, i) => {
        assert.ok(traceLines[i].startsWith('TURN_TRACE ' + marker + ' '), 'marker #' + i + ' must be ' + marker + ' but was: ' + traceLines[i]);
    });

    // turnId correlation: 全行が同じturnIdを持つ
    const turnIdMatch = traceLines[0].match(/turnId=([^,]+),/);
    assert.notStrictEqual(turnIdMatch, null);
    const turnId = turnIdMatch[1];
    traceLines.forEach((line) => {
        assert.ok(line.includes('turnId=' + turnId + ','), 'every marker of one turn must share the same turnId: ' + line);
    });

    // 各行にelapsedMs/deltaFromPreviousMsが数値として存在する
    traceLines.forEach((line) => {
        assert.ok(/elapsedMs=\d+/.test(line), 'elapsedMs must be a recorded number: ' + line);
        assert.ok(/deltaFromPreviousMs=(\d+|null)/.test(line), 'deltaFromPreviousMs must be a number or null: ' + line);
    });
});

test('LATENCY TRACE TEST) 重複防止: response.audio.deltaのように1ターン中に何度も発火するイベントでも、同一markerは1回しか記録されない', () => {
    const { sandbox, timelineTexts } = buildSandbox();
    sandbox.startTurnLatencyTrace(sandbox.callGeneration);
    sandbox.pushTurnLatencyTrace('TURN_FIRST_AUDIO_DELTA');
    sandbox.pushTurnLatencyTrace('TURN_FIRST_AUDIO_DELTA');
    sandbox.pushTurnLatencyTrace('TURN_FIRST_AUDIO_DELTA');
    const deltaLines = timelineTexts().filter(t => t.includes('TURN_TRACE TURN_FIRST_AUDIO_DELTA'));
    assert.strictEqual(deltaLines.length, 1, 'only the first response.audio.delta of the turn should produce a TURN_TRACE line');
});

test('LATENCY TRACE TEST) 重複防止（Tool呼び出しターン想定）: response.createdが1ターン中に2回発火しても（中間応答＋最終応答）、TURN_RESPONSE_CREATEDは初回のみ記録される（既存turnLatencyCommitToResponseMsと同じ設計）', () => {
    const { sandbox, timelineTexts } = buildSandbox();
    sandbox.startTurnLatencyTrace(sandbox.callGeneration);
    sandbox.pushTurnLatencyTrace('TURN_RESPONSE_CREATED'); // 中間応答（function_callのみ）
    sandbox.pushTurnLatencyTrace('TURN_RESPONSE_CREATED'); // 最終応答（Tool結果を踏まえた発話）
    const lines = timelineTexts().filter(t => t.includes('TURN_TRACE TURN_RESPONSE_CREATED'));
    assert.strictEqual(lines.length, 1, 'TURN_RESPONSE_CREATED must be recorded only once per turn, matching the existing "first response.created only" latency convention for tool-call turns');
});

test('LATENCY TRACE TEST) cleanup: TURN_RESPONSE_DONEでトレースが終了し、次のspeech_startedでは新しいturnIdが発行される（前ターンの状態が漏れない）', () => {
    const { sandbox, timelineTexts } = buildSandbox();

    sandbox.startTurnLatencyTrace(sandbox.callGeneration);
    sandbox.endTurnLatencyTrace('TURN_RESPONSE_DONE');
    assert.strictEqual(sandbox.turnLatencyTraceId, null, 'trace state must be fully cleared after TURN_RESPONSE_DONE');

    sandbox.startTurnLatencyTrace(sandbox.callGeneration);
    const secondTurnId = sandbox.turnLatencyTraceId;
    assert.notStrictEqual(secondTurnId, null);

    const startLines = timelineTexts().filter(t => t.includes('TURN_TRACE TURN_INPUT_START'));
    assert.strictEqual(startLines.length, 2, 'two separate turns must each produce their own TURN_INPUT_START');
    const firstTurnIdMatch = startLines[0].match(/turnId=([^,]+),/);
    const secondTurnIdMatch = startLines[1].match(/turnId=([^,]+),/);
    assert.notStrictEqual(firstTurnIdMatch[1], secondTurnIdMatch[1], 'the second turn must get a distinct turnId from the first');
});

test('LATENCY TRACE TEST) cleanup（多分割発話の直接証拠になる設計）: 前のターンがTURN_RESPONSE_DONEに到達しないまま次のspeech_startedが来た場合、黙って上書きせずTURN_TRACE_RESETを記録してから新しいターンを開始する', () => {
    const { sandbox, timelineTexts } = buildSandbox();
    sandbox.startTurnLatencyTrace(sandbox.callGeneration); // 1発話目の途中のはずが…
    sandbox.startTurnLatencyTrace(sandbox.callGeneration); // 新しいspeech_startedが来てしまった（VADが発話を分割した可能性）
    const resetLines = timelineTexts().filter(t => t.includes('TURN_TRACE_RESET'));
    assert.strictEqual(resetLines.length, 1, 'an abandoned turn must be explicitly logged as reset, never silently discarded — this is exactly the signal needed to detect "明日、" being VAD-split from the rest of the utterance');
    assert.ok(resetLines[0].includes('reason=new_speech_started_before_previous_turn_done'));
});

test('LATENCY TRACE TEST) PIIなし: 記録される全TURN_TRACE行に、発話内容（漢字を含む自由文）やユーザー個人情報が一切含まれない。marker名・turnId・数値・既知の固定ラベルのみで構成される', () => {
    const { sandbox, timelineTexts } = buildSandbox();
    sandbox.startTurnLatencyTrace(sandbox.callGeneration);
    sandbox.pushTurnLatencyTrace('TURN_INPUT_STOP');
    sandbox.pushTurnLatencyTrace('TURN_COMMITTED');
    sandbox.pushTurnLatencyTrace('TURN_RESPONSE_REQUESTED', 'source=server_auto_create_response_on_commit');
    sandbox.pushTurnLatencyTrace('TURN_USER_ITEM_CREATED');
    sandbox.pushTurnLatencyTrace('TURN_RESPONSE_CREATED');
    sandbox.endTurnLatencyTrace('TURN_RESPONSE_DONE', 'status=completed');

    const traceLines = timelineTexts().filter(t => t.startsWith('TURN_TRACE '));
    // 許可されたトークン以外の漢字・仮名（＝発話内容の混入）が含まれていないことを
    // 確認する。既知の固定ラベル（marker名・reasonラベル等）はASCII英数字と
    // アンダースコア・記号のみで構成されているため、CJK文字が1文字でも
    // 含まれていればPII混入の疑いとして失敗させる。
    const cjkPattern = /[぀-ヿ㐀-䶿一-鿿]/;
    traceLines.forEach((line) => {
        assert.ok(!cjkPattern.test(line), 'TURN_TRACE line must contain no Japanese text (transcript/PII) — found in: ' + line);
    });
    // 明日/二人/12時等、今回の実機報告発話に含まれる語が万一混入していないかも
    // 直接確認する（二重の安全確認）。
    const forbiddenWords = ['明日', '二人', '12時', '予約したい'];
    traceLines.forEach((line) => {
        forbiddenWords.forEach((w) => {
            assert.ok(!line.includes(w), 'TURN_TRACE line must never contain the reported utterance text ("' + w + '"): ' + line);
        });
    });
});

// =======================================================================
// REGRESSION: 新規追加が既存のUSER_TURN_3S_FALLBACK等の挙動を変更していないこと
// =======================================================================

test('REGRESSION) armUserTurnFallbackTimerの冒頭ガードは今も expectedAnswerType!==\'NONE\' で即returnする（今回の追加が既存ガードを一切変更していないことの固定）', () => {
    const idx = SRC.indexOf('function armUserTurnFallbackTimer(myGeneration)');
    assert.notStrictEqual(idx, -1);
    const braceStart = SRC.indexOf('{', idx);
    const firstLines = SRC.slice(braceStart, braceStart + 200);
    assert.ok(/if\s*\(expectedAnswerType\s*!==\s*'NONE'\)\s*return;/.test(firstLines),
        'the NONE-only scope guard must remain exactly as before');
});

test('REGRESSION) dc.send()呼び出し箇所は今回も増えていない（新規TURN_TRACE機構は診断ログのみで、Realtime APIへの新規送信を一切追加していないことの固定。既存9箇所のまま）', () => {
    // 単純な /dc\.send\(/ の行数カウントだと、"dc.send()"という語を含む
    // コメント行（本フェーズで追加したものも含む）まで誤って数えてしまうため、
    // 実際のコード行（行頭がdc.send(で始まる行）のみを数える。
    const actualCallLines = SRC.split('\n').filter(line => line.trim().startsWith('dc.send('));
    assert.strictEqual(actualCallLines.length, 9, 'dc.send() call-site count must remain 9 — FAST TURN HOTFIX 3 adds zero new Realtime API sends (diagnostic-only phase)');
});

test('REGRESSION) response.output_item.added / response.audio.delta の新規ハンドラは、既存のoutput_audio_buffer.started等のif/else-ifチェーンの外側に独立したif文として追加されており、既存分岐の条件・構造を変更していない', () => {
    const idx = SRC.indexOf("if (type === 'response.output_item.added') {");
    assert.notStrictEqual(idx, -1, 'new standalone handler for response.output_item.added must exist');
    const block = SRC.slice(idx, idx + 400);
    assert.ok(block.includes("pushTurnLatencyTrace('TURN_FIRST_OUTPUT_ITEM')"));
    assert.ok(block.includes("if (type === 'response.audio.delta')"));
    assert.ok(block.includes("pushTurnLatencyTrace('TURN_FIRST_AUDIO_DELTA')"));
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
if (failed > 0) process.exit(1);
