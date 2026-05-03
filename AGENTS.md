# CryoTrader — Agent Context / Working Memory

**Version:** 1.14.0 | **Updated:** April 2026

This file is the **primary orientation guide** for AI agents working on CryoTrader.
Read it fully before touching any code. It is intentionally holistic.

---

## ⚠️ Hard Rules for AI Agents

These are non-negotiable. Follow them every time, no exceptions.

1. **Never deploy to the production server** without explicit user approval. No SSH writes, no rsync, no file swaps, not even "temporary" tests on prod.
2. **Never `git commit` or `git push`** without explicit user approval.
3. **For any task bigger than a small edit: present a plan first.** Wait for the user to say "go" before writing code.
4. **Run tests before and after any code change:** `python -m pytest tests/ -v`

---

## ⚠️ Navigation Warning — Two Separate Applications

This repo contains **two largely independent applications**:

| | **Live Trading Engine** | **Backtester** |
|---|---|---|
| Root | `/` (top level) | `backtester/` |
| Entry | `main.py` | `backtester/run.py` |
| Strategies | `strategies/` | `backtester/strategies/` |
| Config | `config.py`, `.env` | `backtester/config.py`, `backtester/config.toml` |
| Engine | `lifecycle_engine.py` | `backtester/engine.py` |
| Market data | `market_data.py` | `backtester/market_replay.py` |

**Many modules share similar or identical names in both.** Always confirm which application you are working in before reading or editing a file. A change to `strategies/` affects live production. A change to `backtester/strategies/` affects backtesting only.

---

## Current Development Focus (April 2026)

Active areas of work — agents should be aware of these when the context is unclear:

- **Strategy development & refinement** — new strategies in `strategies/` and corresponding backtest strategies in `backtester/strategies/`; `strategy.py` and `lifecycle_engine.py` are candidates for higher-level refactoring
- **Dashboard improvements** (`dashboard.py`, `hub/`)
- **Logging, persistence, trade blotter** (`logging_setup.py`, `persistence.py`)
- **Indicators module** (`indicators/`) — Python ports of TradingView Pine indicators; `turbulence.py` is the reference implementation
- **Backtester future** — possible separation into its own repo; rich GUI under consideration

---

## What this repo is

- **Live trading application** for **crypto options** (multi-leg structures) against real exchanges (Coincall, Deribit).
- **Backtester** for Deribit BTC options — replays historical tick data, runs parameter grids, generates HTML reports.

---

## PART 1 — Live Trading Engine

### Architecture

```
PositionMonitor (10s poll, daemon thread)
  │
  ├─► LifecycleEngine.tick(snapshot)    — advances every active trade's state machine
  │
  └─► StrategyRunner.tick(snapshot)     — per strategy:
        1. check_closed_trades()
        2. entry gate (all conditions)  — time, weekday, margin, equity, EMA filter
        3. resolve_legs()               — LegSpec → concrete TradeLeg
        4. lifecycle_engine.create()    — registers PENDING_OPEN
        5. lifecycle_engine.open()      — routes to limit / rfq executor
```

### Trade State Machine

```
PENDING_OPEN → OPENING → OPEN → PENDING_CLOSE → CLOSING → CLOSED
                                                           └─► FAILED
```

- **Open:** atomic — all legs or none (prevents naked legs)
- **Close:** best_effort — each leg placed independently; bad-priced legs skipped and retried next tick
- **Circuit breaker:** `MAX_CLOSE_ATTEMPTS=10` → FAILED with manual intervention log

### Key Entrypoint Files

