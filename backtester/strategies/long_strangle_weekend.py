#!/usr/bin/env python3
"""
long_strangle_weekend.py — Long ATM strangle (or single leg) opened on Sundays.

Idea: capture the volatility expansion that typically occurs during the
Sunday → Monday transition.  Entry is time-gated to Sundays only; the
position is long premium (buy call + buy put / single leg) so profits
when realised vol exceeds the implied vol priced into the premium paid.

Leg structure (``leg_type`` param):
    "strangle" — buy one ATM call + one ATM put
    "call"     — buy only the ATM call
    "put"      — buy only the ATM put

Legs are selected by target delta (0.40–0.50 for near-ATM).

Exit logic:
    - profit_target_pct : close when value rises to (1 + tp_pct) × entry cost
    - stop_loss_pct     : close when value drops to (1 - sl_pct) × entry cost
    - max_hold_hours    : force-close after N hours
    - expiry settlement : intrinsic value at expiry
"""
from typing import Any, Dict, List, Optional

from backtester.bt_option_selection import select_by_delta
from backtester.expiry_utils import expiry_dt_utc, select_expiry
from backtester.pricing import deribit_fee_per_leg, EXPIRY_HOUR_UTC
from backtester.strategy_base import (
    OpenPosition, Trade, close_trade,
    check_expiry, profit_target_pct, stop_loss_pct, max_hold_hours,
    time_window,
)


# ------------------------------------------------------------------
# Strategy
# ------------------------------------------------------------------

