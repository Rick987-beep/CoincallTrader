"""
Strategy definitions.

Each module exports a function that returns a StrategyConfig.

In production (SLOT_STRATEGY set), main.py imports the single strategy
dynamically via importlib — this file is intentionally empty so that
importing one strategy does not load all others.

In dev mode (no SLOT_STRATEGY), main.py imports individual strategies
directly from their modules.
"""