| File | Role |
|------|------|
| `main.py` | Launcher; crash recovery (order ledger + trade snapshot + reconcile); dynamic strategy import via `SLOT_STRATEGY` |
| `strategy.py` | `TradingContext` DI, `StrategyConfig`, `StrategyRunner`; entry/exit condition factories |
| `lifecycle_engine.py` | Owns `TradeLifecycle` state machine; advances trades on each tick; persists snapshots |
| `trade_lifecycle.py` | Data-only: `TradeState`, `TradeLeg`, `TradeLifecycle`, `RFQParams`, `ExitCondition`, PnL helpers |
| `execution/router.py` | Routes **limit vs RFQ** (incl. "auto" routing by notional threshold) |
| `order_manager.py` | Order ledger; requotes, polling, snapshotting, reconciliation |

### Execution Pipeline

| Mode | Module | When | How |
|------|--------|------|-----|
| `limit` | `trade_execution.py` | Default / Deribit | Per-leg limit orders via `LimitFillManager`; phased pricing; returns "filled"/"requoted"/"failed"/"pending" |
| `rfq` | `rfq.py` | ≥$50k notional (Coincall) | Atomic multi-leg RFQ; `RFQParams` configures timeout, min improvement, fallback |
| `auto` | `execution/router.py` | Default mode | Routes by notional |

**LimitFillManager phases** (configured via `ExecutionParams` list of `ExecutionPhase`):
- Each phase: pricing mode, duration, buffer_pct, reprice_interval
- 6 pricing modes: `fair`, `bid+33%_spread`, `bid`, `ask`, `mark`, `custom`

### Live Strategies (`strategies/`)

All strategies use `_p("NAME", default, cast)` → reads `PARAM_<NAME>` env vars. Register in `strategies/__init__.py`.

| File | Description | Exchange | Active |
|------|-------------|----------|--------|
| `put_sell_80dte.py` | Sell OTM put at -0.15δ, monthly expiry ≥80 DTE, entry 13–14 UTC, TP=95%, SL=250%, EMA-20 filter, max_concurrent=90 | Coincall | **slot-01** |
| `short_strangle_delta_tp.py` | Sell OTM strangle at target δ; TP + min_otm_pct guard | Deribit | **slot-02** |
| `short_strangle_delta.py` | Delta-selected strangle; weekend filter | Deribit | Base |
| `daily_put_sell.py` | 1DTE -0.10δ put, entry 03–04 UTC (legacy) | Coincall | — |
| `long_strangle_index_move.py` | Long ±$2000 OTM strangle; exit on $1500 index move or time | Deribit | — |
| `atm_straddle_index_move.py` | Long ATM straddle; exit on BTC move ≥$1200 or time | Either | — |
| `short_straddle_strangle.py` | Combined short straddle/strangle | Either | — |
| `blueprint_strangle.py` | Template for new strategies | Either | Template |

### Active Production Slots

| Slot | Strategy | Exchange | Account | Key Params |
|------|----------|----------|---------|------------|
| slot-01 | `put_sell_80dte` | Coincall | coincall-main | qty=0.1, δ=-0.15, dte≥80, entry 13–14 UTC |
| slot-02 | `short_strangle_delta_tp` | Deribit | deribit-big | qty=5, δ=0.15, dte=1, entry 18 UTC, SL=5×, TP=80% |

### Exchange Abstraction (`exchanges/`)

- **Interfaces:** `exchanges/base.py` — `ExchangeAuth`, `ExchangeMarketData`, `ExchangeExecutor`, `ExchangeAccountManager`, `ExchangeRFQExecutor`
- **Factory:** `exchanges/__init__.py` — `build_exchange(name)` reads `config.EXCHANGE`
- **Coincall:** `exchanges/coincall/` — thin wrappers; HMAC-SHA256; sides as int (1=buy, 2=sell)
- **Deribit:** `exchanges/deribit/` — OAuth2 client_credentials (proactive refresh at 80% TTL); JSON-RPC 2.0; BTC→USD conversion; tick size snapping; symbol translation (`BTCUSD-03APR26-74000-C` ↔ `BTC-3APR26-74000-C`); RFQ (25 BTC min)

### Infrastructure Modules

