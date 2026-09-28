'use strict';

// RECEPTRA — PHASE C（2026年9月、監査により確認ウィンドウ方式へ修正）
// 「Callback terminal audio の途中切断を修正する / Playback-aware teardown」
// の契約テスト。
//
// 背景（ユーザー指示§0〜§2より）: HOTFIX18のbounded tail grace（1500ms固定
// 待機）を実機投入した後も、callback terminal（担当者への折り返し確約）の
// 最終応答「担当者から折り返しお電話いたします。ありがとうございました。」
// が「担当者から折り返し……」の途中で切れる事例が実機で観測された。
// コンソール順序（[CALLBACK_FINAL_AUDIO_STOP_EVENT] →
// [CALLBACK_DIAG_TAIL_GRACE_START] → [CALLBACK_FINAL_TAIL_GRACE_STARTED] →
// [CALLBACK_FINAL_TAIL_GRACE_COMPLETED] → [CALLBACK_TERMINAL_END_CALL] →
// [CALLBACK_FINAL_END_CALL] → [CALLBACK_DIAG_BEFORE_END_CALL]）と
// unexpectedResponseDuringGrace=false / unexpectedAudioDuringGrace=false の
// 実測により、H1（grace中に想定外のresponse/audioが発生していた）は否定され、
// H2（output_audio_buffer.stopped ≠ remoteAudioElの実際の可聴再生完了）が
// 有力と判断された。
//
// PHASE Cの初版設計: 1500msのCALLBACK_FINAL_AUDIO_TAIL_GRACE_MS baselineは
// そのまま維持し、その直後に新しい「playback-aware」第2フェーズ
// （waitForPlaybackSettleThenEndCall）を追加。remoteAudioElにwaiting/
// stalled/suspendイベントリスナーを登録し、最初に発火した時点を即
// 「settle」とみなしてendCall()していた。
//
// PHASE C 監査（今回、ユーザー指示によるレビューで実施・設計を修正）:
// waiting/stalledはHTML Living Standard上「一時的にデータが枯渇した」
// ことを示すのみで、仕様文言自体が「ユーザーエージェントは近くデータが
// 利用可能になると想定している」という一時性・再開可能性を前提にしている。
// ライブMediaStreamでは、ネットワークジッタによる瞬間的なアンダーラン
// 直後に、既に転送中だった音声フレームが到着し再生が再開する
// （playingが再度発火する）ことが普通に起こり得る。初版実装は最初の
// waiting/stalled 1回だけで即settle確定していたため、この「アンダーラン
// →再開」の最中に誤ってendCall()してしまい、HOTFIX18が修正しようとした
// 「発話途中の強制切断」を別トリガーで再発させ得る欠陥があった。
// さらにsuspendは「先読み停止」を示す、プログレッシブダウンロードを
// 前提とした概念であり、先読みという概念自体が無いライブMediaStreamでは
// 再生開始直後（＝まだ発話中）に発火し得るため、根本的に不適切な
// シグナルと判断し、候補から完全に除外した。
//
// 修正後の設計（このファイルが検証する契約）: waiting/stalledは「settle
// 候補」としてのみ扱う。候補イベント発生後、PLAYBACK_AWARE_SETTLE_
// CONFIRM_MSの確認ウィンドウ内にplaying（再開）イベントが一切発火しなけ
// れば、初めて真にsettleしたとみなす。playingが発火した場合は候補を破棄
// し、監視状態へ戻る（何度でも繰り返せる）。全体はPLAYBACK_AWARE_
// MAX_WAIT_MSのfail-safeで独立して打ち切られるため、無限待機にはならない。
// settle確定（またはfail-safe timeout）後、PLAYBACK_AWARE_SETTLE_MARGIN_MS
// の小さな安全マージンを置いてから、既存と全く同じendCall(...)を呼ぶ。
//
// このファイルは、ユーザー指示（Phase C本編）で明示された15項目のテスト
// 要件（番号付きtest名）に加え、今回の監査で明示的に要求された
// starvation/resumeシナリオの回帰テスト（AUDIT-*）をカバーする。

const fs = require('fs');
const path = require('path');
const assert = require('assert');
const vm = require('vm');
const { spawnSync } = require('child_process');

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

function extractElseIfBody(src, marker) {
    const idx = src.indexOf(marker);
    if (idx === -1) throw new Error('marker not found: ' + marker);
    const braceStart = src.indexOf('{', idx);
    let depth = 0, i = braceStart, bodyStart = -1;
    for (; i < src.length; i++) {
        if (src[i] === '{') { depth++; if (bodyStart === -1) bodyStart = i + 1; }
        else if (src[i] === '}') { depth--; if (depth === 0) break; }
    }
    return src.slice(bodyStart, i);
}

