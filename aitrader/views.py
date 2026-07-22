# -*- coding: utf-8 -*-
"""ペルソナ別の情報源ビュー。

全ペルソナに同じ相場サマリーを渡すと意見が同じ指標(例: RSI)に引っ張られて
相関してしまうため、ペルソナの専門分野ごとに「見えるデータ」を分ける。
情報源が独立するほど、協議会の重み付き投票の価値が上がる。

  trend    (波多野): SMA/EMA/ADX/高値安値 — トレンドの向きと強さ
  momentum (逆瀬川): RSI/MACD/ROC/ボリンジャー — 過熱と行き過ぎ
  flow     (疾風):   板の厚み/テイカーフロー/1分足 — いまこの瞬間の需給
  risk     (堅田):   ATR/VWAP乖離/出来高急増/レンジ — リスクの物差し
  macro    (大局):   1時間足72本/騰落/外部マクロ — 地合い

共通ブロック(全員に渡る)は現在値・スプレッド・板ヘルス・ポジション情報のみ。
"""

from .market import (MarketSnapshot, _adx, _atr, _bollinger, _change_pct,
                     _ema, _macd, _px, _rsi, _sma, _vwap)


def _common_header(s: MarketSnapshot, position: dict = None) -> str:
    spread_pct = s.spread / s.ltp * 100 if s.ltp else 0.0
    text = (
        f"銘柄: {s.product_code}\n"
        f"取得時刻(UTC): {s.timestamp}\n"
        f"最終取引価格: {_px(s.ltp)} JPY\n"
        f"買い気配: {_px(s.best_bid)} / 売り気配: {_px(s.best_ask)} "
        f"(スプレッド: {_px(s.spread)} = {spread_pct:.3f}%)\n"
        f"板状態: {s.board_state} / ヘルス: {s.health}\n"
    )
    text += _position_text(s, position)
    return text


def _position_text(s: MarketSnapshot, position: dict = None) -> str:
    """協議会の現在ポジション。SELLが「利確」か「新規売り」かを判断させる材料。"""
    text = "\n## 現在のポジション(協議会の台帳)\n"
    if not position or position.get("position", 0.0) <= 0:
        text += "保有なし(ノーポジション)。SELLは見送りになるため、実質BUYかHOLDの二択です。\n"
    else:
        pos = position["position"]
        avg = position.get("avg_cost", 0.0)
        pnl_pct = (s.ltp - avg) / avg * 100.0 if avg else 0.0
        text += (f"保有: {pos:.4f} @ 平均取得単価 {_px(avg)} JPY "
                 f"(含み損益 {pnl_pct:+.2f}%)\n")
    last = (position or {}).get("last_trade")
    if last:
        text += f"直近の約定: {last['side']} {_px(last['price'])} JPY ({last['ts']})\n"
    return text


def _incomplete_note(s: MarketSnapshot) -> str:
    if not s.candles_1h or len(s.candles_1h) < 2:
        return ("\n注意: まだ十分な履歴がありません(蓄積開始直後)。"
                "短期データのみで判断し、確信度は控えめにしてください。\n")
    if s.history_hours < 24:
        return ("\n注意: 履歴蓄積を開始してから日が浅く、中期データは不完全です。"
                "中期指標の信頼度は低めに見積もってください。\n")
    return ""


def _hourly_lines(s: MarketSnapshot, hours: int = None) -> str:
    hourly = s.candles_1h or []
    if hours:
        hourly = hourly[-hours:]
    return "\n".join(
        f"{c.time}:00Z  O:{_px(c.open)} H:{_px(c.high)} L:{_px(c.low)} C:{_px(c.close)} "
        f"V:{c.volume:.3f} (データ{c.minutes}分)"
        for c in hourly
    )


def _minute_lines(s: MarketSnapshot, minutes: int = 30) -> str:
    return "\n".join(
        f"{c.time}  O:{_px(c.open)} H:{_px(c.high)} L:{_px(c.low)} C:{_px(c.close)} V:{c.volume:.4f}"
        for c in s.candles_1m[-minutes:]
    )


def _volume_surge(s: MarketSnapshot) -> tuple:
    """(直近1時間の出来高, 過去24時間の1時間平均, 倍率)。データ不足は(0,0,0)。"""
    hourly = [c for c in (s.candles_1h or []) if c.minutes >= 40]
    if len(hourly) < 4:
        return 0.0, 0.0, 0.0
    recent = hourly[-1].volume
    baseline = [c.volume for c in hourly[-25:-1]]
    avg = sum(baseline) / len(baseline) if baseline else 0.0
    return recent, avg, (recent / avg if avg > 0 else 0.0)


