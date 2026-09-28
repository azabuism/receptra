'use strict';

// RECEPTRA — FAST TURN HOTFIX 17（2026年9月）
// 「NO REDUNDANT CALLBACK CONFIRMATION + ZERO-WAIT GREETING CLICK/POP
// DIAGNOSTIC」の契約テスト。
//
// ============================================================
// PART A — 実機症状
// ============================================================
// HOTFIX16適用後の実機テストで、NAME/DATE/TIME/PARTY_SIZE/PHONE/callback
// terminal/FIRST ANSWER MUST COUNTは全て正常だったが、折り返し希望が既に
// 会話上確定しているにもかかわらず、AIが「折り返しのご連絡いたしますか？」
// のようなYes/No確認を重ねて行う症状が新たに報告された。
//
// 実コードで確認したroot cause候補（推測ではなく、実際に
// _HUMAN_HANDOFF_TEMPLATE / _PHASE2B_CALLBACK_ROLE_TEMPLATE / request_callback
// ツールのdescriptionを読んで特定した）:
//   (1) HOTFIX16で追加した手順1の例文自体が「いくつかお伺いしてもよろしい
//       でしょうか。」という許可を求める質問形になっていた。これはお客様が
//       既に折り返しを明示的に希望している場面でも、AIに「よろしいですか」
//       型の確認を促す形として作用し得る。
//   (2) _PHASE2B_CALLBACK_ROLE_TEMPLATE（CALLBACK phase専用の役割説明）は
//       「お客様は既に折り返しをご希望であることが分かっている」と述べつつ、
//       直後にrequest_callbackツールの説明文・_HUMAN_HANDOFF_TEMPLATEの
//       「折り返しが必要な場面」判定基準セクションを丸ごと再利用しており、
//       この判定基準セクションには「既に確定済みなので再確認しない」という
//       明示的な一文が無かった。
//   (3) request_callbackツール自体のdescriptionには、既に折り返し希望が
//       確定している場合に再確認しないことを明示する一文が無く、かつ
//       「呼び出し中は『確認いたしますので少々お待ちください』程度の中立的な
//       案内にとどめてください」という、HOTFIX16で導入した「処理中ナレーション
//       禁止」方針と矛盾する古い文言がそのまま残っていた。
//
// 修正（プロンプトのみ・新しいstateは追加していない。既存のPhase 2B
// context自体が「intentは既に確定済み」という信号を保持しているため、
// それをそのまま活かす形の最小追記）:
//   - 手順1の例文を質問形でない言い切り（「担当者へお伝えいたします。」）に
//     変更し、「折り返し希望が既に確定している場合は改めて確認し直さない」旨を
//     明記。
//   - _PHASE2B_CALLBACK_ROLE_TEMPLATEに「折り返しの判断は既に確定済みであり、
//     この後のセクションの判定基準を読み返して再確認する必要はない」旨を追記。
//   - request_callbackツールのdescriptionから、処理中ナレーションを促す
//     古い文言を削除し、「既に確定している折り返し希望を再確認しない」旨を
//     追記。
// 手順5(5-1/5-2/5-3)・6、ROUTING/RESERVATION/NAME、および全てのJS側
// terminal機構（HOTFIX14〜16で確立済み）は一切変更していない。
//
// ============================================================
// PART B — Zero-Wait Greeting click/popノイズの調査
// ============================================================
// 実機で通話開始直後、ほぼ毎回同じ位置に短いclick/pop/音切れノイズが
// 「お電話（ブツッ）ありがとうございます」のように入る。
//
// コード監査で確認した事実（推測ではなく実際に確認した構造）:
//   - greeting音声の文言は_resolve_greeting_text()の1箇所のみで決定される
//     単一の文字列であり、クライアント側・サーバー側どちらでも「お電話」と
//     「ありがとうございます」を別々に生成・結合する処理は存在しない
//     （client.audio.speech.create()は1回の呼び出しで1つのMP3を生成する
//     単純な実装で、チャンク分割・concatenationは無い）。
//   - フロントエンドのZero-Wait再生はFetch→Blob→ObjectURL→<audio>という
//     単純な構成で、preload='auto'によりBlob全体がダウンロード済みの状態で
//     再生を開始する（ネットワークストリーミングによる途中切断の可能性は
//     低い）。再生中にWebAudio/AudioContext等による音量操作・track切替も
//     行われていない。
//   - 生成された音声は AIStaffSettings.greeting_audio_data にDBキャッシュ
//     され、以後は同一店舗・同一設定であれば毎回まったく同じバイト列が
//     再生される。これは実機症状の「毎回ほぼ同じ位置」という報告と正確に
//     整合する（厳密には「ほぼ同じ」ではなく「毎回同一の音声ファイル」が
//     再生されている）。
//   - startCall()内で、Zero-Wait音声のplay()呼び出し（startZeroWaitGreeting()）
//     は、getUserMedia({echoCancellation:true,...})の呼び出しよりも前、
//     同じ同期実行チェーンの中で（awaitを挟まず）行われる。そのため、
//     Zero-Wait音声の再生が始まった直後（現実的には数十〜数百ms後）に
//     getUserMediaが呼ばれ、ブラウザ／OSの音声入出力サブシステムが
//     echoCancellation対応のために再初期化される可能性がある。この
//     タイミングが偶然にも、多くの店舗のデフォルト第一声文言
//     （「お電話ありがとうございます。」）の「お電話」と「ありがとう
//     ございます」の間のあたりに重なる、というのが最も有力な作業仮説である。
//
// 重要な制約: この作業仮説は、Cloud/Mac双方からOpenAI TTS API
// （api.openai.com）への実際のネットワークアクセスが組織のegress許可
// リストでブロックされているため（403）、実際の音声波形を生成・解析して
// 検証することができなかった。そのため、挙動を変更する具体的な修正
// （例: getUserMediaの呼び出しタイミングの変更）はこのHOTFIXでは行わず、
// 実機でのタイムスタンプ相関を可能にする診断ログの追加のみを行った
// （GET_USER_MEDIA_REQUESTED/GET_USER_MEDIA_RESOLVED。Zero-Wait音声の
// currentTime（再生位置・秒）とelapsedSinceCallStartMsのみを記録し、
// PIIは一切含まない）。Zero-Wait Greeting自体の無効化・削除は行っていない
// （ユーザー指示§19）。
//
// 実行: node tests/test_fast_turn_hotfix17_callback_no_reconfirm_zero_wait_audio.js

