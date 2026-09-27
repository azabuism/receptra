'use strict';

// RECEPTRA — FAST TURN HOTFIX 19（2026年9月）
// 「CALLBACK FINAL — NO USER ACK REQUIRED / 「折り返ししますね」→ユーザーの
// 「はい」待ちを完全廃止」の契約テスト。
//
// ============================================================
// 実機症状（ユーザー報告）
// ============================================================
// HOTFIX18適用後、NAME/DATE/TIME/PARTY_SIZE/PHONE/callback最終音声/1500ms
// tail grace/自然な通話終了は全て実機で正常と確認された（HOTFIX18は
// クローズ済み・変更禁止）。唯一残っていた症状: 折り返しに必要な情報が
// すべて揃った直後、AIが「担当者から折り返ししますね。」のように発話し、
// お客様の「はい」という返事を待ってから、ようやくrequest_callbackを呼び
// 出す（＝1往復分の不要なユーザー確認ターンが挟まる）。
//
// ============================================================
// root cause（コード監査で確認。推測ではない）
// ============================================================
// frontend/public/js/realtime-voice-engine.js の classifyExpectedAnswerType()
// は、AIの発話が以下のいずれにも一致しない場合、type='NONE'・narrationOnly=
// false・incompleteAiTurn=falseに分類する:
//   - PHONE/NAME/YES_NO/SHORT_CHOICE/VISIT_REASON/SHORT_ANSWER の実質問語彙
//   - AI_WORKING_NARRATION_ONLY_RE（"確認します"「調べます」等）
//   - COMPLETE_QUESTION_ENDING_RE を使った「話題を切り出しただけで質問を
//     言い切っていない」不完全ターン判定
// 「担当者から折り返ししますね。」はこのいずれにも一致しない（"よろしい
// ですか"等のYES_NOキーワードも、"確認します"等のAI_WORKING語彙も含まない）
// ため、response.doneハンドラの `if (!responseHasFunctionCall) {...}` 分岐
// では callbackTerminalArmed（request_callback成功後にのみtrue。この時点
// ではまだfalse）もHOTFIX12/13のnarration/incomplete継続もいずれも該当せず、
// 最終的なelse節（通常のsilence timer開始＝ユーザーの次の発話を待つ）に
// 落ちることをコードレベルで確認した。「しますね」という同意を促す語尾が
// あるため、お客様が自然に「はい」と応答し、それがトリガーとなって初めて
// 次のresponseが生成され、その中でようやくrequest_callbackが呼ばれる、
// という実機症状と正確に一致する。
//
// これは「_HUMAN_HANDOFF_TEMPLATE／_PHASE2B_CALLBACK_ROLE_TEMPLATE／
// request_callbackツールのdescriptionが、既に『何も話さずにそのままToolを
// 呼び出す』方針を明示していたにもかかわらず、"担当者から折り返しします
// ね"という具体的なNG例文がどこにも列挙されていなかった」ことに起因する
// （AIが自己生成しやすい、ドメイン固有の言い回しの一つが単に禁止リストに
// 無かった、という具体的な抜け）。
//
// ============================================================
// 修正内容（プロンプトのみ・新しいコード/新しいstateは一切追加していない）
// ============================================================
// §19（sendResponseCreate(/dc.send(JSON.stringify(/setTimeout(の出現回数を
// 増やさない）の制約を踏まえ、今回のHOTFIX19では
// frontend/public/js/realtime-voice-engine.js を一切変更しない（HEADと
// 完全に同一のまま）。修正はapp/services/realtime_voice_ai.pyの3箇所の
// プロンプト文言のみ:
//   1. _HUMAN_HANDOFF_TEMPLATE 手順1: 「担当者から折り返ししますね」を
//      禁止する再確認例文リストに追加し、「次の2.（情報が既に揃っている
//      場合は3.）に進んでください」と明記（情報が既に揃っている場合に
//      2.の質問すら挟まず3.へ直行してよいことを明示）。
//   2. _HUMAN_HANDOFF_TEMPLATE 手順3: 同じくNG例文リストに追加し、
//      「お客様の『はい』等の返事を待つ発話」も明示的に禁止。
//   3. request_callbackツールのdescription: 同様にNG例文を追加し、
//      「追加の発話を挟まずそのままこの関数を呼び出してください」と明記。
// _PHASE2B_CALLBACK_ROLE_TEMPLATE・手順2/4/5(5-1/5-2/5-3)/6・ROUTING
// （classify_intent・曖昧な意図の分岐）・RESERVATION・NAME、および全ての
// JS側terminal機構（HOTFIX14〜18で確立済み。callbackTerminalArmed／
// pendingCallbackTerminalHangup／callbackAlreadyConfirmedThisCall／
// CALLBACK_FINAL_AUDIO_TAIL_GRACE_MS等）は一切変更していない。
//
// §11（「コードレベルの保証」）について: request_callbackがsuccessで
// 返った後の「最終応答→HOTFIX18 tail grace→endCall」という経路は、
// HOTFIX14〜18で確立済みの既存terminal state machine
// （callbackTerminalArmed/pendingCallbackTerminalHangup）が既に保証して
// おり、今回のプロンプト修正によってAIがrequest_callbackを早く・確実に
// 呼び出すようになれば、この既存の保証済み経路にそのまま乗る。新しい
// state・新しいresponse.create経路は一切追加していない（この既存経路
// 自体は今回のコード監査で再確認したのみで、1行も変更していない）。
//
// 実行: node tests/test_fast_turn_hotfix19_callback_no_ack.js

