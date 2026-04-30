#!/usr/bin/env python3
"""
short_generic_sl_usd.py — Short option strategy (delta-selected) with USD stop-loss + time/expiry exit.

Like short_generic but the stop-loss is a fixed dollar amount rather than a
percentage of entry premium.  There is no take-profit; the only active exits
are the dollar stop-loss, optional max-hold, and expiry settlement.

Supports three leg configurations via the ``leg_type`` parameter:

    "strangle" — sell one call + one put (original behaviour)
    "call"     — sell only the call leg
    "put"      — sell only the put leg
"""
from typing import Any, Dict, List, Optional

from backtester.bt_option_selection import select_by_delta, apply_min_otm
from backtester.expiry_utils import parse_expiry_date, expiry_dt_utc, select_expiry
from backtester.pricing import deribit_fee_per_leg, EXPIRY_HOUR_UTC
from backtester.strategy_base import (
    OpenPosition, Trade, close_trade,
    check_expiry, close_short_strangle,
    time_window, max_hold_hours,
    _reprice_legs,
)


# ------------------------------------------------------------------
# Dollar stop-loss exit condition
# ------------------------------------------------------------------

def stop_loss_usd(usd_amount):
    # type: (float) -> Any
    """Close when unrealized loss on the position >= usd_amount USD.

    For short premium: loss = current_ask_usd - entry_price_usd.
    Disabled when usd_amount <= 0.
    """
    def check(state, pos):
        if usd_amount <= 0:
            return None
        current_usd = _reprice_legs(state, pos)
        if current_usd is None:
            return None
        if pos.metadata.get("direction") == "sell":
            if (current_usd - pos.entry_price_usd) >= usd_amount:
                return "stop_loss"
        else:
            if (pos.entry_price_usd - current_usd) >= usd_amount:
                return "stop_loss"
        return None
    return check


# ------------------------------------------------------------------
# Strategy
# ------------------------------------------------------------------

