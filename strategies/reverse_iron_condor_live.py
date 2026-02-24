"""
Reverse Iron Condor — Live Trading Strategy

Opens a reverse iron condor every morning at 7:05 UTC.
Holds until 8:00 UTC (1DTE expiry), then closes automatically.

Structure (all 1DTE):
  - BUY  put  (inner):  delta ≈ -0.45
  - SELL put  (outer):  strike = inner_put_strike - $1000
  - BUY  call (inner):  delta ≈ +0.45
  - SELL call (outer):  strike = inner_call_strike + $1000

Reverse iron condors are long vega/gamma structures with positive premium decay.
They profit from low volatility and consolidation.

Entry:    7:05 UTC daily
Exit:     8:00 UTC (end of 1DTE)
Quantity: 0.5 BTC per leg
Mode:     RFQ (block trades) with intelligent execution
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import List

from option_selection import LegSpec, select_option
from strategy import (
    StrategyConfig,
    min_available_margin_pct,
    utc_time_window,
    utc_datetime_exit,
    structure_delta_limit,
    max_account_delta,
)

logger = logging.getLogger(__name__)

# ─── Configuration ──────────────────────────────────────────────────────────

QTY = 0.6                           # Quantity per leg (fixed at 0.6 BTC)
DTE = 1                             # 1 day to expiry
INNER_CALL_DELTA = 0.45
INNER_PUT_DELTA = -0.45
STRIKE_OFFSET = 1000                # Outer legs are $1000 further OTM
ENTRY_HOUR = 7                      # 7:05 UTC
ENTRY_MINUTE = 5
ENTRY_WINDOW_MINUTES = 5            # Allow 5 minutes for fill
MIN_MARGIN_PCT = 20                 # Require 20% available margin
RFQ_TIMEOUT_SECONDS = 300           # 5 minutes for RFQ before limit order fallback
RFQ_FALLBACK = "limit"              # Fall back to limit orders if RFQ times out


# ─── Helper: Resolve Reverse Iron Condor Legs ────────────────────────────────

def _resolve_reverse_iron_condor_legs(
    qty: float = QTY,
    inner_call_delta: float = INNER_CALL_DELTA,
    inner_put_delta: float = INNER_PUT_DELTA,
    dte: int = DTE,
    strike_offset: float = STRIKE_OFFSET,
) -> List[LegSpec]:
    """
    Resolve reverse iron condor legs by:
      1. Finding inner call/put by delta
      2. Extracting their strike prices
      3. Building outer strikes (offset by $strike_offset)
      4. Returning LegSpecs with exact strike criteria

    The strategy is:
      - BUY  inner put  (δ ≈ -0.45)
      - SELL outer put  (strike - offset)
      - BUY  inner call (δ ≈ +0.45)
      - SELL outer call (strike + offset)

    Args:
        qty: BTC quantity per leg
        inner_call_delta: Target call delta (positive)
        inner_put_delta: Target put delta (negative)
        dte: Days to expiry (1 = 1DTE)
        strike_offset: Distance in USD between inner and outer strikes

    Returns:
        List of 4 LegSpec objects (put, put, call, call)

    Raises:
        ValueError: If inner legs cannot be resolved
    """
    logger.info(
        f"Resolving reverse iron condor: "
        f"call_delta={inner_call_delta}, put_delta={inner_put_delta}, "
        f"dte={dte}, offset=${strike_offset}"
    )

    # Resolve inner call by delta
    inner_call_sym = select_option(
        expiry_criteria={"dte": dte},
        strike_criteria={"type": "delta", "value": inner_call_delta},
        option_type="C",
    )
    if not inner_call_sym:
        raise ValueError(
            f"Could not resolve inner call with delta {inner_call_delta}"
        )

    # Resolve inner put by delta
    inner_put_sym = select_option(
        expiry_criteria={"dte": dte},
        strike_criteria={"type": "delta", "value": inner_put_delta},
        option_type="P",
    )
    if not inner_put_sym:
        raise ValueError(
            f"Could not resolve inner put with delta {inner_put_delta}"
        )

    # Extract strikes from symbol names
    # Format: BTCUSD-27FEB26-75000-C
    parts_call = inner_call_sym.split("-")
    parts_put = inner_put_sym.split("-")

    try:
        inner_call_strike = float(parts_call[2])
        inner_put_strike = float(parts_put[2])
    except (IndexError, ValueError) as e:
        raise ValueError(
            f"Could not parse strikes from symbols: {inner_call_sym}, {inner_put_sym}: {e}"
        )

    # Calculate outer strikes
    outer_call_strike = inner_call_strike + strike_offset
    outer_put_strike = inner_put_strike - strike_offset

    logger.info(
        f"Inner strikes resolved: "
        f"call={inner_call_strike} (from {inner_call_sym}), "
        f"put={inner_put_strike} (from {inner_put_sym})"
    )
    logger.info(
        f"Outer strikes calculated: "
        f"call={outer_call_strike}, put={outer_put_strike}"
    )

    # Build LegSpecs with exact strike criteria
    # Order: inner put (long), outer put (short), inner call (long), outer call (short)
    legs = [
        # Inner put — LONG (buy)
        LegSpec(
            option_type="P",
            side=1,  # BUY
            qty=qty,
            strike_criteria={"type": "strike", "value": inner_put_strike},
            expiry_criteria={"dte": dte},
        ),
        # Outer put — SHORT (sell)
        LegSpec(
            option_type="P",
            side=2,  # SELL
            qty=qty,
            strike_criteria={"type": "strike", "value": outer_put_strike},
            expiry_criteria={"dte": dte},
        ),
        # Inner call — LONG (buy)
        LegSpec(
            option_type="C",
            side=1,  # BUY
            qty=qty,
            strike_criteria={"type": "strike", "value": inner_call_strike},
            expiry_criteria={"dte": dte},
        ),
        # Outer call — SHORT (sell)
        LegSpec(
            option_type="C",
            side=2,  # SELL
            qty=qty,
            strike_criteria={"type": "strike", "value": outer_call_strike},
            expiry_criteria={"dte": dte},
        ),
    ]

    return legs


# ─── Strategy Factory ────────────────────────────────────────────────────────

def reverse_iron_condor_live() -> StrategyConfig:
    """
    Reverse iron condor strategy — live trading at 7:05 UTC daily.

    Entry:
      - Every morning at 7:05 UTC
      - Requires ≥20% available margin
      - Account delta must be within ±0.5 BTC notional

    Legs (1DTE):
      - Buy  put  (δ ≈ -0.45)
      - Sell put  (strike - $1000)
      - Buy  call (δ ≈ +0.45)
      - Sell call (strike + $1000)

    Exit:
      - Hard close at 8:00 UTC (end of 1DTE day)
      - Position delta limited to ±0.15 (delta hedging)

    Execution:
      - RFQ for block trades ($50k+ notional)
      - Intelligent filll with requoting
    """
    # Calculate entry/exit times for today
    now_utc = datetime.now(timezone.utc)
    entry_start = now_utc.replace(hour=ENTRY_HOUR, minute=ENTRY_MINUTE, second=0, microsecond=0)
    entry_end = entry_start + timedelta(minutes=ENTRY_WINDOW_MINUTES)

    # 1DTE expires at 8:00 UTC next day
    exit_time = now_utc.replace(hour=8, minute=0, second=0, microsecond=0)
    if exit_time <= now_utc:
        # Exit time is tomorrow
        exit_time = exit_time + timedelta(days=1)

    logger.info(
        f"Reverse iron condor strategy — "
        f"entry window {entry_start.strftime('%H:%M')}-{entry_end.strftime('%H:%M')} UTC, "
        f"exit at {exit_time.strftime('%H:%M')} UTC"
    )

    # Resolve legs — this pre-fetches inner strikes and builds outer leg criteria
    try:
        legs = _resolve_reverse_iron_condor_legs(
            qty=QTY,
            inner_call_delta=INNER_CALL_DELTA,
            inner_put_delta=INNER_PUT_DELTA,
            dte=DTE,
            strike_offset=STRIKE_OFFSET,
        )
    except ValueError as e:
        logger.error(f"Failed to resolve reverse iron condor legs: {e}")
        raise

    return StrategyConfig(
        name="reverse_iron_condor_live",
        legs=legs,
        entry_conditions=[
            min_available_margin_pct(MIN_MARGIN_PCT),
            utc_time_window(entry_start, entry_end),
            max_account_delta(0.5),  # Account delta within ±0.5 BTC
        ],
        exit_conditions=[
            utc_datetime_exit(exit_time),
            structure_delta_limit(0.15),  # Close if structure delta > ±0.15
        ],
        execution_mode="rfq",
        rfq_action="buy",  # We're buying the structure (net long put spread + long call spread)
        max_concurrent_trades=2,  # Allow 2 concurrent: new 7:05 entry + previous day's 8:00 exit
        max_trades_per_day=1,
        cooldown_seconds=300,  # 5 minutes between attempts
        check_interval_seconds=10,  # Check every 10 seconds
        metadata={
            "strategy_type": "reverse_iron_condor",
            "qty_per_leg": QTY,
            "inner_call_delta": INNER_CALL_DELTA,
            "inner_put_delta": INNER_PUT_DELTA,
            "strike_offset": STRIKE_OFFSET,
            "dte": DTE,
            "entry_window": f"{ENTRY_HOUR:02d}:{ENTRY_MINUTE:02d}-{ENTRY_HOUR:02d}:{ENTRY_MINUTE+ENTRY_WINDOW_MINUTES:02d} UTC",
            "exit_time": exit_time.isoformat(),
            "rfq_timeout_seconds": RFQ_TIMEOUT_SECONDS,
            "rfq_min_improvement_pct": 0.0,  # Accept quotes >= orderbook
            "rfq_fallback": RFQ_FALLBACK,    # Fall back to limit orders if RFQ timeout
        },
    )
