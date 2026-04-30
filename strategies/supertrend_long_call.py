"""
SuperTrend Long Call — Coincall live strategy.

Signal-driven long call buyer on BTC. Mirrors the rules in
docs/Backtest_Report_SuperTrend_DTE30_Delta05_EN.md.

Entry rule:
    SuperTrend(7, 3) on 1h BTCUSDT (Binance public klines) flips from -1 to +1.
    Triggered ONLY on the flip — never on a steady-state +1 trend (clean
    restart behaviour: missed flips during downtime are not back-traded).

Option selection (single leg, BUY CALL):
    - Target DTE ≈ 30 (search window 7–60d, nearest to 30)
    - Target delta ≈ 0.50
    - Quantity = QTY (default 1.0, ~1 BTC notional)

Exit rule:
    SuperTrend trend is -1 on the latest fully closed 1h bar.
    Uses the steady-state trend value (not just the flip) so that a position
    recovered from persistence after a restart will still close on the next
    tick if the trend has already turned down — close signals are never missed.

Execution:
    execution_mode = "limit"
    execution_profile = "aggressive_2phase"
        Buy at ask on entry, sell at bid on exit (matches backtester fill model).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from indicators.data import fetch_klines
from indicators.supertrend import (
    DEFAULT_MULTIPLIER,
    DEFAULT_PERIOD,
    latest_signal,
)
from option_selection import LegSpec
from strategy import StrategyConfig
from telegram_notifier import get_notifier

logger = logging.getLogger(__name__)


# ─── Strategy Parameters ────────────────────────────────────────────────────
# Overridable via PARAM_* env vars (set by slot .toml config at deploy time).

def _p(name, default, cast=float):
    return cast(os.getenv(f"PARAM_{name}", str(default)))


# Structure
QTY           = _p("QTY",           1.0)        # contracts per leg (~1 BTC notional)
TARGET_DELTA  = _p("TARGET_DELTA",  0.50)       # ~ATM call
TARGET_DTE    = _p("TARGET_DTE",    30, int)    # nearest expiry to this many days
DTE_MIN       = _p("DTE_MIN",       7,  int)    # acceptable expiry window — min
DTE_MAX       = _p("DTE_MAX",       60, int)    # acceptable expiry window — max

# Indicator
ST_PERIOD     = _p("ST_PERIOD",     DEFAULT_PERIOD,     int)
ST_MULTIPLIER = _p("ST_MULTIPLIER", DEFAULT_MULTIPLIER, float)

# Indicator data (Binance public klines)
KLINE_SYMBOL   = os.getenv("PARAM_KLINE_SYMBOL", "BTCUSDT")
KLINE_INTERVAL = os.getenv("PARAM_KLINE_INTERVAL", "1h")
# Bars to load. SuperTrend(7,3) only needs ~10; 500 leaves slack and matches
# the indicator-data cache layer's typical batch size.
KLINE_LOOKBACK = _p("KLINE_LOOKBACK", 500, int)

# Operational
# 15s aligns with PositionMonitor's poll cadence and ensures a SuperTrend
# flip on a freshly-closed 1h bar is detected within ~15s of bar close.
CHECK_INTERVAL = _p("CHECK_INTERVAL", 15, int)


# ─── Indicator Snapshot Helper ──────────────────────────────────────────────

def _latest_st_signal() -> Optional[dict]:
    """
    Fetch fresh 1h klines from Binance and evaluate SuperTrend on the last
    fully closed bar.

    Returns the dict from latest_signal() — or None on any failure (network,
    insufficient bars, etc.) so callers can fail-safe.
    """
    try:
        # force_refresh=True: bypass the shared kline cache (default 30-min
        # TTL on 1h bars). For this strategy a flip on a freshly-closed bar
        # is critical and stale cache would delay detection by up to 30 min.
        # Cost is trivial: one Binance public-klines request per tick (~4/min).
        df = fetch_klines(
            symbol=KLINE_SYMBOL,
            interval=KLINE_INTERVAL,
            lookback_bars=KLINE_LOOKBACK,
            force_refresh=True,
        )
    except Exception:
        logger.exception("[SuperTrend] fetch_klines failed")
        return None

    if df is None or df.empty:
        logger.warning("[SuperTrend] No kline data — fail-safe (no signal)")
        return None

    # Drop the last bar if it is still in progress. Binance returns the
    # currently-forming bar at the end; we only want closed bars.
    now_utc = pd.Timestamp.now(tz="UTC")
    last_idx = df.index[-1]
    # Treat a 1h bar as "closed" once we are past its open + 1h.
    if (now_utc - last_idx) < pd.Timedelta("1h"):
        df = df.iloc[:-1]

    sig = latest_signal(df, period=ST_PERIOD, multiplier=ST_MULTIPLIER, strict_first_cycle=True)
    if sig is None:
        logger.debug("[SuperTrend] insufficient bars for signal")
    return sig


# ─── Entry Condition: SuperTrend flip up ────────────────────────────────────
# Module-level dedupe so that, even if the strategy runner ticks several
# times within the same closed-bar window, we only attempt one entry per
# flip-up bar.

_last_entry_bar: Optional[pd.Timestamp] = None


def _supertrend_flip_up_entry():
    """Entry condition: latest closed 1h bar has flip_up == True."""
    label = f"supertrend_flip_up({ST_PERIOD},{ST_MULTIPLIER})"

    def _check(account) -> bool:
        global _last_entry_bar
        sig = _latest_st_signal()
        if sig is None:
            return False
        if not sig["flip_up"]:
            return False
        if _last_entry_bar is not None and sig["bar_ts"] == _last_entry_bar:
            # Already attempted entry on this bar.
            return False
        _last_entry_bar = sig["bar_ts"]
        logger.info(
            "[SuperTrend] flip-up detected on bar %s — entering long call",
            sig["bar_ts"],
        )
        return True

    _check.__name__ = label
    return _check


# ─── Exit Condition: SuperTrend trend == -1 ─────────────────────────────────

def _supertrend_trend_down_exit():
    """
    Exit condition: latest closed 1h bar has trend == -1.

    Uses the persistent trend (not just the flip-down event) so a position
    recovered from snapshot after a restart still closes on the next tick if
    the trend has already turned down — never misses a close signal.
    """
    label = f"supertrend_down({ST_PERIOD},{ST_MULTIPLIER})"

    def _check(account, trade) -> bool:
        sig = _latest_st_signal()
        if sig is None:
            return False
        triggered = sig["trend"] == -1
        if triggered:
            logger.info(
                "[SuperTrend] trend down on bar %s (flip_down=%s) — closing %s",
                sig["bar_ts"], sig["flip_down"], trade.id,
            )
        return triggered

    _check.__name__ = label
    return _check


# ─── Telegram callbacks ─────────────────────────────────────────────────────

def _on_trade_opened(trade, account) -> None:
    leg = trade.open_legs[0] if trade.open_legs else None
    fp = leg.fill_price if leg else None
    qty = leg.filled_qty if leg and leg.filled_qty > 0 else (leg.qty if leg else 0.0)
    sym = leg.symbol if leg else "?"
    cost_str = f"${float(fp) * qty:,.2f}" if fp else "(unfilled)"
    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
    logger.info(
        "[SuperTrend] Trade opened — BUY %.4g× %s @ %s  |  equity=$%,.2f",
        qty, sym, (f"${float(fp):,.4f}" if fp else "unfilled"), account.equity,
    )
    try:
        get_notifier().send(
            f"📈 <b>SuperTrend Long Call — Trade Opened</b>\n"
            f"Time: {ts}\n"
            f"ID: {trade.id}\n"
            f"BUY {qty}× {sym}\n"
            f"Premium: {cost_str}\n"
            f"Equity: ${account.equity:,.2f}  |  "
            f"Avail: ${account.available_margin:,.2f}"
        )
    except Exception:
        pass


def _on_trade_closed(trade, account) -> None:
    pnl = trade.realized_pnl if trade.realized_pnl is not None else 0.0
    fees = float(trade.open_fees or 0.0) + float(trade.close_fees or 0.0)
    net = pnl - fees if (trade.open_fees or trade.close_fees) else pnl
    hold_min = (trade.hold_seconds or 0) / 60.0
    leg = trade.open_legs[0] if trade.open_legs else None
    sym = leg.symbol if leg else "?"
    emoji = "✅" if net >= 0 else "❌"
    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
    logger.info(
        "[SuperTrend] Trade closed — %s  |  net PnL: $%+.4f  |  fees: $%.4f  |  hold: %.1fmin  |  equity=$%,.2f",
        sym, net, fees, hold_min, account.equity,
    )
    try:
        get_notifier().send(
            f"{emoji} <b>SuperTrend Long Call — Trade Closed</b>\n"
            f"Time: {ts}  |  Hold: {hold_min:.1f} min\n"
            f"ID: {trade.id}  |  {sym}\n"
            f"Gross PnL: {pnl:+,.2f}\n"
            f"Fees:      {fees:,.2f}\n"
            f"<b>Net PnL:   {net:+,.2f}</b>\n"
            f"Equity: ${account.equity:,.2f}"
        )
    except Exception:
        pass


# ─── Leg Template ───────────────────────────────────────────────────────────

def _build_legs():
    return [
        LegSpec(
            option_type="C",
            side="buy",
            qty=QTY,
            strike_criteria={"type": "delta", "value": TARGET_DELTA},
            expiry_criteria={
                "dte":     TARGET_DTE,
                "dte_min": DTE_MIN,
                "dte_max": DTE_MAX,
            },
        ),
    ]


# ─── Strategy Factory ───────────────────────────────────────────────────────

def supertrend_long_call() -> StrategyConfig:
    """
    SuperTrend(7, 3) long-call strategy on Coincall BTC options.

    Buy a ~30 DTE / ~0.50 delta call when SuperTrend flips up; close it
    when SuperTrend turns down. Single leg, fixed quantity (default 1.0 BTC).
    """
    logger.info(
        "[SuperTrend] Strategy starting — period=%s, mult=%s, target_dte=%s, "
        "delta=%s, qty=%s, symbol=%s/%s, check_interval=%ss",
        ST_PERIOD, ST_MULTIPLIER, TARGET_DTE,
        TARGET_DELTA, QTY, KLINE_SYMBOL, KLINE_INTERVAL, CHECK_INTERVAL,
    )
    return StrategyConfig(
        name="supertrend_long_call",

        legs=_build_legs(),

        entry_conditions=[
            _supertrend_flip_up_entry(),
        ],
        exit_conditions=[
            _supertrend_trend_down_exit(),
        ],

        execution_mode="limit",
        execution_profile="supertrend_long_call",

        max_concurrent_trades=1,
        max_trades_per_day=0,           # unlimited — flips are inherently rare
        cooldown_seconds=0,
        check_interval_seconds=CHECK_INTERVAL,

        on_trade_opened=_on_trade_opened,
        on_trade_closed=_on_trade_closed,

        metadata={
            "indicator": "supertrend",
            "period":    ST_PERIOD,
            "multiplier": ST_MULTIPLIER,
            "target_dte": TARGET_DTE,
            "target_delta": TARGET_DELTA,
        },
    )