| Module | Purpose |
|--------|---------|
| `option_selection.py` | `LegSpec`, `resolve_legs()`, `find_option()`, `straddle()`, `strangle()`; `min_otm_pct` guard |
| `market_data.py` | Option chains, orderbooks, BTC price; 30s TTL cache |
| `account_manager.py` | `AccountManager`, `AccountSnapshot`, `PositionSnapshot`, `PositionMonitor` |
| `ema_filter.py` | Binance kline EMA-20 filter, 1h cache, entry condition factory |
| `persistence.py` | Append-only `trade_history.jsonl` |
| `health_check.py` | Background health logging (5-min interval) |
| `telegram_notifier.py` | Fire-and-forget alerts, `get_notifier()` singleton |
| `dashboard.py` | Flask+htmx per-slot dashboard; session auth; brute-force lockout |
| `position_closer.py` | Kill switch: two-phase mark-price close via `PositionCloser` |
| `logging_setup.py` | Initialises three structured JSONL tracks + root logger |

### Structured Logging

Three JSONL tracks in `logs/`:
- `health.jsonl` → `ct.health` (5-min snapshots: equity, margin, btc_price)
- `strategy.jsonl` → `ct.strategy` (lifecycle events: DEPLOY_STARTED, TRADE_OPENING, TRADE_OPENED, EXIT_TRIGGERED, TRADE_CLOSED)
- `execution.jsonl` → `ct.execution` (order events: ORDER_PLACED, ORDER_REQUOTED, ORDER_FILLED, PHASE_ENTERED, PHASE_TIMEOUT)
- `trading.log` → human-readable root logger

### Dashboard & Hub

- **Per-slot:** Flask+htmx, port 808X, `DASHBOARD_MODE` env var (`full` / `control` / `disabled`)
- **Hub:** port 8070 at `/opt/ct/hub/`; aggregates all slots; proxies control commands to slot localhost ports
- **Kill switch:** `/api/killswitch` → two-phase mark-price close via `PositionCloser`

### Deployment Model

Production uses a **slot architecture** on a Hetzner CPX22 VPS (Ubuntu 24.04):
- Base dir: `/opt/ct/`; slots at `/opt/ct/slot-XX/`
- Each slot = full codebase copy + own `.env`, `logs/`, venv, systemd unit `ct-slot@XX`
- systemd also runs `ct-hub` (hub dashboard) and `ct-recorder` (tick recorder)
- **No git on server** — sync via rsync/SSH from dev machine

```bash
./deployment/deploy-slot.sh 01           # rsync + restart slot-01
./deployment/deploy-slot.sh hub          # deploy hub dashboard
./deployment/deploy-slot.sh status       # overview of all slots
./deployment/ssh-server.sh               # SSH to VPS
```

Config flow: `slots/slot-XX.toml` → `slot_config.py` → `.env.slot-XX` → rsync to VPS. **Commit is NOT required before deploy.**

---

## PART 2 — Backtester (`backtester/`)

### Overview

Real-data options backtester using Deribit historical tick data. Runs parameter grids, scores results with statistical rigor, and produces self-contained HTML reports.

**Note:** The backtester may be separated into its own repo in the future.

### CLI

```bash
python -m backtester.run --strategy <name>
python -m backtester.run --strategy delta_strangle_tp --robustness --wfo
python -m backtester.run --experiment <name>   # sensitivity / WFO from TOML
```

Strategy aliases: `straddle`, `put_sell`, `short_straddle`, `delta_strangle`, `delta_strangle_tp`, `deltaswipswap`, `deltaswipswap1m`, `weekly_strangle_tp`, `weekly_strangle_cap`, `weekend_strangle`

### Runtime Model

```
Load snapshot parquets → MarketReplay → run_grid_full() → GridResult → generate_html()
```

1. `MarketReplay(...)` yields `MarketState` per 5-min interval
2. `engine.run_grid_full(...)` runs all parameter combos in a single data pass
3. `GridResult(...)` computes vectorised metrics (Sharpe, PnL, R², Omega, Ulcer Index, drawdown) then composite score
4. `reporting_v2.generate_html(...)` renders a self-contained HTML file

