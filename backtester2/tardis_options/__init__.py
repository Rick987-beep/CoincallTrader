"""
tardis_options — Deribit historic option data via tardis.dev.

Workflow:
    fetch.py    — full pipeline: download → extract → delete raw (date range)
    download.py — download a single day's OPTIONS.csv.gz (~4.5 GB)
    extract.py  — filter BTC options with DTE ≤ max_dte → compact parquet
    chain.py    — HistoricOptionChain for fast backtest lookups

Quick start:
    python -m backtester2.tardis_options.fetch --from 2026-03-09 --to 2026-03-23
"""
from backtester2.tardis_options.chain import HistoricOptionChain

__all__ = ["HistoricOptionChain"]
