#!/usr/bin/env python3
"""
deltaswipswap1m.py — Long straddle/strangle + 1-minute delta hedging via BTC-PERPETUAL.

Identical to deltaswipswap.py in every respect except for one key difference:

    5-min version  →  _maybe_rehedge() checks delta once per 5-min tick at state.spot
    1-min version  →  _maybe_rehedge_1m() iterates state.spot_bars and checks delta
                      at every 1-minute bar close, using BS delta with carried-forward IV

IV source: updated from the 5-min snapshot quote on each tick (same as 5-min version).
BS delta uses that carried-forward IV + the 1-min bar's close price for spot.
T (time to expiry) is computed from state.dt — the <5 min difference between bars
within a tick is negligible on a 1DTE option and not worth the added complexity.

Why this matters in theory:
    More rehedge opportunities → tighter delta neutrality throughout the hold.
    In a volatile period, small intra-5min moves that the 5-min version misses
    become banked gamma profits. In a quiet period the extra trades simply add
    more fee drag, so this version is expected to underperform in quiet markets
    and outperform during volatile moves.

See deltaswipswap.py for full docstring on the cash-flow model, fee assumptions,
and P&L accounting.
"""
import re
from datetime import datetime
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from backtester.pricing import (
    deribit_fee_per_leg, deribit_perp_fee,
    bs_call_delta, bs_put_delta,
    HOURS_PER_YEAR, EXPIRY_HOUR_UTC,
)
from backtester.strategy_base import (
    OpenPosition, Trade,
    time_window, weekday_only, time_exit, max_hold_hours,
)


# ------------------------------------------------------------------
# Expiry helpers (identical to deltaswipswap.py)
# ------------------------------------------------------------------

@lru_cache(maxsize=64)
def _parse_expiry_date(expiry_code):
    # type: (str) -> Optional[datetime]
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


def _nearest_valid_expiry(state):
    # type: (Any) -> Optional[str]
    best = None
    best_dt = None
    for exp in state.expiries():
        exp_date = _parse_expiry_date(exp)
        if exp_date is None:
            continue
        exp_dt = exp_date.replace(hour=EXPIRY_HOUR_UTC, tzinfo=state.dt.tzinfo)
        if exp_dt <= state.dt:
            continue
        if best_dt is None or exp_dt < best_dt:
            best = exp
            best_dt = exp_dt
    return best


def _hours_to_expiry(current_dt, expiry_code):
    # type: (Any, str) -> float
    exp_date = _parse_expiry_date(expiry_code)
    if exp_date is None:
        return 0.0
    exp_dt = exp_date.replace(hour=EXPIRY_HOUR_UTC)
    current_naive = current_dt.replace(tzinfo=None)
    return max((exp_dt - current_naive).total_seconds() / 3600.0, 0.0)


# ------------------------------------------------------------------
# Strategy
# ------------------------------------------------------------------