### Key Files

| File | Purpose |
|------|---------|
| `backtester/run.py` | CLI entrypoint |
| `backtester/engine.py` | Single-pass grid runner; `run_grid_full()` |
| `backtester/market_replay.py` | Fast iterator over parquet snapshots; yields `MarketState` |
| `backtester/results.py` | `GridResult`: vectorised scoring, equity metrics |
| `backtester/robustness.py` | Deflated Sharpe Ratio (Bailey & López de Prado) |
| `backtester/walk_forward.py` | Walk-forward optimisation windows |
| `backtester/reporting_v2.py` | HTML report generation |
| `backtester/reporting_charts.py` | Pure SVG chart primitives |
| `backtester/experiment.py` | Sensitivity analysis from TOML experiments |
| `backtester/config.py` / `config.toml` | Scoring weights, grid parameters |

### Backtester Strategies (`backtester/strategies/`)

| File | Description |
|------|-------------|
| `straddle_strangle.py` | Long ATM straddle/OTM strangle + index move exit |
| `daily_put_sell.py` | Short OTM put, SL or expiry exit |
| `short_straddle_strangle.py` | Short straddle/strangle |
| `short_strangle_delta.py` | Delta-selected weekly short strangle |
| `short_strangle_delta_tp.py` | Above + configurable TP + min_otm_pct |
| `short_strangle_weekly_tp.py` | Weekly short strangle with TP/SL |
| `short_strangle_weekly_cap.py` | Above + capacity: target_max_open, max_daily_new |
| `short_strangle_weekend.py` | Weekend-only variant |
| `deltaswipswap.py` | Long straddle/strangle + dynamic delta hedging (gamma scalping) |
| `deltaswipswap1m.py` | 1-minute candle variant of deltaswipswap |

### Data Ingestion (two sources, same schema)

**Tick Recorder** (`backtester/ingest/tickrecorder/`):
- Live Deribit WebSocket → 5-min parquets
- Burst-mode capture: subscribe 10s before each boundary, snapshot 0.2s after, unsubscribe
- `sync.py` — rsync from VPS; `merge.py` — concatenate ranges
- Deployed as systemd `ct-recorder.service`

**Tardis Bulk Download** (`backtester/ingest/bulkdownloadTardis/`):
- `bulk_fetch.py` — downloads `.tar.gz` day-files from tardis.dev
- `stream_extract.py` — streams + decodes into normalised parquet per day
- `clean.py` — validates and repairs gaps

---

## Indicators Module (`indicators/`)

Python ports of TradingView Pine Script indicators. Source Pine scripts live in `/Users/ulrikdeichsel/WorkspacePineStrategy/indicators/`.

| File | Description |
|------|-------------|
| `turbulence.py` | Composite 0–100 turbulence score; 4-component weighted sum (Parkinson RV, trend, burst, decay). Entry condition factory. Ported from `market_wildness_v2.pine`. |
| `supertrend.py` | SuperTrend indicator port |
| `data.py` | Shared data layer: `fetch_klines()` from Binance |
| `hist_data.py` | Historical data utilities |

Pattern: one file per indicator, pure transform function + convenience `get_X_now()` wrapper. Never fetch data inside the transform.

---

## Testing

```bash
python -m pytest tests/ -v          # ~244 tests, ~1.7s — run this always
python -m pytest tests/live/ -m live -v   # live tests — Deribit testnet, never run unless asked
```

- `pyproject.toml`: `addopts = "-m 'not live'"` — live tests auto-deselected
- Shared fixtures: `tests/conftest.py` (`MockExecutor`, `MockMarketData`, helpers)

---

## Coding Conventions

