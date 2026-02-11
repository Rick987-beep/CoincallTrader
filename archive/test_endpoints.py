#!/usr/bin/env python3
"""Test Coincall API endpoints"""

import hashlib
import hmac
import time
import requests
from config import API_KEY, API_SECRET, BASE_URL

def make_request(endpoint, method='GET'):
    ts = int(time.time() * 1000)
    x_req_ts_diff = 5000
    
    prehash = f'{method}{endpoint}?uuid={API_KEY}&ts={ts}&x-req-ts-diff={x_req_ts_diff}'
    signature = hmac.new(API_SECRET.encode(), prehash.encode(), hashlib.sha256).hexdigest().upper()
    
    headers = {
        'X-CC-APIKEY': API_KEY,
        'sign': signature,
        'ts': str(ts),
        'X-REQ-TS-DIFF': str(x_req_ts_diff),
        'Content-Type': 'application/json'
    }
    
    url = f'{BASE_URL}{endpoint}'
    response = requests.get(url, headers=headers)
    return response.status_code, response.json()

# Test different account endpoints
endpoints = [
    '/open/account/summary/v1',
    '/open/user/info/v1',
    '/open/option/position/get/v1',
]

print("Testing Account Endpoints:")
for endpoint in endpoints:
    status, response = make_request(endpoint)
    code = response.get('code')
    print(f'\n{endpoint}')
    print(f'  HTTP: {status}, API Code: {code}')
    if code == 0:
        print(f'  ✅ SUCCESS')
        if 'data' in response:
            print(f'  Data keys: {list(response["data"].keys())[:5]}...')
    else:
        print(f'  Message: {response.get("msg")}')
