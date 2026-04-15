#!/usr/bin/env python3
"""
results.py — GridResult: holds all engine output for a completed grid run
and computes/caches per-combo statistics, scoring, and equity metrics.

Pipeline (executed in GridResult.__init__):
  Step 1  _all_combo_stats()   — vectorised pandas/numpy metrics for ALL combos:
                                  Sharpe, PnL, R², Omega, Ulcer Index, monthly
                                  consistency, max drawdown %, profit factor.
  Step 2  _score_combos()      — percentile-rank composite score (0→1) using
                                  8-metric weighted formula from config.toml.
  Step 3  equity_metrics()     — full daily curve + Sortino/Calmar/streaks for
                                  the top-N combos only (default top 20).

After construction, GridResult is fully self-contained. reporting_v2.py reads
only GridResult public attributes — it never touches the raw trade log or NAV
DataFrames, and calls no functions from this module.

Usage:
    from backtester.results import GridResult
    result = GridResult(df, keys, nav_daily_df, final_nav_df,
                        param_grid=strategy_cls.PARAM_GRID,
                        account_size=10000,
                        date_range=(date_from, date_to))

Single-combo equity detail:
    from backtester.results import equity_metrics
    eq = equity_metrics(nav_daily_df, combo_idx, capital)
"""
import statistics
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

from backtester.config import cfg


# ── Per-Combo Stats ──────────────────────────────────────────────

