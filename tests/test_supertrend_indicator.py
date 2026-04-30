"""
Unit tests for indicators/supertrend.py.

Verify the report's 2-bar range ATR variant of SuperTrend:
  range[i]      = max(close[i], close[i-1]) − min(close[i], close[i-1])
  atr[i]        = SMA(range, period)
  upper_band[i] = close[i] + multiplier × atr[i]
  lower_band[i] = close[i] − multiplier × atr[i]

  trend init +1; flip to +1 when close > prev_upper, to -1 when close < prev_lower.
"""

import numpy as np
import pandas as pd
import pytest

from indicators.supertrend import (
    DEFAULT_MULTIPLIER,
    DEFAULT_PERIOD,
    latest_signal,
    supertrend,
)


def _df(closes):
    """Build a 1h-indexed DataFrame from a list of closes."""
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="1h", tz="UTC")
    return pd.DataFrame({"close": closes}, index=idx)


# ─── ATR / range ────────────────────────────────────────────────────────────

class TestRangeAndATR:
    def test_range_first_bar_is_zero(self):
        df = _df([100.0, 101.0, 102.0])
        out = supertrend(df, period=2, multiplier=1.0)
        # Range[0] uses prev_close = close[0], so range[0] == 0.
        assert out["range"].iloc[0] == pytest.approx(0.0)
        assert out["range"].iloc[1] == pytest.approx(1.0)
        assert out["range"].iloc[2] == pytest.approx(1.0)

    def test_atr_is_sma_of_range(self):
        # closes step by 1, 2, 3, 4 → ranges = 0, 1, 2, 3, 4
        df = _df([100.0, 101.0, 103.0, 106.0, 110.0])
        out = supertrend(df, period=3, multiplier=1.0)
        # First 2 ATR values are NaN (need 3 ranges).
        assert np.isnan(out["atr"].iloc[0])
        assert np.isnan(out["atr"].iloc[1])
        # ATR[2] = mean(0,1,2) = 1.0; ATR[3] = mean(1,2,3) = 2.0
        assert out["atr"].iloc[2] == pytest.approx(1.0)
        assert out["atr"].iloc[3] == pytest.approx(2.0)
        assert out["atr"].iloc[4] == pytest.approx(3.0)

    def test_bands_are_close_plus_minus_mult_atr(self):
        df = _df([100.0, 101.0, 103.0, 106.0])
        out = supertrend(df, period=3, multiplier=2.0)
        atr3 = out["atr"].iloc[3]
        assert out["upper_band"].iloc[3] == pytest.approx(106.0 + 2.0 * atr3)
        assert out["lower_band"].iloc[3] == pytest.approx(106.0 - 2.0 * atr3)


# ─── Trend state machine ────────────────────────────────────────────────────

class TestTrendStateMachine:
    def test_initial_trend_is_plus_one(self):
        df = _df([100.0] * 10)
        out = supertrend(df, period=3, multiplier=2.0)
        # Constant closes → no breakouts → trend stays at +1 throughout.
        assert (out["trend"] == 1).all()
        assert not out["flip_up"].any()
        assert not out["flip_down"].any()

    def test_flip_down_when_close_below_prev_lower(self):
        # Build a series that walks up gently then dumps hard.
        closes = [100.0, 100.5, 101.0, 101.5, 102.0, 102.5, 103.0, 103.5,
                  103.6, 50.0]  # massive drop on the last bar
        df = _df(closes)
        out = supertrend(df, period=3, multiplier=1.0)
        # Last bar's close is way below any prior lower band → flip down.
        assert out["trend"].iloc[-1] == -1
        assert out["flip_down"].iloc[-1] is np.True_ or bool(out["flip_down"].iloc[-1])
        # All prior bars stayed at +1 (nothing dropped through lower band before).
        assert (out["trend"].iloc[:-1] == 1).all()

    def test_flip_up_after_flip_down(self):
        # Down hard, then ramp up hard.
        closes = (
            [100.0, 100.5, 101.0, 101.5, 102.0, 102.5, 103.0, 103.5, 50.0]
            + [60.0, 70.0, 80.0, 200.0]
        )
        df = _df(closes)
        out = supertrend(df, period=3, multiplier=1.0)
        assert out["flip_down"].any()
        # Eventually a flip back up must occur.
        assert out["flip_up"].any()
        # After the up flip, last trend == +1.
        last_up_idx = np.where(out["flip_up"].values)[0][-1]
        assert out["trend"].iloc[last_up_idx] == 1
        assert out["trend"].iloc[last_up_idx - 1] == -1

    def test_no_flip_when_inside_bands(self):
        # Tiny oscillations inside a wide ATR band — trend should not flip.
        rng = np.random.default_rng(42)
        closes = list(100.0 + rng.normal(0, 0.05, size=200))
        df = _df(closes)
        out = supertrend(df, period=7, multiplier=10.0)  # super-wide bands
        # No breakouts allowed by such wide bands → trend stays at +1.
        assert (out["trend"] == 1).all()
        assert not out["flip_down"].any()