def _high_low(s: MarketSnapshot, hours: int) -> tuple:
    window = (s.candles_1h or [])[-hours:]
    if not window:
        return 0.0, 0.0
    return max(c.high for c in window), min(c.low for c in window)


def _sr_text(s: MarketSnapshot) -> str:
    """直近24/72時間の高値・安値と現在値からの乖離。"""
    parts = []
    for hours in (24, 72):
        hi, lo = _high_low(s, hours)
        if hi and s.ltp:
            parts.append(
                f"直近{hours}時間: 高値 {_px(hi)} ({(s.ltp - hi) / hi * 100:+.2f}%乖離) / "
                f"安値 {_px(lo)} ({(s.ltp - lo) / lo * 100:+.2f}%乖離)")
    return "\n".join(parts) + "\n" if parts else "履歴不足のため算出不可\n"


# --- 各ビュー ---

def _trend_view(s: MarketSnapshot) -> str:
    closes_1h = [c.close for c in (s.candles_1h or [])]
    adx = _adx(s.candles_1h or [], 14)
    text = "\n## トレンド指標(1時間足ベース)\n"
    if closes_1h:
        text += (
            f"SMA(8時間): {_px(_sma(closes_1h, 8))} / SMA(24時間): {_px(_sma(closes_1h, 24))}\n"
            f"EMA(8時間): {_px(_ema(closes_1h, 8))} / EMA(24時間): {_px(_ema(closes_1h, 24))}\n"
            f"ADX(14, 1時間足): {adx:.1f} (25以上でトレンドが強い)\n"
            f"24時間騰落率: {s.change_pct_24h:+.2f}% / 60分騰落率: {s.change_pct_60m:+.2f}%\n"
        )
    text += "\n## 節目(高値・安値)\n" + _sr_text(s)
    hourly = _hourly_lines(s)
    if hourly:
        text += f"\n## 1時間足(古い順、最大72本)\n{hourly}\n"
    return text


def _momentum_view(s: MarketSnapshot) -> str:
    closes_1h = [c.close for c in (s.candles_1h or [])]
    text = "\n## モメンタム指標\n"
    text += f"RSI(14, 1分足): {s.rsi_14:.1f} / RSI(14, 1時間足): {s.rsi_14h:.1f}\n"
    if closes_1h:
        macd, signal, hist = _macd(closes_1h)
        mid, upper, lower = _bollinger(closes_1h, 20)
        text += (
            f"MACD(12,26,9 1時間足): MACD {macd:+.0f} / シグナル {signal:+.0f} / "
            f"ヒストグラム {hist:+.0f}\n"
            f"ROC(騰落率): 60分 {s.change_pct_60m:+.2f}% / 3時間 {_change_pct(closes_1h, 3):+.2f}% / "
            f"24時間 {s.change_pct_24h:+.2f}%\n"
        )
        if mid:
            text += (f"ボリンジャーバンド(20, 1時間足): 中心 {_px(mid)} / "
                     f"+2σ {_px(upper)} / -2σ {_px(lower)} (現在値 {_px(s.ltp)})\n")
    text += f"15分騰落率: {s.change_pct_15m:+.2f}%\n"
    closes_recent = [c.close for c in s.candles_1m][-24:]
    if closes_recent:
        text += ("\n## 直近の終値推移(1分足、古い順)\n"
                 + " ".join(_px(c) for c in closes_recent) + "\n")
    return text


def _flow_view(s: MarketSnapshot) -> str:
    total_depth = s.bid_depth + s.ask_depth
    total_flow = s.taker_buy_15m + s.taker_sell_15m
    text = "\n## 板・約定フロー\n"
    if total_depth > 0:
        text += (f"板の厚み(中値±0.5%): 買い {s.bid_depth:.3f} / 売り {s.ask_depth:.3f} "
                 f"(買い板比率 {s.bid_depth / total_depth * 100:.0f}%)\n")
    else:
        text += "板の厚み: 取得できませんでした\n"
    if total_flow > 0:
        text += (f"テイカーフロー(直近15分): 買い {s.taker_buy_15m:.3f} / "
                 f"売り {s.taker_sell_15m:.3f} "
                 f"(買い比率 {s.taker_buy_15m / total_flow * 100:.0f}%)\n")
    else:
        text += "テイカーフロー: 直近15分の約定データなし\n"
    text += (f"騰落率: 15分 {s.change_pct_15m:+.2f}% / 60分 {s.change_pct_60m:+.2f}%\n"
             f"短期SMA(10分): {_px(s.sma_short)} / 長期SMA(30分): {_px(s.sma_long)}\n")
    minute = _minute_lines(s, 30)
    if minute:
        text += f"\n## 直近30分の1分足(古い順)\n{minute}\n"
    return text


