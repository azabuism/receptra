# RECEPTRA システム仕様書
## 飲食店・美容・ホテル向け検索・予約プラットフォーム

**バージョン**: 2.0.0  
**最終更新**: 2024年9月11日  
**ステータス**: Phase 2/3 実装中

---

## 目次
1. [システムアーキテクチャ](#システムアーキテクチャ)
2. [システム構成図](#システム構成図)
3. [API仕様](#api仕様)
4. [データベーススキーマ](#データベーススキーマ)
5. [ユーザーフロー](#ユーザーフロー)
6. [環境設定](#環境設定)
7. [デプロイメント](#デプロイメント)
8. [セキュリティ](#セキュリティ)

---

## システムアーキテクチャ

### 概要
RECEPTRAは以下のコンポーネントで構成される統合予約管理システムです:

```
顧客 → Web/App → RECEPTRA Backend (FastAPI) → SQLite Database
            ↓
       Twilio Voice API
            ↓
       IVR + SMS Gateway
```

### コアモジュール

#### 1. **フロントエンド**
- **顧客向けページ** (`receptra-homepage-with-subcategory-icons.html`)
  - サービス検索インターフェース
  - 店舗一覧表示
  - 予約フォーム
  - 予約確認

- **店舗オーナー向けダッシュボード** (`receptra-store-dashboard-extended.html`)
  - 予約管理
  - リマインド管理（当日/前日/カスタム）
  - ビジネスコール記録
  - 緊急通知管理
  - 来店必要物品設定
  - 店舗情報編集

- **ログイン/登録ページ**
  - 店舗オーナー認証
  - セッション管理

#### 2. **バックエンド (FastAPI)**
- RESTful API エンドポイント
- Twilio Voice IVR統合
- APScheduler によるリマインド自動実行
- SQLiteデータベース管理
- ログ記録とエラーハンドリング

#### 3. **通信インターフェース (Twilio)**
- 音声通話（IVR）
- SMS通知
- 電話での予約受け付け
- リマインド配信

---

## システム構成図

```
┌─────────────────────────────────────────────────────────────────────┐
│                        RECEPTRA Platform                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  Frontend Layer                                                       │
│  ┌──────────────────┐    ┌──────────────────┐    ┌──────────────┐  │
│  │ Customer Pages   │    │ Store Dashboard  │    │ Auth Pages   │  │
│  │ (Home, Search)   │    │ (Reminders,      │    │ (Login/Reg)  │  │
│  │                  │    │  Calls, Notif)   │    │              │  │
│  └────────┬─────────┘    └────────┬─────────┘    └────────┬─────┘  │
│           │                       │                       │         │
│           └───────────────────────┼───────────────────────┘         │
│                                   │                                  │
│                    ┌──────────────▼──────────────┐                  │
│                    │ receptra-api-client.js      │                  │
│                    │ (API Integration Layer)     │                  │
│                    └──────────────┬──────────────┘                  │
│                                   │                                  │
├──────────────────────────────────┼──────────────────────────────────┤
│  Backend Layer (FastAPI 8000)    │                                  │
│                                  ▼                                  │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ API Router & Authentication                                  │  │
│  │ - /api/reservations/* (Create, Update, Get)                 │  │
│  │ - /api/reminders/* (Schedule, Get, Record Response)         │  │
│  │ - /api/business-calls/* (Log, Get, Detail)                  │  │
│  │ - /api/notifications/urgent/* (Create, Get, Acknowledge)    │  │
│  │ - /api/ivr/* (Business Call Menu, Response Handler)         │  │
│  │ - /api/calls/business_incoming (Twilio Webhook)             │  │
│  └────────────────────────────┬─────────────────────────────────┘  │
│                               │                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ Business Logic Layer                                          │  │
│  │ - Reminder Scheduler (APScheduler)                           │  │
│  │ - IVR Call Flow Manager                                      │  │
│  │ - Notification Service                                       │  │
│  │ - TwiML Voice Response Generator                             │  │
│  └────────────────────────────┬─────────────────────────────────┘  │
│                               │                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ Data Access Layer (SQLAlchemy ORM)                           │  │
│  │ - Reservation Management                                     │  │
│  │ - Reminder Schedule CRUD                                     │  │
│  │ - Business Call Logging                                      │  │
│  │ - Urgent Notification Tracking                               │  │
│  └────────────────────────────┬─────────────────────────────────┘  │
│                               │                                     │
├───────────────────────────────┼─────────────────────────────────────┤
│  Database Layer               │                                     │
│                          ┌────▼─────┐                              │
│                          │ SQLite   │                              │
│                          │ (receptra.db)                           │
│                          └──────────┘                              │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│  External Services                                                  │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ Twilio Voice API                                             │  │
│  │ - Inbound Call IVR                                           │  │
│  │ - Outbound Reminder Calls                                    │  │
│  │ - SMS Notifications                                          │  │
│  │ - Call Recording & Transcription                             │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## API仕様

### ベースURL
```
http://localhost:8000  (開発環境)
https://receptra-api.herokuapp.com  (本番環境)
```

### 共通ヘッダー
```
Content-Type: application/json
Authorization: Bearer <token>
```

### レスポンスフォーマット
```json
{
  "status": "success|error",
  "data": {},
  "error": "error message (if error)"
}
```

---

### 1. 予約管理API

#### CREATE: 予約を作成
```
POST /api/reservations
Content-Type: application/json

Request:
{
  "phone_number": "09012345678",
  "shop_id": "shop_001",
  "shop_name": "テスト店舗",
  "reservation_date": "2024-10-15",
  "reservation_time": "19:00",
  "party_size": 4,
  "special_requests": "アレルギー対応: ナッツ",
  "source": "phone"
}

Response:
{
  "reservation_id": "res_12345",
  "status": "confirmed",
  "confirmation_number": "ABC123"
}
```

#### GET: 予約を取得
```
GET /api/reservations/{reservation_id}

Response:
{
  "reservation_id": "res_12345",
  "phone_number": "09012345678",
  "shop_name": "テスト店舗",
  "reservation_date": "2024-10-15",
  "reservation_time": "19:00",
  "party_size": 4,
  "status": "confirmed",
  "special_requests": "アレルギー対応: ナッツ",
  "created_at": "2024-09-10T10:30:00Z",
  "updated_at": "2024-09-10T10:30:00Z"
}
```

---

### 2. リマインド管理API

#### SCHEDULE: リマインドをスケジュール
```
POST /api/reminders/schedule
Content-Type: application/json

Request:
{
  "shop_id": "shop_001",
  "reminder_type": "same_day|day_before|custom",
  "trigger_days_before": 0,
  "enabled": true
}

Response:
{
  "reminder_id": "rem_001",
  "status": "scheduled",
  "next_trigger": "2024-10-15T09:00:00Z"
}
```

#### GET: 予約のリマインド情報を取得
```
GET /api/reminders/{reservation_id}

Response:
{
  "reminder_id": "rem_001",
  "reservation_id": "res_12345",
  "reminder_type": "day_before",
  "trigger_time": "2024-10-14T09:00:00Z",
  "status": "pending",
  "responses": {}
}
```

#### RECORD: リマインド応答を記録
```
PUT /api/reminders/{reminder_id}/response

Request:
{
  "responses": {
    "confirmed": true,
    "party_size_updated": false,
    "special_requests": ""
  },
  "call_status": "completed"
}

Response:
{
  "reminder_id": "rem_001",
  "status": "confirmed"
}
```

---

### 3. ビジネスコール管理API

#### LOG: ビジネスコールを記録
```
POST /api/business-calls/log
Content-Type: application/json

Request:
{
  "shop_id": "shop_001",
  "caller_name": "山田太郎",
  "caller_number": "09098765432",
  "purpose": "営業|問い合わせ|緊急|その他",
  "urgency_level": "low|medium|high",
  "duration": 300,
  "transcription": "新しい食材供給業者の営業です..."
}

Response:
{
  "call_id": "call_001",
  "status": "logged",
  "created_at": "2024-09-10T15:30:00Z"
}
```

#### LIST: ビジネスコールを一覧取得
```
GET /api/business-calls?shop_id=shop_001&urgency_level=high&purpose=営業

Response:
{
  "business_calls": [
    {
      "call_id": "call_001",
      "caller_name": "山田太郎",
      "caller_number": "09098765432",
      "purpose": "営業",
      "urgency_level": "high",
      "duration": 300,
      "timestamp": "2024-09-10T15:30:00Z",
      "transcription": "..."
    }
  ],
  "total": 1
}
```

#### GET: コール詳細を取得
```
GET /api/business-calls/{call_id}

Response:
{
  "call_id": "call_001",
  "caller_name": "山田太郎",
  "caller_number": "09098765432",
  "purpose": "営業",
  "urgency_level": "high",
  "duration": 300,
  "timestamp": "2024-09-10T15:30:00Z",
  "transcription": "新しい食材供給業者の営業です...",
  "recording_url": "https://...",
  "notes": ""
}
```

---

### 4. 緊急通知API

#### CREATE: 緊急通知を作成
```
POST /api/notifications/urgent
Content-Type: application/json

Request:
{
  "shop_id": "shop_001",
  "reservation_id": "res_12345",
  "notification_type": "cancellation|change|special_request",
  "content": "本日18時の予約がキャンセルされました",
  "urgency": "low|medium|high"
}

Response:
{
  "notification_id": "notif_001",
  "status": "pending"
}
```

#### LIST: 緊急通知を一覧取得
```
GET /api/notifications/urgent?shop_id=shop_001&status=pending

Response:
{
  "notifications": [
    {
      "notification_id": "notif_001",
      "type": "cancellation",
      "content": "本日18時の予約がキャンセルされました",
      "urgency": "high",
      "status": "pending",
      "timestamp": "2024-09-10T16:00:00Z"
    }
  ]
}
```

#### ACKNOWLEDGE: 通知を確認
```
PUT /api/notifications/urgent/{notification_id}/acknowledge

Response:
{
  "notification_id": "notif_001",
  "status": "acknowledged",
  "acknowledged_at": "2024-09-10T16:05:00Z"
}
```

---

### 5. IVR API

#### INCOMING: ビジネスコール受け付け
```
POST /api/calls/business_incoming
Content-Type: application/x-www-form-urlencoded

Request (Twilio Webhook):
CallSid=CA123456...
From=%2B81312345678
To=%2B81312345678
CallStatus=ringing

Response:
<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Gather numDigits="1" action="/api/ivr/business_menu" method="POST">
    <Say voice="alice">
      こんにちは。レセプトラの受付です。
      営業のお電話は1を、お問い合わせは2を、
      緊急のご用件は3を、その他は4を押してください。
    </Say>
  </Gather>
</Response>
```

#### MENU: メニュー選択処理
```
POST /api/ivr/business_menu
Content-Type: application/x-www-form-urlencoded

Request:
CallSid=CA123456...
Digits=1

Response:
<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Record action="/api/calls/business_incoming" maxLength="300" />
  <Say voice="alice">通話を記録します。お名前とご用件をお聞きします。</Say>
</Response>
```

---

## データベーススキーマ

### テーブル構造

#### 1. reservations (予約)
```sql
CREATE TABLE reservations (
  id TEXT PRIMARY KEY,
  shop_id TEXT NOT NULL,
  shop_name TEXT NOT NULL,
  phone_number TEXT NOT NULL,
  reservation_date DATE NOT NULL,
  reservation_time TIME NOT NULL,
  party_size INTEGER NOT NULL,
  special_requests TEXT,
  source TEXT DEFAULT 'phone',
  status TEXT DEFAULT 'confirmed',
  reminder_preferences JSON,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (shop_id) REFERENCES shops(id)
);
```

#### 2. reminder_schedules (リマインドスケジュール)
```sql
CREATE TABLE reminder_schedules (
  id TEXT PRIMARY KEY,
  shop_id TEXT NOT NULL,
  reminder_type TEXT NOT NULL,
  trigger_days_before INTEGER NOT NULL,
  enabled BOOLEAN DEFAULT true,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (shop_id) REFERENCES shops(id)
);
```

#### 3. business_call_logs (ビジネスコール)
```sql
CREATE TABLE business_call_logs (
  id TEXT PRIMARY KEY,
  shop_id TEXT NOT NULL,
  caller_name TEXT,
  caller_number TEXT NOT NULL,
  purpose TEXT NOT NULL,
  urgency_level TEXT DEFAULT 'low',
  duration INTEGER,
  transcription TEXT,
  recording_url TEXT,
  status TEXT DEFAULT 'logged',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (shop_id) REFERENCES shops(id)
);
```

#### 4. urgent_notifications (緊急通知)
```sql
CREATE TABLE urgent_notifications (
  id TEXT PRIMARY KEY,
  shop_id TEXT NOT NULL,
  reservation_id TEXT,
  notification_type TEXT NOT NULL,
  content TEXT NOT NULL,
  urgency TEXT DEFAULT 'medium',
  status TEXT DEFAULT 'pending',
  acknowledged_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (shop_id) REFERENCES shops(id),
  FOREIGN KEY (reservation_id) REFERENCES reservations(id)
);
```

#### 5. shops (店舗)
```sql
CREATE TABLE shops (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT UNIQUE NOT NULL,
  phone TEXT,
  address TEXT,
  category TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## ユーザーフロー

### 1. 顧客フロー: 予約申し込み
```
1. ホームページで店舗検索
2. 店舗選択
3. 予約フォーム入力
4. 予約確認メッセージ表示
5. 予約前日/当日: リマインド電話受信
6. リマインド応答（確認/変更/キャンセル）
```

### 2. 店舗オーナーフロー: 予約管理
```
1. ダッシュボードにログイン
2. 本日の予約確認
3. リマインド設定（当日/前日/カスタム）
4. ビジネスコール記録確認
5. 高緊急度コール確認
6. 緊急通知確認・応答
```

### 3. IVR通話フロー: ビジネスコール受け付け
```
店舗への電話着信
   ↓
IVR：メニュー提示
   ↓
用件選択（営業/問い合わせ/緊急/その他）
   ↓
音声録音開始
   ↓
文字起こし処理
   ↓
コール記録保存
   ↓
高緊急度の場合: 緊急通知作成
```

---

## 環境設定

### 必須環境変数

```bash
# FastAPI
API_HOST=http://localhost:8000
DATABASE_PATH=receptra.db
DEBUG=true

# Twilio
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_PHONE_NUMBER=+81312345678
TWILIO_WEBHOOK_URL=https://your-domain/api/calls/business_incoming

# Frontend
API_BASE_URL=http://localhost:8000
USE_LOCAL_STORAGE=true  # demo mode
```

### 開発環境セットアップ

```bash
# Python 3.9+ が必要
python --version

# 依存関係をインストール
pip install -r requirements.txt --break-system-packages

# データベース初期化
python init_db.py

# FastAPI サーバー起動
python receptra_backend_extended.py
# または
uvicorn receptra_backend_extended:app --reload --port 8000

# フロントエンドを開く
# ブラウザで http://localhost:5500 を開く
# または VS Code Live Server を使用
```

---

## デプロイメント

### Heroku へのデプロイ

#### 1. Heroku CLI をインストール
```bash
# macOS
brew tap heroku/brew && brew install heroku

# Linux
curl https://cli-assets.heroku.com/install.sh | sh

# Windows
# Heroku CLI インストーラーをダウンロード
```

#### 2. Heroku にログイン
```bash
heroku login
```

#### 3. アプリケーション作成
```bash
heroku create receptra-api
```

#### 4. 環境変数設定
```bash
heroku config:set TWILIO_ACCOUNT_SID=ACxxxxxxxx
heroku config:set TWILIO_AUTH_TOKEN=your_token
heroku config:set TWILIO_PHONE_NUMBER=+81312345678
heroku config:set DATABASE_PATH=/tmp/receptra.db
```

#### 5. デプロイ
```bash
git push heroku main
```

#### 6. 動作確認
```bash
heroku logs --tail
heroku open
```

### Railway へのデプロイ

#### 1. Railway アカウント作成
https://railway.app に登録

#### 2. Railway CLI インストール
```bash
npm install -g @railway/cli
```

#### 3. プロジェクト初期化
```bash
railway init
```

#### 4. デプロイ
```bash
railway up
```

#### 5. 環境変数設定
```bash
railway variables set TWILIO_ACCOUNT_SID=...
railway variables set TWILIO_AUTH_TOKEN=...
```

### Docker コンテナ化

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY receptra_backend_extended.py .

EXPOSE 8000

CMD ["uvicorn", "receptra_backend_extended:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## セキュリティ

### 認証・認可
- JWT トークンベースの認証
- SessionStorage でトークン保管
- API リクエストごとに認証チェック

### データ保護
- パスワードは bcrypt でハッシュ化
- 電話番号・メールアドレスは暗号化保存
- HTTPS のみで通信

### Twilio セキュリティ
- Twilio署名検証（webhooks）
- アカウントSID/トークンは環境変数で管理
- 本番環境では whitelist IP を設定

### レート制限
```python
# FastAPI RateLimiter を使用
# 1分あたり 60リクエスト
```

---

## トラブルシューティング

### よくある問題

#### 1. Twilio 接続エラー
```
Error: Could not authenticate with Twilio
Solution: 
- TWILIO_ACCOUNT_SID と TWILIO_AUTH_TOKEN を確認
- Twilio Console から認証情報をコピー
```

#### 2. データベースロック
```
Error: database is locked
Solution:
- 複数プロセスからのアクセスを避ける
- SQLite を本番環境では使用しない（PostgreSQL を推奨）
```

#### 3. リマインド実行されない
```
Solution:
- APScheduler が起動しているか確認
- ログで scheduler events を確認
- クロンジョブの実行時刻を確認
```

---

## マイグレーション & アップグレード

### v1.x → v2.0 アップグレード
```bash
# バックアップ
cp receptra.db receptra.db.backup

# マイグレーション実行
python migrate_v1_to_v2.py

# テスト
pytest tests/

# デプロイ
git push heroku main
```

---

## サポート & コミュニティ

- **ドキュメント**: https://docs.receptra.app
- **GitHub**: https://github.com/bariyon/receptra
- **Issues**: https://github.com/bariyon/receptra/issues
- **Email**: support@receptra.app

---

## ライセンス

RECEPTRA © 2024 BARIYON. All rights reserved.
