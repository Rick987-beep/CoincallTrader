#!/usr/bin/env python3
"""
reporting_v2.py — Strategy-agnostic HTML report for backtester V2.

No V1 coupling. Works directly with Dict[Tuple, List[Trade]] from engine.
Auto-discovers parameter names and generates heatmaps for all 2D pairs.

Usage:
    from backtester2.reporting_v2 import generate_html
    html = generate_html(strategy_name, param_grid, results, date_range, ...)
"""
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from itertools import combinations


# ── Per-Combo Stats ──────────────────────────────────────────────

def combo_stats(trades):
    """Compute summary stats from a list of Trade objects."""
    pnls = [t.pnl for t in trades]
    n = len(pnls)
    if n == 0:
        return None
    wins = sum(1 for p in pnls if p > 0)
    triggered = sum(1 for t in trades if t.triggered)
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    stdev = statistics.stdev(pnls) if n >= 2 else 0.0
    return {
        "n": n,
        "total_pnl": sum(pnls),
        "avg_pnl": statistics.mean(pnls),
        "median_pnl": statistics.median(pnls),
        "stdev": stdev,
        "win_rate": wins / n,
        "trigger_rate": triggered / n,
        "max_win": max(pnls),
        "max_loss": min(pnls),
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "sharpe": (statistics.mean(pnls) / stdev) if stdev > 0 else 0.0,
    }


# ── Equity Metrics ───────────────────────────────────────────────

def equity_metrics(trades, capital=10000):
    """Build daily equity curve and compute risk metrics from Trade objects."""
    if not trades:
        return None

    # Group PnL by entry date
    date_pnl = defaultdict(float)
    for t in trades:
        date_pnl[t.entry_date] += t.pnl

    # Fill calendar gaps
    sorted_dates = sorted(date_pnl.keys())
    first = datetime.strptime(sorted_dates[0], "%Y-%m-%d").date()
    last = datetime.strptime(sorted_dates[-1], "%Y-%m-%d").date()
    daily = []
    d = first
    while d <= last:
        ds = d.strftime("%Y-%m-%d")
        daily.append((ds, date_pnl.get(ds, 0.0)))
        d += timedelta(days=1)

    # Cumulative + drawdown
    cum = 0.0
    peak = capital
    max_dd = 0.0
    cumulative = []
    for ds, pnl in daily:
        cum += pnl
        eq = capital + cum
        peak = max(peak, eq)
        dd = peak - eq
        max_dd = max(max_dd, dd)
        cumulative.append((ds, pnl, cum, eq))

    # Profit factor
    gross_win = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    if pf == float("inf"):
        pf = 99.9

    # Sharpe / Sortino (annualised from daily)
    daily_returns = [pnl for _, pnl in daily]
    avg_d = statistics.mean(daily_returns)
    std_d = statistics.stdev(daily_returns) if len(daily_returns) >= 2 else 1.0
    sharpe = (avg_d / std_d * 252**0.5) if std_d > 0 else 0.0
    downside = [r for r in daily_returns if r < 0]
    std_down = statistics.stdev(downside) if len(downside) >= 2 else 1.0
    sortino = (avg_d / std_down * 252**0.5) if std_down > 0 else 0.0

    # Calmar
    calmar = cum / max_dd if max_dd > 0 else 0.0

    # Consecutive wins / losses
    max_cw = max_cl = cw = cl = 0
    for _, pnl in daily:
        if pnl > 0:
            cw += 1
            cl = 0
        elif pnl < 0:
            cl += 1
            cw = 0
        max_cw = max(max_cw, cw)
        max_cl = max(max_cl, cl)

    return {
        "daily": cumulative,  # [(date, pnl, cum_pnl, equity)]
        "total_pnl": cum,
        "max_drawdown": max_dd,
        "max_dd_pct": max_dd / peak * 100 if peak > 0 else 0,
        "profit_factor": pf,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "consec_wins": max_cw,
        "consec_losses": max_cl,
    }


# ── Helpers ──────────────────────────────────────────────────────

def _fmt_val(v):
    """Format a parameter value for display."""
    if isinstance(v, float) and v != int(v):
        return f"{v:.2f}"
    return str(int(v) if isinstance(v, float) else v)


