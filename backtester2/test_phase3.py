#!/usr/bin/env python3
"""Phase 3 test — run both strategies through the engine with real data."""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtester2.market_replay import MarketReplay
from backtester2.engine import run_single, run_grid
from backtester2.strategies.straddle_strangle import ExtrusionStraddleStrangle
from backtester2.strategies.daily_put_sell import DailyPutSell

BASE = os.path.dirname(os.path.abspath(__file__))
SNAP = os.path.join(BASE, "snapshots", "options_20260309_20260323.parquet")
SPOT = os.path.join(BASE, "snapshots", "spot_track_20260309_20260323.parquet")


def test_straddle_single():
    print("=" * 60)
    print("TEST 1: ExtrusionStraddleStrangle — single combo")
    print("=" * 60)
    replay = MarketReplay(SNAP, SPOT)

    params = {"offset": 0, "index_trigger": 500, "max_hold": 4}
    t0 = time.time()
    trades = run_single(ExtrusionStraddleStrangle, params, replay)
    elapsed = time.time() - t0

    print(f"\nParams: {params}")
    print(f"Trades: {len(trades)} in {elapsed:.1f}s")

    if trades:
        total_pnl = sum(t.pnl for t in trades)
        wins = sum(1 for t in trades if t.pnl > 0)
        triggers = sum(1 for t in trades if t.triggered)
        print(f"Total PnL: ${total_pnl:,.2f}")
        print(f"Win rate: {wins}/{len(trades)} ({100*wins/len(trades):.0f}%)")
        print(f"Trigger rate: {triggers}/{len(trades)} ({100*triggers/len(trades):.0f}%)")
        print(f"Avg PnL: ${total_pnl/len(trades):,.2f}")

        # Show first 3 trades
        print("\nFirst 3 trades:")
        for t in trades[:3]:
            print(f"  {t.entry_date} {t.entry_time.strftime('%H:%M')}->{t.exit_time.strftime('%H:%M')} "
                  f"spot=${t.entry_spot:,.0f} entry=${t.entry_price_usd:.2f} "
                  f"exit=${t.exit_price_usd:.2f} pnl=${t.pnl:.2f} "
                  f"reason={t.exit_reason} held={t.exit_hour}h")
    else:
        print("WARNING: No trades produced!")
    print()


def test_put_sell_single():
    print("=" * 60)
    print("TEST 2: DailyPutSell — single combo")
    print("=" * 60)
    replay = MarketReplay(SNAP, SPOT)

    params = {"target_delta": -0.10, "stop_loss_pct": 1.0, "entry_hour": 3}
    t0 = time.time()
    trades = run_single(DailyPutSell, params, replay)
    elapsed = time.time() - t0

    print(f"\nParams: {params}")
    print(f"Trades: {len(trades)} in {elapsed:.1f}s")

    if trades:
        total_pnl = sum(t.pnl for t in trades)
        wins = sum(1 for t in trades if t.pnl > 0)
        expiries = sum(1 for t in trades if t.exit_reason == "expiry")
        stops = sum(1 for t in trades if t.exit_reason == "stop_loss")
        print(f"Total PnL: ${total_pnl:,.2f}")
        print(f"Win rate: {wins}/{len(trades)} ({100*wins/len(trades):.0f}%)")
        print(f"Expiry exits: {expiries}, Stop-loss exits: {stops}")
        print(f"Avg PnL: ${total_pnl/len(trades):,.2f}")

        print("\nAll trades:")
        for t in trades:
            print(f"  {t.entry_date} {t.entry_time.strftime('%H:%M')}->{t.exit_time.strftime('%H:%M')} "
                  f"K={t.metadata.get('strike', '?'):.0f} "
                  f"delta={t.metadata.get('actual_delta', '?'):.3f} "
                  f"entry=${t.entry_price_usd:.2f} exit=${t.exit_price_usd:.2f} "
                  f"pnl=${t.pnl:.2f} reason={t.exit_reason}")
    else:
        print("WARNING: No trades produced!")
    print()


