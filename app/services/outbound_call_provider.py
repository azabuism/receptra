"""
Outbound AI Phase 1: 架電プロバイダーのアダプター抽象化

目的: 将来Vonageの審査が完了し実際の発信が可能になった時点で、ワーカー側
（app.services.outbound_call_worker）やジョブ・ログのデータモデルには一切
手を入れず、ここのプロバイダー実装を差し替えるだけで済むようにする。

本フェーズ（Phase 1）では FakeOutboundCallProvider のみを使用する。実際の
電話網には一切接続せず、ネットワークアクセスも行わない。これは以下の理由による:
- Vonageアカウントが審査中で、実際の日本番号がまだ取得できていない
- Production環境で仮のCaller-ID番号を使って実発信することは明示的に禁止されている
  （RECEPTRA Outbound AI Phase 基盤設計書 参照）
- 「実際に電話をかけずに、AIの発話内容・ジョブキュー・リトライ・ログの一連の
  配管が正しく動くこと」を検証できることが、Vonage審査完了後の実機テストの
  前提条件になる

VonageOutboundCallProvider は、将来の実装先を明示するためのプレースホルダー
としてのみ存在する。呼び出されると明示的にNotImplementedErrorを送出し、
誤って選択された場合に実発信が起きないことを保証する（config.py の
OUTBOUND_CALL_PROVIDER のデフォルトは "fake" のみで、"vonage" を選択しても
ここで安全に停止する）。
"""

import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone

from app.config import get_settings

logger = logging.getLogger("receptra.outbound.provider")


@dataclass
class OutboundCallResult:
    """プロバイダーによる発信結果（成功・失敗どちらも表現できる）。"""
    success: bool
    provider_call_id: str | None = None
    # 失敗時のみ設定。個人情報や生の例外メッセージを含めないこと
    # （app.services.outbound_call_worker が OutboundCallJob.last_error_category
    # にそのまま保存するため、機微な情報を含まない分類名にとどめる）。
    error_category: str | None = None


class OutboundCallProvider(ABC):
    """架電プロバイダーの共通インターフェース。"""

    name: str = "base"

    @abstractmethod
    async def place_call(self, to_phone: str, message_text: str) -> OutboundCallResult:
        """指定の電話番号へ発信し、message_text の内容をAIが伝える。

        to_phone: 数字のみに正規化済みの電話番号（生のPII。ここでのみ使用し、
                  呼び出し元でログに残す際は必ずマスクすること）。
        message_text: app.services.outbound_voice_ai で組み立てた発話内容。
        """
        raise NotImplementedError


class FakeOutboundCallProvider(OutboundCallProvider):
    """Phase 1で唯一使用するプロバイダー。実際の電話網には一切接続しない。

    呼ばれるたびに「発信したつもり」で即座に成功を返す（模擬架電）。
    電話番号はログに一切出力しない（マスクすらせず、そもそも出力しない）。
    これにより、Vonage審査完了前でも、予約確定→ジョブ作成→ワーカーが拾う→
    プロバイダー呼び出し→ログ記録、という一連の配管を安全に検証できる。
    """

    name = "fake"

    async def place_call(self, to_phone: str, message_text: str) -> OutboundCallResult:
        fake_call_id = f"fake-{uuid.uuid4().hex[:12]}"
        logger.info(
            "【模擬架電】Fake provider が発信をシミュレートしました call_id=%s message_len=%d",
            fake_call_id, len(message_text),
        )
        return OutboundCallResult(success=True, provider_call_id=fake_call_id)


class VonageOutboundCallProvider(OutboundCallProvider):
    """将来の実装先プレースホルダー。Vonageのアカウント審査完了・実際の日本番号の
    取得・Production向けCaller-IDの確定を経るまでは、絶対に呼び出してはならない。

    実装時に必要になる想定の要素（本フェーズでは何も実装しない）:
    - app.config.Settings の VONAGE_API_KEY / VONAGE_API_SECRET /
      VONAGE_APPLICATION_ID / VONAGE_PRIVATE_KEY_PATH（既存のInbound IVR
      app.routers.vonage_voice と同じ認証情報を再利用できる見込み）
    - Vonage Voice APIでのオリジネート発信（NCCOによるTTS/Streaming）
    - WebSocketブリッジ経由でOpenAI Realtime APIへ接続する場合の技術検証
      （RECEPTRA Outbound AI Phase 基盤設計書「11. Vonage↔OpenAI Realtime
      ブリッジの技術的不明点」参照）
    """

    name = "vonage"

    async def place_call(self, to_phone: str, message_text: str) -> OutboundCallResult:
        raise NotImplementedError(
            "VonageOutboundCallProviderは未実装です。Vonageアカウントの審査完了・"
            "実際の日本番号の取得後に、あらためて実装してください。"
            "現時点でこのプロバイダーが呼び出されることは想定外の設定ミスです。"
        )


def get_outbound_call_provider() -> OutboundCallProvider:
    """設定（OUTBOUND_CALL_PROVIDER）に基づいてプロバイダーを選択する。

    未知の値が設定された場合も、安全側としてFakeOutboundCallProviderに
    フォールバックする（実発信を誤って有効化してしまうことを防ぐため。
    "vonage"が明示的に指定された場合のみVonageOutboundCallProviderを返すが、
    そちらは呼び出し時に必ずNotImplementedErrorになる）。
    """
    settings = get_settings()
    provider_name = (settings.OUTBOUND_CALL_PROVIDER or "fake").strip().lower()
    if provider_name == "vonage":
        return VonageOutboundCallProvider()
    if provider_name != "fake":
        logger.warning(
            "未知のOUTBOUND_CALL_PROVIDER=%sが指定されたため、安全のためFakeOutboundCallProviderにフォールバックします",
            provider_name,
        )
    return FakeOutboundCallProvider()