class ShortGenericSlUsd:
    """Sell 1 or 2 OTM legs (delta-selected); exit on USD stop-loss, time exit, or expiry."""

    name = "short_generic_sl_usd"
    DATE_RANGE = ("2025-12-21", "2026-04-21")
    DESCRIPTION = (
        "Sells a strangle, naked call, or naked put on a Deribit expiry N calendar "
        "days ahead (dte=1/2/3), with legs chosen by target delta. "
        "leg_type controls the structure: 'strangle' = call+put, 'call' = call only, "
        "'put' = put only. "
        "Stop-loss fires when total unrealized loss >= stop_loss_usd dollars. "
        "Set stop_loss_usd=0 to disable. "
        "One entry per day; up to dte+1 positions open concurrently. "
        "Exits on stop-loss, optional max hold duration, or expiry settlement."
    )

    PARAM_GRID = {
        "leg_type":       ["strangle"],
        "dte":            [1],
        "delta":          [0.075],
        "entry_hour":     [3, 6, 9],
        "stop_loss_usd":  [250,500,750,1000],
        "max_hold_hours": [0],
        "skip_weekends":  [1],
        "min_otm_pct":    [4,5,6],
    }

    def __init__(self):
        self._positions = []          # type: List[OpenPosition]
        self._leg_type = "strangle"
        self._dte = 1
        self._max_concurrent = 1
        self._delta = 0.25
        self._sl_usd = 0.0
        self._entry_hour = 10
        self._max_hold_hours = 0
        self._skip_weekends = 0
        self._min_otm_pct = 0
        self._last_trade_date = None  # type: Optional[Any]
        self._entry_conditions = []
        self._exit_conditions = []

    def configure(self, params):
        # type: (Dict[str, Any]) -> None
        self._leg_type = params.get("leg_type", "strangle")
        self._dte = params.get("dte", 1)
        self._delta = params["delta"]
        self._sl_usd = params.get("stop_loss_usd", 0.0)
        self._entry_hour = params.get("entry_hour", 10)
        self._max_hold_hours = params.get("max_hold_hours", 0)
        self._skip_weekends = params.get("skip_weekends", 0)
        self._min_otm_pct = params.get("min_otm_pct", 0)
        self._max_concurrent = self._dte + 1
        self._positions = []
        self._last_trade_date = None

        self._entry_conditions = [
            time_window(self._entry_hour, self._entry_hour + 1),
        ]
        self._exit_conditions = [
            stop_loss_usd(self._sl_usd),
        ]
        if self._max_hold_hours > 0:
            self._exit_conditions.append(max_hold_hours(self._max_hold_hours))

    def on_market_state(self, state):
        # type: (Any) -> List[Trade]
        trades = []

        to_close = []
        for pos in list(self._positions):
            reason = self._check_expiry(state, pos)
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

        if len(self._positions) < self._max_concurrent:
            today = state.dt.date()
            if self._last_trade_date != today:
                if self._skip_weekends and state.dt.weekday() >= 5:  # 5=Sat, 6=Sun
                    pass
                elif all(cond(state) for cond in self._entry_conditions):
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
            "leg_type":       self._leg_type,
            "dte":            self._dte,
            "delta":          self._delta,
            "stop_loss_usd":  self._sl_usd,
            "entry_hour":     self._entry_hour,
            "max_hold_hours": self._max_hold_hours,
            "skip_weekends":  self._skip_weekends,
            "min_otm_pct":    self._min_otm_pct,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _check_expiry(self, state, pos):
        # type: (Any, OpenPosition) -> Optional[str]
        return check_expiry(state, pos)

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
        if self._min_otm_pct > 0:
            call = apply_min_otm(calls, call, state.spot, self._min_otm_pct, is_call=True)
            put  = apply_min_otm(puts,  put,  state.spot, self._min_otm_pct, is_call=False)
            if call is None or put is None:
                return
        if call.bid <= 0 or put.bid <= 0:
            return
        call_usd = call.bid_usd
        put_usd  = put.bid_usd
        entry_usd = call_usd + put_usd
        if entry_usd <= 0:
            return
        pos = OpenPosition(
            entry_time=state.dt,
            entry_spot=state.spot,
            legs=[
                {"strike": call.strike, "is_call": True,  "expiry": expiry, "side": "sell",
                 "entry_price": call.bid, "entry_price_usd": call_usd, "entry_delta": call.delta},
                {"strike": put.strike,  "is_call": False, "expiry": expiry, "side": "sell",
                 "entry_price": put.bid, "entry_price_usd": put_usd,  "entry_delta": put.delta},
            ],
            entry_price_usd=entry_usd,
            fees_open=deribit_fee_per_leg(state.spot, call_usd) + deribit_fee_per_leg(state.spot, put_usd),
            metadata={
                "leg_type":    "strangle",
                "target_delta": self._delta,
                "expiry":       expiry,
                "expiry_dt":    exp_dt,
                "direction":    "sell",
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
        if self._min_otm_pct > 0:
            leg = apply_min_otm(quotes, leg, state.spot, self._min_otm_pct, is_call=is_call)
            if leg is None:
                return
        if leg.bid <= 0:
            return
        entry_usd = leg.bid_usd
        if entry_usd <= 0:
            return
        leg_type = "call" if is_call else "put"
        strike_key = "call_strike" if is_call else "put_strike"
        delta_key  = "call_delta"  if is_call else "put_delta"
        pos = OpenPosition(
            entry_time=state.dt,
            entry_spot=state.spot,
            legs=[
                {"strike": leg.strike, "is_call": is_call, "expiry": expiry, "side": "sell",
                 "entry_price": leg.bid, "entry_price_usd": entry_usd, "entry_delta": leg.delta},
            ],
            entry_price_usd=entry_usd,
            fees_open=deribit_fee_per_leg(state.spot, entry_usd),
            metadata={
                "leg_type":     leg_type,
                "target_delta": self._delta,
                "expiry":       expiry,
                "expiry_dt":    exp_dt,
                "direction":    "sell",
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
            trade = close_short_strangle(state, pos, reason)
        else:
            trade = self._close_single_leg(state, pos, reason)
        trade.metadata["leg_type"]       = leg_type
        trade.metadata["dte"]            = self._dte
        trade.metadata["stop_loss_usd"]  = self._sl_usd
        trade.metadata["max_hold_hours"] = self._max_hold_hours
        return trade

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
            exit_usd   = q.ask_usd if q and q.ask > 0 else _min_tick_usd
            fees_close = deribit_fee_per_leg(state.spot, exit_usd)

        return close_trade(state, pos, reason, exit_usd, fees_close)
