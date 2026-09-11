# BARIYON Receptra

24時間対応のAI受付スタッフプラットフォーム

## 🚀 クイックスタート（5分）

### 前提条件
- Docker & Docker Compose (v24.0以上)
- Python 3.11+ (ローカル開発の場合)
- 環境変数が設定されていること

### セットアップ

```bash
# 1. 環境ファイルを作成
cp .env.example .env

# 2. .env を編集して API キーを設定
# - OPENAI_API_KEY
# - PAYJP_API_KEY

# 3. Docker コンテナを起動
docker-compose up -d

# 4. 動作確認
curl http://localhost:8000/health
```

## 📍 アクセス方法

| サービス | URL | 説明 |
|---------|-----|------|
| API | http://localhost:8000 | FastAPI サーバー |
| API Docs | http://localhost:8000/docs | Swagger UI |
| Admin | http://localhost:3000 | 管理画面（Next.js） |
| Widget Dev | http://localhost:3001 | チャットウィジェット |
| DB | localhost:5432 | PostgreSQL |
| Redis | localhost:6379 | Redis |

## 📁 プロジェクト構造

```
receptra/
├── app/              # FastAPI アプリケーション
│   ├── main.py       # エントリポイント
│   ├── config.py     # 環境設定
│   ├── database.py   # DB接続
│   ├── deps.py       # 依存性注入
│   ├── models/       # SQLAlchemy ORM
│   ├── schemas/      # Pydantic スキーマ
│   ├── services/     # ビジネスロジック
│   └── routers/      # APIエンドポイント
├── tests/            # テストコード
├── docker/           # Docker関連
├── alembic/          # DBマイグレーション
├── frontend/         # Next.js 管理画面
├── widget/           # チャットウィジェット
└── docs/             # ドキュメント
```

## 🛠️ 開発

### コンテナ内でコマンド実行

```bash
# Python パッケージのインストール
docker-compose exec backend pip install -r requirements.txt

# データベースマイグレーション
docker-compose exec backend alembic upgrade head

# テスト実行
docker-compose exec backend pytest tests/ -v --cov=app
```

### ログ確認

```bash
# 全サービスのログを表示
docker-compose logs -f

# 特定サービスのログのみ
docker-compose logs -f backend
```

### コンテナの停止・削除

```bash
# 停止
docker-compose down

# コンテナとボリュームを削除
docker-compose down -v
```

## 📚 ドキュメント

- [設計書](./docs/) - アーキテクチャ、ER図、API仕様
- [実装ガイド](./docs/IMPLEMENTATION.md) - 実装フェーズ別ガイド
- [デプロイメント](./docs/DEPLOYMENT.md) - Cloud Run デプロイ手順
- [API仕様](./docs/API.md) - API エンドポイント一覧

## 🔐 環境変数

`.env.example` を参照して、以下の情報を設定してください：

- **OPENAI_API_KEY** - OpenAI API キー（必須）
- **PAYJP_API_KEY** - PAY.JP API キー（必須）
- **SECRET_KEY** - JWT シークレットキー（本番環境では変更必須）

## 🚢 デプロイ

本番環境へのデプロイは以下のドキュメントを参照：

- [Staging 環境](./docs/DEPLOYMENT.md#2-ステージング環境デプロイ)
- [本番環境](./docs/DEPLOYMENT.md#3-本番環境デプロイ)

## 📝 変更履歴

- **2026-09-08** - v1.0.0 初版リリース

## 📞 サポート

問題が発生した場合は、[トラブルシューティング](./docs/DEPLOYMENT.md#7-トラブルシューティング)を確認してください。

---

**作成者**: BARIYON  
**ライセンス**: Proprietary
