# Backtester Scoring & Equity Curve Upgrade

**Date:** 2026-04-11  
**Status:** Implemented (2026-04-11)

---

## Background — current state

The engine (`engine.py`) runs a single pass over all parameter combos simultaneously. It already tracks **daily NAV** with `nav_low / nav_high / nav_close` per combo, stored in a `nav_daily_df` DataFrame. This flows into `results.py` which:

1. Computes **per-combo stats** vectorised with pandas groupby (`_all_combo_stats`).
2. Computes a **weighted percentile-rank composite score** (`_score_combos`).
3. Computes detailed **equity metrics** for the best combo only (`equity_metrics`).

Current scoring weights (config.toml):

| Metric | Weight | Direction |
|---|---|---|
| Sharpe (annualised) | 0.30 | higher |
| Total PnL | 0.25 | higher |
| Max drawdown % (EOD) | 0.20 | lower |
| Max drawdown duration (days) | 0.15 | lower |
| Profit factor | 0.10 | higher |

The code already computes `max_intraday_dd_pct` but it is **informational only** and not used in scoring.  
`max_dd_pct` (EOD close-based) is used for scoring.

---

## Problems identified

### P1 — Two overlapping drawdown measures
- `max_dd_pct` (EOD close vs running peak): used in scoring and reporting.
- `max_intraday_dd_pct` (daily low vs running peak): tracked but not used in scoring.
- Keeping both creates confusion about which "drawdown" is authoritative.
- **Decision:** **retire `max_dd_pct` (EOD) entirely; promote `max_intraday_dd_pct` as the single drawdown measure** for both scoring and reporting. The intraday measure is strictly ≥ EOD drawdown and more conservative and realistic.

### P2 — Equity curves built but not fully exploited
- `nav_daily_df` is already in memory for the run's lifetime and used for scoring. `nav_high` is tracked by the engine but never read downstream.
- **Memory** (worst case 10k combos × 400 days): NAV DataFrame ~101 MB (float32) + trade log ~240 MB = ~341 MB total. No mandatory persistence needed.

### P3 — Scoring model is too crude for identifying "beautiful" equity curves
- Total PnL reward: a combo that does nothing and wins big on day 399 scores the same as one that grows steadily.
- Sharpe is necessary but not sufficient: one giant drawdown followed by recovery can yield a high Sharpe if most other days are positive.
- Missing: measures of **consistency**, **linearity of growth**, and **duration of drawdowns**.

### P4 — `equity_metrics()` only runs for the best combo
- Sortino, Calmar, full daily curve, consecutive streaks: only computed for the winner. The leaderboard top-20 get only basic stats.

---

## Current state of reporting_v2.py

`generate_html(strategy_name, result, ...)` receives a `GridResult` and renders HTML. It mostly reads pre-computed attributes (`result.all_stats`, `result.scores`, `result.ranked`, `result.best_eq`) — this is the right pattern. However there is one significant violation:

**`_build_fan_curves()`** lives in `reporting_v2.py` and does real analysis:
- Accepts `nav_daily_df`, `df`, `keys` raw data
- Re-pivots `nav_daily_df` to rebuild equity curves for top-20 combos
- Falls back to re-bucketing the trade log if no NAV data

This is analysis work that belongs in `results.py`. The reporting module should receive ready-to-plot data, not rebuild it from raw inputs.

Other reporting concerns for this upgrade:
- The leaderboard table columns are hardcoded for the old metrics (`max_dd_pct`, `max_intraday_dd_pct`, `max_dd_days`, `sharpe`, `pf`). After P1 and P3 they will be wrong.
- The scoring weight caption in the leaderboard is hardcoded with the old config key names (`w_sharpe`, `w_pnl`, `w_max_dd`, `w_dd_days`). After new metrics are added this needs rebuilding dynamically from `cfg.scoring`.
- The best-combo box and risk summary bar reference `best_eq["max_dd_pct"]` and `best_eq["max_intraday_dd_pct"]` — two fields that collapse to one after P1, and four new fields to add after P3.
- `reporting_v2.py` imports `equity_metrics` from `results.py` but never calls it. The import is dead weight.

