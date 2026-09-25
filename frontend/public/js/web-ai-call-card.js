// PHASE P2: Web AI受付カード（オーナー画面）専用スクリプト。
//
// 目的: 既存の「Web AI受付」フォーム（旧「AIによる電話受付」フォーム、
// id="phone-reception-settings-form"）の保存/読み込みロジックには一切触れず、
// 公開URLの表示・コピー・QRコード表示・保存・SNS案内だけを完全に独立して行う。
//
// 新しいバックエンドAPI呼び出しはこのファイルには一切存在しない。
// 公開URLはログイン中の店舗のshop_id（URLの?idパラメータ、この画面自体が
// 既にそれで開かれている）から、ブラウザ内だけで組み立てる。
// ON/OFF状態の表示は、既存フォームの#ai-phone-reception-enabledチェックボックスの
// .checked値を読み取り専用でポーリング同期する（プログラムによる.checked代入は
// 'change'イベントを発火しないため、既存コードを一切変更せずに正しい状態を
// 追従表示するにはポーリングが最も安全）。
(function () {
    'use strict';

    function qs(id) { return document.getElementById(id); }

    var params = new URLSearchParams(window.location.search);
    var shopId = params.get('id');

    var urlInput = qs('web-ai-call-url');
    var copyBtn = qs('web-ai-call-copy-btn');
    var openBtn = qs('web-ai-call-open-btn');
    var copyMsg = qs('web-ai-call-copy-msg');
    var qrBox = qs('web-ai-call-qr');
    var qrSaveBtn = qs('web-ai-call-qr-save-btn');
    var statusBadge = qs('web-ai-call-status-badge');
    var statusCaveat = qs('web-ai-call-status-caveat');
    var copyInstagramBtn = qs('web-ai-call-copy-instagram');
    var copyLineBtn = qs('web-ai-call-copy-line');
    var copyWebBtn = qs('web-ai-call-copy-web');
    var aiEnabledCheckbox = qs('ai-phone-reception-enabled');

    if (!shopId || !urlInput) {
        // shop_idが取得できない、またはこのブロック自体が存在しない画面では何もしない。
        return;
    }

    var publicUrl = window.location.origin + '/call.html?id=' + encodeURIComponent(shopId);
    urlInput.value = publicUrl;

    function urlWithSource(source) {
        // PHASE4将来のソース分析のための下準備のみ。DB保存や集計は一切行わない。
        // call.html / realtime-voice-engine.jsは既知の?idパラメータのみを読み、
        // 未知のクエリパラメータ(&source=...)は無視することを確認済み（P1/P2監査）。
        return publicUrl + '&source=' + encodeURIComponent(source);
    }

    // ---------- コピー機能（Clipboard API + フォールバック） ----------
    var copyTimers = {};

    function showCopyFeedback(el, text, isError) {
        if (!el) return;
        el.textContent = text;
        el.className = 'msg ' + (isError ? 'error' : 'success');
        if (copyTimers[el.id]) { clearTimeout(copyTimers[el.id]); }
        copyTimers[el.id] = setTimeout(function () {
            el.className = 'msg';
            el.textContent = '';
        }, 2500);
    }

    function copyText(text, feedbackEl, successLabel) {
        if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
            navigator.clipboard.writeText(text).then(function () {
                showCopyFeedback(feedbackEl, successLabel || 'コピーしました。', false);
            }).catch(function () {
                showCopyFeedback(feedbackEl, 'コピーできませんでした。URL欄を直接選択してコピーしてください。', true);
            });
            return;
        }
        // Clipboard APIが使えない場合のフォールバック（古いブラウザ等）。
        try {
            var tmp = document.createElement('textarea');
            tmp.value = text;
            tmp.style.position = 'fixed';
            tmp.style.opacity = '0';
            document.body.appendChild(tmp);
            tmp.focus();
            tmp.select();
            var ok = document.execCommand('copy');
            document.body.removeChild(tmp);
            if (ok) {
                showCopyFeedback(feedbackEl, successLabel || 'コピーしました。', false);
            } else {
                showCopyFeedback(feedbackEl, 'コピーできませんでした。URL欄を直接選択してコピーしてください。', true);
            }
        } catch (e) {
            showCopyFeedback(feedbackEl, 'コピーできませんでした。URL欄を直接選択してコピーしてください。', true);
        }
    }

    if (copyBtn) {
        copyBtn.addEventListener('click', function () {
            copyText(publicUrl, copyMsg, 'コピーしました。');
        });
    }
    if (copyInstagramBtn) {
        copyInstagramBtn.addEventListener('click', function () {
            copyText(urlWithSource('instagram'), copyMsg, 'Instagram用URLをコピーしました。');
        });
    }
    if (copyLineBtn) {
        copyLineBtn.addEventListener('click', function () {
            copyText(urlWithSource('line'), copyMsg, 'LINE公式用URLをコピーしました。');
        });
    }
    if (copyWebBtn) {
        copyWebBtn.addEventListener('click', function () {
            copyText(urlWithSource('website'), copyMsg, 'Web用URLをコピーしました。');
        });
    }

    // ---------- プレビュー（新しいタブで公開ページを開く） ----------
    if (openBtn) {
        openBtn.addEventListener('click', function () {
            window.open(publicUrl, '_blank', 'noopener');
        });
    }

    // ---------- QRコード表示（完全オフライン・クライアント側のみ生成） ----------
    var qrSvgString = null;
    try {
        if (typeof window.qrcode === 'function') {
            var qr = window.qrcode(0, 'M'); // typeNumber=0は自動判定、誤り訂正レベルM
            qr.addData(publicUrl);
            qr.make();
            qrSvgString = qr.createSvgTag({ cellSize: 5, margin: 2 });
            if (qrBox) {
                qrBox.innerHTML = qrSvgString;
                var svgEl = qrBox.querySelector('svg');
                if (svgEl) {
                    svgEl.style.width = '100%';
                    svgEl.style.height = 'auto';
                    svgEl.style.display = 'block';
                }
            }
        } else if (qrBox) {
            qrBox.innerHTML = '<p class="hint" style="margin:0;">QRコードを読み込めませんでした。</p>';
        }
    } catch (e) {
        if (qrBox) {
            qrBox.innerHTML = '<p class="hint" style="margin:0;">QRコードを読み込めませんでした。</p>';
        }
    }

    if (qrSaveBtn) {
        qrSaveBtn.addEventListener('click', function () {
            if (!qrSvgString) {
                showCopyFeedback(copyMsg, 'QRコードの生成に失敗しているため保存できません。', true);
                return;
            }
            try {
                var blob = new Blob([qrSvgString], { type: 'image/svg+xml' });
                var blobUrl = URL.createObjectURL(blob);
                var a = document.createElement('a');
                a.href = blobUrl;
                a.download = 'web-ai-uketsuke-qr.svg';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                setTimeout(function () { URL.revokeObjectURL(blobUrl); }, 1000);
            } catch (e) {
                showCopyFeedback(copyMsg, 'QRコードの保存に失敗しました。', true);
            }
        });
    }

    // ---------- 公開状態バッジ（表示専用・ポーリングで既存チェックボックスに追従） ----------
    function renderStatus() {
        if (!statusBadge) return;
        if (!aiEnabledCheckbox) {
            statusBadge.textContent = '';
            return;
        }
        if (aiEnabledCheckbox.checked) {
            statusBadge.textContent = '● 公開中';
            statusBadge.style.color = '#10b981';
            if (statusCaveat) { statusCaveat.style.display = 'none'; }
        } else {
            statusBadge.textContent = '○ 停止中';
            statusBadge.style.color = '#999';
            if (statusCaveat) {
                statusCaveat.textContent = '現在は停止中です。URLを公開してもAI受付は開始されません。';
                statusCaveat.style.display = 'block';
            }
        }
    }

    renderStatus();
    if (aiEnabledCheckbox) {
        aiEnabledCheckbox.addEventListener('change', renderStatus);
        // プログラムによる.checked代入（読み込み直後・保存成功直後）はchangeイベントを
        // 発火しないため、軽量なポーリングで確実に追従させる。
        setInterval(renderStatus, 500);
    }
})();