def _all_combo_stats(df, keys, capital=10000, nav_daily_df=None, date_from=None, date_to=None):
    """Vectorised per-combo stats for all combos at once.

    date_from / date_to (str "YYYY-MM-DD"): the backtest's first and last snapshot
    day. When provided, all daily series are padded to this full range so metrics
    are comparable and no combo gets an unfair shorter window.

    Uses pandas groupby so 5000 combos cost one pass, not 5000 Python loops.
    Returns dict[param_tuple → stats_dict].
    """
    if df.empty:
        return {}

    g = df.groupby("combo_idx")

    n           = g["pnl"].count()
    total_pnl   = g["pnl"].sum()
    avg_pnl     = g["pnl"].mean()
    median_pnl  = g["pnl"].median()
    std_pnl     = g["pnl"].std(ddof=1).fillna(0.0)
    win_rate    = (df["pnl"] > 0).groupby(df["combo_idx"]).sum() / n
    max_win     = g["pnl"].max()
    max_loss    = g["pnl"].min()

    gross_win  = (df[df["pnl"] > 0].groupby("combo_idx")["pnl"]
                  .sum().reindex(n.index, fill_value=0.0))
    gross_loss = (df[df["pnl"] < 0].groupby("combo_idx")["pnl"]
                  .sum().abs().reindex(n.index, fill_value=0.0))
    pf = (gross_win / gross_loss.replace(0, np.nan)).fillna(99.9).clip(upper=99.9)

    # Common date range for all metrics (full backtest window, not just trade dates)
    _d_from = date_from if date_from else df["entry_date"].min()
    _d_to   = date_to   if date_to   else df["entry_date"].max()
    all_dates = pd.date_range(_d_from, _d_to, freq="D").strftime("%Y-%m-%d")

    # Sharpe + drawdown block.
    # Use NAV-based daily returns (includes mark-to-market of open positions)
    # when nav_daily_df is available — this makes Sharpe consistent with
    # equity_metrics().  Fall back to entry-date bucketed realized PnL only
    # when no NAV data exists.
    if nav_daily_df is not None and not nav_daily_df.empty:
        nav = nav_daily_df.copy()
        nav["date"] = nav["date"].astype(str)

        nav_close = nav.pivot(index="date", columns="combo_idx", values="nav_close")
        nav_low   = nav.pivot(index="date", columns="combo_idx", values="nav_low")
        nav_close = nav_close.reindex(all_dates).ffill().fillna(capital)
        nav_low   = nav_low.reindex(all_dates).fillna(nav_close)

        # Daily returns = diff of nav_close; first day = nav_close[0] - capital
        daily_returns_pivot = nav_close.diff()
        daily_returns_pivot.iloc[0] = nav_close.iloc[0] - capital

        running_peak_close = nav_close.cummax()

        # Max drawdown: intraday low vs running close high watermark (conservative)
        dd_intraday_pivot = (running_peak_close - nav_low) / running_peak_close.replace(0, np.nan)
        max_dd_pct_all = (dd_intraday_pivot.max() * 100).fillna(0.0)

        # Ulcer Index: RMS of % drawdowns from running close peak
        pct_dd_close = (running_peak_close - nav_close) / running_peak_close.replace(0, np.nan) * 100
        ulcer_all = np.sqrt((pct_dd_close ** 2).mean()).fillna(0.0)
    else:
        # Fallback: entry-date bucketed realized PnL.
        daily_by_combo = df.groupby(["combo_idx", "entry_date"])["pnl"].sum()
        daily_returns_pivot = (
            daily_by_combo.unstack(level=0, fill_value=0.0)
            .reindex(all_dates, fill_value=0.0)
        )
        equity_pivot = capital + daily_returns_pivot.cumsum()
        running_peak = equity_pivot.cummax()
        dd_pivot = (running_peak - equity_pivot) / running_peak.replace(0, np.nan)
        max_dd_pct_all = (dd_pivot.max() * 100).fillna(0.0)

        # Ulcer Index: RMS of % drawdowns from running equity peak
        pct_dd_close = (running_peak - equity_pivot) / running_peak.replace(0, np.nan) * 100
        ulcer_all = np.sqrt((pct_dd_close ** 2).mean()).fillna(0.0)

    # ── Shared metrics (both branches use daily_returns_pivot) ──
    avg_d = daily_returns_pivot.mean()
    std_d = daily_returns_pivot.std(ddof=1).replace(0, np.nan)
    sharpe = (avg_d / std_d * 365 ** 0.5).fillna(0.0)

    # Omega ratio (threshold = 0): sum of gains / sum of losses using daily returns
    pos_sum = daily_returns_pivot.clip(lower=0).sum()
    neg_sum = daily_returns_pivot.clip(upper=0).abs().sum()
    omega_all = (pos_sum / neg_sum.replace(0, np.nan)).fillna(99.9).clip(upper=99.9)

    # R² of cumulative equity vs linear trend (0 = random walk, 1 = perfect uptrend)
    n_days_r2 = len(daily_returns_pivot)
    x_r2 = np.arange(n_days_r2, dtype=float)
    x_mean_r2 = x_r2.mean()
    x_cent = x_r2 - x_mean_r2
    cum_mat = daily_returns_pivot.cumsum()
    y_cent = cum_mat.sub(cum_mat.mean(axis=0), axis=1)
    x2_sum = float((x_cent ** 2).sum())
    if x2_sum > 0:
        slope_vec = y_cent.mul(x_cent, axis=0).sum(axis=0) / x2_sum
        y_hat = pd.DataFrame(
            np.outer(x_cent, slope_vec.values),
            index=daily_returns_pivot.index,
            columns=daily_returns_pivot.columns,
        )
        ss_res = ((y_cent - y_hat) ** 2).sum(axis=0)
        ss_tot = (y_cent ** 2).sum(axis=0).replace(0, np.nan)
        r_sq_all = (1.0 - ss_res / ss_tot).fillna(0.0).clip(0.0, 1.0)
    else:
        r_sq_all = pd.Series(0.0, index=daily_returns_pivot.columns)

    # Monthly consistency: fraction of calendar months with net positive return.
    # Requires ≥ 2 unique months to be meaningful — with only 1 month the metric
    # degenerates to a binary duplicate of the PnL sign (0 or 1 for every combo).
    # In that case, emit 0.5 for all combos so the metric contributes no ranking
    # differentiation regardless of its scoring weight.
    month_labels = [d[:7] for d in daily_returns_pivot.index]
    n_unique_months = len(set(month_labels))
    if n_unique_months >= 2:
        monthly_pivot = daily_returns_pivot.copy()
        monthly_pivot.index = month_labels
        monthly_sum = monthly_pivot.groupby(level=0).sum()
        consistency_all = (monthly_sum > 0).mean().fillna(0.0)
    else:
        consistency_all = pd.Series(0.5, index=daily_returns_pivot.columns)

    result = {}
    for combo_idx, key in enumerate(keys):
        if combo_idx not in n.index:
            continue
        result[key] = {
            "n":             int(n[combo_idx]),
            "total_pnl":     float(total_pnl[combo_idx]),
            "avg_pnl":       float(avg_pnl[combo_idx]),
            "median_pnl":    float(median_pnl[combo_idx]),
            "stdev":         float(std_pnl.get(combo_idx, 0.0)),
            "win_rate":      float(win_rate.get(combo_idx, 0.0)),
            "max_win":       float(max_win[combo_idx]),
            "max_loss":      float(max_loss[combo_idx]),
            "profit_factor": float(pf.get(combo_idx, 0.0)),
            "sharpe":        float(sharpe.get(combo_idx, 0.0)),
            "max_dd_pct":    float(max_dd_pct_all.get(combo_idx, 0.0)),
            "omega":         float(omega_all.get(combo_idx, 1.0)),
            "ulcer":         float(ulcer_all.get(combo_idx, 0.0)),
            "r_squared":     float(r_sq_all.get(combo_idx, 0.0)),
            "consistency":   float(consistency_all.get(combo_idx, 0.0)),
        }
    return result


