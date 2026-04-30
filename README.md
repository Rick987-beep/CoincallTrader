# CryoTrader

Automated BTC options trading system with multi-exchange support (Coincall, Deribit).

**Version:** 1.14.0 | **Python:** 3.9+ | **VPS:** Hetzner CPX22, Ubuntu 24.04

---

## What it does

CryoTrader runs multi-leg BTC options strategies (puts, strangles, straddles) against live exchanges. It manages the full lifecycle of each trade — entry, execution, monitoring, exit — across one or more simultaneously running strategies. A separate backtesting subsystem lets you validate strategies on historical Deribit tick data before going live.

---

## Two Applications in One Repo

| | **Live Trading Engine** | **Backtester** |
|---|---|---|
| Root | `/` (top level) | `backtester/` |
| Entry | `main.py` | `backtester/run.py` |
| Strategies | `strategies/` | `backtester/strategies/` |
| Config | `config.py`, `.env` | `backtester/config.py`, `backtester/config.toml` |

Many modules share similar or identical names in both subsystems. Always confirm which application you are working in.

---

## Live Trading Engine

### Architecture

The engine runs a 10-second poll loop (`PositionMonitor`) that drives two parallel state machines:

- **StrategyRunner** — evaluates entry gates, resolves option legs, and creates new trades
- **LifecycleEngine** — advances every active trade through its state machine:

```
PENDING_OPEN → OPENING → OPEN → PENDING_CLOSE → CLOSING → CLOSED
                                                           └─► FAILED
```

Trades open **atomically** (all legs or none). Closes are **best-effort** (each leg independently, retried each tick). A circuit breaker at 10 failed close attempts marks the trade FAILED and fires a manual intervention alert.

### Exchanges

| Exchange | Auth | Notes |
|----------|------|-------|
| Coincall | HMAC-SHA256 | Default; RFQ for large notional |
| Deribit | OAuth2 client_credentials | Full implementation; JSON-RPC 2.0; USD↔BTC at adapter boundary |

### Execution Modes

| Mode | When | How |
|------|------|-----|
| `limit` | Default / Deribit | Per-leg limit orders; phased pricing (fair → bid+33% → bid → ask) |
| `rfq` | ≥$50k notional (Coincall) | Atomic multi-leg RFQ |
| `auto` | Default router | Selects limit or rfq by notional |

### Active Production Slots

| Slot | Strategy | Exchange | Key Params |
|------|----------|----------|------------|
| slot-01 | `put_sell_80dte` | Coincall | Sell OTM put, -0.15δ, ≥80 DTE, entry 13–14 UTC |
| slot-02 | `short_strangle_delta_tp` | Deribit | Sell strangle, 0.15δ, 1 DTE, entry 18 UTC, TP=80% |

### Key Modules

| File | Purpose |
|------|---------|
| `main.py` | Entry point; crash recovery; dynamic strategy import |
| `strategy.py` | `TradingContext`, `StrategyConfig`, `StrategyRunner`, condition factories |
| `lifecycle_engine.py` | Trade state machine; tick driver |
| `trade_lifecycle.py` | Data types: `TradeState`, `TradeLeg`, `TradeLifecycle`, PnL helpers |
| `execution/router.py` | Routes limit vs RFQ; best_effort close logic |
| `order_manager.py` | Order ledger; idempotent placement; reconciliation |
| `trade_execution.py` | `LimitFillManager`; phased execution |
| `option_selection.py` | `LegSpec`, `resolve_legs()`, `straddle()`, `strangle()` |
| `market_data.py` | Option chains, orderbooks, BTC price (30s cache) |
| `dashboard.py` | Flask+htmx per-slot dashboard; session auth |
| `logging_setup.py` | Three structured JSONL log tracks |
| `persistence.py` | Append-only trade history (`trade_history.jsonl`) |

---

## Strategies (`strategies/`)

All strategies use `_p("NAME", default)` → reads `PARAM_<NAME>` env vars. Register in `strategies/__init__.py`.

| File | Description |
|------|-------------|
| `put_sell_80dte.py` | Sell OTM put, -0.15δ, monthly expiry ≥80 DTE, EMA-20 filter |
| `short_strangle_delta_tp.py` | Sell OTM strangle at target delta; take-profit + min_otm_pct guard |
| `short_strangle_delta.py` | Delta-selected strangle; weekend filter |
| `daily_put_sell.py` | 1DTE -0.10δ put, 03–04 UTC (legacy) |
| `long_strangle_index_move.py` | Long ±$2000 OTM strangle; exit on $1500 BTC index move |
| `atm_straddle_index_move.py` | Long ATM straddle; exit on BTC move ≥$1200 or time |
| `blueprint_strangle.py` | Template for new strategies |

