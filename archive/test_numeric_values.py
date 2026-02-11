#!/usr/bin/env python3
"""Test numeric vs string parameter formats"""

from config import API_KEY, API_SECRET, BASE_URL
import time
import json
import hashlib
import hmac
import requests

print("\n" + "="*70)
print("TEST 4: NUMERIC VALUES IN QUERY STRING")
print("="*70 + "\n")

ts = int(time.time() * 1000)
x_req_ts_diff = 5000
endpoint = '/open/option/order/create/v1'

# Use actual numeric values
payload_numeric = {
    'symbol': 'BTCUSD-27FEB26-80000-C',
    'qty': 0.1,  # numeric instead of string
    'tradeSide': 1,  # numeric
    'tradeType': 1,  # numeric
    'clientOrderId': 123,  # numeric
    'price': 100.0  # numeric
}

prehash = f'POST{endpoint}?uuid={API_KEY}&ts={ts}&x-req-ts-diff={x_req_ts_diff}'

signature = hmac.new(
    API_SECRET.encode('utf-8'),
    prehash.encode('utf-8'),
    hashlib.sha256
).hexdigest().upper()

headers = {
    'X-CC-APIKEY': API_KEY,
    'sign': signature,
    'ts': str(ts),
    'X-REQ-TS-DIFF': str(x_req_ts_diff),
    'Content-Type': 'application/json'
}

# Build query string with numeric values
query_parts = [
    f"symbol={payload_numeric['symbol']}",
    f"qty={payload_numeric['qty']}",
    f"tradeSide={payload_numeric['tradeSide']}",
    f"tradeType={payload_numeric['tradeType']}",
    f"clientOrderId={payload_numeric['clientOrderId']}",
    f"price={payload_numeric['price']}"
]
query_string = "&".join(query_parts)

url = f"https://api.coincall.com{endpoint}?{query_string}"
print(f"URL: {url}\n")

response = requests.post(url, headers=headers)
print(f"Response: {response.json()}\n")

print("\n" + "="*70)
print("TEST 5: STRICT STRING FORMATTING WITH DECIMALS")
print("="*70 + "\n")

ts = int(time.time() * 1000)
# Use very specific string formatting
payload_strict = {
    'symbol': 'BTCUSD-27FEB26-80000-C',
    'qty': '0.1000000000',  # with trailing zeros
    'tradeSide': '1',
    'tradeType': '1',
    'clientOrderId': '123',
    'price': '100.0000000000'  # with trailing zeros
}

prehash = f'POST{endpoint}?uuid={API_KEY}&ts={ts}&x-req-ts-diff={x_req_ts_diff}'

signature = hmac.new(
    API_SECRET.encode('utf-8'),
    prehash.encode('utf-8'),
    hashlib.sha256
).hexdigest().upper()

headers = {
    'X-CC-APIKEY': API_KEY,
    'sign': signature,
    'ts': str(ts),
    'X-REQ-TS-DIFF': str(x_req_ts_diff),
    'Content-Type': 'application/json'
}

query_parts = [
    f"symbol={payload_strict['symbol']}",
    f"qty={payload_strict['qty']}",
    f"tradeSide={payload_strict['tradeSide']}",
    f"tradeType={payload_strict['tradeType']}",
    f"clientOrderId={payload_strict['clientOrderId']}",
    f"price={payload_strict['price']}"
]
query_string = "&".join(query_parts)

url = f"https://api.coincall.com{endpoint}?{query_string}"
print(f"URL: {url[:150]}...\n")

response = requests.post(url, headers=headers)
print(f"Response: {response.json()}\n")
