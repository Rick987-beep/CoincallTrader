# Backtester V2

Options backtester using real historic Deribit prices from Tardis. Replays 5-minute option snapshots + 1-minute BTC spot bars, evaluates parameter grids across strategies, and generates self-contained HTML reports.

## Architecture

```
Raw Tardis ticks (13 GB)
    │  snapshot_builder.py  (run once)
    ▼
Option snapshots (5-min, parquet) + Spot track (1-min OHLC)
    │  market_replay.py  (load into RAM)
    ▼
MarketState iterator → engine.py (single-pass multi-combo grid)
    │                       │
    ▼                       ▼
Strategy.on_market_state()  →  List[Trade]  →  reporting_v2.py  →  HTML
```

## Modules

| File | Purpose |
|---|---|
| `snapshot_builder.py` | One-time conversion: raw tick parquets → 5-min option snapshots + 1-min spot OHLC |
| `market_replay.py` | Loads snapshots into memory, iterates `MarketState` objects with option chain + spot data |
| `strategy_base.py` | `Trade`/`OpenPosition` dataclasses, `Strategy` protocol, composable entry/exit conditions |
| `engine.py` | Single-pass grid runner: evaluates all parameter combos in one data scan |
| `reporting_v2.py` | Strategy-agnostic HTML report: best combo, top 20, heatmaps, equity curve, trade log |
| `run.py` | CLI entry point |
| `pricing.py` | Black-Scholes model, Deribit fee calculation |
| `metrics.py` | Stats, equity curves, Sharpe/Sortino/Calmar scoring |

## Strategies

| Strategy | Class | Combos | Description |
|---|---|---|---|
| `straddle` | `ExtrusionStraddleStrangle` | 840 | Buy 0DTE ATM straddle/OTM strangle, exit on BTC index move |
| `put_sell` | `DailyPutSell` | 20 | Sell 1DTE OTM put, exit on stop-loss or expiry |

## Quick Start

### 1. Build snapshots (one-time, ~2 min)

Requires raw Tardis parquets in `analysis/tardis_options/data/`.

```bash
python -m backtester2.snapshot_builder
```

Output: `backtester2/snapshots/options_*.parquet` + `spot_track_*.parquet`

### 2. Run a backtest

```bash
# Straddle (840 combos, ~20s)
python -m backtester2.run --strategy straddle

# Put sell (20 combos, ~5s)
python -m backtester2.run --strategy put_sell

# Custom output path
python -m backtester2.run --strategy straddle --output my_report.html
```

### 3. View the report

Open the generated HTML file in a browser. Sections include:
- **Best combo** with sparkline equity curve
- **Top 20 combos** ranked by total PnL
- **Heatmaps** for every 2D parameter pair (auto-generated)
- **Daily equity** table with drawdown metrics
- **Trade log** for the best combo

## Adding a New Strategy

1. Create `strategies/my_strategy.py` implementing the `Strategy` protocol:
   - `configure(params)` — set parameters
   - `on_market_state(state) → List[Trade]` — process each 5-min tick
   - `on_end(state) → List[Trade]` — force-close at end of data
   - `reset()` — clear state between grid runs
   - `describe_params() → dict`
   - Class attributes: `name: str`, `PARAM_GRID: dict`

2. Register in `run.py`:
   ```python
   from backtester2.strategies.my_strategy import MyStrategy
   STRATEGIES["my_strat"] = MyStrategy
   ```

3. Run: `python -m backtester2.run --strategy my_strat`

## Data Format

- **Option prices:** BTC-denominated (0.0001–4.13 range), converted to USD via `price × spot`
- **Entry pricing:** Buy at ask, sell at bid (worst fill — conservative)
- **Fees:** Deribit model: `MIN(0.03% × index, 12.5% × option_price)` per leg
- **Spot data:** 0 NaN, 0 zero values across all timestamps

## Performance

On M1 Mac (15 days of data, 4,310 intervals):

| Strategy | Combos | Trades | Time |
|---|---|---|---|
| Straddle | 840 | 50,025 | ~21s |
| Put Sell | 20 | 160 | ~5s |

## Requirements

Python 3.9+. Dependencies: `pandas`, `numpy`, `pyarrow`.