def _fmt_pnl(v):
    """Format PnL with $ and sign."""
    return f"${v:,.0f}"


def _param_label(name):
    """index_trigger → Index Trigger"""
    return name.replace("_", " ").title()


def _pnl_class(v):
    if v > 0:
        return "pos"
    if v < 0:
        return "neg"
    return ""


def _heatmap_color(val, vmin, vmax):
    if vmin == vmax:
        return "#f0f0f0"
    t = (val - vmin) / (vmax - vmin)
    if t < 0.5:
        r, g = 255, int(255 * t * 2)
    else:
        r, g = int(255 * (2 - t * 2)), 255
    return f"rgb({r},{g},80)"


def _sparkline_svg(points, width=300, height=40):
    """Generate an inline SVG sparkline from a list of y-values."""
    if not points or len(points) < 2:
        return ""
    ymin = min(points)
    ymax = max(points)
    if ymax == ymin:
        ymax = ymin + 1
    n = len(points)
    coords = []
    for i, y in enumerate(points):
        x = i / (n - 1) * width
        sy = height - (y - ymin) / (ymax - ymin) * (height - 4) - 2
        coords.append(f"{x:.1f},{sy:.1f}")
    # Zero line
    zero_y = height - (0 - ymin) / (ymax - ymin) * (height - 4) - 2
    return (
        f'<svg width="{width}" height="{height}" style="vertical-align:middle">'
        f'<line x1="0" y1="{zero_y:.1f}" x2="{width}" y2="{zero_y:.1f}" '
        f'stroke="#ccc" stroke-width="1" stroke-dasharray="4,3"/>'
        f'<polyline points="{" ".join(coords)}" '
        f'fill="none" stroke="#1565C0" stroke-width="2"/>'
        f'</svg>'
    )


# ── HTML Report ──────────────────────────────────────────────────

CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
       max-width: 1500px; margin: 20px auto; padding: 0 20px; background: #fafafa; color: #222; }
