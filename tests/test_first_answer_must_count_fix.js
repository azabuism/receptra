'use strict';

/**
 * FAST TURN HOTFIX 2 — FIRST ANSWER MUST COUNT（実際の修正の検証）
 *
 * 背景（前フェーズ commit 4a56d46 の診断結果と、今回の追加監査）:
 * 「人数・時間を正しく1回目で答えても反応せず、2回目で反応する」実機不具合の
 * コード監査で、USER_TURN_3S_FALLBACKとANSWER_WINDOWはSHORT_ANSWERに対して
 * 構造上無関係であることが既に確定している（tests/test_first_answer_must_count.js
 * 参照）。残る唯一の新機構はAI SPEAKING PROTECTION（track.enabled=false/true
 * によるマイクミュート）であり、その解除トリガーが
 * output_audio_buffer.stopped / cleared / response.done という「サーバー側
 * イベント」のみに依存していた。
 *
 * 今回、OpenAI公式コミュニティ（community.openai.com、OpenAIサポート自身が
 * Case #09291741として調査受理済み）で、output_audio_buffer.started/stopped
 * イベントが「実際の音声終了から6〜10秒遅延する」「イベント自体が届かない
 * ことがある」ことが確認された（推測ではなく外部の一次情報）。これは
 * ROOT CAUSE TREEの分類A（ユーザーが話した時点でmic track.enabled=false の
 * まま）を裏付ける、最有力の根本原因候補である。
 *
 * 修正: AI SPEAKING PROTECTIONのengage/releaseに、サーバーイベントとは独立
 * した第2の経路として、既存のAnalyserNodeベースのAI音声レベル計測
 * （aiSpeakingNow、既存の「発話中」「待機」UIバッジと同じ信号）を追加した。
 * これはAI自身の既知の音声ストリームのみを見ており、周囲雑音（ローカルmic
 * 入力）は一切含まないため、「雑音と人間発話の音量分類」には当たらない。
 * engage/release自体は完全に冪等なため、既存のサーバーイベント経路を
 * 一切破壊せず、単純に「早い方が勝つ」レースとして追加されている。
 *
 * 本テストは、実際のengageAiSpeakingProtection/releaseAiSpeakingProtection
 * 関数のソースをそのままvmサンドボックスで実行し、
 *   (1) サーバーイベントが遅延/欠落しても、ローカル音声レベル経路だけで
 *       確実にマイクが解放されること
 *   (2) 遅延した古いサーバーイベントが後から届いても、二重発火しないこと
 *       （冪等性）
 *   (3) 発話中の瞬間的な無音（1文中のポーズ相当）で解放→即再engageしても、
 *       安全タイマーが正しくクリア・再設定され、リークしないこと
 *   (4) 20秒安全網タイマー自体は今回の変更で壊れていないこと
 *   (5) USER_LISTENING_READYマーカーが解除の集約点で正しく記録されること
 * を実際にコードを動かして検証する（静的substringチェックのみに頼らない）。
 */

const fs = require('fs');
const path = require('path');
const assert = require('assert');
const vm = require('vm');

const SRC_PATH = path.join(__dirname, '..', 'frontend', 'public', 'js', 'realtime-voice-engine.js');
const SRC = fs.readFileSync(SRC_PATH, 'utf8');

let passed = 0;
let failed = 0;
function test(name, fn) {
    try {
        fn();
        console.log('  ok - ' + name);
        passed++;
    } catch (e) {
        console.log('  FAIL - ' + name);
        console.log('    ' + (e && e.stack ? e.stack : e));
        failed++;
    }
}

function extractFunction(src, signature) {
    const idx = src.indexOf(signature);
    assert.notStrictEqual(idx, -1, 'signature not found: ' + signature);
    let depth = 0;
    let i = idx;
    let started = false;
    for (; i < src.length; i++) {
        if (src[i] === '{') { depth++; started = true; }
        else if (src[i] === '}') {
            depth--;
            if (started && depth === 0) { i++; break; }
        }
    }
    return src.slice(idx, i);
}

console.log('FAST TURN HOTFIX 2 — FIRST ANSWER MUST COUNT: 修正の実行検証テスト');
console.log('source: ' + SRC_PATH);
console.log('');

