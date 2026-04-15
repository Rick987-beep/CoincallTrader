"""
Unit tests for strategies/short_strangle_delta_tp.py (real strategy)
and the min_otm_pct addition to option_selection.py.

Tests cover:
    - strangle() with min_otm_pct embeds it in strike_criteria
    - _select_by_strike_criteria delta + min_otm_pct push logic
    - Strategy factory returns correct StrategyConfig
    - _combined_tp() exit condition (trigger / no-trigger / disabled)
    - _combined_sl() exit condition (unchanged from original)

All tests are pure — no network, no exchange calls.
"""

import time
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

from option_selection import strangle, _select_by_strike_criteria


# ═════════════════════════════════════════════════════════════════════════════
# strangle() — min_otm_pct kwarg
# ═════════════════════════════════════════════════════════════════════════════

class TestStrangleMinOtm:
    def test_no_min_otm_by_default(self):
        legs = strangle(qty=0.5, call_delta=0.15, put_delta=-0.15, dte=2, side="sell")
        for leg in legs:
            assert "min_otm_pct" not in leg.strike_criteria

    def test_min_otm_embedded_in_strike_criteria(self):
        legs = strangle(qty=0.5, call_delta=0.15, put_delta=-0.15, dte=2, side="sell", min_otm_pct=3)
        for leg in legs:
            assert leg.strike_criteria["min_otm_pct"] == 3

    def test_min_otm_zero_not_embedded(self):
        legs = strangle(qty=0.5, call_delta=0.15, put_delta=-0.15, dte=2, side="sell", min_otm_pct=0)
        for leg in legs:
            assert "min_otm_pct" not in leg.strike_criteria


# ═════════════════════════════════════════════════════════════════════════════
# _select_by_strike_criteria — delta + min_otm_pct
# ═════════════════════════════════════════════════════════════════════════════

def _make_option(symbol, strike, delta):
    return {"symbolName": symbol, "strike": strike, "delta": delta}


class FakeMarketDataForStrike:
    def __init__(self, index_price=85000.0):
        self._index_price = index_price

    def get_index_price(self, underlying="BTC"):
        return self._index_price


class TestDeltaMinOtmPush:
    """Test that min_otm_pct pushes strikes further OTM when needed."""

    def test_call_already_far_enough(self):
        # spot=85000, min_otm=3% → floor = 87550
        # strike 88000 >= 87550 → no push
        options = [
            _make_option("BTC-12APR26-86000-C", 86000, 0.30),
            _make_option("BTC-12APR26-88000-C", 88000, 0.15),
            _make_option("BTC-12APR26-90000-C", 90000, 0.08),
        ]
        md = FakeMarketDataForStrike(85000)
        criteria = {"type": "delta", "value": 0.15, "min_otm_pct": 3}
        result = _select_by_strike_criteria(options, criteria, md)
        assert result["strike"] == 88000

    def test_call_pushed_when_too_close(self):
        # spot=85000, min_otm=3% → floor = 87550
        # delta=0.30 → picks 86000 (closest delta), but 86000 < 87550
        # should push to 88000 (nearest >= 87550)
        options = [
            _make_option("BTC-12APR26-86000-C", 86000, 0.30),
            _make_option("BTC-12APR26-88000-C", 88000, 0.15),
            _make_option("BTC-12APR26-90000-C", 90000, 0.08),
        ]
        md = FakeMarketDataForStrike(85000)
        criteria = {"type": "delta", "value": 0.30, "min_otm_pct": 3}
        result = _select_by_strike_criteria(options, criteria, md)
        assert result["strike"] == 88000

    def test_put_already_far_enough(self):
        # spot=85000, min_otm=3% → ceil = 82450
        # strike 82000 <= 82450 → no push
        options = [
            _make_option("BTC-12APR26-84000-P", 84000, -0.30),
            _make_option("BTC-12APR26-82000-P", 82000, -0.15),
            _make_option("BTC-12APR26-80000-P", 80000, -0.08),
        ]
        md = FakeMarketDataForStrike(85000)
        criteria = {"type": "delta", "value": -0.15, "min_otm_pct": 3}
        result = _select_by_strike_criteria(options, criteria, md)
        assert result["strike"] == 82000

    def test_put_pushed_when_too_close(self):
        # spot=85000, min_otm=3% → ceil = 82450
        # delta=-0.30 → picks 84000 (closest delta), but 84000 > 82450
        # should push to 82000 (highest <= 82450)
        options = [
            _make_option("BTC-12APR26-84000-P", 84000, -0.30),
            _make_option("BTC-12APR26-82000-P", 82000, -0.15),
            _make_option("BTC-12APR26-80000-P", 80000, -0.08),
        ]
        md = FakeMarketDataForStrike(85000)
        criteria = {"type": "delta", "value": -0.30, "min_otm_pct": 3}
        result = _select_by_strike_criteria(options, criteria, md)
        assert result["strike"] == 82000

    def test_push_returns_none_if_no_qualifying_strike(self):
        # spot=85000, min_otm=10% → floor = 93500
        # no call strike >= 93500
        options = [
            _make_option("BTC-12APR26-86000-C", 86000, 0.30),
            _make_option("BTC-12APR26-88000-C", 88000, 0.15),
        ]
        md = FakeMarketDataForStrike(85000)
        criteria = {"type": "delta", "value": 0.30, "min_otm_pct": 10}
        result = _select_by_strike_criteria(options, criteria, md)
        assert result is None

    def test_no_push_when_min_otm_zero(self):
        # min_otm_pct=0 → disabled, closest delta wins
        options = [
            _make_option("BTC-12APR26-85500-C", 85500, 0.45),
            _make_option("BTC-12APR26-88000-C", 88000, 0.15),
        ]
        md = FakeMarketDataForStrike(85000)
        criteria = {"type": "delta", "value": 0.45}
        result = _select_by_strike_criteria(options, criteria, md)
        assert result["strike"] == 85500

    def test_no_push_without_market_data(self):
        # No market_data → skip push, return delta selection
        options = [
            _make_option("BTC-12APR26-85500-C", 85500, 0.45),
            _make_option("BTC-12APR26-88000-C", 88000, 0.15),
        ]
        criteria = {"type": "delta", "value": 0.45, "min_otm_pct": 3}
        result = _select_by_strike_criteria(options, criteria, None)
        assert result["strike"] == 85500


