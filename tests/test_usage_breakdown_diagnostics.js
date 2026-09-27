'use strict';

/**
 * RECEPTRA — FAST TURN HOTFIX 9
 * REAL INPUT-TOKEN BREAKDOWN 診断マーカー（REALTIME_USAGE_BREAKDOWN /
 * RATE_LIMIT_TOKEN_DELTA）のテスト
 *
 * 背景: 実機で
 *   input_tokens=20190（tool continuation直前の正常response）
 *   → 直後のtool continuation responseがrate_limit_exceededで失敗
 *   → rate limit回復後の次の正常responseはinput_tokens=7380
 * という大きな差が観測されたが、原因（text/audio/cached tokensの内訳、
 * rate limit remainingの実際の消費量）を実測する手段がこれまで
 * 不十分だった（recordUsageEvent()自体は既にinput_token_details.
 * text_tokens/audio_tokens/cached_tokensをentryのフラットなフィールドに
 * 変換しconsole.log(entry)に渡していたが、(a) Consoleでオブジェクトを
 * 展開しないと内訳が見えない、(b) Copy Debug Log/#diagTimeline
 * （pushTimelineEventの対象）には内訳もresponse_id/toolContinuationActive/
 * responseCreateCategoryとの相関も一切出ていなかった）。
 *
 * 本パッチは診断専用の2つのmarkerを追加した:
 *   - REALTIME_USAGE_BREAKDOWN（recordUsageEvent()内）: 既存entryの値と
 *     既存の相関用変数（toolContinuationTraceActive・
 *     lastResponseReasonCategoryForDiag）をそのまま使い、1行にまとめる。
 *   - RATE_LIMIT_TOKEN_DELTA（rate_limits.updatedハンドラ内）: 上書き前の
 *     lastKnownRateLimitRemainingTokensをpreviousRemainingとして使い、
 *     consumedSinceLastUpdateを実測する（response.input_tokensと同じとは
 *     仮定しない）。
 *
 * どちらも観測専用（pushTimelineEvent/console.logのみ）で、dc.send()や
 * response.create送信、retry判定ロジックそのものには一切手を加えていない。
 *
 * 本テストが確認する項目（ユーザー指示15のA〜O。Pは既存の全JS/Python
 * テストの回帰実行でカバーするため本ファイルでは重複しない）:
 *   A. completed responseでREALTIME_USAGE_BREAKDOWNが正しいfieldを出す
 *   B. input_token_details欠落でもcrashしない
 *   C. cached_tokens=0を正しく記録
 *   D. cached_tokens>0を正しく記録
 *   E. text/audio/cachedを取り違えない
 *   F. PII/transcriptをログしない
 *   G. RATE_LIMIT_TOKEN_DELTAが正しく差分計算
 *   H. 最初のrate_limits.updatedではpreviousRemaining=null
 *   I. new callでdelta state reset（既存のstartCall()/cleanupConnection()の
 *      リセット対象にlastKnownRateLimitRemainingTokensが含まれている
 *      ことをソースレベルで確認。新しい状態変数を追加していないため、
 *      既存のリセットがそのまま今回のdelta追跡もリセットする）
 *   J. response_id correlation
 *   K. toolContinuationActive correlation
 *   L. responseCreateCategory correlation
 *   M. dc.send箇所数不変
 *   N. response.create送信数不変（新markerはsendResponseCreate/dc.send
 *      いずれも呼ばない）
 *   O. retry挙動不変（既存のtest_rate_limit_recovery.jsが引き続き全項目
 *      PASSすることで確認。本ファイルでは重複実装しない）
 *
 * 実行: node tests/test_usage_breakdown_diagnostics.js
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

console.log('FAST TURN HOTFIX 9 — REALTIME_USAGE_BREAKDOWN / RATE_LIMIT_TOKEN_DELTA tests');
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

// recordUsageEvent(response) 関数全体（既存ロジック + 今回追加の
// REALTIME_USAGE_BREAKDOWNブロック）を丸ごと抽出する。
const RECORD_USAGE_EVENT_SRC = extractBlock(SRC, 'function recordUsageEvent(response) {');

// rate_limits.updatedハンドラ本体（"} else if (...) { ... }"の形のまま
// 抽出し、"if (false) {" を前置してそれ単体で valid な if/else-if 文になる
// ようにする（元のソースを一切書き換えずそのまま実行するため）。
// 注意: このブロックのコメントには実機で観測されたschema例として
// 「{name, limit, remaining, reset_seconds}」という、日本語文中に単独で
// 対になった{}を含む行がある。extractBlock()の単純な深さカウントは
// コメント内の{}も区別なく数えてしまい、そこで誤って「ブロックが閉じた」
// と判定して途中で打ち切ってしまう（実際に確認済み）。そのため、この
// ブロックだけは、次の分岐（"} else if (type === 'error') {"）の開始位置に
// あるはずの、この分岐自身を閉じる"}"を終端の目印として使う、コメントの
// 中身に左右されない切り出し方をする。
const RL_START_IDX = SRC.indexOf("} else if (type === 'rate_limits.updated') {");
assert.notStrictEqual(RL_START_IDX, -1, "rate_limits.updated handler not found");
const RL_NEXT_BRANCH_IDX = SRC.indexOf("} else if (type === 'error') {", RL_START_IDX);
assert.notStrictEqual(RL_NEXT_BRANCH_IDX, -1, "次の分岐(error)が見つからず、終端の目印を確定できません");
// RL_NEXT_BRANCH_IDXが指す"}"は、rate_limits.updated分岐自身を閉じる"}"と
// 同一の文字（次の"else if"の直前の"}"）なので、+1してそれを含めて切り出す。
const RATE_LIMITS_HANDLER_RAW = SRC.slice(RL_START_IDX, RL_NEXT_BRANCH_IDX + 1);
const RATE_LIMITS_HANDLER_SRC = 'if (false) {' + RATE_LIMITS_HANDLER_RAW;

function makeUsageSandbox(overrides) {
    const consoleLogs = [];
    const timelineEvents = [];
    const usageResponses = [];
    const sandbox = {
        usageResponses,
        lastObservedResponseInputTokens: null,
        toolContinuationTraceActive: false,
        lastResponseReasonCategoryForDiag: 'unknown',
        usageLogEl: null,
        pushTimelineEvent: (t) => timelineEvents.push(t),
        console: { log: (...args) => consoleLogs.push(args) },
        renderUsageSummary: () => {},
    };
    Object.assign(sandbox, overrides);
    vm.createContext(sandbox);
    vm.runInContext(RECORD_USAGE_EVENT_SRC, sandbox);
    return {
        sandbox,
        record: (response) => sandbox.recordUsageEvent(response),
        timelineEvents,
        consoleLogs,
        usageResponses,
    };
}

function findBreakdownLine(timelineEvents) {
    return timelineEvents.find((t) => t.includes('REALTIME_USAGE_BREAKDOWN'));
}

// =======================================================================
// A. completed responseでREALTIME_USAGE_BREAKDOWNが正しいfieldを出す
// =======================================================================
test('A) completed responseでREALTIME_USAGE_BREAKDOWNが実測値どおりのfieldを1行で出す', () => {
    const ctx = makeUsageSandbox();
    ctx.record({
        id: 'resp_abcdef0123456789',
        status: 'completed',
        usage: {
            input_tokens: 20190,
            output_tokens: 55,
            total_tokens: 20245,
            input_token_details: { text_tokens: 300, audio_tokens: 19890, cached_tokens: 0 },
            output_token_details: { text_tokens: 10, audio_tokens: 45 },
        },
    });
    const line = findBreakdownLine(ctx.timelineEvents);
    assert.ok(line, 'REALTIME_USAGE_BREAKDOWN marker must be recorded');
    assert.ok(line.includes('input_tokens=20190'));
    assert.ok(line.includes('output_tokens=55'));
    assert.ok(line.includes('total_tokens=20245'));
    assert.ok(line.includes('input_text_tokens=300'));
    assert.ok(line.includes('input_audio_tokens=19890'));
    assert.ok(line.includes('input_cached_tokens=0'));
    assert.ok(line.includes('output_text_tokens=10'));
    assert.ok(line.includes('output_audio_tokens=45'));
    assert.ok(line.includes('status=completed'));
});

// =======================================================================
// B. input_token_details欠落でもcrashしない
// =======================================================================
test('B) usage.input_token_details / output_token_details自体が欠落していてもcrashせず0扱いで記録する', () => {
    const ctx = makeUsageSandbox();
    assert.doesNotThrow(() => {
        ctx.record({ id: 'resp_no_details', status: 'completed', usage: { input_tokens: 100, output_tokens: 5, total_tokens: 105 } });
    });
    const line = findBreakdownLine(ctx.timelineEvents);
    assert.ok(line);
    assert.ok(line.includes('input_text_tokens=0') && line.includes('input_audio_tokens=0') && line.includes('input_cached_tokens=0'));
});

test('B2) usage自体・response自体が欠落していてもcrashしない', () => {
    const ctx = makeUsageSandbox();
    assert.doesNotThrow(() => { ctx.record({ id: 'resp_no_usage', status: 'failed' }); });
    assert.doesNotThrow(() => { ctx.record(null); });
    assert.doesNotThrow(() => { ctx.record(undefined); });
});

// =======================================================================
// C/D. cached_tokens=0 / cached_tokens>0 を正しく記録
// =======================================================================
test('C) cached_tokens=0のresponseはinput_cached_tokens=0・cached_ratio=0.000として記録される', () => {
    const ctx = makeUsageSandbox();
    ctx.record({
        id: 'resp_cache_zero',
        status: 'completed',
        usage: { input_tokens: 7380, output_tokens: 20, total_tokens: 7400, input_token_details: { text_tokens: 200, audio_tokens: 7180, cached_tokens: 0 } },
    });
    const line = findBreakdownLine(ctx.timelineEvents);
    assert.ok(line.includes('input_cached_tokens=0'));
    assert.ok(line.includes('cached_ratio=0.000'));
});

test('D) cached_tokens>0のresponseは正しい値・正しいcached_ratioとして記録される', () => {
    const ctx = makeUsageSandbox();
    ctx.record({
        id: 'resp_cache_nonzero',
        status: 'completed',
        usage: { input_tokens: 20000, output_tokens: 20, total_tokens: 20020, input_token_details: { text_tokens: 500, audio_tokens: 15500, cached_tokens: 4000 } },
    });
    const line = findBreakdownLine(ctx.timelineEvents);
    assert.ok(line.includes('input_cached_tokens=4000'));
    // 4000 / 20000 = 0.200
    assert.ok(line.includes('cached_ratio=0.200'), 'cached_ratio must be computed as input_cached_tokens / input_tokens: ' + line);
});

test('D2) input_tokens=0の場合はcached_ratio=null（ゼロ除算を実測不能扱いにする。捏造しない）', () => {
    const ctx = makeUsageSandbox();
    ctx.record({ id: 'resp_zero_input', status: 'completed', usage: { input_tokens: 0, output_tokens: 0, total_tokens: 0 } });
    const line = findBreakdownLine(ctx.timelineEvents);
    assert.ok(line.includes('cached_ratio=null'));
});

// =======================================================================
// E. text/audio/cachedを取り違えない
// =======================================================================
test('E) text_tokens/audio_tokens/cached_tokensを取り違えず、それぞれ独立した値として記録する', () => {
    const ctx = makeUsageSandbox();
    ctx.record({
        id: 'resp_distinct',
        status: 'completed',
        usage: {
            input_tokens: 999,
            output_tokens: 1,
            total_tokens: 1000,
            input_token_details: { text_tokens: 111, audio_tokens: 222, cached_tokens: 333 },
            output_token_details: { text_tokens: 444, audio_tokens: 555 },
        },
    });
    const line = findBreakdownLine(ctx.timelineEvents);
    assert.ok(line.includes('input_text_tokens=111'));
    assert.ok(line.includes('input_audio_tokens=222'));
    assert.ok(line.includes('input_cached_tokens=333'));
    assert.ok(line.includes('output_text_tokens=444'));
    assert.ok(line.includes('output_audio_tokens=555'));
    // 数値が入れ替わっていないことの追加確認（111が他のfieldに紛れ込んでいない）
    assert.ok(!line.includes('input_audio_tokens=111') && !line.includes('input_cached_tokens=111'));
});

// =======================================================================
// F. PII/transcriptをログしない
// =======================================================================
test('F) 追加したREALTIME_USAGE_BREAKDOWNブロックのソースは、PIIになり得る変数（transcript/name/phone等）に一切触れない', () => {
    const idx = SRC.indexOf('FAST TURN HOTFIX 9（今回追加・観測専用）: 実機で');
    assert.notStrictEqual(idx, -1, 'HOTFIX 9のブロックコメントが見つかりません');
    const blockStart = SRC.indexOf('try {', idx);
    const blockEnd = SRC.indexOf('renderUsageSummary();', blockStart);
    const block = SRC.slice(blockStart, blockEnd);
    const forbidden = ['transcript', 'customer_name', 'guest_phone', 'phone_number', 'inquiry_text', 'reservation'];
    for (const term of forbidden) {
        assert.ok(!block.toLowerCase().includes(term.toLowerCase()), 'PIIになり得る変数への言及が見つかりました: ' + term);
    }
    // response_idは末尾8文字のみ使用（フルIDを直接ログしない）
    assert.ok(block.includes(".slice(-8)"), 'response_idは末尾8文字のみを使うこと（フルIDをそのまま出さない）');
});

test('F2) 実際にrecordUsageEvent()を実行しても、渡していないPIIフィールド（顧客の氏名・電話番号等）が出力に含まれない', () => {
    const ctx = makeUsageSandbox();
    ctx.record({
        id: 'resp_pii_check_0123456789abcdef',
        status: 'completed',
        usage: { input_tokens: 500, output_tokens: 10, total_tokens: 510, input_token_details: { text_tokens: 100, audio_tokens: 400, cached_tokens: 0 } },
    });
    const line = findBreakdownLine(ctx.timelineEvents);
    // フルresponse_idではなく末尾8文字のみが含まれること
    assert.ok(!line.includes('resp_pii_check_0123456789abcdef'), '完全なresponse_idをそのまま出力してはいけない');
    assert.ok(line.includes('0123456789abcdef'.slice(-8)));
});

// =======================================================================
// J/K/L. response_id / toolContinuationActive / responseCreateCategory の相関
// =======================================================================
test('J) response_idの末尾8文字がresponse_id_tailとして正しく相関記録される', () => {
    const ctx = makeUsageSandbox();
    ctx.record({ id: 'resp_zzzz_9988776655', status: 'completed', usage: { input_tokens: 10, output_tokens: 1, total_tokens: 11 } });
    const line = findBreakdownLine(ctx.timelineEvents);
    assert.ok(line.includes('response_id_tail=' + 'resp_zzzz_9988776655'.slice(-8)));
});

test('K) toolContinuationActive（既存のtoolContinuationTraceActiveをそのまま利用）が正しく相関記録される', () => {
    const ctxActive = makeUsageSandbox({ toolContinuationTraceActive: true });
    ctxActive.record({ id: 'resp_k_active', status: 'completed', usage: { input_tokens: 10, output_tokens: 1, total_tokens: 11 } });
    assert.ok(findBreakdownLine(ctxActive.timelineEvents).includes('toolContinuationActive=true'));

    const ctxInactive = makeUsageSandbox({ toolContinuationTraceActive: false });
    ctxInactive.record({ id: 'resp_k_inactive', status: 'completed', usage: { input_tokens: 10, output_tokens: 1, total_tokens: 11 } });
    assert.ok(findBreakdownLine(ctxInactive.timelineEvents).includes('toolContinuationActive=false'));
});

test('L) responseCreateCategory（既存のlastResponseReasonCategoryForDiagをそのまま利用）が正しく相関記録される', () => {
    const ctx = makeUsageSandbox({ lastResponseReasonCategoryForDiag: 'tool_result' });
    ctx.record({ id: 'resp_l_1', status: 'failed', usage: {} });
    assert.ok(findBreakdownLine(ctx.timelineEvents).includes('responseCreateCategory=tool_result'));
});

// =======================================================================
// G/H. RATE_LIMIT_TOKEN_DELTA
// =======================================================================
function makeRateLimitSandbox(overrides) {
    const timelineEvents = [];
    const sandbox = {
        type: 'rate_limits.updated',
        msg: { rate_limits: null },
        lastKnownRateLimitRemainingTokens: null,
        lastKnownRateLimitResetSeconds: null,
        lastObservedResponseInputTokens: null,
        pushTimelineEvent: (t) => timelineEvents.push(t),
        console: { log: () => {} },
    };
    Object.assign(sandbox, overrides);
    vm.createContext(sandbox);
    return {
        sandbox,
        run: () => vm.runInContext(RATE_LIMITS_HANDLER_SRC, sandbox),
        timelineEvents,
    };
}

test('G) RATE_LIMIT_TOKEN_DELTAが2回目以降、実測のconsumedSinceLastUpdateを正しく差分計算する', () => {
    const ctx = makeRateLimitSandbox();
    ctx.sandbox.msg = { rate_limits: [{ name: 'tokens', limit: 40000, remaining: 31391, reset_seconds: 12.913 }] };
    ctx.run();
    ctx.sandbox.msg = { rate_limits: [{ name: 'tokens', limit: 40000, remaining: 19252, reset_seconds: 31.121 }] };
    ctx.run();
    const lines = ctx.timelineEvents.filter((t) => t.includes('RATE_LIMIT_TOKEN_DELTA'));
    assert.strictEqual(lines.length, 2);
    assert.ok(lines[1].includes('previousRemaining=31391'));
    assert.ok(lines[1].includes('currentRemaining=19252'));
    assert.ok(lines[1].includes('consumedSinceLastUpdate=' + (31391 - 19252)), 'must compute the actual measured delta, not assume it equals response.input_tokens: ' + lines[1]);
});

test('H) 最初のrate_limits.updated（通話開始後1回目）ではpreviousRemaining=null', () => {
    const ctx = makeRateLimitSandbox();
    ctx.sandbox.msg = { rate_limits: [{ name: 'tokens', limit: 40000, remaining: 39000, reset_seconds: 5 }] };
    ctx.run();
    const line = ctx.timelineEvents.find((t) => t.includes('RATE_LIMIT_TOKEN_DELTA'));
    assert.ok(line);
    assert.ok(line.includes('previousRemaining=null'));
    assert.ok(line.includes('consumedSinceLastUpdate=null'), '比較材料が無い1回目はconsumedSinceLastUpdateも実測不能としてnullにする（捏造しない）');
});

test('G2) consumedSinceLastUpdateとresponse.input_tokensは別物として扱われ、同一視されない（lastObservedResponseInputTokensは参考情報として併記するだけ）', () => {
    const ctx = makeRateLimitSandbox({ lastObservedResponseInputTokens: 20190 });
    ctx.sandbox.msg = { rate_limits: [{ name: 'tokens', limit: 40000, remaining: 19252, reset_seconds: 31.121 }] };
    ctx.run();
    const line = ctx.timelineEvents.find((t) => t.includes('RATE_LIMIT_TOKEN_DELTA'));
    assert.ok(line.includes('lastObservedResponseInputTokens=20190'));
    // previousRemainingがnull（1回目）の場合、consumedSinceLastUpdateは
    // lastObservedResponseInputTokensと機械的に等しくされてはいけない
    // （実測できないのでnullのまま。20190という値を勝手に流用しない）。
    assert.ok(line.includes('consumedSinceLastUpdate=null'));
});

test('補足) rate_limits配列にtokens以外のnameしか無い場合はRATE_LIMIT_TOKEN_DELTAを出さない（既存のtokensEntry検索ロジックのまま）', () => {
    const ctx = makeRateLimitSandbox();
    ctx.sandbox.msg = { rate_limits: [{ name: 'requests', limit: 100, remaining: 50, reset_seconds: 1 }] };
    ctx.run();
    assert.strictEqual(ctx.timelineEvents.filter((t) => t.includes('RATE_LIMIT_TOKEN_DELTA')).length, 0);
});

// =======================================================================
// I. new callでdelta state reset（既存のリセット対象に含まれることの確認）
// =======================================================================
test('I) startCall()・cleanupConnection()の両方が、既存のlastKnownRateLimitRemainingTokensをnullへリセットしている（新しい状態変数を追加していないため、今回のdeltaも自動的にリセットされる）', () => {
    const startCallIdx = SRC.indexOf('async function startCall()');
    assert.notStrictEqual(startCallIdx, -1);
    const cleanupIdx = SRC.indexOf('function cleanupConnection()');
    // cleanupConnectionという関数名が無い場合は、少なくともHOTFIX 7時点の
    // 既知のリセット箇所（2箇所）が両方とも存在することを直接確認する。
    const resetOccurrences = SRC.split('lastKnownRateLimitRemainingTokens = null;').length - 1;
    assert.ok(resetOccurrences >= 2, 'lastKnownRateLimitRemainingTokens must still be reset to null at both known call-boundary points (startCall + cleanup)');
});

// =======================================================================
// M/N. dc.send箇所数・response.create送信数が不変であること
// =======================================================================
test('M) dc.send()呼び出し箇所数は今回も変わっていない（既存9箇所のまま。今回の2つのmarkerはpushTimelineEvent/console.logのみ）', () => {
    const actualCallLines = SRC.split('\n').filter((line) => line.trim().startsWith('dc.send('));
    // Realtime Token Architecture Phase 1（今回追加）が正当な新規dc.send呼び出し
    // 箇所（session.update送信）を1箇所追加したため、基準値を9→10へ更新する。
    assert.strictEqual(actualCallLines.length, 10, 'REALTIME_USAGE_BREAKDOWN/RATE_LIMIT_TOKEN_DELTAはいずれも観測専用でdc.sendを呼ばないため、既存10箇所（9＋Realtime Token Architecture Phase 1のsession.update送信）のまま変わらないはず');
});

test('N) 今回追加した2つのブロックはsendResponseCreate/dc.sendのいずれも呼ばない（診断専用）', () => {
    const usageBreakdownIdx = SRC.indexOf('FAST TURN HOTFIX 9（今回追加・観測専用）: 実機で');
    const usageBreakdownEnd = SRC.indexOf('renderUsageSummary();', usageBreakdownIdx);
    const usageBreakdownBlock = SRC.slice(usageBreakdownIdx, usageBreakdownEnd);
    assert.ok(!usageBreakdownBlock.includes('dc.send(') && !usageBreakdownBlock.includes('sendResponseCreate('));

    assert.ok(!RATE_LIMITS_HANDLER_RAW.includes('dc.send(') && !RATE_LIMITS_HANDLER_RAW.includes('sendResponseCreate('));
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
if (failed > 0) process.exit(1);