**Target state:** `reporting_v2.py` does **zero analysis or computation**. It only renders what `GridResult` provides. The `generate_html()` function's only inputs are `GridResult` + cosmetic args (strategy name, description, qty).

---

## The pipeline — how it works after each run

After the engine finishes, `GridResult.__init__` executes three sequential steps. Understanding which step does what is essential for the implementation.

```
Engine run
  └─→ nav_daily_df  (ALL combos × ALL days, low/high/close)   [in memory]
  └─→ trade log df  (ALL combos × ALL trades)                  [in memory]
            │
            ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │ STEP 1 — Vectorised metrics for ALL combos                          │
  │ _all_combo_stats()                                                  │
  │                                                                     │
  │ Pivots nav_close into a [days × combos] matrix, then applies        │
  │ pandas / numpy operations across all columns at once.               │
  │                                                                     │
  │ Output: one stats dict per combo (all_stats)                        │
  └─────────────────────────────────────────────────────────────────────┘
            │
            ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │ STEP 2 — Score and rank ALL combos                                  │
  │ _score_combos()                                                     │
  │                                                                     │
  │ Percentile-ranks each metric across eligible combos,                │
  │ applies weights → composite score (0→1).                            │
  │                                                                     │
  │ Output: scores dict + ranked list                                   │
  └─────────────────────────────────────────────────────────────────────┘
            │
            ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │ STEP 3 — Detailed equity metrics for top-20 only                    │
  │ equity_metrics() × top-20                                           │
  │                                                                     │
  │ Pure-Python loop per combo, produces the full daily curve tuple     │
  │ (date, pnl, cum, high, low, close) plus Sortino, Calmar,            │
  │ consecutive streaks. Used by reporting only — NOT used for scoring. │
  │                                                                     │
  │ Output: top_n_eq dict (key → equity_metrics result)                 │
  └─────────────────────────────────────────────────────────────────────┘
            │
            │  GridResult is now complete.
            │  ALL data is pre-computed. reporting_v2 receives this
            │  and only renders — it does zero analysis.
            ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │ REPORTING — generate_html(result, ...)                              │
  │ reporting_v2.py                                                     │
  │                                                                     │
  │ Reads: result.all_stats, result.scores, result.ranked,             │
  │        result.best_eq, result.top_n_eq                              │
  │ Renders: risk summary, best-combo box, leaderboard table,          │
  │          fan chart (from top_n_eq curves), heatmaps, trade log     │
  │                                                                     │
  │ Does NOT touch: nav_daily_df, df, keys, equity_metrics()           │
  └─────────────────────────────────────────────────────────────────────┘
```

**Key rule:** any metric used for scoring MUST be in Step 1 (vectorised, all combos). Step 3 is reporting detail only. This is why the new metrics below (R², Omega, Ulcer Index, monthly consistency) belong in Step 1, not Step 3.

---

## Requirements

### R1 — Single drawdown measure
- Remove `max_dd_pct` (EOD-close-based) from all stats and display. It is strictly weaker than the intraday measure.
- `max_intraday_dd_pct` becomes the one drawdown measure. Rename it to `max_dd_pct` throughout for clarity (drop the "intraday" qualifier now that it is unambiguous). Its scoring weight (`w_max_dd`) is **kept** — 0.15 in the final model (R4).
- `max_dd_days` is **retired** from both scoring and display — its job is taken over by Ulcer Index (R3), which captures duration more richly. Remove `w_dd_days` config key.
- **`max_dd_pct` and `total_pnl` are permanent scoring fields and permanent display fields.** They appear in the leaderboard table and best-combo box regardless of their weight value.
- Reporting: update HTML report labels — remove EOD drawdown row, relabel intraday drawdown row as "Max DD".

### R2 — Equity curve memory efficiency + nav_high
The engine already builds `nav_daily_df` in memory; it is the input to Step 1. No structural changes are needed. Two small improvements:

