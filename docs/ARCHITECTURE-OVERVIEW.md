# BARIYON Receptra - アーキテクチャ概要

## 📐 全体アーキテクチャ

```
┌─────────────────────────────────────────────────────────────┐
│                     クライアント (Web/Mobile)                 │
│              http://localhost:8000/api/v1/auth              │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI アプリケーション                    │
│                    (app/main.py)                            │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ ミドルウェア層                                          │  │
│  │ ├─ CORSMiddleware                                   │  │
│  │ └─ Lifespan (DB初期化)                              │  │
│  └──────────────────────────────────────────────────────┘  │
│                         │                                    │
│  ┌──────────────────────┴──────────────────────────────┐   │
│  │ ルーティング層 (app/routers/auth.py)                │   │
│  │ ├─ POST /api/v1/auth/register                      │   │
│  │ ├─ POST /api/v1/auth/login                         │   │
│  │ ├─ GET  /api/v1/auth/me                            │   │
│  │ ├─ POST /api/v1/auth/logout                        │   │
│  │ └─ GET  /api/v1/auth/health                        │   │
│  └──────────────────────┬──────────────────────────────┘   │
│                         │                                    │
│  ┌──────────────────────┴──────────────────────────────┐   │
│  │ 依存性注入層 (app/deps.py)                          │   │
│  │ ├─ get_db()              [DB セッション]           │   │
│  │ ├─ get_current_user()    [JWT 検証]                │   │
│  │ ├─ get_current_admin()   [管理者検証]              │   │
│  │ ├─ get_current_tenant()  [テナント情報]            │   │
│  │ └─ get_optional_current_user() [オプション認証]    │   │
│  └──────────────────────┬──────────────────────────────┘   │
│                         │                                    │
│  ┌──────────────────────┴──────────────────────────────┐   │
│  │ ビジネスロジック層 (app/services/)                  │   │
│  │ ├─ TenantService (app/services/user.py)           │   │
│  │ │  ├─ create_tenant()                             │   │
│  │ │  ├─ get_tenant_by_id()                          │   │
│  │ │  └─ get_tenant_by_slug()                        │   │
│  │ │                                                   │   │
│  │ └─ UserService (app/services/user.py)             │   │
│  │    ├─ create_user()                               │   │
│  │    ├─ get_user_by_id()                            │   │
│  │    ├─ get_user_by_email()                         │   │
│  │    ├─ authenticate_user()                         │   │
│  │    ├─ update_user()                               │   │
│  │    └─ get_tenant_users()                          │   │
│  └──────────────────────┬──────────────────────────────┘   │
│                         │                                    │
│  ┌──────────────────────┴──────────────────────────────┐   │
│  │ スキーマ・検証層 (app/schemas/user.py)             │   │
│  │ ├─ Tenant スキーマ (Create/Update/Response)        │   │
│  │ ├─ User スキーマ (Create/Login/Response)           │   │
│  │ ├─ Token スキーマ (TokenResponse/TokenData)        │   │
│  │ └─ CurrentUser スキーマ                            │   │
│  └──────────────────────┬──────────────────────────────┘   │
│                         │                                    │
│  ┌──────────────────────┴──────────────────────────────┐   │
│  │ セキュリティ層 (app/core/security.py)              │   │
│  │ ├─ hash_password()    [bcrypt ハッシング]          │   │
│  │ ├─ verify_password()  [パスワード検証]             │   │
│  │ ├─ create_access_token() [JWT生成]                │   │
│  │ └─ verify_token()     [JWT検証]                    │   │
│  └──────────────────────┬──────────────────────────────┘   │
│                         │                                    │
│  ┌──────────────────────┴──────────────────────────────┐   │
│  │ データモデル層 (app/models/user.py)                │   │
│  │ ├─ Tenant モデル (SQLAlchemy ORM)                  │   │
│  │ └─ User モデル (SQLAlchemy ORM)                    │   │
│  └──────────────────────┬──────────────────────────────┘   │
└─────────────────────────┼────────────────────────────────────┘
                          │
                          ▼
          ┌───────────────────────────────┐
          │   PostgreSQL Database         │
          │  ┌─────────────────────────┐  │
          │  │ Tenant Table            │  │
          │  │ - id (UUID)             │  │
          │  │ - name, slug            │  │
          │  │ - email, status         │  │
          │  │ - created_at, ...       │  │
          │  └─────────────────────────┘  │
          │  ┌─────────────────────────┐  │
          │  │ User Table              │  │
          │  │ - id (UUID)             │  │
          │  │ - tenant_id (FK)        │  │
          │  │ - email, password_hash  │  │
          │  │ - created_at, ...       │  │
          │  └─────────────────────────┘  │
          └───────────────────────────────┘
```

