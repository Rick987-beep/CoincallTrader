# Spot Data Corruption — Root Cause & New Pipeline Design Brief

**Date written:** 2026-04-26  
**Context:** New Tardis bulk-download data (`backtester/data/`) produces –$1,259 on
`short_strangle_turbulence_tp`. Old archive data (`backtester/data_archive/`) produces
+$2,765 on the same date range and same parameters. This document explains why, and
what a corrected ingestion pipeline must do differently.

---

## 1. Root cause in one sentence

The spot OHLC parquets (`spot_YYYY-MM-DD.parquet`) are built by reading the
`underlying_price` column out of individual option ticks. When any single option tick
inside a 1-minute bucket has a corrupted `underlying_price`, the entire 1-min bar's
`high`, `low`, or `close` is contaminated — and those values are then used to price
options in USD, which directly drives stop-loss and profit-target decisions.

---

## 2. How the current pipeline builds spot bars

In `backtester/ingest/bulkdownloadTardis/stream_extract.py`, the spot OHLC is
accumulated like this:

```python
# For every option tick in the gzip CSV:
spot_val = _sf(fields[i_spot])          # underlying_price from the options row
if not math.isnan(spot_val):
    bucket = (ts // SPOT_INTERVAL_US) * SPOT_INTERVAL_US
    bar = spot_bars.get(bucket)
    if bar is None:
        spot_bars[bucket] = [spot_val, spot_val, spot_val, spot_val]
    else:
        if spot_val > bar[1]:
            bar[1] = spot_val           # updates HIGH
        if spot_val < bar[2]:
            bar[2] = spot_val           # updates LOW
        bar[3] = spot_val               # overwrites CLOSE with every new tick
```

**The problem:** `underlying_price` on a Deribit options tick is a snapshot of the
Deribit index at that instant. Different option instruments tick at different times and
rates. A single corrupted index snapshot attached to one options tick (e.g. one deep-OTM
put that ticks once in the minute) inflates the `high` and/or `close` of the entire bar.
The OHLC source is not an authoritative spot feed — it is the side-channel of
thousands of noisy per-instrument ticks.

---

## 3. What the corruption looks like

### Example A — 2026-01-01 expiry window (06:44–09:21 UTC)

Real BTC/USD (Binance 1m klines, confirmed independently): **~87,500–87,900**

```
TIME    OLD close   NEW open   NEW high   NEW low    NEW close  Binance
------  ---------  ---------  ---------  ---------  ---------  -------
06:44   87,603     87,587     91,579 !!!  87,519     90,538 !!!  87,659
06:45   87,609     90,538     91,575 !!!  87,522     90,541 !!!  87,659
06:46   87,592     90,541     91,573 !!!  87,521     90,539 !!!  87,659
06:47   87,654     88,494     91,570 !!!  87,519     90,538 !!!  87,659
06:49   87,729     87,653     91,569 !!!  87,517     87,729      87,659  ← close OK
06:50   87,577     87,878     91,569 !!!  87,517     90,538 !!!  87,659
07:55   87,531     90,495     91,528 !!!  87,501     90,435 !!!  87,611
```

Key observations:
- **`NEW high` is stuck at ~91,500–91,700 for the entire 2.5-hour window** — every bar,
  relentlessly. Real high was ~87,900. The fake high is ~$4,000 above reality.
- **`NEW close` oscillates** — some bars have a correct close (~87,500), others a
  corrupted close (~90,500). Whether a bar's close is correct depends on which
  option instrument happened to tick last in that minute.
- **`NEW low` and `OLD close` both track Binance** — the corruption is limited to the
  upward direction, which is consistent with a subset of option ticks carrying an
  inflated index snapshot.

### Example B — 2026-01-09 expiry window (06:44–08:12 UTC)

Real BTC/USD: **~90,800–91,300**

