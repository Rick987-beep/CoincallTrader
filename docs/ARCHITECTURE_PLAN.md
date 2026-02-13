# CoincallTrader Architecture & Development Plan

**Version:** 1.4  
**Date:** February 14, 2026  
**Status:** Phase 4 Complete (Strategy Framework)

---

## Executive Summary

This document outlines the transformation of CoincallTrader from a simple options trading bot into a comprehensive, multi-instrument trading management system capable of running complex, time-aware strategies while maintaining code elegance and manageability.

---

## Current State Assessment

### What We Have
- ✅ Working authentication (`auth.py`) with HMAC-SHA256 signing
- ✅ Environment switching (testnet ↔ production) via `config.py`
- ✅ Market data retrieval (`market_data.py`) - options, BTC futures price
- ✅ Option selection logic (`option_selection.py`) - expiry/strike filtering
- ✅ Basic order placement/cancellation (`trade_execution.py`)
- ✅ Simple scheduler-based execution (APScheduler in `main.py`)
- ✅ Config-driven strategy parameters

### What's Missing
- ✅ Complete position lifecycle management
- ✅ **Smart orderbook execution for multi-leg trades** - Completed Feb 13, 2026
- ✅ **Strategy framework with entry/exit conditions** - Completed Feb 14, 2026
- ⚠️ Portfolio hierarchy (positions → portfolios → accounts)
- ⚠️ Multi-instrument support (futures, spot)
- ✅ **RFQ (Request for Quote) execution** - Completed Feb 9, 2026
- ✅ **Time-based trading conditions (scheduling)** - Included in strategy framework
- ⚠️ Web dashboard for monitoring
- ⚠️ Persistence and recovery

---

## Coincall API Capabilities (from official docs)

### Instruments Supported
| Instrument | Order Types | API Endpoints |
|------------|-------------|---------------|
| **Options** | LIMIT, POST_ONLY, BLOCK_TRADE | `/open/option/order/*` |
| **Futures** | LIMIT, MARKET, POST_ONLY, STOP_LIMIT, STOP_MARKET | `/open/futures/order/*` |
| **Spot** | LIMIT, MARKET, POST_ONLY | `/open/spot/trade/*` |

### RFQ System (Block Trades)
The Coincall RFQ system enables multi-leg block trades with the following workflow:

**As Taker (We request a quote):**
1. `POST /open/option/blocktrade/request/create/v1` - Create RFQ with legs
2. `GET /open/option/blocktrade/request/getQuotesReceived/v1` - Poll for quotes
3. `POST /open/option/blocktrade/request/accept/v1` - Execute a received quote

**As Maker (We provide quotes):**
1. Subscribe to RFQ stream via WebSocket (`dataType: "rfqMaker"`)
2. `POST /open/option/blocktrade/quote/create/v1` - Submit quote
3. Quote gets filled or expires

**RFQ Request Structure:**
```json
{
  "legs": [
    {"instrumentName": "BTCUSD-29OCT25-109000-C", "side": "BUY", "qty": "0.2"},
    {"instrumentName": "BTCUSD-29OCT25-109000-P", "side": "SELL", "qty": "0.2"}
  ]
}
```

**RFQ States:** `ACTIVE`, `CANCELLED`, `FILLED`, `EXPIRED`, `TRADED_AWAY`

### WebSocket Channels
| Channel | Data Type | Use Case |
|---------|-----------|----------|
| `order` | Private | Order status updates |
| `position` | Private | Position changes |
| `positionEvent` | Private | Position events (new) |
| `trade` | Private | Trade confirmations |
| `orderBook` | Public | Market depth |
| `lastTrade` | Public | Recent trades |
| `rfqMaker` / `rfqTaker` | Private | RFQ notifications |
| `quoteReceived` | Private | Incoming quotes (taker) |
| `blockTradeDetail` | Private | Block trade confirmations |

### Account Endpoints
| Endpoint | Purpose |
|----------|---------|
| `GET /open/account/summary/v1` | Account balance, equity, margin |
| `GET /open/account/wallet/v1` | Wallet holdings |
| `GET /open/futures/position/get/v1` | Futures positions |
| `GET /open/option/position/get/v1` | Options positions |

### Rate Limits
- Order placement: 60/s
- General API: Varies by endpoint

