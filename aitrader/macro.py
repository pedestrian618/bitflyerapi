# -*- coding: utf-8 -*-
"""外部マクロデータの取得(マクロ分析官・大局のビュー用)。

すべてAPIキー不要の公開エンドポイントを使う。取得はベストエフォートで、
失敗したデータは辞書に入れない(呼び出し側は欠損を「取得失敗」と表示する)。
外部依存の障害が売買サイクルを止めないよう、例外はすべて握りつぶす。
"""

import logging

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 6


def _coingecko_global(out: dict):
    """BTCドミナンスと暗号資産市場全体の24時間増減。"""
    r = requests.get("https://api.coingecko.com/api/v3/global", timeout=_TIMEOUT)
    r.raise_for_status()
    data = r.json()["data"]
    out["btc_dominance"] = float(data["market_cap_percentage"]["btc"])
    out["crypto_mcap_change_24h"] = float(
        data.get("market_cap_change_percentage_24h_usd", 0.0))


def _stooq_quote(symbol: str) -> tuple:
    """stooqの無料CSVから(始値, 終値/現在値)を取る。"""
    r = requests.get(
        "https://stooq.com/q/l/",
        params={"s": symbol, "f": "sd2t2ohlcv", "h": "", "e": "csv"},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    lines = r.text.strip().splitlines()
    # 1行目ヘッダ: Symbol,Date,Time,Open,High,Low,Close,Volume
    fields = lines[1].split(",")
    return float(fields[3]), float(fields[6])


def fetch_macro() -> dict:
    """取得できたものだけを含む辞書を返す。

    キー: btc_dominance, crypto_mcap_change_24h,
          usdjpy, usdjpy_change_pct, nasdaq, nasdaq_change_pct
    """
    out = {}
    for fetch in (_coingecko_global, _fetch_usdjpy, _fetch_nasdaq):
        try:
            fetch(out)
        except Exception:
            logger.debug("マクロデータ取得失敗: %s", fetch.__name__, exc_info=True)
    return out


def _fetch_usdjpy(out: dict):
    o, c = _stooq_quote("usdjpy")
    out["usdjpy"] = c
    if o:
        out["usdjpy_change_pct"] = (c - o) / o * 100.0


def _fetch_nasdaq(out: dict):
    o, c = _stooq_quote("^ndq")  # NASDAQ総合指数
    out["nasdaq"] = c
    if o:
        out["nasdaq_change_pct"] = (c - o) / o * 100.0
