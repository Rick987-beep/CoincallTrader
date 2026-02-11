#!/usr/bin/env python3
"""
Configuration Module

Centralized configuration for the Coincall trading bot.
Supports both testnet and production environments with simple switching.

To switch environments:
  Set TRADING_ENVIRONMENT variable in .env file:
    TRADING_ENVIRONMENT=testnet   (default)
    TRADING_ENVIRONMENT=production
"""

import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# =============================================================================
# ENVIRONMENT SELECTION
# =============================================================================

# Simple environment switcher - change this or set TRADING_ENVIRONMENT in .env
ENVIRONMENT = os.getenv('TRADING_ENVIRONMENT', 'testnet').lower()

if ENVIRONMENT not in ['testnet', 'production']:
    raise ValueError(f"Invalid TRADING_ENVIRONMENT: '{ENVIRONMENT}'. Must be 'testnet' or 'production'")

# =============================================================================
# TESTNET CONFIGURATION
# =============================================================================

TESTNET = {
    'base_url': 'https://betaapi.coincall.com',
    'api_key': os.getenv('COINCALL_API_KEY_TEST'),
    'api_secret': os.getenv('COINCALL_API_SECRET_TEST'),
    'ws_options': 'wss://betaws.coincall.com/options',
    'ws_futures': 'wss://betaws.coincall.com/futures',
    'ws_spot': 'wss://betaws.coincall.com/spot',
}

# =============================================================================
# PRODUCTION CONFIGURATION
# =============================================================================

PRODUCTION = {
    'base_url': 'https://api.coincall.com',
    'api_key': os.getenv('COINCALL_API_KEY_PROD'),
    'api_secret': os.getenv('COINCALL_API_SECRET_PROD'),
    'ws_options': 'wss://ws.coincall.com/options',
    'ws_futures': 'wss://ws.coincall.com/futures',
    'ws_spot': 'wss://ws.coincall.com/spot',
}

# =============================================================================
# ACTIVE CONFIGURATION (Selected by TRADING_ENVIRONMENT)
# =============================================================================

ACTIVE_CONFIG = TESTNET if ENVIRONMENT == 'testnet' else PRODUCTION

# Export commonly used values for convenience
BASE_URL = ACTIVE_CONFIG['base_url']
API_KEY = ACTIVE_CONFIG['api_key']
API_SECRET = ACTIVE_CONFIG['api_secret']

# WebSocket endpoints
WS_OPTIONS = ACTIVE_CONFIG['ws_options']
WS_FUTURES = ACTIVE_CONFIG['ws_futures']
WS_SPOT = ACTIVE_CONFIG['ws_spot']

# API endpoints
ENDPOINTS = {
    'base': BASE_URL,
    'public': f'{BASE_URL}/open',
    'private': f'{BASE_URL}/open',
    'ws_options': WS_OPTIONS,
    'ws_futures': WS_FUTURES,
    'ws_spot': WS_SPOT,
}

# =============================================================================
# ACCOUNT CONFIGURATION
# =============================================================================

# Account Settings
ACCOUNT_CONFIG = {
    'default_leverage': 1,  # Default leverage for positions
    'max_positions': 5,     # Maximum number of open positions
    'max_orders': 10,       # Maximum number of open orders
}

# Risk Management
RISK_CONFIG = {
    'max_portfolio_risk': 0.1,      # Maximum 10% of equity at risk
    'max_single_position_risk': 0.05,  # Maximum 5% of equity per position
    'max_daily_loss': 0.05,         # Maximum 5% daily loss
    'min_margin_level': 1.5,        # Minimum margin level (150%)
    'max_leverage': 5,              # Maximum leverage allowed
}

# =============================================================================
# TRADING CONFIGURATION
# =============================================================================

# Monitoring and Execution
TRADING_CONFIG = {
    'monitor_interval': 60,         # Account monitoring interval (seconds)
    'order_timeout': 30,            # Order execution timeout (seconds)
    'max_retries': 3,               # Maximum API retry attempts
    'retry_delay': 1,               # Delay between retries (seconds)
    'requote_interval': 10,         # Requote interval for limit orders (seconds)
}

# Position Conditions
OPEN_POSITION_CONDITIONS = {
    'underlying_price_range': (10000, 200000),  # Very wide BTCUSD range for testing
    'iv_threshold': 0.0,  # No IV requirement for testing
    'delta_min': 0.0,  # No delta requirement for testing
}

CLOSE_POSITION_CONDITIONS = {
    'profit_target': 0.05,  # 5%
    'loss_limit': -0.02,  # -2%
}

# =============================================================================
# STRATEGY CONFIGURATION
# =============================================================================

# Position configuration for automated trading
POSITION_CONFIG = {
    # Target a single option by symbol expiry token used in Coincall symbolName (no ms math)
    'expiry_criteria': {'symbol': '4FEB26'},
    'legs': [
        {
            'option_type': 'C',  # Call
            'strike_criteria': {'type': 'strike', 'value': 75000},  # Strike $75,000
            'side': 1,  # buy
            'qty': 1
        }
    ]
}

# =============================================================================
# LOGGING CONFIGURATION
# =============================================================================

LOGGING_CONFIG = {
    'level': 'INFO',
    'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    'file': 'logs/trading_bot.log',
    'max_file_size': 10 * 1024 * 1024,  # 10MB
    'backup_count': 5
}

# =============================================================================
# CONFIGURATION VALIDATION
# =============================================================================

def validate_config():
    """Validate that all required configuration is present"""
    required_keys = ['API_KEY', 'API_SECRET']
    missing = []

    for key in required_keys:
        value = globals().get(key)
        if not value:
            missing.append(key)

    if missing:
        env_str = f"({ENVIRONMENT} mode)" if ENVIRONMENT else ""
        raise ValueError(
            f"Missing required API credentials {env_str}: {', '.join(missing)}\n"
            f"Please set environment variables in .env file:\n"
            f"  For testnet: COINCALL_API_KEY_TEST, COINCALL_API_SECRET_TEST\n"
            f"  For production: COINCALL_API_KEY_PROD, COINCALL_API_SECRET_PROD"
        )

    # Validate risk parameters
    if RISK_CONFIG.get('max_portfolio_risk', 0.1) > 1.0:
        raise ValueError("max_portfolio_risk cannot exceed 1.0 (100%)")
    
    if RISK_CONFIG.get('max_daily_loss', 0.05) > 1.0:
        raise ValueError("max_daily_loss cannot exceed 1.0 (100%)")


# Validate on import
validate_config()

# Print configuration status
print(f"[CONFIG] Environment: {ENVIRONMENT.upper()}")
print(f"[CONFIG] Base URL: {BASE_URL}")
print(f"[CONFIG] API Key: {API_KEY[:20]}..." if API_KEY else "[CONFIG] API Key: NOT SET")