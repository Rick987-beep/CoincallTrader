"""
Smoke Test Strangle — Deribit Adapter Verification

Tests Deribit exchange adapters under slightly more complex quoting.
Opens a 1-contract long strangle and escalates pricing across three
phases over 60 seconds to probe orderbook + mark price handling.

Phased execution (buy side escalation):
  Phase 1 (20s): passive — post at bid (most conservative)
  Phase 2 (20s): mark   — use Deribit mark price (BTC→USD converted)
  Phase 3 (20s): aggressive — cross the spread at ask + buffer

Expected behavior:
  1. Opens 1-contract long strangle (±0.30 delta, next expiry)
  2. Walks through 3 pricing phases, repricing every 10s
  3. Holds for ~60 seconds (max_hold_hours exit)
  4. Closes via same phased pricing
  5. Logs result + Telegram notification

Requires:
  EXCHANGE=deribit  TRADING_ENVIRONMENT=testnet
  .env must have DERIBIT_CLIENT_ID_TEST / DERIBIT_CLIENT_SECRET_TEST
"""

import logging

from option_selection import strangle
from strategy import (
    StrategyConfig,
    time_window,
    max_hold_hours,
)
from trade_execution import ExecutionParams, ExecutionPhase
from telegram_notifier import get_notifier

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


# ─── Parameters ─────────────────────────────────────────────────────────────

QTY = 1
CALL_DELTA = 0.30
PUT_DELTA = -0.30
DTE = "next"
SIDE = "buy"
HOLD_MINUTES = 1

EXECUTION_PHASES = ExecutionParams(
    phases=[
        ExecutionPhase(pricing="passive",    duration_seconds=20, reprice_interval=10),
        ExecutionPhase(pricing="mark",       duration_seconds=20, reprice_interval=10),
        ExecutionPhase(pricing="aggressive", duration_seconds=20, reprice_interval=10, buffer_pct=10.0),
    ]
)


# ─── Callbacks ──────────────────────────────────────────────────────────────

def _on_trade_opened(trade, account) -> None:
    logger.info(f"[DERIBIT SMOKE] Trade opened: {trade.id}")
    for leg in trade.open_legs:
        logger.debug(f"  Leg: {leg.symbol}  side={leg.side}  qty={leg.qty}  fill={leg.fill_price}")
    try:
        get_notifier().notify_trade_opened(
            strategy_name="Smoke Test Strangle (Deribit)",
            trade_id=trade.id,
            legs=trade.open_legs,
            entry_cost=trade.total_entry_cost(),
        )
    except Exception:
        pass


def _on_trade_closed(trade, account) -> None:
    pnl = trade.realized_pnl if trade.realized_pnl is not None else 0.0
    entry_cost = trade.total_entry_cost()
    roi = (pnl / abs(entry_cost) * 100) if entry_cost else 0.0
    hold_seconds = trade.hold_seconds or 0

    logger.info(
        f"[DERIBIT SMOKE] Trade closed: {trade.id}  |  "
        f"PnL: ${pnl:+.2f}  |  ROI: {roi:+.1f}%  |  "
        f"Hold: {hold_seconds:.0f}s  |  Entry: ${entry_cost:.2f}"
    )
    try:
        get_notifier().notify_trade_closed(
            strategy_name="Smoke Test Strangle (Deribit)",
            trade_id=trade.id,
            pnl=pnl,
            roi=roi,
            hold_minutes=hold_seconds / 60,
            entry_cost=entry_cost,
            close_legs=trade.close_legs,
        )
    except Exception:
        pass


# ─── Strategy Factory ──────────────────────────────────────────────────────

def smoke_test_strangle() -> StrategyConfig:
    """Deribit testnet: 1-contract strangle with 3-phase pricing escalation."""
    return StrategyConfig(
        name="smoke_test_strangle",

        legs=strangle(
            qty=QTY,
            call_delta=CALL_DELTA,
            put_delta=PUT_DELTA,
            dte=DTE,
            side=SIDE,
        ),

        entry_conditions=[
            time_window(0, 23),
        ],

        exit_conditions=[
            max_hold_hours(HOLD_MINUTES / 60),
        ],

        execution_mode="limit",
        execution_params=EXECUTION_PHASES,

        max_concurrent_trades=1,
        max_trades_per_day=1,
        cooldown_seconds=0,
        check_interval_seconds=5,

        on_trade_opened=_on_trade_opened,
        on_trade_closed=_on_trade_closed,
    )
