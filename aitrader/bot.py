# -*- coding: utf-8 -*-
"""メインループ: 相場取得 → AI協議会 → 執行 を一定間隔で繰り返す。"""

import logging
import time

from .config import Config
from .council import Council
from .dashboard import write_dashboard
from .history import HistoryStore
from .market import fetch_market_snapshot
from .paper import PaperBook
from .trader import Trader

logger = logging.getLogger(__name__)


def update_dashboard(config: Config):
    """ダッシュボードHTMLを再生成する(失敗しても売買処理には影響させない)。"""
    if not config.dashboard_path:
        return
    try:
        path = write_dashboard(config)
        logger.info("ダッシュボード更新: %s", path)
    except Exception:
        logger.exception("ダッシュボード生成に失敗(処理は継続します)")


def run_once(config: Config, council: Council, trader: Trader,
             store: HistoryStore = None, paper: PaperBook = None) -> dict:
    """1サイクル実行して結果を返す。"""
    snapshot = fetch_market_snapshot(config.product_code, store=store)
    logger.info("現在値: %.0f JPY (RSI=%.1f, 15分騰落 %+.2f%%, 履歴 %d時間分)",
                snapshot.ltp, snapshot.rsi_14, snapshot.change_pct_15m,
                snapshot.history_hours)

    decision = council.convene(snapshot)
    print(decision.summary())

    if paper is not None:
        paper.record_cycle(snapshot, decision)

    result = trader.execute(decision.decision)
    logger.info("執行結果: %s", result["reason"])

    update_dashboard(config)
    return {"snapshot": snapshot, "decision": decision, "result": result}


def run_loop(config: Config = None):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = config or Config()
    config.validate_for_trading()

    council = Council(config)
    trader = Trader(config)
    store = HistoryStore(config.history_path)
    paper = PaperBook.from_config(config)

    mode = "ドライラン(実注文なし)" if config.dry_run else "実売買"
    logger.info("AI協議会トレーダー起動 [%s] 銘柄=%s 間隔=%d秒 注文サイズ=%.4f BTC 履歴DB=%s",
                mode, config.product_code, config.interval_sec,
                config.order_size_btc, config.history_path)

    try:
        while True:
            try:
                run_once(config, council, trader, store=store, paper=paper)
            except KeyboardInterrupt:
                logger.info("停止します")
                break
            except Exception:
                logger.exception("サイクル実行中にエラー。次の周期で再試行します。")
            time.sleep(config.interval_sec)
    finally:
        store.close()
        paper.close()
