# Exchange Trade Log Integration

**Status:** Planned  
**Target version:** v1.11.0  
**Scope:** Live trading application (all slots, both exchanges)

---

## Design Principle: Exchange Agnosticism

The application core (`trade_log_reconciler.py`, `lifecycle_engine.py`, `persistence.py`) must have **zero knowledge of any specific exchange**. All exchange-specific logic — endpoint URLs, authentication, response field names, date ranges, unit conversions, currency normalization — lives exclusively in the exchange adapter modules (`exchanges/deribit/`, `exchanges/coincall/`, etc.).

The rest of the system depends only on the `ExchangeTradeLog` ABC defined in `exchanges/base.py`. This means:
- Adding a new exchange (Binance, OKX, etc.) requires adding one new adapter class, nothing else.
- The reconciler, the data model, and the persistence layer are untouched when exchanges are added or changed.

---

## Problem Statement

Realized PnL is currently computed inside the app from order-level fill prices recorded during execution:

```
realized_pnl = -(total_entry_cost + total_exit_cost)
```

This has three weaknesses:

1. **Fees are completely ignored.** The exchange charges a maker/taker fee on every fill. These are non-trivial (typically 0.03–0.05% per leg). The app doesn't know them and doesn't track them.
2. **Fill prices are estimated.** `TradeLeg.fill_price` comes from polling `get_order_status()` during the fill-wait loop, not from the exchange's confirmed trade record. Edge cases (partial fills, requotes, rapid fills) can leave the recorded price slightly off.
3. **No durable per-fill audit trail.** The only permanent record is the `trade_history.jsonl` line written at close, which carries whatever the app happened to capture. There is no link back to the exchange's own trade records.

Both exchanges maintain a **trade log** — a server-side, immutable record of every fill event. It is the real source of truth: confirmed qty, confirmed price, and confirmed fee. The app should use this log to produce its final accounting numbers.

---

## When to Integrate: Design Decision

This is the key question. Two approaches are possible:

### Option A — At action time (synchronous)

Query the exchange trade log immediately after each fill confirmation, then use those confirmed values for downstream logic (SL calculation, Telegram notifications, PnL display).

**Problems:**
- Exchange trade log entries arrive with a delay after execution. Querying immediately is likely to return an empty list and force a retry loop, adding latency and complexity to the hot path.
- The execution path is already carefully designed to be fast and fault-tolerant. Adding a new blocking dependency here is fragile. If the trade log API is slow or errors, the close flow stalls.
- At open time, the fill price from the order response is already correct enough for SL/entry-cost purposes. High-precision reconciliation at open buys nothing meaningful.

### Option B — Post-close deferred reconciliation (recommended)

After a trade transitions to `CLOSED`, enqueue it for reconciliation. A lightweight reconciler runs on every tick, retrying with backoff until the exchange trade log entries appear. Once confirmed data arrives, the trade record is enriched. Fees are added, confirmed prices replace estimated prices, and the final PnL number is recalculated.

**Advantages:**
- Execution hot path is completely untouched.
- Retry-with-backoff handles the latency problem cleanly.
- Fees can only be known after the fact anyway — this is not a limitation.
- A failed reconciliation (e.g. API outage) degrades gracefully: the app keeps its estimated PnL and logs a warning. Nothing breaks.

**Also consider: open-time reconciliation as a background sanity check**  
Optionally, reconcile open fills too (non-blocking, no retries, no downstream effect). This validates that the fill price we recorded matches the exchange record, and captures fees for the open. It improves accuracy of the _open-leg fee cost_ but is lower priority.

**Conclusion: implement Option B. Post-close reconciliation is the right integration point. Open-leg reconciliation is a Phase 2 addition if desired.**

---

## Architecture Overview

```
lifecycle_engine.py
  │  trade → CLOSED
  │  _finalize_close()  ← unchanged (still computes estimated PnL from fills)
  │  trade enqueued → TradeLogReconciler
  │
  └── TradeLogReconciler.tick()   ← called each lifecycle tick
        │  retry loop with backoff (5s, 10s, 20s, 40s, 60s)
        │  fetch trade log entries per order_id from exchange
        │  normalize to TradeLogEntry (USD, sign)
        │  update TradeLeg.confirmed_fill_price + TradeLeg.fee_usd
        │  recalculate TradeLifecycle.exchange_confirmed_pnl
        │
        ├── persistence.py — append RECONCILED record to trade_history.jsonl
        └── telegram_notifier.py — send confirmed PnL update (optional)
```

---

## Data Model Changes

### `TradeLeg` — new optional fields

```python
@dataclass
class TradeLeg:
    # ... existing fields unchanged ...

    # Populated by reconciler after close
    exchange_trade_ids: List[str] = field(default_factory=list)
    confirmed_fill_price: Optional[float] = None   # exchange-confirmed avg price
    fee_usd: Optional[float] = None                # total fee in USD for this leg
```

Rationale for `exchange_trade_ids` as a list: a single order can generate multiple partial fills in the exchange trade log, each with its own `trade_id`.

### `TradeLifecycle` — new fields

