"""
RFQ Endurance Test — 3-cycle scheduled strangle via RFQ.

Opens and closes a cheap long strangle 3 times at fixed UTC timestamps.
Each cycle:
  - OPEN  window: 5 minutes (RFQ first, limit fallback)
  - HOLD  period: 10 minutes
  - CLOSE window: 5 minutes (RFQ first, limit fallback)
  - GAP   before next cycle: 5 minutes

Schedule (computed from launch time T):
  Cycle 1: open at T+1m,  close at T+11m
  Cycle 2: open at T+16m, close at T+26m
  Cycle 3: open at T+31m, close at T+41m

Instrument:
  0.05Δ BTC strangle, 1 DTE, 0.5 lot (buy)
  Notional ≈ $65K (above $50K RFQ minimum)

Execution:
  1. Try RFQ for up to 5 minutes (accept immediately if ≥ orderbook)
  2. If RFQ fails/expires, fall back to limit orders

Usage:
    from strategies import rfq_endurance_test
    configs = rfq_endurance_test()  # returns list of 3 StrategyConfigs
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import List

from option_selection import strangle
from strategy import (
    StrategyConfig,
    min_available_margin_pct,
    utc_time_window,
    utc_datetime_exit,
    max_hold_hours,
)

logger = logging.getLogger(__name__)

# ─── Constants ───────────────────────────────────────────────────────────────

NUM_CYCLES = 3
OPEN_WINDOW_MIN = 5        # minutes allowed for RFQ + fallback execution
HOLD_MIN = 10              # minutes between open fill and scheduled close
GAP_MIN = 5                # minutes between close and next open
FIRST_OPEN_OFFSET_MIN = 1  # minutes after launch for first open

QTY = 0.5
CALL_DELTA = 0.05
PUT_DELTA = -0.05
DTE = 2                 # 25Feb expiry (better liquidity than 1 DTE)

RFQ_TIMEOUT_SECONDS = 300  # 5 minutes
RFQ_FALLBACK = "limit"     # fall back to per-leg limit orders


# ─── Schedule Builder ────────────────────────────────────────────────────────

def _build_schedule(
    launch: datetime,
    num_cycles: int = NUM_CYCLES,
) -> List[dict]:
    """
    Compute fixed UTC open/close timestamps for each cycle.

    Returns list of dicts with keys: cycle, open_start, open_end, close_at.
    """
    schedule = []
    cursor = launch + timedelta(minutes=FIRST_OPEN_OFFSET_MIN)

    for i in range(num_cycles):
        open_start = cursor
        open_end = open_start + timedelta(minutes=OPEN_WINDOW_MIN)
        close_at = open_start + timedelta(minutes=HOLD_MIN)
        schedule.append({
            "cycle": i + 1,
            "open_start": open_start,
            "open_end": open_end,
            "close_at": close_at,
        })
        # Next cycle starts after close_at + GAP
        cursor = close_at + timedelta(minutes=GAP_MIN)

    return schedule


def _log_schedule(schedule: List[dict]) -> None:
    """Pretty-print the schedule to the log and stdout."""
    fmt = "%H:%M:%S UTC"
    border = "=" * 60
    lines = [
        border,
        " RFQ ENDURANCE TEST — SCHEDULE",
        border,
    ]
    for s in schedule:
        lines.append(
            f"  Cycle {s['cycle']}: "
            f"OPEN {s['open_start'].strftime(fmt)} – {s['open_end'].strftime(fmt)}, "
            f"CLOSE at {s['close_at'].strftime(fmt)}"
        )
    est_end = schedule[-1]["close_at"] + timedelta(minutes=OPEN_WINDOW_MIN)
    lines.append(f"  Estimated completion: ~{est_end.strftime(fmt)}")
    lines.append(border)
    msg = "\n".join(lines)
    logger.info(msg)
    print(msg)


# ─── Public Factory ──────────────────────────────────────────────────────────

def rfq_endurance_test() -> List[StrategyConfig]:
    """
    Generate 3 StrategyConfigs — one per scheduled cycle.

    Returns a list (not a single config) so the launcher can register
    each as its own StrategyRunner.
    """
    launch = datetime.now(timezone.utc)
    schedule = _build_schedule(launch)
    _log_schedule(schedule)

    legs = strangle(
        qty=QTY,
        call_delta=CALL_DELTA,
        put_delta=PUT_DELTA,
        dte=DTE,
        side=1,  # buy
    )

    configs: List[StrategyConfig] = []

    for s in schedule:
        name = f"rfq_endurance_c{s['cycle']}"

        config = StrategyConfig(
            name=name,
            legs=legs,
            entry_conditions=[
                min_available_margin_pct(30),
                utc_time_window(s["open_start"], s["open_end"]),
            ],
            exit_conditions=[
                utc_datetime_exit(s["close_at"]),
                # Safety net: force-close after 20 min no matter what
                max_hold_hours(20 / 60),
            ],
            execution_mode="rfq",
            rfq_action="buy",
            max_concurrent_trades=1,
            max_trades_per_day=1,
            cooldown_seconds=0,
            check_interval_seconds=5,
            metadata={
                "rfq_timeout_seconds": RFQ_TIMEOUT_SECONDS,
                "rfq_min_improvement_pct": 0.0,  # only accept quotes ≥ orderbook
                "rfq_fallback": RFQ_FALLBACK,
                "cycle": s["cycle"],
                "schedule": {
                    "open_start": s["open_start"].isoformat(),
                    "open_end": s["open_end"].isoformat(),
                    "close_at": s["close_at"].isoformat(),
                },
            },
        )
        configs.append(config)

    return configs