class DeltaSwipSwap1m:
    """Long straddle/strangle + dynamic delta-hedging at 1-minute resolution.

    Same logic as DeltaSwipSwap but rehedging iterates the 1-min spot bars
    inside each 5-min tick, using BS delta with carried-forward IV.
    Exit conditions and option pricing remain at 5-min resolution.
    """

    name = "deltaswipswap1m"
    DATE_RANGE = ("2026-03-09", "2026-03-23")
    DESCRIPTION = (
        "Long ATM straddle or OTM strangle, delta-neutralised at entry via BTC-PERPETUAL. "
        "Perp rebalanced at 1-minute resolution using BS delta + carried-forward IV. "
        "Captures more gamma events vs 5-min version at the cost of higher perp fees."
    )

    PARAM_GRID = {
        "offset":        [1500, 2000, 2500],
        "entry_hour":    [9, 10, 12, 13, 14, 16],
        "close_hour":    [17, 18, 19, 20, 21, 22],
        "rehedge_delta": [0.05, 0.10, 0.20, 0.30],
        "max_hold":      [4, 6, 8, 10],
    }

    def __init__(self):
        self._position = None         # type: Optional[OpenPosition]
        self._offset = 0
        self._entry_hour = 9
        self._close_hour = 15
        self._rehedge_delta = 0.10
        self._max_hold = 8
        self._last_trade_date = None  # type: Optional[Any]

        # Perp state — reset for each new trade
        self._perp_qty = 0.0
        self._perp_cash_flows = 0.0
        self._perp_fees_total = 0.0
        self._perp_trades = 0

        # --- 1min addition: IV cache for BS delta at 1-min bars ---
        # Updated on each 5-min tick from snapshot quotes.
        # Used as σ in bs_call_delta / bs_put_delta at every 1-min bar.
        self._last_iv_call = 0.5      # type: float
        self._last_iv_put = 0.5       # type: float

        self._entry_conditions = []
        self._exit_conditions = []

    def configure(self, params):
        # type: (Dict[str, Any]) -> None
        self._offset = params["offset"]
        self._entry_hour = params["entry_hour"]
        self._close_hour = params["close_hour"]
        self._rehedge_delta = params["rehedge_delta"]
        self._max_hold = params.get("max_hold", 8)
        self._position = None
        self._last_trade_date = None
        self._reset_perp()

        self._entry_conditions = [
            weekday_only(),
            time_window(self._entry_hour, self._entry_hour + 1),
        ]
        self._exit_conditions = [
            time_exit(self._close_hour),
            max_hold_hours(self._max_hold),
        ]

    def on_market_state(self, state):
        # type: (Any) -> List[Trade]
        trades = []

        if self._position is not None:
            # Check exits at 5-min resolution (unchanged from 5-min version)
            reason = self._check_expiry(state)
            if reason is None:
                for cond in self._exit_conditions:
                    reason = cond(state, self._position)
                    if reason:
                        break
            if reason:
                trades.append(self._close(state, reason))
            else:
                # --- 1min addition: update IV cache from this 5-min snapshot,
                #     then rehedge at each 1-min bar instead of at state.spot ---
                self._refresh_iv_cache(state)
                self._maybe_rehedge_1m(state)

        if self._position is None:
            today = state.dt.date()
            if self._last_trade_date != today:
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
        self._last_trade_date = None
        self._reset_perp()

    def describe_params(self):
        # type: () -> Dict[str, Any]
        return {
            "offset":        self._offset,
            "entry_hour":    self._entry_hour,
            "close_hour":    self._close_hour,
            "rehedge_delta": self._rehedge_delta,
            "max_hold":      self._max_hold,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reset_perp(self):
        # type: () -> None
        self._perp_qty = 0.0
        self._perp_cash_flows = 0.0
        self._perp_fees_total = 0.0
        self._perp_trades = 0
        self._last_iv_call = 0.5
        self._last_iv_put = 0.5

    def _check_expiry(self, state):
        # type: (Any) -> Optional[str]
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

    # --- 1min addition: separate IV refresh from delta computation ---
    # In the 5-min version, _get_option_deltas() does both in one call.
    # Here we split them: refresh IV once per 5-min tick, then compute
    # pure BS delta cheaply at each 1-min bar without touching the option chain.

    def _refresh_iv_cache(self, state):
        # type: (Any) -> None
        """Update cached IVs from the current 5-min snapshot quotes.

        --- 1min addition ---
        Called once per 5-min tick before the 1-min rehedge loop.
        Falls back silently if quotes are unavailable.
        """
        pos = self._position
        expiry = pos.metadata["expiry"]
        call_q = state.get_option(expiry, pos.metadata["call_strike"], True)
        put_q = state.get_option(expiry, pos.metadata["put_strike"], False)
        if call_q is not None and call_q.mark_iv > 0:
            self._last_iv_call = call_q.mark_iv / 100.0
        if put_q is not None and put_q.mark_iv > 0:
            self._last_iv_put = put_q.mark_iv / 100.0

    def _maybe_rehedge_1m(self, state):
        # type: (Any) -> None
        """Iterate 1-min spot bars and rehedge at each bar close if needed.

        --- 1min addition ---
        Replaces the single-point _maybe_rehedge(state) call in the 5-min version.

        For each bar:
          1. Compute BS call delta and BS put delta at bar.close with carried-forward IV.
          2. net_delta = delta_call + delta_put + current perp_qty
          3. If |net_delta| >= rehedge_delta: trade perp at bar.close to re-zero delta.

        T uses state.dt (the 5-min tick time). Within a 5-min window, T changes
        by at most 5/8760 ≈ 0.06% of a year — negligible on a 1DTE option.
        """
        pos = self._position
        expiry = pos.metadata["expiry"]
        call_strike = pos.metadata["call_strike"]
        put_strike = pos.metadata["put_strike"]
        T = max(_hours_to_expiry(state.dt, expiry), 0.001) / HOURS_PER_YEAR

        for bar in state.spot_bars:
            spot_1m = bar.close
            delta_call = bs_call_delta(spot_1m, call_strike, T, self._last_iv_call)
            delta_put = bs_put_delta(spot_1m, put_strike, T, self._last_iv_put)
            net_delta = delta_call + delta_put + self._perp_qty
            if abs(net_delta) >= self._rehedge_delta:
                self._trade_perp(-net_delta, spot_1m)

    def _trade_perp(self, delta_qty, spot):
        # type: (float, float) -> None
        notional = abs(delta_qty) * spot
        fee = deribit_perp_fee(notional)
        self._perp_cash_flows -= delta_qty * spot
        self._perp_fees_total += fee
        self._perp_qty += delta_qty
        self._perp_trades += 1

    def _try_open(self, state):
        # type: (Any) -> None
        expiry = _nearest_valid_expiry(state)
        if expiry is None:
            return

        if self._offset == 0:
            call, put = state.get_straddle(expiry)
        else:
            call, put = state.get_strangle(expiry, self._offset)

        if call is None or put is None:
            return
        if call.ask <= 0 or put.ask <= 0:
            return

        entry_usd = call.ask_usd + put.ask_usd
        if entry_usd <= 0 or entry_usd != entry_usd:
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
            },
        )

        # Cache IVs for BS delta fallback
        if call.mark_iv > 0:
            self._last_iv_call = call.mark_iv / 100.0
        if put.mark_iv > 0:
            self._last_iv_put = put.mark_iv / 100.0

        # Delta-neutralise at entry using snapshot delta or BS fallback
        T_entry = max(_hours_to_expiry(state.dt, expiry), 0.001) / HOURS_PER_YEAR
        if call.delta == call.delta and call.delta != 0.0:
            delta_call_entry = call.delta
        else:
            delta_call_entry = bs_call_delta(state.spot, call.strike, T_entry, self._last_iv_call)
        if put.delta == put.delta and put.delta != 0.0:
            delta_put_entry = put.delta
        else:
            delta_put_entry = bs_put_delta(state.spot, put.strike, T_entry, self._last_iv_put)

        net_option_delta = delta_call_entry + delta_put_entry
        if abs(net_option_delta) > 1e-6:
            self._trade_perp(-net_option_delta, state.spot)

    def _close(self, state, reason):
        # type: (Any, str) -> Trade
        pos = self._position
        expiry = pos.metadata["expiry"]
        call_strike = pos.metadata["call_strike"]
        put_strike = pos.metadata["put_strike"]

        # --- Close options at 5-min bid (unchanged from 5-min version) ---
        if reason == "expiry":
            call_intrinsic = max(0.0, state.spot - call_strike)
            put_intrinsic = max(0.0, put_strike - state.spot)
            exit_usd = call_intrinsic + put_intrinsic
            fees_close_options = 0.0
        else:
            call_q = state.get_option(expiry, call_strike, True)
            put_q = state.get_option(expiry, put_strike, False)
            call_bid_usd = call_q.bid_usd if call_q else 0.0
            put_bid_usd = put_q.bid_usd if put_q else 0.0
            if call_bid_usd != call_bid_usd:
                call_bid_usd = 0.0
            if put_bid_usd != put_bid_usd:
                put_bid_usd = 0.0
            exit_usd = call_bid_usd + put_bid_usd
            fees_close_options = (
                deribit_fee_per_leg(state.spot, call_bid_usd)
                + deribit_fee_per_leg(state.spot, put_bid_usd)
            )

        # --- Close perp: unwind entire position at spot ---
        perp_qty_pre_close = self._perp_qty
        if abs(self._perp_qty) > 1e-9:
            close_delta = -self._perp_qty
            self._perp_cash_flows -= close_delta * state.spot
            self._perp_fees_total += deribit_perp_fee(abs(close_delta) * state.spot)
            self._perp_trades += 1

        # --- P&L ---
        option_pnl = exit_usd - pos.entry_price_usd - (pos.fees_open + fees_close_options)
        perp_pnl = self._perp_cash_flows - self._perp_fees_total
        total_pnl = option_pnl + perp_pnl
        total_fees = pos.fees_open + fees_close_options + self._perp_fees_total

        held_s = (state.dt - pos.entry_time).total_seconds()
        trade = Trade(
            entry_time=pos.entry_time,
            exit_time=state.dt,
            entry_spot=pos.entry_spot,
            exit_spot=state.spot,
            entry_price_usd=pos.entry_price_usd,
            exit_price_usd=exit_usd,
            fees=total_fees,
            pnl=total_pnl,
            triggered=False,
            exit_reason=reason,
            exit_hour=int(held_s / 3600),
            entry_date=pos.entry_time.strftime("%Y-%m-%d"),
            metadata={
                **pos.metadata,
                "option_pnl":         round(option_pnl, 4),
                "perp_pnl":           round(perp_pnl, 4),
                "perp_trades":        self._perp_trades,
                "perp_fees":          round(self._perp_fees_total, 4),
                "perp_qty_pre_close": round(perp_qty_pre_close, 6),
                "rehedge_delta":      self._rehedge_delta,
                "close_hour":         self._close_hour,
                "rehedge_resolution": "1m",  # 1min addition: tag for comparison
            },
        )

        self._last_trade_date = pos.entry_time.date()
        self._position = None
        self._reset_perp()
        return trade
