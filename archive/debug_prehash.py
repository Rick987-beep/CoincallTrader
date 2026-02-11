#!/usr/bin/env python3
"""
Debug the prehash string generation
"""

import json
import hashlib
import hmac
import time

api_key = "Z6RKno2TE9gS0DnblX5kSOtz2vARUZQD4zy8DDHana4="
api_secret = "Z6RKno2TE9gS0DnblX5kSOtz2vARUZQD4zy8DDHana4="

# Test data
order_data = {
    "symbol": "BTCUSD-27FEB26-80000-C",
    "tradeSide": 1,
    "tradeType": 1,
    "qty": 0.1,
    "price": 100.0,
    "clientOrderId": 123456
}

method = "POST"
endpoint = "/open/option/order/create/v1"
ts = int(time.time() * 1000)
x_req_ts_diff = 5000

print("=" * 80)
print("DEBUG: Prehash Generation")
print("=" * 80)
print(f"API Key: {api_key}")
print(f"Endpoint: {endpoint}")
print(f"Timestamp: {ts}")
print(f"Order Data: {order_data}")

# Flatten data for query string format
def flatten_and_sort(d):
    items = []
    for k, v in sorted(d.items()):
        if v is None:
            continue
        if isinstance(v, dict):
            # Skip nested dicts for this test
            pass
        elif isinstance(v, list):
            items.append((k, json.dumps(v, separators=(',', ':'))))
        else:
            items.append((k, str(v)))
    return items

flat_items = flatten_and_sort(order_data)
print(f"\nFlattened items (sorted): {flat_items}")

# Build prehash
param_string = '&'.join([f"{k}={v}" for k, v in flat_items])
print(f"Param string: {param_string}")

prehash = f'{method}{endpoint}?{param_string}&uuid={api_key}&ts={ts}&x-req-ts-diff={x_req_ts_diff}'
print(f"\nPrehash string:\n{prehash}")

# Sign it
signature = hmac.new(
    api_secret.encode('utf-8'),
    prehash.encode('utf-8'),
    hashlib.sha256
).hexdigest().upper()

print(f"\nSignature: {signature}")

print("\n" + "=" * 80)
print("Comparison with example from docs:")
print("=" * 80)
print("""
Example 2 from docs shows:
prehashString:POST/open/options/create/v1?name=mike&num=2&orders=[...array...]&uuid=...&ts=...&x-req-ts-diff=...

Our format:
POST/open/option/order/create/v1?clientOrderId=123456&price=100.0&qty=0.1&symbol=BTCUSD-27FEB26-80000-C&tradeSide=1&tradeType=1&uuid=...&ts=...&x-req-ts-diff=...
""")
