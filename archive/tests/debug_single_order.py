#!/usr/bin/env python3
"""
Simple single order placement test to debug order visibility in web interface
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import API_KEY, API_SECRET
from market_data import get_option_orderbook
from trade_execution import execute_trade
import logging

# Set up basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def test_single_order():
    """Place a single simple order and check if it appears in web interface"""

    # Use a simple option symbol that should exist
    symbol = "BTCUSD-5FEB26-80000-C"

    print(f"🔍 Testing single order placement for {symbol}")
    print("=" * 50)

    # First, check the orderbook to see current market
    print("📊 Checking current orderbook...")
    try:
        depth = get_option_orderbook(symbol)
        if 'data' in depth and 'asks' in depth['data'] and depth['data']['asks']:
            best_ask = float(depth['data']['asks'][0]['price'])
            print(f"Best ask price: {best_ask}")
            order_price = best_ask  # Place at market
        else:
            print("⚠️  No asks in orderbook, using default price")
            order_price = 500.0  # Fallback price
    except Exception as e:
        print(f"❌ Error getting orderbook: {e}")
        order_price = 500.0  # Fallback price

    print(f"💰 Will place order at price: {order_price}")

    # Place a simple limit order
    print("📤 Placing single limit order...")
    try:
        result = execute_trade(
            symbol=symbol,
            qty=1.0,  # Changed from quantity to qty
            side=1,  # Buy
            timeout_seconds=30  # 30 second timeout
        )

        print("📋 Order placement result:")
        print(f"   Result: {result}")

        if result and 'orderId' in result:
            print(f"✅ Order placed successfully! Order ID: {result['orderId']}")
            print("🔍 Check your web browser now to see if this order appears!")
            print("   Look for the order in your Coincall options trading interface.")
        else:
            print("❌ Order placement failed or returned unexpected result")
            print(f"   Full response: {result}")

    except Exception as e:
        print(f"❌ Exception during order placement: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_single_order()