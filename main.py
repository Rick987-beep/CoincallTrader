#!/usr/bin/env python3
"""
CoincallTrader — Main Entry Point (Launcher)

Wires all services via TradingContext, registers strategies, and runs
the position monitor loop.  Strategy definitions live in strategies/.

Usage:
    python main.py
"""

import logging
import os
import signal
import sys
import time

from strategy import build_context, StrategyRunner
from strategies import micro_strangle_test

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
# Active Strategies
# =============================================================================

STRATEGIES = [
    micro_strangle_test,
    # Add more strategy factories here, e.g.:
    # iron_condor_weekly,
]


# =============================================================================
# Main
# =============================================================================

def main():
    """Start the trading system."""
    logger.info("=" * 60)
    logger.info("CoincallTrader starting")
    logger.info("=" * 60)

    ctx = build_context(poll_interval=2)
    logger.info(f"Context built — {ctx.auth.base_url}")

    # ── Register strategies ──────────────────────────────────────────────
    runners: list = []

    for factory in STRATEGIES:
        config = factory()
        runner = StrategyRunner(config, ctx)
        ctx.position_monitor.on_update(runner.tick)
        runners.append(runner)
        logger.info(f"Strategy registered: {config.name}")

    # ── Start ────────────────────────────────────────────────────────────
    ctx.position_monitor.start()
    logger.info(
        f"Position monitor started (interval={ctx.position_monitor._poll_interval}s) "
        f"— press Ctrl+C to stop"
    )

    def shutdown(sig=None, frame=None):
        logger.info("Shutting down...")
        for r in runners:
            r.stop()
        ctx.position_monitor.stop()
        logger.info("Shutdown complete")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()