### Market Maker Features
- MMP (Market Maker Protection) flag on orders
- Countdown/cancel-all functionality
- Batch order operations (up to 40 orders)

---

## Target Architecture

### Design Principles
1. **Composition over inheritance** - Use protocols and mixins
2. **Dataclasses everywhere** - Simple, typed data containers
3. **Single responsibility** - Each module does one thing well
4. **Configuration-driven** - Strategies defined in config, not hardcoded
5. **Event-driven core** - Cleaner than pure polling
6. **Fail-safe defaults** - Conservative behavior when uncertain

### Proposed Directory Structure
```
CoincallTrader/
├── main.py                    # Event loop + scheduler entry point
├── config.py                  # Environment & strategy configuration
├── auth.py                    # Authentication (unchanged)
│
├── core/                      # Core abstractions
│   ├── __init__.py
│   ├── events.py              # Event types (MarketEvent, SignalEvent, etc.)
│   ├── event_queue.py         # Central event queue
│   └── scheduler.py           # Time-based triggers
│
├── portfolio/                 # Position & portfolio management
│   ├── __init__.py
│   ├── position.py            # Single instrument position
│   ├── portfolio.py           # Collection of positions
│   └── account.py             # Account-level view, margin checks
│
├── execution/                 # Order execution layer
│   ├── __init__.py
│   ├── order.py               # Order types, states, lifecycle
│   ├── executor.py            # Order routing & management
│   ├── rfq.py                 # RFQ-specific logic
│   └── algos.py               # Execution algorithms (TWAP, etc.)
│
├── data/                      # Market data layer
│   ├── __init__.py
│   ├── market_data.py         # Unified interface
│   ├── options.py             # Options-specific data
│   ├── futures.py             # Futures-specific data
│   ├── spot.py                # Spot-specific data
│   └── websocket.py           # WebSocket connections
│
├── strategies/                # Trading strategies
│   ├── __init__.py
│   ├── base.py                # Strategy ABC
│   ├── short_strangle.py      # Example: short strangle
│   └── delta_hedger.py        # Example: delta hedging
│
├── dashboard/                 # Web monitoring interface
│   ├── __init__.py
│   ├── app.py                 # FastAPI/Flask app
│   └── templates/             # HTML templates
│
├── persistence/               # State persistence
│   ├── __init__.py
│   └── database.py            # SQLite or JSON storage
│
├── docs/                      # Documentation
│   ├── ARCHITECTURE_PLAN.md   # This file
│   ├── API_REFERENCE.md       # Coincall API notes
│   └── STRATEGY_GUIDE.md      # How to write strategies
│
├── tests/                     # Test suite
├── logs/                      # Log files
└── archive/                   # Legacy code
```

**Estimated size:** ~15-20 Python files, ~2500-3500 lines total

---

## Requirements Specification

### 1. Trade Lifecycle Management

| Requirement | Priority | Description |
|-------------|----------|-------------|
| REQ-TL-01 | High | Dynamic instrument selection based on criteria (expiry, strike, delta) |
| REQ-TL-02 | High | Order placement with execution mode selection (limit, RFQ, aggressive) |
| REQ-TL-03 | ✅ **Done** | RFQ execution for multi-leg options trades |
| REQ-TL-04 | ✅ **Done** | Position tracking: link orders → fills → positions |
| REQ-TL-05 | ✅ **Done** | Conditional exit logic (profit targets, stop losses, time decay) |
| REQ-TL-06 | Medium | Partial fill handling and execution quality tracking |
| REQ-TL-07 | Medium | Order amendment and requoting |

### 2. Scheduling & Time-Based Conditions

| Requirement | Priority | Description |
|-------------|----------|-------------|
| REQ-SC-01 | ✅ **Done** | Time-of-day triggers (e.g., "open position at 08:00 UTC") |
| REQ-SC-02 | ✅ **Done** | Weekday filters (e.g., "no new positions on Friday") |
| REQ-SC-03 | High | Expiry awareness (close before expiry, roll positions) |
| REQ-SC-04 | Medium | Month-end logic (e.g., rebalancing triggers) |
| REQ-SC-05 | Medium | Calendar awareness (exchange holidays) |
| REQ-SC-06 | Low | Cron-like arbitrary scheduling expressions |

### 3. Portfolio Hierarchy & Architecture

