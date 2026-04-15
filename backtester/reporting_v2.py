#!/usr/bin/env python3
"""
reporting_v2.py — Strategy-agnostic self-contained HTML report generator.

Receives a fully pre-computed GridResult and renders it into a single-file
HTML report with no external dependencies. Does zero analysis or recomputation
— all metrics, equity curves, and fan-chart data are read directly from the
GridResult attributes supplied by results.py.

Report sections:
  • Risk summary bar — key metrics for the best combo at a glance
  • Best-combo box — parameters, all scoring metrics, Sortino, Calmar
  • Fan chart — equity curves for the top-N combos, shaded intraday band
  • Leaderboard — top-N combos ranked by composite score with all metrics
  • Heatmaps — auto-generated for every 2D parameter pair
  • Trade log — every entry/exit for the best combo

Usage:
    from backtester.reporting_v2 import generate_html
    html = generate_html(result, strategy_name=..., n_intervals=..., runtime_s=...)
    with open('report.html', 'w') as f:
        f.write(html)
"""
import math
import pandas as pd
from datetime import datetime
from itertools import combinations

from backtester.config import cfg
from backtester.results import GridResult


# ── Heatmap helpers ──────────────────────────────────────────────

def _build_heatmap_data(df, keys, pa, pb):
    """Pool trades by (pa_val, pb_val) and compute cell metrics.

    Cells aggregate across all other parameters, so trade counts are
    balanced and no single thin combo can distort the picture.

    Returns:
        grid_pnl  — {(a,b): total_pnl}
        grid_wr   — {(a,b): win_rate_pct}
        grid_n    — {(a,b): trade_count}
        a_vals, b_vals — sorted unique axis values
    """
    if df.empty:
        return {}, {}, {}, [], []

    mapping = pd.DataFrame({
        "combo_idx": pd.array(range(len(keys)), dtype=df["combo_idx"].dtype),
        "pa_val":    [dict(k).get(pa) for k in keys],
        "pb_val":    [dict(k).get(pb) for k in keys],
    })
    merged = df.merge(mapping, on="combo_idx")

    grp = merged.groupby(["pa_val", "pb_val"])
    grid_pnl = grp["pnl"].sum().to_dict()
    grid_n   = grp["pnl"].count().to_dict()
    wins     = (merged["pnl"] > 0).groupby([merged["pa_val"], merged["pb_val"]]).sum()
    grid_wr  = (wins / grp["pnl"].count() * 100).to_dict()

    a_vals = sorted(set(k[0] for k in grid_pnl))
    b_vals = sorted(set(k[1] for k in grid_pnl))
    return grid_pnl, grid_wr, grid_n, a_vals, b_vals



def _select_pairs(result, heatmap_pairs_override=None):
    """Return (pa, pb) pairs to render in heatmaps.

    Priority:
      1. Caller-supplied override (e.g. strategy HEATMAP_PAIRS)
      2. result.heatmap_pairs — pre-ranked by PnL spread in results.py
    """
    if heatmap_pairs_override:
        all_pairs = list(combinations(sorted(result.param_names), 2))
        valid = [tuple(p) for p in heatmap_pairs_override
                 if tuple(sorted(p)) in [tuple(sorted(x)) for x in all_pairs]]
        if valid:
            return valid
    return list(result.heatmap_pairs)


# ── Formatting helpers ───────────────────────────────────────────

def _fmt_val(v):
    if isinstance(v, float) and v != int(v):
        return f"{v:.2f}"
    return str(int(v) if isinstance(v, float) else v)


def _fmt_pnl(v):
    return f"${v:,.0f}"


def _param_label(name):
    return name.replace("_", " ").title()


def _pnl_class(v):
    if v > 0: return "pos"
    if v < 0: return "neg"
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
    if not points or len(points) < 2:
        return ""
    ymin, ymax = min(points), max(points)
    if ymax == ymin:
        ymax = ymin + 1
    n = len(points)
    coords = [
        f"{i / (n-1) * width:.1f},"
        f"{height - (y - ymin) / (ymax - ymin) * (height-4) - 2:.1f}"
        for i, y in enumerate(points)
    ]
    zero_y = height - (0 - ymin) / (ymax - ymin) * (height-4) - 2
    return (
        f'<svg width="{width}" height="{height}" style="vertical-align:middle">'
        f'<line x1="0" y1="{zero_y:.1f}" x2="{width}" y2="{zero_y:.1f}" '
        f'stroke="#ccc" stroke-width="1" stroke-dasharray="4,3"/>'
        f'<polyline points="{" ".join(coords)}" '
        f'fill="none" stroke="#1565C0" stroke-width="2"/>'
        f'</svg>'
    )


