#!/usr/bin/env python3
"""
Test Order Execution for Long Strangle

This test demonstrates the complete flow:
1. Select strangle options (5 Feb expiry, delta ±0.25)
2. Place orders using the trade execution logic
3. Show order placement, requoting, and status

Note: Testnet orderbooks are typically empty, so orders may not fill,
but we can test the selection and order placement logic.
"""

from market_data import get_btc_futures_price
from option_selection import select_option
from trade_execution import execute_trade, execute_multiple_trades
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def test_strangle_order_execution():
    """Test complete strangle selection and order execution"""
    print("🚀 Testing Long Strangle Order Execution")
    print("=" * 60)

    # Get current BTC/USDT perpetual futures price
    spot_price = get_btc_futures_price()
    print(f"📊 Current BTC/USDT futures price: ${spot_price:.2f}")
    print()

    # Define strangle position config: 5 Feb expiry, delta ±0.25
    strangle_config = {
        'expiry_criteria': {'symbol': '5FEB26'},  # 5 February 2026
        'legs': [
            {
                'option_type': 'C',  # Call
                'strike_criteria': {'type': 'delta', 'value': 0.25},  # Delta +0.25
                'side': 1,  # buy
                'qty': 1
            },
            {
                'option_type': 'P',  # Put
                'strike_criteria': {'type': 'delta', 'value': -0.25},  # Delta -0.25
                'side': 1,  # buy
                'qty': 1
            }
        ]
    }

    selected_options = []

    # Test each leg selection
    for i, leg in enumerate(strangle_config['legs']):
        print(f"🎯 Leg {i+1}: {leg['option_type']} with target {leg['strike_criteria']}")

        symbol = select_option(
            strangle_config['expiry_criteria'],
            leg['strike_criteria'],
            leg['option_type'],
            'BTC'
        )

        if symbol:
            print(f"✅ Selected: {symbol}")
            selected_options.append((leg['option_type'], symbol, leg['side'], leg['qty']))
        else:
            print(f"❌ No option found for {leg['option_type']} leg")
            continue

        print()

    if not selected_options:
        print("❌ No options selected, cannot proceed with order execution")
        return

    print("📋 SELECTED STRANGLE POSITION:")
    print("-" * 40)
    for option_type, symbol, side, qty in selected_options:
        side_text = "BUY" if side == 1 else "SELL"
        print(f"  {side_text} {qty} x {symbol}")
    print()

    # Execute orders one by one to show the process
    print("⚡ EXECUTING ORDERS INDIVIDUALLY:")
    print("-" * 40)

    order_results = []
    for i, (option_type, symbol, side, qty) in enumerate(selected_options):
        print(f"\n🔄 Executing Leg {i+1}: {option_type} {symbol}")
        print("-" * 30)

        # Execute the trade with 30-second timeout
        result = execute_trade(symbol, qty, side, timeout_seconds=30)

        if result:
            print(f"✅ Order completed for {symbol}")
            print(f"   Result: {result}")
            order_results.append(result)
        else:
            print(f"❌ Order failed for {symbol}")
            order_results.append(None)

    print("\n" + "=" * 60)
    print("📊 ORDER EXECUTION SUMMARY:")
    print("=" * 60)

    successful_orders = [r for r in order_results if r is not None]
    failed_orders = len(order_results) - len(successful_orders)

    print(f"Total orders attempted: {len(order_results)}")
    print(f"Successful orders: {len(successful_orders)}")
    print(f"Failed orders: {failed_orders}")

    if successful_orders:
        print("\n✅ Successful orders:")
        for i, result in enumerate(successful_orders):
            print(f"  {i+1}. {result}")

    if failed_orders > 0:
        print(f"\n⚠️  {failed_orders} orders failed (likely due to empty testnet orderbook)")

    print("\n💡 Note: Testnet orderbooks are typically empty, so orders may not fill.")
    print("   This test demonstrates the order selection and placement logic.")

    print("\n" + "=" * 60)
    print("STRANGLE ORDER EXECUTION TEST COMPLETED")
    print("=" * 60)

def test_concurrent_execution():
    """Test concurrent execution of multiple orders"""
    print("\n\n🔄 Testing Concurrent Order Execution")
    print("=" * 50)

    # Use the same selected options from above
    # For this test, we'll assume we have the options selected
    # In a real scenario, you'd select them first

    # Example concurrent trades (using the same options as above)
    concurrent_trades = [
        ('BTCUSD-5FEB26-80000-C', 1, 1, 30),  # Buy 1 call
        ('BTCUSD-5FEB26-76000-P', 1, 1, 30),  # Buy 1 put
    ]

    print("Concurrent trades to execute:")
    for symbol, qty, side, timeout in concurrent_trades:
        side_text = "BUY" if side == 1 else "SELL"
        print(f"  {side_text} {qty} x {symbol} (timeout: {timeout}s)")

    print("\nExecuting concurrently...")
    results = execute_multiple_trades(concurrent_trades)

    print(f"\nResults: {len(results)} orders processed")
    for i, result in enumerate(results):
        if result:
            print(f"  Order {i+1}: ✅ Success - {result}")
        else:
            print(f"  Order {i+1}: ❌ Failed")

if __name__ == "__main__":
    test_strangle_order_execution()
    test_concurrent_execution()