| Requirement | Priority | Description |
|-------------|----------|-------------|
| REQ-PH-01 | ✅ **Done** | Position abstraction: PositionSnapshot in account_manager.py |
| REQ-PH-02 | ✅ **Done** | Structure grouping: TradeLifecycle groups legs (e.g., strangle = 1 lifecycle, 2 legs) |
| REQ-PH-03 | ✅ **Done** | Account abstraction: AccountSnapshot with equity, margins, aggregated Greeks |
| REQ-PH-04 | ✅ **Done** | Strategy abstraction: StrategyConfig + StrategyRunner in strategy.py |
| REQ-PH-05 | Medium | Event-driven core with typed events |
| REQ-PH-06 | ✅ **Done** | Structure-level and account-level Greeks aggregation |

### 4. Multi-Instrument Support

| Requirement | Priority | Description |
|-------------|----------|-------------|
| REQ-MI-01 | High | Futures trading (perpetuals and dated) |
| REQ-MI-02 | Medium | Spot trading (for hedging or cash management) |
| REQ-MI-03 | High | Unified order interface across all instruments |
| REQ-MI-04 | Medium | Cross-instrument hedging logic |

### 5. Account Information

| Requirement | Priority | Description |
|-------------|----------|-------------|
| REQ-AI-01 | High | Balance & equity queries |
| REQ-AI-02 | High | Margin monitoring with alerts |
| REQ-AI-03 | Medium | Wallet holdings per asset |
| REQ-AI-04 | Low | Historical P&L tracking |

### 6. Web Dashboard

| Requirement | Priority | Description |
|-------------|----------|-------------|
| REQ-WD-01 | Medium | Strategy status display (running, paused, stopped) |
| REQ-WD-02 | Medium | Open positions view with P&L and Greeks |
| REQ-WD-03 | Medium | Account health (margin level, equity curve) |
| REQ-WD-04 | Medium | Remote access (mobile-friendly) |
| REQ-WD-05 | Low | Manual intervention (pause strategy, close position) |

### 7. Persistence & Recovery

| Requirement | Priority | Description |
|-------------|----------|-------------|
| REQ-PR-01 | Medium | Persist open positions to database |
| REQ-PR-02 | Medium | Persist order history |
| REQ-PR-03 | Medium | Persist strategy state |
| REQ-PR-04 | Medium | Restart recovery: reload state on startup |

---

## Implementation Phases

### Phase 0: Foundation Cleanup (1-2 days)
**Goal:** Introduce event-driven architecture without breaking existing functionality.

**Tasks:**
1. Create `core/events.py` with event types:
   - `MarketEvent` - New market data available
   - `SignalEvent` - Strategy wants to trade
   - `OrderEvent` - Order to be placed
   - `FillEvent` - Order was filled
2. Create `core/event_queue.py` with simple Queue wrapper
3. Refactor `main.py` to use event loop pattern alongside scheduler

**Deliverables:**
- [ ] `core/events.py`
- [ ] `core/event_queue.py`
- [ ] Updated `main.py`

---

### Phase 1: RFQ Execution ✅ COMPLETE (Feb 9, 2026)
**Goal:** Enable RFQ-based execution for multi-leg options trades.

**Implementation Summary:**
Created `rfq.py` module (~800 lines) with complete RFQ lifecycle management.

**Key Classes:**
- `OptionLeg` - Dataclass for leg definition (instrument, side, qty)
- `RFQQuote` - Quote from market maker with `is_we_buy`/`is_we_sell` properties
- `RFQResult` - Execution result with success, total_cost, improvement_pct
- `RFQExecutor` - Main executor with `execute(legs, action='buy'|'sell')`

**Key Learnings:**
1. RFQs must always be submitted with legs as `side: "BUY"` to Coincall
2. Market makers respond with two-way quotes (both BUY and SELL)
3. Quote `side` indicates MM's action: `MM SELL` = we buy, `MM BUY` = we sell
4. Accept/Cancel endpoints require `application/x-www-form-urlencoded` content type
5. Minimum notional: $50,000 (sum of strike values)
6. Quotes typically arrive within 3-5 seconds

