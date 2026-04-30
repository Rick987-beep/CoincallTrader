"""
Unit tests for strategies/supertrend_long_call.py.

Mocks indicators.data.fetch_klines so no network calls are made. Verifies:
  - Entry condition fires only on flip-up bars
  - Entry condition dedupes within the same bar
  - Exit condition fires whenever latest trend is -1 (incl. post-restart)
  - Strategy factory wires the right execution profile and leg spec
"""

from datetime import timezone
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from tests.conftest import make_account


def _df_from_closes(closes, end_ts="2026-04-28 12:00"):
    """Build a 1h-indexed DataFrame ending at end_ts (UTC)."""
    end = pd.Timestamp(end_ts, tz="UTC")
    idx = pd.date_range(end=end, periods=len(closes), freq="1h")
    return pd.DataFrame({
        "open":   closes,
        "high":   closes,
        "low":    closes,
        "close":  closes,
        "volume": [0.0] * len(closes),
    }, index=idx)


@pytest.fixture(autouse=True)
def _reset_dedupe():
    """Reset the entry-condition's per-bar dedupe between tests."""
    import strategies.supertrend_long_call as mod
    mod._last_entry_bar = None
    yield
    mod._last_entry_bar = None


@pytest.fixture(autouse=True)
def _freeze_time(monkeypatch):
    """Pretend 'now' is later than any test bar so closed-bar trimming
    keeps the last input bar in latest_signal()."""
    fake_now = pd.Timestamp("2026-04-29", tz="UTC")
    real_now = pd.Timestamp.now

    def _now(tz=None):
        if tz is None:
            return real_now()
        return fake_now.tz_convert(tz) if fake_now.tzinfo else fake_now.tz_localize(tz)

    monkeypatch.setattr(pd.Timestamp, "now", staticmethod(_now))


# ─── Entry Condition ────────────────────────────────────────────────────────

class TestEntryCondition:
    def test_no_signal_when_no_data(self):
        from strategies.supertrend_long_call import _supertrend_flip_up_entry
        with patch("strategies.supertrend_long_call.fetch_klines", return_value=None):
            cond = _supertrend_flip_up_entry()
            assert cond(make_account()) is False

    def test_no_signal_when_not_enough_bars(self):
        from strategies.supertrend_long_call import _supertrend_flip_up_entry
        df = _df_from_closes([100.0] * 3)  # < period+1
        with patch("strategies.supertrend_long_call.fetch_klines", return_value=df):
            cond = _supertrend_flip_up_entry()
            assert cond(make_account()) is False

    def test_no_entry_when_steady_uptrend(self):
        """Trend = +1 but no flip → must NOT enter (clean restart rule)."""
        from strategies.supertrend_long_call import _supertrend_flip_up_entry
        # Constant closes → trend stays at initial +1, no flip event ever.
        df = _df_from_closes([100.0] * 100)
        with patch("strategies.supertrend_long_call.fetch_klines", return_value=df):
            cond = _supertrend_flip_up_entry()
            assert cond(make_account()) is False

    def test_entry_on_flip_up_bar(self):
        """Down hard, then up hard → final bar is a flip_up → enter."""
        from strategies.supertrend_long_call import _supertrend_flip_up_entry
        # Use small period/multiplier to force the regime change quickly.
        with patch("strategies.supertrend_long_call.ST_PERIOD", 3), \
             patch("strategies.supertrend_long_call.ST_MULTIPLIER", 1.0):
            closes = (
                [100.0, 100.5, 101.0, 101.5, 102.0, 102.5, 103.0, 103.5, 50.0]
                + [60.0, 70.0, 80.0, 200.0]  # final bar = flip up
            )
            df = _df_from_closes(closes)
            with patch("strategies.supertrend_long_call.fetch_klines", return_value=df):
                cond = _supertrend_flip_up_entry()
                assert cond(make_account()) is True

    def test_entry_dedupes_within_same_bar(self):
        """A second tick on the same flip-up bar must not re-enter."""
        from strategies.supertrend_long_call import _supertrend_flip_up_entry
        with patch("strategies.supertrend_long_call.ST_PERIOD", 3), \
             patch("strategies.supertrend_long_call.ST_MULTIPLIER", 1.0):
            closes = (
                [100.0, 100.5, 101.0, 101.5, 102.0, 102.5, 103.0, 103.5, 50.0]
                + [60.0, 70.0, 80.0, 200.0]
            )
            df = _df_from_closes(closes)
            cond = _supertrend_flip_up_entry()
            with patch("strategies.supertrend_long_call.fetch_klines", return_value=df):
                assert cond(make_account()) is True   # first tick → enter
                assert cond(make_account()) is False  # same bar → block


# ─── Exit Condition ─────────────────────────────────────────────────────────

