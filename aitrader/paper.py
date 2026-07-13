# -*- coding: utf-8 -*-
"""仮想P&L記録(ペーパートレード台帳)。

協議会の結論と各ペルソナの個別判断を「もしその通りに売買していたら」
という仮想ポジションとしてSQLiteに記録する。実注文とは独立しており、
ドライラン期間中でも協議会・各ペルソナの判断の良し悪しを事後評価できる。

約定モデル: BUYはask・SELLはbidで即時全量約定(成行相当のコストを織り込む)。
実売買と同じ注文サイズ・最大ポジション制約を適用し、現物同様ロングのみ。
JPY残高制約は掛けない(「判断に従えたか」ではなく「判断が正しいか」を測るため)。
"""

import logging
import sqlite3

from .config import Config
from .personas import PERSONAS

logger = logging.getLogger(__name__)

COUNCIL_ACTOR = "council"

_ACTOR_NAMES = {COUNCIL_ACTOR: "協議会"}
_ACTOR_NAMES.update({p.key: p.name for p in PERSONAS})


class PaperBook:
    def __init__(self, path: str = "aitrader_history.db",
                 order_size: float = 0.001, max_position: float = 0.01):
        self.conn = sqlite3.connect(path)
        self.order_size = order_size
        self.max_position = max_position
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_ledger (
                ts TEXT NOT NULL,            -- スナップショット時刻(UTC)
                actor TEXT NOT NULL,         -- 'council' または persona.key
                vote TEXT NOT NULL,          -- BUY / SELL / HOLD
                executed INTEGER NOT NULL,   -- 仮想約定したか(制約で見送りは0)
                price REAL NOT NULL,         -- 約定価格(BUY=ask, SELL=bid)
                size REAL NOT NULL,          -- 約定量(未約定は0)
                ltp REAL NOT NULL,           -- 記録時の最終取引価格(評価損益用)
                position REAL NOT NULL,      -- 約定後の仮想ポジション(BTC)
                avg_cost REAL NOT NULL,      -- 約定後の平均取得単価
                realized_pnl REAL NOT NULL,  -- 累計実現損益(JPY)
                PRIMARY KEY (ts, actor)
            )
        """)
        self.conn.commit()

    @classmethod
    def from_config(cls, config: Config) -> "PaperBook":
        return cls(path=config.history_path,
                   order_size=config.order_size_btc,
                   max_position=config.max_position_btc)

    def close(self):
        self.conn.close()

    # --- 記録 ---

    def record_cycle(self, snapshot, council_decision):
        """1サイクル分の判断を協議会+全ペルソナについて記録する。"""
        entries = [(COUNCIL_ACTOR, council_decision.decision)]
        entries += [(r.persona.key, r.vote.decision)
                    for r in council_decision.votes]
        for actor, vote in entries:
            self._apply(actor, vote, snapshot)
        self.conn.commit()

    def _last_state(self, actor: str):
        cur = self.conn.execute("""
            SELECT position, avg_cost, realized_pnl FROM paper_ledger
            WHERE actor = ? ORDER BY ts DESC LIMIT 1
        """, (actor,))
        row = cur.fetchone()
        return row if row else (0.0, 0.0, 0.0)

    def _apply(self, actor: str, vote: str, snapshot):
        position, avg_cost, realized = self._last_state(actor)
        executed, size, price = 0, 0.0, snapshot.ltp

        if vote == "BUY" and position + self.order_size <= self.max_position + 1e-12:
            price = snapshot.best_ask
            size = self.order_size
            avg_cost = (position * avg_cost + size * price) / (position + size)
            position += size
            executed = 1
        elif vote == "SELL" and position >= self.order_size - 1e-12:
            price = snapshot.best_bid
            size = self.order_size
            realized += (price - avg_cost) * size
            position -= size
            if position <= 1e-12:
                position, avg_cost = 0.0, 0.0
            executed = 1

        self.conn.execute("""
            INSERT OR REPLACE INTO paper_ledger
                (ts, actor, vote, executed, price, size, ltp,
                 position, avg_cost, realized_pnl)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (snapshot.timestamp, actor, vote, executed, price, size,
              snapshot.ltp, position, avg_cost, realized))

    # --- 集計 ---

    def report_text(self) -> str:
        cur = self.conn.execute("""
            SELECT MIN(ts), MAX(ts), COUNT(DISTINCT ts) FROM paper_ledger
        """)
        first_ts, last_ts, cycles = cur.fetchone()
        if not cycles:
            return "仮想P&Lの記録はまだありません(次のサイクルから記録されます)。"

        first_ltp = self.conn.execute(
            "SELECT ltp FROM paper_ledger ORDER BY ts ASC LIMIT 1").fetchone()[0]
        last_ltp = self.conn.execute(
            "SELECT ltp FROM paper_ledger ORDER BY ts DESC LIMIT 1").fetchone()[0]

        lines = [
            f"=== 仮想P&L台帳 {first_ts} 〜 {last_ts} ({cycles}サイクル) ===",
            f"BTC現物(参考): {first_ltp:,.0f} → {last_ltp:,.0f} JPY "
            f"({(last_ltp - first_ltp) / first_ltp * 100:+.2f}%)",
        ]
        for actor in [COUNCIL_ACTOR] + [p.key for p in PERSONAS]:
            cur = self.conn.execute("""
                SELECT SUM(executed),
                       SUM(CASE WHEN executed = 1 AND vote = 'BUY' THEN 1 ELSE 0 END),
                       SUM(CASE WHEN executed = 1 AND vote = 'SELL' THEN 1 ELSE 0 END)
                FROM paper_ledger WHERE actor = ?
            """, (actor,))
            trades, buys, sells = (v or 0 for v in cur.fetchone())
            position, avg_cost, realized = self._last_state(actor)
            unrealized = position * (last_ltp - avg_cost)
            lines.append(
                f"[{_ACTOR_NAMES.get(actor, actor)}] 約定 {trades}回 "
                f"(BUY {buys}/SELL {sells})  ポジ {position:.4f} BTC  "
                f"実現 {realized:+,.0f}  評価 {unrealized:+,.0f}  "
                f"合計 {realized + unrealized:+,.0f} JPY"
            )
        return "\n".join(lines)
