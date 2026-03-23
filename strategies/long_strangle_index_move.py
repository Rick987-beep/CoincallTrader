"""
Long Strangle (Index Move) — Daily Long ±$2000 Strangle with Index Distance Exit

Opens a long strangle (buy OTM call + buy OTM put, each ±$2000 from ATM)
at 13:00 UTC daily, then closes it when either:
  1. The BTC index has moved ≥ $1500 from entry (symmetric — up or down), OR
  2. The hard close time is reached at 19:00 UTC (6h max hold).

This is the #1 ranked configuration from the Deribit backtester composite score:
  - Structure: ±$2000 OTM strangle (nearest available $500 Deribit strike)
  - Entry: 13:00 UTC
  - Trigger: $1500 index move from entry
  - Max hold: 6h (hard close 19:00 UTC)
  - Backtest stats (25 trades, composite score 0.805):
      Avg P&L $40 | Median $39 | Win rate 52% | Sharpe 0.34 | Calmar 3.8

Framework features used:
  ✓ LegSpec with spotOffset strike criteria (call ATM+$2000, put ATM-$2000)
  ✓ Entry conditions: time_window, min_available_margin_pct
  ✓ Exit conditions: index_move_distance (custom), time_exit
  ✓ on_trade_opened / on_trade_closed callbacks
  ✓ max_trades_per_day for daily repeat
  ✓ on_runner_created to capture TradingContext for exchange-aware index price

Usage:
    # In main.py STRATEGIES list:
    from strategies import long_strangle_index_move
    STRATEGIES = [long_strangle_index_move]
"""

import logging
from datetime import datetime, timezone
from typing import List

from option_selection import LegSpec
from strategy import (
    StrategyConfig,
    # Entry conditions
    time_window,
    min_available_margin_pct,
    # Exit conditions
    time_exit,
)
from telegram_notifier import get_notifier

logger = logging.getLogger(__name__)


# ─── Strategy Parameters ────────────────────────────────────────────────────
# Overridable via PARAM_* env vars (set by slot .toml config at deploy time).
# Defaults below match the current production values.

import os as _os
def _p(name, default, cast=float):
    """Read PARAM_<NAME> from env, falling back to default."""
    return cast(_os.getenv(f"PARAM_{name}", str(default)))

# Structure
QTY = _p("QTY", 0.1)                             # BTC per leg
DTE = "next"                                       # expiry: nearest available on Deribit
STRIKE_OFFSET_USD = _p("STRIKE_OFFSET_USD", 2000, int)  # each leg is this far OTM from ATM
                                                   # Deribit $500 strike spacing: nearest strike selected

# Scheduling
OPEN_HOUR = _p("OPEN_HOUR", 13, int)              # UTC hour to open (13:00 UTC)
CLOSE_HOUR = _p("CLOSE_HOUR", 19, int)            # UTC hour for hard close (19:00 UTC — 6h max hold)
CLOSE_MINUTE = _p("CLOSE_MINUTE", 0, int)

# Index move exit
MOVE_DISTANCE_USD = _p("MOVE_DISTANCE_USD", 1500, int)  # close when BTC index moves $1500 from entry

# Risk / margin
MIN_MARGIN_PCT = _p("MIN_MARGIN_PCT", 20, int)    # require ≥20% available margin before entry

# Operational
CHECK_INTERVAL = _p("CHECK_INTERVAL", 30, int)    # seconds between entry/exit evaluations


# ─── TradingContext Capture ──────────────────────────────────────────────────

_ctx = None  # type: ignore  # populated by the on_runner_created hook


def _capture_context(runner) -> None:
    """on_runner_created hook: captures TradingContext for exchange-aware index price."""
    global _ctx
    _ctx = runner.ctx
    logger.info("[Long Strangle] TradingContext captured.")


def _get_index_price(use_cache: bool = False):
    """Fetch BTC index price via the active exchange adapter."""
    if _ctx is not None:
        return _ctx.market_data.get_index_price(use_cache=use_cache)
    logger.warning("[Long Strangle] _ctx not set — cannot fetch BTC index price")
    return None


# ─── Exit Condition: Index Move Distance ────────────────────────────────────

def index_move_distance(distance_usd):
    """
    Exit condition factory: close when the BTC index has moved ≥ distance_usd
    from the entry price captured in trade.metadata["entry_index_price"].
    """
    def _check(account, trade):
        entry_price = trade.metadata.get("entry_index_price")
        if entry_price is None:
            return False

        current_price = _get_index_price(use_cache=False)
        if current_price is None:
            return False

        move = abs(current_price - entry_price)
        if move >= distance_usd:
            logger.info(
                f"[Index Move] BTC moved ${move:.0f} "
                f"(entry: ${entry_price:.0f}, now: ${current_price:.0f}, "
                f"threshold: ${distance_usd})"
            )
            return True
        return False

    _check.__name__ = f"index_move_{distance_usd}usd"
    return _check


# ─── Trade Callbacks ────────────────────────────────────────────────────────