- **float32 for NAV columns**: cast at DataFrame construction in `engine.py`. Worst-case footprint (10k combos × 400 days) drops from ~149 MB to ~101 MB. The scoring pivot operates in float64 anyway (numpy upcasts automatically); this is a storage-only saving.
- **nav_high is unused**: the engine already tracks `nav_high` per day but nothing in Step 1 or Step 3 reads it. Add it to:
  - Step 1: available for future metrics (not yet used in scoring).
  - Step 3: `equity_metrics()` daily output tuple extended to `(date, pnl, cum, high, low, close)`.
  - `reporting_v2.py`: shade the intraday high/low band on the equity curve chart.

**Persistence**: keep `nav_daily_df` in memory as-is. Add an optional `--save-nav` CLI flag that writes a Parquet file *after* report generation for post-hoc analysis. This is not on the critical path.

### R3 — New scoring metrics (Step 1, ALL combos, vectorised)

All new metrics are computed from the `nav_close` pivot matrix — the same matrix already used for Sharpe and drawdown. No extra engine work. All run inside `_all_combo_stats()`.

#### Metrics that catch "beautiful curve" failures

| Pitfall | Fools naïve scorer because… | Metric that catches it |
|---|---|---|
| **Sleeping Giant** | flat for 390 days, one huge spike | R² (non-linear shape) + monthly consistency |
| **One-Disaster** | great most of the time, one catastrophic week | Ulcer Index, Omega |
| **Lucky Streak** | strong start, slow decay in second half | R² (slope reversal) + monthly consistency |
| **Noisy Grinder** | positive mean but wild daily swings | Sharpe (kept) + R² |

**R² — linearity of the equity curve**  
Fit a straight line through the equity curve (day index → nav_close). R² measures how closely the curve tracks that line.  
- R² = 1.0 → perfectly straight upward slope.  
- Catches Sleeping Giant (R² ≈ 0 if curve is flat then spikes), Lucky Streak (R² drops if curve bends back down), and Noisy Grinder (low R² from oscillation).  
- Computed vectorised: `np.polyfit` or direct formula applied column-wise on the pivot.

**Omega Ratio (Gain-to-Pain)**  
$$\Omega = \frac{\sum \max(r_i, 0)}{\sum \max(-r_i, 0)}$$  
Uses the full return distribution, not just mean and std. One catastrophic day hurts Omega far more than Sharpe. Equivalent to `gain_to_pain_ratio` in QuantStats.

**Ulcer Index**  
$$UI = \sqrt{\frac{1}{N}\sum_{i=1}^{N} D_i^2}$$  
where $D_i$ is the % drawdown at day $i$ vs running high watermark.  
Squares every underwater day — 2 months at −10% costs 60× more than 1 day at −10%. Replaces both `max_dd_pct` (old EOD) and `max_dd_days` with a single, richer risk measure. Lower is better.  
Computed vectorised: `cummax` then squared-distance on the pivot, then column-wise mean and sqrt.

**Monthly consistency (%)**  
Group daily nav_close by calendar month. Count the fraction of months that ended above where they started. Sleeping Giant: 1 profitable month out of 13. Lucky Streak: strong first half, negative second half months.  
Computed vectorised: resample pivot to month-end, diff, count positive fraction per column.

#### What is NOT added to scoring
- **Martin Ratio** (CAGR / Ulcer Index): Calmar already exists in `equity_metrics()` (Step 3). Martin Ratio is the Ulcer-based analogue; it is informational but redundant with having Ulcer Index and total PnL both in the score.
- **Tail Ratio**: informational, useful for reporting detail (Step 3), not scoring.

### R4 — Revised scoring model (Step 2)

Proposed weights (configurable in `config.toml`):

