#!/usr/bin/env python3
"""
Production Account Manager with Proper Authentication

Uses correct Coincall API signature format to authenticate requests.
"""

import os
import hashlib
import hmac
import time
import requests
import logging
from config import API_KEY, API_SECRET, BASE_URL, ENVIRONMENT

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class CoincallAccountManager:
    """Manages Coincall account operations with proper API authentication"""
    
    def __init__(self):
        self.api_key = API_KEY
        self.api_secret = API_SECRET
        self.base_url = BASE_URL
        self.session = requests.Session()
        self.environment = ENVIRONMENT
    
    def _create_signature(self, method, endpoint, ts, x_req_ts_diff=5000):
        """
        Create HMAC SHA256 signature according to Coincall API spec
        
        Prehash format: METHOD + ENDPOINT + ?uuid=api_key&ts=timestamp&x-req-ts-diff=diff
        """
        prehash = f'{method}{endpoint}?uuid={self.api_key}&ts={ts}&x-req-ts-diff={x_req_ts_diff}'
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            prehash.encode('utf-8'),
            hashlib.sha256
        ).hexdigest().upper()
        return signature
    
    def _make_request(self, method, endpoint):
        """Make authenticated API request"""
        ts = int(time.time() * 1000)
        x_req_ts_diff = 5000
        
        # Create signature
        signature = self._create_signature(method, endpoint, ts, x_req_ts_diff)
        
        # Prepare headers
        headers = {
            'X-CC-APIKEY': self.api_key,
            'sign': signature,
            'ts': str(ts),
            'X-REQ-TS-DIFF': str(x_req_ts_diff),
            'Content-Type': 'application/json'
        }
        
        url = f'{self.base_url}{endpoint}'
        
        try:
            response = self.session.request(method, url, headers=headers)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Request failed: {e}")
            return {'code': 500, 'msg': str(e), 'data': None}
    
    def get_account_summary(self):
        """Get account summary information"""
        response = self._make_request('GET', '/open/account/summary/v1')
        
        if response.get('code') == 0:
            return {
                'success': True,
                'data': response.get('data', {})
            }
        else:
            logger.error(f"Account summary failed: {response}")
            return {
                'success': False,
                'error': response.get('msg')
            }
    
    def get_user_info(self):
        """Get user information"""
        response = self._make_request('GET', '/open/user/info/v1')
        
        if response.get('code') == 0:
            return {
                'success': True,
                'data': response.get('data', {})
            }
        else:
            logger.error(f"User info failed: {response}")
            return {
                'success': False,
                'error': response.get('msg')
            }
    
    def get_positions(self):
        """Get open option positions"""
        response = self._make_request('GET', '/open/option/position/get/v1')
        
        if response.get('code') == 0:
            positions = response.get('data', [])
            if isinstance(positions, list):
                logger.info(f"Retrieved {len(positions)} open positions")
                return {
                    'success': True,
                    'positions': positions,
                    'count': len(positions)
                }
            else:
                return {
                    'success': True,
                    'positions': [],
                    'count': 0
                }
        else:
            logger.error(f"Get positions failed: {response}")
            return {
                'success': False,
                'error': response.get('msg'),
                'positions': []
            }

def test_production_account():
    """Test production account access"""
    
    print("=" * 70)
    print("PRODUCTION ACCOUNT ACCESS TEST")
    print("=" * 70)
    print(f"Environment: {ENVIRONMENT}")
    print(f"Base URL: {BASE_URL}")
    print(f"API Key: {API_KEY[:20]}...")
    print()
    
    manager = CoincallAccountManager()
    
    # Test user info
    print("1. Testing User Info...")
    user_result = manager.get_user_info()
    if user_result['success']:
        user_data = user_result['data']
        print(f"   ✅ User Info Retrieved")
        print(f"   Name: {user_data.get('name', 'N/A')}")
        print(f"   Email: {user_data.get('email', 'N/A')}")
        print(f"   User ID: {user_data.get('userId', 'N/A')}")
    else:
        print(f"   ❌ Failed: {user_result.get('error')}")
    
    # Test account summary
    print("\n2. Testing Account Summary...")
    account_result = manager.get_account_summary()
    if account_result['success']:
        data = account_result['data']
        print(f"   ✅ Account Summary Retrieved")
        print(f"   Total Balance (USDT): {data.get('totalUsdtValue', 'N/A')}")
        print(f"   Available Margin: {data.get('availableMargin', 'N/A')}")
        print(f"   Equity: {data.get('equity', 'N/A')}")
        print(f"   Accounts: {len(data.get('accounts', []))} accounts found")
    else:
        print(f"   ❌ Failed: {account_result.get('error')}")
    
    # Test positions
    print("\n3. Testing Positions...")
    positions_result = manager.get_positions()
    if positions_result['success']:
        count = positions_result['count']
        print(f"   ✅ Positions Retrieved")
        print(f"   Open Positions: {count}")
        if count > 0:
            pos = positions_result['positions'][0]
            print(f"   First Position: {pos.get('symbol', 'N/A')}")
    else:
        print(f"   ❌ Failed: {positions_result.get('error')}")
    
    print("\n" + "=" * 70)
    if user_result['success'] and account_result['success'] and positions_result['success']:
        print("🎉 ALL TESTS PASSED! Production account access is working!")
    else:
        print("⚠️  Some tests failed. Check API key permissions in Coincall dashboard.")
    print("=" * 70)

if __name__ == "__main__":
    test_production_account()