def _equity_chart_svg(daily_rows, capital=10000, width=860, height=260):
    """Full equity curve SVG with labelled dollar Y-axis and day-number X-axis.

    daily_rows: list of (date_str, day_pnl, cum_pnl, high, low, close)
    Returns a self-contained <svg> string.
    """
    if not daily_rows or len(daily_rows) < 2:
        return ""

    ml, mr, mt, mb = 80, 20, 18, 36   # margins: left, right, top, bottom
    pw = width - ml - mr
    ph = height - mt - mb

    eq_vals = [row[5] for row in daily_rows]  # close (NAV at end of day)
    hi_vals  = [row[3] for row in daily_rows]  # intraday high
    lo_vals  = [row[4] for row in daily_rows]  # intraday low
    # Prepend Day 0 = initial capital so x-axis starts at 0
    plot_vals = [capital] + eq_vals
    plot_hi   = [capital] + hi_vals
    plot_lo   = [capital] + lo_vals
    n_pts = len(plot_vals)
    # Include full intraday range and capital baseline in y-axis bounds
    y_min = min(min(plot_lo), capital)
    y_max = max(max(plot_hi), capital)
    y_range = max(y_max - y_min, 1.0)
    y_lo = y_min - y_range * 0.05
    y_hi = y_max + y_range * 0.05

    # Nice round Y-axis ticks
    def _nice_step(span, n_ticks=6):
        raw = span / n_ticks
        mag = 10 ** math.floor(math.log10(max(raw, 1e-9)))
        for f in (1, 2, 2.5, 5, 10):
            if raw <= f * mag:
                return f * mag
        return 10 * mag

    step = _nice_step(y_hi - y_lo)
    first_tick = math.ceil(y_lo / step) * step
    y_ticks = []
    t = first_tick
    while t <= y_hi + step * 0.01:
        y_ticks.append(t)
        t += step

    def sx(i):    # point index (0 = Day 0) → pixel x
        return ml + i / max(n_pts - 1, 1) * pw

    def sy(v):    # equity value → pixel y
        return mt + (1.0 - (v - y_lo) / (y_hi - y_lo)) * ph

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'style="display:block;font-family:-apple-system,BlinkMacSystemFont,sans-serif;'
        f'font-size:11px;color:#333">'
    ]

    # Clip path for plot area (prevents fill/line overflow into margins)
    parts.append(
        f'<defs><clipPath id="plot-clip">'
        f'<rect x="{ml}" y="{mt}" width="{pw}" height="{ph}"/>'
        f'</clipPath></defs>'
    )

    # Plot area background
    parts.append(
        f'<rect x="{ml}" y="{mt}" width="{pw}" height="{ph}" '
        f'fill="#fafafa" stroke="#ddd" stroke-width="1"/>'
    )

    # Y-axis gridlines + labels
    for tick in y_ticks:
        py = sy(tick)
        if mt - 1 <= py <= mt + ph + 1:
            parts.append(
                f'<line x1="{ml}" y1="{py:.1f}" x2="{ml+pw}" y2="{py:.1f}" '
                f'stroke="#e0e0e0" stroke-width="1"/>'
            )
            label = f"${tick:,.0f}"
            parts.append(
                f'<text x="{ml-6}" y="{py+4:.1f}" text-anchor="end" fill="#666">{label}</text>'
            )

    # Capital / zero-gain baseline (dashed)
    py_cap = sy(capital)
    if mt <= py_cap <= mt + ph:
        parts.append(
            f'<line x1="{ml}" y1="{py_cap:.1f}" x2="{ml+pw}" y2="{py_cap:.1f}" '
            f'stroke="#999" stroke-width="1" stroke-dasharray="6,4"/>'
        )
        parts.append(
            f'<text x="{ml+4}" y="{py_cap-4:.1f}" fill="#888" font-size="10">'
            f'start ${capital:,.0f}</text>'
        )

    # X-axis tick labels (day number, spread evenly, ~8 labels max)
    x_step = max(1, round(n_pts / 8))
    for i in range(n_pts):
        if i == 0 or i == n_pts - 1 or i % x_step == 0:
            px = sx(i)
            parts.append(
                f'<line x1="{px:.1f}" y1="{mt+ph}" x2="{px:.1f}" y2="{mt+ph+4}" '
                f'stroke="#aaa" stroke-width="1"/>'
            )
            parts.append(
                f'<text x="{px:.1f}" y="{mt+ph+16}" text-anchor="middle" fill="#666">'
                f'Day {i}</text>'
            )

    # Axis lines
    parts.append(
        f'<line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt+ph}" stroke="#aaa" stroke-width="1"/>'
    )
    parts.append(
        f'<line x1="{ml}" y1="{mt+ph}" x2="{ml+pw}" y2="{mt+ph}" stroke="#aaa" stroke-width="1"/>'
    )

    # Axis titles
    parts.append(
        f'<text transform="rotate(-90)" x="-{mt+ph//2}" y="14" '
        f'text-anchor="middle" fill="#555" font-size="11">Equity (USD)</text>'
    )
    parts.append(
        f'<text x="{ml + pw // 2}" y="{height - 2}" '
        f'text-anchor="middle" fill="#555" font-size="11">Day #</text>'
    )

    # Intraday high/low band (shaded, clipped to plot bounds)
    band_fwd = " ".join(f"{sx(i):.1f},{sy(v):.1f}" for i, v in enumerate(plot_hi))
    band_rev = " ".join(f"{sx(i):.1f},{sy(v):.1f}" for i, v in reversed(list(enumerate(plot_lo))))
    parts.append(
        f'<polygon points="{band_fwd} {band_rev}" fill="#1565C0" fill-opacity="0.08" '
        f'clip-path="url(#plot-clip)"/>'
    )

    # Fill under curve (light blue area, clipped to plot bounds)
    fill_pts = (
        f"{sx(0):.1f},{sy(capital):.1f} "
        + " ".join(f"{sx(i):.1f},{sy(v):.1f}" for i, v in enumerate(plot_vals))
        + f" {sx(n_pts-1):.1f},{sy(capital):.1f}"
    )
    parts.append(
        f'<polygon points="{fill_pts}" fill="#1565C0" fill-opacity="0.07" clip-path="url(#plot-clip)"/>'
    )

    # Equity curve line
    line_pts = " ".join(f"{sx(i):.1f},{sy(v):.1f}" for i, v in enumerate(plot_vals))
    parts.append(
        f'<polyline points="{line_pts}" fill="none" stroke="#1565C0" '
        f'stroke-width="2" stroke-linejoin="round" clip-path="url(#plot-clip)"/>'
    )

    # Final dot
    parts.append(
        f'<circle cx="{sx(n_pts-1):.1f}" cy="{sy(plot_vals[-1]):.1f}" r="3" '
        f'fill="#1565C0"/>'
    )

    parts.append("</svg>")
    return "\n".join(parts)


# ── Performance fan chart helpers ────────────────────────────────

def _lerp_color(c1, c2, t):
    """Linearly interpolate between two '#rrggbb' hex colors."""
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    return (f"#{int(r1+(r2-r1)*t):02x}"
            f"{int(g1+(g2-g1)*t):02x}"
            f"{int(b1+(b2-b1)*t):02x}")


def _rank_style(rank, n_curves):
    """Return (color_hex, opacity, stroke_width) for a rank (1 = best)."""
    if rank == 1:
        return "#1b5e20", 1.0, 2.5
    if rank <= 5:                                   # top-tier greens
        t = (rank - 2) / max(3.0, 1)
        return _lerp_color("#43a047", "#a5d6a7", t), 0.85, 1.5
    if rank <= 12:                                  # mid-tier ambers
        t = (rank - 6) / max(6.0, 1)
        return _lerp_color("#fb8c00", "#ffe082", t), 0.65, 1.0
    t = (rank - 13) / max(float(n_curves - 13), 1.0)   # bottom-tier reds
    return _lerp_color("#e53935", "#ffcdd2", t), 0.45, 0.8


