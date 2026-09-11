# RECEPTRA フロントエンド統合ガイド

## 概要

HTML フロントエンドに以下の電話機能を統合：

1. **顧客側**: 予約フォームに電話番号入力フィールド追加
2. **オーナー側**: 電話対応・リマインド設定パネル
3. **顧客側**: 登録時に電話番号の同意取得

---

## 1. API エンドポイント一覧

バックエンド（FastAPI）の REST API エンドポイント：

```
POST   /api/customers                      # 顧客登録
GET    /api/customers/{phone_number}      # 顧客情報取得
POST   /api/reservations                  # 予約作成
GET    /api/reservations/{phone_number}   # 予約検索
GET    /api/shops/{shop_id}/availability  # 空き状況確認

POST   /api/reminder-settings             # リマインド設定保存
GET    /api/reminder-settings/{shop_id}   # リマインド設定取得
POST   /api/calls/remind/{reservation_id} # リマインド発信

GET    /health                            # ヘルスチェック
GET    /api/status                        # API ステータス
```

---

## 2. ユーザー登録フロー修正

### 2.1 現在のフロー
```
メールアドレス → パスワード → ユーザー登録
```

### 2.2 新フロー（推奨）
```
メールアドレス → パスワード → プロフィール設定
  ├─ 氏名
  ├─ 📱 電話番号（オプション・推奨）
  └─ メールマガジン購読
```

### 2.3 HTML 修正案

`receptra.html` の `handleUserSignup()` 関数を修正：

```javascript
async function handleUserSignup() {
    const email = document.getElementById('signupEmail').value;
    const password = document.getElementById('signupPassword').value;
    const username = document.getElementById('signupUsername')?.value || '';
    const phone = document.getElementById('signupPhone')?.value || '';

    if (!email || !password) {
        alert('メールアドレスとパスワードは必須です');
        return;
    }

    // メール形式検証
    if (!email.match(/^[^\s@]+@[^\s@]+\.[^\s@]+$/)) {
        alert('有効なメールアドレスを入力してください');
        return;
    }

    // 電話番号検証（オプション）
    if (phone && !phone.match(/^0\d{9,10}$/)) {
        alert('電話番号の形式が正しくありません（09012345678など）');
        return;
    }

    const userData = {
        email: email,
        password: password,
        username: username || email.split('@')[0],
        phone: phone,
        registeredAt: new Date().toISOString()
    };

    // バックエンドに顧客登録（電話番号がある場合）
    if (phone) {
        try {
            const response = await fetch(`${API_HOST}/api/customers`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    phone_number: phone,
                    name: userData.username,
                    email: email
                })
            });
            const result = await response.json();
            userData.customerId = result.id;
        } catch (error) {
            console.warn('Customer registration failed:', error);
            // 失敗しても Web 登録は続行
        }
    }

    // ローカル localStorage に保存（従来通り）
    localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(userData));
    userState = userData;
    userState.isLoggedIn = true;

    alert('✅ アカウントが作成されました！');
    showHome();
    updateNavbar();
}
```

---

## 3. 予約フォーム修正

### 3.1 電話番号フィールド追加

`receptra.html` の予約フォームに以下を追加：

```html
<!-- 既存フィールド: 日付、時間、人数など -->

<!-- 📱 電話番号フィールド（新規） -->
<div class="form-group">
    <label for="reservationPhone">
        📱 電話番号 <span style="color: var(--error);">*</span>
    </label>
    <input 
        type="tel" 
        id="reservationPhone"
        class="form-input"
        placeholder="09012345678"
        pattern="0\d{9,10}"
        title="09から始まる10-11桁の電話番号を入力してください"
        required
    />
    <small style="color: #666; margin-top: 4px; display: block;">
        🔔 リマインド機能にご登録いただくと、予約前日に確認のお電話をさせていただきます
    </small>
</div>

<!-- 特別なリクエスト -->
<div class="form-group">
    <label for="reservationNotes">特別なリクエスト</label>
    <textarea 
        id="reservationNotes"
        class="form-input"
        placeholder="例：アレルギー、座席の希望など"
        rows="3"
    ></textarea>
</div>

<!-- 同意チェックボックス -->
<div class="form-group" style="display: flex; gap: 8px; align-items: flex-start;">
    <input 
        type="checkbox" 
        id="reservationPhoneConsent"
        required
    />
    <label for="reservationPhoneConsent" style="margin: 0; line-height: 1.4;">
        ☎️ リマインドのためにお電話させていただくことに同意します
    </label>
</div>

<!-- 予約ボタン -->
<button 
    id="submitReservationBtn"
    class="btn-primary"
    onclick="submitReservation()"
>
    ✅ 予約を確定する
</button>
```

