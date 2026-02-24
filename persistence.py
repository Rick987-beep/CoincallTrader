#!/usr/bin/env python3
"""
Trade State Persistence Module

Saves and recovers active trade state to/from JSON.
Useful for crash recovery and operational visibility.

On every `tick()`, saves active trades to `logs/trade_state.json`.
On startup, can recover open positions from persistent state.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


class TradeStatePersistence:
    """Manages trade state snapshots for crash recovery."""

    def __init__(self, state_file: str = "logs/trade_state.json"):
        """
        Initialize persistence layer.

        Args:
            state_file: Path to JSON file for state snapshots
        """
        self.state_file = state_file
        self._last_save_time = 0
        self._save_interval = 60  # Save every 60 seconds

    def save_trades(self, trades: List[Any]) -> None:
        """
        Save active trades to JSON snapshot.

        Throttled to save every 60 seconds to avoid excessive I/O.

        Args:
            trades: List of TradeLifecycle objects with state to persist
        """
        now = time.time()
        if now - self._last_save_time < self._save_interval:
            return  # Skip if we saved recently

        # Ensure logs directory exists
        os.makedirs(os.path.dirname(self.state_file) or "logs", exist_ok=True)

        try:
            snapshot = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trade_count": len(trades),
                "trades": [],
            }

            for trade in trades:
                trade_dict = {
                    "id": trade.id,
                    "strategy_id": getattr(trade, "strategy_id", "unknown"),
                    "state": trade.state.value if hasattr(trade.state, "value") else str(trade.state),
                    "created_at": trade.created_at,
                    "open_legs": [
                        {
                            "symbol": leg.symbol,
                            "qty": leg.qty,
                            "side": leg.side,
                            "order_id": leg.order_id,
                        }
                        for leg in trade.open_legs
                    ] if hasattr(trade, "open_legs") else [],
                    "entry_cost": trade.total_entry_cost() if hasattr(trade, "total_entry_cost") else 0,
                }
                snapshot["trades"].append(trade_dict)

            with open(self.state_file, "w") as f:
                json.dump(snapshot, f, indent=2)

            logger.debug(f"Saved {len(trades)} active trades to {self.state_file}")
            self._last_save_time = now

        except Exception as e:
            logger.error(f"Failed to save trade state: {e}")

    def load_trades(self) -> Optional[Dict[str, Any]]:
        """
        Load last saved trade state from JSON.

        Returns:
            Loaded state dict, or None if no state file exists
        """
        if not os.path.exists(self.state_file):
            return None

        try:
            with open(self.state_file, "r") as f:
                state = json.load(f)
            logger.info(f"Loaded trade state: {len(state.get('trades', []))} trades from {self.state_file}")
            return state
        except Exception as e:
            logger.error(f"Failed to load trade state: {e}")
            return None

    def clear(self) -> None:
        """Clear the persistent state file."""
        if os.path.exists(self.state_file):
            try:
                os.remove(self.state_file)
                logger.debug(f"Cleared trade state file: {self.state_file}")
            except Exception as e:
                logger.error(f"Failed to clear trade state: {e}")
