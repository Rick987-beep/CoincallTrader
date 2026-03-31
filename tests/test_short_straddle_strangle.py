"""
Unit tests for strategies/short_straddle_strangle.py and
the strangle_by_offset() addition to option_selection.py.

All tests are pure — no network, no exchange calls.
"""

import time
from unittest.mock import patch, MagicMock

import pytest

from option_selection import strangle_by_offset, straddle, LegSpec


# ═════════════════════════════════════════════════════════════════════════════
# strangle_by_offset() — option_selection addition
# ═════════════════════════════════════════════════════════════════════════════

class TestStrangleByOffset:
    def test_returns_two_legs(self):
        legs = strangle_by_offset(qty=1, offset=1000)
        assert len(legs) == 2

    def test_call_and_put_types(self):
        legs = strangle_by_offset(qty=1, offset=1000)
        types = {l.option_type for l in legs}
        assert types == {"C", "P"}

    def test_call_uses_positive_offset(self):
        legs = strangle_by_offset(qty=1, offset=1000)
        call_leg = next(l for l in legs if l.option_type == "C")
        assert call_leg.strike_criteria == {"type": "spotOffset", "value": +1000}

    def test_put_uses_negative_offset(self):
        legs = strangle_by_offset(qty=1, offset=1000)
        put_leg = next(l for l in legs if l.option_type == "P")
        assert put_leg.strike_criteria == {"type": "spotOffset", "value": -1000}

    def test_qty_applied_to_both_legs(self):
        legs = strangle_by_offset(qty=3, offset=500)
        assert all(l.qty == 3 for l in legs)

    def test_side_applied_to_both_legs(self):
        legs = strangle_by_offset(qty=1, offset=1000, side="sell")
        assert all(l.side == "sell" for l in legs)

    def test_side_buy(self):
        legs = strangle_by_offset(qty=1, offset=500, side="buy")
        assert all(l.side == "buy" for l in legs)

    def test_zero_offset_uses_atm(self):
        legs = strangle_by_offset(qty=1, offset=0)
        call_leg = next(l for l in legs if l.option_type == "C")
        put_leg = next(l for l in legs if l.option_type == "P")
        assert call_leg.strike_criteria["value"] == 0
        assert put_leg.strike_criteria["value"] == 0

    def test_dte_forwarded(self):
        legs = strangle_by_offset(qty=1, offset=1000, dte=1)
        assert all(l.expiry_criteria == {"dte": 1} for l in legs)

    def test_dte_next_default(self):
        legs = strangle_by_offset(qty=1, offset=1000)
        assert all(l.expiry_criteria == {"dte": "next"} for l in legs)

    def test_underlying_forwarded(self):
        legs = strangle_by_offset(qty=1, offset=1000, underlying="ETH")
        assert all(l.underlying == "ETH" for l in legs)

    def test_different_offsets(self):
        for offset in [500, 1000, 1500, 2000]:
            legs = strangle_by_offset(qty=1, offset=offset)
            call_leg = next(l for l in legs if l.option_type == "C")
            put_leg = next(l for l in legs if l.option_type == "P")
            assert call_leg.strike_criteria["value"] == +offset
            assert put_leg.strike_criteria["value"] == -offset


# ═════════════════════════════════════════════════════════════════════════════
# _combined_sl() exit condition
# ═════════════════════════════════════════════════════════════════════════════

def _make_trade(call_fill=0.04, put_fill=0.03, call_symbol="BTC-1APR26-85000-C",
                put_symbol="BTC-1APR26-75000-P"):
    """Return a minimal mock TradeLifecycle with two open legs."""
    from trade_lifecycle import TradeLeg, TradeLifecycle, TradeState

    call_leg = TradeLeg(symbol=call_symbol, qty=1, side="sell",
                        fill_price=call_fill, filled_qty=1)
    put_leg = TradeLeg(symbol=put_symbol, qty=1, side="sell",
                       fill_price=put_fill, filled_qty=1)
    trade = TradeLifecycle(state=TradeState.OPEN, open_legs=[call_leg, put_leg])
    return trade


def _make_account():
    from account_manager import AccountSnapshot
    return AccountSnapshot(
        equity=10000.0, available_margin=8000.0,
        initial_margin=2000.0, maintenance_margin=1000.0,
        unrealized_pnl=0.0, margin_utilization=20.0,
        positions=(), net_delta=0.0, net_gamma=0.0,
        net_theta=0.0, net_vega=0.0,
        timestamp=time.time(),
    )


def _mock_fair(call_fair, put_fair, call_sym, put_sym):
    """Patch get_option_market_data to return controlled bid/ask/mark."""
    def side_effect(symbol):
        if symbol == call_sym:
            return {"bid": call_fair * 0.95, "ask": call_fair * 1.05, "mark_price": call_fair}
        if symbol == put_sym:
            return {"bid": put_fair * 0.95, "ask": put_fair * 1.05, "mark_price": put_fair}
        return None
    return side_effect


