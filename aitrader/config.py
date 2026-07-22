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
    # heavy はコスト都合で Opus ではなく Sonnet(判断タスクならほぼ同品質で約6割安)
    claude_model_heavy: str = field(default_factory=lambda: os.environ.get(
        "AITRADER_CLAUDE_MODEL_HEAVY", os.environ.get("AITRADER_MODEL", "claude-sonnet-5")))
    claude_model_light: str = field(default_factory=lambda: os.environ.get(
        "AITRADER_CLAUDE_MODEL_LIGHT", "claude-haiku-4-5"))
    # 2026-07時点の現行世代。luna は gpt-5.1 より新しく安い($1/$6 vs $1.25/$10)ため
    # heavy/light とも luna をデフォルトにする(判断タスクには十分)
    openai_model_heavy: str = field(default_factory=lambda: os.environ.get(
        "AITRADER_OPENAI_MODEL_HEAVY", "gpt-5.6-luna"))
    openai_model_light: str = field(default_factory=lambda: os.environ.get(
        "AITRADER_OPENAI_MODEL_LIGHT", "gpt-5.6-luna"))
    # Gemini は固定バージョンの廃止が早いため常に最新安定版を指すエイリアスを使う
    # (gemini-2.5-pro は 2026-07 時点で新規ユーザーに 404 を返す)
    gemini_model_heavy: str = field(default_factory=lambda: os.environ.get(
        "AITRADER_GEMINI_MODEL_HEAVY", "gemini-pro-latest"))
    gemini_model_light: str = field(default_factory=lambda: os.environ.get(
        "AITRADER_GEMINI_MODEL_LIGHT", "gemini-flash-latest"))
    llm_cooldown_sec: int = field(default_factory=lambda: int(os.environ.get(
        "AITRADER_LLM_COOLDOWN_SEC", "600")))

    def llm_models(self) -> dict:
        return {
            "claude": {"heavy": self.claude_model_heavy, "light": self.claude_model_light},
            "openai": {"heavy": self.openai_model_heavy, "light": self.openai_model_light},
            "gemini": {"heavy": self.gemini_model_heavy, "light": self.gemini_model_light},
        }

    # 売買設定
    # order_size_btc / max_position_btc の単位は「取引銘柄の基軸通貨」
    # (BTC_JPYならBTC、ETH_JPYならETH)。フィールド名は互換のため据え置き。
    # AITRADER_ORDER_SIZE / AITRADER_MAX_POSITION が優先され、
    # 旧名の *_BTC はBTC専用時代との互換用フォールバック。
    dry_run: bool = field(default_factory=lambda: _env_bool("AITRADER_DRY_RUN", True))
    order_size_btc: float = field(default_factory=lambda: float(os.environ.get(
        "AITRADER_ORDER_SIZE", os.environ.get("AITRADER_ORDER_SIZE_BTC", "0.001"))))
    max_position_btc: float = field(default_factory=lambda: float(os.environ.get(
        "AITRADER_MAX_POSITION", os.environ.get("AITRADER_MAX_POSITION_BTC", "0.01"))))
    min_jpy_balance: float = field(default_factory=lambda: float(os.environ.get("AITRADER_MIN_JPY_BALANCE", "10000")))
    interval_sec: int = field(default_factory=lambda: int(os.environ.get("AITRADER_INTERVAL_SEC", "3600")))
    trade_cooldown_sec: int = field(default_factory=lambda: int(os.environ.get("AITRADER_COOLDOWN_SEC", "1800")))

    # 履歴蓄積(1分足をSQLiteに貯めて中期指標を育てる)
    history_path: str = field(default_factory=lambda: os.environ.get("AITRADER_HISTORY_PATH", "aitrader_history.db"))

    # ダッシュボード(静的HTML)の出力先。空なら生成しない。
    # public_html 配下を指定するとブラウザから稼働状況を確認できる
    dashboard_path: str = field(default_factory=lambda: os.environ.get("AITRADER_DASHBOARD_PATH", ""))

    # 複数銘柄インスタンスのダッシュボードを相互リンクするタブ。
    # 形式: "BTC_JPY=./,ETH_JPY=./eth/" (ラベル=URL をカンマ区切り。
    # ラベルが自分の product_code と一致するタブがハイライトされる)
    dashboard_links: str = field(default_factory=lambda: os.environ.get("AITRADER_DASHBOARD_LINKS", ""))

    @property
    def base_currency(self) -> str:
        """取引銘柄の基軸通貨(BTC_JPY → BTC)。表示用。"""
        return self.product_code.split("_")[0]

    # 協議会の合意条件
    min_agree_votes: int = field(default_factory=lambda: int(os.environ.get("AITRADER_MIN_AGREE_VOTES", "3")))
    min_score_ratio: float = field(default_factory=lambda: float(os.environ.get("AITRADER_MIN_SCORE_RATIO", "0.55")))

    # LLMコストの円換算レート(表示用。厳密なレートである必要はない)
    usdjpy_rate: float = field(default_factory=lambda: float(os.environ.get("AITRADER_USDJPY", "155")))

    # 売買の往復コスト(%)。ペルソナが期待騰落率と比較するHOLD閾値として
    # プロンプトに埋め込まれる(取引所手数料0.15%×2 + スプレッド概算)
    round_trip_cost_pct: float = field(default_factory=lambda: float(
        os.environ.get("AITRADER_ROUND_TRIP_COST_PCT", "0.35")))

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
