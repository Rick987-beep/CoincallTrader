"""
Live test: Measure how fast Deribit populates the trade log after a fill.

Opens a market buy on the cheapest OTM option, then polls
`private/get_user_trades_by_order` in a tight loop (50ms interval)
to see how quickly the trade log entry appears.  Repeats for the
close (market sell).

Usage:
    EXCHANGE=deribit TRADING_ENVIRONMENT=testnet \
        python -m pytest tests/live/test_trade_log_latency.py -m live -v -s
"""

import os
import sys
import time
import pytest

pytestmark = pytest.mark.live

os.environ.setdefault("TRADING_ENVIRONMENT", "testnet")
os.environ.setdefault("EXCHANGE", "deribit")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─── Fixtures ────────────────────────────────────────────────────────────────

def _skip_if_no_creds():
    from config import DERIBIT_CLIENT_ID, DERIBIT_CLIENT_SECRET
    if not DERIBIT_CLIENT_ID or not DERIBIT_CLIENT_SECRET:
        pytest.skip("DERIBIT_CLIENT_ID / DERIBIT_CLIENT_SECRET not set")


@pytest.fixture(scope="module")
def auth():
    _skip_if_no_creds()
    from exchanges.deribit.auth import DeribitAuth
    return DeribitAuth()


@pytest.fixture(scope="module")
def executor(auth):
    from exchanges.deribit.executor import DeribitExecutorAdapter
    return DeribitExecutorAdapter(auth)


@pytest.fixture(scope="module")
def market_data(auth):
    from exchanges.deribit.market_data import DeribitMarketDataAdapter
    return DeribitMarketDataAdapter(auth)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _find_cheapest_option(market_data, min_dte_days=7):
    """Find an ATM call with ~60+ DTE for a liquid fill test."""
    instruments = market_data.get_option_instruments("BTC")
    assert instruments, "No BTC option instruments on testnet"

    index_price = market_data.get_index_price("BTC")
    assert index_price, "Could not get BTC index price"

    now = time.time() * 1000
    min_dte_ms = min_dte_days * 24 * 3600 * 1000

    # Filter: calls only, >min_dte DTE
    candidates = [
        i for i in instruments
        if i.get("expirationTimestamp", 0) - now > min_dte_ms
        and i.get("symbolName", "").endswith("-C")
    ]
    assert candidates, f"No call options with >{min_dte_days} DTE"

    # Sort by distance from ATM (closest first)
    candidates.sort(key=lambda i: abs(i.get("strike", 0) - index_price))

    # Pick the closest-to-ATM call that has both bid and ask
    for inst in candidates[:20]:
        ob = market_data.get_option_orderbook(inst["symbolName"])
        if ob and ob.get("asks") and ob.get("bids"):
            ask = float(ob["asks"][0]["price"])
            bid = float(ob["bids"][0]["price"])
            if ask > 0 and bid > 0:
                print(f"  Selected: {inst['symbolName']}  "
                      f"(strike={inst.get('strike')}, index={index_price:.0f}, "
                      f"bid={bid:.4f}, ask={ask:.4f})")
                return inst["symbolName"], ask, bid

    pytest.skip("No liquid ATM call with bid+ask found on testnet")


def _poll_trade_log(auth, order_id, timeout=10.0, interval=0.05):
    """
    Poll private/get_user_trades_by_order until entries appear.

    Returns (trades_list, latency_seconds, poll_count).
    """
    start = time.monotonic()
    polls = 0

    while True:
        elapsed = time.monotonic() - start
        if elapsed > timeout:
            return [], elapsed, polls

        resp = auth.call("private/get_user_trades_by_order", {
            "order_id": order_id,
            "sorting": "asc",
        })

        polls += 1

        if auth.is_successful(resp):
            result = resp.get("result", [])
            # Deribit returns either a list of trades directly,
            # or a dict with {"trades": [...], "has_more": bool}
            if isinstance(result, list):
                trades = result
            elif isinstance(result, dict):
                trades = result.get("trades", [])
            else:
                trades = []
            if trades:
                latency = time.monotonic() - start
                return trades, latency, polls

        time.sleep(interval)


