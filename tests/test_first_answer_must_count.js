'use strict';

// RECEPTRA — FAST TURN HOTFIX: FIRST ANSWER MUST COUNT
// 「人数・時間を2回言わないと反応しない」実機不具合の監査用テスト
//
// 背景: 直前のNOISY ENVIRONMENT / 3-SECOND TURN BOUNDARY実装
// (commit 5b8c2ae) の後、実機Macで「AIから質問された後、ユーザーが1回目に
// 正しく答えてもAIが処理せず、2回目でようやく反応する」という新しい不具合が
// 報告された（特に人数・時間で確認）。
//
// 監査の結果（コード上で確認できた事実。断定はしない）:
//   (1) USER_TURN_3S_FALLBACK（今回追加の3秒fallback機構）は
//       armUserTurnFallbackTimer()の冒頭で
//       `if (expectedAnswerType !== 'NONE') return;` により、
//       expectedAnswerType==='NONE'（1st turn「要件を聞く」場面）以外では
//       一切armされない。人数・時間はclassifyExpectedAnswerType()により
//       'SHORT_ANSWER'に分類されるため、この3秒fallback・manual commitは
//       物理的に発火し得ない。よってSTEP7/8（3秒fallbackとの競合、
//       natural/manual commitのrace）は、この特定の症状（人数・時間）の
//       原因からは除外できる。
//   (2) ANSWER_WINDOW（startAnswerWindowIfNeeded）は、O5.8で
//       ANSWER_WINDOW_LIMITS_MSからSHORT_ANSWERキーが削除されたため、
//       `const limitMs = ANSWER_WINDOW_LIMITS_MS[expectedAnswerType]; if
//       (!limitMs) return;` により、SHORT_ANSWER（人数・時間）に対しては
//       何もしない（完全なno-op）。よってSTEP9（ANSWER_WINDOW）も、この
//       特定の症状の原因からは除外できる。
//   (3) 残る主要な新規変更点はAI SPEAKING PROTECTION（マイクの
//       track.enabled=false/true）であり、これは全てのexpectedAnswerType
//       （SHORT_ANSWERを含む）に対して無条件で適用される。もしAI発話終了後の
//       マイク再有効化（release）が何らかの理由で遅延・失敗する経路が
//       存在すれば、ユーザーの1回目の回答がサーバーへ届かず、症状と
//       整合する。ただし現時点でこれを断定する実機ログはまだない。
//
// このテストは、上記(1)(2)を「今後も絶対に壊れない前提」として明示的に
// 固定するリグレッションガードと、(3)の仮説を次回の実機テストで検証する
// ために今回追加した新しい観測専用ログ
// （USER_SPEECH_STARTED_MIC_STATE: expectedAnswerType/aiSpeakingProtected/
// msSinceMicReadyを記録）が正しく実装・配線されていることを、実際の
// 実装コードに対して検証する（tests/test_short_answer_turn.js等と
// 同じ方式）。
//
// 重要: 今回のフェーズはSTEP18/19（実機Mac確認）が終わるまで、
// 挙動そのもの（mute engage/release条件、fallback条件）は一切変更して
// いない。追加したのは純粋な観測用ログ（pushTimelineEvent呼び出し）と、
// その値を計算するための1個のタイムスタンプ変数（lastAiSpeakingEndAt）
// のみである。このテストはそのことも明示的に確認する。
//
// 実行: node tests/test_first_answer_must_count.js

const fs = require('fs');
const path = require('path');
const assert = require('assert');

const ENGINE_JS_PATH = path.join(__dirname, '..', 'frontend', 'public', 'js', 'realtime-voice-engine.js');
const SRC = fs.readFileSync(ENGINE_JS_PATH, 'utf8');

let passed = 0, failed = 0;
function test(name, fn) {
    try {
        fn();
        passed++;
        console.log('  ok - ' + name);
    } catch (e) {
        failed++;
        console.log('  FAIL - ' + name);
        console.log('    ' + (e && e.stack ? e.stack.split('\n').slice(0, 4).join('\n    ') : e));
    }
}

console.log('FAST TURN HOTFIX — FIRST ANSWER MUST COUNT regression tests');
console.log('source: ' + ENGINE_JS_PATH);
console.log('');

// ===== STEP7/8結論の固定: USER_TURN_3S_FALLBACKはSHORT_ANSWER等には物理的に発火し得ない =====

test('STEP7/8) armUserTurnFallbackTimerの冒頭ガードは今も expectedAnswerType!==\'NONE\' で即returnする（SHORT_ANSWER/NAME/PHONE等では3秒fallback自体が絶対にarmされないことの固定）', () => {
    const idx = SRC.indexOf('function armUserTurnFallbackTimer(myGeneration)');
    assert.notStrictEqual(idx, -1, 'armUserTurnFallbackTimer not found');
    const braceStart = SRC.indexOf('{', idx);
    const firstLines = SRC.slice(braceStart, braceStart + 200);
    assert.ok(/if\s*\(expectedAnswerType\s*!==\s*'NONE'\)\s*return;/.test(firstLines),
        'the very first guard must still be the NONE-only scope check (人数/時間=SHORT_ANSWER には3秒fallbackが関与できないことの根拠)');
});

