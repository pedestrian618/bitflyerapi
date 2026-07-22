# -*- coding: utf-8 -*-
"""メインループ: 相場取得 → AI協議会 → 執行 を一定間隔で繰り返す。"""

import logging
import time

from . import guard
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

    position = None
    if paper is not None:
        try:
            position = paper.council_state()
        except Exception:
            logger.exception("ポジション取得に失敗(ポジション情報なしで協議します)")

    decision = council.convene(snapshot, position=position)
    print(decision.summary())

    if paper is not None:
        paper.record_cycle(snapshot, decision)

    result = trader.execute(decision.decision)
    logger.info("執行結果: %s", result["reason"])

    update_dashboard(config)
    return {"snapshot": snapshot, "decision": decision, "result": result}


def run_collect(config: Config):
    """毎時の収集+ガード。LLMは急変時の臨時協議会でのみ呼ばれる。

    - 1分足を蓄積し、ダッシュボードを更新する(従来の --collect)
    - ガード判定(guard.py): ルール損切り / 急変時の臨時協議会
    """
    store = HistoryStore(config.history_path)
    paper = PaperBook.from_config(config)
    try:
        snapshot = fetch_market_snapshot(config.product_code, store=store,
                                         include_macro=False)
        logger.info("収集完了: 現在値 %.0f JPY / 1分足%d本 / 履歴 %d時間分",
                    snapshot.ltp, len(snapshot.candles_1m),
                    snapshot.history_hours)

        try:
            action, reason = guard.evaluate(
                config, snapshot, paper.council_state(), paper.conn)
        except Exception:
            logger.exception("ガード判定に失敗(収集処理は継続します)")
            action, reason = guard.ACTION_NONE, ""

        if action == guard.ACTION_STOP_LOSS:
            logger.warning("ガード発動: %s", reason)
            size = paper.record_guard_exit(snapshot, reason)
            result = Trader(config).close_position(size)
            logger.warning("損切り執行: %s", result["reason"])
        elif action == guard.ACTION_EMERGENCY:
            logger.warning("ガード発動: %s → 臨時協議会を開催します", reason)
            run_once(config, Council(config), Trader(config),
                     store=store, paper=paper)
            return  # run_once がダッシュボードまで更新済み
        elif reason:
            logger.info("ガード: %s", reason)
    finally:
        store.close()
        paper.close()

    update_dashboard(config)


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
    logger.info("AI協議会トレーダー起動 [%s] 銘柄=%s 間隔=%d秒 注文サイズ=%.4f %s 履歴DB=%s",
                mode, config.product_code, config.interval_sec,
                config.order_size_btc, config.base_currency, config.history_path)

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