### 3.2 JavaScript: 予約送信ロジック

```javascript
const API_HOST = 'https://api.receptra.com';  // 本番: 自分のドメイン

async function submitReservation() {
    // フィールド取得
    const phone = document.getElementById('reservationPhone').value;
    const date = document.getElementById('reservationDate').value;
    const time = document.getElementById('reservationTime').value;
    const party_size = parseInt(document.getElementById('reservationParty').value);
    const notes = document.getElementById('reservationNotes').value;
    const consent = document.getElementById('reservationPhoneConsent').checked;

    // バリデーション
    if (!phone) {
        alert('📱 電話番号を入力してください');
        return;
    }

    if (!phone.match(/^0\d{9,10}$/)) {
        alert('📱 電話番号の形式が正しくありません\n例：09012345678');
        return;
    }

    if (!date || !time || !party_size) {
        alert('日付、時間、人数を入力してください');
        return;
    }

    if (!consent) {
        alert('☎️ リマインド電話への同意をしてください');
        return;
    }

    // 読み込み表示
    const btn = document.getElementById('submitReservationBtn');
    btn.disabled = true;
    btn.textContent = '📤 送信中...';

    try {
        // バックエンドに予約作成をリクエスト
        const response = await fetch(`${API_HOST}/api/reservations`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                phone_number: phone,
                shop_id: currentState.selectedShop.id,
                shop_name: currentState.selectedShop.name,
                reservation_date: date,
                reservation_time: time,
                party_size: party_size,
                special_requests: notes,
                source: 'web'
            })
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const data = await response.json();

        // 成功メッセージ
        alert(`
✅ ご予約ありがとうございました！

予約ID: ${data.reservation_id}
店舗: ${currentState.selectedShop.name}
日時: ${date} ${time}
人数: ${party_size}名

📞 予約前日にリマインドのお電話をさせていただきます。
        `);

        // ローカルにも保存（従来通り）
        const reservation = {
            id: data.reservation_id,
            shop_id: currentState.selectedShop.id,
            shopName: currentState.selectedShop.name,
            date: date,
            time: time,
            party: party_size,
            status: 'confirmed',
            phone: phone,
            createdAt: new Date().toISOString()
        };

        const existing = loadReservations();
        existing.push(reservation);
        localStorage.setItem(RESERVATIONS_STORAGE_KEY, JSON.stringify(existing));

        // UI リセット
        document.getElementById('reservationPhone').value = '';
        document.getElementById('reservationNotes').value = '';
        document.getElementById('reservationPhoneConsent').checked = false;

        // ホームに戻る
        showHome();

    } catch (error) {
        console.error('Reservation error:', error);
        alert('⚠️ 予約の送信に失敗しました\n\nもう一度お試しください。\n' + error.message);
    } finally {
        btn.disabled = false;
        btn.textContent = '✅ 予約を確定する';
    }
}
```

---

## 4. オーナー設定パネル（新規）

### 4.1 設定画面 HTML

オーナー向けの電話・リマインド設定UI：