// ---------------------------------------------------------------------
// vmサンドボックスの構築: engage/release両関数の実ソースをそのまま実行する。
// ---------------------------------------------------------------------
function buildSandbox() {
    const engageSrc = extractFunction(SRC, 'function engageAiSpeakingProtection(reason) {');
    const releaseSrc = extractFunction(SRC, 'function releaseAiSpeakingProtection(reason) {');

    const events = [];
    const mockTrack = { enabled: true };
    const pendingTimers = new Map();
    let nextTimerId = 1;

    const sandbox = {
        aiSpeakingProtected: false,
        aiSpeakingProtectionSafetyTimerId: null,
        lastAiSpeakingEndAt: null,
        AI_SPEAKING_PROTECTION_MAX_MS: 20000,
        localStream: { getAudioTracks: () => [mockTrack] },
        pushTimelineEvent: (msg) => { events.push(msg); },
        performance: { now: () => Date.now() },
        // setTimeout/clearTimeoutは実際にスケジュールせず、コールバックと
        // idだけを記録する（20秒待たずに安全網タイマーのロジックだけを
        // 検証するため）。
        setTimeout: (cb) => {
            const id = nextTimerId++;
            pendingTimers.set(id, cb);
            return id;
        },
        clearTimeout: (id) => { pendingTimers.delete(id); },
        console,
    };
    vm.createContext(sandbox);
    vm.runInContext(engageSrc + '\n' + releaseSrc, sandbox);

    return { sandbox, events, mockTrack, pendingTimers };
}

test('SETUP) engageAiSpeakingProtection/releaseAiSpeakingProtectionの実ソースがvmサンドボックスで正常にロードできる', () => {
    const { sandbox } = buildSandbox();
    assert.strictEqual(typeof sandbox.engageAiSpeakingProtection, 'function');
    assert.strictEqual(typeof sandbox.releaseAiSpeakingProtection, 'function');
});

test('(1) サーバーイベント(output_audio_buffer.stopped)が到着しなくても、新しいローカル音声レベル経路(ai_audio_level_silent)だけでマイクが解放される', () => {
    const { sandbox, events, mockTrack } = buildSandbox();

    // AIが話し始めた（サーバーイベント経由でengage）
    sandbox.engageAiSpeakingProtection('output_audio_buffer_started');
    assert.strictEqual(mockTrack.enabled, false, 'engage直後はマイクが無効化されているはず');
    assert.strictEqual(sandbox.aiSpeakingProtected, true);

    // ここでOpenAI側のoutput_audio_buffer.stoppedが遅延/欠落したと仮定
    // （＝呼び出さない）。かわりに、AI自身の音声が実際に無音になったと
    // ローカルAnalyserNodeが検知した、という新経路だけを発火させる。
    sandbox.releaseAiSpeakingProtection('ai_audio_level_silent');

    assert.strictEqual(mockTrack.enabled, true, 'サーバーイベントなしでもマイクが再度有効化されるべき（今回の修正の核心）');
    assert.strictEqual(sandbox.aiSpeakingProtected, false);
    assert.ok(events.some(e => e.includes('AI_SPEAKING_END (reason=ai_audio_level_silent)')));
    assert.ok(events.some(e => e.includes('USER_LISTENING_READY (reason=ai_audio_level_silent)')),
        'releaseの集約点でUSER_LISTENING_READYマーカーが記録されるべき');
});

test('(2) 遅延した古いサーバーイベントが後から届いても、二重解放（重複ログ・副作用）が起きない（冪等性）', () => {
    const { sandbox, events, mockTrack } = buildSandbox();

    sandbox.engageAiSpeakingProtection('output_audio_buffer_started');
    sandbox.releaseAiSpeakingProtection('ai_audio_level_silent'); // 新経路が先に解放
    const countAfterFirstRelease = events.filter(e => e.startsWith('AI_SPEAKING_END')).length;

    // 遅延していたoutput_audio_buffer.stoppedが後から到着したケース
    sandbox.releaseAiSpeakingProtection('output_audio_buffer_stopped');

    assert.strictEqual(mockTrack.enabled, true, '状態は変わらず解放済みのまま');
    assert.strictEqual(sandbox.aiSpeakingProtected, false);
    const countAfterLateEvent = events.filter(e => e.startsWith('AI_SPEAKING_END')).length;
    assert.strictEqual(countAfterLateEvent, countAfterFirstRelease,
        '既に解放済みの状態への追加releaseはno-opであるべき（AI_SPEAKING_ENDが重複記録されない）');
});