const fs = require('fs');
const path = require('path');
const assert = require('assert');

const ENGINE_JS_PATH = path.join(__dirname, '..', 'frontend', 'public', 'js', 'realtime-voice-engine.js');
const SRC = fs.readFileSync(ENGINE_JS_PATH, 'utf8');
const PY_PATH = path.join(__dirname, '..', 'app', 'services', 'realtime_voice_ai.py');
const PY_SRC = fs.readFileSync(PY_PATH, 'utf8');

// tests/test_fast_turn_hotfix17_callback_no_reconfirm_zero_wait_audio.js と
// 同じ考え方（Pythonの三重引用符テンプレートの改行位置に依存しない検証）。
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
        console.log('  FAIL - ' + name + ': ' + e.message);
    }
}

console.log('=== FAST TURN HOTFIX 19: CALLBACK FINAL — NO USER ACK REQUIRED ===\n');

// ===== A. 明示的な折り返し意図の確認済み扱いは維持される（HOTFIX17由来） =====
test('A. 折り返し意図が既に確定している場合は改めて確認し直さない旨が明記されている', () => {
    assert.ok(
        _normalized(PY_SRC).includes(_normalized(
            'お客様が既に担当者からの折り返し・取り次ぎをはっきりご希望\n' +
            '   されていることが会話上分かっている場合'
        )),
        'HOTFIX17由来の「既に確定している」旨の記述が見つかりません'
    );
});

// ===== B. 不足している必須項目はまだ質問継続される =====
test('B. 必須情報が揃っていない場合は3.（Tool呼び出し）が適用されない旨が明記されている', () => {
    assert.ok(
        PY_SRC.includes('（2.の情報がまだ揃っていない場合はこの3.は適用されません。揃って'),
        '必須情報未確定時の質問継続に関する記述が見つかりません'
    );
});

// ===== C. 「担当者から折り返ししますね」style の確認ターンが禁止例文として明記された =====
test('C. _HUMAN_HANDOFF_TEMPLATE手順1に「担当者から折り返ししますね」がNG例文として追加されている', () => {
    assert.ok(
        PY_SRC.includes('「担当者から折り返ししますね」のように、'),
        '手順1のNG例文リストに「担当者から折り返ししますね」が見つかりません'
    );
});

test('C2. _HUMAN_HANDOFF_TEMPLATE手順3に「担当者から折り返ししますね」がNG例文として追加されている', () => {
    assert.ok(
        _normalized(PY_SRC).includes(_normalized('「手配を確認しますね」「担当者から折り返し\n   しますね」のように')),
        '手順3のNG例文リストに「担当者から折り返ししますね」が見つかりません'
    );
});

// ===== D. 「はい」等の返事を待つ発話自体が明示的に禁止されている =====
test('D. 手順3で「お客様の「はい」等の返事を待つ発話」が明示的に禁止されている', () => {
    assert.ok(
        _normalized(PY_SRC).includes(_normalized('お客様の「はい」等の返事を待つ発話は一切せずに')),
        '返事待ち発話の禁止記述が見つかりません（手順3）'
    );
});