h1 { border-bottom: 3px solid #333; padding-bottom: 10px; }
h2 { margin-top: 36px; color: #333; border-bottom: 1px solid #ddd; padding-bottom: 6px; }
h3 { margin-top: 20px; color: #555; }
.meta { background: #e8eaf6; padding: 12px 18px; border-radius: 6px; margin: 16px 0;
        display: flex; gap: 28px; flex-wrap: wrap; font-size: 14px; }
.meta b { color: #333; }
.best-box { background: #e8f5e9; border: 2px solid #4caf50; border-radius: 8px;
            padding: 18px 24px; margin: 16px 0; }
.best-box.negative { background: #fff3e0; border-color: #ff9800; }
.best-box h3 { margin: 0 0 10px; color: #2e7d32; }
.best-box.negative h3 { color: #e65100; }
.best-box .params { font-size: 17px; font-weight: 700; color: #00695c; margin: 8px 0; }
.best-box.negative .params { color: #bf360c; }
.metric { display: inline-block; margin: 4px 20px 4px 0; }
.metric-label { color: #666; font-size: 12px; }
.metric-value { font-size: 16px; font-weight: 600; }
.grid-info { background: #f5f5f5; border: 1px solid #ddd; border-radius: 6px;
             padding: 12px 18px; margin: 10px 0; font-size: 13px; }
.grid-info code { background: #e0e0e0; padding: 1px 5px; border-radius: 3px; }
table { border-collapse: collapse; font-size: 13px; margin: 10px 0 24px; }
th, td { padding: 5px 8px; text-align: right; border: 1px solid #ccc; white-space: nowrap; }
th { background: #333; color: #fff; font-weight: 600; position: sticky; top: 0; }
th:first-child { text-align: left; }
td:first-child { text-align: left; }
.pos { color: #2e7d32; font-weight: 600; }
.neg { color: #c62828; }
.empty { color: #bbb; background: #f8f8f8; }
.hm-wrap { overflow-x: auto; margin: 8px 0 24px; }
.hm-label { text-align: left !important; font-weight: 600; background: #f0f0f0 !important;
             color: #333 !important; min-width: 60px; }
.eq-bar { display: inline-block; height: 14px; border-radius: 2px; vertical-align: middle; }
.eq-pos { background: #4caf50; }
.eq-neg { background: #e53935; }
"""


def generate_html(strategy_name, param_grid, results, date_range, n_intervals, runtime_s):
    """Generate a self-contained HTML backtest report.

    Args:
        strategy_name: Strategy.name (e.g. "extrusion_straddle_strangle")
        param_grid: dict of param_name → [values]
        results: Dict[Tuple, List[Trade]] from run_grid_full()
        date_range: (first_date_str, last_date_str)
        n_intervals: number of 5-min market states
        runtime_s: grid execution time in seconds

    Returns:
        Complete self-contained HTML string.
    """
    # ── Compute stats for all combos ─────────────────────────────
    all_stats = {}  # key → stats dict
    for key, trades in results.items():
        s = combo_stats(trades)
        if s:
            all_stats[key] = s

    ranked = sorted(all_stats.items(), key=lambda x: x[1]["total_pnl"], reverse=True)
    total_trades = sum(s["n"] for s in all_stats.values())
    param_names = sorted(param_grid.keys())

    best_key = ranked[0][0] if ranked else None
    best_stats = ranked[0][1] if ranked else None
    best_trades = results.get(best_key, []) if best_key else []
    best_eq = equity_metrics(best_trades) if best_trades else None
    best_params = dict(best_key) if best_key else {}

    title = strategy_name.replace("_", " ").title()
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    is_negative = best_stats and best_stats["total_pnl"] < 0

    # ── Start HTML ───────────────────────────────────────────────
    parts = []
    parts.append(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Backtest: {title}</title>
<style>{CSS}</style>
</head><body>
<h1>Backtest Report &mdash; {title}</h1>
<div class="meta">
  <span><b>Generated:</b> {now}</span>
  <span><b>Data:</b> {date_range[0]} to {date_range[1]}</span>
  <span><b>Intervals:</b> {n_intervals:,}</span>
  <span><b>Combos:</b> {len(all_stats):,}</span>
  <span><b>Trades:</b> {total_trades:,}</span>
  <span><b>Runtime:</b> {runtime_s:.1f}s</span>
</div>""")

    # ── Best Combo Box ───────────────────────────────────────────
    if best_stats:
        neg_cls = " negative" if is_negative else ""
        param_str = " &nbsp;|&nbsp; ".join(
            f"{_param_label(p)}={_fmt_val(best_params[p])}" for p in param_names)
        parts.append(f"""
<h2>Best Combo</h2>
<div class="best-box{neg_cls}">
  <h3>{"Best Result (all combos negative)" if is_negative else "Top Performing Configuration"}</h3>
  <div class="params">{param_str}</div>""")

        pnl_cls = _pnl_class(best_stats["total_pnl"])
        metrics_html = [
            ("Total PnL", f'<span class="{pnl_cls}">{_fmt_pnl(best_stats["total_pnl"])}</span>'),
            ("Trades", str(best_stats["n"])),
            ("Avg PnL", _fmt_pnl(best_stats["avg_pnl"])),
            ("Win Rate", f'{best_stats["win_rate"]*100:.0f}%'),
            ("Trigger Rate", f'{best_stats["trigger_rate"]*100:.0f}%'),
        ]
        if best_eq:
            metrics_html.extend([
                ("Max DD", _fmt_pnl(best_eq["max_drawdown"])),
                ("Sharpe", f'{best_eq["sharpe"]:.2f}'),
                ("Sortino", f'{best_eq["sortino"]:.2f}'),
                ("Profit Factor", f'{best_eq["profit_factor"]:.2f}'),
            ])

        for label, val in metrics_html:
            parts.append(f'  <span class="metric">'
                         f'<span class="metric-label">{label}</span><br>'
                         f'<span class="metric-value">{val}</span></span>')

        # Sparkline
        if best_eq and best_eq["daily"]:
            sparkline_pts = [row[2] for row in best_eq["daily"]]  # cumulative PnL
            parts.append(f'<div style="margin-top:10px">{_sparkline_svg(sparkline_pts)}</div>')

        parts.append("</div>")

    # ── Parameter Grid Info ──────────────────────────────────────
    parts.append('<h2>Parameter Grid</h2><div class="grid-info">')
    for p in param_names:
        vals = param_grid[p]
        parts.append(f'<b>{_param_label(p)}:</b> <code>{vals}</code> '
                     f'({len(vals)} values)<br>')
    n_combos = 1
    for v in param_grid.values():
        n_combos *= len(v)
    parts.append(f'<b>Total combos:</b> {n_combos:,}</div>')

    # ── Top 20 Combos Table ──────────────────────────────────────
    top_n = min(20, len(ranked))
    parts.append(f'<h2>Top {top_n} Combos</h2>')
    parts.append('<div class="hm-wrap"><table>')

    # Header
    hdr = "<tr><th>#</th>"
    for p in param_names:
        hdr += f"<th>{_param_label(p)}</th>"
    hdr += ("<th>Trades</th><th>Total PnL</th><th>Avg PnL</th><th>Med PnL</th>"
            "<th>Win%</th><th>Trig%</th><th>Max Win</th><th>Max Loss</th>"
            "<th>Sharpe</th><th>PF</th></tr>")
    parts.append(hdr)

    for rank, (key, s) in enumerate(ranked[:top_n], 1):
        params = dict(key)
        pnl_cls = _pnl_class(s["total_pnl"])
        avg_cls = _pnl_class(s["avg_pnl"])
        pf_str = f'{s["profit_factor"]:.2f}' if s["profit_factor"] < 100 else "99+"
        row = f'<tr><td>{rank}</td>'
        for p in param_names:
            row += f'<td>{_fmt_val(params[p])}</td>'
        row += (
            f'<td>{s["n"]}</td>'
            f'<td class="{pnl_cls}">{_fmt_pnl(s["total_pnl"])}</td>'
            f'<td class="{avg_cls}">{_fmt_pnl(s["avg_pnl"])}</td>'
            f'<td>{_fmt_pnl(s["median_pnl"])}</td>'
            f'<td>{s["win_rate"]*100:.0f}%</td>'
            f'<td>{s["trigger_rate"]*100:.0f}%</td>'
            f'<td class="pos">{_fmt_pnl(s["max_win"])}</td>'
            f'<td class="neg">{_fmt_pnl(s["max_loss"])}</td>'
            f'<td>{s["sharpe"]:.2f}</td>'
            f'<td>{pf_str}</td></tr>'
        )
        parts.append(row)
    parts.append("</table></div>")

    # ── Heatmaps ─────────────────────────────────────────────────
    if len(param_names) >= 2:
        parts.append("<h2>Parameter Sensitivity</h2>")
        parts.append("<p>Avg PnL per trade, averaged over all other parameters.</p>")

        for pa, pb in combinations(param_names, 2):
            a_vals = sorted(set(dict(k)[pa] for k in all_stats))
            b_vals = sorted(set(dict(k)[pb] for k in all_stats))

            # Aggregate: for each (a, b), collect avg_pnl across all other params
            cells = defaultdict(list)
            for key, s in all_stats.items():
                params = dict(key)
                cells[(params[pa], params[pb])].append(s["avg_pnl"])

            grid = {}
            all_vals = []
            for (a, b), pnls in cells.items():
                v = statistics.mean(pnls)
                grid[(a, b)] = v
                all_vals.append(v)

            if not all_vals:
                continue

            vmin = min(all_vals)
            vmax = max(all_vals)

            parts.append(f'<h3>{_param_label(pa)} &times; {_param_label(pb)}</h3>')
            parts.append('<div class="hm-wrap"><table>')
            # Column headers
            parts.append(f'<tr><th class="hm-label">{_param_label(pa)} \\ {_param_label(pb)}</th>')
            for b in b_vals:
                parts.append(f'<th>{_fmt_val(b)}</th>')
            parts.append('</tr>')

            for a in a_vals:
                parts.append(f'<tr><td class="hm-label">{_fmt_val(a)}</td>')
                for b in b_vals:
                    v = grid.get((a, b))
                    if v is not None:
                        bg = _heatmap_color(v, vmin, vmax)
                        parts.append(
                            f'<td style="background:{bg}">{_fmt_pnl(v)}</td>')
                    else:
                        parts.append('<td class="empty">&mdash;</td>')
                parts.append('</tr>')
            parts.append('</table></div>')

    # ── Daily Equity — Best Combo ────────────────────────────────
    if best_eq and best_eq["daily"]:
        parts.append("<h2>Daily Equity &mdash; Best Combo</h2>")
        parts.append('<div class="hm-wrap"><table>')
        parts.append('<tr><th style="text-align:left">Date</th>'
                     '<th>Day PnL</th><th>Cumulative</th><th>Equity</th>'
                     '<th style="min-width:120px">Visual</th></tr>')

        max_abs = max(abs(row[1]) for row in best_eq["daily"]) or 1
        for ds, pnl, cum, eq in best_eq["daily"]:
            pnl_cls = _pnl_class(pnl)
            cum_cls = _pnl_class(cum)
            bar_w = min(abs(pnl) / max_abs * 100, 100)
            bar_cls = "eq-pos" if pnl >= 0 else "eq-neg"
            sign = "+" if pnl > 0 else ""
            parts.append(
                f'<tr><td style="text-align:left">{ds}</td>'
                f'<td class="{pnl_cls}">{sign}{_fmt_pnl(pnl)}</td>'
                f'<td class="{cum_cls}">{_fmt_pnl(cum)}</td>'
                f'<td>{_fmt_pnl(eq)}</td>'
                f'<td><span class="eq-bar {bar_cls}" '
                f'style="width:{bar_w:.0f}%"></span></td></tr>'
            )
        parts.append("</table></div>")

        # Summary metrics
        eq = best_eq
        parts.append(f"""
<div class="grid-info">
  <b>Max Drawdown:</b> {_fmt_pnl(eq["max_drawdown"])} ({eq["max_dd_pct"]:.1f}%) &nbsp;|&nbsp;
  <b>Sharpe:</b> {eq["sharpe"]:.2f} &nbsp;|&nbsp;
  <b>Sortino:</b> {eq["sortino"]:.2f} &nbsp;|&nbsp;
  <b>Calmar:</b> {eq["calmar"]:.2f} &nbsp;|&nbsp;
  <b>Profit Factor:</b> {eq["profit_factor"]:.2f} &nbsp;|&nbsp;
  <b>Consec Wins:</b> {eq["consec_wins"]} &nbsp;|&nbsp;
  <b>Consec Losses:</b> {eq["consec_losses"]}
</div>""")

    # ── Trade Log — Best Combo ───────────────────────────────────
    if best_trades:
        parts.append("<h2>Trade Log &mdash; Best Combo</h2>")
        parts.append(f'<p>{len(best_trades)} trades total</p>')
        parts.append('<div class="hm-wrap"><table>')
        parts.append(
            '<tr><th style="text-align:left">Date</th>'
            '<th>Entry Time</th><th>Exit Time</th>'
            '<th>Entry Spot</th><th>Exit Spot</th>'
            '<th>Entry USD</th><th>Exit USD</th>'
            '<th>Fees</th><th>PnL</th><th>Reason</th></tr>')

        for t in best_trades:
            pnl_cls = _pnl_class(t.pnl)
            parts.append(
                f'<tr><td style="text-align:left">{t.entry_date}</td>'
                f'<td>{t.entry_time.strftime("%H:%M")}</td>'
                f'<td>{t.exit_time.strftime("%H:%M")}</td>'
                f'<td>${t.entry_spot:,.0f}</td>'
                f'<td>${t.exit_spot:,.0f}</td>'
                f'<td>${t.entry_price_usd:,.2f}</td>'
                f'<td>${t.exit_price_usd:,.2f}</td>'
                f'<td>${t.fees:,.2f}</td>'
                f'<td class="{pnl_cls}">${t.pnl:,.2f}</td>'
                f'<td>{t.exit_reason}</td></tr>'
            )
        parts.append("</table></div>")

    # ── Footer ───────────────────────────────────────────────────
    parts.append(f"""
<div style="margin-top:40px; padding-top:12px; border-top:1px solid #ddd;
            color:#999; font-size:12px;">
  Backtester V2 &mdash; Real Deribit prices via Tardis &mdash;
  Generated {now} &mdash; {runtime_s:.1f}s grid + report
</div>
</body></html>""")

    return "\n".join(parts)
