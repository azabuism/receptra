'use strict';

/**
 * RECEPTRA — SMART INTERRUPTION / NOISE RESILIENCE HOTFIX
 * 契約テスト（N1〜N9 / C1〜C9 / B1〜B4）
 *
 * 対象:
 *  - 局所音声レベル計測経路（AnalyserNodeベースのai_audio_level_silent）に
 *    追加したAI_SPEAKING_LEVEL_SILENCE_HOLD_MS（瞬間的な無音でのマイク
 *    ミュート解除の誤発火を防ぐhangover）
 *  - request_callback呼び出し直前のPHONE確認ゲート（phoneConfirmationIncomplete）
 *
 * 実行: node tests/test_smart_interruption_noise_resilience_n_c_b.js
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ENGINE_JS_PATH = path.join(__dirname, '..', 'frontend', 'public', 'js', 'realtime-voice-engine.js');
const SRC = fs.readFileSync(ENGINE_JS_PATH, 'utf8');
const PY_PATH = path.join(__dirname, '..', 'app', 'services', 'realtime_voice_ai.py');
const PY_SRC = fs.readFileSync(PY_PATH, 'utf8');

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

let passed = 0;
// SMART INTERRUPTION / NOISE RESILIENCE HOTFIX（今回追加）: 一部のテスト
// （N3/N4/N5/C9、C9補助）は、抽出したrequest_callbackディスパッチ本体
// （正常系でawait callRequestCallbackTool(...)を含む）をvm.runInContextで
// 実行する必要があり、そのためasync IIFEでラップしたPromiseを返す。
// fn()が Promise（thenable）を返した場合は、そのPromiseの解決/拒否を
// 待ってから合否を確定する（awaitせずにpassed++してしまうと、async関数
// 内部の例外が「見かけ上PASS」になってしまう既知の罠を避けるため）。
const pendingAsync = [];
function test(name, fn) {
    let result;
    try {
        result = fn();
    } catch (e) {
        console.error('FAIL: ' + name);
        console.error(e);
        process.exitCode = 1;
        return;
    }
    if (result && typeof result.then === 'function') {
        pendingAsync.push(
            result.then(() => {
                passed++;
                console.log('OK: ' + name);
            }).catch((e) => {
                console.error('FAIL: ' + name);
                console.error(e);
                process.exitCode = 1;
            })
        );
    } else {
        passed++;
        console.log('OK: ' + name);
    }
}

// ============================================================
// N1/N6/N7/N8/N9共通: tick()内の音量ベース無音判定にhold時間が
// 効いており、瞬間的な無音（ノイズによる短い途切れ・単語間の自然な
// ポーズ相当）ではreleaseAiSpeakingProtectionが呼ばれないことを、
// 実ソースの該当ロジックを抽出して直接シミュレートする。
// ============================================================
// tick()内、nowSpeaking計算より後ろの本体ロジック（無音カウントリセット
// のif文 + nowSpeaking遷移によるengage/release分岐のif/else-if）を、
// 2つのextractBlockで連続する範囲として1つにまとめて抽出する
// （else-if単独をextractBlockに渡すと、直前の閉じ括弧を含む文字列
// マッチにより深さカウントがずれるため、"if (...) {"側だけを渡す）。
const TICK_RESET_SIGNATURE = 'if (nowSpeaking) {\n                    // 発話（とみなせる音量）を検知した時点で無音カウントを';
const TICK_START_IDX = SRC.indexOf(TICK_RESET_SIGNATURE);
assert.notStrictEqual(TICK_START_IDX, -1, 'tick()のnowSpeakingリセット部が見つかりません');
const TICK_ELSEIF_SIGNATURE = 'if (!nowSpeaking && aiSpeakingNow) {';
const TICK_ELSEIF_BLOCK = extractBlock(SRC, TICK_ELSEIF_SIGNATURE, TICK_START_IDX);
const TICK_ELSEIF_IDX = SRC.indexOf(TICK_ELSEIF_SIGNATURE, TICK_START_IDX);
const TICK_END_IDX = TICK_ELSEIF_IDX + TICK_ELSEIF_BLOCK.length;
const TICK_LOGIC_SRC = SRC.slice(TICK_START_IDX, TICK_END_IDX);
assert.ok(TICK_LOGIC_SRC.includes('AI_SPEAKING_LEVEL_SILENCE_HOLD_MS'), '抽出範囲にhold判定が含まれていません（抽出範囲がずれている可能性）');

const HOLD_CONST_MATCH = SRC.match(/const AI_SPEAKING_LEVEL_SILENCE_HOLD_MS = (\d+);/);
assert.ok(HOLD_CONST_MATCH, 'AI_SPEAKING_LEVEL_SILENCE_HOLD_MS定数が見つかりません');
const HOLD_MS = parseInt(HOLD_CONST_MATCH[1], 10);

function simulateTickSequence(nowSpeakingSequence, tMsStep) {
    // 実ソースのtick()内ロジック（nowSpeaking計算後の部分）を、
    // avgではなくnowSpeakingを直接注入できる形でvm実行する。
    const engageCalls = [];
    const releaseCalls = [];
    let currentT = 0;
    const context = {
        aiSpeakingNow: false,
        aiSpeakingLevelSilenceSince: null,
        AI_SPEAKING_LEVEL_SILENCE_HOLD_MS: HOLD_MS,
        performance: { now: () => currentT },
        engageAiSpeakingProtection: (reason) => { engageCalls.push(reason); },
        releaseAiSpeakingProtection: (reason) => { releaseCalls.push(reason); },
        bigMic: { classList: { add: () => {}, remove: () => {} } },
        setStatus: () => {},
        stAiSpeakEl: {},
        greetingTiming: { firstAiAudioPlaybackStarted: null },
        logGreetingLatenciesIfReady: () => {},
        lastSpeechStoppedAt: null,
        recordLatencySample: () => {},
        debugMode: false,
    };
    vm.createContext(context);
    for (const nowSpeaking of nowSpeakingSequence) {
        context.nowSpeaking = nowSpeaking;
        vm.runInContext(TICK_LOGIC_SRC, context);
        currentT += tMsStep;
    }
    return { engageCalls, releaseCalls, aiSpeakingNow: context.aiSpeakingNow };
}

test('N1/N6/N7/N8/N9. 瞬間的な無音（hold時間未満）ではreleaseAiSpeakingProtectionが呼ばれない（PHONE/NAME/PURPOSE/DATE-TIME/terminal closing問わず共通のAI音声保護機構）', () => {
    // AIが話している(true) → 短い無音(false) → すぐ話に戻る(true) というシーケンス。
    // 無音の継続時間は1ステップ(=16ms相当)のみでHOLD_MSより十分短い。
    const seq = [true, false, true, true];
    const { releaseCalls, aiSpeakingNow } = simulateTickSequence(seq, 16);
    assert.strictEqual(releaseCalls.length, 0, '瞬間的な無音でreleaseAiSpeakingProtectionが呼ばれてしまっている');
    assert.strictEqual(aiSpeakingNow, true, 'aiSpeakingNowがfalseへ倒れてしまっている（保護が外れた可能性）');
});

test('N1補助. hold時間を超えて継続した無音では正しくreleaseされる（安全網としての機能は維持）', () => {
    const steps = Math.ceil(HOLD_MS / 16) + 3; // hold時間を確実に超える回数
    const seq = [true];
    for (let i = 0; i < steps; i++) seq.push(false);
    const { releaseCalls } = simulateTickSequence(seq, 16);
    assert.ok(releaseCalls.includes('ai_audio_level_silent'), '十分に長い無音の後もreleaseが発火していない（安全網が壊れている）');
});

test('N1補助2. 短い無音を繰り返しても（毎回発話に戻れば）releaseされない（デバウンスのリセットが正しく機能）', () => {
    // true→false→true→false→true という、短い無音を2回挟むパターン。
    const seq = [true, false, true, false, true];
    const { releaseCalls } = simulateTickSequence(seq, 16);
    assert.strictEqual(releaseCalls.length, 0);
});

// ============================================================
// N2. noiseだけでresponse.cancelしない（既存回帰：クライアント側は
// 一度もresponse.cancelを送信しない設計を維持）。
// ============================================================
test('N2. クライアント側はresponse.cancelを一切送信しない（既存設計の回帰確認）', () => {
    assert.ok(!/type:\s*['"]response\.cancel['"]/.test(SRC), 'response.cancel送信箇所が新規に追加されています');
});

// ============================================================
// N3/N4/N5/C9: PHONE確認未完了時にrequest_callbackがバックエンドへ
// 転送されないこと（新設のphoneConfirmationIncompleteゲート）。
// ============================================================
const CALLBACK_DISPATCH_SRC = extractBlock(
    SRC,
    "else if (item.name === 'request_callback') {",
    SRC.indexOf("else if (item.name === 'set_conversation_language') {")
);
assert.ok(CALLBACK_DISPATCH_SRC.includes('phoneConfirmationIncomplete'), 'request_callback分岐にphoneConfirmationIncompleteゲートが見当たりません');
assert.ok(CALLBACK_DISPATCH_SRC.includes("reason_code: 'phone_not_confirmed'"), '安全な失敗形の合成出力(reason_code=phone_not_confirmed)が見当たりません');
assert.ok(!CALLBACK_DISPATCH_SRC.includes('callRequestCallbackTool(args || {}, callId)')
    || CALLBACK_DISPATCH_SRC.indexOf("phoneConfirmationIncomplete")
        < CALLBACK_DISPATCH_SRC.indexOf('callRequestCallbackTool(args || {}, callId)'),
    'ゲートがcallRequestCallbackTool呼び出しより後に配置されている（先にバックエンド呼び出しされてしまう）');

// ディスパッチブロックはif/else if連鎖の一部（'item.name === ...'比較を
// 含む形）なので、単独実行できるよう最小のif文でラップする。さらに、
// このブロックの正常系（else節）にはawait callRequestCallbackTool(...)が
// 含まれるため、trueブランチ（ゲートで即return相当の合成出力を返す側）
// しか実際には実行されない場合でも、構文解析の時点でトップレベルの
// awaitはSyntaxErrorになる。よってasync IIFEで包み、
// vm.runInContextが返すPromiseを必ずawaitする（同期実行のまま放置すると、
// async関数内部の例外がtry/catchで捕捉されず「見かけ上PASS」になる）。
function buildCallbackDispatchWrapped() {
    return '(async () => { if (true) ' + CALLBACK_DISPATCH_SRC.replace(/^else\s*if/, 'if') + ' })()';
}

test('N3/N4/N5/C9. phoneConfirmationIncomplete===trueの場合、request_callbackはバックエンドへ転送されず、terminal状態にも入らない', async () => {
    const calls = [];
    const context = {
        item: { name: 'request_callback' },
        args: { customer_name: 'x', customer_phone: 'x', inquiry_text: 'x' },
        callId: 'call_test',
        callbackAlreadyConfirmedThisCall: false,
        callbackTerminalArmed: false,
        phoneConfirmationIncomplete: true,
        phoneReadbackTurnCompletedThisCall: true,
        phoneReadbackAwaitingUserReply: false,
        output: undefined,
        logEvent: () => {},
        pushTimelineEvent: () => {},
        console: { log: () => {} },
        callRequestCallbackTool: async () => { calls.push('CALLED'); return { success: true }; },
        isToolOutputFailure: (o) => !!(o && o.success === false),
        pendingCallbackTerminalHangup: false,
    };
    vm.createContext(context);
    await vm.runInContext(buildCallbackDispatchWrapped(), context);
    assert.strictEqual(calls.length, 0, 'phoneConfirmationIncomplete=trueにもかかわらずバックエンドのcallRequestCallbackToolが呼ばれてしまった');
    assert.strictEqual(context.output.success, false);
    assert.strictEqual(context.output.reason_code, 'phone_not_confirmed');
    assert.strictEqual(context.callbackTerminalArmed, false, 'terminal状態に入ってしまっている');
});

test('C9補助. phoneConfirmationIncomplete===falseの場合は通常どおりバックエンドへ転送される（回帰・過剰ブロックしていないこと）', async () => {
    const calls = [];
    const context = {
        item: { name: 'request_callback' },
        args: { customer_name: 'x', customer_phone: '090-1234-5678', inquiry_text: 'x' },
        callId: 'call_test',
        callbackAlreadyConfirmedThisCall: false,
        callbackTerminalArmed: false,
        phoneConfirmationIncomplete: false,
        phoneReadbackTurnCompletedThisCall: true,
        phoneReadbackAwaitingUserReply: false,
        output: undefined,
        logEvent: () => {},
        pushTimelineEvent: () => {},
        console: { log: () => {} },
        callRequestCallbackTool: async (a, c) => { calls.push('CALLED'); return { success: true, id: 'cb_1' }; },
        isToolOutputFailure: (o) => !!(o && o.success === false),
        pendingCallbackTerminalHangup: false,
    };
    vm.createContext(context);
    await vm.runInContext(buildCallbackDispatchWrapped(), context);
    assert.strictEqual(calls.length, 1, 'phoneConfirmationIncomplete=falseなのにバックエンド呼び出しがブロックされてしまった');
    assert.strictEqual(context.output.success, true, '正常系のoutputが期待通りでない');
});

// ============================================================
// C1-C8: 既存の訂正契約（NAME/PURPOSE/PHONE）が保持されていることの
// 回帰確認（新しいJS keyword判定を追加していないことも含む）。
// ============================================================
test('C1/C2. NAME訂正の既存契約（ROUTING側での再確認・上書き）が維持されている', () => {
    const idx = PY_SRC.indexOf('_PHASE2_ROUTING_ROLE_TEMPLATE = """');
    const endIdx = PY_SRC.indexOf('"""', idx + '_PHASE2_ROUTING_ROLE_TEMPLATE = """'.length);
    const body = PY_SRC.slice(idx, endIdx);
    assert.ok(body.includes('訂正とみなし'), 'NAME訂正を検知する既存契約が見当たりません');
    assert.ok(body.includes('失礼しました。'), '訂正後の受け止めフレーズが見当たりません');
});

test('C3/C4/C5. PURPOSE訂正時、classify_intentが最新intentで再分類でき、stale transitionを実行しない設計が維持されている', () => {
    const fnIdx = SRC.indexOf('async function callClassifyIntentTool(args) {');
    const fnEnd = SRC.indexOf('\n        }\n', fnIdx);
    const fnBody = SRC.slice(fnIdx, fnEnd);
    assert.ok(fnBody.includes('armPhaseTransitionAfterResponse(intent)'), 'classify_intentが最新intentでarmする経路が見当たりません');
    // armPhaseTransitionAfterResponseは新しいpendingPhaseTransitionTargetで
    // 上書きするだけの単純な代入であり、古いtransitionを実行してから
    // 上書きするような「実行済みのものを取り消す」処理を必要としない
    // （pendingPhaseTransitionTargetはresponse.done境界まで未消費のまま
    // 保持されるため、2回目のclassify_intentが1回目の値を単純に上書き
    // できる）ことを、armPhaseTransitionAfterResponse自体の実装で確認する。
    const armIdx = SRC.indexOf('function armPhaseTransitionAfterResponse(targetPhase) {');
    const armEnd = SRC.indexOf('\n        }\n', armIdx);
    const armBody = SRC.slice(armIdx, armEnd);
    assert.ok(/pendingPhaseTransitionTarget\s*=\s*targetPhase/.test(armBody), 'pendingPhaseTransitionTargetへの単純代入（上書き）が見当たりません');
});

test('C6/C7/C8. PHONE訂正: 推測・補完禁止の既存契約、および customer_phone は常にAI引数から直接取得（JS側でのキャッシュ値の再利用が無い）', () => {
    assert.ok(PY_SRC.includes('推測や聞き取れなかった桁の補完は絶対にしないでください'), 'PHONE推測・補完禁止の既存契約が見当たりません');
    const fnIdx = SRC.indexOf('async function callRequestCallbackTool(args, callId) {');
    const fnEnd = SRC.indexOf('\n        }\n', fnIdx);
    const fnBody = SRC.slice(fnIdx, fnEnd);
    assert.ok(/customer_phone:\s*args\s*&&\s*args\.customer_phone/.test(fnBody), 'customer_phoneがargsから直接取得されていません（キャッシュされた古い値を使っている可能性）');
});

// ============================================================
// B1-B4: 通常のbarge-in・既存VAD/turn_detectionへの非干渉確認。
// ============================================================
test('B1. AI_SPEAKING_PROTECTIONは永久ミュートにならない（20秒安全網タイマーが維持されている）', () => {
    assert.ok(SRC.includes('const AI_SPEAKING_PROTECTION_MAX_MS = 20000;'), '20秒安全網タイマー定数が見当たりません（削除・変更されている可能性）');
    assert.ok(/AI_SPEAKING_PROTECTION_MAX_MS\);/.test(SRC) || SRC.includes('AI_SPEAKING_PROTECTION_MAX_MS)'), '安全網タイマーのsetTimeout呼び出しが見当たりません');
});

test('B2/B3. hold時間の追加はAI自身の音声(remote audio)の無音判定にのみ影響し、ユーザー側マイク入力・turn_detection・semantic_vadには一切影響しない', () => {
    // HOLD定数の全ての出現箇所が、「定数宣言（＋直前の説明コメント）から
    // tick()関数の終端まで」という1つの連続したローカル領域に収まって
    // いること（ユーザー側VAD/speech_started分岐など、離れた場所に
    // 一切紛れ込んでいないこと）を確認する。定数宣言自体はtick()関数の
    // 直前（同じ閉スコープ内）に置かれる設計であり、tick()関数の
    // 波括弧の「内側」だけを見る判定は誤検出になるため、宣言位置を
    // 含む領域として検証する。
    const tickFnIdx = SRC.indexOf('function tick() {');
    assert.notStrictEqual(tickFnIdx, -1);
    let depth = 0, i = SRC.indexOf('{', tickFnIdx), started = false, tickFnEnd = -1;
    for (; i < SRC.length; i++) {
        if (SRC[i] === '{') { depth++; started = true; }
        else if (SRC[i] === '}') {
            depth--;
            if (started && depth === 0) { tickFnEnd = i + 1; break; }
        }
    }
    assert.notStrictEqual(tickFnEnd, -1, 'tick()関数の終端が特定できません');
    const firstOccIdx = SRC.indexOf('AI_SPEAKING_LEVEL_SILENCE_HOLD_MS');
    assert.notStrictEqual(firstOccIdx, -1, 'AI_SPEAKING_LEVEL_SILENCE_HOLD_MSが見つかりません');
    assert.ok(firstOccIdx < tickFnEnd, '定数の最初の出現がtick()関数の終端より後ろにあります（想定構造と異なる）');
    const localRegionSrc = SRC.slice(firstOccIdx, tickFnEnd);
    const totalOccurrences = (SRC.match(/AI_SPEAKING_LEVEL_SILENCE_HOLD_MS/g) || []).length;
    const inRegionOccurrences = (localRegionSrc.match(/AI_SPEAKING_LEVEL_SILENCE_HOLD_MS/g) || []).length;
    assert.strictEqual(totalOccurrences, inRegionOccurrences,
        'AI_SPEAKING_LEVEL_SILENCE_HOLD_MSが定数宣言〜tick()終端の連続領域外にも出現しています（ユーザー側VADへの混入の可能性）: total='
        + totalOccurrences + ' inRegion=' + inRegionOccurrences);
    // ユーザー側の発話開始イベントハンドラ自体にはHOLD定数への参照が無いこと。
    const speechStartedIdx = SRC.indexOf("} else if (type === 'input_audio_buffer.speech_started') {");
    assert.notStrictEqual(speechStartedIdx, -1);
    assert.ok(!SRC.slice(speechStartedIdx, speechStartedIdx + 6000).includes('AI_SPEAKING_LEVEL_SILENCE_HOLD_MS'),
        'input_audio_buffer.speech_startedハンドラ内にHOLD定数への参照が混入しています');
});

test('B4. semantic_vad / turn_detection設定はPython側で無変更（サーバー側VAD自体は一切変更していない）', () => {
    const matches = PY_SRC.match(/"type":\s*"semantic_vad"/g) || [];
    assert.strictEqual(matches.length, 2, 'semantic_vad設定箇所数が想定と異なります（通常セッション+voice previewの2箇所のはず）');
    assert.ok(!PY_SRC.includes('interrupt_response'), 'interrupt_responseが新規に明示設定されています（影響調査なしでの変更は禁止）');
    assert.ok(!PY_SRC.includes('"create_response"'), 'create_responseが新規に明示設定されています（影響調査なしでの変更は禁止）');
});

// 非同期テスト（N3/N4/N5/C9, C9補助）の解決を待ってから最終サマリを出す。
Promise.all(pendingAsync).then(() => {
    console.log('\n' + passed + ' tests passed.');
    if (process.exitCode) {
        console.error('SOME TESTS FAILED');
    } else {
        console.log('ALL N1-N9 / C1-C9 / B1-B4 TESTS PASSED');
    }
});
