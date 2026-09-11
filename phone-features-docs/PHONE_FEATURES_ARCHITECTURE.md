# RECEPTRA 電話対応・リマインド機能 アーキテクチャ設計

## 概要
ユーザー要件：
- **電話対応機能**：顧客の電話での予約受付、空き確認、DB入力、オーナー通知
- **リマインド機能**：予約前日の自動電話、オーナー設定質問への回答記録

ハイブリッドモデル：クライアント（HTML5） + バックエンド（Python FastAPI）+ VoIP（Twilio）

---

## 1. システムアーキテクチャ

### 1.1 全体構成
```
┌─────────────────────────────────────────┐
│  クライアント (receptra.html)            │
│  - ユーザー予約管理UI                    │
│  - 店舗管理画面（オーナー）               │
│  - 電話番号登録フォーム                  │
│  - リマインド設定画面                    │
└──────────────┬──────────────────────────┘
               │ REST API
┌──────────────▼──────────────────────────┐
│  バックエンド (FastAPI)                  │
│  - 予約管理API                          │
│  - 顧客管理API                          │
│  - 電話呼出制御                          │
│  - リマインドスケジューラー               │
│  - Twilio連携                           │
└──────────────┬──────────────────────────┘
               │ 
┌──────────────▼──────────────────────────┐
│  外部サービス                           │
│  - Twilio (VoIP)                       │
│  - データベース (SQLite/PostgreSQL)      │
└──────────────────────────────────────────┘
```

### 1.2 データベーススキーマ

#### テーブル1: customers（顧客）
```sql
CREATE TABLE customers (
    id TEXT PRIMARY KEY,
    phone_number TEXT UNIQUE NOT NULL,
    name TEXT,
    email TEXT,
    is_repeat BOOLEAN DEFAULT FALSE,
    first_contact_date DATETIME,
    last_contact_date DATETIME,
    notes TEXT
);
```

#### テーブル2: reservations（予約）
```sql
CREATE TABLE reservations (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    shop_id TEXT NOT NULL,
    reservation_date DATE NOT NULL,
    reservation_time TIME,
    party_size INT,
    phone_number TEXT NOT NULL,
    special_requests TEXT,
    created_at DATETIME,
    source ENUM('web', 'phone', 'app'),
    status ENUM('confirmed', 'cancelled', 'completed'),
    FOREIGN KEY (customer_id) REFERENCES customers(id),
    INDEX (reservation_date, shop_id)
);
```

#### テーブル3: reminder_settings（リマインド設定）
```sql
CREATE TABLE reminder_settings (
    id TEXT PRIMARY KEY,
    shop_id TEXT NOT NULL,
    shop_name TEXT,
    enabled BOOLEAN DEFAULT TRUE,
    reminder_time INT DEFAULT 24,  -- 時間単位（24 = 前日）
    custom_questions TEXT,  -- JSON: [{"question": "何名様?", "key": "party_size"}]
    notification_method ENUM('phone', 'email', 'both'),
    owner_phone TEXT,
    owner_email TEXT
);
```

#### テーブル4: reminder_responses（リマインド回答）
```sql
CREATE TABLE reminder_responses (
    id TEXT PRIMARY KEY,
    reservation_id TEXT NOT NULL,
    reminder_call_date DATETIME,
    call_duration INT,  -- 秒
    responses TEXT,  -- JSON: {"party_size": "4名", "notes": "..."}
    call_status ENUM('completed', 'failed', 'no_answer'),
    FOREIGN KEY (reservation_id) REFERENCES reservations(id)
);
```

---

## 2. API エンドポイント設計

### 2.1 顧客管理API

#### `POST /api/customers`
顧客登録（電話番号ベース）
```json
{
  "phone_number": "09012345678",
  "name": "田中太郎"
}
```

#### `GET /api/customers/{phone_number}`
顧客情報取得
```json
{
  "id": "cust_001",
  "phone_number": "09012345678",
  "name": "田中太郎",
  "is_repeat": true,
  "last_contact_date": "2025-01-20T14:30:00",
  "last_reservation": { "shop_name": "焼肉田中", "date": "2025-01-20" }
}
```

### 2.2 予約管理API

#### `POST /api/reservations`
予約作成
```json
{
  "phone_number": "09012345678",
  "shop_id": "shop_001",
  "reservation_date": "2025-02-15",
  "reservation_time": "19:00",
  "party_size": 4,
  "source": "phone"
}
```

#### `GET /api/shops/{shop_id}/availability`
空き状況確認
```json
{
  "shop_id": "shop_001",
  "date": "2025-02-15",
  "available_slots": [
    {"time": "17:00", "capacity": 4},
    {"time": "19:00", "capacity": 2},
    {"time": "20:30", "capacity": 6}
  ]
}
```

### 2.3 電話制御API

#### `POST /api/calls/initiate`
電話発信（オーナー通知用）
```json
{
  "to_phone": "09087654321",
  "reservation_id": "res_001",
  "message_type": "new_reservation"
}
```

#### `POST /api/calls/remind`
リマインド電話発信
```json
{
  "customer_phone": "09012345678",
  "reservation_id": "res_001",
  "reminder_settings_id": "rm_001"
}
```

### 2.4 Twilio Webhook

#### `POST /api/webhooks/twilio`
Twilio→バックエンド（通話状態更新）
```json
{
  "CallSid": "CA...",
  "CallStatus": "completed",
  "Duration": "120",
  "RecordingUrl": "https://..."
}
```

---

## 3. 音声IVR（Interactive Voice Response）フロー設計

### 3.1 電話対応フロー

