# RECEPTRA 電話機能 クイックスタート（30分版）

**⏱️ この手順は 30 分で完了します**

---

## 🎯 ゴール

5 分以内に Twilio 番号への着信テストに成功する

---

## ステップ 1: Twilio アカウント作成（10分）

```bash
1. https://www.twilio.com/try-twilio を開く

2. メールアドレス・パスワード登録

3. 電話番号確認（SMS）

4. 「Restaurant」選択

5. 日本の +81 番号をリクエスト

✅ 完了: Account SID, Auth Token を取得
```

**取得する情報:**
```
Account SID: ACxxxxxxxxx
Auth Token:  yyyyyyyyyyyy
Phone:       +81312345678
```

---

## ステップ 2: Python 環境セットアップ（10分）

```bash
# macOS/Linux

# 1. フォルダ作成
mkdir receptra-backend
cd receptra-backend

# 2. 仮想環境
python3 -m venv venv
source venv/bin/activate

# 3. 依存関係
pip install -r requirements.txt

# 4. .env ファイル作成
cat > .env << 'ENVFILE'
TWILIO_ACCOUNT_SID=ACxxxxxxxxx
TWILIO_AUTH_TOKEN=yyyyyyyyyyyy
TWILIO_PHONE_NUMBER=+81312345678
API_HOST=http://localhost:8000
DATABASE_PATH=receptra.db
ENVFILE

# 5. 起動
python receptra_backend.py

# ✅ 完了: http://localhost:8000/health で確認
```

---

## ステップ 3: ngrok トンネル開設（5分）

**新しいターミナルタブで:**

```bash
# 1. ngrok インストール（初回のみ）
brew install ngrok

# 2. トンネル開設
ngrok http 8000

# ✅ 完了: 下記のような URL が表示される
# Forwarding    https://xxxx-xxxx.ngrok.io → http://localhost:8000
```

**表示された URL をコピー:**
```
https://xxxx-xxxx.ngrok.io
```

---

## ステップ 4: Twilio Webhook 設定（5分）

```bash
1. Twilio Console: https://console.twilio.com

2. 左パネル → Phone Numbers

3. 購入した番号をクリック

4. Voice セクション → Incoming calls

5. Webhook URL 入力:
   https://xxxx-xxxx.ngrok.io/api/calls/incoming
   （ngrok の URL を貼り付け）

6. 保存

✅ 完了: Webhook 設定完了
```

---

## 🔔 テスト電話！

あなたの携帯電話から Twilio 番号に電話

📱 → 📞 Twilio番号

**聞こえるもの:**
```
「いらっしゃいませ。お電話ありがとうございます。」
「ご予約はメニューの1を...」
```

**キー入力テスト:**
```
1 を押す → 新規予約フロー
2 を押す → 予約確認フロー
0 を押す → 終了
```

✅ **成功！**

---

## 📊 ログで確認

バックエンド起動ターミナルで以下が表示されるはず：

```bash
2025-01-21 10:30:00 INFO: Incoming call from +81-90-xxxx-xxxx
2025-01-21 10:30:05 INFO: User pressed digit: 1
2025-01-21 10:30:10 INFO: Created reservation: res_abc12345
```

---

## 🚀 次のステップ

```bash
✅ 完了: 基本的な電話対応が動作

📋 次: フロントエンド統合
   - receptra.html に電話番号フィールド追加
   - submitReservation() 関数修正

📋 その次: オーナー設定パネル
   - リマインド設定画面実装

📋 最後: 本番デプロイ
   - Heroku / AWS にデプロイ
```

---

## 🆘 トラブル

### 「着信がない」

```
□ ngrok は起動中？
  → 別ターミナルで確認

□ Webhook URL は正しい？
  → https://xxxx.ngrok.io/api/calls/incoming

□ バックエンド起動？
  → python receptra_backend.py

→ Twilio Console → Monitor → Log で詳細確認
```

### 「音声が聞こえない」

```
□ Twilio 残高はある？
   → Account → Billing で確認

□ 別の番号から試す？
   → 友人・家族の電話で試す

□ API テスト？
   → curl http://localhost:8000/health
```

---

**🎉 おめでとうございます！**

これで電話対応の基本が動作します。
次は IMPLEMENTATION_SUMMARY.md を読んで全体計画を確認しましょう。