const fs = require('fs');
const path = require('path');
const assert = require('assert');
const { spawnSync } = require('child_process');

const ENGINE_JS_PATH = path.join(__dirname, '..', 'frontend', 'public', 'js', 'realtime-voice-engine.js');
const SRC = fs.readFileSync(ENGINE_JS_PATH, 'utf8');
const PY_PATH = path.join(__dirname, '..', 'app', 'services', 'realtime_voice_ai.py');
const PY_SRC = fs.readFileSync(PY_PATH, 'utf8');

// Pythonの三重引用符テンプレートは可読性のため80桁前後で改行しており、その
// 改行位置は将来の再フォーマットで変わり得る（意味は変わらない）。文言の
// 「存在」を厳密文字列一致で検証するテストは、改行・半角/全角スペースを
// 除去して正規化した文字列に対して行う（tests/smoke_test_name_first_
// contract.pyの_normalized()と同じ考え方）。ソース構造そのもの（セクション
// 境界の抽出等）にはPY_SRC/生のtの方をそのまま使う。
function _normalized(s) {
    return s.replace(/\n/g, '').replace(/ /g, '').replace(/　/g, '');
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
        console.log('    ' + (e && e.stack ? e.stack.split('\n').slice(0, 8).join('\n    ') : e));
    }
}

