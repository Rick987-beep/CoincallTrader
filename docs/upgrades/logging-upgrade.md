# Logging Upgrade Plan

**Status:** Planned  
**Target:** Live trading application only (not backtester)  
**New dependencies:** None — stdlib only

---

## Problem Statement

The current setup writes everything into a single `logs/trading.log`. After a busy trading session or a bad incident, reconstructing "what happened to trade X" means grep-ing through a wall of mixed health checks, order events, strategy decisions, and debug noise. The health checker in particular emits a large multi-line ASCII banner that is visually distinctive but impossible to parse programmatically.

---

## Design Decisions

### Format: structured JSONL, not binary or database

Options considered:

| Option | Verdict |
|--------|---------|
| SQLite | Adds schema management, WAL/locking concerns, requires tooling. Not worth it for a single-process app. |
| Binary (msgpack, protobuf) | Sub-ms latency gains irrelevant here. Removes the ability to `tail -f` or `jq` directly. |
| `structlog` or similar | Extra dependency, complex configuration. |
| **JSONL (stdlib `json`)** | ✅ Human-readable, `jq`-queryable, easy to parse in Python/pandas, zero new deps. Proven by `order_ledger.jsonl` and `trade_history.jsonl` already in the repo. |

Each JSON line is fully self-describing (no shared state across lines). Every record carries `ts`, `event`, `slot`, and `strategy` fields so old and new deployments coexist in the same file without ambiguity.

---

## Log Tracks

Four files in `logs/`. Existing files (`order_ledger.jsonl`, `trade_history.jsonl`, `active_orders.json`, `trades_snapshot.json`) are **unchanged**.

