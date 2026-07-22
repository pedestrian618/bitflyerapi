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
from aitrader.dashboard import generate_html, write_dashboard
from aitrader.history import HistoryStore, HourCandle
from aitrader.llm import LLMError, LLMRouter
from aitrader.market import (Candle, MarketSnapshot, _adx, _atr, _bollinger,
                             _build_candles_1m, _ema, _macd, _rsi, _sma,
                             _taker_flow, _vwap)
from aitrader.views import build_view_text
from aitrader.paper import PaperBook
from aitrader.personas import PERSONAS, PRODUCT_MARKER, product_label
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

    def test_action_weight_only_boosts_buy_sell(self):
        # 堅田: HOLDは重み1.0のまま、BUY/SELLのときだけ1.5に増える
        self.assertAlmostEqual(_record(0, "HOLD", 1.0).score, 1.0)
        self.assertAlmostEqual(_record(0, "BUY", 1.0).score, 1.5)
        self.assertAlmostEqual(_record(0, "SELL", 0.8).score, 1.2)
        # action_weight未設定のペルソナは従来通り
        self.assertAlmostEqual(_record(1, "BUY", 1.0).score, 1.0)
        self.assertAlmostEqual(_record(1, "HOLD", 1.0).score, 1.0)

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


class _FakeProvider:
    """LLMRouterのテスト用ダミープロバイダ。"""
    def __init__(self, name, fail=False, configured=True):
        self.name = name
        self.fail = fail
        self._configured = configured
        self.models = {"heavy": f"{name}-heavy", "light": f"{name}-light"}
        self.calls = 0

    def configured(self):
        return self._configured

    def ask(self, tier, system, user):
        self.calls += 1
        if self.fail:
            raise RuntimeError(f"{self.name} down")
        return PersonaVote(decision="BUY", confidence=0.7,
                           reasoning=f"{self.name}/{tier}")


def _router(**providers) -> LLMRouter:
    r = LLMRouter.__new__(LLMRouter)
    r._providers = providers
    r.cooldown_sec = 600
    r._down_until = {}
    import threading
    r._lock = threading.Lock()
    return r


def _hourly_candles(n=72, start=100.0, step=1.0):
    return [
        HourCandle(time=f"2026-07-{1 + i // 24:02d}T{i % 24:02d}",
                   open=start + step * i - 0.5, high=start + step * i + 1.0,
                   low=start + step * i - 1.0, close=start + step * i,
                   volume=5.0, minutes=60)
        for i in range(n)
    ]


class TestIndicatorsExtra(unittest.TestCase):
    def test_ema_constant_series(self):
        self.assertAlmostEqual(_ema([100.0] * 50, 8), 100.0)

    def test_atr_reflects_range(self):
        candles = _hourly_candles(20)  # 高値-安値=2, ギャップ含めTR=2〜3
        atr = _atr(candles, 14)
        self.assertGreater(atr, 1.9)
        self.assertLess(atr, 3.1)

    def test_adx_trend_vs_flat(self):
        trending = _adx(_hourly_candles(40, step=2.0), 14)
        flat = _adx(_hourly_candles(40, step=0.0), 14)
        self.assertGreater(trending, 25.0)
        self.assertLessEqual(flat, trending)

    def test_macd_positive_in_uptrend(self):
        macd, signal, hist = _macd([float(v) for v in range(100, 160)])
        self.assertGreater(macd, 0.0)

    def test_bollinger_constant_collapses(self):
        mid, upper, lower = _bollinger([100.0] * 30, 20)
        self.assertAlmostEqual(mid, 100.0)
        self.assertAlmostEqual(upper, lower)

    def test_vwap_weighted(self):
        candles = [
            Candle(time="t1", open=0, high=0, low=0, close=100.0, volume=1.0),
            Candle(time="t2", open=0, high=0, low=0, close=200.0, volume=3.0),
        ]
        self.assertAlmostEqual(_vwap(candles), 175.0)

    def test_taker_flow_window(self):
        executions = [  # 新しい順
            {"exec_date": "2026-07-22T10:14:30.123", "side": "BUY", "size": 0.5},
            {"exec_date": "2026-07-22T10:10:00.0", "side": "SELL", "size": 0.2},
            {"exec_date": "2026-07-22T09:50:00.0", "side": "BUY", "size": 9.9},  # 窓外
        ]
        buy, sell = _taker_flow(executions, minutes=15)
        self.assertAlmostEqual(buy, 0.5)
        self.assertAlmostEqual(sell, 0.2)