**API Endpoints Used:**
- `POST /open/option/blocktrade/request/create/v1` (JSON)
- `GET /open/option/blocktrade/request/getQuotesReceived/v1`
- `POST /open/option/blocktrade/request/accept/v1` (form-urlencoded)
- `POST /open/option/blocktrade/request/cancel/v1` (form-urlencoded)

**Deliverables:**
- [x] `rfq.py` - Complete RFQ execution module
- [x] `tests/test_rfq_integration.py` - Integration tests
- [x] Updated `auth.py` with `use_form_data` support
- [x] Updated `docs/API_REFERENCE.md` with RFQ documentation

---

### Phase 2: Position Monitoring & Trade Lifecycle ✅ COMPLETE (Feb 10, 2026)
**Goal:** Monitor positions with live Greeks, and orchestrate trades through their full lifecycle (open → manage → close).

**Implementation Summary:**

**Part A: Position Monitoring** (added to `account_manager.py`):
- `PositionSnapshot` — frozen dataclass for a single position (Greeks, PnL, mark price)
- `AccountSnapshot` — frozen dataclass for full account state (equity, margins, aggregated Greeks)
- `PositionMonitor` — background polling with thread-safe snapshot access and callbacks
- Uses `upnlByMarkPrice` / `roiByMarkPrice` for accurate options PnL

**Part B: Trade Lifecycle** (new file `trade_lifecycle.py`):
- `TradeState` enum: PENDING_OPEN → OPENING → OPEN → PENDING_CLOSE → CLOSING → CLOSED | FAILED
- `TradeLeg` — tracks a single leg from intent through order, fill, and position
- `TradeLifecycle` — groups legs into a trade with exit conditions and execution mode
- `LifecycleManager` — state machine that advances trades via `tick()` callback
  - Supports "limit" mode (per-leg orders via TradeExecutor) and "rfq" mode (atomic via RFQExecutor)
  - `tick()` hooks into PositionMonitor.on_update() for automatic advancement
  - `force_close()` and `cancel()` for manual intervention

**Exit Condition System:**
Exit conditions are callables `(AccountSnapshot, TradeLifecycle) -> bool`.
Factory functions provided for common patterns:
- `profit_target(pct)` — structure PnL as % of entry cost
- `max_loss(pct)` — structure loss limit
- `max_hold_hours(hours)` — time-based exit
- `account_delta_limit(threshold)` — account-level Greek limit
- `structure_delta_limit(threshold)` — structure-level Greek limit
- `leg_greek_limit(leg_index, greek, op, value)` — per-leg Greek threshold
- Custom lambdas/functions for anything else

**Key Design Decisions:**
1. Flat architecture — no Portfolio/Account wrapper classes; lifecycle IS the trade
2. Callable exit conditions instead of Strategy ABC — composable, testable, no class hierarchy
3. `tick()` model — driven by PositionMonitor, no extra threads or event queues
4. Multi-leg native — Iron Condor = one lifecycle with 4 legs

**Deliverables:**
- [x] `PositionSnapshot`, `AccountSnapshot`, `PositionMonitor` in `account_manager.py`
- [x] `trade_lifecycle.py` — TradeState, TradeLeg, TradeLifecycle, LifecycleManager
- [x] Exit condition factories (profit, loss, time, Greeks at all levels)
- [x] `tests/test_position_monitor.py` — position monitoring integration test
- [x] `tests/test_trade_lifecycle.py` — lifecycle dry-run and live test

---

### Phase 3: Smart Orderbook Execution ✅ COMPLETE (Feb 13, 2026)
**Goal:** Enable smart multi-leg orderbook execution with chunking, continuous quoting, and aggressive fallback for trades below RFQ minimum ($50k notional).

**Implementation Summary:**

**Module:** `multileg_orderbook.py` (~1000 lines)

**Core Algorithm:**
1. **Chunk Calculation** — Split total order into N proportional chunks (e.g., 0.4 contracts → 2 chunks of 0.2 each, maintaining leg ratios)
2. **Position-Aware Tracking** — Track delta from starting position using `abs(current - starting)` to handle:
   - Opens: Starting=0.0, Target=0.2 → fill delta 0.2
   - Closes: Starting=0.2, Target=0.2 → fill delta 0.2 (position goes to 0)