```
顧客からの着信
    ↓
┌─────────────────────────────────────────┐
│ 1. ウェルカムメッセージ                 │
│    「いらっしゃいませ。焼肉田中です。」  │
│    「ご予約のお電話ですか？」             │
│    (キー入力待機: 1=予約, 2=照会, 0=終了) │
└─────────────────────────────────────────┘
    │
    ├─→ [1] 新規予約
    │     ├─ 電話番号確認（データベース照会）
    │     ├─ リピーター判定
    │     ├─ 日付・時間・人数聴取
    │     ├─ 特別リクエスト記録
    │     ├─ 予約確認
    │     └─ 予約成功＆DB保存 → オーナーに通知
    │
    ├─→ [2] 予約照会
    │     └─ 電話番号から予約検索・案内
    │
    └─→ [0] 終了
```

### 3.2 リマインドコール フロー

```
予約前日に自動発信
    ↓
┌─────────────────────────────────────────┐
│ 「いつもご利用ありがとうございます」     │
│ 「田中太郎様、焼肉田中の予約確認です」   │
│ 「明日19:00のご予約ですね」              │
│ 「ご参加者は何名ですか？」               │
│ (キー入力: 1=1名, 2=2名, ..., 9=9名以上) │
└─────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────┐
│ 「何かお持ちになるものがありますか？」   │
│ 「例：予約票、身分証明書」               │
│ (キー入力: 1=はい, 2=いいえ)            │
│ 1→テキスト入力音声ガイダンス            │
└─────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────┐
│ 「ご回答ありがとうございました。」       │
│ 「明日のご来店をお待ちしております。」   │
│ DB保存＆オーナーに結果通知              │
└─────────────────────────────────────────┘
```

---

## 4. 実装フェーズ

### Phase 1: バックエンド基盤（2-3日）
- [ ] FastAPI プロジェクト初期化
- [ ] SQLite/PostgreSQL データベース設定
- [ ] 基本CRUD API実装
- [ ] Twilio SDK統合
- [ ] 電話制御ロジック実装

### Phase 2: Twilio統合（2-3日）
- [ ] Twilio アカウント作成＆設定
- [ ] IVR音声ガイダンス作成（TwiML）
- [ ] Webhook実装
- [ ] テスト電話機能

### Phase 3: フロントエンド拡張（1-2日）
- [ ] 電話番号フィールド追加
- [ ] オーナー用リマインド設定UI
- [ ] 顧客履歴表示
- [ ] 予約管理画面修正

### Phase 4: テスト＆最適化（1-2日）
- [ ] 統合テスト
- [ ] 音声品質テスト
- [ ] エラーハンドリング
- [ ] ユーザー受け入れテスト

---

## 5. Twilio 統合詳細

### 5.1 アカウント設定
1. Twilio アカウント作成（https://www.twilio.com）
2. 専用電話番号取得（日本の番号推奨：+81ではじまる）
3. API キー・トークン取得

### 5.2 TwiML（Twilio Markup Language）例

**ウェルカムメッセージ:**
```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say language="ja-JP">いらっしゃいませ。焼肉田中です。</Say>
  <Gather numDigits="1" action="/api/ivr/main_menu" method="POST">
    <Say language="ja-JP">ご予約のお電話ですか？予約は1、照会は2、終了は0を押してください。</Say>
  </Gather>
</Response>
```

**リマインドコール:**
```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say language="ja-JP">いつもご利用ありがとうございます。</Say>
  <Say language="ja-JP" voiceName="Alice">明日19時のご予約確認です。ご参加は何名ですか？</Say>
  <Gather numDigits="1" action="/api/ivr/reminder_party_size" method="POST">
    <Say language="ja-JP">1から9をお押しください。</Say>
  </Gather>
</Response>
```

---

## 6. セキュリティ考慮事項

- [ ] 電話番号の暗号化保存
- [ ] API認証（OAuth2 / API Key）
- [ ] HTTPS必須
- [ ] Twilio Webhook署名検証
- [ ] レート制限（DDoS対策）
- [ ] 個人情報のアクセスログ記録

---

## 7. 技術スタック案

### バックエンド
- **言語**: Python 3.9+
- **フレームワーク**: FastAPI
- **データベース**: PostgreSQL（本番）/ SQLite（開発）
- **ORM**: SQLAlchemy
- **タスクスケジューラー**: APScheduler（リマインド自動発信）
- **VoIP**: Twilio Python SDK

### ホスティング案
- **オプション1**: Heroku（簡単、小規模向け）
- **オプション2**: AWS Lambda + RDS（スケーラブル）
- **オプション3**: Google Cloud Run + Cloud SQL
- **オプション4**: ユーザーのVPS（自由度高い）

---

## 8. 実装優先度と推定工数

| 機能 | 優先度 | 工数 | 備考 |
|------|--------|------|------|
| 顧客電話番号管理 | P0 | 1日 | 基本機能 |
| 予約API実装 | P0 | 1日 | 基本機能 |
| 電話着信受付IVR | P1 | 2日 | Twilio統合 |
| リマインド自動発信 | P1 | 2日 | スケジューラー実装 |
| UI修正（電話番号入力） | P1 | 1日 | フロント拡張 |
| 顧客履歴表示 | P2 | 1日 | 利便性向上 |
| オーナー通知システム | P2 | 1日 | メール+電話 |

**合計推定工数**: 9-10営業日

---

## 9. 次のステップ

1. **Twilio アカウント作成**（ユーザー実施）
2. **バックエンド実装スタート** → FastAPI プロジェクト初期化
3. **データベース設計** → スキーマ作成
4. **IVR テスト環境構築**
5. **段階的統合テスト**