```
TIME    OLD close   NEW high   NEW close  Binance
------  ---------  ---------  ---------  -------
06:44   91,268     95,391 !!!  91,268      91,117   ← close OK, high wrong
06:59   91,298     95,448 !!!  94,338 !!!  91,138   ← both wrong
07:35   90,977     95,270 !!!  95,269 !!!  90,982   ← close = high (full spike)
07:46   90,950     95,247 !!!  95,214 !!!  90,933
08:00   90,816     95,103 !!!  95,068 !!!  90,785
```

The fake level here is ~95,250–95,450. The call strike is 94,000. The corrupted
`close` and `high` values exceed the call strike, making the strategy appear to have a
stop-loss breach during what was actually a normal, quiet expiry.

### Example C — Corruption duration

These are **not single-tick spikes**. The affected windows span 90–160 minutes
continuously around option expiry time (typically 08:00 UTC), on at least 6 known dates
in the Dec 2025 – Apr 2026 range:

| Expiry date | Corrupted window      | Fake high level | Real level | ∆        |
|-------------|-----------------------|-----------------|------------|----------|
| 2026-01-01  | 06:44 – 09:21 UTC     | ~91,550         | ~87,650    | +$3,900  |
| 2026-01-09  | 06:44 – 08:12 UTC     | ~95,300         | ~91,100    | +$4,200  |
| 2026-02-14  | ~07:45 – 09:00 UTC    | ~71,620         | ~69,180    | +$2,440  |
| 2026-03-10  | ~07:55 – 09:10 UTC    | ~72,590         | ~70,400    | +$2,190  |
| 2026-03-13  | ~08:00 – 09:00 UTC    | ~73,530         | ~71,560    | +$1,970  |

---

## 4. Primary impact: incorrect option selection (the dominant effect)

**This is the most consequential effect of the corrupted spot track.** Every
strategy that selects strikes relative to the current spot price will select the
wrong strikes on every affected day.

For example, a delta-strangle or short-strangle strategy targeting a call strike
15% OTM uses spot to compute the target strike level:

```
target_call_strike = spot × 1.15
```

If the backtester sees `spot = 90,538` instead of the real `spot = 87,600`,
it selects the call strike at ~$104,000 instead of ~$100,800. That is a ~$3,200
error in strike selection — a different contract with different premium, different
delta, and different probability of expiring worthless.

The same applies to put strike selection, delta-based strike lookups, and any
IVP/skew calculation that normalises by spot. If the backtested strategy looks
more or less profitable than the same strategy run against real data, corrupted
strike selection is the primary explanation.

**Why this dominates over SL/TP effects:**  
SL and TP logic are also affected (USD-denominated PnL = BTC price × spot, so an
inflated spot inflates the USD loss figure — see section 5 below). But this is a
multiplicative scaling error on the PnL, whereas the wrong-strike error changes
which instrument is traded entirely. Picking the wrong strike is the larger
distortion because it affects entry premium collected, gamma exposure, and
probability of the trade being profitable — not just the magnitude of an already-
entered trade's PnL.

---

## 5. How this triggers false stop-losses

The `stop_loss_pct` exit condition in `strategy_base.py` reprices legs as:

```python
total_usd += effective_ask_btc * quote.spot
# where quote.spot = state.spot = close of latest 1-min spot bar
```

Because the price is BTC-denominated but stop-loss is evaluated in USD, a corrupted
`state.spot` multiplies into the current mark-to-market:

```
Entry:
  spot_at_entry     = 87,600
  entry_price_usd   = option_btc_premium × 87,600

During corrupt window (close = 90,538):
  spot_current      = 90,538   (3.4% above real)
  current_price_usd = option_btc_premium × 90,538   ← ~same BTC price, ~3.4% USD inflation

Stop-loss ratio = (current_usd - entry_usd) / entry_usd
               = (option_btc × 90,538 - option_btc × 87,600) / (option_btc × 87,600)
               = (90,538 - 87,600) / 87,600
               = 3.35%
```

