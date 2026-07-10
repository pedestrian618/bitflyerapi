# -*- coding: utf-8 -*-
"""CLIエントリポイント。

使い方:
    python -m aitrader            # ループ実行(デフォルト: ドライラン)
    python -m aitrader --once     # 1サイクルだけ実行して終了
    python -m aitrader --collect  # 市況データの収集のみ(LLM・売買なし)
"""

import argparse
import logging
import os
from pathlib import Path

from .bot import run_loop, run_once
from .config import Config
from .council import Council
from .history import HistoryStore
from .market import fetch_market_snapshot
from .trader import Trader


def _load_dotenv():
    """プロジェクト直下の .env と共有AIキーを読み込む(設定済みの環境変数は上書きしない)。

    通常は direnv (.envrc) が同じファイルを環境変数に展開するので何もしない。
    direnv の効かない cron / launchd などからの起動時のフォールバック。
    """
    paths = (
        Path(__file__).resolve().parent.parent / ".env",
        Path.home() / ".config" / "ai" / "keys.env",
    )
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main():
    _load_dotenv()
    parser = argparse.ArgumentParser(description="AI協議会ビットコイン自動売買ボット")
    parser.add_argument("--once", action="store_true",
                        help="1サイクルだけ実行して終了する")
    parser.add_argument("--collect", action="store_true",
                        help="市況データを履歴DBに蓄積するだけで終了する(LLM・売買なし)")
    args = parser.parse_args()

    if args.collect:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        config = Config()
        store = HistoryStore(config.history_path)
        try:
            snapshot = fetch_market_snapshot(config.product_code, store=store)
            logging.getLogger("aitrader.collect").info(
                "収集完了: 現在値 %.0f JPY / 1分足%d本 / 履歴 %d時間分",
                snapshot.ltp, len(snapshot.candles_1m), snapshot.history_hours)
        finally:
            store.close()
        return

    if args.once:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        config = Config()
        config.validate_for_trading()
        council = Council(config)
        trader = Trader(config)
        store = HistoryStore(config.history_path)
        try:
            run_once(config, council, trader, store=store)
        finally:
            store.close()
    else:
        run_loop()


if __name__ == "__main__":
    main()
