"""
tardis_options — Deribit historic option data via tardis.dev.

Workflow:
    1. download.py  — fetch raw options_chain .csv.gz from tardis.dev
    2. extract.py   — filter BTC 0DTE/1DTE → compact parquet
    3. chain.py     — HistoricOptionChain for fast backtest lookups
"""
from analysis.tardis_options.chain import HistoricOptionChain

__all__ = ["HistoricOptionChain"]