class TestViews(unittest.TestCase):
    def _snap(self):
        s = _snapshot_for_paper()
        s.candles_1h = _hourly_candles()
        s.history_hours = 72
        s.rsi_14h = 60.0
        s.bid_depth, s.ask_depth = 3.0, 1.0
        s.taker_buy_15m, s.taker_sell_15m = 2.0, 1.0
        s.macro = {"btc_dominance": 55.0, "usdjpy": 155.0}
        return s

    def test_views_are_independent(self):
        s = self._snap()
        trend = build_view_text(s, "trend")
        momentum = build_view_text(s, "momentum")
        flow = build_view_text(s, "flow")
        risk = build_view_text(s, "risk")
        macro = build_view_text(s, "macro")
        # 各ビューは専門指標を含み、他派の看板指標を含まない
        self.assertIn("ADX", trend)
        self.assertNotIn("RSI", trend)
        self.assertIn("MACD", momentum)
        self.assertIn("RSI", momentum)
        self.assertNotIn("ADX", momentum)
        self.assertIn("テイカーフロー", flow)
        self.assertIn("板の厚み", flow)
        self.assertNotIn("SMA(8時間)", flow)
        self.assertIn("ATR", risk)
        self.assertNotIn("RSI", risk)
        self.assertIn("BTCドミナンス", macro)
        self.assertNotIn("テイカー", macro)

    def test_position_context(self):
        s = self._snap()  # ltp=10,000,000
        held = build_view_text(s, "risk", {
            "position": 0.002, "avg_cost": 9800000.0,
            "last_trade": {"ts": "2026-07-21T16:07", "side": "BUY",
                           "price": 9800000.0}})
        self.assertIn("保有: 0.0020", held)
        self.assertIn("+2.04%", held)  # 含み益
        self.assertIn("直近の約定: BUY", held)
        flat = build_view_text(s, "risk", {"position": 0.0, "avg_cost": 0.0,
                                           "last_trade": None})
        self.assertIn("保有なし", flat)

    def test_unknown_view_falls_back_to_full_summary(self):
        s = self._snap()
        self.assertEqual(build_view_text(s, ""), s.to_prompt_text())

    def test_macro_view_reports_fetch_failure(self):
        s = self._snap()
        s.macro = {}
        self.assertIn("取得できませんでした", build_view_text(s, "macro"))

    def test_all_personas_have_views(self):
        from aitrader.views import _VIEWS
        for p in PERSONAS:
            self.assertIn(p.view, _VIEWS, f"{p.name} のビューが未定義")


