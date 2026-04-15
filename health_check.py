#!/usr/bin/env python3
"""
Health Check Module — Observability Only

Logs system health status every 5 minutes.
Pure observability: no restart logic, no notifications.
Process supervision is handled by NSSM; daily summary by TelegramNotifier.

Provides visibility into:
- Uptime tracking
- Account equity/margin
- Active positions
- Warning escalation for high margin / low equity
"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional, Callable

logger = logging.getLogger(__name__)
_health_logger = logging.getLogger("ct.health")  # structured JSONL → logs/health.jsonl


class HealthChecker:
    """Logs system health at regular intervals. Observability only — no side effects."""

    def __init__(self, check_interval: int = 300, account_snapshot_fn: Optional[Callable] = None,
                 market_data=None):
        """
        Initialize health checker.

        Args:
            check_interval: Interval between health checks in seconds (default 5 min = 300s)
            account_snapshot_fn: Function to call for latest account snapshot
            market_data: ExchangeMarketData adapter for BTC index price
        """
        self.check_interval = check_interval
        self.account_snapshot_fn = account_snapshot_fn
        self._market_data = market_data
        self._running = False
        self._thread = None
        self._start_time = time.time()

    def set_account_snapshot_fn(self, fn: Callable) -> None:
        """Set the function to fetch account snapshots."""
        self.account_snapshot_fn = fn

    def start(self) -> None:
        """Start background health check thread."""
        if self._running:
            logger.warning("HealthChecker already running")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._check_loop,
            name="HealthChecker",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"HealthChecker started (interval={self.check_interval}s)")

    def stop(self) -> None:
        """Stop health check thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=self.check_interval + 2)
            self._thread = None
        logger.info("HealthChecker stopped")

    def _check_loop(self) -> None:
        """Background loop: periodic health checks."""
        while self._running:
            try:
                self._log_health_status()
            except Exception as e:
                logger.error(f"Health check error: {e}", exc_info=True)

            # Sleep in small increments so stop() is responsive
            for _ in range(self.check_interval * 10):
                if not self._running:
                    return
                time.sleep(0.1)

    def _log_health_status(self) -> None:
        """Emit one structured record to ct.health and escalate warnings to trading.log."""
        uptime_secs = int(time.time() - self._start_time)

        record: dict = {
            "event": "health_check",
            "uptime_s": uptime_secs,
        }
        level = "ok"

        # Account snapshot
        if self.account_snapshot_fn:
            try:
                snapshot = self.account_snapshot_fn()
                if snapshot:
                    record.update({
                        "equity": round(snapshot.equity, 2),
                        "avail_margin": round(snapshot.available_margin, 2),
                        "margin_pct": round(snapshot.margin_utilization, 1),
                        "net_delta": round(snapshot.net_delta, 4),
                        "positions": snapshot.position_count,
                    })
                    if snapshot.margin_utilization > 80:
                        level = "warn"
                        logger.warning(f"HIGH MARGIN UTILIZATION: {snapshot.margin_utilization:.1f}%")
                    if snapshot.equity < 100:
                        level = "warn"
                        logger.warning(f"LOW EQUITY: ${snapshot.equity:,.2f}")
                else:
                    level = "warn"
                    logger.warning("Health check: account snapshot returned None")
            except Exception as e:
                level = "warn"
                logger.warning(f"Health check: account snapshot failed: {e}")

        # BTC index price
        try:
            if self._market_data:
                idx_price = self._market_data.get_index_price()
            else:
                from market_data import get_btc_index_price
                idx_price = get_btc_index_price(use_cache=False)
            if idx_price is not None:
                record["btc_price"] = idx_price
            else:
                level = "warn"
                logger.warning("Health check: BTC index price unavailable")
        except Exception as e:
            level = "warn"
            logger.warning(f"Health check: BTC index price failed: {e}")

        record["level"] = level
        _health_logger.info(record)

    @staticmethod
    def _format_uptime(seconds: int) -> str:
        """Format uptime as human-readable string."""
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60

        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        if secs > 0 or not parts:
            parts.append(f"{secs}s")

        return " ".join(parts)
