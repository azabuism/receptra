# Phase 1-B デプロイメント・チェックリスト

## 📋 実装確認チェックリスト

### コードの確認
- [x] `app/main.py` - 認証ルーター登録済み
- [x] `app/core/security.py` - パスワード・JWT実装済み
- [x] `app/models/user.py` - Tenant/User モデル実装済み
- [x] `app/schemas/user.py` - 全スキーマ実装済み
- [x] `app/services/user.py` - TenantService/UserService 実装済み
- [x] `app/routers/auth.py` - 全 API エンドポイント実装済み
- [x] `app/deps.py` - 認証依存性実装済み

### 設定確認
- [x] `.env` ファイル存在確認
- [x] `DATABASE_URL` 設定確認
- [x] `SECRET_KEY` 設定確認
- [x] `CORS_ORIGINS` 設定確認

---

## 🚀 ローカルデプロイメント手順

### ステップ 1: ファイル確認

```bash
cd ~/www/receptra
ls -la app/
ls -la app/core/
ls -la app/models/
ls -la app/schemas/
ls -la app/services/
ls -la app/routers/
```

**確認項目:**
- [ ] `app/main.py` が更新されている
- [ ] `app/core/security.py` が存在
- [ ] `app/models/user.py` が存在
- [ ] `app/schemas/user.py` が存在
- [ ] `app/services/user.py` が存在
- [ ] `app/routers/auth.py` が存在
- [ ] `app/deps.py` が存在

### ステップ 2: Docker イメージ再構築

```bash
# 既存コンテナを停止・削除
docker-compose down

# イメージを完全削除（キャッシュなし）
docker image rm receptra-backend:latest || true

# 再構築（--no-cache で依存関係を再解決）
docker-compose build --no-cache

# ログを確認しながら起動
docker-compose up -d
sleep 5
docker-compose logs backend
```

**期待される出力:**
```
backend_1  | 🚀 BARIYON Receptra API starting up...
backend_1  | ✅ Database tables initialized
backend_1  | ✅ FastAPI application created successfully
backend_1  | INFO:     Uvicorn running on http://0.0.0.0:8000
```

### ステップ 3: ヘルスチェック

```bash
# ヘルスチェック API
curl http://localhost:8000/health

# 期待される応答 (200 OK)
# {
#   "status": "ok",
#   "service": "BARIYON Receptra API",
#   "version": "1.0.0",
#   "environment": "development"
# }
```

### ステップ 4: API ドキュメント確認

ブラウザで以下にアクセス:
```
http://localhost:8000/docs
```

**確認項目:**
- [ ] Swagger UI が表示される
- [ ] `/api/v1/auth/register` エンドポイントが表示
- [ ] `/api/v1/auth/login` エンドポイントが表示
- [ ] `/api/v1/auth/me` エンドポイントが表示
- [ ] `/health` エンドポイントが表示

---

## 🧪 機能テスト

### テスト 1: ユーザー登録

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_name": "Test Company",
    "tenant_slug": "test-company",
    "email": "admin@test-company.com",
    "password": "Test@12345",
    "display_name": "Admin User"
  }' | jq .
```

**期待される応答:**
```json
{
  "access_token": "...",
  "token_type": "bearer",
  "user": {
    "id": "...",
    "email": "admin@test-company.com",
    "display_name": "Admin User",
    "is_admin": true,
    "tenant_id": "..."
  }
}
```

**テストケース:**
- [ ] 新規テナント・ユーザー作成成功 (200)
- [ ] レスポンスに token が含まれている
- [ ] token_type が "bearer"
- [ ] ユーザー情報が正しい

### テスト 2: ログイン

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "admin@test-company.com",
    "password": "Test@12345"
  }' | jq .
```

**テストケース:**
- [ ] ログイン成功 (200)
- [ ] 新しい token が発行される
- [ ] 前回と異なる token

### テスト 3: 認証済みアクセス

```bash
# 登録時の token を使用
TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."

curl -X GET http://localhost:8000/api/v1/auth/me \
  -H "Authorization: Bearer $TOKEN" | jq .
```