def test_straddle_grid():
    print("=" * 60)
    print("TEST 3: ExtrusionStraddleStrangle — small grid")
    print("=" * 60)
    replay = MarketReplay(SNAP, SPOT)

    # Small grid: 3 offsets × 3 triggers × 3 holds = 27 combos
    small_grid = {
        "offset": [0, 1000, 2000],
        "index_trigger": [500, 800, 1200],
        "max_hold": [2, 4, 8],
    }

    t0 = time.time()
    results = run_grid(ExtrusionStraddleStrangle, small_grid, replay)
    elapsed = time.time() - t0

    print(f"\nGrid: {len(results)} combos in {elapsed:.1f}s")

    # Show top 5 by total PnL
    rankings = []
    for key, trade_tuples in results.items():
        if not trade_tuples:
            continue
        total = sum(t[0] for t in trade_tuples)
        n = len(trade_tuples)
        wins = sum(1 for t in trade_tuples if t[0] > 0)
        params = dict(key)
        rankings.append((total, n, wins, params))

    rankings.sort(reverse=True)
    print(f"\nTop 5 by total PnL:")
    for total, n, wins, params in rankings[:5]:
        print(f"  ${total:>8,.0f}  n={n:>3}  wr={100*wins/n:>3.0f}%  {params}")

    print(f"\nBottom 5:")
    for total, n, wins, params in rankings[-5:]:
        print(f"  ${total:>8,.0f}  n={n:>3}  wr={100*wins/n:>3.0f}%  {params}")
    print()


def test_put_sell_grid():
    print("=" * 60)
    print("TEST 4: DailyPutSell — full grid")
    print("=" * 60)
    replay = MarketReplay(SNAP, SPOT)

    t0 = time.time()
    results = run_grid(DailyPutSell, DailyPutSell.PARAM_GRID, replay)
    elapsed = time.time() - t0

    print(f"\nGrid: {len(results)} combos in {elapsed:.1f}s")

    rankings = []
    for key, trade_tuples in results.items():
        if not trade_tuples:
            continue
        total = sum(t[0] for t in trade_tuples)
        n = len(trade_tuples)
        wins = sum(1 for t in trade_tuples if t[0] > 0)
        params = dict(key)
        rankings.append((total, n, wins, params))

    rankings.sort(reverse=True)
    print(f"\nAll combos by total PnL:")
    for total, n, wins, params in rankings:
        print(f"  ${total:>8,.0f}  n={n:>3}  wr={100*wins/n:>3.0f}%  "
              f"delta={params['target_delta']:.2f} sl={params['stop_loss_pct']:.1f}")
    print()


def test_full_straddle_grid():
    print("=" * 60)
    print("TEST 5: ExtrusionStraddleStrangle — FULL 840-combo grid")
    print("=" * 60)
    replay = MarketReplay(SNAP, SPOT)

    t0 = time.time()
    results = run_grid(ExtrusionStraddleStrangle,
                       ExtrusionStraddleStrangle.PARAM_GRID, replay)
    elapsed = time.time() - t0

    print(f"\nGrid: {len(results)} combos in {elapsed:.1f}s")
    total_trades = sum(len(v) for v in results.values())
    print(f"Total trades: {total_trades:,}")

    # Quick top 5
    rankings = []
    for key, trade_tuples in results.items():
        if not trade_tuples:
            continue
        total = sum(t[0] for t in trade_tuples)
        n = len(trade_tuples)
        wins = sum(1 for t in trade_tuples if t[0] > 0)
        params = dict(key)
        rankings.append((total, n, wins, params))

    rankings.sort(reverse=True)
    print(f"\nTop 10 combos:")
    for total, n, wins, params in rankings[:10]:
        print(f"  ${total:>8,.0f}  n={n:>3}  wr={100*wins/n:>3.0f}%  "
              f"off={params['offset']:>4}  trig={params['index_trigger']:>4}  "
              f"hold={params['max_hold']:>2}h")
    print()


if __name__ == "__main__":
    test_straddle_single()
    test_put_sell_single()
    test_straddle_grid()
    test_put_sell_grid()
    test_full_straddle_grid()
    print("All Phase 3 tests complete!")
