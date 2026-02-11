#!/usr/bin/env python3
"""
Production API Diagnostic Tool

This script helps diagnose issues with production API credentials.
"""

import os
from dotenv import load_dotenv
from coincall import Options
import json

load_dotenv()

def diagnose_production_api():
    """Run comprehensive diagnostics on production API"""
    
    print("=" * 60)
    print("COINCALL PRODUCTION API DIAGNOSTICS")
    print("=" * 60)
    
    # Check environment variables
    print("\n1. Environment Variables:")
    print(f"   TRADING_ENVIRONMENT: {os.getenv('TRADING_ENVIRONMENT')}")
    
    api_key_prod = os.getenv('COINCALL_API_KEY_PROD')
    api_secret_prod = os.getenv('COINCALL_API_SECRET_PROD')
    
    print(f"   API Key (PROD): {'✓ Set' if api_key_prod else '✗ Missing'}")
    print(f"   API Secret (PROD): {'✓ Set' if api_secret_prod else '✗ Missing'}")
    
    if not api_key_prod or not api_secret_prod:
        print("\n❌ Production credentials are missing!")
        return
    
    print(f"   API Key length: {len(api_key_prod)}")
    print(f"   API Secret length: {len(api_secret_prod)}")
    
    # Test API connection
    print("\n2. API Connection Test:")
    base_url = 'https://api.coincall.com'
    print(f"   Base URL: {base_url}")
    
    try:
        options_api = Options.OptionsAPI(api_key_prod, api_secret_prod)
        options_api.domain = base_url
        
        # Test various endpoints
        print("\n3. Testing Endpoints:")
        
        endpoints_to_test = [
            ('/open/option/account/info/v1', 'Account Info'),
            ('/open/option/account/balance/v1', 'Account Balance'),
            ('/open/option/position/v1', 'Positions'),
            ('/open/option/order/pending/v1', 'Pending Orders'),
        ]
        
        for endpoint, name in endpoints_to_test:
            try:
                response = options_api.client.get(endpoint)
                data = response.json()
                
                status = "✓" if data.get('code') == 0 else "✗"
                print(f"   {status} {name}: code={data.get('code')}, msg={data.get('msg')}")
                
                if data.get('code') == 0:
                    print(f"      Data keys: {list(data.get('data', {}).keys()) if isinstance(data.get('data'), dict) else type(data.get('data'))}")
                    
            except Exception as e:
                print(f"   ✗ {name}: Exception - {str(e)}")
        
        # Common error codes
        print("\n4. Common Error Codes:")
        print("   Code 0: Success")
        print("   Code 400: Bad request (check parameters)")
        print("   Code 401: Unauthorized (check API keys)")
        print("   Code 403: Forbidden (check permissions/IP whitelist)")
        print("   Code 500: Server error (API keys may not be enabled)")
        
        print("\n5. Troubleshooting Steps:")
        print("   • Verify API keys are enabled in Coincall dashboard")
        print("   • Check if API keys have options trading permissions")
        print("   • Verify account has completed KYC if required")
        print("   • Check if IP whitelisting is enabled (try disabling)")
        print("   • Ensure account has options trading activated")
        print("   • Try regenerating API keys in the dashboard")
        
    except Exception as e:
        print(f"\n❌ Error initializing API: {e}")

if __name__ == "__main__":
    diagnose_production_api()
