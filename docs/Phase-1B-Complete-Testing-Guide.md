# Phase 1-B 認証システム実装完了ガイド

## 📋 実装状況

### ✅ 完了した実装

1. **app/main.py** - 更新完了
   - ✅ 認証ルーター (`auth_router`) を import
   - ✅ SQLAlchemy Base をimport
   - ✅ ルーター登録: `/api/v1/auth` プレフィックス
   - ✅ lifespan コンテキストマネージャーでデータベース初期化
   - ✅ 不要な import を削除

2. **app/core/security.py** - パスワード・JWT実装
   - ✅ Bcrypt パスワードハッシング
   - ✅ JWT トークン生成・検証
   - ✅ TokenData モデル

3. **app/models/user.py** - ユーザー・テナントモデル
   - ✅ Tenant モデル（マルチテナント対応）
   - ✅ User モデル（テナント外部キー、ユニーク制約）

4. **app/schemas/user.py** - リクエスト・レスポンススキーマ
   - ✅ Tenant スキーマ
   - ✅ User スキーマ
   - ✅ Token レスポンススキーマ

5. **app/services/user.py** - ビジネスロジック
   - ✅ TenantService - テナント作成・取得
   - ✅ UserService - ユーザー認証・管理

6. **app/routers/auth.py** - APIエンドポイント
   - ✅ POST /register - ユーザー登録
   - ✅ POST /login - ログイン
   - ✅ GET /me - 現在のユーザー情報

7. **app/deps.py** - 依存性注入
   - ✅ get_db() - DB セッション
   - ✅ get_current_user() - JWT認証
   - ✅ get_current_admin() - 管理者認証
   - ✅ get_current_tenant() - テナント情報

---

## 🚀 ローカルテスト手順

### 1️⃣ Docker 再構築

```bash
cd ~/www/receptra

# 既存コンテナを停止・削除
docker-compose down

# キャッシュなしで再構築
docker-compose build --no-cache

# 起動
docker-compose up -d

# ステータス確認
docker-compose ps
```

### 2️⃣ ヘルスチェック

```bash
curl http://localhost:8000/health
```

**期待される応答:**
```json
{
  "status": "ok",
  "service": "BARIYON Receptra API",
  "version": "1.0.0",
  "environment": "development"
}
```

### 3️⃣ API ドキュメント

```
http://localhost:8000/docs
```

Swagger UI ですべてのエンドポイントが表示されます。

---

## 🧪 認証エンドポイントテスト

### テスト A: テナント・ユーザー登録

**リクエスト:**
```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_name": "テスト会社",
    "tenant_slug": "test-company",
    "email": "admin@test-company.com",
    "password": "securePassword123!",
    "display_name": "テスト太郎"
  }'
```

**期待される応答:** (200 OK)
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "user": {
    "id": "uuid-here",
    "email": "admin@test-company.com",
    "display_name": "テスト太郎",
    "is_admin": true,
    "tenant_id": "uuid-here"
  }
}
```

### テスト B: ログイン

**リクエスト:**
```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "admin@test-company.com",
    "password": "securePassword123!"
  }'
```

**期待される応答:** (200 OK)
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "user": {
    "id": "uuid-here",
    "email": "admin@test-company.com",
    "display_name": "テスト太郎",
    "is_admin": true,
    "tenant_id": "uuid-here"
  }
}
```

### テスト C: 現在のユーザー情報取得

**リクエスト:**
```bash
# ログインで得たトークンを使用
TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."

curl -X GET http://localhost:8000/api/v1/auth/me \
  -H "Authorization: Bearer $TOKEN"
```

**期待される応答:** (200 OK)
```json
{
  "id": "uuid-here",
  "email": "admin@test-company.com",
  "display_name": "テスト太郎",
  "is_admin": true,
  "tenant": {
    "id": "uuid-here",
    "name": "テスト会社",
    "slug": "test-company",
    "email": "admin@test-company.com",
    "is_active": true,
    "subscription_status": "trial"
  }
}
```

### テスト D: エラーハンドリング

**不正なトークン:**
```bash
curl -X GET http://localhost:8000/api/v1/auth/me \
  -H "Authorization: Bearer invalid_token"
```

**期待される応答:** (401 Unauthorized)
```json
{
  "detail": "Invalid authentication credentials"
}
```

**存在しないユーザーでログイン:**
```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "notfound@example.com",
    "password": "anypassword"
  }'
```

**期待される応答:** (401 Unauthorized)
```json
{
  "detail": "Invalid email or password"
}
```

---

## 📊 データベース確認

### PostgreSQL にログイン

```bash
docker-compose exec db psql -U receptra -d receptra
```

### テーブル確認

```sql
-- テナントテーブル
\d tenant

-- ユーザーテーブル
\d "user"

-- データ確認
SELECT id, name, slug FROM tenant;
SELECT id, email, tenant_id, is_admin FROM "user";
```

---

## 🔐 セキュリティチェックリスト

- ✅ パスワードハッシング: bcrypt (CryptContext)
- ✅ JWT 署名: HS256 アルゴリズム
- ✅ トークン有効期限: 24時間
- ✅ マルチテナント分離: tenant_id ForeignKey + ユニーク制約
- ✅ パスワード送信: HTTPS/TLS 推奨（本番環境）
- ✅ トークンリフレッシュ: 実装待ち（Phase 2）

---

## 📝 環境変数確認

`.env` ファイルで以下を確認してください:

```bash
# データーベース
DATABASE_URL=postgresql+asyncpg://receptra:receptra@db:5432/receptra

# JWT シークレット
SECRET_KEY=your-super-secret-key-change-in-production

# 環境
ENVIRONMENT=development

# CORS オリジン
CORS_ORIGINS=http://localhost:3000,http://localhost:8080
```

---

## ⚠️ トラブルシューティング

### エラー 1: "テーブルが見つからない"
```
sqlalchemy.exc.OperationalError: (psycopg2.OperationalError)
relation "tenant" does not exist
```

**解決策:** lifespan で自動生成されます。ログを確認:
```bash
docker-compose logs backend
```

### エラー 2: "認証トークンが無効"
```json
{"detail": "Invalid authentication credentials"}
```

**確認:**
- トークン形式: `Bearer <token>`
- SECRET_KEY が一致しているか
- トークンの有効期限

### エラー 3: "SQLAlchemy ドライバエラー"
Alembic 問題の場合は `alembic/env.py` の DATABASE_URL 設定を確認

---

## 🎯 次のステップ (Phase 1-C)

1. **トークンリフレッシュ機能**
   - リフレッシュトークン実装
   - アクセストークン短命化

2. **メール認証**
   - メール検証リンク送信
   - メール確認エンドポイント

3. **パスワードリセット**
   - リセットトークン生成
   - パスワード変更エンドポイント

4. **セッション管理**
   - ログアウト機能
   - トークンブラックリスト

---

## 📚 参考資料

- FastAPI 認証: https://fastapi.tiangolo.com/tutorial/security/
- SQLAlchemy 非同期: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
- JWT 仕様: https://tools.ietf.org/html/rfc7519

---

## ✨ 実装完了！

**Phase 1-B: 認証システム** が完了しました。

次のコマンドでテストを開始してください:

```bash
cd ~/www/receptra
docker-compose down
docker-compose build --no-cache
docker-compose up -d
curl http://localhost:8000/health
```

すべてのエンドポイントは `/docs` で確認可能です！🎉