```python
@dataclass
class TradeLifecycle:
    # ... existing fields unchanged ...

    # Populated by reconciler
    total_fees_usd: Optional[float] = None
    exchange_confirmed_pnl: Optional[float] = None  # estimated_pnl - fees
    reconciliation_state: str = "pending"           # "pending"|"complete"|"failed"|"skipped"
```

`reconciliation_state` is persisted to `trades_snapshot.json` so a reconciliation can resume after a crash.

---

## New Module: `trade_log_reconciler.py`

### `TradeLogEntry` — normalized fill record

```python
@dataclass
class TradeLogEntry:
    exchange_trade_id: str
    order_id: str
    symbol: str
    side: str           # "buy" or "sell"
    qty: float          # contracts
    price_usd: float    # always USD (converted if needed)
    fee_usd: float      # always USD
    timestamp: float    # unix epoch
```

### `ExchangeTradeLog` — new ABC in `exchanges/base.py`

The interface is deliberately simple and hides all exchange-specific complexity (pagination, time ranges, currency conversion, field mapping) inside the adapter:

```python
class ExchangeTradeLog(ABC):
    @abstractmethod
    def get_fills_for_order(
        self,
        order_id: str,
        fill_time_hint: float,   # unix epoch — adapter uses this to narrow the query window
    ) -> List[TradeLogEntry]:
        """Return all confirmed fills for this order_id, normalized to TradeLogEntry.

        Returns an empty list if the exchange has not yet recorded the entry
        (caller should retry). The fill_time_hint allows adapters that lack
        orderId-level filtering to narrow a time-range query efficiently.

        All currency conversions, field remapping, and pagination are handled
        inside the adapter — the caller always receives USD-denominated entries.
        """
```

The `fill_time_hint` parameter is the key to making the interface exchange-agnostic: adapters that can query by order ID directly (Deribit) ignore it; adapters that only support time-range queries (Coincall) use it to build an efficient window without exposing that complexity to the caller.

### `TradeLogReconciler`

```python
class TradeLogReconciler:
    def __init__(self, trade_log: ExchangeTradeLog): ...

    def enqueue(self, trade: TradeLifecycle) -> None:
        """Enqueue a CLOSED trade for reconciliation."""

    def tick(self) -> None:
        """Called on every lifecycle tick. Processes the pending queue."""
```

**Retry schedule:** 5 attempts, delays `[5, 10, 20, 40, 60]` seconds between attempts. After 5 failures, mark `reconciliation_state = "failed"` and log a warning. The original estimated PnL remains intact as a fallback.

**Processing per trade:**
1. For each leg (open + close), call `get_fills_for_order(leg.order_id, fill_time_hint=leg_fill_time)`.
2. If the list is empty for any leg, abort and schedule retry.
3. Compute `confirmed_fill_price` = qty-weighted average of all partial fills.
4. Sum `fee_usd` across all fills for the leg.
5. Once all legs are reconciled, compute:
   ```
   exchange_confirmed_pnl = -(confirmed_entry_cost + confirmed_exit_cost) - total_fees_usd
   ```
6. Update `TradeLifecycle` fields, set `reconciliation_state = "complete"`.
7. Call persistence + notification hooks.

---

## Exchange-Specific Implementations

> These details live entirely inside the adapter classes in `exchanges/deribit/` and `exchanges/coincall/`. Nothing below is visible to the reconciler or any other application module.

### `exchanges/deribit/trade_log.py` — `DeribitTradeLog`

Deribit supports direct orderId lookup, so `fill_time_hint` is ignored.

- **Endpoint:** `private/get_user_trades_by_order`, params `{ "order_id": "...", "sorting": "asc" }`
- **Field mapping:** `trade_id → exchange_trade_id`, `amount → qty`, `direction → side`, `timestamp (ms) → timestamp (epoch)`
- **Currency conversion (all in BTC):** `price_usd = price_btc × btc_index_price`, `fee_usd = fee_btc × btc_index_price`. Index price fetched via injected `ExchangeMarketData.get_index_price()` at reconciliation time.
- **Empty list returned** if the API call fails or returns no trades (triggers reconciler retry).

### `exchanges/coincall/trade_log.py` — `CoincallTradeLog`

Coincall has no orderId filter; the adapter implements the time-window pattern internally.

- **Primary endpoint:** `GET /open/option/order/history/v1` — one record per order, fee already aggregated into a single `fee` field plus `avgPrice`. Simplest path.
- **Fallback endpoint:** `GET /open/option/trade/history/v1` — one record per fill, for partial-fill detail if needed.
- **Lookup strategy (hidden from caller):** use `fill_time_hint` to build a `startTime = hint_ms - 60_000`, `endTime = hint_ms + 120_000` window, then filter results by `orderId` in Python. On retry, double the window.
- **Currency:** prices and fees are already USD — no conversion needed.
- **Side normalization:** `tradeSide` int (1/2) → `"buy"`/`"sell"` string, same pattern as `CoincallExecutorAdapter`.

### Adding a future exchange (Binance, OKX, …)