def _risk_view(s: MarketSnapshot) -> str:
    hourly = s.candles_1h or []
    text = "\n## リスク・出来高指標\n"
    if hourly:
        atr = _atr(hourly, 14)
        atr_pct = atr / s.ltp * 100 if s.ltp else 0.0
        vwap = _vwap(hourly[-24:])
        text += f"ATR(14, 1時間足): {_px(atr)} (現在値比 {atr_pct:.2f}%)\n"
        if vwap:
            text += (f"VWAP(24時間): {_px(vwap)} "
                     f"(現在値乖離 {(s.ltp - vwap) / vwap * 100:+.2f}%)\n")
        hi, lo = _high_low(s, 24)
        if hi and lo:
            text += f"直近24時間レンジ: {_px(lo)} 〜 {_px(hi)} (幅 {(hi - lo) / lo * 100:.2f}%)\n"
    recent, avg, surge = _volume_surge(s)
    if surge:
        text += (f"出来高: 直近1時間 {recent:.2f} / 過去24時間平均 {avg:.2f} "
                 f"(平常比 {surge:.1f}倍)\n")
    text += f"24時間騰落率: {s.change_pct_24h:+.2f}% / 60分騰落率: {s.change_pct_60m:+.2f}%\n"
    text += "\n## 節目(高値・安値)\n" + _sr_text(s)
    hourly_text = _hourly_lines(s, 24)
    if hourly_text:
        text += f"\n## 1時間足(直近24本、古い順)\n{hourly_text}\n"
    return text


def _macro_view(s: MarketSnapshot) -> str:
    closes_1h = [c.close for c in (s.candles_1h or [])]
    text = "\n## 地合い(中期)\n"
    if closes_1h:
        text += (
            f"SMA(8時間): {_px(_sma(closes_1h, 8))} / SMA(24時間): {_px(_sma(closes_1h, 24))}\n"
            f"24時間騰落率: {s.change_pct_24h:+.2f}%\n"
        )
    text += f"24時間出来高: {s.volume_24h:.2f} {s.product_code.split('_')[0]}\n"

    text += "\n## 外部マクロ(参考値)\n"
    m = s.macro or {}
    if "btc_dominance" in m:
        text += f"BTCドミナンス: {m['btc_dominance']:.1f}%\n"
    if "crypto_mcap_change_24h" in m:
        text += f"暗号資産時価総額(24時間): {m['crypto_mcap_change_24h']:+.2f}%\n"
    if "nasdaq" in m:
        chg = f" (日中 {m['nasdaq_change_pct']:+.2f}%)" if "nasdaq_change_pct" in m else ""
        text += f"NASDAQ総合: {m['nasdaq']:,.0f}{chg}\n"
    if "usdjpy" in m:
        chg = f" (日中 {m['usdjpy_change_pct']:+.2f}%)" if "usdjpy_change_pct" in m else ""
        text += f"ドル円: {m['usdjpy']:.2f}{chg}\n"
    if not m:
        text += "外部データは取得できませんでした(bitFlyerのデータのみで判断してください)\n"

    hourly = _hourly_lines(s)
    if hourly:
        text += f"\n## 1時間足(古い順、最大72本)\n{hourly}\n"
    return text


_VIEWS = {
    "trend": _trend_view,
    "momentum": _momentum_view,
    "flow": _flow_view,
    "risk": _risk_view,
    "macro": _macro_view,
}


def build_view_text(snapshot: MarketSnapshot, view: str,
                    position: dict = None) -> str:
    """ペルソナのビューに応じた相場テキストを組み立てる。

    未知のビュー(または未指定)は従来どおり全部入りのサマリーを返す。
    """
    builder = _VIEWS.get(view)
    if builder is None:
        return snapshot.to_prompt_text()
    return (_common_header(snapshot, position)
            + builder(snapshot)
            + _incomplete_note(snapshot))
