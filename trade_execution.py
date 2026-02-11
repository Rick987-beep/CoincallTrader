#!/usr/bin/env python3
"""
Trade Execution Module

Handles all order and trade execution operations.
Environment-agnostic - works the same for testnet and production.
The environment is controlled via config.py.
"""

import logging
import time
import concurrent.futures
from typing import Dict, List, Tuple, Optional, Any
from config import BASE_URL, API_KEY, API_SECRET
from auth import CoincallAuth
from market_data import get_option_orderbook

logger = logging.getLogger(__name__)


class TradeExecutor:
    """Executes trades and manages orders"""

    def __init__(self):
        """Initialize trade executor with authenticated API client"""
        self.auth = CoincallAuth(API_KEY, API_SECRET, BASE_URL)

    def place_order(
        self,
        symbol: str,
        qty: float,
        side: int,
        order_type: int = 1,
        price: Optional[float] = None,
        client_order_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Place a single order. Returns dict with orderId or None on error."""
        try:
            payload = {
                'symbol': symbol,
                'qty': qty,
                'tradeSide': side,
                'tradeType': order_type,
            }
            
            if price is not None:
                payload['price'] = price
            
            if client_order_id:
                payload['clientOrderId'] = int(client_order_id)
            
            response = self.auth.post('/open/option/order/create/v1', payload)
            
            if self.auth.is_successful(response):
                order_id = response.get('data')
                logger.info(f"Order placed: {order_id} for {symbol}")
                return {'orderId': order_id}
            else:
                logger.error(f"Order failed for {symbol}: {response.get('msg')}")
                return None
        
        except Exception as e:
            logger.error(f"Exception placing order for {symbol}: {e}")
            return None

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an order by ID
        
        Args:
            order_id: Order ID to cancel
            
        Returns:
            True if cancelled successfully
        """
        try:
            response = self.auth.post('/open/option/order/cancel/v1', {'orderId': order_id})
            
            if self.auth.is_successful(response):
                logger.info(f"Order cancelled: {order_id}")
                return True
            else:
                logger.error(f"Failed to cancel order {order_id}: {response.get('msg')}")
                return False
        
        except Exception as e:
            logger.error(f"Exception cancelling order {order_id}: {e}")
            return False

    def get_order_status(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Get order status by ID
        
        Args:
            order_id: Order ID
            
        Returns:
            Order information dict or None on error
        """
        try:
            response = self.auth.get(f'/open/option/order/{order_id}/v1')
            
            if self.auth.is_successful(response):
                return response.get('data', {})
            else:
                logger.error(f"Failed to get order status for {order_id}: {response.get('msg')}")
                return None
        
        except Exception as e:
            logger.error(f"Exception getting order status for {order_id}: {e}")
            return None

    def execute_trade(
        self,
        symbol: str,
        qty: float,
        side: int,
        timeout_seconds: int = 60,
        requote_interval: int = 10
    ) -> Optional[Dict[str, Any]]:
        """
        Execute a trade with limit order, requoting at the top of the book every N seconds,
        handling partial fills and retrying on errors, then market if not filled within timeout_seconds.

        Args:
            symbol: Option symbol
            qty: Quantity
            side: 1 for buy, 2 for sell
            timeout_seconds: Time to try filling the limit order before going to market
            requote_interval: Seconds between requote attempts

        Returns:
            Order result dict or None if failed
        """
        try:
            start_time = time.time()
            remaining_qty = qty
            order_id = None
            total_filled = 0.0

            while time.time() - start_time < timeout_seconds and remaining_qty > 0:
                # Get current orderbook
                try:
                    depth = get_option_orderbook(symbol)
                    if not depth or 'data' not in depth:
                        logger.error(f"Could not get orderbook for {symbol}")
                        time.sleep(requote_interval)
                        continue

                    orderbook_data = depth['data']

                    if side == 1:  # buy
                        if not orderbook_data.get('asks') or len(orderbook_data['asks']) == 0:
                            logger.error(f"No asks available in orderbook for {symbol}")
                            time.sleep(requote_interval)
                            continue
                        price = float(orderbook_data['asks'][0]['price'])
                    else:  # sell
                        if not orderbook_data.get('bids') or len(orderbook_data['bids']) == 0:
                            logger.error(f"No bids available in orderbook for {symbol}")
                            time.sleep(requote_interval)
                            continue
                        price = float(orderbook_data['bids'][0]['price'])

                except Exception as e:
                    logger.error(f"Error getting orderbook for {symbol}: {e}")
                    time.sleep(requote_interval)
                    continue

                # Cancel existing order if any
                if order_id:
                    try:
                        self.cancel_order(order_id)
                        logger.info(f"Cancelled order {order_id} for requoting")
                    except Exception as e:
                        logger.warning(f"Failed to cancel order {order_id}: {e}")

                # Place new limit order for remaining quantity
                try:
                    order = self.place_order(symbol, remaining_qty, side, order_type=1, price=price)
                    if order:
                        order_id = order.get('orderId')
                        logger.info(f"Placed limit order {order_id} for {symbol}, qty {remaining_qty}, side {side}, price {price}")
                except Exception as e:
                    logger.error(f"Failed to place order for {symbol}: {e}")
                    time.sleep(requote_interval)
                    continue

                # Wait before next requote
                time.sleep(requote_interval)

                # Check order status and update fills
                if order_id:
                    try:
                        order_info = self.get_order_status(order_id)
                        if order_info:
                            executed_qty = float(order_info.get('executedQty', 0))
                            if executed_qty > total_filled:
                                additional_fill = executed_qty - total_filled
                                total_filled = executed_qty
                                remaining_qty -= additional_fill
                                logger.info(f"Order {order_id} filled: {executed_qty} (additional: {additional_fill})")
                            
                            if order_info.get('state') == 2:  # Filled
                                logger.info(f"Order fully filled: {order_id}, total filled {total_filled}")
                                return order_info
                    except Exception as e:
                        logger.warning(f"Failed to check order status for {order_id}: {e}")

            # Timeout reached, if remaining > 0, try aggressive phase
            if remaining_qty > 0:
                logger.info(f"Timeout on requoting, attempting aggressive fill for remaining {remaining_qty} of {symbol}")
                return self._aggressive_fill_phase(symbol, remaining_qty, side, order_id, timeout_seconds=30)
            else:
                return self.get_order_status(order_id) if order_id else None

        except Exception as e:
            logger.error(f"Error executing trade for {symbol}: {e}")
            return None

    def _aggressive_fill_phase(
        self,
        symbol: str,
        qty: float,
        side: int,
        existing_order_id: Optional[str] = None,
        timeout_seconds: int = 30
    ) -> Optional[Dict[str, Any]]:
        """
        Aggressively attempt to fill remaining quantity by crossing the spread
        
        Args:
            symbol: Option symbol
            qty: Remaining quantity to fill
            side: 1 for buy, 2 for sell
            existing_order_id: Order to cancel before aggressive fill
            timeout_seconds: Max time for aggressive fill attempts
            
        Returns:
            Order result dict or None if failed
        """
        start_time = time.time()
        remaining_qty = qty
        order_id = None

        while time.time() - start_time < timeout_seconds and remaining_qty > 0:
            try:
                # Get current orderbook
                depth = get_option_orderbook(symbol)
                if not depth or 'data' not in depth:
                    logger.error(f"Could not get orderbook for aggressive phase {symbol}")
                    time.sleep(5)
                    continue

                orderbook_data = depth['data']

                if side == 1:  # buy - cross the ask
                    if not orderbook_data.get('asks') or len(orderbook_data['asks']) == 0:
                        logger.error(f"No asks in aggressive phase {symbol}")
                        time.sleep(5)
                        continue
                    price = float(orderbook_data['asks'][0]['price'])
                else:  # sell - cross the bid
                    if not orderbook_data.get('bids') or len(orderbook_data['bids']) == 0:
                        logger.error(f"No bids in aggressive phase {symbol}")
                        time.sleep(5)
                        continue
                    price = float(orderbook_data['bids'][0]['price'])

                # Cancel previous order if exists
                if order_id or existing_order_id:
                    self.cancel_order(order_id or existing_order_id)

                # Place aggressive limit order
                order = self.place_order(symbol, remaining_qty, side, order_type=1, price=price)
                if order:
                    order_id = order.get('orderId')
                    logger.info(f"Aggressive order {order_id} for {symbol}, qty {remaining_qty}, price {price}")

                # Wait less time in aggressive phase
                time.sleep(5)

                # Check fills
                if order_id:
                    order_info = self.get_order_status(order_id)
                    if order_info:
                        executed_qty = float(order_info.get('executedQty', 0))
                        remaining_qty = float(order_info.get('unfilledQty', 0))
                        
                        if order_info.get('state') == 2:  # Filled
                            logger.info(f"Aggressive order fully filled: {order_id}")
                            return order_info

            except Exception as e:
                logger.error(f"Error in aggressive fill phase: {e}")
                time.sleep(5)

        logger.error(f"Could not fill remaining {remaining_qty} for {symbol} even with aggressive attempts")
        return None

    def execute_multiple_trades(self, trades: List[Tuple[str, float, int, int]]) -> List[Optional[Dict[str, Any]]]:
        """
        Execute multiple trades concurrently using threading

        Args:
            trades: List of tuples (symbol, qty, side, timeout_seconds)

        Returns:
            List of order results
        """
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(self.execute_trade, symbol, qty, side, timeout): (symbol, qty, side, timeout)
                for symbol, qty, side, timeout in trades
            }
            
            for future in concurrent.futures.as_completed(futures):
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    logger.error(f"Trade execution failed: {e}")
                    results.append(None)

        return results


# Global instance
trade_executor = TradeExecutor()


# Convenience functions
def place_order(symbol: str, qty: float, side: int, order_type: int = 1, price: Optional[float] = None, client_order_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Place an order"""
    return trade_executor.place_order(symbol, qty, side, order_type, price, client_order_id)


def cancel_order(order_id: str) -> bool:
    """Cancel an order"""
    return trade_executor.cancel_order(order_id)


def get_order_status(order_id: str) -> Optional[Dict[str, Any]]:
    """Get order status"""
    return trade_executor.get_order_status(order_id)


def execute_trade(symbol: str, qty: float, side: int, timeout_seconds: int = 60) -> Optional[Dict[str, Any]]:
    """Execute a trade with requoting"""
    return trade_executor.execute_trade(symbol, qty, side, timeout_seconds)


def execute_multiple_trades(trades: List[Tuple[str, float, int, int]]) -> List[Optional[Dict[str, Any]]]:
    """Execute multiple trades concurrently"""
    return trade_executor.execute_multiple_trades(trades)
