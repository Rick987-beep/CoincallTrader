#!/usr/bin/env python3
"""
daily_put_sell.py — Short OTM put, exit on stop-loss or expiry.

Maps to production's daily_put_sell strategy. Sells a 1DTE OTM put at a
target delta, collects premium, and exits either when the stop-loss
triggers or the option expires.

Grid parameters:
    target_delta  [-0.05, -0.10, -0.15, -0.20]  — OTM put delta to target
    stop_loss_pct [0.5, 0.7, 1.0, 1.5, 2.0]     — SL as fraction of premium

Pricing modes:
    "real"  — sell at bid, buy back at ask (conservative, default)
    "bs"    — Black-Scholes with snapshot IV
"""
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from backtester2.pricing import deribit_fee_per_leg, bs_put, HOURS_PER_YEAR
from backtester2.strategy_base import (
    OpenPosition, Trade, close_trade,
    time_window, weekday_only, stop_loss_pct,
)

EXPIRY_HOUR_UTC = 8


def _parse_expiry_date(expiry_code):
    # type: (str) -> Optional[datetime]
    """Parse Deribit expiry code like '15MAR26' to a datetime."""
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


def _nearest_1dte_expiry(state):
    # type: (Any) -> Optional[str]
    """Find the nearest 1DTE expiry (expires tomorrow)."""
    tomorrow = (state.dt.date().__class__(
        state.dt.year, state.dt.month, state.dt.day
    ))
    # Find expiry that is 1 day out
    from datetime import timedelta
    target_date = tomorrow + timedelta(days=1)

    best = None
    best_diff = None
    for exp in state.expiries():
        exp_date = _parse_expiry_date(exp)
        if exp_date is None:
            continue
        diff = (exp_date.date() - tomorrow).days
        if diff >= 1:  # At least 1 day out
            if best_diff is None or diff < best_diff:
                best = exp
                best_diff = diff
    return best


class DailyPutSell:
    """Sell 1DTE OTM put daily, exit on stop-loss or expiry."""

    name = "daily_put_sell"

    PARAM_GRID = {
        "target_delta": [-0.05, -0.10, -0.15, -0.20],
        "stop_loss_pct": [0.5, 0.7, 1.0, 1.5, 2.0],
        "entry_hour": [3],
    }

    def __init__(self):
        self._position = None  # type: Optional[OpenPosition]
        self._target_delta = -0.10
        self._sl_pct = 1.0
        self._entry_hour = 3
        self._pricing_mode = "real"
        self._entry_conditions = []
        self._exit_conditions = []
        self._trades_today = 0
        self._last_date = None

    def configure(self, params):
        # type: (Dict[str, Any]) -> None
        self._target_delta = params["target_delta"]
        self._sl_pct = params["stop_loss_pct"]
        self._entry_hour = params.get("entry_hour", 3)
        self._pricing_mode = params.get("pricing_mode", "real")
        self._position = None
        self._trades_today = 0
        self._last_date = None

        self._entry_conditions = [
            weekday_only(),
            time_window(self._entry_hour, self._entry_hour + 1),
        ]
        self._exit_conditions = [
            stop_loss_pct(self._sl_pct),
        ]

    def on_market_state(self, state):
        # type: (Any) -> List[Trade]
        trades = []

        # Reset daily counter
        today = state.dt.date()
        if today != self._last_date:
            self._trades_today = 0
            self._last_date = today

        # Check if position expired
        if self._position is not None:
            reason = self._check_expiry(state)
            if reason is None:
                for exit_cond in self._exit_conditions:
                    reason = exit_cond(state, self._position)
                    if reason:
                        break
            if reason:
                trades.append(self._close(state, reason))

        # Check entry if flat + max 1 per day
        if self._position is None and self._trades_today < 1:
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
        self._trades_today = 0
        self._last_date = None

    def describe_params(self):
        # type: () -> Dict[str, Any]
        return {
            "target_delta": self._target_delta,
            "stop_loss_pct": self._sl_pct,
            "entry_hour": self._entry_hour,
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
        exp_dt = exp_date.replace(hour=EXPIRY_HOUR_UTC, tzinfo=state.dt.tzinfo)
        if state.dt >= exp_dt:
            return "expiry"
        return None

    def _try_open(self, state):
        # type: (Any) -> None
        """Find and sell the OTM put nearest to target delta."""
        expiry = _nearest_1dte_expiry(state)
        if expiry is None:
            return

        chain = state.get_chain(expiry)
        if not chain:
            return

        # Filter to puts with valid delta
        puts = [q for q in chain if not q.is_call and q.delta is not None
                and q.delta < 0]
        if not puts:
            return

        # Find put nearest to target delta
        best = min(puts, key=lambda q: abs(q.delta - self._target_delta))

        if self._pricing_mode == "real":
            # Sell at bid (worst fill for seller)
            entry_usd = best.bid_usd
        else:
            # BS mode
            exp_date = _parse_expiry_date(expiry)
            dte_h = (exp_date.replace(hour=EXPIRY_HOUR_UTC) -
                     state.dt.replace(tzinfo=None)).total_seconds() / 3600
            if dte_h <= 0:
                return
            T = dte_h / HOURS_PER_YEAR
            put_iv = best.mark_iv / 100.0
            entry_usd = bs_put(state.spot, best.strike, T, put_iv)

        # Skip if premium too low
        if entry_usd < 1.0:
            return

        fees = deribit_fee_per_leg(state.spot, entry_usd)

        self._position = OpenPosition(
            entry_time=state.dt,
            entry_spot=state.spot,
            legs=[{
                "strike": best.strike,
                "is_call": False,
                "expiry": expiry,
                "side": "sell",
                "entry_price": best.bid,
                "entry_price_usd": entry_usd,
            }],
            entry_price_usd=entry_usd,
            fees_open=fees,
            metadata={
                "target_delta": self._target_delta,
                "actual_delta": best.delta,
                "expiry": expiry,
                "direction": "sell",
                "strike": best.strike,
                "pricing_mode": self._pricing_mode,
            },
        )
        self._trades_today += 1

    def _close(self, state, reason):
        # type: (Any, str) -> Trade
        """Close the short put position."""
        pos = self._position
        leg = pos.legs[0]
        expiry = pos.metadata["expiry"]
        strike = pos.metadata["strike"]

        if reason == "expiry":
            # At expiry: put intrinsic value (what we owe if ITM)
            exit_usd = max(0.0, strike - state.spot)
        elif self._pricing_mode == "real":
            # Buy back at ask (worst fill for buyer)
            quote = state.get_option(expiry, strike, is_call=False)
            exit_usd = quote.ask_usd if quote else 0.0
        else:
            # BS mode
            exp_date = _parse_expiry_date(expiry)
            dte_h = (exp_date.replace(hour=EXPIRY_HOUR_UTC) -
                     state.dt.replace(tzinfo=None)).total_seconds() / 3600
            dte_h = max(dte_h, 0.001)
            T = dte_h / HOURS_PER_YEAR
            quote = state.get_option(expiry, strike, is_call=False)
            put_iv = (quote.mark_iv / 100.0) if quote else 0.5
            exit_usd = bs_put(state.spot, strike, T, put_iv)

        fees_close = deribit_fee_per_leg(state.spot, exit_usd)

        trade = close_trade(state, pos, reason, exit_usd, fees_close)
        trade.metadata["stop_loss_pct"] = self._sl_pct
        self._position = None
        return trade