3. **Per-Chunk Execution:**
   - **Phase A (Quoting):** Place limit orders at calculated prices for `time_per_chunk` seconds
     - Continuous repricing every `reprice_interval` seconds (min 10s)
     - Cancel and reprice when market moves beyond `reprice_price_threshold`
     - Stop quoting legs individually as they fill (others continue)
   - **Phase B (Aggressive Fallback):** If not filled, use aggressive limit orders crossing the spread
     - Multiple retry attempts with configurable wait times
     - Exits early when all legs filled
4. **Early Termination** — Between chunks, check if target already reached and stop processing remaining chunks

**Key Classes:**
- `SmartExecConfig` — Configuration with 12+ parameters (chunk_count, time_per_chunk, quoting_strategy, etc.)
- `LegChunkState` — Per-leg state within a chunk (filled_qty, starting_position, remaining_qty, is_filled)
- `ChunkState` — State machine for chunk execution (QUOTING → FALLBACK → COMPLETED)
- `SmartExecResult` — Execution summary (success, chunks_completed, fills, costs, fallback_count)
- `SmartOrderbookExecutor` — Main executor class integrating with TradeExecutor and AccountManager

**Quoting Strategies:**
- `"top_of_book"` — Use orderbook bid/ask directly
- `"top_of_book_offset_pct"` — Offset from top by spread_pct (e.g., ±0.5%)
- `"mid"` — Use (bid + ask) / 2
- `"mark"` — Use mark price (fallbacks to mid if unavailable)

**Critical Fixes During Development:**
1. **Close Detection Bug** — Changed fill tracking from `max(0.0, current - starting)` to `abs(current - starting)` 
   - Without this, closes would return negative deltas clamped to 0
   - Algorithm would think nothing filled and loop indefinitely
   - Fix enabled both opens (0→0.1) and closes (0.2→0.1) to be tracked correctly

**Integration with LifecycleManager:**
- Opening trades: Uses `LifecycleManager.create()` with `execution_mode="smart"` and `smart_config`
- Closing trades: Currently direct call to `SmartOrderbookExecutor.execute_smart_multi_leg()` 
  - LifecycleManager doesn't yet support smart close mode (future enhancement)

**Testing Results:**
- ✅ Butterfly spread (3 legs, different quantities: 0.2/0.4/0.2)
  - Opening: 57.1s execution, 100% fills, 2 chunks
  - Closing: 65.4s execution, 100% fills, 2 chunks, positions fully closed
- ✅ Proportional chunking maintains leg ratios
- ✅ Mid-price quoting reduces slippage vs aggressive orders
- ✅ Early termination when fills complete
- ✅ Handles both increasing positions (opens) and decreasing positions (closes)

**Configuration Example:**
```python
smart_config = SmartExecConfig(
    chunk_count=2,              # Split into 2 chunks
    time_per_chunk=20.0,        # 20 seconds per chunk
    quoting_strategy="mid",     # Quote at mid-price
    reprice_interval=10.0,      # Reprice every 10s
    reprice_price_threshold=0.1,# Reprice if price moves >0.1
    min_order_qty=0.01,         # Minimum order size
    aggressive_attempts=10,     # Max fallback attempts
    aggressive_wait_seconds=5.0,# Wait 5s per attempt
    aggressive_retry_pause=1.0  # 1s between attempts
)
```

**API Integration:**
- Uses TradeExecutor for order placement/cancellation (limit orders)
- Uses AccountManager for position polling (fill detection)
- Uses market_data.get_option_orderbook() for pricing

**Deliverables:**
- [x] `multileg_orderbook.py` — Complete smart execution module
- [x] `tests/test_smart_butterfly.py` — Full lifecycle test (open + close)
- [x] `tests/close_butterfly_now.py` — Emergency close utility
- [x] Position-aware fill tracking for opens and closes
- [x] Comprehensive logging and execution reporting

---

### Phase 4: Strategy Framework ✅ COMPLETE (Feb 14, 2026)
**Goal:** Enable declarative, config-driven strategy definitions with composable entry/exit conditions, dependency injection, and dry-run mode.

**Implementation Summary:**

**New Module:** `strategy.py` (~578 lines)

