'use strict';

/**
 * RECEPTRA — FAST TURN HOTFIX 6
 * TOOL CONTINUATION RATE_LIMIT_EXCEEDED — bounded recovery
 *
 * 背景: FAST TURN HOTFIX 5で追加したRESPONSE_DONE_FAILED診断マーカーにより、
 * 実機で直接：
 *   status=failed, status_details_type=failed,
 *   error_type=tokens, error_code=Rate_limit_exceeded,
 *   toolContinuationActive=true, responseCreateCategory=tool_result
 * が観測された。これはcheck_availability等のTool結果を受けた2回目の
 * response.create（Tool継続response）が、OpenAI Realtime API側のrate limit
 * によりfailedになったことを示す直接証拠である。
 *
 * 本パッチは、この「Tool継続中・rate limit由来」という証拠がある場合に限り、
 * call_idごとに最大1回だけ、追加の待機時間を挟まずresponse.createを
 * 再送する、bounded（上限付き）recoveryを追加した。
 *   - 固定delay/exponential backoffは、公式ドキュメントから安全な待機秒数を
 *     確認できなかったため、勘の数値を避けて意図的に追加していない。
 *   - retry storm防止: 送信前に即座にtoolContinuationRateLimitRetryUsedFor
 *     CallIdをセットするため、同一call_idへの2回目のretryは絶対に発生しない。
 *   - 汎用のsendResponseCreate()をそのまま使うため、新しいdc.send()呼び出し
 *     経路は増えない。
 *   - Tool出力の再送信（function_call_output）やcreate_reservationの再実行は
 *     一切行わない（response.createの再送のみ）。
 *
 * 本テストは、実際のRESPONSE_DONE_FAILEDブロック（rate limit retryロジックを
 * 含む）の実ソースをvmサンドボックスで実行し、以下を確認する:
 *   A. rate limitではない通常の失敗ではretryしない
 *   B. Rate_limit_exceededを正しく検知する（大文字小文字を問わない）
 *   C. Tool継続中・rate limit由来の失敗でのみ、最大1回のretryを行う
 *   D. retryはsendResponseCreate()を1回だけ呼ぶ（新しいdc.send経路を増やさない）
 *   E. 同一call_idへの2回目の失敗では、絶対に2回目のretryを送信しない
 *   F. response.createの重複送信がないこと（retry含めて合計送信回数を確認）
 *   G. call_idが異なれば独立してretry判定される（call_id相関）
 *   H. response_id（の末尾8文字）がretryログに正しく相関記録される
 *   I. Tool出力（function_call_output）の再送信は一切行わない
 *   J. create_reservationの再実行は一切行わない
 *   K. dc.send()呼び出し箇所数は増えていない（既存9箇所のまま）
 *   L〜S. PHONE/NAME/TIME/Forced Commit/FIRST ANSWER/NOISE RECOVERY/
 *        AI SPEAKING PROTECTION/TURN TRACE/T0〜T10は本パッチが一切触れて
 *        いないロジックであり、既存の全JSテストファイル（test_phone_
 *        forced_commit.js等）がこのコミットでも全てpassすることで
 *        regressionが無いことを確認する（本ファイルでは重複実装しない）。
 *
 * 実行: node tests/test_rate_limit_recovery.js
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

console.log('FAST TURN HOTFIX 6 — TOOL CONTINUATION RATE_LIMIT_EXCEEDED bounded recovery tests');
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

// response.doneのstatus=failed/incompleteブロック全体（RESPONSE_DONE_FAILED
// マーカー本体 + 今回追加のbounded retryロジック）を丸ごと抽出する。
const FAILED_BLOCK_SRC = extractBlock(
    SRC,
    "if (respStatus === 'failed' || respStatus === 'incomplete') {"
);

function makeSandbox(overrides) {
    const consoleLogs = [];
    const timelineEvents = [];
    const sendResponseCreateCalls = [];
    const subStatusText = { textContent: '' };
    const sandbox = {
        respStatus: null,
        msg: { response: null },
        toolContinuationTraceActive: false,
        toolContinuationTraceCallId: null,
        toolContinuationRateLimitRetryUsedForCallId: null,
        // FAST TURN HOTFIX 7で追加された、rate limit残量に基づくretryスキップ
        // 判定に使う状態。デフォルトはnull（未取得＝比較材料が無い）とし、
        // HOTFIX 6時点の「無条件でretryを許可する」動作をテストのデフォルト
        // として維持する（既存A〜Kのテストが引き続き無変更で通ることを保証する）。
        // 個別テストでoverridesとして上書きし、insufficient budgetの場合の
        // 挙動を検証する。
        lastKnownRateLimitRemainingTokens: null,
        lastObservedResponseInputTokens: null,
        turnLatencyTraceId: null,
        lastResponseReasonCategoryForDiag: 'unknown',
        debugMode: false,
        subStatusText,
        pushTimelineEvent: (t) => timelineEvents.push(t),
        console: { log: (...args) => consoleLogs.push(args) },
        sendResponseCreate: (reason) => { sendResponseCreateCalls.push(reason); return true; },
    };
    Object.assign(sandbox, overrides);
    vm.createContext(sandbox);
    return {
        sandbox,
        run: () => vm.runInContext(FAILED_BLOCK_SRC, sandbox),
        timelineEvents,
        consoleLogs,
        sendResponseCreateCalls,
        subStatusText,
    };
}

// =======================================================================
// A. rate limitではない通常の失敗ではretryしない
// =======================================================================
test('A) status=failedだがerror_codeがrate_limit_exceeded以外の場合、retryは一切行われない', () => {
    const ctx = makeSandbox({
        respStatus: 'failed',
        msg: { response: { id: 'resp_other_fail', status: 'failed', status_details: { error: { type: 'server_error', code: 'internal_error' } } } },
        toolContinuationTraceActive: true,
        toolContinuationTraceCallId: 'call_aaa111',
    });
    ctx.run();
    assert.strictEqual(ctx.sendResponseCreateCalls.length, 0, 'no retry for a non-rate-limit failure');
    assert.strictEqual(ctx.timelineEvents.filter(t => t.includes('RATE_LIMIT_RETRY')).length, 0);
});

test('A2) Tool継続中でない（toolContinuationTraceActive=false）通常ターンのrate limit失敗ではretryしない（今回はTool継続に限定した対応のため）', () => {
    const ctx = makeSandbox({
        respStatus: 'failed',
        msg: { response: { id: 'resp_plain_ratelimited', status: 'failed', status_details: { error: { type: 'tokens', code: 'rate_limit_exceeded' } } } },
        toolContinuationTraceActive: false,
        toolContinuationTraceCallId: null,
    });
    ctx.run();
    assert.strictEqual(ctx.sendResponseCreateCalls.length, 0, 'plain-turn (non tool-continuation) rate limit failures are out of scope for this fix');
});

// =======================================================================
// B. Rate_limit_exceededの検知（大文字小文字を問わない）
// =======================================================================
test('B) 実機で観測された正確な文字列 "Rate_limit_exceeded"（大文字混じり）でも正しく検知される', () => {
    const ctx = makeSandbox({
        respStatus: 'failed',
        msg: { response: { id: 'resp_realdevice', status: 'failed', status_details: { type: 'failed', error: { type: 'tokens', code: 'Rate_limit_exceeded' } } } },
        toolContinuationTraceActive: true,
        toolContinuationTraceCallId: 'call_realdevice123',
    });
    ctx.run();
    assert.strictEqual(ctx.sendResponseCreateCalls.length, 1, 'the exact real-device-observed casing must be detected');
    assert.ok(ctx.timelineEvents.some(t => t.includes('TOOL_CONTINUATION_RATE_LIMIT_RETRY')));
});

test('B2) 小文字standard形（rate_limit_exceeded）でも検知される', () => {
    const ctx = makeSandbox({
        respStatus: 'failed',
        msg: { response: { id: 'resp_lower', status: 'failed', status_details: { error: { type: 'tokens', code: 'rate_limit_exceeded' } } } },
        toolContinuationTraceActive: true,
        toolContinuationTraceCallId: 'call_lower123',
    });
    ctx.run();
    assert.strictEqual(ctx.sendResponseCreateCalls.length, 1);
});

// =======================================================================
// C. Tool継続中・rate limit由来の失敗でのみ、最大1回のretryを行う
// =======================================================================
test('C) Tool継続中・rate limit由来の失敗で、ちょうど1回だけsendResponseCreateが呼ばれる', () => {
    const ctx = makeSandbox({
        respStatus: 'failed',
        msg: { response: { id: 'resp_c1', status: 'failed', status_details: { error: { type: 'tokens', code: 'rate_limit_exceeded' } } } },
        toolContinuationTraceActive: true,
        toolContinuationTraceCallId: 'call_c_111',
    });
    ctx.run();
    assert.strictEqual(ctx.sendResponseCreateCalls.length, 1);
    assert.strictEqual(ctx.sendResponseCreateCalls[0], 'tool_continuation_rate_limit_retry');
    // ユーザーには「確認しています」を維持する（待機中表示に戻さない）。
    assert.strictEqual(ctx.subStatusText.textContent, '確認しています');
});

// =======================================================================
// D. retryはsendResponseCreate()を1回だけ呼ぶ（新しいdc.send経路を増やさない）
// =======================================================================
test('D) retryロジック自体はsendResponseCreate()以外の送信手段（dc.send直呼び出し等）を一切持たない', () => {
    // ブロック全体（RESPONSE_DONE_FAILEDマーカー本体 + retryロジック）が
    // dc.send を直接呼んでいないことをソースレベルで確認する
    // （既存のsendResponseCreate()内部のdc.sendは、この抽出範囲に含まれる
    // 関数呼び出しの「先」であり、ここでのdc.send文字列一致は発生しない）。
    //
    // 注意: このブロックには「新しいdc.send()呼び出し経路は増えない」旨を
    // 説明する日本語コメント（// ...）が含まれており、コメント行を含めた
    // 単純な文字列一致だとそのコメント自体に含まれる「dc.send(」にも
    // 誤って一致してしまう（test_response_done_failed_marker.jsのH
    // テストで見つかった、文字列リテラルをコード代入と誤検知するのと同種の
    // 問題）。実際のコード行のみを対象にするため、各行の// 以降を取り除いた
    // 上で判定する。
    const codeOnly = FAILED_BLOCK_SRC
        .split('\n')
        .map((line) => {
            const idx = line.indexOf('//');
            return idx === -1 ? line : line.slice(0, idx);
        })
        .join('\n');
    assert.ok(!codeOnly.includes('dc.send('), 'the failed-response block must never call dc.send directly, only via sendResponseCreate()');
});

// =======================================================================
// E. 同一call_idへの2回目の失敗では、絶対に2回目のretryを送信しない
// =======================================================================
test('E) 同一call_idに対する2回目のrate limit失敗では、2回目のretryは送信されない（retry storm防止）', () => {
    const ctx = makeSandbox({
        respStatus: 'failed',
        msg: { response: { id: 'resp_e1', status: 'failed', status_details: { error: { type: 'tokens', code: 'rate_limit_exceeded' } } } },
        toolContinuationTraceActive: true,
        toolContinuationTraceCallId: 'call_e_999',
    });
    ctx.run(); // 1回目: retryが送信される
    assert.strictEqual(ctx.sendResponseCreateCalls.length, 1);

    // 2回目: 同じcall_id・同じsandbox（toolContinuationRateLimitRetryUsedForCallIdが
    // 引き継がれる）で、retry自体が再びrate limitでfailedになったケースを模擬。
    ctx.sandbox.msg = { response: { id: 'resp_e2', status: 'failed', status_details: { error: { type: 'tokens', code: 'rate_limit_exceeded' } } } };
    ctx.run();
    assert.strictEqual(ctx.sendResponseCreateCalls.length, 1, 'a second rate-limit failure for the SAME call_id must NOT trigger a second retry');
});

test('E2) 例外が送信前に発生してもtoolContinuationRateLimitRetryUsedForCallIdは既にセット済みのため、2回目のretryは発生しない', () => {
    // sendResponseCreate自体が例外を投げるケースでも、使用済みフラグは
    // sendResponseCreate呼び出しより前にセットされるため、retry stormには
    // ならないことを確認する。
    const ctx = makeSandbox({
        respStatus: 'failed',
        msg: { response: { id: 'resp_throw1', status: 'failed', status_details: { error: { type: 'tokens', code: 'rate_limit_exceeded' } } } },
        toolContinuationTraceActive: true,
        toolContinuationTraceCallId: 'call_throw_1',
        sendResponseCreate: () => { throw new Error('simulated dc failure'); },
    });
    assert.doesNotThrow(() => ctx.run(), 'an exception inside the retry send must be caught by the existing try/catch and must not propagate');
    assert.strictEqual(ctx.sandbox.toolContinuationRateLimitRetryUsedForCallId, 'call_throw_1',
        'the used-flag must already be set before the send attempt, even if that attempt throws');
});

// =======================================================================
// F. response.createの重複送信がないこと
// =======================================================================
test('F) 正常系（rate limitではない）を挟んでも、retry対象の失敗1件につき送信は1回のみ', () => {
    const ctx = makeSandbox({
        toolContinuationTraceActive: true,
        toolContinuationTraceCallId: 'call_f_1',
    });
    // 1回目: completed（何もしない）
    ctx.sandbox.respStatus = 'completed';
    ctx.sandbox.msg = { response: { id: 'resp_f_completed', status: 'completed' } };
    ctx.run();
    // 2回目: rate limit失敗 → 1回だけretry
    ctx.sandbox.respStatus = 'failed';
    ctx.sandbox.msg = { response: { id: 'resp_f_failed', status: 'failed', status_details: { error: { type: 'tokens', code: 'rate_limit_exceeded' } } } };
    ctx.run();
    assert.strictEqual(ctx.sendResponseCreateCalls.length, 1);
});

// =======================================================================
// G. call_idが異なれば独立してretry判定される
// =======================================================================
test('G) 異なるcall_idであれば、それぞれ独立して最大1回ずつretryできる（call_id相関）', () => {
    const ctx = makeSandbox({
        respStatus: 'failed',
        toolContinuationTraceActive: true,
    });
    ctx.sandbox.toolContinuationTraceCallId = 'call_g_A';
    ctx.sandbox.msg = { response: { id: 'resp_g_A', status: 'failed', status_details: { error: { type: 'tokens', code: 'rate_limit_exceeded' } } } };
    ctx.run();

    ctx.sandbox.toolContinuationTraceCallId = 'call_g_B';
    ctx.sandbox.msg = { response: { id: 'resp_g_B', status: 'failed', status_details: { error: { type: 'tokens', code: 'rate_limit_exceeded' } } } };
    ctx.run();

    assert.strictEqual(ctx.sendResponseCreateCalls.length, 2, 'two distinct call_ids must each get their own bounded retry');
});

// =======================================================================
// H. response_id相関がretryログに正しく記録される
// =======================================================================
test('H) retryログに、失敗したresponseのidの末尾8文字がpreviousResponseIdTailとして記録される', () => {
    const ctx = makeSandbox({
        respStatus: 'failed',
        msg: { response: { id: 'resp_0123456789abcdef', status: 'failed', status_details: { error: { type: 'tokens', code: 'rate_limit_exceeded' } } } },
        toolContinuationTraceActive: true,
        toolContinuationTraceCallId: 'call_h_1',
    });
    ctx.run();
    const line = ctx.timelineEvents.find(t => t.includes('TOOL_CONTINUATION_RATE_LIMIT_RETRY'));
    assert.ok(line);
    assert.ok(line.includes('previousResponseIdTail=' + '0123456789abcdef'.slice(-8)));
    assert.ok(line.includes('callIdTail=' + 'call_h_1'.slice(-8)));
});

// =======================================================================
// I. Tool出力（function_call_output）の再送信は一切行わない
// =======================================================================
test('I) retryロジックはfunction_call_output（Tool結果）の再送信を一切行わない', () => {
    assert.ok(!FAILED_BLOCK_SRC.includes('function_call_output'), 'the retry must only resend response.create, never the tool result itself');
});

// =======================================================================
// J. create_reservationの再実行は一切行わない
// =======================================================================
test('J) retryロジックはcreate_reservation関連の呼び出しを一切含まない', () => {
    assert.ok(!FAILED_BLOCK_SRC.includes('callCreateReservationTool') && !FAILED_BLOCK_SRC.includes('create_reservation'),
        'the retry must never re-trigger reservation creation');
});

// =======================================================================
// K. dc.send()呼び出し箇所数は増えていない
// =======================================================================
test('K) dc.send()呼び出し箇所数は今回も増えていない（既存9箇所のまま。retryはsendResponseCreate()の既存経路を再利用）', () => {
    const actualCallLines = SRC.split('\n').filter(line => line.trim().startsWith('dc.send('));
    // Realtime Token Architecture Phase 1（今回追加）が正当な新規dc.send呼び出し
    // 箇所（session.update送信）を1箇所追加したため、基準値を9→10へ更新する。
    assert.strictEqual(actualCallLines.length, 10, 'dc.send() call-site count must remain 10 (9 + Realtime Token Architecture Phase 1\'s session.update send) — the retry reuses the existing sendResponseCreate() path');
});

// =======================================================================
// L〜O. FAST TURN HOTFIX 7: rate limit残量に基づくretryスキップ判定
// =======================================================================
// 背景: HOTFIX 6のimmediate retryを実機検証したところ、rate_limits.updated
// で観測されたremaining（14711）が、直前の正常responseが実際に消費した
// input_tokens（24320）を下回っている状況で、retry（attempt=1）が即座に
// 同じRate_limit_exceededで再失敗することが直接確認された。そのため、
// retryを送信する前に実測データ同士を比較し、明らかに不足していれば
// retry自体を送信しないガードを追加した。

test('L) rate limit残量(remaining)が直近の正常response消費量(lastObservedResponseInputTokens)を下回る場合、retryは送信されず、専用のskipマーカーが記録される', () => {
    const ctx = makeSandbox({
        respStatus: 'failed',
        msg: { response: { id: 'resp_l_1', status: 'failed', status_details: { error: { type: 'tokens', code: 'Rate_limit_exceeded' } } } },
        toolContinuationTraceActive: true,
        toolContinuationTraceCallId: 'call_l_1',
        // 実機で実際に観測された値と同じ関係（remaining < 直前の消費量）。
        lastKnownRateLimitRemainingTokens: 14711,
        lastObservedResponseInputTokens: 24320,
    });
    ctx.run();
    assert.strictEqual(ctx.sendResponseCreateCalls.length, 0, 'must not send a retry when the budget clearly looks insufficient');
    const skipLine = ctx.timelineEvents.find(t => t.includes('TOOL_CONTINUATION_RATE_LIMIT_RETRY_SKIPPED'));
    assert.ok(skipLine, 'must record a distinct skip marker (not silently do nothing)');
    assert.ok(skipLine.includes('remaining=14711') && skipLine.includes('lastResponseInputTokens=24320'));
    // ユーザーを無音のまま放置しない（新しい音声は追加せず、UIテキストのみ
    // 更新する）。
    assert.strictEqual(ctx.subStatusText.textContent, '混み合っています。少々お待ちください');
});

test('L2) skip判定でも、同一call_idへの再判定（2回目のresponse.done failed）は発生しない（retry storm防止と同じ仕組みを共有）', () => {
    const ctx = makeSandbox({
        respStatus: 'failed',
        toolContinuationTraceActive: true,
        toolContinuationTraceCallId: 'call_l2_1',
        lastKnownRateLimitRemainingTokens: 100,
        lastObservedResponseInputTokens: 24320,
    });
    ctx.sandbox.msg = { response: { id: 'resp_l2_a', status: 'failed', status_details: { error: { type: 'tokens', code: 'rate_limit_exceeded' } } } };
    ctx.run();
    ctx.sandbox.msg = { response: { id: 'resp_l2_b', status: 'failed', status_details: { error: { type: 'tokens', code: 'rate_limit_exceeded' } } } };
    ctx.run();
    assert.strictEqual(ctx.sendResponseCreateCalls.length, 0);
    assert.strictEqual(ctx.timelineEvents.filter(t => t.includes('TOOL_CONTINUATION_RATE_LIMIT_RETRY_SKIPPED')).length, 1,
        'the skip decision itself must also be made at most once per call_id, just like an actual retry would be');
});

test('M) rate limit残量が直近の正常response消費量以上の場合は、従来通りretryを送信する（回帰）', () => {
    const ctx = makeSandbox({
        respStatus: 'failed',
        msg: { response: { id: 'resp_m_1', status: 'failed', status_details: { error: { type: 'tokens', code: 'rate_limit_exceeded' } } } },
        toolContinuationTraceActive: true,
        toolContinuationTraceCallId: 'call_m_1',
        lastKnownRateLimitRemainingTokens: 30000,
        lastObservedResponseInputTokens: 24320,
    });
    ctx.run();
    assert.strictEqual(ctx.sendResponseCreateCalls.length, 1, 'must retry as before when the observed budget looks sufficient');
    assert.strictEqual(ctx.subStatusText.textContent, '確認しています');
    assert.strictEqual(ctx.timelineEvents.filter(t => t.includes('TOOL_CONTINUATION_RATE_LIMIT_RETRY_SKIPPED')).length, 0);
});

test('N) rate limit残量・直近消費量のいずれかが未取得(null)の場合は、比較材料が無いためHOTFIX 6と同じくretryを許可する（安全側デフォルトの回帰）', () => {
    const ctxRemainingUnknown = makeSandbox({
        respStatus: 'failed',
        msg: { response: { id: 'resp_n_1', status: 'failed', status_details: { error: { type: 'tokens', code: 'rate_limit_exceeded' } } } },
        toolContinuationTraceActive: true,
        toolContinuationTraceCallId: 'call_n_1',
        lastKnownRateLimitRemainingTokens: null,
        lastObservedResponseInputTokens: 24320,
    });
    ctxRemainingUnknown.run();
    assert.strictEqual(ctxRemainingUnknown.sendResponseCreateCalls.length, 1);

    const ctxUsageUnknown = makeSandbox({
        respStatus: 'failed',
        msg: { response: { id: 'resp_n_2', status: 'failed', status_details: { error: { type: 'tokens', code: 'rate_limit_exceeded' } } } },
        toolContinuationTraceActive: true,
        toolContinuationTraceCallId: 'call_n_2',
        lastKnownRateLimitRemainingTokens: 100,
        lastObservedResponseInputTokens: null,
    });
    ctxUsageUnknown.run();
    assert.strictEqual(ctxUsageUnknown.sendResponseCreateCalls.length, 1);
});

test('O) recordUsageEvent()は、usage.input_tokensが得られるたびにlastObservedResponseInputTokensを更新する配線になっている（0の場合は上書きしない）', () => {
    const idx = SRC.indexOf('function recordUsageEvent(response)');
    assert.notStrictEqual(idx, -1, 'recordUsageEvent not found');
    const block = SRC.slice(idx, idx + 800);
    assert.ok(/if\s*\(usage\.input_tokens\)\s*\{\s*lastObservedResponseInputTokens\s*=\s*usage\.input_tokens;/.test(block),
        'recordUsageEvent must update lastObservedResponseInputTokens only when usage.input_tokens is truthy (a failed response often reports 0, which must not overwrite the last known real value)');
});

// =======================================================================
// 補足: rate_limits.updated診断ハンドラの健全性（HOTFIX 6で新規追加・
// HOTFIX 7で実機確認済みschemaの抽出を追加）
// =======================================================================
test('補足) rate_limits.updatedハンドラは、HOTFIX 6時点では未確認だったfield名を、HOTFIX 7で実機確認できたため安全に抽出するようになった（ただし生JSONの汎用ログも維持）', () => {
    // HOTFIX 6時点ではrate_limits.updatedの正確なfield名（name/limit/
    // remaining/reset_seconds）を公式ドキュメントから確認できなかったため、
    // 推測でfield名を決め打ちせず、msg.rate_limitsを汎用的にJSON化して
    // 記録するだけに留めていた。HOTFIX 7で実機から
    //   name:"tokens" limit:40000 remaining:14711 reset_seconds:37.932
    // という実際の中身が直接観測されたため、このテストの前提
    // （field名に一切触れてはいけない）はもはや正しくない。現在は
    // name==="tokens"のentryからremaining/reset_secondsを安全に
    // （Array.isArray・typeof チェック付きで）抽出しつつ、生JSONの汎用ログも
    // 引き続き残す設計になっていることを確認する。
    const idx = SRC.indexOf("} else if (type === 'rate_limits.updated') {");
    assert.notStrictEqual(idx, -1, 'rate_limits.updated handler not found');
    const block = SRC.slice(idx, idx + 2200);
    assert.ok(block.includes('JSON.stringify(rl)'), 'must still log the raw object generically for future real-device schema discovery of other rate limit types');
    assert.ok(block.includes("e.name === 'tokens'") || block.includes('e.name === "tokens"'),
        'must look up the confirmed "tokens" entry by name rather than assuming array order');
    assert.ok(/typeof\s+tokensEntry\.remaining\s*===\s*'number'/.test(block),
        'must guard the confirmed remaining field with a type check rather than trusting it unconditionally');
    assert.ok(/typeof\s+tokensEntry\.reset_seconds\s*===\s*'number'/.test(block),
        'must guard the confirmed reset_seconds field with a type check rather than trusting it unconditionally');
});

test('補足2) rate_limits.updatedハンドラは送信を一切行わない（観測専用）', () => {
    const idx = SRC.indexOf("} else if (type === 'rate_limits.updated') {");
    const endIdx = SRC.indexOf("} else if (type === 'error') {", idx);
    const block = SRC.slice(idx, endIdx);
    assert.ok(!block.includes('dc.send') && !block.includes('sendResponseCreate'));
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
if (failed > 0) process.exit(1);