class TestCombinedSL:
    def _get_sl_condition(self):
        import strategies.short_straddle_strangle as m
        return m._combined_sl()

    def test_does_not_trigger_below_threshold(self):
        cond = self._get_sl_condition()
        trade = _make_trade(call_fill=0.04, put_fill=0.03)  # combined = 0.07
        # fair = 0.05 + 0.04 = 0.09; threshold = 0.07 * (1 + 3.0) = 0.28 — not triggered
        with patch("strategies.short_straddle_strangle.get_option_market_data",
                   side_effect=_mock_fair(0.05, 0.04,
                                          "BTC-1APR26-85000-C", "BTC-1APR26-75000-P")):
            result = cond(_make_account(), trade)
        assert result is False

    def test_triggers_above_threshold(self):
        cond = self._get_sl_condition()
        trade = _make_trade(call_fill=0.04, put_fill=0.03)  # combined = 0.07
        # threshold = 0.07 * 4.0 = 0.28; fair = 0.20 + 0.09 = 0.29 → triggered
        with patch("strategies.short_straddle_strangle.get_option_market_data",
                   side_effect=_mock_fair(0.20, 0.09,
                                          "BTC-1APR26-85000-C", "BTC-1APR26-75000-P")):
            result = cond(_make_account(), trade)
        assert result is True

    def test_sets_sl_triggered_metadata(self):
        cond = self._get_sl_condition()
        trade = _make_trade(call_fill=0.04, put_fill=0.03)
        with patch("strategies.short_straddle_strangle.get_option_market_data",
                   side_effect=_mock_fair(0.20, 0.09,
                                          "BTC-1APR26-85000-C", "BTC-1APR26-75000-P")):
            cond(_make_account(), trade)
        assert trade.metadata.get("sl_triggered") is True

    def test_sl_threshold_stored_on_first_check(self):
        cond = self._get_sl_condition()
        trade = _make_trade(call_fill=0.04, put_fill=0.03)  # combined=0.07
        with patch("strategies.short_straddle_strangle.get_option_market_data",
                   side_effect=_mock_fair(0.02, 0.02,
                                          "BTC-1APR26-85000-C", "BTC-1APR26-75000-P")):
            cond(_make_account(), trade)
        # threshold = 0.07 * (1 + 3.0) = 0.28
        assert abs(trade.metadata["sl_threshold"] - 0.28) < 1e-9

    def test_combined_premium_stored(self):
        cond = self._get_sl_condition()
        trade = _make_trade(call_fill=0.04, put_fill=0.03)
        with patch("strategies.short_straddle_strangle.get_option_market_data",
                   side_effect=_mock_fair(0.02, 0.02,
                                          "BTC-1APR26-85000-C", "BTC-1APR26-75000-P")):
            cond(_make_account(), trade)
        assert abs(trade.metadata["combined_premium"] - 0.07) < 1e-9

    def test_returns_false_on_missing_market_data(self):
        cond = self._get_sl_condition()
        trade = _make_trade()
        with patch("strategies.short_straddle_strangle.get_option_market_data",
                   return_value=None):
            result = cond(_make_account(), trade)
        assert result is False

    def test_returns_false_with_fewer_than_two_legs(self):
        from trade_lifecycle import TradeLeg, TradeLifecycle, TradeState
        cond = self._get_sl_condition()
        single_leg = TradeLeg(symbol="BTC-1APR26-85000-C", qty=1, side="sell",
                               fill_price=0.04, filled_qty=1)
        trade = TradeLifecycle(state=TradeState.OPEN, open_legs=[single_leg])
        with patch("strategies.short_straddle_strangle.get_option_market_data",
                   return_value={"bid": 0.05, "ask": 0.07, "mark_price": 0.06}):
            result = cond(_make_account(), trade)
        assert result is False

    def test_sl_configures_execution_params_on_trigger(self):
        from trade_execution import ExecutionParams
        cond = self._get_sl_condition()
        trade = _make_trade(call_fill=0.04, put_fill=0.03)
        with patch("strategies.short_straddle_strangle.get_option_market_data",
                   side_effect=_mock_fair(0.20, 0.09,
                                          "BTC-1APR26-85000-C", "BTC-1APR26-75000-P")):
            cond(_make_account(), trade)
        assert isinstance(trade.execution_params, ExecutionParams)
        assert len(trade.execution_params.phases) == 2

    def test_threshold_not_recalculated_on_second_call(self):
        cond = self._get_sl_condition()
        trade = _make_trade(call_fill=0.04, put_fill=0.03)
        # First call: sets threshold to 0.28
        with patch("strategies.short_straddle_strangle.get_option_market_data",
                   side_effect=_mock_fair(0.02, 0.02,
                                          "BTC-1APR26-85000-C", "BTC-1APR26-75000-P")):
            cond(_make_account(), trade)
        first_threshold = trade.metadata["sl_threshold"]

        # Mutate fill price — threshold must NOT change
        trade.open_legs[0].fill_price = 0.99
        with patch("strategies.short_straddle_strangle.get_option_market_data",
                   side_effect=_mock_fair(0.02, 0.02,
                                          "BTC-1APR26-85000-C", "BTC-1APR26-75000-P")):
            cond(_make_account(), trade)
        assert trade.metadata["sl_threshold"] == first_threshold


