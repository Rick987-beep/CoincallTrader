"""
backtester/indicators.py — Indicator pre-computation for backtesting.

Strategies declare which indicators they need via the ``indicator_deps``
class attribute. The engine calls ``build_indicators()`` once before the
grid replay starts, and injects the result into every strategy instance
via ``strategy.set_indicators(ind)``.

Usage in a strategy::

    from backtester.indicators import IndicatorDep

    class MyStrategy:
        indicator_deps = [
            IndicatorDep(name="turbulence", symbol="BTCUSDT", interval="15m"),
        ]

        def set_indicators(self, ind):
            self._turbulence = ind.get("turbulence")

        def on_market_state(self, state):
            if self._turbulence is not None:
                hour_ts = state.dt.replace(minute=0, second=0, microsecond=0)
                try:
                    row = self._turbulence.loc[hour_ts]
                    if row["signal"] == "red":
                        return []
                except KeyError:
                    pass
            ...

Adding a new indicator
----------------------
Register it in the ``_BUILDERS`` dict at the bottom of this file:

    _BUILDERS["my_indicator"] = _build_my_indicator

where ``_build_my_indicator(df_raw, **params)`` takes a kline DataFrame
and returns a DataFrame/Series indexed by the relevant timestamps.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from indicators.hist_data import load_klines
from indicators.turbulence import turbulence as _turbulence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class IndicatorDep:
    """
    Declares one indicator dependency for a strategy.

    Attributes:
        name:     Key used in the ``indicators`` dict passed to
                  ``strategy.set_indicators()``.  Must match a registered
                  builder in ``_BUILDERS``.
        symbol:   Binance spot symbol, e.g. ``"BTCUSDT"``.
        interval: Kline interval required by the indicator, e.g. ``"15m"``.
        params:   Optional keyword arguments forwarded to the builder function.
        warmup_days: Extra history before the backtest start date needed for
                  the indicator's rolling windows to warm up fully.
                  Default 30 days covers Turbulence (14-day lookback).
    """
    name: str
    symbol: str
    interval: str
    params: Dict[str, Any] = field(default_factory=dict)
    warmup_days: int = 30


# ---------------------------------------------------------------------------
# Builder functions  (one per indicator)
# ---------------------------------------------------------------------------

def _build_turbulence(df_raw: pd.DataFrame, **params) -> pd.DataFrame:
    return _turbulence(df_raw, **params)


# Registry: indicator name → builder function
_BUILDERS: Dict[str, Callable[..., Any]] = {
    "turbulence": _build_turbulence,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_indicators(
    deps: List[IndicatorDep],
    start: datetime,
    end: datetime,
) -> Dict[str, pd.DataFrame]:
    """
    Fetch/cache klines and compute all declared indicators.

    Called once per grid run, before the replay loop starts.

    Args:
        deps:  List of ``IndicatorDep`` objects declared by the strategy.
        start: First timestamp of the backtest range (tz-aware UTC).
        end:   Last timestamp of the backtest range (tz-aware UTC).

    Returns:
        Dict mapping indicator name → computed DataFrame/Series.
        Passed directly to ``strategy.set_indicators()``.
    """
    result: Dict[str, pd.DataFrame] = {}

    for dep in deps:
        builder = _BUILDERS.get(dep.name)
        if builder is None:
            raise ValueError(
                f"Unknown indicator '{dep.name}'. "
                f"Registered indicators: {sorted(_BUILDERS)}"
            )

        logger.info(
            "build_indicators: loading %s klines for %s (%s → %s, +%dd warmup)",
            dep.interval, dep.symbol, start.date(), end.date(), dep.warmup_days,
        )
        df_raw = load_klines(
            symbol=dep.symbol,
            interval=dep.interval,
            start=start,
            end=end,
            warmup_days=dep.warmup_days,
        )
        logger.info(
            "build_indicators: computing '%s' from %d raw bars",
            dep.name, len(df_raw),
        )
        result[dep.name] = builder(df_raw, **dep.params)
        logger.info(
            "build_indicators: '%s' ready — %d output bars",
            dep.name, len(result[dep.name]),
        )

    return result
