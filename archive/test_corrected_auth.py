#!/usr/bin/env python3
"""
Test the corrected authentication with JSON body in POST requests
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from auth import CoincallAuth
import time

# Initialize auth with correct API key and secret
auth = CoincallAuth(
    api_key="Z6RKno2TE9gS0DnblX5kSOtz2vARUZQD4zy8DDHana4=",
    api_secret="OiS8RsOn8BWUiF9ceEGD66+0yVQilP3ldilwlMA3CAI=",  # Correct production secret
    base_url="https://api.coincall.com"
)

print("=" * 80)
print("TEST: Place Option Order with Corrected JSON Body Signature")
print("=" * 80)

# Test order placement
order_data = {
    "symbol": "BTCUSD-27FEB26-80000-C",
    "tradeSide": 1,  # Buy
    "tradeType": 1,  # Limit
    "qty": 0.1,
    "price": 100.0,
    "clientOrderId": int(time.time() * 1000)
}

print(f"\nOrder Data: {order_data}")
print("\nMaking POST request to /open/option/order/create/v1 with JSON body...")

response = auth.post('/open/option/order/create/v1', order_data)

print(f"\nResponse: {response}")

if response.get('code') == 0:
    print(f"✓ SUCCESS: Order placed! Order ID: {response.get('data')}")
elif response.get('code') == 4003:
    print(f"✗ Token auth fail (4003) - Signature still wrong")
elif response.get('code') == 10000:
    print(f"✗ Try again later (10000) - Likely a different issue now")
else:
    print(f"✗ Error: {response.get('msg')}")

print("\n" + "=" * 80)