# ═════════════════════════════════════════════════════════════════════════════
# _fair() price helper
# ═════════════════════════════════════════════════════════════════════════════

class TestFairHelper:
    def _call(self, bid, ask, mark):
        import strategies.short_straddle_strangle as m
        with patch("strategies.short_straddle_strangle.get_option_market_data",
                   return_value={"bid": bid, "ask": ask, "mark_price": mark}):
            return m._fair("FAKE-C")

    def test_mark_within_spread_uses_mark(self):
        result = self._call(bid=0.03, ask=0.05, mark=0.04)
        assert result["fair"] == pytest.approx(0.04)

    def test_mark_outside_spread_uses_mid(self):
        result = self._call(bid=0.03, ask=0.05, mark=0.06)  # mark above ask
        assert result["fair"] == pytest.approx(0.04)  # mid = (0.03+0.05)/2

    def test_bid_only_uses_max_mark_bid(self):
        result = self._call(bid=0.03, ask=0, mark=0.05)
        assert result["fair"] == pytest.approx(0.05)  # max(mark, bid)

    def test_bid_only_mark_below_uses_bid(self):
        result = self._call(bid=0.05, ask=0, mark=0.02)
        assert result["fair"] == pytest.approx(0.05)

    def test_no_book_uses_mark(self):
        result = self._call(bid=0, ask=0, mark=0.04)
        assert result["fair"] == pytest.approx(0.04)

    def test_no_data_returns_none(self):
        import strategies.short_straddle_strangle as m
        with patch("strategies.short_straddle_strangle.get_option_market_data",
                   return_value=None):
            result = m._fair("FAKE-C")
        assert result is None

    def test_all_zero_returns_none(self):
        result = self._call(bid=0, ask=0, mark=0)
        assert result is None


# ═════════════════════════════════════════════════════════════════════════════
# _build_legs() / strategy factory
# ═════════════════════════════════════════════════════════════════════════════

class TestBuildLegs:
    def test_nonzero_offset_uses_spot_offset(self):
        import strategies.short_straddle_strangle as m
        original_offset = m.OFFSET
        m.OFFSET = 1000
        try:
            legs = m._build_legs()
            assert len(legs) == 2
            call_leg = next(l for l in legs if l.option_type == "C")
            put_leg = next(l for l in legs if l.option_type == "P")
            assert call_leg.strike_criteria == {"type": "spotOffset", "value": +1000}
            assert put_leg.strike_criteria == {"type": "spotOffset", "value": -1000}
        finally:
            m.OFFSET = original_offset

    def test_zero_offset_uses_atm_straddle(self):
        import strategies.short_straddle_strangle as m
        original_offset = m.OFFSET
        m.OFFSET = 0
        try:
            legs = m._build_legs()
            assert len(legs) == 2
            # straddle uses closestStrike with value=0 for both legs
            for leg in legs:
                assert leg.strike_criteria == {"type": "closestStrike", "value": 0}
        finally:
            m.OFFSET = original_offset

    def test_build_legs_side_is_sell(self):
        import strategies.short_straddle_strangle as m
        legs = m._build_legs()
        assert all(l.side == "sell" for l in legs)


class TestStrategyFactory:
    def test_returns_strategy_config(self):
        from strategy import StrategyConfig
        import strategies.short_straddle_strangle as m
        cfg = m.short_straddle_strangle()
        assert isinstance(cfg, StrategyConfig)

    def test_strategy_name(self):
        import strategies.short_straddle_strangle as m
        cfg = m.short_straddle_strangle()
        assert cfg.name == "short_straddle_strangle"

    def test_has_two_legs(self):
        import strategies.short_straddle_strangle as m
        cfg = m.short_straddle_strangle()
        assert len(cfg.legs) == 2

    def test_execution_mode_is_limit(self):
        import strategies.short_straddle_strangle as m
        cfg = m.short_straddle_strangle()
        assert cfg.execution_mode == "limit"

    def test_has_two_exit_conditions(self):
        import strategies.short_straddle_strangle as m
        cfg = m.short_straddle_strangle()
        assert len(cfg.exit_conditions) == 2

    def test_max_trades_per_day(self):
        import strategies.short_straddle_strangle as m
        cfg = m.short_straddle_strangle()
        assert cfg.max_trades_per_day == 1

    def test_open_execution_has_two_phases(self):
        from trade_execution import ExecutionParams
        import strategies.short_straddle_strangle as m
        cfg = m.short_straddle_strangle()
        assert isinstance(cfg.execution_params, ExecutionParams)
        assert len(cfg.execution_params.phases) == 2

    def test_phase2_is_aggressive(self):
        import strategies.short_straddle_strangle as m
        cfg = m.short_straddle_strangle()
        phase2 = cfg.execution_params.phases[1]
        assert phase2.fair_aggression == 1.0

    def test_callbacks_wired(self):
        import strategies.short_straddle_strangle as m
        cfg = m.short_straddle_strangle()
        assert cfg.on_trade_opened is not None
        assert cfg.on_trade_closed is not None
