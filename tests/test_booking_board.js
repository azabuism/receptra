// Owner Booking Board V1 / V1.1 (Owner Daily Operations UX Polish): 純粋関数
// （日付計算・座標変換・時計ラベル・衝突配置アルゴリズム・現在時刻/次の予約の
// 判定）のvm-based検証。frontend/public/shop-manage.htmlの実装コードをそのまま
// 抽出して実行する（既存のtest_shop_hours_ux.jsと同じextractFunctionSource方式。
// DOM操作を含む関数（boardShowTab, loadBoard, _boardRenderTimeline等）は対象外
// ——それらはブラウザでの手動確認・本番でのread-only確認で担保する）。
//
// V1.1 Section3の最重要修正の検証: 内部のsession-relative座標
// （_boardMinutesCoord系）は24を超えて増え続ける値のまま一切変更していないが、
// オーナー向け表示ラベル（_boardWallClockLabel/_boardWallHourLabel）は
// 24:00/25:00という延長表記を一切出さず、必ず0-23時の普通の時計表記に
// 折り返すことを確認する。
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
    '_boardParseDateParts', '_boardAddDays', '_boardFormatDateLabel', '_boardFormatShortDateLabel',
    '_boardParseNaiveDateTime', '_boardMinutesCoord', '_boardMinutesCoordFromIso', '_boardCoordToParts',
    '_boardWallClockLabel', '_boardWallHourLabel', '_boardBuildHourMarks',
    '_boardComputeNextDayBoundaryOffset', '_boardFormatHoursRangeLabel', '_boardStripLeadingZeroHour',
    '_boardComputeLayout', '_boardFindNextReservation', '_boardComputeTimelineTimeState',
    '_boardIsViewingToday',
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

// ===== Section45-A/B/C/D/E/F/G: Pure Helper Tests =====

// A. same-day time label
assertEq('A. same-day time label (9:05, no leading zero on hour)', context._boardWallClockLabel(
    context._boardMinutesCoord({ y: 2026, mo: 9, d: 24, h: 9, mi: 5 })
), '9:05');
assertEq('A. same-day time label (18:00)', context._boardWallClockLabel(
    context._boardMinutesCoord({ y: 2026, mo: 9, d: 24, h: 18, mi: 0 })
), '18:00');

// B. overnight time label: 翌日01:00は "1:00"（"25:00"ではない）
const startCoordB = context._boardMinutesCoordFromIso('2026-09-28T18:00:00');
const overnightCoordB = context._boardMinutesCoordFromIso('2026-09-29T01:00:00');
assertEq('B. overnight time label is wall-clock (1:00, not 25:00)', context._boardWallClockLabel(overnightCoordB), '1:00');

// C. session minute 420 (18:00開始+7h=翌1:00) -> "1:00"
assertEq('C. session minute 420 from 18:00 start -> 1:00', context._boardWallClockLabel(startCoordB + 420), '1:00');

// D. midnight boundary: 開始18:00, 総540分(翌03:00まで) -> 境界offsetは360分後(0:00)
assertEq('D. midnight boundary offset (18:00開始, 540分中)', context._boardComputeNextDayBoundaryOffset({ h: 18, mi: 0 }, 540), 360);
// 開始が0:00ちょうどの場合は境界なし
assertEq('D. no boundary when session starts exactly at 0:00', context._boardComputeNextDayBoundaryOffset({ h: 0, mi: 0 }, 540), null);
// 境界がセッション範囲を超える場合はnull（例: 10:00開始、300分＝15:00までなら日跨ぎしない）
assertEq('D. no boundary when session never crosses midnight', context._boardComputeNextDayBoundaryOffset({ h: 10, mi: 0 }, 300), null);

// E. next-day separator label
assertEq('E. short date label (no year)', context._boardFormatShortDateLabel('2026-09-25'), '9月25日（金）');

