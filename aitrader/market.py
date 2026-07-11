# -*- coding: utf-8 -*-
"""bitFlyer公開APIから相場データを取得し、テクニカル指標を計算する。

短期(1分足・直近約30〜60分)は毎サイクルAPIから直接取得し、
中期(1時間足・最大72時間)はHistoryStoreに蓄積した1分足から組み立てる。
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from bitflyerapi import bitFlyerAPI

from .history import HistoryStore


@dataclass
class Candle:
    time: str   # ISO8601(分単位)
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class MarketSnapshot:
    product_code: str
    timestamp: str
    ltp: float                  # 最終取引価格
    best_bid: float
    best_ask: float
    spread: float
    volume_24h: float
    board_state: str
    health: str

    # 短期(1分足)
    candles_1m: list            # 直近のローソク足(古い順)
    sma_short: float            # 短期SMA(10本)
    sma_long: float             # 長期SMA(30本)
    rsi_14: float
    change_pct_15m: float       # 直近15分の騰落率(%)
    change_pct_60m: float       # 直近60分の騰落率(%)

    # 中期(1時間足、履歴蓄積から)
    candles_1h: list = None     # list[HourCandle](古い順)
    sma_8h: float = 0.0
    sma_24h: float = 0.0
    rsi_14h: float = 50.0
    change_pct_24h: float = 0.0
    history_hours: int = 0      # 蓄積済みデータのhour数(充足度)

    def to_prompt_text(self) -> str:
        """ペルソナに渡す相場サマリーのテキスト表現。"""
        recent = self.candles_1m[-30:]
        candle_lines = "\n".join(
            f"{c.time}  O:{c.open:.0f} H:{c.high:.0f} L:{c.low:.0f} C:{c.close:.0f} V:{c.volume:.4f}"
            for c in recent
        )

        text = (
            f"銘柄: {self.product_code}\n"
            f"取得時刻(UTC): {self.timestamp}\n"
            f"最終取引価格: {self.ltp:.0f} JPY\n"
            f"買い気配: {self.best_bid:.0f} / 売り気配: {self.best_ask:.0f} (スプレッド: {self.spread:.0f})\n"
            f"24時間出来高: {self.volume_24h:.2f} BTC\n"
            f"板状態: {self.board_state} / ヘルス: {self.health}\n"
            f"\n## 短期(1分足ベース)\n"
            f"短期SMA(10分): {self.sma_short:.0f} / 長期SMA(30分): {self.sma_long:.0f}\n"
            f"RSI(14, 1分足): {self.rsi_14:.1f}\n"
            f"騰落率: 15分 {self.change_pct_15m:+.2f}% / 60分 {self.change_pct_60m:+.2f}%\n"
            f"直近30分の1分足(古い順):\n{candle_lines}\n"
        )

        text += "\n## 中期(1時間足ベース、ローカル蓄積データ)\n"
        hourly = self.candles_1h or []
        if len(hourly) >= 2:
            hour_lines = "\n".join(
                f"{c.time}:00Z  O:{c.open:.0f} H:{c.high:.0f} L:{c.low:.0f} C:{c.close:.0f} "
                f"V:{c.volume:.3f} (データ{c.minutes}分)"
                for c in hourly
            )
            text += (
                f"蓄積データ: 約{self.history_hours}時間分\n"
                f"SMA(8時間): {self.sma_8h:.0f} / SMA(24時間): {self.sma_24h:.0f}\n"
                f"RSI(14, 1時間足): {self.rsi_14h:.1f}\n"
                f"24時間騰落率: {self.change_pct_24h:+.2f}%\n"
                f"1時間足(古い順、最大72本):\n{hour_lines}\n"
            )
            if self.history_hours < 24:
                text += (
                    "\n注意: 履歴蓄積を開始してから日が浅く、中期データは不完全です。"
                    "中期指標の信頼度は低めに見積もってください。\n"
                )
        else:
            text += (
                "まだ十分な履歴がありません(蓄積開始直後)。"
                "短期データのみで判断し、確信度は控えめにしてください。\n"
            )
        return text


def _build_candles_1m(executions: list) -> list:
    """約定履歴(新しい順で返る)から1分足を組み立てる。古い順で返す。"""
    buckets = {}
    for ex in executions:
        # exec_date例: "2024-01-01T12:34:56.789"
        minute = ex["exec_date"][:16]  # "YYYY-MM-DDTHH:MM"
        price = float(ex["price"])
        size = float(ex["size"])
        b = buckets.get(minute)
        if b is None:
            # 新しい順に走査するので、最初に見た約定がそのバケットの「最後(close)」
            buckets[minute] = {"open": price, "high": price, "low": price,
                               "close": price, "volume": size}
        else:
            b["open"] = price  # 走査が進むほど古い約定 → openを上書き
            b["high"] = max(b["high"], price)
            b["low"] = min(b["low"], price)
            b["volume"] += size
    candles = [
        Candle(time=minute + ":00Z", **vals)
        for minute, vals in sorted(buckets.items())
    ]
    return candles


def _sma(closes: list, n: int) -> float:
    if not closes:
        return 0.0
    window = closes[-n:]
    return sum(window) / len(window)


def _rsi(closes: list, n: int = 14) -> float:
    if len(closes) < n + 1:
        return 50.0
    gains, losses = 0.0, 0.0
    for prev, cur in zip(closes[-n - 1:-1], closes[-n:]):
        diff = cur - prev
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100.0 - 100.0 / (1.0 + rs)


def _change_pct(closes: list, periods: int) -> float:
    if len(closes) <= periods:
        return 0.0
    base = closes[-periods - 1]
    if base == 0:
        return 0.0
    return (closes[-1] - base) / base * 100.0


def fetch_market_snapshot(product_code: str = "BTC_JPY",
                          store: HistoryStore = None) -> MarketSnapshot:
    """相場スナップショットを構築する(認証不要)。

    store を渡すと、取得した1分足を蓄積し、蓄積済みデータから
    中期(1時間足)の指標も計算して含める。
    """
    api = bitFlyerAPI(key="", secret="")

    ticker = api.ticker(product_code=product_code)
    executions = api.executions(product_code=product_code, count=500)
    try:
        boardstate = api.getboardstate(product_code=product_code)
    except Exception:
        boardstate = {"state": "UNKNOWN", "health": "UNKNOWN"}

    candles = _build_candles_1m(executions)
    closes = [c.close for c in candles]

    ltp = float(ticker["ltp"])
    best_bid = float(ticker["best_bid"])
    best_ask = float(ticker["best_ask"])

    snapshot = MarketSnapshot(
        product_code=product_code,
        timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ltp=ltp,
        best_bid=best_bid,
        best_ask=best_ask,
        spread=best_ask - best_bid,
        volume_24h=float(ticker.get("volume_by_product", 0.0)),
        board_state=str(boardstate.get("state", "UNKNOWN")),
        health=str(boardstate.get("health", "UNKNOWN")),
        candles_1m=candles,
        sma_short=_sma(closes, 10),
        sma_long=_sma(closes, 30),
        rsi_14=_rsi(closes, 14),
        change_pct_15m=_change_pct(closes, 15),
        change_pct_60m=_change_pct(closes, 60),
    )

    if store is not None:
        store.upsert_candles(product_code, candles)
        hourly = store.hourly_candles(product_code, hours=72)
        hourly_closes = [c.close for c in hourly]
        snapshot.candles_1h = hourly
        snapshot.sma_8h = _sma(hourly_closes, 8)
        snapshot.sma_24h = _sma(hourly_closes, 24)
        snapshot.rsi_14h = _rsi(hourly_closes, 14)
        snapshot.change_pct_24h = _change_pct(hourly_closes, 24)
        snapshot.history_hours = store.coverage_hours(product_code)

    return snapshot