### `logs/trading.log` — human-readable catch-all (EXISTING, keep)
- All modules write here via the root logger, same as today
- Purpose: `tail -f`, journalctl, incident watching
- Rotation: `TimedRotatingFileHandler`, rotates at midnight, **`backupCount=14`** (14 days of `.log.YYYY-MM-DD` files, older ones auto-deleted by Python's handler)

### `logs/health.jsonl` — periodic account snapshots (NEW)
Source: `health_check.py`  
Rotation: daily, **`backupCount=30`** — health data is cheap to keep longer  
One record per 5-minute check:
```json
{"ts":"2026-04-11T03:05:00Z","event":"health_check","slot":"01","strategy":"daily_put_sell","equity":12450.00,"avail_margin":8200.00,"margin_pct":34.1,"net_delta":-0.08,"positions":1,"btc_price":82900,"uptime_s":7200,"level":"ok"}
```
`level` is `"ok"` | `"warn"` | `"critical"` — makes it easy to filter for degraded periods.

### `logs/strategy.jsonl` — lifecycle events only (NEW)
Source: `lifecycle_engine.py`, `strategy.py`, strategy modules  
Rotation: daily, **`backupCount=60`**  
Only "big things" — one record per lifecycle state change or entry/exit decision:

| `event` | When | Key fields |
|---------|------|-----------|
| `DEPLOY_STARTED` | process startup | `slot`, `strategy`, `version` |
| `ENTRY_TRIGGERED` | entry conditions met | `symbol`, `qty`, `target_delta` |
| `ENTRY_BLOCKED` | entry conditions met but blocked | `reason` (e.g. `"liquidity_guard"`, `"ema_filter"`) |
| `TRADE_OPENING` | PENDING_OPEN state entered | `trade_id`, `legs` |
| `TRADE_OPENED` | all legs filled → OPEN | `trade_id`, `open_premium`, `open_price` |
| `TRADE_OPEN_FAILED` | fill manager exhausted | `trade_id`, `reason` |
| `EXIT_TRIGGERED` | exit condition fires | `trade_id`, `condition`, `reason`, `btc_price` |
| `TRADE_CLOSING` | PENDING_CLOSE state entered | `trade_id`, `trigger` |
| `TRADE_CLOSED` | all close legs filled | `trade_id`, `close_price`, `pnl`, `pnl_usd`, `duration_s` |
| `TRADE_CANCELLED` | cancelled with no fills | `trade_id`, `reason` |
| `RECONCILE_WARN` | reconciliation found issues | `issues` list |

Example:
```json
{"ts":"2026-04-11T03:42:17Z","event":"TRADE_CLOSED","slot":"01","strategy":"daily_put_sell","trade_id":"abc-123","symbol":"BTC-11APR26-82000-P","close_price":0.0038,"pnl":0.00142,"pnl_usd":116.40,"duration_s":3261,"trigger":"expiry"}
```

### `logs/execution.jsonl` — order and phase trace (NEW)
Source: `order_manager.py`, `trade_execution.py`, `rfq.py`  
Rotation: daily, **`backupCount=14`** — high-volume, less need to keep long-term  
One record per order event or phase transition:

| `event` | Key fields |
|---------|-----------|
| `PHASE_ENTERED` | `trade_id`, `phase`, `phase_label`, `direction` (open/close) |
| `PHASE_TIMEOUT` | `trade_id`, `phase`, `elapsed_s` |
| `ORDER_PLACED` | `trade_id`, `order_id`, `symbol`, `side`, `qty`, `price`, `purpose` |
| `ORDER_FILLED` | `trade_id`, `order_id`, `symbol`, `fill_price`, `fill_qty` |
| `ORDER_PARTIAL` | `trade_id`, `order_id`, `filled_qty`, `remaining_qty` |
| `ORDER_CANCELLED` | `trade_id`, `order_id`, `reason` |
| `ORDER_REQUOTED` | `trade_id`, `old_order_id`, `new_order_id`, `old_price`, `new_price` |
| `RFQ_SENT` | `trade_id`, `symbol`, `qty` |
| `RFQ_FILLED` | `trade_id`, `symbol`, `fill_price`, `fill_qty` |

Example:
```json
{"ts":"2026-04-11T03:41:55Z","event":"ORDER_REQUOTED","slot":"01","trade_id":"abc-123","symbol":"BTC-11APR26-82000-P","old_order_id":"ORD-001","new_order_id":"ORD-002","old_price":0.0041,"new_price":0.0039,"phase":"phase_2_1"}
```

---

## Log Rotation & Disk Usage

All handlers use Python's stdlib `TimedRotatingFileHandler` (`when='midnight'`). Python's handler renames the current file at midnight and auto-deletes the oldest rotated file when `backupCount` is exceeded — no cron job needed.

| File | `backupCount` | Approx max size |
|------|-------------|----------------|
| `trading.log` | 14 | ~50 MB total (estimated) |
| `health.jsonl` | 30 | ~5 MB total (~2 KB/day) |
| `strategy.jsonl` | 60 | ~2 MB total (~3 KB/day) |
| `execution.jsonl` | 14 | ~20 MB total (~1 MB/day on active trading) |

Total worst-case ceiling: **~80 MB** on a slot that trades every day. This is safe for the CPX22 setup.

---

## Deployment Robustness

### Why old logs won't corrupt new ones
Each JSONL line is fully self-describing and independent. There is no schema version, header, or shared state. A reader (Python or `jq`) that doesn't know a field simply ignores it. New events can add fields freely without breaking old records.

### The `DEPLOY_STARTED` marker
The first event `logging_setup.py` writes to `strategy.jsonl` on process startup is:
```json
{"ts":"...","event":"DEPLOY_STARTED","slot":"01","strategy":"daily_put_sell","version":"1.10.0","pid":12345}
```
This creates a clear deployment boundary in the file. When you look at execution or strategy events, you can always find the nearest preceding `DEPLOY_STARTED` to know exactly which codebase version produced them.

### Rotation filenames survive deploys
`TimedRotatingFileHandler` renames the previous day's file to `health.jsonl.2026-04-10` before creating a new `health.jsonl`. A deploy mid-day picks up the existing `health.jsonl` and appends to it — the `DEPLOY_STARTED` marker in `strategy.jsonl` is the record of when the new version took over.

### What to do on a forced fresh-start
Dev mode already deletes `trading.log`. If you ever want a clean slate for all JSONL tracks, add the new file names to the `_stale` list in `main.py`'s dev-mode cleanup block.

---

## Dashboard Log Section

Currently `dashboard.py` attaches a `DashboardLogHandler` to the **root logger**, capturing everything including verbose health checks. After this upgrade:

- The `DashboardLogHandler` is attached **only to the `ct.strategy` logger** (capturing all `strategy.jsonl` events)
- Additionally, root logger records at **WARNING or above** are also captured (errors, connectivity failures, etc.)
- `ct.health` and `ct.execution` are explicitly **excluded** from the dashboard handler

This means the dashboard log tail shows: entry/exit decisions, lifecycle state changes, errors, and warnings — the things you'd want to check at a glance. The 5-minute health pings and the per-order micro-events stay in their dedicated files.

Implementation note: `ct.strategy` logger sets `propagate=False` so its records do NOT flow up to the root logger and double-appear in `trading.log` via the `DashboardLogHandler`. The `strategy.jsonl` file handler on `ct.strategy` is the sole destination for those records.

---

## New Loggers

Three named loggers, all in the `ct.*` namespace, all with `propagate=False`:

| Logger | Handler | File |
|--------|---------|------|
| `ct.health` | `TimedRotatingFileHandler` + `JsonlFormatter` | `logs/health.jsonl` |
| `ct.strategy` | `TimedRotatingFileHandler` + `JsonlFormatter` + `DashboardLogHandler` | `logs/strategy.jsonl` |
| `ct.execution` | `TimedRotatingFileHandler` + `JsonlFormatter` | `logs/execution.jsonl` |

The `JsonlFormatter` is a small custom `logging.Formatter` subclass (~20 lines) that serialises the `LogRecord` `msg` dict to a JSON line. Module-level loggers (`logger = logging.getLogger(__name__)`) continue writing human-readable text to `trading.log` via the root logger — no changes to those call sites.

---

## Files Changed

| File | Change |
|------|--------|
| `logging_setup.py` | **NEW** — `setup_logging(dev_mode, logs_dir)`, `JsonlFormatter`, three track handlers |
| `main.py` | Replace `basicConfig` block with `setup_logging()` call; no other changes |
| `health_check.py` | Replace multi-line banner with single `ct.health` structured log call |
| `lifecycle_engine.py` | Add `ct.strategy` calls at the 8 lifecycle events listed above |
| `order_manager.py` | Add `ct.execution` calls at ORDER_PLACED, ORDER_FILLED, ORDER_CANCELLED, ORDER_REQUOTED |
| `trade_execution.py` | Add `ct.execution` calls at PHASE_ENTERED, PHASE_TIMEOUT, fill events |
| `dashboard.py` | Change handler attachment from root logger to `ct.strategy` + WARNING filter on root |

Existing text `logger.info/warning/error` calls in all modules are **untouched**. The new track loggers are additive.

---

## Usage Examples

```bash
# Full timeline for trade abc-123
jq 'select(.trade_id == "abc-123")' logs/strategy.jsonl logs/execution.jsonl \
  | jq -r '[.ts, .event, (.reason // ""), (.phase // ""), (.price // "")] | @tsv'

# All exit triggers in the last 7 days (with context)
jq 'select(.event == "EXIT_TRIGGERED") | {ts, condition, reason, btc_price}' logs/strategy.jsonl

# Execution phases for a slow fill — why did it take so long?
jq 'select(.event | startswith("PHASE") or . == "ORDER_REQUOTED") | [.ts, .event, .phase, .trade_id] | @tsv' \
  -r logs/execution.jsonl

# Account state during a stop-loss window
jq 'select(.ts >= "2026-04-10T03:00" and .ts <= "2026-04-10T04:00")' logs/health.jsonl

# All blocked entries today (liquidity guard, EMA filter, etc.)
jq 'select(.event == "ENTRY_BLOCKED") | {ts, reason}' logs/strategy.jsonl

# Find the deploy that was running when a specific trade was opened
jq 'select(.event == "DEPLOY_STARTED" or (.trade_id == "abc-123" and .event == "TRADE_OPENING"))' \
  logs/strategy.jsonl | jq -r '[.ts, .event, (.version // ""), (.trade_id // "")] | @tsv'
```
