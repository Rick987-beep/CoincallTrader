#!/usr/bin/env python3
"""
CryoTrader — Application Configuration

Resolves environment variables (from .env) into typed Python constants that
the rest of the application imports.  This module is the **single entry
point** for three configuration axes:

  1. DEPLOYMENT_TARGET  — 'development' (macOS local) or 'production' (VPS)
  2. EXCHANGE           — 'coincall' or 'deribit'
  3. ENVIRONMENT        — 'testnet' or 'production' (trading venue)

Design principles:
  • Secrets live in .env (gitignored).  This file never contains secrets.
  • Exchange URLs are owned by their respective adapters
    (exchanges/coincall/, exchanges/deribit/).  This module re-exports
    BASE_URL and DERIBIT_BASE_URL for legacy callers only.
  • Strategy parameters are NOT here — they flow through the slot system:
    slots/slot-XX.toml → slot_config.py → .env.slot-XX → PARAM_* env vars.
  • Execution profiles live in execution_profiles.toml.
  • Account-to-credential mappings live in accounts.toml.

Env vars consumed (all optional with defaults shown):
  DEPLOYMENT_TARGET      = development
  EXCHANGE               = coincall
  TRADING_ENVIRONMENT    = testnet
  COINCALL_API_KEY_TEST / _PROD
  COINCALL_API_SECRET_TEST / _PROD
  DERIBIT_CLIENT_ID_TEST / _PROD
  DERIBIT_CLIENT_SECRET_TEST / _PROD
  DASHBOARD_MODE         = full
  SLOT_NAME              = (empty)
"""

import os
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap: load .env before anything else reads os.getenv()
# ---------------------------------------------------------------------------
load_dotenv()


# =============================================================================
# 1. DEPLOYMENT TARGET
# =============================================================================
# 'development' — local macOS; enables stale-log cleanup, verbose logging.
# 'production'  — VPS; disables dev-only helpers.

DEPLOYMENT_TARGET = os.getenv('DEPLOYMENT_TARGET', 'development').lower()

if DEPLOYMENT_TARGET not in ['development', 'production']:
    raise ValueError(f"Invalid DEPLOYMENT_TARGET: '{DEPLOYMENT_TARGET}'. Must be 'development' or 'production'")


# =============================================================================
# 2. EXCHANGE SELECTION
# =============================================================================
# Determines which exchange adapter package (exchanges/coincall or
# exchanges/deribit) is instantiated by exchanges.build_exchange().

EXCHANGE = os.getenv('EXCHANGE', 'coincall').lower()

if EXCHANGE not in ['coincall', 'deribit']:
    raise ValueError(f"Invalid EXCHANGE: '{EXCHANGE}'. Must be 'coincall' or 'deribit'")


# =============================================================================
# 3. TRADING ENVIRONMENT
# =============================================================================
# 'testnet'    — exchange sandbox / paper trading.
# 'production' — real money, real exchange endpoints.
# This controls which set of API credentials is loaded below and which
# endpoint URLs the exchange adapters resolve.

ENVIRONMENT = os.getenv('TRADING_ENVIRONMENT', 'testnet').lower()

if ENVIRONMENT not in ['testnet', 'production']:
    raise ValueError(f"Invalid TRADING_ENVIRONMENT: '{ENVIRONMENT}'. Must be 'testnet' or 'production'")


# =============================================================================
# 4. COINCALL CREDENTIALS
# =============================================================================
# Keyed by ENVIRONMENT so the correct testnet/production secrets are
# selected automatically.  The actual values come from .env.

_COINCALL_CREDS = {
    'testnet': {
        'api_key': os.getenv('COINCALL_API_KEY_TEST'),
        'api_secret': os.getenv('COINCALL_API_SECRET_TEST'),
    },
    'production': {
        'api_key': os.getenv('COINCALL_API_KEY_PROD'),
        'api_secret': os.getenv('COINCALL_API_SECRET_PROD'),
    },
}

API_KEY = _COINCALL_CREDS[ENVIRONMENT]['api_key']
API_SECRET = _COINCALL_CREDS[ENVIRONMENT]['api_secret']

