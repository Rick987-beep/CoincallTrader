# Backtester Robustness Testing

> **Status as of April 2026** — Items 1–3 below are **fully implemented** and
> merged into the main backtester pipeline. Items 4–7 remain planned.

## The Problem

Running a parameter grid search and picking the best result is **statistically dangerous**.
With enough combos, you will find a "best" configuration by pure chance — the strategy has no
real edge, just lucky alignment with the historical sample. This is variously called data
snooping, overfitting, backtest overfitting, or multiple-testing bias.

**Key research:**

- **Bailey, Borwein, López de Prado, Zhu (2014)** — *"The Probability of Backtest Overfitting"*
  [(paper)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253).
  Introduces CSCV (Combinatorially Symmetric Cross-Validation) and derives the
  *Probability of Backtest Overfitting* (PBO). For a grid of N combos, PBO rises
  sharply with N. At 100+ combos PBO can easily exceed 50% — meaning you're more
  likely to have mined noise than found signal.

- **Harvey, Liu, Zhu (2015)** — *"… and the Cross-Section of Expected Returns"*
  [(paper)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2249314).
  When you test N strategies/combos, the t-statistic required for significance at 5%
  rises from 1.96 toward 3.0+ (Bonferroni) or ~2.8 (Holm-Bonferroni). Our grid of
  4,480 combos requires t ≈ 4.5+ for 5% family-wise significance. We never compute
  this.

- **López de Prado (2018)** — *Advances in Financial Machine Learning*, Chapters 11–14.
  Deflated Sharpe Ratio (DSR), CSCV, and combinatorial purged cross-validation
  (CPCV) are the practical tools. A Sharpe that looks good but was selected from
  many trials is a biased estimate; DSR corrects for the number of trials.

- **Practical rule of thumb:** a strategy needs roughly as many independent
  out-of-sample data points as it has free parameters. Combo #14 has 8 free
  parameters. It was evaluated on 64 trades across 90 days. That is marginal.

---

## Current State

The backtester computes per-combo:

| Metric | Status |
|--------|--------|
| Sharpe (annualised, NAV-based) | ✅ |
| Max drawdown % | ✅ |
| Ulcer index | ✅ |
| R² (equity vs trend) | ✅ |
| Omega ratio | ✅ |
| Profit factor | ✅ |
| Monthly consistency | ✅ |
| Win rate, avg win/loss | ✅ |
| Composite percentile score | ✅ |
| Heatmaps (2D param pairs) | ✅ |
| **Deflated Sharpe Ratio** | ✅ **Done** |
| **Walk-forward OOS validation** | ✅ **Done** |
| **Experiment pipeline (Discovery → Sensitivity → WFO)** | ✅ **Done** |
| **Monte Carlo permutation test** | ❌ |
| **Regime segmentation** | ❌ |
| **CSCV / PBO** | ❌ |
| **Out-of-time test (2024–2025 data)** | ❌ |

---

## Ideas and Implementation Plan

Each item is rated: **[effort: S/M/L]** and **[signal value: low/medium/high]**.

---

### 1. Parameter Sensitivity (±% perturbation) ✅ DONE
**Effort: S | Signal value: high | Priority: 1**

#### What it tests
Whether the best combo sits on a smooth hill or an isolated spike. A spike means
the chosen values happen to look good but performance degrades sharply the moment
you deviate slightly. A hill means the general region is profitable — a more honest signal.

#### What was built
The sensitivity concept is now fully decoupled from the strategy definition and
lives in **experiment TOML files** (`backtester/experiments/`). Each experiment
captures a known-good parameter set and per-parameter deviation rules:

```toml
# backtester/experiments/delta_strangle_tp_v1.toml
[sensitivity.best]
delta = 0.15
entry_hour = 18
stop_loss_pct = 5.0
take_profit_pct = 0.80

[sensitivity.deviation.delta]
type = "pct"    # ± 10% of 0.15 → [0.135, 0.143, 0.15, 0.158, 0.165]
amount = 10

[sensitivity.deviation.entry_hour]
type = "abs"    # ± 2 hours (not %, because time is not ratio-scale)
amount = 2      # → [16, 17, 18, 19, 20]
```

Deviation types:
- `"pct"` — ±N% of the best value, evenly distributed over `steps` points
- `"abs"` — ±N in natural units (required for time/integer params)
- `"fixed"` — hold constant (disabled params, binary flags)

`backtester/experiment.py` loads the TOML and exposes `Experiment.build_sensitivity_grid()`
which produces a full `{param: [values]}` dict ready to pass directly to the engine.

Run with:
```bash
python -m backtester.run --experiment delta_strangle_tp_v1 --mode sensitivity
```
This auto-enables `--robustness` (shows sensitivity heatmaps and the all-combos table).

#### Files added / changed
- `backtester/experiment.py` — `DeviationSpec`, `Experiment`, `_build_range`, `load_experiment`
- `backtester/experiments/delta_strangle_tp_v1.toml` — first experiment file
- `backtester/run.py` — `--experiment` + `--mode` flags

---

### 2. Deflated Sharpe Ratio (DSR) ✅ DONE
**Effort: S | Signal value: high | Priority: 2**