const MAYBE_HANGUP_CALLBACK_TERMINAL_FN = extractFunctionSource(SRC, 'maybeHangUpAfterCallbackTerminal', false);
const WAIT_FOR_PLAYBACK_SETTLE_FN = extractFunctionSource(SRC, 'waitForPlaybackSettleThenEndCall', false);
const CANCEL_PLAYBACK_AWARE_WAIT_FN = extractFunctionSource(SRC, 'cancelCallbackPlaybackAwareWait', false);
const REQUEST_CALLBACK_BRANCH_BODY = extractElseIfBody(SRC, "item.name === 'request_callback'");

let passed = 0, failed = 0;
function test(name, fn) {
    try {
        fn();
        passed++;
        console.log('  ok - ' + name);
    } catch (e) {
        failed++;
        console.log('  FAIL - ' + name);
        console.log('    ' + (e && e.stack ? e.stack.split('\n').slice(0, 8).join('\n    ') : e));
    }
}

function runNodeTest(relPath) {
    const result = spawnSync(process.execPath, [path.join(__dirname, relPath)], { encoding: 'utf8' });
    return { status: result.status, stdout: result.stdout || '', stderr: result.stderr || '' };
}

console.log('PHASE C — PLAYBACK-AWARE TEARDOWN (callback terminal final audio) contract tests');
console.log('source: ' + ENGINE_JS_PATH);
console.log('');

// ============================================================
// テストハーネス: maybeHangUpAfterCallbackTerminal /
// waitForPlaybackSettleThenEndCall / cancelCallbackPlaybackAwareWait を
// 実際にVMコンテキスト内で連結実行し、実機タイムラインを再現する。
// ============================================================

function makeMockAudioEl() {
    const listeners = {};
    return {
        addEventListener(type, fn) {
            (listeners[type] = listeners[type] || []).push(fn);
        },
        removeEventListener(type, fn) {
            if (!listeners[type]) return;
            listeners[type] = listeners[type].filter((l) => l !== fn);
        },
        listenerCount(type) {
            return (listeners[type] || []).length;
        },
        totalListenerCount() {
            return Object.keys(listeners).reduce((sum, k) => sum + listeners[k].length, 0);
        },
        fire(type) {
            (listeners[type] || []).slice().forEach((fn) => fn({ type }));
        },
    };
}

function buildContext(overrides) {
    const events = [];
    const consoleLogs = [];
    const endCallCalls = [];
    const scheduled = []; // { fn, ms, fired }
    const context = {
        pendingCallbackTerminalHangup: false,
        callbackFinalTailGraceTimerId: null,
        CALLBACK_FINAL_AUDIO_TAIL_GRACE_MS: 1500,
        callbackPlaybackAwareWaitTimerId: null,
        callbackPlaybackAwareListenersCleanup: null,
        PLAYBACK_AWARE_MAX_WAIT_MS: 2500,
        PLAYBACK_AWARE_SETTLE_MARGIN_MS: 300,
        PLAYBACK_AWARE_SETTLE_CONFIRM_MS: 400,
        remoteAudioEl: null,
        callbackDiagTerminalResponseId: null,
        callbackDiagCurrentResponseId: null,
        callbackDiagUnexpectedResponseDuringGrace: false,
        callbackDiagUnexpectedAudioDuringGrace: false,
        callbackDiagCaptureAudioState: () => 'audioElPresent=' + (overrides && overrides.remoteAudioEl ? 'true' : 'false'),
        responseState: 'done',
        aiAudioOutputActive: false,
        performance: { now: () => Date.now() },
        pushTimelineEvent: (t) => events.push(t),
        console: { log: (l) => consoleLogs.push(l) },
        setTimeout: (fn, ms) => { const id = scheduled.length; scheduled.push({ fn, ms, fired: false }); return id; },
        clearTimeout: (id) => { if (typeof id === 'number' && scheduled[id]) scheduled[id].fired = true; },
        isStaleCallEvent: () => !!(overrides && overrides.__stale),
        endCall: (text, reason) => endCallCalls.push({ text, reason }),
    };
    Object.assign(context, overrides || {});
    vm.createContext(context);
    vm.runInContext(MAYBE_HANGUP_CALLBACK_TERMINAL_FN, context);
    vm.runInContext(WAIT_FOR_PLAYBACK_SETTLE_FN, context);
    vm.runInContext(CANCEL_PLAYBACK_AWARE_WAIT_FN, context);
    return { context, events, consoleLogs, endCallCalls, scheduled };
}

// 未発火のタイマーのうち、最も新しく(最後に)スケジュールされたものを1つだけ
// 発火する。新設計ではfail-safeタイマー（wait開始時に張られる）とconfirm/
// margin タイマー（候補イベント発生のたびに新たに張られる）が同時に未発火の
// まま共存し得るため、単純な「配列先頭から最初の未発火」ではなく「直近で
// 新たに積まれたもの」を選ぶことで、各テストが意図した時間経過（＝直前の
// 操作が引き起こした次のタイマー）を1ステップずつ進められるようにする。
function fireNextScheduled(built) {
    for (let i = built.scheduled.length - 1; i >= 0; i--) {
        if (!built.scheduled[i].fired) {
            built.scheduled[i].fired = true;
            built.scheduled[i].fn();
            return true;
        }
    }
    return false;
}

