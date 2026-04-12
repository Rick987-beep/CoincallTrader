#!/usr/bin/env python3
"""
short_strangle_weekly_tp.py — Daily short strangle targeting a specific week-bucket DTE.

Each calendar day we sell one delta-selected OTM strangle on the expiry that
falls inside the target week window:

    target_weeks=1 → expiry DTE in [7, 13]   (~1 week out)
    target_weeks=2 → expiry DTE in [14, 20]  (~2 weeks out)
    target_weeks=3 → expiry DTE in [21, 27]  (~3 weeks out)

Since we open one position per day and positions may run for many days, many
concurrent open positions are normal.  Each OpenPosition is tracked
independently — identical expiry + strike across different entry days is fine.

Exits (all evaluated independently per position each tick):
    take_profit  — combined ask drops to (1 - take_profit_pct) × entry premium
    stop_loss    — combined ask rises to (1 + stop_loss_pct) × entry premium
    max_hold     — position open for >= max_hold_days calendar days (0 = disabled)
    expiry       — settlement at expiry hour (intrinsic value)
    end_of_data  — force-closed at market at last data tick (on_end)
"""
import re
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Dict, List, Optional

from backtester.pricing import deribit_fee_per_leg, EXPIRY_HOUR_UTC
from backtester.strategy_base import (
    OpenPosition, Trade, close_trade,
    time_window, stop_loss_pct, max_hold_days,
)


# ------------------------------------------------------------------
# Expiry helpers
# ------------------------------------------------------------------

_MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4,
    "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8,
    "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


@lru_cache(maxsize=128)
def _parse_expiry_date(expiry_code):
    # type: (str) -> Optional[datetime]
    m = re.match(r"(\d{1,2})([A-Z]{3})(\d{2})", expiry_code)
    if not m:
        return None
    day = int(m.group(1))
    month = _MONTH_MAP.get(m.group(2))
    year = 2000 + int(m.group(3))
    if month is None:
        return None
    return datetime(year, month, day)


@lru_cache(maxsize=128)
def _expiry_dt_utc(expiry_code, tzinfo):
    # type: (str, Any) -> Optional[datetime]
    exp_date = _parse_expiry_date(expiry_code)
    if exp_date is None:
        return None
    return exp_date.replace(hour=EXPIRY_HOUR_UTC, tzinfo=tzinfo)


def _select_expiry_for_week(state, target_weeks):
    # type: (Any, int) -> Optional[str]
    """Return the expiry whose DTE falls in [target_weeks*7, target_weeks*7+6].

    When multiple expiries qualify, picks the one with the lowest DTE
    (closest to the start of the bucket — most conservative choice).
    Returns None if no qualifying expiry exists in the data.
    """
    lo = target_weeks * 7
    hi = lo + 6
    today = state.dt.date()

    best_expiry = None
    best_dte = None
    for exp in state.expiries():
        exp_date = _parse_expiry_date(exp)
        if exp_date is None:
            continue
        dte = (exp_date.date() - today).days
        if lo <= dte <= hi:
            if best_dte is None or dte < best_dte:
                best_expiry = exp
                best_dte = dte
    return best_expiry


def _select_by_delta(chain, target_delta):
    # type: (list, float) -> Optional[Any]
    candidates = [q for q in chain if q.delta != 0.0]
    if not candidates:
        candidates = chain
    if not candidates:
        return None
    return min(candidates, key=lambda q: abs(q.delta - target_delta))


# ------------------------------------------------------------------
# Strategy
# ------------------------------------------------------------------