class TestLLMRouter(unittest.TestCase):
    def test_preferred_provider_used(self):
        r = _router(claude=_FakeProvider("claude"),
                    openai=_FakeProvider("openai"),
                    gemini=_FakeProvider("gemini"))
        vote, served = r.ask("openai", "heavy", "sys", "user")
        self.assertEqual(served, "openai:openai-heavy")
        self.assertEqual(vote.reasoning, "openai/heavy")

    def test_failover_to_next_provider_same_tier(self):
        r = _router(claude=_FakeProvider("claude"),
                    openai=_FakeProvider("openai", fail=True),
                    gemini=_FakeProvider("gemini"))
        vote, served = r.ask("openai", "light", "sys", "user")
        # openai失敗 → PROVIDER_ORDER順でclaudeへ、同じlightティア
        self.assertEqual(served, "claude:claude-light")

    def test_unconfigured_provider_skipped(self):
        r = _router(claude=_FakeProvider("claude", configured=False),
                    openai=_FakeProvider("openai", configured=False),
                    gemini=_FakeProvider("gemini"))
        vote, served = r.ask("claude", "heavy", "sys", "user")
        self.assertEqual(served, "gemini:gemini-heavy")

    def test_all_providers_fail_raises(self):
        r = _router(claude=_FakeProvider("claude", fail=True),
                    openai=_FakeProvider("openai", fail=True),
                    gemini=_FakeProvider("gemini", fail=True))
        with self.assertRaises(LLMError):
            r.ask("claude", "heavy", "sys", "user")

    def test_no_configured_provider_raises(self):
        r = _router(claude=_FakeProvider("claude", configured=False),
                    openai=_FakeProvider("openai", configured=False),
                    gemini=_FakeProvider("gemini", configured=False))
        with self.assertRaises(LLMError):
            r.ask("claude", "heavy", "sys", "user")

    def test_circuit_breaker_avoids_failed_provider(self):
        failing = _FakeProvider("openai", fail=True)
        r = _router(claude=_FakeProvider("claude"),
                    openai=failing,
                    gemini=_FakeProvider("gemini"))
        r.ask("openai", "heavy", "s", "u")   # openai失敗 → ダウン記録
        r.ask("openai", "heavy", "s", "u")   # 回避中なのでopenaiは呼ばれない
        self.assertEqual(failing.calls, 1)

    def test_recovery_after_success(self):
        p = _FakeProvider("openai", fail=True)
        r = _router(claude=_FakeProvider("claude"),
                    openai=p,
                    gemini=_FakeProvider("gemini"))
        r.ask("openai", "heavy", "s", "u")
        p.fail = False
        r._down_until["openai"] = 0.0  # クールダウン明けを再現
        vote, served = r.ask("openai", "heavy", "s", "u")
        self.assertEqual(served, "openai:openai-heavy")


class TestMultiProduct(unittest.TestCase):
    def test_common_rules_use_product_marker(self):
        # 銘柄はハードコードせずマーカーで埋め込まれている
        for p in PERSONAS:
            self.assertIn(PRODUCT_MARKER, p.system_prompt)
            self.assertNotIn("BTC/JPY", p.system_prompt)

    def test_product_label(self):
        self.assertEqual(product_label("BTC_JPY"), "ビットコイン(BTC/JPY)")
        self.assertEqual(product_label("ETH_JPY"), "イーサリアム(ETH/JPY)")
        self.assertEqual(product_label("DOGE_JPY"), "DOGE/JPY")  # 未知はコード

    def test_council_substitutes_product(self):
        c = Council.__new__(Council)
        c.product_label = product_label("ETH_JPY")
        prompt = c._system_prompt(PERSONAS[0])
        self.assertIn("イーサリアム(ETH/JPY)", prompt)
        self.assertNotIn(PRODUCT_MARKER, prompt)

    def test_generic_order_size_env(self):
        os.environ["AITRADER_ORDER_SIZE"] = "0.01"
        os.environ["AITRADER_ORDER_SIZE_BTC"] = "0.005"
        try:
            self.assertAlmostEqual(Config().order_size_btc, 0.01)  # 新名が優先
        finally:
            del os.environ["AITRADER_ORDER_SIZE"]
            self.assertAlmostEqual(Config().order_size_btc, 0.005)  # 旧名フォールバック
            del os.environ["AITRADER_ORDER_SIZE_BTC"]

    def test_base_currency(self):
        config = Config()
        config.product_code = "ETH_JPY"
        self.assertEqual(config.base_currency, "ETH")

    def test_prompt_text_uses_base_currency_and_decimals(self):
        snap = _snapshot_for_paper(ltp=88.123)
        snap.product_code = "XRP_JPY"
        snap.best_bid, snap.best_ask, snap.spread = 88.10, 88.15, 0.05
        text = snap.to_prompt_text()
        self.assertIn("24時間出来高: 1000.00 XRP", text)
        self.assertIn("88.123", text)   # 低単価は小数を残す
        self.assertIn("0.050", text)    # スプレッドが「0」に潰れない