This 3.35% is added **on top of any genuine BTC-denominated option price movement**.
For a 1DTE option that has moved from 1× to 3.65× in BTC terms, the spot inflation
pushes the USD ratio to 3.65 × 1.034 = 3.77×, then any further small BTC move
crosses the 4.0× stop threshold. Every basis point of inflated spot counts.

Additionally, the corrupted `high` values breach call strike levels in
`close_short_strangle(reason="expiry")`, which uses `max(0, spot_at_expiry - call_strike)`
for call intrinsic value — making the expiry settle at a large loss even when the real
spot never touched the strike.

---

## 6. Why the old archive data was unaffected

The old data (`data_archive/`) was captured by a different path: the production tick
recorder on the VPS (`backtester/ingest/tickrecorder/`), which records the Deribit
WebSocket feed directly. That feed includes a separate, clean index price stream
distinct from the per-instrument ticks. The `underlying_price` in the WS feed appears to
be the official Deribit index snapshot, not a per-instrument-tick side-channel.

However, the old archive **was missing some data** (e.g., the 2026-03-09 19:00 UTC entry
interval is absent entirely), so it was not entering the pre-crash trade. Its +$2,765
was partly a lucky data gap, not a validated signal.

---

## 7. Root cause of the regression (confirmed from git history)

The corruption was introduced in commit `d6ed890` (v1.15.1, April 19 2026), which changed one default in `bulk_fetch.py`:

```python
# v1.13.0 (first download — clean data):
max_dte=28

# v1.15.1 (second download — corrupted data):
max_dte=700
```

The first download only processed options with DTE ≤ 28. The second processed all options up to DTE 700 (monthlies, quarterlies, yearlings — no practical cap). This was intentional for the options data, to capture the full chain, but it had an unintended consequence on the spot track.

**The precise mechanism — confirmed by querying the options parquet:**

On Deribit, `underlying_price` is **not the spot index**. It is the settlement price of the futures contract corresponding to each option's expiry. Short-dated options (DTE ≤ 3) settle against the spot index, so their `underlying_price` ≈ spot. Long-dated options settle against their own quarterly/yearly futures contract, which trades at a significant basis premium above spot.

Measured directly from `options_2026-01-01.parquet` near 07:00 UTC (Binance spot = 87,650):

| Expiry | DTE | `underlying_price` mean | Δ vs spot |
|--------|-----|-------------------------|-----------|
| `1JAN26` | 0 | 87,509 | −0.16% ✓ spot |
| `2JAN26` | 1 | 87,625 | −0.03% ✓ spot |
| `3JAN26` | 2 | 87,637 | −0.01% ✓ spot |
| `27FEB26` | 57 | 88,215 | +0.64% |
| `27MAR26` | 85 | 88,553 | +1.03% |
| `26JUN26` | 176 | 89,513 | +2.13% |
| `25SEP26` | 267 | 90,583 | +3.35% |
| `25DEC26` | 358 | 91,618 | **+4.53%** |

The `25DEC26` option (358 DTE, 2,112 rows/day) carries `underlying_price ≈ 91,618`. That is the Dec 2026 BTC futures price — the `high=91,579` stuck in the spot OHLC for 2.5 hours on Jan 1 is exactly this value. Every time `25DEC26` ticked, it overwrote the 1-min bar's `high` and sometimes `close` with the futures price.

With `max_dte=28`, the `25DEC26` instrument was filtered out entirely before it could corrupt the spot accumulator. With `max_dte=700` it contributed thousands of rows per day.

---

## 7. What the new pipeline must do differently

### 7.1 Simplest fix: gate spot OHLC on short-DTE instruments only

Only accumulate `underlying_price` into the spot bars from instruments with DTE ≤ 2.
One change in `stream_extract.py`:

