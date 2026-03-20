"""
ATM_Str_fixpnl_Deribit — Daily Long ATM Straddle with Fixed-Dollar TP

Data-driven strategy derived from the Optimal Entry Window analysis
(analysis/optimal_entry_window/).  Top parameter combo from 4-week
backtest (Feb 19 – Mar 19, 2026):

    Structure:  Long ATM straddle (buy call + buy put at nearest strike)
    Entry:      10:00 UTC daily (weekdays only)
    Expiry:     Next-day (08:00 UTC tomorrow ≈ 22h DTE at entry)
    Quantity:   0.1 BTC per leg
    TP:         $1,000 BTC index excursion (|BTC_now - BTC_entry| >= $1,000)
    Time exit:  19:00 UTC (9h after entry — hard close)
    Execution:  Two-phase orderbook limit orders
                  Phase 1 (1 min): mid-price quoting, reprice every 15s
                  Phase 2 (1 min): aggressive limits crossing the spread

Exchange: Deribit (BTC-denominated option prices → USD conversion at boundary)

BTC↔USD PnL conversion:
    Deribit option prices are in BTC.  The custom dollar_profit_target exit
    condition uses structure_pnl() which reads position unrealized_pnl,
    already converted to USD by the DeribitAccountAdapter.  Fallback:
    executable_pnl() returns BTC PnL × index_price → USD.

Usage:
    # In main.py STRATEGIES list:
    from strategies.straddle_10utc import straddle_10utc
    STRATEGIES = [straddle_10utc]
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from option_selection import straddle
from strategy import (
    StrategyConfig,
    # Entry conditions
    time_window,
    min_available_margin_pct,
    # Exit conditions
    time_exit,
)
from trade_execution import ExecutionParams, ExecutionPhase
from telegram_notifier import get_notifier

logger = logging.getLogger(__name__)


# ─── Strategy Parameters ────────────────────────────────────────────────────

# Structure
QTY = 0.1                           # BTC per leg
DTE = "next"                        # Nearest expiry (next-day 08:00 UTC)

# Scheduling — when to open and close (UTC)
OPEN_HOUR = 10                      # Entry at 10:00 UTC
CLOSE_HOUR = 19                     # Hard exit at 19:00 UTC (9h hold)
CLOSE_MINUTE = 0

# Take-profit — BTC index excursion
EXCURSION_USD = 1000.0               # Close when |BTC_now - BTC_at_entry| >= $1,000

# Risk / margin
MIN_MARGIN_PCT = 20                 # Require ≥20% available margin

# Execution — two-phase limit order plan
PHASE1_SECONDS = 60                 # Phase 1: 1 min at mid price
PHASE1_REPRICE = 15                 # Reprice every 15s in phase 1
PHASE2_SECONDS = 60                 # Phase 2: 1 min aggressive
PHASE2_BUFFER_PCT = 5.0             # Cross spread with 5% buffer
PHASE2_REPRICE = 10                 # Reprice every 10s in phase 2

# Operational
CHECK_INTERVAL = 30                 # Seconds between entry/exit evaluations
STRATEGY_NAME = "ATM_Str_fixpnl_Deribit"


# ─── Execution Configuration ────────────────────────────────────────────────

def _build_execution_params() -> ExecutionParams:
    """Two-phase limit order plan: mid-price → aggressive cross."""
    return ExecutionParams(
        phases=[
            ExecutionPhase(
                pricing="mid",
                duration_seconds=PHASE1_SECONDS,
                reprice_interval=PHASE1_REPRICE,
            ),
            ExecutionPhase(
                pricing="aggressive",
                duration_seconds=PHASE2_SECONDS,
                buffer_pct=PHASE2_BUFFER_PCT,
                reprice_interval=PHASE2_REPRICE,
            ),
        ],
    )


# ─── Custom Entry Condition: Weekday Filter ─────────────────────────────────

def _weekday_only():
    """Entry condition: skip Saturday (5) and Sunday (6)."""
    def _check(account) -> bool:
        return datetime.now(timezone.utc).weekday() < 5
    _check.__name__ = "weekday_only"
    return _check


# ─── Custom Exit Condition: Index Excursion TP ──────────────────────────────

def _index_excursion_tp(excursion_usd: float):
    """
    Exit condition: close when BTC index price has moved ≥ excursion_usd
    from the price recorded at trade open, in either direction.

    This mirrors the metric from the hourly excursion analysis:
      max(|BTC_high - BTC_entry|, |BTC_entry - BTC_low|) ≥ threshold

    The entry price is stored in trade.metadata["entry_index_price"]
    by the on_trade_opened callback.

    Args:
        excursion_usd: Dollar move threshold (e.g. 1000.0 for $1,000).
    """
    label = f"excursion_tp(${excursion_usd:.0f})"

    def _check(account, trade) -> bool:
        entry_price = trade.metadata.get("entry_index_price")
        if not entry_price:
            return False  # not yet recorded — skip this tick

        from market_data import get_btc_index_price
        current_price = get_btc_index_price(use_cache=True)
        if not current_price or current_price <= 0:
            return False

        move = abs(current_price - entry_price)
        triggered = move >= excursion_usd
        if triggered:
            direction = "↑" if current_price > entry_price else "↓"
            logger.info(
                f"[{trade.id}] {label} triggered: BTC moved {direction}${move:,.0f} "
                f"(${entry_price:,.0f} → ${current_price:,.0f})"
            )
        return triggered

    _check.__name__ = label
    return _check


# ─── Trade Callbacks ────────────────────────────────────────────────────────

def _on_trade_opened(trade, account) -> None:
    """Send Telegram notification when straddle opens."""
    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
    entry_cost = trade.total_entry_cost()
    legs_text = "\n".join(
        f"  {leg.side.upper()} {leg.filled_qty}× {leg.symbol} "
        f"@ {leg.fill_price:.6f} BTC"
        for leg in trade.open_legs
        if leg.fill_price is not None
    )

    # Capture entry index price for close reporting
    from market_data import get_btc_index_price
    index_price = get_btc_index_price(use_cache=False)
    if index_price:
        trade.metadata["entry_index_price"] = index_price
        entry_cost_usd = abs(entry_cost) * index_price
    else:
        entry_cost_usd = 0.0

    logger.info(
        f"[{STRATEGY_NAME}] Opened: {trade.id} | "
        f"Entry cost: {entry_cost:.6f} BTC (${entry_cost_usd:,.2f}) | "
        f"BTC=${index_price:,.0f} | "
        f"TP: ±${EXCURSION_USD:,.0f} move"
    )

    try:
        msg_parts = [
            f"📈 <b>{STRATEGY_NAME} — Trade Opened</b>",
            f"Time: {ts}",
            f"ID: {trade.id}",
            legs_text,
            f"Entry cost: {entry_cost:.6f} BTC (${entry_cost_usd:,.2f})",
        ]
        if index_price:
            msg_parts.append(f"BTC: ${index_price:,.0f}")
        msg_parts.append(
            f"TP: BTC ±${EXCURSION_USD:,.0f} move | Hard close: {CLOSE_HOUR}:00 UTC"
        )
        msg_parts.append(f"Equity: ${account.equity:,.2f}")
        get_notifier().send("\n".join(msg_parts))
    except Exception:
        pass


def _on_trade_closed(trade, account) -> None:
    """Log PnL and send Telegram notification when straddle closes."""
    # Compute PnL in USD
    pnl_btc = trade.realized_pnl if trade.realized_pnl is not None else 0.0
    entry_cost = trade.total_entry_cost()
    hold_seconds = trade.hold_seconds or 0

    # BTC→USD conversion for realized PnL
    from market_data import get_btc_index_price
    index_price = get_btc_index_price(use_cache=False)
    pnl_usd = pnl_btc * index_price if index_price else 0.0
    entry_cost_usd = abs(entry_cost) * index_price if index_price else 0.0
    roi = (pnl_btc / abs(entry_cost) * 100) if entry_cost else 0.0

    # Determine exit reason
    entry_index = trade.metadata.get("entry_index_price", 0)
    btc_move = abs(index_price - entry_index) if index_price and entry_index else 0
    exit_reason = "unknown"
    if btc_move >= EXCURSION_USD * 0.9:  # within 10% of threshold → likely TP
        direction = "↑" if index_price > entry_index else "↓"
        exit_reason = f"excursion TP ({direction}${btc_move:,.0f})"
    elif hold_seconds >= (CLOSE_HOUR - OPEN_HOUR) * 3600 - 120:
        exit_reason = f"time exit ({CLOSE_HOUR}:00 UTC)"
    elif pnl_usd > 0:
        exit_reason = "profit (other)"
    else:
        exit_reason = f"time exit (PnL ${pnl_usd:+,.0f})"

    logger.info(
        f"[{STRATEGY_NAME}] Closed: {trade.id} | "
        f"PnL: {pnl_btc:+.6f} BTC (${pnl_usd:+,.2f}) | "
        f"ROI: {roi:+.1f}% | Hold: {hold_seconds/60:.1f} min | "
        f"Exit: {exit_reason}"
    )

    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
    emoji = "✅" if pnl_usd >= 0 else "❌"
    close_detail = ""
    if trade.close_legs:
        close_detail = "\n".join(
            f"  {leg.side.upper()} {leg.filled_qty}× {leg.symbol} "
            f"@ {leg.fill_price:.6f} BTC"
            for leg in trade.close_legs
            if leg.fill_price is not None
        )
    try:
        msg_parts = [
            f"{emoji} <b>{STRATEGY_NAME} — Trade Closed</b>",
            f"Time: {ts}",
            f"ID: {trade.id}",
            f"Exit: {exit_reason}",
            f"PnL: <b>${pnl_usd:+,.2f}</b> ({pnl_btc:+.6f} BTC, {roi:+.1f}%)",
            f"Hold: {hold_seconds/60:.1f} min",
            f"Entry cost: ${entry_cost_usd:,.2f}",
        ]
        if entry_index and index_price:
            msg_parts.append(
                f"BTC: ${entry_index:,.0f} → ${index_price:,.0f} "
                f"(move: ${btc_move:+,.0f})"
            )
        if close_detail:
            msg_parts.append(close_detail)
        msg_parts.append(f"Equity: ${account.equity:,.2f}")
        get_notifier().send("\n".join(msg_parts))
    except Exception:
        pass


# ─── Strategy Factory ──────────────────────────────────────────────────────

def straddle_10utc() -> StrategyConfig:
    """
    ATM_Str_fixpnl_Deribit — daily long ATM straddle with index excursion TP.

    Derived from Optimal Entry Window analysis: enters at 10:00 UTC,
    closes when BTC moves $1,000 from entry, hard-closes at 19:00 UTC,
    weekdays only.

    Returns a StrategyConfig for registration in main.py's STRATEGIES list.
    """
    return StrategyConfig(
        name=STRATEGY_NAME,

        # ── What to trade ────────────────────────────────────────────
        legs=straddle(
            qty=QTY,
            dte=DTE,
            side="buy",
        ),

        # ── When to enter ────────────────────────────────────────────
        # All conditions must be True:
        #   1. Weekday (Mon–Fri)
        #   2. Inside the 10:00–11:00 UTC window
        #   3. Sufficient margin available
        entry_conditions=[
            _weekday_only(),
            time_window(OPEN_HOUR, OPEN_HOUR + 1),
            min_available_margin_pct(MIN_MARGIN_PCT),
        ],

        # ── When to exit ─────────────────────────────────────────────
        # ANY condition returning True triggers a close:
        #   1. Index excursion TP → close when BTC moves ±$1,000 from entry
        #   2. Time exit → hard close at 19:00 UTC (9h hold)
        exit_conditions=[
            _index_excursion_tp(EXCURSION_USD),
            time_exit(CLOSE_HOUR, CLOSE_MINUTE),
        ],

        # ── How to execute ───────────────────────────────────────────
        # Orderbook-based limit orders, two phases:
        #   Phase 1: 1 min quoting at mid price
        #   Phase 2: 1 min aggressive (cross spread with buffer)
        execution_mode="limit",
        execution_params=_build_execution_params(),

        # ── Operational limits ───────────────────────────────────────
        max_concurrent_trades=1,
        max_trades_per_day=1,
        cooldown_seconds=0,
        check_interval_seconds=CHECK_INTERVAL,

        # ── Callbacks ────────────────────────────────────────────────
        on_trade_opened=_on_trade_opened,
        on_trade_closed=_on_trade_closed,
    )