class ShortStrangleWeeklyTp:
    """Sell a delta-selected strangle targeting a specific week-bucket DTE.

    One entry per calendar day.  Multiple concurrent open positions are expected
    (up to max_hold_days+2, or target_weeks*7+2 when holding to expiry).
    Each position is exit-managed fully independently.
    """

    name = "short_strangle_weekly_tp"
    DATE_RANGE = ("2026-03-09", "2026-03-23")
    DESCRIPTION = (
        "Sells one delta-selected OTM strangle per day targeting an expiry in the "
        "specified week bucket (target_weeks=1→7-13 DTE, 2→14-20 DTE, 3→21-27 DTE). "
        "Exits per-position on: take-profit (combined ask falls to tp_pct of entry), "
        "stop-loss (combined ask rises to sl_pct above entry), max-hold-days, expiry "
        "settlement, or end-of-data mark-to-market close. "
        "Multiple concurrent positions on identical expiries/strikes are tracked independently."
    )

    PARAM_GRID = {
        "target_weeks":     [1, 2, 3],
        "delta":            [0.10, 0.15, 0.20],
        "entry_hour":       [10, 14, 18, 20],
        "stop_loss_pct":    [2.0, 3.0, 4.0],
        "take_profit_pct":  [0.40, 0.50, 0.60, 0.70],
        "max_hold_days":    [0, 5, 7, 10]   # 0 = hold to expiry / end-of-data
    }

    def __init__(self):
        self._positions = []          # type: List[OpenPosition]
        self._target_weeks = 2
        self._delta = 0.15
        self._sl_pct = 2.0
        self._tp_pct = 0.50
        self._entry_hour = 10
        self._max_hold_days = 0
        self._last_trade_date = None  # type: Optional[Any]
        self._entry_conditions = []
        self._exit_conditions = []

    def configure(self, params):
        # type: (Dict[str, Any]) -> None
        self._target_weeks = params.get("target_weeks", 2)
        self._delta = params["delta"]
        self._sl_pct = params["stop_loss_pct"]
        self._tp_pct = params["take_profit_pct"]
        self._entry_hour = params.get("entry_hour", 10)
        self._max_hold_days = params.get("max_hold_days", 0)
        self._positions = []
        self._last_trade_date = None

        self._entry_conditions = [
            time_window(self._entry_hour, self._entry_hour + 1),
        ]
        self._exit_conditions = [
            stop_loss_pct(self._sl_pct),
        ]
        if self._max_hold_days > 0:
            self._exit_conditions.append(max_hold_days(self._max_hold_days))

    def on_market_state(self, state):
        # type: (Any) -> List[Trade]
        trades = []

        to_close = []
        for pos in list(self._positions):
            reason = self._check_expiry(state, pos)
            if reason is None:
                reason = self._check_take_profit(state, pos)
            if reason is None:
                for exit_cond in self._exit_conditions:
                    reason = exit_cond(state, pos)
                    if reason:
                        break
            # Guard: skip tick if option data missing (data gap), unless expiry settlement
            if reason and reason != "expiry":
                expiry = pos.metadata["expiry"]
                if (state.get_option(expiry, pos.metadata["call_strike"], True) is None
                        or state.get_option(expiry, pos.metadata["put_strike"], False) is None):
                    reason = None
            if reason:
                trades.append(self._close(state, pos, reason))
                to_close.append(pos)
        for pos in to_close:
            self._positions.remove(pos)

        # Open at most one new position per calendar day
        today = state.dt.date()
        if self._last_trade_date != today:
            if all(cond(state) for cond in self._entry_conditions):
                self._try_open(state)

        return trades

    def on_end(self, state):
        # type: (Any) -> List[Trade]
        """Force-close all remaining positions at market (last data tick)."""
        trades = []
        for pos in list(self._positions):
            trades.append(self._close(state, pos, "end_of_data"))
        self._positions.clear()
        return trades

    def reset(self):
        # type: () -> None
        self._positions = []
        self._last_trade_date = None

    def describe_params(self):
        # type: () -> Dict[str, Any]
        return {
            "target_weeks":     self._target_weeks,
            "delta":            self._delta,
            "stop_loss_pct":    self._sl_pct,
            "take_profit_pct":  self._tp_pct,
            "entry_hour":       self._entry_hour,
            "max_hold_days":    self._max_hold_days,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _check_expiry(self, state, pos):
        # type: (Any, OpenPosition) -> Optional[str]
        exp_dt = pos.metadata.get("expiry_dt")
        if exp_dt is None:
            return None
        if state.dt >= exp_dt:
            return "expiry"
        return None

    def _check_take_profit(self, state, pos):
        # type: (Any, OpenPosition) -> Optional[str]
        """Close when combined ask drops to (1 - tp_pct) × entry premium.

        Uses raw ask prices — no mark/fair floor.
        Returns None if ask data is missing for either leg (skip tick).
        """
        if self._tp_pct <= 0:
            return None
        expiry = pos.metadata["expiry"]
        call_q = state.get_option(expiry, pos.metadata["call_strike"], True)
        put_q  = state.get_option(expiry, pos.metadata["put_strike"], False)
        if call_q is None or put_q is None:
            return None
        if call_q.ask <= 0 or put_q.ask <= 0:
            return None
        current_usd = call_q.ask_usd + put_q.ask_usd
        profit_ratio = (pos.entry_price_usd - current_usd) / max(pos.entry_price_usd, 0.01)
        if profit_ratio >= self._tp_pct:
            return "take_profit"
        return None

    def _try_open(self, state):
        # type: (Any) -> None
        expiry = _select_expiry_for_week(state, self._target_weeks)
        if expiry is None:
            return

        chain = state.get_chain(expiry)
        if not chain:
            return

        calls = [q for q in chain if q.is_call]
        puts  = [q for q in chain if not q.is_call]

        call = _select_by_delta(calls, +self._delta)
        put  = _select_by_delta(puts,  -self._delta)

        if call is None or put is None:
            return

        if call.bid <= 0 or put.bid <= 0:
            return

        call_entry_usd = call.bid_usd
        put_entry_usd  = put.bid_usd
        entry_usd = call_entry_usd + put_entry_usd
        if entry_usd <= 0:
            return

        fee_call = deribit_fee_per_leg(state.spot, call_entry_usd)
        fee_put  = deribit_fee_per_leg(state.spot, put_entry_usd)
        exp_dt   = _expiry_dt_utc(expiry, state.dt.tzinfo)

        today = state.dt.date()
        exp_date = _parse_expiry_date(expiry)
        dte = (exp_date.date() - today).days if exp_date else None

        pos = OpenPosition(
            entry_time=state.dt,
            entry_spot=state.spot,
            legs=[
                {
                    "strike": call.strike, "is_call": True,
                    "expiry": expiry, "side": "sell",
                    "entry_price": call.bid, "entry_price_usd": call_entry_usd,
                    "entry_delta": call.delta,
                },
                {
                    "strike": put.strike, "is_call": False,
                    "expiry": expiry, "side": "sell",
                    "entry_price": put.bid, "entry_price_usd": put_entry_usd,
                    "entry_delta": put.delta,
                },
            ],
            entry_price_usd=entry_usd,
            fees_open=fee_call + fee_put,
            metadata={
                "target_delta":  self._delta,
                "expiry":        expiry,
                "expiry_dt":     exp_dt,
                "direction":     "sell",
                "call_strike":   call.strike,
                "put_strike":    put.strike,
                "call_delta":    call.delta,
                "put_delta":     put.delta,
                "dte_at_entry":  dte,
            },
        )
        self._positions.append(pos)
        self._last_trade_date = today

    def _close(self, state, pos, reason):
        # type: (Any, OpenPosition, str) -> Trade
        expiry      = pos.metadata["expiry"]
        call_strike = pos.metadata["call_strike"]
        put_strike  = pos.metadata["put_strike"]

        if reason == "expiry":
            # Intrinsic value at settlement
            call_exit_usd = max(0.0, state.spot - call_strike)
            put_exit_usd  = max(0.0, put_strike  - state.spot)
        else:
            # Buy back at ask; fall back to entry price on missing data
            call_q = state.get_option(expiry, call_strike, True)
            put_q  = state.get_option(expiry, put_strike,  False)
            call_exit_usd = (call_q.ask_usd if call_q and call_q.ask > 0
                             else pos.legs[0]["entry_price_usd"])
            put_exit_usd  = (put_q.ask_usd if put_q and put_q.ask > 0
                             else pos.legs[1]["entry_price_usd"])

        exit_usd   = call_exit_usd + put_exit_usd
        fees_close = 0.0 if reason == "expiry" else (
            deribit_fee_per_leg(state.spot, call_exit_usd) +
            deribit_fee_per_leg(state.spot, put_exit_usd)
        )

        trade = close_trade(state, pos, reason, exit_usd, fees_close)
        trade.metadata["target_weeks"]    = self._target_weeks
        trade.metadata["stop_loss_pct"]   = self._sl_pct
        trade.metadata["take_profit_pct"] = self._tp_pct
        trade.metadata["max_hold_days"]   = self._max_hold_days
        trade.metadata["dte_at_entry"]    = pos.metadata.get("dte_at_entry")
        return trade
