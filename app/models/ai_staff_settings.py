"""
AIスタッフ設定（Realtime Voice AI Phase2）

店舗オーナーが、自店のRealtime Voice AI受付の声・名前・性格・話し方を
カスタマイズするための設定。shop_id と 1:1 の関係を持つ。

設計方針（重要・必ず守ること）:
- ここに保存する値は「OpenAI Realtime APIへそのまま渡すパラメータ」ではない。
  Realtime APIが実際にAPIパラメータとして受け付けるのは voice と
  turn_detection・reasoning.effort 等のごく一部のみで、「明るさ」
  「敬語レベル」「テンション」のようなAPI項目は存在しない。
  これらは app.services.realtime_voice_ai 側で instructions（自然文の
  プロンプト）に変換してから送信する。このモデルはあくまで店舗オーナーが
  選んだ「設定値」を保存するだけで、Realtime APIの仕様そのものではない。
- レコードが存在しない店舗（shop_id に対応する行が無い）は、Phase1相当の
  デフォルト挙動にフォールバックする（app.services.realtime_voice_ai 側で
  None を安全に扱う）。既存店舗に対して強制的にレコードを作成する必要は
  ない（lazy creation: オーナーが初めて設定画面を開いて保存した時点で
  作成される）。
- 日本語固定・電話番号や日付の日本語読みなどのシステムレベルの安全ルールは
  ここでは一切上書きできない。custom_instructions は
  app.services.realtime_voice_ai の instructions 組み立て処理の中で、
  常にシステムルール・言語ルールより下位（補助的な指示）として扱われる。
"""

import uuid
from datetime import datetime

from sqlalchemy import Column, String, Text, DateTime, ForeignKey, LargeBinary, Boolean
from sqlalchemy.orm import relationship, deferred

from app.database import Base


class AIStaffSettings(Base):
    """店舗ごとのAIスタッフ設定（Realtime Voice AI Phase2）"""

    __tablename__ = "ai_staff_settings"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # shop_id との1:1関係。unique=True により1店舗につき1レコードのみ許可。
    shop_id = Column(
        String(36),
        ForeignKey("shops.id"),
        nullable=False,
        unique=True,
        index=True,
    )

    # AIスタッフの名前（例:「さくら」）。未設定の場合、名乗らない汎用の
    # 挨拶にフォールバックする（realtime_voice_ai 側で処理）。
    staff_name = Column(String(100), nullable=True)

    # Realtime API用のvoice（例: "marin"）。未設定の場合は
    # app.config.Settings.OPENAI_REALTIME_VOICE のデフォルト値を使用する。
    #
    # 注意: ここに保存される値はRealtime API用のvoice一覧
    # （app.services.realtime_voice_ai.REALTIME_VOICES）の中から選ばれた
    # ものであることを想定している。テキスト読み上げ(/audio/speech)用の
    # voice一覧とは別物であり、混同しないこと。
    voice = Column(String(30), nullable=True)

    # 接客スタイルのプリセット名（例: "polite" "friendly" "luxury"
    # "energetic" "calm"）。UI側の「かんたん設定」用のラベルであり、
    # 実際の挙動は politeness_level / brightness / energy_level の値に
    # 変換されて保存される想定（プリセット選択時にUIがこれらへ反映する）。
    personality_preset = Column(String(50), nullable=True)

    # 敬語レベル（例: "formal"=丁寧 / "standard"=標準 / "casual"=くだけた）
    politeness_level = Column(String(20), nullable=True)

    # 明るさ（例: "bright"=明るい / "neutral"=普通 / "calm"=落ち着いた）
    brightness = Column(String(20), nullable=True)

    # テンション・元気さ（例: "high" / "medium" / "low"）
    energy_level = Column(String(20), nullable=True)

    # 電話に出たときの第一声（自由入力）。未設定の場合は店舗名を使った
    # 汎用の挨拶にフォールバックする。
    greeting = Column(Text, nullable=True)

    # 店舗独自の補助的な接客指示（自由入力）。
    # 重要: システムルール・言語ルールより下位に位置づけられ、矛盾する
    # 場合は常にシステムルール・言語ルールが優先される
    # （app.services.realtime_voice_ai の instructions 組み立て処理を参照）。
    custom_instructions = Column(Text, nullable=True)

    # ===== Fast Reservation Flow: 来店理由の確認設定 =====
    #
    # 予約受付時に、お客様へ来店・来院の目的を確認するかどうかのYES/NO設定。
    # 病院・美容室・整体等では有用な場合が多いが、飲食店等では不要な場合が
    # 多いため、店舗オーナーが任意でON/OFFできるようにする（デフォルトOFF＝
    # 既存店舗の挙動を変更しない）。
    # 重要: この設定は「詳細なメニュー・施術・診療内容の選択」を求めるものでは
    # ない。あくまで一言程度の来店目的を尋ねるかどうかだけを制御する
    # （app.services.realtime_voice_ai._VISIT_REASON_TEMPLATE参照）。
    ask_visit_reason_enabled = Column(Boolean, nullable=False, default=False)

    # ===== Phase3E-3: Zero-Wait Greeting音声のDBキャッシュ =====
    #
    # Phase3C.1のPoCでは、事前生成した第一声音声(mp3)を開発者が手動で
    # frontend/public/greeting_audio/{shop_id}.mp3 として配置・コミットする
    # 運用だった。この方式には、staff_name・voice・greeting（このモデルの値）を
    # 変更しても音声だけが古いまま残り続けるという既知の問題があった
    # （app.services.realtime_voice_ai.generate_greeting_tts_audio()の
    # docstring参照）。
    #
    # 対応方針（最小限。過剰なインフラを避ける）: 生成した音声バイト列そのものを
    # このテーブルの行にキャッシュとして保持する（Railwayのコンテナ
    # ローカルファイルシステムはデプロイ毎に消えるため不可。S3等の新規インフラは
    # 導入しない。Shop.logo_data等、既存のLargeBinaryカラムと同じ考え方）。
    # greeting_audio_fingerprintに、生成時点の(voice, 実際に話す第一声文言)から
    # 計算したハッシュ値を保存しておき、読み出し時に現在の設定から計算した
    # フィンガープリントと比較する。一致すればキャッシュを再利用し、
    # 不一致（＝staff_name/voice/greetingのいずれかが変更された）なら
    # その場で再生成してキャッシュを更新する。明示的な「変更を検知して
    # invalidateする」処理を各設定更新箇所に追加する必要がなく、読み出し時の
    # 比較だけで自己修復的にキャッシュが最新化される設計。
    greeting_audio_data = deferred(Column(LargeBinary, nullable=True))
    greeting_audio_content_type = Column(String(50), nullable=True)
    greeting_audio_fingerprint = Column(String(64), nullable=True)
    greeting_audio_generated_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    shop = relationship("Shop", foreign_keys=[shop_id], back_populates="ai_staff_settings")

    def __repr__(self):
        return f"<AIStaffSettings(shop_id={self.shop_id}, staff_name={self.staff_name}, voice={self.voice})>"