# ═════════════════════════════════════════════════════════════════════════════
# Strategy factory — short_strangle_delta_tp()
# ═════════════════════════════════════════════════════════════════════════════

class TestStrategyFactory:
    @patch.dict("os.environ", {
        "PARAM_QTY": "0.5",
        "PARAM_DTE": "2",
        "PARAM_DELTA": "0.15",
        "PARAM_ENTRY_HOUR": "12",
        "PARAM_STOP_LOSS_PCT": "3.0",
        "PARAM_TAKE_PROFIT_PCT": "0.6",
        "PARAM_MAX_HOLD_HOURS": "0",
        "PARAM_MIN_OTM_PCT": "3",
        "PARAM_WEEKEND_FILTER": "1",
        "PARAM_CHECK_INTERVAL": "15",
    })
    def test_factory_returns_strategy_config(self):
        # Re-import with env vars set (module-level params read at import)
        import importlib
        mod = importlib.import_module("strategies.short_strangle_delta_tp")
        importlib.reload(mod)

        config = mod.short_strangle_delta_tp()
        assert config.name == "short_strangle_delta_tp"
        assert len(config.legs) == 2
        assert config.max_trades_per_day == 1
        assert config.execution_mode == "limit"

    @patch.dict("os.environ", {
        "PARAM_TAKE_PROFIT_PCT": "0.6",
        "PARAM_STOP_LOSS_PCT": "3.0",
    })
    def test_tp_and_sl_exit_conditions_present(self):
        import importlib
        mod = importlib.import_module("strategies.short_strangle_delta_tp")
        importlib.reload(mod)

        config = mod.short_strangle_delta_tp()
        names = [ec.__name__ for ec in config.exit_conditions]
        assert any("tp" in n for n in names)
        assert any("sl" in n for n in names)


# ═════════════════════════════════════════════════════════════════════════════
# _combined_tp() exit condition
# ═════════════════════════════════════════════════════════════════════════════

def _mock_trade(call_fill=0.003, put_fill=0.002):
    """Build a mock trade with two open legs."""
    call_leg = SimpleNamespace(symbol="BTC-12APR26-88000-C", fill_price=call_fill)
    put_leg  = SimpleNamespace(symbol="BTC-12APR26-82000-P", fill_price=put_fill)
    trade = MagicMock()
    trade.open_legs = [call_leg, put_leg]
    trade.metadata = {}
    trade.id = "test-trade-1"
    return trade