| Metric | Weight | Direction | Note |
|---|---|---|---|
| R² (linearity) | 0.15 | higher | new |
| Sharpe | 0.15 | higher | reduced from 0.30 |
| Total PnL | 0.15 | higher | reduced from 0.25, kept |
| Max DD % | 0.15 | lower | kept; now uses intraday measure |
| Omega / Gain-to-Pain | 0.10 | higher | new |
| Ulcer Index | 0.10 | lower | new; replaces dd_days only |
| Monthly consistency % | 0.10 | higher | new |
| Profit factor | 0.10 | higher | unchanged |

**Sum: 1.00**

Rationale:
- Total PnL stays at a meaningful weight (0.15). A strategy that earns nothing should not rank highly regardless of how smooth its flat curve is.
- Max DD % stays at a meaningful weight (0.15), now using the intraday measure which is strictly more conservative. A human wants to know the strategy survived without blowing up, and that should be in the score.
- Ulcer Index (0.10) *supplements* max DD — it penalises prolonged recovery time, which max DD alone misses. Both are in the score, they are complementary not redundant.
- Sharpe and PnL weights are both reduced to make room for the new metrics, but neither is eliminated.
- R² gets the same weight as Sharpe and PnL because it is the single strongest guard against the "beautiful curve" failure modes.
- Profit factor stays at 0.10 — useful signal but partially overlaps with Omega.

### R5 — Step 3: top-N detail (was "equity_metrics for best only")
- Currently `equity_metrics()` runs only for the single best combo.
- After ranking in Step 2, run it for **top-20** (configurable via `cfg.simulation.top_n_report`, a new config key — distinct from `top_n_console = 5` which controls terminal print volume).
- Store as `GridResult.top_n_eq: dict[key → equity_metrics_result]`.
- `reporting_v2.py`: use for the leaderboard table (Sortino, Calmar, consec streaks, full daily curve for charting).
- Cost: 20 × 400 = 8,000 iterations in the pure-Python loop — negligible.

---

## Implementation phases

### Phase 1 — Drawdown unification (R1) ✅ IMPLEMENTED
- `results.py`: remove `max_dd_pct` (EOD) + its peak tracker from `_all_combo_stats()` and `equity_metrics()`. Rename `max_intraday_dd_pct` → `max_dd_pct`. Remove `max_dd_days` computation.
- `config.toml` / `config.py`: rename `w_max_dd` → stays as `w_max_dd` (now points to the intraday measure, value unchanged at 0.20 temporarily); remove `w_dd_days` (0.15); add temporary `w_pnl = 0.40` (absorbs the freed 0.15 to keep weights summing to 1.0 until Phase 3 redistributes across the new metrics).
- `reporting_v2.py`: remove EOD drawdown row; relabel intraday row as "Max DD"; remove DD Days row.
- No test files reference these metrics; no test changes needed in Phase 1.

### Phase 2 — nav_high wired through (R2) ✅ IMPLEMENTED
- `engine.py`: confirm NAV columns cast to `float32` at DataFrame build.
- `results.py` `equity_metrics()`: extend daily tuple to `(date, pnl, cum, high, low, close)` (was 4-tuple, becomes 6-tuple).
- `reporting_v2.py` `_equity_chart_svg()`: update to accept 6-tuple; add intraday high/low shaded band. *(This is the only place this function is touched — Phase 5 does not revisit it.)*
- `run.py`: add optional `--save-nav` flag.

### Phase 3 — New scoring metrics (R3, R4) ✅ IMPLEMENTED
- `results.py` `_all_combo_stats()`: add vectorised computation of R², Omega, Ulcer Index, monthly consistency on the `nav_close` pivot.
- `_score_combos()`: add new metrics to the percentile-rank + weight block; update Ulcer Index to use `(1 − rank)` inversion (lower = better, same pattern as `max_dd_pct`).
- `config.toml` / `config.py` `ScoringConfig`: add `w_r2`, `w_omega`, `w_ulcer`, `w_consistency`; set final weights per R4 table (R² 0.15, Sharpe 0.15, PnL 0.15, Max DD 0.15, Omega 0.10, Ulcer 0.10, Consistency 0.10, PF 0.10); remove the temporary `w_pnl = 0.40` from Phase 1; `w_max_dd` value drops from 0.20 → 0.15. Remove `w_dd_days` (already gone since Phase 1).

