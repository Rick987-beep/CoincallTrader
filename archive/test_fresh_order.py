#!/usr/bin/env python3
"""
Final test - place a fresh order to confirm everything works
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from config import ENVIRONMENT
from account_manager import AccountManager
from market_data import get_option_market_data
from trade_execution import place_order
import time

print("=" * 70)
print("FINAL TEST: Place Fresh Order with Corrected Authentication")
print("=" * 70)
print(f"Environment: {ENVIRONMENT.upper()}")
print()

# Get account info
print("1. Getting account balance...")
manager = AccountManager()
account_info = manager.get_account_info(force_refresh=True)
if account_info:
    print(f"   ✅ Available margin: ${account_info.get('available_margin', 0):,.2f}")
else:
    print("   ❌ Failed")
    sys.exit(1)

# Test order placement with fresh clientOrderId
symbol = "BTCUSD-27FEB26-80000-C"
print(f"\n2. Placing order for {symbol}...")

client_order_id = int(time.time() * 1000)  # Use timestamp for unique ID
result = place_order(
    symbol=symbol,
    qty=0.1,
    side=1,
    order_type=1,
    price=100.0,
    client_order_id=client_order_id
)

if result:
    print(f"   ✅ SUCCESS: Order placed!")
    print(f"   Order ID: {result.get('orderId')}")
    print(f"   Client Order ID: {client_order_id}")
else:
    print(f"   ❌ Failed to place order")

print("\n" + "=" * 70)
print("TEST COMPLETE")
print("=" * 70)
