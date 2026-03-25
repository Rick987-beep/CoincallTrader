#!/usr/bin/env python3
"""
market_replay.py — Market state iterator for backtesting.

Loads pre-built snapshot parquets (from snapshot_builder.py) and provides
a time-stepped iterator that yields MarketState objects at each 5-min
interval. Strategies see a clean, read-only market view at each step.

Key design:
    - Simple iterator (not event bus). Strategies pull data, no callbacks.
    - Option data keyed by (expiry, strike, is_call) for O(1) lookup.
    - Spot track as NumPy arrays for fast excursion range queries.
    - Pre-computed cumulative max/min for O(1) excursion lookups.
    - Strategy-scoped expiry filtering at load time — one snapshot serves all.

Usage:
    replay = MarketReplay(
        "snapshots/options_20260309_20260323.parquet",
        "snapshots/spot_track_20260309_20260323.parquet",
    )
    for state in replay:
        # state.spot, state.get_option(...), state.spot_bars, etc.
        pass
"""
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

import numpy as np
import pandas as pd


# ------------------------------------------------------------------
# Data types
# ------------------------------------------------------------------

@dataclass
class OptionQuote:
    """Single option quote at a point in time."""
    strike: float
    is_call: bool
    expiry: str
    bid: float              # BTC-denominated
    ask: float
    mark: float
    mark_iv: float
    delta: float
    spot: float             # Underlying BTC price at this snapshot

    @property
    def bid_usd(self):
        # type: () -> float
        """Bid price in USD (bid_btc × spot)."""
        return self.bid * self.spot

    @property
    def ask_usd(self):
        # type: () -> float
        """Ask price in USD (ask_btc × spot)."""
        return self.ask * self.spot

    @property
    def mark_usd(self):
        # type: () -> float
        """Mark price in USD (mark_btc × spot)."""
        return self.mark * self.spot


@dataclass
class SpotBar:
    """1-minute OHLC bar for BTC spot price."""
    timestamp: int      # Microseconds since epoch
    open: float
    high: float
    low: float
    close: float


@dataclass
class MarketState:
    """Snapshot of the market at one 5-min interval.

    Provides option chain lookups and spot price data. Constructed by
    MarketReplay — strategies consume this, never build it.
    """
    timestamp: int              # Microseconds (5-min aligned)
    dt: datetime                # UTC datetime
    spot: float                 # BTC/USD (close of latest 1-min bar)
    spot_bars: List[SpotBar]    # 1-min bars since last MarketState (up to 5)

    # Internal: pre-grouped option data for this interval
    _options: Dict[Tuple[str, float, bool], "OptionQuote"] = field(
        default_factory=dict, repr=False
    )
    _expiries: List[str] = field(default_factory=list, repr=False)

    # Internal: reference to replay's spot arrays for excursion lookups
    _spot_ts: Optional[np.ndarray] = field(default=None, repr=False)
    _spot_highs_cum: Optional[np.ndarray] = field(default=None, repr=False)
    _spot_lows_cum: Optional[np.ndarray] = field(default=None, repr=False)
    _spot_close: Optional[np.ndarray] = field(default=None, repr=False)

    def get_option(self, expiry, strike, is_call):
        # type: (str, float, bool) -> Optional[OptionQuote]
        """Single option lookup. O(1) dict access."""
        return self._options.get((expiry, float(strike), bool(is_call)))

    def get_chain(self, expiry):
        # type: (str) -> List[OptionQuote]
        """All options for one expiry, sorted by strike."""
        result = [q for key, q in self._options.items() if key[0] == expiry]
        result.sort(key=lambda q: (q.strike, q.is_call))
        return result

    def get_atm_strike(self, expiry):
        # type: (str) -> Optional[float]
        """ATM strike (nearest to spot) for an expiry."""
        strikes = set()
        for (exp, strike, _), _ in self._options.items():
            if exp == expiry:
                strikes.add(strike)
        if not strikes:
            return None
        return min(strikes, key=lambda s: abs(s - self.spot))

    def get_straddle(self, expiry, strike=None):
        # type: (str, Optional[float]) -> Tuple[Optional[OptionQuote], Optional[OptionQuote]]
        """ATM or specific-strike call+put pair."""
        if strike is None:
            strike = self.get_atm_strike(expiry)
        if strike is None:
            return None, None
        call = self.get_option(expiry, strike, True)
        put = self.get_option(expiry, strike, False)
        return call, put

    def get_strangle(self, expiry, offset):
        # type: (str, float) -> Tuple[Optional[OptionQuote], Optional[OptionQuote]]
        """OTM call+put at ±offset from ATM.

        offset=0 is equivalent to get_straddle(expiry).
        """
        atm = self.get_atm_strike(expiry)
        if atm is None:
            return None, None
        # Find nearest available strikes to atm+offset and atm-offset
        call_target = atm + offset
        put_target = atm - offset
        strikes = set()
        for (exp, s, _), _ in self._options.items():
            if exp == expiry:
                strikes.add(s)
        if not strikes:
            return None, None
        call_strike = min(strikes, key=lambda s: abs(s - call_target))
        put_strike = min(strikes, key=lambda s: abs(s - put_target))
        return (
            self.get_option(expiry, call_strike, True),
            self.get_option(expiry, put_strike, False),
        )

    def expiries(self):
        # type: () -> List[str]
        """Available expiries at this time step."""
        return list(self._expiries)

    def spot_high_since(self, entry_time_us):
        # type: (int) -> float
        """Highest spot price since entry_time (µs). O(1) via cummax."""
        if self._spot_ts is None:
            return self.spot
        i_start = np.searchsorted(self._spot_ts, entry_time_us, side="left")
        i_end = np.searchsorted(self._spot_ts, self.timestamp, side="right") - 1
        i_end = max(i_end, i_start)
        if i_end < len(self._spot_highs_cum):
            return float(self._spot_highs_cum[i_end])
        return self.spot

    def spot_low_since(self, entry_time_us):
        # type: (int) -> float
        """Lowest spot price since entry_time (µs). O(1) via cummin."""
        if self._spot_ts is None:
            return self.spot
        i_start = np.searchsorted(self._spot_ts, entry_time_us, side="left")
        i_end = np.searchsorted(self._spot_ts, self.timestamp, side="right") - 1
        i_end = max(i_end, i_start)
        if i_end < len(self._spot_lows_cum):
            return float(self._spot_lows_cum[i_end])
        return self.spot


