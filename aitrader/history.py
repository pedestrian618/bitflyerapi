# -*- coding: utf-8 -*-
"""1分足のローカル蓄積(SQLite)と1時間足への集約。

bitFlyer公開APIはローソク足を提供しないため、サイクルごとに取得した
1分足を蓄積し、数日運用することで自前の中期データ(1時間足)を育てる。
"""

import sqlite3
from dataclasses import dataclass


@dataclass
class HourCandle:
    time: str   # "YYYY-MM-DDTHH" (UTC)
    open: float
    high: float
    low: float
    close: float
    volume: float
    minutes: int  # そのhourに含まれる1分足の本数(データ充足度)


class HistoryStore:
    def __init__(self, path: str = "aitrader_history.db"):
        self.conn = sqlite3.connect(path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS candles_1m (
                product_code TEXT NOT NULL,
                minute TEXT NOT NULL,      -- "YYYY-MM-DDTHH:MM" (UTC)
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                PRIMARY KEY (product_code, minute)
            )
        """)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def upsert_candles(self, product_code: str, candles: list):
        """1分足を蓄積する。

        同じ分を再取得した場合は出来高が大きい方(=約定の取りこぼしが
        少ない方)を採用する。500約定の窓で端の分が欠けていても、
        次のサイクルの完全なデータで上書きされる。
        """
        rows = [
            (product_code, c.time[:16], c.open, c.high, c.low, c.close, c.volume)
            for c in candles
        ]
        self.conn.executemany("""
            INSERT INTO candles_1m (product_code, minute, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (product_code, minute) DO UPDATE SET
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume = excluded.volume
            WHERE excluded.volume >= candles_1m.volume
        """, rows)
        self.conn.commit()

    def hourly_candles(self, product_code: str, hours: int = 72) -> list:
        """蓄積済み1分足から直近N時間分の1時間足を組み立てる(古い順)。"""
        cur = self.conn.execute("""
            SELECT minute, open, high, low, close, volume
            FROM candles_1m
            WHERE product_code = ?
            ORDER BY minute DESC
            LIMIT ?
        """, (product_code, hours * 60))
        rows = cur.fetchall()

        buckets = {}
        for minute, o, h, l, c, v in rows:
            hour = minute[:13]  # "YYYY-MM-DDTHH"
            b = buckets.get(hour)
            if b is None:
                # DESC走査なので最初に見た分がそのhourの「最後(close)」
                buckets[hour] = {"open": o, "high": h, "low": l,
                                 "close": c, "volume": v, "minutes": 1}
            else:
                b["open"] = o  # 走査が進むほど古い分 → openを上書き
                b["high"] = max(b["high"], h)
                b["low"] = min(b["low"], l)
                b["volume"] += v
                b["minutes"] += 1

        candles = [HourCandle(time=hour, **vals)
                   for hour, vals in sorted(buckets.items())]
        return candles[-hours:]

    def coverage_hours(self, product_code: str) -> int:
        """蓄積されているデータのおおよその時間数(hour数)。"""
        cur = self.conn.execute("""
            SELECT COUNT(DISTINCT substr(minute, 1, 13))
            FROM candles_1m WHERE product_code = ?
        """, (product_code,))
        return int(cur.fetchone()[0])
