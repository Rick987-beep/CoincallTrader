# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-02-13

### Added - Smart Orderbook Execution (Phase 3)
- **Smart multi-leg orderbook execution** (`multileg_orderbook.py`)
  - Proportional chunking algorithm splits orders into configurable chunks
  - Continuous quoting with multiple strategies (top-of-book, mid, mark)
  - Automatic repricing based on market movement thresholds
  - Aggressive fallback with limit orders crossing the spread
  - Position-aware fill tracking for both opens and closes
  - Early termination when target fills reached between chunks
- **SmartExecConfig** - 12+ configurable parameters for fine-tuning execution
- **ChunkState** - State machine tracking chunk execution (QUOTING → FALLBACK → COMPLETED)
- **LegChunkState** - Per-leg tracking within chunks (filled_qty, remaining_qty, is_filled)
- **SmartExecResult** - Comprehensive execution summary with fills, costs, timings

### Changed
- **Position tracking algorithm** - Now uses `abs(current - starting)` instead of `max(0, current - starting)`
  - Critical fix enabling close detection (decreasing positions)
  - Without this, closes would return negative deltas clamped to 0
  - Algorithm would loop indefinitely thinking nothing filled
- **LifecycleManager integration** - Smart mode now supported for opening trades
  - `execution_mode="smart"` with optional `smart_config`
  - Closing via direct SmartOrderbookExecutor call (LifecycleManager smart close TBD)

### Fixed
- Close position detection in smart execution
- Fill tracking for both increasing and decreasing positions
- Early chunk termination logic

### Testing
- **test_smart_butterfly.py** - Full lifecycle test (open + wait + close)
  - 3-leg butterfly with different quantities (0.2/0.4/0.2)
  - Opening: 57.1s, 100% fills, 2 chunks
  - Closing: 65.4s, 100% fills, 2 chunks, complete position closure
- **close_butterfly_now.py** - Emergency position closer with trade_side awareness

### Documentation
- Updated `docs/ARCHITECTURE_PLAN.md` with Phase 3 details
- Updated `README.md` with smart execution highlights
- Added comprehensive inline comments to `multileg_orderbook.py`

---

## [0.2.0] - 2026-02-10

### Added - Position Monitoring & Trade Lifecycle (Phase 2)
- **PositionSnapshot** - Frozen dataclass for single position with Greeks, PnL, mark price
- **AccountSnapshot** - Frozen dataclass for account state (equity, margins, aggregated Greeks)
- **PositionMonitor** - Background polling with thread-safe snapshot access and callbacks
- **TradeLifecycle** - State machine managing trade lifecycle (PENDING_OPEN → OPENING → OPEN → PENDING_CLOSE → CLOSING → CLOSED)
- **TradeLeg** - Individual leg tracking from intent through order, fill, and position
- **LifecycleManager** - Orchestrates trades with `tick()` callback pattern
- **Exit condition system** - Composable callables for exit logic
  - `profit_target(pct)` - Exit on structure PnL % of entry cost
  - `max_loss(pct)` - Exit on structure loss limit
  - `max_hold_hours(hours)` - Time-based exit
  - `account_delta_limit(threshold)` - Account-level Greek limit
  - `structure_delta_limit(threshold)` - Structure-level Greek limit
  - `leg_greek_limit(leg_idx, greek, op, value)` - Per-leg Greek threshold

### Changed
- Enhanced `account_manager.py` with position monitoring infrastructure
- `trade_lifecycle.py` supports both "limit" and "rfq" execution modes

### Documentation
- Created `docs/ARCHITECTURE_PLAN.md` Phase 2 documentation
- Added position monitoring and lifecycle examples

---

## [0.1.0] - 2026-02-09

### Added - RFQ Execution (Phase 1)
- **RFQ execution system** (`rfq.py`) for multi-leg block trades
  - `OptionLeg` - Dataclass for leg definition (instrument, side, qty)
  - `RFQQuote` - Quote from market maker with direction helpers
  - `RFQResult` - Execution result with cost, improvement metrics
  - `RFQExecutor` - Main executor with `execute(legs, action='buy'|'sell')`
- **Best-quote selection** - Automatically selects best quote from multiple market makers
- **Quote polling** - Configurable polling interval and max wait time
- **Minimum notional validation** - $50,000 minimum for RFQ trades

### Changed
- **auth.py** - Added `use_form_data` parameter for form-urlencoded content type
  - RFQ accept/cancel endpoints require this format
- **Symbol format** - Confirmed BTCUSD-{expiry}-{strike}-{C/P} format
- **Side parameters** - Using integers (1=BUY, 2=SELL) instead of strings

### Fixed
- RFQ quote interpretation (`MM SELL` = we buy, `MM BUY` = we sell)
- Content-Type handling for different API endpoints
- Quote direction logic in best-quote selection

### Documentation
- Created `docs/API_REFERENCE.md` with RFQ endpoint documentation
- Created `docs/ARCHITECTURE_PLAN.md` with full roadmap
- Added RFQ examples and integration tests

---

## [0.0.1] - 2026-02-08 (Initial)

### Added - Foundation
- Basic options trading functionality
- HMAC-SHA256 authentication (`auth.py`)
- Environment switching (testnet ↔ production) via `config.py`
- Market data retrieval (`market_data.py`)
- Option selection logic (`option_selection.py`)
- Basic order placement/cancellation (`trade_execution.py`)
- Scheduler-based execution (APScheduler in `main.py`)
- Config-driven strategy parameters
- Logging infrastructure

### Infrastructure
- Python 3.9+ compatibility
- Requirements.txt with core dependencies
- .env configuration support
- Basic project structure

---

## Version Comparison

| Version | Key Feature | Lines of Code | Test Coverage |
|---------|-------------|---------------|---------------|
| 0.3.0 | Smart Orderbook Execution | ~1000 (multileg_orderbook.py) | Butterfly lifecycle test |
| 0.2.0 | Position Monitoring & Lifecycle | ~800 (trade_lifecycle.py, account_manager.py) | Position monitor, lifecycle tests |
| 0.1.0 | RFQ Block Trades | ~800 (rfq.py) | RFQ integration tests |
| 0.0.1 | Foundation | ~500 (core modules) | Basic functionality |

---

## Migration Notes

### Upgrading to 0.3.0
- **LifecycleManager** now supports `execution_mode="smart"` with `smart_config` parameter
- **Position tracking** - No code changes required, but close detection now works correctly
- **Test files** - Moved to `tests/` folder (test_smart_butterfly.py, close_butterfly_now.py)

### Upgrading to 0.2.0
- **Exit conditions** - Replace old exit logic with new exit condition callables
- **Position tracking** - Use `PositionMonitor` instead of manual position queries
- **Trade management** - Use `LifecycleManager` instead of direct TradeExecutor calls

### Upgrading to 0.1.0
- **RFQ integration** - For large trades ($50k+), use RFQExecutor instead of direct orders
- **Authentication** - auth.py now supports both JSON and form-urlencoded content types
- **Symbol format** - Ensure using BTCUSD-{expiry}-{strike}-{C/P} format

---

## Upcoming Features

See [docs/ARCHITECTURE_PLAN.md](docs/ARCHITECTURE_PLAN.md) for the complete roadmap.

**Next up (Phase 4):**
- Scheduling & time-based conditions
- Weekday filters
- Time window definitions
- Expiry date awareness

---

*For detailed technical documentation, see individual module docstrings and [docs/](docs/) folder.*
