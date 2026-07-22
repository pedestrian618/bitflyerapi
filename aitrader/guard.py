# -*- coding: utf-8 -*-
"""毎時ガード: 協議会の谷間を埋めるルールベースの安全弁。

協議会が3時間ごとの構成でも、毎時の --collect がこのガードを実行する
(LLMは呼ばない=無料)。判定は次の優先順:

  1. 市場異常(板ヘルスがNORMAL以外 / 板がRUNNING以外) → 何もしない
     (不安定な板に成行を投げるとスリッページ事故になるため)
  2. ハードストップ: 含み損が閾値(デフォルト-2%)以下
     → 協議会を通さず全量成行SELL。損切りを合議に委ねると
     「もう少し様子見」バイアスで切れないため、機械的に執行する
  3. 急変検知: |60分騰落率| が閾値(デフォルト3%)以上
     → 臨時協議会の開催を要求(方向判断はLLMの仕事)。
     連発防止のクールダウン付き(既定3時間、DBに永続化)

evaluate() は判定だけを行い、執行は呼び出し側(bot.run_collect)が担う。
"""

import logging
import sqlite3
import time

logger = logging.getLogger(__name__)

ACTION_NONE = ""
ACTION_STOP_LOSS = "stop_loss"
ACTION_EMERGENCY = "emergency"

_STATE_KEY_EMERGENCY = "last_emergency_at"


def _ensure_state_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS guard_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)


def _get_state(conn, key: str) -> str:
    _ensure_state_table(conn)
    row = conn.execute(
        "SELECT value FROM guard_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else ""


def _set_state(conn, key: str, value: str):
    _ensure_state_table(conn)
    conn.execute("""
        INSERT INTO guard_state (key, value) VALUES (?, ?)
        ON CONFLICT (key) DO UPDATE SET value = excluded.value
    """, (key, value))
    conn.commit()


def evaluate(config, snapshot, position_state: dict,
             conn: sqlite3.Connection, now: float = None) -> tuple:
    """(action, reason) を返す。

    ACTION_EMERGENCY を返すときはクールダウンをここで消費する
    (呼び出し側が必ず臨時協議会を開催する前提)。
    """
    now = now if now is not None else time.time()

    # 1. 市場異常時は何もしない(損切りより優先)
    if snapshot.board_state != "RUNNING" or snapshot.health != "NORMAL":
        return ACTION_NONE, (f"市場異常のため様子見"
                             f"(板状態 {snapshot.board_state} / ヘルス {snapshot.health})")

    # 2. ハードストップ(損切り)
    position = (position_state or {}).get("position", 0.0)
    avg_cost = (position_state or {}).get("avg_cost", 0.0)
    if position > 0 and avg_cost > 0:
        pnl_pct = (snapshot.ltp - avg_cost) / avg_cost * 100.0
        if pnl_pct <= -config.stop_loss_pct:
            return ACTION_STOP_LOSS, (
                f"ルール損切り: 含み損 {pnl_pct:+.2f}% ≤ "
                f"-{config.stop_loss_pct:g}% (平均取得 {avg_cost:,.0f})")

    # 3. 急変検知 → 臨時協議会(クールダウン付き)
    if abs(snapshot.change_pct_60m) >= config.emergency_move_pct:
        raw = _get_state(conn, _STATE_KEY_EMERGENCY)
        if not raw or now - float(raw) >= config.emergency_cooldown_sec:
            _set_state(conn, _STATE_KEY_EMERGENCY, str(now))
            return ACTION_EMERGENCY, (
                f"急変検知: 60分騰落 {snapshot.change_pct_60m:+.2f}% "
                f"(閾値 ±{config.emergency_move_pct:g}%)")
        return ACTION_NONE, (
            f"急変検知({snapshot.change_pct_60m:+.2f}%)だが臨時協議会は"
            f"クールダウン中")

    return ACTION_NONE, ""