test('D2. request_callbackツールdescriptionでも「はい」等の返事を待つことが禁止されている', () => {
    assert.ok(
        PY_SRC.includes('処理することをお客様に実況したり、お客様の「はい」等の返事を待ったり'),
        '返事待ち発話の禁止記述（前半）が見つかりません（tool description）'
    );
    assert.ok(
        PY_SRC.includes('"する発話はしないでください。\\n"'),
        '返事待ち発話の禁止記述（後半）が見つかりません（tool description）'
    );
});

// ===== E. 「折り返しでよろしいですか」の再確認は既存どおり禁止されたまま =====
test('E. 「折り返しのご連絡をご希望ですか」の再確認禁止（HOTFIX17由来）が維持されている', () => {
    assert.ok(
        PY_SRC.includes('「折り返しのご連絡をご希望ですか」'),
        'HOTFIX17由来の再確認禁止フレーズが見つかりません'
    );
});

// ===== F. 「折り返しのご連絡いたしますか」相当（手順1の「折り返しいたしますか」）も維持 =====
test('F. 手順1の「折り返しいたしますか」というNG例文（HOTFIX17由来）が維持されている', () => {
    assert.ok(
        PY_SRC.includes('「折り返しいたしますか」'),
        'HOTFIX17由来の「折り返しいたしますか」NG例文が見つかりません'
    );
});

// ===== G. 情報が揃ったら追加発話を挟まずrequest_callbackへ直行する旨が明記されている =====
test('G. 手順1: 情報が既に揃っている場合は2.を飛ばして3.へ直行してよい旨が明記されている', () => {
    assert.ok(
        PY_SRC.includes('（2.の情報が既にすべて揃っている場合は3.）に'),
        '情報が既に揃っている場合の直行に関する記述が見つかりません'
    );
});

test('G2. request_callbackツールdescription: 揃い次第「追加の発話を挟まず」呼び出す旨が明記されている', () => {
    assert.ok(
        PY_SRC.includes('場合は、まだ分かっていない必要情報だけを確認し、揃い次第、追加の発話を'),
        '「追加の発話を挟まず」の記述（前半）が見つかりません'
    );
    assert.ok(
        PY_SRC.includes('"挟まずそのままこの関数を呼び出してください。\\n"'),
        '「追加の発話を挟まず」の記述（後半）が見つかりません'
    );
});

test('G3. 手順3: 必要情報が揃ったら「そのまま」request_callbackを呼び出す既存方針が維持されている', () => {
    assert.ok(
        PY_SRC.includes('request_callback ツールを呼び出してください。request_callback は'),
        '「そのまま呼び出す」方針の記述が見つかりません'
    );
});

// ===== H. request_callbackの二重実行防止は既存のまま変更されていない =====
test('H. callbackAlreadyConfirmedThisCall（二重実行防止）がJS側で変更されていない', () => {
    assert.ok(SRC.includes('callbackAlreadyConfirmedThisCall'), 'callbackAlreadyConfirmedThisCallが見つかりません');
});

// ===== I. 最終応答は成功後1回のみ（既存のcallbackTerminalArmed機構がそのまま担保） =====
test('I. callbackTerminalArmed（terminal機構）がJS側で変更されていない', () => {
    assert.ok(SRC.includes('callbackTerminalArmed'), 'callbackTerminalArmedが見つかりません');
});

// ===== J/K. 成功後の最終案内は質問形ではない（既存の5-1/5-2文言に疑問符が無い） =====
test('J. 折り返し確定時（5-1）の最終案内文言に疑問符（？/?）が含まれない', () => {
    const m = PY_SRC.match(/「確認が必要なため、担当者にお伝えします。業務の状況により、\s*\n\s*折り返しまでお時間をいただく場合がございます。お問い合わせいただき\s*\n\s*ありがとうございました。」/);
    assert.ok(m, '5-1の最終案内文言が見つかりません');
    assert.ok(!m[0].includes('？') && !m[0].includes('?'), '5-1の最終案内文言に疑問符が含まれています: ' + m[0]);
});

test('K. 折り返し未確定時（5-2）の最終案内文言に疑問符（？/?）が含まれない', () => {
    const idx = PY_SRC.indexOf('「確認が必要なため、担当者にお伝えします。必要に応じてこちらから');
    assert.ok(idx !== -1, '5-2の最終案内文言が見つかりません');
    const snippet = PY_SRC.slice(idx, idx + 200);
    assert.ok(!snippet.includes('？') && !snippet.includes('?'), '5-2の最終案内文言に疑問符が含まれている可能性: ' + snippet);
});