function fireAllScheduled(built) {
    // 配列の末尾から先頭方向に繰り返し「最新の未発火」を発火し続ける
    // （fireNextScheduledと同じ選び方を、尽きるまで繰り返す）。
    while (fireNextScheduled(built)) { /* continue */ }
}

// ============================================================
// (1) callback terminal final audio enters the playback-aware teardown path
// ============================================================

test('(1) confirmed callback terminalのtail grace完了後、waitForPlaybackSettleThenEndCall()に到達し、remoteAudioElへwaiting/stalled（settle候補）とplaying（再開検知用）のリスナーが登録される（suspendは監査により候補から除外済み）', () => {
    const el = makeMockAudioEl();
    const built = buildContext({ pendingCallbackTerminalHangup: true, remoteAudioEl: el });
    vm.runInContext('maybeHangUpAfterCallbackTerminal', built.context)(1, 'ai_audio_stopped');
    assert.strictEqual(built.scheduled.length, 1, 'tail grace timer must be scheduled first');
    fireNextScheduled(built); // tail grace (1500ms) 発火 → playback-aware wait開始
    assert.ok(built.consoleLogs.some((l) => l.indexOf('[CALLBACK_DIAG_PLAYBACK_WAIT_START]') === 0),
        'playback-aware wait開始の診断ログが出ること');
    assert.strictEqual(el.listenerCount('waiting'), 1);
    assert.strictEqual(el.listenerCount('stalled'), 1);
    assert.strictEqual(el.listenerCount('playing'), 1, '再開検知用のplayingリスナーも登録される（監査で追加）');
    assert.strictEqual(el.listenerCount('suspend'), 0, 'suspendは監査によりsettle候補から除外され、もはやリスナー登録されない');
    assert.strictEqual(built.endCallCalls.length, 0, 'settle/timeoutが起きるまでendCall()はまだ呼ばれない');
});

// ============================================================
// (2) normal response does not enter the playback-aware teardown path
// ============================================================

test('(2) callback terminal未確定（pendingCallbackTerminalHangup=false = 通常応答）ではmaybeHangUpAfterCallbackTerminal自体が即return し、tail grace/playback-aware待機のいずれも一切開始されない', () => {
    const el = makeMockAudioEl();
    const built = buildContext({ pendingCallbackTerminalHangup: false, remoteAudioEl: el });
    vm.runInContext('maybeHangUpAfterCallbackTerminal', built.context)(1, 'ai_audio_stopped');
    assert.strictEqual(built.scheduled.length, 0, '通常応答ではタイマーが一切スケジュールされない');
    assert.strictEqual(el.totalListenerCount(), 0, '通常応答ではremoteAudioElへのリスナー登録も一切発生しない');
    assert.strictEqual(built.endCallCalls.length, 0);
});

// ============================================================
// (3) unconfirmed callback terminal does not enter the playback-aware teardown path
// ============================================================

test('(3) stale generation（この折り返し確定より後に次の通話が始まっている=未確定として扱われるべきケース）では、pendingフラグは消費されるがplayback-aware待機は開始されない', () => {
    const el = makeMockAudioEl();
    const built = buildContext({ pendingCallbackTerminalHangup: true, remoteAudioEl: el, __stale: true });
    vm.runInContext('maybeHangUpAfterCallbackTerminal', built.context)(1, 'ai_audio_stopped');
    assert.strictEqual(built.context.pendingCallbackTerminalHangup, false, 'フラグ自体は消費される（既存のone-shot設計を維持）');
    assert.strictEqual(built.scheduled.length, 0, 'stale世代ではtail graceタイマーすら開始されない');
    assert.strictEqual(el.totalListenerCount(), 0);
    assert.strictEqual(built.endCallCalls.length, 0);
});

// ============================================================
// (4) real endCall path is reached only after playback completion/settled determination
// ============================================================