---

## 🔄 認証フロー

### 1️⃣ ユーザー登録フロー

```
クライアント                      FastAPI                  Database
    │                               │                          │
    │ POST /api/v1/auth/register   │                          │
    │──────────────────────────────>│                          │
    │  (tenant_name, email,password) │                         │
    │                               │                          │
    │                               │ ① validate input        │
    │                               ├─────────────────────>   │
    │                               │                          │
    │                               │ ② hash password         │
    │                               ├─────────────────────>   │
    │                               │                          │
    │                               │ ③ create tenant        │
    │                               ├─────────────────────>   │
    │                               │  INSERT Tenant          │
    │                               │  CREATE TABLE user      │
    │                               │<─────────────────────   │
    │                               │                          │
    │                               │ ④ create user          │
    │                               ├─────────────────────>   │
    │                               │  INSERT User            │
    │                               │<─────────────────────   │
    │                               │                          │
    │                               │ ⑤ generate JWT         │
    │                               ├─────────────────────>   │
    │                               │                          │
    │  TokenResponse (token, user)  │                          │
    │<──────────────────────────────┤                          │
    │  {                            │                          │
    │    "access_token": "...",    │                          │
    │    "token_type": "bearer",   │                          │
    │    "user": { ... }           │                          │
    │  }                            │                          │
```

### 2️⃣ ログインフロー

```
クライアント                      FastAPI                  Database
    │                               │                          │
    │ POST /api/v1/auth/login      │                          │
    │──────────────────────────────>│                          │
    │  (email, password)            │                          │
    │                               │                          │
    │                               │ ① find user            │
    │                               ├─────────────────────>   │
    │                               │  SELECT * FROM user     │
    │                               │  WHERE email = ?        │
    │                               │<─────────────────────   │
    │                               │  UserRecord             │
    │                               │                          │
    │                               │ ② verify password      │
    │                               │  (bcrypt compare)       │
    │                               │                          │
    │                               │ ③ generate JWT         │
    │                               │  (with tenant_id)       │
    │                               │                          │
    │  TokenResponse                │                          │
    │<──────────────────────────────┤                          │
```

### 3️⃣ 認証済みアクセスフロー

```
クライアント                      FastAPI                  Database
    │                               │                          │
    │ GET /api/v1/auth/me          │                          │
    │ Authorization: Bearer ...    │                          │
    │──────────────────────────────>│                          │
    │                               │                          │
    │                               │ ① extract token       │
    │                               │  from headers          │
    │                               │                          │
    │                               │ ② verify JWT          │
    │                               │  (check signature,     │
    │                               │   expiration, etc.)    │
    │                               │                          │
    │                               │ ③ get user & tenant  │
    │                               ├─────────────────────>   │
    │                               │  SELECT * FROM user    │
    │                               │  WHERE id = ?          │
    │                               │  JOIN tenant ...       │
    │                               │<─────────────────────   │
    │                               │  UserRecord            │
    │                               │                          │
    │  UserDetailResponse           │                          │
    │<──────────────────────────────┤                          │
```

