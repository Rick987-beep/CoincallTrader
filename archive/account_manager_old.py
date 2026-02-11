#!/usr/bin/env python3
"""
Account Management Module

Handles all account-related operations including:
- Account balance and equity monitoring
- Position tracking
- Order management
- Margin and risk monitoring
- Wallet information
"""

from coincall import Options, Public
from config import API_KEY, API_SECRET, BASE_URL, ENVIRONMENT
import logging
import time
import requests

# Initialize APIs
public_api = Public.PublicAPI()
options_api = Options.OptionsAPI(API_KEY, API_SECRET)

# Set domain from config
public_api.domain = BASE_URL
options_api.domain = BASE_URL


class AccountManager:
    """Manages account information and monitoring"""

    def __init__(self):
        self.last_update = None
        self._account_info = None
        self._positions = None
        self._orders = None

    def get_account_info(self, force_refresh=False):
        """
        Get comprehensive account information

        Returns:
            dict: Account information including balance, equity, margin, etc.
        """
        try:
            # Cache for 30 seconds unless force refresh
            if not force_refresh and self._account_info and (time.time() - self.last_update) < 30:
                return self._account_info

            # For testnet, return mock data
            if ENVIRONMENT == 'testnet':
                logging.info("Using mock account data for testnet environment")
                self._account_info = {
                    'total_balance': 10000.0,  # Mock $10,000 balance
                    'available_balance': 9500.0,  # Mock available balance
                    'used_margin': 500.0,  # Mock used margin
                    'equity': 10000.0,  # Mock equity
                    'unrealized_pnl': 0.0,  # Mock P&L
                    'margin_level': 20.0,  # Mock margin level (2000%)
                    'maintenance_margin': 250.0,  # Mock maintenance margin
                    'timestamp': time.time()
                }
                self.last_update = time.time()
                return self._account_info

            # For production, use real API calls
            logging.info("Fetching real account data from production environment")
            endpoint = '/open/option/account/info/v1'
            response = options_api.client.get(endpoint)

            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0 and data.get('data'):
                    account_data = data['data']
                    self._account_info = {
                        'total_balance': float(account_data.get('totalBalance', 0)),
                        'available_balance': float(account_data.get('availableBalance', 0)),
                        'used_margin': float(account_data.get('usedMargin', 0)),
                        'equity': float(account_data.get('equity', 0)),
                        'unrealized_pnl': float(account_data.get('unrealizedPnl', 0)),
                        'margin_level': float(account_data.get('marginLevel', 0)),
                        'maintenance_margin': float(account_data.get('maintenanceMargin', 0)),
                        'timestamp': time.time()
                    }
                    self.last_update = time.time()
                    logging.info(f"Successfully retrieved production account info: ${self._account_info['available_balance']:.2f} available")
                    return self._account_info
                else:
                    logging.error(f"API returned error: {data}")
            else:
                logging.error(f"HTTP error {response.status_code}: {response.text}")

            return None

        except Exception as e:
            logging.error(f"Exception getting account info: {e}")
            return None

    def get_positions(self, force_refresh=False):
        """
        Get all current positions

        Returns:
            list: List of position dictionaries
        """
        try:
            # Cache for 10 seconds unless force refresh
            if not force_refresh and self._positions and (time.time() - self.last_update) < 10:
                return self._positions

            # For testnet, return mock data
            if ENVIRONMENT == 'testnet':
                logging.info("Using mock positions data for testnet environment")
                self._positions = []  # No positions in testnet
                return self._positions

            # For production, use real API calls
            logging.info("Fetching real positions data from production environment")
            endpoint = '/open/option/position/get/v1'
            response = options_api.client.get(endpoint)

            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    positions_data = data.get('data', [])
                    self._positions = []

                    for pos in positions_data:
                        position = {
                            'symbol': pos.get('symbol'),
                            'side': pos.get('side'),  # 1: long, 2: short
                            'quantity': float(pos.get('quantity', 0)),
                            'entry_price': float(pos.get('entryPrice', 0)),
                            'mark_price': float(pos.get('markPrice', 0)),
                            'unrealized_pnl': float(pos.get('unrealizedPnl', 0)),
                            'margin': float(pos.get('margin', 0)),
                            'leverage': float(pos.get('leverage', 1)),
                            'liquidation_price': float(pos.get('liquidationPrice', 0))
                        }
                        self._positions.append(position)

                    logging.info(f"Successfully retrieved {len(self._positions)} positions from production")
                    return self._positions
                else:
                    logging.error(f"API returned error: {data}")
                    return []
            else:
                logging.error(f"HTTP error {response.status_code}: {response.text}")
                return []

        except Exception as e:
            logging.error(f"Exception getting positions: {e}")
            return []

    def get_open_orders(self, symbol=None, force_refresh=False):
        """
        Get all open orders, optionally filtered by symbol

        Args:
            symbol (str, optional): Filter orders by symbol
            force_refresh (bool): Force API call instead of using cache

        Returns:
            list: List of open order dictionaries
        """
        try:
            # Cache for 5 seconds unless force refresh
            if not force_refresh and self._orders and (time.time() - self.last_update) < 5:
                orders = self._orders
            else:
                # For testnet, return mock data
                if ENVIRONMENT == 'testnet':
                    logging.info("Using mock orders data for testnet environment")
                    self._orders = []  # No orders in testnet
                    orders = self._orders
                else:
                    # For production, use real API calls
                    logging.info("Fetching real orders data from production environment")
                    endpoint = '/open/option/order/pending/v1'
                    params = {'page': 1, 'pageSize': 50}  # Get up to 50 orders
                    response = options_api.client.get(endpoint, params=params)

                    if response.status_code == 200:
                        data = response.json()
                        if data.get('code') == 0:
                            orders_data = data.get('data', {}).get('list', [])
                            self._orders = []

                            for order in orders_data:
                                order_info = {
                                    'order_id': order.get('orderId'),
                                    'symbol': order.get('symbol'),
                                    'side': order.get('side'),  # 1: buy, 2: sell
                                    'type': order.get('type'),  # 1: market, 2: limit
                                    'quantity': float(order.get('quantity', 0)),
                                    'price': float(order.get('price', 0)),
                                    'filled_quantity': float(order.get('filledQuantity', 0)),
                                    'remaining_quantity': float(order.get('remainingQuantity', 0)),
                                    'status': order.get('status'),
                                    'created_time': order.get('createdTime'),
                                    'updated_time': order.get('updatedTime')
                                }
                                self._orders.append(order_info)
                            orders = self._orders
                            logging.info(f"Successfully retrieved {len(orders)} orders from production")
                        else:
                            logging.error(f"API returned error: {data}")
                            return []
                    else:
                        logging.error(f"HTTP error {response.status_code}: {response.text}")
                        return []

            # Filter by symbol if specified
            if symbol:
                return [order for order in orders if order['symbol'] == symbol]
            return orders

        except Exception as e:
            logging.error(f"Exception getting open orders: {e}")
            return []

    def get_wallet_info(self):
        """
        Get wallet/balance information for all currencies

        Returns:
            dict: Wallet information by currency
        """
        try:
            # For testnet, return mock data
            if ENVIRONMENT == 'testnet':
                logging.info("Using mock wallet data for testnet environment")
                return {
                    'USDT': {
                        'balance': 10000.0,
                        'available': 9500.0,
                        'frozen': 500.0,
                        'bonus': 0.0
                    }
                }

            # For production, use real API calls
            logging.info("Fetching real wallet data from production environment")
            endpoint = '/open/option/account/balance/v1'
            response = options_api.client.get(endpoint)

            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    wallet_data = data.get('data', [])
                    wallet_info = {}

                    for wallet in wallet_data:
                        currency = wallet.get('currency', 'USD')
                        wallet_info[currency] = {
                            'balance': float(wallet.get('balance', 0)),
                            'available': float(wallet.get('available', 0)),
                            'frozen': float(wallet.get('frozen', 0)),
                            'bonus': float(wallet.get('bonus', 0))
                        }

                    return wallet_info
                else:
                    logging.error(f"API returned error: {data}")
                    return {}
            else:
                logging.error(f"HTTP error {response.status_code}: {response.text}")
                return {}

        except Exception as e:
            logging.error(f"Exception getting wallet info: {e}")
            return {}

    def get_margin_info(self):
        """
        Get margin account information

        Returns:
            dict: Margin account details
        """
        account_info = self.get_account_info()
        if account_info:
            return {
                'total_balance': account_info['total_balance'],
                'available_balance': account_info['available_balance'],
                'used_margin': account_info['used_margin'],
                'equity': account_info['equity'],
                'margin_level': account_info['margin_level'],
                'maintenance_margin': account_info['maintenance_margin'],
                'free_margin': account_info['available_balance'] - account_info['maintenance_margin']
            }
        return {}

    def get_account_summary(self):
        """
        Get a comprehensive account summary

        Returns:
            dict: Complete account status
        """
        return {
            'account_info': self.get_account_info(force_refresh=True),
            'positions': self.get_positions(force_refresh=True),
            'open_orders': self.get_open_orders(force_refresh=True),
            'wallet': self.get_wallet_info(),
            'margin': self.get_margin_info(),
            'timestamp': time.time()
        }

    def get_risk_metrics(self):
        """
        Calculate key risk metrics

        Returns:
            dict: Risk metrics
        """
        try:
            account_info = self.get_account_info()
            positions = self.get_positions()

            if not account_info:
                logging.error("No account info available for risk metrics")
                return {}

            if positions is None:
                logging.error("Positions data is None")
                return {}

            total_unrealized_pnl = sum(pos['unrealized_pnl'] for pos in positions)
            total_margin_used = sum(pos['margin'] for pos in positions)

            return {
                'total_unrealized_pnl': total_unrealized_pnl,
                'total_margin_used': total_margin_used,
                'margin_utilization': (total_margin_used / account_info['equity']) * 100 if account_info['equity'] > 0 else 0,
                'open_positions_count': len(positions),
                'account_equity': account_info['equity'],
                'available_margin': account_info['available_balance']
            }
        except Exception as e:
            logging.error(f"Exception in get_risk_metrics: {e}")
            return {}


# Global instance for easy access
account_manager = AccountManager()


def get_account_balance():
    """Convenience function to get account balance"""
    info = account_manager.get_account_info()
    return info['available_balance'] if info else 0


def get_account_equity():
    """Convenience function to get account equity"""
    info = account_manager.get_account_info()
    return info['equity'] if info else 0


def get_open_positions():
    """Convenience function to get open positions"""
    return account_manager.get_positions()


def get_account_summary():
    """Convenience function to get account summary"""
    return account_manager.get_account_summary()


def get_risk_metrics():
    """Convenience function to get risk metrics"""
    return account_manager.get_risk_metrics()