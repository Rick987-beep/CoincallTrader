#!/usr/bin/env python3
"""
Production Environment Test Script

This script demonstrates how to test production account reading safely.
Before running, you need to:

1. Set TRADING_ENVIRONMENT=production in your .env file
2. Add your production API credentials:
   COINCALL_API_KEY_PROD=your_production_api_key
   COINCALL_API_SECRET_PROD=your_production_api_secret

This script will only read account information and will NOT place any trades.
"""

import os
from dotenv import load_dotenv
from account_manager import account_manager

def test_production_account_reading():
    """Test production account reading safely"""

    # Load environment variables
    load_dotenv()

    # Check environment
    environment = os.getenv('TRADING_ENVIRONMENT', 'testnet').lower()
    print(f"Environment: {environment}")

    if environment != 'production':
        print("ERROR: Set TRADING_ENVIRONMENT=production in .env file to test production")
        return

    # Check for production credentials
    prod_key = os.getenv('COINCALL_API_KEY_PROD')
    prod_secret = os.getenv('COINCALL_API_SECRET_PROD')

    if not prod_key or not prod_secret:
        print("ERROR: Production API credentials not found in .env file")
        print("Add these lines to your .env file:")
        print("COINCALL_API_KEY_PROD=your_production_api_key")
        print("COINCALL_API_SECRET_PROD=your_production_api_secret")
        return

    print("Production credentials found ✓")
    print("Testing account info reading...")

    try:
        # Test account info (safe - read-only)
        account_info = account_manager.get_account_info()
        if account_info:
            print("✓ Account info retrieved successfully")
            print(f"  Available balance: {account_info.get('available_balance', 'N/A')}")
            print(f"  Total equity: {account_info.get('equity', 'N/A')}")
        else:
            print("✗ Failed to get account info")
            return

        # Test positions (safe - read-only)
        positions = account_manager.get_positions()
        print(f"✓ Positions retrieved: {len(positions)} open positions")

        # Test open orders (safe - read-only)
        orders = account_manager.get_open_orders()
        print(f"✓ Open orders retrieved: {len(orders)} open orders")

        # Test wallet info (safe - read-only)
        wallet = account_manager.get_wallet_info()
        print(f"✓ Wallet info retrieved: {len(wallet)} currencies")

        print("\n🎉 All production account reading tests passed!")
        print("You can now safely proceed with production trading implementation.")

    except Exception as e:
        print(f"✗ Error during production testing: {e}")
        print("Check your production API credentials and network connection.")

if __name__ == "__main__":
    test_production_account_reading()