#!/usr/bin/env python3
"""
run.py — CLI entry point for backtester V2.

Usage:
    python -m backtester2.run
    python -m backtester2.run --strategy straddle
    python -m backtester2.run --strategy put_sell
    python -m backtester2.run --strategy straddle --output report.html
"""
import argparse
import os
import sys
import time
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backtester2.market_replay import MarketReplay
from backtester2.engine import run_grid_full
from backtester2.reporting_v2 import generate_html, combo_stats
from backtester2.strategies.straddle_strangle import ExtrusionStraddleStrangle
from backtester2.strategies.daily_put_sell import DailyPutSell

# ── Strategy Registry ────────────────────────────────────────────

STRATEGIES = {
    "straddle": ExtrusionStraddleStrangle,
    "put_sell": DailyPutSell,
}

DEFAULT_OPTIONS = "backtester2/snapshots/options_20260309_20260323.parquet"
DEFAULT_SPOT = "backtester2/snapshots/spot_track_20260309_20260323.parquet"


# ── Main ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Backtester V2")
    parser.add_argument("--strategy", default="straddle",
                        choices=list(STRATEGIES.keys()))
    parser.add_argument("--options", default=DEFAULT_OPTIONS)
    parser.add_argument("--spot", default=DEFAULT_SPOT)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    strategy_cls = STRATEGIES[args.strategy]

    print(f"\n{'='*60}")
    print(f"  Backtester V2 — {strategy_cls.name}")
    print(f"{'='*60}")

    # Load data
    t0 = time.time()
    replay = MarketReplay(args.options, args.spot)
    print(f"  Data loaded: {len(replay._timestamps):,} intervals in {time.time()-t0:.1f}s")

    # Run grid
    t1 = time.time()
    results = run_grid_full(strategy_cls, strategy_cls.PARAM_GRID, replay)
    grid_time = time.time() - t1

    total_trades = sum(len(v) for v in results.values())
    print(f"  {len(results):,} combos, {total_trades:,} trades in {grid_time:.1f}s")

    # Date range from spot data
    first_dt = datetime.fromtimestamp(
        int(replay._spot_ts[0]) / 1_000_000, tz=timezone.utc)
    last_dt = datetime.fromtimestamp(
        int(replay._spot_ts[-1]) / 1_000_000, tz=timezone.utc)
    date_range = (first_dt.strftime("%Y-%m-%d"), last_dt.strftime("%Y-%m-%d"))

    # Console summary — top 5
    ranked = sorted(results.items(),
                    key=lambda kv: sum(t.pnl for t in kv[1]), reverse=True)
    print(f"\n  Top 5 combos:")
    for key, trades in ranked[:5]:
        params = dict(key)
        total = sum(t.pnl for t in trades)
        wins = sum(1 for t in trades if t.pnl > 0)
        wr = wins / len(trades) * 100 if trades else 0
        label = " | ".join(f"{k}={_fmt_val(v)}" for k, v in sorted(params.items()))
        print(f"    {label}  →  ${total:,.0f}  ({len(trades)} trades, {wr:.0f}% win)")

    # Generate HTML report
    html = generate_html(
        strategy_name=strategy_cls.name,
        param_grid=strategy_cls.PARAM_GRID,
        results=results,
        date_range=date_range,
        n_intervals=len(replay._timestamps),
        runtime_s=grid_time,
    )

    output_path = args.output or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"{args.strategy}_report.html")
    with open(output_path, "w") as f:
        f.write(html)

    print(f"\n  Report: {output_path}")
    print(f"  Total:  {time.time()-t0:.1f}s\n")


def _fmt_val(v):
    if isinstance(v, float) and v != int(v):
        return f"{v:.2f}"
    return str(int(v) if isinstance(v, float) else v)


if __name__ == "__main__":
    main()

