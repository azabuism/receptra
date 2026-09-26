'use strict';

// RECEPTRA — PHASE O1: INDUSTRY ADAPTIVE OWNER UX — regression test suite
//
// 背景: frontend/public/shop-manage.html の「テーブル・席の管理」カードが、
// 業種（Shop.business_type/category）に関わらず全店舗に無条件表示されており、
// 美容室・整体院・クリニック等のオーナーにも飲食店用の「テーブル・席」UIが
// 出てしまっていた。本フェーズでは、
//   - normalizeBusinessType()  … 実際に監査した3系統の値表現
//       （taxonomy業種キー／旧カテゴリー大文字値／未知値）を単一の正規値へ変換
//   - INDUSTRY_UI_CONFIG / resolveIndustryUIConfig() … 業種ごとのテーブル表示
//       可否・予約リソースの自然な名称・新規追加時のデフォルトresource_typeを
//       一元管理する中央mapping（if (businessType === 'beauty') ... をHTML各所に
//       散らさないため）
//   - applyIndustryUIConfig() / applyResourceTypeDefault() … 上記設定を実際の
//       DOM（テーブルカードの表示/非表示、リソースカードの見出し、リソース
//       種別selectの初期値）へ反映する副作用関数
// を追加した。ShopTable/Resourceのバックエンド・DBスキーマ・既存データは
// 一切変更していない（UIの表示/非表示・文言のみ）。
//
// このテストは、shop-manage.html内の実際のソースをHTMLファイルから直接抽出し、
// Node.jsのvmモジュール上で実行することで、「本物の実装コード」に対して
// アサーションを行う（tests/test_shop_hours_ux.js等と同じ方式）。
// normalizeBusinessType/resolveIndustryUIConfigはDOMに触れない純粋関数のため
// モック不要。applyIndustryUIConfig/applyResourceTypeDefaultはdocument.
// getElementById()を呼ぶため、最小限のDOM要素スタブを与える。
//
// 実行: node tests/test_industry_adaptive_owner_ui.js

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const HTML_PATH = path.join(__dirname, '..', 'frontend', 'public', 'shop-manage.html');
const HTML = fs.readFileSync(HTML_PATH, 'utf8');

const scriptMatch = HTML.match(/<script>([\s\S]*?)<\/script>/);
if (!scriptMatch) throw new Error('inline <script> block not found in shop-manage.html');
const SRC = scriptMatch[1];

// KNOWN_BUSINESS_TYPES 〜 applyIndustryUIConfig() までを一括りのソース片として
// 抽出する（この並びで連続して定義されているため、開始トークンから次の既存
// コード（if (!token || !shopId) {）の直前までを丸ごと切り出せば十分）。
const START_TOKEN = 'const KNOWN_BUSINESS_TYPES';
const END_TOKEN = 'if (!token || !shopId) {';
const startIdx = SRC.indexOf(START_TOKEN);
const endIdx = SRC.indexOf(END_TOKEN);
if (startIdx === -1) throw new Error('KNOWN_BUSINESS_TYPES not found in source');
if (endIdx === -1 || endIdx <= startIdx) throw new Error('could not bound the industry-config source block');
const CONFIG_BLOCK_SRC = SRC.slice(startIdx, endIdx);

// ---- 回帰防止（重要）: HTML側にid付与し忘れていないかのソースレベル確認 ----
assert.ok(HTML.includes('<div class="card" id="table-card">'),
    'the table card must carry id="table-card" so applyIndustryUIConfig() can toggle it');
assert.ok(HTML.includes('<h2 id="resource-card-title">予約リソースの管理</h2>'),
    'the resource card heading must carry id="resource-card-title"');

function makeFakeElement(initial) {
    return Object.assign({ style: {}, textContent: '', value: '' }, initial || {});
}

