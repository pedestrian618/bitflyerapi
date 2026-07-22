# -*- coding: utf-8 -*-
"""静的HTMLダッシュボード生成。

サイクルごとに履歴DB(aitrader_history.db)の内容から自己完結のHTMLを
1枚生成して AITRADER_DASHBOARD_PATH に書き出す。XServer の public_html
配下を指定すれば、SSHせずにブラウザから稼働状況・協議会の判断・仮想P&Lを
確認できる。外部アセットなし(CSS/SVGはすべてインライン)。
認証は同ディレクトリに置く .htaccess のBasic認証を想定
(deploy/htaccess.example 参照。認証なしで公開する場合は置かなくてよい)。
"""

import bisect
import html
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Config
from .paper import COUNCIL_ACTOR, PaperBook, ensure_log_columns
from .personas import PERSONAS

JST = timezone(timedelta(hours=9), name="JST")

# 表示上限
HISTORY_CYCLES = 24     # 判断履歴テーブルの行数
CHART_HOURS = 48        # 価格チャートの期間
CHART_MAX_POINTS = 480  # チャートのダウンサンプリング上限
LONG_CHART_DAYS = 14    # 長期チャート(1時間足)の期間

_VOTE_CLASS = {"BUY": "buy", "SELL": "sell", "HOLD": "hold"}

