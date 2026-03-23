"""
Strategy definitions.

Each module exports a function that returns a StrategyConfig.
Import them here for convenient access from main.py.

Note: With SLOT_STRATEGY env var, main.py imports strategies dynamically
and does not depend on this file.  These imports are for dev convenience.
"""

from strategies.blueprint_strangle import blueprint_strangle
from strategies.atm_straddle_index_move import atm_straddle_index_move
from strategies.daily_put_sell import daily_put_sell
from strategies.long_strangle_index_move import long_strangle_index_move

__all__ = [
    "blueprint_strangle",
    "atm_straddle_index_move",
    "daily_put_sell",
    "long_strangle_index_move",
]