# ─── Test ────────────────────────────────────────────────────────────────────

class TestTradeLogLatency:
    """Measure trade log population latency on Deribit testnet."""

    def test_trade_log_latency_open_and_close(self, auth, executor, market_data):
        """
        1. Limit-buy 0.1 ATM call at the ask (should fill instantly)
        2. Poll trade log aggressively → measure latency
        3. Limit-sell 0.1 at the bid to close
        4. Poll trade log aggressively → measure latency
        5. Report both measurements
        """
        symbol, ask_price, bid_price = _find_cheapest_option(market_data, min_dte_days=50)
        print(f"\n{'='*60}")
        print(f"Trade Log Latency Test")
        print(f"Symbol: {symbol}")
        print(f"Ask: {ask_price:.4f} BTC  |  Bid: {bid_price:.4f} BTC")
        print(f"{'='*60}")

        # ── Step 1: Limit buy at ask (open) ─────────────────────────
        print(f"\n[OPEN] Placing limit buy 0.1x {symbol} @ {ask_price:.4f} (at ask)...")
        t_order_sent = time.monotonic()
        result = executor.place_order(
            symbol=symbol,
            qty=0.1,
            side="buy",
            order_type=1,  # limit
            price=ask_price,
        )
        t_order_returned = time.monotonic()
        order_latency = t_order_returned - t_order_sent

        assert result is not None, f"Market buy failed for {symbol}"
        open_order_id = result["orderId"]
        fill_qty = result.get("fillQty", 0)
        avg_price = result.get("avgPrice", 0)
        immediate_trades = result.get("_trades", [])

        print(f"[OPEN] Order response in {order_latency*1000:.0f}ms")
        print(f"[OPEN] order_id={open_order_id}, fillQty={fill_qty}, "
              f"avgPrice={avg_price:.4f}")
        print(f"[OPEN] Immediate _trades in response: {len(immediate_trades)}")

        # If order didn't fill immediately, wait briefly
        if fill_qty < 0.1:
            print("[OPEN] Waiting for fill...")
            for _ in range(20):
                time.sleep(0.25)
                status = executor.get_order_status(open_order_id)
                if status and status.get("fillQty", 0) >= 0.1:
                    fill_qty = status["fillQty"]
                    avg_price = status.get("avgPrice", 0)
                    break
            else:
                # Cancel unfilled order and skip
                executor.cancel_order(open_order_id)
                pytest.skip(f"Market buy didn't fill within 5s for {symbol}")

        print(f"[OPEN] Filled: {fill_qty} @ {avg_price:.4f} BTC")

        # ── Step 2: Poll trade log for open ──────────────────────────
        print(f"\n[OPEN] Polling trade log (50ms interval, 10s timeout)...")
        open_trades, open_latency, open_polls = _poll_trade_log(
            auth, open_order_id, timeout=10.0, interval=0.05
        )

        if open_trades:
            print(f"[OPEN] Trade log entry appeared after "
                  f"{open_latency*1000:.0f}ms ({open_polls} polls)")
            print(f"[OPEN] Trade log entries: {len(open_trades)}")
            for t in open_trades:
                print(f"       trade_id={t.get('trade_id')}, "
                      f"amount={t.get('amount')}, "
                      f"price={t.get('price')}, "
                      f"fee={t.get('fee')}, "
                      f"fee_currency={t.get('fee_currency')}")
        else:
            print(f"[OPEN] WARNING: Trade log empty after 10s ({open_polls} polls)")

        # ── Step 3: Limit sell at bid (close) ────────────────────────
        print(f"\n[CLOSE] Placing limit sell 0.1x {symbol} @ {bid_price:.4f} (at bid)...")
        t_close_sent = time.monotonic()
        close_result = executor.place_order(
            symbol=symbol,
            qty=0.1,
            side="sell",
            order_type=1,  # limit
            price=bid_price,
            reduce_only=True,
        )
        t_close_returned = time.monotonic()
        close_order_latency = t_close_returned - t_close_sent

        assert close_result is not None, f"Market sell failed for {symbol}"
        close_order_id = close_result["orderId"]
        close_fill_qty = close_result.get("fillQty", 0)
        close_avg_price = close_result.get("avgPrice", 0)
        close_immediate_trades = close_result.get("_trades", [])

        print(f"[CLOSE] Order response in {close_order_latency*1000:.0f}ms")
        print(f"[CLOSE] order_id={close_order_id}, fillQty={close_fill_qty}, "
              f"avgPrice={close_avg_price:.4f}")
        print(f"[CLOSE] Immediate _trades in response: {len(close_immediate_trades)}")

        # If close didn't fill immediately, wait briefly
        if close_fill_qty < 0.1:
            print("[CLOSE] Waiting for fill...")
            for _ in range(20):
                time.sleep(0.25)
                status = executor.get_order_status(close_order_id)
                if status and status.get("fillQty", 0) >= 0.1:
                    close_fill_qty = status["fillQty"]
                    close_avg_price = status.get("avgPrice", 0)
                    break
            else:
                print("[CLOSE] WARNING: Close didn't fill within 5s")

        print(f"[CLOSE] Filled: {close_fill_qty} @ {close_avg_price:.4f} BTC")

        # ── Step 4: Poll trade log for close ─────────────────────────
        print(f"\n[CLOSE] Polling trade log (50ms interval, 10s timeout)...")
        close_trades, close_latency, close_polls = _poll_trade_log(
            auth, close_order_id, timeout=10.0, interval=0.05
        )

        if close_trades:
            print(f"[CLOSE] Trade log entry appeared after "
                  f"{close_latency*1000:.0f}ms ({close_polls} polls)")
            print(f"[CLOSE] Trade log entries: {len(close_trades)}")
            for t in close_trades:
                print(f"        trade_id={t.get('trade_id')}, "
                      f"amount={t.get('amount')}, "
                      f"price={t.get('price')}, "
                      f"fee={t.get('fee')}, "
                      f"fee_currency={t.get('fee_currency')}")
        else:
            print(f"[CLOSE] WARNING: Trade log empty after 10s ({close_polls} polls)")

        # ── Step 5: Summary ──────────────────────────────────────────
        print(f"\n{'='*60}")
        print(f"SUMMARY")
        print(f"{'='*60}")
        print(f"Symbol:              {symbol}")
        print(f"Open order latency:  {order_latency*1000:.0f}ms")
        print(f"Open fill price:     {avg_price:.4f} BTC")
        print(f"Open trade log:      ", end="")
        if open_trades:
            print(f"{open_latency*1000:.0f}ms ({open_polls} polls)")
        else:
            print("NOT FOUND within 10s")
        print(f"Close order latency: {close_order_latency*1000:.0f}ms")
        print(f"Close fill price:    {close_avg_price:.4f} BTC")
        print(f"Close trade log:     ", end="")
        if close_trades:
            print(f"{close_latency*1000:.0f}ms ({close_polls} polls)")
        else:
            print("NOT FOUND within 10s")
        print(f"{'='*60}")

        # Also check: were _trades already in the order response?
        print(f"\nImmediate _trades in order response:")
        print(f"  Open:  {len(immediate_trades)} trade(s)")
        print(f"  Close: {len(close_immediate_trades)} trade(s)")
        if immediate_trades or close_immediate_trades:
            print(f"  → Fills are available IMMEDIATELY in the order response!")
            print(f"  → The separate trade log query may not even be needed.")

        # Assert we got at least the open trade log
        assert open_trades, "Open trade log never appeared within 10s"