---

## Backtester (`backtester/`)

Real-data backtester for Deribit BTC options. Replays historical tick snapshots, runs parameter grids, and produces self-contained HTML reports.

### CLI

```bash
python -m backtester.run --strategy delta_strangle_tp
python -m backtester.run --strategy delta_strangle_tp --robustness --wfo
python -m backtester.run --experiment my_experiment
```

Strategy aliases: `straddle`, `put_sell`, `short_straddle`, `delta_strangle`, `delta_strangle_tp`, `deltaswipswap`, `deltaswipswap1m`, `weekly_strangle_tp`, `weekly_strangle_cap`, `weekend_strangle`

### Runtime Model

```
Load parquet snapshots
  → MarketReplay  (yields MarketState per 5-min interval)
  → run_grid_full()  (all parameter combos in one pass)
  → GridResult  (vectorised Sharpe, PnL, R², Omega, Ulcer, drawdown + composite score)
  → generate_html()  (self-contained HTML report)
```

### Key Files

| File | Purpose |
|------|---------|
| `backtester/engine.py` | Single-pass grid runner |
| `backtester/market_replay.py` | Fast parquet iterator |
| `backtester/results.py` | `GridResult`: scoring and equity metrics |
| `backtester/robustness.py` | Deflated Sharpe Ratio |
| `backtester/walk_forward.py` | Walk-forward optimisation |
| `backtester/reporting_v2.py` | HTML report generation |
| `backtester/experiment.py` | Sensitivity analysis (TOML-driven) |

### Data Ingestion

- **Tick recorder** (`backtester/ingest/tickrecorder/`) — live Deribit WebSocket → 5-min parquets, deployed as `ct-recorder.service`
- **Tardis bulk download** (`backtester/ingest/bulkdownloadTardis/`) — historical bulk download from tardis.dev

---

## Indicators Module (`indicators/`)

Python ports of TradingView Pine Script indicators.

| File | Description |
|------|-------------|
| `turbulence.py` | Composite 0–100 turbulence score (Parkinson RV, trend, burst, decay). Ported from `market_wildness_v2.pine`. |
| `supertrend.py` | SuperTrend indicator port |
| `data.py` | Shared data layer: `fetch_klines()` from Binance |

---

## Deployment

Production uses a **slot architecture** on a Hetzner VPS:
- Base dir: `/opt/ct/`; slots at `/opt/ct/slot-XX/`
- Each slot = full codebase copy + isolated `.env`, `logs/`, venv, systemd unit
- **No git on server** — deploy via rsync from dev machine

```bash
./deployment/deploy-slot.sh 01       # rsync + restart slot-01
./deployment/deploy-slot.sh hub      # deploy hub dashboard
./deployment/deploy-slot.sh status   # overview of all slots
./deployment/ssh-server.sh           # SSH to VPS
```

Config flow: `slots/slot-XX.toml` → `slot_config.py` → `.env.slot-XX` → rsync to VPS.

---

## Testing

```bash
python -m pytest tests/ -v           # ~244 tests, ~1.7s — run always
python -m pytest tests/live/ -m live -v   # Deribit testnet — only when asked
```

Live tests are auto-deselected in `pyproject.toml`. Shared fixtures in `tests/conftest.py`.

---

## Repository Structure

```
/                     — Live trading engine
  strategies/         — Live trading strategies
  exchanges/          — Exchange adapters (Coincall, Deribit)
  execution/          — Execution router
  indicators/         — Pine Script → Python indicator ports
  hub/                — Hub dashboard (aggregates all slots)
  slots/              — Per-slot configuration TOML files
  deployment/         — Deploy scripts + VPS setup guide
  tests/              — Test suite
  docs/               — API reference, module reference
backtester/           — Backtester (separate subsystem)
  strategies/         — Backtester strategies
  ingest/             — Data ingestion (tick recorder + Tardis)
  experiments/        — TOML experiment configs
```

---

## For AI Agents

See [AGENTS.md](AGENTS.md) for the full orientation guide, hard rules, and working conventions.