# ─── Manual reference vector ────────────────────────────────────────────────

class TestReferenceVector:
    """Hand-computed reference for a tiny series — guards against regressions."""

    def test_known_uptrend_breakout(self):
        # period=3, multiplier=1
        # closes      :  10, 11, 12, 13, 14, 30
        # ranges      :   0,  1,  1,  1,  1, 16
        # atr (i>=3)  :  na, na, 0.667, 1.0, 1.0, 6.0
        # upper[i]    :              13.667, 14.0, 15.0, 36.0
        # lower[i]    :              12.333, 12.0, 13.0, 24.0
        # trend init +1; close[5]=30 > upper[4]=15 → trend stays +1, no flip
        closes = [10.0, 11.0, 12.0, 13.0, 14.0, 30.0]
        df = _df(closes)
        out = supertrend(df, period=3, multiplier=1.0)
        assert out["atr"].iloc[2] == pytest.approx(2/3)
        assert out["atr"].iloc[3] == pytest.approx(1.0)
        assert out["atr"].iloc[5] == pytest.approx(6.0)
        assert (out["trend"] == 1).all()


# ─── latest_signal helper ───────────────────────────────────────────────────

class TestLatestSignal:
    def test_returns_none_when_too_few_bars(self):
        df = _df([100.0] * 5)  # period default 7 → need >7
        assert latest_signal(df) is None

    def test_returns_dict_with_expected_keys(self):
        df = _df([100.0 + i * 0.1 for i in range(50)])
        sig = latest_signal(df, period=DEFAULT_PERIOD, multiplier=DEFAULT_MULTIPLIER)
        assert sig is not None
        assert set(sig.keys()) == {"bar_ts", "trend", "flip_up", "flip_down"}
        assert sig["trend"] in (1, -1)
        assert isinstance(sig["flip_up"], bool)
        assert isinstance(sig["flip_down"], bool)

    def test_bar_ts_is_last_index(self):
        df = _df([100.0 + i for i in range(20)])
        sig = latest_signal(df, period=3, multiplier=1.0)
        assert sig["bar_ts"] == df.index[-1]

    def test_flip_up_marked_on_recovery_bar(self):
        closes = (
            [100.0, 100.5, 101.0, 101.5, 102.0, 102.5, 103.0, 103.5, 50.0]
            + [60.0, 70.0, 80.0, 200.0]
        )
        df = _df(closes)
        sig = latest_signal(df, period=3, multiplier=1.0)
        # Last bar (200.0) should produce flip_up=True with trend=+1.
        assert sig["trend"] == 1
        assert sig["flip_up"] is True


# ─── Edge cases ─────────────────────────────────────────────────────────────

class TestEdges:
    def test_empty_df(self):
        df = pd.DataFrame({"close": []})
        out = supertrend(df, period=7, multiplier=3.0)
        assert len(out) == 0
        assert list(out.columns) == [
            "range", "atr", "upper_band", "lower_band",
            "trend", "flip_up", "flip_down",
        ]

    def test_missing_close_raises(self):
        df = pd.DataFrame({"open": [1.0, 2.0]})
        with pytest.raises(ValueError):
            supertrend(df)

    def test_single_bar(self):
        df = _df([100.0])
        out = supertrend(df, period=7, multiplier=3.0)
        assert len(out) == 1
        assert out["trend"].iloc[0] == 1
        assert not out["flip_up"].iloc[0]
        assert not out["flip_down"].iloc[0]
