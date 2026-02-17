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

from strategy import (
    build_context,
    StrategyRunner,
    StrategyConfig,
    profit_target,
    max_loss,
    max_hold_hours,
    time_exit,
    time_window,
    min_available_margin_pct,
)
from option_selection import strangle
from trade_execution import ExecutionParams

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
# Strategy Definitions
# =============================================================================

def micro_strangle_test() -> StrategyConfig:
    """
    Micro strangle — live execution test.

    Buy 0.01-lot 0.15Δ strangle, hold ~10s, close, repeat once (2 cycles).
    Uses LimitFillManager with 30s requote timeout.
    """
    return StrategyConfig(
        name="micro_strangle_test",
        legs=strangle(
            qty=0.01,
            call_delta=0.15,
            put_delta=-0.15,
            dte="next",
            side=1,                                        # buy
        ),
        entry_conditions=[
            min_available_margin_pct(30),
        ],
        exit_conditions=[
            max_hold_hours(10 / 3600),                     # ~10 seconds
        ],
        max_concurrent_trades=1,
        max_trades_per_day=2,
        cooldown_seconds=10,
        check_interval_seconds=5,
        dry_run=False,
        metadata={
            "execution_params": ExecutionParams(
                fill_timeout_seconds=30.0,
                aggressive_buffer_pct=2.0,
                max_requote_rounds=10,
            ),
        },
    )


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

    config = micro_strangle_test()
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