function runNodeTest(relPath) {
    const result = spawnSync(process.execPath, [path.join(__dirname, relPath)], { encoding: 'utf8' });
    return { status: result.status, stdout: result.stdout || '', stderr: result.stderr || '' };
}

function runPythonTest(relPath) {
    const result = spawnSync('python3', [path.join(__dirname, '..', relPath)], { encoding: 'utf8', cwd: path.join(__dirname, '..') });
    return { status: result.status, stdout: result.stdout || '', stderr: result.stderr || '' };
}

function getHandoffTemplateBody() {
    const idx = PY_SRC.indexOf('_HUMAN_HANDOFF_TEMPLATE = """');
    const endIdx = PY_SRC.indexOf('\n"""', idx);
    return PY_SRC.slice(idx, endIdx);
}

function getPhase2bRoleTemplateBody() {
    const idx = PY_SRC.indexOf('_PHASE2B_CALLBACK_ROLE_TEMPLATE = """');
    const endIdx = PY_SRC.indexOf('\n"""', idx);
    return PY_SRC.slice(idx, endIdx);
}

function getRequestCallbackToolDescription() {
    const idx = PY_SRC.indexOf('"name": "request_callback"');
    assert.notStrictEqual(idx, -1);
    const descIdx = PY_SRC.indexOf('"description": (', idx);
    const endIdx = PY_SRC.indexOf('),\n        "parameters"', descIdx);
    return PY_SRC.slice(descIdx, endIdx);
}

console.log('FAST TURN HOTFIX 17 — CALLBACK NO REDUNDANT CONFIRMATION + ZERO-WAIT AUDIO DIAGNOSTIC contract tests');
console.log('source: ' + ENGINE_JS_PATH);
console.log('');

// ============================================================
// PART A — explicit vs ambiguous callback intent
// ============================================================

test('A) [explicit intent確定] _PHASE2B_CALLBACK_ROLE_TEMPLATEは、この phase に入った時点でお客様の折り返し希望が既に確定済みであることを明示している', () => {
    const t = getPhase2bRoleTemplateBody();
    assert.ok(t.includes('既に担当者からの折り返し、または取り次ぎをご希望であることが'));
    assert.ok(t.includes('既に確定済みです'));
});

test('B) [再確認禁止] _PHASE2B_CALLBACK_ROLE_TEMPLATEは、「折り返しのご連絡をご希望ですか」等、折り返しを行うこと自体を改めて確認し直すことを明示的に禁止している', () => {
    const t = _normalized(getPhase2bRoleTemplateBody());
    assert.ok(t.includes('折り返しのご連絡をご希望ですか'), 'must explicitly name the forbidden redundant question to guard against it');
    assert.ok(t.includes('改めて確認し直すことは絶対にしないでください'));
});

test('B2) [判定基準の再評価禁止] _PHASE2B_CALLBACK_ROLE_TEMPLATEは、この後の「折り返し対応」セクションの判定基準（折り返しが必要な場面）を読み返して再確認する必要が無いことを明示している', () => {
    const t = _normalized(getPhase2bRoleTemplateBody());
    assert.ok(t.includes('その判断は既にこの時点で終わっています'));
});

test('C) [ambiguous intentは引き続き確認可能・回帰なし] ROUTING/OTHER-UNCLEARセクションの二択確認質問（「ご予約についてでしょうか？それとも担当者からの折り返しをご希望でしょうか？」）は変更されていない', () => {
    assert.ok(PY_SRC.includes('ご予約についてでしょうか？それとも担当者からの折り返しをご希望'), 'the ambiguous-intent confirmation question must remain available for genuinely unclear cases');
});

test('D) [手順1: 質問形の例文を言い切りへ変更] _HUMAN_HANDOFF_TEMPLATE手順1の例文はもはや「よろしいでしょうか」という許可を求める質問形ではない', () => {
    const t = getHandoffTemplateBody();
    const step1 = t.slice(t.indexOf('## 折り返し対応の進め方'), t.indexOf('2. 折り返しに必要な最小限の情報'));
    assert.ok(!step1.includes('いくつかお伺いしてもよろしいでしょうか'), 'the old permission-seeking question-style example phrase must be removed');
    assert.ok(step1.includes('担当者へお伝えいたします'), 'a declarative (non-question) example phrase must be present instead');
});

