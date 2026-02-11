#!/usr/bin/env python3
"""
Trade Lifecycle Integration Test

Runs a minimal lifecycle against the live account to verify the state machine.

Modes:
  --dry-run     Create a lifecycle and inspect it without placing orders (default)
  --live        Actually place a small order, monitor fills, and close

Examples:
    python tests/test_trade_lifecycle.py
    python tests/test_trade_lifecycle.py --live --symbol BTCUSD-20FEB26-70000-C --qty 0.01 --side buy
    python tests/test_trade_lifecycle.py --live --symbol BTCUSD-20FEB26-70000-C --qty 0.01 --side buy --close-after 30
"""

import argparse
import os
import sys
import time
import logging
from datetime import datetime

# Add parent directory for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force unbuffered output
sys.stdout.reconfigure(line_buffering=True)

from account_manager import PositionMonitor
from trade_lifecycle import (
    LifecycleManager,
    TradeLifecycle,
    TradeLeg,
    TradeState,
    profit_target,
    max_loss,
    max_hold_hours,
    account_delta_limit,
    structure_delta_limit,
    leg_greek_limit,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger('test_lifecycle')


def ts() -> str:
    return datetime.now().strftime('%H:%M:%S')


def run_dry_run():
    """Create a lifecycle without placing orders — verify data structures."""
    print(f"\n[{ts()}] === DRY RUN: Trade Lifecycle Test ===\n")

    manager = LifecycleManager()

    # Create a sample single-leg trade
    trade = manager.create(
        legs=[
            TradeLeg(symbol="BTCUSD-20FEB26-70000-C", qty=0.01, side=1),
        ],
        exit_conditions=[
            profit_target(50),
            max_loss(100),
            max_hold_hours(48),
            account_delta_limit(0.5),
        ],
        execution_mode="limit",
        metadata={"strategy": "test", "note": "dry run"},
    )

    print(f"[{ts()}] Created trade: {trade.id}")
    print(f"[{ts()}]   State:      {trade.state.value}")
    print(f"[{ts()}]   Mode:       {trade.execution_mode}")
    print(f"[{ts()}]   Legs:       {len(trade.open_legs)}")
    for i, leg in enumerate(trade.open_legs):
        print(f"[{ts()}]     [{i}] {leg.side_label} {leg.qty}x {leg.symbol}")
    print(f"[{ts()}]   Conditions: {len(trade.exit_conditions)}")
    for cond in trade.exit_conditions:
        print(f"[{ts()}]     - {getattr(cond, '__name__', repr(cond))}")
    print(f"[{ts()}]   Metadata:   {trade.metadata}")

    # Also create a multi-leg example (iron condor)
    ic_trade = manager.create(
        legs=[
            TradeLeg(symbol="BTCUSD-20FEB26-75000-C", qty=0.1, side=2),
            TradeLeg(symbol="BTCUSD-20FEB26-80000-C", qty=0.1, side=1),
            TradeLeg(symbol="BTCUSD-20FEB26-65000-P", qty=0.1, side=2),
            TradeLeg(symbol="BTCUSD-20FEB26-60000-P", qty=0.1, side=1),
        ],
        exit_conditions=[
            profit_target(50),
            max_loss(200),
            structure_delta_limit(0.3),
            leg_greek_limit(0, "theta", "<", -5.0),
        ],
        execution_mode="rfq",
        rfq_action="sell",
        metadata={"strategy": "short_iron_condor"},
    )

    print(f"\n[{ts()}] Created Iron Condor trade: {ic_trade.id}")
    print(f"[{ts()}]   State:      {ic_trade.state.value}")
    print(f"[{ts()}]   Mode:       {ic_trade.execution_mode} (action={ic_trade.rfq_action})")
    print(f"[{ts()}]   Legs:       {len(ic_trade.open_legs)}")
    for i, leg in enumerate(ic_trade.open_legs):
        print(f"[{ts()}]     [{i}] {leg.side_label} {leg.qty}x {leg.symbol}")

    # Status report
    print(f"\n[{ts()}] Status Report:")
    print(manager.status_report())

    # Take a snapshot to test greeks helpers
    monitor = PositionMonitor()
    snap = monitor.snapshot()
    print(f"\n[{ts()}] Account snapshot: equity=${snap.equity:,.2f}, positions={snap.position_count}")

    print(f"\n[{ts()}] Dry run complete. All data structures working.\n")


def run_live(symbol: str, qty: float, side: int, close_after: int, monitor_interval: int):
    """Place a real order and run the lifecycle."""
    print(f"\n[{ts()}] === LIVE: Trade Lifecycle Test ===\n")
    print(f"[{ts()}]   Symbol:    {symbol}")
    print(f"[{ts()}]   Qty:       {qty}")
    print(f"[{ts()}]   Side:      {'buy' if side == 1 else 'sell'}")
    print(f"[{ts()}]   Close:     after {close_after}s (or exit conditions)")
    print(f"[{ts()}]   Monitor:   every {monitor_interval}s")

    # Set up manager and monitor
    manager = LifecycleManager()
    monitor = PositionMonitor(poll_interval=monitor_interval)

    # Hook tick into monitor
    monitor.on_update(manager.tick)

    # Also print status on each tick
    def print_status(snap):
        for trade in manager.active_trades:
            print(f"[{ts()}] {trade.summary(snap)}", flush=True)

    monitor.on_update(print_status)

    # Create trade with a time-based exit (close_after seconds)
    close_hours = close_after / 3600.0
    trade = manager.create(
        legs=[TradeLeg(symbol=symbol, qty=qty, side=side)],
        exit_conditions=[
            max_hold_hours(close_hours),
            profit_target(50),
            max_loss(200),
        ],
        execution_mode="limit",
        metadata={"strategy": "test_live"},
    )
    print(f"\n[{ts()}] Created trade {trade.id}")

    # Open it
    print(f"[{ts()}] Placing open order...")
    success = manager.open(trade.id)
    if not success:
        print(f"[{ts()}] Failed to open trade: {trade.error}")
        return

    print(f"[{ts()}] Order placed. Starting monitor...\n")

    # Start background monitoring
    monitor.start()

    try:
        # Wait until trade is CLOSED or FAILED, or user hits Ctrl+C
        while trade.state not in (TradeState.CLOSED, TradeState.FAILED):
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"\n[{ts()}] Interrupted — forcing close...")
        manager.force_close(trade.id)
        # Give a few ticks for close to process
        time.sleep(monitor_interval * 2 + 5)
    finally:
        monitor.stop()

    print(f"\n[{ts()}] Final state: {trade.state.value}")
    if trade.error:
        print(f"[{ts()}] Error: {trade.error}")
    print(f"[{ts()}] Done.\n")


def main():
    parser = argparse.ArgumentParser(description='Trade Lifecycle Test')
    parser.add_argument('--live', action='store_true',
                        help='Actually place orders (default: dry run)')
    parser.add_argument('--symbol', type=str, default='BTCUSD-20FEB26-70000-C',
                        help='Option symbol (default: BTCUSD-20FEB26-70000-C)')
    parser.add_argument('--qty', type=float, default=0.01,
                        help='Quantity (default: 0.01)')
    parser.add_argument('--side', type=str, choices=['buy', 'sell'], default='buy',
                        help='Side: buy or sell (default: buy)')
    parser.add_argument('--close-after', type=int, default=60,
                        help='Seconds before time-based exit (default: 60)')
    parser.add_argument('--monitor-interval', type=int, default=10,
                        help='Monitor poll interval in seconds (default: 10)')
    args = parser.parse_args()

    if args.live:
        side_int = 1 if args.side == 'buy' else 2
        run_live(args.symbol, args.qty, side_int, args.close_after, args.monitor_interval)
    else:
        run_dry_run()


if __name__ == '__main__':
    main()
