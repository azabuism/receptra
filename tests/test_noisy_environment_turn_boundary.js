'use strict';

// FAST TURN — NOISY ENVIRONMENT / 3-SECOND TURN BOUNDARY regression test suite
//
// 背景: 実機Macテストで、call.htmlのRealtime音声AI「1st turn（要件を聞く）」
// 場面において、静かな環境では正常だが、周囲の雑音が多い環境ではユーザー
// 発話終了後もAIが黙ったままになり次の質問へ進まないことがある、という
// 症状が報告された。
//
// 監査の結果（推測ではなくコード・公式ドキュメントで確認済みの事実）:
//   (1) OpenAI Realtime APIのspeech_started/speech_stoppedイベントには
//       雑音と人間発話を区別できるフィールドが一切存在しない。
//   (2) expectedAnswerType==='NONE'（第一声の挨拶直後を含む）には、
//       startAnswerWindowIfNeeded()が即returnするため、既存のNAME/PHONE/
//       YES_NO/SHORT_CHOICE/SHORT_ANSWERのいずれのForced Commit機構も
//       一切働かない、唯一無防備な状態だった。
//   (3) interrupt_response（未設定＝デフォルトtrue相当）により、AIの応答
//       出力中に雑音起因のspeech_startedが発生すると、サーバー側が自動的に
//       その応答をcancel（truncate）してしまう（PHASE6「無言化調査」の
//       既存診断ログが実際に観測していた事象と一致）。
//
// この2つの根本原因に対応するため、以下の独立した2つの新機構を追加した:
//   - USER_TURN_3S_FALLBACK: expectedAnswerType==='NONE'の間のみ、
//     speech_stopped起点で3秒のfallbackタイマーをarmし、正常完了イベント
//     （committed/item_created/response.created/function_call）が来ないまま
//     満了した場合にのみ、手動でinput_audio_buffer.commitを1回だけ送る
//     （既存Forced Commit4機構とは完全に独立した新規コード）。
//   - AI SPEAKING PROTECTION: AIの音声出力中（output_audio_buffer.started〜
//     stopped/cleared）は、ローカルのマイクtrack自体をMediaStreamTrack.
//     enabled=falseで一時的にミュートし、雑音・音声を問わずサーバーへ
//     一切音声を送らないことで、AIの発話が途中で打ち切られることを確定的に
//     防ぐ（トレードオフ: この区間は人間の明確なbarge-inも同様に届かない。
//     OpenAI側に雑音/人間発話の判別シグナルが無いため、両立は現在の
//     APIだけでは不可能。この限界は最終報告に明記する）。
//
// このテストは、frontend/public/js/realtime-voice-engine.js内の実際の関数
// ソースを直接抽出し、Node.jsのvmモジュール上で最小限のモックとともに実行
// することで、「本物の実装コード」に対してアサーションを行う
// （tests/test_short_answer_turn.js等と同じ方式）。ユーザー指示の必須テスト
// A〜Jに対応する。
//
// STOP CONDITION（重要）: このテストが全て通過することは「コード上・自動
// テスト上、設計どおりに動く」ことの確認に過ぎない。この問題自体は実機・
// マイク・雑音環境に依存するため、実機Macでの確認なしに「直った」とは
// 判断しない（最終報告で(a)コード確認/(b)自動テスト確認/(c)実機確認要、を
// 明確に分離する）。
//
// 実行: node tests/test_noisy_environment_turn_boundary.js

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ENGINE_JS_PATH = path.join(__dirname, '..', 'frontend', 'public', 'js', 'realtime-voice-engine.js');
const SRC = fs.readFileSync(ENGINE_JS_PATH, 'utf8');