class TestCombinedTp:
    @patch.dict("os.environ", {"PARAM_TAKE_PROFIT_PCT": "0.6", "PARAM_STOP_LOSS_PCT": "3.0"})
    def test_tp_triggers_when_ask_drops_enough(self):
        import importlib
        mod = importlib.import_module("strategies.short_strangle_delta_tp")
        importlib.reload(mod)

        check = mod._combined_tp()
        trade = _mock_trade(call_fill=0.003, put_fill=0.002)
        # combined_premium = 0.005
        # TP at 60%: trigger when combined_ask ≤ 0.005 × 0.40 = 0.002

        # Mock _fair to return ask prices.  0.001 call + 0.0008 put = 0.0018 < 0.002
        with patch.object(mod, "_fair") as mock_fair:
            mock_fair.side_effect = lambda sym: {
                "fair": 0.001, "bid": 0.0008, "ask": 0.001, "mark": 0.001, "index_price": 85000
            } if "C" in sym else {
                "fair": 0.0008, "bid": 0.0006, "ask": 0.0008, "mark": 0.0008, "index_price": 85000
            }
            result = check(MagicMock(), trade)
        assert result is True
        assert trade.metadata.get("tp_triggered") is True

    @patch.dict("os.environ", {"PARAM_TAKE_PROFIT_PCT": "0.6", "PARAM_STOP_LOSS_PCT": "3.0"})
    def test_tp_does_not_trigger_when_ask_high(self):
        import importlib
        mod = importlib.import_module("strategies.short_strangle_delta_tp")
        importlib.reload(mod)

        check = mod._combined_tp()
        trade = _mock_trade(call_fill=0.003, put_fill=0.002)
        # combined = 0.005, threshold at 60% → ask <= 0.002
        # combined_ask = 0.004 → profit_ratio = 0.2 < 0.6  → no trigger
        with patch.object(mod, "_fair") as mock_fair:
            mock_fair.return_value = {
                "fair": 0.002, "bid": 0.0018, "ask": 0.002, "mark": 0.002, "index_price": 85000
            }
            result = check(MagicMock(), trade)
        assert result is False
        assert "tp_triggered" not in trade.metadata

    @patch.dict("os.environ", {"PARAM_TAKE_PROFIT_PCT": "0", "PARAM_STOP_LOSS_PCT": "3.0"})
    def test_tp_disabled_when_zero(self):
        import importlib
        mod = importlib.import_module("strategies.short_strangle_delta_tp")
        importlib.reload(mod)

        check = mod._combined_tp()
        trade = _mock_trade()
        result = check(MagicMock(), trade)
        assert result is False

    @patch.dict("os.environ", {"PARAM_TAKE_PROFIT_PCT": "0.6", "PARAM_STOP_LOSS_PCT": "3.0"})
    def test_tp_skips_when_ask_missing(self):
        import importlib
        mod = importlib.import_module("strategies.short_strangle_delta_tp")
        importlib.reload(mod)

        check = mod._combined_tp()
        trade = _mock_trade(call_fill=0.003, put_fill=0.002)
        # _fair returns None for call → skip
        with patch.object(mod, "_fair") as mock_fair:
            mock_fair.side_effect = lambda sym: None if "C" in sym else {
                "fair": 0.001, "bid": 0.001, "ask": 0.001, "mark": 0.001, "index_price": 85000
            }
            result = check(MagicMock(), trade)
        assert result is False

    @patch.dict("os.environ", {"PARAM_TAKE_PROFIT_PCT": "0.6", "PARAM_STOP_LOSS_PCT": "3.0"})
    def test_tp_skips_when_ask_is_none(self):
        import importlib
        mod = importlib.import_module("strategies.short_strangle_delta_tp")
        importlib.reload(mod)

        check = mod._combined_tp()
        trade = _mock_trade(call_fill=0.003, put_fill=0.002)
        # _fair returns data but ask=None
        with patch.object(mod, "_fair") as mock_fair:
            mock_fair.return_value = {
                "fair": 0.001, "bid": 0.001, "ask": None, "mark": 0.001, "index_price": 85000
            }
            result = check(MagicMock(), trade)
        assert result is False


# ═════════════════════════════════════════════════════════════════════════════
# _combined_sl() exit condition — smoke test (logic unchanged from original)
# ═════════════════════════════════════════════════════════════════════════════

class TestCombinedSl:
    @patch.dict("os.environ", {"PARAM_STOP_LOSS_PCT": "1.0", "PARAM_TAKE_PROFIT_PCT": "0.6"})
    def test_sl_triggers_when_fair_exceeds_threshold(self):
        import importlib
        mod = importlib.import_module("strategies.short_strangle_delta_tp")
        importlib.reload(mod)

        check = mod._combined_sl()
        trade = _mock_trade(call_fill=0.003, put_fill=0.002)
        # premium=0.005, SL@100% → threshold=0.010
        # fair=0.006+0.005=0.011 >= 0.010 → trigger
        with patch.object(mod, "_fair") as mock_fair:
            mock_fair.side_effect = lambda sym: {
                "fair": 0.006, "bid": 0.005, "ask": 0.007, "mark": 0.006, "index_price": 85000
            } if "C" in sym else {
                "fair": 0.005, "bid": 0.004, "ask": 0.006, "mark": 0.005, "index_price": 85000
            }
            result = check(MagicMock(), trade)
        assert result is True
        assert trade.metadata.get("sl_triggered") is True

    @patch.dict("os.environ", {"PARAM_STOP_LOSS_PCT": "1.0", "PARAM_TAKE_PROFIT_PCT": "0.6"})
    def test_sl_does_not_trigger_below_threshold(self):
        import importlib
        mod = importlib.import_module("strategies.short_strangle_delta_tp")
        importlib.reload(mod)

        check = mod._combined_sl()
        trade = _mock_trade(call_fill=0.003, put_fill=0.002)
        # premium=0.005, SL@100% → threshold=0.010
        # fair=0.003+0.003=0.006 < 0.010 → no trigger
        with patch.object(mod, "_fair") as mock_fair:
            mock_fair.return_value = {
                "fair": 0.003, "bid": 0.002, "ask": 0.004, "mark": 0.003, "index_price": 85000
            }
            result = check(MagicMock(), trade)
        assert result is False
