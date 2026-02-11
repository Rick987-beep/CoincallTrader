#!/usr/bin/env python3
"""
Live Strangle Lifecycle Test via RFQ

Opens a long strangle, monitors it for a few seconds, then closes it.
Both open and close via RFQ.

Strangle:
  - Long BTCUSD-13FEB26-58000-P × 0.5
  - Long BTCUSD-13FEB26-78000-C × 0.5

Usage:
    python tests/test_strangle_lifecycle.py
    python tests/test_strangle_lifecycle.py --monitor 20
"""

import argparse
import os
import sys
import time
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(line_buffering=True)

from account_manager import PositionMonitor, AccountSnapshot
from trade_lifecycle import (
    LifecycleManager,
    TradeLeg,
    TradeState,
    profit_target,
    max_loss,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger('test_strangle')


def ts() -> str:
    return datetime.now().strftime('%H:%M:%S')


def print_snapshot(snap: AccountSnapshot, trade=None):
    """Compact snapshot focused on the strangle."""
    print(f"\n[{ts()}] --- Snapshot ---", flush=True)
    print(f"  Equity: ${snap.equity:>12,.2f}   UPnL: ${snap.unrealized_pnl:>10,.4f}   Margin: {snap.margin_utilization:.1f}%", flush=True)
    print(f"  Net Δ: {snap.net_delta:>+10.4f}   Γ: {snap.net_gamma:>+10.6f}   Θ: {snap.net_theta:>+10.4f}   V: {snap.net_vega:>+10.4f}", flush=True)

    if snap.positions:
        print(f"  Positions ({snap.position_count}):", flush=True)
        for p in snap.positions:
            print(
                f"    {p.symbol:<32} {p.side:<5} {p.qty:>8.4f}  "
                f"entry=${p.entry_price:>9.2f}  mark=${p.mark_price:>9.2f}  "
                f"PnL=${p.unrealized_pnl:>9.4f}  Δ={p.delta:>+8.4f}",
                flush=True,
            )

    if trade and trade.state == TradeState.OPEN:
        greeks = trade.structure_greeks(snap)
        pnl = trade.structure_pnl(snap)
        print(f"  Structure: PnL=${pnl:>+10.4f}  Δ={greeks['delta']:>+8.4f}  Θ={greeks['theta']:>+8.4f}  V={greeks['vega']:>+8.4f}", flush=True)


def main():
    parser = argparse.ArgumentParser(description='Strangle Lifecycle Test')
    parser.add_argument('--monitor', type=int, default=15,
                        help='Seconds to monitor the open position (default: 15)')
    args = parser.parse_args()

    PUT_LEG = "BTCUSD-13FEB26-58000-P"
    CALL_LEG = "BTCUSD-13FEB26-78000-C"
    QTY = 0.5

    print(f"\n[{ts()}] ============================================")
    print(f"[{ts()}]  STRANGLE LIFECYCLE TEST (RFQ)")
    print(f"[{ts()}] ============================================")
    print(f"[{ts()}]  Put:  Long {QTY}x {PUT_LEG}")
    print(f"[{ts()}]  Call: Long {QTY}x {CALL_LEG}")
    print(f"[{ts()}]  Notional: ${(58000 + 78000) * QTY:,.0f}")
    print(f"[{ts()}]  Monitor:  {args.monitor}s")
    print(f"[{ts()}] ============================================\n")

    manager = LifecycleManager()
    monitor = PositionMonitor(poll_interval=10)

    # ---- STEP 1: Open the strangle via RFQ ----
    print(f"[{ts()}] STEP 1: Opening strangle via RFQ...")

    trade = manager.create(
        legs=[
            TradeLeg(symbol=PUT_LEG, qty=QTY, side=1),   # buy put
            TradeLeg(symbol=CALL_LEG, qty=QTY, side=1),   # buy call
        ],
        exit_conditions=[profit_target(50), max_loss(200)],
        execution_mode="rfq",
        rfq_action="buy",
        metadata={"strategy": "test_strangle", "test": True},
    )
    print(f"[{ts()}] Trade {trade.id} created (PENDING_OPEN)")

    success = manager.open(trade.id)
    if not success:
        print(f"[{ts()}] FAILED to open: {trade.error}")
        print(f"[{ts()}] RFQ result: {trade.rfq_result}")
        return

    print(f"[{ts()}] Strangle OPENED via RFQ. State: {trade.state.value}")
    if trade.rfq_result:
        print(f"[{ts()}]   RFQ cost:      ${trade.rfq_result.total_cost:.4f}")
        print(f"[{ts()}]   Orderbook cost: ${trade.rfq_result.orderbook_cost:.4f}")
        print(f"[{ts()}]   Improvement:    {trade.rfq_result.improvement_pct:.2f}%")

    # ---- STEP 2: Monitor the open position ----
    print(f"\n[{ts()}] STEP 2: Monitoring for {args.monitor}s...")

    # Take immediate snapshot
    snap = monitor.snapshot()
    print_snapshot(snap, trade)

    # Start background polling
    monitor.on_update(lambda s: print_snapshot(s, trade))
    monitor.start()

    time.sleep(args.monitor)
    monitor.stop()

    # Final snapshot before close
    snap = monitor.snapshot()
    print(f"\n[{ts()}] Final snapshot before close:")
    print_snapshot(snap, trade)

    # ---- STEP 3: Close the strangle via RFQ ----
    print(f"\n[{ts()}] STEP 3: Closing strangle via RFQ...")

    success = manager.close(trade.id)
    if not success:
        print(f"[{ts()}] FAILED to close: {trade.error}")
        print(f"[{ts()}] Close RFQ result: {trade.close_rfq_result}")
        # Try to take a snapshot to see what happened
        snap = monitor.snapshot()
        print(f"[{ts()}] Current positions: {snap.position_count}")
        return

    print(f"[{ts()}] Strangle CLOSED via RFQ. State: {trade.state.value}")
    if trade.close_rfq_result:
        print(f"[{ts()}]   Close RFQ cost: ${trade.close_rfq_result.total_cost:.4f}")

    # Final verification
    time.sleep(2)
    snap = monitor.snapshot()
    print(f"\n[{ts()}] Post-close verification:")
    print(f"[{ts()}]   Positions: {snap.position_count}")
    print(f"[{ts()}]   Equity:    ${snap.equity:,.2f}")

    print(f"\n[{ts()}] ============================================")
    print(f"[{ts()}]  TEST COMPLETE")
    print(f"[{ts()}]  Trade {trade.id}: {trade.state.value}")
    print(f"[{ts()}] ============================================\n")


if __name__ == '__main__':
    main()
