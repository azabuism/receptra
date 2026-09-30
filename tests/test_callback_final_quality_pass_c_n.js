'use strict';

/**
 * RECEPTRA — CALLBACK FINAL QUALITY PASS 契約テスト（C1〜C12 / N1〜N8）
 *
 * 対象:
 *  - _HUMAN_HANDOFF_TEMPLATEの新設「2-1. 電話番号の復唱確認」手順
 *    （お客様の返事を待つことの明文化・訂正時の再確認・重複確認防止）
 *  - request_callback呼び出し直前の新設ゲート
 *    （phoneReadbackTurnCompletedThisCall / phoneReadbackAwaitingUserReply）
 *  - NAME認識まわりの既存契約の回帰確認（今回のスコープでは変更していない）
 *
 * 正直な限界（STEP17/STEP29の方針に従い、新しいkeyword/NLU判定は追加して
 * いないため、以下は構造的には検証できない。テスト内でその旨を明記する）:
 *  - C7（お客様が「いいえ」と答えた場合にrequest_callbackが呼ばれないこと）
 *  - N7（訂正後に古い名前へ戻らないこと）
 *  はいずれも、モデル自身の自然言語理解による判断に委ねられており、
 *  JS側には「お客様の返事の内容が肯定か否定か」を判定する仕組みが無い
 *  （これは既存のcreate_reservationも同様であり、今回新たに生じた限界
 *  ではない）。instructions側の文言契約（訂正時は3.へ進まない旨の明記）
 *  としてのみ検証する。
 *
 * 実行: node tests/test_callback_final_quality_pass_c_n.js
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

function getHandoffTemplateBody() {
    const idx = PY_SRC.indexOf('_HUMAN_HANDOFF_TEMPLATE = """');
    const endIdx = PY_SRC.indexOf('"""', idx + '_HUMAN_HANDOFF_TEMPLATE = """'.length);
    return PY_SRC.slice(idx, endIdx);
}

function getCallbackRoleTemplateBody() {
    const idx = PY_SRC.indexOf('_PHASE2B_CALLBACK_ROLE_TEMPLATE = """');
    const endIdx = PY_SRC.indexOf('"""', idx + '_PHASE2B_CALLBACK_ROLE_TEMPLATE = """'.length);
    return PY_SRC.slice(idx, endIdx);
}

let passed = 0;
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
            result.then(() => { passed++; console.log('OK: ' + name); })
                .catch((e) => { console.error('FAIL: ' + name); console.error(e); process.exitCode = 1; })
        );
    } else {
        passed++;
        console.log('OK: ' + name);
    }
}

// ============================================================
// C1/C2/C3: classify_intent(callback)→CALLBACK phase、phone未取得なら
// 尋ね、取得したら日本語で1桁ずつ一度復唱する既存契約の回帰確認。
// ============================================================
test('C1. classify_intent(callback)によりCALLBACK phaseのtoolsはrequest_callbackのみへ絞られる', () => {
    const idx = PY_SRC.indexOf('_CALLBACK_TOOL_NAMES = ');
    assert.notStrictEqual(idx, -1);
    const endIdx = PY_SRC.indexOf('\n', idx);
    assert.ok(PY_SRC.slice(idx, endIdx).includes('"request_callback"'));
});

test('C2. まだ電話番号を伺っていない場合は尋ねる旨が_PHASE2B_CALLBACK_ROLE_TEMPLATEに明記されている', () => {
    const body = getCallbackRoleTemplateBody();
    assert.ok(body.includes('まだ伺っていなければ、「折り返し先のお電話番号をお願いします。」と'));
});

test('C3. 電話番号は必ず1桁ずつ読み上げて復唱する契約が明記されている', () => {
    const handoff = getHandoffTemplateBody();
    assert.ok(handoff.includes('必ず1桁ずつ読み上げて復唱し'));
    assert.ok(handoff.includes('推測や聞き取れなかった桁の補完は絶対にしないでください'));
});

// ============================================================
// C4: 電話番号復唱後、YES/NOを待つことが明記されている（今回の root cause
// 修正の中心）。
// ============================================================
test('C4. 2-1（電話番号の復唱確認）はお客様の明確な返事を待つことを明記し、手順3の「実況・返事待ち禁止」の対象外であることも明記している', () => {
    const handoff = getHandoffTemplateBody();
    const idx21 = handoff.indexOf('2-1. 電話番号の復唱確認');
    const idx3 = handoff.indexOf('3. 上記の必要な情報がすべて揃ったら');
    assert.notStrictEqual(idx21, -1, '2-1セクションが見つかりません');
    assert.notStrictEqual(idx3, -1);
    assert.ok(idx21 < idx3, '2-1は3.より前に配置されている必要があります');
    const s21 = handoff.slice(idx21, idx3);
    // テンプレート内では長文が折り返されているため、空白・改行を除去した上で照合する。
    const s21Flat = s21.replace(/\s+/g, '');
    assert.ok(s21Flat.includes('お客様の明確な返事（肯定または訂正）を必ず待ってください'));
    assert.ok(s21Flat.includes('ゼロキュウゼロ…でよろしいでしょうか？'));
    assert.ok(s21Flat.includes('この電話番号の復唱確認には適用されません'));
});

// ============================================================
// C5: 構造的ゲート — お客様の返事（input_audio_buffer.committed）を
// まだ一度も受け取っていない間はrequest_callbackがブロックされる。
// ============================================================
const CALLBACK_DISPATCH_SRC = extractBlock(
    SRC,
    "else if (item.name === 'request_callback') {",
    SRC.indexOf("else if (item.name === 'set_conversation_language') {")
);
assert.ok(CALLBACK_DISPATCH_SRC.includes('phoneReadbackTurnCompletedThisCall'), 'phoneReadbackTurnCompletedThisCallゲートが見当たりません');
assert.ok(CALLBACK_DISPATCH_SRC.includes('phoneReadbackAwaitingUserReply'), 'phoneReadbackAwaitingUserReplyゲートが見当たりません');

function buildCallbackDispatchWrapped() {
    return '(async () => { if (true) ' + CALLBACK_DISPATCH_SRC.replace(/^else\s*if/, 'if') + ' })()';
}

function buildDispatchContext(overrides) {
    const calls = [];
    const context = Object.assign({
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
        callRequestCallbackTool: async () => { calls.push('CALLED'); return { success: true }; },
        isToolOutputFailure: (o) => !!(o && o.success === false),
        pendingCallbackTerminalHangup: false,
    }, overrides || {});
    vm.createContext(context);
    return { context, calls };
}

test('C5. phoneReadbackTurnCompletedThisCall===falseの場合（AIが一度もPHONE型発話を完了していない）、request_callbackはブロックされる', async () => {
    const { context, calls } = buildDispatchContext({ phoneReadbackTurnCompletedThisCall: false });
    await vm.runInContext(buildCallbackDispatchWrapped(), context);
    assert.strictEqual(calls.length, 0);
    assert.strictEqual(context.output.reason_code, 'phone_not_confirmed');
});

test('C5-2. phoneReadbackAwaitingUserReply===true（読み上げ済みだがお客様の返事をまだ受け取っていない）の場合、request_callbackはブロックされる', async () => {
    const { context, calls } = buildDispatchContext({ phoneReadbackTurnCompletedThisCall: true, phoneReadbackAwaitingUserReply: true });
    await vm.runInContext(buildCallbackDispatchWrapped(), context);
    assert.strictEqual(calls.length, 0);
    assert.strictEqual(context.output.reason_code, 'phone_not_confirmed');
});

test('C6. phoneReadbackTurnCompletedThisCall===true かつ phoneReadbackAwaitingUserReply===false（読み上げ済み・返事も受領済み）の場合はrequest_callbackがバックエンドへ転送される（過剰ブロックしていないこと）', async () => {
    const { context, calls } = buildDispatchContext({});
    await vm.runInContext(buildCallbackDispatchWrapped(), context);
    assert.strictEqual(calls.length, 1);
    assert.strictEqual(context.output.success, true);
});

test('C7. [正直な限界・instructions契約のみ] お客様が電話番号を訂正した場合、request_callbackへ進まず再度確認する旨が明記されている（お客様の返事が肯定か否定かをJS側で判定する仕組みは無く、モデル自身の理解に委ねられる。create_reservationと同じ既存の設計限界）', () => {
    const handoff = getHandoffTemplateBody();
    const idx21 = handoff.indexOf('2-1. 電話番号の復唱確認');
    const idx3 = handoff.indexOf('3. 上記の必要な情報がすべて揃ったら');
    const s21 = handoff.slice(idx21, idx3);
    assert.ok(s21.includes('訂正された場合はrequest_callbackへ進まず'));
});

test('C8. 訂正された場合、新しい番号を同様に1桁ずつ読み上げて確認し直す旨が明記されている', () => {
    const handoff = getHandoffTemplateBody();
    const idx21 = handoff.indexOf('2-1. 電話番号の復唱確認');
    const idx3 = handoff.indexOf('3. 上記の必要な情報がすべて揃ったら');
    const s21 = handoff.slice(idx21, idx3);
    assert.ok(s21.includes('新しい番号を同様に'));
    assert.ok(s21.includes('1桁ずつ読み上げて確認し直してください'));
});

test('C9. [構造ゲート] 訂正による新しいPHONE型発話（完了）が発生すると、returnAwaitingUserReplyが再度trueへ戻り、その直後（お客様の返事より前）のrequest_callbackは再びブロックされる', async () => {
    // classify branch（PHONE分類・完了時のフラグ更新ロジック）を抽出して
    // 直接シミュレートする。新しいkeyword判定は使わず、既存の
    // lastResponseTranscriptWasIncompleteAiTurnをそのまま注入するのみ。
    const CLASSIFY_UPDATE_SRC = extractBlock(SRC, "if (type === 'PHONE') {");
    assert.ok(CLASSIFY_UPDATE_SRC.includes('phoneReadbackTurnCompletedThisCall'));
    const ctx = {
        type: 'PHONE',
        lastResponseTranscriptWasIncompleteAiTurn: false,
        phoneConfirmationIncomplete: true, // 訂正前の古い状態を持ち越してみる
        phoneReadbackTurnCompletedThisCall: true,
        phoneReadbackAwaitingUserReply: false, // 訂正前は既に返事を受領済みだった
    };
    vm.createContext(ctx);
    vm.runInContext(CLASSIFY_UPDATE_SRC, ctx);
    // 新しい（訂正後の）完了したPHONE発話により、再度「返事待ち」に戻る。
    assert.strictEqual(ctx.phoneReadbackAwaitingUserReply, true, '訂正後の新しいPHONE発話後、返事待ちフラグが再度trueに戻っていません');
    assert.strictEqual(ctx.phoneConfirmationIncomplete, false);
});

test('C10. request_callback呼び出し時のcustomer_phoneは常にAIの引数から直接取得され、JS側でキャッシュされた古い番号が使われない（既存契約の回帰）', () => {
    const fnIdx = SRC.indexOf('async function callRequestCallbackTool(args, callId) {');
    const fnEnd = SRC.indexOf('\n        }\n', fnIdx);
    const fnBody = SRC.slice(fnIdx, fnEnd);
    assert.ok(/customer_phone:\s*args\s*&&\s*args\.customer_phone/.test(fnBody));
});

test('C11. 電話番号を英語の数字（ninety等）で読まない契約が維持されている（日本語のゼロ・キュウ等の読みのみ）', () => {
    const handoff = getHandoffTemplateBody();
    // FINAL TUNING（実機フィードバック対応）でブロック内の間を除去した新しい
    // 例文（ゼロキュウゼロ）に更新。日本語での桁読み自体（英語読みでない）が
    // 維持されていることの確認が目的であり、句読点（・の有無）は対象外。
    assert.ok(handoff.includes('ゼロキュウゼロ'));
    assert.ok(!/ninety|zero nine zero/i.test(handoff));
});

test('C12. 既にこの通話中に電話番号を復唱確認済みの場合は再確認不要である旨が明記されている（無限ループ防止）', () => {
    const handoff = getHandoffTemplateBody();
    const idx21 = handoff.indexOf('2-1. 電話番号の復唱確認');
    const idx3 = handoff.indexOf('3. 上記の必要な情報がすべて揃ったら');
    const s21 = handoff.slice(idx21, idx3);
    assert.ok(s21.includes('復唱確認済みの場合は再確認不要です'));
});

// ============================================================
// N1-N8: NAME認識の既存契約回帰確認（今回のスコープでは一切変更していない。
// STEP17により、Tool schema変更・input_audio_transcription導入は
// 今回実装していない＝report onlyのため、変化が無いことを確認するのみ）。
// ============================================================
test('N1/N2. confirm_customer_nameは1回の通話につき一度だけ呼び出す契約が維持されている', () => {
    const idx = PY_SRC.indexOf('"name": "confirm_customer_name"');
    const endIdx = PY_SRC.indexOf('},\n    },', idx);
    const body = PY_SRC.slice(idx, endIdx);
    assert.ok(body.includes('この関数は1回の通話につき一度だけ呼び出してください'));
});

test('N3. NAME確認後にROUTINGへ進む既存のphase遷移機構が変更されていない', () => {
    assert.ok(SRC.includes("armPhaseTransitionAfterResponse('routing'"));
});

test('N4. ROUTING phaseはお名前を再質問しない契約が維持されている', () => {
    const idx = PY_SRC.indexOf('_PHASE2_ROUTING_ROLE_TEMPLATE = """');
    const endIdx = PY_SRC.indexOf('"""', idx + '_PHASE2_ROUTING_ROLE_TEMPLATE = """'.length);
    const body = PY_SRC.slice(idx, endIdx);
    assert.ok(body.includes('お名前は直前のフェーズで既に確認済みです'));
    assert.ok(body.includes('お名前を重ねて確認・復唱しないでください'));
});

test('N5. CALLBACK phaseはお名前を再質問しない契約が維持されている', () => {
    const body = getCallbackRoleTemplateBody();
    assert.ok(body.includes('お名前は既に伺っています。'));
    assert.ok(body.includes('改めてお名前を尋ねないでください。'));
});

test('N6. NAME訂正（「違います」等）を検知しお客様の新しい名前を受理する既存契約が維持されている', () => {
    const idx = PY_SRC.indexOf('_PHASE2_ROUTING_ROLE_TEMPLATE = """');
    const endIdx = PY_SRC.indexOf('"""', idx + '_PHASE2_ROUTING_ROLE_TEMPLATE = """'.length);
    const body = PY_SRC.slice(idx, endIdx);
    assert.ok(body.includes('訂正とみなし'));
    assert.ok(body.includes('失礼しました。'));
});

test('N7. [正直な限界・instructions契約のみ] 訂正後に古い名前へ戻らないことは、NAME訂正契約の一部として明記されているが、JS側に構造的な「確定済み名前」の保持は無い（confirm_customer_nameは引数ゼロのTool。詳細はSTEP12-16参照）', () => {
    const idx = PY_SRC.indexOf('"name": "confirm_customer_name"');
    const endIdx = PY_SRC.indexOf('"parameters": {', idx);
    assert.ok(idx !== -1 && endIdx !== -1);
    // arg-zero設計（PII最小化）が今回も変更されていないことのみ確認する。
    const paramsIdx = PY_SRC.indexOf('"properties": {}', endIdx);
    const paramsEnd = PY_SRC.indexOf('\n    },\n]', paramsIdx);
    assert.notStrictEqual(paramsIdx, -1, 'confirm_customer_nameがarg-zeroでなくなっています（今回はTool schema変更を実装していないはず）');
});

test('N8. 特定の実在姓（谷村/田村）がNAME関連のTool定義・role templateにハードコードされていない', () => {
    const nameToolIdx = PY_SRC.indexOf('_NAME_TOOLS = [');
    const nameToolEnd = PY_SRC.indexOf('\n]', nameToolIdx);
    const nameToolBody = PY_SRC.slice(nameToolIdx, nameToolEnd);
    assert.ok(!nameToolBody.includes('谷村') && !nameToolBody.includes('田村'));

    const p1Idx = PY_SRC.indexOf('_PHASE1_NAME_ROLE_TEMPLATE = """');
    const p1End = PY_SRC.indexOf('"""', p1Idx + '_PHASE1_NAME_ROLE_TEMPLATE = """'.length);
    const p1Body = PY_SRC.slice(p1Idx, p1End);
    assert.ok(!p1Body.includes('谷村') && !p1Body.includes('田村'));
});

// ============================================================
// バックエンド永続化・オーナー通知（STEP10/STEP11）の実装状況の回帰確認
// （静的チェックのみ。実際のDB/LINE配信は実機/staging確認が必要）。
// ============================================================
test('STEP10. request_callback Toolエンドポイントはidempotency_keyの一意インデックスでDB保存の冪等性を担保している', () => {
    const routerPath = path.join(__dirname, '..', 'app', 'routers', 'realtime_voice.py');
    const routerSrc = fs.readFileSync(routerPath, 'utf8');
    assert.ok(routerSrc.includes('idempotency_key = f"realtime_voice:{shop_id}:{request.call_id}"'));
    assert.ok(routerSrc.includes('IntegrityError'));
});

test('STEP11. LINE通知配信ワーカー（Phase N2）がapp.main.pyのlifespanから起動されている', () => {
    const mainPath = path.join(__dirname, '..', 'app', 'main.py');
    const mainSrc = fs.readFileSync(mainPath, 'utf8');
    assert.ok(mainSrc.includes('run_line_notification_worker_loop'));
});

console.log('\n' + passed + ' tests passed.');
Promise.all(pendingAsync).then(() => {
    if (process.exitCode) {
        console.error('SOME TESTS FAILED');
    } else {
        console.log('ALL CALLBACK FINAL QUALITY PASS TESTS PASSED');
    }
});