class TestPersonaAssignments(unittest.TestCase):
    def test_all_personas_have_valid_provider_and_tier(self):
        from aitrader.llm import PROVIDER_ORDER
        providers_used = set()
        tiers_used = set()
        for p in PERSONAS:
            self.assertIn(p.provider, PROVIDER_ORDER)
            self.assertIn(p.tier, ("heavy", "light"))
            providers_used.add(p.provider)
            tiers_used.add(p.tier)
        # 3プロバイダ・両ティアが実際に使われている(混合構成)
        self.assertEqual(providers_used, {"claude", "openai", "gemini"})
        self.assertEqual(tiers_used, {"heavy", "light"})

    def test_config_llm_models_shape(self):
        models = Config().llm_models()
        for provider in ("claude", "openai", "gemini"):
            self.assertIn("heavy", models[provider])
            self.assertIn("light", models[provider])


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


def _snapshot_for_paper(ts="2026-07-07T10:00:00+00:00", ltp=10000000.0):
    return MarketSnapshot(
        product_code="BTC_JPY", timestamp=ts,
        ltp=ltp, best_bid=ltp - 1000, best_ask=ltp + 1000, spread=2000,
        volume_24h=1000.0, board_state="RUNNING", health="NORMAL",
        candles_1m=[], sma_short=0, sma_long=0, rsi_14=50.0,
        change_pct_15m=0.0, change_pct_60m=0.0,
    )


def _council_decision(votes):
    """[(persona_idx, decision, confidence), ...] から結論を組み立てる。"""
    records = [_record(i, d, c) for i, d, c in votes]
    return _council()._aggregate(records)


