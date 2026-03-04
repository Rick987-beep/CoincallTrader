#!/usr/bin/env python3
"""
Trade History Persistence Module

Append-only log of completed trades to `logs/trade_history.jsonl`.
One JSON object per line for easy parsing, tailing, and analytics.

Active trade state is handled by LifecycleManager._persist_all_trades()
which writes `logs/trades_snapshot.json` on every tick.
Crash recovery reads that snapshot directly in main.py.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Any

logger = logging.getLogger(__name__)

HISTORY_FILE = os.path.join("logs", "trade_history.jsonl")


class TradeStatePersistence:
    """Manages the append-only completed-trade history log."""

    def __init__(self):
        os.makedirs("logs", exist_ok=True)

    def save_completed_trade(self, trade: Any) -> None:
        """
        Append a completed trade record to the history log.

        Called once per trade when it transitions to CLOSED.
        The history file is an append-only JSON-lines file
        (one JSON object per line) for easy parsing and tailing.

        Args:
            trade: A TradeLifecycle object in CLOSED state.
        """
        history_file = HISTORY_FILE

        try:
            record = {
                "id": trade.id,
                "strategy_id": getattr(trade, "strategy_id", "unknown"),
                "state": trade.state.value if hasattr(trade.state, "value") else str(trade.state),
                "created_at": trade.created_at,
                "opened_at": getattr(trade, "opened_at", None),
                "closed_at": getattr(trade, "closed_at", None),
                "hold_seconds": trade.hold_seconds,
                "entry_cost": trade.total_entry_cost() if hasattr(trade, "total_entry_cost") else 0,
                "exit_cost": getattr(trade, "exit_cost", None),
                "realized_pnl": getattr(trade, "realized_pnl", None),
                "open_legs": [
                    {
                        "symbol": leg.symbol,
                        "qty": leg.qty,
                        "side": leg.side,
                        "fill_price": leg.fill_price,
                        "filled_qty": leg.filled_qty,
                    }
                    for leg in trade.open_legs
                ] if hasattr(trade, "open_legs") else [],
                "close_legs": [
                    {
                        "symbol": leg.symbol,
                        "qty": leg.qty,
                        "side": leg.side,
                        "fill_price": leg.fill_price,
                        "filled_qty": leg.filled_qty,
                    }
                    for leg in trade.close_legs
                ] if hasattr(trade, "close_legs") else [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            with open(history_file, "a") as f:
                f.write(json.dumps(record) + "\n")

            logger.info(
                f"Saved completed trade {trade.id} to history "
                f"(PnL={record['realized_pnl']}, strategy={record['strategy_id']})"
            )

        except Exception as e:
            logger.error(f"Failed to save completed trade {trade.id}: {e}")

    def load_trade_history(self) -> List[Dict[str, Any]]:
        """
        Load all completed trade records from the history log.

        Returns:
            List of trade record dicts, or empty list if no history exists.
        """
        history_file = HISTORY_FILE
        if not os.path.exists(history_file):
            return []

        records = []
        try:
            with open(history_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
            logger.info(f"Loaded {len(records)} completed trades from {history_file}")
        except Exception as e:
            logger.error(f"Failed to load trade history: {e}")
        return records
