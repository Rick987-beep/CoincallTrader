# Release Notes — v0.4.0 "Strategy Framework"

**Release Date:** February 14, 2026  
**Previous Version:** v0.3.0 (Smart Orderbook Execution)

---

## Overview

v0.4.0 introduces the **Strategy Framework** — a declarative, config-driven approach to defining and running trading strategies. Instead of subclassing strategy ABCs, you compose a `StrategyConfig` that declares _what_ to trade, _when_ to enter, _when_ to exit, and _how_ to execute. The `StrategyRunner` handles the mechanics.

This release also includes critical API endpoint fixes, dependency injection via `TradingContext`, dry-run simulation mode, and comprehensive test coverage (72/72 unit + 27/27 integration assertions).

---

## Key Features

### 1. Declarative Strategy Definitions

Strategies are data, not class hierarchies:

```python
from strategy import StrategyConfig, time_window, weekday_filter, min_available_margin_pct
from option_selection import LegSpec
from trade_lifecycle import profit_target, max_loss, max_hold_hours

config = StrategyConfig(
    name="short_strangle_daily",
    legs=[
        LegSpec("C", side=2, qty=0.1,
                strike_criteria={"type": "delta", "value": 0.25},
                expiry_criteria={"symbol": "28MAR26"}),
        LegSpec("P", side=2, qty=0.1,
                strike_criteria={"type": "delta", "value": -0.25},
                expiry_criteria={"symbol": "28MAR26"}),
    ],
    entry_conditions=[
        time_window(8, 20),
        weekday_filter(["mon", "tue", "wed", "thu"]),
        min_available_margin_pct(50),
    ],
    exit_conditions=[profit_target(50), max_loss(100), max_hold_hours(24)],
    max_concurrent_trades=1,
    cooldown_seconds=3600,
    check_interval_seconds=60,
)
```

### 2. Dependency Injection with TradingContext

All services live in a single container — no module-level globals:

```python
from strategy import build_context

ctx = build_context()
# ctx.auth, ctx.market_data, ctx.executor, ctx.rfq_executor,
# ctx.smart_executor, ctx.account_manager, ctx.position_monitor,
# ctx.lifecycle_manager
```

For tests, individual services can be replaced with mocks.

### 3. Entry Condition Factories

Seven composable entry conditions, mirroring the existing exit condition pattern:

| Factory | Description |
|---------|-------------|
| `time_window(start, end)` | UTC hour window |
| `weekday_filter(days)` | Day-of-week filter |
| `min_available_margin_pct(pct)` | Minimum free margin % |
| `min_equity(usd)` | Minimum account equity |
| `max_account_delta(limit)` | Account delta ceiling |
| `max_margin_utilization(pct)` | IM/equity ceiling |
| `no_existing_position_in(symbols)` | Block if positioned |

All conditions must return `True` before a strategy opens a trade.

### 4. LegSpec and resolve_legs()

Legs are specified declaratively and resolved to concrete symbols at runtime:

```python
from option_selection import LegSpec, resolve_legs

leg = LegSpec("C", side=2, qty=0.1,
              strike_criteria={"type": "delta", "value": 0.25},
              expiry_criteria={"symbol": "28MAR26"})

# resolve_legs() queries market data and returns TradeLeg objects
# with actual symbols like "BTCUSD-28MAR26-105000-C"
```

Supported strike criteria: `delta`, `closestStrike`, `spotdistance%`, `strike` (exact).

### 5. Dry-Run Mode

```python
config = StrategyConfig(
    name="test_strategy",
    legs=[...],
    dry_run=True,  # no real orders placed
)
```

- Fetches live prices from the exchange via `get_option_details()`
- Simulates full lifecycle (entry, position, exit evaluation)
- Logs estimated fill prices, PnL, and structure details
- Use for strategy validation before committing capital

### 6. Tick-Driven Execution

`StrategyRunner.tick()` is registered on `PositionMonitor.on_update()`:
1. Position monitor polls the exchange (configurable interval)
2. Calls all registered `runner.tick(snapshot)` callbacks
3. Runner checks entry conditions, creates trades, advances lifecycle
4. No extra threads, timers, or event queues

---

## Bug Fixes

### get_order_status 404 Error (Critical)
- **Problem:** `get_order_status()` used path-based URL `/open/option/order/{id}/v1` → 404
- **Fix:** Changed to `GET /open/option/order/singleQuery/v1?orderId={id}`

### Wrong Fill Field Name
- **Problem:** Code checked `executedQty` — field does not exist in API response
- **Fix:** Changed to `fillQty`

### Wrong Cancel State Code
- **Problem:** Code treated state 4 as CANCELED
- **Fix:** State 3 = CANCELED per API docs (state 4 = PRE_CANCEL)

### cancel_order Type Error
- **Problem:** `orderId` sent as string; API requires integer
- **Fix:** Added `int()` cast in `cancel_order()`

---

## File Changes

| File | Change |
|------|--------|
| `strategy.py` | **NEW** — 578 lines |
| `option_selection.py` | **Modified** — Added LegSpec, resolve_legs() |
| `trade_lifecycle.py` | **Modified** — strategy_id, _get_orderbook_price(), fixed fillQty/state codes |
| `trade_execution.py` | **Modified** — Fixed get_order_status endpoint, cancel_order int cast |
| `main.py` | **Rewritten** — DI wiring, strategy registration, signal handling |
| `tests/test_strategy_framework.py` | **NEW** — 72/72 assertions |
| `tests/test_live_dry_run.py` | **NEW** — 27/27 assertions |

---

## Testing Results

### Unit Tests (72/72)
| Test | Assertions | Description |
|------|-----------|-------------|
| 1. Config validation | 10 | StrategyConfig defaults, field types |
| 2. TradingContext | 9 | DI container wiring, build_context() |
| 3. Entry conditions | 16 | All 7 entry condition factories |
| 4. LegSpec & resolve_legs | 10 | Dataclass fields, resolution logic |
| 5. StrategyRunner | 12 | Tick lifecycle, cooldown, concurrency |
| 6. Dry-run mode | 8 | Simulated execution, no real orders |
| 7. Edge cases | 7 | Empty legs, no conditions, boundary values |

### Integration Tests (27/27)
| Test | Assertions | Description |
|------|-----------|-------------|
| 8a. Live dry-run | 11 | Real API, live pricing, no orders |
| 8b. Micro-trade | 16 | Full lifecycle in 11.3s, entry $95 exit $70 |

---

## Migration Guide

**main.py** has been rewritten. If you customised the old scheduler-based main.py:
1. Review the new `build_context()` + `StrategyRunner` pattern
2. Convert strategy parameters to `StrategyConfig` + `LegSpec`
3. Register runners on `PositionMonitor.on_update()`

---

## What's Next

- **Phase 5:** Multi-instrument support (futures, spot)
- **Phase 6:** Account alerts and pre-trade checks
- **Phase 7:** Web dashboard
- **Phase 8:** Persistence and crash recovery

See [docs/ARCHITECTURE_PLAN.md](docs/ARCHITECTURE_PLAN.md) for the complete roadmap.

---

## Project Statistics

| Metric | Value |
|--------|-------|
| Version | 0.4.0 |
| Release Date | February 14, 2026 |
| New Module | strategy.py (~578 lines) |
| Total Core Modules | 11 |
| Unit Tests | 72/72 |
| Integration Tests | 27/27 |
| API Fixes | 3 |
| Python | 3.9+ |

---

*CoincallTrader Development Team*
