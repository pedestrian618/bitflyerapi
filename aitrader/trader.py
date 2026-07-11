# -*- coding: utf-8 -*-
"""注文執行とリスク管理。デフォルトはドライラン(実注文なし)。"""

import logging
import time

from bitflyerapi import bitFlyerAPI

from .config import Config

logger = logging.getLogger(__name__)


class Trader:
    def __init__(self, config: Config):
        self.config = config
        self.api = None
        if config.bitflyer_key and config.bitflyer_secret:
            self.api = bitFlyerAPI(key=config.bitflyer_key,
                                   secret=config.bitflyer_secret)
        self._last_trade_at = 0.0

    # --- 残高・ポジション ---

    def get_balances(self) -> dict:
        """{"JPY": float, "BTC": float} を返す。APIキーが無ければ0扱い。"""
        balances = {"JPY": 0.0, "BTC": 0.0}
        if self.api is None:
            return balances
        for b in self.api.getbalance():
            code = b.get("currency_code")
            if code in balances:
                balances[code] = float(b.get("available", 0.0))
        return balances

    # --- リスクチェック ---

    def check_risk(self, decision: str) -> str:
        """発注可否を判定する。発注可なら空文字、不可なら理由を返す。"""
        now = time.time()
        if now - self._last_trade_at < self.config.trade_cooldown_sec:
            remain = int(self.config.trade_cooldown_sec - (now - self._last_trade_at))
            return f"クールダウン中(あと{remain}秒)"

        if self.config.dry_run:
            return ""  # ドライランは常に通す(ログ目的)

        balances = self.get_balances()
        if decision == "BUY":
            if balances["JPY"] < self.config.min_jpy_balance:
                return f"JPY残高不足({balances['JPY']:.0f} < {self.config.min_jpy_balance:.0f})"
            if balances["BTC"] + self.config.order_size_btc > self.config.max_position_btc:
                return (f"最大ポジション超過(現在 {balances['BTC']:.4f} BTC, "
                        f"上限 {self.config.max_position_btc:.4f} BTC)")
        elif decision == "SELL":
            if balances["BTC"] < self.config.order_size_btc:
                return f"BTC残高不足({balances['BTC']:.6f} < {self.config.order_size_btc:.6f})"
        return ""

    # --- 執行 ---

    def execute(self, decision: str) -> dict:
        """協議会の結論に従って成行注文を出す。

        戻り値: {"executed": bool, "reason": str, "order": dict|None}
        """
        if decision == "HOLD":
            return {"executed": False, "reason": "HOLD(様子見)", "order": None}

        blocked = self.check_risk(decision)
        if blocked:
            logger.warning("発注見送り: %s", blocked)
            return {"executed": False, "reason": blocked, "order": None}

        if self.config.dry_run:
            logger.info("[DRY RUN] %s %s %.6f BTC (成行) — 実注文は送信していません",
                        self.config.product_code, decision, self.config.order_size_btc)
            self._last_trade_at = time.time()
            return {"executed": False,
                    "reason": "ドライランのため実注文なし",
                    "order": {"side": decision, "size": self.config.order_size_btc,
                              "dry_run": True}}

        result = self.api.sendchildorder(
            product_code=self.config.product_code,
            child_order_type="MARKET",
            side=decision,
            size=self.config.order_size_btc,
        )
        if isinstance(result, dict) and "child_order_acceptance_id" in result:
            self._last_trade_at = time.time()
            logger.info("発注成功: %s %s %.6f BTC (受付ID: %s)",
                        self.config.product_code, decision,
                        self.config.order_size_btc,
                        result["child_order_acceptance_id"])
            return {"executed": True, "reason": "発注成功", "order": result}

        logger.error("発注失敗: %s", result)
        return {"executed": False, "reason": f"発注失敗: {result}", "order": result}
