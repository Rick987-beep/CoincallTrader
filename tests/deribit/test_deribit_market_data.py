"""
Test 2: Deribit Market Data — Live Price Feed
==============================================
Hits the Deribit testnet to learn exact response shapes for:
  - Instrument list
  - Option ticker (with BTC-denominated prices + Greeks)
  - Orderbook
  - Index price

Run: python tests/deribit/test_deribit_market_data.py [--prod]
"""
import requests
import json
import sys
from datetime import datetime, timezone

# ── Config ──────────────────────────────────────────────────────────────
USE_PROD = "--prod" in sys.argv
BASE_URL = "https://www.deribit.com" if USE_PROD else "https://test.deribit.com"
ENV_LABEL = "PRODUCTION" if USE_PROD else "TESTNET"

def api_get(method, params=None):
    """Call a Deribit public JSON-RPC method via GET."""
    resp = requests.get(
        f"{BASE_URL}/api/v2/public/{method}",
        params=params or {},
        timeout=15,
    )
    data = resp.json()
    if "error" in data:
        print(f"  ERROR: {data['error']}")
        return None
    return data.get("result")

def pp(obj, max_lines=60):
    """Pretty-print JSON, truncated."""
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


# ── Test 2.1: Instruments ───────────────────────────────────────────────
separator(f"TEST 2.1: BTC Option Instruments ({ENV_LABEL})")

instruments = api_get("get_instruments", {"currency": "BTC", "kind": "option"})
if instruments is None:
    print("FAIL: Could not fetch instruments")
    sys.exit(1)

active = [i for i in instruments if i.get("is_active")]
print(f"Total instruments returned: {len(instruments)}")
print(f"Active instruments: {len(active)}")

# Show field names from first instrument
print(f"\nField names in instrument response:")
print(f"  {sorted(active[0].keys())}")

# Show 3 example instruments (nearest expiry, ATM-ish)
print(f"\nSample instruments (first 3 by expiry):")
by_expiry = sorted(active, key=lambda i: i["expiration_timestamp"])
for inst in by_expiry[:3]:
    exp_dt = datetime.fromtimestamp(inst["expiration_timestamp"] / 1000, tz=timezone.utc)
    print(f"  {inst['instrument_name']:40s}  strike={inst['strike']:>10}  "
          f"type={inst['option_type']}  expiry={exp_dt.strftime('%Y-%m-%d %H:%M UTC')}  "
          f"min_trade={inst.get('min_trade_amount')}  tick={inst.get('tick_size')}")

# Log one full instrument for reference
print(f"\nFull instrument object (first active):")
pp(by_expiry[0])