- Python 3.12 — `Optional[X]` / `Union[X, Y]` style is still used throughout; newer `X | None` syntax is permitted but not required for consistency with existing code
- Venv: `.venv` — use `.venv/bin/python3` directly
- Dataclasses everywhere; frozen for thread-safe snapshots
- Factory functions for conditions — return callables, not classes
- Side as string (`"buy"` / `"sell"`) in all internal code; exchange adapters convert at API boundary
- `_p("NAME", default)` for strategy params from env vars
- `logging.getLogger(__name__)` in every module
- Fire-and-forget for Telegram — never crashes the bot

---

## Key Documents

| Document | Content |
|----------|---------|
| `deployment/UBUNTU_DEPLOYMENT.md` | VPS setup, systemd, deployment guide |
| `docs/MODULE_REFERENCE.md` | Classes, methods, dataclasses |
| `docs/API_REFERENCE.md` | Exchange REST API endpoints |
| `backtester/README.md` | Backtester workflow and research flow |
| `backtester/ingest/tickrecorder/README.md` | Tick recorder operation |

## What this repo is

- **Live trading application** for **crypto options** (multi-leg structures), trading against real exchanges.
- **Backtester** for Deribit BTC options that replays historical data and runs wide parameter grids.

## Live trading: architecture at a glance

The live system is a **poll/heartbeat architecture** driven by `PositionMonitor`. It wires:

- **Strategy evaluation** (entry signals, trade intents)
- **Lifecycle state machine** (trade states, exits, close logic)
- **Execution + order ledger** (limit/RFQ routing, fills, idempotency, reconciliation)
- **Persistence & crash recovery** (snapshots + trade history)
- **Dashboards** (per-slot control endpoint + hub aggregation)

### Key entrypoints / spine files

- `main.py`: launcher; builds context, registers strategies, starts monitor loop + dashboard; does crash recovery.
- `strategy.py`: `TradingContext`, `StrategyConfig`, `StrategyRunner`; strategies are configs (not subclasses).
- `lifecycle_engine.py`: owns `TradeLifecycle` state machine; advances trades on each tick; persists snapshots.
- `trade_lifecycle.py`: trade dataclasses/state, PnL helpers, leg definition, exit condition type.
- `execution/router.py`: routes **limit vs RFQ** (incl. “auto” routing by notional threshold).
- `order_manager.py`: order ledger; requotes, polling, snapshotting (`logs/active_orders.json`), reconciliation.

### Trade flow (signal → orders → fills → persistence)

1. `PositionMonitor` polls account/market state and triggers callbacks.
2. `StrategyRunner.tick()` checks entry gates; if open, resolves legs and calls:
   - `LifecycleEngine.create(...)` → registers intent (PENDING_OPEN)
   - `LifecycleEngine.open(trade.id)` → places open orders via `Router`
3. `LifecycleEngine.tick(account)` drives:
   - open fills (OPENING → OPEN)
   - exit evaluation (OPEN → PENDING_CLOSE)
   - close execution + fill checks (CLOSING → CLOSED)
4. Persistence:
   - snapshot: `logs/trades_snapshot.json` (crash recovery)
   - order ledger: `logs/active_orders.json`
   - completed history: `logs/trade_history.jsonl` (via `persistence.py`)

## Exchange abstraction

The core system depends on interfaces; exchange-specific details live behind adapters.

- Interfaces / boundary: `exchanges/base.py`
- Factory/wiring: `exchanges/__init__.py` (`build_exchange()`)
- Implementations:
  - `exchanges/deribit/`
  - `exchanges/coincall/`

## Deployment model (production)

Production uses a **slot architecture** on an Ubuntu VPS:

- Base dir: `/opt/ct/`
- Slots: `/opt/ct/slot-XX/` (isolated `.env`, venv, logs, systemd unit)
- systemd units:
  - `ct-slot@XX` (slots)
  - `ct-hub` (hub dashboard)
  - `ct-recorder` (tick recorder for backtester data)

Deploy philosophy: **no git on server**; sync via rsync/SSH from dev machine.

Primary docs/scripts:

