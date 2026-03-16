"""
Test 4: Deribit Order Management — Full Round Trip
====================================================
TESTNET ONLY — places real orders, modifies, cancels, and fills.

Tests:
  4a  Place a limit order far from market → read → modify → cancel
  4b  Place at best_ask to get filled → verify position → close → verify gone
  4c  Edge cases: reduce_only w/o position, below min size, expired instrument

Run:  python tests/deribit/test_deribit_orders.py
"""
import requests
import json
import sys
import time
from datetime import datetime, timezone

# ── Config (TESTNET ONLY) ──────────────────────────────────────────────
if "--prod" in sys.argv:
    print("ERROR: Test 4 is testnet-only.  Do NOT run with --prod.")
    sys.exit(1)

BASE_URL  = "https://test.deribit.com"
CLIENT_ID = "CWlZBUXA"
CLIENT_SECRET = "sVrL_Bdz-j8_mtLB-y4EdxPS-YGkqeMtLzh4Wi1sz2E"
ENV_LABEL = "TESTNET"
TOKEN = None

# ── Helpers ─────────────────────────────────────────────────────────────
def authenticate():
    global TOKEN
    resp = requests.post(
        f"{BASE_URL}/api/v2/public/auth",
        json={
            "jsonrpc": "2.0", "id": 1,
            "method": "public/auth",
            "params": {
                "grant_type": "client_credentials",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
        },
        timeout=10,
    )
    data = resp.json()
    if "result" in data:
        TOKEN = data["result"]["access_token"]
        print(f"Auth OK — token acquired (expires in {data['result']['expires_in']}s)")
        return True
    print(f"Auth FAILED: {data.get('error')}")
    return False

def api_public(method, params=None):
    resp = requests.get(
        f"{BASE_URL}/api/v2/public/{method}",
        params=params or {},
        timeout=15,
    )
    data = resp.json()
    if "error" in data:
        print(f"  API ERROR (public/{method}): {data['error']}")
        return None
    return data.get("result")

def api_private(method, params=None):
    resp = requests.post(
        f"{BASE_URL}/api/v2/private/{method}",
        json={
            "jsonrpc": "2.0", "id": 1,
            "method": f"private/{method}",
            "params": params or {},
        },
        headers={"Authorization": f"Bearer {TOKEN}"},
        timeout=15,
    )
    data = resp.json()
    if "error" in data:
        print(f"  API ERROR (private/{method}): {data['error']}")
        return data  # return full response so callers can inspect the error
    return data.get("result")

def round_to_tick(price, tick=0.0005):
    """Round a price DOWN to the nearest valid tick.
    Deribit BTC options use 0.0005 tick for prices >= 0.005, 0.0001 below."""
    if price < 0.005:
        tick = 0.0001
    return round(int(price / tick) * tick, 5)

def pp(obj, max_lines=60):
    text = json.dumps(obj, indent=2, default=str)
    lines = text.split("\n")
    for line in lines[:max_lines]:
        print(f"  {line}")
    if len(lines) > max_lines:
        print(f"  ... ({len(lines) - max_lines} more lines)")

def separator(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")

def pick_atm_option():
    """Pick a liquid ATM call with ~7 DTE for testing."""
    # Get index price
    idx = api_public("get_index_price", {"index_name": "btc_usd"})
    if not idx:
        return None, None, None
    index_price = idx["index_price"]
    print(f"  BTC index price: ${index_price:,.2f}")

    # Get instruments, find nearest expiry >= 3 days out
    instruments = api_public("get_instruments", {
        "currency": "BTC", "kind": "option", "expired": "false"
    })
    if not instruments:
        return None, None, None

    now_ms = int(time.time() * 1000)
    min_expiry_ms = now_ms + 3 * 86400 * 1000  # at least 3 days

    # Filter calls with expiry >= 3 days
    calls = [i for i in instruments
             if i["option_type"] == "call"
             and i["expiration_timestamp"] > min_expiry_ms]
    if not calls:
        print("  No suitable calls found")
        return None, None, None

    # Find nearest expiry bucket
    expiries = sorted(set(c["expiration_timestamp"] for c in calls))
    target_expiry = expiries[0]  # nearest expiry >= 3 days

    # Among that expiry, find ATM strike
    bucket = [c for c in calls if c["expiration_timestamp"] == target_expiry]
    bucket.sort(key=lambda c: abs(c["strike"] - index_price))
    atm = bucket[0]
    expiry_dt = datetime.fromtimestamp(target_expiry / 1000, tz=timezone.utc)
    print(f"  Selected: {atm['instrument_name']}  (strike={atm['strike']}, "
          f"expiry={expiry_dt.strftime('%Y-%m-%d')}, "
          f"DTE={max(0, (target_expiry - now_ms) // 86400000)})")
    return atm["instrument_name"], index_price, atm

def get_ticker(instrument):
    """Get current ticker for an instrument."""
    return api_public("ticker", {"instrument_name": instrument})

PASSED = 0
FAILED = 0

def check(condition, label):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ✓ {label}")
    else:
        FAILED += 1
        print(f"  ✗ FAIL: {label}")

# ────────────────────────────────────────────────────────────────────────
#  AUTHENTICATE
# ────────────────────────────────────────────────────────────────────────
separator(f"AUTHENTICATION ({ENV_LABEL})")
if not authenticate():
    sys.exit(1)

# ────────────────────────────────────────────────────────────────────────
#  PICK AN ATM OPTION
# ────────────────────────────────────────────────────────────────────────
separator("INSTRUMENT SELECTION")
instrument, index_price, instr_meta = pick_atm_option()
if not instrument:
    print("Cannot proceed without an instrument."); sys.exit(1)

ticker = get_ticker(instrument)
if not ticker:
    print("Cannot get ticker."); sys.exit(1)

best_bid = ticker.get("best_bid_price", 0)
best_ask = ticker.get("best_ask_price", 0)
print(f"  best_bid={best_bid}  best_ask={best_ask}  mark={ticker.get('mark_price')}")

if not best_bid or not best_ask:
    print("  WARNING: no bid/ask — market may be illiquid. Trying anyway.")

# ────────────────────────────────────────────────────────────────────────
#  TEST 4a: PLACE → READ → MODIFY → CANCEL
# ────────────────────────────────────────────────────────────────────────
separator(f"TEST 4a: Place → Read → Modify → Cancel ({ENV_LABEL})")

# Step 1: place a limit buy far below market
far_price = round_to_tick(max(best_bid * 0.3, 0.0001))
print(f"  Placing limit BUY at {far_price} (far below bid={best_bid})")

buy_result = api_private("buy", {
    "instrument_name": instrument,
    "amount": 0.1,
    "type": "limit",
    "price": far_price,
    "label": "test_4a_001",
})

if isinstance(buy_result, dict) and "error" in buy_result:
    print(f"  Order placement failed: {buy_result['error']}")
    sys.exit(1)

order = buy_result.get("order", {})
trades = buy_result.get("trades", [])
order_id = order.get("order_id")

print(f"\n  Order response:")
pp(buy_result)

check(order_id is not None, "order_id present in response")
check(order.get("order_state") == "open", f"order_state == 'open' (got: {order.get('order_state')})")
check(order.get("label") == "test_4a_001", f"label round-tripped (got: {order.get('label')})")
check(order.get("direction") == "buy", f"direction == 'buy' (got: {order.get('direction')})")
check(order.get("instrument_name") == instrument, f"instrument matches")
check(order.get("price") == far_price, f"price matches {far_price}")
check(order.get("amount") == 0.1, f"amount == 0.1 (got: {order.get('amount')})")
check(len(trades) == 0, f"no immediate fill (trades={len(trades)})")

print(f"\n  ── Order field names ──")
print(f"  {sorted(order.keys())}")

# Step 2: read order status
separator("TEST 4a.2: Read Order Status")
order_state = api_private("get_order_state", {"order_id": order_id})
if order_state:
    print("  Order state response:")
    pp(order_state)
    check(order_state.get("order_id") == order_id, "order_id matches")
    check(order_state.get("label") == "test_4a_001", f"label preserved (got: {order_state.get('label')})")
    check(order_state.get("order_state") == "open", f"still open")

# Step 3: find in open orders list
separator("TEST 4a.3: Find in Open Orders")
open_orders = api_private("get_open_orders_by_currency", {"currency": "BTC"})
if open_orders is not None:
    our_order = [o for o in open_orders if o.get("order_id") == order_id]
    check(len(our_order) == 1, f"found our order in open orders list ({len(open_orders)} total)")
    if our_order:
        check(our_order[0].get("label") == "test_4a_001", "label visible in list")
else:
    print("  Could not fetch open orders")

# Step 4: modify the order (change price)
separator("TEST 4a.4: Modify Order")
new_price = round_to_tick(far_price + 0.0005)
print(f"  Editing order {order_id}: price {far_price} → {new_price}")

edit_result = api_private("edit", {
    "order_id": order_id,
    "amount": 0.1,
    "price": new_price,
})

if edit_result and "order" in edit_result:
    edited = edit_result["order"]
    print("  Edit response:")
    pp(edit_result)
    new_order_id = edited.get("order_id")
    check(edited.get("price") == new_price, f"price updated to {new_price}")
    check(edited.get("order_state") == "open", "still open after edit")
    print(f"\n  ── order_id after edit: {new_order_id}")
    print(f"  ── order_id changed? {'YES' if new_order_id != order_id else 'NO'}")
    if new_order_id != order_id:
        print(f"      old={order_id}  new={new_order_id}")
        order_id = new_order_id  # use new ID going forward
elif isinstance(edit_result, dict) and "error" in edit_result:
    print(f"  Edit failed: {edit_result['error']}")
else:
    print(f"  Unexpected edit response: {edit_result}")

# Step 5: cancel the order
separator("TEST 4a.5: Cancel Order")
cancel_result = api_private("cancel", {"order_id": order_id})
if cancel_result:
    print("  Cancel response:")
    pp(cancel_result)
    check(cancel_result.get("order_state") == "cancelled",
          f"order_state == 'cancelled' (got: {cancel_result.get('order_state')})")

# Step 6: verify gone from open orders
separator("TEST 4a.6: Verify Cancelled")
open_orders = api_private("get_open_orders_by_currency", {"currency": "BTC"})
if open_orders is not None:
    remaining = [o for o in open_orders if o.get("order_id") == order_id]
    check(len(remaining) == 0, "order no longer in open orders list")

print(f"\n  ── Test 4a subtotal: {PASSED} passed, {FAILED} failed ──")

# ────────────────────────────────────────────────────────────────────────
#  TEST 4b: PLACE → FILL → VERIFY POSITION → CLOSE → VERIFY GONE
# ────────────────────────────────────────────────────────────────────────
separator(f"TEST 4b: Full Position Lifecycle ({ENV_LABEL})")

# Refresh ticker
ticker = get_ticker(instrument)
best_bid = ticker.get("best_bid_price", 0)
best_ask = ticker.get("best_ask_price", 0)
print(f"  Refreshed ticker: bid={best_bid}  ask={best_ask}")

if not best_ask or best_ask == 0:
    print("  SKIP: no ask price — cannot attempt fill test")
else:
    # Step 1: buy at best_ask (should fill immediately)
    print(f"\n  Placing limit BUY at best_ask={best_ask} for 0.1 contracts")
    fill_result = api_private("buy", {
        "instrument_name": instrument,
        "amount": 0.1,
        "type": "limit",
        "price": best_ask,
        "label": "test_4b_buy",
    })

    if isinstance(fill_result, dict) and "error" in fill_result:
        print(f"  Buy failed: {fill_result['error']}")
    else:
        fill_order = fill_result.get("order", {})
        fill_trades = fill_result.get("trades", [])
        fill_oid = fill_order.get("order_id")
        fill_state = fill_order.get("order_state")

        print(f"  Order state: {fill_state}  |  Trades: {len(fill_trades)}")
        pp(fill_order)

        # If not filled immediately, poll
        if fill_state not in ("filled",):
            print(f"  Not immediately filled — polling (max 30s)...")
            for i in range(15):
                time.sleep(2)
                status = api_private("get_order_state", {"order_id": fill_oid})
                if status:
                    fill_state = status.get("order_state")
                    filled_amt = status.get("filled_amount", 0)
                    print(f"    poll {i+1}: state={fill_state}  filled={filled_amt}")
                    if fill_state == "filled":
                        fill_order = status
                        break

        check(fill_state == "filled", f"order filled (state={fill_state})")

        if fill_trades:
            print(f"\n  ── Fill trade details ──")
            for t in fill_trades:
                print(f"    trade_id={t.get('trade_id')}  price={t.get('price')}  "
                      f"amount={t.get('amount')}  fee={t.get('fee')} {t.get('fee_currency')}")
        else:
            print(f"  No trades returned in buy response — checking history...")

        # Step 2: verify position exists
        separator("TEST 4b.2: Verify Position")
        positions = api_private("get_positions", {"currency": "BTC", "kind": "option"})
        if positions is not None:
            our_pos = [p for p in positions if p.get("instrument_name") == instrument and p.get("size", 0) > 0]
            check(len(our_pos) >= 1, f"position exists for {instrument}")
            if our_pos:
                pos = our_pos[0]
                print(f"  Position:")
                pp(pos)
                check(pos.get("size") == 0.1, f"size == 0.1 (got: {pos.get('size')})")
                check(pos.get("direction") == "buy", f"direction == 'buy' (got: {pos.get('direction')})")
                print(f"  delta={pos.get('delta')}  pnl={pos.get('floating_profit_loss')}  "
                      f"mark={pos.get('mark_price')}")

        # Step 3: close the position — sell at best_bid
        separator("TEST 4b.3: Close Position")
        ticker = get_ticker(instrument)
        best_bid = ticker.get("best_bid_price", 0)
        print(f"  Placing limit SELL at best_bid={best_bid} for 0.1 contracts (reduce_only=true)")

        close_result = api_private("sell", {
            "instrument_name": instrument,
            "amount": 0.1,
            "type": "limit",
            "price": best_bid,
            "label": "test_4b_close",
            "reduce_only": True,
        })

        if isinstance(close_result, dict) and "error" in close_result:
            print(f"  Sell failed: {close_result['error']}")
        else:
            close_order = close_result.get("order", {})
            close_trades = close_result.get("trades", [])
            close_state = close_order.get("order_state")
            close_oid = close_order.get("order_id")

            print(f"  Close state: {close_state}  |  Trades: {len(close_trades)}")

            # Poll if not filled
            if close_state not in ("filled",):
                print(f"  Not immediately filled — polling (max 30s)...")
                for i in range(15):
                    time.sleep(2)
                    status = api_private("get_order_state", {"order_id": close_oid})
                    if status:
                        close_state = status.get("order_state")
                        filled_amt = status.get("filled_amount", 0)
                        print(f"    poll {i+1}: state={close_state}  filled={filled_amt}")
                        if close_state == "filled":
                            break
                # If still not filled after 30s, cancel the order to clean up
                if close_state not in ("filled",):
                    print("  Timeout — cancelling close order to avoid orphan position")
                    api_private("cancel", {"order_id": close_oid})

            check(close_state == "filled", f"close order filled (state={close_state})")

            if close_trades:
                for t in close_trades:
                    print(f"    trade_id={t.get('trade_id')}  price={t.get('price')}  "
                          f"fee={t.get('fee')} {t.get('fee_currency')}")

        # Step 4: verify position gone
        separator("TEST 4b.4: Verify Position Closed")
        positions = api_private("get_positions", {"currency": "BTC", "kind": "option"})
        if positions is not None:
            active = [p for p in positions if p.get("instrument_name") == instrument and p.get("size", 0) > 0]
            check(len(active) == 0, f"position gone (or size=0) for {instrument}")

        # Step 5: check trade history
        separator("TEST 4b.5: Trade History")
        history = api_private("get_user_trades_by_currency", {"currency": "BTC", "count": 10})
        if history and "trades" in history:
            our_trades = [t for t in history["trades"]
                          if t.get("instrument_name") == instrument
                          and t.get("label", "").startswith("test_4b")]
            print(f"  Found {len(our_trades)} trades with test_4b label:")
            for t in our_trades:
                print(f"    {t.get('direction'):4s} {t.get('amount')} @ {t.get('price')}  "
                      f"fee={t.get('fee')} {t.get('fee_currency')}  "
                      f"label={t.get('label')}  id={t.get('trade_id')}")
            check(len(our_trades) >= 2, f"both buy and sell trades recorded ({len(our_trades)} found)")
        elif history:
            print(f"  Trade history response keys: {list(history.keys()) if isinstance(history, dict) else type(history)}")

# ────────────────────────────────────────────────────────────────────────
#  TEST 4c: EDGE CASES
# ────────────────────────────────────────────────────────────────────────
separator(f"TEST 4c: Edge Cases ({ENV_LABEL})")

# 4c.1: reduce_only with no position
print("  4c.1: reduce_only SELL with no position...")
edge1 = api_private("sell", {
    "instrument_name": instrument,
    "amount": 0.1,
    "type": "limit",
    "price": round_to_tick(best_bid * 0.5) if best_bid else 0.001,
    "reduce_only": True,
    "label": "test_4c_edge1",
})
if isinstance(edge1, dict) and "error" in edge1:
    err = edge1["error"]
    print(f"    → Rejected: code={err.get('code')}  message={err.get('message')}")
    check(True, f"reduce_only with no position rejected (code={err.get('code')})")
else:
    # Might succeed with an immediate cancel or open order — check
    print(f"    → Unexpected success: {edge1}")
    # Clean up if an order was placed
    if isinstance(edge1, dict) and "order" in edge1:
        oid = edge1["order"]["order_id"]
        api_private("cancel", {"order_id": oid})
        check(False, "reduce_only with no position should have been rejected")

# 4c.2: below minimum size
print("\n  4c.2: Below minimum trade amount (0.01)...")
edge2 = api_private("buy", {
    "instrument_name": instrument,
    "amount": 0.01,
    "type": "limit",
    "price": round_to_tick(best_bid * 0.3) if best_bid else 0.001,
    "label": "test_4c_edge2",
})
if isinstance(edge2, dict) and "error" in edge2:
    err = edge2["error"]
    print(f"    → Rejected: code={err.get('code')}  message={err.get('message')}")
    check(True, f"below min size rejected (code={err.get('code')})")
else:
    print(f"    → Unexpected response: {edge2}")
    if isinstance(edge2, dict) and "order" in edge2:
        api_private("cancel", {"order_id": edge2["order"]["order_id"]})

# 4c.3: expired/invalid instrument
print("\n  4c.3: Expired/invalid instrument...")
edge3 = api_private("buy", {
    "instrument_name": "BTC-1JAN20-10000-C",
    "amount": 0.1,
    "type": "limit",
    "price": 0.001,
    "label": "test_4c_edge3",
})
if isinstance(edge3, dict) and "error" in edge3:
    err = edge3["error"]
    print(f"    → Rejected: code={err.get('code')}  message={err.get('message')}")
    check(True, f"invalid instrument rejected (code={err.get('code')})")
else:
    print(f"    → Unexpected response: {edge3}")
    if isinstance(edge3, dict) and "order" in edge3:
        api_private("cancel", {"order_id": edge3["order"]["order_id"]})

# ────────────────────────────────────────────────────────────────────────
#  FINAL SUMMARY
# ────────────────────────────────────────────────────────────────────────
separator("TEST 4 SUMMARY")
print(f"Environment: {ENV_LABEL} ({BASE_URL})")
print(f"Instrument:  {instrument}")
print(f"Checks passed: {PASSED}")
print(f"Checks failed: {FAILED}")
print(f"\nTest 4: {'PASSED ✓' if FAILED == 0 else 'FAILED ✗'}")