test('STEP9結論の固定: ANSWER_WINDOW_LIMITS_MSに今もSHORT_ANSWERキーが存在しない（人数/時間へのANSWER_WINDOW介入がno-opであることの固定）', () => {
    const m = SRC.match(/const\s+ANSWER_WINDOW_LIMITS_MS\s*=\s*(\{[^}]*\})/);
    assert.notStrictEqual(m, null, 'ANSWER_WINDOW_LIMITS_MS not found');
    const table = eval('(' + m[1] + ')');
    assert.strictEqual(Object.prototype.hasOwnProperty.call(table, 'SHORT_ANSWER'), false,
        'SHORT_ANSWER (TIME/DATE/PARTY_SIZE) must remain absent from ANSWER_WINDOW_LIMITS_MS — this rules out ANSWER_WINDOW as a cause of the reported "must say twice" symptom for 人数/時間');
});

test('STEP7/8結論の固定: classifyExpectedAnswerTypeで「何名様/何時」等は今もSHORT_ANSWERに分類される（3秒fallback対象のNONEとは異なる分類のまま）', () => {
    const idx = SRC.indexOf('function classifyExpectedAnswerType(aiTranscript)');
    assert.notStrictEqual(idx, -1);
    const braceStart = SRC.indexOf('{', idx);
    let depth = 0, i = braceStart;
    for (; i < SRC.length; i++) {
        if (SRC[i] === '{') depth++;
        else if (SRC[i] === '}') { depth--; if (depth === 0) { i++; break; } }
    }
    const fnSrc = SRC.slice(idx, i);
    const vm = require('vm');
    const ctx = { pushTimelineEvent: () => {}, expectedAnswerType: 'NONE', nameAnswerGeneration: 0, nameTurnNormalCompletionSeen: false, phoneAnswerGeneration: 0, phoneTurnNormalCompletionSeen: false, shortChoiceAnswerGeneration: 0, shortChoiceTurnNormalCompletionSeen: false, quickAnswerGeneration: 0, quickAnswerTurnNormalCompletionSeen: false };
    vm.createContext(ctx);
    vm.runInContext(fnSrc, ctx);
    vm.runInContext("classifyExpectedAnswerType('何名様ですか？')", ctx);
    assert.strictEqual(ctx.expectedAnswerType, 'SHORT_ANSWER', '「何名様ですか？」 must classify as SHORT_ANSWER, never NONE (so USER_TURN_3S_FALLBACK cannot apply to it)');
    vm.runInContext("classifyExpectedAnswerType('何時をご希望ですか？')", ctx);
    assert.strictEqual(ctx.expectedAnswerType, 'SHORT_ANSWER', '「何時をご希望ですか？」 must classify as SHORT_ANSWER, never NONE');
});

// ===== 新規診断ログ（USER_SPEECH_STARTED_MIC_STATE）の実装確認 =====

test('診断用タイムスタンプ lastAiSpeakingEndAt が releaseAiSpeakingProtection() 内で、AI_SPEAKING_END記録と同じ実際の解除タイミングでセットされている', () => {
    const idx = SRC.indexOf('function releaseAiSpeakingProtection(reason)');
    assert.notStrictEqual(idx, -1);
    const braceStart = SRC.indexOf('{', idx);
    let depth = 0, i = braceStart;
    for (; i < SRC.length; i++) {
        if (SRC[i] === '{') depth++;
        else if (SRC[i] === '}') { depth--; if (depth === 0) { i++; break; } }
    }
    const fnSrc = SRC.slice(idx, i);
    const setIdx = fnSrc.indexOf('lastAiSpeakingEndAt = performance.now();');
    const logIdx = fnSrc.indexOf("pushTimelineEvent('AI_SPEAKING_END");
    assert.notStrictEqual(setIdx, -1, 'lastAiSpeakingEndAt must be set inside releaseAiSpeakingProtection()');
    assert.notStrictEqual(logIdx, -1, 'AI_SPEAKING_END must still be logged');
    // 多重release防止ガード(if (!aiSpeakingProtected) return;)より後、かつ
    // AI_SPEAKING_ENDログより前（同じ実際の解除タイミング）でセットされること。
    const guardIdx = fnSrc.indexOf('if (!aiSpeakingProtected) return;');
    assert.ok(guardIdx !== -1 && guardIdx < setIdx, 'timestamp must be set only on an actual transition (after the idempotency guard), not on a no-op release call');
    assert.ok(setIdx < logIdx, 'timestamp should be set at (or just before) the same point AI_SPEAKING_END is logged, for temporal accuracy');
});