class TestExitCondition:
    def _trade_stub(self):
        class T: pass
        t = T()
        t.id = "t-1"
        return t

    def test_no_exit_when_trend_up(self):
        from strategies.supertrend_long_call import _supertrend_trend_down_exit
        df = _df_from_closes([100.0 + i * 0.5 for i in range(50)])
        with patch("strategies.supertrend_long_call.fetch_klines", return_value=df):
            cond = _supertrend_trend_down_exit()
            assert cond(make_account(), self._trade_stub()) is False

    def test_exit_on_flip_down_bar(self):
        from strategies.supertrend_long_call import _supertrend_trend_down_exit
        with patch("strategies.supertrend_long_call.ST_PERIOD", 3), \
             patch("strategies.supertrend_long_call.ST_MULTIPLIER", 1.0):
            closes = [100.0, 100.5, 101.0, 101.5, 102.0, 102.5, 103.0,
                      103.5, 103.6, 50.0]
            df = _df_from_closes(closes)
            with patch("strategies.supertrend_long_call.fetch_klines", return_value=df):
                cond = _supertrend_trend_down_exit()
                assert cond(make_account(), self._trade_stub()) is True

    def test_exit_when_trend_down_post_restart(self):
        """After a restart the strategy has no in-memory flip history. The
        exit must still fire if latest trend is -1 (steady-state down)."""
        from strategies.supertrend_long_call import _supertrend_trend_down_exit
        with patch("strategies.supertrend_long_call.ST_PERIOD", 3), \
             patch("strategies.supertrend_long_call.ST_MULTIPLIER", 1.0):
            # Drop, then several flat bars below — trend stays at -1, no
            # flip_down on the final bar.
            closes = ([100.0, 100.5, 101.0, 101.5, 102.0, 102.5, 103.0,
                       103.5, 103.6, 50.0]
                      + [50.0] * 5)
            df = _df_from_closes(closes)
            with patch("strategies.supertrend_long_call.fetch_klines", return_value=df):
                cond = _supertrend_trend_down_exit()
                assert cond(make_account(), self._trade_stub()) is True

    def test_no_exit_when_no_data(self):
        from strategies.supertrend_long_call import _supertrend_trend_down_exit
        with patch("strategies.supertrend_long_call.fetch_klines", return_value=None):
            cond = _supertrend_trend_down_exit()
            assert cond(make_account(), self._trade_stub()) is False


# ─── Strategy Factory ───────────────────────────────────────────────────────

class TestFactory:
    def test_factory_returns_well_formed_config(self):
        from strategies.supertrend_long_call import (
            QTY,
            TARGET_DELTA,
            TARGET_DTE,
            supertrend_long_call,
        )

        cfg = supertrend_long_call()
        assert cfg.name == "supertrend_long_call"
        assert cfg.execution_mode == "limit"
        assert cfg.execution_profile == "supertrend_long_call"
        assert cfg.max_concurrent_trades == 1
        assert len(cfg.entry_conditions) == 1
        assert len(cfg.exit_conditions) == 1

        # Single long-call leg with delta strike + DTE-window expiry.
        assert len(cfg.legs) == 1
        leg = cfg.legs[0]
        assert leg.option_type == "C"
        assert leg.side == "buy"
        assert leg.qty == QTY
        assert leg.strike_criteria == {"type": "delta", "value": TARGET_DELTA}
        assert leg.expiry_criteria.get("dte") == TARGET_DTE
        assert "dte_min" in leg.expiry_criteria
        assert "dte_max" in leg.expiry_criteria

    def test_factory_metadata_includes_indicator_params(self):
        from strategies.supertrend_long_call import supertrend_long_call

        cfg = supertrend_long_call()
        meta = cfg.metadata
        assert meta["indicator"] == "supertrend"
        assert "period" in meta
        assert "multiplier" in meta


# ─── Closed-bar trimming ────────────────────────────────────────────────────

class TestClosedBarTrim:
    """Verify the strategy drops the still-forming current bar."""

    def test_drops_in_progress_bar(self, monkeypatch):
        from strategies.supertrend_long_call import _latest_st_signal

        # Stub now() so the LAST bar is "in progress" (less than 1h old).
        bars = pd.date_range(end="2026-04-28 12:00", periods=20, freq="1h", tz="UTC")
        df = pd.DataFrame({"close": list(range(100, 120))}, index=bars)

        # "now" = 12:30 → last bar (12:00) is still forming.
        fake_now = pd.Timestamp("2026-04-28 12:30", tz="UTC")
        monkeypatch.setattr(
            pd.Timestamp, "now",
            staticmethod(lambda tz=None: fake_now if tz else fake_now),
        )
        with patch("strategies.supertrend_long_call.fetch_klines", return_value=df):
            sig = _latest_st_signal()
            assert sig is not None
            # bar_ts should be the SECOND-LAST input bar (11:00), not 12:00.
            assert sig["bar_ts"] == bars[-2]
