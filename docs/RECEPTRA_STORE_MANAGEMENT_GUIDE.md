# RECEPTRA 店舗オーナー管理システム - 使用ガイド

## システム概要

店舗オーナー向けの統合管理システムで、以下の機能を提供します：

1. **予約フォームのカスタマイズ** - お客さんが予約時に答える質問を自由に設定
2. **来店必要物品の管理** - 来店時に必要なものをお客さんに伝える
3. **店舗情報管理** - 基本的な店舗情報の編集
4. **ダッシュボード** - 予約状況の概要表示

---

## ファイル構成

```
RECEPTRA Store Management System
├── receptra-homepage-with-subcategory-icons.html     (メインホームページ)
├── receptra-store-login.html                          (店舗オーナーログイン)
├── receptra-store-register.html                       (店舗新規登録)
├── receptra-store-dashboard.html                      (管理ダッシュボード)
└── RECEPTRA_STORE_MANAGEMENT_GUIDE.md                 (このファイル)
```

---

## ナビゲーションフロー

### ユーザー（お客さん）の流れ
```
ホームページ
  ↓
カテゴリ・地域検索
  ↓
店舗選択
  ↓
予約フォーム入力（カスタマイズ質問）
  ↓
必要物品確認
  ↓
予約完了
```

### 店舗オーナーの流れ
```
ホームページ
  ↓
右上「店舗ページ」クリック
  ↓
モーダル表示
  ├─ 「店舗ログイン」→ receptra-store-login.html
  └─ 「店舗登録」  → receptra-store-register.html
      ↓
   ダッシュボード（receptra-store-dashboard.html）
      ├─ 予約設定
      ├─ 来店必要物品管理
      ├─ 店舗情報管理
      └─ ダッシュボード
```

---

## 各ページの詳細

### 1. ホームページ (receptra-homepage-with-subcategory-icons.html)

**役割:** ユーザーと店舗オーナーの入り口

**主な機能:**
- 8カテゴリの表示（グルメ、ビューティー、ホテル、教育、ヘルスケア、フィットネス、ショッピング、その他）
- 各カテゴリの複数の詳細業種
- 店舗検索
- 右上ナビゲーション：「ユーザー登録」「店舗ページ」

**店舗オーナー用モーダル:**
- 「店舗ページ」をクリック → モーダル表示
- 「店舗ログイン」ボタン → receptra-store-login.html
- 「店舗登録」ボタン → receptra-store-register.html

---

### 2. 店舗ログインページ (receptra-store-login.html)

**役割:** 既存店舗オーナーがシステムにログイン

**主な機能:**
- メールアドレスとパスワードでログイン
- 「ログイン情報を保存する」チェックボック（次回訪問時にメール復元）
- エラーハンドリング（未登録メール、パスワード不一致など）

**バリデーション:**
```
✓ メールアドレス形式チェック
✓ パスワード一致確認
✓ 登録済みメールアドレスの確認
```

**ログイン後:**
- sessionStorage に認証情報を保存
- receptra-store-dashboard.html へ自動遷移

**テスト用ログイン方法:**
```javascript
// ブラウザコンソルで実行（デモ用）
initializeDemoData();
// Email: demo@store.com
// Password: Demo123
```

---

### 3. 店舗登録ページ (receptra-store-register.html)

**役割:** 新規店舗オーナーがシステムに登録

**登録フォーム項目（全13項目）:**

#### 基本情報グループ
- 店舗名 (必須)
- 業種 (必須) - 8カテゴリから選択
- 詳細カテゴリ (必須) - 例：「ラーメン、パスタ」

#### 連絡先情報グループ
- メールアドレス (必須)
- 電話番号 (必須)
- ウェブサイト (任意)

#### 住所・営業時間グループ
- 都道府県 (必須) - 全47都道府県
- 住所 (必須)
- 営業開始時間 (必須)
- 営業終了時間 (必須)

#### 店舗説明グループ
- 店舗紹介 (任意)

#### セキュリティグループ
- パスワード (必須)
- パスワード確認 (必須)

#### 利用規約グループ
- 利用規約同意チェック (必須)

**バリデーション:**
```javascript
✓ パスワード一致チェック
✓ 利用規約同意確認
✓ HTML5 形式バリデーション (email, tel, url)
```

---

### 4. 店舗ダッシュボード (receptra-store-dashboard.html)

**役割:** 店舗オーナーが予約・来店物品を管理

**レイアウト:**
```
┌─────────────────────────────────────────┐
│ RECEPTRA              [ログアウト]      │ ← ナビゲーション
├──────────┬────────────────────────────┤
│サイドバー│  メインコンテンツ           │
│  メニュー│  (ページ切り替え)           │
├──────────┤                            │
│ダッシュ  │  [選択ページ内容]           │
│ボード    │                            │
│          │                            │
│予約設定  │                            │
│          │                            │
│来店物品  │                            │
│          │                            │
│店舗情報  │                            │
└──────────┴────────────────────────────┘
```

