# BARIYON Receptra API - クイックリファレンス

## 🚀 クイックスタート

### 1. サーバー起動
```bash
cd ~/www/receptra
docker-compose up -d
```

### 2. API ドキュメント表示
```
http://localhost:8000/docs
```

### 3. ヘルスチェック
```bash
curl http://localhost:8000/health
```

---

## 📋 一般的な API 操作

### セットアップ
```bash
# ベース URL (以下の例で使用)
BASE_URL="http://localhost:8000"

# 環境変数にトークンを保存
TOKEN=""
```

---

## 🔐 認証フロー

### ステップ 1: 新規テナント・ユーザー登録

```bash
curl -X POST "$BASE_URL/api/v1/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_name": "My Company",
    "tenant_slug": "my-company",
    "email": "admin@mycompany.com",
    "password": "MySecurePassword123!",
    "display_name": "Admin User"
  }' | jq .
```

**応答:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "email": "admin@mycompany.com",
    "display_name": "Admin User",
    "is_admin": true,
    "tenant_id": "550e8400-e29b-41d4-a716-446655440001"
  }
}
```

**トークン保存:**
```bash
TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

---

### ステップ 2: ログイン

```bash
curl -X POST "$BASE_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "admin@mycompany.com",
    "password": "MySecurePassword123!"
  }' | jq .
```

---

### ステップ 3: 現在のユーザー情報取得

```bash
curl -X GET "$BASE_URL/api/v1/auth/me" \
  -H "Authorization: Bearer $TOKEN" | jq .
```

---

## 📊 レスポンス形式

### 成功レスポンス (2xx)

#### 200 OK - リクエスト成功
```json
{
  "status": "ok",
  "data": { ... }
}
```

#### 201 Created - リソース作成成功
```json
{
  "id": "...",
  "status": "created"
}
```

---

### エラーレスポンス (4xx, 5xx)

#### 400 Bad Request - リクエスト形式エラー
```json
{
  "detail": "Invalid request format"
}
```

#### 401 Unauthorized - 認証エラー
```json
{
  "detail": "Invalid authentication credentials"
}
```

#### 403 Forbidden - 権限不足
```json
{
  "detail": "Not enough permissions"
}
```

#### 404 Not Found - リソースなし
```json
{
  "detail": "Resource not found"
}
```

#### 500 Internal Server Error - サーバーエラー
```json
{
  "detail": "Internal server error"
}
```

---

## 🔑 トークン処理

### トークンのデコード（デバッグ用）

Python で検証:
```python
import json
import base64

token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
header, payload, signature = token.split('.')

# ペイロードをデコード（パディング追加）
payload_padded = payload + '=' * (4 - len(payload) % 4)
decoded = json.loads(base64.urlsafe_b64decode(payload_padded))

print(json.dumps(decoded, indent=2))
# 出力:
# {
#   "sub": "550e8400-e29b-41d4-a716-446655440000",
#   "tenant_id": "550e8400-e29b-41d4-a716-446655440001",
#   "exp": 1694193600
# }
```

### トークン有効期限確認

```bash
# exp (expiration) を確認
TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."

# ペイロード部分を抽出（簡易版）
python3 -c "
import json, base64
payload = '$TOKEN'.split('.')[1]
payload_padded = payload + '=' * (4 - len(payload) % 4)
data = json.loads(base64.urlsafe_b64decode(payload_padded))
import datetime
exp_time = datetime.datetime.fromtimestamp(data['exp'])
print(f'Token expires at: {exp_time}')
"
```

---

## 🧪 テストシナリオ

### シナリオ 1: 完全な登録・ログインフロー

```bash
#!/bin/bash
BASE_URL="http://localhost:8000"

# 1. 登録
echo "1️⃣ Registering new user..."
RESPONSE=$(curl -s -X POST "$BASE_URL/api/v1/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_name": "Test Company",
    "tenant_slug": "test-co",
    "email": "test@test-co.com",
    "password": "Test@1234",
    "display_name": "Test User"
  }')

TOKEN=$(echo $RESPONSE | jq -r '.access_token')
echo "✅ Registered. Token: ${TOKEN:0:20}..."

# 2. ユーザー情報取得
echo ""
echo "2️⃣ Fetching user info..."
curl -s -X GET "$BASE_URL/api/v1/auth/me" \
  -H "Authorization: Bearer $TOKEN" | jq .

# 3. ログアウト
echo ""
echo "3️⃣ Logging out..."
curl -s -X POST "$BASE_URL/api/v1/auth/logout" \
  -H "Authorization: Bearer $TOKEN" | jq .

# 4. ログイン
echo ""
echo "4️⃣ Logging back in..."
RESPONSE=$(curl -s -X POST "$BASE_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@test-co.com",
    "password": "Test@1234"
  }')

TOKEN=$(echo $RESPONSE | jq -r '.access_token')
echo "✅ Logged in. New Token: ${TOKEN:0:20}..."
```

