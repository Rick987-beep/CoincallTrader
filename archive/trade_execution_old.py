#!/usr/bin/env python3
"""
Trade Execution Module

Handles trade execution including:
- Single trade execution with limit orders and requoting
- Multiple concurrent trade execution
- Order management and status checking
"""

from coincall import Options
from config import API_KEY, API_SECRET, BASE_URL
import logging
import time
import concurrent.futures
from market_data import get_option_orderbook

# Initialize API
options_api = Options.OptionsAPI(API_KEY, API_SECRET)
options_api.domain = BASE_URL


def execute_trade(symbol, qty, side, timeout_seconds=60):
    """
    Execute a trade with limit order, requoting at the top of the book every 10 seconds,
    handling partial fills and retrying on errors, then market if not filled within timeout_seconds.

    Args:
        symbol (str): Option symbol
        qty (int): Quantity
        side (int): 1 for buy, 2 for sell
        timeout_seconds (int): Time to try filling the limit order before going to market

    Returns:
        dict: Order result or None if failed
    """
    try:
        start_time = time.time()
        remaining_qty = qty
        order_id = None
        total_filled = 0

        while time.time() - start_time < timeout_seconds and remaining_qty > 0:
            # Get current orderbook
            depth = get_option_orderbook(symbol)
            if not depth or 'data' not in depth:
                logging.error(f"Could not get orderbook for {symbol}")
                time.sleep(10)
                continue

            orderbook_data = depth['data']

            if side == 1:  # buy
                if not orderbook_data.get('asks'):
                    logging.error(f"No asks available in orderbook for {symbol}")
                    time.sleep(10)
                    continue
                price = float(orderbook_data['asks'][0]['price'])  # best ask
            else:  # sell
                if not orderbook_data.get('bids'):
                    logging.error(f"No bids available in orderbook for {symbol}")
                    time.sleep(10)
                    continue
                price = float(orderbook_data['bids'][0]['price'])  # best bid

            if order_id:
                # Cancel existing order
                try:
                    options_api.cancel_order(orderId=order_id)
                    logging.info(f"Cancelled order {order_id} for requoting")
                except Exception as e:
                    logging.warning(f"Failed to cancel order {order_id}: {e}")

            # Place new limit order for remaining quantity
            try:
                order = options_api.place_order(symbol, remaining_qty, side, 2, price=price)
                order_id = order.get('orderId')
                logging.info(f"Placed/updated limit order {order_id} for {symbol}, remaining_qty {remaining_qty}, side {side}, price {price}")
            except Exception as e:
                logging.error(f"Failed to place order for {symbol}: {e}")
                # Skip this iteration, try again after sleep
                time.sleep(10)
                continue

            # Wait 10 seconds before next requote
            time.sleep(10)

            # Check order status and update fills
            try:
                order_info = options_api.get_order_by_id(orderId=order_id)
                executed_qty = float(order_info.get('executedQty', 0))
                total_filled += executed_qty
                remaining_qty -= executed_qty
                if order_info.get('status') == 'filled' or remaining_qty <= 0:
                    logging.info(f"Order fully filled: {order_id}, total_filled {total_filled}")
                    return order
            except Exception as e:
                logging.warning(f"Failed to check order status for {order_id}: {e}")
                # Continue, will try again

        # Timeout reached, if remaining > 0, start aggressive requoting phase
        if remaining_qty > 0:
            logging.info(f"Starting aggressive requoting phase for remaining {remaining_qty} of {symbol}")
            final_timeout = 30  # seconds for aggressive phase
            final_start = time.time()
            while time.time() - final_start < final_timeout and remaining_qty > 0:
                # Get price to cross the order book
                depth = get_option_orderbook(symbol)
                if not depth or 'data' not in depth:
                    logging.error(f"Could not get orderbook for aggressive phase {symbol}")
                    time.sleep(5)
                    continue

                orderbook_data = depth['data']

                if side == 1:  # buy
                    if not orderbook_data.get('asks'):
                        logging.error(f"No asks available in orderbook for aggressive phase {symbol}")
                        time.sleep(5)
                        continue
                    price = float(orderbook_data['asks'][0]['price'])  # best ask to cross
                else:  # sell
                    if not orderbook_data.get('bids'):
                        logging.error(f"No bids available in orderbook for aggressive phase {symbol}")
                        time.sleep(5)
                        continue
                    price = float(orderbook_data['bids'][0]['price'])  # best bid to cross

                if order_id:
                    try:
                        options_api.cancel_order(orderId=order_id)
                        logging.info(f"Cancelled order {order_id} in aggressive phase")
                    except Exception as e:
                        logging.warning(f"Failed to cancel order {order_id}: {e}")

                # Place aggressive limit order
                try:
                    order = options_api.place_order(symbol, remaining_qty, side, 2, price=price)
                    order_id = order.get('orderId')
                    logging.info(f"Placed aggressive limit order {order_id} for {symbol}, remaining_qty {remaining_qty}, side {side}, price {price}")
                except Exception as e:
                    logging.error(f"Failed to place aggressive order for {symbol}: {e}")
                    time.sleep(5)
                    continue

                # Wait 5 seconds before next aggressive requote
                time.sleep(5)

                # Check order status and update fills
                try:
                    order_info = options_api.get_order_by_id(orderId=order_id)
                    executed_qty = float(order_info.get('executedQty', 0))
                    total_filled += executed_qty
                    remaining_qty -= executed_qty
                    if remaining_qty <= 0:
                        logging.info(f"Aggressive order fully filled: {order_id}, total_filled {total_filled}")
                        return order
                except Exception as e:
                    logging.warning(f"Failed to check aggressive order status for {order_id}: {e}")

            # If still remaining after aggressive phase
            if remaining_qty > 0:
                logging.error(f"Failed to fill remaining {remaining_qty} for {symbol} even with aggressive requoting")
                return None
        else:
            # Already fully filled
            return order if order_id else None

    except Exception as e:
        logging.error(f"Error executing trade for {symbol}: {e}")
        return None


def execute_multiple_trades(trades):
    """
    Execute multiple trades concurrently using threading.

    Args:
        trades (list): List of tuples (symbol, qty, side, timeout_seconds)

    Returns:
        list: List of order results in the order they complete
    """
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [executor.submit(execute_trade, *trade) for trade in trades]
        results = [future.result() for future in concurrent.futures.as_completed(futures)]
    return results


def cancel_order(order_id):
    """
    Cancel an order by ID.

    Args:
        order_id (str): Order ID to cancel

    Returns:
        bool: True if cancelled successfully
    """
    try:
        result = options_api.cancel_order(orderId=order_id)
        logging.info(f"Cancelled order {order_id}")
        return True
    except Exception as e:
        logging.error(f"Failed to cancel order {order_id}: {e}")
        return False


def get_order_status(order_id):
    """
    Get order status by ID.

    Args:
        order_id (str): Order ID

    Returns:
        dict: Order information or None if failed
    """
    try:
        order_info = options_api.get_order_by_id(orderId=order_id)
        return order_info
    except Exception as e:
        logging.error(f"Failed to get order status for {order_id}: {e}")
        return None