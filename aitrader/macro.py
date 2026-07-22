# -*- coding: utf-8 -*-
"""外部マクロデータの取得(マクロ分析官・大局のビュー用)。

すべてAPIキー不要の公開エンドポイントを使う。取得はベストエフォートで、
失敗したデータは辞書に入れない(呼び出し側は欠損を「取得失敗」と表示する)。
外部依存の障害が売買サイクルを止めないよう、例外はすべて握りつぶす。

データソース:
  NASDAQ:  FRED(セントルイス連銀の公開CSV。終値ベースで約1営業日遅れ)
  ドル円:   frankfurter(ECB参照レート。1日1回更新、直近2営業日で変化率を出す)
  ドミナンス: CoinGecko

かつて使っていた stooq はエンドポイント消滅+bot対策で、Yahoo Finance は
cookie なしアクセスへの 429 で、どちらも無認証では恒常的に使えなくなった。
"""

import logging
from datetime import date, timedelta

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 6
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "*/*",
}


def _get(url: str, params: dict = None, headers: dict = _HEADERS) -> requests.Response:
    r = requests.get(url, params=params, timeout=_TIMEOUT, headers=headers)
    r.raise_for_status()
    return r


def _coingecko_global(out: dict):
    """BTCドミナンスと暗号資産市場全体の24時間増減。"""
    r = _get("https://api.coingecko.com/api/v3/global")
    data = r.json()["data"]
    out["btc_dominance"] = float(data["market_cap_percentage"]["btc"])
    out["crypto_mcap_change_24h"] = float(
        data.get("market_cap_change_percentage_24h_usd", 0.0))


def _fetch_nasdaq(out: dict):
    """FREDの公開CSVからNASDAQ総合の直近2営業日の終値を取る。

    行形式は "YYYY-MM-DD,26206.890"。休場日は値が "." になるので読み飛ばす。
    cosd(取得開始日)を絞らないと1971年からの全履歴が返ってくる。
    FREDはブラウザ偽装UAをTLSフィンガープリント不一致で遮断するため、
    素のUA(python-requests)で取得すること。
    """
    start = (date.today() - timedelta(days=14)).isoformat()
    r = _get("https://fred.stlouisfed.org/graph/fredgraph.csv",
             params={"id": "NASDAQCOM", "cosd": start}, headers=None)
    closes = []
    for line in r.text.strip().splitlines()[1:]:
        _, _, value = line.partition(",")
        if value and value != ".":
            closes.append(float(value))
    if not closes:
        raise RuntimeError("有効な終値なし")
    out["nasdaq"] = closes[-1]
    if len(closes) >= 2 and closes[-2]:
        out["nasdaq_change_pct"] = (closes[-1] - closes[-2]) / closes[-2] * 100.0


def _fetch_usdjpy(out: dict):
    """frankfurter(ECB参照レート)の時系列から直近2営業日のドル円を取る。"""
    start = (date.today() - timedelta(days=14)).isoformat()
    r = _get(f"https://api.frankfurter.dev/v1/{start}..",
             params={"base": "USD", "symbols": "JPY"})
    rates = r.json()["rates"]
    values = [float(rates[day]["JPY"]) for day in sorted(rates)]
    if not values:
        raise RuntimeError("有効なレートなし")
    out["usdjpy"] = values[-1]
    if len(values) >= 2 and values[-2]:
        out["usdjpy_change_pct"] = (values[-1] - values[-2]) / values[-2] * 100.0


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