function buildSandbox() {
    const elements = {
        'table-card': makeFakeElement({ style: { display: '' } }),
        'resource-card-title': makeFakeElement({ textContent: '予約リソースの管理' }),
        'resource-type': makeFakeElement({ value: 'room' }),
    };
    const document = {
        getElementById: (id) => elements[id] || null,
    };
    const context = { document, console };
    vm.createContext(context);
    vm.runInContext(CONFIG_BLOCK_SRC, context);
    return { ctx: context, elements };
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

console.log('PHASE O1: Industry Adaptive Owner UX regression tests');
console.log('source: ' + HTML_PATH);
console.log('');

// ---------- normalizeBusinessType() ----------

test('normalize A) 現行taxonomy業種キーはそのまま正規値として通る（restaurant/beauty/medical/education/fitness/hotel/entertainment/other）', () => {
    const { ctx } = buildSandbox();
    ['restaurant', 'beauty', 'medical', 'education', 'fitness', 'hotel', 'entertainment', 'other'].forEach((bt) => {
        assert.strictEqual(vm.runInContext('normalizeBusinessType(' + JSON.stringify(bt) + ', null)', ctx), bt);
    });
});

test('normalize B) 旧カテゴリー大文字値（LEGACY_CATEGORY_MAPと同一の値）はbusiness_typeが空でも正しい業種へ変換される', () => {
    const { ctx } = buildSandbox();
    const cases = {
        RESTAURANT: 'restaurant', CAFE: 'restaurant', RAMEN: 'restaurant',
        SUSHI: 'restaurant', IZAKAYA: 'restaurant', BAR: 'restaurant',
        BEAUTY: 'beauty', SALON: 'beauty',
        CLINIC: 'medical',
        OTHER: 'other',
    };
    Object.keys(cases).forEach((legacy) => {
        assert.strictEqual(
            vm.runInContext('normalizeBusinessType(null, ' + JSON.stringify(legacy) + ')', ctx),
            cases[legacy],
            'legacy category ' + legacy + ' must normalize to ' + cases[legacy]
        );
    });
});

test('normalize C) business_typeが正しい既知キーの場合は、categoryの値に関わらずbusiness_type側が優先される', () => {
    const { ctx } = buildSandbox();
    // business_typeが既に taxonomy の正規キー(beauty)なので、旧カテゴリー値の
    // categoryを渡してもbusiness_type側の判定が優先されるべき（実データの
    // 二重解釈で誤動作しないことの確認）。
    assert.strictEqual(vm.runInContext('normalizeBusinessType("beauty", "RESTAURANT")', ctx), 'beauty');
});

test('normalize D) 未知カテゴリー・未知business_typeはotherへ安全にフォールバックする（JSエラーを起こさない）', () => {
    const { ctx } = buildSandbox();
    assert.strictEqual(vm.runInContext('normalizeBusinessType(null, null)', ctx), 'other');
    assert.strictEqual(vm.runInContext('normalizeBusinessType("", "")', ctx), 'other');
    assert.strictEqual(vm.runInContext('normalizeBusinessType("some_unknown_future_type", "謎のカテゴリー")', ctx), 'other');
    assert.strictEqual(vm.runInContext('normalizeBusinessType(undefined, undefined)', ctx), 'other');
});

// ---------- resolveIndustryUIConfig() ----------

test('resolve E) restaurant) テーブルカードが表示対象になる（tableVisible=true）', () => {
    const { ctx } = buildSandbox();
    const config = vm.runInContext('resolveIndustryUIConfig("restaurant", "ラーメン")', ctx);
    assert.strictEqual(config.tableVisible, true);
});

test('resolve F) beauty/medical/education/fitness/hotel/entertainment/other) すべてテーブルカードが非表示対象になる（tableVisible=false）', () => {
    const { ctx } = buildSandbox();
    ['beauty', 'medical', 'education', 'fitness', 'hotel', 'entertainment', 'other'].forEach((bt) => {
        const config = vm.runInContext('resolveIndustryUIConfig(' + JSON.stringify(bt) + ', null)', ctx);
        assert.strictEqual(config.tableVisible, false, bt + ' must hide the table card');
    });
});

test('resolve G) beauty) 通常カテゴリー（ヘアサロン等）のリソース名は「施術席」、デフォルト種別はchair', () => {
    const { ctx } = buildSandbox();
    const config = vm.runInContext('resolveIndustryUIConfig("beauty", "ヘアサロン")', ctx);
    assert.strictEqual(config.resourceLabel, '施術席');
    assert.strictEqual(config.resourceDefaultType, 'chair');
});

test('resolve H) beauty) エステ・脱毛/マッサージ・リラクゼーションは、既存taxonomyのサブカテゴリー名で安全に判定できるため「施術ベッド」/bedへ細分化される（整体・カイロ相当）', () => {
    const { ctx } = buildSandbox();
    ['エステ・脱毛', 'マッサージ・リラクゼーション'].forEach((cat) => {
        const config = vm.runInContext('resolveIndustryUIConfig("beauty", ' + JSON.stringify(cat) + ')', ctx);
        assert.strictEqual(config.resourceLabel, '施術ベッド', cat + ' must resolve to 施術ベッド');
        assert.strictEqual(config.resourceDefaultType, 'bed', cat + ' must default to bed');
    });
});

test('resolve I) medical) リソース名は「診察室」、デフォルト種別はroom（歯科医院等の細分類が未確定でも中立表現を維持）', () => {
    const { ctx } = buildSandbox();
    ['一般クリニック', '歯科医院', '整形外科', undefined, null].forEach((cat) => {
        const config = vm.runInContext('resolveIndustryUIConfig("medical", ' + JSON.stringify(cat || null) + ')', ctx);
        assert.strictEqual(config.resourceLabel, '診察室');
        assert.strictEqual(config.resourceDefaultType, 'room');
    });
});

test('resolve J) education) リソース名は「教室」、デフォルト種別はclassroom', () => {
    const { ctx } = buildSandbox();
    const config = vm.runInContext('resolveIndustryUIConfig("education", "プログラミング")', ctx);
    assert.strictEqual(config.resourceLabel, '教室');
    assert.strictEqual(config.resourceDefaultType, 'classroom');
});

test('resolve K) hotel) リソース名は「客室」、デフォルト種別はroom', () => {
    const { ctx } = buildSandbox();
    const config = vm.runInContext('resolveIndustryUIConfig("hotel", "ビジネスホテル")', ctx);
    assert.strictEqual(config.resourceLabel, '客室');
    assert.strictEqual(config.resourceDefaultType, 'room');
});

test('resolve L) entertainment) カラオケのみ既存taxonomyで安全に判定できるため「ルーム」/karaoke_roomへ細分化。それ以外（ボウリング等）は中立表現「設備・予約枠」のまま', () => {
    const { ctx } = buildSandbox();
    const karaoke = vm.runInContext('resolveIndustryUIConfig("entertainment", "カラオケ")', ctx);
    assert.strictEqual(karaoke.resourceLabel, 'ルーム');
    assert.strictEqual(karaoke.resourceDefaultType, 'karaoke_room');
    const bowling = vm.runInContext('resolveIndustryUIConfig("entertainment", "ボウリング")', ctx);
    assert.strictEqual(bowling.resourceLabel, '設備・予約枠', 'unrelated entertainment subcategories must not be mislabeled as karaoke rooms');
    assert.strictEqual(bowling.resourceDefaultType, 'other');
});

test('resolve M) 未知カテゴリー) fallbackはTable非表示・Resource「設備・予約枠」・JSエラーなし', () => {
    const { ctx } = buildSandbox();
    const config = vm.runInContext('resolveIndustryUIConfig("totally_unknown_business_type", "謎")', ctx);
    assert.strictEqual(config.tableVisible, false);
    assert.strictEqual(config.resourceLabel, '設備・予約枠');
    assert.strictEqual(config.resourceDefaultType, 'other');
});

test('resolve N) resource_typeの値は既存のALLOWED_RESOURCE_TYPES（app/schemas/resource.py）の範囲内にのみ収まっている', () => {
    const { ctx } = buildSandbox();
    const ALLOWED = ['room', 'bed', 'chair', 'vehicle', 'karaoke_room', 'classroom', 'equipment', 'other'];
    const businessTypesAndCategories = [
        ['restaurant', null], ['beauty', null], ['beauty', 'エステ・脱毛'], ['beauty', 'マッサージ・リラクゼーション'],
        ['medical', null], ['education', null], ['fitness', null], ['hotel', null],
        ['entertainment', null], ['entertainment', 'カラオケ'], ['other', null],
    ];
    businessTypesAndCategories.forEach(([bt, cat]) => {
        const config = vm.runInContext('resolveIndustryUIConfig(' + JSON.stringify(bt) + ', ' + JSON.stringify(cat) + ')', ctx);
        assert.ok(ALLOWED.indexOf(config.resourceDefaultType) !== -1,
            'resourceDefaultType "' + config.resourceDefaultType + '" for ' + bt + '/' + cat + ' must be a real backend-accepted resource_type');
    });
});

// ---------- applyIndustryUIConfig() / applyResourceTypeDefault() (DOM副作用) ----------

test('apply O) restaurant) table-cardが表示され(display:block)、resource-card-titleは「設備・予約枠の管理」になる', () => {
    const { ctx, elements } = buildSandbox();
    vm.runInContext('applyIndustryUIConfig("restaurant", "ラーメン")', ctx);
    assert.strictEqual(elements['table-card'].style.display, 'block');
    assert.strictEqual(elements['resource-card-title'].textContent, '設備・予約枠の管理');
});

test('apply P) beauty) table-cardが非表示になり(display:none)、resource-card-titleは「施術席の管理」、resource-typeの初期値がchairになる', () => {
    const { ctx, elements } = buildSandbox();
    vm.runInContext('applyIndustryUIConfig("beauty", "ヘアサロン")', ctx);
    assert.strictEqual(elements['table-card'].style.display, 'none');
    assert.strictEqual(elements['resource-card-title'].textContent, '施術席の管理');
    assert.strictEqual(elements['resource-type'].value, 'chair');
});

test('apply Q) medical) table-cardが非表示になり、resource-card-titleは「診察室の管理」になる', () => {
    const { ctx, elements } = buildSandbox();
    vm.runInContext('applyIndustryUIConfig("medical", "一般クリニック")', ctx);
    assert.strictEqual(elements['table-card'].style.display, 'none');
    assert.strictEqual(elements['resource-card-title'].textContent, '診察室の管理');
});

test('apply R) education) table-cardが非表示になり、resource-card-titleは「教室の管理」になる', () => {
    const { ctx, elements } = buildSandbox();
    vm.runInContext('applyIndustryUIConfig("education", null)', ctx);
    assert.strictEqual(elements['table-card'].style.display, 'none');
    assert.strictEqual(elements['resource-card-title'].textContent, '教室の管理');
});

test('apply S) hotel) table-cardが非表示になり、resource-card-titleは「客室の管理」になる', () => {
    const { ctx, elements } = buildSandbox();
    vm.runInContext('applyIndustryUIConfig("hotel", null)', ctx);
    assert.strictEqual(elements['table-card'].style.display, 'none');
    assert.strictEqual(elements['resource-card-title'].textContent, '客室の管理');
});

test('apply T) 未知カテゴリー) JS例外を投げずにtable非表示・「設備・予約枠の管理」を表示する', () => {
    const { ctx, elements } = buildSandbox();
    assert.doesNotThrow(() => {
        vm.runInContext('applyIndustryUIConfig("some_future_unknown_type", "未知のカテゴリー")', ctx);
    });
    assert.strictEqual(elements['table-card'].style.display, 'none');
    assert.strictEqual(elements['resource-card-title'].textContent, '設備・予約枠の管理');
});

test('apply U) applyResourceTypeDefault) defaultTypeが空/未指定の場合、既存のresource-type選択値を勝手に上書きしない', () => {
    const { ctx, elements } = buildSandbox();
    elements['resource-type'].value = 'vehicle'; // オーナーが既に選択済みの状態を模倣
    vm.runInContext('applyResourceTypeDefault(null)', ctx);
    assert.strictEqual(elements['resource-type'].value, 'vehicle', 'a falsy defaultType must not clobber the existing selection');
    vm.runInContext('applyResourceTypeDefault(undefined)', ctx);
    assert.strictEqual(elements['resource-type'].value, 'vehicle');
});

// ---------- 既存ShopTable/Resourceデータ・APIへの非干渉（回帰防止） ----------

test('regression V) applyIndustryUIConfig/normalizeBusinessType等の新規コードは、fetch/DELETE等のネットワーク呼び出しを一切含まない（表示制御のみで既存データへ副作用がない）', () => {
    assert.ok(!/fetch\s*\(/.test(CONFIG_BLOCK_SRC), 'the industry-config block must not perform any network calls');
    assert.ok(!/DELETE/.test(CONFIG_BLOCK_SRC), 'the industry-config block must never delete anything');
});

test('regression W) table-form/resource-formの既存submit先エンドポイント文字列は変更されていない（ShopTable/Resource APIの挙動は無変更）', () => {
    assert.ok(SRC.includes("shopId) + '/tables'"), 'the ShopTable API path must remain untouched');
    assert.ok(SRC.includes("shopId) + '/resources'"), 'the Resource API path must remain untouched');
});

test('regression X) 予約リソースフォームのresetは維持されたまま、業種デフォルト値の再適用のみが追加されている（既存の追加/一覧再取得フローを変更していない）', () => {
    const idx = SRC.indexOf("document.getElementById('resource-form').reset();");
    assert.notStrictEqual(idx, -1, 'resource-form reset call must still exist');
    const block = SRC.slice(idx, idx + 500);
    assert.ok(block.includes('applyResourceTypeDefault(currentIndustryUIConfig.resourceDefaultType);'));
    assert.ok(block.includes('loadResources();'), 'loadResources() must still be called after adding a resource');
});

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
process.exit(failed > 0 ? 1 : 0);