# ── Combo Scoring ─────────────────────────────────────────────────

def _score_combos(all_stats):
    """Rank combos using a percentile-weighted composite score (0–1).

    Each metric is percentile-ranked across eligible combos (0 = worst, 1 = best).
    Metrics where *lower is better* (max_dd_pct) are inverted with
    (1 − rank) before weighting, so the safest combo still scores 1.0 on those.

    Combos below cfg.scoring.min_trades are ineligible and receive score 0.0,
    sinking them to the bottom of the ranked list.

    Returns dict[key → float].
    """
    sc = cfg.scoring
    items = list(all_stats.items())
    eligible = [(k, s) for k, s in items if s["n"] >= sc.min_trades]

    if not eligible:
        return {k: 0.0 for k, _ in items}

    def _prank(vals):
        """Percentile rank: 0.0 (lowest value) → 1.0 (highest value)."""
        n = len(vals)
        if n == 1:
            return [0.5]
        order = sorted(range(n), key=lambda i: vals[i])
        ranks = [0.0] * n
        for pos, idx in enumerate(order):
            ranks[idx] = pos / (n - 1)
        return ranks

    sharpe_r  = _prank([s["sharpe"]        for _, s in eligible])
    pnl_r     = _prank([s["total_pnl"]     for _, s in eligible])
    dd_r      = _prank([s["max_dd_pct"]    for _, s in eligible])   # inverted — lower is better
    pf_r      = _prank([s["profit_factor"] for _, s in eligible])
    r2_r      = _prank([s["r_squared"]     for _, s in eligible])
    omega_r   = _prank([s["omega"]         for _, s in eligible])
    ulcer_r   = _prank([s["ulcer"]         for _, s in eligible])   # inverted — lower is better
    consist_r = _prank([s["consistency"]   for _, s in eligible])

    scores = {}
    for i, (k, _) in enumerate(eligible):
        scores[k] = (
            sc.w_r_squared       * r2_r[i]
            + sc.w_sharpe        * sharpe_r[i]
            + sc.w_pnl           * pnl_r[i]
            + sc.w_max_dd        * (1.0 - dd_r[i])
            + sc.w_omega         * omega_r[i]
            + sc.w_ulcer         * (1.0 - ulcer_r[i])
            + sc.w_consistency   * consist_r[i]
            + sc.w_profit_factor * pf_r[i]
        )

    # Ineligible combos score 0 and sink to the bottom
    for k, _ in items:
        scores.setdefault(k, 0.0)
    return scores


# ── Robustness Stats ─────────────────────────────────────────────

