# RECEPTRA クイックリファレンスカード

## ⚡ 30秒セットアップ

```bash
# 1. 仮想環境
python3 -m venv receptra_env && source receptra_env/bin/activate

# 2. インストール
pip install fastapi uvicorn sqlalchemy pydantic python-dotenv

# 3. バックエンド起動
python3 -m uvicorn receptra_backend_extended:app --reload

# 4. ブラウザを開く（別ターミナル）
python3 -m http.server 8080
# http://localhost:8080/receptra-homepage-with-subcategory-icons.html
```

---

## 📁 主なファイル

| ファイル | 用途 | サイズ |
|---------|------|--------|
| `receptra-homepage-with-subcategory-icons.html` | 顧客向けホームページ | 96 KB |
| `receptra-store-dashboard-extended.html` | 店舗管理ダッシュボード | 43 KB |
| `receptra-api-client.js` | API クライアント | 9.5 KB |
| `receptra_backend_extended.py` | FastAPI バックエンド | 40 KB |
| `README.md` | プロジェクト説明 | 15 KB |
| `RECEPTRA_SYSTEM_SPECIFICATION.md` | 技術仕様書 | 22 KB |
| `DEPLOYMENT_GUIDE.md` | デプロイ手順 | 7.2 KB |
| `LOCAL_TESTING_GUIDE.md` | テスト手順 | 11.3 KB |

---

## 🧪 テストコマンド

### ヘルスチェック
```bash
curl http://localhost:8000/api/health
```

### リマインダースケジュール
```bash
curl -X POST http://localhost:8000/api/reminders/schedule \
  -H "Content-Type: application/json" \
  -d '{"shop_id":"shop_001","reminder_type":"day_before","trigger_days_before":1,"enabled":true}'
```

### ビジネスコールをログ
```bash
curl -X POST http://localhost:8000/api/business-calls/log \
  -H "Content-Type: application/json" \
  -d '{"shop_id":"shop_001","caller_name":"テスト","caller_number":"09012345678","purpose":"営業","urgency_level":"medium"}'
```

### 緊急通知を作成
```bash
curl -X POST http://localhost:8000/api/notifications/urgent \
  -H "Content-Type: application/json" \
  -d '{"shop_id":"shop_001","notification_type":"cancellation","content":"キャンセル","urgency":"high"}'
```

### ビジネスコール一覧
```bash
curl http://localhost:8000/api/business-calls?shop_id=shop_001
```

---

## 🔗 重要なURL

これらはすべてのページのフッターに統合済み：

| URL | ページ |
|-----|--------|
| https://www.bariyon.com/privacy.html | プライバシーポリシー |
| https://www.bariyon.com/terms.html | 利用規約 |
| https://www.bariyon.com/contact.html | お問い合わせ |

確認: `grep -r "bariyon.com" *.html`

---

## 🌍 ブラウザでのテスト

### 1. ホームページ
```
file:///path/to/receptra-homepage-with-subcategory-icons.html
またはHTTPサーバー経由:
http://localhost:8080/receptra-homepage-with-subcategory-icons.html
```
**テスト内容:**
- ページが読み込まれること
- フッターのURLが表示されること
- "今すぐ予約" が機能すること

### 2. ダッシュボード
```
ログイン: test@store.com / TestPass123
（または実装内のデモアカウント参照）
```
**テスト内容:**
- 7つのメニューが表示されること
- 各機能が動作すること（リマインダー、コール、通知）
- localStorage にデータが保存されること

---

## 🚀 デプロイ

### Heroku
```bash
heroku create receptra-app
heroku config:set TWILIO_ACCOUNT_SID=xxx
heroku addons:create heroku-postgresql:hobby-dev
git push heroku main
```

### Railway
```bash
railway login
railway init
railway add postgresql
railway up
```

### Docker
```bash
docker build -t receptra:latest .
docker run -p 8000:8000 receptra:latest
```

---

## 🐛 よくあるエラー

| エラー | 解決方法 |
|-------|---------|
| `ModuleNotFoundError: fastapi` | `pip install fastapi uvicorn` |
| `Address already in use :8000` | `lsof -i :8000` で PID を確認し `kill -9 <PID>` |
| `database is locked` | `rm receptra.db` でリセット |
| CORS エラー | `.env` で `CORS_ORIGINS` を確認 |

---

## 📊 データ確認

### ブラウザの localStorage
```javascript
// DevTools コンソールで実行
JSON.parse(localStorage.getItem('receptra_reminders'))
JSON.parse(localStorage.getItem('receptra_business_calls'))
JSON.parse(localStorage.getItem('receptra_notifications'))
```

### SQLite データベース
```bash
sqlite3 receptra.db
sqlite> SELECT * FROM shops;
sqlite> SELECT * FROM reservations;
sqlite> .quit
```

---

## 📱 レスポンシブテスト

### DevTools でのテスト
1. F12 を開く
2. Ctrl+Shift+M (または ⌘+Shift+M on Mac)
3. デバイスを選択:
   - iPhone 12 (390×844px)
   - iPad (768×1024px)
   - Desktop (1440×900px)

---

## 🔍 システム検証

```bash
# 全チェックを実行
python3 verify_system.py

# 期待される結果
# ✓ 全ファイルが存在
# ✓ 全 URL が統合されている
# ✓ ドキュメントが完全
# ✓ バックエンドが実装されている
```

---

## 📞 サポート

### ドキュメント
1. **ローカルテスト**: [LOCAL_TESTING_GUIDE.md](LOCAL_TESTING_GUIDE.md)
2. **デプロイ**: [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)
3. **技術仕様**: [RECEPTRA_SYSTEM_SPECIFICATION.md](RECEPTRA_SYSTEM_SPECIFICATION.md)

### 公式リンク
- Bariyon Privacy: https://www.bariyon.com/privacy.html
- Bariyon Terms: https://www.bariyon.com/terms.html
- Bariyon Contact: https://www.bariyon.com/contact.html

---

## ✅ チェックリスト

- [ ] システム検証スクリプト実行: `python3 verify_system.py`
- [ ] ローカルで起動テスト
- [ ] ホームページ表示確認
- [ ] ログイン・ダッシュボード動作確認
- [ ] API テスト実行
- [ ] localStorage データ確認
- [ ] 本番デプロイ（Heroku/Railway/Docker）
- [ ] Twilio 設定（オプション）
- [ ] 本番 DB (PostgreSQL) に移行

---

**最終更新**: 2026-09-11  
**バージョン**: 3.0 (Phase 3 Complete) ✅

