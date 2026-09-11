# RECEPTRA 電話機能 デプロイメントガイド

## 前提条件

- Python 3.9以上
- Twilio アカウント（無料トライアル可）
- テスト用の電話番号
- インターネット接続

---

## ステップ 1: ローカル開発環境セットアップ

### 1.1 Python環境構築

```bash
# プロジェクトディレクトリ作成
mkdir receptra-backend
cd receptra-backend

# 仮想環境作成
python3 -m venv venv

# 仮想環境有効化
# macOS/Linux:
source venv/bin/activate
# Windows:
venv\Scripts\activate

# 依存関係インストール
pip install -r requirements.txt
```

### 1.2 環境変数設定

`.env` ファイルを作成：

```bash
# Twilio 設定
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token_here
TWILIO_PHONE_NUMBER=+81312345678  # Twilio提供の日本の番号

# API設定
API_HOST=http://localhost:8000
DATABASE_PATH=receptra.db

# オプション
LOG_LEVEL=INFO
```

### 1.3 ローカルサーバー起動

```bash
# 開発モード
python receptra_backend.py

# または
uvicorn receptra_backend:app --reload --host 0.0.0.0 --port 8000

# ブラウザで確認
curl http://localhost:8000/health
```

**出力例:**
```json
{
  "status": "ok",
  "timestamp": "2025-01-21T10:30:00",
  "twilio_configured": true
}
```

---

## ステップ 2: Twilio アカウント作成＆設定

### 2.1 Twilio アカウント登録

1. https://www.twilio.com/ja-jp/try-twilio にアクセス
2. メールアドレス、パスワード設定
3. 電話番号認証（SMS）
4. 日本の電話番号リクエスト
   - アカウント種類：「ビジネス」
   - ユースケース：「Restaurants」または「Messaging & Voice」
   - 用途：「Call Center」

### 2.2 API キー取得

