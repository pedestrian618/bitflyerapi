# -*- coding: utf-8 -*-
"""bitFlyer公開APIから相場データを取得し、テクニカル指標を計算する。

短期(1分足・直近約30〜60分)は毎サイクルAPIから直接取得し、
中期(1時間足・最大72時間)はHistoryStoreに蓄積した1分足から組み立てる。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from bitflyerapi import bitFlyerAPI

from .history import HistoryStore


def _px(v: float) -> str:
    """プロンプト用の価格文字列。低単価銘柄(XRP等)は小数を残す。"""
    return f"{v:.0f}" if abs(v) >= 1000 else f"{v:.3f}"


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

    # 板・約定フロー(板読みビュー用)
    bid_depth: float = 0.0      # 中値-0.5%以内の買い板数量
    ask_depth: float = 0.0      # 中値+0.5%以内の売り板数量
    taker_buy_15m: float = 0.0  # 直近15分のテイカー買い数量
    taker_sell_15m: float = 0.0  # 直近15分のテイカー売り数量

    # 外部マクロ(マクロビュー用。取得失敗したキーは入らない)
    macro: dict = None

    def to_prompt_text(self) -> str:
        """ペルソナに渡す相場サマリーのテキスト表現。"""
        recent = self.candles_1m[-30:]
        candle_lines = "\n".join(
            f"{c.time}  O:{_px(c.open)} H:{_px(c.high)} L:{_px(c.low)} C:{_px(c.close)} V:{c.volume:.4f}"
            for c in recent
        )
        base = self.product_code.split("_")[0]

        text = (
            f"銘柄: {self.product_code}\n"
            f"取得時刻(UTC): {self.timestamp}\n"
            f"最終取引価格: {_px(self.ltp)} JPY\n"
            f"買い気配: {_px(self.best_bid)} / 売り気配: {_px(self.best_ask)} (スプレッド: {_px(self.spread)})\n"
            f"24時間出来高: {self.volume_24h:.2f} {base}\n"
            f"板状態: {self.board_state} / ヘルス: {self.health}\n"
            f"\n## 短期(1分足ベース)\n"
            f"短期SMA(10分): {_px(self.sma_short)} / 長期SMA(30分): {_px(self.sma_long)}\n"
            f"RSI(14, 1分足): {self.rsi_14:.1f}\n"
            f"騰落率: 15分 {self.change_pct_15m:+.2f}% / 60分 {self.change_pct_60m:+.2f}%\n"
            f"直近30分の1分足(古い順):\n{candle_lines}\n"
        )

        text += "\n## 中期(1時間足ベース、ローカル蓄積データ)\n"
        hourly = self.candles_1h or []
        if len(hourly) >= 2:
            hour_lines = "\n".join(
                f"{c.time}:00Z  O:{_px(c.open)} H:{_px(c.high)} L:{_px(c.low)} C:{_px(c.close)} "
                f"V:{c.volume:.3f} (データ{c.minutes}分)"
                for c in hourly
            )
            text += (
                f"蓄積データ: 約{self.history_hours}時間分\n"
                f"SMA(8時間): {_px(self.sma_8h)} / SMA(24時間): {_px(self.sma_24h)}\n"
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


def _ema_series(values: list, n: int) -> list:
    if not values:
        return []
    k = 2.0 / (n + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _ema(closes: list, n: int) -> float:
    series = _ema_series(closes, n)
    return series[-1] if series else 0.0


def _true_ranges(candles: list) -> list:
    return [max(cur.high - cur.low,
                abs(cur.high - prev.close),
                abs(cur.low - prev.close))
            for prev, cur in zip(candles[:-1], candles[1:])]


def _atr(candles: list, n: int = 14) -> float:
    """Average True Range。candlesは high/low/close を持つオブジェクト(古い順)。"""
    trs = _true_ranges(candles)
    if not trs:
        return 0.0
    window = trs[-n:]
    return sum(window) / len(window)


def _adx(candles: list, n: int = 14) -> float:
    """簡易ADX(トレンドの強さ 0〜100)。25以上でトレンドが強いとされる。"""
    if len(candles) < n + 1:
        return 0.0
    plus_dm, minus_dm = [], []
    for prev, cur in zip(candles[:-1], candles[1:]):
        up, down = cur.high - prev.high, prev.low - cur.low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    trs = _true_ranges(candles)
    dxs = []
    for i in range(n - 1, len(trs)):
        tr_sum = sum(trs[i - n + 1:i + 1]) or 1e-9
        pdi = 100.0 * sum(plus_dm[i - n + 1:i + 1]) / tr_sum
        mdi = 100.0 * sum(minus_dm[i - n + 1:i + 1]) / tr_sum
        dxs.append(100.0 * abs(pdi - mdi) / ((pdi + mdi) or 1e-9))
    window = dxs[-n:]
    return sum(window) / len(window) if window else 0.0


def _macd(closes: list, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple:
    """(MACD線, シグナル線, ヒストグラム) を返す。データ不足は(0,0,0)。"""
    if len(closes) < slow + signal:
        return (0.0, 0.0, 0.0)
    macd_line = [f - s for f, s in zip(_ema_series(closes, fast),
                                       _ema_series(closes, slow))]
    signal_line = _ema_series(macd_line, signal)
    return (macd_line[-1], signal_line[-1], macd_line[-1] - signal_line[-1])


def _bollinger(closes: list, n: int = 20) -> tuple:
    """(中心線, +2σ, -2σ) を返す。データ不足は(0,0,0)。"""
    if len(closes) < n:
        return (0.0, 0.0, 0.0)
    window = closes[-n:]
    mid = sum(window) / n
    sd = (sum((c - mid) ** 2 for c in window) / n) ** 0.5
    return (mid, mid + 2 * sd, mid - 2 * sd)


def _vwap(candles: list) -> float:
    """出来高加重平均価格(終値近似)。"""
    total_v = sum(c.volume for c in candles)
    if total_v <= 0:
        return 0.0
    return sum(c.close * c.volume for c in candles) / total_v


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


def _taker_flow(executions: list, minutes: int = 15) -> tuple:
    """約定履歴(新しい順)から直近N分のテイカー(買い数量, 売り数量)を集計する。"""
    if not executions:
        return 0.0, 0.0
    try:
        ref = datetime.fromisoformat(executions[0]["exec_date"][:19])
    except (ValueError, KeyError):
        return 0.0, 0.0
    cutoff = ref - timedelta(minutes=minutes)
    buy = sell = 0.0
    for ex in executions:
        try:
            t = datetime.fromisoformat(ex["exec_date"][:19])
        except (ValueError, KeyError):
            continue
        if t < cutoff:
            break  # 新しい順なのでここから先はすべて窓の外
        size = float(ex.get("size", 0.0))
        side = ex.get("side")
        if side == "BUY":
            buy += size
        elif side == "SELL":
            sell += size
    return buy, sell


def _board_depth(api, product_code: str, ltp: float, band_pct: float = 0.5) -> tuple:
    """中値±band_pct%以内の板数量(買い, 売り)。取得失敗は(0, 0)。"""
    try:
        board = api.board(product_code=product_code)
        mid = float(board.get("mid_price") or ltp)
        band = mid * band_pct / 100.0
        bid = sum(float(b["size"]) for b in board.get("bids", [])
                  if float(b["price"]) >= mid - band)
        ask = sum(float(a["size"]) for a in board.get("asks", [])
                  if float(a["price"]) <= mid + band)
        return bid, ask
    except Exception:
        return 0.0, 0.0


def fetch_market_snapshot(product_code: str = "BTC_JPY",
                          store: HistoryStore = None,
                          include_macro: bool = True) -> MarketSnapshot:
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

    bid_depth, ask_depth = _board_depth(api, product_code, ltp)
    taker_buy, taker_sell = _taker_flow(executions)

    macro = None
    if include_macro:
        from .macro import fetch_macro
        macro = fetch_macro()

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
        bid_depth=bid_depth,
        ask_depth=ask_depth,
        taker_buy_15m=taker_buy,
        taker_sell_15m=taker_sell,
        macro=macro,
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
