# tardis_options — Historic Deribit Option Data

Pull historic tick-level Deribit option data from [tardis.dev](https://tardis.dev) and query it for backtesting.

## Workflow

```
1. Download   →   raw .csv.gz (~4.5 GB per day, all instruments)
2. Extract    →   filtered .parquet (~87 MB for BTC 0DTE+1DTE)
3. Query      →   HistoricOptionChain for instant lookups
```

### 1. Download

```bash
python -m analysis.tardis_options.download 2025-03-01
```

Free tier: 1st of each month only, no API key needed.
Produces `data/options_chain_2025-03-01.csv.gz`.

### 2. Extract

```bash
python -m analysis.tardis_options.extract --date 2025-03-01 --expiries 2MAR25 3MAR25
```

Scans the full gzip, keeps only BTC rows matching the expiries, writes compressed parquet.

Options:
- `--expiries 2MAR25 3MAR25` — specific expiry strings
- `--all-btc` — keep all BTC expiries

### 3. Query

```python
from analysis.tardis_options import HistoricOptionChain

chain = HistoricOptionChain("analysis/tardis_options/data/btc_0dte_1dte_2025-03-01.parquet")

# Single option
opt = chain.get("2025-03-01 12:00", "2MAR25", 85000, is_call=True)

# ATM straddle
call, put = chain.get_atm_straddle("2025-03-01 12:00", "2MAR25")

# Full chain snapshot (all strikes at one point in time)
snap = chain.get_chain("2025-03-01 14:00", "2MAR25")

# Spot price
spot = chain.get_spot("2025-03-01 15:30")

# Iterate minute by minute
for minute_ts in chain.minutes():
    spot = chain.get_spot(minute_ts)
    call, put = chain.get_atm_straddle(minute_ts, "2MAR25")
```

## Data Model

The source data is **tick-level**: each row represents a single instrument updating at one microsecond. At any given time, only a few instruments have new data. The `HistoricOptionChain` handles this by building per-instrument sorted arrays and using binary search to find the latest update at or before the query time.

## Performance

| Operation | Time |
|---|---|
| Load + index build | ~0.8s |
| Single option lookup | ~40 µs |
| ATM straddle | ~90 µs |
| Full chain snapshot | ~3 ms |

## File Structure

```
tardis_options/
├── __init__.py       # exports HistoricOptionChain
├── download.py       # fetch raw .csv.gz from tardis.dev
├── extract.py        # filter to parquet
├── chain.py          # HistoricOptionChain class
├── README.md
└── data/
    ├── options_chain_*.csv.gz   # raw downloads
    └── btc_*_*.parquet          # extracted datasets
```
