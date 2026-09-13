# RECEPTRA Restaurant Reservation System
## Twilio Phone Support & Reminder Calls

### クイックスタートガイド

このドキュメントでは、Twilioを使用したレストラン予約システムのセットアップと実行方法について説明します。

---

## プロジェクト構成

```
receptra/
├── venv/                          # Python仮想環境
├── .env                           # Twilio認証情報（機密）
├── requirements.txt               # Python依存パッケージ
├── receptra_backend.py            # FastAPI バックエンド
├── QUICK_START.md                 # このファイル
└── receptra.db                    # SQLiteデータベース（自動生成）
```

---

## ステップ 1: 環境確認

### インストール済み要件
- ✓ Python 3.11.15
- ✓ 仮想環境 (venv) - 作成済み
- ✓ 依存パッケージ - インストール済み

### インストール済みパッケージ確認

```bash
cd ~/receptra
source venv/bin/activate
pip list | grep -E "fastapi|twilio|apscheduler"
```

期待される出力：
```
apscheduler           3.10.4
fastapi              0.104.1
twilio               8.10.0
```

---

## ステップ 2: 環境変数確認

`.env` ファイルに以下の値が設定されていることを確認してください：

```bash
cat .env
```

期待される出力：
```
TWILIO_ACCOUNT_SID=ACe95d93e67c5a6449bf3ac320f94fdf72
TWILIO_AUTH_TOKEN=d0c1fd6f867d20e52f52f5b9
TWILIO_PHONE_NUMBER=+81312345678
API_HOST=http://localhost:8000
DATABASE_PATH=receptra.db
```

**注意：** テスト用の仮電話番号 (+81312345678) を使用します。

---

## ステップ 3: バックエンド サーバー起動

### サーバーを起動

```bash
cd ~/receptra
source venv/bin/activate
uvicorn receptra_backend:app --reload --host 0.0.0.0 --port 8000
```

成功時の出力：
```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Application startup complete
```

### ブラウザで動作確認