---

## 📦 ファイル構造

```
app/
├── __init__.py
│   └── User, Tenant models import
│
├── main.py (UPDATED)
│   ├── FastAPI インスタンス作成
│   ├── CORS ミドルウェア
│   ├── Lifespan (DB初期化)
│   ├── Auth ルーター登録 ← NEW
│   ├── Health check エンドポイント
│   └── Root エンドポイント
│
├── config.py
│   └── Settings (DATABASE_URL, SECRET_KEY, etc.)
│
├── core/
│   └── security.py (NEW)
│       ├── CryptContext (bcrypt)
│       ├── hash_password()
│       ├── verify_password()
│       ├── create_access_token()
│       ├── verify_token()
│       └── TokenData
│
├── models/
│   ├── __init__.py
│   │   └── User, Tenant export
│   └── user.py (NEW)
│       ├── Base (SQLAlchemy declarative base)
│       ├── Tenant (ORM model)
│       └── User (ORM model)
│
├── schemas/
│   ├── __init__.py
│   │   └── Pydantic schemas export
│   └── user.py (NEW)
│       ├── TenantBase, Create, Update, Response
│       ├── UserBase, Create, Login, Response
│       ├── TokenResponse
│       ├── TokenData
│       ├── CurrentUser
│       └── UserDetailResponse
│
├── services/
│   ├── __init__.py
│   │   └── Service classes export
│   └── user.py (NEW)
│       ├── TenantService
│       │   ├── create_tenant()
│       │   ├── get_tenant_by_id()
│       │   └── get_tenant_by_slug()
│       └── UserService
│           ├── create_user()
│           ├── get_user_by_id()
│           ├── get_user_by_email()
│           ├── authenticate_user()
│           ├── update_user()
│           └── get_tenant_users()
│
├── routers/
│   ├── __init__.py
│   │   └── auth_router export
│   └── auth.py (NEW)
│       ├── POST /register
│       ├── POST /login
│       ├── GET /me
│       ├── POST /logout
│       └── GET /health
│
├── deps.py (NEW)
│   ├── get_db()
│   ├── get_current_user()
│   ├── get_current_admin()
│   ├── get_current_tenant()
│   └── get_optional_current_user()
│
└── (その他の app 内ファイル)
```

---

## 🔐 データセキュリティ

### パスワード保護

```
ユーザー入力: "MyPassword123"
     │
     ▼
  hash_password()
  (CryptContext - bcrypt)
     │
     ▼
  bcrypt hash: $2b$12$...50文字...
     │
     ▼
  Database に保存
```

### JWT トークン

```
ペイロード:
{
  "sub": "user-id",           # ユーザーID
  "tenant_id": "tenant-id",   # テナントID
  "exp": 1694193600           # 有効期限 (Unix timestamp)
}
     │
     ▼
  署名 (HS256 + SECRET_KEY)
     │
     ▼
  Encoded JWT:
  eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

### マルチテナント分離

```
User テーブル:
┌────────────────────────────────────┐
│ id  │ tenant_id │ email      │ ... │
├─────┼───────────┼────────────┼─────┤
│ u1  │ t1        │ a@t1.com   │ ... │
│ u2  │ t1        │ b@t1.com   │ ... │
│ u3  │ t2        │ c@t2.com   │ ... │
└────────────────────────────────────┘

t1 のユーザーは u1, u2 のみアクセス可能
t2 のユーザーは u3 のみアクセス可能