1. Create `exchanges/<name>/trade_log.py` implementing `ExchangeTradeLog`.
2. Register it in `exchanges/__init__.py` `build_trade_log()` factory.
3. No other files change.

---

## Changes to Existing Modules

### `lifecycle_engine.py`

- Inject `TradeLogReconciler` at construction (alongside `ExecutionRouter`, `OrderManager`).
- After `_finalize_close()` (two call sites + `_close_expiry`), call `reconciler.enqueue(trade)`.
- In the main `tick()` method, call `reconciler.tick()` once per tick.
- No changes to the close logic itself.

### `persistence.py`

- `save_completed_trade()` adds new fields to the JSONL record:
  ```json
  {
    "reconciliation_state": "pending",
    "total_fees_usd": null,
    "exchange_confirmed_pnl": null
  }
  ```
- Add `update_reconciled_trade(trade_id, confirmed_pnl, fees, state)` — appends a second JSONL line with `"event": "reconciled"` rather than overwriting the original record. This preserves the append-only guarantee and makes the timeline auditable.

### `telegram_notifier.py`

- `notify_trade_closed()` adds a small note when fees are not yet confirmed:  
  `"(fees not yet confirmed — will update)"`
- Add `notify_trade_reconciled(trade, delta_pnl)` — sends a follow-up message only if `abs(exchange_confirmed_pnl - estimated_pnl) > threshold` (e.g. $1.00). Avoids noisy notifications when the estimated price was already accurate.

### `exchanges/base.py`

- Add `ExchangeTradeLog` ABC.
- Add `TradeLogEntry` dataclass (shared, so it lives here alongside the other exchange types).

### `exchanges/__init__.py`

- `build_exchange()` factory gains a `build_trade_log(name, auth, market_data)` factory function.

### `TradeLeg.to_dict()` / `TradeLifecycle.to_dict()`

- Include new fields so crash-recovery snapshot preserves reconciliation state.

---

## JSONL Record Format (after this upgrade)

**At close (existing record, extended):**
```json
{
  "id": "abc123",
  "strategy_id": "daily_put_sell",
  "state": "closed",
  "realized_pnl": 142.50,
  "exit_cost": -50.10,
  "total_fees_usd": null,
  "exchange_confirmed_pnl": null,
  "reconciliation_state": "pending",
  "open_legs": [...],
  "close_legs": [...],
  "timestamp": "2026-04-11T04:01:00Z"
}
```

**Reconciliation update (new appended line):**
```json
{
  "event": "reconciled",
  "id": "abc123",
  "reconciliation_state": "complete",
  "total_fees_usd": 8.34,
  "exchange_confirmed_pnl": 134.16,
  "estimated_pnl": 142.50,
  "pnl_delta": -8.34,
  "close_legs": [
    {"symbol": "BTC-11APR26-80000-P", "confirmed_fill_price": 125.00, "fee_usd": 4.17, ...}
  ],
  "timestamp": "2026-04-11T04:01:22Z"
}
```

---

## Implementation Phases

### Phase 1 — Core reconciliation (required)

1. Add `ExchangeTradeLog` ABC and `TradeLogEntry` to `exchanges/base.py`
2. Implement `DeribitTradeLog` in `exchanges/deribit/trade_log.py`
3. Implement `CoincallTradeLog` in `exchanges/coincall/trade_log.py` (endpoint confirmed — see exchange-specific section)
4. Register both in `exchanges/__init__.py` `build_trade_log()` factory
5. Write `trade_log_reconciler.py` with retry logic — depends only on `ExchangeTradeLog` ABC
6. Add new fields to `TradeLeg` and `TradeLifecycle`
7. Wire reconciler into `lifecycle_engine.py`
8. Update `persistence.py` — close record + reconciliation update record
9. Update `telegram_notifier.py` — provisional close message + reconciled update
10. Tests: unit tests for `TradeLogReconciler` with `MockTradeLog` — no exchange knowledge in tests

### Phase 2 — Open fill reconciliation (optional, lower priority)

11. Reconcile open legs too (non-blocking, best-effort)
12. `TradeLeg.fee_usd` for open fees → improves total cost of entry reporting

---

## What This Does NOT Change

- The execution hot path — no additional API calls during open/close
- The SL / exit condition logic — continues using estimated fill prices in real time (accurate enough)
- The crash-recovery snapshot format — new fields are additive; existing snapshots load without error (default values)
- The backtester — operates on historical Tardis data, not live exchange logs

---

## Open Questions

1. **Time-window strategy for Coincall (and any exchange without orderId filtering):** The `fill_time_hint` passed to `CoincallTradeLog.get_fills_for_order()` defines the query window. Default proposal: `hint ± 90s`, doubling on each retry. Confirm this is acceptable before implementation.
2. **Deribit BTC index at reconciliation time:** Is using `get_index_price()` at the moment of reconciliation (potentially 60s after close) accurate enough, or should we snapshot the BTC price at the exact moment of close and store it in `TradeLifecycle`?
3. **Notification threshold:** What PnL delta justifies sending a reconciliation Telegram update? Suggested default: `$1.00`. Configurable via `RECONCILE_NOTIFY_THRESHOLD_USD` env var?