class LongStrangleWeekend:
    """Buy 1 or 2 near-ATM legs on Sundays; exit on TP, SL, max-hold, or expiry."""

    name = "long_strangle_weekend"
    DATE_RANGE = ("2025-11-21", "2026-04-21")
    DESCRIPTION = (
        "Buys a strangle, naked call, or naked put on a Sunday, targeting volatility "
        "expansion into Monday. Legs are near-ATM (delta ~0.40–0.50). "
        "leg_type controls the structure: 'strangle' = call+put, 'call' = call only, "
        "'put' = put only. "
        "Exits on profit-target, stop-loss, optional max hold duration, or expiry settlement."
    )

    PARAM_GRID = {
        "leg_type":          ["strangle", "call", "put"],
        "dte":               [0,1],
        "delta":             [0.05, 0.1, 0.15, 0.2],
        "entry_weekday":     [0],  # 0=Monday … 6=Sunday
        "entry_hour":        [1,2,3,6],
        "stop_loss_pct":     [0],
        "profit_target_pct": [0, 0.25, 0.50, 1, 1.5],
        "max_hold_hours":    [0],
    }

    def __init__(self):
        self._positions = []          # type: List[OpenPosition]
        self._leg_type = "strangle"
        self._dte = 1
        self._delta = 0.45
        self._sl_pct = 0.50
        self._tp_pct = 1.00
        self._entry_hour = 20
        self._entry_weekday = 6  # Sunday
        self._max_hold_hours = 0
        self._last_trade_date = None  # type: Optional[Any]
        self._entry_conditions = []
        self._exit_conditions = []

    def configure(self, params):
        # type: (Dict[str, Any]) -> None
        self._leg_type = params.get("leg_type", "strangle")
        self._dte = params.get("dte", 1)
        self._delta = params["delta"]
        self._sl_pct = params["stop_loss_pct"]
        self._tp_pct = params["profit_target_pct"]
        self._entry_hour = params.get("entry_hour", 20)
        self._entry_weekday = params.get("entry_weekday", 6)
        self._max_hold_hours = params.get("max_hold_hours", 0)
        self._positions = []
        self._last_trade_date = None

        self._entry_conditions = [
            time_window(self._entry_hour, self._entry_hour + 1),
        ]
        self._exit_conditions = []
        if self._sl_pct > 0:
            self._exit_conditions.append(stop_loss_pct(self._sl_pct))
        if self._tp_pct > 0:
            self._exit_conditions.append(profit_target_pct(self._tp_pct))
        if self._max_hold_hours > 0:
            self._exit_conditions.append(max_hold_hours(self._max_hold_hours))

    def on_market_state(self, state):
        # type: (Any) -> List[Trade]
        trades = []

        # --- check exits first ---
        to_close = []
        for pos in list(self._positions):
            reason = check_expiry(state, pos)
            if reason is None:
                for exit_cond in self._exit_conditions:
                    reason = exit_cond(state, pos)
                    if reason:
                        break
            if reason and reason != "expiry":
                # Data gap guard — skip close if quotes are missing
                expiry = pos.metadata["expiry"]
                leg_type = pos.metadata["leg_type"]
                if leg_type == "strangle":
                    if (state.get_option(expiry, pos.metadata["call_strike"], True) is None
                            or state.get_option(expiry, pos.metadata["put_strike"], False) is None):
                        reason = None
                else:
                    is_call = (leg_type == "call")
                    strike = pos.metadata["call_strike"] if is_call else pos.metadata["put_strike"]
                    if state.get_option(expiry, strike, is_call) is None:
                        reason = None
            if reason:
                trades.append(self._close(state, pos, reason))
                to_close.append(pos)
        for pos in to_close:
            self._positions.remove(pos)

        # --- check entry: target weekday only, one trade per day ---
        if len(self._positions) == 0:
            today = state.dt.date()
            if self._last_trade_date != today:
                if state.dt.weekday() == self._entry_weekday:
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
            "leg_type":          self._leg_type,
            "dte":               self._dte,
            "delta":             self._delta,
            "stop_loss_pct":     self._sl_pct,
            "profit_target_pct": self._tp_pct,
            "entry_hour":        self._entry_hour,
            "entry_weekday":     self._entry_weekday,
            "max_hold_hours":    self._max_hold_hours,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

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
        exp_dt = expiry_dt_utc(expiry, state.dt.tzinfo)

        if self._leg_type == "strangle":
            self._open_strangle(state, expiry, exp_dt, calls, puts)
        elif self._leg_type == "call":
            self._open_single(state, expiry, exp_dt, calls, is_call=True)
        else:  # put
            self._open_single(state, expiry, exp_dt, puts, is_call=False)

    def _open_strangle(self, state, expiry, exp_dt, calls, puts):
        # type: (Any, str, Any, list, list) -> None
        call = select_by_delta(calls, +self._delta)
        put  = select_by_delta(puts,  -self._delta)
        if call is None or put is None:
            return
        # Long entry: pay the ask
        if call.ask <= 0 or put.ask <= 0:
            return
        call_usd  = call.ask_usd
        put_usd   = put.ask_usd
        entry_usd = call_usd + put_usd
        if entry_usd <= 0:
            return
        pos = OpenPosition(
            entry_time=state.dt,
            entry_spot=state.spot,
            legs=[
                {"strike": call.strike, "is_call": True,  "expiry": expiry, "side": "buy",
                 "entry_price": call.ask, "entry_price_usd": call_usd, "entry_delta": call.delta},
                {"strike": put.strike,  "is_call": False, "expiry": expiry, "side": "buy",
                 "entry_price": put.ask, "entry_price_usd": put_usd,  "entry_delta": put.delta},
            ],
            entry_price_usd=entry_usd,
            fees_open=deribit_fee_per_leg(state.spot, call_usd) + deribit_fee_per_leg(state.spot, put_usd),
            metadata={
                "leg_type":     "strangle",
                "target_delta": self._delta,
                "expiry":       expiry,
                "expiry_dt":    exp_dt,
                "direction":    "buy",
                "call_strike":  call.strike,
                "put_strike":   put.strike,
                "call_delta":   call.delta,
                "put_delta":    put.delta,
            },
        )
        self._positions.append(pos)
        self._last_trade_date = state.dt.date()

    def _open_single(self, state, expiry, exp_dt, quotes, is_call):
        # type: (Any, str, Any, list, bool) -> None
        target_delta = +self._delta if is_call else -self._delta
        leg = select_by_delta(quotes, target_delta)
        if leg is None:
            return
        if leg.ask <= 0:
            return
        entry_usd = leg.ask_usd
        if entry_usd <= 0:
            return
        leg_type   = "call" if is_call else "put"
        strike_key = "call_strike" if is_call else "put_strike"
        delta_key  = "call_delta"  if is_call else "put_delta"
        pos = OpenPosition(
            entry_time=state.dt,
            entry_spot=state.spot,
            legs=[
                {"strike": leg.strike, "is_call": is_call, "expiry": expiry, "side": "buy",
                 "entry_price": leg.ask, "entry_price_usd": entry_usd, "entry_delta": leg.delta},
            ],
            entry_price_usd=entry_usd,
            fees_open=deribit_fee_per_leg(state.spot, entry_usd),
            metadata={
                "leg_type":     leg_type,
                "target_delta": self._delta,
                "expiry":       expiry,
                "expiry_dt":    exp_dt,
                "direction":    "buy",
                strike_key:     leg.strike,
                delta_key:      leg.delta,
            },
        )
        self._positions.append(pos)
        self._last_trade_date = state.dt.date()

    def _close(self, state, pos, reason):
        # type: (Any, OpenPosition, str) -> Trade
        leg_type = pos.metadata["leg_type"]
        if leg_type == "strangle":
            trade = self._close_strangle(state, pos, reason)
        else:
            trade = self._close_single_leg(state, pos, reason)
        trade.metadata["leg_type"]          = leg_type
        trade.metadata["dte"]               = self._dte
        trade.metadata["stop_loss_pct"]     = self._sl_pct
        trade.metadata["profit_target_pct"] = self._tp_pct
        trade.metadata["max_hold_hours"]    = self._max_hold_hours
        return trade

    def _close_strangle(self, state, pos, reason):
        # type: (Any, OpenPosition, str) -> Trade
        expiry      = pos.metadata["expiry"]
        call_strike = pos.metadata["call_strike"]
        put_strike  = pos.metadata["put_strike"]

        if reason == "expiry":
            call_exit_usd = max(0.0, state.spot - call_strike)
            put_exit_usd  = max(0.0, put_strike - state.spot)
            fees_close    = 0.0
        else:
            _min_tick_usd = 0.0001 * state.spot
            call_q = state.get_option(expiry, call_strike, True)
            put_q  = state.get_option(expiry, put_strike,  False)
            call_exit_usd = call_q.bid_usd if (call_q and call_q.bid > 0) else _min_tick_usd
            put_exit_usd  = put_q.bid_usd  if (put_q  and put_q.bid  > 0) else _min_tick_usd
            fees_close = (
                deribit_fee_per_leg(state.spot, call_exit_usd)
                + deribit_fee_per_leg(state.spot, put_exit_usd)
            )

        exit_usd = call_exit_usd + put_exit_usd
        return close_trade(state, pos, reason, exit_usd, fees_close)

    def _close_single_leg(self, state, pos, reason):
        # type: (Any, OpenPosition, str) -> Trade
        leg_type = pos.metadata["leg_type"]
        is_call  = (leg_type == "call")
        expiry   = pos.metadata["expiry"]
        strike   = pos.metadata["call_strike"] if is_call else pos.metadata["put_strike"]

        if reason == "expiry":
            exit_usd   = max(0.0, state.spot - strike) if is_call else max(0.0, strike - state.spot)
            fees_close = 0.0
        else:
            _min_tick_usd = 0.0001 * state.spot
            q = state.get_option(expiry, strike, is_call)
            exit_usd   = q.bid_usd if (q and q.bid > 0) else _min_tick_usd
            fees_close = deribit_fee_per_leg(state.spot, exit_usd)

        return close_trade(state, pos, reason, exit_usd, fees_close)