def _robustness_stats(all_stats, keys, param_grid):
    """Compute grid-wide robustness metrics from per-combo stats.

    Returns a dict with:
        pnl_all          — list of (key, total_pnl) sorted by combo index order
        pct_profitable   — fraction of combos with total_pnl > 0
        median_pnl       — median total PnL across all combos
        p10_pnl          — 10th-percentile PnL
        p90_pnl          — 90th-percentile PnL
        pnl_iqr          — interquartile range (P75 - P25)
        fragility_score  — (max - min) / abs(median); lower = more robust
        param_sensitivity — dict[param_name → list of (value, mean_pnl, p10, p90)]
                            marginal curve: for each unique param value, aggregate
                            PnL over all combos sharing that value
        monotonicity      — dict[param_name → Spearman ρ]
                            |ρ| near 1 = smooth hill; near 0 = no clear trend
        heatmap_pairs     — list of (pa, pb) sorted by PnL spread, most informative first
    """
    if not all_stats:
        return {
            "pnl_all": [],
            "pct_profitable": 0.0,
            "median_pnl": 0.0,
            "p10_pnl": 0.0,
            "p90_pnl": 0.0,
            "pnl_iqr": 0.0,
            "fragility_score": 0.0,
            "param_sensitivity": {},
            "monotonicity": {},
            "heatmap_pairs": [],
        }

    items = list(all_stats.items())
    pnl_all = [(k, s["total_pnl"]) for k, s in items]
    pnls = sorted(s["total_pnl"] for _, s in items)
    n = len(pnls)

    def _percentile(sorted_vals, p):
        if not sorted_vals:
            return 0.0
        idx = (len(sorted_vals) - 1) * p / 100.0
        lo, hi = int(idx), min(int(idx) + 1, len(sorted_vals) - 1)
        return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo)

    median_pnl = _percentile(pnls, 50)
    p10 = _percentile(pnls, 10)
    p25 = _percentile(pnls, 25)
    p75 = _percentile(pnls, 75)
    p90 = _percentile(pnls, 90)
    pnl_min, pnl_max = pnls[0], pnls[-1]

    pct_profitable = sum(1 for v in pnls if v > 0) / n
    iqr = p75 - p25
    fragility = (pnl_max - pnl_min) / abs(median_pnl) if abs(median_pnl) > 1e-9 else 0.0

    # ── Per-parameter marginal curves ────────────────────────────
    # Continuous params: more than 1 unique value
    continuous = {p: vals for p, vals in param_grid.items() if len(vals) > 1}

    param_sensitivity = {}
    monotonicity = {}

    for param, values in continuous.items():
        marginal = []
        for val in sorted(set(values)):
            subset_pnls = sorted(
                s["total_pnl"] for k, s in items
                if dict(k).get(param) == val
            )
            if not subset_pnls:
                continue
            mean_v = sum(subset_pnls) / len(subset_pnls)
            p10_v = _percentile(subset_pnls, 10)
            p90_v = _percentile(subset_pnls, 90)
            marginal.append((val, mean_v, p10_v, p90_v))
        param_sensitivity[param] = marginal

        # Spearman ρ between param value rank and mean_pnl rank
        if len(marginal) >= 3:
            x_vals = [pt[0] for pt in marginal]
            y_vals = [pt[1] for pt in marginal]
            n_m = len(x_vals)

            def _ranks(lst):
                order = sorted(range(n_m), key=lambda i: lst[i])
                r = [0.0] * n_m
                for pos, idx in enumerate(order):
                    r[idx] = pos + 1
                return r

            rx = _ranks(x_vals)
            ry = _ranks(y_vals)
            r_mean = (n_m + 1) / 2.0
            num = sum((rx[i] - r_mean) * (ry[i] - r_mean) for i in range(n_m))
            denom_x = sum((rx[i] - r_mean) ** 2 for i in range(n_m)) ** 0.5
            denom_y = sum((ry[i] - r_mean) ** 2 for i in range(n_m)) ** 0.5
            denom = denom_x * denom_y
            monotonicity[param] = num / denom if denom > 1e-12 else 0.0
        else:
            monotonicity[param] = 0.0

    # ── Heatmap pair ranking (moved from reporting_v2._select_pairs) ──
    # Pool PnL by (pa_val, pb_val) and rank pairs by spread.
    all_param_names = sorted(continuous.keys())
    heatmap_pairs = []
    if len(all_param_names) >= 2:
        from itertools import combinations
        pair_spreads = []
        for pa, pb in combinations(all_param_names, 2):
            cell_pnls = {}
            for k, s in items:
                kd = dict(k)
                cell_key = (kd.get(pa), kd.get(pb))
                cell_pnls.setdefault(cell_key, []).append(s["total_pnl"])
            pooled = {ck: sum(vs) for ck, vs in cell_pnls.items()}
            if pooled:
                spread = max(pooled.values()) - min(pooled.values())
                pair_spreads.append((spread, pa, pb))
        pair_spreads.sort(reverse=True)
        heatmap_pairs = [(pa, pb) for _, pa, pb in pair_spreads[:3]]

    return {
        "pnl_all": pnl_all,
        "pct_profitable": pct_profitable,
        "median_pnl": median_pnl,
        "p10_pnl": p10,
        "p90_pnl": p90,
        "pnl_iqr": iqr,
        "fragility_score": fragility,
        "param_sensitivity": param_sensitivity,
        "monotonicity": monotonicity,
        "heatmap_pairs": heatmap_pairs,
    }


