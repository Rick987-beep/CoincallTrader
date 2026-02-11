#!/usr/bin/env python3
"""
Market Data Module

Handles all market data retrieval including:
- BTC/USDT futures prices from Coincall and Binance
- Option instruments from Coincall
- Option details and Greeks from Coincall
- Option orderbook depth

Environment-agnostic - works the same for testnet and production.
"""

import logging
import requests
from typing import Dict, List, Optional, Any
from config import BASE_URL, API_KEY, API_SECRET
from auth import CoincallAuth

logger = logging.getLogger(__name__)


class MarketData:
    """Handles market data retrieval"""

    def __init__(self):
        """Initialize market data client"""
        self.auth = CoincallAuth(API_KEY, API_SECRET, BASE_URL)
        self._price_cache = None
        self._price_cache_time = None

    def get_btc_futures_price(self, use_cache: bool = True) -> float:
        """
        Get BTC/USDT perpetual futures price from Coincall, fallback to Binance.

        Args:
            use_cache: Use cached price if available (cache expires every 30 seconds)

        Returns:
            BTC/USDT futures price
        """
        import time
        
        # Check cache
        if use_cache and self._price_cache is not None:
            if time.time() - self._price_cache_time < 30:
                return self._price_cache

        try:
            # Try Coincall futures ticker endpoint
            response = self.auth.get('/open/futures/ticker/BTCUSDT')
            
            if self.auth.is_successful(response):
                data = response.get('data', {})
                price_fields = ['lastPrice', 'price', 'markPrice']
                for field in price_fields:
                    if field in data:
                        price = float(data[field])
                        if price > 0:
                            self._price_cache = price
                            self._price_cache_time = time.time()
                            logger.debug(f"BTC/USDT futures price from Coincall: {price}")
                            return price

        except Exception as e:
            logger.warning(f"Coincall futures price failed: {e}")

        # Try Binance API as fallback
        try:
            response = requests.get('https://fapi.binance.com/fapi/v1/ticker/price?symbol=BTCUSDT', timeout=5)
            if response.status_code == 200:
                data = response.json()
                price = float(data.get('price', 0))
                if price > 0:
                    self._price_cache = price
                    self._price_cache_time = time.time()
                    logger.info(f"BTC/USDT from Binance: {price}")
                    return price
        except Exception as e:
            logger.warning(f"Binance price failed: {e}")

        # Final fallback
        fallback_price = 72000.0
        logger.warning(f"Using fallback price: {fallback_price}")
        return fallback_price

    def get_option_instruments(self, underlying: str = 'BTC') -> Optional[List[Dict[str, Any]]]:
        """
        Get available option instruments from Coincall

        Args:
            underlying: Underlying symbol (BTC, ETH, etc.)

        Returns:
            List of option instruments or None if failed
        """
        try:
            # Try the correct endpoint as a public request (no auth)
            endpoint = f'/open/option/getInstruments/{underlying}'
            logger.debug(f"Trying public endpoint: {endpoint}")
            
            import requests
            url = f"{self.auth.base_url}{endpoint}"
            logger.debug(f"Full URL: {url}")
            response = requests.get(url, timeout=10)
            logger.debug(f"Public request status: {response.status_code}")
            logger.debug(f"Response headers: {response.headers}")
            logger.debug(f"Response text: {response.text[:500]}")  # First 500 chars
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    logger.debug(f"Parsed response: {data}")
                    if data.get('code') == 0 and data.get('data'):
                        instruments = data['data']
                        if isinstance(instruments, list) and len(instruments) > 0:
                            logger.debug(f"Retrieved {len(instruments)} option instruments for {underlying}")
                            return instruments
                except Exception as e:
                    logger.debug(f"JSON parse error: {e}")
            
            # If public request fails, try with authentication
            logger.debug("Public request failed, trying with authentication")
            response = self.auth.get(endpoint)
            logger.debug(f"Auth response: {response}")
            if self.auth.is_successful(response):
                data = response.get('data', [])
                if isinstance(data, list) and len(data) > 0:
                    logger.debug(f"Retrieved {len(data)} option instruments for {underlying} with auth")
                    return data
            
            logger.error(f"Failed to get option instruments for {underlying}")
            return None
        
        except Exception as e:
            logger.error(f"Error getting option instruments for {underlying}: {e}")
            return None

    def get_option_details(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information for a specific option

        Args:
            symbol: Option symbol

        Returns:
            Dict with option details or None if failed
        """
        try:
            # Try the option details endpoint
            response = self.auth.get(f'/open/option/detail/v1/{symbol}')
            
            if self.auth.is_successful(response):
                details = response.get('data', {})
                logger.debug(f"Retrieved details for {symbol}")
                return details
            else:
                logger.debug(f"Option details endpoint failed for {symbol}: {response.get('msg')}")
                
                # Try as public request
                import requests
                url = f"{self.auth.base_url}/open/option/detail/v1/{symbol}"
                response = requests.get(url, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    if data.get('code') == 0 and data.get('data'):
                        logger.debug(f"Retrieved details for {symbol} (public)")
                        return data['data']
                
                logger.error(f"Failed to get details for {symbol}: {response.get('msg')}")
                return None

        except Exception as e:
            logger.error(f"Error getting option details for {symbol}: {e}")
            return None

    def get_option_greeks(self, symbol: str) -> Optional[Dict[str, float]]:
        """
        Extract Greeks from option details

        Args:
            symbol: Option symbol

        Returns:
            Dict with delta, theta, vega, gamma or None if failed
        """
        try:
            details = self.get_option_details(symbol)
            if not details:
                return None

            greeks = {
                'delta': float(details.get('delta', 0)),
                'theta': float(details.get('theta', 0)),
                'vega': float(details.get('vega', 0)),
                'gamma': float(details.get('gamma', 0)),
            }
            return greeks

        except Exception as e:
            logger.error(f"Error extracting greeks for {symbol}: {e}")
            return None

    def get_option_market_data(self, symbol: str) -> Optional[Dict[str, float]]:
        """
        Extract market data from option details

        Args:
            symbol: Option symbol

        Returns:
            Dict with bid, ask, mark_price, implied_volatility or None if failed
        """
        try:
            details = self.get_option_details(symbol)
            if not details:
                return None

            market_data = {
                'bid': float(details.get('bid', 0)),
                'ask': float(details.get('ask', 0)),
                'mark_price': float(details.get('markPrice', 0)),
                'implied_volatility': float(details.get('impliedVolatility', 0)),
            }
            return market_data

        except Exception as e:
            logger.error(f"Error extracting market data for {symbol}: {e}")
            return None

    def get_option_orderbook(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get option orderbook depth (100-level)

        Args:
            symbol: Option symbol

        Returns:
            Dict with orderbook data (bids, asks) or None if failed
        """
        try:
            # Correct endpoint per Coincall API docs
            response = self.auth.get(f'/open/option/order/orderbook/v1/{symbol}')
            
            if self.auth.is_successful(response):
                depth = response.get('data', {})
                return depth
            else:
                logger.error(f"Failed to get orderbook for {symbol}: {response.get('msg')}")
                return None

        except Exception as e:
            logger.error(f"Error getting orderbook for {symbol}: {e}")
            return None


# Global instance
market_data = MarketData()


# Convenience functions
def get_btc_futures_price(use_cache: bool = True) -> float:
    """Get BTC/USDT futures price"""
    return market_data.get_btc_futures_price(use_cache)


def get_option_instruments(underlying: str = 'BTC') -> Optional[List[Dict[str, Any]]]:
    """Get available option instruments"""
    return market_data.get_option_instruments(underlying)


def get_option_details(symbol: str) -> Optional[Dict[str, Any]]:
    """Get option details"""
    return market_data.get_option_details(symbol)


def get_option_greeks(symbol: str) -> Optional[Dict[str, float]]:
    """Get option Greeks"""
    return market_data.get_option_greeks(symbol)


def get_option_market_data(symbol: str) -> Optional[Dict[str, float]]:
    """Get option market data"""
    return market_data.get_option_market_data(symbol)


def get_option_orderbook(symbol: str) -> Optional[Dict[str, Any]]:
    """Get option orderbook"""
    return market_data.get_option_orderbook(symbol)