#### What it tests
The standard Sharpe is inflated when selected from many trials. DSR adjusts for:
- The number of strategy combinations tested (`n_trials`)
- Non-normality of returns (skewness, kurtosis)

Formula (Bailey & López de Prado, 2014):

$$\text{DSR}(\hat{SR}^*) = \Phi\!\left(\frac{(\hat{SR}^* - E[\hat{SR}^{(k)}_{max}])\sqrt{T-1}}{\sqrt{1 - \gamma_3 \hat{SR}^* + \frac{\gamma_4-1}{4}\hat{SR}^{*2}}}\right)$$

The output is a probability: DSR = 0.95 means there's a 95% chance the Sharpe is
genuinely positive after correcting for the number of trials. DSR < 0.5 is
more likely noise than signal.

#### What was built
`deflated_sharpe_ratio(pnl_list, capital, n_trials)` lives in `backtester/robustness.py`.
It takes raw trade PnL values (not a pre-computed Sharpe) and derives Sharpe,
skewness and kurtosis from the sample, then applies the DSR correction.

`GridResult` computes `self.dsr` for the best combo in its `__init__`. The HTML
report shows it as a card in the best-combo robustness section.

#### Files added / changed
- `backtester/robustness.py` — `deflated_sharpe_ratio()`, `_robustness_stats()`
- `backtester/results.py` — `self.dsr` on `GridResult`
- `backtester/reporting_v2.py` — DSR metric card in best-combo section

---

### 3. Walk-Forward Validation (WFO) ✅ DONE
**Effort: M | Signal value: very high | Priority: 3**

#### What it tests
Whether the chosen parameters generalise to unseen time periods. The most honest
test because it mimics actual deployment: optimise on the past, trade the future,
do not look ahead.

#### Mechanics
Rolling windows over the full date range:

```
Window 1:  IS [Jan 11 – Feb 28]  →  best combo  →  OOS [Mar 01 – Mar 21]
Window 2:  IS [Jan 25 – Mar 14]  →  best combo  →  OOS [Mar 22 – Apr 04]
Window 3:  IS [Feb 08 – Mar 28]  →  best combo  →  OOS [Apr 05 – Apr 10]
```

For each window: run `PARAM_GRID` on IS → pick best combo → freeze params → run OOS.

#### What was built
`backtester/walk_forward.py` exposes `run_walk_forward()` which returns a `WFOResult`
dataclass (list of `WFOWindow` objects + aggregate stats).

WFO params (IS days, OOS days, step days) are defined in the experiment TOML:

```toml
[wfo]
is_days   = 45
oos_days  = 15
step_days = 15
```

The strategy's `PARAM_GRID` is now a **wide discovery grid** (600 combos for
`delta_strangle_tp`) so the IS optimiser has a real search space — not a narrow
post-hoc sensitivity grid that makes OOS look artificially good.

Run with:
```bash
python -m backtester.run --experiment delta_strangle_tp_v1 --mode wfo
```

Or with the legacy flags (backward-compatible):
```bash
python -m backtester.run --strategy delta_strangle_tp --wfo --is-days 45 --oos-days 15
```

First real WFO run result (Apr 2026): 3 windows, 1/3 profitable OOS,
OOS total PnL +$633, avg OOS Sharpe 6.34.

#### Files added / changed
- `backtester/walk_forward.py` — `WFOWindow`, `WFOResult`, `run_walk_forward()`
- `backtester/run.py` — `--wfo`, `--is-days`, `--oos-days`, `--step-days` (all modes)
- `backtester/reporting_v2.py` — `_wfo_section_html()`, `wfo_result=` param in `generate_html()`
- `backtester/strategies/short_strangle_delta_tp.py` — `PARAM_GRID` replaced with wide discovery grid (600 combos)

---

### The Three-Step Pipeline

The three items above compose into a clean research workflow:

```
Step 1 — Discovery
  python -m backtester.run --strategy delta_strangle_tp
  → Wide PARAM_GRID (600 combos), find the best-performing region
  → Pick a candidate: e.g. delta=0.15, entry_hour=18, SL=5.0, TP=0.80

Step 2 — Sensitivity
  python -m backtester.run --experiment delta_strangle_tp_v1 --mode sensitivity
  → Narrow grid centred on candidate (±10%/±2h), 5 steps per param
  → Robustness heatmaps auto-generated. Smooth hill = ok. Spike = suspect.
  → DSR shown automatically.

Step 3 — Walk-Forward
  python -m backtester.run --experiment delta_strangle_tp_v1 --mode wfo
  → IS uses wide PARAM_GRID (honest search); OOS is truly unseen
  → Checks whether the general region remains profitable on future data
```

Strategy files stay clean (one `PARAM_GRID` = wide honest discovery).
Experiment files capture the "what we think is good and why" separately.

---

### 4. Monte Carlo Permutation Test
**Effort: M | Signal value: high | Priority: 4**

#### What it tests
Whether the observed PnL could have been generated by chance from a random
entry process. This directly attacks the question "does the entry timing matter,
or would any random entry have done as well?"