def _fan_chart_svg(curves, capital=10000, width=920, height=340):
    """Performance fan — all top-N equity curves in one SVG.

    Three layers (bottom to top):
      1. Shaded envelope band  — min/max range across all combos
      2. Non-winner curves     — rank 20→2, green/amber/red gradient
      3. Winner curve          — bold dark-green, final PnL label
    """
    if not curves or len(curves[0][2]) < 2:
        return ""

    n_curves = len(curves)
    n_days   = len(curves[0][2])

    ml, mr, mt, mb = 80, 30, 20, 42
    pw = width - ml - mr
    ph = height - mt - mb

    # Axis range — include starting capital in bounds
    all_vals = [v for _, _, eq, _ in curves for v in eq] + [float(capital)]
    y_min, y_max = min(all_vals), max(all_vals)
    y_range = max(y_max - y_min, 1.0)
    y_lo = y_min - y_range * 0.06
    y_hi = y_max + y_range * 0.08

    def _nice_step(span, n_ticks=6):
        raw = span / n_ticks
        mag = 10 ** math.floor(math.log10(max(raw, 1e-9)))
        for f in (1, 2, 2.5, 5, 10):
            if raw <= f * mag:
                return f * mag
        return 10 * mag

    step       = _nice_step(y_hi - y_lo)
    first_tick = math.ceil(y_lo / step) * step
    y_ticks    = []
    t = first_tick
    while t <= y_hi + step * 0.01:
        y_ticks.append(t)
        t += step

    def sx(i):  return ml + i / max(n_days - 1, 1) * pw
    def sy(v):  return mt + (1.0 - (v - y_lo) / (y_hi - y_lo)) * ph

    p = []
    p.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'style="display:block;font-family:-apple-system,BlinkMacSystemFont,sans-serif;'
        f'font-size:11px">'
    )

    # Plot background
    p.append(f'<rect x="{ml}" y="{mt}" width="{pw}" height="{ph}" '
             f'fill="#fafafa" stroke="#ddd" stroke-width="1"/>')

    # Y-axis gridlines + labels
    for tick in y_ticks:
        py = sy(tick)
        if mt - 1 <= py <= mt + ph + 1:
            p.append(f'<line x1="{ml}" y1="{py:.1f}" x2="{ml+pw}" y2="{py:.1f}" '
                     f'stroke="#ececec" stroke-width="1"/>')
            p.append(f'<text x="{ml-6}" y="{py+4:.1f}" text-anchor="end" '
                     f'fill="#777">${tick:,.0f}</text>')

    # Capital baseline (dashed)
    py_cap = sy(capital)
    if mt <= py_cap <= mt + ph:
        p.append(f'<line x1="{ml}" y1="{py_cap:.1f}" x2="{ml+pw}" y2="{py_cap:.1f}" '
                 f'stroke="#bbb" stroke-width="1" stroke-dasharray="5,4"/>')
        p.append(f'<text x="{ml+4}" y="{py_cap-4:.1f}" fill="#aaa" font-size="10">'
                 f'start ${capital:,.0f}</text>')

    # X-axis ticks
    x_step = max(1, round(n_days / 8))
    for i in range(n_days):
        if i == 0 or i == n_days - 1 or i % x_step == 0:
            px = sx(i)
            p.append(f'<line x1="{px:.1f}" y1="{mt+ph}" x2="{px:.1f}" y2="{mt+ph+4}" '
                     f'stroke="#aaa" stroke-width="1"/>')
            p.append(f'<text x="{px:.1f}" y="{mt+ph+16}" text-anchor="middle" fill="#777">'
                     f'Day {i+1}</text>')

    # ── Layer 1: Envelope band ───────────────────────────────────
    env_top     = [max(c[2][i] for c in curves) for i in range(n_days)]
    env_bot     = [min(c[2][i] for c in curves) for i in range(n_days)]
    top_pts     = " ".join(f"{sx(i):.1f},{sy(v):.1f}" for i, v in enumerate(env_top))
    bot_pts_rev = " ".join(f"{sx(i):.1f},{sy(v):.1f}"
                           for i, v in reversed(list(enumerate(env_bot))))
    p.append(f'<polygon points="{top_pts} {bot_pts_rev}" '
             f'fill="#bbdefb" fill-opacity="0.35" stroke="none"/>')
    p.append(f'<polyline points="{top_pts}" fill="none" '
             f'stroke="#90caf9" stroke-width="0.8" stroke-opacity="0.6"/>')
    p.append(f'<polyline points="{" ".join(f"{sx(i):.1f},{sy(v):.1f}" for i, v in enumerate(env_bot))}" '
             f'fill="none" stroke="#90caf9" stroke-width="0.8" stroke-opacity="0.6"/>')

    # ── Layer 2: Non-winner curves (worst → best order so best sits on top) ──
    # Each curve: invisible fat hit-area overlay (12px) for hover, then visible line.
    for rank, total_pnl, eq, tooltip in reversed(curves[1:]):
        color, opacity, sw = _rank_style(rank, n_curves)
        pts = " ".join(f"{sx(i):.1f},{sy(v):.1f}" for i, v in enumerate(eq))
        # Hit area (transparent, wide enough to catch mouse)
        p.append(f'<polyline points="{pts}" fill="none" stroke="#000" '
                 f'stroke-width="12" stroke-opacity="0" stroke-linejoin="round">'
                 f'<title>{tooltip}</title></polyline>')
        # Visible line
        p.append(f'<polyline points="{pts}" fill="none" stroke="{color}" '
                 f'stroke-width="{sw}" stroke-opacity="{opacity}" stroke-linejoin="round"'
                 f' pointer-events="none"/>')

    # ── Layer 3: Winner ──────────────────────────────────────────
    w_rank, w_pnl, w_eq, w_tip = curves[0]
    w_pts = " ".join(f"{sx(i):.1f},{sy(v):.1f}" for i, v in enumerate(w_eq))
    # Hit area
    p.append(f'<polyline points="{w_pts}" fill="none" stroke="#000" '
             f'stroke-width="12" stroke-opacity="0" stroke-linejoin="round">'
             f'<title>{w_tip}</title></polyline>')
    # Visible line
    p.append(f'<polyline points="{w_pts}" fill="none" stroke="#1b5e20" '
             f'stroke-width="2.5" stroke-linejoin="round" pointer-events="none"/>')
    wx, wy = sx(n_days - 1), sy(w_eq[-1])
    p.append(f'<circle cx="{wx:.1f}" cy="{wy:.1f}" r="4" fill="#1b5e20" pointer-events="none"/>')
    # Label centered above the final dot, with white backing rect for legibility
    sign = "+" if w_pnl >= 0 else ""
    lbl_text = f"{sign}{_fmt_pnl(w_pnl)}"
    lbl_w, lbl_h = 64, 16
    p.append(f'<rect x="{wx - lbl_w/2:.1f}" y="{wy - 26:.1f}" width="{lbl_w}" height="{lbl_h}" '
             f'fill="white" fill-opacity="0.85" rx="3" pointer-events="none"/>')
    p.append(f'<text x="{wx:.1f}" y="{wy - 14:.1f}" text-anchor="middle" fill="#1b5e20" '
             f'font-weight="bold" font-size="11" pointer-events="none">{lbl_text}</text>')

    # ── Axis lines + titles ──────────────────────────────────────
    p.append(f'<line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt+ph}" stroke="#aaa" stroke-width="1"/>')
    p.append(f'<line x1="{ml}" y1="{mt+ph}" x2="{ml+pw}" y2="{mt+ph}" stroke="#aaa" stroke-width="1"/>')
    p.append(f'<text transform="rotate(-90)" x="-{mt+ph//2}" y="14" '
             f'text-anchor="middle" fill="#555" font-size="11">Equity (USD)</text>')
    p.append(f'<text x="{ml + pw//2}" y="{height-4}" '
             f'text-anchor="middle" fill="#555" font-size="11">Day #</text>')

    # ── Legend (top-left inside plot area) ───────────────────────
    lx, ly = ml + 10, mt + 14
    legend = [
        ("#1b5e20", 1.0,  2.5, False, "#1 Winner"),
        ("#43a047", 0.85, 1.5, False, "Rank 2\u20135"),
        ("#fb8c00", 0.65, 1.0, False, "Rank 6\u201312"),
        ("#e53935", 0.45, 0.8, False, "Rank 13\u201320"),
        ("#bbdefb", 0.35, 0,   True,  "Min/Max band"),
    ]
    leg_h = len(legend) * 16 + 8
    p.append(f'<rect x="{lx-4}" y="{ly-12}" width="138" height="{leg_h}" '
             f'fill="white" fill-opacity="0.88" rx="3" stroke="#ddd" stroke-width="0.5"/>')
    for j, (color, op, sw, is_fill, lbl) in enumerate(legend):
        yj = ly + j * 16
        if is_fill:
            p.append(f'<rect x="{lx}" y="{yj-6}" width="18" height="9" '
                     f'fill="{color}" fill-opacity="{op}" stroke="#90caf9" stroke-width="0.5"/>')
        else:
            p.append(f'<line x1="{lx}" y1="{yj}" x2="{lx+18}" y2="{yj}" '
                     f'stroke="{color}" stroke-width="{sw}" stroke-opacity="{op}"/>')
            if sw > 2:
                p.append(f'<circle cx="{lx+9}" cy="{yj}" r="2.5" fill="{color}"/>')
        p.append(f'<text x="{lx+24}" y="{yj+4}" fill="#444" font-size="11">{lbl}</text>')

    p.append("</svg>")
    return "\n".join(p)


# ── Robustness SVG helpers ───────────────────────────────────────