### シナリオ 2: エラーハンドリング

```bash
#!/bin/bash
BASE_URL="http://localhost:8000"

echo "🔴 Testing error scenarios..."

# 1. 不正なパスワード
echo ""
echo "1️⃣ Invalid password:"
curl -s -X POST "$BASE_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "admin@test-co.com",
    "password": "wrong"
  }' | jq .

# 2. 存在しないユーザー
echo ""
echo "2️⃣ User not found:"
curl -s -X POST "$BASE_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "nonexistent@test-co.com",
    "password": "anything"
  }' | jq .

# 3. トークンなしで認証が必要なエンドポイントにアクセス
echo ""
echo "3️⃣ Missing authentication:"
curl -s -X GET "$BASE_URL/api/v1/auth/me" | jq .

# 4. 無効なトークン
echo ""
echo "4️⃣ Invalid token:"
curl -s -X GET "$BASE_URL/api/v1/auth/me" \
  -H "Authorization: Bearer invalid_token_12345" | jq .
```

---

## 🔍 デバッグコマンド

### ログ確認

```bash
# リアルタイムログ表示
docker-compose logs -f backend

# 最後の 50 行
docker-compose logs --tail=50 backend

# 特定の時刻以降のログ
docker-compose logs --since 10m backend
```

### データベース確認

```bash
# PostgreSQL 接続
docker-compose exec db psql -U receptra -d receptra

# テナント一覧
SELECT id, name, slug, is_active FROM tenant;

# ユーザー一覧
SELECT id, email, display_name, is_admin, created_at FROM "user";

# 特定テナントのユーザー
SELECT u.id, u.email, u.display_name, u.is_admin 
FROM "user" u 
WHERE u.tenant_id = '550e8400-e29b-41d4-a716-446655440001';
```

### ネットワークテスト

```bash
# エンドポイント疎通確認
curl -v http://localhost:8000/health

# タイムアウト確認
curl --max-time 5 http://localhost:8000/health

# ヘッダー確認
curl -i http://localhost:8000/health
```

---

## 📱 cURL の便利なTips

### ヘッダーの表示
```bash
curl -i http://localhost:8000/health
```

### JSON の整形出力
```bash
curl -s http://localhost:8000/health | jq .
```

### リクエスト・レスポンスの詳細表示
```bash
curl -v http://localhost:8000/health
```

### タイムアウト設定
```bash
curl --max-time 10 http://localhost:8000/health
```

### リトライ（失敗時に3回試行）
```bash
curl --retry 3 http://localhost:8000/health
```

### POST でファイルを送信
```bash
curl -X POST http://localhost:8000/api/v1/endpoint \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/path/to/file.pdf"
```

---

## 🛠️ よくあるトラブル

### Q: "Connection refused"
```
curl: (7) Failed to connect to localhost port 8000: Connection refused
```
**A:** サーバーが起動していません。`docker-compose up -d` で起動してください。

### Q: "Invalid token"
```json
{"detail": "Invalid authentication credentials"}
```
**A:** 
- トークンの有効期限が切れている（24時間）
- トークンが改ざんされている
- 新しくログインしてください

### Q: "Not enough permissions"
```json
{"detail": "Not enough permissions"}
```
**A:** 管理者権限が必要なエンドポイントです。管理者アカウントでログインしてください。

### Q: "Resource not found"
```json
{"detail": "Resource not found"}
```
**A:** 
- 存在しないテナント/ユーザー ID でアクセス
- URL が間違っている
- リソースが削除されている

---

## 💾 データエクスポート

### ユーザー一覧をエクスポート

```bash
docker-compose exec db psql -U receptra -d receptra -c \
  "SELECT id, email, display_name, is_admin, created_at FROM \"user\";" \
  > users.csv
```

### テナント情報をエクスポート

```bash
docker-compose exec db psql -U receptra -d receptra -c \
  "SELECT id, name, slug, is_active, subscription_status FROM tenant;" \
  > tenants.csv
```

---

## 📞 サポート情報

### ヘルスチェック
```bash
curl http://localhost:8000/health
```

### API ドキュメント
```
http://localhost:8000/docs
```

### ReDoc (別形式のドキュメント)
```
http://localhost:8000/redoc
```

### OpenAPI スキーマ
```
http://localhost:8000/openapi.json
```

---

## ✨ まとめ

このガイドで一般的な API 操作がカバーできます。詳細は `/docs` を参照してください。

**Happy Coding! 🎉**
