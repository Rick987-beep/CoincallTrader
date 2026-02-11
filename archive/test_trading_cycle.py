#!/usr/bin/env python3
"""
Production Trading Test Script

Tests full trading cycle without opening positions:
1. Connect to production environment
2. Get account balance
3. Select option: BTC 27FEB 80000 Call
4. Display bid/mark/ask prices
5. Place limit order with minimal quantity that won't fill

IMPORTANT: This script places a real order - ensure quantity is minimal!
"""

import logging
import time
from config import ENVIRONMENT
from account_manager import AccountManager
from market_data import get_option_market_data
from trade_execution import place_order

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def test_production_trading():
    """Run the production trading test"""

    print("=" * 60)
    print("PRODUCTION TRADING TEST")
    print("=" * 60)
    print(f"Environment: {ENVIRONMENT.upper()}")
    print()

    # 1. Get account balance
    print("1. Getting account balance...")
    try:
        manager = AccountManager()
        account_info = manager.get_account_info(force_refresh=True)

        if account_info:
            balance = account_info.get('available_margin', 0)
            equity = account_info.get('equity', 0)
            print(f"   ✅ Available margin: ${balance:,.2f}")
            print(f"   ✅ Account equity: ${equity:,.2f}")
        else:
            print("❌ Failed to get account balance")
            return

    except Exception as e:
        print(f"❌ Error getting account balance: {e}")
        return

    print()

    # 2. Select option: hardcoded 27 Feb 80000 Call
    print("2. Selecting option: 27 Feb 80000 Call...")

    # First, let's test basic auth with a simple endpoint
    print("   Testing basic authentication...")
    from auth import CoincallAuth
    from config import API_KEY, API_SECRET, BASE_URL
    auth = CoincallAuth(API_KEY, API_SECRET, BASE_URL)

    # Test user info endpoint (should work)
    response = auth.get('/open/user/info/v1')
    if auth.is_successful(response):
        print("   ✅ Basic auth working")
    else:
        print(f"   ❌ Basic auth failed: {response.get('msg')}")
        return

    # Hardcode selection: 27 Feb 80000 Call
    symbol = "BTCUSD-27FEB26-80000-C"

    print(f"✅ Selected option: {symbol}")

    print()

    # 3. Display bid/mark/ask prices
    print("3. Getting market data...")

    try:
        market_data = get_option_market_data(symbol)

        if market_data:
            bid = market_data.get('bid', 0)
            ask = market_data.get('ask', 0)
            mark = market_data.get('mark_price', 0)

            print(f"   ✅ Bid: ${bid:.6f}")
            print(f"   ✅ Mark: ${mark:.6f}")
            print(f"   ✅ Ask: ${ask:.6f}")
        else:
            print("❌ No market data available")
            return

    except Exception as e:
        print(f"❌ Error getting market data: {e}")
        return

    print()

    # 4. Place limit order with specified parameters
    print("4. Placing limit order...")

    # Place a buy limit order at a low test price
    limit_price = 100.0  # $100 (low test price that won't fill)
    qty = 0.1  # Test quantity

    print(f"   Limit price: ${limit_price:.6f}")
    print(f"   Quantity: {qty}")
    print(f"   Side: Buy (1)")
    print(f"   Order Type: Limit (1)")

    try:
        # Confirm before placing
        confirm = input("\n⚠️  This will place a REAL order. Continue? (yes/no): ")
        if confirm.lower() != 'yes':
            print("Order placement cancelled.")
            return

        order_result = place_order(symbol, qty, side=1, order_type=1, price=limit_price, client_order_id=123)

        if order_result:
            order_id = order_result.get('orderId')
            print(f"✅ Order placed successfully!")
            print(f"   Order ID: {order_id}")
            print(f"   Status: {order_result.get('state', 'Unknown')}")

            # Wait a moment and check status
            time.sleep(2)
            from trade_execution import get_order_status
            status = get_order_status(order_id)
            if status:
                print(f"   Current status: {status.get('state', 'Unknown')}")
                print(f"   Filled quantity: {status.get('executedQty', 0)}")

        else:
            print("❌ Failed to place order")

    except Exception as e:
        print(f"❌ Error placing order: {e}")

    print()
    print("=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    test_production_trading()