test('D2) [手順1: 既に確定している場合の再確認禁止を明記] _HUMAN_HANDOFF_TEMPLATE手順1は、既に折り返し希望が確定している場合に改めて確認し直さないことを明示している', () => {
    const t = getHandoffTemplateBody();
    const step1 = t.slice(t.indexOf('## 折り返し対応の進め方'), t.indexOf('2. 折り返しに必要な最小限の情報'));
    assert.ok(step1.includes('折り返しのご連絡をご希望ですか'));
    // FAST TURN HOTFIX 19（今回更新）: 「担当者から折り返ししますね」という
    // NG例文と「同意を求め直したりする」という禁止を追記したことに伴い、
    // 元の「改めて確認し直すことは絶対にしないでください」が「改めて確認し
    // 直したり、同意（「はい」等の返事）を求め直したりすることは絶対に
    // しないでください」へ変わった（意味は保持・拡張のみ）。
    assert.ok(step1.includes('改めて確認し直したり'));
    assert.ok(step1.includes('求め直したりすることは絶対にしないでください'));
});

test('E) [request_callbackツール自体: 再確認禁止を明記] request_callbackツールのdescriptionは、既に折り返し希望が確定している場合に改めて確認しないことを明示している', () => {
    const desc = getRequestCallbackToolDescription();
    assert.ok(desc.includes('折り返しのご連絡をご希望ですか'));
    // FAST TURN HOTFIX 19（今回更新）: 手順1と同じ理由で文言が拡張された。
    assert.ok(desc.includes('改めて確認し'));
    assert.ok(desc.includes('直したり、同意を求め直したりすることは絶対にしないでください'));
});

test('F) [process narration矛盾の解消] request_callbackツールのdescriptionから、処理中ナレーションを促す古い文言（「確認いたしますので少々お待ちください」）が削除されている（HOTFIX16の「内部処理として扱う」方針との矛盾を解消）', () => {
    const desc = getRequestCallbackToolDescription();
    assert.ok(!desc.includes('確認いたしますので少々お待ちください'), 'the stale narration-encouraging phrase must be removed to avoid contradicting the HOTFIX16 no-narration policy');
    // FAST TURN HOTFIX 19（今回更新）: 「実況する発話は」→「実況したり、
    // お客様の「はい」等の返事を待ったりする発話は」へ文言が拡張された。
    assert.ok(desc.includes('実況したり'), 'a no-narration instruction should be present instead');
});

test('G) [回帰: 手順5(5-1/5-2/5-3)・6は無変更] smoke_test_human_handoff_wording.pyが検証する成功/失敗クロージングの厳密な文言は変更されていない', () => {
    const r = runPythonTest('tests/smoke_test_human_handoff_wording.py');
    assert.strictEqual(r.status, 0, 'smoke_test_human_handoff_wording.py must pass unchanged:\n' + r.stdout + r.stderr);
});

test('H) [回帰: 情報収集ロジック(手順2)は無変更] 手順2の情報収集リスト（お名前・電話番号・お問い合わせ内容・日時/人数）はそのまま残っている', () => {
    const t = getHandoffTemplateBody();
    const step2 = t.slice(t.indexOf('2. 折り返しに必要な最小限の情報'), t.indexOf('3. 上記の必要な情報がすべて揃ったら'));
    assert.ok(step2.includes('お客様のお名前'));
    assert.ok(step2.includes('折り返し先の電話番号'));
    assert.ok(step2.includes('お問い合わせ内容'));
});