# BASE_URL: the Coincall REST endpoint.  URL ownership lives in the
# Coincall adapter (exchanges/coincall/__init__.py); we re-export it
# here because legacy root modules (market_data.py, rfq.py,
# trade_execution.py, account_manager.py) still do
# `from config import BASE_URL`.
from exchanges.coincall import get_coincall_base_url as _get_cc_url
BASE_URL = _get_cc_url(ENVIRONMENT)


# =============================================================================
# 5. DERIBIT CREDENTIALS
# =============================================================================
# Same pattern as Coincall: credentials from .env, URL from adapter.

_DERIBIT_CREDS = {
    'testnet': {
        'client_id': os.getenv('DERIBIT_CLIENT_ID_TEST'),
        'client_secret': os.getenv('DERIBIT_CLIENT_SECRET_TEST'),
    },
    'production': {
        'client_id': os.getenv('DERIBIT_CLIENT_ID_PROD'),
        'client_secret': os.getenv('DERIBIT_CLIENT_SECRET_PROD'),
    },
}

DERIBIT_CLIENT_ID = _DERIBIT_CREDS[ENVIRONMENT]['client_id']
DERIBIT_CLIENT_SECRET = _DERIBIT_CREDS[ENVIRONMENT]['client_secret']

# DERIBIT_BASE_URL: same re-export pattern as BASE_URL above.
from exchanges.deribit import get_deribit_base_url as _get_db_url
DERIBIT_BASE_URL = _get_db_url(ENVIRONMENT)


# =============================================================================
# 6. CONFIGURATION VALIDATION
# =============================================================================
# Runs on import.  Ensures the selected exchange has credentials set.
# Fails fast so we don't discover missing keys mid-trade.

def validate_config():
    """Raise ValueError if required API credentials for the active exchange are missing."""
    if EXCHANGE == 'coincall':
        required = {'API_KEY': API_KEY, 'API_SECRET': API_SECRET}
    else:
        required = {'DERIBIT_CLIENT_ID': DERIBIT_CLIENT_ID,
                     'DERIBIT_CLIENT_SECRET': DERIBIT_CLIENT_SECRET}

    missing = [k for k, v in required.items() if not v]
    if missing:
        raise ValueError(
            f"Missing required API credentials for {EXCHANGE} "
            f"({ENVIRONMENT} mode): {', '.join(missing)}\n"
            f"Please set environment variables in .env file."
        )


# =============================================================================
# 7. DASHBOARD MODE
# =============================================================================
# Controls the web dashboard exposed by each slot process.
#   'full'     — normal dashboard with UI (default for dev)
#   'control'  — headless; only control endpoints, bound to 127.0.0.1
#   'disabled' — no HTTP server at all

DASHBOARD_MODE = os.getenv('DASHBOARD_MODE', 'full').lower()

if DASHBOARD_MODE not in ['full', 'control', 'disabled']:
    raise ValueError(f"Invalid DASHBOARD_MODE: '{DASHBOARD_MODE}'. Must be 'full', 'control', or 'disabled'")

# Human-readable slot label shown in the hub dashboard (e.g. "Put Sell 80 DTE").
# Set automatically by slot_config.py when generating .env.slot-XX files.
SLOT_NAME = os.getenv('SLOT_NAME', '')


# =============================================================================
# 8. STARTUP VALIDATION & BANNER
# =============================================================================

validate_config()

print(f"[CONFIG] Deployment: {DEPLOYMENT_TARGET.upper()}")
print(f"[CONFIG] Exchange: {EXCHANGE.upper()}")
print(f"[CONFIG] Environment: {ENVIRONMENT.upper()}")
if EXCHANGE == 'coincall':
    print(f"[CONFIG] Base URL: {BASE_URL}")
    print(f"[CONFIG] API Key: {API_KEY[:20]}..." if API_KEY else "[CONFIG] API Key: NOT SET")
else:
    print(f"[CONFIG] Base URL: {DERIBIT_BASE_URL}")
    print(f"[CONFIG] Client ID: {DERIBIT_CLIENT_ID[:20]}..." if DERIBIT_CLIENT_ID else "[CONFIG] Client ID: NOT SET")