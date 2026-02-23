"""
Strategy definitions.

Each module exports a function that returns a StrategyConfig (or a list
of StrategyConfigs for multi-cycle strategies like rfq_endurance).
Import them here for convenient access from main.py.
"""

from strategies.micro_strangle import micro_strangle_test
from strategies.rfq_endurance import rfq_endurance_test

__all__ = [
    "micro_strangle_test",
    "rfq_endurance_test",
]