// F. overnight summary: "18:00〜翌3:00"（"18:00〜27:00"は禁止）
assertEq('F. overnight hours range label', context._boardFormatHoursRangeLabel('18:00', '03:00', true), '18:00〜翌3:00');
assertEq('F. same-day hours range label (no 翌)', context._boardFormatHoursRangeLabel('10:00', '19:00', false), '10:00〜19:00');

// G. reservation start/end display: 内部座標はそのまま(25:30相当)でも表示は"1:30〜3:00"
const resStartCoord = context._boardMinutesCoordFromIso('2026-09-29T01:30:00'); // 月曜18:00開始セッションの翌1:30
const resEndCoord = resStartCoord + 90;
assertEq('G. reservation display end label wraps correctly (1:30+90min -> 3:00)', context._boardWallClockLabel(resEndCoord), '3:00');
assertEq('G. reservation display start label', context._boardWallClockLabel(resStartCoord), '1:30');

// _boardStripLeadingZeroHour（休憩時間表示用）
assertEq('stripLeadingZeroHour "00:30" -> "0:30"', context._boardStripLeadingZeroHour('00:30'), '0:30');
assertEq('stripLeadingZeroHour "18:00" -> "18:00" (unchanged)', context._boardStripLeadingZeroHour('18:00'), '18:00');

// ===== _boardBuildHourMarks: ラベルは常に24で折り返す（延長表記を出さない） =====
const marksOnHour = context._boardBuildHourMarks({ h: 18, mi: 0 }, 540);
assertEq('hourMarks 正時開始: 最初のマークはoffset0, 18:00', marksOnHour[0], { offset: 0, label: '18:00' });
assertEq('hourMarks 正時開始: 最後のマークは540分後, ラベルは"3:00"（"27:00"ではない）', marksOnHour[marksOnHour.length - 1], { offset: 540, label: '3:00' });
assertEq('hourMarks 正時開始: マーク数', marksOnHour.length, 10); // 18,19,...,26(内部) = 10個、ラベルは折り返し済み
// 深夜0時のマークも含まれ、ラベルは"0:00"
const midnightMark = marksOnHour.find((m) => m.offset === 360);
assertEq('hourMarks: 深夜0時のマークはラベル"0:00"', midnightMark, { offset: 360, label: '0:00' });

// 開始が正時でない場合(18:30開始)は、最初のマークは次の正時(19:00, 30分後)から
const marksOffHour = context._boardBuildHourMarks({ h: 18, mi: 30 }, 510);
assertEq('hourMarks 半端開始: 最初のマークは次の正時(30分後)', marksOffHour[0], { offset: 30, label: '19:00' });

// ===== _boardComputeLayout（列ベース衝突配置。V1.1でもアルゴリズム自体は不変） =====
// Section47: 1/2/3/5件の重複 + 境界一致 + overnight座標での重複
const layout1 = context._boardComputeLayout([{ id: 'a', _startOffset: 0, _endOffset: 60 }]);
assertEq('47A. 1件のみ: colCount=1', layout1[0]._colCount, 1);

const layout2 = context._boardComputeLayout([
    { id: 'a', _startOffset: 0, _endOffset: 90 },
    { id: 'b', _startOffset: 30, _endOffset: 90 },
]);
assertEq('47B. 2件重複: colCountは2', layout2.find((x) => x.id === 'a')._colCount, 2);

const layout3 = context._boardComputeLayout([
    { id: 'a', _startOffset: 0, _endOffset: 90 },
    { id: 'b', _startOffset: 10, _endOffset: 90 },
    { id: 'c', _startOffset: 20, _endOffset: 90 },
]);
const cols3 = ['a', 'b', 'c'].map((id) => layout3.find((x) => x.id === id)._col).sort();
assertEq('47C. 3件完全重複: 3つの異なる列', cols3, [0, 1, 2]);
assertEq('47C. 3件完全重複: colCountは3', layout3.find((x) => x.id === 'a')._colCount, 3);

