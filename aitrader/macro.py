# -*- coding: utf-8 -*-
"""外部マクロデータの取得(マクロ分析官・大局のビュー用)。

すべてAPIキー不要の公開エンドポイントを使う。取得はベストエフォートで、
失敗したデータは辞書に入れない(呼び出し側は欠損を「取得失敗」と表示する)。
外部依存の障害が売買サイクルを止めないよう、例外はすべて握りつぶす。

データソースは複数持ち、上から順に試して最初に成功したものを使う:
  NASDAQ:  stooq → Yahoo Finance
  ドル円:   stooq → Yahoo Finance → frankfurter(ECB参照レート、レベルのみ)
  ドミナンス: CoinGecko

stooq / Yahoo はbotのデフォルトUAを弾くことがあるため、ブラウザ相当の
User-Agent を付ける。
"""

import logging
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 6
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "*/*",
}


def _get(url: str, params: dict = None) -> requests.Response:
    r = requests.get(url, params=params, timeout=_TIMEOUT, headers=_HEADERS)
    r.raise_for_status()
    return r


def _stooq_quote(symbol: str) -> tuple:
    """stooqの無料CSVから(基準値=始値, 現在値)を取る。"""
    r = _get("https://stooq.com/q/l/",
             params={"s": symbol, "f": "sd2t2ohlcv", "h": "", "e": "csv"})
    # 1行目ヘッダ: Symbol,Date,Time,Open,High,Low,Close,Volume
    fields = r.text.strip().splitlines()[1].split(",")
    return float(fields[3]), float(fields[6])


def _yahoo_quote(symbol: str) -> tuple:
    """Yahoo Financeのチャートエンドポイントから(前日終値, 現在値)を取る。"""
    r = _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}",
             params={"range": "2d", "interval": "1d"})
    meta = r.json()["chart"]["result"][0]["meta"]
    last = float(meta["regularMarketPrice"])
    prev = float(meta.get("chartPreviousClose")
                 or meta.get("previousClose") or 0.0)
    return prev, last


def _frankfurter_usdjpy() -> tuple:
    """ECB参照レート(1日1回更新)。変化率は取れないので基準値0を返す。"""
    r = _get("https://api.frankfurter.app/latest",
             params={"from": "USD", "to": "JPY"})
    return 0.0, float(r.json()["rates"]["JPY"])


def _first_success(sources: list) -> tuple:
    """(基準値, 現在値) を返す最初に成功したソースを使う。全滅ならNone。"""
    for source in sources:
        try:
            return source()
        except Exception:
            continue
    return None


def _coingecko_global(out: dict):
    """BTCドミナンスと暗号資産市場全体の24時間増減。"""
    r = _get("https://api.coingecko.com/api/v3/global")
    data = r.json()["data"]
    out["btc_dominance"] = float(data["market_cap_percentage"]["btc"])
    out["crypto_mcap_change_24h"] = float(
        data.get("market_cap_change_percentage_24h_usd", 0.0))


def _fetch_nasdaq(out: dict):
    quote_pair = _first_success([
        lambda: _stooq_quote("^ndq"),
        lambda: _yahoo_quote("^IXIC"),
    ])
    if quote_pair is None:
        raise RuntimeError("全ソース失敗")
    base, last = quote_pair
    out["nasdaq"] = last
    if base:
        out["nasdaq_change_pct"] = (last - base) / base * 100.0


def _fetch_usdjpy(out: dict):
    quote_pair = _first_success([
        lambda: _stooq_quote("usdjpy"),
        lambda: _yahoo_quote("USDJPY=X"),
        _frankfurter_usdjpy,
    ])
    if quote_pair is None:
        raise RuntimeError("全ソース失敗")
    base, last = quote_pair
    out["usdjpy"] = last
    if base:
        out["usdjpy_change_pct"] = (last - base) / base * 100.0


def fetch_macro() -> dict:
    """取得できたものだけを含む辞書を返す。

    キー: btc_dominance, crypto_mcap_change_24h,
          usdjpy, usdjpy_change_pct, nasdaq, nasdaq_change_pct
    """
    out = {}
    failed = []
    for name, fetch in (("dominance", _coingecko_global),
                        ("usdjpy", _fetch_usdjpy),
                        ("nasdaq", _fetch_nasdaq)):
        try:
            fetch(out)
        except Exception:
            failed.append(name)
    if failed:
        # 障害の切り分けができるよう、失敗ソースをINFOで残す(スタックは出さない)
        logger.info("マクロデータ取得失敗: %s (サイクルは継続)", ", ".join(failed))
    return out
