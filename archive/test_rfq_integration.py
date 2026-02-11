#!/usr/bin/env python3
"""
RFQ Integration Test - Production

Tests the complete RFQ workflow including:
  - Creating RFQ requests
  - Receiving quotes from market makers
  - Accepting quotes (buy or sell)
  - Cancelling RFQs

Usage:
    python tests/test_rfq_integration.py --action buy    # Open position
    python tests/test_rfq_integration.py --action sell   # Close position
    python tests/test_rfq_integration.py --cancel        # Test without filling

Structure tested: 13FEB26 strangle (80000C + 58000P)
"""

import argparse
import sys
import time
import os
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfq import RFQExecutor, OptionLeg

# Force unbuffered output
sys.stdout.reconfigure(line_buffering=True)


def log(msg: str):
    """Print with timestamp."""
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] {msg}", flush=True)


def run_rfq_test(action: str = "buy", cancel_only: bool = False, timeout: int = 60):
    """
    Run an RFQ test.
    
    Args:
        action: "buy" or "sell"
        cancel_only: If True, don't accept any quotes (just test flow)
        timeout: Timeout in seconds
    """
    print("=" * 60, flush=True)
    if cancel_only:
        print("RFQ TEST - CANCEL ONLY (no fills)", flush=True)
    else:
        print(f"RFQ TEST - {action.upper()}", flush=True)
    print("=" * 60, flush=True)
    print(flush=True)
    
    # Initialize
    log("Initializing RFQ executor...")
    executor = RFQExecutor()
    
    # Define the structure
    call_symbol = "BTCUSD-13FEB26-80000-C"
    put_symbol = "BTCUSD-13FEB26-58000-P"
    qty = 1.0
    
    log(f"Structure: {call_symbol} + {put_symbol}")
    log(f"Action: {action.upper()}")
    log(f"Quantity: {qty}")
    print(flush=True)
    
    # Create legs
    legs = [
        OptionLeg(instrument=call_symbol, side='buy', qty=qty),
        OptionLeg(instrument=put_symbol, side='buy', qty=qty),
    ]
    
    # Get orderbook baseline
    log("Getting orderbook baseline...")
    orderbook_cost = executor.get_orderbook_cost(legs)
    if orderbook_cost:
        log(f"  Orderbook cost: ${orderbook_cost:.2f}")
    else:
        log("  WARNING: Could not get orderbook cost")
    print(flush=True)
    
    # Create RFQ
    log("Creating RFQ...")
    rfq_response = executor.create_rfq(legs)
    
    if not rfq_response:
        log("ERROR: Failed to create RFQ!")
        return False
    
    request_id = rfq_response.get('requestId')
    log(f"  ✅ RFQ Created! Request ID: {request_id}")
    print(flush=True)
    
    # Monitor for quotes
    poll_interval = 3
    start_time = time.time()
    seen_quotes = set()
    best_quote = None
    accepted = False
    want_to_buy = action.lower() == "buy"
    
    log(f"Monitoring for quotes ({timeout}s timeout)...")
    log(f"Looking for {'BUY' if want_to_buy else 'SELL'} quotes...")
    print(flush=True)
    
    while time.time() - start_time < timeout:
        elapsed = int(time.time() - start_time)
        remaining = timeout - elapsed
        
        # Get quotes
        quotes = executor.get_quotes(request_id)
        
        if quotes:
            for quote in quotes:
                quote_id = quote.quote_id
                
                if quote_id not in seen_quotes:
                    seen_quotes.add(quote_id)
                    
                    # Check direction
                    if want_to_buy and not quote.is_we_buy:
                        log(f"📨 SELL quote {quote_id}: ${abs(quote.total_cost):.2f} - skipping")
                        continue
                    elif not want_to_buy and not quote.is_we_sell:
                        log(f"📨 BUY quote {quote_id}: ${quote.total_cost:.2f} - skipping")
                        continue
                    
                    # This is the right direction
                    quote_type = "WE BUY" if quote.is_we_buy else "WE SELL"
                    log(f"📨 {quote_type} quote {quote_id}")
                    log(f"   Total: ${quote.total_cost:.2f}")
                    
                    for leg in quote.legs:
                        log(f"   {leg.get('side')} {leg.get('quantity')} x {leg.get('instrumentName')} @ ${float(leg.get('price', 0)):.2f}")
                    
                    best_quote = quote
                    
                    if not cancel_only:
                        log("")
                        if quote.is_we_buy:
                            log(f"🎯 Accepting - paying ${quote.total_cost:.2f}")
                        else:
                            log(f"🎯 Accepting - receiving ${abs(quote.total_cost):.2f}")
                        
                        success = executor.accept_quote(request_id, quote_id)
                        
                        if success:
                            log("   ✅ TRADE EXECUTED!")
                            accepted = True
                            break
                        else:
                            log("   ❌ Failed to accept, continuing...")
                    else:
                        log("   (cancel_only mode - not accepting)")
                    
                    print(flush=True)
        
        if accepted:
            break
        
        # Status update
        if elapsed % 15 == 0:
            log(f"⏳ {remaining}s remaining... ({len(seen_quotes)} quotes seen)")
        
        time.sleep(poll_interval)
    
    # Cancel if not accepted
    if not accepted:
        log("")
        log("Cancelling RFQ...")
        if executor.cancel_rfq(request_id):
            log("   ✅ RFQ cancelled")
        else:
            log("   ⚠️ Cancel failed (may have expired)")
    
    # Summary
    print(flush=True)
    print("=" * 60, flush=True)
    print("SUMMARY", flush=True)
    print("=" * 60, flush=True)
    log(f"Request ID: {request_id}")
    log(f"Quotes received: {len(seen_quotes)}")
    if accepted and best_quote:
        if best_quote.total_cost > 0:
            log(f"Result: BOUGHT for ${best_quote.total_cost:.2f}")
        else:
            log(f"Result: SOLD for ${abs(best_quote.total_cost):.2f}")
    else:
        log("Result: CANCELLED")
    print("=" * 60, flush=True)
    
    return accepted


def main():
    parser = argparse.ArgumentParser(description='RFQ Integration Test')
    parser.add_argument('--action', choices=['buy', 'sell'], default='buy',
                        help='Action: buy (open) or sell (close)')
    parser.add_argument('--cancel', action='store_true',
                        help='Cancel-only mode (no fills)')
    parser.add_argument('--timeout', type=int, default=60,
                        help='Timeout in seconds')
    
    args = parser.parse_args()
    
    success = run_rfq_test(
        action=args.action,
        cancel_only=args.cancel,
        timeout=args.timeout
    )
    
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
