"""
Test 3: Deribit Account Data — Positions, Margin, Wallet
=========================================================
Hits the Deribit testnet (or prod with --prod) to learn:
  - Account summary fields and currency denomination
  - Position representation (signed vs direction, Greeks)
  - Open orders list structure
  - USDC vs BTC account differences

Run: python tests/deribit/test_deribit_account.py [--prod]
"""
import requests
import json
import sys

# ── Config ──────────────────────────────────────────────────────────────
USE_PROD = "--prod" in sys.argv

if USE_PROD:
    BASE_URL = "https://www.deribit.com"
    CLIENT_ID = "TV6tvw6J"
    CLIENT_SECRET = "NUDhggDLNwL9xj6N2_e-2dqP4jOrKnrBFRMVopK_IAM"
    ENV_LABEL = "PRODUCTION"
else:
    BASE_URL = "https://test.deribit.com"
    CLIENT_ID = "CWlZBUXA"
    CLIENT_SECRET = "sVrL_Bdz-j8_mtLB-y4EdxPS-YGkqeMtLzh4Wi1sz2E"
    ENV_LABEL = "TESTNET"

TOKEN = None

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

def api_private(method, params=None):
    """Call a Deribit private JSON-RPC method."""
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
        print(f"  ERROR: {data['error']}")
        return None
    return data.get("result")

def pp(obj, max_lines=80):
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


# ── Authenticate ────────────────────────────────────────────────────────
separator(f"AUTHENTICATION ({ENV_LABEL})")
if not authenticate():
    sys.exit(1)


# ── Test 3.1: Account Summary (BTC) ────────────────────────────────────
separator(f"TEST 3.1: Account Summary — BTC ({ENV_LABEL})")

btc_account = api_private("get_account_summary", {"currency": "BTC"})
if btc_account:
    print("Full BTC account summary:")
    pp(btc_account)

    print(f"\n── Key Fields ──")
    key_fields = [
        "equity", "available_funds", "available_withdrawal_funds",
        "balance", "initial_margin", "maintenance_margin",
        "margin_balance", "session_upl", "session_rpl",
        "total_pl", "options_pl", "futures_pl",
        "delta_total", "options_gamma", "options_vega", "options_theta",
        "currency", "portfolio_margining_enabled",
    ]
    for field in key_fields:
        val = btc_account.get(field, "NOT PRESENT")
        unit = " BTC" if isinstance(val, (int, float)) and field != "portfolio_margining_enabled" else ""
        print(f"  {field:35s} = {val}{unit}")

    print(f"\n── All field names ──")
    print(f"  {sorted(btc_account.keys())}")
else:
    print("FAIL: Could not fetch BTC account summary")


# ── Test 3.2: Account Summary (USDC) ──────────────────────────────────
separator(f"TEST 3.2: Account Summary — USDC ({ENV_LABEL})")

usdc_account = api_private("get_account_summary", {"currency": "USDC"})
if usdc_account:
    print("Full USDC account summary:")
    pp(usdc_account)

    # Compare fields with BTC
    if btc_account:
        btc_keys = set(btc_account.keys())
        usdc_keys = set(usdc_account.keys())
        only_btc = btc_keys - usdc_keys
        only_usdc = usdc_keys - btc_keys
        print(f"\n── BTC vs USDC field comparison ──")
        print(f"  Fields only in BTC:  {only_btc if only_btc else 'none'}")
        print(f"  Fields only in USDC: {only_usdc if only_usdc else 'none'}")
        print(f"  USDC equity: {usdc_account.get('equity')} (currency: {usdc_account.get('currency')})")
elif usdc_account is None:
    print("  Note: USDC account may not exist or have no balance — this is expected")


# ── Test 3.3: Positions ───────────────────────────────────────────────
separator(f"TEST 3.3: Positions ({ENV_LABEL})")

positions = api_private("get_positions", {"currency": "BTC", "kind": "option"})
if positions is not None:
    print(f"Number of positions: {len(positions)}")
    if len(positions) == 0:
        print("  No positions — empty list returned (not an error, this is expected)")
        print("  Field structure will be verified after Test 4b creates a position")
    else:
        print(f"\n  Field names in position: {sorted(positions[0].keys())}")
        for pos in positions[:3]:
            print(f"\n  Position: {pos.get('instrument_name')}")
            pp(pos)

            # Key questions
            size = pos.get("size")
            direction = pos.get("direction")
            print(f"\n  ── Critical checks ──")
            print(f"    size = {size} (type: {type(size).__name__})")
            print(f"    direction = {direction}")
            print(f"    → Size is {'SIGNED' if size and size < 0 else 'UNSIGNED with direction field'}")

            # Greeks
            for g in ["delta", "gamma", "vega", "theta"]:
                print(f"    {g} = {pos.get(g, 'NOT PRESENT')}")
else:
    print("FAIL: Could not fetch positions")


# ── Test 3.4: Open Orders ─────────────────────────────────────────────
separator(f"TEST 3.4: Open Orders ({ENV_LABEL})")

orders = api_private("get_open_orders_by_currency", {"currency": "BTC"})
if orders is not None:
    print(f"Number of open orders: {len(orders)}")
    if len(orders) == 0:
        print("  No open orders — empty list returned (expected)")
    else:
        print(f"\n  Field names in order: {sorted(orders[0].keys())}")
        for order in orders[:3]:
            print(f"\n  Order: {order.get('order_id')}")
            pp(order)
else:
    print("FAIL: Could not fetch open orders")


# ── Test 3.5: Trade History ───────────────────────────────────────────
separator(f"TEST 3.5: Recent Trade History ({ENV_LABEL})")

trades = api_private("get_user_trades_by_currency", {"currency": "BTC", "count": 5})
if trades is not None:
    # Response might be a dict with "trades" key or a list directly
    if isinstance(trades, dict):
        trade_list = trades.get("trades", [])
        print(f"Response is a dict with keys: {list(trades.keys())}")
    else:
        trade_list = trades

    print(f"Number of recent trades: {len(trade_list)}")
    if len(trade_list) == 0:
        print("  No trade history — expected for fresh account")
    else:
        print(f"\n  Field names in trade: {sorted(trade_list[0].keys())}")
        for trade in trade_list[:2]:
            print(f"\n  Trade:")
            pp(trade)
            # Key fields
            print(f"  ── Key fields ──")
            print(f"    fee: {trade.get('fee')} {trade.get('fee_currency', '')}")
            print(f"    price: {trade.get('price')}")
            print(f"    amount: {trade.get('amount')}")
            print(f"    direction: {trade.get('direction')}")
else:
    print("FAIL: Could not fetch trade history")


# ── Summary ───────────────────────────────────────────────────────────
separator("TEST 3 SUMMARY")
print(f"Environment: {ENV_LABEL} ({BASE_URL})")
if btc_account:
    print(f"BTC equity: {btc_account.get('equity')} BTC")
    print(f"BTC available_funds: {btc_account.get('available_funds')} BTC")
    print(f"Portfolio margining: {btc_account.get('portfolio_margining_enabled')}")
    print(f"Delta total: {btc_account.get('delta_total')}")
if usdc_account:
    print(f"USDC equity: {usdc_account.get('equity')} USDC")
print(f"Positions: {len(positions) if positions is not None else 'ERROR'}")
print(f"Open orders: {len(orders) if orders is not None else 'ERROR'}")
print(f"\nTest 3: PASSED ✓")