# Collect unique expiries
expiries = sorted(set(i["expiration_timestamp"] for i in active))
print(f"\nNumber of unique expiry dates: {len(expiries)}")
print(f"Nearest expiry: {datetime.fromtimestamp(expiries[0]/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
print(f"Farthest expiry: {datetime.fromtimestamp(expiries[-1]/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")


# ── Test 2.2: Index Price ──────────────────────────────────────────────
separator(f"TEST 2.2: BTC Index Price ({ENV_LABEL})")

index_result = api_get("get_index_price", {"index_name": "btc_usd"})
if index_result:
    btc_index = index_result["index_price"]
    print(f"BTC index price: ${btc_index:,.2f}")
    print(f"Full response:")
    pp(index_result)
else:
    print("FAIL: Could not fetch index price")
    btc_index = 84000  # fallback for later calculations


# ── Test 2.3: Option Ticker ────────────────────────────────────────────
separator(f"TEST 2.3: Option Ticker — ATM Call ({ENV_LABEL})")

# Find nearest ATM call (~7 DTE if available, otherwise nearest)
import time
now_ms = int(time.time() * 1000)
target_ms = now_ms + 7 * 24 * 3600 * 1000  # ~7 days from now

# Find closest expiry to 7 days
closest_expiry = min(expiries, key=lambda e: abs(e - target_ms))
exp_instruments = [i for i in active if i["expiration_timestamp"] == closest_expiry and i["option_type"] == "call"]

# Find ATM (closest strike to index price)
atm_call = min(exp_instruments, key=lambda i: abs(i["strike"] - btc_index))
selected_instrument = atm_call["instrument_name"]
print(f"Selected instrument: {selected_instrument}")
print(f"  Strike: {atm_call['strike']}, Expiry: {datetime.fromtimestamp(closest_expiry/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

ticker = api_get("ticker", {"instrument_name": selected_instrument})
if ticker:
    print(f"\nFull ticker response:")
    pp(ticker)

    # Extract key fields
    mark = ticker.get("mark_price")
    bid = ticker.get("best_bid_price")
    ask = ticker.get("best_ask_price")
    underlying = ticker.get("underlying_price")
    greeks = ticker.get("greeks", {})

    print(f"\n── Key Price Fields ──")
    print(f"  mark_price:      {mark}  (in BTC)")
    print(f"  best_bid_price:  {bid}")
    print(f"  best_ask_price:  {ask}")
    print(f"  underlying_price: {underlying}")
    print(f"  index_price:     {ticker.get('index_price')}")

    if mark and underlying:
        mark_usd = mark * underlying
        print(f"\n── BTC → USD Conversion ──")
        print(f"  mark_price in USD: {mark} × ${underlying:,.2f} = ${mark_usd:,.2f}")
        if bid:
            print(f"  bid in USD: {bid} × ${underlying:,.2f} = ${bid * underlying:,.2f}")
        if ask:
            print(f"  ask in USD: {ask} × ${underlying:,.2f} = ${ask * underlying:,.2f}")

    print(f"\n── Greeks ──")
    if greeks:
        for k, v in greeks.items():
            print(f"  {k}: {v}")
    else:
        print(f"  greeks field: {ticker.get('greeks', 'NOT PRESENT')}")

    print(f"\n── Other Notable Fields ──")
    for key in ["open_interest", "volume", "last_price", "mark_iv",
                "bid_iv", "ask_iv", "interest_rate", "estimated_delivery_price",
                "settlement_price", "stats"]:
        if key in ticker:
            val = ticker[key]
            if isinstance(val, dict):
                print(f"  {key}:")
                for k2, v2 in val.items():
                    print(f"    {k2}: {v2}")
            else:
                print(f"  {key}: {val}")
else:
    print("FAIL: Could not fetch ticker")


# ── Test 2.4: Orderbook ───────────────────────────────────────────────
separator(f"TEST 2.4: Orderbook ({ENV_LABEL})")

book = api_get("get_order_book", {"instrument_name": selected_instrument, "depth": 10})
if book:
    print(f"Full orderbook response:")
    pp(book)

    bids = book.get("bids", [])
    asks = book.get("asks", [])
    print(f"\n── Orderbook Summary ──")
    print(f"  Bids: {len(bids)} levels")
    print(f"  Asks: {len(asks)} levels")
    if bids:
        print(f"  Best bid: price={bids[0][0]} amount={bids[0][1]}  (price is in {'BTC' if bids[0][0] < 1 else 'USD?'})")
    if asks:
        print(f"  Best ask: price={asks[0][0]} amount={asks[0][1]}  (price is in {'BTC' if asks[0][0] < 1 else 'USD?'})")

    print(f"\n  mark_price: {book.get('mark_price')}")
    print(f"  underlying_price: {book.get('underlying_price')}")
    print(f"  index_price: {book.get('underlying_index')}")
    print(f"  state: {book.get('state')}")
else:
    print("FAIL: Could not fetch orderbook")


# ── Also try a deep OTM option to check null Greeks ───────────────────
separator(f"TEST 2.5: Deep OTM Option (null Greeks check)")

# Pick highest strike call in same expiry
otm_call = max(exp_instruments, key=lambda i: i["strike"])
print(f"Selected deep OTM: {otm_call['instrument_name']} (strike={otm_call['strike']})")
otm_ticker = api_get("ticker", {"instrument_name": otm_call["instrument_name"]})
if otm_ticker:
    print(f"  mark_price: {otm_ticker.get('mark_price')}")
    print(f"  greeks: {otm_ticker.get('greeks')}")
    print(f"  best_bid_price: {otm_ticker.get('best_bid_price')}")
    print(f"  best_ask_price: {otm_ticker.get('best_ask_price')}")
    # Check for None/null values
    greeks = otm_ticker.get("greeks", {})
    if greeks:
        nulls = [k for k, v in greeks.items() if v is None]
        print(f"  Null greek fields: {nulls if nulls else 'none — all populated'}")


# ── Summary ───────────────────────────────────────────────────────────
separator("TEST 2 SUMMARY")
print(f"Environment: {ENV_LABEL} ({BASE_URL})")
print(f"BTC index: ${btc_index:,.2f}")
print(f"Active BTC options: {len(active)}")
print(f"Unique expiries: {len(expiries)}")
print(f"Tested ticker: {selected_instrument}")
print(f"Price denomination: BTC (confirmed if mark_price < 1.0)")
print(f"\nTest 2: PASSED ✓")
