# CoincallTrader

A comprehensive trading management system for the Coincall exchange, supporting options, futures, and spot trading with sophisticated strategy execution.

## Project Status

🔧 **In Development** - Evolving from a simple options bot to a full trading management system.

See [docs/ARCHITECTURE_PLAN.md](docs/ARCHITECTURE_PLAN.md) for the complete roadmap and requirements.

## Highlights

- **Trade lifecycle management**: Full open → manage → close cycle with state machine ✅
- **Position monitoring**: Live Greeks, PnL, and account snapshots with background polling ✅
- **RFQ execution**: Block trades for multi-leg options strategies with best-quote selection ✅
- **Exit conditions**: Composable callables — profit target, max loss, time, Greeks limits ✅
- **Multi-leg native**: Strangles, Iron Condors, spreads — any structure as one lifecycle ✅
- **Environment switching**: Seamless testnet ↔ production
- **HMAC-SHA256 authentication**: Secure API access via `auth.py`
- **Config-driven strategies**: Parameters defined in `config.py`
- **Modular architecture**: Clean separation of concerns

## Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Environment
Copy `.env.example` to `.env` and fill in your API keys:
```
TRADING_ENVIRONMENT=testnet   # or production

# Testnet
COINCALL_API_KEY_TEST=your_testnet_key
COINCALL_API_SECRET_TEST=your_testnet_secret

# Production
COINCALL_API_KEY_PROD=your_production_key
COINCALL_API_SECRET_PROD=your_production_secret
```

### 3. Run the Bot
```bash
python main.py
```

## Project Structure

```
CoincallTrader/
├── main.py              # Entry point with scheduler
├── config.py            # Environment & strategy config
├── auth.py              # API authentication
├── market_data.py       # Market data retrieval
├── option_selection.py  # Option filtering logic
├── trade_execution.py   # Order management
├── rfq.py               # RFQ block-trade execution (multi-leg)
├── trade_lifecycle.py   # Trade lifecycle state machine
├── account_manager.py   # Account info, position monitoring, snapshots
├── docs/                # Documentation
│   ├── ARCHITECTURE_PLAN.md  # Development roadmap
│   └── API_REFERENCE.md      # Coincall API notes
├── tests/               # Unit tests
├── logs/                # Trading logs
└── archive/             # Legacy code & integration tests
```

## Configuration

Edit `config.py` to adjust:

| Section | Purpose |
|---------|---------|
| `POSITION_CONFIG` | Strategy legs, expiry criteria |
| `TRADING_CONFIG` | Intervals, timeouts, retries |
| `RISK_CONFIG` | Position limits, margin thresholds |
| `OPEN_POSITION_CONDITIONS` | Entry criteria |
| `CLOSE_POSITION_CONDITIONS` | Exit criteria |

## Documentation

- **[Architecture Plan](docs/ARCHITECTURE_PLAN.md)** - Full roadmap, requirements, and implementation phases
- **[API Reference](docs/API_REFERENCE.md)** - Coincall API endpoints and examples

## Roadmap

1. ✅ Basic options trading
2. ✅ RFQ execution (block trades with best-quote selection)
3. ✅ Position monitoring (live Greeks, PnL, account snapshots)
4. ✅ Trade lifecycle management (open → manage → close state machine)
5. ⬜ Scheduling & time-based conditions
6. ⬜ Multi-instrument (futures, spot)
7. ⬜ Web dashboard
8. ⬜ Persistence & recovery

## API Documentation

Official Coincall API: https://docs.coincall.com/

## Disclaimer

⚠️ **Trading involves significant risk of loss.** This software is provided as-is, without warranty. Use at your own risk. Always test thoroughly on testnet before production use.