```html
<!-- 新しいセクション: owner-settings-page -->
<div id="ownerSettingsPage" data-state="hidden">
    <header>
        <button class="back-btn" onclick="showUserProfile()">←</button>
        <h1>☎️ 電話・リマインド設定</h1>
    </header>

    <main class="container">
        <section class="settings-section">
            <h2>📞 電話対応設定</h2>
            
            <div class="setting-item">
                <label>
                    <input 
                        type="checkbox" 
                        id="phoneAnsweringEnabled"
                        onchange="savePhoneSettings()"
                    />
                    電話での予約受付を有効にする
                </label>
                <small>✓ 有効な場合、顧客は Twilio 番号に電話して予約できます</small>
            </div>

            <div class="setting-item">
                <label>オーナーの電話番号:</label>
                <input 
                    type="tel"
                    id="ownerPhone"
                    placeholder="09012345678"
                    pattern="0\d{9,10}"
                    onchange="savePhoneSettings()"
                />
                <small>新規予約時の通知先</small>
            </div>

            <div class="setting-item">
                <label>オーナーのメールアドレス:</label>
                <input 
                    type="email"
                    id="ownerEmail"
                    onchange="savePhoneSettings()"
                />
            </div>

            <div class="setting-item">
                <label>新規予約通知方法:</label>
                <select id="notificationMethod" onchange="savePhoneSettings()">
                    <option value="email">📧 メールのみ</option>
                    <option value="phone">☎️ 電話のみ</option>
                    <option value="both">📧 メール + ☎️ 電話</option>
                </select>
            </div>
        </section>

        <hr style="margin: 24px 0; border: none; height: 1px; background: #ddd;">

        <section class="settings-section">
            <h2>🔔 リマインド設定</h2>

            <div class="setting-item">
                <label>
                    <input 
                        type="checkbox" 
                        id="reminderEnabled"
                        onchange="saveReminderSettings()"
                    />
                    リマインド電話を有効にする
                </label>
                <small>✓ 有効な場合、予約前日に顧客に自動電話します</small>
            </div>

            <div class="setting-item">
                <label>リマインド時刻（予約前の時間）:</label>
                <select id="reminderTime" onchange="saveReminderSettings()">
                    <option value="24">前日同時刻</option>
                    <option value="48">2日前</option>
                    <option value="12">12時間前</option>
                    <option value="2">2時間前</option>
                </select>
            </div>

            <div class="setting-item">
                <label>リマインド時に聞きたい質問:</label>
                <div id="customQuestionsContainer">
                    <!-- 動的生成 -->
                </div>
                <button onclick="addCustomQuestion()" class="btn-secondary">
                    + 質問を追加
                </button>
            </div>
        </section>

        <hr style="margin: 24px 0;">

        <section class="settings-section">
            <h2>📊 電話対応統計</h2>
            <div id="phoneStats" style="background: #f5f5f5; padding: 16px; border-radius: 8px;">
                <p>📞 本月の電話予約: <strong>0件</strong></p>
                <p>🔔 リマインド送信: <strong>0件</strong></p>
                <p>📝 顧客回答率: <strong>0%</strong></p>
            </div>
        </section>

        <hr style="margin: 24px 0;">

        <button class="btn-primary" onclick="testReminderCall()">
            🔊 テスト電話を送信
        </button>

        <div id="settingsMessage" style="margin-top: 16px; padding: 12px; border-radius: 6px; display: none;"></div>
    </main>
</div>
```

### 4.2 JavaScript: 設定管理

