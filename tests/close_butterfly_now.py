#!/usr/bin/env python3
"""
Quick script to close butterfly positions with aggressive limit orders.
"""

import logging
import time
from trade_execution import TradeExecutor
from market_data import get_option_orderbook
from account_manager import account_manager

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def get_aggressive_price(symbol: str, side: int) -> float:
    """Get aggressive limit price from orderbook (crosses the spread)."""
    orderbook = get_option_orderbook(symbol)
    
    if not orderbook:
        logger.error(f"No orderbook data for {symbol}")
        return None
    
    asks = orderbook.get('asks', [])
    bids = orderbook.get('bids', [])
    
    if side == 1:  # BUY - hit the ask
        if not asks or len(asks) == 0:
            logger.error(f"No asks for {symbol}")
            return None
        return float(asks[0]['price'])  # Best ask price
    else:  # SELL - hit the bid
        if not bids or len(bids) == 0:
            logger.error(f"No bids for {symbol}")
            return None
        return float(bids[0]['price'])  # Best bid price


def main():
    logger.info("=" * 80)
    logger.info("EMERGENCY BUTTERFLY CLOSE")
    logger.info("=" * 80)
    
    # Get current positions  
    logger.info("\nCurrent positions:")
    positions = account_manager.get_positions(force_refresh=True)
    
    butterfly_symbols = [
        "BTCUSD-27FEB26-80000-C",
        "BTCUSD-27FEB26-82000-C",
        "BTCUSD-27FEB26-84000-C"
    ]
    
    # Build close legs based on actual positions
    legs_to_close = []
    for pos in positions:
        symbol = pos.get('symbol')
        if symbol in butterfly_symbols:
            qty = float(pos.get('qty', 0))
            trade_side = pos.get('trade_side')  # 1=long, 2=short
            
            # To close: reverse the trade_side
            close_side = 2 if trade_side == 1 else 1
            close_side_label = "SELL" if close_side == 2 else "BUY"
            
            logger.info(f"  {symbol}: {qty:.3f} ({'LONG' if trade_side == 1 else 'SHORT'}) -> {close_side_label} to close")
            
            legs_to_close.append({
                "symbol": symbol,
                "qty": qty,
                "side": close_side
            })
    
    if not legs_to_close:
        logger.info("No butterfly positions to close!")
        return
    
    # Place aggressive limit orders
    executor = TradeExecutor()
    
    logger.info("\nPlacing aggressive limit orders to close positions...")
    for leg in legs_to_close:
        symbol = leg['symbol']
        qty = leg['qty']
        side = leg['side']
        
        # Get aggressive price
        price = get_aggressive_price(symbol, side)
        if price is None:
            logger.error(f"Could not get price for {symbol}, skipping")
            continue
        
        side_label = "BUY" if side == 1 else "SELL"
        logger.info(f"\n{side_label} {qty} x {symbol} @ {price}")
        
        # Place order
        result = executor.place_order(
            symbol=symbol,
            qty=qty,
            side=side,
            order_type=1,  # LIMIT
            price=price
        )
        
        if result:
            order_id = result.get('orderId')
            logger.info(f"  ✓ Order placed: {order_id}")
        else:
            logger.error(f"  ✗ Order failed")
        
        time.sleep(0.5)  # Brief pause between orders
    
    # Wait a moment and check positions
    logger.info("\nWaiting 3 seconds for fills...")
    time.sleep(3)
    
    logger.info("\nFinal positions:")
    positions = account_manager.get_positions(force_refresh=True)
    butterfly_found = False
    for symbol in butterfly_symbols:
        for pos in positions:
            if pos.get('symbol') == symbol:
                qty = float(pos.get('qty', 0))
                logger.info(f"  {symbol}: {qty:.3f}")
                butterfly_found = True
                break
    
    if not butterfly_found:
        logger.info("  No butterfly positions remaining - all closed!")
    
    logger.info("\n" + "=" * 80)
    logger.info("CLOSE COMPLETE")
    logger.info("=" * 80)


if __name__ == "__main__":
    main()