- `deployment/UBUNTU_DEPLOYMENT.md`: canonical deployment guide
- `deployment/deploy-slot.sh`: deploy/operate slots/hub/recorder
- `servers.toml`: server registry (IP/user/base path)
- `slots/slot-XX.toml`: per-slot config (strategy/account/params); generates `.env.slot-XX` via `slot_config.py`

## Dashboards

- Per-slot dashboard/control endpoint: `dashboard.py` (binds to localhost port per slot).
- Hub dashboard: `hub/hub_dashboard.py` + `hub/templates/`
  - Aggregates slot status into hub “cards”
  - Also surfaces recorder health

## Backtester (separate subsystem)

The backtester is **almost a separate application** inside this repo. It is built around:

- **Speed-optimized market replay** (`MarketReplay`)
- **Single-pass grid evaluation** across many parameter combos
- **Vectorised scoring** and **self-contained HTML reports**
- A structured research flow: **discovery → sensitivity → walk-forward validation**

### Key files (spine)

- Overview / intended workflow: `backtester/README.md`
- CLI entrypoint: `backtester/run.py`
- Market replay (fast iterator): `backtester/market_replay.py`
- Grid runner (single-pass across all combos): `backtester/engine.py`
- Results + scoring (vectorised): `backtester/results.py`
- Robustness stats: `backtester/robustness.py`
- Walk-forward optimisation: `backtester/walk_forward.py`
- Reporting (render-only): `backtester/reporting_v2.py` + `backtester/reporting_charts.py`
- Experiments (TOML): `backtester/experiment.py` + `backtester/experiments/*.toml`

### Runtime model (data → replay → grid → result → report)

1. **Load snapshot parquets** (daily directory or merged range files)
2. `MarketReplay(...)` yields `MarketState` per 5-min interval
3. `engine.run_grid_full(...)` runs **all parameter combos in one pass**
4. `GridResult(...)` computes:
   - per-combo stats for **all combos** (vectorised)
   - composite scoring (percentile weights from `backtester/config.toml`)
   - deeper equity metrics for top-N combos only
5. `reporting_v2.generate_html(...)` renders one **self-contained HTML** file (no recomputation)

### Experiments (newer “research steps” layer)

Experiments capture a candidate found in discovery and drive:

- **Sensitivity mode**: local grid around best params (deviation rules: `pct` / `abs` / `fixed`)
- **WFO mode**: walk-forward window parameters (IS/OOS/step days) live in the experiment file

CLI:

- Discovery: `python -m backtester.run --strategy <strategy>`
- Sensitivity: `python -m backtester.run --experiment <name> --mode sensitivity`
- WFO: `python -m backtester.run --experiment <name> --mode wfo`

### Data ingestion (two sources, same schema)

The backtester consumes snapshot parquets in the schema produced by:

- **Production tick recorder (Deribit WS)**:
  - Recorder daemon: `backtester/ingest/tickrecorder/recorder.py`
  - Snapshot writer: `backtester/ingest/tickrecorder/snapshotter.py`
  - Sync down to dev: `backtester/ingest/tickrecorder/sync.py`
  - Merge daily → range files: `backtester/ingest/tickrecorder/merge.py`

- **Tardis bulk download (Deribit OPTIONS.csv.gz)**:
  - Bulk pipeline: `backtester/ingest/bulkdownloadTardis/bulk_fetch.py`
  - Stream extract to daily snapshots: `backtester/ingest/bulkdownloadTardis/stream_extract.py`
  - Cleaning: `backtester/ingest/bulkdownloadTardis/clean.py`

Note: multiple docs reference `backtester/ingest/snapshot_builder.py`, but that module
is not present in this workspace snapshot; current ingestion paths are via recorder
daily parquets + merge, or Tardis stream_extract.

## Testing

- Tests: `tests/`
- Pytest config: `pyproject.toml`
  - Live exchange tests are marked `live` and **skipped by default**.

