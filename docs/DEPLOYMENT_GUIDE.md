# RECEPTRA デプロイメントガイド

## 準備

### 1. 前提条件
- Python 3.9 以上
- Git
- Heroku または Railway アカウント
- Twilio アカウント（オプション - デモモード で試用可能）

### 2. ローカル環境で確認

```bash
# リポジトリをクローン
git clone https://github.com/yourusername/receptra.git
cd receptra

# 仮想環境を作成
python -m venv venv
source venv/bin/activate  # macOS/Linux
# または
.\venv\Scripts\activate  # Windows

# 依存関係をインストール
pip install -r requirements.txt --break-system-packages

# 環境変数を設定
cp .env.example .env
# .env ファイルを編集して実際の値を設定

# データベースを初期化
python init_db.py

# サーバーを起動
python receptra_backend_extended.py
# または
uvicorn receptra_backend_extended:app --reload --port 8000
```

### 3. ローカルでテスト

ブラウザで以下にアクセス:
- フロントエンド: `http://localhost:5500` (Live Server)
- API ドキュメント: `http://localhost:8000/docs`
- ヘルスチェック: `http://localhost:8000/api/health`

---

## Heroku へのデプロイ

### ステップ 1: Heroku CLI をインストール

```bash
# macOS
brew tap heroku/brew && brew install heroku

# Linux
curl https://cli-assets.heroku.com/install.sh | sh

# Windows
# https://devcenter.heroku.com/articles/heroku-cli から インストーラーをダウンロード
```

### ステップ 2: Heroku にログイン

```bash
heroku login
# ブラウザでログイン情報を入力
```

### ステップ 3: アプリケーションを作成

```bash
heroku create receptra-api-yourname
```

### ステップ 4: 環境変数を設定

```bash
# Twilio 認証情報
heroku config:set TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
heroku config:set TWILIO_AUTH_TOKEN=your_auth_token
heroku config:set TWILIO_PHONE_NUMBER=+81312345678

# その他の設定
heroku config:set API_HOST=https://receptra-api-yourname.herokuapp.com
heroku config:set DEBUG=false
heroku config:set SECRET_KEY=$(openssl rand -hex 32)

# 確認
heroku config
```

### ステップ 5: PostgreSQL アドオンを追加（推奨）

```bash
# SQLite の代わりに PostgreSQL を使用
heroku addons:create heroku-postgresql:hobby-dev

# 自動的に DATABASE_URL 環境変数が設定される
```

### ステップ 6: デプロイ

```bash
# Git でコミット
git add .
git commit -m "Prepare for Heroku deployment"

# Heroku にプッシュ
git push heroku main

# ログを確認
heroku logs --tail
```

### ステップ 7: 動作確認

```bash
# ブラウザで開く
heroku open

# または手動で確認
curl https://receptra-api-yourname.herokuapp.com/api/health
```

---

## Railway へのデプロイ

### ステップ 1: Railway CLI をインストール

```bash
# Node.js 経由
npm install -g @railway/cli

# または Homebrew
brew install railway
```

### ステップ 2: Railway にログイン

```bash
railway login
# ブラウザでログイン
```

### ステップ 3: プロジェクトを初期化

```bash
railway init
# プロジェクト名を入力（例: receptra-api）
```

### ステップ 4: 環境変数を設定

```bash
railway variables set TWILIO_ACCOUNT_SID=ACxxxxxxxx
railway variables set TWILIO_AUTH_TOKEN=your_token
railway variables set TWILIO_PHONE_NUMBER=+81312345678
railway variables set DEBUG=false
railway variables set SECRET_KEY=$(openssl rand -hex 32)

# 確認
railway variables
```

### ステップ 5: PostgreSQL をプロビジョニング

```bash
# Railway ダッシュボードで PostgreSQL を追加
# または CLI で
railway add postgres
```

### ステップ 6: デプロイ

```bash
railway up

# または Dockerfile からビルド
railway build && railway deploy
```

### ステップ 7: URL を確認

```bash
railway open
```

---

## Docker でのローカルテスト

### ビルド

```bash
docker build -t receptra-api .
```

### 実行

```bash
docker run \
  -e TWILIO_ACCOUNT_SID=your_sid \
  -e TWILIO_AUTH_TOKEN=your_token \
  -e TWILIO_PHONE_NUMBER=+81312345678 \
  -p 8000:8000 \
  receptra-api
```

### Docker Compose で複数サービス

```yaml
# docker-compose.yml
version: '3.8'

services:
  api:
    build: .
    ports:
      - "8000:8000"
    environment:
      DATABASE_PATH: /data/receptra.db
      TWILIO_ACCOUNT_SID: ${TWILIO_ACCOUNT_SID}
      TWILIO_AUTH_TOKEN: ${TWILIO_AUTH_TOKEN}
    volumes:
      - ./data:/data

  postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: receptra
      POSTGRES_PASSWORD: password
    volumes:
      - postgres_data:/var/lib/postgresql/data

volumes:
  postgres_data:
```

実行:
```bash
docker-compose up -d
```

---

## 本番環境でのベストプラクティス

### 1. データベース
- SQLite ❌ → PostgreSQL/MySQL ✅
- 定期バックアップを設定
- 読み取り専用レプリカを検討

### 2. セキュリティ
- HTTPS のみ使用 ✅
- SSL/TLS 証明書を設定
- ファイアウォールルールを設定
- CORS を制限（ホワイトリスト方式）

### 3. スケーリング
- ロードバランサーを設定
- キャッシング層（Redis）を検討
- CDN で静的ファイルを配信

### 4. 監視・ロギング
- CloudWatch/Datadog でモニタリング
- ロテーションログを設定
- エラー追跡（Sentry）を統合

### 5. バックアップ・リカバリ
- 毎日バックアップ実行
- リカバリープロセスをテスト
- ディザスタリカバリープランを作成

---

## トラブルシューティング

### ビルドエラー

```
Error: No matching distribution found for ...
```

**解決策:**
```bash
pip install --upgrade pip
pip install -r requirements.txt --break-system-packages
```

### Twilio 接続エラー

```
Error: Could not authenticate with Twilio
```

**解決策:**
```bash
# 認証情報を確認
heroku config:get TWILIO_ACCOUNT_SID
heroku config:get TWILIO_AUTH_TOKEN

# Twilio Console で認証情報をリセット
# https://www.twilio.com/console
```

### データベースエラー

```
Error: database is locked
```

**解決策:**
- PostgreSQL に移行
- SQLite WAL モードを有効化

```python
import sqlite3
conn = sqlite3.connect('receptra.db')
conn.execute('PRAGMA journal_mode=WAL;')
```

### メモリ不足

```bash
# Heroku のメモリを確認
heroku dyno:type

# より大きいインスタンスにアップグレード
heroku dyno:type standard-1x
```

---

## パフォーマンス最適化

### 1. キャッシング
```python
# Redis キャッシングを追加
from redis import Redis
cache = Redis(host='localhost', port=6379)
```

### 2. クエリ最適化
```python
# インデックスを追加
# Eager loading を使用
# N+1 クエリを回避
```

### 3. 非同期処理
```python
# バックグラウンドタスク（Celery）
# WebSocket でリアルタイム更新
```

---

## ロールバック

```bash
# Heroku のロールバック
heroku releases
heroku rollback v10  # 特定のバージョンへロール戻し

# Railway のロールバック
railway rollback
```

---

## サポート

何か問題が発生した場合:
1. ログを確認: `heroku logs --tail`
2. API ドキュメント確認: `/docs`
3. GitHub Issues で報告
4. メール: support@receptra.app
