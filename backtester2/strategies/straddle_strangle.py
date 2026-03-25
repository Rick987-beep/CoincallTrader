#!/usr/bin/env python3
"""
straddle_strangle.py — Long straddle/strangle + index extrusion exit.

Maps to production's atm_straddle_index_move strategy. Buys an ATM straddle
(offset=0) or OTM strangle (offset>0) and exits when BTC spot moves by a
configurable trigger distance, or after max_hold hours.

Grid parameters:
    offset        [0, 500, 1000, ...]   — distance from ATM for strangle legs
    index_trigger [300, 400, 500, ...]  — BTC move in USD to trigger exit
    max_hold      [1, 2, 3, ..., 12]    — max hours before forced close

Pricing modes:
    "real"  — buy at ask, sell at bid (conservative, default)
    "bs"    — Black-Scholes with snapshot IV (for model comparison)
"""
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from backtester2.pricing import deribit_fee_per_leg, bs_call, bs_put, HOURS_PER_YEAR
from backtester2.strategy_base import (
    OpenPosition, Trade, close_trade,
    time_window, weekday_only, index_move_trigger, max_hold_hours,
)

# Deribit 0DTE expiry hour
EXPIRY_HOUR_UTC = 8


def _parse_expiry_date(expiry_code):
    # type: (str) -> Optional[datetime]
    """Parse Deribit expiry code like '9MAR26' to a datetime date."""
    month_map = {
        "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4,
        "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8,
        "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
    }
    m = re.match(r"(\d{1,2})([A-Z]{3})(\d{2})", expiry_code)
    if not m:
        return None
    day = int(m.group(1))
    month = month_map.get(m.group(2))
    year = 2000 + int(m.group(3))
    if month is None:
        return None
    return datetime(year, month, day)


def _is_0dte(expiry_code, current_dt):
    # type: (str, datetime) -> bool
    """Check if expiry is 0DTE (expires today, after 08:00 UTC)."""
    exp_date = _parse_expiry_date(expiry_code)
    if exp_date is None:
        return False
    return exp_date.date() == current_dt.date()


def _nearest_0dte_expiry(state):
    # type: (Any) -> Optional[str]
    """Find the 0DTE expiry for the current day."""
    for exp in state.expiries():
        if _is_0dte(exp, state.dt):
            return exp
    return None


