// Owner Booking Board V1: 純粋関数（日付計算・座標変換・衝突配置アルゴリズム）の
// vm-based検証。frontend/public/shop-manage.htmlの実装コードをそのまま抽出して
// 実行する（既存のtest_shop_hours_ux.jsと同じextractFunctionSource方式。
// DOM操作を含む関数（boardShowTab, loadBoard, _boardRenderTimeline等）は対象外
// ——それらはブラウザでの手動確認・本番でのread-only確認で担保する）。
//
// 実行: node tests/test_booking_board.js
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const HTML_PATH = path.join(__dirname, '..', 'frontend', 'public', 'shop-manage.html');
const html = fs.readFileSync(HTML_PATH, 'utf-8');
const scriptMatch = html.match(/<script>([\s\S]*?)<\/script>/);
const SRC = scriptMatch[1];

function extractFunctionSource(src, fnName) {
    const startToken = 'function ' + fnName + '(';
    const startIdx = src.indexOf(startToken);
    if (startIdx === -1) throw new Error('function not found: ' + fnName);
    const braceStart = src.indexOf('{', startIdx);
    let depth = 0, i = braceStart;
    for (; i < src.length; i++) {
        if (src[i] === '{') depth++;
        else if (src[i] === '}') { depth--; if (depth === 0) { i++; break; } }
    }
    return src.slice(startIdx, i);
}
function extractConst(src, name) {
    const re = new RegExp('const\\s+' + name + '\\s*=\\s*([^;]+);');
    const m = src.match(re);
    if (!m) throw new Error('const not found: ' + name);
    return m[1];
}

const context = { console };
vm.createContext(context);

const fnNames = [
    '_boardParseDateParts', '_boardAddDays', '_boardFormatDateLabel',
    '_boardParseNaiveDateTime', '_boardMinutesCoord', '_boardMinutesCoordFromIso',
    '_boardExtendedHourLabel', '_boardBuildHourMarks', '_boardComputeLayout',
    '_boardMinutesToClockLabel',
];
const BOARD_PX_PER_MINUTE_EXPR = extractConst(SRC, 'BOARD_PX_PER_MINUTE');
const WEEKDAY_NAMES_EXPR = extractConst(SRC, 'BOARD_WEEKDAY_NAMES_JA');
const fnSrc = fnNames.map((n) => extractFunctionSource(SRC, n)).join('\n\n');

vm.runInContext(
    'const BOARD_PX_PER_MINUTE = ' + BOARD_PX_PER_MINUTE_EXPR + ';\n' +
    'const BOARD_WEEKDAY_NAMES_JA = ' + WEEKDAY_NAMES_EXPR + ';\n' +
    fnSrc,
    context
);

let failures = 0;
function assertEq(name, actual, expected) {
    const pass = JSON.stringify(actual) === JSON.stringify(expected);
    console.log((pass ? 'PASS' : 'FAIL') + ' - ' + name + (pass ? '' : ` (expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)})`));
    if (!pass) failures++;
}

// ===== _boardParseDateParts / _boardAddDays（月・年またぎ含む） =====
assertEq('parseDateParts', context._boardParseDateParts('2026-09-28'), { y: 2026, mo: 9, d: 28 });
assertEq('addDays +1 (通常)', context._boardAddDays('2026-09-28', 1), '2026-09-29');
assertEq('addDays 月またぎ', context._boardAddDays('2026-09-30', 1), '2026-10-01');
assertEq('addDays 年またぎ', context._boardAddDays('2026-12-31', 1), '2027-01-01');
assertEq('addDays -1', context._boardAddDays('2026-10-01', -1), '2026-09-30');

// ===== _boardFormatDateLabel（既知の曜日で検証。2026-09-24は木曜日） =====
assertEq('formatDateLabel 木曜', context._boardFormatDateLabel('2026-09-24'), '2026年9月24日（木）');
assertEq('formatDateLabel 日曜', context._boardFormatDateLabel('2026-09-27'), '2026年9月27日（日）');

// ===== _boardParseNaiveDateTime / _boardMinutesCoord(FromIso) =====
assertEq('parseNaiveDateTime', context._boardParseNaiveDateTime('2026-09-28T18:00:00'),
    { y: 2026, mo: 9, d: 28, h: 18, mi: 0 });

