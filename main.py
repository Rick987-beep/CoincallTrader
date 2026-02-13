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

    # Build service context
    ctx = build_context()
    logger.info(f"Context built — {ctx.auth.base_url}")

    # =========================================================================
    # Register strategies below
    # =========================================================================
    #
    # Example — uncomment and customise:
    #
    # from option_selection import LegSpec
    # from trade_lifecycle import profit_target, max_loss, max_hold_hours
    # from strategy import time_window, weekday_filter, min_available_margin_pct
    #
    # config = StrategyConfig(
    #     name="short_strangle_daily",
    #     legs=[
    #         LegSpec("C", side=2, qty=0.1,
    #                 strike_criteria={"type": "delta", "value": 0.25},
    #                 expiry_criteria={"symbol": "28MAR26"}),
    #         LegSpec("P", side=2, qty=0.1,
    #                 strike_criteria={"type": "delta", "value": -0.25},
    #                 expiry_criteria={"symbol": "28MAR26"}),
    #     ],
    #     entry_conditions=[
    #         time_window(8, 20),
    #         weekday_filter(["mon", "tue", "wed", "thu"]),
    #         min_available_margin_pct(50),
    #     ],
    #     exit_conditions=[
    #         profit_target(50),
    #         max_loss(100),
    #         max_hold_hours(24),
    #     ],
    #     max_concurrent_trades=1,
    #     cooldown_seconds=3600,
    #     check_interval_seconds=60,
    # )
    # runner = StrategyRunner(config, ctx)
    # ctx.position_monitor.on_update(runner.tick)
    # runners.append(runner)

    runners: list = []

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