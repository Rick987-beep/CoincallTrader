"""
Deribit Exchange Adapter Package

Implements the exchange abstraction interfaces for Deribit.
All prices internally are BTC-denominated (Deribit native); USD conversion
happens at adapter boundaries where the rest of the system expects USD.
"""
# ---------------------------------------------------------------------------
# Deribit endpoint URLs — owned by this adapter, not config.py
# ---------------------------------------------------------------------------
DERIBIT_URLS = {
    'testnet': {
        'base_url': 'https://test.deribit.com',
    },
    'production': {
        'base_url': 'https://www.deribit.com',
    },
}


def get_deribit_base_url(environment: str) -> str:
    """Resolve the Deribit REST base URL for the given environment."""
    return DERIBIT_URLS[environment]['base_url']
from order_manager import OrderStatus

# Deribit order_state (string) → internal OrderStatus
DERIBIT_STATE_MAP = {
    "open": OrderStatus.LIVE,
    "filled": OrderStatus.FILLED,
    "cancelled": OrderStatus.CANCELLED,
    "rejected": OrderStatus.REJECTED,
    "untriggered": OrderStatus.LIVE,      # stop orders waiting for trigger
}


def build_deribit() -> dict:
    """Construct all Deribit adapter instances."""
    from exchanges.deribit.auth import DeribitAuth
    from exchanges.deribit.market_data import DeribitMarketDataAdapter
    from exchanges.deribit.executor import DeribitExecutorAdapter
    from exchanges.deribit.account import DeribitAccountAdapter
    from exchanges.deribit.rfq import DeribitRFQAdapter

    auth = DeribitAuth()

    return {
        "auth": auth,
        "market_data": DeribitMarketDataAdapter(auth),
        "executor": DeribitExecutorAdapter(auth),
        "account_manager": DeribitAccountAdapter(auth),
        "rfq_executor": DeribitRFQAdapter(auth),
        "state_map": DERIBIT_STATE_MAP,
    }
