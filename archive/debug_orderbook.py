#!/usr/bin/env python3
"""
Debug orderbook API response
"""

from market_data import get_option_orderbook
import json

# Test orderbook response
symbol = 'BTCUSD-5FEB26-80000-C'
print(f"Testing orderbook for {symbol}")

orderbook = get_option_orderbook(symbol)

if orderbook:
    print("Orderbook response:")
    print(json.dumps(orderbook, indent=2))

    # Check what keys are available
    print(f"\nAvailable keys: {list(orderbook.keys())}")

    # Check if it has the expected structure
    if 'asks' in orderbook:
        print(f"Asks: {orderbook['asks']}")
    else:
        print("No 'asks' key found")

    if 'bids' in orderbook:
        print(f"Bids: {orderbook['bids']}")
    else:
        print("No 'bids' key found")
else:
    print("Failed to get orderbook")