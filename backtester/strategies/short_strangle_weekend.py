#!/usr/bin/env python3
"""short_strangle_weekend.py — Short N-DTE strangle (delta-selected) sold on chosen days.

Based on short_strangle_delta_tp.py, with one key difference:

    open_days — comma-separated weekday names controlling which days
    entries are allowed, e.g. "sunday", "saturday,sunday", "sunday,monday".

All other behaviour (delta selection, entry window, SL, TP, max-hold,
expiry settlement) is identical to ShortStrangleDeltaTp.
"""
from typing import Any, Dict, List, Optional

from backtester.bt_option_selection import select_by_delta, apply_min_otm
from backtester.expiry_utils import (
    parse_expiry_date, expiry_dt_utc, select_expiry,
    parse_open_days, open_days_label,
)
from backtester.pricing import deribit_fee_per_leg, EXPIRY_HOUR_UTC
from backtester.strategy_base import (
    OpenPosition, Trade, close_trade,
    check_expiry, check_take_profit_strangle, close_short_strangle,
    time_window, stop_loss_pct, max_hold_hours,
)


# ------------------------------------------------------------------
# Strategy
# ------------------------------------------------------------------


class ShortStrangleWeekend:
    """Sell N-DTE OTM strangle on chosen days; exit on TP, SL, time exit, or expiry."""

    name = "short_strangle_weekend"
    DATE_RANGE = ("2025-11-20", "2026-04-21")
    DESCRIPTION = (
        "Sells a strangle on a Deribit expiry N calendar days ahead (dte=1/2), "
        "with legs chosen by target delta, but entries are restricted to specific days. "
        "open_days is a comma-separated list of weekday names (e.g. 'sunday,monday'). "
        "Take-profit closes when combined ask drops to (1-tp_pct) × entry premium. "
        "TP uses raw ask prices. SL uses mark/fair prices. "
        "One entry per day; up to dte+1 positions open concurrently."
    )

    PARAM_GRID = {
        "dte":              [1,2],
        "delta":            [0.08, 0.12, 0.15],
        "entry_hour":       [18,19,20,21,22,23],
        "stop_loss_pct":    [0, 2.5, 5.0],
        "take_profit_pct":  [0, 0.5, 0.9],
        "max_hold_hours":   [0],
        "open_days":        ["friday"],
        "min_otm_pct":      [1],
    }

    def __init__(self):
        self._positions = []          # type: List[OpenPosition]
        self._dte = 1
        self._max_concurrent = 1
        self._delta = 0.25
        self._sl_pct = 5.0
        self._tp_pct = 0.65
        self._entry_hour = 16
        self._max_hold_hours = 0
        self._open_days = frozenset([5, 6])   # saturday,sunday by default
        self._min_otm_pct = 0
        self._last_trade_date = None  # type: Optional[Any]
        self._entry_conditions = []
        self._exit_conditions = []

    def configure(self, params):
        # type: (Dict[str, Any]) -> None
        self._dte = params.get("dte", 1)
        self._delta = params["delta"]
        self._sl_pct = params["stop_loss_pct"]
        self._tp_pct = params["take_profit_pct"]
        self._entry_hour = params.get("entry_hour", 16)
        self._max_hold_hours = params.get("max_hold_hours", 0)
        self._min_otm_pct = params.get("min_otm_pct", 0)
        self._max_concurrent = self._dte + 1
        self._positions = []
        self._last_trade_date = None

        open_days_str = params.get("open_days", "saturday,sunday")
        self._open_days = parse_open_days(open_days_str)

        self._entry_conditions = [
            time_window(self._entry_hour, self._entry_hour + 1),
        ]
        self._exit_conditions = []
        if self._sl_pct > 0:
            self._exit_conditions.append(stop_loss_pct(self._sl_pct))
        if self._max_hold_hours > 0:
            self._exit_conditions.append(max_hold_hours(self._max_hold_hours))

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
            if reason and reason != "expiry":
                expiry = pos.metadata["expiry"]
                if (state.get_option(expiry, pos.metadata["call_strike"], True) is None
                        or state.get_option(expiry, pos.metadata["put_strike"], False) is None):
                    reason = None  # data gap — retry next tick
            if reason:
                trades.append(self._close(state, pos, reason))
                to_close.append(pos)
        for pos in to_close:
            self._positions.remove(pos)

        if len(self._positions) < self._max_concurrent:
            today = state.dt.date()
            if self._last_trade_date != today:
                if state.dt.weekday() in self._open_days:
                    if all(cond(state) for cond in self._entry_conditions):
                        self._try_open(state)

        return trades

    def on_end(self, state):
        # type: (Any) -> List[Trade]
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
            "dte":              self._dte,
            "delta":            self._delta,
            "stop_loss_pct":    self._sl_pct,
            "take_profit_pct":  self._tp_pct,
            "entry_hour":       self._entry_hour,
            "max_hold_hours":   self._max_hold_hours,
            "open_days":        open_days_label(self._open_days),
            "min_otm_pct":      self._min_otm_pct,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _check_expiry(self, state, pos):
        # type: (Any, OpenPosition) -> Optional[str]
        return check_expiry(state, pos)

    def _check_take_profit(self, state, pos):
        # type: (Any, OpenPosition) -> Optional[str]
        return check_take_profit_strangle(state, pos, self._tp_pct)

    def _try_open(self, state):
        # type: (Any) -> None
        expiry = select_expiry(state, self._dte)
        if expiry is None:
            return

        chain = state.get_chain(expiry)
        if not chain:
            return

        calls = [q for q in chain if q.is_call]
        puts  = [q for q in chain if not q.is_call]

        call = select_by_delta(calls, +self._delta)
        put  = select_by_delta(puts,  -self._delta)

        if call is None or put is None:
            return

        if self._min_otm_pct > 0:
            call = apply_min_otm(calls, call, state.spot, self._min_otm_pct, is_call=True)
            put  = apply_min_otm(puts,  put,  state.spot, self._min_otm_pct, is_call=False)
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
        exp_dt   = expiry_dt_utc(expiry, state.dt.tzinfo)

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
                "target_delta":    self._delta,
                "expiry":          expiry,
                "expiry_dt":       exp_dt,
                "direction":       "sell",
                "call_strike":     call.strike,
                "put_strike":      put.strike,
                "call_delta":      call.delta,
                "put_delta":       put.delta,
            },
        )
        self._positions.append(pos)
        self._last_trade_date = state.dt.date()

    def _close(self, state, pos, reason):
        # type: (Any, OpenPosition, str) -> Trade
        trade = close_short_strangle(state, pos, reason)
        trade.metadata["dte"]              = self._dte
        trade.metadata["stop_loss_pct"]    = self._sl_pct
        trade.metadata["take_profit_pct"]  = self._tp_pct
        trade.metadata["max_hold_hours"]   = self._max_hold_hours
        return trade
