#!/usr/bin/env python3
"""
Market Data Module

Handles all market data retrieval including:
- BTC/USDT futures prices from Coincall and Binance
- Option instruments from Coincall
- Option details and Greeks from Coincall
"""

from coincall import Options, Public
from config import API_KEY, API_SECRET, BASE_URL
import logging
import requests

# Initialize APIs
public_api = Public.PublicAPI()
options_api = Options.OptionsAPI(API_KEY, API_SECRET)

# Set domain from config
public_api.domain = BASE_URL
options_api.domain = BASE_URL


def get_btc_futures_price():
    """
    Get BTC/USDT perpetual futures price from Coincall, fallback to Binance.

    Returns:
        float: BTC/USDT futures price
    """
    try:
        # Try Coincall first
        spot_price = 0
        endpoints = ['/open/futures/ticker/BTCUSDT', '/open/futures/index/BTCUSDT']
        for endpoint in endpoints:
            try:
                response = public_api.client.get(endpoint)
                if response.status_code == 200:
                    data = response.json()
                    if data.get('code') == 0 and data.get('data'):
                        price_fields = ['lastPrice', 'price', 'indexPrice', 'markPrice']
                        for field in price_fields:
                            if field in data['data']:
                                spot_price = float(data['data'][field])
                                break
                        if spot_price > 0:
                            logging.info(f"Live BTC/USDT futures price from Coincall: {spot_price}")
                            return spot_price
            except Exception as e:
                logging.warning(f"Coincall endpoint {endpoint} failed: {e}")

        # If Coincall failed, try Binance API
        if spot_price == 0:
            try:
                binance_response = requests.get('https://fapi.binance.com/fapi/v1/ticker/price?symbol=BTCUSDT', timeout=5)
                if binance_response.status_code == 200:
                    binance_data = binance_response.json()
                    spot_price = float(binance_data['price'])
                    logging.info(f"Using live BTC/USDT futures price from Binance: {spot_price}")
                    return spot_price
                else:
                    logging.warning("Binance API failed, using fallback")
            except Exception as e:
                logging.warning(f"Error getting Binance price: {e}")

        # Final fallback
        spot_price = 72000.0
        logging.warning(f"Using fallback futures price: {spot_price}")
        return spot_price

    except Exception as e:
        spot_price = 72000.0
        logging.warning(f"Error getting futures price, using fallback: {spot_price}")
        return spot_price


def get_option_instruments(underlying='BTC'):
    """
    Get available option instruments from Coincall.

    Args:
        underlying (str): Underlying symbol, default 'BTC'

    Returns:
        list: List of option instruments or None if failed
    """
    try:
        instruments_response = options_api.get_instruments(base=underlying)
        if not instruments_response or 'data' not in instruments_response:
            logging.error("No options available or invalid response format")
            return None

        options_list = instruments_response['data']
        if not options_list:
            logging.error("No options in data")
            return None

        return options_list

    except Exception as e:
        logging.error(f"Error getting option instruments: {e}")
        return None


def get_option_details(symbol):
    """
    Get detailed option data including Greeks and market data.

    Args:
        symbol (str): Option symbol (e.g., 'BTCUSD-5FEB26-80000-C')

    Returns:
        dict: Option details or None if failed
    """
    try:
        details = options_api.get_option_by_name(symbol)
        if details and 'data' in details and details['code'] == 0:
            return details['data']
        else:
            logging.error(f"Failed to get option details for {symbol}: {details}")
            return None

    except Exception as e:
        logging.error(f"Error getting option details for {symbol}: {e}")
        return None


def get_option_greeks(symbol):
    """
    Extract Greeks from option details.

    Args:
        symbol (str): Option symbol

    Returns:
        dict: Greeks dictionary with delta, theta, vega, gamma
    """
    details = get_option_details(symbol)
    if not details:
        return None

    greeks = {
        'delta': details.get('delta'),
        'theta': details.get('theta'),
        'vega': details.get('vega'),
        'gamma': details.get('gamma')
    }

    return greeks


def get_option_market_data(symbol):
    """
    Extract market data from option details.

    Args:
        symbol (str): Option symbol

    Returns:
        dict: Market data dictionary with bid, ask, mark_price, iv
    """
    details = get_option_details(symbol)
    if not details:
        return None

    market_data = {
        'bid': details.get('bid'),
        'ask': details.get('ask'),
        'mark_price': details.get('markPrice'),
        'implied_volatility': details.get('impliedVolatility')
    }

    return market_data


def get_option_orderbook(symbol):
    """
    Get option orderbook depth.

    Args:
        symbol (str): Option symbol

    Returns:
        dict: Orderbook data or None if failed
    """
    try:
        depth = options_api.get_depth(symbol)
        return depth
    except Exception as e:
        logging.error(f"Error getting orderbook for {symbol}: {e}")
        return None