test('(4) endCall()は、waiting/stalled候補イベント→PLAYBACK_AWARE_SETTLE_CONFIRM_MSの確認ウィンドウ経過（playingで再開されなかった場合のみ）→PLAYBACK_AWARE_SETTLE_MARGIN_MSの安全マージン、の3段階すべてを経てからのみ呼ばれる', () => {
    const el = makeMockAudioEl();
    const built = buildContext({ pendingCallbackTerminalHangup: true, remoteAudioEl: el });
    vm.runInContext('maybeHangUpAfterCallbackTerminal', built.context)(1, 'ai_audio_stopped');
    fireNextScheduled(built); // tail grace 完了 → playback-aware wait開始（fail-safeタイマー起動）
    assert.strictEqual(built.endCallCalls.length, 0);

    el.fire('waiting'); // ブラウザ側の候補イベント（一時的なアンダーラン)を模擬
    assert.strictEqual(built.endCallCalls.length, 0, '候補イベント発火直後は確認ウィンドウが挟まるためまだendCall()しない');
    assert.ok(!built.consoleLogs.some((l) => l.indexOf('[CALLBACK_DIAG_PLAYBACK_SETTLED]') === 0), 'settle確定前にSETTLEDログは出ない');
    assert.ok(built.consoleLogs.some((l) => l.indexOf('[CALLBACK_DIAG_PLAYBACK_CANDIDATE]') === 0 && l.indexOf('via=waiting') !== -1));

    // 確認ウィンドウ（PLAYBACK_AWARE_SETTLE_CONFIRM_MS）を経過させる（playingは発火させない）
    const confirmFired = fireNextScheduled(built);
    assert.ok(confirmFired, '確認ウィンドウ用のタイマーがスケジュールされているはず');
    assert.strictEqual(built.endCallCalls.length, 0, '確認ウィンドウ経過直後はsettle margin待ちのためまだendCall()しない');
    assert.ok(built.consoleLogs.some((l) => l.indexOf('[CALLBACK_DIAG_PLAYBACK_SETTLED]') === 0 && l.indexOf('via=waiting') !== -1));

    // settle margin タイマーを経過させる
    const marginFired = fireNextScheduled(built);
    assert.ok(marginFired, 'settle margin用の新しいタイマーがスケジュールされているはず');
    assert.strictEqual(built.endCallCalls.length, 1, 'settle margin経過後に初めてendCall()される');
    assert.strictEqual(built.endCallCalls[0].text, 'お電話ありがとうございました。');
    assert.strictEqual(built.endCallCalls[0].reason, 'callback_terminal');
});

// ============================================================
// (5) fail-safe timeout guarantees termination even if completion can't be detected
// ============================================================

test('(5) waiting/stalledのいずれも一切発火しなくても、PLAYBACK_AWARE_MAX_WAIT_MSのfail-safeタイマーにより必ずendCall()へ到達する（無期限待機の禁止）', () => {
    const el = makeMockAudioEl();
    const built = buildContext({ pendingCallbackTerminalHangup: true, remoteAudioEl: el });
    vm.runInContext('maybeHangUpAfterCallbackTerminal', built.context)(1, 'ai_audio_stopped');
    fireNextScheduled(built); // tail grace 完了
    assert.strictEqual(built.scheduled.filter((s) => !s.fired).length, 1, 'fail-safeタイマーが1つだけ待機中のはず（候補イベントが一切無いので確認タイマーは存在しない）');

    // イベントは一切発火させず、fail-safeタイマーのみを発火する。
    fireNextScheduled(built);
    assert.ok(built.consoleLogs.some((l) => l.indexOf('[CALLBACK_DIAG_PLAYBACK_TIMEOUT]') === 0), 'timeout診断ログが出ること');
    // settle marginタイマーが積まれ、それを発火させて初めてendCall()。
    fireNextScheduled(built);
    assert.strictEqual(built.endCallCalls.length, 1, 'fail-safe timeout経由でも最終的に必ずendCall()される');
});

// ============================================================
// (6) no double endCall
// ============================================================

test('(6) settle確定経路とfail-safeタイムアウトが競合しても、endCall()は1回だけ呼ばれる（settle確定時にcancelCallbackPlaybackAwareWaitがfail-safeタイマー/リスナーを破棄するため、後発のtimeoutは無効化される）', () => {
    const el = makeMockAudioEl();
    const built = buildContext({ pendingCallbackTerminalHangup: true, remoteAudioEl: el });
    vm.runInContext('maybeHangUpAfterCallbackTerminal', built.context)(1, 'ai_audio_stopped');
    fireNextScheduled(built); // tail grace 完了 → playback-aware wait開始（fail-safeタイマー起動）

    el.fire('waiting'); // 候補イベント
    fireNextScheduled(built); // 確認ウィンドウ経過 → settle確定 → settle marginタイマー起動（この時点でfail-safeは破棄済み）
    fireNextScheduled(built); // settle marginタイマー発火 → endCall()
    assert.strictEqual(built.endCallCalls.length, 1);

    // settle確定時点でfail-safeタイマーは既にclearTimeoutされている
    // （cancelCallbackPlaybackAwareWait経由）ため、残っている未発火タイマーは
    // 存在しないはず（二重発火の余地が無いことの確認）。
    const remainingUnfired = built.scheduled.filter((s) => !s.fired);
    assert.strictEqual(remainingUnfired.length, 0, 'settle成立後、fail-safeタイマーは残っていない（clearTimeoutで無効化済み）');
    assert.strictEqual(built.endCallCalls.length, 1, 'endCall()は依然として1回のみ');
});

// ============================================================
// (7) no double timer
// ============================================================

