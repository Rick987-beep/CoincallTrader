#!/usr/bin/env python3
"""
Test 8a: Dry-run  — full pipeline minus order placement.
Test 8b: Micro-trade — real order on production, minimal qty.

Both hit the live API.  Test 8b places a real order (0.01 contract of a
deep OTM BTC call ≈ $0.95 premium) and closes immediately.

Run:
    python3 tests/test_live_dry_run.py          # 8a + 8b
    python3 tests/test_live_dry_run.py --dry     # 8a only
"""

import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from account_manager import AccountSnapshot, PositionSnapshot
from trade_lifecycle import (
    LifecycleManager, TradeLifecycle, TradeLeg, TradeState,
    max_hold_hours,
)
from option_selection import LegSpec
from strategy import (
    StrategyConfig, StrategyRunner, TradingContext, build_context,
)

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Results tracker ──────────────────────────────────────────────────────────
_results = []


def record(name, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    _results.append((name, passed, detail))
    symbol = "✓" if passed else "✗"
    print(f"  {symbol} {name}" + (f"  ({detail})" if detail else ""))


# ── Config: deep OTM instruments for minimum cost ───────────────────────────
# 27MAR26-100000-C: strike 100k vs spot ~69k, mark ~$91, ask ~$95
# Max loss at 0.01 qty = $0.95. Completely disposable.
MICRO_SYMBOL = "BTCUSD-27MAR26-100000-C"
MICRO_QTY = 0.01


# =============================================================================
# TEST 8a — Dry-Run
# =============================================================================

def test_8a_dry_run():
    """
    Build a strangle strategy with dry_run=True.
    Trigger one tick → verify dry-run result has leg details, prices,
    and that zero orders were placed.
    """
    print("\n" + "=" * 60)
    print("Test 8a: Dry-Run (full pipeline, no orders)")
    print("=" * 60)

    ctx = build_context()

    # Two-leg strangle (deep OTM both sides)
    config = StrategyConfig(
        name="test_8a_dry_strangle",
        legs=[
            LegSpec(
                side=1,
                qty=MICRO_QTY,
                option_type="C",
                expiry_criteria={"symbol": "27MAR26"},
                strike_criteria={"type": "closestStrike", "value": 100000},
            ),
            LegSpec(
                side=1,
                qty=MICRO_QTY,
                option_type="P",
                expiry_criteria={"symbol": "27MAR26"},
                strike_criteria={"type": "closestStrike", "value": 50000},
            ),
        ],
        entry_conditions=[],           # no gates — always enter
        exit_conditions=[max_hold_hours(1)],
        execution_mode="limit",
        check_interval_seconds=0,       # no throttle for test
        dry_run=True,
    )

    runner = StrategyRunner(config, ctx)

    # 1. Runner is in dry-run mode
    record("runner.dry_run is True", runner.dry_run is True)
    record("no dry run result yet", runner.last_dry_run_result is None)

    # 2. Fetch a live account snapshot
    acct = ctx.position_monitor.snapshot()
    record("got account snapshot", acct is not None and acct.equity > 0,
           f"equity=${acct.equity:,.2f}" if acct else "no snapshot")
    if not acct:
        print("  ✗ SKIPPING remaining 8a checks — no account data")
        return

    # 3. Fire one tick — should resolve legs, fetch orderbooks, NOT place orders
    runner.tick(acct)
    result = runner.last_dry_run_result

    record("dry run result populated", result is not None)
    if not result:
        print("  ✗ SKIPPING remaining 8a checks — dry-run produced no result")
        return

    # 4. Validate result contents
    record("has legs", "legs" in result and len(result["legs"]) == 2,
           f"legs={len(result.get('legs', []))}")

    # Check each leg has market data
    for i, linfo in enumerate(result.get("legs", [])):
        has_prices = "mark_price" in linfo and "best_bid" in linfo and "best_ask" in linfo
        no_error = "error" not in linfo
        label = f"leg[{i}] {linfo.get('symbol', '?')}"
        record(f"{label} has prices", has_prices and no_error,
               f"mark={linfo.get('mark_price')}, bid={linfo.get('best_bid')}, ask={linfo.get('best_ask')}")

    record("total_notional > 0", result.get("total_notional", 0) > 0,
           f"${result.get('total_notional', 0):.4f}")
    record("execution_mode resolved", result.get("execution_mode") is not None,
           result.get("execution_mode"))
    record("would_open flag", result.get("would_open") is True)

    # 5. Confirm no trades were actually created in the lifecycle manager
    trades = ctx.lifecycle_manager.get_trades_for_strategy("test_8a_dry_strangle")
    record("zero real trades created", len(trades) == 0,
           f"trades={len(trades)}")

    print(f"\n  Dry-run summary: {len(result['legs'])} legs, "
          f"notional=${result['total_notional']:.4f}, mode={result['execution_mode']}")


# =============================================================================
# TEST 8b — Micro-Trade (real order, 0.01 lot, ~$0.95 premium)
# =============================================================================

def test_8b_micro_trade():
    """
    Open a real single-leg trade (0.01x deep OTM call) through the
    lifecycle manager, wait for fill, then close immediately.
    Validates the full lifecycle:
        PENDING_OPEN → OPENING → OPEN → PENDING_CLOSE → CLOSING → CLOSED
    """
    print("\n" + "=" * 60)
    print("Test 8b: Micro-Trade (real order, ~$0.95 max cost)")
    print("=" * 60)

    ctx = build_context()

    # Pre-flight: verify orderbook has liquidity
    from market_data import get_option_orderbook
    ob = get_option_orderbook(MICRO_SYMBOL)
    if not ob:
        record("orderbook available", False, "no orderbook data")
        print("  ✗ ABORTING 8b — cannot confirm liquidity")
        return

    # Orderbook response: {"bids": [{"price": "50", "size": "18.18"}], "asks": [...]}
    ask_price = float(ob['asks'][0]['price']) if ob.get('asks') else 0.0
    bid_price = float(ob['bids'][0]['price']) if ob.get('bids') else 0.0
    record("orderbook has ask", ask_price > 0, f"ask=${ask_price}")
    record("orderbook has bid", bid_price > 0, f"bid=${bid_price}")

    if ask_price <= 0:
        print("  ✗ ABORTING 8b — no ask price, cannot safely buy")
        return

    max_cost = ask_price * MICRO_QTY
    print(f"\n  Instrument: {MICRO_SYMBOL}")
    print(f"  Qty: {MICRO_QTY}")
    print(f"  Ask: ${ask_price}  Bid: ${bid_price}")
    print(f"  Max open cost: ${max_cost:.4f}")
    print(f"  Expected round-trip loss: ~${(ask_price - bid_price) * MICRO_QTY:.4f}")
    print()

    # Sanity guard: abort if the ask price is unexpectedly high
    if max_cost > 5.0:
        record("cost sanity check", False, f"${max_cost:.2f} > $5 safety cap")
        print("  ✗ ABORTING 8b — cost exceeds $5 safety cap")
        return
    record("cost sanity check", True, f"${max_cost:.4f} < $5 cap")

    # ── Create & open trade via lifecycle manager ────────────────────────
    lm = ctx.lifecycle_manager

    # Exit condition: always true → close as soon as the fill is detected
    def always_exit(acct: AccountSnapshot, trade: TradeLifecycle) -> bool:
        return True
    always_exit.__name__ = "always_exit"

    trade = lm.create(
        legs=[
            TradeLeg(symbol=MICRO_SYMBOL, qty=MICRO_QTY, side=1),
        ],
        exit_conditions=[always_exit],
        execution_mode="limit",
        strategy_id="test_8b_micro",
        metadata={"test": True},
    )

    trade_id = trade.id
    record("trade created", trade.state == TradeState.PENDING_OPEN,
           f"id={trade_id}, state={trade.state.value}")

    # Open (places the order)
    ok = lm.open(trade_id)
    record("open() returned True", ok is True)
    record("state is OPENING", trade.state == TradeState.OPENING,
           trade.state.value)

    if not ok:
        record("trade opened", False, f"open() failed: {trade.error}")
        print(f"  ✗ ABORTING 8b — could not place order: {trade.error}")
        return

    # Verify order ID was captured
    order_id = trade.open_legs[0].order_id
    record("order_id captured", order_id is not None and len(order_id) > 0,
           f"orderId={order_id}")

    # ── Poll for fills → exit → close fills (max 60s) ───────────────────
    # Need a live account snapshot for exit condition evaluation
    acct = ctx.position_monitor.snapshot()
    if not acct:
        # Fabricate a minimal snapshot so tick() doesn't crash
        acct = AccountSnapshot(
            equity=1000, available_margin=1000, initial_margin=0,
            maintenance_margin=0, unrealized_pnl=0, margin_utilization=0,
            positions=(), net_delta=0, net_gamma=0, net_theta=0, net_vega=0,
            timestamp=time.time(),
        )

    states_seen = [trade.state.value]
    start = time.time()
    timeout = 90  # seconds
    poll_interval = 3  # seconds

    print(f"\n  Polling lifecycle (timeout={timeout}s, interval={poll_interval}s)...")

    while time.time() - start < timeout:
        lm.tick(acct)

        if trade.state.value not in states_seen:
            states_seen.append(trade.state.value)
            elapsed = time.time() - start
            print(f"    [{elapsed:5.1f}s] → {trade.state.value}")

        if trade.state in (TradeState.CLOSED, TradeState.FAILED):
            break

        time.sleep(poll_interval)

    elapsed = time.time() - start

    # ── Validate final state ─────────────────────────────────────────────
    record("trade reached terminal state",
           trade.state in (TradeState.CLOSED, TradeState.FAILED),
           f"state={trade.state.value} after {elapsed:.1f}s")

    record("states seen include OPENING", "opening" in states_seen)

    if trade.state == TradeState.CLOSED:
        record("lifecycle completed: CLOSED", True)
        record("states seen include open", "open" in states_seen)
        record("states seen include closing", "closing" in states_seen)

        # Check fill prices
        open_leg = trade.open_legs[0]
        record("open fill_price > 0",
               open_leg.fill_price is not None and open_leg.fill_price > 0,
               f"${open_leg.fill_price}")
        record("open filled_qty == micro_qty",
               open_leg.filled_qty >= MICRO_QTY,
               f"{open_leg.filled_qty}")

        if trade.close_legs:
            close_leg = trade.close_legs[0]
            record("close fill_price > 0",
                   close_leg.fill_price is not None and close_leg.fill_price > 0,
                   f"${close_leg.fill_price}")
            record("close filled_qty == micro_qty",
                   close_leg.filled_qty >= MICRO_QTY,
                   f"{close_leg.filled_qty}")

            # Round-trip cost
            if open_leg.fill_price and close_leg.fill_price:
                pnl = (close_leg.fill_price - open_leg.fill_price) * MICRO_QTY
                print(f"\n  Round-trip PnL: ${pnl:.4f} "
                      f"(bought @ ${open_leg.fill_price}, sold @ ${close_leg.fill_price})")
        else:
            record("close_legs populated", False, "no close legs")

    elif trade.state == TradeState.FAILED:
        record("lifecycle completed: FAILED", False, trade.error or "unknown error")
        # Attempt cleanup: cancel any dangling orders
        print(f"  Attempting cleanup of any open orders...")
        for leg in trade.open_legs:
            if leg.order_id and not leg.is_filled:
                try:
                    ctx.executor.cancel_order(leg.order_id)
                    print(f"    Cancelled order {leg.order_id}")
                except Exception as e:
                    print(f"    Failed to cancel {leg.order_id}: {e}")
    else:
        record("lifecycle completed in time", False,
               f"timed out in {trade.state.value} after {elapsed:.1f}s")
        # Timeout cleanup: cancel pending orders
        print(f"  Timed out — cancelling any open orders...")
        for leg in trade.open_legs + trade.close_legs:
            if leg.order_id and not leg.is_filled:
                try:
                    ctx.executor.cancel_order(leg.order_id)
                    print(f"    Cancelled order {leg.order_id}")
                except Exception as e:
                    print(f"    Failed to cancel {leg.order_id}: {e}")

    print(f"\n  State progression: {' → '.join(states_seen)}")


# =============================================================================
# Runner
# =============================================================================

def main():
    dry_only = "--dry" in sys.argv

    print("=" * 60)
    print("Live Tests: Dry-Run & Micro-Trade")
    print("=" * 60)

    test_8a_dry_run()

    if not dry_only:
        test_8b_micro_trade()
    else:
        print("\n  [--dry flag] Skipping Test 8b (micro-trade)")

    # Summary
    print("\n" + "=" * 60)
    passed = sum(1 for _, p, _ in _results if p)
    failed = sum(1 for _, p, _ in _results if not p)
    total = len(_results)
    print(f"Results: {passed}/{total} passed, {failed} failed")

    if failed > 0:
        print("\nFailed tests:")
        for name, p, detail in _results:
            if not p:
                print(f"  ✗ {name}  ({detail})")

    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
