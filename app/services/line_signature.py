"""
Phase N2: LINE Webhook署名検証

LINE公式ドキュメント (developers.line.biz/en/docs/messaging-api/verify-webhook-signature/)
に基づく実装:
- Channel SecretをHMACキーとして、Webhookのリクエストボディ(生バイト列)に対して
  HMAC-SHA256を計算し、Base64エンコードした値が `x-line-signature` ヘッダーの
  値と一致することを確認する。
- 一致しない場合、またはヘッダーが無い場合は、そのペイロードを一切信用・処理しない。
- 比較は必ずタイミング攻撃耐性のある hmac.compare_digest を使う。
"""

import base64
import hashlib
import hmac


def compute_line_signature(body: bytes, channel_secret: str) -> str:
    mac = hmac.new(channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(mac).decode("utf-8")


def verify_line_signature(body: bytes, signature_header: str, channel_secret: str) -> bool:
    """署名を検証する。channel_secretが空文字列の場合は常にFalseを返す
    （呼び出し元は「LINE連携が設定されていない」ケースとして扱い、
    ペイロードは一切処理せずWebhookへは200のみ返すこと）。"""
    if not channel_secret or not signature_header:
        return False
    expected = compute_line_signature(body, channel_secret)
    try:
        return hmac.compare_digest(expected, signature_header)
    except Exception:
        return False