**Core Classes:**
- `TradingContext` — Dependency injection container holding every service (auth, market data, executor, RFQ, smart executor, account manager, position monitor, lifecycle manager). Strategies and tests receive this instead of importing globals.
- `StrategyConfig` — Declarative strategy definition: legs (`LegSpec` list), entry conditions, exit conditions, execution mode, concurrency limits, cooldown, and dry-run flag.
- `StrategyRunner` — Tick-driven executor: checks entry conditions, resolves `LegSpec`s to concrete symbols via `resolve_legs()`, creates trade lifecycles, delegates to `LifecycleManager`.
- `build_context()` — Factory function that wires all services from `config.py` settings.

**Entry Condition Factories:**
| Factory | Description |
|---------|-------------|
| `time_window(start, end)` | UTC hour window (e.g., 8–20) |
| `weekday_filter(days)` | Day-of-week filter (e.g., Mon–Thu) |
| `min_available_margin_pct(pct)` | Minimum free margin % |
| `min_equity(usd)` | Minimum account equity |
| `max_account_delta(limit)` | Account delta threshold |
| `max_margin_utilization(pct)` | IM/equity ceiling |
| `no_existing_position_in(symbols)` | Block if already positioned |

**Modified Module:** `option_selection.py` — Added:
- `LegSpec` dataclass — declares option_type, side, qty, strike_criteria, expiry_criteria, underlying
- `resolve_legs()` — converts `list[LegSpec]` to `list[TradeLeg]` by querying market data

**Modified Module:** `trade_lifecycle.py` — Added:
- `strategy_id` field on `TradeLifecycle` for per-strategy tracking
- `_get_orderbook_price()` helper for live pricing
- `get_trades_for_strategy()` and `active_trades_for_strategy()` on `LifecycleManager`

**Modified Module:** `trade_execution.py` — Fixed:
- `get_order_status()` endpoint: `/open/option/order/singleQuery/v1?orderId={id}`
- `cancel_order()` sends orderId as `int()` per API spec
- Fill field: `fillQty` (was `executedQty`), state 3 = CANCELED (was 4)

**Modified Module:** `main.py` — Rewritten:
- Uses `build_context()` for service wiring
- Registers `StrategyRunner` instances on `PositionMonitor.on_update()`
- Signal handling (SIGINT/SIGTERM) for graceful shutdown

**Dry-Run Mode:**
- `StrategyConfig(dry_run=True)` enables simulated execution
- Uses `get_option_details()` for live pricing without placing orders
- Logs entry/exit prices, estimated PnL, and position details
- Full lifecycle reporting with `_execute_dry_run()`

**Key Design Decisions:**
1. No Strategy ABC — strategies are `StrategyConfig` data, not class hierarchies
2. Entry conditions mirror exit conditions — both `Callable[[AccountSnapshot, ...], bool]`
3. `resolve_legs()` decouples leg specification from symbol resolution
4. DI container enables testing with mock services
5. `StrategyRunner.tick()` is registered on PositionMonitor — no extra threads

**Testing Results:**
- Tests 1–7 (unit): 72/72 assertions passed
  - Config validation, context building, entry condition logic, LegSpec/resolve_legs, runner lifecycle, dry-run mode, edge cases
- Test 8a (integration dry-run): 11/11 passed — live pricing, no orders
- Test 8b (integration micro-trade): 16/16 passed — full lifecycle: opening → open → pending_close → closing → closed in 11.3s

**Deliverables:**
- [x] `strategy.py` — TradingContext, StrategyConfig, StrategyRunner, entry conditions, build_context()
- [x] `option_selection.py` updates — LegSpec dataclass, resolve_legs()
- [x] `trade_lifecycle.py` updates — strategy_id, _get_orderbook_price(), per-strategy queries
- [x] `trade_execution.py` fixes — correct endpoint, field names, state codes
- [x] `main.py` rewrite — DI wiring, strategy registration, signal handling
- [x] `tests/test_strategy_framework.py` — 72/72 unit test assertions
- [x] `tests/test_live_dry_run.py` — 27/27 integration test assertions
- [x] Workspace cleanup — 6 legacy files moved to archive/

---

### Phase 5: Multi-Instrument Support (2-3 days)
**Goal:** Extend trading to futures and spot markets.

**Tasks:**
1. Create `data/futures.py`:
   - `get_futures_instruments()`
   - `get_futures_orderbook(symbol)`
   - `get_futures_position()`

2. Create `data/spot.py`:
   - `get_spot_instruments()`
   - `get_spot_orderbook(symbol)`

