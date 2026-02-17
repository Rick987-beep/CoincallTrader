"""
Micro Strangle — live execution test strategy.

Buy 0.01-lot 0.15Δ strangle, hold ~10s, close, repeat once (2 cycles).
Uses LimitFillManager with 30s requote timeout.
"""

from strategy import (
    StrategyConfig,
    max_hold_hours,
    min_available_margin_pct,
)
from option_selection import strangle
from trade_execution import ExecutionParams


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
        metadata={
            "execution_params": ExecutionParams(
                fill_timeout_seconds=30.0,
                aggressive_buffer_pct=2.0,
                max_requote_rounds=10,
            ),
        },
    )