def _histogram_svg(pnl_values, highlight_pnl=None, n_bins=20, width=700, height=200):
    """Bar histogram of all combo PnLs with an optional vertical highlight marker.

    pnl_values   — list of floats (one per combo)
    highlight_pnl — if given, draw a vertical marker at this PnL (the live combo)
    """
    if not pnl_values:
        return ""

    pnl_min = min(pnl_values)
    pnl_max = max(pnl_values)
    if pnl_max == pnl_min:
        pnl_max = pnl_min + 1

    bin_w = (pnl_max - pnl_min) / n_bins
    bins = [0] * n_bins
    for v in pnl_values:
        idx = min(int((v - pnl_min) / bin_w), n_bins - 1)
        bins[idx] += 1

    max_count = max(bins) if bins else 1

    ml, mr, mt, mb = 50, 20, 14, 36
    pw = width - ml - mr
    ph = height - mt - mb
    bar_gap = 1
    bar_w = pw / n_bins

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'style="display:block;font-family:-apple-system,BlinkMacSystemFont,sans-serif;font-size:11px">'
    ]

    # Background
    parts.append(f'<rect x="{ml}" y="{mt}" width="{pw}" height="{ph}" fill="#fafafa" stroke="#ddd" stroke-width="1"/>')

    # Bars
    for i, count in enumerate(bins):
        x = ml + i * bar_w
        bar_h = (count / max_count) * ph if max_count else 0
        bin_centre = pnl_min + (i + 0.5) * bin_w
        color = "#4caf50" if bin_centre >= 0 else "#e53935"
        if bar_h > 0:
            parts.append(
                f'<rect x="{x + bar_gap:.1f}" y="{mt + ph - bar_h:.1f}" '
                f'width="{bar_w - bar_gap * 2:.1f}" height="{bar_h:.1f}" '
                f'fill="{color}" fill-opacity="0.75">'
                f'<title>{count} combos  ~${bin_centre:,.0f}</title></rect>'
            )

    # Y-axis count labels (just 0 and max)
    parts.append(f'<text x="{ml - 4}" y="{mt + ph}" text-anchor="end" fill="#888">0</text>')
    parts.append(f'<text x="{ml - 4}" y="{mt + 8}" text-anchor="end" fill="#888">{max_count}</text>')

    # X-axis ticks (min, 0 if in range, max)
    def _sx(v):
        return ml + (v - pnl_min) / (pnl_max - pnl_min) * pw

    for label_v in [pnl_min, pnl_max]:
        px = _sx(label_v)
        parts.append(f'<line x1="{px:.1f}" y1="{mt+ph}" x2="{px:.1f}" y2="{mt+ph+4}" stroke="#aaa" stroke-width="1"/>')
        parts.append(f'<text x="{px:.1f}" y="{mt+ph+16}" text-anchor="middle" fill="#666">${label_v:,.0f}</text>')
    if pnl_min < 0 < pnl_max:
        px0 = _sx(0)
        parts.append(f'<line x1="{px0:.1f}" y1="{mt}" x2="{px0:.1f}" y2="{mt+ph}" stroke="#999" stroke-width="1" stroke-dasharray="4,3"/>')
        parts.append(f'<text x="{px0:.1f}" y="{mt+ph+16}" text-anchor="middle" fill="#666">$0</text>')

    # Highlight marker (live combo)
    if highlight_pnl is not None and pnl_min <= highlight_pnl <= pnl_max:
        hx = _sx(highlight_pnl)
        parts.append(f'<line x1="{hx:.1f}" y1="{mt}" x2="{hx:.1f}" y2="{mt+ph}" stroke="#1565C0" stroke-width="2" stroke-dasharray="5,3"/>')
        parts.append(f'<text x="{hx:.1f}" y="{mt - 2}" text-anchor="middle" fill="#1565C0" font-weight="bold" font-size="10">live: ${highlight_pnl:,.0f}</text>')

    # Axis lines
    parts.append(f'<line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt+ph}" stroke="#aaa" stroke-width="1"/>')
    parts.append(f'<line x1="{ml}" y1="{mt+ph}" x2="{ml+pw}" y2="{mt+ph}" stroke="#aaa" stroke-width="1"/>')
    parts.append(f'<text transform="rotate(-90)" x="-{mt+ph//2}" y="14" text-anchor="middle" fill="#555" font-size="11">Combos</text>')
    parts.append(f'<text x="{ml+pw//2}" y="{height-2}" text-anchor="middle" fill="#555" font-size="11">Total PnL</text>')

    parts.append("</svg>")
    return "\n".join(parts)


def _marginal_bar_chart_svg(sensitivity_rows, param_name, width=340, height=180):
    """Horizontal bar chart for one parameter's marginal PnL.

    sensitivity_rows — list of (value, mean_pnl, p10, p90) for each param value
    Shows mean PnL as a bar, with a p10–p90 error band.
    """
    if not sensitivity_rows:
        return ""

    ml, mr, mt, mb = 80, 30, 14, 20
    pw = width - ml - mr
    ph = height - mt - mb

    all_lo = [r[2] for r in sensitivity_rows]  # p10
    all_hi = [r[3] for r in sensitivity_rows]  # p90
    all_mean = [r[1] for r in sensitivity_rows]

    y_lo = min(min(all_lo), 0) * 1.1 if min(all_lo) < 0 else 0
    y_hi = max(max(all_hi), 0) * 1.1 if max(all_hi) > 0 else 1
    if y_hi == y_lo:
        y_hi = y_lo + 1

    n = len(sensitivity_rows)
    bar_h_px = ph / n * 0.6
    gap_h_px = ph / n

    def _sx(v):  # value → pixel x
        return ml + (v - y_lo) / (y_hi - y_lo) * pw

    def _sy(i):  # row index → pixel y centre
        return mt + (i + 0.5) * gap_h_px

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'style="display:block;font-family:-apple-system,BlinkMacSystemFont,sans-serif;font-size:10px">'
    ]
    parts.append(f'<rect x="{ml}" y="{mt}" width="{pw}" height="{ph}" fill="#fafafa" stroke="#ddd" stroke-width="1"/>')

    # Zero line
    x0 = _sx(0)
    if ml <= x0 <= ml + pw:
        parts.append(f'<line x1="{x0:.1f}" y1="{mt}" x2="{x0:.1f}" y2="{mt+ph}" stroke="#bbb" stroke-width="1" stroke-dasharray="4,3"/>')

    for i, (val, mean_v, p10_v, p90_v) in enumerate(sensitivity_rows):
        cy = _sy(i)
        x_mean = _sx(mean_v)
        x_zero = _sx(0)
        color = "#4caf50" if mean_v >= 0 else "#e53935"

        # Bar from 0 to mean
        bar_x = min(x_zero, x_mean)
        bar_w_px = abs(x_mean - x_zero)
        parts.append(
            f'<rect x="{bar_x:.1f}" y="{cy - bar_h_px/2:.1f}" '
            f'width="{bar_w_px:.1f}" height="{bar_h_px:.1f}" '
            f'fill="{color}" fill-opacity="0.72"/>'
        )

        # p10-p90 error band
        x_p10 = _sx(p10_v)
        x_p90 = _sx(p90_v)
        parts.append(
            f'<line x1="{x_p10:.1f}" y1="{cy:.1f}" x2="{x_p90:.1f}" y2="{cy:.1f}" '
            f'stroke="#555" stroke-width="1.5" opacity="0.5"/>'
        )
        parts.append(f'<line x1="{x_p10:.1f}" y1="{cy-4:.1f}" x2="{x_p10:.1f}" y2="{cy+4:.1f}" stroke="#555" stroke-width="1" opacity="0.5"/>')
        parts.append(f'<line x1="{x_p90:.1f}" y1="{cy-4:.1f}" x2="{x_p90:.1f}" y2="{cy+4:.1f}" stroke="#555" stroke-width="1" opacity="0.5"/>')

        # Y-axis label (param value)
        parts.append(f'<text x="{ml-4}" y="{cy+3:.1f}" text-anchor="end" fill="#444">{_fmt_val(val)}</text>')

        # Mean value label at end of bar
        lbl_x = x_mean + (5 if mean_v >= 0 else -5)
        anchor = "start" if mean_v >= 0 else "end"
        parts.append(f'<text x="{lbl_x:.1f}" y="{cy+3:.1f}" text-anchor="{anchor}" fill="{color}" font-weight="600">${mean_v:,.0f}</text>')

    # Axis
    parts.append(f'<line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt+ph}" stroke="#aaa" stroke-width="1"/>')
    parts.append(f'<line x1="{ml}" y1="{mt+ph}" x2="{ml+pw}" y2="{mt+ph}" stroke="#aaa" stroke-width="1"/>')

    # Title
    label = param_name.replace("_", " ").title()
    parts.append(f'<text x="{ml+pw//2}" y="{mt-2}" text-anchor="middle" fill="#333" font-size="11" font-weight="600">{label}</text>')

    parts.append("</svg>")
    return "\n".join(parts)