```javascript
// 電話設定の保存
async function savePhoneSettings() {
    const settings = {
        shop_id: userState.email,  // オーナーのメールアドレス
        shop_name: userState.username,  // 店舗名
        enabled: document.getElementById('phoneAnsweringEnabled').checked,
        owner_phone: document.getElementById('ownerPhone').value,
        owner_email: document.getElementById('ownerEmail').value,
        notification_method: document.getElementById('notificationMethod').value
    };

    try {
        const response = await fetch(`${API_HOST}/api/reminder-settings`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(settings)
        });

        if (!response.ok) throw new Error('Settings save failed');

        showMessage('✅ 設定が保存されました', 'success');
    } catch (error) {
        console.error('Error:', error);
        showMessage('⚠️ 設定の保存に失敗しました', 'error');
    }
}

// リマインド設定の保存
async function saveReminderSettings() {
    const questions = Array.from(document.querySelectorAll('.custom-question'))
        .map(el => ({
            question: el.querySelector('.question-text').value,
            key: el.querySelector('.question-key').value
        }));

    const settings = {
        shop_id: userState.email,
        shop_name: userState.username,
        enabled: document.getElementById('reminderEnabled').checked,
        reminder_time: parseInt(document.getElementById('reminderTime').value),
        custom_questions: questions,
        notification_method: document.getElementById('notificationMethod').value,
        owner_phone: document.getElementById('ownerPhone').value,
        owner_email: document.getElementById('ownerEmail').value
    };

    try {
        const response = await fetch(`${API_HOST}/api/reminder-settings`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(settings)
        });

        if (!response.ok) throw new Error('Reminder settings save failed');

        showMessage('✅ リマインド設定が保存されました', 'success');
    } catch (error) {
        console.error('Error:', error);
        showMessage('⚠️ リマインド設定の保存に失敗しました', 'error');
    }
}

// 質問追加
function addCustomQuestion() {
    const container = document.getElementById('customQuestionsContainer');
    const questionId = `q_${Date.now()}`;

    const html = `
        <div class="custom-question" id="${questionId}">
            <input 
                type="text"
                class="question-text"
                placeholder="例：何名様でご来店ですか？"
                onchange="saveReminderSettings()"
            />
            <input 
                type="text"
                class="question-key"
                placeholder="キー（party_size など）"
                onchange="saveReminderSettings()"
            />
            <button class="btn-danger" onclick="this.parentElement.remove(); saveReminderSettings();">
                🗑️
            </button>
        </div>
    `;

    container.insertAdjacentHTML('beforeend', html);
}

// テスト電話送信
async function testReminderCall() {
    const phone = document.getElementById('ownerPhone').value;
    if (!phone) {
        alert('テスト前にオーナーの電話番号を入力してください');
        return;
    }

    try {
        const response = await fetch(`${API_HOST}/api/calls/remind/test`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                owner_phone: phone,
                message: 'これはテスト電話です'
            })
        });

        showMessage('☎️ テスト電話を送信しました。少々お待ちください。', 'info');
    } catch (error) {
        showMessage('⚠️ テスト電話送信に失敗しました', 'error');
    }
}

// メッセージ表示ヘルパー
function showMessage(message, type) {
    const el = document.getElementById('settingsMessage');
    el.textContent = message;
    el.className = `message message-${type}`;
    el.style.display = 'block';
    setTimeout(() => el.style.display = 'none', 5000);
}
```

---

## 5. スタイル追加

`receptra.html` の CSS に以下を追加：

```css
/* 設定フォーム */
.settings-section {
    margin-bottom: 24px;
}

.setting-item {
    margin-bottom: 16px;
}

.setting-item label {
    display: block;
    font-weight: 600;
    margin-bottom: 6px;
}

.setting-item input[type="text"],
.setting-item input[type="tel"],
.setting-item input[type="email"],
.setting-item select {
    width: 100%;
    padding: 8px 12px;
    border: 1px solid #ddd;
    border-radius: 6px;
    font-size: 14px;
}

.setting-item small {
    display: block;
    color: #666;
    margin-top: 4px;
    font-size: 12px;
}

.custom-question {
    display: flex;
    gap: 8px;
    margin-bottom: 8px;
}

.custom-question input {
    flex: 1;
}

.btn-danger {
    background: #ef4444;
    color: white;
    border: none;
    padding: 8px 12px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 14px;
}

.btn-danger:hover {
    background: #dc2626;
}

.message {
    padding: 12px 16px;
    border-radius: 6px;
    font-weight: 600;
}

.message-success {
    background: #d1fae5;
    color: #065f46;
    border: 1px solid #6ee7b7;
}

.message-error {
    background: #fee2e2;
    color: #7f1d1d;
    border: 1px solid #fca5a5;
}

.message-info {
    background: #e0e7ff;
    color: #1e1b4b;
    border: 1px solid #a5b4fc;
}
```

---

## 6. ナビゲーション修正

`showUserProfile()` 関数に設定画面へのリンクを追加：

```html
<!-- プロフィール画面内に追加 -->
<button class="menu-item" onclick="showOwnerSettings()" id="phoneSettingsBtn">
    ☎️ 電話・リマインド設定
</button>
```

JavaScript:
```javascript
function showOwnerSettings() {
    hideAllPages();
    document.getElementById('ownerSettingsPage').removeAttribute('data-state');
}
```

---

## 7. テストチェックリスト

- [ ] 予約フォームで電話番号が必須になった
- [ ] バリデーション（09から始まる10-11桁）
- [ ] バックエンドに POST /api/reservations が動作
- [ ] 登録成功時に予約ID表示
- [ ] オーナー設定パネルが表示される
- [ ] リマインド設定保存が動作
- [ ] テスト電話が送信される