JWT の tenant_id で自動フィルタリング
```

---

## 🚀 デプロイメントフロー

```
ローカル開発                   Docker コンテナ            本番環境
   │                               │                         │
   │ 編集・テスト                   │                         │
   ├─>  git push                  │                         │
   │                               │                         │
   │                     build     │                         │
   │<──────────────────────────────┤                         │
   │                               │                         │
   │                     docker-compose up                   │
   │<───────────────────────────────┤                        │
   │                               │                         │
   │                   localhost:8000                         │
   │<───────────────────────────────┤                        │
   │                               │                         │
   │ テスト・検証 OK               │                         │
   └────────────────>             │                         │
                                  │                         │
                                  │ push to registry        │
                                  ├────────────────────────>│
                                  │                         │
                                  │                    deploy
                                  │                    ├─────>
                                  │                         │
                                  │              api.receptra.bariyon.com
                                  │                    ├─────>
```

---

## 📊 データフロー例

### 完全なリクエスト・レスポンスサイクル

```
1. クライアント
   POST /api/v1/auth/login
   Content-Type: application/json
   {
     "email": "user@example.com",
     "password": "password123"
   }

2. FastAPI (app/routers/auth.py)
   ├─ リクエスト検証 (Pydantic)
   ├─ dependency injection で db セッション取得
   ├─ UserService.authenticate_user() を呼び出し

3. Service レイヤー (app/services/user.py)
   ├─ DB からユーザー検索
   ├─ パスワード検証
   └─ 認証成功時、ユーザー情報を返す

4. Security レイヤー (app/core/security.py)
   ├─ JWT トークンを生成
   └─ トークンを返す

5. FastAPI Response
   ├─ TokenResponse をシリアライズ
   └─ JSON レスポンス

6. HTTP Response
   200 OK
   Content-Type: application/json
   {
     "access_token": "eyJhbGc...",
     "token_type": "bearer",
     "user": {
       "id": "...",
       "email": "user@example.com",
       ...
     }
   }

7. クライアント
   ├─ access_token を保存
   ├─ 以後のリクエストで Authorization ヘッダーに含める
   └─ Authorization: Bearer eyJhbGc...
```

---

## 🔗 依存関係グラフ

```
main.py
  ├─> routers/auth.py
  │    ├─> deps.py (get_db, get_current_user)
  │    ├─> services/user.py
  │    │    ├─> models/user.py (User, Tenant)
  │    │    ├─> core/security.py
  │    │    └─> config.py
  │    └─> schemas/user.py
  │
  ├─> models/user.py (Base)
  │
  ├─> config.py (get_settings)
  │
  └─> (middleware)

deps.py
  ├─> models/user.py
  ├─> config.py
  ├─> core/security.py
  └─> schemas/user.py

services/user.py
  ├─> models/user.py
  ├─> schemas/user.py
  └─> core/security.py

core/security.py
  ├─> schemas/user.py
  └─> config.py
```

---

## 📈 スケーリング考慮事項

### 現在（Phase 1-B）
- ✅ シンプルな認証
- ✅ 1 つの DB インスタンス
- ✅ JWT ベース（ステートレス）
- ✅ マルチテナント対応

### 将来（Phase 2-3）
- ⏳ Redis キャッシング
- ⏳ トークンブラックリスト
- ⏳ リフレッシュトークン
- ⏳ DB レプリケーション
- ⏳ 負荷分散
- ⏳ 監査ログ

---

## ✨ 実装のハイライト

### ✅ 設計パターン

| パターン | 実装 | 場所 |
|---------|------|------|
| Dependency Injection | FastAPI Depends | deps.py |
| Service Layer | TenantService, UserService | services/user.py |
| Schema Validation | Pydantic Models | schemas/user.py |
| ORM | SQLAlchemy | models/user.py |
| Async/Await | async functions | 全体 |
| JWT | create_access_token | core/security.py |
| Bcrypt | hash_password | core/security.py |

### ✅ ベストプラクティス

- 非同期処理（async/await）で I/O 効率化
- セキュリティ層の分離
- サービス層のビジネスロジック集中
- Pydantic による型安全性
- 環境変数による設定管理

---

**Status:** ✅ Phase 1-B 完了
**Next:** Phase 1-C (トークンリフレッシュ・セッション管理)
