"""
Strategy definitions.

Each module exports a function that returns a StrategyConfig.
Import them here for convenient access from main.py.
"""

from strategies.micro_strangle import micro_strangle_test

__all__ = [
    "micro_strangle_test",
]
