#!/usr/bin/env python3
"""
Position Monitor Integration Test

Runs the PositionMonitor against the live account for ~30 seconds,
printing each snapshot to the terminal.

Usage:
    python tests/test_position_monitor.py
    python tests/test_position_monitor.py --interval 5 --duration 60
"""

import argparse
import os
import sys
import time
from datetime import datetime

# Add parent directory for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force unbuffered output
sys.stdout.reconfigure(line_buffering=True)

from account_manager import PositionMonitor, AccountSnapshot


def ts() -> str:
    return datetime.now().strftime('%H:%M:%S')


def print_snapshot(snap: AccountSnapshot) -> None:
    """Pretty-print a snapshot to the terminal."""
    print(f"\n[{ts()}] {'=' * 58}", flush=True)
    print(f"[{ts()}] ACCOUNT SNAPSHOT", flush=True)
    print(f"[{ts()}] {'=' * 58}", flush=True)
    
    # Account summary
    print(f"[{ts()}]   Equity:           ${snap.equity:>12,.2f}", flush=True)
    print(f"[{ts()}]   Available Margin:  ${snap.available_margin:>12,.2f}", flush=True)
    print(f"[{ts()}]   Initial Margin:    ${snap.initial_margin:>12,.2f}", flush=True)
    print(f"[{ts()}]   Unrealised P&L:    ${snap.unrealized_pnl:>12,.2f}", flush=True)
    print(f"[{ts()}]   Margin Util:       {snap.margin_utilization:>12.1f}%", flush=True)
    
    # Aggregated Greeks
    print(f"[{ts()}]   ---", flush=True)
    print(f"[{ts()}]   Net Delta:  {snap.net_delta:>+12.4f}", flush=True)
    print(f"[{ts()}]   Net Gamma:  {snap.net_gamma:>+12.6f}", flush=True)
    print(f"[{ts()}]   Net Theta:  {snap.net_theta:>+12.4f}", flush=True)
    print(f"[{ts()}]   Net Vega:   {snap.net_vega:>+12.4f}", flush=True)
    
    # Positions
    print(f"[{ts()}]   ---", flush=True)
    print(f"[{ts()}]   Positions: {snap.position_count}", flush=True)
    
    if snap.positions:
        # Header
        print(f"[{ts()}]   {'Symbol':<30} {'Side':<6} {'Qty':>8} {'Entry':>10} {'Mark':>10} {'UPnL':>10} {'Δ':>8} {'Θ':>8}", flush=True)
        print(f"[{ts()}]   {'-'*30} {'-'*6} {'-'*8} {'-'*10} {'-'*10} {'-'*10} {'-'*8} {'-'*8}", flush=True)
        
        for p in snap.positions:
            print(
                f"[{ts()}]   {p.symbol:<30} {p.side:<6} {p.qty:>8.4f} "
                f"${p.entry_price:>9.2f} ${p.mark_price:>9.2f} "
                f"${p.unrealized_pnl:>9.4f} {p.delta:>+8.4f} {p.theta:>+8.4f}",
                flush=True
            )
    else:
        print(f"[{ts()}]   (no open positions)", flush=True)
    
    print(f"[{ts()}] {'=' * 58}", flush=True)


def main():
    parser = argparse.ArgumentParser(description='Position Monitor Test')
    parser.add_argument('--interval', type=int, default=10,
                        help='Poll interval in seconds (default: 10)')
    parser.add_argument('--duration', type=int, default=30,
                        help='How long to run in seconds (default: 30)')
    args = parser.parse_args()
    
    print(f"[{ts()}] Starting PositionMonitor test", flush=True)
    print(f"[{ts()}]   Interval: {args.interval}s", flush=True)
    print(f"[{ts()}]   Duration: {args.duration}s", flush=True)
    
    # Create monitor with callback
    monitor = PositionMonitor(poll_interval=args.interval)
    monitor.on_update(print_snapshot)
    
    # Take an immediate snapshot before starting the loop
    print(f"\n[{ts()}] Taking initial snapshot...", flush=True)
    snap = monitor.snapshot()
    print_snapshot(snap)
    
    # Start background polling
    print(f"\n[{ts()}] Starting background monitor...", flush=True)
    monitor.start()
    
    try:
        time.sleep(args.duration)
    except KeyboardInterrupt:
        print(f"\n[{ts()}] Interrupted by user", flush=True)
    
    monitor.stop()
    
    # Final summary
    final = monitor.latest
    if final:
        print(f"\n[{ts()}] Final snapshot:", flush=True)
        print(f"[{ts()}]   {final.summary_str()}", flush=True)
    
    print(f"\n[{ts()}] Done.", flush=True)


if __name__ == '__main__':
    main()
