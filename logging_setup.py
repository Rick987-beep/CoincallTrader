#!/usr/bin/env python3
"""
Logging Setup — Structured multi-track logging for CoincallTrader.

Initialises three track loggers in the ct.* namespace, each writing
structured JSONL to a dedicated rotating file:

  ct.health    → logs/health.jsonl    (5-min account snapshots, 30 days)
  ct.strategy  → logs/strategy.jsonl  (lifecycle events, 60 days)
  ct.execution → logs/execution.jsonl (order/phase events, 14 days)

Root logger continues writing human-readable text to trading.log.

All TimedRotatingFileHandler instances use delay=True (file not opened
until first write) to avoid phantom files on idle slots, and the
stdlib midnight rotation auto-deletes files beyond backupCount.

Call once at process startup before any other module imports that use
logging.
"""

import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler

# Production module loggers that should be promoted from WARNING to INFO.
_PRODUCTION_INFO_LOGGERS = (
    "__main__", "strategy", "trade_lifecycle", "trade_execution",
    "rfq", "account_manager", "dashboard", "persistence",
    "strategies.daily_put_sell", "strategies.atm_straddle",
    "strategies.blueprint_strangle", "strategies.long_strangle_index_move",
    "order_manager", "ema_filter", "telegram_notifier", "health_check",
    "execution_router",
)


def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class JsonlFormatter(logging.Formatter):
    """
    Formats LogRecord.msg dict as a single JSON line.

    Automatically injects:
      - "ts"       : ISO-8601 UTC timestamp
      - "slot"     : SLOT_ID env var (default "??")
      - "strategy" : SLOT_STRATEGY env var (default "??")

    If record.msg is not a dict, wraps it as {"msg": str(...)}.
    Reads SLOT_ID / SLOT_STRATEGY at format time (not construction time)
    so tests can patch os.environ.
    """

    def format(self, record: logging.LogRecord) -> str:
        slot = os.getenv("SLOT_ID", "??")
        strategy = os.getenv("SLOT_STRATEGY", "??")

        msg = record.msg
        if isinstance(msg, dict):
            data: dict = {"ts": _now_ts(), "slot": slot, "strategy": strategy, **msg}
        else:
            data = {
                "ts": _now_ts(),
                "slot": slot,
                "strategy": strategy,
                "msg": record.getMessage(),
            }
        try:
            return json.dumps(data, default=str)
        except Exception:
            return json.dumps({"ts": data.get("ts", ""), "error": "serialization_failed"})


def _make_rotating_handler(path: str, backup_count: int) -> logging.Handler:
    """
    Create a TimedRotatingFileHandler with safe defaults.

    Uses delay=True: file is not opened until the first write, so idle
    slots (e.g. inactive slot-02) don't create empty log files.
    Falls back to a plain FileHandler if TimedRotatingFileHandler fails.
    """
    try:
        h = TimedRotatingFileHandler(
            path,
            when="midnight",
            backupCount=backup_count,
            delay=True,
            encoding="utf-8",
        )
        return h
    except Exception:
        return logging.FileHandler(path, encoding="utf-8", delay=True)


def setup_logging(dev_mode: bool, logs_dir: str = "logs") -> None:
    """
    Configure root logger + three ct.* track loggers.

    Root logger → trading.log (human-readable, daily rotation, 14 days)
                + stdout StreamHandler
    ct.health    → health.jsonl    (JSONL, 30 days)
    ct.strategy  → strategy.jsonl  (JSONL, 60 days)
    ct.execution → execution.jsonl (JSONL, 14 days)

    Args:
        dev_mode: True = DEBUG level, False = WARNING root with key modules at INFO.
        logs_dir: Directory for all log files (created if absent).
    """
    try:
        os.makedirs(logs_dir, exist_ok=True)
    except Exception as exc:
        # Can't create the logs directory — fall back gracefully.
        logging.basicConfig(level=logging.DEBUG if dev_mode else logging.WARNING)
        logging.getLogger(__name__).warning(
            f"Could not create logs dir {logs_dir!r}: {exc} — using basicConfig fallback"
        )
        return

    # ── Root logger ───────────────────────────────────────────────────────
    root_level = logging.DEBUG if dev_mode else logging.WARNING
    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    root = logging.getLogger()
    root.setLevel(root_level)

    file_h = _make_rotating_handler(os.path.join(logs_dir, "trading.log"), backup_count=14)
    file_h.setFormatter(fmt)
    root.addHandler(file_h)

    stream_h = logging.StreamHandler()
    stream_h.setFormatter(fmt)
    root.addHandler(stream_h)

    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    if not dev_mode:
        for name in _PRODUCTION_INFO_LOGGERS:
            logging.getLogger(name).setLevel(logging.INFO)

    # ── Track loggers ────────────────────────────────────────────────────
    jsonl_fmt = JsonlFormatter()
    for logger_name, filename, backup_count in (
        ("ct.health",    "health.jsonl",    30),
        ("ct.strategy",  "strategy.jsonl",  60),
        ("ct.execution", "execution.jsonl", 14),
    ):
        lg = logging.getLogger(logger_name)
        lg.setLevel(logging.INFO)
        lg.propagate = False
        h = _make_rotating_handler(os.path.join(logs_dir, filename), backup_count)
        h.setFormatter(jsonl_fmt)
        lg.addHandler(h)
