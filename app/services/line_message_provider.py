"""
Phase N2: LINE Messaging API呼び出しのアダプター抽象化

app.services.outbound_call_provider と全く同じ設計思想:
- 実際にLINE APIへネットワークアクセスするのは LineMessageProvider のみ。
- LINE_CHANNEL_ACCESS_TOKEN が空文字列（未設定）の場合は自動的に
  FakeLineMessageProvider にフォールバックし、一切のネットワークアクセスを
  行わない（本フェーズのテスト・初回デプロイ時の安全動作）。
- 呼び出し元（webhook handler / worker / test-notification endpoint）は
  常に get_line_message_provider() 経由でのみプロバイダーを取得し、
  実装の詳細（httpxを使う、エンドポイントURL等）を意識しない。

このモジュール自体は「実際にLINEへ送るかどうか」を判断しない
（設定に応じてFake/Realのどちらを返すかだけを判断する）。「本当に送ってよいか」
というビジネス上の判断（重複防止・設定ON/OFF・連携状態）は呼び出し元
(app/services/line_notification_worker.py 等)の責務。
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import httpx

from app.config import get_settings

logger = logging.getLogger("receptra.line.provider")

LINE_API_BASE = "https://api.line.me"


@dataclass
class LinePushResult:
    success: bool
    error_category: Optional[str] = None


class LineMessageProviderBase(ABC):
    """LINE Messaging API呼び出しの共通インターフェース。"""

    name: str = "base"

    @abstractmethod
    async def push_message(self, line_user_id: str, text: str) -> LinePushResult:
        """指定のLINE userIdへpushメッセージを送る（課金対象）。"""
        raise NotImplementedError

    @abstractmethod
    async def reply_message(self, reply_token: str, text: str) -> LinePushResult:
        """replyTokenを使って返信する（課金対象外）。"""
        raise NotImplementedError

    @abstractmethod
    async def issue_link_token(self, line_user_id: str) -> Optional[str]:
        """指定userIdに対するlinkTokenを発行する（アカウント連携フロー用）。
        失敗時はNoneを返す（呼び出し元は例外を伝播させないこと）。"""
        raise NotImplementedError


class FakeLineMessageProvider(LineMessageProviderBase):
    """LINE_CHANNEL_ACCESS_TOKEN未設定時、および本フェーズの全自動テストで
    使用する。実際のLINEネットワークには一切接続しない。呼び出し内容を
    ログにのみ記録し、常に成功を返す（テストから呼び出し履歴を検証できるよう、
    テスト側でこのクラスのメソッドをmonkeypatchして記録することを想定）。
    """

    name = "fake"

    async def push_message(self, line_user_id: str, text: str) -> LinePushResult:
        logger.info("【模擬LINE送信】push_message text_len=%d", len(text))
        return LinePushResult(success=True)

    async def reply_message(self, reply_token: str, text: str) -> LinePushResult:
        logger.info("【模擬LINE送信】reply_message text_len=%d", len(text))
        return LinePushResult(success=True)

    async def issue_link_token(self, line_user_id: str) -> Optional[str]:
        return f"fake-link-token-{line_user_id[-8:]}"


class LineMessageProvider(LineMessageProviderBase):
    """実際にLINE Messaging APIへ接続する実装。
    LINE_CHANNEL_ACCESS_TOKEN が設定されている場合のみ get_line_message_provider()
    から返される。"""

    name = "line"

    def __init__(self, access_token: str):
        self._access_token = access_token

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    async def push_message(self, line_user_id: str, text: str) -> LinePushResult:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{LINE_API_BASE}/v2/bot/message/push",
                    headers=self._headers(),
                    json={"to": line_user_id, "messages": [{"type": "text", "text": text}]},
                )
            if resp.status_code == 200:
                return LinePushResult(success=True)
            logger.warning("LINE push_message failed status=%d", resp.status_code)
            return LinePushResult(success=False, error_category=f"http_{resp.status_code}")
        except Exception:
            logger.exception("LINE push_message で例外が発生しました")
            return LinePushResult(success=False, error_category="provider_exception")

    async def reply_message(self, reply_token: str, text: str) -> LinePushResult:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{LINE_API_BASE}/v2/bot/message/reply",
                    headers=self._headers(),
                    json={"replyToken": reply_token, "messages": [{"type": "text", "text": text}]},
                )
            if resp.status_code == 200:
                return LinePushResult(success=True)
            logger.warning("LINE reply_message failed status=%d", resp.status_code)
            return LinePushResult(success=False, error_category=f"http_{resp.status_code}")
        except Exception:
            logger.exception("LINE reply_message で例外が発生しました")
            return LinePushResult(success=False, error_category="provider_exception")

    async def issue_link_token(self, line_user_id: str) -> Optional[str]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{LINE_API_BASE}/v2/bot/user/{line_user_id}/linkToken",
                    headers=self._headers(),
                )
            if resp.status_code == 200:
                return resp.json().get("linkToken")
            logger.warning("LINE issue_link_token failed status=%d", resp.status_code)
            return None
        except Exception:
            logger.exception("LINE issue_link_token で例外が発生しました")
            return None


def get_line_message_provider() -> LineMessageProviderBase:
    """設定に基づいてプロバイダーを選択する。
    LINE_CHANNEL_ACCESS_TOKENが空文字列（未設定）の場合は、安全のため常に
    FakeLineMessageProviderにフォールバックする（実送信を誤って有効化しない）。
    """
    settings = get_settings()
    token = (settings.LINE_CHANNEL_ACCESS_TOKEN or "").strip()
    if not token:
        return FakeLineMessageProvider()
    return LineMessageProvider(token)