test('(3) 発話中の瞬間的な無音（1文中のポーズ相当）→即再engageしても、安全タイマーがリークせず正しく管理される', () => {
    const { sandbox, mockTrack, pendingTimers } = buildSandbox();

    sandbox.engageAiSpeakingProtection('output_audio_buffer_started');
    assert.strictEqual(pendingTimers.size, 1, 'engage時に安全網タイマーが1つ張られる');

    // 1文中の短いポーズ相当: ローカル音声レベルが一瞬nowSpeaking=falseへ
    sandbox.releaseAiSpeakingProtection('ai_audio_level_silent');
    assert.strictEqual(mockTrack.enabled, true);
    assert.strictEqual(pendingTimers.size, 0, 'release時に安全網タイマーは必ずクリアされる（リークしない）');

    // 次のtickでAI音声が再開したと検知（同じ発話の続き）
    sandbox.engageAiSpeakingProtection('ai_audio_level_active');
    assert.strictEqual(mockTrack.enabled, false, '再engageで即座に保護がかかり直す');
    assert.strictEqual(pendingTimers.size, 1, '再engage時に新しい安全網タイマーが1つだけ張られる（重複なし）');

    // 本当にAIの発話が終わった（サーバーイベントでもローカル信号でも良い）
    sandbox.releaseAiSpeakingProtection('output_audio_buffer_stopped');
    assert.strictEqual(mockTrack.enabled, true);
    assert.strictEqual(pendingTimers.size, 0, '最終的に解放された状態でタイマーが残っていない');
});

test('(4) 20秒安全網タイマー自体は今回の変更後も壊れていない（フェイルセーフのregression確認）', () => {
    const { sandbox, events, mockTrack, pendingTimers } = buildSandbox();

    sandbox.engageAiSpeakingProtection('output_audio_buffer_started');
    assert.strictEqual(pendingTimers.size, 1);
    const timerCb = [...pendingTimers.values()][0];

    // どちらの経路（サーバーイベントもローカル音声レベルも）も発火しなかった
    // 最悪ケースを模して、20秒経過相当でタイマーコールバックを直接実行する。
    timerCb();

    assert.strictEqual(mockTrack.enabled, true, '安全網タイマーが最終的にマイクを必ず解放する');
    assert.strictEqual(sandbox.aiSpeakingProtected, false);
    assert.ok(events.some(e => e.includes('AI_SPEAKING_PROTECTION_SAFETY_UNMUTE')) || events.some(e => e.includes('reason=safety_timeout')),
        '安全網タイマー経由の解放であることがログから分かる');
});

test('(5) USER_LISTENING_READYは新経路(ai_audio_level_silent)・旧経路(output_audio_buffer_stopped/cleared/response_done_fallback/safety_timeout)のすべてで一貫して記録される', () => {
    const reasons = ['output_audio_buffer_stopped', 'output_audio_buffer_cleared', 'response_done_fallback', 'ai_audio_level_silent', 'safety_timeout'];
    for (const reason of reasons) {
        const { sandbox, events } = buildSandbox();
        sandbox.engageAiSpeakingProtection('output_audio_buffer_started');
        sandbox.releaseAiSpeakingProtection(reason);
        assert.ok(events.some(e => e.includes('USER_LISTENING_READY (reason=' + reason + ')')),
            'reason=' + reason + ' でもUSER_LISTENING_READYが記録されるべき');
    }
});

test('LIVE-WIRING(1)) tick()内のnowSpeaking遷移(false→true)で、他のUI更新より前にengageAiSpeakingProtection(\'ai_audio_level_active\')が呼ばれている', () => {
    const anchor = "if (nowSpeaking && !aiSpeakingNow) {\n                    aiSpeakingNow = true;";
    const idx = SRC.indexOf(anchor);
    assert.notStrictEqual(idx, -1, 'アンカーが見つからない（tick()の構造が変わった可能性）');
    // 実測: 説明コメントが長いため、engageAiSpeakingProtection呼び出しは
    // アンカーから1588文字後に出現する。ウィンドウを1600→1800へ拡張。
    const block = SRC.slice(idx, idx + 1800);
    const engageIdx = block.indexOf("engageAiSpeakingProtection('ai_audio_level_active')");
    const uiIdx = block.indexOf("bigMic.classList.add('ai-speaking')");
    assert.notStrictEqual(engageIdx, -1);
    assert.notStrictEqual(uiIdx, -1);
    assert.ok(engageIdx < uiIdx, 'engageはUI更新より前（早く）呼ばれているべき');
});