class TestPaperCouncilLog(unittest.TestCase):
    def test_record_cycle_logs_reasoning(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            book = PaperBook(path=os.path.join(tmp, "t.db"))
            decision = _council_decision(
                [(0, "BUY", 0.8), (1, "BUY", 0.9), (2, "BUY", 0.7),
                 (3, "HOLD", 0.5), (4, "BUY", 0.6)])
            book.record_cycle(_snapshot_for_paper(), decision)
            rows = book.conn.execute(
                "SELECT actor, decision, reasoning FROM council_log").fetchall()
            actors = {r[0] for r in rows}
            self.assertIn("council", actors)
            self.assertEqual(len(rows), 1 + len(PERSONAS))
            persona_row = next(r for r in rows if r[0] != "council")
            self.assertEqual(persona_row[2], "test")
            council_row = next(r for r in rows if r[0] == "council")
            self.assertEqual(council_row[1], "BUY")
            self.assertIn("賛成", council_row[2])
            book.close()


class TestCouncilState(unittest.TestCase):
    def test_council_state_after_buy(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            book = PaperBook(path=os.path.join(tmp, "t.db"))
            decision = _council_decision(
                [(0, "BUY", 0.8), (1, "BUY", 0.9), (2, "BUY", 0.7),
                 (3, "HOLD", 0.5), (4, "BUY", 0.6)])
            book.record_cycle(_snapshot_for_paper(), decision)
            state = book.council_state()
            book.close()
            self.assertAlmostEqual(state["position"], 0.001)
            self.assertAlmostEqual(state["avg_cost"], 10001000.0)  # ask約定
            self.assertEqual(state["last_trade"]["side"], "BUY")

    def test_council_state_empty(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            book = PaperBook(path=os.path.join(tmp, "t.db"))
            state = book.council_state()
            book.close()
            self.assertEqual(state["position"], 0.0)
            self.assertIsNone(state["last_trade"])


class TestDashboard(unittest.TestCase):
    def _config(self, tmp):
        config = Config()
        config.history_path = os.path.join(tmp, "history.db")
        config.dashboard_path = os.path.join(tmp, "www", "index.html")
        return config

    def _populate(self, config):
        """1分足・仮想売買・協議会ログを1サイクル分書き込む。"""
        store = HistoryStore(config.history_path)
        candles = [
            Candle(time=f"2026-07-07T{h:02d}:{m:02d}:00Z",
                   open=10000000, high=10000010, low=9999990,
                   close=10000000 + h * 100 + m, volume=1.0)
            for h in range(9, 11) for m in range(0, 60, 5)
        ]
        store.upsert_candles(config.product_code, candles)
        store.close()

        book = PaperBook.from_config(config)
        decision = _council_decision(
            [(0, "BUY", 0.8), (1, "BUY", 0.9), (2, "BUY", 0.7),
             (3, "HOLD", 0.5), (4, "BUY", 0.6)])
        book.record_cycle(_snapshot_for_paper(ts="2026-07-07T10:55:00+00:00"),
                          decision)
        book.close()

    def test_write_dashboard_with_data(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp)
            self._populate(config)
            path = write_dashboard(config)
            self.assertEqual(path, config.dashboard_path)
            html = open(path, encoding="utf-8").read()
            self.assertIn("aitrader ダッシュボード", html)
            self.assertIn("BTC_JPY", html)
            self.assertIn("ドライラン", html)
            self.assertIn("<svg", html)                     # 価格チャート
            self.assertIn(PERSONAS[0].name, html)           # 協議会テーブル
            self.assertIn("test", html)                     # 判断根拠
            self.assertIn("協議会", html)                   # P&L・履歴
            # 秘密情報を含まないこと(万一キーが環境にあっても混入しない)
            self.assertNotIn("API_KEY", html)

    def test_action_cycle_details_shown_for_trades(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp)
            self._populate(config)  # 協議会BUYを1サイクル記録
            html = open(write_dashboard(config), encoding="utf-8").read()
            self.assertIn("売買が動いたサイクルの協議会詳細", html)
            self.assertIn("<details class='cycle'>", html)
            self.assertIn("約定", html)

    def test_action_cycle_details_all_hold(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp)
            book = PaperBook.from_config(config)
            decision = _council_decision(
                [(i, "HOLD", 0.5) for i in range(len(PERSONAS))])
            book.record_cycle(_snapshot_for_paper(), decision)
            html = generate_html(book.conn, config)
            book.close()
            self.assertIn("すべてHOLDでした", html)
            self.assertNotIn("<details class='cycle'>", html)

    def test_dashboard_product_tabs(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp)
            config.product_code = "ETH_JPY"
            config.dashboard_links = "BTC_JPY=../,ETH_JPY=./"
            self._populate(config)
            html = open(write_dashboard(config), encoding="utf-8").read()
            self.assertIn('<nav class="tabs">', html)
            self.assertIn('href="../"', html)
            self.assertIn('class="active" href="./"', html)  # 自銘柄がハイライト
            self.assertIn("0.0000 ETH", html)  # P&L表の単位も基軸通貨

    def test_write_dashboard_empty_db(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp)
            path = write_dashboard(config)  # DBもテーブルも空
            html = open(path, encoding="utf-8").read()
            self.assertIn("まだありません", html)

    def test_reasoning_is_html_escaped(self):
        import sqlite3
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp)
            book = PaperBook.from_config(config)
            records = [_record(0, "BUY", 0.8)]
            records[0].vote.reasoning = "<script>alert(1)</script>"
            decision = _council()._aggregate(records)
            book.record_cycle(_snapshot_for_paper(), decision)
            html = generate_html(book.conn, config)
            book.close()
            self.assertNotIn("<script>alert(1)</script>", html)
            self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