# ------------------------------------------------------------------
# MarketReplay — the iterator
# ------------------------------------------------------------------

class MarketReplay:
    """Loads snapshot parquets and iterates as MarketState objects.

    Args:
        snapshot_path: Path to option snapshot parquet.
        spot_track_path: Path to spot track OHLC parquet.
        expiry_filter: Optional list of expiry codes to keep (runtime filter).
        start: Optional start time (inclusive). Accepts str/int/datetime.
        end: Optional end time (inclusive).
        step_minutes: Iteration step (default 5, must be >= snapshot interval).
    """

    def __init__(
        self,
        snapshot_path,      # type: str
        spot_track_path,    # type: str
        expiry_filter=None, # type: Optional[List[str]]
        start=None,         # type: Optional[Any]
        end=None,           # type: Optional[Any]
        step_minutes=5,     # type: int
    ):
        # Load option snapshots
        self._opt_df = pd.read_parquet(snapshot_path)
        if expiry_filter:
            self._opt_df = self._opt_df[
                self._opt_df["expiry"].isin(expiry_filter)
            ].reset_index(drop=True)

        # Load spot track
        self._spot_df = pd.read_parquet(spot_track_path)

        # Time filtering
        if start is not None:
            start_us = self._to_us(start)
            self._opt_df = self._opt_df[
                self._opt_df["timestamp"] >= start_us
            ].reset_index(drop=True)
            self._spot_df = self._spot_df[
                self._spot_df["timestamp"] >= start_us
            ].reset_index(drop=True)
        if end is not None:
            end_us = self._to_us(end)
            self._opt_df = self._opt_df[
                self._opt_df["timestamp"] <= end_us
            ].reset_index(drop=True)
            self._spot_df = self._spot_df[
                self._spot_df["timestamp"] <= end_us
            ].reset_index(drop=True)

        # Pre-group options by timestamp for fast access
        self._opt_groups = {}  # type: Dict[int, pd.DataFrame]
        for ts, grp in self._opt_df.groupby("timestamp"):
            self._opt_groups[int(ts)] = grp

        # All 5-min timestamps, filtered by step
        all_ts = np.array(sorted(self._opt_groups.keys()), dtype=np.int64)
        if step_minutes > 5:
            step_us = step_minutes * 60 * 1_000_000
            all_ts = all_ts[all_ts % step_us == 0]
        self._timestamps = all_ts

        # Spot track as NumPy arrays
        self._spot_ts = self._spot_df["timestamp"].values.astype(np.int64)
        self._spot_open = self._spot_df["open"].values.astype(np.float64)
        self._spot_high = self._spot_df["high"].values.astype(np.float64)
        self._spot_low = self._spot_df["low"].values.astype(np.float64)
        self._spot_close = self._spot_df["close"].values.astype(np.float64)

        # Pre-compute cumulative max/min of high/low for O(1) excursion
        self._spot_cum_high = np.maximum.accumulate(self._spot_high)
        self._spot_cum_low = np.minimum.accumulate(self._spot_low)

        # Drop DataFrames no longer needed
        del self._spot_df

        n_opt = len(self._opt_df)
        n_ts = len(self._timestamps)
        n_spot = len(self._spot_ts)
        print(
            f"MarketReplay loaded: {n_opt:,} option rows, "
            f"{n_ts} intervals, {n_spot} spot bars"
        )

    @staticmethod
    def _to_us(t):
        """Convert time arg to microseconds."""
        if isinstance(t, (int, np.integer)):
            return int(t)
        if isinstance(t, str):
            t = pd.Timestamp(t, tz="UTC")
        if isinstance(t, datetime):
            t = pd.Timestamp(t)
        if isinstance(t, pd.Timestamp):
            if t.tz is None:
                t = t.tz_localize("UTC")
            return int(t.timestamp() * 1_000_000)
        raise TypeError(f"Cannot convert {type(t)} to timestamp")

    @property
    def timestamps(self):
        # type: () -> np.ndarray
        """All available 5-min timestamps (microseconds)."""
        return self._timestamps

    @property
    def time_range(self):
        # type: () -> Tuple[datetime, datetime]
        """Data coverage as (start, end) UTC datetimes."""
        return (
            datetime.fromtimestamp(
                self._timestamps[0] / 1_000_000, tz=timezone.utc
            ),
            datetime.fromtimestamp(
                self._timestamps[-1] / 1_000_000, tz=timezone.utc
            ),
        )

    def __len__(self):
        # type: () -> int
        return len(self._timestamps)

    def __iter__(self):
        # type: () -> Iterator[MarketState]
        """Yield MarketState for each time step."""
        prev_ts = None
        for ts in self._timestamps:
            state = self._build_state(ts, prev_ts)
            yield state
            prev_ts = ts

    def _build_state(self, ts, prev_ts):
        # type: (int, Optional[int]) -> MarketState
        """Construct MarketState for one 5-min interval."""
        dt = datetime.fromtimestamp(ts / 1_000_000, tz=timezone.utc)

        # Spot: close of the latest 1-min bar at or before this timestamp
        spot_idx = np.searchsorted(self._spot_ts, ts, side="right") - 1
        if spot_idx < 0:
            spot_idx = 0
        spot = float(self._spot_close[spot_idx])

        # Spot bars since last state (up to step_minutes bars)
        if prev_ts is not None:
            bar_start = np.searchsorted(self._spot_ts, prev_ts, side="right")
        else:
            bar_start = max(0, spot_idx - 4)  # First state: grab up to 5 bars
        bar_end = spot_idx + 1  # inclusive
        spot_bars = []
        for i in range(bar_start, min(bar_end, len(self._spot_ts))):
            spot_bars.append(SpotBar(
                timestamp=int(self._spot_ts[i]),
                open=float(self._spot_open[i]),
                high=float(self._spot_high[i]),
                low=float(self._spot_low[i]),
                close=float(self._spot_close[i]),
            ))

        # Options: build dict keyed by (expiry, strike, is_call)
        options = {}  # type: Dict[Tuple[str, float, bool], OptionQuote]
        expiries = set()
        grp = self._opt_groups.get(ts)
        if grp is not None:
            for row in grp.itertuples(index=False):
                expiry = str(row.expiry)
                strike = float(row.strike)
                is_call = bool(row.is_call)
                expiries.add(expiry)
                options[(expiry, strike, is_call)] = OptionQuote(
                    strike=strike,
                    is_call=is_call,
                    expiry=expiry,
                    bid=float(row.bid_price),
                    ask=float(row.ask_price),
                    mark=float(row.mark_price),
                    mark_iv=float(row.mark_iv),
                    delta=float(row.delta),
                    spot=spot,
                )

        return MarketState(
            timestamp=ts,
            dt=dt,
            spot=spot,
            spot_bars=spot_bars,
            _options=options,
            _expiries=sorted(expiries),
            _spot_ts=self._spot_ts,
            _spot_highs_cum=self._spot_cum_high,
            _spot_lows_cum=self._spot_cum_low,
            _spot_close=self._spot_close,
        )
