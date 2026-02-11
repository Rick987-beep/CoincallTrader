#!/usr/bin/env python3
"""
Position Monitor Module

Monitors open positions and closes them when profit/loss targets are reached.
Environment-agnostic - works the same for testnet and production.
"""

import logging
from config import CLOSE_POSITION_CONDITIONS
from account_manager import get_open_positions
from trade_execution import execute_trade

logger = logging.getLogger(__name__)


def monitor_positions():
    """
    Monitor open positions and close them based on profit/loss conditions
    """
    try:
        positions = get_open_positions()
        
        if not positions:
            logger.debug("No open positions to monitor")
            return

        logger.info(f"Monitoring {len(positions)} open positions")
        
        for position in positions:
            position_id = position.get('position_id')
            symbol = position.get('symbol')
            unrealized_pnl = position.get('unrealized_pnl', 0)
            qty = position.get('qty', 0)
            trade_side = position.get('trade_side', 1)  # 1: buy, 2: sell
            roi = position.get('roi', 0)
            
            logger.debug(f"Position {position_id}: {symbol}, PnL: ${unrealized_pnl:.2f}, ROI: {roi*100:.2f}%")
            
            # Check if position should be closed
            should_close = False
            close_reason = None
            
            # Check profit target
            if unrealized_pnl >= CLOSE_POSITION_CONDITIONS.get('profit_target', float('inf')):
                should_close = True
                close_reason = f"profit target reached (${unrealized_pnl:.2f})"
            
            # Check loss limit
            elif unrealized_pnl <= CLOSE_POSITION_CONDITIONS.get('loss_limit', float('-inf')):
                should_close = True
                close_reason = f"loss limit hit (${unrealized_pnl:.2f})"
            
            if should_close:
                # Close position by placing opposite order
                close_side = 2 if trade_side == 1 else 1  # opposite side
                result = execute_trade(symbol, abs(qty), close_side, timeout_seconds=30)
                
                if result:
                    logger.info(f"Closed position {position_id} ({symbol}) - {close_reason}")
                else:
                    logger.warning(f"Failed to close position {position_id} ({symbol})")
            else:
                logger.debug(f"Position {position_id} ({symbol}) still held - PnL: ${unrealized_pnl:.2f}")

    except Exception as e:
        logger.error(f"Error monitoring positions: {e}", exc_info=True)


def get_position_summary():
    """
    Get summary of all open positions
    
    Returns:
        Dict with position summary statistics
    """
    try:
        positions = get_open_positions()
        
        if not positions:
            return {
                'total_positions': 0,
                'total_unrealized_pnl': 0,
                'positions': []
            }
        
        total_unrealized_pnl = sum(p.get('unrealized_pnl', 0) for p in positions)
        total_roi = sum(p.get('roi', 0) for p in positions)
        
        position_summary = []
        for pos in positions:
            position_summary.append({
                'symbol': pos.get('symbol'),
                'qty': pos.get('qty'),
                'unrealized_pnl': pos.get('unrealized_pnl'),
                'roi': pos.get('roi'),
                'delta': pos.get('delta'),
                'theta': pos.get('theta'),
            })
        
        return {
            'total_positions': len(positions),
            'total_unrealized_pnl': total_unrealized_pnl,
            'total_roi': total_roi,
            'positions': position_summary
        }
    
    except Exception as e:
        logger.error(f"Error getting position summary: {e}")
        return {'error': str(e)}