_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0f172a; color: #e2e8f0; font-family: -apple-system, "Hiragino Sans", "Noto Sans JP", sans-serif; padding: 16px; max-width: 1080px; margin: 0 auto; }
h1 { font-size: 1.2rem; margin-bottom: 4px; }
h2 { font-size: 1rem; color: #94a3b8; margin: 24px 0 8px; }
.meta { color: #64748b; font-size: 0.8rem; margin-bottom: 12px; }
.tabs { display: flex; gap: 6px; margin: 10px 0; flex-wrap: wrap; }
.tabs a { padding: 4px 14px; border-radius: 999px; background: #1e293b; color: #94a3b8; text-decoration: none; font-size: 0.8rem; font-weight: 600; }
.tabs a.active { background: #0ea5e9; color: #0f172a; }
.badge { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 0.75rem; font-weight: 600; margin-right: 6px; }
.badge.dry { background: #1e3a8a; color: #93c5fd; }
.badge.live { background: #7f1d1d; color: #fca5a5; }
.warn { background: #422006; border: 1px solid #a16207; color: #fbbf24; padding: 10px 14px; border-radius: 8px; margin: 12px 0; font-size: 0.85rem; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin: 12px 0; }
.card { background: #1e293b; border-radius: 10px; padding: 12px 14px; }
.card .label { color: #94a3b8; font-size: 0.7rem; }
.card .value { font-size: 1.15rem; font-weight: 700; margin-top: 2px; }
.card .sub { color: #64748b; font-size: 0.7rem; margin-top: 2px; }
table { width: 100%; border-collapse: collapse; font-size: 0.8rem; background: #1e293b; border-radius: 10px; overflow: hidden; }
th, td { padding: 7px 9px; text-align: left; border-bottom: 1px solid #0f172a; white-space: nowrap; }
th { color: #94a3b8; font-weight: 600; font-size: 0.7rem; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
tr:last-child td { border-bottom: none; }
.scroll { overflow-x: auto; }
.vote { display: inline-block; min-width: 3.2em; text-align: center; padding: 1px 6px; border-radius: 4px; font-weight: 700; font-size: 0.72rem; }
.vote.buy { background: #14532d; color: #4ade80; }
.vote.sell { background: #7f1d1d; color: #f87171; }
.vote.hold { background: #334155; color: #94a3b8; }
.pos { color: #4ade80; } .neg { color: #f87171; }
.reason { white-space: normal; color: #cbd5e1; min-width: 220px; }
.served { color: #64748b; font-size: 0.7rem; }
details.cycle { background: #1e293b; border-radius: 10px; margin: 8px 0; }
details.cycle summary { cursor: pointer; padding: 10px 14px; font-size: 0.85rem; }
details.cycle[open] summary { border-bottom: 1px solid #0f172a; }
details.cycle .inner { padding: 10px 12px 12px; }
details.cycle .inner .meta { margin-bottom: 8px; }
svg { width: 100%; height: auto; display: block; background: #1e293b; border-radius: 10px; }
footer { color: #475569; font-size: 0.7rem; margin: 24px 0 8px; }
"""


def _jst(ts: str, fmt: str = "%m/%d %H:%M") -> str:
    """UTCのISO文字列("...+00:00" またはnaive)をJST表記にする。"""
    try:
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return str(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(JST).strftime(fmt)


def _esc(text) -> str:
    return html.escape(str(text), quote=True)


def _fmt_price(v: float) -> str:
    """価格表示。低単価銘柄(XRP等)は小数第3位まで残す。"""
    return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:,.3f}"


def _nav_tabs(config: Config) -> str:
    """AITRADER_DASHBOARD_LINKS から銘柄タブを組み立てる(未設定なら空)。"""
    items = []
    for part in (config.dashboard_links or "").split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        label, _, url = part.partition("=")
        label, url = label.strip(), url.strip()
        cls = ' class="active"' if label == config.product_code else ""
        items.append(f'<a{cls} href="{_esc(url)}">{_esc(label)}</a>')
    return f'<nav class="tabs">{"".join(items)}</nav>' if items else ""


def _vote_chip(vote: str) -> str:
    cls = _VOTE_CLASS.get(vote, "hold")
    return f'<span class="vote {cls}">{_esc(vote)}</span>'


def _signed(value: float, unit: str = "") -> str:
    cls = "pos" if value >= 0 else "neg"
    return f'<span class="{cls}">{value:+,.0f}{unit}</span>'


def _short_name(name: str) -> str:
    """「慎重派リスク管理者・堅田」→「堅田」"""
    return name.rsplit("・", 1)[-1]


def _query(conn, sql, params=()):
    """テーブル未作成(蓄積開始前)の場合は空リストを返す。"""
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


# --- 各セクション ---

def _minute_closes(conn, product_code: str) -> list:
    """チャート用の (minute, close)。古い順、直近CHART_HOURS時間・上限件数まで。"""
    rows = _query(conn, """
        SELECT minute, close FROM candles_1m
        WHERE product_code = ? ORDER BY minute DESC LIMIT ?
    """, (product_code, CHART_HOURS * 60))
    rows.reverse()
    if len(rows) > CHART_MAX_POINTS:
        step = -(-len(rows) // CHART_MAX_POINTS)  # ceil
        sampled = rows[::step]
        if sampled[-1] != rows[-1]:
            sampled.append(rows[-1])
        rows = sampled
    return rows


def _council_moods(conn) -> list:
    """協議サイクルごとの空気感 [(分キー, ネットスコア -1〜+1), ...](古い順)。

    ネットスコア = (BUY合計スコア - SELL合計スコア) / 総スコア。
    +1に近いほど買い一色、-1に近いほど売り一色、0付近はHOLD優勢。
    """
    rows = _query(conn, """
        SELECT ts, decision, score FROM council_log
        WHERE actor != ? AND score > 0
    """, (COUNCIL_ACTOR,))
    by_ts = {}
    for ts, decision, score in rows:
        d = by_ts.setdefault(str(ts)[:16], {"buy": 0.0, "sell": 0.0, "total": 0.0})
        d["total"] += score
        if decision == "BUY":
            d["buy"] += score
        elif decision == "SELL":
            d["sell"] += score
    return sorted(
        (minute, (d["buy"] - d["sell"]) / d["total"])
        for minute, d in by_ts.items() if d["total"] > 0)


def _rolling_sma(values: list, window: int) -> list:
    out, acc = [], 0.0
    for i, v in enumerate(values):
        acc += v
        if i >= window:
            acc -= values[i - window]
        out.append(acc / min(i + 1, window))
    return out


def _hourly_closes(conn, product_code: str, days: int) -> list:
    """長期チャート用の (時キー+':00', 終値)。古い順、直近N日分。"""
    rows = _query(conn, """
        SELECT minute, close FROM candles_1m
        WHERE product_code = ? ORDER BY minute DESC LIMIT ?
    """, (product_code, days * 24 * 60))
    rows.reverse()
    buckets = {}
    for minute, close in rows:  # 昇順走査なので各hourの最後の分のcloseが残る
        buckets[minute[:13] + ":00"] = close
    return sorted(buckets.items())


def _price_chart(conn, product_code: str) -> str:
    rows = _minute_closes(conn, product_code)
    return _render_chart(conn, product_code, rows,
                         trend_window_ratio=6, trend_label="8時間SMA")


def _long_price_chart(conn, product_code: str) -> str:
    """直近LONG_CHART_DAYS日の1時間足チャート。48時間分以下なら省略。"""
    rows = _hourly_closes(conn, product_code, LONG_CHART_DAYS)
    if len(rows) <= CHART_HOURS:
        return ("<p class='meta'>長期チャートは48時間を超える蓄積ができてから"
                "表示されます。</p>")
    # 24時間SMA基準でトレンドを塗り分け(1時間足なので窓=24点)
    return _render_chart(conn, product_code, rows,
                         trend_window=24, trend_label="24時間SMA")


def _render_chart(conn, product_code: str, rows: list,
                  trend_window: int = None, trend_window_ratio: int = 6,
                  trend_label: str = "8時間SMA") -> str:
    if len(rows) < 2:
        return "<p class='meta'>チャート表示に必要な価格データがまだありません。</p>"

    width, height = 960, 260
    pad_l, pad_r, pad_t, pad_b = 70, 16, 16, 28
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b

    closes = [r[1] for r in rows]
    lo, hi = min(closes), max(closes)
    span = (hi - lo) or 1.0
    lo -= span * 0.03
    hi += span * 0.03
    span = hi - lo

    def x(i):
        return pad_l + plot_w * i / (len(rows) - 1)

    def y(price):
        return pad_t + plot_h * (1 - (price - lo) / span)

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" '
             f'aria-label="{_esc(product_code)} 価格チャート">']

    minutes = [minute for minute, _ in rows]

    # 背景の縦帯 = 協議会の空気感(緑=買い優勢 / 赤=売り優勢、濃さ=偏り)
    moods = [(m, net) for m, net in _council_moods(conn) if m <= minutes[-1]]
    for idx, (minute, net) in enumerate(moods):
        end_minute = moods[idx + 1][0] if idx + 1 < len(moods) else None
        if end_minute is not None and end_minute < minutes[0]:
            continue  # 帯全体がチャート左端より前
        i0 = bisect.bisect_left(minutes, minute)
        i1 = (bisect.bisect_left(minutes, end_minute)
              if end_minute is not None else len(minutes) - 1)
        i0, i1 = min(i0, len(minutes) - 1), min(i1, len(minutes) - 1)
        if i1 <= i0 or abs(net) < 0.02:
            continue
        color = "#22c55e" if net > 0 else "#ef4444"
        opacity = min(0.04 + 0.12 * abs(net), 0.16)
        parts.append(
            f'<rect class="mood" x="{x(i0):.1f}" y="{pad_t}" '
            f'width="{x(i1) - x(i0):.1f}" height="{plot_h}" '
            f'fill="{color}" opacity="{opacity:.3f}"/>')

    # 水平グリッドと価格ラベル
    for i in range(5):
        gy = pad_t + plot_h * i / 4
        price = hi - span * i / 4
        parts.append(f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" '
                     f'y2="{gy:.1f}" stroke="#334155" stroke-width="1"/>')
        parts.append(f'<text x="{pad_l - 6}" y="{gy + 4:.1f}" fill="#94a3b8" '
                     f'font-size="11" text-anchor="end">{_fmt_price(price)}</text>')

    # 価格線 = トレンドで塗り分け(SMAより上=緑 / 下=赤)。
    # 同色の連続区間ごとにpolylineを分け、境界点は両方に含めて線を繋ぐ
    window = trend_window or max(2, len(closes) // trend_window_ratio)
    sma = _rolling_sma(closes, window)
    up = [c >= s for c, s in zip(closes, sma)]
    seg_start = 0
    for i in range(1, len(closes) + 1):
        if i == len(closes) or up[i] != up[seg_start]:
            seg = range(max(seg_start - 1, 0), i)  # 前区間と1点重ねて繋ぐ
            points = " ".join(f"{x(j):.1f},{y(closes[j]):.1f}" for j in seg)
            color = "#34d399" if up[seg_start] else "#f87171"
            parts.append(f'<polyline points="{points}" fill="none" '
                         f'stroke="{color}" stroke-width="1.6"/>')
            seg_start = i

    # 協議会の仮想約定マーカー(BUY ▲ / SELL ▼)
    # ダウンサンプリングで分が間引かれているため最近傍の点に置く
    trades = _query(conn, """
        SELECT ts, vote, price FROM paper_ledger
        WHERE actor = ? AND executed = 1 ORDER BY ts
    """, (COUNCIL_ACTOR,))
    for ts, vote, price in trades:
        minute = str(ts)[:16]
        if minute < minutes[0] or minute > minutes[-1]:
            continue
        i = min(bisect.bisect_left(minutes, minute), len(minutes) - 1)
        cx, cy = x(i), y(price)
        if vote == "BUY":
            parts.append(f'<path d="M{cx:.1f} {cy + 12:.1f} l-6 10 h12 z" '
                         f'fill="#4ade80"/>')
        else:
            parts.append(f'<path d="M{cx:.1f} {cy - 12:.1f} l-6 -10 h12 z" '
                         f'fill="#f87171"/>')

    # 時刻ラベル(両端、JST)
    for i, anchor in ((0, "start"), (len(rows) - 1, "end")):
        label = _jst(rows[i][0])
        parts.append(f'<text x="{x(i):.1f}" y="{height - 8}" fill="#94a3b8" '
                     f'font-size="11" text-anchor="{anchor}">{label} JST</text>')

    parts.append("</svg>")
    parts.append(f"<p class='meta'>線色: <span class='pos'>緑=上昇トレンド</span>"
                 f"(価格≥{_esc(trend_label)}) / "
                 "<span class='neg'>赤=下落トレンド</span> ／ "
                 "背景帯: 協議会の空気感(緑=買い優勢 / 赤=売り優勢、濃さ=偏り) ／ "
                 "▲=仮想BUY / ▼=仮想SELL</p>")
    return "\n".join(parts)


def _council_cycle(conn, ts) -> tuple:
    """council_logの1サイクル分を (協議会行, ペルソナ行リスト) で返す。"""
    rows = _query(conn, """
        SELECT actor, decision, confidence, weight, score, served_by, reasoning,
               cost_usd, expected_pct
        FROM council_log WHERE ts = ?
    """, (ts,))
    council = next((r for r in rows if r[0] == COUNCIL_ACTOR), None)
    personas = sorted((r for r in rows if r[0] != COUNCIL_ACTOR),
                      key=lambda r: r[4], reverse=True)
    return council, personas


def _persona_table(personas, usdjpy_rate: float = 155.0) -> str:
    names = {p.key: p.name for p in PERSONAS}
    parts = ["<div class='scroll'><table><tr>"
             "<th>ペルソナ</th><th>判断</th><th>期待値</th><th>確信度</th>"
             "<th>スコア</th><th>応答モデル</th><th>コスト</th><th>判断根拠</th></tr>"]
    for (actor, decision, conf, weight, score, served_by, reasoning,
         cost, expected) in personas:
        cost_cell = f"¥{cost * usdjpy_rate:,.1f}" if cost else "—"
        expected_cell = f"{expected:+.2f}%" if expected is not None else "—"
        reason_html = _esc(reasoning).replace("\n", "<br>")
        parts.append(
            f"<tr><td>{_esc(names.get(actor, actor))}</td>"
            f"<td>{_vote_chip(decision)}</td>"
            f"<td class='num'>{expected_cell}</td>"
            f"<td class='num'>{conf:.2f} × {weight:g}</td>"
            f"<td class='num'>{score:.2f}</td>"
            f"<td class='served'>{_esc(served_by)}</td>"
            f"<td class='num'>{cost_cell}</td>"
            f"<td class='reason'>{reason_html}</td></tr>")
    parts.append("</table></div>")
    return "\n".join(parts)


def _latest_council(conn, usdjpy_rate: float = 155.0) -> str:
    latest = _query(conn, "SELECT MAX(ts) FROM council_log")
    ts = latest[0][0] if latest else None
    if not ts:
        return "<p class='meta'>協議会の記録はまだありません(次のサイクルから記録されます)。</p>"

    council, personas = _council_cycle(conn, ts)
    parts = []
    if council:
        parts.append(f"<p style='margin-bottom:8px'>結論: {_vote_chip(council[1])} "
                     f"<span class='meta'>{_esc(council[6])}</span></p>")
    parts.append(_persona_table(personas, usdjpy_rate))
    return "\n".join(parts)


def _action_cycle_details(conn, usdjpy_rate: float = 155.0) -> str:
    """直近HISTORY_CYCLESサイクルのうち、協議会がBUY/SELLに動いたサイクルの
    協議会詳細(全ペルソナの判断根拠つき)を折りたたみで表示する。"""
    recent = _query(conn, """
        SELECT DISTINCT ts FROM paper_ledger ORDER BY ts DESC LIMIT ?
    """, (HISTORY_CYCLES,))
    if not recent:
        return "<p class='meta'>判断履歴はまだありません。</p>"

    placeholders = ",".join("?" * len(recent))
    actions = _query(conn, f"""
        SELECT ts, vote, executed, price, ltp FROM paper_ledger
        WHERE actor = ? AND vote IN ('BUY', 'SELL') AND ts IN ({placeholders})
        ORDER BY ts DESC
    """, (COUNCIL_ACTOR, *[r[0] for r in recent]))
    if not actions:
        return (f"<p class='meta'>直近{HISTORY_CYCLES}サイクルの協議会の結論は"
                "すべてHOLDでした(売買なし)。</p>")

    parts = []
    for ts, vote, executed, price, ltp in actions:
        status = (f"約定 {_fmt_price(price)} JPY" if executed
                  else f"見送り(ポジション制約) ／ 当時値 {_fmt_price(ltp)} JPY")
        council, personas = _council_cycle(conn, ts)
        summary = (f"{_esc(_jst(ts))} JST {_vote_chip(vote)} "
                   f"<span class='meta'>{status}</span>")
        inner = []
        if council:
            inner.append(f"<p class='meta'>{_esc(council[6])}</p>")
        inner.append(_persona_table(personas, usdjpy_rate)
                     if personas else
                     "<p class='meta'>このサイクルの協議会ログはありません。</p>")
        parts.append(f"<details class='cycle'><summary>{summary}</summary>"
                     f"<div class='inner'>{''.join(inner)}</div></details>")
    return "\n".join(parts)


def _vote_history(conn) -> str:
    rows = _query(conn, """
        SELECT ts, actor, vote, ltp FROM paper_ledger
        WHERE ts IN (SELECT DISTINCT ts FROM paper_ledger ORDER BY ts DESC LIMIT ?)
        ORDER BY ts DESC
    """, (HISTORY_CYCLES,))
    if not rows:
        return "<p class='meta'>判断履歴はまだありません。</p>"

    cycles = {}  # ts → {actor: vote, "ltp": ...}(挿入順=新しい順)
    for ts, actor, vote, ltp in rows:
        cycles.setdefault(ts, {"ltp": ltp})[actor] = vote

    keys = [COUNCIL_ACTOR] + [p.key for p in PERSONAS]
    headers = ["時刻(JST)", "価格"] + ["協議会"] + \
        [_short_name(p.name) for p in PERSONAS]
    parts = ["<div class='scroll'><table><tr>" +
             "".join(f"<th>{_esc(h)}</th>" for h in headers) + "</tr>"]
    for ts, votes in cycles.items():
        cells = [f"<td>{_esc(_jst(ts))}</td>",
                 f"<td class='num'>{_fmt_price(votes['ltp'])}</td>"]
        cells += [f"<td>{_vote_chip(votes[k]) if k in votes else '<span class=meta>—</span>'}</td>"
                  for k in keys]
        parts.append("<tr>" + "".join(cells) + "</tr>")
    parts.append("</table></div>")
    return "\n".join(parts)


def _pnl_table(summary: dict, base_currency: str = "BTC") -> str:
    if not summary["cycles"]:
        return "<p class='meta'>仮想P&Lの記録はまだありません。</p>"
    parts = ["<div class='scroll'><table><tr>"
             "<th>アクター</th><th>約定</th><th>ポジション</th><th>平均取得単価</th>"
             "<th>実現損益</th><th>評価損益</th><th>合計</th></tr>"]
    for a in summary["actors"]:
        avg = _fmt_price(a["avg_cost"]) if a["position"] > 0 else "—"
        parts.append(
            f"<tr><td>{_esc(a['name'])}</td>"
            f"<td class='num'>{a['trades']}回 (B{a['buys']}/S{a['sells']})</td>"
            f"<td class='num'>{a['position']:.4f} {_esc(base_currency)}</td>"
            f"<td class='num'>{avg}</td>"
            f"<td class='num'>{_signed(a['realized'])}</td>"
            f"<td class='num'>{_signed(a['unrealized'])}</td>"
            f"<td class='num'>{_signed(a['total'], ' JPY')}</td></tr>")
    parts.append("</table></div>")
    return "\n".join(parts)


def _llm_cost_card(conn, config: Config, now: datetime):
    """(ラベル, 値, サブ文言) を返す。コスト記録がなければ None。"""
    rows = _query(conn, """
        SELECT COALESCE(SUM(cost_usd), 0),
               COUNT(DISTINCT CASE WHEN cost_usd IS NOT NULL THEN ts END)
        FROM council_log
    """)
    if not rows or not rows[0][1]:
        return None
    total_usd, cycles = rows[0]
    cutoff = (now - timedelta(hours=24)).isoformat(timespec="seconds")
    recent = _query(conn, """
        SELECT COALESCE(SUM(cost_usd), 0) FROM council_log WHERE ts >= ?
    """, (cutoff,))
    recent_usd = recent[0][0] if recent else 0.0
    rate = config.usdjpy_rate
    sub = (f"直近24時間 ¥{recent_usd * rate:,.0f} ／ "
           f"約¥{total_usd / cycles * rate:,.1f}/サイクル")
    return ("LLMコスト(累計・概算)", f"¥{total_usd * rate:,.0f}", sub)


def _summary_cards(conn, summary: dict, config: Config,
                   now: datetime = None) -> str:
    now = now or datetime.now(timezone.utc)
    cards = []

    last_ltp = summary.get("last_ltp")
    if last_ltp:
        cards.append(("現在値(最終サイクル)", f"{_fmt_price(last_ltp)} JPY", ""))

    # 24時間騰落(蓄積した1分足から)
    closes = _query(conn, """
        SELECT close FROM candles_1m WHERE product_code = ?
        ORDER BY minute DESC LIMIT 1440
    """, (config.product_code,))
    if len(closes) >= 2:
        now_c, base_c = closes[0][0], closes[-1][0]
        pct = (now_c - base_c) / base_c * 100 if base_c else 0.0
        hours = min(len(closes) / 60, 24)
        cards.append((f"騰落率(直近{hours:.0f}時間)", f"{pct:+.2f}%", ""))

    council = next((a for a in summary.get("actors", [])
                    if a["actor"] == COUNCIL_ACTOR), None)
    if council:
        cards.append(("協議会 仮想損益(累計)",
                      f"{council['total']:+,.0f} JPY",
                      f"実現 {council['realized']:+,.0f} / 評価 {council['unrealized']:+,.0f}"))

    coverage = _query(conn, """
        SELECT COUNT(DISTINCT substr(minute, 1, 13)) FROM candles_1m
        WHERE product_code = ?
    """, (config.product_code,))
    hours = coverage[0][0] if coverage else 0
    cards.append(("履歴蓄積", f"約{hours}時間分",
                  f"判断 {summary['cycles']}サイクル" if summary["cycles"] else "サイクル記録なし"))

    cost_card = _llm_cost_card(conn, config, now)
    if cost_card:
        cards.append(cost_card)

    return "<div class='cards'>" + "".join(
        f"<div class='card'><div class='label'>{_esc(label)}</div>"
        f"<div class='value'>{value}</div>"
        + (f"<div class='sub'>{sub}</div>" if sub else "")
        + "</div>"
        for label, value, sub in cards) + "</div>"


def _staleness_warning(summary: dict, config: Config, now: datetime) -> str:
    last_ts = summary.get("last_ts")
    if not last_ts:
        return ""
    try:
        last = datetime.fromisoformat(last_ts)
    except ValueError:
        return ""
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    age = (now - last).total_seconds()
    if age > config.interval_sec * 2 + 600:
        return (f"<div class='warn'>⚠ 最終サイクルから {age / 3600:.1f} 時間更新が"
                f"ありません。cron やボットが停止していないか確認してください。</div>")
    return ""


def _deploy_version() -> str:
    path = Path(__file__).resolve().parent.parent / ".deploy_version"
    try:
        return path.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError):
        return ""


def generate_html(conn, config: Config, now: datetime = None) -> str:
    """履歴DBの接続からダッシュボードHTML全体を組み立てる。"""
    now = now or datetime.now(timezone.utc)
    try:
        ensure_log_columns(conn)  # 古いDBでも後付け列を参照できるようにする
    except sqlite3.OperationalError:
        pass  # 読み取り専用接続など。該当欄は「—」表示になるだけ
    book = PaperBook.__new__(PaperBook)  # 既存接続を共有(closeしない)
    book.conn = conn
    try:
        summary = book.summary()
    except sqlite3.OperationalError:  # 蓄積開始前でテーブル未作成
        summary = {"cycles": 0, "actors": []}

    mode_badge = ('<span class="badge dry">ドライラン(実注文なし)</span>'
                  if config.dry_run else
                  '<span class="badge live">実売買モード</span>')
    last_cycle = (f"最終サイクル: {_jst(summary['last_ts'], '%Y/%m/%d %H:%M')} JST"
                  if summary["cycles"] else "サイクル記録なし")
    version = _deploy_version()

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="300">
<meta name="robots" content="noindex, nofollow">
<title>aitrader ダッシュボード — {_esc(config.product_code)}</title>
<style>{_CSS}</style>
</head>
<body>
<h1>aitrader — AI協議会トレーダー</h1>
<p class="meta">{mode_badge} 銘柄: {_esc(config.product_code)} ／ {last_cycle} ／
ページ生成: {_jst(now.isoformat(), '%Y/%m/%d %H:%M')} JST(5分ごとに自動再読込)</p>
{_nav_tabs(config)}
{_staleness_warning(summary, config, now)}
{_summary_cards(conn, summary, config, now)}
<h2>価格チャート(直近{CHART_HOURS}時間)</h2>
{_price_chart(conn, config.product_code)}
<h2>長期チャート(直近{LONG_CHART_DAYS}日・1時間足)</h2>
{_long_price_chart(conn, config.product_code)}
<h2>最新の協議会</h2>
{_latest_council(conn, config.usdjpy_rate)}
<h2>判断履歴(直近{HISTORY_CYCLES}サイクル)</h2>
{_vote_history(conn)}
<h2>売買が動いたサイクルの協議会詳細(直近{HISTORY_CYCLES}サイクル)</h2>
{_action_cycle_details(conn, config.usdjpy_rate)}
<h2>仮想P&L(ペーパートレード)</h2>
{_pnl_table(summary, config.base_currency)}
<footer>aitrader dashboard{f' ／ deploy: {_esc(version)}' if version else ''}</footer>
</body>
</html>
"""


def write_dashboard(config: Config = None, path: str = None) -> str:
    """ダッシュボードHTMLを生成してアトミックに書き出し、パスを返す。"""
    config = config or Config()
    path = path or config.dashboard_path or "aitrader_dashboard.html"

    conn = sqlite3.connect(config.history_path)
    try:
        html_text = generate_html(conn, config)
    finally:
        conn.close()

    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".dashboard-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(html_text)
        os.chmod(tmp, 0o644)  # mkstempは0600。Webサーバーから読めるようにする
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path
