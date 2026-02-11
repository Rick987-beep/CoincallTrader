#!/usr/bin/env python3
"""Test different request formats for order placement"""

from config import API_KEY, API_SECRET, BASE_URL
import time
import json
import hashlib
import hmac
import requests

ts = int(time.time() * 1000)
x_req_ts_diff = 5000
endpoint = '/open/option/order/create/v1'

payload = {
    'symbol': 'BTCUSD-27FEB26-80000-C',
    'qty': '0.1',
    'tradeSide': '1',
    'tradeType': '1',
    'clientOrderId': '123',
    'price': '100.0'
}

print("\n" + "="*70)
print("TEST 2: JSON BODY WITH BODY-INCLUDED SIGNATURE")
print("="*70 + "\n")

# Create signature WITH body included
prehash = f'POST{endpoint}?uuid={API_KEY}&ts={ts}&x-req-ts-diff={x_req_ts_diff}'
body_str = json.dumps(payload, separators=(',', ':'))
prehash_with_body = prehash + body_str

signature_with_body = hmac.new(
    API_SECRET.encode('utf-8'),
    prehash_with_body.encode('utf-8'),
    hashlib.sha256
).hexdigest().upper()

headers = {
    'X-CC-APIKEY': API_KEY,
    'sign': signature_with_body,
    'ts': str(ts),
    'X-REQ-TS-DIFF': str(x_req_ts_diff),
    'Content-Type': 'application/json'
}

print(f"Prehash (first 150 chars): {prehash_with_body[:150]}...")
print(f"Signature: {signature_with_body}\n")
print(f"Sending JSON body: {json.dumps(payload)}\n")

response = requests.post(
    f"https://api.coincall.com{endpoint}",
    json=payload,
    headers=headers
)
print(f"Response: {response.json()}\n")

print("\n" + "="*70)
print("TEST 3: FORM-ENCODED DATA")
print("="*70 + "\n")

# Try form-encoded (application/x-www-form-urlencoded)
ts = int(time.time() * 1000)
prehash = f'POST{endpoint}?uuid={API_KEY}&ts={ts}&x-req-ts-diff={x_req_ts_diff}'

signature_form = hmac.new(
    API_SECRET.encode('utf-8'),
    prehash.encode('utf-8'),
    hashlib.sha256
).hexdigest().upper()

headers_form = {
    'X-CC-APIKEY': API_KEY,
    'sign': signature_form,
    'ts': str(ts),
    'X-REQ-TS-DIFF': str(x_req_ts_diff),
    'Content-Type': 'application/x-www-form-urlencoded'
}

print(f"Signature: {signature_form}\n")

response = requests.post(
    f"https://api.coincall.com{endpoint}",
    data=payload,
    headers=headers_form
)
print(f"Response: {response.json()}\n")