#### 4.1 ダッシュボードページ

**表示内容:**
- 本日の予約件数
- 今週の予約件数
- 最近の予約一覧（今後実装）

---

#### 4.2 予約設定ページ

**機能:** お客さんが予約時に答える質問をカスタマイズ

**固定質問（自動追加）:**
```
1. 来店日時 (日時選択) - 必須
2. 人数 (数字入力) - 必須
```

**カスタム質問の追加:**

**ステップ:**
1. 「+ 質問を追加」ボタンをクリック
2. モーダルが開く
3. 以下を入力：
   - 質問内容（例：「利用目的」）
   - 質問形式：
     - テキスト入力
     - 数字入力
     - 選択肢（セレクトボックス）
     - 複数選択（チェックボックス）
     - 長文入力（textarea）
   - 必須・任意の設定
   - 選択肢を指定する場合：カンマ区切り（例：「ランチ, ディナー, 個室」）
4. 「追加」ボタンで保存

**質問の削除:**
- カスタム質問の右側「削除」ボタンをクリック
- 固定質問（来店日時、人数）は削除不可

**データ保存:**
- 自動的に localStorage に保存
- ブラウザのデータ削除まで保持

**例：飲食店での設定**
```
固定:
  - 来店日時
  - 人数

カスタム:
  - 利用目的（選択肢）→ "ランチ, ディナー, 宴会"
  - 特別なリクエスト（長文入力）
  - 個室希望（複数選択）→ "あり, なし"
  - アレルギー（テキスト入力）
```

**例：美容院での設定**
```
固定:
  - 来店日時
  - 人数

カスタム:
  - 施術内容（選択肢）→ "カット, パーマ, カラー, ネイル"
  - スタイリスト指名（テキスト入力）
  - 前回施術情報（長文入力）
  - 予算（数字入力）
```

---

#### 4.3 来店必要物品管理ページ

**機能:** お客さんに来店時に必要なものを伝える

**定義済み物品（8種類）:**
```
🆔 免許証
💴 現金
🏥 保険証
📳 マイナンバーカード
💳 クレジットカード
🛂 パスポート
🎓 学生証
📧 予約確認メール
```

**使用方法:**

1. **物品の選択:**
   - グリッド表示から必要な物品をクリック
   - 選択状態が視覚的に変わる（背景色、枠線）

2. **カスタム物品の追加:**
   - 物品名を入力（例：「健康保険証」）
   - 絵文字を入力（最大2文字、例：「📋」）
   - 「追加」ボタンをクリック
   - 自動的に選択状態で追加される

3. **選択物品の確認:**
   - 下部「選択された物品」セクションに表示
   - 個別削除ボタンで削除可能

**例：医療機関での設定**
```
選択物品:
  🏥 保険証
  📳 マイナンバーカード
  (カスタム) 📋 初診票
  (カスタム) 💊 常用薬一覧
```

**例：ホテルでの設定**
```
選択物品:
  🆔 免許証
  💳 クレジットカード
  📧 予約確認メール
  (カスタム) 🛂 国際運転免許（外国人の場合）
```

---

#### 4.4 店舗情報ページ

**編集可能な情報:**
- 店舗名
- 業種（8カテゴリから選択）
- メールアドレス
- 電話番号
- 営業開始時間
- 営業終了時間

**保存方法:**
- 各項目を入力
- 「保存する」ボタンをクリック
- localStorage に自動保存
- alert でメッセージ表示

---

## データ永続化

### localStorage キー

```javascript
// 店舗ダッシュボードのメインデータ
'receptra_store_dashboard'
{
  questions: [
    { id: 1, label: '来店日時', type: 'datetime', required: true, fixed: true },
    { id: 2, label: '人数', type: 'number', required: true, fixed: true },
    // ... カスタム質問
  ],
  availableItems: [
    { id: 1, name: '免許証', icon: '🆔', selected: true },
    // ... その他物品
  ],
  shopInfo: {
    name: '店舗名',
    category: 'restaurant',
    email: 'store@example.com',
    phone: '090-1234-5678',
    openTime: '11:00',
    closeTime: '23:00'
  }
}

// 店舗登録情報（ログイン時に使用）
'receptra_store_register'
{
  email: 'store@example.com',
  password: 'Password123',
  shopName: '店舗名',
  // ... その他の登録情報
}

// ログイン履歴（「ログイン情報を保存」時）
'receptra_store_login'
{
  email: 'store@example.com',
  timestamp: '2026-09-11T12:34:56.789Z'
}
```

---

## セッション管理

### ログイン時に保存される sessionStorage

```javascript
// ログイン成功時
sessionStorage.setItem('receptra_authenticated', 'true');
sessionStorage.setItem('receptra_store_email', 'store@example.com');
sessionStorage.setItem('receptra_store_name', 'My Store');
```

