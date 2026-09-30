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
// PHASE C 監査（ユーザー指示によるレビューで実施・設計を修正）:
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
// TASK6追加監査（実機不具合調査・今回）: 上記confirm window方式の「形」は
// 正しかったが、そもそもの土台にしていたwaiting/stalled DOMイベントが、
// ライブWebRTC MediaStream経由の<audio>要素では実質的に発火しないことが
// 実機ログで判明した（readyState=4, streamActive=true,
// audioTrackReadyState=live のまま一度もwaiting/stalledが発火せず、必ず
// PLAYBACK_AWARE_MAX_WAIT_MSのfail-safeへ落ちていた）。トラック自体は
// 生きたまま無音を送り続けるため、ブラウザ側のバッファアンダーラン検知が
// 働かないためと考えられる。OpenAI Realtime APIにも「クライアント側再生
// 完了」を示す公式イベントは存在しない（output_audio_buffer.*はサーバー側
// 送信完了のみを示し、WebRTC/SIP専用でWebSocketでは送られない）。
// そのため、candidate→confirm window→resumeで打ち消し、という判定の
// 「形」自体は維持しつつ、判定の元となる信号だけを、不発火なDOMイベント
// から、既にこのコードベースに存在し実測に基づく信号である aiSpeakingNow
// （setupLatencyMeter()内のAnalyserNodeで、AI自身の音声ストリームのみを
// 毎フレーム計測している既存シグナル。FAST TURN HOTFIX 2でAI SPEAKING
// PROTECTIONの契機として既に実績あり）のポーリングに差し替えた。
// このファイルは、この信号源の置き換え後も「形」（candidate→confirm
// window→resumeで打ち消し、fail-safeで必ず打ち切り）が保たれていること
// を検証する。
//
// 修正後の設計（このファイルが検証する契約）: aiSpeakingNow=falseは「settle
// 候補」としてのみ扱う。候補検知後、PLAYBACK_AWARE_SETTLE_CONFIRM_MSの
// 確認ウィンドウ内にaiSpeakingNowが一切trueに戻らなければ、初めて真に
// settleしたとみなす。trueに戻った場合は候補を破棄し、監視状態へ戻る
// （何度でも繰り返せる）。全体はPLAYBACK_AWARE_MAX_WAIT_MSのfail-safeで
// 独立して打ち切られるため、無限待機にはならない。settle確定（または
// fail-safe timeout）後、PLAYBACK_AWARE_SETTLE_MARGIN_MSの小さな安全
// マージンを置いてから、既存と全く同じendCall(...)を呼ぶ。
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
//
// TASK6により、settle判定の信号源がremoteAudioElのDOMイベント
// （waiting/stalled/playing）から、既存の aiSpeakingNow 変数のポーリング
// （setInterval）に置き換わった。そのため、このハーネスも
// setInterval/clearInterval の疑似実装と、aiSpeakingNowを直接書き換えて
// ポーリングtickを1回分だけ進める setAiSpeaking() ヘルパーを提供する
// （el.fire('waiting')相当の役割）。remoteAudioEl自体（makeMockAudioEl）は
// 「waiting/stalled/playingへのリスナー登録がもう一切発生しないこと」の
// 回帰確認用として引き続き使う。
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
    const scheduled = []; // { fn, ms, fired } -- setTimeout
    const intervals = []; // { fn, ms, cleared } -- setInterval
    const context = {
        pendingCallbackTerminalHangup: false,
        callbackFinalTailGraceTimerId: null,
        CALLBACK_FINAL_AUDIO_TAIL_GRACE_MS: 1500,
        callbackPlaybackAwareWaitTimerId: null,
        callbackPlaybackAwareListenersCleanup: null,
        PLAYBACK_AWARE_MAX_WAIT_MS: 2500,
        PLAYBACK_AWARE_SETTLE_MARGIN_MS: 300,
        PLAYBACK_AWARE_SETTLE_CONFIRM_MS: 400,
        PLAYBACK_AWARE_POLL_INTERVAL_MS: 100,
        remoteAudioEl: null,
        // TASK6追加: settle判定の信号源。デフォルトはfalse（無音）だが、
        // ほとんどのテストは「まだAIが発話中」の状態から始めたいため
        // overridesでaiSpeakingNow: trueを明示的に渡す。
        aiSpeakingNow: false,
        // TASK6追加（安全性監査により追加）: aiSpeakingNowの計測基盤
        // （setupLatencyMeter()内のAnalyserNode）が実際に初期化済みかどうかの
        // readinessシグナル。デフォルトは初期化済み（truthy）。nullを渡すと、
        // 「aiSpeakingNowの値自体が信頼できない」状態を模擬でき、その場合は
        // settle候補化を一切行わずfail-safeのみに委ねることを検証できる。
        remoteAudioCtx: {},
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
        setInterval: (fn, ms) => { const id = intervals.length; intervals.push({ fn, ms, cleared: false }); return id; },
        clearInterval: (id) => { if (typeof id === 'number' && intervals[id]) intervals[id].cleared = true; },
        isStaleCallEvent: () => !!(overrides && overrides.__stale),
        endCall: (text, reason) => endCallCalls.push({ text, reason }),
    };
    Object.assign(context, overrides || {});
    vm.createContext(context);
    vm.runInContext(MAYBE_HANGUP_CALLBACK_TERMINAL_FN, context);
    vm.runInContext(WAIT_FOR_PLAYBACK_SETTLE_FN, context);
    vm.runInContext(CANCEL_PLAYBACK_AWARE_WAIT_FN, context);
    return { context, events, consoleLogs, endCallCalls, scheduled, intervals };
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

