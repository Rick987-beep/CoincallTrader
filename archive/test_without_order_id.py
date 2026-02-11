#!/usr/bin/env python3
"""Test without clientOrderId and with fresh timestamps"""

from config import API_KEY, API_SECRET, BASE_URL
import time
import hashlib
import hmac
import requests

print("\n" + "="*70)
print("TEST 6: WITHOUT clientOrderId")
print("="*70 + "\n")

ts = int(time.time() * 1000)
x_req_ts_diff = 5000
endpoint = '/open/option/order/create/v1'

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

# No clientOrderId
query_string = "symbol=BTCUSD-27FEB26-80000-C&qty=0.1&tradeSide=1&tradeType=1&price=100.0"
url = f"https://api.coincall.com{endpoint}?{query_string}"

print(f"URL: {url}\n")

response = requests.post(url, headers=headers)
print(f"Response: {response.json()}\n")

print("\n" + "="*70)
print("TEST 7: VERY LARGE clientOrderId (as unix timestamp in microseconds)")
print("="*70 + "\n")

ts = int(time.time() * 1000)
x_req_ts_diff = 5000

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

# Very large clientOrderId (microseconds since epoch)
large_order_id = int(time.time() * 1000000)
query_string = f"symbol=BTCUSD-27FEB26-80000-C&qty=0.1&tradeSide=1&tradeType=1&clientOrderId={large_order_id}&price=100.0"
url = f"https://api.coincall.com{endpoint}?{query_string}"

print(f"clientOrderId: {large_order_id}")
print(f"URL (partial): {url[:150]}...\n")

response = requests.post(url, headers=headers)
print(f"Response: {response.json()}\n")

print("\n" + "="*70)
print("TEST 8: CHECK PARAMETER ORDERING IN URL")
print("="*70 + "\n")

ts = int(time.time() * 1000)
x_req_ts_diff = 5000

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

# Try different parameter order (alphabetical)
query_string = "clientOrderId=1234567890&price=100.0&qty=0.1&symbol=BTCUSD-27FEB26-80000-C&tradeSide=1&tradeType=1"
url = f"https://api.coincall.com{endpoint}?{query_string}"

print(f"URL: {url}\n")

response = requests.post(url, headers=headers)
print(f"Response: {response.json()}\n")