---

## トラブルシューティング

### ログインできない場合

**Q: 「このメールアドレスは登録されていません」とエラーが出る**

A: 以下を確認してください
1. 店舗登録ページで登録したメールアドレスと完全に一致しているか
2. スペースや大文字・小文字が間違っていないか

**Q: パスワードが違うと言われる**

A: 以下を確認してください
1. Caps Lock がオンになっていないか
2. 店舗登録時のパスワードと完全に一致しているか
3. クリップボードからの貼り付けにスペースが含まれていないか

### データが保存されない場合

**Q: 質問や物品の設定が保存されない**

A: 以下を確認してください
1. ブラウザの private/incognito モードを使用していないか
2. localStorage が有効になっているか（ブラウザ設定で確認）
3. ストレージの容量が不足していないか（ブラウザの開発ツールで確認）

### 推奨される解決方法

```javascript
// ブラウザコンソルで実行
// 現在のすべてのデータを確認
console.log(JSON.parse(localStorage.getItem('receptra_store_dashboard')));

// データをリセットしたい場合
localStorage.removeItem('receptra_store_dashboard');
localStorage.removeItem('receptra_store_register');
localStorage.removeItem('receptra_store_login');
sessionStorage.clear();
```

---

## デプロイ手順

### 本番環境への配置

すべてのファイルを同じディレクトリに配置してください：

```
/www/receptra/ または /bariyon.com/receptra/
├── receptra-homepage-with-subcategory-icons.html
├── receptra-store-login.html
├── receptra-store-register.html
├── receptra-store-dashboard.html
└── (その他のファイル)
```

### FTP アップロード

```
Host: ftp.bariyon.com
User: (FTP ユーザー名)
Password: (FTP パスワード)
Port: 21

Remote Path: /public_html/RECEPTRA/
Files:
  - receptra-homepage-with-subcategory-icons.html
  - receptra-store-login.html
  - receptra-store-register.html
  - receptra-store-dashboard.html
```

### URL 例

```
ホームページ:      https://www.bariyon.com/RECEPTRA/receptra-homepage-with-subcategory-icons.html
店舗ログイン:      https://www.bariyon.com/RECEPTRA/receptra-store-login.html
店舗登録:          https://www.bariyon.com/RECEPTRA/receptra-store-register.html
店舗ダッシュボード: https://www.bariyon.com/RECEPTRA/receptra-store-dashboard.html
```

---

## 技術仕様

### 使用技術
- HTML5
- CSS3 (Flexbox, Grid, Media Queries)
- JavaScript (ES6+)
- Google Fonts (M PLUS Rounded 1c)
- Browser APIs (localStorage, sessionStorage)

### ブラウザ互換性
- Chrome 90+
- Firefox 88+
- Safari 14+
- Edge 90+
- Mobile Safari (iOS 14+)
- Chrome Mobile (Android 8+)

### パフォーマンス
- ホームページ: ~45KB
- ログインページ: ~9KB
- 登録ページ: ~25KB
- ダッシュボード: ~32KB
- **合計: ~111KB**（外部ライブラリなし）

### セキュリティに関する注意事項

⚠️ **重要な注意:**

このバージョンは **デモ・プロトタイプ版** です。本番環境で使用する場合は以下の対応が必要です：

1. **パスワード保護:**
   - 現在: localStorage にプレーンテキストで保存
   - 本番: bcrypt などで ハッシュ化して保存

2. **HTTPS:**
   - SSL/TLS 暗号化を強制

3. **バックエンド認証:**
   - localStorage のみに依存しない
   - JWT または セッションベースの認証を実装

4. **データベース:**
   - localStorage ではなく、PostgreSQL などで保存

5. **API セキュリティ:**
   - CORS 設定
   - レート制限
   - 入力バリデーション

---

## 今後の拡張計画

### Phase 1: 機能拡張
- [ ] 予約管理画面（受付、キャンセル対応）
- [ ] 顧客管理機能（会員情報、予約履歴）
- [ ] 分析＆レポート機能（予約数、人気時間帯など）
- [ ] クーポン・プロモーション管理

### Phase 2: バックエンド統合
- [ ] FastAPI バックエンド接続
- [ ] PostgreSQL データベース
- [ ] JWT ベースの認証
- [ ] API エンドポイント実装

### Phase 3: 高度な機能
- [ ] リアルタイム予約通知
- [ ] SMS/メール自動配信
- [ ] 顧客属性に基づく推奨表示
- [ ] 多言語対応（日本語、英語、中国語など）

---

## サポート

問題が発生した場合は、以下の情報をお知らせください：

- ブラウザ名とバージョン
- エラーメッセージ（スクリーンショット）
- 実行したアクション
- ブラウザコンソールのエラー出力

---

**最終更新:** 2026年9月11日
**バージョン:** 1.0.0 (Beta)
**開発:** BARIYON by Akira Tanimura