def _robustness_section_html(result, highlight_key=None):
    """Render the full Robustness section as an HTML string.

    highlight_key — param tuple for the 'live' combo to mark in charts/table.
                    If None, no highlight is applied.
    """
    parts = []
    parts.append("<h2>Robustness Analysis</h2>")
    parts.append(
        "<p>Distribution of results across <em>all</em> parameter combinations tested. "
        "A flat plateau (low fragility, high % profitable) indicates the edge is real "
        "across the region — not an isolated in-sample spike.</p>"
    )

    # ── Summary card ─────────────────────────────────────────────
    n_combos = len(result.pnl_all)
    pct_pos = result.pct_profitable * 100
    frag = result.fragility_score
    frag_color = "#2e7d32" if frag < 1.5 else ("#fb8c00" if frag < 3.0 else "#c62828")
    pct_color = "#2e7d32" if pct_pos >= 90 else ("#fb8c00" if pct_pos >= 70 else "#c62828")

    # Monotonicity summary: average |ρ| across continuous params
    mono_vals = list(result.monotonicity.values())
    avg_mono = sum(abs(v) for v in mono_vals) / len(mono_vals) if mono_vals else 0.0
    mono_color = "#2e7d32" if avg_mono >= 0.7 else ("#fb8c00" if avg_mono >= 0.4 else "#c62828")
    mono_tip = "smoothly monotone" if avg_mono >= 0.7 else ("mixed" if avg_mono >= 0.4 else "non-monotone / noisy")

    parts.append(f"""<div class="grid-info" style="display:flex;gap:36px;flex-wrap:wrap;align-items:flex-start">
  <div>
    <div class="metric-label">Combos tested</div>
    <div class="metric-value">{n_combos:,}</div>
  </div>
  <div>
    <div class="metric-label">% Profitable</div>
    <div class="metric-value" style="color:{pct_color}">{pct_pos:.0f}%</div>
  </div>
  <div>
    <div class="metric-label">Median PnL</div>
    <div class="metric-value">{_fmt_pnl(result.median_pnl)}</div>
  </div>
  <div>
    <div class="metric-label">P10 PnL</div>
    <div class="metric-value {_pnl_class(result.p10_pnl)}">{_fmt_pnl(result.p10_pnl)}</div>
  </div>
  <div>
    <div class="metric-label">P90 PnL</div>
    <div class="metric-value {_pnl_class(result.p90_pnl)}">{_fmt_pnl(result.p90_pnl)}</div>
  </div>
  <div>
    <div class="metric-label">IQR (P25&ndash;P75)</div>
    <div class="metric-value">{_fmt_pnl(result.pnl_iqr)}</div>
  </div>
  <div title="(max PnL − min PnL) / |median PnL|. Lower = flatter plateau = more robust.">
    <div class="metric-label">Fragility score &#9432;</div>
    <div class="metric-value" style="color:{frag_color}">{frag:.2f}</div>
  </div>
  <div title="Mean |Spearman ρ| across marginal param curves. Higher = smoother hill.">
    <div class="metric-label">Avg monotonicity &#9432;</div>
    <div class="metric-value" style="color:{mono_color}">{avg_mono:.2f} <span style="font-size:12px;color:#888">({mono_tip})</span></div>
  </div>
</div>""")

    # ── PnL distribution histogram ───────────────────────────────
    pnl_values = [pnl for _, pnl in result.pnl_all]
    highlight_pnl = None
    if highlight_key is not None and highlight_key in dict(result.pnl_all):
        highlight_pnl = dict(result.pnl_all)[highlight_key]

    parts.append("<h3>PnL Distribution — All Combos</h3>")
    parts.append(
        "<p style=\"color:#555;font-size:13px;margin:4px 0 8px\">"
        "Green bars = profitable combos. Red = losing. "
        "Blue dashed line = live/target combo (if applicable).</p>"
    )
    parts.append(_histogram_svg(pnl_values, highlight_pnl=highlight_pnl))

    # ── Per-parameter marginal charts ────────────────────────────
    if result.param_sensitivity:
        parts.append("<h3>Per-Parameter Marginal Sensitivity</h3>")
        parts.append(
            "<p style=\"color:#555;font-size:13px;margin:4px 0 12px\">"
            "Average PnL (bar) across all combos sharing each parameter value. "
            "Whiskers show P10&ndash;P90 range. Spearman &rho; measures smoothness of the hill.</p>"
        )
        parts.append('<div style="display:flex;flex-wrap:wrap;gap:24px;margin-bottom:16px">')
        for param, rows in sorted(result.param_sensitivity.items()):
            rho = result.monotonicity.get(param, 0.0)
            rho_color = "#2e7d32" if abs(rho) >= 0.7 else ("#fb8c00" if abs(rho) >= 0.4 else "#c62828")
            parts.append('<div>')
            parts.append(_marginal_bar_chart_svg(rows, param))
            parts.append(
                f'<div style="text-align:center;font-size:11px;color:{rho_color};margin-top:2px">'
                f'Spearman &rho; = {rho:+.2f}</div>'
            )
            parts.append('</div>')
        parts.append('</div>')

    # ── Compact all-combos sortable table ────────────────────────
    parts.append("<h3>All Combos</h3>")
    parts.append(
        "<p style=\"color:#555;font-size:13px;margin:4px 0 8px\">"
        "Click any column header to sort. "
        + (f"Highlighted row = live/target combo." if highlight_key is not None else "")
        + "</p>"
    )

    # Inline JS for sort
    parts.append("""<script>
function sortRobTable(col, th) {
  var tbl = document.getElementById('rob-table');
  var tbody = tbl.querySelector('tbody');
  var rows = Array.from(tbody.querySelectorAll('tr'));
  var asc = th.dataset.asc === '1';
  rows.sort(function(a, b) {
    var av = a.cells[col].dataset.v || a.cells[col].textContent.replace(/[$,%]/g,'').trim();
    var bv = b.cells[col].dataset.v || b.cells[col].textContent.replace(/[$,%]/g,'').trim();
    var an = parseFloat(av), bn = parseFloat(bv);
    if (!isNaN(an) && !isNaN(bn)) return asc ? an - bn : bn - an;
    return asc ? av.localeCompare(bv) : bv.localeCompare(av);
  });
  rows.forEach(function(r){ tbody.appendChild(r); });
  th.dataset.asc = asc ? '0' : '1';
}
</script>""")

    # Build header
    param_names = result.param_names
    all_items = sorted(result.all_stats.items(),
                       key=lambda kv: result.scores.get(kv[0], 0.0), reverse=True)

    hdr = '<thead><tr>'
    hdr += f'<th onclick="sortRobTable(0,this)" data-asc="0" style="cursor:pointer">#</th>'
    col = 1
    for p in param_names:
        hdr += f'<th onclick="sortRobTable({col},this)" data-asc="0" style="cursor:pointer">{_param_label(p)}</th>'
        col += 1
    for lbl in ["PnL", "Win%", "Sharpe", "MaxDD%", "PF", "Score"]:
        hdr += f'<th onclick="sortRobTable({col},this)" data-asc="0" style="cursor:pointer">{lbl}</th>'
        col += 1
    hdr += '</tr></thead>'

    parts.append('<div class="hm-wrap"><table id="rob-table" style="font-size:12px">')
    parts.append(hdr)
    parts.append('<tbody>')
    for rank, (key, s) in enumerate(all_items, 1):
        params = dict(key)
        score = result.scores.get(key, 0.0)
        pnl_cls = _pnl_class(s["total_pnl"])
        pf_str = f'{s["profit_factor"]:.2f}' if s["profit_factor"] < 100 else "99+"
        is_highlight = (key == highlight_key)
        row_style = ' style="background:#e3f2fd;font-weight:600"' if is_highlight else ''
        row = f'<tr{row_style}>'
        row += f'<td data-v="{rank}">{rank}</td>'
        for p in param_names:
            row += f'<td data-v="{params[p]}">{_fmt_val(params[p])}</td>'
        row += (
            f'<td class="{pnl_cls}" data-v="{s["total_pnl"]:.0f}">{_fmt_pnl(s["total_pnl"])}</td>'
            f'<td data-v="{s["win_rate"]*100:.0f}">{s["win_rate"]*100:.0f}%</td>'
            f'<td data-v="{s["sharpe"]:.2f}">{s["sharpe"]:.2f}</td>'
            f'<td data-v="{s["max_dd_pct"]:.1f}">{s["max_dd_pct"]:.1f}%</td>'
            f'<td data-v="{s["profit_factor"]:.2f}">{pf_str}</td>'
            f'<td data-v="{score:.3f}">{score:.3f}</td>'
        )
        row += '</tr>'
        parts.append(row)
    parts.append('</tbody></table></div>')

    return "\n".join(parts)


