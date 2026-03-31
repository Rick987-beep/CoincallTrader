"""
Short Straddle / Strangle — 1DTE BTC Short Volatility Strategy

Sells an ATM straddle (OFFSET=0) or an OTM strangle (OFFSET>0) on the
nearest available Deribit expiry.  Collects the combined call+put premium
and exits when the first of these conditions fires:

    1. Stop-loss   — combined fair value of both legs rises to
                     (1 + STOP_LOSS_PCT) × combined premium collected.
                     E.g. STOP_LOSS_PCT=3.0 → SL fires when buyback
                     costs 4× what we sold for, a 300% loss.
    2. Max hold    — position has been open for MAX_HOLD_HOURS hours;
                     closes aggressively at bid.
    3. Expiry      — 08:00 UTC settlement by exchange; no close needed.

One entry per day; up to 2 positions open concurrently (yesterday's may
overlap with today's pre-08:00 entry).

Backtester2 optimised defaults (combo #2 — composite score 0.991):
    Entry hour:    04:00 UTC
    Offset:        $1000 from spot
    Stop-loss:     300% of premium collected
    Max hold:      20 hours

Open Execution (two phases, total ≤ 60s):
    Phase 1 (30s): both legs placed at fair price (fair = mid if mark off).
    Phase 2 (30s): any remaining unfilled leg re-priced to bid — aggressive
                   fill to eliminate legging risk.

Close Execution — SL triggered (two phases, total ≤ 60s):
    Phase 1 (30s): both open legs bought at fair price.
    Phase 2 (30s): any remaining unfilled leg re-priced to ask — aggressive.

Close Execution — Max-hold / manual (single aggressive phase, 30s):
    Both legs bought at ask in one shot.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from market_data import get_btc_index_price, get_option_market_data
from option_selection import strangle_by_offset, straddle
from strategy import (
    StrategyConfig,
    max_hold_hours,
    time_window,
)
from trade_execution import ExecutionParams, ExecutionPhase
from trade_lifecycle import RFQParams
from telegram_notifier import get_notifier

logger = logging.getLogger(__name__)


# ─── Strategy Parameters ────────────────────────────────────────────────────
# Overridable via PARAM_* env vars (set by slot .toml config at deploy time).
# Defaults are the backtester2 combo #2 (score 0.991, $7,820 PnL, 14 trades).

import os as _os
def _p(name, default, cast=float):
    """Read PARAM_<NAME> from env, falling back to default."""
    return cast(_os.getenv(f"PARAM_{name}", str(default)))

# Structure
QTY = _p("QTY", 1.0)                                  # contracts per leg (Deribit min=0.1, float)
OFFSET = _p("OFFSET", 1000, int)                       # USD offset from spot; 0 = ATM straddle
DTE = "next"                                            # nearest Deribit expiry

# Scheduling
ENTRY_HOUR = _p("ENTRY_HOUR", 4, int)                  # UTC hour to open (04:00 UTC window)

# Risk
STOP_LOSS_PCT = _p("STOP_LOSS_PCT", 3.0)               # SL fires when combined fair ≥ premium × (1 + pct)
MAX_HOLD_HOURS = _p("MAX_HOLD_HOURS", 20, int)          # force close after N hours

# Open execution
LIMIT_OPEN_FAIR_SECONDS = _p("LIMIT_OPEN_FAIR_SECONDS", 30, int)    # Phase 1: quote at fair
LIMIT_OPEN_AGG_SECONDS = _p("LIMIT_OPEN_AGG_SECONDS", 30, int)      # Phase 2: aggressive at bid

# SL close execution
SL_CLOSE_FAIR_SECONDS = _p("SL_CLOSE_FAIR_SECONDS", 30, int)        # Phase 1: buy at fair
SL_CLOSE_AGG_SECONDS = _p("SL_CLOSE_AGG_SECONDS", 30, int)          # Phase 2: aggressive at ask

# Max-hold close execution
HOLD_CLOSE_AGG_SECONDS = _p("HOLD_CLOSE_AGG_SECONDS", 30, int)      # Single aggressive phase at ask

# Operational
MAX_CONCURRENT = _p("MAX_CONCURRENT", 2, int)           # allow overlap (yesterday + today)
CHECK_INTERVAL = _p("CHECK_INTERVAL", 15, int)          # seconds between strategy ticks


# ─── Fair Price Helper ──────────────────────────────────────────────────────

def _fair(symbol):
    # type: (str) -> Optional[dict]
    """
    Compute fair price for one option leg.

    Returns dict with: fair, bid, ask, mark.
    Returns None if no market data.

    Fair price logic (same model as daily_put_sell):
      - mark, if mark is within bid/ask spread
      - mid = (bid+ask)/2, if mark is outside spread
      - max(mark, bid), if only bid side exists
      - mark alone, if book is completely empty
    """
    mkt = get_option_market_data(symbol)
    if not mkt:
        return None

    bid = float(mkt.get("bid", 0) or 0)
    ask = float(mkt.get("ask", 0) or 0)
    mark = float(mkt.get("mark_price", 0) or 0)

    if bid > 0 and ask > 0:
        fair = mark if bid <= mark <= ask else (bid + ask) / 2
    elif bid > 0:
        fair = max(mark, bid) if mark > 0 else bid
    elif mark > 0:
        fair = mark
    else:
        return None

    return {
        "fair": fair,
        "bid": bid if bid > 0 else None,
        "ask": ask if ask > 0 else None,
        "mark": mark,
    }


# ─── Exit Condition: Combined Fair-Price Stop Loss ──────────────────────────

def _combined_sl():
    """
    Exit condition: combined fair-price based stop loss.

    Fires when (call_fair + put_fair) >= combined_fill_premium * (1 + STOP_LOSS_PCT).
    On trigger, configures a phased limit buy-to-close on the trade.
    """
    label = f"combined_fair_sl({STOP_LOSS_PCT:.0%})"

    def _check(account, trade):
        open_legs = trade.open_legs
        if len(open_legs) < 2:
            return False

        call_leg = next((l for l in open_legs if l.symbol.endswith("-C")), None)
        put_leg = next((l for l in open_legs if l.symbol.endswith("-P")), None)
        if not call_leg or not put_leg:
            return False

        if not call_leg.fill_price or not put_leg.fill_price:
            return False

        sl_threshold = trade.metadata.get("sl_threshold")
        if sl_threshold is None:
            combined_premium = float(call_leg.fill_price) + float(put_leg.fill_price)
            sl_threshold = combined_premium * (1.0 + STOP_LOSS_PCT)
            trade.metadata["sl_threshold"] = sl_threshold
            trade.metadata["combined_premium"] = combined_premium

        # Current combined fair value
        call_fp = _fair(call_leg.symbol)
        put_fp = _fair(put_leg.symbol)
        if not call_fp or not put_fp:
            return False

        combined_fair = call_fp["fair"] + put_fp["fair"]
        triggered = combined_fair >= sl_threshold

        if triggered:
            combined_premium = trade.metadata.get("combined_premium", 0)
            loss_pct = (combined_fair - combined_premium) / combined_premium * 100 if combined_premium else 0
            logger.info(
                f"[{trade.id}] {label} TRIGGERED: combined_fair={combined_fair:.4f} "
                f">= threshold={sl_threshold:.4f} "
                f"(premium={combined_premium:.4f}, loss={loss_pct:.1f}%)"
            )
            logger.info(
                f"[{trade.id}] SL legs: "
                f"call fair={call_fp['fair']:.4f} bid={call_fp['bid'] or 0:.4f} ask={call_fp['ask'] or 0:.4f} | "
                f"put  fair={put_fp['fair']:.4f} bid={put_fp['bid'] or 0:.4f} ask={put_fp['ask'] or 0:.4f}"
            )
            trade.execution_mode = "limit"
            trade.metadata["sl_triggered"] = True
            trade.metadata["sl_triggered_at"] = time.time()
            trade.execution_params = ExecutionParams(phases=[
                # Phase 1: buy both legs at fair (passive)
                ExecutionPhase(
                    pricing="fair", fair_aggression=0.0,
                    duration_seconds=SL_CLOSE_FAIR_SECONDS,
                    reprice_interval=SL_CLOSE_FAIR_SECONDS,
                ),
                # Phase 2: aggressive — any unfilled leg bought at ask
                ExecutionPhase(
                    pricing="fair", fair_aggression=1.0,
                    duration_seconds=SL_CLOSE_AGG_SECONDS,
                    reprice_interval=15,
                ),
            ])

        return triggered

    _check.__name__ = label
    return _check


# ─── Trade Callbacks ────────────────────────────────────────────────────────

def _on_trade_opened(trade, account):
    # type: (...) -> None
    """Log entry details and send Telegram notification."""
    index_price = get_btc_index_price(use_cache=False)
    if index_price is not None:
        trade.metadata["entry_index_price"] = index_price

    open_legs = trade.open_legs
    call_leg = next((l for l in open_legs if l.symbol.endswith("-C")), None)
    put_leg = next((l for l in open_legs if l.symbol.endswith("-P")), None)

    call_fill = float(call_leg.fill_price) if call_leg and call_leg.fill_price else 0.0
    put_fill = float(put_leg.fill_price) if put_leg and put_leg.fill_price else 0.0
    combined_premium = call_fill + put_fill

    # Compute and store SL threshold
    if combined_premium > 0:
        sl_threshold = combined_premium * (1.0 + STOP_LOSS_PCT)
        trade.metadata["sl_threshold"] = sl_threshold
        trade.metadata["combined_premium"] = combined_premium

    # Configure max-hold close execution params (aggressive single phase)
    trade.execution_params = ExecutionParams(phases=[
        ExecutionPhase(
            pricing="fair", fair_aggression=1.0,
            duration_seconds=HOLD_CLOSE_AGG_SECONDS,
            reprice_interval=15,
        ),
    ])

    duration_s = int(trade.opened_at - trade.created_at) if trade.opened_at and trade.created_at else 0
    if duration_s <= LIMIT_OPEN_FAIR_SECONDS:
        phase_label = "Phase 1 (at fair)"
    else:
        phase_label = "Phase 2 (at bid)"

    # Fair prices at open for reference
    call_fp = _fair(call_leg.symbol) if call_leg else None
    put_fp = _fair(put_leg.symbol) if put_leg else None

    call_fair = call_fp["fair"] if call_fp else 0.0
    put_fair = put_fp["fair"] if put_fp else 0.0
    combined_fair = call_fair + put_fair

    vs_fair = (
        f"{(combined_premium - combined_fair) / combined_fair * 100:+.1f}% vs fair"
        if combined_fair > 0 else ""
    )

    structure = "straddle" if OFFSET == 0 else f"strangle ±${OFFSET:,}"
    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
    idx = index_price or 0.0

    logger.info(
        f"[ShortStraddleStrangle] Opened {structure}: "
        f"CALL {call_leg.symbol if call_leg else '?'} @ {call_fill:.4f} | "
        f"PUT  {put_leg.symbol if put_leg else '?'} @ {put_fill:.4f} | "
        f"combined={combined_premium:.4f}  SL@={trade.metadata.get('sl_threshold', 0):.4f}  "
        f"BTC=${idx:,.0f}  {phase_label} {duration_s}s"
    )

    try:
        get_notifier().send(
            f"📉 <b>Short {structure.title()} — Trade Opened</b>\n\n"
            f"Time: {ts}  |  BTC: ${idx:,.0f}\n"
            f"ID: {trade.id}\n\n"
            f"SELL {QTY}× {call_leg.symbol if call_leg else '?'}  @ {call_fill:.4f}\n"
            f"SELL {QTY}× {put_leg.symbol if put_leg else '?'}  @ {put_fill:.4f}\n\n"
            f"Combined premium: <b>{combined_premium:.4f}</b>  ({vs_fair})\n"
            f"SL threshold: {trade.metadata.get('sl_threshold', 0):.4f}  "
            f"(+{STOP_LOSS_PCT:.0%})\n"
            f"Max hold: {MAX_HOLD_HOURS}h  |  {phase_label}  {duration_s}s\n\n"
            f"Fair call: {call_fair:.4f}  |  Fair put: {put_fair:.4f}  |  "
            f"Fair combined: {combined_fair:.4f}\n"
            f"Equity: ${account.equity:,.2f}  |  "
            f"Avail: ${account.available_margin:,.2f}"
        )
    except Exception:
        pass


def _on_trade_closed(trade, account):
    # type: (...) -> None
    """Log close details and send Telegram notification."""
    pnl = trade.realized_pnl if trade.realized_pnl is not None else 0.0
    combined_premium = trade.metadata.get("combined_premium", 0.0)
    roi = (pnl / abs(combined_premium) * 100) if combined_premium else 0.0
    hold_seconds = trade.hold_seconds or 0

    if trade.metadata.get("sl_triggered"):
        exit_label = f"Stop Loss ({STOP_LOSS_PCT:.0%})"
    elif hold_seconds >= MAX_HOLD_HOURS * 3600:
        exit_label = f"Max Hold ({MAX_HOLD_HOURS}h)"
    elif trade.metadata.get("expiry_settled"):
        exit_label = "Expiry"
    else:
        exit_label = "closed"

    logger.info(
        f"[ShortStraddleStrangle] Closed: {trade.id}  |  PnL: ${pnl:+.4f}  |  "
        f"ROI: {roi:+.1f}%  |  Hold: {hold_seconds / 60:.1f}min  |  Exit: {exit_label}"
    )

    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
    emoji = "✅" if pnl >= 0 else "❌"
    idx_now = get_btc_index_price(use_cache=False)
    idx = idx_now or 0.0

    # Entry prices
    open_legs = trade.open_legs
    call_open = next((l for l in open_legs if l.symbol.endswith("-C")), None)
    put_open = next((l for l in open_legs if l.symbol.endswith("-P")), None)
    call_fill_open = float(call_open.fill_price) if call_open and call_open.fill_price else 0.0
    put_fill_open = float(put_open.fill_price) if put_open and put_open.fill_price else 0.0

    # Close prices
    close_legs = trade.close_legs or []
    call_close = next((l for l in close_legs if l.symbol.endswith("-C")), None)
    put_close = next((l for l in close_legs if l.symbol.endswith("-P")), None)
    call_fill_close = float(call_close.fill_price) if call_close and call_close.fill_price else 0.0
    put_fill_close = float(put_close.fill_price) if put_close and put_close.fill_price else 0.0
    combined_close = call_fill_close + put_fill_close

    # Fair at close (for SL info)
    call_sym = call_close.symbol if call_close else (call_open.symbol if call_open else None)
    put_sym = put_close.symbol if put_close else (put_open.symbol if put_open else None)
    call_fp = _fair(call_sym) if call_sym else None
    put_fp = _fair(put_sym) if put_sym else None
    combined_fair_now = (
        (call_fp["fair"] if call_fp else 0.0) + (put_fp["fair"] if put_fp else 0.0)
    )

    structure = "straddle" if OFFSET == 0 else f"strangle ±${OFFSET:,}"

    try:
        sl_line = ""
        if trade.metadata.get("sl_triggered"):
            sl_thresh = trade.metadata.get("sl_threshold", 0)
            sl_line = f"SL threshold: {sl_thresh:.4f}  |  Fair now: {combined_fair_now:.4f}\n"

        get_notifier().send(
            f"{emoji} <b>Short {structure.title()} — Trade Closed</b>\n\n"
            f"Time: {ts}  |  BTC: ${idx:,.0f}\n"
            f"ID: {trade.id}  |  Hold: {hold_seconds / 60:.1f} min\n\n"
            f"Trigger: <b>{exit_label}</b>\n"
            f"{sl_line}"
            f"\nOpen:  CALL {call_fill_open:.4f}  PUT {put_fill_open:.4f}  "
            f"→  {combined_premium:.4f} combined\n"
            f"Close: CALL {call_fill_close:.4f}  PUT {put_fill_close:.4f}  "
            f"→  {combined_close:.4f} combined\n\n"
            f"PnL: <b>${pnl:+.2f}</b>  ({roi:+.1f}%)\n"
            f"Equity: ${account.equity:,.2f}"
        )
    except Exception:
        pass


# ─── Leg Template Builder ────────────────────────────────────────────────────

def _build_legs():
    """
    Build the call + put LegSpec list.

    OFFSET=0 → ATM straddle (both legs at closest strike to spot).
    OFFSET>0 → OTM strangle (call at spot+OFFSET, put at spot-OFFSET).
    """
    if OFFSET == 0:
        return straddle(qty=QTY, dte=DTE, side="sell")
    return strangle_by_offset(qty=QTY, offset=OFFSET, dte=DTE, side="sell")


# ─── Open Execution Params ───────────────────────────────────────────────────
#
# Phase 1 (30s): quote both legs at fair price.
# Phase 2 (30s): any unfilled leg repriced to bid — eliminates legging risk.

_OPEN_PARAMS = ExecutionParams(phases=[
    ExecutionPhase(
        pricing="fair", fair_aggression=0.0,
        duration_seconds=LIMIT_OPEN_FAIR_SECONDS,
        reprice_interval=LIMIT_OPEN_FAIR_SECONDS,
    ),
    ExecutionPhase(
        pricing="fair", fair_aggression=1.0,
        duration_seconds=LIMIT_OPEN_AGG_SECONDS,
        reprice_interval=15,
    ),
])


# ─── Strategy Factory ────────────────────────────────────────────────────────

def short_straddle_strangle() -> StrategyConfig:
    """
    Short 1DTE straddle/strangle — sell combined premium, exit on SL, max-hold, or expiry.

    Backtester2 combo #2:  offset=$1000, entry=04:00 UTC, SL=300%, max_hold=20h.
    Score 0.991 — $7,820 total PnL, 14 trades, 86% win rate, Sharpe 22.43.
    """
    return StrategyConfig(
        name="short_straddle_strangle",

        # ── What to trade ─────────────────────────────────────────────
        legs=_build_legs(),

        # ── When to enter ─────────────────────────────────────────────
        entry_conditions=[
            time_window(ENTRY_HOUR, ENTRY_HOUR + 1),
        ],

        # ── When to exit ──────────────────────────────────────────────
        exit_conditions=[
            _combined_sl(),
            max_hold_hours(MAX_HOLD_HOURS),
        ],

        # ── How to execute ────────────────────────────────────────────
        execution_mode="limit",
        execution_params=_OPEN_PARAMS,

        # RFQ fallback for emergency manual closes only
        rfq_params=RFQParams(
            timeout_seconds=15,
            min_improvement_pct=-999,
            fallback_mode="limit",
        ),

        # ── Operational limits ────────────────────────────────────────
        max_concurrent_trades=MAX_CONCURRENT,
        max_trades_per_day=1,
        cooldown_seconds=0,
        check_interval_seconds=CHECK_INTERVAL,

        # ── Callbacks ─────────────────────────────────────────────────
        on_trade_opened=_on_trade_opened,
        on_trade_closed=_on_trade_closed,
    )