# ── Equity Metrics ───────────────────────────────────────────────

def equity_metrics(df_combo, capital=10000, nav_daily_combo=None, date_from=None, date_to=None):
    """Build daily equity curve and compute risk metrics from a per-combo DataFrame.

    date_from / date_to (str "YYYY-MM-DD" or None): when provided, the daily
    returns array is padded to cover this full range with zero-return days.
    Pass the global backtest date range so Sharpe matches _all_combo_stats().

    Sortino and Calmar match QuantStats formulas.
    """
    if nav_daily_combo is not None and not nav_daily_combo.empty:
        nav = nav_daily_combo.copy()
        nav["date"] = nav["date"].astype(str)
        nav = nav.sort_values("date")

        # Use global date bounds when provided so Sharpe matches _all_combo_stats
        first_str = date_from if date_from else nav["date"].iloc[0]
        last_str  = date_to   if date_to   else nav["date"].iloc[-1]
        first = datetime.strptime(first_str, "%Y-%m-%d").date()
        last  = datetime.strptime(last_str,  "%Y-%m-%d").date()
        all_dates = pd.date_range(first, last, freq="D").strftime("%Y-%m-%d")

        nav_close = nav.set_index("date")["nav_close"].reindex(all_dates).ffill().fillna(capital)
        nav_low = nav.set_index("date")["nav_low"].reindex(all_dates)
        nav_low = nav_low.fillna(nav_close)
        nav_high = nav.set_index("date")["nav_high"].reindex(all_dates)
        nav_high = nav_high.fillna(nav_close)

        daily_returns = nav_close.diff().fillna(nav_close.iloc[0] - capital).tolist()
        cumulative = []
        cum = 0.0
        peak_close = capital
        max_dd_pct = 0.0
        peak_close_at_max_dd = capital

        for i, ds in enumerate(all_dates):
            pnl = float(daily_returns[i])
            cum += pnl
            eq = float(nav_close.iloc[i])
            peak_close = max(peak_close, eq)
            low  = float(nav_low.iloc[i])
            high = float(nav_high.iloc[i])

            # Max drawdown: intraday low vs running close high watermark (conservative)
            dd_pct = (peak_close - low) / peak_close if peak_close > 0 else 0.0
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct
                peak_close_at_max_dd = peak_close
            cumulative.append((ds, pnl, cum, high, low, eq))
    else:
        if df_combo is None or df_combo.empty:
            return None

        date_pnl = df_combo.groupby("entry_date")["pnl"].sum().to_dict()

        sorted_dates = sorted(date_pnl.keys())
        # Use global date bounds when provided so Sharpe matches _all_combo_stats
        first_str = date_from if date_from else sorted_dates[0]
        last_str  = date_to   if date_to   else sorted_dates[-1]
        first = datetime.strptime(first_str, "%Y-%m-%d").date()
        last  = datetime.strptime(last_str,  "%Y-%m-%d").date()
        daily = []
        d = first
        while d <= last:
            ds = d.strftime("%Y-%m-%d")
            daily.append((ds, date_pnl.get(ds, 0.0)))
            d += timedelta(days=1)

        cum = 0.0
        peak_close = capital
        max_dd_pct = 0.0
        peak_close_at_max_dd = capital
        cumulative = []
        for ds, pnl in daily:
            cum += pnl
            eq = capital + cum
            peak_close = max(peak_close, eq)
            dd_pct = (peak_close - eq) / peak_close if peak_close > 0 else 0.0
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct
                peak_close_at_max_dd = peak_close
            cumulative.append((ds, pnl, cum, eq, eq, eq))  # no intraday data in fallback

        daily_returns = [pnl for _, pnl in daily]

    max_dd = max_dd_pct * peak_close_at_max_dd

    gross_win = sum(p for p in daily_returns if p > 0)
    gross_loss = abs(sum(p for p in daily_returns if p < 0))
    pf = (gross_win / gross_loss) if gross_loss > 0 else 99.9

    # Crypto: 365 trading days per year (matches QuantStats periods= usage for crypto)
    PERIODS = 365

    # Sharpe (daily-annualised)
    n_days = len(daily_returns)
    avg_d = statistics.mean(daily_returns)
    std_d = statistics.stdev(daily_returns) if n_days >= 2 else 1.0
    sharpe = (avg_d / std_d * PERIODS ** 0.5) if std_d > 0 else 0.0

    # Sortino — QuantStats: downside = sqrt(sum(neg^2) / N), target = 0
    neg_sq_sum = sum(r * r for r in daily_returns if r < 0)
    downside_rms = (neg_sq_sum / n_days) ** 0.5 if n_days > 0 else 0.0
    sortino = (avg_d / downside_rms * PERIODS ** 0.5) if downside_rms > 0 else 0.0

    # Calmar — CAGR / abs(max_drawdown_pct)
    # Years = n_days / PERIODS (same time base as Sharpe/Sortino, not trade-date span)
    # Max drawdown is the running peak-to-trough fraction computed above.
    final_eq = capital + cum
    years = max(n_days / PERIODS, 1 / PERIODS)
    cagr = (final_eq / capital) ** (1.0 / years) - 1 if capital > 0 else 0.0
    calmar = cagr / max_dd_pct if max_dd_pct > 0 else 0.0

    max_cw = max_cl = cw = cl = 0
    for pnl in daily_returns:
        if pnl > 0:
            cw += 1; cl = 0
        elif pnl < 0:
            cl += 1; cw = 0
        max_cw = max(max_cw, cw)
        max_cl = max(max_cl, cl)

    return {
        "daily": cumulative,
        "total_pnl": cum,
        "max_drawdown": max_dd,
        "max_dd_pct": max_dd_pct * 100,
        "profit_factor": pf,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "consec_wins": max_cw,
        "consec_losses": max_cl,
    }