# ── CSS ──────────────────────────────────────────────────────────

CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
       max-width: 1500px; margin: 20px auto; padding: 0 20px; background: #fafafa; color: #222; }
h1 { border-bottom: 3px solid #333; padding-bottom: 10px; }
h2 { margin-top: 36px; color: #333; border-bottom: 1px solid #ddd; padding-bottom: 6px; }
h3 { margin-top: 20px; color: #555; }
h4 { margin: 0 0 6px; font-size: 13px; color: #555; font-weight: 600; }
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
th:first-child, td:first-child { text-align: left; }
.pos { color: #2e7d32; font-weight: 600; }
.neg { color: #c62828; }
.empty { color: #bbb; background: #f8f8f8; }
.hm-wrap { overflow-x: auto; margin: 4px 0 12px; }
.hm-label { text-align: left !important; font-weight: 600; background: #f0f0f0 !important;
             color: #333 !important; min-width: 60px; }
.hm-pair { display: flex; gap: 32px; flex-wrap: wrap; align-items: flex-start;
           margin-bottom: 28px; }
.hm-pair > div { flex: 0 0 auto; }
.eq-bar { display: inline-block; height: 14px; border-radius: 2px; vertical-align: middle; }
.eq-pos { background: #4caf50; }
.eq-neg { background: #e53935; }
"""


# ── HTML Report ──────────────────────────────────────────────────

def generate_html(strategy_name, result, n_intervals, runtime_s,
                  strategy_description="", qty=1, heatmap_pairs=None,
                  robustness=False):
    """Generate a self-contained HTML backtest report.

    Args:
        strategy_name:        Strategy.name string
        result:               GridResult from backtester.results
        n_intervals:          number of 5-min market states processed
        runtime_s:            grid execution time in seconds
        strategy_description: Short prose description shown near the top
        qty:                  Contracts per trade (default 1)
        heatmap_pairs:        Optional list of (pa, pb) tuples to pin;
                              falls back to auto-selection by PnL spread
        robustness:           If True, include the robustness analysis section
                              (distribution histogram, marginal charts, all-combos
                              table). Off by default for fast discovery runs.

    Returns:
        Complete self-contained HTML string.
    """
    # ── Unpack from GridResult ────────────────────────────────────
    df           = result.df
    keys         = result.keys
    param_grid   = result.param_grid
    account_size = result.account_size
    date_range   = result.date_range
    all_stats    = result.all_stats
    scores       = result.scores
    ranked       = result.ranked
    total_trades = result.total_trades
    param_names  = result.param_names
    best_key     = result.best_key
    best_stats   = result.best_stats
    df_best      = result.df_best
    best_eq      = result.best_eq
    best_final_nav = result.best_final_nav
    best_params  = result.best_params
    top_n_eq     = result.top_n_eq

    title = strategy_name.replace("_", " ").title()
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    is_negative = best_stats and best_stats["total_pnl"] < 0

    parts = []

    # ── Head ─────────────────────────────────────────────────────
    desc_html = (
        f'\n<div class="grid-info" style="margin-top:12px">'
        f'<b>Strategy:</b> {strategy_description}</div>'
        if strategy_description else ""
    )
    parts.append(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Backtest: {title}</title>
<style>{CSS}</style>
</head><body>
<h1>Backtest Report &mdash; {title}</h1>{desc_html}
<div class="meta">
  <span><b>Generated:</b> {now}</span>
  <span><b>Data:</b> {date_range[0]} to {date_range[1]}</span>
  <span><b>Intervals:</b> {n_intervals:,}</span>
  <span><b>Combos:</b> {len(all_stats):,}</span>
  <span><b>Trades:</b> {total_trades:,}</span>
  <span><b>Runtime:</b> {runtime_s:.1f}s</span>
  <span><b>Account:</b> ${account_size:,} / {qty} contract{"s" if qty != 1 else ""}</span>
</div>""")

    # ── Risk summary bar ─────────────────────────────────────────
    if best_eq:
        _eq = best_eq
        _pf = f'{_eq["profit_factor"]:.2f}' if _eq["profit_factor"] < 100 else "99+"
        _bs = best_stats or {}
        parts.append(f"""<div class="grid-info">
  <b>Best Combo &mdash; Risk Summary:</b> &nbsp;
    Max DD: {_fmt_pnl(_eq["max_drawdown"])} ({_eq["max_dd_pct"]:.1f}%) &nbsp;|&nbsp;
  Sharpe: {_eq["sharpe"]:.2f} &nbsp;|&nbsp;
  Sortino: {_eq["sortino"]:.2f} &nbsp;|&nbsp;
  Calmar: {_eq["calmar"]:.2f} &nbsp;|&nbsp;
  Profit Factor: {_pf} &nbsp;|&nbsp;
  Consec Wins: {_eq["consec_wins"]} &nbsp;|&nbsp;
  Consec Losses: {_eq["consec_losses"]} &nbsp;|&nbsp;
  R&sup2;: {_bs.get("r_squared", 0):.2f} &nbsp;|&nbsp;
  Omega: {_bs.get("omega", 0):.2f} &nbsp;|&nbsp;
  Ulcer: {_bs.get("ulcer", 0):.1f} &nbsp;|&nbsp;
  Consistency: {_bs.get("consistency", 0)*100:.0f}%
</div>""")

    # ── Best combo box ───────────────────────────────────────────
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
        ]
        if best_final_nav is not None:
            metrics_html.append(("Final NAV", _fmt_pnl(best_final_nav)))
        if best_eq:
            metrics_html.extend([
                ("Max DD", f'{_fmt_pnl(best_eq["max_drawdown"])} ({best_eq["max_dd_pct"]:.1f}%)'),
                ("Sharpe", f'{best_eq["sharpe"]:.2f}'),
                ("Sortino", f'{best_eq["sortino"]:.2f}'),
                ("Calmar", f'{best_eq["calmar"]:.2f}'),
                ("Profit Factor", f'{best_eq["profit_factor"]:.2f}'),
                ("Consec Wins", str(best_eq["consec_wins"])),
                ("Consec Losses", str(best_eq["consec_losses"])),
                ("R\u00b2", f'{best_stats.get("r_squared", 0):.2f}'),
                ("Omega", f'{best_stats.get("omega", 0):.2f}'),
                ("Ulcer Index", f'{best_stats.get("ulcer", 0):.1f}'),
                ("Consistency", f'{best_stats.get("consistency", 0)*100:.0f}%'),
            ])

        for label, val in metrics_html:
            parts.append(
                f'  <span class="metric">'
                f'<span class="metric-label">{label}</span><br>'
                f'<span class="metric-value">{val}</span></span>'
            )

        if best_eq and best_eq["daily"]:
            chart_svg = _equity_chart_svg(best_eq["daily"], capital=account_size)
            parts.append(
                f'<div style="margin-top:14px">{chart_svg}</div>')

        parts.append("</div>")

    # ── Parameter grid info ──────────────────────────────────────
    parts.append('<h2>Parameter Grid</h2><div class="grid-info">')
    for p in param_names:
        vals = param_grid[p]
        parts.append(
            f'<b>{_param_label(p)}:</b> <code>{vals}</code> ({len(vals)} values)<br>')
    n_combos = 1
    for v in param_grid.values():
        n_combos *= len(v)
    parts.append(f'<b>Total combos:</b> {n_combos:,}</div>')

    # ── Top 20 combos table ──────────────────────────────────────
    top_n = min(20, len(ranked))
    parts.append(f'<h2>Top {top_n} Combos</h2>')
    _sc = cfg.scoring
    parts.append(
        f'<p style="color:#555;font-size:13px;margin:4px 0 8px">'
        f'Ranked by composite score &mdash; '
        f'<b>R&sup2;</b> {_sc.w_r_squared*100:.0f}% &middot; '
        f'<b>Sharpe</b> {_sc.w_sharpe*100:.0f}% &middot; '
        f'<b>PnL</b> {_sc.w_pnl*100:.0f}% &middot; '
        f'<b>Max&nbsp;DD</b> {_sc.w_max_dd*100:.0f}% (&#x2193;&nbsp;better) &middot; '
        f'<b>Omega</b> {_sc.w_omega*100:.0f}% &middot; '
        f'<b>Ulcer</b> {_sc.w_ulcer*100:.0f}% (&#x2193;&nbsp;better) &middot; '
        f'<b>Consistency</b> {_sc.w_consistency*100:.0f}% &middot; '
        f'<b>Profit&nbsp;Factor</b> {_sc.w_profit_factor*100:.0f}% &nbsp;|&nbsp; '
        f'min trades: {_sc.min_trades}'
        f'</p>'
    )
    parts.append('<div class="hm-wrap"><table>')
    hdr = "<tr><th>#</th>"
    for p in param_names:
        hdr += f"<th>{_param_label(p)}</th>"
    hdr += ("<th>Trades</th><th>Total PnL</th><th>Avg PnL</th><th>Med PnL</th>"
            "<th>Win%</th><th>Max Win</th><th>Max Loss</th>"
            "<th>Max DD</th><th>Sharpe</th><th>Sortino</th><th>Calmar</th><th>PF</th>"
            "<th>R&sup2;</th><th>Omega</th><th>Ulcer</th><th>Consist</th>"
            "<th>Score</th></tr>")
    parts.append(hdr)
    for rank, (key, s) in enumerate(ranked[:top_n], 1):
        params = dict(key)
        pnl_cls = _pnl_class(s["total_pnl"])
        avg_cls = _pnl_class(s["avg_pnl"])
        pf_str = f'{s["profit_factor"]:.2f}' if s["profit_factor"] < 100 else "99+"
        row = f'<tr><td>{rank}</td>'
        for p in param_names:
            row += f'<td>{_fmt_val(params[p])}</td>'
        _eq_detail = top_n_eq.get(key)
        _sortino_str = f'{_eq_detail["sortino"]:.2f}' if _eq_detail else "&mdash;"
        _calmar_str  = f'{_eq_detail["calmar"]:.2f}'  if _eq_detail else "&mdash;"
        row += (
            f'<td>{s["n"]}</td>'
            f'<td class="{pnl_cls}">{_fmt_pnl(s["total_pnl"])}</td>'
            f'<td class="{avg_cls}">{_fmt_pnl(s["avg_pnl"])}</td>'
            f'<td>{_fmt_pnl(s["median_pnl"])}</td>'
            f'<td>{s["win_rate"]*100:.0f}%</td>'
            f'<td class="pos">{_fmt_pnl(s["max_win"])}</td>'
            f'<td class="neg">{_fmt_pnl(s["max_loss"])}</td>'
            f'<td class="neg">{s["max_dd_pct"]:.1f}%</td>'
            f'<td>{s["sharpe"]:.2f}</td>'
            f'<td>{_sortino_str}</td>'
            f'<td>{_calmar_str}</td>'
            f'<td>{pf_str}</td>'
            f'<td>{s["r_squared"]:.2f}</td>'
            f'<td>{s["omega"]:.2f}</td>'
            f'<td>{s["ulcer"]:.1f}</td>'
            f'<td>{s["consistency"]*100:.0f}%</td>'
            f'<td>{scores[key]:.3f}</td></tr>'
        )
        parts.append(row)
    parts.append("</table></div>")

    # ── Top 5 absolute PnL combos ────────────────────────────────
    top_abs = sorted(all_stats.items(), key=lambda kv: kv[1]["total_pnl"], reverse=True)[:5]
    parts.append('<h2>Top 5 Absolute PnL Combos</h2>')
    parts.append(
        '<p style="color:#555;font-size:13px;margin:4px 0 8px">'
        'Ranked by total PnL only &mdash; no scoring applied.</p>'
    )
    parts.append('<div class="hm-wrap"><table>')
    hdr2 = "<tr><th>#</th>"
    for p in param_names:
        hdr2 += f"<th>{_param_label(p)}</th>"
    hdr2 += ("<th>Trades</th><th>Total PnL</th><th>Avg PnL</th><th>Med PnL</th>"
             "<th>Win%</th><th>Max Win</th><th>Max Loss</th>"
             "<th>Max DD</th><th>Sharpe</th><th>PF</th><th>Score</th></tr>")
    parts.append(hdr2)
    for rank, (key, s) in enumerate(top_abs, 1):
        params = dict(key)
        pnl_cls = _pnl_class(s["total_pnl"])
        avg_cls = _pnl_class(s["avg_pnl"])
        pf_str = f'{s["profit_factor"]:.2f}' if s["profit_factor"] < 100 else "99+"
        score_str = f'{scores[key]:.3f}' if key in scores else "&mdash;"
        row = f'<tr><td>{rank}</td>'
        for p in param_names:
            row += f'<td>{_fmt_val(params[p])}</td>'
        row += (
            f'<td>{s["n"]}</td>'
            f'<td class="{pnl_cls}">{_fmt_pnl(s["total_pnl"])}</td>'
            f'<td class="{avg_cls}">{_fmt_pnl(s["avg_pnl"])}</td>'
            f'<td>{_fmt_pnl(s["median_pnl"])}</td>'
            f'<td>{s["win_rate"]*100:.0f}%</td>'
            f'<td class="pos">{_fmt_pnl(s["max_win"])}</td>'
            f'<td class="neg">{_fmt_pnl(s["max_loss"])}</td>'
            f'<td class="neg">{s["max_dd_pct"]:.1f}%</td>'
            f'<td>{s["sharpe"]:.2f}</td>'
            f'<td>{pf_str}</td>'
            f'<td>{score_str}</td></tr>'
        )
        parts.append(row)
    parts.append("</table></div>")

    # ── Robustness section (opt-in via --robustness flag) ─────────
    if robustness:
        parts.append(_robustness_section_html(result, highlight_key=best_key))

    # ── Performance fan chart ─────────────────────────────────────
    if result.fan_curves:
        fan_top = len(result.fan_curves)
        parts.append(
            f'<h2>Top {fan_top} Equity Curves</h2>'
            f'<p style="color:#555;font-size:13px;margin:4px 0 8px">'
            f'Hover any curve for its parameters and PnL. '
            f'Shaded band = full min&ndash;max range across all {fan_top} combos.</p>'
        )
        parts.append(_fan_chart_svg(result.fan_curves, capital=account_size))

    # ── Parameter sensitivity heatmaps ───────────────────────────
    # Design:
    #  - Cells pool ALL trades from combos sharing (pa_val, pb_val), so no
    #    combo with few trades can distort the cell value.
    #  - Left table: total PnL. Right table: win rate %.
    #  - Auto-selects top-3 most informative pairs by PnL spread.
    #  - Strategy can override with HEATMAP_PAIRS.
    if len(param_names) >= 2:
        parts.append("<h2>Parameter Sensitivity</h2>")
        parts.append(
            "<p>Each cell pools <em>all</em> trades sharing those two parameter "
            "values (marginalised over all other parameters). "
            "<b>Left:</b> Total PnL &nbsp; <b>Right:</b> Win rate. "
            "Pairs ranked by PnL spread — most informative first.</p>"
        )

        selected_pairs = _select_pairs(result, heatmap_pairs_override=heatmap_pairs)

        for pa, pb in selected_pairs:
            grid_pnl, grid_wr, grid_n, a_vals, b_vals = _build_heatmap_data(
                df, keys, pa, pb)
            if not grid_pnl:
                continue

            pnl_vals = list(grid_pnl.values())
            wr_vals = list(grid_wr.values())
            spread = max(pnl_vals) - min(pnl_vals)
            pnl_min, pnl_max = min(pnl_vals), max(pnl_vals)
            wr_min, wr_max = min(wr_vals), max(wr_vals)

            parts.append(
                f'<h3>{_param_label(pa)} &times; {_param_label(pb)} '
                f'<span style="font-size:12px;color:#888;font-weight:normal">'
                f'spread {_fmt_pnl(spread)}</span></h3>'
            )
            parts.append('<div class="hm-pair">')

            # PnL table
            parts.append('<div>')
            parts.append('<h4>Total PnL (pooled trades)</h4>')
            parts.append('<div class="hm-wrap"><table>')
            parts.append(
                f'<tr><th class="hm-label">{_param_label(pa)} \\ {_param_label(pb)}</th>')
            for b in b_vals:
                parts.append(f'<th>{_fmt_val(b)}</th>')
            parts.append('</tr>')
            for a in a_vals:
                parts.append(f'<tr><td class="hm-label">{_fmt_val(a)}</td>')
                for b in b_vals:
                    v = grid_pnl.get((a, b))
                    if v is not None:
                        bg = _heatmap_color(v, pnl_min, pnl_max)
                        cls = _pnl_class(v)
                        n = grid_n.get((a, b), 0)
                        parts.append(
                            f'<td style="background:{bg}" title="{n} trades">'
                            f'<span class="{cls}">{_fmt_pnl(v)}</span></td>')
                    else:
                        parts.append('<td class="empty">&mdash;</td>')
                parts.append('</tr>')
            parts.append('</table></div></div>')

            # Win rate table
            parts.append('<div>')
            parts.append('<h4>Win Rate %</h4>')
            parts.append('<div class="hm-wrap"><table>')
            parts.append(
                f'<tr><th class="hm-label">{_param_label(pa)} \\ {_param_label(pb)}</th>')
            for b in b_vals:
                parts.append(f'<th>{_fmt_val(b)}</th>')
            parts.append('</tr>')
            for a in a_vals:
                parts.append(f'<tr><td class="hm-label">{_fmt_val(a)}</td>')
                for b in b_vals:
                    wr = grid_wr.get((a, b))
                    if wr is not None:
                        bg = _heatmap_color(wr, wr_min, wr_max)
                        parts.append(f'<td style="background:{bg}">{wr:.0f}%</td>')
                    else:
                        parts.append('<td class="empty">&mdash;</td>')
                parts.append('</tr>')
            parts.append('</table></div></div>')

            parts.append('</div>')  # .hm-pair

    # ── Daily equity — best combo ────────────────────────────────
    if best_eq and best_eq["daily"]:
        parts.append("<h2>Daily Equity &mdash; Best Combo</h2>")
        parts.append('<div class="hm-wrap"><table>')
        parts.append(
            '<tr><th style="text-align:left">Date</th>'
            '<th>Day PnL</th><th>Cumulative</th><th>Equity</th>'
            '<th style="min-width:120px">Visual</th></tr>')
        max_abs = max(abs(row[1]) for row in best_eq["daily"]) or 1
        for ds, pnl, cum, high, low, eq in best_eq["daily"]:
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

        eq = best_eq
        _pf2 = f'{eq["profit_factor"]:.2f}' if eq["profit_factor"] < 100 else "99+"
        parts.append(f"""
<div class="grid-info">
  <b>Max Drawdown:</b> {_fmt_pnl(eq["max_drawdown"])} ({eq["max_dd_pct"]:.1f}%) &nbsp;|&nbsp;
  <b>Sharpe:</b> {eq["sharpe"]:.2f} &nbsp;|&nbsp;
  <b>Sortino:</b> {eq["sortino"]:.2f} &nbsp;|&nbsp;
  <b>Calmar:</b> {eq["calmar"]:.2f} &nbsp;|&nbsp;
  <b>Profit Factor:</b> {_pf2} &nbsp;|&nbsp;
  <b>Consec Wins:</b> {eq["consec_wins"]} &nbsp;|&nbsp;
  <b>Consec Losses:</b> {eq["consec_losses"]}
</div>""")

    # ── Trade log — best combo ───────────────────────────────────
    if df_best is not None and not df_best.empty:
        parts.append("<h2>Trade Log &mdash; Best Combo</h2>")
        parts.append(f'<p>{len(df_best)} trades total</p>')
        parts.append('<div class="hm-wrap"><table>')
        parts.append(
            '<tr><th style="text-align:left">Date</th>'
            '<th>Entry Time</th><th>Exit Time</th>'
            '<th>Entry Spot</th><th>Exit Spot</th>'
            '<th>Entry USD</th><th>Exit USD</th>'
            '<th>Fees</th><th>PnL</th><th>Reason</th></tr>')
        for t in df_best.itertuples(index=False):
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
