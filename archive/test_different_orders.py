#!/usr/bin/env python3
"""Test with completely different option symbol"""

from config import API_KEY, API_SECRET, BASE_URL
import time
import hashlib
import hmac
import requests

print("\n" + "="*70)
print("TEST 9: DIFFERENT OPTION (BTC 6FEB 76000 CALL)")
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

query_string = "symbol=BTCUSD-6FEB26-76000-C&qty=0.01&tradeSide=1&tradeType=1&clientOrderId=999&price=200.0"
url = f"https://api.coincall.com{endpoint}?{query_string}"

print(f"URL: {url}\n")

response = requests.post(url, headers=headers)
print(f"Response: {response.json()}\n")

print("\n" + "="*70)
print("TEST 10: SELL ORDER INSTEAD OF BUY")
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

# Sell order instead of buy (tradeSide=2)
query_string = "symbol=BTCUSD-27FEB26-80000-C&qty=0.1&tradeSide=2&tradeType=1&clientOrderId=888&price=900.0"
url = f"https://api.coincall.com{endpoint}?{query_string}"

print(f"URL: {url}\n")

response = requests.post(url, headers=headers)
print(f"Response: {response.json()}\n")

print("\n" + "="*70)
print("TEST 11: MARKET ORDER INSTEAD OF LIMIT")
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

# Market order (tradeType=2, no price)
query_string = "symbol=BTCUSD-27FEB26-80000-C&qty=0.1&tradeSide=1&tradeType=2&clientOrderId=777"
url = f"https://api.coincall.com{endpoint}?{query_string}"

print(f"URL: {url}\n")

response = requests.post(url, headers=headers)
print(f"Response: {response.json()}\n")