// TASK6追加: アクティブな（clearされていない）全setIntervalを1回だけ
// 評価する。実際のsetIntervalがPLAYBACK_AWARE_POLL_INTERVAL_MSごとに
// 自動的に呼ばれるのと同じ挙動を、テストの中で明示的に1tick分だけ
// 進めるためのヘルパー。
function tickPoll(built) {
    built.intervals.forEach((iv) => { if (!iv.cleared) iv.fn(); });
}

// TASK6追加: aiSpeakingNowを書き換えてから1tick分ポーリングを進める。
// 旧実装のel.fire('waiting')/el.fire('playing')に相当する、このテスト
// ファイルの主要な操作ヘルパー。
function setAiSpeaking(built, value) {
    built.context.aiSpeakingNow = value;
    tickPoll(built);
}

// ============================================================
// (1) callback terminal final audio enters the playback-aware teardown path
// ============================================================

test('(1) confirmed callback terminalのtail grace完了後、waitForPlaybackSettleThenEndCall()に到達し、aiSpeakingNowをポーリングするsetIntervalが1個だけ登録される（waiting/stalled/playing DOMイベントへのリスナー登録はTASK6監査によりもはや一切発生しない）', () => {
    const el = makeMockAudioEl();
    const built = buildContext({ pendingCallbackTerminalHangup: true, remoteAudioEl: el, aiSpeakingNow: true });
    vm.runInContext('maybeHangUpAfterCallbackTerminal', built.context)(1, 'ai_audio_stopped');
    assert.strictEqual(built.scheduled.length, 1, 'tail grace timer must be scheduled first');
    fireNextScheduled(built); // tail grace (1500ms) 発火 → playback-aware wait開始
    assert.ok(built.consoleLogs.some((l) => l.indexOf('[CALLBACK_DIAG_PLAYBACK_WAIT_START]') === 0),
        'playback-aware wait開始の診断ログが出ること');
    const activeIntervals = built.intervals.filter((iv) => !iv.cleared);
    assert.strictEqual(activeIntervals.length, 1, 'aiSpeakingNowをポーリングするsetIntervalが1個だけ登録される');
    assert.strictEqual(activeIntervals[0].ms, 100, 'ポーリング間隔はPLAYBACK_AWARE_POLL_INTERVAL_MS(=100)であること');
    assert.strictEqual(el.totalListenerCount(), 0, 'remoteAudioElへのwaiting/stalled/playing DOMイベントリスナー登録はもう一切発生しない');
    assert.strictEqual(built.endCallCalls.length, 0, 'aiSpeakingNow=trueの間はsettle/timeoutが起きるまでendCall()はまだ呼ばれない');
});

// ============================================================
// (2) normal response does not enter the playback-aware teardown path
// ============================================================