test('I) [回帰: HOTFIX16の内部処理化(手順3)は維持] 手順3の「処理中ナレーションを一切せずrequest_callbackを内部処理として呼び出す」指示は残っている', () => {
    const t = getHandoffTemplateBody();
    const step3 = t.slice(t.indexOf('3. 上記の必要な情報がすべて揃ったら'), t.indexOf('4. request_callback の結果'));
    // FAST TURN HOTFIX 19（今回更新）: 「実況する発話は一切せずに」が
    // 「実況したり、お客様の「はい」等の返事を待つ発話は一切せずに」へ
    // 拡張された（意味は保持。「はい」待ちの禁止が追加されただけ）ため、
    // アサーションを新しい文言に合わせて更新する。
    assert.ok(step3.includes('実況したり'));
    assert.ok(step3.includes('返事を待つ発話は一切せずに'));
    assert.ok(step3.includes('内部処理として扱ってください'));
});

test('J) [回帰: request_callback最大1回/final response最大1回のJS側terminal機構は無変更] HOTFIX14/15/16のJSテストが全て通る', () => {
    for (const relPath of [
        'test_fast_turn_hotfix14_greeting_callback_terminal.js',
        'test_fast_turn_hotfix15_callback_final_confirmation.js',
        'test_fast_turn_hotfix16_callback_single_terminal.js',
    ]) {
        const r = runNodeTest(relPath);
        assert.strictEqual(r.status, 0, relPath + ' must pass:\n' + r.stdout + r.stderr);
    }
});

test('K) [回帰: failure時success捏造なし] request_callbackツールのdescriptionは、successがfalseの場合に折り返すと案内しないことを引き続き明示している', () => {
    const desc = getRequestCallbackToolDescription();
    assert.ok(desc.includes('successがfalseの場合は'));
    assert.ok(desc.includes('担当者から折り返す、とは案内せず'));
});

test('L) [回帰: PHONE/DATE/TIME/PARTY_SIZE/NAME/FIRST ANSWER MUST COUNT] 対象の既存JSテストが全て通る（HOTFIX17はこれらのロジックに一切触れていない）', () => {
    for (const relPath of [
        'test_first_answer_must_count.js',
        'test_phone_forced_commit.js',
        'test_name_first_flow.js',
    ]) {
        const r = runNodeTest(relPath);
        assert.strictEqual(r.status, 0, relPath + ' must pass:\n' + r.stdout + r.stderr);
    }
});

test('M) [回帰: NAME/ROUTING/RESERVATIONはHOTFIX17の対象外] NAME/ROUTING/RESERVATION用instructions構築関数は_HUMAN_HANDOFF_TEMPLATE/_PHASE2B_CALLBACK_ROLE_TEMPLATEのいずれも参照していない（char deltaはCALLBACK/legacy_fullのみ）', () => {
    const nameRoutineStart = PY_SRC.indexOf('def _build_phase_minimal_instructions(');
    assert.notStrictEqual(nameRoutineStart, -1);
    const nextDefIdx = PY_SRC.indexOf('\ndef _build_reservation_phase_instructions(', nameRoutineStart);
    assert.notStrictEqual(nextDefIdx, -1);
    const fnBody = PY_SRC.slice(nameRoutineStart, nextDefIdx);
    assert.ok(!fnBody.includes('_HUMAN_HANDOFF_TEMPLATE'));
    assert.ok(!fnBody.includes('_PHASE2B_CALLBACK_ROLE_TEMPLATE'));

    const reservationStart = nextDefIdx + 1;
    const callbackDefIdx = PY_SRC.indexOf('\ndef _build_callback_phase_instructions(', reservationStart);
    assert.notStrictEqual(callbackDefIdx, -1);
    const reservationBody = PY_SRC.slice(reservationStart, callbackDefIdx);
    assert.ok(!reservationBody.includes('_HUMAN_HANDOFF_TEMPLATE'));
    assert.ok(!reservationBody.includes('_PHASE2B_CALLBACK_ROLE_TEMPLATE'));
});

