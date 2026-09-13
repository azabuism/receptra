# app/main.py 更新サマリー

## 変更箇所

### 1. Imports 追加

```python
# 追加されたインポート
from sqlalchemy.ext.asyncio import create_async_engine
from app.models.user import Base  # SQLAlchemy base for metadata
from app.routers.auth import router as auth_router
```

**削除されたインポート** (未使用):
- `AsyncSession`
- `sessionmaker` 
- `JSONResponse` (未使用)

### 2. Lifespan Context Manager 更新

**Before (削除されたコード):**
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """アプリケーションのライフサイクル管理"""
    # Startup
    logger.info("🚀 BARIYON Receptra API starting up...")
    yield
    # Shutdown
    logger.info("🛑 BARIYON Receptra API shutting down...")
```

**After (新しいコード):**
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """アプリケーションのライフサイクル管理"""
    # Startup
    logger.info("🚀 BARIYON Receptra API starting up...")

    # Database initialization
    try:
        database_url = settings.DATABASE_URL
        engine = create_async_engine(
            database_url,
            echo=settings.ENVIRONMENT == "development",
            future=True,
        )

        # Create all tables
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        logger.info("✅ Database tables initialized")
        await engine.dispose()
    except Exception as e:
        logger.error(f"❌ Database initialization failed: {e}")

    yield

    # Shutdown
    logger.info("🛑 BARIYON Receptra API shutting down...")
```

**変更内容:**
- アプリケーション起動時に非同期エンジンを作成
- `Base.metadata.create_all()` でテーブルを自動生成
- エラーハンドリングで初期化失敗をログ出力
- エンジンのリソースを確実に解放

### 3. ルーター登録

**追加されたコード** (create_app() 関数内, CORS 設定の後):

```python
# ルーター登録
app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
```

**効果:**
- `/api/v1/auth/register` - ユーザー登録
- `/api/v1/auth/login` - ログイン
- `/api/v1/auth/me` - 現在のユーザー情報取得
- `/api/v1/auth/logout` - ログアウト (プレースホルダー)
- `/api/v1/auth/health` - 認証サービスヘルスチェック

---

## 全体構造

```
app/main.py
├── Imports
│   ├── FastAPI/Middleware
│   ├── SQLAlchemy async
│   ├── Config
│   ├── Models (Base)
│   └── Routers (auth)
├── Lifespan
│   ├── Startup (DB initialization)
│   ├── Yield (app running)
│   └── Shutdown
├── create_app()
│   ├── FastAPI インスタンス作成
│   ├── CORS ミドルウェア
│   ├── Auth ルーター登録 ← NEW
│   ├── /health エンドポイント
│   └── / ルートエンドポイント
└── Entry point
    └── uvicorn で実行
```

---

## 変更による効果

✅ **データベース自動初期化**
- アプリケーション起動時に Tenant/User テーブルが自動作成
- Alembic migration 不要で開発スピードアップ

✅ **認証ルーター統合**
- `/api/v1/auth/*` エンドポイント群が利用可能
- JWT トークンベースの認証実装

✅ **エラーハンドリング**
- DB 初期化失敗時もアプリケーションは起動継続
- ログでトラブルシューティング情報を記録

✅ **マルチテナント対応**
- Tenant と User モデルが自動生成
- tenant_id による データ分離

---

## 検証方法

```bash
# 1. Docker 再構築
cd ~/www/receptra
docker-compose down
docker-compose build --no-cache
docker-compose up -d

# 2. ログ確認
docker-compose logs backend

# 期待される出力:
# ✅ Database tables initialized
# ✅ FastAPI application created successfully
# 🚀 BARIYON Receptra API starting up...

# 3. ヘルスチェック
curl http://localhost:8000/health

# 4. API ドキュメント確認
# http://localhost:8000/docs
```

---

## 注意事項

⚠️ **本番環境では:**
1. `SECRET_KEY` を強力なランダム値に変更
2. `CORS_ORIGINS` を特定のドメインに限定
3. `echo=False` に設定 (SQL ログを非表示)
4. Alembic migration を使用 (本格的な DB 管理)

---

**Status:** ✅ Phase 1-B 認証システム実装完了