#### Mechanics
1. Take the 64 actual trades produced by combo #14.
2. Keep all exit prices and hold durations exactly as-is.
3. Randomly shuffle the **entry timestamps** (within valid trading hours/days).
4. Recompute total PnL for this shuffled sequence.
5. Repeat 10,000 times → build a null distribution of PnL.
6. p-value = fraction of shuffled PnLs that beat the real PnL.

If p < 0.05 the real entry timing contributes meaningfully to the edge. If p > 0.20
the strategy is earning its returns from something other than entry timing (e.g.
selling vol premium during a calm IV regime — which is still potentially real, but
could also be regime-specific luck).

Note: a complementary permutation shuffles the **PnL values themselves** (not
timestamps) to test whether the win/loss distribution is unusual vs random Bernoulli.

#### Implementation
Add `backtester/montecarlo.py`:

```python
def permutation_test(trades_df, n_simulations=10_000, seed=42):
    """
    Shuffle entry_date labels n times, compute PnL each time.
    Returns (real_pnl, null_pnls_array, p_value).
    """
    ...
```

Run from `run.py --mode montecarlo`. Output: histogram of null PnL distribution
with real PnL marked, p-value.

#### Files to add / change
- `backtester/montecarlo.py` (new)
- `backtester/run.py` — `--mode montecarlo`

---

### 5. Regime Segmentation
**Effort: M | Signal value: medium | Priority: 5**

#### What it tests
Whether the strategy has positive EV across different market conditions, or whether
its PnL is entirely concentrated in one specific regime (e.g. "only works in low-IV
calm weeks").

#### Regimes to segment by
- **IV regime** — rolling 7-day average of option IV vs median IV over full sample.
  High IV = above median; Low IV = below median.
- **Trend regime** — rolling 7-day BTC return. Up > +5% = uptrend, Down < -5% =
  downtrend, else sideways.
- **Day of week** — check if edge is specific to certain weekdays.

#### Implementation
Add regime labels to the trade DataFrame in `engine.py` (tag each trade at entry
with current IV percentile and trend state from spot data). Add a regime breakdown
table to `reporting_v2.py`: PnL and win rate split by regime bucket.

#### Files to change
- `backtester/engine.py` — compute and tag `iv_regime`, `trend_regime` per trade
- `backtester/reporting_v2.py` — regime table section

---

### 6. CSCV — Combinatorially Symmetric Cross-Validation
**Effort: L | Signal value: very high | Priority: 6**

#### What it tests
The *Probability of Backtest Overfitting* (PBO). Directly answers: "given that this
combo ranked #1 in our grid, what is the probability it is genuinely better than
all other combos, vs being a lucky winner from the multiple-testing lottery?"

#### Mechanics (Bailey et al. 2014)
1. Split trades into S = 8 subsets (date ranges).
2. For each combination of S/2 subsets (there are C(8,4) = 70 such combos):
   - Rank all combos by Sharpe on the "in-sample" 4 subsets
   - Apply the in-sample winner to the "out-of-sample" 4 subsets
   - Record the out-of-sample rank of the in-sample winner
3. Aggregate all 70 OOS relative ranks → logit-transform → PBO = fraction below
   the median rank (0.5 in logit space).
4. PBO < 0.1 is excellent. PBO > 0.5 means the optimisation is pathological.

#### Implementation
Add `backtester/cscv.py`. This is the most complex addition but is algorithmically
self-contained. Output: PBO scalar, OOS rank distribution histogram.

---

### 7. Out-of-Time Test (historical data extension)
**Effort: L (data acquisition) | Signal value: very high | Priority: 7**

#### What it tests
Whether combo #14 (frozen, no re-optimisation) was profitable in a completely
different historical period: 2024–2025.

#### Mechanics
1. Download 2024-Q1 → 2025-Q4 Tardis data (same format as current dataset).
2. Run combo #14 frozen on this data using the existing engine.
3. Compare: PnL, win rate, profit factor, drawdown.

If all three periods (2024, 2025, 2026 YTD) show positive EV with similar stats,
that is compelling evidence of a real edge. If only the 2026 backtest period is
positive, the strategy may be regime-specific.

#### Files to change
- `backtester/run.py` — `--oot` flag that runs frozen single combo on alternate
  date range
- No analysis code needed — existing engine handles it

---

## Summary Table

| # | Feature | Effort | Signal | Status |
|---|---------|--------|--------|--------|
| 1 | Parameter sensitivity + experiment pipeline | S | High | ✅ Done |
| 2 | Deflated Sharpe Ratio | S | High | ✅ Done |
| 3 | Walk-forward validation | M | Very high | ✅ Done |
| 4 | Monte Carlo permutation | M | High | ❌ Planned |
| 5 | Regime segmentation | M | Medium | ❌ Planned |
| 6 | CSCV / PBO | L | Very high | ❌ Planned |
| 7 | Out-of-time test | L | Very high | ❌ Needs data |

The practical "good enough" robustness check for deciding whether to continue
live-trading a strategy is items 1 + 2 + 3 — all now implemented. If all three
are favourable the strategy is not obviously overfit and the edge is plausible.
Add items 4 and 5 before significantly increasing size.