3. Extend `execution/executor.py`:
   - `place_futures_order()`
   - `place_spot_order()`

4. Create unified `Instrument` base class

**API Endpoints Used:**
- `GET /open/futures/market/instruments/v1`
- `POST /open/futures/order/create/v1`
- `GET /open/spot/market/instruments`
- `POST /open/spot/trade/order/v1`

**Deliverables:**
- [ ] `data/futures.py`
- [ ] `data/spot.py`
- [ ] Extended executor
- [ ] Integration tests

---

### Phase 6: Account Information (1 day)
**Goal:** Consolidate and enhance account-level information.

**Tasks:**
1. Consolidate `account_manager.py` into `portfolio/account.py`
2. Add margin monitoring with configurable alerts
3. Implement account health checks before trading

**Deliverables:**
- [ ] Enhanced `portfolio/account.py`
- [ ] Margin alert system
- [ ] Pre-trade account checks

---

### Phase 7: Web Dashboard (2-3 days)
**Goal:** Create a simple web interface for monitoring.

**Tasks:**
1. Create FastAPI app in `dashboard/app.py`:
   - `GET /` - Dashboard home
   - `GET /api/strategies` - Running strategies
   - `GET /api/positions` - Current positions
   - `GET /api/account` - Account info
   - `GET /api/logs` - Recent log entries

2. Create simple HTML template with auto-refresh

3. Optional: Add authentication for remote access

**Deliverables:**
- [ ] `dashboard/app.py`
- [ ] HTML templates
- [ ] Basic CSS styling

---

### Phase 8: Persistence & Recovery (1-2 days)
**Goal:** Enable state persistence and crash recovery.

**Tasks:**
1. Create `persistence/database.py` with SQLite backend:
   - `save_position(position)`
   - `load_positions() -> List[Position]`
   - `save_order(order)`
   - `save_strategy_state(strategy_id, state)`

2. Implement startup recovery:
   - Load persisted positions
   - Reconcile with exchange state
   - Resume strategies

**Deliverables:**
- [ ] `persistence/database.py`
- [ ] Database schema
- [ ] Startup recovery logic

---

## Priority Order Summary

| Priority | Phase | Effort | Why This Order |
|----------|-------|--------|----------------|
| 1 | **Phase 1: RFQ** | ✅ Done | Block trade execution for multi-leg options |
| 2 | **Phase 2: Position Monitoring & Lifecycle** | ✅ Done | Live monitoring, trade state machine, exit conditions |
| 3 | **Phase 3: Smart Orderbook Execution** | ✅ Done | Chunked orderbook execution for trades below RFQ minimum |
| 4 | **Phase 4: Strategy Framework** | ✅ Done | Declarative strategies, entry/exit conditions, DI, dry-run |
| 5 | Phase 5: Multi-Instrument | 2-3 days | Futures and spot support |
| 6 | Phase 6: Account Info | 1 day | Margin alerts, pre-trade checks |
| 7 | Phase 7: Dashboard | 2-3 days | Web monitoring interface |
| 8 | Phase 8: Persistence | 1-2 days | State persistence and crash recovery |

**Total estimated effort:** 15-22 days of focused development (12-14 days completed)

---

## Open Questions

1. **Persistence format:** SQLite vs JSON files vs something else?
2. **Dashboard auth:** Simple password vs OAuth vs VPN-only access?
3. **Concurrent strategies:** Expected to run 2-3 or 10+?
4. **Deployment target:** VPS, local machine, cloud?
5. **Backtesting:** Is this a future requirement?

---

## Appendix: Coincall API Quick Reference

### Common Response Codes
- `0` - Success
- `10534` - Order size exceeds limit
- `10540` - Order expired
- `10558` - Less than min amount

### Order States
- `0` - NEW
- `1` - FILLED
- `2` - PARTIALLY_FILLED
- `3` - CANCELED
- `6` - INVALID
- `10` - CANCEL_BY_EXERCISE

### Trade Types
- `1` - LIMIT
- `2` - MARKET
- `3` - POST_ONLY
- `4` - STOP_LIMIT
- `5` - STOP_MARKET
- `14` - BLOCK_TRADE (RFQ)

### Trade Sides
- `1` - BUY
- `2` - SELL

---

*Document maintained by the CoincallTrader development team.*
