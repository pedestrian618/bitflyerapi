# -*- coding: utf-8 -*-
"""環境変数ベースの設定。

必須:
    ANTHROPIC_API_KEY      Claude APIキー(未設定でも `ant auth login` プロファイルがあれば可)

実売買する場合のみ必須:
    BITFLYER_API_KEY       bitFlyer APIキー
    BITFLYER_API_SECRET    bitFlyer APIシークレット
    AITRADER_DRY_RUN=0     ドライラン解除(デフォルトは 1 = 実注文なし)
"""

import os
from dataclasses import dataclass, field


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "")


@dataclass
class Config:
    # bitFlyer
    bitflyer_key: str = field(default_factory=lambda: os.environ.get("BITFLYER_API_KEY", ""))
    bitflyer_secret: str = field(default_factory=lambda: os.environ.get("BITFLYER_API_SECRET", ""))
    product_code: str = field(default_factory=lambda: os.environ.get("AITRADER_PRODUCT_CODE", "BTC_JPY"))

    # LLM(プロバイダ × 軽量/重量ティア)
    # ペルソナごとに provider/tier が割り当てられ、障害時は他プロバイダの
    # 同ティアモデルへ自動フェイルオーバーする。
    claude_model_heavy: str = field(default_factory=lambda: os.environ.get(
        "AITRADER_CLAUDE_MODEL_HEAVY", os.environ.get("AITRADER_MODEL", "claude-opus-4-8")))
    claude_model_light: str = field(default_factory=lambda: os.environ.get(
        "AITRADER_CLAUDE_MODEL_LIGHT", "claude-haiku-4-5"))
    openai_model_heavy: str = field(default_factory=lambda: os.environ.get(
        "AITRADER_OPENAI_MODEL_HEAVY", "gpt-5.1"))
    openai_model_light: str = field(default_factory=lambda: os.environ.get(
        "AITRADER_OPENAI_MODEL_LIGHT", "gpt-5-mini"))
    gemini_model_heavy: str = field(default_factory=lambda: os.environ.get(
        "AITRADER_GEMINI_MODEL_HEAVY", "gemini-2.5-pro"))
    gemini_model_light: str = field(default_factory=lambda: os.environ.get(
        "AITRADER_GEMINI_MODEL_LIGHT", "gemini-2.5-flash"))
    llm_cooldown_sec: int = field(default_factory=lambda: int(os.environ.get(
        "AITRADER_LLM_COOLDOWN_SEC", "600")))

    def llm_models(self) -> dict:
        return {
            "claude": {"heavy": self.claude_model_heavy, "light": self.claude_model_light},
            "openai": {"heavy": self.openai_model_heavy, "light": self.openai_model_light},
            "gemini": {"heavy": self.gemini_model_heavy, "light": self.gemini_model_light},
        }

    # 売買設定
    dry_run: bool = field(default_factory=lambda: _env_bool("AITRADER_DRY_RUN", True))
    order_size_btc: float = field(default_factory=lambda: float(os.environ.get("AITRADER_ORDER_SIZE_BTC", "0.001")))
    max_position_btc: float = field(default_factory=lambda: float(os.environ.get("AITRADER_MAX_POSITION_BTC", "0.01")))
    min_jpy_balance: float = field(default_factory=lambda: float(os.environ.get("AITRADER_MIN_JPY_BALANCE", "10000")))
    interval_sec: int = field(default_factory=lambda: int(os.environ.get("AITRADER_INTERVAL_SEC", "3600")))
    trade_cooldown_sec: int = field(default_factory=lambda: int(os.environ.get("AITRADER_COOLDOWN_SEC", "1800")))

    # 履歴蓄積(1分足をSQLiteに貯めて中期指標を育てる)
    history_path: str = field(default_factory=lambda: os.environ.get("AITRADER_HISTORY_PATH", "aitrader_history.db"))

    # 協議会の合意条件
    min_agree_votes: int = field(default_factory=lambda: int(os.environ.get("AITRADER_MIN_AGREE_VOTES", "3")))
    min_score_ratio: float = field(default_factory=lambda: float(os.environ.get("AITRADER_MIN_SCORE_RATIO", "0.55")))

    def validate_for_trading(self):
        """実売買(dry_run=False)に必要な設定が揃っているか確認する。"""
        if self.dry_run:
            return
        missing = []
        if not self.bitflyer_key:
            missing.append("BITFLYER_API_KEY")
        if not self.bitflyer_secret:
            missing.append("BITFLYER_API_SECRET")
        if missing:
            raise RuntimeError(
                "実売買モードには次の環境変数が必要です: " + ", ".join(missing)
            )
