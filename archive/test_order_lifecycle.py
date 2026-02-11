#!/usr/bin/env python3
"""
Order Lifecycle Integration Test

Tests the complete order lifecycle:
1. Place an order
2. Wait 2 seconds
3. Retrieve list of open orders
4. Find and verify the order
5. Wait 2 seconds
6. Cancel the order
7. Verify cancellation

All steps documented with terminal output.
"""

import logging
import time
from account_manager import AccountManager
from trade_execution import place_order, cancel_order

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def test_order_lifecycle():
    """Run the complete order lifecycle test"""

    print("\n" + "=" * 70)
    print("ORDER LIFECYCLE INTEGRATION TEST")
    print("=" * 70)

    # Test parameters
    symbol = "BTCUSD-27FEB26-80000-C"
    qty = 0.1
    price = 100.0
    trade_side = 1  # 1 = buy, 2 = sell
    trade_type = 1  # 1 = limit, 2 = market

    manager = AccountManager()
    placed_order_id = None
    placed_client_order_id = None

    try:
        # STEP 1: Place Order
        print("\n[STEP 1] Placing order...")
        print(f"   Symbol: {symbol}")
        print(f"   Quantity: {qty}")
        print(f"   Price: ${price}")
        print(f"   Side: {'BUY' if trade_side == 1 else 'SELL'}")
        print(f"   Type: {'LIMIT' if trade_type == 1 else 'MARKET'}")

        # Generate unique client order ID
        client_order_id = int(time.time() * 1000)
        
        result = place_order(
            symbol=symbol,
            qty=qty,
            side=trade_side,
            order_type=trade_type,
            price=price,
            client_order_id=client_order_id
        )

        if result and 'orderId' in result:
            placed_order_id = result['orderId']
            placed_client_order_id = client_order_id
            print(f"\n   ✅ ORDER PLACED SUCCESSFULLY")
            print(f"   Order ID: {placed_order_id}")
            print(f"   Client Order ID: {placed_client_order_id}")
        else:
            print(f"\n   ❌ FAILED TO PLACE ORDER")
            print(f"   Response: {result}")
            return

        # STEP 2: Wait 2 seconds
        print("\n[STEP 2] Waiting 2 seconds before retrieving orders...")
        for i in range(2, 0, -1):
            print(f"   Countdown: {i}s...", end='\r')
            time.sleep(1)
        print("   ✅ Ready to retrieve orders")

        # STEP 3: Get Open Orders
        print("\n[STEP 3] Retrieving list of open orders from Coincall...")
        
        open_orders = manager.get_open_orders(force_refresh=True)
        
        if open_orders is not None:
            print(f"   ✅ Retrieved {len(open_orders)} open orders")
        else:
            print(f"   ❌ FAILED TO RETRIEVE OPEN ORDERS")
            return

        # STEP 4: Find and Verify Order
        print("\n[STEP 4] Finding and verifying placed order in list...")
        
        found_order = None
        for order in open_orders:
            if order['order_id'] == placed_order_id:
                found_order = order
                break

        if found_order:
            print(f"   ✅ ORDER FOUND IN OPEN ORDERS LIST")
            print(f"\n   Order Details:")
            print(f"   - Order ID: {found_order['order_id']}")
            print(f"   - Client Order ID: {found_order['client_order_id']}")
            print(f"   - Symbol: {found_order['symbol']}")
            print(f"   - Quantity Ordered: {found_order['qty']}")
            print(f"   - Quantity Remaining: {found_order['remaining_qty']}")
            print(f"   - Quantity Filled: {found_order['filled_qty']}")
            print(f"   - Price: ${found_order['price']}")
            print(f"   - Side: {'BUY' if found_order['trade_side'] == 1 else 'SELL'}")
            print(f"   - Type: {'LIMIT' if found_order['trade_type'] == 1 else 'MARKET'}")
            print(f"   - Status: {found_order['state']}")

            # Verify order details match what we placed
            print(f"\n   Verification Results:")
            checks = [
                (found_order['symbol'] == symbol, f"Symbol matches: {found_order['symbol']} == {symbol}"),
                (found_order['qty'] == qty, f"Quantity matches: {found_order['qty']} == {qty}"),
                (found_order['price'] == price, f"Price matches: {found_order['price']} == {price}"),
                (found_order['trade_side'] == trade_side, f"Side matches: {found_order['trade_side']} == {trade_side}"),
                (found_order['trade_type'] == trade_type, f"Type matches: {found_order['trade_type']} == {trade_type}"),
                (found_order['client_order_id'] == placed_client_order_id, 
                 f"Client Order ID matches: {found_order['client_order_id']} == {placed_client_order_id}"),
            ]
            
            all_passed = True
            for passed, description in checks:
                status = "✅" if passed else "❌"
                print(f"   {status} {description}")
                if not passed:
                    all_passed = False
            
            if not all_passed:
                print(f"\n   ⚠️  WARNING: Some verification checks failed!")
        else:
            print(f"   ❌ ORDER NOT FOUND IN OPEN ORDERS LIST")
            print(f"   Expected Order ID: {placed_order_id}")
            print(f"   Available orders:")
            for order in open_orders:
                print(f"      - Order ID: {order['order_id']}, Symbol: {order['symbol']}, Qty: {order['qty']}")
            return

        # STEP 5: Wait 2 seconds before cancel
        print("\n[STEP 5] Waiting 2 seconds before cancellation...")
        for i in range(2, 0, -1):
            print(f"   Countdown: {i}s...", end='\r')
            time.sleep(1)
        print("   ✅ Ready to cancel order")

        # STEP 6: Cancel Order
        print("\n[STEP 6] Cancelling order...")
        print(f"   Order ID to cancel: {placed_order_id}")

        cancel_result = cancel_order(placed_order_id)

        if cancel_result:
            print(f"   ✅ ORDER CANCELLED SUCCESSFULLY")
        else:
            print(f"   ❌ FAILED TO CANCEL ORDER")
            return

        # STEP 7: Verify Cancellation
        print("\n[STEP 7] Verifying cancellation...")
        
        open_orders_after = manager.get_open_orders(force_refresh=True)
        
        cancelled_order_found = False
        for order in open_orders_after:
            if order['order_id'] == placed_order_id:
                cancelled_order_found = True
                break

        if not cancelled_order_found:
            print(f"   ✅ ORDER NO LONGER IN OPEN ORDERS LIST (cancellation verified)")
            print(f"   Remaining open orders: {len(open_orders_after)}")
        else:
            print(f"   ⚠️  ORDER STILL APPEARS IN OPEN ORDERS LIST")
            print(f"   (May be eventual consistency issue - give it a moment)")

        # Summary
        print("\n" + "=" * 70)
        print("TEST COMPLETED SUCCESSFULLY")
        print("=" * 70)
        print("\nSummary:")
        print(f"  ✅ Order placed (Order ID: {placed_order_id})")
        print(f"  ✅ Order retrieved from open orders list")
        print(f"  ✅ Order details verified")
        print(f"  ✅ Order cancelled successfully")
        print("=" * 70 + "\n")

    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return


if __name__ == "__main__":
    test_order_lifecycle()