test('LIVE-WIRING(2)) tick()内のnowSpeaking遷移(true→false)で、releaseAiSpeakingProtection(\'ai_audio_level_silent\')が呼ばれている', () => {
    const anchor = "} else if (!nowSpeaking && aiSpeakingNow) {\n                    aiSpeakingNow = false;";
    const idx = SRC.indexOf(anchor);
    assert.notStrictEqual(idx, -1, 'アンカーが見つからない（tick()の構造が変わった可能性）');
    const block = SRC.slice(idx, idx + 1200);
    assert.ok(block.includes("releaseAiSpeakingProtection('ai_audio_level_silent')"));
});

test('REGRESSION) engageAiSpeakingProtection呼び出し箇所は2箇所（既存のoutput_audio_buffer.started + 今回追加のai_audio_level_active）', () => {
    const matches = SRC.match(/engageAiSpeakingProtection\('[^']*'\)/g) || [];
    assert.strictEqual(matches.length, 2, '呼び出し箇所数: ' + matches.length + ' / ' + JSON.stringify(matches));
    assert.ok(matches.includes("engageAiSpeakingProtection('output_audio_buffer_started')"));
    assert.ok(matches.includes("engageAiSpeakingProtection('ai_audio_level_active')"));
});

test('REGRESSION) releaseAiSpeakingProtection呼び出し箇所は5箇所（既存4箇所 + 今回追加のai_audio_level_silent）', () => {
    const matches = SRC.match(/releaseAiSpeakingProtection\('[^']*'\)/g) || [];
    assert.strictEqual(matches.length, 5, '呼び出し箇所数: ' + matches.length + ' / ' + JSON.stringify(matches));
    for (const expected of [
        "releaseAiSpeakingProtection('output_audio_buffer_stopped')",
        "releaseAiSpeakingProtection('output_audio_buffer_cleared')",
        "releaseAiSpeakingProtection('response_done_fallback')",
        "releaseAiSpeakingProtection('safety_timeout')",
        "releaseAiSpeakingProtection('ai_audio_level_silent')",
    ]) {
        assert.ok(matches.includes(expected), 'missing: ' + expected);
    }
});

test('REGRESSION) dc.send(JSON.stringify(...)) の呼び出し箇所は今回も9箇所のまま（今回の修正はマイクmute/unmuteの発火条件追加のみで、新しい送信は一切追加していない）', () => {
    const count = (SRC.match(/dc\.send\(JSON\.stringify\(/g) || []).length;
    assert.strictEqual(count, 9);
});

test('REGRESSION) aiAudioOutputActiveの意味・既存の設定箇所は今回変更していない（output_audio_buffer.started/stopped/cleared/response.doneのみで制御されたまま）', () => {
    // aiAudioOutputActive自体には一切触れていないことを確認する
    // （AI SPEAKING PROTECTIONのengage/release条件だけを拡張し、Tool
    // continuation・barge-in分類・診断パネル等が参照するaiAudioOutputActive
    // の意味は変えないという設計方針の固定）。
    const engageFnSrc = (() => {
        const idx = SRC.indexOf('function engageAiSpeakingProtection(reason) {');
        let depth = 0, i = idx, started = false;
        for (; i < SRC.length; i++) {
            if (SRC[i] === '{') { depth++; started = true; }
            else if (SRC[i] === '}') { depth--; if (started && depth === 0) { i++; break; } }
        }
        return SRC.slice(idx, i);
    })();
    const releaseFnSrc = (() => {
        const idx = SRC.indexOf('function releaseAiSpeakingProtection(reason) {');
        let depth = 0, i = idx, started = false;
        for (; i < SRC.length; i++) {
            if (SRC[i] === '{') { depth++; started = true; }
            else if (SRC[i] === '}') { depth--; if (started && depth === 0) { i++; break; } }
        }
        return SRC.slice(idx, i);
    })();
    assert.ok(!engageFnSrc.includes('aiAudioOutputActive'));
    assert.ok(!releaseFnSrc.includes('aiAudioOutputActive'));
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
if (failed > 0) process.exit(1);