test('診断ログ USER_SPEECH_STARTED_MIC_STATE が speech_started ハンドラ内に存在し、expectedAnswerType/aiSpeakingProtected/msSinceMicReadyの3値を記録する', () => {
    const idx = SRC.indexOf("} else if (type === 'input_audio_buffer.speech_started') {");
    assert.notStrictEqual(idx, -1);
    const block = SRC.slice(idx, idx + 6000);
    const markerIdx = block.indexOf('USER_SPEECH_STARTED_MIC_STATE');
    assert.notStrictEqual(markerIdx, -1, 'USER_SPEECH_STARTED_MIC_STATE marker must exist inside the speech_started handler');
    const nearby = block.slice(markerIdx, markerIdx + 400);
    assert.ok(nearby.includes('expectedAnswerType='), 'must record the current expectedAnswerType (so NAME/PHONE/TIME/PARTY_SIZE etc. are all covered, not just NONE)');
    assert.ok(nearby.includes('aiSpeakingProtected='), 'must record whether the mic is (still) protected/muted at the moment speech_started fires — the smoking-gun check for STEP4');
    assert.ok(nearby.includes('msSinceMicReady='), 'must record elapsed ms since the mic was last re-enabled, to evaluate the "answer clipped right after unmute" hypothesis (STEP13)');
    assert.ok(nearby.includes('lastAiSpeakingEndAt'), 'the elapsed-time calculation must reference the new lastAiSpeakingEndAt timestamp');
});

test('診断ログはUSER_SPEECH_STARTEDの直後（既存ログの近傍）にあり、AI音声出力中かどうかの既存分岐(if (aiAudioOutputActive))より前に評価される（全ターン種別で必ず記録される保証）', () => {
    const idx = SRC.indexOf("pushTimelineEvent('USER_SPEECH_STARTED (AI音声出力中=");
    assert.notStrictEqual(idx, -1);
    // 実測: USER_SPEECH_STARTED_MIC_STATE診断ログの説明コメントが長いため、
    // if (aiAudioOutputActive) { はアンカーから1282文字後に出現する。
    // ウィンドウを700→1400へ拡張（測定値+余裕分）。
    const block = SRC.slice(idx, idx + 1400);
    const micStateIdx = block.indexOf('USER_SPEECH_STARTED_MIC_STATE');
    const branchIdx = block.indexOf('if (aiAudioOutputActive) {');
    assert.notStrictEqual(micStateIdx, -1);
    assert.notStrictEqual(branchIdx, -1);
    assert.ok(micStateIdx < branchIdx, 'the new diagnostic must be unconditional (not nested inside the aiAudioOutputActive-only branch), so it fires for every speech_started regardless of AI state or answer type');
});

test('挙動不変の確認: 今回追加した診断ログはpushTimelineEvent呼び出しのみで、dc.send/response.create/track.enabled等の実際の制御は一切追加していない', () => {
    const idx = SRC.indexOf('USER_SPEECH_STARTED_MIC_STATE');
    const block = SRC.slice(idx - 30, idx + 400);
    assert.ok(!block.includes('dc.send'), 'the new diagnostic block must not send anything over the data channel');
    assert.ok(!block.includes('track.enabled'), 'the new diagnostic block must not itself toggle mic mute state (observation only)');
});

test('REGRESSION) dc.send(JSON.stringify(...)) の呼び出し箇所は今回も9箇所のまま（今回のホットフィックス調査フェーズは純粋な観測ログ追加のみで、新しい送信は一切追加していない）', () => {
    const sendCount = (SRC.match(/dc\.send\(JSON\.stringify\(/g) || []).length;
    assert.strictEqual(sendCount, 9, 'baseline must remain 9 — this diagnostic-only phase adds zero new dc.send call sites');
});

// ===== startCall()リセット配線の確認（診断値の通話間リーク防止） =====

test('LIVE-WIRING) startCall()のリセットブロックが lastAiSpeakingEndAt を新しい通話ごとにリセットしている（前回通話のタイムスタンプが次の通話のspeech_started判定に混入しない）', () => {
    const idx = SRC.indexOf('if (aiSpeakingProtectionSafetyTimerId !== null) { clearTimeout(aiSpeakingProtectionSafetyTimerId); aiSpeakingProtectionSafetyTimerId = null; }\n            aiSpeakingProtected = false;');
    assert.notStrictEqual(idx, -1, 'startCall() AI SPEAKING PROTECTION reset block not found');
    const block = SRC.slice(idx, idx + 400);
    assert.ok(block.includes('lastAiSpeakingEndAt = null;'), 'startCall() must reset lastAiSpeakingEndAt for every new call');
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
if (failed > 0) process.exit(1);