**期待される応答:**
```json
{
  "id": "...",
  "email": "admin@test-company.com",
  "display_name": "Admin User",
  "is_admin": true,
  "tenant": {
    "id": "...",
    "name": "Test Company",
    "slug": "test-company",
    ...
  }
}
```

**テストケース:**
- [ ] 認証済みアクセス成功 (200)
- [ ] ユーザー情報取得成功
- [ ] テナント情報が含まれている

### テスト 4: エラーハンドリング

```bash
# 無効なトークン
curl -X GET http://localhost:8000/api/v1/auth/me \
  -H "Authorization: Bearer invalid" | jq .
```

**期待される応答:** (401 Unauthorized)
```json
{
  "detail": "Invalid authentication credentials"
}
```

**テストケース:**
- [ ] 無効なトークン → 401 Unauthorized
- [ ] トークンなし → 401 Unauthorized
- [ ] 不正なパスワード → 401 Unauthorized
- [ ] 存在しないユーザー → 401 Unauthorized

### テスト 5: データベース検証

```bash
# PostgreSQL に接続
docker-compose exec db psql -U receptra -d receptra

# SQL でテーブル確認
\d tenant
\d "user"

# データ確認
SELECT id, name, slug FROM tenant;
SELECT id, email, display_name, is_admin FROM "user";
```

**確認項目:**
- [ ] Tenant テーブルが存在
- [ ] User テーブルが存在
- [ ] 作成したテナント・ユーザーが表示される
- [ ] パスワードがハッシュ化されている

---

## 🔒 セキュリティチェック

### チェック 1: パスワードハッシング

```bash
# DB でパスワードハッシュ確認
docker-compose exec db psql -U receptra -d receptra -c \
  "SELECT email, password_hash FROM \"user\" LIMIT 1;"
```

**確認:**
- [ ] password_hash が `$2b$...` で始まる (bcrypt)
- [ ] password_hash が平文でない
- [ ] password_hash が30文字以上

### チェック 2: JWT トークン

```bash
# トークンをデコード（Python）
python3 << 'EOF'
import json, base64

token = "YOUR_TOKEN_HERE"
payload = token.split('.')[1]
payload_padded = payload + '=' * (4 - len(payload) % 4)
data = json.loads(base64.urlsafe_b64decode(payload_padded))
print(json.dumps(data, indent=2))
EOF
```

**確認:**
- [ ] `sub` (user_id) が含まれている
- [ ] `tenant_id` が含まれている
- [ ] `exp` (有効期限) が含まれている
- [ ] 現在時刻 < `exp`

### チェック 3: マルチテナント分離

```bash
# 複数テナントを作成して分離を確認
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_name": "Company A",
    "tenant_slug": "company-a",
    "email": "admin@company-a.com",
    "password": "Pass123",
    "display_name": "Admin A"
  }' | jq '.user.tenant_id' > tenant_a.txt

curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_name": "Company B",
    "tenant_slug": "company-b",
    "email": "admin@company-b.com",
    "password": "Pass123",
    "display_name": "Admin B"
  }' | jq '.user.tenant_id' > tenant_b.txt

# 異なるテナント ID か確認
diff tenant_a.txt tenant_b.txt
```

**確認:**
- [ ] 異なるテナント ID が作成される
- [ ] Company A のユーザーが Company B のテナント ID を持たない

---

## 📊 パフォーマンスチェック

### レスポンスタイム測定

```bash
# ログイン レスポンスタイム
time curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "admin@test-company.com",
    "password": "Test@12345"
  }' > /dev/null

# 期待値: < 500ms
```

### 負荷テスト（軽量版）

```bash
# ab コマンドでベンチマーク（Apache Bench）
# インストール: brew install httpd (macOS) or apt install apache2-utils (Linux)

ab -n 100 -c 10 http://localhost:8000/health
```

**確認:**
- [ ] Requests per second: > 50
- [ ] Failed requests: 0
- [ ] Mean time per request: < 100ms

---

## 📝 ログ確認

### アプリケーションログ

```bash
# リアルタイムログ表示
docker-compose logs -f backend

# 最後の 100 行
docker-compose logs --tail=100 backend

# エラーログのみ
docker-compose logs backend | grep -i error
```

