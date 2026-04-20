# CryoTrader — Agent Context / Working Memory

This file is a **high-signal orientation guide** for AI agents (and humans) working on CryoTrader.
It captures the *current mental model* of the system so a new session can become productive fast.

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