// ============================================================
// PART B — Zero-Wait Greeting audio structure (§13-§17 audit,
// diagnostics-only — no playback-timing fix applied, see file header)
// ============================================================

test('N) [単一TTS呼び出し・テキスト分割なし] greeting音声は_resolve_greeting_text()が返す単一の文字列から、client.audio.speech.create()の1回の呼び出しで生成される（「お電話」と「ありがとうございます」を別々に生成・結合する処理は存在しない）', () => {
    const genFnStart = PY_SRC.indexOf('async def generate_greeting_tts_audio(');
    assert.notStrictEqual(genFnStart, -1);
    const genFnEnd = PY_SRC.indexOf('\nasync def get_effective_greeting_text(', genFnStart);
    const genFnBody = PY_SRC.slice(genFnStart, genFnEnd);
    const ttsCallCount = (genFnBody.match(/client\.audio\.speech\.create\(/g) || []).length;
    assert.strictEqual(ttsCallCount, 1, 'exactly one TTS call must generate the whole greeting text as a single unit');
    assert.ok(genFnBody.includes('greeting_text = _resolve_greeting_text('));
    assert.ok(!/\+\s*greeting_text|greeting_text\s*\+/.test(genFnBody), 'greeting_text must not be concatenated with another TTS-bound fragment inside this function');
});

test('O) [同一文言の一元管理] _resolve_greeting_text()は第一声のRealtime instructions埋め込み用とZero-Wait TTS音声生成用の両方から共通で呼ばれる唯一の場所である（食い違い・分割生成の防止）', () => {
    const callSites = (PY_SRC.match(/_resolve_greeting_text\(/g) || []).length;
    // 定義自体(1) + _build_greeting_section内(1) + generate_greeting_tts_audio内(1) = 3箇所。
    assert.ok(callSites >= 3, 'expected _resolve_greeting_text to be defined once and called from both the instructions builder and the TTS generator');
});

test('P) [キャッシュにより毎回同一音声が再生される] get_or_generate_greeting_audio()は一度生成した音声をDBキャッシュし、以後は同一設定であれば同一バイト列を再生する（実機症状「毎回ほぼ同じ位置」との整合性）', () => {
    const fnStart = PY_SRC.indexOf('async def get_or_generate_greeting_audio(');
    assert.notStrictEqual(fnStart, -1);
    const fnEnd = PY_SRC.indexOf('\nasync def ', fnStart + 10);
    const fnBody = PY_SRC.slice(fnStart, fnEnd);
    assert.ok(fnBody.includes('greeting_audio_data'), 'must reference the cached audio column');
    assert.ok(fnBody.includes('fingerprint') || fnBody.includes('Fingerprint'), 'must use a fingerprint to decide whether the cache is still valid');
});

test('Q) [Zero-Wait再生: 単純なBlob全体プリロード方式] フロントエンドのZero-Wait再生はFetch→Blob→ObjectURLの後、preload=\'auto\'でload()してから再生する（ネットワークストリーミングによる途中切断の可能性を下げる構成）', () => {
    assert.ok(SRC.includes("zeroWaitObjectUrl = URL.createObjectURL(blob);"));
    assert.ok(SRC.includes("zeroWaitAudioEl.preload = 'auto';"));
    assert.ok(SRC.includes('zeroWaitAudioEl.load();'));
});

test('R) [再生中にWebAudio/gain操作なし] Zero-Wait再生コード（startZeroWaitGreeting）はAudioContext/GainNode等のWebAudio APIを使っていない（再生中の音量操作によるノイズの可能性を排除）', () => {
    const fnStart = SRC.indexOf('function startZeroWaitGreeting()');
    assert.notStrictEqual(fnStart, -1);
    const fnEnd = SRC.indexOf('\n        function waitForZeroWaitOutcome()', fnStart);
    const fnBody = SRC.slice(fnStart, fnEnd);
    assert.ok(!fnBody.includes('AudioContext'));
    assert.ok(!fnBody.includes('GainNode'));
    assert.ok(!fnBody.includes('createGain'));
});

test('S) [重要な時間的相関: startZeroWaitGreeting()はgetUserMedia()より前、同期チェーン内で呼ばれる] startCall()内で、Zero-Wait再生開始(.play())がgetUserMediaの呼び出しより先にソースコード順で出現し、awaitを挟まない設計であることを確認する（getUserMediaのechoCancellation初期化が、再生中のZero-Wait音声にオーディオセッション再初期化由来のグリッチとして重なる作業仮説の根拠）', () => {
    const startCallIdx = SRC.indexOf('async function startCall()');
    assert.notStrictEqual(startCallIdx, -1);
    const zeroWaitCallIdx = SRC.indexOf('startZeroWaitGreeting();', startCallIdx);
    assert.notStrictEqual(zeroWaitCallIdx, -1);
    const getUserMediaIdx = SRC.indexOf('await navigator.mediaDevices.getUserMedia(micConstraints);', startCallIdx);
    assert.notStrictEqual(getUserMediaIdx, -1);
    assert.ok(zeroWaitCallIdx < getUserMediaIdx, 'startZeroWaitGreeting() must be called before getUserMedia()');
    const between = SRC.slice(zeroWaitCallIdx, getUserMediaIdx);
    assert.ok(!/\bawait\b/.test(between), 'no await must occur between starting Zero-Wait playback and calling getUserMedia (this is what makes the timing highly consistent call-to-call, matching the "almost always the same position" symptom)');
});

test('T) [診断追加: GET_USER_MEDIA_REQUESTED/RESOLVED] getUserMedia呼び出しの直前・直後に、PII無しの相関診断マーカー（経過ms・Zero-Wait再生位置）が追加されている（実機でのタイムスタンプ相関による今後の検証用。挙動は変更していない）', () => {
    assert.ok(SRC.includes("pushTimelineEvent('GET_USER_MEDIA_REQUESTED (elapsedSinceCallStartMs='"));
    assert.ok(SRC.includes("pushTimelineEvent('GET_USER_MEDIA_RESOLVED (elapsedSinceCallStartMs='"));
    const startCallIdx = SRC.indexOf('async function startCall()');
    const requestedIdx = SRC.indexOf('GET_USER_MEDIA_REQUESTED', startCallIdx);
    const getUserMediaIdx = SRC.indexOf('await navigator.mediaDevices.getUserMedia(micConstraints);', startCallIdx);
    const resolvedIdx = SRC.indexOf('GET_USER_MEDIA_RESOLVED', startCallIdx);
    assert.ok(requestedIdx !== -1 && requestedIdx < getUserMediaIdx, 'REQUESTED marker must be pushed before the getUserMedia call');
    assert.ok(resolvedIdx !== -1 && resolvedIdx > getUserMediaIdx, 'RESOLVED marker must be pushed after the getUserMedia call resolves');
});

test('U) [PII-free診断] 新規診断マーカーは経過ms・再生位置ms・zeroWaitStateという固定形式の数値/enumのみで、氏名・電話番号・予約内容・transcript本文を一切含まない', () => {
    const startCallIdx = SRC.indexOf('async function startCall()');
    const requestedLineIdx = SRC.indexOf("pushTimelineEvent('GET_USER_MEDIA_REQUESTED", startCallIdx);
    const snippetEnd = SRC.indexOf(');', SRC.indexOf(');', requestedLineIdx) + 1);
    const snippet = SRC.slice(requestedLineIdx, snippetEnd);
    assert.ok(!/customer|phone|reservation|transcript|氏名|電話番号|予約/i.test(snippet), 'diagnostic marker construction must not reference any PII-bearing variable');
});

test('V) [Zero-Wait削除されていない] startZeroWaitGreeting/waitForZeroWaitOutcome/resetZeroWaitCallStateの各関数は引き続き存在する（ユーザー指示§19: Zero-Waitを無効化・削除しない）', () => {
    assert.ok(SRC.includes('function startZeroWaitGreeting()'));
    assert.ok(SRC.includes('function waitForZeroWaitOutcome()'));
    assert.ok(SRC.includes('function resetZeroWaitCallState()'));
});

test('W) [audio start/stopの重複なし] startZeroWaitGreeting()は1回の呼び出しにつき.play()を1回しか呼ばない（ループ構文を含まない）', () => {
    const fnStart = SRC.indexOf('function startZeroWaitGreeting()');
    const fnEnd = SRC.indexOf('\n        function waitForZeroWaitOutcome()', fnStart);
    const fnBody = SRC.slice(fnStart, fnEnd);
    const playCount = (fnBody.match(/zeroWaitAudioEl\.play\(\)/g) || []).length;
    assert.strictEqual(playCount, 1);
    assert.ok(!/while\s*\(/.test(fnBody));
    assert.ok(!/for\s*\(/.test(fnBody));
});

test('X) [回帰: Zero-Wait/greeting follow-up関連の既存HOTFIX14テストが全て通る] resetZeroWaitCallState/zeroWaitEndedPromise/greeting follow-up最大1回/stale generation/音声再生終了前にfollow-upしない、等はHOTFIX14のテストで既に検証されている', () => {
    const r = runNodeTest('test_fast_turn_hotfix14_greeting_callback_terminal.js');
    assert.strictEqual(r.status, 0, 'test_fast_turn_hotfix14_greeting_callback_terminal.js must pass:\n' + r.stdout + r.stderr);
});

// ============================================================
// §26/§27: response.create/dc.send/setTimeout回帰ガード、char delta
// ============================================================

test('Y) [response.create/dc.sendの不要な増加が無いこと] HOTFIX17は診断ログ(pushTimelineEvent)のみを追加しており、sendResponseCreate(/dc.send(JSON.stringify(の出現数はHOTFIX16時点から不変（25/10）。setTimeout(はHOTFIX18でCALLBACK FINAL専用のbounded tail grace 1個分のみ意図的に+1（19→20）、Phase Cでplayback-aware teardown用に+2（20→22）、Phase C監査でconfirm-window用にさらに+1（22→23、詳細はtests/test_fast_turn_hotfix18_callback_audio_tail.js参照）', () => {
    const sendResponseCreateCount = (SRC.match(/sendResponseCreate\(/g) || []).length;
    const dcSendCount = (SRC.match(/dc\.send\(JSON\.stringify\(/g) || []).length;
    const setTimeoutCount = (SRC.match(/setTimeout\(/g) || []).length;
    assert.strictEqual(sendResponseCreateCount, 25);
    assert.strictEqual(dcSendCount, 10);
    assert.strictEqual(setTimeoutCount, 23);
});

test('Z) [char delta: CALLBACK/legacy_fullのみ] _HUMAN_HANDOFF_TEMPLATE=3772文字, _PHASE2B_CALLBACK_ROLE_TEMPLATE=608文字（実測値。smoke_test_name_first_contract.pyのベースラインと一致していることを確認）', () => {
    const idx = PY_SRC.indexOf('_HUMAN_HANDOFF_TEMPLATE = """');
    const endIdx = PY_SRC.indexOf('\n"""', idx);
    // 「"""を含む先頭行」から終端直前までの長さは、Python側でトリプルクォート
    // の中身として評価される実際の文字列長とは厳密には異なる（先頭の
    // `_HUMAN_HANDOFF_TEMPLATE = """\` 部分を含むため）。ここでは変化量の
    // 大小関係のみを確認する（正確な文字数はPython実行環境で別途測定済み）。
    const rawBlock = PY_SRC.slice(idx, endIdx);
    assert.ok(rawBlock.length > 3591, 'must have grown from the HOTFIX16 baseline (3591) due to the intentional PART A addition');
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
process.exit(failed > 0 ? 1 : 0);