### データベースログ

```bash
# PostgreSQL ログ（有効な場合）
docker-compose logs db
```

---

## 🐛 トラブルシューティング

### 問題 1: "テーブルが見つからない"

**症状:**
```
sqlalchemy.exc.OperationalError: (psycopg2.OperationalError)
relation "tenant" does not exist
```

**原因:** Lifespan で DB 初期化されていない

**解決策:**
```bash
# 1. ログを確認
docker-compose logs backend | grep -i "database\|error"

# 2. データベースが起動しているか確認
docker-compose ps db

# 3. DB に接続可能か確認
docker-compose exec db psql -U receptra -d receptra -c "SELECT 1;"

# 4. 強制再構築
docker-compose down -v  # ボリュームも削除
docker-compose build --no-cache
docker-compose up -d
```

### 問題 2: "ポート 8000 が使用中"

**症状:**
```
OSError: [Errno 48] Address already in use
```

**解決策:**
```bash
# 既存プロセスを終了
lsof -ti:8000 | xargs kill -9

# または別のポートを使用
docker-compose down
# docker-compose.yml でポート変更
docker-compose up -d
```

### 問題 3: "Connection refused"

**症状:**
```
curl: (7) Failed to connect to localhost port 8000
```

**解決策:**
```bash
# サーバーが起動しているか確認
docker-compose ps

# ログを確認
docker-compose logs backend

# 起動していない場合は起動
docker-compose up -d

# 起動に失敗している場合は再構築
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

---

## 📚 ドキュメント確認

- [x] Phase-1B-Implementation-Status.md - 詳細な実装情報
- [x] Phase-1B-Complete-Testing-Guide.md - テスト方法
- [x] API-Quick-Reference.md - API リファレンス
- [x] main-py-changes-summary.md - main.py の変更内容

---

## ✅ デプロイメント完了チェック

すべてが完了したら、以下を確認:

```bash
# 1. サーバー起動確認
curl http://localhost:8000/health

# 2. API ドキュメント確認
# http://localhost:8000/docs

# 3. ユーザー登録テスト
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_name": "Final Test",
    "tenant_slug": "final-test",
    "email": "final@test.com",
    "password": "Test@123",
    "display_name": "Final Test User"
  }' | jq '.access_token' && echo "✅ Registration successful"

# 4. データベース確認
docker-compose exec db psql -U receptra -d receptra -c \
  "SELECT COUNT(*) as tenant_count FROM tenant; SELECT COUNT(*) as user_count FROM \"user\";"
```

---

## 🎯 次のステップ

Phase 1-B のデプロイメントが完了したら、以下の進捗に進んでください:

### Phase 1-C: トークンリフレッシュ・セッション管理
- [ ] リフレッシュトークン実装
- [ ] トークンブラックリスト
- [ ] ログアウト機能強化

### Phase 2: メール認証
- [ ] メール検証機能
- [ ] パスワードリセット

### Phase 3: ドメイン設定
- [ ] ムームードメインで DNS 設定
- [ ] SSL 証明書設定
- [ ] API エンドポイント公開

---

## 📞 サポート

### よくある質問

**Q: 本番環境での SECRET_KEY は?**
A: 強力なランダム文字列に変更してください
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

**Q: CORS_ORIGINS は何に設定すべき?**
A: フロントエンドのドメインを指定
```
CORS_ORIGINS=https://app.receptra.bariyon.com,https://admin.receptra.bariyon.com
```

**Q: パスワードポリシーは?**
A: 現在は制限なし。Phase 2 で追加予定

**Q: トークン有効期限は変更可能?**
A: `app/routers/auth.py` の `create_access_token()` 呼び出し時に `expires_in_hours` パラメータで変更可能

---

## 🎉 完了！

**Phase 1-B: 認証システム** のデプロイメントが完了しました！

次のコマンドで確認してください:

```bash
cd ~/www/receptra
docker-compose ps
curl http://localhost:8000/docs
```

Happy Coding! 🚀

---

**Last Updated:** 2026-09-08
**Status:** ✅ READY FOR DEPLOYMENT