function extractFunctionSource(src, fnName, fromIndex) {
    const startToken = 'function ' + fnName + '(';
    const startIdx = src.indexOf(startToken, fromIndex || 0);
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

function extractConstExpr(src, name) {
    const re = new RegExp('const\\s+' + name + '\\s*=\\s*([^;]+);');
    const m = src.match(re);
    if (!m) throw new Error('const not found in source: ' + name);
    return m[1].trim();
}

const FN = {
    isStaleCallEvent: extractFunctionSource(SRC, 'isStaleCallEvent'),
    msSince: extractFunctionSource(SRC, 'msSince'),
    armUserTurnFallbackTimer: extractFunctionSource(SRC, 'armUserTurnFallbackTimer'),
    cancelUserTurnFallbackTimer: extractFunctionSource(SRC, 'cancelUserTurnFallbackTimer'),
    maybeSendUserTurnFallbackCommit: extractFunctionSource(SRC, 'maybeSendUserTurnFallbackCommit'),
    engageAiSpeakingProtection: extractFunctionSource(SRC, 'engageAiSpeakingProtection'),
    releaseAiSpeakingProtection: extractFunctionSource(SRC, 'releaseAiSpeakingProtection'),
};

const USER_TURN_FALLBACK_GRACE_MS_EXPR = extractConstExpr(SRC, 'USER_TURN_FALLBACK_GRACE_MS');
const USER_TURN_FALLBACK_GRACE_MS = eval('(' + USER_TURN_FALLBACK_GRACE_MS_EXPR + ')');
const AI_SPEAKING_PROTECTION_MAX_MS_EXPR = extractConstExpr(SRC, 'AI_SPEAKING_PROTECTION_MAX_MS');
const AI_SPEAKING_PROTECTION_MAX_MS = eval('(' + AI_SPEAKING_PROTECTION_MAX_MS_EXPR + ')');

assert.strictEqual(USER_TURN_FALLBACK_GRACE_MS, 3000, 'USER_TURN_FALLBACK_GRACE_MS must be exactly 3000ms per user instruction ("約3秒を上限の目安として")');
assert.strictEqual(AI_SPEAKING_PROTECTION_MAX_MS, 20000, 'AI_SPEAKING_PROTECTION_MAX_MS safety-net ceiling must remain 20000ms');

function buildSandbox(overrides) {
    const timers = new Map();
    // clearTimeoutされた後も、防御的世代ガード自体をテストできるよう、
    // fn参照だけは別registryに保持しておく（実ブラウザのclearTimeoutは
    // タイマーを完全に消すが、「もし取りこぼしがあったら」という
    // defense-in-depthのガード自体を直接検証するためのテスト専用の仕掛け。
    // timers（生存中のみ）とは別に、全fnをここに残す）。
    const allFnsById = new Map();
    let nextTimerId = 1;
    const events = [];

    const context = {
        performance: { now: () => Date.now() },
        pushTimelineEvent: (text) => { events.push(text); },
        console: console,
    };
    // 重要: 同一のtrackオブジェクトを毎回返す（実際のMediaStreamTrackと同じく
    // 単一の永続オブジェクト）。呼び出しのたびに新しいオブジェクトを返すと、
    // engage/release前後で異なる参照を見比べてしまい、実際のミュート挙動を
    // 正しく検証できない。
    const sharedTrack = { enabled: true };
    const state = Object.assign({
        callGeneration: 1,
        ended: false,
        expectedAnswerType: 'NONE',
        toolContinuationTraceActive: false,
        userTurnFallbackGeneration: 0,
        userTurnFallbackTimerId: null,
        userTurnFallbackArmedAt: null,
        userTurnFallbackArmedForGeneration: null,
        userTurnFallbackCommitSentGeneration: null,
        userTurnFallbackNormalCompletionSeen: false,
        userTurnFallbackCommitSentAt: null,
        userTurnFallbackCommitCallGeneration: null,
        aiSpeakingProtected: false,
        aiSpeakingProtectionSafetyTimerId: null,
        localStream: { getAudioTracks: () => [sharedTrack] },
        dc: { readyState: 'open', sent: [], send(payload) { this.sent.push(JSON.parse(payload)); } },
    }, overrides || {});
    Object.assign(context, state);
    context.setTimeout = (fn, delay) => {
        const id = nextTimerId++;
        timers.set(id, { fn, delay });
        allFnsById.set(id, { fn, delay });
        return id;
    };
    context.clearTimeout = (id) => { timers.delete(id); };

    vm.createContext(context);
    vm.runInContext(
        'const USER_TURN_FALLBACK_GRACE_MS = ' + USER_TURN_FALLBACK_GRACE_MS_EXPR + ';\n' +
        'const AI_SPEAKING_PROTECTION_MAX_MS = ' + AI_SPEAKING_PROTECTION_MAX_MS_EXPR + ';\n' +
        Object.values(FN).join('\n\n'),
        context
    );

    return {
        ctx: context,
        events,
        fireTimer: (id) => { const t = timers.get(id); timers.delete(id); if (t) t.fn(); },
        // defense-in-depthガード専用: clearTimeoutで既に消えたタイマーIDでも、
        // そのタイマーが本来持っていたコールバック自体を強制的に実行する
        // （本物のclearTimeoutでは起こり得ないが、「もし取りこぼしたら」という
        // 内部の世代ガード自体が正しく機能するかを直接検証するためのテスト専用API）。
        fireTimerEvenIfCancelled: (id) => { const t = allFnsById.get(id); if (t) t.fn(); },
        timerDelay: (id) => { const t = timers.get(id); return t ? t.delay : null; },
        timerCount: () => timers.size,
        activeTimerIds: () => Array.from(timers.keys()),
    };
}

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

console.log('NOISY ENVIRONMENT / 3-SECOND TURN BOUNDARY regression tests');
console.log('source: ' + ENGINE_JS_PATH);
console.log('');

// ===== A: 正常なユーザー発話 → 従来通り自然に回答 =====

test('A: expectedAnswerType !== NONE (通常の分類済みターン) では armUserTurnFallbackTimer は一切armしない（既存の5機構の領域を侵食しない）', () => {
    for (const t of ['NAME', 'PHONE', 'YES_NO', 'SHORT_CHOICE', 'VISIT_REASON', 'SHORT_ANSWER']) {
        const sb = buildSandbox({ expectedAnswerType: t });
        vm.runInContext('armUserTurnFallbackTimer(callGeneration)', sb.ctx);
        assert.strictEqual(sb.timerCount(), 0, 'no fallback timer should be armed for expectedAnswerType=' + t);
        assert.strictEqual(sb.ctx.userTurnFallbackGeneration, 0, 'generation must not advance for expectedAnswerType=' + t);
    }
});

test('A2: expectedAnswerType===NONE で armすると USER_TURN_FALLBACK_ARMED が1回だけ記録される', () => {
    const sb = buildSandbox({ expectedAnswerType: 'NONE' });
    vm.runInContext('armUserTurnFallbackTimer(callGeneration)', sb.ctx);
    assert.strictEqual(sb.timerCount(), 1, 'exactly one timer must be armed');
    assert.strictEqual(sb.ctx.userTurnFallbackGeneration, 1);
    const armedEvents = sb.events.filter((e) => e.startsWith('USER_TURN_FALLBACK_ARMED'));
    assert.strictEqual(armedEvents.length, 1);
});

// ===== B: ユーザー発話後に静音 → 正常にAIへturn =====

test('B: armした後、満了前に正常完了(committed相当)を先に見た場合はfallback commitを送らない', () => {
    const sb = buildSandbox({ expectedAnswerType: 'NONE' });
    vm.runInContext('armUserTurnFallbackTimer(callGeneration)', sb.ctx);
    const timerId = sb.activeTimerIds()[0];
    // 正常完了イベント（committed）をシミュレート: 本物のハンドラがしているのと
    // 同じく、normalCompletionSeenを立ててからcancelする。
    sb.ctx.userTurnFallbackNormalCompletionSeen = true;
    vm.runInContext("cancelUserTurnFallbackTimer('committed')", sb.ctx);
    assert.strictEqual(sb.timerCount(), 0, 'timer must be cancelled on normal committed completion');
    assert.strictEqual(sb.ctx.dc.sent.length, 0, 'no manual commit should ever be sent on the normal-completion path');
});

test('B2: 満了直前に正常完了イベントが先着した場合（レース）、fallback送信本体自身もnormalCompletionSeenで二重送信を防ぐ', () => {
    const sb = buildSandbox({ expectedAnswerType: 'NONE' });
    vm.runInContext('armUserTurnFallbackTimer(callGeneration)', sb.ctx);
    const timerId = sb.activeTimerIds()[0];
    // タイマーはキャンセルされずに残っているが、直前にresponse.created相当が
    // 届いてnormalCompletionSeenが立った、というレースを再現する。
    sb.ctx.userTurnFallbackNormalCompletionSeen = true;
    sb.fireTimer(timerId);
    assert.strictEqual(sb.ctx.dc.sent.length, 0, 'the race guard inside maybeSendUserTurnFallbackCommit must prevent a redundant commit');
    assert.ok(sb.events.some((e) => e.startsWith('USER_TURN_FALLBACK_COMMIT_SKIPPED (reason=normal_completion_won_race)')));
});

// ===== C: 雑音が継続 → AIが永久待機しない =====

test('C: speech_started/speech_stoppedの雑音ping-pongはタイマーを蓄積させず、常に高々1本に保つ', () => {
    const sb = buildSandbox({ expectedAnswerType: 'NONE' });
    for (let i = 0; i < 5; i++) {
        vm.runInContext('armUserTurnFallbackTimer(callGeneration)', sb.ctx); // speech_stopped相当（内部でcancel→re-arm）
        assert.ok(sb.timerCount() <= 1, 'no more than one live fallback timer at any point (iteration ' + i + ')');
        vm.runInContext("cancelUserTurnFallbackTimer('speech_started_again')", sb.ctx); // speech_started相当
        assert.strictEqual(sb.timerCount(), 0, 'speech_started must always clear the pending fallback (iteration ' + i + ')');
    }
    // 最終的にノイズが止み、次のspeech_stoppedの後に満了まで到達すれば1回だけ発火する。
    vm.runInContext('armUserTurnFallbackTimer(callGeneration)', sb.ctx);
    const timerId = sb.activeTimerIds()[0];
    sb.fireTimer(timerId);
    assert.strictEqual(sb.ctx.dc.sent.length, 1, 'exactly one manual commit once the noise settles and the grace period elapses uninterrupted');
});

// ===== D: 聞き取り不能 → 約3秒を目安に区切る → (NOISE_RECOVERYマーカー) =====

test('D: 正常完了イベントが一切来ないまま3000ms満了すると、ちょうど1回だけinput_audio_buffer.commitを送る', () => {
    const sb = buildSandbox({ expectedAnswerType: 'NONE' });
    vm.runInContext('armUserTurnFallbackTimer(callGeneration)', sb.ctx);
    const timerId = sb.activeTimerIds()[0];
    assert.strictEqual(sb.timerDelay(timerId), USER_TURN_FALLBACK_GRACE_MS, 'the fallback must fire at exactly the 3000ms guideline');
    sb.fireTimer(timerId);
    assert.strictEqual(sb.ctx.dc.sent.length, 1, 'exactly one dc.send must occur');
    assert.deepStrictEqual(sb.ctx.dc.sent[0], { type: 'input_audio_buffer.commit' },
        'the fallback must send ONLY input_audio_buffer.commit — never response.create, never conversation.item.* content');
    assert.ok(sb.events.some((e) => e.startsWith('USER_TURN_3S_FALLBACK')), 'USER_TURN_3S_FALLBACK diagnostic marker must be recorded (実機DEBUG要件)');
    assert.ok(sb.events.some((e) => e.startsWith('NOISE_RECOVERY')), 'NOISE_RECOVERY diagnostic marker must be recorded (実機DEBUG要件)');
});

test('D2: 世代ガード) 満了時に既に次のarmへ進んでいれば、本来clearTimeoutで消えているはずの古いタイマーが万一取りこぼされて発火しても、世代不一致ガードにより何もしない（defense-in-depth）', () => {
    const sb = buildSandbox({ expectedAnswerType: 'NONE' });
    vm.runInContext('armUserTurnFallbackTimer(callGeneration)', sb.ctx);
    const staleTimerId = sb.activeTimerIds()[0];
    // 新しいspeech_stoppedでre-armされ、世代が進む（内部でcancel→+1）。
    // 通常のclearTimeoutならstaleTimerIdはここで完全に無効化されているはず。
    vm.runInContext('armUserTurnFallbackTimer(callGeneration)', sb.ctx);
    assert.strictEqual(sb.ctx.userTurnFallbackGeneration, 2, 'generation must have advanced to 2');
    // その上で、あえて古いタイマーのコールバック自体を強制発火させ
    // （本来のブラウザ環境では起こり得ない状況を意図的に作り出し）、
    // setTimeoutコールバック内部の
    // `if (armedForGeneration !== userTurnFallbackGeneration) return;` が
    // 独立した2段目の防御として機能することを確認する。
    sb.fireTimerEvenIfCancelled(staleTimerId);
    assert.strictEqual(sb.ctx.dc.sent.length, 0, 'a stale (already superseded) generation must never send a commit, even if its cleared timer were somehow invoked');
});

test('D3: dc未オープン・送信例外は非fatal（通話を継続する）', () => {
    const sbClosed = buildSandbox({ expectedAnswerType: 'NONE', dc: { readyState: 'connecting', sent: [] } });
    vm.runInContext('armUserTurnFallbackTimer(callGeneration)', sbClosed.ctx);
    sbClosed.fireTimer(sbClosed.activeTimerIds()[0]);
    assert.ok(sbClosed.events.some((e) => e.startsWith('USER_TURN_FALLBACK_COMMIT_SKIPPED (reason=datachannel_not_open)')));

    const sbThrow = buildSandbox({
        expectedAnswerType: 'NONE',
        dc: { readyState: 'open', send() { throw new Error('boom'); } },
    });
    vm.runInContext('armUserTurnFallbackTimer(callGeneration)', sbThrow.ctx);
    assert.doesNotThrow(() => sbThrow.fireTimer(sbThrow.activeTimerIds()[0]));
    assert.ok(sbThrow.events.some((e) => e.startsWith('USER_TURN_FALLBACK_COMMIT_ERROR')));
});

// ===== E: AIが質問中に背景雑音 → AI発話が途中終了しない =====

test('E: engageAiSpeakingProtectionはマイクtrack.enabledをfalseにし、releaseするまでtrueへ戻さない', () => {
    const sb = buildSandbox({});
    const track = sb.ctx.localStream.getAudioTracks()[0];
    assert.strictEqual(track.enabled, true);
    vm.runInContext("engageAiSpeakingProtection('output_audio_buffer_started')", sb.ctx);
    assert.strictEqual(track.enabled, false, 'mic track must be muted while AI is speaking');
    assert.strictEqual(sb.ctx.aiSpeakingProtected, true);
    assert.ok(sb.events.some((e) => e.startsWith('AI_SPEAKING_START')));
    assert.ok(sb.events.some((e) => e.startsWith('AI_SPEAKING_PROTECTED')), 'AI_SPEAKING_PROTECTED diagnostic marker must be recorded (実機DEBUG要件)');
    // 保護中に何度engageを呼んでも多重発火しない（no-op）
    vm.runInContext("engageAiSpeakingProtection('output_audio_buffer_started')", sb.ctx);
    assert.strictEqual(sb.events.filter((e) => e.startsWith('AI_SPEAKING_START')).length, 1, 'engage must be idempotent while already protected');
    vm.runInContext("releaseAiSpeakingProtection('output_audio_buffer_stopped')", sb.ctx);
    assert.strictEqual(track.enabled, true, 'mic track must be unmuted once the AI has finished speaking');
    assert.strictEqual(sb.ctx.aiSpeakingProtected, false);
    assert.ok(sb.events.some((e) => e.startsWith('AI_SPEAKING_END')));
});

test('E2: engage/releaseはdc.sendを一切呼ばない（response.cancel/conversation.item.truncateを一切送らない、純粋なローカルミュート機構）', () => {
    const sb = buildSandbox({});
    vm.runInContext("engageAiSpeakingProtection('output_audio_buffer_started')", sb.ctx);
    vm.runInContext("releaseAiSpeakingProtection('output_audio_buffer_stopped')", sb.ctx);
    assert.strictEqual(sb.ctx.dc.sent.length, 0, 'AI SPEAKING PROTECTION must never touch the Realtime data channel — muting is purely local (WebRTC track.enabled)');
});

test('E3: 保護中に安全網タイマーが動いており、AI_SPEAKING_PROTECTION_MAX_MS経過で強制解除される（マイク恒久ミュート防止）', () => {
    const sb = buildSandbox({});
    vm.runInContext("engageAiSpeakingProtection('output_audio_buffer_started')", sb.ctx);
    const safetyTimerId = sb.activeTimerIds().find((id) => sb.timerDelay(id) === AI_SPEAKING_PROTECTION_MAX_MS);
    assert.ok(safetyTimerId, 'a safety-net timer at AI_SPEAKING_PROTECTION_MAX_MS must be armed');
    sb.fireTimer(safetyTimerId);
    assert.strictEqual(sb.ctx.aiSpeakingProtected, false, 'the safety net must force-unmute if stopped/cleared/response.done never arrive');
    assert.strictEqual(sb.ctx.localStream.getAudioTracks()[0].enabled, true);
    assert.ok(sb.events.some((e) => e.startsWith('AI_SPEAKING_PROTECTION_SAFETY_UNMUTE')));
});

test('E4: マイクtrackが存在しない場合はfail-openで何もしない（例外を投げない）', () => {
    const sb = buildSandbox({ localStream: { getAudioTracks: () => [] } });
    assert.doesNotThrow(() => vm.runInContext("engageAiSpeakingProtection('output_audio_buffer_started')", sb.ctx));
    assert.strictEqual(sb.ctx.aiSpeakingProtected, false, 'must not claim protection is engaged when there is no track to mute');
});

// ===== F: 取得済みslotあり → 取得済みslotを聞き直さない =====
// (このJSレイヤーの新機構自体は、AIが実際に何を話すか＝会話内容には一切
//  関与しない。manual commitはinput_audio_buffer.commitのみで、
//  conversation.item.create等で会話履歴やslot情報を書き換える処理は一切
//  存在しない。実際のNOISE RECOVERY応答文言はapp/services/realtime_voice_ai.py
//  のsession instructions側の責務であり、そちらはPythonのsmoke testで別途
//  検証する。ここではJS側がconversation contentに一切触れないことだけを
//  確認する。)

test('F: maybeSendUserTurnFallbackCommitはinput_audio_buffer.commit以外のいかなるconversation/response操作も行わない（既知slotの上書き・再送は一切しない）', () => {
    assert.ok(!FN.maybeSendUserTurnFallbackCommit.includes("type: 'conversation.item"),
        'the fallback must never touch conversation.item.* (it cannot and must not rewrite what the user already said)');
    // 注: ソースコメント中には「response.createを送ることは絶対にしない」旨の
    // 説明文が含まれる（"response.create"という語自体はコメントに出現する）ため、
    // 単純な文字列不在チェックではなく、実際のdc.send用オブジェクトリテラル
    // パターン（type: 'response.create'）の不在を確認する（testIと同じ方式）。
    assert.ok(!FN.maybeSendUserTurnFallbackCommit.includes("type: 'response.create'"),
        'the fallback must never create a new response itself (see also test I)');
});

// ===== G: 明確な人間barge-in → 現在可能な範囲で既存UXを維持 =====

test('G: AI SPEAKING PROTECTIONが働いていない区間（release後）では、track.enabledはtrueのままで通常のbarge-inを一切妨げない（回帰確認）', () => {
    const sb = buildSandbox({});
    // 保護区間に一度も入っていない通常状態
    assert.strictEqual(sb.ctx.localStream.getAudioTracks()[0].enabled, true);
    assert.strictEqual(sb.ctx.aiSpeakingProtected, false);
    // このモジュール自体はaiSpeakingProtected===falseの間、track.enabledに
    // 一切触れない（engage/releaseの呼び出し自体がなければno-op）。
});

test('G2: 正直な限界の明文化) 保護区間中はマイクが物理的にミュートされるため、雑音由来か人間の明確な発話かを問わず一律にサーバーへ届かない（現在のOpenAI Realtime APIには両者を区別するシグナルが存在しないための設計判断であることをソースコードのコメントが明記している）', () => {
    const commentBlockStart = SRC.indexOf('===== AI SPEAKING PROTECTION（今回追加） =====');
    assert.notStrictEqual(commentBlockStart, -1, 'the AI SPEAKING PROTECTION design-rationale comment block must exist in source');
    const commentBlock = SRC.slice(commentBlockStart, commentBlockStart + 2600);
    assert.ok(commentBlock.includes('雑音と人間発話を区別する') || commentBlock.includes('区別する既存シグナルが無い') || commentBlock.includes('区別するシグナルが存在しない'),
        'the trade-off (cannot distinguish noise from genuine human speech with the current API) must be documented in source, not silently implemented');
});

// ===== H: Tool call中 → 3秒fallbackが誤発火しない =====

test('H: toolContinuationTraceActive===true の間はarmUserTurnFallbackTimer自体が何もしない', () => {
    const sb = buildSandbox({ expectedAnswerType: 'NONE', toolContinuationTraceActive: true });
    vm.runInContext('armUserTurnFallbackTimer(callGeneration)', sb.ctx);
    assert.strictEqual(sb.timerCount(), 0, 'no timer must be armed while a Tool round-trip is in progress');
});

test('H2: armした後にTool呼び出しが始まった場合（タイマー発火時点でtoolContinuationTraceActive===trueになっている）、発火時ガードによりcommitを送らない', () => {
    const sb = buildSandbox({ expectedAnswerType: 'NONE', toolContinuationTraceActive: false });
    vm.runInContext('armUserTurnFallbackTimer(callGeneration)', sb.ctx);
    const timerId = sb.activeTimerIds()[0];
    // タイマーが生きたまま、Tool呼び出しが始まった状況を再現する。
    sb.ctx.toolContinuationTraceActive = true;
    sb.fireTimer(timerId);
    assert.strictEqual(sb.ctx.dc.sent.length, 0, 'the fired-timer callback must re-check toolContinuationTraceActive and skip if a Tool call started in the meantime');
    assert.ok(sb.events.some((e) => e.startsWith('USER_TURN_FALLBACK_SKIPPED (reason=tool_call_active)')));
});

// ===== I: response生成中 → duplicate responseを作らない =====

test('I: armUserTurnFallbackTimer/maybeSendUserTurnFallbackCommitのソース中に response.create を送る箇所が一切ない', () => {
    assert.ok(!FN.armUserTurnFallbackTimer.includes("type: 'response.create'"));
    assert.ok(!FN.maybeSendUserTurnFallbackCommit.includes("type: 'response.create'"));
});

test('I2: 1世代につき最大1回しかmanual commitを送らない（発火後に同じ世代を再度呼んでも二重送信しない）', () => {
    const sb = buildSandbox({ expectedAnswerType: 'NONE' });
    vm.runInContext('armUserTurnFallbackTimer(callGeneration)', sb.ctx);
    const timerId = sb.activeTimerIds()[0];
    sb.fireTimer(timerId);
    assert.strictEqual(sb.ctx.dc.sent.length, 1);
    // 同じ世代に対して手動で再度呼び出しても送信されない。
    vm.runInContext('maybeSendUserTurnFallbackCommit(callGeneration, 1)', sb.ctx);
    assert.strictEqual(sb.ctx.dc.sent.length, 1, 'at most one manual commit per generation, even if invoked again');
});

// ===== J: 通話終了後 → timer/fallbackが残らない =====

test('J: isStaleCallEventがtrueの間はmaybeSendUserTurnFallbackCommitが何もしない（通話終了後の遅延発火対策）', () => {
    const sb = buildSandbox({ expectedAnswerType: 'NONE' });
    vm.runInContext('armUserTurnFallbackTimer(callGeneration)', sb.ctx);
    const timerId = sb.activeTimerIds()[0];
    sb.ctx.ended = true; // 通話終了をシミュレート
    sb.fireTimer(timerId);
    assert.strictEqual(sb.ctx.dc.sent.length, 0, 'ended call must never trigger a manual commit');
});

test('J2: LIVE-WIRING) startCall()のリセットブロックがUSER_TURN_3S_FALLBACK/AI SPEAKING PROTECTIONの全stateを初期化している', () => {
    // 'quickAnswerFinalizeGeneration = null;' 自体は変数宣言箇所
    // （let quickAnswerFinalizeGeneration = null;）にも一致してしまうため、
    // startCall()内のリセットブロック直前にある一意な行
    // （quickAnswerFinalizeTimerIdの防御的clearTimeout）を確実なアンカーとして使う。
    const idx = SRC.indexOf('if (quickAnswerFinalizeTimerId !== null) { clearTimeout(quickAnswerFinalizeTimerId); quickAnswerFinalizeTimerId = null; }');
    assert.notStrictEqual(idx, -1);
    const block = SRC.slice(idx, idx + 1700);
    for (const stateVar of [
        'userTurnFallbackTimerId', 'userTurnFallbackGeneration', 'userTurnFallbackArmedAt',
        'userTurnFallbackArmedForGeneration', 'userTurnFallbackCommitSentGeneration',
        'userTurnFallbackNormalCompletionSeen', 'userTurnFallbackCommitSentAt',
        'userTurnFallbackCommitCallGeneration', 'aiSpeakingProtectionSafetyTimerId', 'aiSpeakingProtected',
    ]) {
        assert.ok(block.includes(stateVar), 'startCall() reset block must reset ' + stateVar + ' for every new call (no cross-call leakage)');
    }
});

test('J3: LIVE-WIRING) cleanupConnection()がuserTurnFallbackTimerId/aiSpeakingProtectionSafetyTimerId/aiSpeakingProtectedを確実にクリアしている', () => {
    const idx = SRC.indexOf('if (silenceWarningTimerId) { clearTimeout(silenceWarningTimerId); silenceWarningTimerId = null; }');
    assert.notStrictEqual(idx, -1);
    const block = SRC.slice(idx, idx + 900);
    assert.ok(block.includes('if (userTurnFallbackTimerId)') && block.includes('clearTimeout(userTurnFallbackTimerId)'),
        'cleanupConnection() must clear any leftover USER_TURN_3S_FALLBACK timer');
    assert.ok(block.includes('if (aiSpeakingProtectionSafetyTimerId)') && block.includes('clearTimeout(aiSpeakingProtectionSafetyTimerId)'),
        'cleanupConnection() must clear any leftover AI SPEAKING PROTECTION safety-net timer');
    assert.ok(block.includes('aiSpeakingProtected = false'),
        'cleanupConnection() must reset aiSpeakingProtected so no stale mute state survives across calls');
});

// ===== LIVE-WIRING: 実際のhandleDataChannelEvent内の配線確認 =====
// (ここまでのテストはvm上での関数単体の挙動確認。以下は「本当にその関数が
//  正しいイベントハンドラから呼ばれているか」を実ソーステキストに対して
//  静的に検証する。tests/test_tool_continuation_resilience.js等と同じ方式。)

test('K: LIVE-WIRING) output_audio_buffer.started ハンドラが engageAiSpeakingProtection を呼んでいる', () => {
    const idx = SRC.indexOf("if (type === 'output_audio_buffer.started') {");
    assert.notStrictEqual(idx, -1);
    const block = SRC.slice(idx, idx + 900);
    assert.ok(block.includes("engageAiSpeakingProtection('output_audio_buffer_started')"));
});

test('K2: LIVE-WIRING) output_audio_buffer.stopped ハンドラが releaseAiSpeakingProtection を呼んでいる', () => {
    const idx = SRC.indexOf("} else if (type === 'output_audio_buffer.stopped') {");
    assert.notStrictEqual(idx, -1);
    const block = SRC.slice(idx, idx + 500);
    assert.ok(block.includes("releaseAiSpeakingProtection('output_audio_buffer_stopped')"));
});

test('K3: LIVE-WIRING) output_audio_buffer.cleared ハンドラが releaseAiSpeakingProtection を呼んでいる（安全網）', () => {
    const idx = SRC.indexOf("} else if (type === 'output_audio_buffer.cleared') {");
    assert.notStrictEqual(idx, -1);
    const block = SRC.slice(idx, idx + 2000);
    assert.ok(block.includes("releaseAiSpeakingProtection('output_audio_buffer_cleared')"));
});

test('K4: LIVE-WIRING) response.done ハンドラが releaseAiSpeakingProtection を安全網として呼んでいる', () => {
    const idx = SRC.indexOf("} else if (type === 'response.done') {");
    assert.notStrictEqual(idx, -1);
    const block = SRC.slice(idx, idx + 1900);
    assert.ok(block.includes("releaseAiSpeakingProtection('response_done_fallback')"));
});

test('L: LIVE-WIRING) input_audio_buffer.speech_stopped ハンドラが armUserTurnFallbackTimer を呼んでいる', () => {
    const idx = SRC.indexOf("} else if (type === 'input_audio_buffer.speech_stopped') {");
    assert.notStrictEqual(idx, -1);
    const block = SRC.slice(idx, idx + 3900);
    assert.ok(block.includes('armUserTurnFallbackTimer(callGeneration)'));
});

test('L2: LIVE-WIRING) input_audio_buffer.speech_started ハンドラが cancelUserTurnFallbackTimer を呼び、かつUSER_TURN_STARTを記録している', () => {
    const idx = SRC.indexOf("} else if (type === 'input_audio_buffer.speech_started') {");
    assert.notStrictEqual(idx, -1);
    // FAST TURN HOTFIX（FIRST ANSWER MUST COUNT）フェーズで、speech_startedハンドラ
    // 冒頭付近にUSER_SPEECH_STARTED_MIC_STATE診断ログ（実機DEBUG要件）が追加され、
    // 実測オフセットが5249文字まで伸びたため、ウィンドウを4600→5800へ拡張。
    const block = SRC.slice(idx, idx + 5800);
    assert.ok(block.includes("cancelUserTurnFallbackTimer('speech_started_again')"));
    assert.ok(block.includes("if (expectedAnswerType === 'NONE')") && block.includes("USER_TURN_START"));
});

test('M: LIVE-WIRING) input_audio_buffer.committed ハンドラが userTurnFallbackNormalCompletionSeen を立てて cancelUserTurnFallbackTimer を呼んでいる', () => {
    const idx = SRC.indexOf("} else if (type === 'input_audio_buffer.committed') {");
    assert.notStrictEqual(idx, -1);
    const block = SRC.slice(idx, idx + 2600);
    assert.ok(block.includes('userTurnFallbackNormalCompletionSeen = true') && block.includes("cancelUserTurnFallbackTimer('committed')"));
});

test('M2: LIVE-WIRING) conversation.item.created(user) ハンドラが userTurnFallbackNormalCompletionSeen を立てて cancelUserTurnFallbackTimer を呼んでいる', () => {
    const idx = SRC.indexOf("} else if (type === 'conversation.item.created' && msg.item && msg.item.role === 'user') {");
    assert.notStrictEqual(idx, -1);
    const block = SRC.slice(idx, idx + 2000);
    assert.ok(block.includes('userTurnFallbackNormalCompletionSeen = true') && block.includes("cancelUserTurnFallbackTimer('item')"));
});

test('M3: LIVE-WIRING) response.created ハンドラが userTurnFallbackNormalCompletionSeen を立てて cancelUserTurnFallbackTimer を呼んでいる', () => {
    const idx = SRC.indexOf('maybeLogQuickAnswerReactionElapsed(\'responseCreated\', \'response.created\');');
    assert.notStrictEqual(idx, -1);
    const block = SRC.slice(idx, idx + 1400);
    assert.ok(block.includes('userTurnFallbackNormalCompletionSeen = true') && block.includes("cancelUserTurnFallbackTimer('response_created')"));
});

test('M4: LIVE-WIRING) function_call検出ハンドラが userTurnFallbackNormalCompletionSeen を立てて cancelUserTurnFallbackTimer を呼んでいる', () => {
    const idx = SRC.indexOf("} else if (type === 'response.output_item.done' && msg.item && msg.item.type === 'function_call') {");
    assert.notStrictEqual(idx, -1);
    const block = SRC.slice(idx, idx + 1400);
    assert.ok(block.includes('userTurnFallbackNormalCompletionSeen = true') && block.includes("cancelUserTurnFallbackTimer('function_call_started')"));
});

// ===== REGRESSION PROTECTION: 既存機構の呼び出し回数・独立性を再確認 =====

test('N: REGRESSION) 新機構は既存4つのForced Commit関数（NAME/SHORT_CHOICE/PHONE/旧SHORT_ANSWER）を一切呼び出さない（流用・改造していない）', () => {
    for (const fnSrc of [FN.armUserTurnFallbackTimer, FN.cancelUserTurnFallbackTimer, FN.maybeSendUserTurnFallbackCommit]) {
        assert.ok(!fnSrc.includes('maybeSendNameCommitPoc('));
        assert.ok(!fnSrc.includes('maybeSendShortAnswerCommit('));
        assert.ok(!fnSrc.includes('maybeSendPhoneCommit('));
        assert.ok(!fnSrc.includes('maybeSendQuickAnswerCommit('));
        assert.ok(!fnSrc.includes('armQuickAnswerFinalizeTimer('));
    }
});

test('O: REGRESSION) dc.send(JSON.stringify(...)) の呼び出し箇所は9箇所のまま（このフェーズで新規に追加したのはmaybeSendUserTurnFallbackCommitの1箇所のみ、という事実の再確認）', () => {
    const sendCount = (SRC.match(/dc\.send\(JSON\.stringify\(/g) || []).length;
    assert.strictEqual(sendCount, 9, 'baseline is 9: 8 pre-existing + 1 new (maybeSendUserTurnFallbackCommit). If this changes, re-audit which new call site was added.');
});

test('P: REGRESSION) VISIT_REASONは意図的にスコープ外のまま（O5.7 Auditの教訓により、3秒固定タイマーを長い自由回答へ適用しない）', () => {
    const sb = buildSandbox({ expectedAnswerType: 'VISIT_REASON' });
    vm.runInContext('armUserTurnFallbackTimer(callGeneration)', sb.ctx);
    assert.strictEqual(sb.timerCount(), 0, 'VISIT_REASON must never be armed by USER_TURN_3S_FALLBACK (long free-form answers must not be cut off)');
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
if (failed > 0) process.exit(1);
