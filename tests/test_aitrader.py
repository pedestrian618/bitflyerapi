# -*- coding: utf-8 -*-
"""aitraderのオフラインテスト(APIキー不要)。

実行: python -m pytest tests/ または python tests/test_aitrader.py
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from aitrader.config import Config
from aitrader.council import Council, PersonaVote, VoteRecord
from aitrader.history import HistoryStore
from aitrader.market import Candle, MarketSnapshot, _build_candles_1m, _rsi, _sma
from aitrader.personas import PERSONAS
from aitrader.trader import Trader


def _council():
    # Anthropicクライアントを初期化せずに集約ロジックだけテストする
    c = Council.__new__(Council)
    c.personas = PERSONAS
    c.min_agree_votes = 3
    c.min_score_ratio = 0.55
    return c


def _record(persona_idx, decision, confidence):
    return VoteRecord(
        persona=PERSONAS[persona_idx],
        vote=PersonaVote(decision=decision, confidence=confidence, reasoning="test"),
    )


class TestAggregation(unittest.TestCase):
    def test_strong_buy_consensus(self):
        records = [
            _record(0, "BUY", 0.8),   # 堅田 1.5
            _record(1, "BUY", 0.9),   # 波多野 1.0
            _record(2, "HOLD", 0.5),  # 逆瀬川 1.0
            _record(3, "BUY", 0.7),   # 疾風 0.8
            _record(4, "BUY", 0.6),   # 大局 1.2
        ]
        d = _council()._aggregate(records)
        self.assertEqual(d.decision, "BUY")
        self.assertEqual(d.agree_votes, 4)

    def test_split_votes_result_in_hold(self):
        records = [
            _record(0, "BUY", 0.6),
            _record(1, "SELL", 0.6),
            _record(2, "BUY", 0.5),
            _record(3, "SELL", 0.5),
            _record(4, "HOLD", 0.9),
        ]
        d = _council()._aggregate(records)
        self.assertEqual(d.decision, "HOLD")

    def test_low_confidence_results_in_hold(self):
        records = [_record(i, "BUY", 0.2) for i in range(3)] + [
            _record(3, "HOLD", 0.9),
            _record(4, "HOLD", 0.9),
        ]
        d = _council()._aggregate(records)
        self.assertEqual(d.decision, "HOLD")

    def test_insufficient_agree_votes(self):
        # スコア比は高いが賛成2名のみ → HOLD
        records = [
            _record(0, "SELL", 1.0),  # 1.5
            _record(4, "SELL", 1.0),  # 1.2
            _record(1, "HOLD", 0.1),
            _record(2, "HOLD", 0.1),
            _record(3, "HOLD", 0.1),
        ]
        d = _council()._aggregate(records)
        self.assertEqual(d.decision, "HOLD")


class TestIndicators(unittest.TestCase):
    def test_build_candles_newest_first(self):
        # bitFlyerは新しい順で返す
        executions = [
            {"exec_date": "2026-07-07T10:01:30.0", "price": 105, "size": 0.1},
            {"exec_date": "2026-07-07T10:01:10.0", "price": 103, "size": 0.2},
            {"exec_date": "2026-07-07T10:00:50.0", "price": 102, "size": 0.1},
            {"exec_date": "2026-07-07T10:00:10.0", "price": 100, "size": 0.3},
        ]
        candles = _build_candles_1m(executions)
        self.assertEqual(len(candles), 2)
        self.assertEqual(candles[0].open, 100)
        self.assertEqual(candles[0].close, 102)
        self.assertEqual(candles[1].open, 103)
        self.assertEqual(candles[1].close, 105)
        self.assertAlmostEqual(candles[1].volume, 0.3)

    def test_sma(self):
        self.assertEqual(_sma([1, 2, 3, 4], 2), 3.5)

    def test_rsi_all_gains(self):
        closes = list(range(1, 20))
        self.assertEqual(_rsi(closes), 100.0)

    def test_rsi_insufficient_data(self):
        self.assertEqual(_rsi([1, 2, 3]), 50.0)


class TestHistoryStore(unittest.TestCase):
    def _make_store(self):
        return HistoryStore(":memory:")

    def _candle(self, minute, price, volume):
        return Candle(time=minute + ":00Z", open=price, high=price + 10,
                      low=price - 10, close=price + 5, volume=volume)

    def test_upsert_keeps_more_complete_minute(self):
        store = self._make_store()
        # 最初は欠けた分(出来高小)、次に完全な分(出来高大)
        store.upsert_candles("BTC_JPY", [self._candle("2026-07-07T10:00", 100, 0.1)])
        store.upsert_candles("BTC_JPY", [self._candle("2026-07-07T10:00", 200, 0.5)])
        # 出来高が小さいデータで上書きしようとしても無視される
        store.upsert_candles("BTC_JPY", [self._candle("2026-07-07T10:00", 300, 0.2)])
        hourly = store.hourly_candles("BTC_JPY")
        self.assertEqual(len(hourly), 1)
        self.assertEqual(hourly[0].close, 205)  # price=200 の close
        store.close()

    def test_hourly_aggregation(self):
        store = self._make_store()
        candles = []
        # 10時台に3本、11時台に2本
        for m, price in [("10:00", 100), ("10:30", 110), ("10:59", 105),
                         ("11:00", 120), ("11:01", 125)]:
            candles.append(self._candle(f"2026-07-07T{m}", price, 1.0))
        store.upsert_candles("BTC_JPY", candles)
        hourly = store.hourly_candles("BTC_JPY")
        self.assertEqual(len(hourly), 2)
        h10, h11 = hourly
        self.assertEqual(h10.open, 100)       # 10:00のopen
        self.assertEqual(h10.close, 110)      # 10:59のclose (105+5)
        self.assertEqual(h10.minutes, 3)
        self.assertEqual(h11.high, 135)       # 11:01のhigh (125+10)
        self.assertAlmostEqual(h11.volume, 2.0)
        self.assertEqual(store.coverage_hours("BTC_JPY"), 2)
        store.close()

    def test_products_are_isolated(self):
        store = self._make_store()
        store.upsert_candles("BTC_JPY", [self._candle("2026-07-07T10:00", 100, 1.0)])
        store.upsert_candles("ETH_JPY", [self._candle("2026-07-07T10:00", 50, 1.0)])
        self.assertEqual(len(store.hourly_candles("BTC_JPY")), 1)
        self.assertEqual(store.hourly_candles("BTC_JPY")[0].open, 100)
        self.assertEqual(store.hourly_candles("ETH_JPY")[0].open, 50)
        store.close()


class TestSnapshotPrompt(unittest.TestCase):
    def _snapshot(self, **overrides):
        snap = MarketSnapshot(
            product_code="BTC_JPY", timestamp="2026-07-07T10:00:00+00:00",
            ltp=10000000, best_bid=9999000, best_ask=10001000, spread=2000,
            volume_24h=1234.5,
            candles_1m=[Candle("2026-07-07T10:00:00Z", 1, 2, 0.5, 1.5, 3)],
            sma_short=10000000, sma_long=9900000, rsi_14=55.0,
            change_pct_15m=0.5, change_pct_60m=-1.2,
            board_state="RUNNING", health="NORMAL",
        )
        for k, v in overrides.items():
            setattr(snap, k, v)
        return snap

    def test_to_prompt_text_without_history(self):
        text = self._snapshot().to_prompt_text()
        self.assertIn("BTC_JPY", text)
        self.assertIn("RSI(14, 1分足): 55.0", text)
        self.assertIn("RUNNING", text)
        self.assertIn("まだ十分な履歴がありません", text)

    def test_to_prompt_text_with_short_history_warns(self):
        from aitrader.history import HourCandle
        hourly = [HourCandle(f"2026-07-07T{h:02d}", 100, 110, 90, 105, 5.0, 60)
                  for h in range(10)]
        text = self._snapshot(candles_1h=hourly, history_hours=10,
                              sma_8h=105, sma_24h=100, rsi_14h=60.0,
                              change_pct_24h=1.5).to_prompt_text()
        self.assertIn("約10時間分", text)
        self.assertIn("RSI(14, 1時間足): 60.0", text)
        self.assertIn("中期データは不完全", text)

    def test_to_prompt_text_with_full_history(self):
        from aitrader.history import HourCandle
        hourly = [HourCandle(f"2026-07-0{d}T{h:02d}", 100, 110, 90, 105, 5.0, 60)
                  for d in (6, 7) for h in range(24)]
        text = self._snapshot(candles_1h=hourly, history_hours=48).to_prompt_text()
        self.assertIn("約48時間分", text)
        self.assertNotIn("中期データは不完全", text)


class TestTraderRisk(unittest.TestCase):
    def test_dry_run_never_sends_order(self):
        config = Config()
        config.dry_run = True
        trader = Trader(config)
        result = trader.execute("BUY")
        self.assertFalse(result["executed"])
        self.assertTrue(result["order"]["dry_run"])

    def test_hold_does_nothing(self):
        trader = Trader(Config())
        result = trader.execute("HOLD")
        self.assertFalse(result["executed"])
        self.assertIsNone(result["order"])

    def test_cooldown(self):
        config = Config()
        config.dry_run = True
        trader = Trader(config)
        trader.execute("BUY")
        result = trader.execute("SELL")  # 直後の2回目はクールダウンで弾かれる
        self.assertIn("クールダウン", result["reason"])

    def test_validate_for_trading_requires_keys(self):
        config = Config()
        config.dry_run = False
        config.bitflyer_key = ""
        config.bitflyer_secret = ""
        with self.assertRaises(RuntimeError):
            config.validate_for_trading()


if __name__ == "__main__":
    unittest.main(verbosity=2)