class ExtrusionStraddleStrangle:
    """Buy 0DTE ATM straddle or OTM strangle, exit on BTC index move."""

    name = "extrusion_straddle_strangle"

    PARAM_GRID = {
        "offset": [0, 500, 1000, 1500, 2000, 2500, 3000],
        "index_trigger": [300, 400, 500, 600, 700, 800, 1000, 1200, 1500, 2000],
        "max_hold": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
    }

    def __init__(self):
        self._position = None  # type: Optional[OpenPosition]
        self._offset = 0
        self._trigger = 500
        self._max_hold = 4
        self._max_entry_hour = 20
        self._pricing_mode = "real"
        self._entry_conditions = []
        self._exit_conditions = []

    def configure(self, params):
        # type: (Dict[str, Any]) -> None
        self._offset = params["offset"]
        self._trigger = params["index_trigger"]
        self._max_hold = params["max_hold"]
        self._max_entry_hour = params.get("max_entry_hour", 20)
        self._pricing_mode = params.get("pricing_mode", "real")
        self._position = None

        self._entry_conditions = [
            weekday_only(),
            time_window(0, self._max_entry_hour + 1),
        ]
        self._exit_conditions = [
            index_move_trigger(self._trigger),
            max_hold_hours(self._max_hold),
        ]

    def on_market_state(self, state):
        # type: (Any) -> List[Trade]
        trades = []

        # Check exits on open position
        if self._position is not None:
            # Check if past expiry (0DTE expired at 08:00 next day)
            reason = self._check_expiry(state)
            if reason is None:
                for exit_cond in self._exit_conditions:
                    reason = exit_cond(state, self._position)
                    if reason:
                        break
            if reason:
                trades.append(self._close(state, reason))

        # Check entry if flat
        if self._position is None:
            if all(cond(state) for cond in self._entry_conditions):
                self._try_open(state)

        return trades

    def on_end(self, state):
        # type: (Any) -> List[Trade]
        if self._position is not None:
            return [self._close(state, "end_of_data")]
        return []

    def reset(self):
        # type: () -> None
        self._position = None

    def describe_params(self):
        # type: () -> Dict[str, Any]
        return {
            "offset": self._offset,
            "index_trigger": self._trigger,
            "max_hold": self._max_hold,
        }

    def _check_expiry(self, state):
        # type: (Any) -> Optional[str]
        """Check if held position's expiry has passed."""
        expiry_code = self._position.metadata.get("expiry")
        if expiry_code is None:
            return None
        exp_date = _parse_expiry_date(expiry_code)
        if exp_date is None:
            return None
        # Expired if we're past the expiry date's 08:00 UTC
        exp_dt = exp_date.replace(hour=EXPIRY_HOUR_UTC, tzinfo=state.dt.tzinfo)
        if state.dt >= exp_dt:
            return "expiry"
        return None

    def _try_open(self, state):
        # type: (Any) -> None
        """Try to open a straddle/strangle position."""
        expiry = _nearest_0dte_expiry(state)
        if expiry is None:
            return

        # Must be before expiry hour on expiry day
        if state.dt.hour >= EXPIRY_HOUR_UTC and _is_0dte(expiry, state.dt):
            return

        if self._offset == 0:
            call, put = state.get_straddle(expiry)
        else:
            call, put = state.get_strangle(expiry, self._offset)

        if call is None or put is None:
            return

        # Skip if bid/ask are zero (illiquid)
        if call.ask <= 0 or put.ask <= 0:
            return

        if self._pricing_mode == "real":
            # Buy at ask (worst fill)
            entry_usd = call.ask_usd + put.ask_usd
        else:
            # BS mode: use IV from snapshot
            exp_date = _parse_expiry_date(expiry)
            dte_h = (exp_date.replace(hour=EXPIRY_HOUR_UTC) -
                     state.dt.replace(tzinfo=None)).total_seconds() / 3600
            if dte_h <= 0:
                return
            T = dte_h / HOURS_PER_YEAR
            call_iv = call.mark_iv / 100.0
            put_iv = put.mark_iv / 100.0
            call_bs = bs_call(state.spot, call.strike, T, call_iv)
            put_bs = bs_put(state.spot, put.strike, T, put_iv)
            entry_usd = call_bs + put_bs

        if entry_usd <= 0 or entry_usd != entry_usd:  # also skip NaN
            return

        fee_call = deribit_fee_per_leg(state.spot, call.ask_usd)
        fee_put = deribit_fee_per_leg(state.spot, put.ask_usd)

        self._position = OpenPosition(
            entry_time=state.dt,
            entry_spot=state.spot,
            legs=[
                {"strike": call.strike, "is_call": True,
                 "expiry": expiry, "side": "buy",
                 "entry_price": call.ask, "entry_price_usd": call.ask_usd},
                {"strike": put.strike, "is_call": False,
                 "expiry": expiry, "side": "buy",
                 "entry_price": put.ask, "entry_price_usd": put.ask_usd},
            ],
            entry_price_usd=entry_usd,
            fees_open=fee_call + fee_put,
            metadata={
                "offset": self._offset,
                "expiry": expiry,
                "direction": "buy",
                "call_strike": call.strike,
                "put_strike": put.strike,
                "pricing_mode": self._pricing_mode,
            },
        )

    def _close(self, state, reason):
        # type: (Any, str) -> Trade
        """Close the position and create a Trade record."""
        pos = self._position
        expiry = pos.metadata["expiry"]
        call_strike = pos.metadata["call_strike"]
        put_strike = pos.metadata["put_strike"]

        if reason == "expiry":
            # At expiry: intrinsic value only
            call_intrinsic = max(0, state.spot - call_strike)
            put_intrinsic = max(0, put_strike - state.spot)
            exit_usd = call_intrinsic + put_intrinsic
        elif self._pricing_mode == "real":
            # Sell at bid (worst fill); NaN bid → 0 (illiquid / no quote)
            call_q = state.get_option(expiry, call_strike, True)
            put_q = state.get_option(expiry, put_strike, False)
            call_bid_usd = call_q.bid_usd if call_q else 0.0
            put_bid_usd = put_q.bid_usd if put_q else 0.0
            if call_bid_usd != call_bid_usd:  # NaN check
                call_bid_usd = 0.0
            if put_bid_usd != put_bid_usd:
                put_bid_usd = 0.0
            exit_usd = call_bid_usd + put_bid_usd
        else:
            # BS mode
            exp_date = _parse_expiry_date(expiry)
            dte_h = (exp_date.replace(hour=EXPIRY_HOUR_UTC) -
                     state.dt.replace(tzinfo=None)).total_seconds() / 3600
            dte_h = max(dte_h, 0.001)
            T = dte_h / HOURS_PER_YEAR
            call_q = state.get_option(expiry, call_strike, True)
            put_q = state.get_option(expiry, put_strike, False)
            call_iv = (call_q.mark_iv / 100.0) if call_q else 0.5
            put_iv = (put_q.mark_iv / 100.0) if put_q else 0.5
            exit_usd = bs_call(state.spot, call_strike, T, call_iv) + \
                        bs_put(state.spot, put_strike, T, put_iv)

        fee_call = deribit_fee_per_leg(state.spot, exit_usd / 2)
        fee_put = deribit_fee_per_leg(state.spot, exit_usd / 2)
        fees_close = fee_call + fee_put

        trade = close_trade(state, pos, reason, exit_usd, fees_close)
        trade.metadata["index_trigger"] = self._trigger
        trade.metadata["max_hold"] = self._max_hold
        self._position = None
        return trade