test('(2) callback terminal未確定（pendingCallbackTerminalHangup=false = 通常応答）ではmaybeHangUpAfterCallbackTerminal自体が即return し、tail grace/playback-aware待機のいずれも一切開始されない', () => {
    const el = makeMockAudioEl();
    const built = buildContext({ pendingCallbackTerminalHangup: false, remoteAudioEl: el });
    vm.runInContext('maybeHangUpAfterCallbackTerminal', built.context)(1, 'ai_audio_stopped');
    assert.strictEqual(built.scheduled.length, 0, '通常応答ではタイマーが一切スケジュールされない');
    assert.strictEqual(built.intervals.length, 0, '通常応答ではポーリング用setIntervalも一切開始されない');
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

test('(4) endCall()は、aiSpeakingNow=false（候補検知）→PLAYBACK_AWARE_SETTLE_CONFIRM_MSの確認ウィンドウ経過（再びtrueに戻らなかった場合のみ）→PLAYBACK_AWARE_SETTLE_MARGIN_MSの安全マージン、の3段階すべてを経てからのみ呼ばれる', () => {
    const el = makeMockAudioEl();
    const built = buildContext({ pendingCallbackTerminalHangup: true, remoteAudioEl: el, aiSpeakingNow: true });
    vm.runInContext('maybeHangUpAfterCallbackTerminal', built.context)(1, 'ai_audio_stopped');
    fireNextScheduled(built); // tail grace 完了 → playback-aware wait開始（fail-safeタイマー起動）
    assert.strictEqual(built.endCallCalls.length, 0);

    setAiSpeaking(built, false); // AIの実測音声が無音になったことをポーリングが検知（settle候補化）
    assert.strictEqual(built.endCallCalls.length, 0, '候補検知直後は確認ウィンドウが挟まるためまだendCall()しない');
    assert.ok(!built.consoleLogs.some((l) => l.indexOf('[CALLBACK_DIAG_PLAYBACK_SETTLED]') === 0), 'settle確定前にSETTLEDログは出ない');
    assert.ok(built.consoleLogs.some((l) => l.indexOf('[CALLBACK_DIAG_PLAYBACK_CANDIDATE]') === 0 && l.indexOf('via=ai_speaking_now_false') !== -1));

    // 確認ウィンドウ（PLAYBACK_AWARE_SETTLE_CONFIRM_MS）を経過させる（aiSpeakingNowはfalseのまま）
    const confirmFired = fireNextScheduled(built);
    assert.ok(confirmFired, '確認ウィンドウ用のタイマーがスケジュールされているはず');
    assert.strictEqual(built.endCallCalls.length, 0, '確認ウィンドウ経過直後はsettle margin待ちのためまだendCall()しない');
    assert.ok(built.consoleLogs.some((l) => l.indexOf('[CALLBACK_DIAG_PLAYBACK_SETTLED]') === 0 && l.indexOf('via=ai_speaking_now_false') !== -1));

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

test('(5) aiSpeakingNowが一度もfalseにならなくても（＝AI音声が鳴り続けている、またはこのwaitの間ポーリングで無音を検知できなかった想定）、PLAYBACK_AWARE_MAX_WAIT_MSのfail-safeタイマーにより必ずendCall()へ到達する（無期限待機の禁止）', () => {
    const el = makeMockAudioEl();
    const built = buildContext({ pendingCallbackTerminalHangup: true, remoteAudioEl: el, aiSpeakingNow: true });
    vm.runInContext('maybeHangUpAfterCallbackTerminal', built.context)(1, 'ai_audio_stopped');
    fireNextScheduled(built); // tail grace 完了
    assert.strictEqual(built.scheduled.filter((s) => !s.fired).length, 1, 'fail-safeタイマーが1つだけ待機中のはず（aiSpeakingNowが一度もfalseにならないので確認タイマーは存在しない）');

    // aiSpeakingNowは一切falseにせず（tickPollもしない）、fail-safeタイマーのみを発火する。
    fireNextScheduled(built);
    assert.ok(built.consoleLogs.some((l) => l.indexOf('[CALLBACK_DIAG_PLAYBACK_TIMEOUT]') === 0), 'timeout診断ログが出ること');
    // settle marginタイマーが積まれ、それを発火させて初めてendCall()。
    fireNextScheduled(built);
    assert.strictEqual(built.endCallCalls.length, 1, 'fail-safe timeout経由でも最終的に必ずendCall()される');
});

// ============================================================
// (6) no double endCall
// ============================================================

test('(6) settle確定経路とfail-safeタイムアウトが競合しても、endCall()は1回だけ呼ばれる（settle確定時にcancelCallbackPlaybackAwareWaitがfail-safeタイマー/ポーリング用setIntervalを破棄するため、後発のtimeoutは無効化される）', () => {
    const el = makeMockAudioEl();
    const built = buildContext({ pendingCallbackTerminalHangup: true, remoteAudioEl: el, aiSpeakingNow: true });
    vm.runInContext('maybeHangUpAfterCallbackTerminal', built.context)(1, 'ai_audio_stopped');
    fireNextScheduled(built); // tail grace 完了 → playback-aware wait開始（fail-safeタイマー起動）

    setAiSpeaking(built, false); // settle候補検知
    fireNextScheduled(built); // 確認ウィンドウ経過 → settle確定 → settle marginタイマー起動（この時点でfail-safeは破棄済み）
    fireNextScheduled(built); // settle marginタイマー発火 → endCall()
    assert.strictEqual(built.endCallCalls.length, 1);

    // settle確定時点でfail-safeタイマーは既にclearTimeoutされている
    // （cancelCallbackPlaybackAwareWait経由）ため、残っている未発火タイマーは
    // 存在しないはず（二重発火の余地が無いことの確認）。
    const remainingUnfired = built.scheduled.filter((s) => !s.fired);
    assert.strictEqual(remainingUnfired.length, 0, 'settle成立後、fail-safeタイマーは残っていない（clearTimeoutで無効化済み）');
    assert.strictEqual(built.endCallCalls.length, 1, 'endCall()は依然として1回のみ');
    const remainingActiveIntervals = built.intervals.filter((iv) => !iv.cleared);
    assert.strictEqual(remainingActiveIntervals.length, 0, 'settle成立後、ポーリング用setIntervalも残っていない（clearIntervalで無効化済み）');
});

// ============================================================
// (7) no double timer
// ============================================================

test('(7) waitForPlaybackSettleThenEndCall()の1回の実行につき、fail-safeタイマー・ポーリング用setIntervalはいずれも常に1個だけスケジュールされる（再入時は既存分を先に破棄してから新規に1個だけ張り直す）', () => {
    const el = makeMockAudioEl();
    const built = buildContext({ pendingCallbackTerminalHangup: true, remoteAudioEl: el, aiSpeakingNow: true });
    vm.runInContext('maybeHangUpAfterCallbackTerminal', built.context)(1, 'ai_audio_stopped');
    fireNextScheduled(built); // tail grace 完了 → playback-aware wait開始（fail-safeタイマー1個目）
    assert.strictEqual(built.scheduled.filter((s) => !s.fired).length, 1);

    // 想定外の再入（本来起きないが、防御的な再入防止ロジックを直接検証する）。
    vm.runInContext('waitForPlaybackSettleThenEndCall', built.context)(1, 'ai_audio_stopped');
    const unfiredAfterReentry = built.scheduled.filter((s) => !s.fired);
    assert.strictEqual(unfiredAfterReentry.length, 1, '再入後も未発火タイマーは常に1個のみ（重複タイマーが放置されない）');

    const activeIntervalsAfterReentry = built.intervals.filter((iv) => !iv.cleared);
    assert.strictEqual(activeIntervalsAfterReentry.length, 1, '再入後もアクティブなポーリング用setIntervalは常に1個のみ（TASK6で追加した観点）');
});

// ============================================================
// (8) existing terminal-invalidation conditions (new response / unexpected audio) not broken
// ============================================================

test('(8) grace中の想定外response/audio診断フラグ（callbackDiagUnexpectedResponseDuringGrace/callbackDiagUnexpectedAudioDuringGrace）は読み取り専用のまま維持され、trueであってもplayback-aware待機・endCall()の制御フロー自体は変更しない（HOTFIX16〜18のPhase B診断契約を壊さない）', () => {
    const el = makeMockAudioEl();
    const built = buildContext({ pendingCallbackTerminalHangup: true, remoteAudioEl: el, aiSpeakingNow: true });
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
    setAiSpeaking(built, false);
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

test('(9)(10) Phase Cはwaitforplayback/cancelplaybackaware関連コードにsendResponseCreate(/dc.send(を一切追加していない（新しいresponse.create・data-channel送信経路をゼロ追加。TASK6のaiSpeakingNowポーリング化も同様）', () => {
    for (const token of ['sendResponseCreate(', 'dc.send(']) {
        assert.ok(!WAIT_FOR_PLAYBACK_SETTLE_FN.includes(token), 'waitForPlaybackSettleThenEndCall() must not call ' + token);
        assert.ok(!CANCEL_PLAYBACK_AWARE_WAIT_FN.includes(token), 'cancelCallbackPlaybackAwareWait() must not call ' + token);
    }
    // ファイル全体の出現数もHOTFIX17基準（25/10）から不変であることを再確認する
    // （詳細な内訳・コメントはtest_fast_turn_hotfix18/19側を参照）。
    const sendResponseCreateCount = (SRC.match(/sendResponseCreate\(/g) || []).length;
    const dcSendCount = (SRC.match(/dc\.send\(JSON\.stringify\(/g) || []).length;
    // 雑音誤検知対策 TASK D（STEP6）で意図的に+1（25→26。詳細は
    // tests/test_noisy_environment_turn_boundary.js参照）。TASK6（今回）は
    // response.create/dc.sendを一切追加していないため、この基準値は不変。
    assert.strictEqual(sendResponseCreateCount, 26, 'sendResponseCreate( occurrence count must be exactly +1 from the HOTFIX17 baseline of 25 (TASK D STEP6 noisy-environment goodbye only)');
    assert.strictEqual(dcSendCount, 10, 'dc.send(JSON.stringify( occurrence count must remain unchanged from the HOTFIX17 baseline');
});

// ============================================================
// (11) existing callback tool continuation not broken
// ============================================================

test('(11) request_callbackブランチ自体はPhase C・TASK6で一切変更されていない（HOTFIX16 テストLで確認済みの「この分岐自体はsendResponseCreate()を直接呼ばない＝tool結果送信は直後の共通コード経由」という既存契約を維持したまま、新しいplayback-aware関連関数への参照も一切追加していない）', () => {
    assert.ok(!REQUEST_CALLBACK_BRANCH_BODY.includes('sendResponseCreate('), 'request_callback branch itself must still NOT call sendResponseCreate() directly (existing HOTFIX16 test L invariant: tool-result continuation is sent by the shared post-branch code, not from inside this branch)');
    assert.ok(!REQUEST_CALLBACK_BRANCH_BODY.includes('waitForPlaybackSettleThenEndCall'), 'request_callback branch itself (tool result handling) must not reference the new playback-aware teardown — that only applies to the terminal closing response');
    assert.ok(!REQUEST_CALLBACK_BRANCH_BODY.includes('cancelCallbackPlaybackAwareWait'), 'request_callback branch itself must not reference the new cancel helper either');
});

// ============================================================
// (12) existing barge-in not broken
// ============================================================

test('(12) playback-aware teardown関連の新規コード（waitForPlaybackSettleThenEndCall/cancelCallbackPlaybackAwareWait）はbarge-in・マイクミュート機構（engageAiSpeakingProtection/track.enabled/userTurnFallback系）に一切触れていない（TASK6でaiSpeakingNow「変数」の読み取りのみを追加した後も同様）', () => {
    for (const token of ['engageAiSpeakingProtection', 'releaseAiSpeakingProtection', 'track.enabled', 'userTurnFallbackTimerId', 'armUserTurnFallbackTimer', 'cancelUserTurnFallbackTimer']) {
        assert.ok(!WAIT_FOR_PLAYBACK_SETTLE_FN.includes(token), 'waitForPlaybackSettleThenEndCall() must not reference ' + token);
        assert.ok(!CANCEL_PLAYBACK_AWARE_WAIT_FN.includes(token), 'cancelCallbackPlaybackAwareWait() must not reference ' + token);
    }
});

// ============================================================
// (13) existing 1500ms constant not changed
// ============================================================

test('(13) CALLBACK_FINAL_AUDIO_TAIL_GRACE_MSは引き続き1500のまま（Phase C・TASK6はこの既存baselineを単純延長せず、別フェーズ/別信号源として追加・置換した。「推測でtimeoutだけ延ばす」対応はしていない）', () => {
    assert.ok(SRC.includes('const CALLBACK_FINAL_AUDIO_TAIL_GRACE_MS = 1500;'), 'CALLBACK_FINAL_AUDIO_TAIL_GRACE_MS must remain exactly 1500ms, unchanged by Phase C / TASK6');
});

// ============================================================
// (14) Phase B diagnostic markers maintained
// ============================================================

test('(14) Phase Bの読み取り専用診断マーカー（[CALLBACK_DIAG_TERMINAL_TRANSCRIPT]/[CALLBACK_DIAG_AUDIO_STATE_AT_STOP]/[CALLBACK_DIAG_AUDIO_STATE_AT_TAIL_GRACE_START]/[CALLBACK_DIAG_AUDIO_STATE_BEFORE_END]）は、Phase C・TASK6での構造変更（endCall呼び出し箇所の移動、確認ウィンドウ追加、信号源のaiSpeakingNowポーリングへの置換）後も全て存在し続けている', () => {
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

test('(15) Phase C・Phase C監査・TASK6で追加した新規診断ログ（[CALLBACK_DIAG_PLAYBACK_WAIT_START]/[CALLBACK_DIAG_PLAYBACK_CANDIDATE]/[CALLBACK_DIAG_PLAYBACK_RESUME_DETECTED]/[CALLBACK_DIAG_PLAYBACK_SETTLED]/[CALLBACK_DIAG_PLAYBACK_TIMEOUT]）はいずれも固定文言・数値・enum・responseIdのみで、生transcript本文（diagTranscript等）や顧客の発話内容を一切含まない', () => {
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
//
// TASK6により信号源がaiSpeakingNowのポーリングへ置き換わったため、以下は
// すべてel.fire('waiting'/'playing')ではなくsetAiSpeaking(built, false/true)
// を使う形に更新した。検証している契約（「形」）自体は元のPhase C監査時から
// 変わっていない。
// ============================================================

test('(AUDIT-1) [危険シナリオの再現・修正確認] aiSpeakingNowがfalseになった直後（確認ウィンドウが経過するより前）に再びtrueへ戻った場合、endCall()は呼ばれず、監視状態に戻る（旧DOM版でも同じ危険シナリオを検証していた。TASK6のaiSpeakingNow版でも同じ安全策が機能することを確認する）', () => {
    const el = makeMockAudioEl();
    const built = buildContext({ pendingCallbackTerminalHangup: true, remoteAudioEl: el, aiSpeakingNow: true });
    vm.runInContext('maybeHangUpAfterCallbackTerminal', built.context)(1, 'ai_audio_stopped');
    fireNextScheduled(built); // tail grace 完了 → playback-aware wait開始

    // 1回目: 瞬間的にaiSpeakingNow=falseへ振れる（一時的な無音・ジッタ等を模擬）。
    setAiSpeaking(built, false);
    assert.ok(built.consoleLogs.some((l) => l.indexOf('[CALLBACK_DIAG_PLAYBACK_CANDIDATE]') === 0));
    assert.strictEqual(built.endCallCalls.length, 0);

    // 確認ウィンドウ（PLAYBACK_AWARE_SETTLE_CONFIRM_MS）が経過する"前"に、
    // AIの発話が実際には再開した（=aiSpeakingNowが再びtrueに戻った）と仮定する。
    setAiSpeaking(built, true);
    assert.ok(built.consoleLogs.some((l) => l.indexOf('[CALLBACK_DIAG_PLAYBACK_RESUME_DETECTED]') === 0),
        '再開検知の診断ログが出ること（次回実機テストでのH2再検証に必須）');
    assert.strictEqual(built.endCallCalls.length, 0, '[最重要] 一時的な無音からの再開中にendCall()してはならない（HOTFIX18のroot causeの再発）');

    // この時点でconfirmタイマーは既にキャンセル済み。残っているのは
    // まだ独立して継続中のfail-safeタイマーのみのはず（監視は継続する）。
    const unfiredAfterResume = built.scheduled.filter((s) => !s.fired);
    assert.strictEqual(unfiredAfterResume.length, 1, 'キャンセルされたconfirmタイマーの残骸が残ってはならない（no double timer）');
    const activeIntervalsAfterResume = built.intervals.filter((iv) => !iv.cleared);
    assert.strictEqual(activeIntervalsAfterResume.length, 1, '再開検知後もポーリング自体（setInterval）は継続している（何度でも再候補化できる）');
});

test('(AUDIT-2) [再監視の継続] AUDIT-1の後、実際に発話が終わった（2回目のaiSpeakingNow=falseの後、確認ウィンドウ内にtrueへ一切戻らない）場合には、通常どおりsettleが確定しendCall()へ到達する', () => {
    const el = makeMockAudioEl();
    const built = buildContext({ pendingCallbackTerminalHangup: true, remoteAudioEl: el, aiSpeakingNow: true });
    vm.runInContext('maybeHangUpAfterCallbackTerminal', built.context)(1, 'ai_audio_stopped');
    fireNextScheduled(built); // tail grace 完了 → playback-aware wait開始

    // 1回目: 一時的な無音→即再開（AUDIT-1と同じ）。
    setAiSpeaking(built, false);
    setAiSpeaking(built, true);
    assert.strictEqual(built.endCallCalls.length, 0);

    // 2回目: 今度こそ本当に発話が終わった（以後trueには戻らない）。
    setAiSpeaking(built, false);
    assert.strictEqual(built.endCallCalls.length, 0, '2回目の候補もまず確認ウィンドウを経なければならない');

    fireNextScheduled(built); // 2回目の確認ウィンドウ経過 → settle確定
    assert.strictEqual(built.endCallCalls.length, 0, 'settle確定後もsettle marginを経るまではendCall()しない');
    fireNextScheduled(built); // settle margin経過 → endCall()
    assert.strictEqual(built.endCallCalls.length, 1, '再開が無かった候補は最終的に必ずendCall()へ到達する');
});

test('(AUDIT-3) [DOMイベント監視の廃止を確認] waitForPlaybackSettleThenEndCall()のソースはもはやwaiting/stalled/playing/suspendのいずれのDOMイベントも一切リッスンしておらず、代わりに既存のaiSpeakingNow信号をsetIntervalでポーリングしている（TASK6実機不具合調査により、ライブWebRTC MediaStreamで実質発火しないDOMイベントベースの判定を全廃したため）', () => {
    for (const token of ["addEventListener('waiting'", "addEventListener('stalled'", "addEventListener('playing'", "addEventListener('suspend'"]) {
        assert.ok(!WAIT_FOR_PLAYBACK_SETTLE_FN.includes(token), 'waitForPlaybackSettleThenEndCall() must no longer listen for: ' + token);
    }
    assert.ok(WAIT_FOR_PLAYBACK_SETTLE_FN.includes('aiSpeakingNow'), 'waitForPlaybackSettleThenEndCall() must instead poll the existing aiSpeakingNow signal');
    assert.ok(WAIT_FOR_PLAYBACK_SETTLE_FN.includes('setInterval('), 'settle判定はsetIntervalによるポーリングで行われる');
});

test('(AUDIT-4) [繰り返しでも無限待機しない] 複数回のaiSpeakingNow false→trueサイクル（無音→再開の繰り返し）が起きても、全体はPLAYBACK_AWARE_MAX_WAIT_MSのfail-safeで独立して打ち切られ続ける（confirm windowの繰り返しがfail-safeの発火自体を遅延・無効化しない）', () => {
    const el = makeMockAudioEl();
    const built = buildContext({ pendingCallbackTerminalHangup: true, remoteAudioEl: el, aiSpeakingNow: true });
    vm.runInContext('maybeHangUpAfterCallbackTerminal', built.context)(1, 'ai_audio_stopped');
    fireNextScheduled(built); // tail grace 完了 → fail-safeタイマー起動（この時点でscheduled配列中、fail-safeが確定的にidx1に存在する）
    const failSafeIndex = built.scheduled.findIndex((s) => !s.fired);
    assert.notStrictEqual(failSafeIndex, -1);

    // 何度もstarvation/resumeサイクル（aiSpeakingNowのfalse→trueの往復）を起こす。
    for (let i = 0; i < 3; i++) {
        setAiSpeaking(built, false);
        setAiSpeaking(built, true);
    }
    assert.strictEqual(built.endCallCalls.length, 0, '繰り返しの無音→再開だけではendCall()に到達しない');
    // fail-safeタイマー自体はこの間ずっと未発火のまま残っているはず
    // （confirm windowの繰り返しによってキャンセルされたり延長されたりしない）。
    assert.strictEqual(built.scheduled[failSafeIndex].fired, false, 'fail-safeタイマーはstarvation/resumeサイクルの影響を受けず独立して継続する');

    // 最終的にfail-safeタイマー自体を発火させれば、確実にendCall()へ到達する。
    fireNextScheduled(built); // 直近の未発火＝実際にはfail-safeが唯一残っているはず
    fireNextScheduled(built); // settle margin
    assert.strictEqual(built.endCallCalls.length, 1, '繰り返しのstarvation/resumeの後でも、fail-safeにより必ずendCall()へ到達する');
});

test('(AUDIT-5) [安全性監査で追加] aiSpeakingNowの計測基盤（setupLatencyMeter()のAnalyserNode）が未初期化/既に破棄済み（remoteAudioCtx=null）の場合、aiSpeakingNow=falseであってもsettle候補化しない（信頼できない信号で早合点してendCall()し、発話の途中を切ってしまうことを防ぐ）。この場合もPLAYBACK_AWARE_MAX_WAIT_MSのfail-safeは独立して機能し、無限待機にはならない', () => {
    const el = makeMockAudioEl();
    const built = buildContext({
        pendingCallbackTerminalHangup: true,
        remoteAudioEl: el,
        aiSpeakingNow: false, // 実際には未初期化なのでfalseのまま（信頼できない値）
        remoteAudioCtx: null, // 計測基盤が無い状態を模擬
    });
    vm.runInContext('maybeHangUpAfterCallbackTerminal', built.context)(1, 'ai_audio_stopped');
    fireNextScheduled(built); // tail grace 完了 → playback-aware wait開始（初回同期評価はremoteAudioCtx=nullによりno-opのはず）

    assert.ok(!built.consoleLogs.some((l) => l.indexOf('[CALLBACK_DIAG_PLAYBACK_CANDIDATE]') === 0),
        'remoteAudioCtxが無い間はaiSpeakingNow=falseでもsettle候補化されない（CANDIDATEログが出ない）');
    assert.strictEqual(built.endCallCalls.length, 0);

    // ポーリングを明示的に何度か進めても（tickPoll相当）、依然として候補化されない。
    tickPoll(built);
    tickPoll(built);
    assert.ok(!built.consoleLogs.some((l) => l.indexOf('[CALLBACK_DIAG_PLAYBACK_CANDIDATE]') === 0));
    assert.strictEqual(built.endCallCalls.length, 0, 'remoteAudioCtxが無い間は誤って音声途中でendCall()されない');

    // それでも、fail-safeだけは独立して機能し、必ずendCall()へ到達する（無限待機の禁止）。
    fireNextScheduled(built); // fail-safe timeout
    assert.ok(built.consoleLogs.some((l) => l.indexOf('[CALLBACK_DIAG_PLAYBACK_TIMEOUT]') === 0));
    fireNextScheduled(built); // settle margin
    assert.strictEqual(built.endCallCalls.length, 1, 'remoteAudioCtxが無くても、最終的にはfail-safeにより必ずendCall()される');
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

test('REGRESSION) HOTFIX18は完全green（会話品質改善フェーズ2026年9月更新: §22/23のrealtime_voice_ai.py diff禁止アサーションはHOTFIX18固有のスコープ監査であり、以後の正当なフェーズによる同ファイルへの変更を妨げないよう恒久ガードから解除済み。詳細はtest_fast_turn_hotfix18_callback_audio_tail.jsのMEASURE2コメント参照）', () => {
    const r = runNodeTest('test_fast_turn_hotfix18_callback_audio_tail.js');
    assert.strictEqual(r.status, 0, 'HOTFIX18 must be fully green now that the retired §22/23 scope-guard no longer fails:\n' + r.stdout + r.stderr);
    const failCount = (r.stdout.match(/^  FAIL - /gm) || []).length;
    assert.strictEqual(failCount, 0, 'no failures expected, found ' + failCount);
});

test('REGRESSION) phase_mini1は完全green（会話品質改善フェーズ2026年9月更新: A1が子プロセスで実行するHOTFIX18が完全greenになったためカスケードも解消）', () => {
    const r = runNodeTest('test_phase_mini1_callback_diag_instrumentation.js');
    assert.strictEqual(r.status, 0, 'phase_mini1 must be fully green now that the HOTFIX18 cascade is resolved:\n' + r.stdout + r.stderr);
    const failCount = (r.stdout.match(/^  FAIL - /gm) || []).length;
    assert.strictEqual(failCount, 0, 'no failures expected, found ' + failCount);
});

test('REGRESSION) noisy-environment / tool-continuation resilienceスイートはPhase C・TASK6のコード追加（realtime-voice-engine.jsへの追加分）後も完全green（固定オフセット窓の崩れが無いことの確認）', () => {
    for (const rel of ['test_noisy_environment_turn_boundary.js', 'test_tool_continuation_resilience.js']) {
        const r = runNodeTest(rel);
        assert.strictEqual(r.status, 0, rel + ' must pass:\n' + r.stdout + r.stderr);
    }
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
if (failed > 0) process.exit(1);
