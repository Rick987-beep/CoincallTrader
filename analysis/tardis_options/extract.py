#!/usr/bin/env python3
"""
Extract BTC short-dated options from raw tardis.dev gzip into parquet.

Reads the full options_chain .csv.gz (ALL instruments, ~93M rows, ~4.5GB),
filters to BTC options matching specified expiries, and writes a compact
parquet file with float32 columns and zstd compression.

The resulting parquet is what HistoricOptionChain loads for backtesting.

Usage:
    python -m analysis.tardis_options.extract                          # defaults
    python -m analysis.tardis_options.extract --date 2025-03-01 --expiries 2MAR25 3MAR25
    python -m analysis.tardis_options.extract --all-btc                # all BTC expiries
"""
import argparse
import gzip
import os
import sys
import time

import numpy as np

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    print("pip install pyarrow")
    sys.exit(1)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def parse_symbol(sym):
    """BTC-2MAR25-86000-C -> (expiry_str, strike, is_call) or None."""
    parts = sym.split("-")
    if len(parts) != 4 or parts[0] != "BTC":
        return None
    return parts[1], float(parts[2]), parts[3] == "C"


def safe_float(val):
    """Convert string to float, empty/missing -> NaN."""
    if not val or val == "":
        return float("nan")
    return float(val)


