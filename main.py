#!/usr/bin/env python3
"""
CoincallTrader — Main Entry Point

Wires all services via TradingContext and runs the position monitor loop.
Strategies are registered here and execute on each monitor tick.

Usage:
    python main.py
"""

import logging
import os
import signal
import sys
import time

from strategy import build_context, StrategyRunner, StrategyConfig

# =============================================================================
# Logging
# =============================================================================

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("logs/trading.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


# =============================================================================
# Main
# =============================================================================

def main():
    """Start the trading system."""
    logger.info("=" * 60)
    logger.info("CoincallTrader starting")
    logger.info("=" * 60)

    # Build service context (poll every 2s for fast test cycle)
    ctx = build_context(poll_interval=2)
    logger.info(f"Context built — {ctx.auth.base_url}")

    # =========================================================================
    # Register strategies below
    # =========================================================================

    # --- LIVE TEST: Buy strangle, 2 cycles ------------------------------------
    # Opens a 0.15Δ strangle (buy), holds ~10s, closes, repeats once, then stops.

    from option_selection import strangle
    from strategy import (
        profit_target, time_exit, max_hold_hours,
        time_window, min_available_margin_pct,
    )

    live_strangle = StrategyConfig(
        name="live_strangle_test",
        legs=strangle(                                     # Buy OTM strangle
            qty=0.01,                                      # Smallest contract size
            call_delta=0.15,
            put_delta=-0.15,
            dte="next",
            side=1,                                        # Buy
        ),
        entry_conditions=[
            min_available_margin_pct(30),                   # Require 30% margin headroom
        ],
        exit_conditions=[
            max_hold_hours(10 / 3600),                     # Close after ~10 seconds in OPEN
        ],
        max_concurrent_trades=1,
        max_trades_per_day=2,                              # Exactly 2 cycles, then stop
        cooldown_seconds=10,                               # 10s pause between cycles
        check_interval_seconds=5,                          # First check after 5s
        dry_run=False,                                     # ← LIVE TRADING
    )

    runners: list = []

    runner = StrategyRunner(live_strangle, ctx)
    ctx.position_monitor.on_update(runner.tick)
    runners.append(runner)

    # --- Add more strategies here ------------------------------------------

    # =========================================================================
    # Start
    # =========================================================================
    ctx.position_monitor.start()
    logger.info(
        f"Position monitor started (interval={ctx.position_monitor._poll_interval}s) "
        f"— press Ctrl+C to stop"
    )

    # Graceful shutdown
    def shutdown(sig=None, frame=None):
        logger.info("Shutting down...")
        for r in runners:
            r.stop()
        ctx.position_monitor.stop()
        logger.info("Shutdown complete")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()