test('(7) waitForPlaybackSettleThenEndCall()の1回の実行につき、fail-safeタイマーは常に1個だけスケジュールされる（再入時は既存分を先に破棄してから新規に1個だけ張り直す）', () => {
    const el = makeMockAudioEl();
    const built = buildContext({ pendingCallbackTerminalHangup: true, remoteAudioEl: el });
    vm.runInContext('maybeHangUpAfterCallbackTerminal', built.context)(1, 'ai_audio_stopped');
    fireNextScheduled(built); // tail grace 完了 → playback-aware wait開始（fail-safeタイマー1個目）
    assert.strictEqual(built.scheduled.filter((s) => !s.fired).length, 1);

    // 想定外の再入（本来起きないが、防御的な再入防止ロジックを直接検証する）。
    vm.runInContext('waitForPlaybackSettleThenEndCall', built.context)(1, 'ai_audio_stopped');
    const unfiredAfterReentry = built.scheduled.filter((s) => !s.fired);
    assert.strictEqual(unfiredAfterReentry.length, 1, '再入後も未発火タイマーは常に1個のみ（重複タイマーが放置されない）');
});

// ============================================================
// (8) existing terminal-invalidation conditions (new response / unexpected audio) not broken
// ============================================================

test('(8) grace中の想定外response/audio診断フラグ（callbackDiagUnexpectedResponseDuringGrace/callbackDiagUnexpectedAudioDuringGrace）は読み取り専用のまま維持され、trueであってもplayback-aware待機・endCall()の制御フロー自体は変更しない（HOTFIX16〜18のPhase B診断契約を壊さない）', () => {
    const el = makeMockAudioEl();
    const built = buildContext({ pendingCallbackTerminalHangup: true, remoteAudioEl: el });
    vm.runInContext('maybeHangUpAfterCallbackTerminal', built.context)(1, 'ai_audio_stopped');
    // 注意: maybeHangUpAfterCallbackTerminal()自身がtail grace開始時点で
    // callbackDiagUnexpectedResponseDuringGrace/callbackDiagUnexpectedAudioDuringGrace
    // を毎回falseへリセットする（新しいgraceウィンドウの開始＝既存の
    // Phase B診断契約）ため、初期overridesではなく、tail grace発火後
    // （＝実際のresponse.created/output_audio_buffer.startedハンドラが
    // 想定外イベントを検知してこれらのフラグを立てるタイミングを模擬）に
    // trueへ書き換える。
    fireNextScheduled(built);
    built.context.callbackDiagUnexpectedResponseDuringGrace = true;
    built.context.callbackDiagUnexpectedAudioDuringGrace = true;
    el.fire('waiting');
    fireNextScheduled(built); // 確認ウィンドウ経過 → settle確定
    fireNextScheduled(built); // settle margin経過 → endCall()
    assert.strictEqual(built.endCallCalls.length, 1, '診断フラグがtrueでも最終的にendCall()へ到達する（診断は制御に影響しない）');
    assert.ok(built.consoleLogs.some((l) => l.indexOf('[CALLBACK_DIAG_BEFORE_END_CALL]') === 0 && l.indexOf('unexpectedResponseDuringGrace=true') !== -1 && l.indexOf('unexpectedAudioDuringGrace=true') !== -1),
        'BEFORE_END_CALLログにフラグの値がそのまま反映されること（観測用途のみで挙動は変えない）');
});

// ============================================================
// (9) sendResponseCreate() call count not increased
// (10) dc.send() call count not increased
// ============================================================