const layout5 = context._boardComputeLayout([
    { id: 'a', _startOffset: 0, _endOffset: 60 },
    { id: 'b', _startOffset: 0, _endOffset: 60 },
    { id: 'c', _startOffset: 0, _endOffset: 60 },
    { id: 'd', _startOffset: 0, _endOffset: 60 },
    { id: 'e', _startOffset: 0, _endOffset: 60 },
]);
assertEq('47D. 5件完全重複: colCountは5', layout5[0]._colCount, 5);
const cols5 = layout5.map((x) => x._col).sort();
assertEq('47D. 5件完全重複: 5つの異なる列', cols5, [0, 1, 2, 3, 4]);

const layoutTouch = context._boardComputeLayout([
    { id: 'a', _startOffset: 0, _endOffset: 60 },
    { id: 'b', _startOffset: 60, _endOffset: 120 },
]);
assertEq('47E. 境界一致(触れるだけ)は重なりとみなさず同じ列を共有', [layoutTouch.find((x) => x.id === 'a')._col, layoutTouch.find((x) => x.id === 'b')._col], [0, 0]);
assertEq('47E. 境界一致: colCountは1', layoutTouch.find((x) => x.id === 'a')._colCount, 1);

// 47F. overnight overlap: session-relative offsetが400台(=深夜帯)の2件が重複
const layoutOvernight = context._boardComputeLayout([
    { id: 'a', _startOffset: 400, _endOffset: 460 }, // 18:00開始セッションの翌0:40〜1:40相当
    { id: 'b', _startOffset: 430, _endOffset: 490 },
]);
assertEq('47F. overnight座標(400台)でも重複判定は正しく機能する', layoutOvernight.find((x) => x.id === 'a')._colCount, 2);

const layout4 = context._boardComputeLayout([
    { id: 'a', _startOffset: 0, _endOffset: 60 },
    { id: 'b', _startOffset: 30, _endOffset: 90 },
    { id: 'c', _startOffset: 90, _endOffset: 150 },
]);
assertEq('layout 部分重複: colCountは2（a,bのみ同時に重なる）', layout4.find((x) => x.id === 'a')._colCount, 2);
assertEq('layout 部分重複: cはaの列を再利用できる(col=0)', layout4.find((x) => x.id === 'c')._col, 0);

// ===== Section45-H/I, 46: _boardFindNextReservation / _boardComputeTimelineTimeState =====

// H. next reservation calculation
const nextResItems = [
    { id: 'past', status: 'confirmed', _absStart: 100 },
    { id: 'future1', status: 'confirmed', _absStart: 200 },
    { id: 'future2', status: 'confirmed', _absStart: 300 },
];
assertEq('45H. 次の予約は最も早いfuture', context._boardFindNextReservation(nextResItems, 150).id, 'future1');
assertEq('45H. nowと完全一致(>=)の予約も対象', context._boardFindNextReservation(nextResItems, 200).id, 'future1');
assertEq('45H. 該当なしはnull', context._boardFindNextReservation(nextResItems, 350), null);

// I. cancelled exclusion（next reservation計算から除外）
const withCancelled = [
    { id: 'cancelled-first', status: 'cancelled', _absStart: 150 },
    { id: 'active-next', status: 'confirmed', _absStart: 200 },
];
assertEq('45I. cancelledは次の予約の候補から除外される', context._boardFindNextReservation(withCancelled, 100).id, 'active-next');

// 46. Current Time Tests (A-G): _boardComputeTimelineTimeState
const baseOpts = {
    viewedDate: '2026-09-28', todayDate: '2026-09-28',
    sessionStartCoord: 1000, sessionEndCoord: 1540, // 540分のセッション
    reservations: [{ id: 'r1', status: 'confirmed', _absStart: 1200 }],
};