[http://localhost:8000/health](http://localhost:8000/health) にアクセス

期待される応答：
```json
{
  "status": "healthy",
  "timestamp": "2026-09-10T...",
  "twilio_configured": true
}
```

---

## ステップ 4: API ドキュメント

### Swagger UI（インタラクティブドキュメント）

```
http://localhost:8000/docs
```

全エンドポイント：
- `GET /health` - ヘルスチェック
- `POST /call/incoming` - 着信処理（IVR開始）
- `POST /call/process_name` - 顧客名入力処理
- `POST /call/process_date` - 予約日入力処理
- `POST /call/process_time` - 予約時間入力処理
- `POST /call/process_party_size` - 人数入力処理
- `POST /call/process_confirmation` - 確認処理
- `POST /call/reminder/{reservation_id}` - リマインダー通話
- `POST /call/reminder_response/{reservation_id}` - リマインダー応答処理
- `GET /reservations` - 全予約取得
- `GET /reservations/{id}` - 予約詳細取得
- `POST /reservations` - 予約新規作成
- `DELETE /reservations/{id}` - 予約削除

---

## ステップ 5: テスト用の予約を手動作成

### cURL でテスト予約を作成

```bash
curl -X POST http://localhost:8000/reservations \
  -H "Content-Type: application/json" \
  -d '{
    "phone_number": "+819012345678",
    "customer_name": "山田太郎",
    "reservation_date": "2026-09-20T19:00:00",
    "party_size": 4,
    "special_requests": "アレルギー対応不要"
  }'
```

成功時の応答：
```json
{
  "id": 1,
  "phone_number": "+819012345678",
  "customer_name": "山田太郎",
  "reservation_date": "2026-09-20T19:00:00",
  "party_size": 4,
  "special_requests": "アレルギー対応不要",
  "created_at": "2026-09-10T...",
  "reminder_sent": 0
}
```

---

## ステップ 6: 予約一覧の確認

### 全予約を取得

```bash
curl http://localhost:8000/reservations
```

### 特定の予約を取得

```bash
curl http://localhost:8000/reservations/1
```

---

## ステップ 7: ngrok で外部アクセス可能にする（本番準備）

Twilioから自分のサーバーにコールバックを送信するために、ngrokトンネルが必要です。

### ngrokのインストール（初回のみ）

```bash
# macOS
brew install ngrok

# Linux (Ubuntu/Debian)
sudo apt-get install ngrok
```

### ngrokトンネル起動

```bash
# 別のターミナルウィンドウで実行
ngrok http 8000
```

出力例：
```
ngrok (Ctrl+C to quit)

Session Status                online
Account                       ...
Version                       3.0.0
Region                        us
Forwarding                    https://1a2b3c4d5e6f.ngrok.io -> http://localhost:8000
Connections                   ...
```

### Twilio ウェブフック設定

1. [Twilio Console](https://console.twilio.com) にログイン
2. **Phone Numbers** → **Manage Numbers** を選択
3. テスト電話番号をクリック
4. **Voice Configuration** セクションで以下を設定：
   - **A Call Comes In:** `https://1a2b3c4d5e6f.ngrok.io/call/incoming` (ngrokのURLに置き換え)
   - **Primary Handler:** Post-Request

5. **Save** をクリック

---

## ステップ 8: IVR フロー説明

### 顧客が電話をかけた時の流れ

1. **グリーティング**: "いらっしゃいませ。レストラン予約システムへようこそ"
2. **お名前入力**: "お名前をお聞きします"
3. **予約日入力**: "ご予約希望日を教えてください（例：915で9月15日）"
4. **予約時間入力**: "ご予約希望時間を教えてください（例：1900で19時）"
5. **来店人数入力**: "ご来店人数を教えてください"
6. **内容確認**: 入力内容を読み上げ確認
7. **確定**: "1"を入力で予約確定、"2"で修正
8. **完了**: 予約確定メッセージ＆予約IDを返却

### リマインダー通話（予約前日14時）

1. 顧客に自動で電話をかける
2. "予約確認のお電話です"と案内
3. "予約は本日のままで変わりないか"確認
   - "1"入力で確定
   - "2"入力で修正案内（直接店舗に電話するよう指示）

---

## トラブルシューティング

### Twilio認証エラー

```
Error: Invalid Twilio Credentials
```

**解決法：**
- `.env` ファイルの `TWILIO_ACCOUNT_SID` と `TWILIO_AUTH_TOKEN` を確認
- [Twilio Console](https://console.twilio.com/account/keys-credentials/api-keys-and-tokens) で認証情報を取得

### データベースエラー

```
Error: database is locked
```

**解決法：**
```bash
# 既存のDB削除（開発環境のみ）
rm ~/receptra/receptra.db

# サーバーを再起動
```

### ポート8000が使用中

```
Address already in use
```

**解決法：**
```bash
# ポート使用プロセスを確認
lsof -i :8000

# プロセスを停止
kill -9 <PID>
```

### ngrokコネクション失敗

Twilioから `https://xxxx.ngrok.io/call/incoming` にアクセスできない場合：

1. ngrokが実行中か確認
2. Twilioウェブフック設定が正しいか確認
3. ファイアウォール設定を確認

---

## 開発リソース

### ログの確認

```bash
# リアルタイムログ確認
tail -f /tmp/receptra.log
```

### データベース確認（SQLite）

```bash
# SQLite CLIで確認
sqlite3 ~/receptra/receptra.db

# テーブル一覧
sqlite> .tables

# 予約データ確認
sqlite> SELECT * FROM reservations;

# 終了
sqlite> .exit
```

### Python REPL でテスト

```bash
cd ~/receptra
source venv/bin/activate
python

# 以下をPythonで実行
from receptra_backend import SessionLocal, Reservation
db = SessionLocal()
reservations = db.query(Reservation).all()
for r in reservations:
    print(f"{r.id}: {r.customer_name} - {r.reservation_date}")
db.close()
```

---

## 本番環境チェックリスト

- [ ] Twilio本番アカウント利用（トライアルから切り替え）
- [ ] 実際の日本電話番号を取得
- [ ] セキュアな環境変数管理（`.env`をgitignoreに）
- [ ] HTTPSの有効化（ngrok → Let's Encryptなど）
- [ ] データベースをPostgreSQLに移行
- [ ] エラーロギングの確認
- [ ] レート制限の実装
- [ ] 認証・認可の追加
- [ ] 使用条件・プライバシーポリシーの確認

---

## 次のステップ

1. **フロントエンド連携**: HTML/JavaScriptで予約確認ページを構築
2. **リマインダー拡張**: SMS/メール通知の追加
3. **決済連携**: 仮払い・キャンセル料処理
4. **分析機能**: 予約状況、キャンセル率の集計
5. **レストラン管理画面**: 営業日管理、収容人数変更など

---

## サポート

問題が発生した場合：

1. ログを確認する (`uvicorn` の出力)
2. [Twilio Documentation](https://www.twilio.com/docs) を確認
3. [FastAPI Documentation](https://fastapi.tiangolo.com) を確認

---

**作成日:** 2026-09-10  
**最終更新:** 2026-09-10  
**バージョン:** 1.0.0