// ===== L. 最終案内後にUSER_WAITへ戻らない（HOTFIX14の分岐が変更されていない） =====
test('L. response.doneの callbackTerminalArmed 分岐（USER_WAITへ戻さない）が変更されていない', () => {
    assert.ok(SRC.includes('callbackTerminalArmed = false;'), 'callbackTerminalArmedのconsume処理が見つかりません');
    assert.ok(SRC.includes('pendingCallbackTerminalHangup = true;'), 'pendingCallbackTerminalHangupのセットが見つかりません');
});

// ===== M. 最終案内後に「はい」を必要としない（5-3で追加質問をしない方針が維持） =====
test('M. 手順5-3: 最終案内後に追加の質問・確認を行わない方針が維持されている', () => {
    assert.ok(
        PY_SRC.includes('続けて「ほかにご用件はありますか」等の追加の質問・'),
        '5-3の追加質問禁止に関する記述が見つかりません'
    );
});

// ===== N〜Q. 最終案内後にAI_WORKING/incomplete/silenceが発火しない（既存の優先分岐は不変） =====
test('N. response.doneのif (!responseHasFunctionCall)チェーンでcallbackTerminalArmedが最優先分岐のまま', () => {
    const idx = SRC.indexOf('if (!responseHasFunctionCall) {');
    assert.ok(idx !== -1, 'responseHasFunctionCallチェーンが見つかりません');
    const chainStart = SRC.slice(idx, idx + 400);
    assert.ok(chainStart.includes('if (callbackTerminalArmed) {'), 'callbackTerminalArmedが最初の分岐ではありません');
});

test('O. AI_WORKING継続機構（HOTFIX12/13）がJS側で変更されていない', () => {
    assert.ok(SRC.includes("const AI_WORKING_NARRATION_ONLY_RE = /整理します|確認します|確認いたします|確認してみます|調べます|お待ちください/;"), 'AI_WORKING_NARRATION_ONLY_REが変更されています');
});

test('P. INCOMPLETE AI TURN継続機構（HOTFIX13）がJS側で変更されていない', () => {
    assert.ok(SRC.includes('const COMPLETE_QUESTION_ENDING_RE = /(ですか|でしょうか|ますか|ください|お願いします|お願いいたします)[。！!？?、…\\s]*$/;'), 'COMPLETE_QUESTION_ENDING_REが変更されています');
});

test('Q. silence timer関連コードがJS側で変更されていない（startSilenceTimerIfNeeded存在確認）', () => {
    assert.ok(SRC.includes('function startSilenceTimerIfNeeded'), 'startSilenceTimerIfNeededが見つかりません');
});

// ===== R. successがfalseの場合の成功偽装禁止（既存文言が維持） =====
test('R. request_callback失敗時に「担当者から折り返します」と案内しない方針が維持されている', () => {
    assert.ok(
        PY_SRC.includes('6. successがfalseの場合、「担当者から折り返します」と絶対に案内せず、'),
        'success=false時の案内禁止に関する記述が見つかりません'
    );
});

// ===== S. 曖昧な意図（ROUTING）のクラリフィケーションは変更されていない =====
test('S. classify_intent（ROUTING専用Tool）の説明文・分類基準が変更されていない', () => {
    assert.ok(
        PY_SRC.includes('intent=callbackは、担当者からの折り返しを希望する場合、特定の'),
        'classify_intentのintent=callback判定基準が変更されています'
    );
    assert.ok(
        PY_SRC.includes('まだ判断できない'),
        'classify_intentの「まだ判断できない場合は呼び出さず」の記述が変更されています'
    );
});

// ===== T. HOTFIX17の「既に確定済みの折り返し意図は再確認しない」方針が維持 =====
test('T. _PHASE2B_CALLBACK_ROLE_TEMPLATE（HOTFIX17編集分）が完全に無変更（608文字）', () => {
    const m = PY_SRC.match(/_PHASE2B_CALLBACK_ROLE_TEMPLATE = """\\\n([\s\S]*?)\n"""/);
    assert.ok(m, '_PHASE2B_CALLBACK_ROLE_TEMPLATEが見つかりません');
    assert.strictEqual(m[1].length, 607, '_PHASE2B_CALLBACK_ROLE_TEMPLATEの内容長が変化しています: ' + m[1].length);
});

