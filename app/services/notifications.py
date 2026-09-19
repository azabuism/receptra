"""
ゲストへの自動通知（キャンセル連絡など）

現時点では Vonage の日本の電話番号がまだ承認待ちのため、実際の自動音声架電・SMS送信は
行わず、ログに記録するのみとしている。電話番号が使えるようになり次第、この関数の中身を
Vonage 経由の自動音声架電（および必要ならSMS）に差し替える。
"""

import logging

logger = logging.getLogger("receptra.notifications")


async def notify_guest_of_cancellation(reservation, message: str) -> None:
    """
    臨時休業などによる自動キャンセルをゲストへ知らせる。

    TODO: Vonage の日本の電話番号が承認・購入され次第、ここで自動音声架電
    （必要なら SMS も）を実際に発信する処理に接続する。
    """
    logger.info(
        "[通知予定・電話番号承認待ちのため未送信] 予約 %s（%s / %s）へ: %s",
        getattr(reservation, "id", "?"),
        getattr(reservation, "guest_name", "?"),
        getattr(reservation, "guest_phone", "?"),
        message,
    )
