/*
 * Phase O4: Owner Console shared navigation shell.
 *
 * Section63/section "don't break existing pages, integrate gradually":
 * this is a SMALL, ADDITIVE chip-row component, not a full-page rewrite.
 * Each existing Owner page keeps its own <nav>/layout completely intact;
 * this component is injected into one new, empty container div that a
 * page opts into, and every link here points at a page/anchor that
 * already exists (or, for "ページ", explicitly does not exist yet -- see
 * Section71/72/125/185, rendered as a disabled "準備中" chip rather than
 * a broken or fabricated link).
 *
 * Section1-9: the full target Owner Console IA --
 * ホーム/注文/予約/顧客/メニュー/在庫/原材料・レシピ/スタッフ/ページ/
 * アクセス分析/店舗設定 -- expressed here as plain links, several of
 * which point at anchor ids (#id) inside the existing, unmodified
 * shop-manage.html long-scroll page (today-ops-card, commerce-orders-card,
 * reservations-card, customers-card, menu-card, service-staff-card,
 * business-settings-card), so no existing page needed to be restructured
 * into tabs for this phase.
 *
 * IMPORTANT: shop-manage.html's own URL convention uses ?id=, NOT
 * ?shop_id= (confirmed by reading its own `params.get('id')` line) --
 * every OTHER owner page in this app uses ?shop_id=. This component
 * builds each link with the correct param name per target page rather
 * than assuming one convention everywhere.
 */
(function () {
  'use strict';

  var NAV_ITEMS = [
    { key: 'home', label: 'ホーム', href: 'shop-manage.html', param: 'id', hash: 'today-ops-card' },
    { key: 'orders', label: '注文', href: 'shop-manage.html', param: 'id', hash: 'commerce-orders-card' },
    { key: 'reservations', label: '予約', href: 'shop-manage.html', param: 'id', hash: 'reservations-card' },
    { key: 'customers', label: '顧客', href: 'shop-manage.html', param: 'id', hash: 'customers-card' },
    { key: 'menu', label: 'メニュー', href: 'shop-manage.html', param: 'id', hash: 'menu-card' },
    { key: 'inventory', label: '在庫', href: 'shop-inventory.html', param: 'shop_id' },
    { key: 'ingredients', label: '原材料・レシピ', href: 'shop-ingredients.html', param: 'shop_id' },
    { key: 'staff', label: 'スタッフ', href: 'shop-manage.html', param: 'id', hash: 'service-staff-card' },
    { key: 'page', label: 'ページ', disabled: true },
    { key: 'analytics', label: 'アクセス分析', href: 'shop-analytics.html', param: 'shop_id' },
    { key: 'management', label: '経営ダッシュボード', href: 'shop-dashboard.html', param: 'shop_id' },
    { key: 'settings', label: '店舗設定', href: 'shop-manage.html', param: 'id', hash: 'business-settings-card' },
  ];

  function escapeAttr(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function buildUrl(item, shopId) {
    if (!item.href) return null;
    var u = item.href + '?' + item.param + '=' + encodeURIComponent(shopId || '');
    if (item.hash) u += '#' + item.hash;
    return u;
  }

  function render(containerId, activeKey, shopId) {
    var el = document.getElementById(containerId);
    if (!el) return;
    var baseStyle = 'flex:0 0 auto;padding:7px 13px;border-radius:999px;font-size:12.5px;' +
      'font-weight:600;white-space:nowrap;text-decoration:none;border:1px solid #e7e4dc;';
    var html = '<nav aria-label="オーナーコンソール ナビゲーション" ' +
      'style="display:flex;gap:6px;overflow-x:auto;padding:8px 2px;margin-bottom:10px;-webkit-overflow-scrolling:touch;">';
    NAV_ITEMS.forEach(function (item) {
      if (item.disabled) {
        html += '<span style="' + baseStyle + 'color:#bdb9ae;background:#f3f2ee;cursor:default;" ' +
          'title="準備中（今後のフェーズで提供予定）">' + escapeAttr(item.label) + '（準備中）</span>';
        return;
      }
      var active = item.key === activeKey;
      var style = baseStyle + (active
        ? 'background:#2f7d6f;color:#fff;border-color:#2f7d6f;'
        : 'background:#fff;color:#2a2a28;');
      var aria = active ? ' aria-current="page"' : '';
      html += '<a href="' + escapeAttr(buildUrl(item, shopId)) + '" style="' + style + '"' + aria + '>' +
        escapeAttr(item.label) + '</a>';
    });
    html += '</nav>';
    el.innerHTML = html;
  }

  window.ReceptraOwnerNav = { render: render };
})();