def extract(date_str="2025-03-01", expiries=None, all_btc=False):
    """Extract BTC options from raw gzip to parquet.

    Args:
        date_str:  Date string YYYY-MM-DD.
        expiries:  Set of expiry strings to include, e.g. {"2MAR25", "3MAR25"}.
                   If None, defaults to 0DTE+1DTE for 2025-03-01.
        all_btc:   If True, include all BTC expiries (ignores expiries arg).

    Returns:
        Path to the output parquet file.
    """
    gz_path = os.path.join(DATA_DIR, f"options_chain_{date_str}.csv.gz")
    if not os.path.exists(gz_path):
        print(f"Source file not found: {gz_path}")
        print("Run download.py first.")
        sys.exit(1)

    if expiries is None:
        expiries = {"2MAR25", "3MAR25"}

    # Build output filename
    if all_btc:
        out_name = f"btc_all_{date_str}.parquet"
    else:
        exp_tag = "_".join(sorted(expiries)).lower()
        out_name = f"btc_{exp_tag}_{date_str}.parquet"
    out_path = os.path.join(DATA_DIR, out_name)

    print(f"Source: {gz_path} ({os.path.getsize(gz_path):,} bytes)")
    label = "all BTC expiries" if all_btc else f"expiries {expiries}"
    print(f"Filter: {label}")

    # Accumulate rows in column lists
    rows = {
        "timestamp": [], "expiry_str": [], "strike": [], "is_call": [],
        "underlying_price": [], "mark_price": [], "mark_iv": [],
        "bid_price": [], "bid_amount": [], "bid_iv": [],
        "ask_price": [], "ask_amount": [], "ask_iv": [],
        "last_price": [], "open_interest": [],
        "delta": [], "gamma": [], "vega": [], "theta": [],
    }

    matched = 0
    total = 0
    t0 = time.time()

    with gzip.open(gz_path, "rt", errors="replace") as f:
        header = f.readline().strip().split(",")
        idx = {col: i for i, col in enumerate(header)}

        for line in f:
            total += 1
            fields = line.split(",")
            sym = fields[idx["symbol"]]

            if not sym.startswith("BTC"):
                continue

            parsed = parse_symbol(sym)
            if parsed is None:
                continue

            expiry_str, strike, is_call = parsed
            if not all_btc and expiry_str not in expiries:
                continue

            matched += 1
            rows["timestamp"].append(int(fields[idx["timestamp"]]))
            rows["expiry_str"].append(expiry_str)
            rows["strike"].append(strike)
            rows["is_call"].append(is_call)
            rows["underlying_price"].append(safe_float(fields[idx["underlying_price"]]))
            rows["mark_price"].append(safe_float(fields[idx["mark_price"]]))
            rows["mark_iv"].append(safe_float(fields[idx["mark_iv"]]))
            rows["bid_price"].append(safe_float(fields[idx["bid_price"]]))
            rows["bid_amount"].append(safe_float(fields[idx["bid_amount"]]))
            rows["bid_iv"].append(safe_float(fields[idx["bid_iv"]]))
            rows["ask_price"].append(safe_float(fields[idx["ask_price"]]))
            rows["ask_amount"].append(safe_float(fields[idx["ask_amount"]]))
            rows["ask_iv"].append(safe_float(fields[idx["ask_iv"]]))
            rows["last_price"].append(safe_float(fields[idx["last_price"]]))
            rows["open_interest"].append(safe_float(fields[idx["open_interest"]]))
            rows["delta"].append(safe_float(fields[idx["delta"]]))
            rows["gamma"].append(safe_float(fields[idx["gamma"]]))
            rows["vega"].append(safe_float(fields[idx["vega"]]))
            rows["theta"].append(safe_float(fields[idx["theta"]]))

            if matched % 200000 == 0:
                elapsed = time.time() - t0
                print(f"  {matched:>10,} matched / {total:>12,} scanned  ({elapsed:.0f}s)")

    elapsed = time.time() - t0
    print(f"\nScan complete in {elapsed:.0f}s")
    print(f"  Total scanned:  {total:,}")
    print(f"  Matched rows:   {matched:,}")

    if matched == 0:
        print("No matching rows found!")
        return None

    # Build parquet with compact types
    table = pa.table({
        "timestamp": pa.array(rows["timestamp"], type=pa.int64()),
        "expiry": pa.array(rows["expiry_str"], type=pa.dictionary(pa.int8(), pa.string())),
        "strike": pa.array(rows["strike"], type=pa.float32()),
        "is_call": pa.array(rows["is_call"], type=pa.bool_()),
        "underlying_price": pa.array(rows["underlying_price"], type=pa.float32()),
        "mark_price": pa.array(rows["mark_price"], type=pa.float32()),
        "mark_iv": pa.array(rows["mark_iv"], type=pa.float32()),
        "bid_price": pa.array(rows["bid_price"], type=pa.float32()),
        "bid_amount": pa.array(rows["bid_amount"], type=pa.float32()),
        "bid_iv": pa.array(rows["bid_iv"], type=pa.float32()),
        "ask_price": pa.array(rows["ask_price"], type=pa.float32()),
        "ask_amount": pa.array(rows["ask_amount"], type=pa.float32()),
        "ask_iv": pa.array(rows["ask_iv"], type=pa.float32()),
        "last_price": pa.array(rows["last_price"], type=pa.float32()),
        "open_interest": pa.array(rows["open_interest"], type=pa.float32()),
        "delta": pa.array(rows["delta"], type=pa.float32()),
        "gamma": pa.array(rows["gamma"], type=pa.float32()),
        "vega": pa.array(rows["vega"], type=pa.float32()),
        "theta": pa.array(rows["theta"], type=pa.float32()),
    })

    pq.write_table(table, out_path, compression="zstd")
    size = os.path.getsize(out_path)
    print(f"\nSaved: {out_path}")
    print(f"  Size: {size:,} bytes ({size / 1024 / 1024:.1f} MB)")
    print(f"  Rows: {len(table):,}")

    # Per-expiry summary
    from datetime import datetime
    unique_expiries = set(rows["expiry_str"])
    for exp in sorted(unique_expiries):
        n = sum(1 for e in rows["expiry_str"] if e == exp)
        strikes = set(s for s, e in zip(rows["strike"], rows["expiry_str"]) if e == exp)
        print(f"  {exp}: {n:,} rows, {len(strikes)} strikes ({min(strikes):.0f}–{max(strikes):.0f})")

    ts_min, ts_max = min(rows["timestamp"]), max(rows["timestamp"])
    print(f"  Time: {datetime.utcfromtimestamp(ts_min/1e6)} → {datetime.utcfromtimestamp(ts_max/1e6)} UTC")
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract BTC options to parquet")
    parser.add_argument("--date", default="2025-03-01", help="YYYY-MM-DD")
    parser.add_argument("--expiries", nargs="+", help="e.g. 2MAR25 3MAR25")
    parser.add_argument("--all-btc", action="store_true", help="All BTC expiries")
    args = parser.parse_args()
    expiries = set(args.expiries) if args.expiries else None
    extract(args.date, expiries=expiries, all_btc=args.all_btc)
