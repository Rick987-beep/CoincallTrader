#!/usr/bin/env python3
"""
Basic test for account manager functionality
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from account_manager import account_manager, get_account_balance, get_account_equity
import logging

logging.basicConfig(level=logging.INFO)

def test_account_manager():
    """Test basic account manager functionality"""

    print("🔍 Testing Account Manager...")

    # Test account info
    print("\n📊 Getting account info...")
    account_info = account_manager.get_account_info()
    if account_info:
        print(f"✅ Account info retrieved:")
        print(f"   Balance: ${account_info['available_balance']:.2f}")
        print(f"   Equity: ${account_info['equity']:.2f}")
        print(f"   Margin Level: {account_info['margin_level']:.2f}")
    else:
        print("❌ Failed to get account info")

    # Test positions
    print("\n📈 Getting positions...")
    positions = account_manager.get_positions()
    print(f"✅ Found {len(positions)} open positions")

    # Test open orders
    print("\n📋 Getting open orders...")
    orders = account_manager.get_open_orders()
    print(f"✅ Found {len(orders)} open orders")

    # Test wallet info
    print("\n💰 Getting wallet info...")
    wallet = account_manager.get_wallet_info()
    if wallet:
        print(f"✅ Wallet info retrieved for {len(wallet)} currencies")
        for currency, info in wallet.items():
            print(f"   {currency}: ${info['available']:.2f} available")
    else:
        print("❌ Failed to get wallet info")

    # Test convenience functions
    print("\n🔧 Testing convenience functions...")
    balance = get_account_balance()
    equity = get_account_equity()
    print(f"✅ Balance: ${balance:.2f}, Equity: ${equity:.2f}")

    # Test risk metrics
    print("\n⚠️  Getting risk metrics...")
    risk = account_manager.get_risk_metrics()
    if risk:
        print(f"✅ Risk metrics:")
        print(f"   Unrealized P&L: ${risk['total_unrealized_pnl']:.2f}")
        print(f"   Margin Utilization: {risk['margin_utilization']:.1f}%")
        print(f"   Open Positions: {risk['open_positions_count']}")
    else:
        print("❌ Failed to get risk metrics")

    print("\n🎉 Account Manager test completed!")

if __name__ == "__main__":
    test_account_manager()