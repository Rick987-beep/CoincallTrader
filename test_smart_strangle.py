#!/usr/bin/env python3
"""
Test: Smart Orderbook Strangle Execution

Opens a long strangle using smart multi-leg orderbook execution,
waits for fills, then closes the position.

Test Parameters:
  - 3 chunks
  - Quote at orderbook bid/ask for 60 seconds per chunk
  - Reprice every 10 seconds (minimum)
  - If no fills after time expires, use aggressive limit orders
"""

import logging
import time
from trade_lifecycle import LifecycleManager, TradeLeg, TradeState
from multileg_orderbook import create_smart_config

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """Run the smart strangle test."""
    
    print("=" * 80)
    print("TEST: Smart Orderbook Strangle Execution")
    print("=" * 80)
    
    # Initialize lifecycle manager
    manager = LifecycleManager(
        rfq_notional_threshold=100000.0,  # Force smart mode for testing
        smart_notional_threshold=1000.0,
    )
    print(f"\n✓ LifecycleManager initialized")
    print(f"  - RFQ threshold: ${manager.rfq_notional_threshold:,.0f}")
    print(f"  - Smart threshold: ${manager.smart_notional_threshold:,.0f}")
    
    # =========================================================================
    # PART 1: OPEN THE STRANGLE
    # =========================================================================
    
    print("\n" + "=" * 80)
    print("PART 1: OPENING STRANGLE")
    print("=" * 80)
    
    # TODO: Replace with actual contract names
    call_contract = "BTCUSD-27FEB26-85000-C"   # BTC 27FEB 85000 Call
    put_contract = "BTCUSD-27FEB26-40000-P"    # BTC 27FEB 40000 Put
    
    # Create strangle legs
    open_legs = [
        TradeLeg(symbol=call_contract, qty=0.3, side=1),  # Long call, 0.3 contracts
        TradeLeg(symbol=put_contract, qty=0.3, side=1),   # Long put, 0.3 contracts
    ]
    
    # Create smart execution config
    smart_config = create_smart_config(
        chunk_count=3,
        time_per_chunk=60.0,          # 60 seconds per chunk (multiple of 10)
        quoting_strategy="top_of_book",  # Quote at orderbook bid/ask
        reprice_interval=10.0,        # Reprice every 10 seconds (minimum)
    )
    
    # Create trade with smart execution
    trade = manager.create(
        legs=open_legs,
        execution_mode="smart",  # Explicit smart mode
        smart_config=smart_config,
        metadata={
            "strategy": "long_strangle",
            "call": call_contract,
            "put": put_contract,
        }
    )
    
    print(f"\n✓ Trade created: {trade.id}")
    print(f"  Legs: {len(trade.open_legs)}")
    print(f"  - {open_legs[0].symbol} (BUY 0.3)")
    print(f"  - {open_legs[1].symbol} (BUY 0.3)")
    print(f"  Mode: {trade.execution_mode}")
    print(f"  Smart config:")
    print(f"    - Chunks: {smart_config.chunk_count} (0.1 per chunk)")
    print(f"    - Time per chunk: {smart_config.time_per_chunk}s")
    print(f"    - Quoting strategy: {smart_config.quoting_strategy}")
    print(f"    - Reprice interval: {smart_config.reprice_interval}s")
    
    # Open the trade (execute smart algorithm)
    print(f"\n→ Opening trade {trade.id}...")
    start_time = time.time()
    opened = manager.open(trade.id)
    
    if not opened:
        print(f"✗ Failed to open trade: {trade.error}")
        return False
    
    elapsed = time.time() - start_time
    print(f"✓ Trade opened successfully")
    print(f"  State: {trade.state.value}")
    print(f"  Opened at: {trade.opened_at}")
    print(f"  Execution time: {elapsed:.1f}s")
    
    # Show final leg status from positions
    print(f"\n" + "=" * 80)
    print("FINAL POSITION STATUS")
    print("=" * 80)
    
    from account_manager import account_manager
    positions = account_manager.get_positions(force_refresh=True)
    
    for leg in trade.open_legs:
        pos_qty = 0.0
        for pos in positions:
            if pos.get('symbol') == leg.symbol:
                pos_qty = abs(float(pos.get('qty', 0)))
                break
        
        fill_pct = (pos_qty / leg.qty * 100) if leg.qty > 0 else 0
        print(f"  {leg.symbol}:")
        print(f"    Target: {leg.qty}")
        print(f"    Filled: {pos_qty} ({fill_pct:.1f}%)")
    
    print(f"\n✓ Test completed!")
    return True


if __name__ == "__main__":
    try:
        success = main()
        exit_code = 0 if success else 1
    except Exception as e:
        logger.error(f"Test failed with exception: {e}", exc_info=True)
        exit_code = 1
    
    print("\n" + "=" * 80)
    exit(exit_code)