### Phase 4 — Step 3 top-N (R5) ✅ IMPLEMENTED
- `GridResult.__init__`: after Step 2 ranking, loop `equity_metrics()` over top-20 combos (top-N count: use a dedicated `cfg.simulation.top_n_report` config key, default 20 — distinct from `top_n_console = 5` which controls terminal printing).
- Store as `self.top_n_eq: dict[key → equity_metrics_result]`.
- **`best_eq` becomes an alias**: `self.best_eq = self.top_n_eq[self.best_key]`. It is no longer computed separately. The `GridResult` attribute is kept for backwards compatibility with any callers.
- **Move `_build_fan_curves()` logic into `GridResult`**: build `self.fan_curves` (list of `(rank, total_pnl, eq_values, tooltip_label)`) and `self.fan_dates` (shared x-axis) directly from `top_n_eq` daily curves — using the `eq` (NAV close) values from each combo's daily tuple. The reporting module will read these; it will no longer touch `nav_daily_df` or the raw trade log.

### Phase 5 — Reporting module cleanup ✅ IMPLEMENTED
With `GridResult` now fully self-contained, strip all analysis from `reporting_v2.py`:

- **Remove `_build_fan_curves()`** — replaced by `result.fan_curves` / `result.fan_dates`.
- **Remove `equity_metrics` import** — never called in reporting; dead import.
- **`generate_html()` signature**: remove any remaining raw data pass-throughs (`nav_daily_df`, `df`, `keys`); it receives only `GridResult` + cosmetic args (`n_intervals`, `runtime_s`, `strategy_description`, `qty`, `heatmap_pairs`).
- **`_equity_chart_svg()`**: already updated in Phase 2. No further changes here.
- **Leaderboard table**: the `max_dd_pct` (EOD) column was already removed in Phase 1; `max_dd_days` column is removed here. **Keep `max_dd_pct` (the renamed intraday measure) and `total_pnl` as permanent display columns** — these are always shown regardless of whether they carry scoring weight. Add `r2`, `omega`, `ulcer_index`, `consistency_pct` from `all_stats`; add `sortino`, `calmar` from `top_n_eq` where available.
- **Scoring weight caption**: replace hardcoded weight strings with a dynamic loop over `cfg.scoring` weight fields — so it can never drift out of sync with the config.
- **Best-combo box + risk summary bar**: single drawdown field (`max_dd_pct`); add display rows for R², Omega, Ulcer Index, monthly consistency from `best_eq`.

After Phase 5, `reporting_v2.py` accesses **only** `GridResult` public attributes. It imports nothing from `results.py` except `GridResult`.

---

## Open questions / decisions needed

1. **Rename `max_intraday_dd_pct` → `max_dd_pct`**: plan proceeds with this rename. Blast radius is `results.py`, `reporting_v2.py`, `run.py`. Confirm.
2. **float32 precision**: float32 on a $10,000 NAV resolves to ~$0.01. Sufficient for scoring. No downstream code path reads NAV at sub-cent precision.
3. **Scoring weights**: the proposed weights (R² 0.15, Sharpe 0.15, PnL 0.15, Max DD 0.15, Omega 0.10, Ulcer 0.10, Consistency 0.10, PF 0.10) are a starting point. After Phase 3, run the new scorer against a known backtest and compare old vs new top-20 as a sanity check before locking in.
4. **Phases 1 and 3 tight coupling**: Phase 1 uses `w_pnl = 0.40` as a temporary placeholder. If Phases 1 and 3 are done in the same session, skip the placeholder — just go straight to the final Phase 3 weights. Only separate them if Phase 1 is deployed independently.
5. **`n_intervals` in `generate_html()`**: stays as a cosmetic pass-through from `run.py`. Not analysis. No change needed.