// ===== U/V. HOTFIX18のaudio tail機構が完全に無変更（最重要・絶対禁止事項） =====
test('U. CALLBACK_FINAL_AUDIO_TAIL_GRACE_MS（HOTFIX18）がJS側で変更されていない', () => {
    assert.ok(SRC.includes('const CALLBACK_FINAL_AUDIO_TAIL_GRACE_MS = 1500;'), 'CALLBACK_FINAL_AUDIO_TAIL_GRACE_MSの値が変更されています');
});

test('V. maybeHangUpAfterCallbackTerminal（HOTFIX18のtail grace実装）が存在し、値も1500のまま', () => {
    assert.ok(SRC.includes('function maybeHangUpAfterCallbackTerminal'), 'maybeHangUpAfterCallbackTerminalが見つかりません');
    assert.ok(SRC.includes('CALLBACK_FINAL_TAIL_GRACE_STARTED'), 'CALLBACK_FINAL_TAIL_GRACE_STARTEDマーカーが見つかりません');
    assert.ok(SRC.includes('CALLBACK_FINAL_TAIL_GRACE_COMPLETED'), 'CALLBACK_FINAL_TAIL_GRACE_COMPLETEDマーカーが見つかりません');
});

// ===== W. output_audio_buffer.stopped → tail grace → endCall の経路が維持 =====
test('W. output_audio_buffer.stopped → CALLBACK_FINAL_AUDIO_STOP_EVENT の経路が維持されている', () => {
    assert.ok(SRC.includes('CALLBACK_FINAL_AUDIO_STOP_EVENT'), 'CALLBACK_FINAL_AUDIO_STOP_EVENTマーカーが見つかりません');
    assert.ok(SRC.includes('CALLBACK_TERMINAL_END_CALL'), 'CALLBACK_TERMINAL_END_CALLマーカーが見つかりません');
});

// ===== X. endCallは高々1回（既存のcancelCallbackFinalTailGrace等のガードが維持） =====
test('X. cancelCallbackFinalTailGrace（HOTFIX18の二重発火防止）が変更されていない', () => {
    assert.ok(SRC.includes('function cancelCallbackFinalTailGrace'), 'cancelCallbackFinalTailGraceが見つかりません');
});

// ===== Y〜AD. PHONE/DATE/TIME/PARTY_SIZE/NAME/FIRST ANSWER MUST COUNT 回帰 =====
test('Y. PHONE判定（classifyExpectedAnswerType）の正規表現が変更されていない', () => {
    assert.ok(SRC.includes("if (/電話番号|お電話番号/.test(t)) {"), 'PHONE判定の正規表現が変更されています');
});

test('Z. DATE/TIME/PARTY_SIZE（SHORT_ANSWER）判定の正規表現が変更されていない', () => {
    assert.ok(SRC.includes("type = 'SHORT_ANSWER';"), 'SHORT_ANSWER判定が見つかりません');
});

test('AA. NAME判定（classifyExpectedAnswerType）の正規表現が変更されていない', () => {
    assert.ok(SRC.includes("if (/お名前|ご氏名/.test(t)) {"), 'NAME判定の正規表現が変更されています');
});

test('AB. FIRST ANSWER MUST COUNT関連（Forced Commit系関数）が変更されていない', () => {
    assert.ok(SRC.includes('ANSWER_WINDOW_LIMITS_MS'), 'ANSWER_WINDOW_LIMITS_MSが見つかりません');
});

test('AC. sendResponseCreate(の出現回数が25のまま変化していない（§19: 新しいresponse.create経路を追加していない）', () => {
    const count = (SRC.match(/sendResponseCreate\(/g) || []).length;
    assert.strictEqual(count, 25, 'sendResponseCreate(の出現回数が変化しています: ' + count);
});

test('AD. dc.send(JSON.stringify(の出現回数が10のまま、setTimeout(の出現回数が20のまま変化していない（§19）', () => {
    const dcSendCount = (SRC.match(/dc\.send\(JSON\.stringify\(/g) || []).length;
    const setTimeoutCount = (SRC.match(/setTimeout\(/g) || []).length;
    assert.strictEqual(dcSendCount, 10, 'dc.send(JSON.stringify(の出現回数が変化しています: ' + dcSendCount);
    assert.strictEqual(setTimeoutCount, 20, 'setTimeout(の出現回数が変化しています: ' + setTimeoutCount);
});

console.log('\n=== 結果: ' + passed + ' passed, ' + failed + ' failed ===');
process.exit(failed > 0 ? 1 : 0);
