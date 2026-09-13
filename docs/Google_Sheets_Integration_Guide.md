# Google Sheets連携 完全ガイド

RECEPTRA から Google スプレッドシートへの自動データ連携を設定する方法です。

## 📊 何ができるのか

- ✅ 予約データが自動的にスプレッドシートに記録される
- ✅ 日付、時間、人数、顧客情報、特別リクエストすべてが記録される
- ✅ リアルタイム分析やレポート作成が可能
- ✅ ビジネス上の意思決定データとして活用可能

---

## 🚀 セットアップ手順（3ステップ）

### ステップ1️⃣ Google スプレッドシートを作成

1. [Google スプレッドシート](https://sheets.google.com)を開く
2. **「+」** をクリックして新規スプレッドシートを作成
3. スプレッドシート名を `RECEPTRA 予約管理` に変更
4. 1行目に以下のヘッダーを入力：

| A | B | C | D | E | F | G | H | I |
|---|---|---|---|---|---|---|---|---|
| 予約ID | 顧客名 | メール | 来店日 | 時間 | 人数 | 特別リクエスト | ステータス | 予約日時 |

---

### ステップ2️⃣ Google Apps Script を設定

1. スプレッドシート画面の上部にある **「拡張機能」** をクリック
2. **「Apps Script」** をクリック（新しいタブで開きます）
3. 編集画面の既存コードをすべて削除
4. **以下のコードを全部貼り付け**：

```javascript
// Google Apps Script - RECEPTRA 予約データ受信エンドポイント
function doPost(e) {
  try {
    const data = JSON.parse(e.postData.contents);
    const sheet = SpreadsheetApp.getActiveSheet();
    
    // テストモードの場合はスキップ
    if (data.testMode) {
      return ContentService.createTextOutput(JSON.stringify({ success: true, message: 'test' }))
        .setMimeType(ContentService.MimeType.JSON);
    }
    
    // 予約データを追加
    sheet.appendRow([
      data.id,
      data.username,
      data.userEmail,
      data.date,
      data.time,
      data.partySize,
      data.specialRequest || '',
      data.status || 'pending',
      new Date().toLocaleString('ja-JP')
    ]);

    return ContentService.createTextOutput(JSON.stringify({ success: true }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (error) {
    Logger.log('エラー: ' + error);
    return ContentService.createTextOutput(JSON.stringify({ success: false, error: error.toString() }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}
```

5. 画面上部の「💾 保存」をクリック
6. プロジェクト名を入力（例：`RECEPTRA GAS`）して保存

---

### ステップ3️⃣ ウェブアプリとしてデプロイ

1. Apps Script エディタの左側メニューから **「デプロイ」** をクリック
2. **「新しいデプロイ」** をクリック
3. 右上の歯車アイコン `⚙️` をクリック
4. **デプロイタイプ** から **「ウェブアプリ」** を選択
5. 設定値：
   - **実行者**：「自分」を選択
   - **アクセス権限**：「全員」を選択
6. **「デプロイ」** をクリック
7. 「このアプリケーションは Google で確認されていません」と出たら、「詳細」をクリック
8. ページ最下部の「**RECEPTRA（セキュリティなし）に移動**」をクリック
9. 許可を求められたら「許可」をクリック
10. デプロイが完了し、URLが表示されます
    - **例**: `https://script.google.com/macros/d/1234567890abcdefg/usercopy`
11. このURLを **コピー** してください

---

## 🔗 RECEPTRA アプリで URL を設定

1. RECEPTRA の**オーナー管理ページ**にログイン
2. 左側で設定したい店舗を選択
3. 下にスクロールして **「📊 Google Sheets連携」** セクションを見つけ、Google Apps Script のデプロイURLを貼り付け
4. **「保存」** ボタンをクリック
5. **「接続テスト」** ボタンをクリックして、スプレッドシートに接続できるか確認

✅ テスト完了メッセージが表示されれば成功です！

---

## 📝 使用方法

### オーナー側
1. RECEPTRA アプリで予約を確認
2. **「確定」** または **「キャンセル」** ボタンをクリック
3. 自動的にスプレッドシートに記録される

### ユーザー側
1. アプリで予約を入力
2. **「予約リクエストを送信」** をクリック
3. 設定済みの場合、自動的にスプレッドシートに記録される

---

## ✨ 活用例

### 📊 営業データの分析
- 日別予約数の推移
- ピーク時間帯の分析
- 顧客層の把握

### 📈 Google Sheets での分析
```
=COUNTIF(H:H, "confirmed")  // 確定済み予約の件数
=COUNTIF(I:I, ">=2024-01-01")  // 今月の予約
=AVERAGE(F:F)  // 平均人数
```

### 📧 Google Form との連携
- 自動応答メール設定
- フォローアップ調査
- 顧客満足度調査

---

## 🔧 トラブルシューティング

### Q: テスト送信後、スプレッドシートに何も表示されない

**A:** 以下を確認してください：
1. Apps Script のコード内に `testMode` チェックがあるか確認
2. Google Sheets のシート名が正しいか確認
3. ブラウザコンソール（F12）でエラーを確認

### Q: 「接続テスト」でエラーが出る

**A:** 
- URLが正しくコピーされているか確認
- Google Apps Script のデプロイURLで、`usercopy` で終わっているか確認
- スプレッドシートが削除されていないか確認

### Q: 実際の予約がスプレッドシートに送信されない

**A:**
- オーナー管理ページで URL が保存されているか確認
- 保存後、ユーザーが**新しく予約**をしたか確認（保存前の予約は送信されません）
- ブラウザコンソール（F12）でネットワークエラーがないか確認

### Q: 同じ予約が重複して記録される

**A:** 
- Apps Script を変更したが デプロイを新規作成した可能性があります
- 古い URL を削除して、新しい URL を設定し直してください

---

## 🔐 セキュリティに関する注意

⚠️ **現在の設定について**
- Google Apps Script のデプロイは「全員」のアクセスを許可しています
- テスト環境では問題ありませんが、**本番運用時は以下を検討してください**：

### 推奨される改善案

1. **デプロイ時のアクセス権限を制限**
   - 「自分のみ」に変更
   - スプレッドシートへのアクセスを明確に

2. **認証トークンの追加**（オプション）
   ```javascript
   const EXPECTED_TOKEN = 'your_secret_token_here';
   if (data.token !== EXPECTED_TOKEN) {
       return ContentService.createTextOutput('Unauthorized')
           .setHttpCode(401);
   }
   ```

3. **IP制限**（Advanced Sheets API を使用する場合）

---

## 📞 さらに詳しく知りたい場合

- [Google Apps Script 公式ドキュメント](https://developers.google.com/apps-script)
- [Sheets API リファレンス](https://developers.google.com/sheets/api)
- BARIYON サポート：https://www.bariyon.com/contact.html

---

**質問や問題があれば、お気軽にお問い合わせください！** 🎉
