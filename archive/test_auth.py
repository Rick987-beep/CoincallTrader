#!/usr/bin/env python3
"""
Coincall API Authentication Test

Tests the exact signature format required by Coincall API according to documentation.
"""

import hashlib
import hmac
import time
import requests
from config import API_KEY, API_SECRET, BASE_URL

def create_signature(method, uri, params, api_secret, ts, x_req_ts_diff=5000):
    """
    Create proper HMAC SHA256 signature for Coincall API
    
    According to docs:
    prehashString = METHOD + URI + ?uuid=api_key&ts=timestamp&x-req-ts-diff=diff
    Then sign with HMAC SHA256
    """
    # Build prehash string according to docs
    # Format: METHOD + URI + ?uuid=your_api_key&ts=your_timestamp&x-req-ts-diff=your_ts_diff
    
    prehash = f"{method}{uri}?uuid={API_KEY}&ts={ts}&x-req-ts-diff={x_req_ts_diff}"
    
    # HMAC SHA256 signature
    signature = hmac.new(
        api_secret.encode('utf-8'),
        prehash.encode('utf-8'),
        hashlib.sha256
    ).hexdigest().upper()
    
    return signature

def test_account_info():
    """Test account info endpoint with proper authentication"""
    print("=" * 60)
    print("COINCALL API AUTHENTICATION TEST")
    print("=" * 60)
    
    # Parameters
    endpoint = '/open/option/account/info/v1'
    method = 'GET'
    ts = int(time.time() * 1000)
    x_req_ts_diff = 5000
    
    print(f"\n1. Request Parameters:")
    print(f"   Method: {method}")
    print(f"   Endpoint: {endpoint}")
    print(f"   Timestamp: {ts}")
    print(f"   X-REQ-TS-DIFF: {x_req_ts_diff}")
    print(f"   API Key: {API_KEY[:20]}...")
    
    # Create signature
    signature = create_signature(method, endpoint, {}, API_SECRET, ts, x_req_ts_diff)
    
    print(f"\n2. Signature Information:")
    print(f"   Signature: {signature}")
    print(f"   Signature length: {len(signature)}")
    
    # Prepare headers
    headers = {
        'X-CC-APIKEY': API_KEY,
        'sign': signature,
        'ts': str(ts),
        'X-REQ-TS-DIFF': str(x_req_ts_diff),
        'Content-Type': 'application/json'
    }
    
    print(f"\n3. Headers:")
    for key, value in headers.items():
        if 'APIKEY' in key or 'sign' in key:
            print(f"   {key}: {value[:20]}...")
        else:
            print(f"   {key}: {value}")
    
    # Make request
    print(f"\n4. Making Request:")
    url = f"{BASE_URL}{endpoint}"
    print(f"   URL: {url}")
    
    response = requests.get(url, headers=headers)
    
    print(f"\n5. Response:")
    print(f"   Status Code: {response.status_code}")
    print(f"   Response: {response.json()}")
    
    if response.status_code == 200:
        data = response.json()
        if data.get('code') == 0:
            print("\n✅ SUCCESS! Account info retrieved successfully")
            print(f"   Available Balance: {data.get('data', {}).get('availableBalance', 'N/A')}")
        else:
            print(f"\n❌ API Error: {data}")
            if data.get('code') == 500:
                print("   Error Code 500 usually means:")
                print("   - API keys not enabled for this account type")
                print("   - API keys lack required permissions")
                print("   - Account settings restrict API access")
    else:
        print(f"\n❌ HTTP Error {response.status_code}")

def test_public_endpoint():
    """Test public endpoint (no authentication)"""
    print("\n" + "=" * 60)
    print("PUBLIC ENDPOINT TEST (No Authentication)")
    print("=" * 60)
    
    url = f"{BASE_URL}/open/public/config/v1"
    print(f"\nURL: {url}")
    
    response = requests.get(url)
    print(f"Status: {response.status_code}")
    data = response.json()
    
    if data.get('code') == 0:
        print("✅ Public endpoint working")
        if 'optionConfig' in data.get('data', {}):
            print(f"   Available symbols: {list(data['data']['optionConfig'].keys())[:5]}...")
    else:
        print(f"❌ Error: {data}")

if __name__ == "__main__":
    print("Testing Coincall API Authentication...\n")
    
    # Test public endpoint first
    test_public_endpoint()
    
    # Test authenticated endpoint
    test_account_info()
    
    print("\n" + "=" * 60)
    print("TROUBLESHOOTING GUIDE")
    print("=" * 60)
    print("""
If you're getting error 500:

1. **Verify API Key Permissions**
   - Log into Coincall dashboard
   - Go to API Keys settings
   - Check that "Options Trading" permission is enabled
   - Ensure "Read" and "Write" permissions are checked

2. **Check Account Status**
   - Verify account has options trading enabled
   - Ensure account KYC is complete
   - Check if account is in "good standing"

3. **Timestamp Synchronization**
   - Ensure your system time is synchronized with NTP
   - The server time should be within X-REQ-TS-DIFF (5000ms) of client time

4. **API Key Activation**
   - New API keys might need to be activated
   - Try regenerating API keys in dashboard
   - Wait a few minutes for activation

5. **IP Whitelisting**
   - Check if your IP is whitelisted (if enabled)
   - If IP restrictions are enabled, add your current IP
   - Or disable IP whitelist temporarily for testing
""")