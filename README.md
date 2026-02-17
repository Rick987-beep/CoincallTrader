# CoincallTrader

A strategy-driven options trading system for the [Coincall](https://www.coincall.com/) exchange.  
Strategies are declared as configuration — not coded as classes — and the framework handles entry checks, leg resolution, execution, lifecycle management, and exits automatically.

**Current version:** 0.4.0 — Strategy Framework

## Highlights

- **Declarative strategy framework**: Define _what_ to trade, _when_ to enter, _when_ to exit, and _how_ to execute — all via `StrategyConfig` ✅
- **Dependency injection**: `TradingContext` wires every service; strategies and tests receive the same container ✅
- **Entry conditions**: Composable factories — `time_window()`, `weekday_filter()`, `min_available_margin_pct()`, `min_equity()`, `max_account_delta()`, `max_margin_utilization()`, `no_existing_position_in()` ✅
- **Leg specifications**: `LegSpec` dataclass resolves strike/expiry criteria into concrete symbols at runtime ✅
- **Trade lifecycle**: Full open → manage → close state machine with automatic exit evaluation ✅
- **Exit conditions**: `profit_target()`, `max_loss()`, `max_hold_hours()`, `account_delta_limit()`, `structure_delta_limit()`, `leg_greek_limit()` ✅
- **Three execution modes**: Limit orders, RFQ block trades ($50 k+), and smart orderbook (chunked quoting with aggressive fallback) ✅
- **Dry-run mode**: Live pricing from the exchange, no orders placed ✅
- **Position monitoring**: Background polling with live Greeks, PnL, account snapshots, and tick-driven strategy execution ✅
- **Multi-leg native**: Strangles, Iron Condors, Butterflies — any structure as one lifecycle ✅
- **HMAC-SHA256 authentication**: Secure API access via `auth.py` ✅

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure environment
Copy `.env.example` to `.env` and set your API keys:
```
TRADING_ENVIRONMENT=production   # or testnet

COINCALL_API_KEY_PROD=your_key
COINCALL_API_SECRET_PROD=your_secret
```

### 3. Define a strategy in `main.py`
```python
from option_selection import LegSpec
from trade_lifecycle import profit_target, max_loss, max_hold_hours
from strategy import (
    build_context, StrategyConfig, StrategyRunner,
    time_window, weekday_filter, min_available_margin_pct,
)

ctx = build_context()

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
    exit_conditions=[
        profit_target(50),
        max_loss(100),
        max_hold_hours(24),
    ],
    max_concurrent_trades=1,
    cooldown_seconds=3600,
    check_interval_seconds=60,
)

runner = StrategyRunner(config, ctx)
ctx.position_monitor.on_update(runner.tick)
ctx.position_monitor.start()
```

### 4. Run
```bash
python main.py          # live trading
# or set dry_run=True in StrategyConfig for simulated execution
```

## Project Structure

```
CoincallTrader/
├── main.py                 # Entry point — wires context, registers runners
├── strategy.py             # Strategy framework (TradingContext, StrategyConfig, StrategyRunner)
├── config.py               # Environment & global config (.env loading)
├── auth.py                 # HMAC-SHA256 API authentication
├── market_data.py          # Market data (option chains, orderbooks, BTC price)
├── option_selection.py     # LegSpec, resolve_legs(), select_option(), find_option()
├── trade_execution.py      # Order placement, cancellation, status queries
├── trade_lifecycle.py      # TradeState machine, TradeLeg, LifecycleManager, exit conditions
├── multileg_orderbook.py   # Smart chunked multi-leg execution
├── rfq.py                  # RFQ block-trade execution ($50k+ notional)
├── account_manager.py      # AccountSnapshot, PositionMonitor, margin/equity queries
├── docs/
│   ├── ARCHITECTURE_PLAN.md   # Roadmap, phases, requirements
│   └── API_REFERENCE.md       # Coincall API & internal module reference
├── tests/
│   ├── test_strategy_framework.py  # Unit tests — config, context, conditions (72/72)
│   └── test_live_dry_run.py        # Integration — dry-run + micro-trade (27/27)
├── logs/                   # Runtime logs (gitignored)
├── archive/                # Legacy code (gitignored)
├── CHANGELOG.md
├── RELEASE_NOTES.md
└── requirements.txt
```

## Architecture Overview

```
┌──────────────────────────────────────────────────────┐
│  main.py                                             │
│  build_context() → TradingContext (DI container)     │
│  StrategyRunner.tick() registered on PositionMonitor │
└────────────────┬─────────────────────────────────────┘
                 │ on each tick
   ┌─────────────▼──────────────┐
   │  StrategyRunner            │
   │  • check entry conditions  │
   │  • resolve LegSpecs        │
   │  • create trade lifecycle  │
   │  • LifecycleManager.tick() │
   │    evaluates exit conds    │
   └─────┬─────────────┬───────┘
         │             │
   ┌─────▼─────┐ ┌────▼───────────┐
   │ option_   │ │ trade_         │
   │ selection │ │ lifecycle.py   │
   │ LegSpec → │ │ TradeState FSM │
   │ TradeLeg  │ │ exit conditions│
   │ find_     │ └──────┬─────────┘
   │ option()  │
                        │
         ┌──────────────┼──────────────┐
         │              │              │
   ┌─────▼─────┐ ┌─────▼─────┐ ┌─────▼──────────┐
   │ trade_    │ │ rfq.py    │ │ multileg_      │
   │ execution │ │ $50k+     │ │ orderbook.py   │
   │ (limit)   │ │ block     │ │ smart chunked  │
   └───────────┘ └───────────┘ └────────────────┘
```

## Configuration

### StrategyConfig fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Unique strategy identifier |
| `legs` | `list[LegSpec]` | What to trade — option type, side, qty, strike/expiry criteria |
| `entry_conditions` | `list[EntryCondition]` | All must pass before opening |
| `exit_conditions` | `list[ExitCondition]` | Any triggers a close |
| `execution_mode` | `str` | `"limit"`, `"rfq"`, or `"smart"` |
| `max_concurrent_trades` | `int` | Max simultaneous open trades |
| `cooldown_seconds` | `float` | Delay between new trades |
| `check_interval_seconds` | `float` | Throttle between entry checks |
| `dry_run` | `bool` | Simulate with live prices, no real orders |

### LegSpec fields

| Field | Type | Description |
|-------|------|-------------|
| `option_type` | `str` | `"C"` or `"P"` |
| `side` | `int` | `1` = BUY, `2` = SELL |
| `qty` | `float` | Contract quantity |
| `strike_criteria` | `dict` | `{"type": "delta", "value": 0.25}`, `{"type": "closestStrike"}`, `{"type": "spotdistance%", "value": 10}` |
| `expiry_criteria` | `dict` | `{"symbol": "28MAR26"}` |
| `underlying` | `str` | Default `"BTC"` |

### find_option() — Compound Selection

For strategies that need multiple simultaneous constraints, use `find_option()` instead of `LegSpec` + `select_option()`:

```python
from option_selection import find_option

# OTM put, 6-13 days, 0.5%+ below ATM, delta between -0.45 and -0.15
option = find_option(
    option_type="P",
    expiry={"min_days": 6, "max_days": 13, "target": "near"},
    strike={"below_atm": True, "min_distance_pct": 0.5},
    delta={"min": -0.45, "max": -0.15},
    rank_by="delta_mid",
)
# Returns enriched dict: symbolName, strike, delta, days_to_expiry, distance_pct, index_price
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `underlying` | `str` | Default `"BTC"` |
| `option_type` | `str` | `"C"` or `"P"` |
| `expiry` | `dict` | `min_days`, `max_days`, `target` (`"near"`/`"far"`/`"mid"`) |
| `strike` | `dict` | `below_atm`, `above_atm`, `min_strike`, `max_strike`, `min_distance_pct`, `max_distance_pct`, `min_otm_pct`, `max_otm_pct` |
| `delta` | `dict` | `min`, `max`, `target` |
| `rank_by` | `str` | `"delta_mid"`, `"delta_target"`, `"strike_atm"`, `"strike_otm"`, `"strike_itm"` |

### Entry condition factories

| Factory | Description |
|---------|-------------|
| `time_window(start_hour, end_hour)` | UTC hour window |
| `weekday_filter(days)` | e.g. `["mon", "tue", "wed", "thu"]` |
| `min_available_margin_pct(pct)` | Minimum free margin % |
| `min_equity(usd)` | Minimum account equity |
| `max_account_delta(limit)` | Account delta threshold |
| `max_margin_utilization(pct)` | IM/equity ceiling |
| `no_existing_position_in(symbols)` | Block if already positioned |

## Testing

```bash
# Unit tests (72 assertions)
python -m pytest tests/test_strategy_framework.py -v

# Integration tests — dry-run + micro-trade (27 assertions)
python -m pytest tests/test_live_dry_run.py -v

# Compound option selection test (32 assertions, hits live API)
python3 tests/test_complex_option_selection.py
```

## Documentation

- **[Architecture Plan](docs/ARCHITECTURE_PLAN.md)** — Phases, requirements, and roadmap
- **[API Reference](docs/API_REFERENCE.md)** — Coincall API endpoints and internal module docs
- **[Changelog](CHANGELOG.md)** — Version history
- **[Release Notes](RELEASE_NOTES.md)** — Detailed v0.4.0 release notes

## Roadmap

1. ✅ Foundation — auth, config, market data, option selection
2. ✅ RFQ execution — block trades with best-quote selection
3. ✅ Position monitoring — live Greeks, PnL, account snapshots
4. ✅ Trade lifecycle — open → manage → close state machine
5. ✅ Smart orderbook execution — chunked quoting with aggressive fallback
6. ✅ **Strategy framework** — declarative configs, entry/exit conditions, DI, dry-run
7. ⬜ Multi-instrument — futures, spot trading
8. ⬜ Web dashboard — monitoring interface
9. ⬜ Persistence & recovery — state persistence, crash recovery

## Disclaimer

⚠️ **Trading involves significant risk of loss.** This software is provided as-is, without warranty. Use at your own risk. Always test on testnet or in dry-run mode before live production trading.