def _on_trade_opened(trade, account) -> None:
    """Capture BTC index price at entry and send Telegram notification."""
    index_price = _get_index_price(use_cache=False)
    if index_price is not None:
        trade.metadata["entry_index_price"] = index_price
        logger.info(
            f"[Long Strangle] Opened — entry index: ${index_price:.0f}, "
            f"exit trigger: ±${MOVE_DISTANCE_USD}, hard close: {CLOSE_HOUR:02d}:00 UTC"
        )
    else:
        logger.warning("[Long Strangle] Could not capture entry index price!")

    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
    entry_cost = trade.total_entry_cost()
    legs_text = "\n".join(
        f"  {leg.side.upper()} {leg.qty}× {leg.symbol}"
        for leg in trade.open_legs
    )
    idx_text = f"BTC index: ${index_price:,.0f}" if index_price else "BTC index: N/A"
    try:
        get_notifier().send(
            f"📊 <b>Long Strangle (Index Move) — Trade Opened</b>\n"
            f"Time: {ts}\n"
            f"ID: {trade.id}\n"
            f"{legs_text}\n"
            f"Entry cost: ${entry_cost:.2f}\n"
            f"{idx_text}  |  Trigger: ±${MOVE_DISTANCE_USD}  |  Hard close: {CLOSE_HOUR:02d}:00 UTC\n"
            f"Equity: ${account.equity:,.2f}\n"
            f"Avail margin: ${account.available_margin:,.2f} "
            f"({100 - account.margin_utilization:.1f}% free)"
        )
    except Exception:
        pass


def _on_trade_closed(trade, account) -> None:
    """Log PnL and index move at close, send Telegram notification."""
    pnl = trade.realized_pnl if trade.realized_pnl is not None else 0.0
    entry_cost = trade.total_entry_cost()
    roi = (pnl / abs(entry_cost) * 100) if entry_cost else 0.0
    hold_seconds = trade.hold_seconds or 0

    entry_index = trade.metadata.get("entry_index_price")
    close_index = _get_index_price(use_cache=False)
    index_move = abs(close_index - entry_index) if (entry_index and close_index) else None

    msg = (
        f"[Long Strangle] Trade closed: {trade.id}  |  "
        f"PnL: ${pnl:+.2f}  |  ROI: {roi:+.1f}%  |  "
        f"Hold: {hold_seconds / 60:.1f} min  |  "
        f"Entry cost: ${entry_cost:.2f}"
    )
    if index_move is not None:
        msg += f"  |  Index move: ${index_move:.0f}"
    logger.info(msg)

    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
    emoji = "✅" if pnl >= 0 else "❌"
    legs_text = ""
    if trade.close_legs:
        legs_text = "\n".join(
            f"  {leg.side.upper()} {leg.filled_qty}× {leg.symbol} @ ${leg.fill_price}"
            for leg in trade.close_legs
        ) + "\n"
    entry_idx_text = f"Entry index: ${entry_index:,.0f}" if entry_index else ""
    close_idx_text = f"Close index: ${close_index:,.0f}" if close_index else ""
    idx_text = f"Index move: ${index_move:,.0f}" if index_move is not None else ""
    try:
        get_notifier().send(
            f"{emoji} <b>Long Strangle (Index Move) — Trade Closed</b>\n"
            f"Time: {ts}\n"
            f"ID: {trade.id}\n"
            f"PnL: <b>${pnl:+.2f}</b> ({roi:+.1f}%)\n"
            f"Hold: {hold_seconds / 60:.1f} min\n"
            f"Entry cost: ${entry_cost:.2f}\n"
            f"{legs_text}"
            f"{entry_idx_text}  →  {close_idx_text}  |  {idx_text}\n"
            f"Equity: ${account.equity:,.2f}\n"
            f"Avail margin: ${account.available_margin:,.2f} "
            f"({100 - account.margin_utilization:.1f}% free)"
        )
    except Exception:
        pass


# ─── Leg Templates ──────────────────────────────────────────────────────────

def _build_legs() -> List[LegSpec]:
    """
    Build LegSpec list for a ±$2000 long strangle.

    Uses the 'spotOffset' strike criteria type (added to option_selection.py):
    the value is a USD offset applied to the spot price at selection time.
    Deribit strike spacing is $500 — the nearest available strike is picked.
    """
    expiry = {"dte": DTE}
    return [
        LegSpec(
            option_type="C",
            side="buy",
            qty=QTY,
            strike_criteria={"type": "spotOffset", "value": +STRIKE_OFFSET_USD},
            expiry_criteria=expiry,
        ),
        LegSpec(
            option_type="P",
            side="buy",
            qty=QTY,
            strike_criteria={"type": "spotOffset", "value": -STRIKE_OFFSET_USD},
            expiry_criteria=expiry,
        ),
    ]


# ─── Strategy Factory ──────────────────────────────────────────────────────

def long_strangle_index_move() -> StrategyConfig:
    """
    Daily ±$2000 long strangle — buy OTM call + OTM put, close on
    $1500 BTC index move or at 19:00 UTC (6h hard cap).

    Backtested #1 composite config: score 0.805, avg P&L $40, Sharpe 0.34.
    """
    return StrategyConfig(
        name="long_strangle_index_move",

        # ── What to trade ────────────────────────────────────────────────
        legs=_build_legs(),

        # ── When to enter ────────────────────────────────────────────────
        entry_conditions=[
            time_window(OPEN_HOUR, OPEN_HOUR + 1),
            min_available_margin_pct(MIN_MARGIN_PCT),
        ],

        # ── When to exit ─────────────────────────────────────────────────
        exit_conditions=[
            index_move_distance(MOVE_DISTANCE_USD),
            time_exit(CLOSE_HOUR, CLOSE_MINUTE),
        ],

        # ── How to execute ───────────────────────────────────────────────
        execution_mode="limit",

        # ── Operational limits ───────────────────────────────────────────
        max_concurrent_trades=1,
        max_trades_per_day=1,
        cooldown_seconds=0,
        check_interval_seconds=CHECK_INTERVAL,

        # ── Callbacks ────────────────────────────────────────────────────
        on_trade_opened=_on_trade_opened,
        on_trade_closed=_on_trade_closed,

        metadata={
            "on_runner_created": _capture_context,
        },
    )