const startCoord = context._boardMinutesCoordFromIso('2026-09-28T18:00:00');
const nextDayCoord = context._boardMinutesCoordFromIso('2026-09-29T01:00:00');
assertEq('翌日01:00は開始(18:00)から420分後', nextDayCoord - startCoord, 420);

// ===== _boardExtendedHourLabel（24時以降も折り返さない） =====
assertEq('extendedHourLabel 18時', context._boardExtendedHourLabel(18), '18:00');
assertEq('extendedHourLabel 25時（日跨ぎ延長表記）', context._boardExtendedHourLabel(25), '25:00');

// ===== _boardBuildHourMarks =====
// 開始18:00、翌03:00まで(9時間=540分)の場合、18,19,...,26,27の刻み
// (27:00は540分ちょうどなので含まれる)
const marksOnHour = context._boardBuildHourMarks({ h: 18, mi: 0 }, 540);
assertEq('hourMarks 正時開始: 最初のマークはoffset0', marksOnHour[0], { offset: 0, label: '18:00' });
assertEq('hourMarks 正時開始: 最後のマークは27:00(540分)', marksOnHour[marksOnHour.length - 1], { offset: 540, label: '27:00' });
assertEq('hourMarks 正時開始: マーク数', marksOnHour.length, 10); // 18,19,...,27 = 10個

// 開始が正時でない場合(18:30開始)は、最初のマークは次の正時(19:00, 30分後)から
const marksOffHour = context._boardBuildHourMarks({ h: 18, mi: 30 }, 510);
assertEq('hourMarks 半端開始: 最初のマークは次の正時(30分後)', marksOffHour[0], { offset: 30, label: '19:00' });

// ===== _boardMinutesToClockLabel =====
assertEq('minutesToClockLabel', context._boardMinutesToClockLabel(startCoord + 60), '19:00');

// ===== _boardComputeLayout（列ベース衝突配置） =====
// ケース1: 完全に独立した2件 -> それぞれ1列(colCount=1)
const layout1 = context._boardComputeLayout([
    { id: 'a', _startOffset: 0, _endOffset: 60 },
    { id: 'b', _startOffset: 120, _endOffset: 180 },
]);
assertEq('layout 独立2件: aのcolCount', layout1.find((x) => x.id === 'a')._colCount, 1);
assertEq('layout 独立2件: bのcolCount', layout1.find((x) => x.id === 'b')._colCount, 1);

// ケース2: 触れるだけ(境界一致)は重なりとみなさず同じ列を共有できる
const layoutTouch = context._boardComputeLayout([
    { id: 'a', _startOffset: 0, _endOffset: 60 },
    { id: 'b', _startOffset: 60, _endOffset: 120 },
]);
assertEq('layout 境界一致: aとbは同じ列(col=0)', [layoutTouch.find((x) => x.id === 'a')._col, layoutTouch.find((x) => x.id === 'b')._col], [0, 0]);
assertEq('layout 境界一致: colCountは1', layoutTouch.find((x) => x.id === 'a')._colCount, 1);

// ケース3: 完全に重なる3件 -> 3列必要
const layout3 = context._boardComputeLayout([
    { id: 'a', _startOffset: 0, _endOffset: 90 },
    { id: 'b', _startOffset: 10, _endOffset: 90 },
    { id: 'c', _startOffset: 20, _endOffset: 90 },
]);
const cols3 = ['a', 'b', 'c'].map((id) => layout3.find((x) => x.id === id)._col).sort();
assertEq('layout 3件完全重複: 3つの異なる列', cols3, [0, 1, 2]);
assertEq('layout 3件完全重複: colCountは3', layout3.find((x) => x.id === 'a')._colCount, 3);

// ケース4: 部分的な重なり（a,bが重なり、bが終わった後にcが始まる=列を再利用できる）
const layout4 = context._boardComputeLayout([
    { id: 'a', _startOffset: 0, _endOffset: 60 },
    { id: 'b', _startOffset: 30, _endOffset: 90 },
    { id: 'c', _startOffset: 90, _endOffset: 150 },
]);
assertEq('layout 部分重複: colCountは2（a,bのみ同時に重なる）', layout4.find((x) => x.id === 'a')._colCount, 2);
assertEq('layout 部分重複: cはaの列を再利用できる(col=0)', layout4.find((x) => x.id === 'c')._col, 0);

console.log('\n' + (failures === 0 ? 'ALL BOOKING BOARD JS CHECKS PASSED' : failures + ' FAILURES'));
process.exit(failures === 0 ? 0 : 1);