# ── GridResult ───────────────────────────────────────────────────

class GridResult:
    """All engine output and derived statistics for a completed grid run.

    Constructed from the 4-tuple returned by engine.run_grid_full(), plus
    strategy metadata. Computes and caches all per-combo stats on init so
    rendering is decoupled from statistics.

    Attributes:
        df            — trade log DataFrame (one row per trade, all combos)
        keys          — list of param tuples; keys[i] maps combo_idx i → params
        nav_daily_df  — daily NAV (low/high/close) per combo
        final_nav_df  — final NAV per combo
        param_grid    — {param_name: [values]}
        account_size  — virtual account size in USD
        date_range    — (date_from_str, date_to_str) covering full backtest window
        param_names   — sorted list of parameter names
        key_to_idx    — reverse map: param tuple → combo_idx
        all_stats     — dict[key → stats_dict] for every combo
        scores        — dict[key → float] composite score (0–1)
        ranked        — all_stats items sorted best-first by score
        total_trades  — total trade count across all combos
        best_key      — param tuple of the top-ranked combo
        best_stats    — stats dict for the top-ranked combo
        best_combo_idx — integer combo_idx for the top-ranked combo
        best_params   — dict form of best_key
        df_best       — trade log filtered to the best combo
        top_n_eq      — dict[key → equity_metrics_result] for top-N ranked combos
        best_eq       — alias for top_n_eq[best_key] (backwards compatible)
        fan_curves    — list of (rank, total_pnl, eq_values, tooltip) for fan chart
        fan_dates     — shared x-axis date strings from top_n_eq daily curves
        best_final_nav — final NAV value for the best combo (float or None)

        Robustness attributes (computed from all combos):
        pnl_all          — list of (key, total_pnl) in combo-index order
        pct_profitable   — fraction of combos with total_pnl > 0
        median_pnl       — median total PnL across all combos
        p10_pnl          — 10th-percentile PnL
        p90_pnl          — 90th-percentile PnL
        pnl_iqr          — interquartile range (P75 − P25)
        fragility_score  — (max − min) / |median|; lower = more robust plateau
        param_sensitivity — dict[param → list of (value, mean_pnl, p10, p90)]
        monotonicity      — dict[param → Spearman ρ]; |ρ|≈1 = smooth hill
        heatmap_pairs     — list of (pa, pb) sorted by PnL spread (most informative first)
    """

    def __init__(self, df, keys, nav_daily_df, final_nav_df,
                 param_grid, account_size, date_range):
        self.df = df
        self.keys = keys
        self.nav_daily_df = nav_daily_df
        self.final_nav_df = final_nav_df
        self.param_grid = param_grid
        self.account_size = float(account_size)
        self.date_range = date_range  # (date_from_str, date_to_str)

        # Derived metadata
        self.param_names = sorted(param_grid.keys())
        self.key_to_idx = {k: i for i, k in enumerate(keys)}

        # Vectorised stats over all combos
        _d_from, _d_to = date_range
        self.all_stats = _all_combo_stats(
            df, keys, capital=self.account_size,
            nav_daily_df=nav_daily_df,
            date_from=_d_from, date_to=_d_to,
        )
        self.scores = _score_combos(self.all_stats)
        self.ranked = sorted(
            self.all_stats.items(),
            key=lambda x: self.scores[x[0]],
            reverse=True,
        )
        self.total_trades = sum(s["n"] for s in self.all_stats.values())

        # Best combo
        self.best_key = self.ranked[0][0] if self.ranked else None
        self.best_stats = self.ranked[0][1] if self.ranked else None
        self.best_combo_idx = (
            self.key_to_idx[self.best_key] if self.best_key is not None else None
        )
        self.best_params = dict(self.best_key) if self.best_key else {}

        if self.best_combo_idx is not None:
            self.df_best = (
                df[df["combo_idx"] == self.best_combo_idx]
                .sort_values("entry_time")
            )
        else:
            self.df_best = None

        _best_nav_daily = None
        if (nav_daily_df is not None and not nav_daily_df.empty
                and self.best_combo_idx is not None):
            _best_nav_daily = nav_daily_df[
                nav_daily_df["combo_idx"] == self.best_combo_idx
            ]

        # Step 3: detailed equity metrics for top-N ranked combos
        _top_n = cfg.simulation.top_n_report
        self.top_n_eq = {}
        for _key, _ in self.ranked[:_top_n]:
            _cidx = self.key_to_idx[_key]
            _nav = (
                nav_daily_df[nav_daily_df["combo_idx"] == _cidx]
                if nav_daily_df is not None and not nav_daily_df.empty
                else None
            )
            _df_c = (
                df[df["combo_idx"] == _cidx]
                if df is not None and not df.empty
                else None
            )
            _eq = equity_metrics(
                _df_c,
                capital=self.account_size,
                nav_daily_combo=_nav,
                date_from=_d_from,
                date_to=_d_to,
            )
            if _eq is not None:
                self.top_n_eq[_key] = _eq

        # best_eq is an alias — backwards compatible with existing callers
        self.best_eq = self.top_n_eq.get(self.best_key)

        # Pre-build fan chart data so reporting_v2 reads ready-to-plot lists
        self.fan_curves = []
        self.fan_dates = []
        for _rank, (_key, _stats) in enumerate(self.ranked[:_top_n], 1):
            if _key not in self.top_n_eq:
                continue
            _daily = self.top_n_eq[_key]["daily"]
            if not _daily:
                continue
            if not self.fan_dates:
                self.fan_dates = [row[0] for row in _daily]
            _vals = [row[5] for row in _daily]   # close (index 5 of 6-tuple)
            _params = dict(_key)
            _label = " | ".join(f"{p}={_params[p]}" for p in self.param_names)
            _tooltip = f"#{_rank}  {_label}  \u2192  ${float(_stats['total_pnl']):+,.0f}"
            self.fan_curves.append((_rank, float(_stats["total_pnl"]), _vals, _tooltip))

        self.best_final_nav = None
        if (final_nav_df is not None and not final_nav_df.empty
                and self.best_combo_idx is not None):
            row = final_nav_df[final_nav_df["combo_idx"] == self.best_combo_idx]
            if not row.empty:
                self.best_final_nav = float(row.iloc[0]["final_nav"])

        # Step 4: grid-wide robustness statistics
        _rob = _robustness_stats(self.all_stats, keys, param_grid)
        self.pnl_all          = _rob["pnl_all"]
        self.pct_profitable   = _rob["pct_profitable"]
        self.median_pnl       = _rob["median_pnl"]
        self.p10_pnl          = _rob["p10_pnl"]
        self.p90_pnl          = _rob["p90_pnl"]
        self.pnl_iqr          = _rob["pnl_iqr"]
        self.fragility_score  = _rob["fragility_score"]
        self.param_sensitivity = _rob["param_sensitivity"]
        self.monotonicity     = _rob["monotonicity"]
        self.heatmap_pairs    = _rob["heatmap_pairs"]