// A. today daytime session（営業時間内）
const stateA = context._boardComputeTimelineTimeState(Object.assign({}, baseOpts, { nowCoord: 1100 }));
assertEq('46A. 営業時間内: nowOffsetが計算される', stateA.nowOffset, 100);
assertEq('46A. 営業時間内: autoScrollTargetはnow', stateA.autoScrollTarget.type, 'now');
assertEq('46A. 営業時間内: nextReservationが正しく見つかる', stateA.nextReservation.id, 'r1');

// B. today before opening（開店前、予約あり -> 最初の予約へscroll）
const stateB = context._boardComputeTimelineTimeState(Object.assign({}, baseOpts, { nowCoord: 900 }));
assertEq('46B. 開店前: nowOffsetはnull', stateB.nowOffset, null);
assertEq('46B. 開店前: autoScrollTargetは最初の予約', stateB.autoScrollTarget, { type: 'first-reservation', offset: 200 });

// 開店前・予約なし -> 営業開始位置へ
const stateB2 = context._boardComputeTimelineTimeState(Object.assign({}, baseOpts, { nowCoord: 900, reservations: [] }));
assertEq('46B. 開店前・予約なし: autoScrollTargetはstart', stateB2.autoScrollTarget, { type: 'start', offset: 0 });

// C. today after close（閉店後、予約あり -> 最後の予約付近へscroll）
const stateC = context._boardComputeTimelineTimeState(Object.assign({}, baseOpts, { nowCoord: 1600 }));
assertEq('46C. 閉店後: nowOffsetはnull', stateC.nowOffset, null);
assertEq('46C. 閉店後: autoScrollTargetは最後の予約', stateC.autoScrollTarget, { type: 'last-reservation', offset: 200 });

// 閉店後・予約なし -> timeline末尾へ
const stateC2 = context._boardComputeTimelineTimeState(Object.assign({}, baseOpts, { nowCoord: 1600, reservations: [] }));
assertEq('46C. 閉店後・予約なし: autoScrollTargetはend', stateC2.autoScrollTarget, { type: 'end', offset: 540 });

// D. overnight session before midnight（深夜0時より前の時間帯を見ている）
const overnightOpts = Object.assign({}, baseOpts, { sessionStartCoord: 1000, sessionEndCoord: 1540 });
const stateD = context._boardComputeTimelineTimeState(Object.assign({}, overnightOpts, { nowCoord: 1050 }));
assertEq('46D. overnight session (深夜前): 営業時間内として扱われる', stateD.nowOffset, 50);

// E. overnight session after midnight（実際のoffsetが400台=深夜帯でも正しく機能）
const stateE = context._boardComputeTimelineTimeState(Object.assign({}, overnightOpts, { nowCoord: 1450 }));
assertEq('46E. overnight session (深夜後/翌日側): 営業時間内として扱われる', stateE.nowOffset, 450);

// F. viewing past date（過去日付を見ている場合はnowOffset/nextReservation/autoScrollすべてnull）
const stateF = context._boardComputeTimelineTimeState(Object.assign({}, baseOpts, { viewedDate: '2026-09-27', nowCoord: 1100 }));
assertEq('46F. 過去日付: isTodayはfalse', stateF.isToday, false);
assertEq('46F. 過去日付: nowOffsetはnull', stateF.nowOffset, null);
assertEq('46F. 過去日付: nextReservationはnull', stateF.nextReservation, null);
assertEq('46F. 過去日付: autoScrollTargetはnull', stateF.autoScrollTarget, null);

// G. viewing future date（同様にすべてnull）
const stateG = context._boardComputeTimelineTimeState(Object.assign({}, baseOpts, { viewedDate: '2026-09-29', nowCoord: 1100 }));
assertEq('46G. 未来日付: isTodayはfalse、すべてnull', [stateG.isToday, stateG.nowOffset, stateG.nextReservation, stateG.autoScrollTarget], [false, null, null, null]);

console.log('\n' + (failures === 0 ? 'ALL BOOKING BOARD JS CHECKS PASSED' : failures + ' FAILURES'));
process.exit(failures === 0 ? 0 : 1);