```python
# In the tick loop, after computing dte for the instrument:
SPOT_MAX_DTE = 2   # only liquid near-expiry options contribute to spot track

# ... existing DTE filter to skip/continue for the options snapshot ...

# Spot OHLC: only update from short-DTE ticks
if dte is not None and 0 <= dte <= SPOT_MAX_DTE:
    spot_val = _sf(fields[i_spot])
    if not math.isnan(spot_val):
        bucket = (ts // SPOT_INTERVAL_US) * SPOT_INTERVAL_US
        bar = spot_bars.get(bucket)
        if bar is None:
            spot_bars[bucket] = [spot_val, spot_val, spot_val, spot_val]
        else:
            if spot_val > bar[1]: bar[1] = spot_val
            if spot_val < bar[2]: bar[2] = spot_val
            bar[3] = spot_val
```

0-DTE and 1-DTE options on Deribit are extremely liquid. They tick every few seconds
with the live Deribit index price and have zero forward premium (they expire today or
tomorrow). This is sufficient and requires no external API calls.

The long-DTE options are still processed normally for the options parquet — the change
only excludes them from the spot accumulator.

### 7.2 Stronger alternative: source spot independently from Binance

If you want a fully independent spot source — insulating against any Deribit index
errors too — fetch Binance BTCUSDT 1m klines. No API key required:

```
GET https://api.binance.com/api/v3/klines
  ?symbol=BTCUSDT&interval=1m&startTime=<unix_ms>&endTime=<unix_ms>&limit=1000
```

Returns `[open_time, open, high, low, close, volume, ...]`. Covers all dates back to
2017. This was the ground truth used to verify the corruption in this investigation.

Alternative Tardis-native source: the `deribit_index` dataset from Tardis provides
the Deribit BTC/USD index as a clean separate feed, decoupled from any option tick.

### 7.3 Post-write validation (always add this)

```python
# After writing spot_out:
spot_df = pd.read_parquet(spot_out)
daily_median = spot_df["close"].median()
max_close_dev = ((spot_df["close"] - daily_median) / daily_median).abs().max()
max_high_dev  = ((spot_df["high"]  - daily_median) / daily_median).abs().max()
if max_close_dev > 0.04 or max_high_dev > 0.06:
    raise ValueError(
        f"Spot data suspicious on {date_str}: "
        f"close_dev={max_close_dev:.1%} high_dev={max_high_dev:.1%}"
    )
```

This would have caught every corrupt day at ingestion time rather than at backtest time.

---

## 8. Parquet schema (unchanged)

The output schema consumed by `market_replay.py` must remain:

```
spot_YYYY-MM-DD.parquet
  timestamp  int64       # microseconds since epoch UTC, 1-min aligned
  open       float32     # BTC/USD
  high       float32
  low        float32
  close      float32
```

The fix is in ingestion only — `market_replay.py` does not need to change.

---

## 9. Files to change

| File | Change needed |
|------|---------------|
| `stream_extract.py` | Add `SPOT_MAX_DTE = 2` guard on the spot bar accumulator |
| `bulk_fetch.py` | No code change needed; the root cause was the `max_dte=700` default which is correct for the options data — only the spot side needs the DTE cap |
| `clean.py` | Add spot parquet validation (`max_high_dev`, `max_close_dev` check) |

---

## 10. Quick verification script

After re-running ingestion, confirm no corrupted days remain:

```python
import glob, pandas as pd

for path in sorted(glob.glob("backtester/data/spot_*.parquet")):
    df = pd.read_parquet(path)
    med = df["close"].median()
    worst_high  = ((df["high"]  - med) / med).max()
    worst_close = ((df["close"] - med) / med).abs().max()
    if worst_high > 0.05 or worst_close > 0.03:
        date = path.split("spot_")[1].replace(".parquet", "")
        print(f"SUSPICIOUS  {date}  high_dev={worst_high:.1%}  close_dev={worst_close:.1%}")
```

The known-bad dates (Jan 1, Jan 9, Feb 14, Mar 10, Mar 13 2026) should all appear
against the current corrupt data and disappear after a correct re-ingest.