test('(9)(10) Phase Cはwaitforplayback/cancelplaybackaware関連コードにsendResponseCreate(/dc.send(を一切追加していない（新しいresponse.create・data-channel送信経路をゼロ追加）', () => {
    for (const token of ['sendResponseCreate(', 'dc.send(']) {
        assert.ok(!WAIT_FOR_PLAYBACK_SETTLE_FN.includes(token), 'waitForPlaybackSettleThenEndCall() must not call ' + token);
        assert.ok(!CANCEL_PLAYBACK_AWARE_WAIT_FN.includes(token), 'cancelCallbackPlaybackAwareWait() must not call ' + token);
    }
    // ファイル全体の出現数もHOTFIX17基準（25/10）から不変であることを再確認する
    // （詳細な内訳・コメントはtest_fast_turn_hotfix18/19側を参照）。
    const sendResponseCreateCount = (SRC.match(/sendResponseCreate\(/g) || []).length;
    const dcSendCount = (SRC.match(/dc\.send\(JSON\.stringify\(/g) || []).length;
    assert.strictEqual(sendResponseCreateCount, 25, 'sendResponseCreate( occurrence count must remain unchanged from the HOTFIX17 baseline');
    assert.strictEqual(dcSendCount, 10, 'dc.send(JSON.stringify( occurrence count must remain unchanged from the HOTFIX17 baseline');
});

// ============================================================
// (11) existing callback tool continuation not broken
// ============================================================

test('(11) request_callbackブランチ自体はPhase Cで一切変更されていない（HOTFIX16 テストLで確認済みの「この分岐自体はsendResponseCreate()を直接呼ばない＝tool結果送信は直後の共通コード経由」という既存契約を維持したまま、新しいplayback-aware関連関数への参照も一切追加していない）', () => {
    assert.ok(!REQUEST_CALLBACK_BRANCH_BODY.includes('sendResponseCreate('), 'request_callback branch itself must still NOT call sendResponseCreate() directly (existing HOTFIX16 test L invariant: tool-result continuation is sent by the shared post-branch code, not from inside this branch)');
    assert.ok(!REQUEST_CALLBACK_BRANCH_BODY.includes('waitForPlaybackSettleThenEndCall'), 'request_callback branch itself (tool result handling) must not reference the new playback-aware teardown — that only applies to the terminal closing response');
    assert.ok(!REQUEST_CALLBACK_BRANCH_BODY.includes('cancelCallbackPlaybackAwareWait'), 'request_callback branch itself must not reference the new cancel helper either');
});

// ============================================================
// (12) existing barge-in not broken
// ============================================================

test('(12) playback-aware teardown関連の新規コード（waitForPlaybackSettleThenEndCall/cancelCallbackPlaybackAwareWait）はbarge-in・マイクミュート機構（engageAiSpeakingProtection/track.enabled/userTurnFallback系）に一切触れていない', () => {
    for (const token of ['engageAiSpeakingProtection', 'releaseAiSpeakingProtection', 'track.enabled', 'userTurnFallbackTimerId', 'armUserTurnFallbackTimer', 'cancelUserTurnFallbackTimer']) {
        assert.ok(!WAIT_FOR_PLAYBACK_SETTLE_FN.includes(token), 'waitForPlaybackSettleThenEndCall() must not reference ' + token);
        assert.ok(!CANCEL_PLAYBACK_AWARE_WAIT_FN.includes(token), 'cancelCallbackPlaybackAwareWait() must not reference ' + token);
    }
});

// ============================================================
// (13) existing 1500ms constant not changed
// ============================================================

test('(13) CALLBACK_FINAL_AUDIO_TAIL_GRACE_MSは引き続き1500のまま（Phase Cはこの既存baselineを単純延長せず、別フェーズとして追加した。監査による確認ウィンドウの追加もこの値には一切触れていない）', () => {
    assert.ok(SRC.includes('const CALLBACK_FINAL_AUDIO_TAIL_GRACE_MS = 1500;'), 'CALLBACK_FINAL_AUDIO_TAIL_GRACE_MS must remain exactly 1500ms, unchanged by Phase C');
});

// ============================================================
// (14) Phase B diagnostic markers maintained
// ============================================================

test('(14) Phase Bの読み取り専用診断マーカー（[CALLBACK_DIAG_TERMINAL_TRANSCRIPT]/[CALLBACK_DIAG_AUDIO_STATE_AT_STOP]/[CALLBACK_DIAG_AUDIO_STATE_AT_TAIL_GRACE_START]/[CALLBACK_DIAG_AUDIO_STATE_BEFORE_END]）は、Phase Cでの構造変更（endCall呼び出し箇所の移動、および今回の監査による確認ウィンドウ追加）後も全て存在し続けている', () => {
    for (const marker of [
        '[CALLBACK_DIAG_TERMINAL_TRANSCRIPT]',
        '[CALLBACK_DIAG_AUDIO_STATE_AT_STOP]',
        '[CALLBACK_DIAG_AUDIO_STATE_AT_TAIL_GRACE_START]',
        '[CALLBACK_DIAG_AUDIO_STATE_BEFORE_END]',
    ]) {
        assert.ok(SRC.includes(marker), 'Phase B diagnostic marker must still exist: ' + marker);
    }
});

// ============================================================
// (15) raw transcript never logged to console
// ============================================================

test('(15) Phase C・Phase C監査で追加した新規診断ログ（[CALLBACK_DIAG_PLAYBACK_WAIT_START]/[CALLBACK_DIAG_PLAYBACK_CANDIDATE]/[CALLBACK_DIAG_PLAYBACK_RESUME_DETECTED]/[CALLBACK_DIAG_PLAYBACK_SETTLED]/[CALLBACK_DIAG_PLAYBACK_TIMEOUT]）はいずれも固定文言・数値・enum・responseIdのみで、生transcript本文（diagTranscript等）や顧客の発話内容を一切含まない', () => {
    for (const forbidden of ['diagTranscript', 'msg.transcript', 'customer_phone', 'customerPhone']) {
        assert.ok(!WAIT_FOR_PLAYBACK_SETTLE_FN.includes(forbidden), 'waitForPlaybackSettleThenEndCall() must not reference ' + forbidden);
        assert.ok(!MAYBE_HANGUP_CALLBACK_TERMINAL_FN.includes(forbidden), 'maybeHangUpAfterCallbackTerminal() must not reference ' + forbidden);
    }
    // 新規マーカーが実際にソース中に存在すること（無いものを「含まない」と
    // 主張しても無意味なため、存在確認もあわせて行う）。
    assert.ok(SRC.includes('[CALLBACK_DIAG_PLAYBACK_WAIT_START]'));
    assert.ok(SRC.includes('[CALLBACK_DIAG_PLAYBACK_CANDIDATE]'));
    assert.ok(SRC.includes('[CALLBACK_DIAG_PLAYBACK_RESUME_DETECTED]'));
    assert.ok(SRC.includes('[CALLBACK_DIAG_PLAYBACK_SETTLED]'));
    assert.ok(SRC.includes('[CALLBACK_DIAG_PLAYBACK_TIMEOUT]'));
});

// ============================================================
// AUDIT-1〜4: ユーザー指示によるPhase C監査で明示的に要求された
// starvation/resume（一時的なアンダーラン→再開）シナリオの回帰テスト。
// 「waiting/stalledの単発発火＝settle確定」という初版の誤った前提を
// 直接再現し、修正後の確認ウィンドウ方式がこれを正しく防ぐことを検証する。
// ============================================================

test('(AUDIT-1) [危険シナリオの再現・修正確認] waiting発火直後（確認ウィンドウが経過するより前）にplaying（再開）が発火した場合、endCall()は呼ばれず、監視状態に戻る（旧実装ならここで誤ってendCall()していた）', () => {
    const el = makeMockAudioEl();
    const built = buildContext({ pendingCallbackTerminalHangup: true, remoteAudioEl: el });
    vm.runInContext('maybeHangUpAfterCallbackTerminal', built.context)(1, 'ai_audio_stopped');
    fireNextScheduled(built); // tail grace 完了 → playback-aware wait開始

    // 1回目のwaiting: ネットワークジッタによる一時的なアンダーランを模擬。
    el.fire('waiting');
    assert.ok(built.consoleLogs.some((l) => l.indexOf('[CALLBACK_DIAG_PLAYBACK_CANDIDATE]') === 0));
    assert.strictEqual(built.endCallCalls.length, 0);

    // 確認ウィンドウ（PLAYBACK_AWARE_SETTLE_CONFIRM_MS）が経過する"前"に、
    // 既に転送中だった音声フレームが到着して再生が再開したと仮定する。
    el.fire('playing');
    assert.ok(built.consoleLogs.some((l) => l.indexOf('[CALLBACK_DIAG_PLAYBACK_RESUME_DETECTED]') === 0),
        '再開検知の診断ログが出ること（次回実機テストでのH2再検証に必須）');
    assert.strictEqual(built.endCallCalls.length, 0, '[最重要] 一時的なアンダーランからの再開中にendCall()してはならない（HOTFIX18のroot causeの再発）');

    // この時点でconfirmタイマーは既にキャンセル済み。残っているのは
    // まだ独立して継続中のfail-safeタイマーのみのはず（監視は継続する）。
    const unfiredAfterResume = built.scheduled.filter((s) => !s.fired);
    assert.strictEqual(unfiredAfterResume.length, 1, 'キャンセルされたconfirmタイマーの残骸が残ってはならない（no double timer）');
});

test('(AUDIT-2) [再監視の継続] AUDIT-1の後、実際に発話が終わった（2回目のwaitingの後、確認ウィンドウ内にplayingが一切来ない）場合には、通常どおりsettleが確定しendCall()へ到達する', () => {
    const el = makeMockAudioEl();
    const built = buildContext({ pendingCallbackTerminalHangup: true, remoteAudioEl: el });
    vm.runInContext('maybeHangUpAfterCallbackTerminal', built.context)(1, 'ai_audio_stopped');
    fireNextScheduled(built); // tail grace 完了 → playback-aware wait開始

    // 1回目: 一時的なアンダーラン→即再開（AUDIT-1と同じ）。
    el.fire('waiting');
    el.fire('playing');
    assert.strictEqual(built.endCallCalls.length, 0);

    // 2回目: 今度こそ本当に発話が終わった（以後playingは来ない）。
    el.fire('waiting');
    assert.strictEqual(built.endCallCalls.length, 0, '2回目の候補もまず確認ウィンドウを経なければならない');

    fireNextScheduled(built); // 2回目の確認ウィンドウ経過 → settle確定
    assert.strictEqual(built.endCallCalls.length, 0, 'settle確定後もsettle marginを経るまではendCall()しない');
    fireNextScheduled(built); // settle margin経過 → endCall()
    assert.strictEqual(built.endCallCalls.length, 1, '再開が無かった候補は最終的に必ずendCall()へ到達する');
});

test('(AUDIT-3) [suspendは候補から除外] waitForPlaybackSettleThenEndCall()のソースはもはやsuspendイベントを一切リッスンしていない（先読み停止を示すのみでライブMediaStreamの再生完了とは無関係というHTML仕様上の理由で、settleの根拠から完全に除外した）', () => {
    assert.ok(!WAIT_FOR_PLAYBACK_SETTLE_FN.includes("addEventListener('suspend'"), 'waitForPlaybackSettleThenEndCall() must no longer listen for suspend at all');
});

test('(AUDIT-4) [繰り返しでも無限待機しない] 複数回のstarvation/resumeサイクルが起きても、全体はPLAYBACK_AWARE_MAX_WAIT_MSのfail-safeで独立して打ち切られ続ける（confirm windowの繰り返しがfail-safeの発火自体を遅延・無効化しない）', () => {
    const el = makeMockAudioEl();
    const built = buildContext({ pendingCallbackTerminalHangup: true, remoteAudioEl: el });
    vm.runInContext('maybeHangUpAfterCallbackTerminal', built.context)(1, 'ai_audio_stopped');
    fireNextScheduled(built); // tail grace 完了 → fail-safeタイマー起動（この時点でscheduled配列中、fail-safeが確定的にidx1に存在する）
    const failSafeIndex = built.scheduled.findIndex((s) => !s.fired);
    assert.notStrictEqual(failSafeIndex, -1);

    // 何度もstarvation/resumeサイクルを起こす。
    for (let i = 0; i < 3; i++) {
        el.fire('waiting');
        el.fire('playing');
    }
    assert.strictEqual(built.endCallCalls.length, 0, '繰り返しのstarvation/resumeだけではendCall()に到達しない');
    // fail-safeタイマー自体はこの間ずっと未発火のまま残っているはず
    // （confirm windowの繰り返しによってキャンセルされたり延長されたりしない）。
    assert.strictEqual(built.scheduled[failSafeIndex].fired, false, 'fail-safeタイマーはstarvation/resumeサイクルの影響を受けず独立して継続する');

    // 最終的にfail-safeタイマー自体を発火させれば、確実にendCall()へ到達する。
    fireNextScheduled(built); // 直近の未発火＝最後のconfirmサイクルの残骸ではなく、実際にはfail-safeが唯一残っているはず
    fireNextScheduled(built); // settle margin
    assert.strictEqual(built.endCallCalls.length, 1, '繰り返しのstarvation/resumeの後でも、fail-safeにより必ずendCall()へ到達する');
});

// ============================================================
// REGRESSION: 関連する既存JSテストファイル群が全て通ること
// （HOTFIX18/phase_mini1は§22/23の既知failureのみ許容する）
// ============================================================

test('REGRESSION) HOTFIX14/15/16/17/19（callback terminal機構全体）が完全green', () => {
    for (const rel of [
        'test_fast_turn_hotfix14_greeting_callback_terminal.js',
        'test_fast_turn_hotfix15_callback_final_confirmation.js',
        'test_fast_turn_hotfix16_callback_single_terminal.js',
        'test_fast_turn_hotfix17_callback_no_reconfirm_zero_wait_audio.js',
        'test_fast_turn_hotfix19_callback_no_ack.js',
    ]) {
        const r = runNodeTest(rel);
        assert.strictEqual(r.status, 0, rel + ' must pass:\n' + r.stdout + r.stderr);
    }
});

test('REGRESSION) HOTFIX18は既知の§22/23（realtime_voice_ai.py diff禁止アサーション、Phase Aのprompt編集による意図的な既知failure）のみが残り、他は全green', () => {
    const r = runNodeTest('test_fast_turn_hotfix18_callback_audio_tail.js');
    assert.notStrictEqual(r.status, 0, 'HOTFIX18 is expected to have exactly the known §22/23 failure remaining');
    assert.ok(r.stdout.includes('FAIL - MEASURE2)'), 'the only failure must be MEASURE2 (§22/23 known scope-guard failure)');
    const failCount = (r.stdout.match(/^  FAIL - /gm) || []).length;
    assert.strictEqual(failCount, 1, 'exactly one known failure (MEASURE2) is expected, found ' + failCount);
});

test('REGRESSION) phase_mini1は既知の§22/23カスケード（A1がHOTFIX18を子プロセスで実行しているため）のみが残り、他は全green', () => {
    const r = runNodeTest('test_phase_mini1_callback_diag_instrumentation.js');
    assert.notStrictEqual(r.status, 0, 'phase_mini1 is expected to have exactly the known §22/23 cascade failure remaining (via A1)');
    assert.ok(r.stdout.includes('FAIL - A1)'), 'the only failure must be A1 (cascades from the known HOTFIX18 MEASURE2 failure)');
    const failCount = (r.stdout.match(/^  FAIL - /gm) || []).length;
    assert.strictEqual(failCount, 1, 'exactly one known cascade failure (A1) is expected, found ' + failCount);
});

test('REGRESSION) noisy-environment / tool-continuation resilienceスイートはPhase Cのコード追加（realtime-voice-engine.jsへの追加分）後も完全green（固定オフセット窓の崩れが無いことの確認）', () => {
    for (const rel of ['test_noisy_environment_turn_boundary.js', 'test_tool_continuation_resilience.js']) {
        const r = runNodeTest(rel);
        assert.strictEqual(r.status, 0, rel + ' must pass:\n' + r.stdout + r.stderr);
    }
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
if (failed > 0) process.exit(1);
