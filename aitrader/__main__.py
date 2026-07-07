# -*- coding: utf-8 -*-
"""CLIエントリポイント。

使い方:
    python -m aitrader            # ループ実行(デフォルト: ドライラン)
    python -m aitrader --once     # 1サイクルだけ実行して終了
"""

import argparse
import logging

from .bot import run_loop, run_once
from .config import Config
from .council import Council
from .history import HistoryStore
from .trader import Trader


def main():
    parser = argparse.ArgumentParser(description="AI協議会ビットコイン自動売買ボット")
    parser.add_argument("--once", action="store_true",
                        help="1サイクルだけ実行して終了する")
    args = parser.parse_args()

    if args.once:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        config = Config()
        config.validate_for_trading()
        council = Council(
            model=config.model,
            min_agree_votes=config.min_agree_votes,
            min_score_ratio=config.min_score_ratio,
        )
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