1. [Twilio Console](https://console.twilio.com) にログイン
2. 左パネル → **Account**
3. **Account SID** と **Auth Token** をコピー
4. `.env` ファイルに貼り付け

### 2.3 電話番号確保

1. Console → **Phone Numbers**
2. **Buy a number** をクリック
3. 国: Japan, 機能: Voice, SMS
4. 希望の番号選択
5. 番号を `.env` の `TWILIO_PHONE_NUMBER` に設定

### 2.4 Webhook URL設定

Twilio が着信時にバックエンドを呼び出すよう設定：

1. Console → **Phone Numbers** → 購入した番号をクリック
2. **Voice** セクション
3. **Incoming calls** → Webhook URL 入力：
   ```
   https://your-domain.com/api/calls/incoming
   ```
   （開発時は ngrok 使用 → 後述）

---

## ステップ 3: ngrok でローカルテスト

公開インターネットからローカルマシンへのトンネル作成。

### 3.1 ngrok インストール

```bash
# macOS (Homebrew)
brew install ngrok

# Linux
wget https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.zip
unzip ngrok-v3-stable-linux-amd64.zip

# Windows
# https://ngrok.com/download からダウンロード
```

### 3.2 ngrok 実行

```bash
# 新しいターミナルタブで
ngrok http 8000

# 出力例:
# Session Status                online
# Session Expires               1 hour, 59 minutes
# Update Status                 update available (version.py:3720)
# Version                       3.0.0
# Region                        United States (us)
# Latency                       87ms
# Web Interface                 http://127.0.0.1:4040
# Forwarding                    https://XXXX-XX-XXX-XXXX-XX.ngrok.io -> http://localhost:8000
```

**フォワード URL をコピー**: `https://XXXX-XX-XXX-XXXX-XX.ngrok.io`

### 3.3 Twilio Webhook 設定

1. Twilio Console
2. Phone Numbers → 購入した番号
3. Incoming calls Webhook：
   ```
   https://XXXX-XX-XXX-XXXX-XX.ngrok.io/api/calls/incoming
   ```

---

## ステップ 4: テスト電話

### 4.1 基本テスト

1. Twilio の電話番号に**テスト用電話**から電話
2. IVR メニューが応答：「いらっしゃいませ。お電話ありがとうございます。」
3. キー入力テスト：
   - **1**: 新規予約フロー
   - **2**: 予約確認フロー
   - **0**: 終了

### 4.2 予約API テスト

```bash
curl -X POST http://localhost:8000/api/reservations \
  -H "Content-Type: application/json" \
  -d '{
    "phone_number": "09012345678",
    "shop_id": "shop_001",
    "shop_name": "焼肉田中",
    "reservation_date": "2025-02-15",
    "reservation_time": "19:00",
    "party_size": 4,
    "special_requests": "タレはカルビ用でお願いします"
  }'
```

**レスポンス例:**
```json
{
  "reservation_id": "res_abc12345",
  "status": "confirmed"
}
```

### 4.3 顧客検索テスト

```bash
curl http://localhost:8000/api/customers/09012345678
```

---

## ステップ 5: 本番デプロイ

### オプション A: Heroku デプロイ（簡単）

#### Heroku CLI インストール
```bash
# macOS
brew tap heroku/brew && brew install heroku

# Windows/Linux
# https://devcenter.heroku.com/articles/heroku-cli からダウンロード
```

#### デプロイ
```bash
# Heroku ログイン
heroku login

# アプリ作成
heroku create receptra-backend

# 環境変数設定
heroku config:set TWILIO_ACCOUNT_SID=ACxxxxxx
heroku config:set TWILIO_AUTH_TOKEN=xxx
heroku config:set TWILIO_PHONE_NUMBER=+81312345678
heroku config:set API_HOST=https://receptra-backend.herokuapp.com

# デプロイ
git init
git add .
git commit -m "Initial commit"
git push heroku main

# ログ確認
heroku logs --tail
```

#### Procfile 作成（Heroku用）

```
web: uvicorn receptra_backend:app --host 0.0.0.0 --port $PORT
```

#### runtime.txt 作成（Python バージョン指定）

```
python-3.11.7
```

### オプション B: AWS Lambda + RDS（スケーラブル）

#### 使用サービス
- **Lambda**: FastAPI（Zapa または Mangum ライブラリ）
- **RDS**: PostgreSQL
- **API Gateway**: REST API エンドポイント
- **CloudFormation**: インフラストラクチャコード

#### デプロイスクリプト例
```bash
# 環境変数設定
export RECEPTRA_DB_URL="postgresql://user:pass@db.amazonaws.com/receptra"
export API_HOST="https://api.receptra.com"

# Zapa を使用した Lambda デプロイ
zapa init
zapa deploy production
zapa update production  # 以降の更新時
```

### オプション C: Docker + VPS（自由度高い）

#### Dockerfile 作成
```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY receptra_backend.py .

CMD ["uvicorn", "receptra_backend:app", "--host", "0.0.0.0", "--port", "8000"]
```

#### Docker Compose
```yaml
version: '3.8'

services:
  api:
    build: .
    ports:
      - "8000:8000"
    environment:
      TWILIO_ACCOUNT_SID: ${TWILIO_ACCOUNT_SID}
      TWILIO_AUTH_TOKEN: ${TWILIO_AUTH_TOKEN}
      TWILIO_PHONE_NUMBER: ${TWILIO_PHONE_NUMBER}
      DATABASE_PATH: /data/receptra.db
    volumes:
      - ./data:/data

  nginx:
    image: nginx:latest
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./ssl:/etc/nginx/ssl:ro
```

#### VPS デプロイ例（Ubuntu）
```bash
# SSH で VPS に接続
ssh ubuntu@your-vps-ip

# 必要なツールインストール
sudo apt update
sudo apt install -y python3.11 python3-pip docker.io

# リポジトリクローン
git clone https://github.com/your-org/receptra-backend.git
cd receptra-backend

# 環境変数設定
cp .env.example .env
nano .env  # 編集

# Docker コンテナ起動
docker-compose up -d

# ログ確認
docker-compose logs -f api
```

---

## ステップ 6: フロントエンド統合

### 6.1 HTML 修正

`receptra.html` に以下を追加：

#### 予約フォームに電話番号フィールド追加
```html
<div class="form-group">
  <label for="reservationPhone">📱 電話番号 *</label>
  <input 
    type="tel" 
    id="reservationPhone" 
    placeholder="09012345678"
    required 
    pattern="0\d{9,10}"
  />
</div>
```

#### JavaScript: 予約送信ロジック
```javascript
async function submitReservation() {
  const formData = {
    phone_number: document.getElementById('reservationPhone').value,
    shop_id: currentState.selectedShop.id,
    shop_name: currentState.selectedShop.name,
    reservation_date: document.getElementById('reservationDate').value,
    reservation_time: document.getElementById('reservationTime').value,
    party_size: parseInt(document.getElementById('reservationParty').value),
    special_requests: document.getElementById('reservationNotes').value,
    source: 'web'
  };

  const response = await fetch('https://api.receptra.com/api/reservations', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(formData)
  });

  const data = await response.json();
  if (data.reservation_id) {
    alert('✅ 予約が完了しました！');
  }
}
```

---

## ステップ 7: 監視＆ロギング

### 7.1 ログ確認

```bash
# ローカル開発
tail -f receptra.log

# Heroku
heroku logs --tail

# AWS CloudWatch
aws logs tail /aws/lambda/receptra-api --follow
```

### 7.2 エラーハンドリング

主要なエラーシナリオ：

| エラー | 原因 | 解決方法 |
|--------|------|--------|
| `Twilio not configured` | `TWILIO_ACCOUNT_SID` 未設定 | 環境変数確認 |
| `403 Forbidden from Webhook` | Twilio 署名検証失敗 | Webhook URL確認 |
| `Call failed: no-answer` | 顧客が出ない | リトライロジック追加 |
| `Database locked` | SQLite 同時アクセス | PostgreSQL に移行 |

---

## ステップ 8: 本番運用チェックリスト

- [ ] Twilio 本番アカウントに切り替え
- [ ] API ホスト名を本番ドメインに変更
- [ ] HTTPS（SSL/TLS）設定
- [ ] データベースバックアップ設定
- [ ] ロギング＆モニタリング設定
- [ ] エラーアラート設定
- [ ] 顧客サポート体制
- [ ] SLA（応答時間）定義

---

## トラブルシューティング

### 問題: Twilio 着信時に 404 エラー

```
Error - 11200
Unable to fetch required params from TwiML Response
```

**解決:**
1. Webhook URL が正しいか確認
2. `ngrok` が実行中か確認
3. サーバーログで詳細エラー確認
4. Twilio Console → Monitor → Log で詳細確認

### 問題: リマインド電話が発信されない

**解決:**
1. APScheduler が起動してるか確認：ログで `Scheduler started`
2. 予約が DB に保存されてるか確認：
   ```bash
   sqlite3 receptra.db "SELECT * FROM reservations LIMIT 1;"
   ```
3. Twilio アカウントの残高確認

### 問題: 日本語音声が流れない

**解決:**
1. TwiML の `language="ja-JP"` を確認
2. Twilio が日本語音声をサポートしてるか確認
3. 別の音声（例：`Mizuki`）を試す：
   ```xml
   <Say language="ja-JP" voice="Mizuki">テスト</Say>
   ```

---

## 参考リンク

- [Twilio Voice API ドキュメント](https://www.twilio.com/docs/voice)
- [TwiML リファレンス](https://www.twilio.com/docs/voice/twiml)
- [FastAPI デプロイメント](https://fastapi.tiangolo.com/deployment/)
- [ngrok ドキュメント](https://ngrok.com/docs)

