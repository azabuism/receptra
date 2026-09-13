# スマホ忍者 — Twilio Voice API 統合 引き継ぎメモ

**プロジェクト:** BARIYON スマホ忍者 (Smartphone Ninja Delivery App)  
**目的:** LINE LIFF で利用者が忍者を呼び出し、通話で詳細確認（Uber ライドシェアモデル）  
**状態:** ほぼ完成、別チャットで開発中

---

## 🎯 Twilio Voice Integration — 技術設計

### 概要
- **使用電話番号:** +81 50 1724 3988（購入予定、共用予定）
- **バックエンド:** 同じ FastAPI サーバー（localhost:8080）
- **TwiML Application:** https://679e257598e5.ngrok.app/call/incoming

### アーキテクチャ

```
[利用者の LINE LIFF]
    ↓
[POST] タスク作成 → 忍者マッチング
    ↓
[Twilio 呼び出し] → FastAPI /call/incoming
    ↓
[判別ロジック]
├─ RECEPTRA フロー（レストラン予約）
└─ スマホ忍者フロー（タスク通話）
    ↓
[<Dial> で利用者と忍者を接続]
```

### 実装ステップ

#### 1. LINE LIFF 統合（既存実装）
```javascript
// 利用者がタスクをリクエスト
liff.sendMessages([{
  type: 'text',
  text: '配達を依頼しました'
}]);

// バックエンドに POST
POST /api/tasks/create
{
  userId: "user123",
  serviceType: "smafo-ninja",  // ← これで判別
  taskDetails: {...}
}
```

#### 2. FastAPI バックエンド（RECEPTRA と共用）
```python
# /call/incoming で判別
@app.post("/call/incoming")
def incoming_call(request: CallRequest):
    service_type = request.form.get("serviceType", "unknown")
    
    if service_type == "smafo-ninja":
        # スマホ忍者フロー
        return ninja_greeting_twiml()
    else:
        # RECEPTRA フロー
        return restaurant_greeting_twiml()
```

#### 3. Twilio TwiML フロー（スマホ忍者用）
```xml
<!-- 1. 挨拶 -->
<Say voice="woman" language="ja-JP">
  こんにちは。配達アシスタントです。タスク確認にご協力ください。
</Say>

<!-- 2. 忍者側の確認 -->
<Gather numDigits="1" action="/call/ninja_confirm">
  <Say>タスクの詳細を聞きます。1 を押してください。</Say>
</Gather>

<!-- 3. 利用者と忍者を接続 -->
<Dial>
  <!-- 忍者の電話番号 -->
  <Number>+81XXXXXXXXXX</Number>
</Dial>
```

---

## 📋 開発チェックリスト

- [ ] Twilio 電話番号購入完了（+81 50 1724 3988）
- [ ] JAPAN NATIONAL BUSINESS Regulatory Bundle 作成/割り当て
- [ ] FastAPI バックエンドに `/call/ninja_confirm` エンドポイント追加
- [ ] スマホ忍者用 TwiML フロー実装
- [ ] LINE LIFF 側で serviceType パラメータ送信
- [ ] 利用者と忍者の接続テスト
- [ ] エラーハンドリング（通話キャンセル、タイムアウト等）
- [ ] ログ記録と監視

---

## 🔧 Twilio 設定

### 現在の環境（RECEPTRA から継承）
- **Twilio Account SID:** ACe95d93e67c5a6449bf3ac320f94fdf72
- **Twilio Phone:** +81312345678（テスト用）
- **ngrok URL:** https://679e257598e5.ngrok.app
- **TwiML App:** RECEPTRA（既作成）

### スマホ忍者用に追加必要な設定
1. **新しい TwiML アプリケーション** (オプション)
   - RECEPTRA と共用するなら不要（/call/incoming で判別）
2. **Phone Number の割り当て**
   - +81 50 1724 3988 → TwiML App (RECEPTRA or 新規) にリンク
3. **Webhook URL**
   - Voice Request: https://679e257598e5.ngrok.app/call/incoming
   - (serviceType で自動判別)

---

## 📞 電話番号購入手順（Sep 11, 2026 進行中）

### ステップ
1. ✅ 電話番号選択: +81 50 1724 3988
2. ✅ エンドユーザー選択: Business
3. ⏳ **Regulatory Bundle 割り当て** ← 現在ここ
   - [Create a Regulatory Bundle](https://console.twilio.com/us/account/compliance) をクリック
   - JAPAN NATIONAL BUSINESS タイプを作成
   - 会社情報・代表者情報を入力
   - 承認待ち（1-2日）
   - Bundle ID をコピー → ダイアログに貼り付け
4. ⏳ 購入確定: Buy ボタン押下
5. ⏳ 電話番号をアクティベート
6. ⏳ TwiML App に割り当て

---

## 🚀 次のアクション（優先順位）

1. **RECEPTRA を完成させる** ← 現在のフォーカス
   - Twilio 電話番号購入
   - 実機テスト完了
   - レストラン IVR フロー 完全動作確認

2. **その後、スマホ忍者対応を実装**
   - FastAPI に `serviceType` 判別ロジック追加
   - スマホ忍者用エンドポイント実装
   - LINE LIFF 側で serviceType パラメータ送信
   - 統合テスト

---

**質問・サポートが必要な場合は、このメモを参照してください。** 📚
