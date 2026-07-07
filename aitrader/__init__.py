# -*- coding: utf-8 -*-
"""AI協議会方式のビットコイン自動売買ボット。

複数の人格(ペルソナ)を持ったAIがそれぞれ相場を分析し、
重み付き投票で売買タイミングを決定する。
"""

from .config import Config
from .council import Council, CouncilDecision
from .history import HistoryStore
from .market import MarketSnapshot, fetch_market_snapshot
from .personas import PERSONAS, Persona
from .trader import Trader

__all__ = [
    "Config",
    "Council",
    "CouncilDecision",
    "HistoryStore",
    "MarketSnapshot",
    "fetch_market_snapshot",
    "PERSONAS",
    "Persona",
    "Trader",
]
