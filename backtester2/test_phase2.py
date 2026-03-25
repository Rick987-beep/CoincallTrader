#!/usr/bin/env python3
"""Quick smoke test for market_replay.py and strategy_base.py."""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtester2.market_replay import MarketReplay
from backtester2.strategy_base import (
    Trade, OpenPosition, time_window, weekday_only, index_move_trigger,
    max_hold_hours, close_trade,
)

SNAP = os.path.join(os.path.dirname(__file__), "snapshots", "options_20260309_20260323.parquet")
SPOT = os.path.join(os.path.dirname(__file__), "snapshots", "spot_track_20260309_20260323.parquet")


def test_load_and_iterate():
    t0 = time.time()
    replay = MarketReplay(SNAP, SPOT)
    load_time = time.time() - t0
    print(f"Load time: {load_time:.2f}s")
    print(f"Time range: {replay.time_range}")
    print(f"Steps: {len(replay)}")
    print()

    # First 3 states
    for i, state in enumerate(replay):
        if i >= 3:
            break
        print(f"--- State {i} ---")
        print(f"  ts: {state.dt}")
        print(f"  spot: ${state.spot:,.0f}")
        print(f"  expiries: {state.expiries()}")
        print(f"  spot_bars: {len(state.spot_bars)}")

        exp = state.expiries()[0]
        atm = state.get_atm_strike(exp)
        print(f"  ATM strike ({exp}): {atm}")

        call, put = state.get_straddle(exp)
        if call and put:
            print(f"  ATM call: bid=${call.bid_usd:.2f} ask=${call.ask_usd:.2f} delta={call.delta:.3f}")
            print(f"  ATM put:  bid=${put.bid_usd:.2f} ask=${put.ask_usd:.2f} delta={put.delta:.3f}")
            print(f"  Straddle ask (entry): ${call.ask_usd + put.ask_usd:.2f}")

        chain = state.get_chain(exp)
        print(f"  Chain size ({exp}): {len(chain)} quotes")

        oc, op = state.get_strangle(exp, 1000)
        if oc and op:
            print(f"  1000-offset strangle: call K={oc.strike:.0f} put K={op.strike:.0f}")

    print()

    # Full iteration speed
    t0 = time.time()
    count = 0
    last_state = None
    for state in replay:
        count += 1
        last_state = state
    elapsed = time.time() - t0
    print(f"Full iteration: {count} states in {elapsed:.2f}s ({count/elapsed:.0f} states/s)")

    # Excursion test
    if last_state:
        first_ts = replay.timestamps[0]
        print(f"Spot high since start: ${last_state.spot_high_since(first_ts):,.0f}")
        print(f"Spot low since start:  ${last_state.spot_low_since(first_ts):,.0f}")

    print()


def test_strategy_base():
    print("=== strategy_base tests ===")

    # Test condition helpers
    replay = MarketReplay(SNAP, SPOT)
    states = []
    for i, state in enumerate(replay):
        states.append(state)
        if i >= 50:
            break

    # time_window
    tw = time_window(8, 16)
    results = [(s.dt.hour, tw(s)) for s in states[:20]]
    print(f"time_window(8,16): {results[:5]}")

    # weekday_only
    wd = weekday_only()
    print(f"weekday_only (first state): day={states[0].dt.strftime('%A')} -> {wd(states[0])}")

    # index_move_trigger with a mock OpenPosition
    state = states[20]
    pos = OpenPosition(
        entry_time=states[0].dt,
        entry_spot=states[0].spot,
        legs=[],
        entry_price_usd=100.0,
        fees_open=0.5,
        metadata={"direction": "buy"},
    )
    trigger_100 = index_move_trigger(100)
    trigger_10000 = index_move_trigger(10000)
    r1 = trigger_100(state, pos)
    r2 = trigger_10000(state, pos)
    print(f"index_move_trigger(100): {r1} (spot diff=${abs(state.spot - pos.entry_spot):.0f})")
    print(f"index_move_trigger(10000): {r2}")

    # max_hold_hours
    mh = max_hold_hours(1)
    r3 = mh(states[0], pos)
    r4 = mh(states[20], pos)
    held = (states[20].dt - pos.entry_time).total_seconds() / 3600
    print(f"max_hold_hours(1): state[0]={r3}, state[20]={r4} (held {held:.1f}h)")

    # close_trade helper
    trade = close_trade(states[20], pos, "trigger", current_usd=120.0, fees_close=0.5)
    print(f"close_trade: pnl=${trade.pnl:.2f}, exit_reason={trade.exit_reason}, triggered={trade.triggered}")

    print()
    print("All tests passed!")


if __name__ == "__main__":
    test_load_and_iterate()
    test_strategy_base()
