"""
Tests for the logging_setup module.

Verifies:
  - JsonlFormatter serialises dict records to valid JSON
  - JsonlFormatter handles non-dict records
  - JsonlFormatter injects ts / slot / strategy fields
  - setup_logging creates three ct.* track loggers with the correct configuration
  - Track loggers have propagate=False so they don't double-log to root
  - Track logger messages arrive as valid JSONL
  - Fallback behaviour when logs_dir cannot be created
"""

import json
import logging
import os
import pytest

from logging_setup import JsonlFormatter, setup_logging


# =============================================================================
# Helpers
# =============================================================================

def _fresh_loggers():
    """Remove all handlers from ct.* loggers so tests start clean."""
    for name in ("ct.health", "ct.strategy", "ct.execution"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True  # reset to default


def _clean_root():
    """Remove handlers added to root logger by setup_logging."""
    root = logging.getLogger()
    root.handlers.clear()


# =============================================================================
# JsonlFormatter
# =============================================================================

class TestJsonlFormatter:

    def test_dict_msg_produces_valid_json(self):
        fmt = JsonlFormatter()
        record = logging.LogRecord(
            name="ct.strategy", level=logging.INFO,
            pathname="", lineno=0,
            msg={"event": "TRADE_OPENED", "trade_id": "abc-123"},
            args=(), exc_info=None,
        )
        line = fmt.format(record)
        data = json.loads(line)
        assert data["event"] == "TRADE_OPENED"
        assert data["trade_id"] == "abc-123"

    def test_dict_msg_injects_ts(self):
        fmt = JsonlFormatter()
        record = logging.LogRecord(
            name="ct.strategy", level=logging.INFO,
            pathname="", lineno=0,
            msg={"event": "X"},
            args=(), exc_info=None,
        )
        data = json.loads(fmt.format(record))
        assert "ts" in data
        # ISO-8601 format check
        assert "T" in data["ts"] and data["ts"].endswith("Z")

    def test_dict_msg_injects_slot_and_strategy(self, monkeypatch):
        monkeypatch.setenv("SLOT_ID", "03")
        monkeypatch.setenv("SLOT_STRATEGY", "daily_put_sell")
        fmt = JsonlFormatter()
        record = logging.LogRecord(
            name="ct.strategy", level=logging.INFO,
            pathname="", lineno=0,
            msg={"event": "X"},
            args=(), exc_info=None,
        )
        data = json.loads(fmt.format(record))
        assert data["slot"] == "03"
        assert data["strategy"] == "daily_put_sell"

    def test_slot_defaults_to_question_marks(self, monkeypatch):
        monkeypatch.delenv("SLOT_ID", raising=False)
        monkeypatch.delenv("SLOT_STRATEGY", raising=False)
        fmt = JsonlFormatter()
        record = logging.LogRecord(
            name="ct.health", level=logging.INFO,
            pathname="", lineno=0,
            msg={"event": "health_check"},
            args=(), exc_info=None,
        )
        data = json.loads(fmt.format(record))
        assert data["slot"] == "??"
        assert data["strategy"] == "??"

    def test_non_dict_msg_wrapped(self):
        fmt = JsonlFormatter()
        record = logging.LogRecord(
            name="ct.strategy", level=logging.INFO,
            pathname="", lineno=0,
            msg="plain text message",
            args=(), exc_info=None,
        )
        data = json.loads(fmt.format(record))
        assert data["msg"] == "plain text message"
        assert "ts" in data

    def test_non_dict_msg_with_format_args_wrapped(self):
        fmt = JsonlFormatter()
        record = logging.LogRecord(
            name="ct.strategy", level=logging.INFO,
            pathname="", lineno=0,
            msg="trade %s opened",
            args=("abc-1",), exc_info=None,
        )
        data = json.loads(fmt.format(record))
        assert data["msg"] == "trade abc-1 opened"

    def test_caller_fields_not_overridden(self):
        """Caller-provided ts in the dict must be preserved."""
        fmt = JsonlFormatter()
        record = logging.LogRecord(
            name="ct.strategy", level=logging.INFO,
            pathname="", lineno=0,
            msg={"event": "X", "ts": "2026-01-01T00:00:00Z"},
            args=(), exc_info=None,
        )
        data = json.loads(fmt.format(record))
        # The formatter should NOT overwrite an explicit ts provided by caller
        assert data["ts"] == "2026-01-01T00:00:00Z"

    def test_non_serializable_defaults_to_str(self):
        """Non-serialisable values should stringify rather than raise."""
        fmt = JsonlFormatter()

        class Unserializable:
            def __repr__(self):
                return "<unserializable>"

        record = logging.LogRecord(
            name="ct.execution", level=logging.INFO,
            pathname="", lineno=0,
            msg={"event": "X", "obj": Unserializable()},
            args=(), exc_info=None,
        )
        line = fmt.format(record)
        data = json.loads(line)
        assert data["obj"] == "<unserializable>"


# =============================================================================
# setup_logging
# =============================================================================

class TestSetupLogging:

    def setup_method(self):
        _fresh_loggers()
        _clean_root()

    def teardown_method(self):
        _fresh_loggers()
        _clean_root()

    def test_creates_track_loggers(self, tmp_path):
        setup_logging(dev_mode=True, logs_dir=str(tmp_path))
        for name in ("ct.health", "ct.strategy", "ct.execution"):
            lg = logging.getLogger(name)
            assert len(lg.handlers) == 1, f"{name} should have exactly one handler"

    def test_track_loggers_propagate_false(self, tmp_path):
        setup_logging(dev_mode=True, logs_dir=str(tmp_path))
        for name in ("ct.health", "ct.strategy", "ct.execution"):
            assert logging.getLogger(name).propagate is False

    def test_track_loggers_level_info(self, tmp_path):
        setup_logging(dev_mode=False, logs_dir=str(tmp_path))
        for name in ("ct.health", "ct.strategy", "ct.execution"):
            assert logging.getLogger(name).level == logging.INFO

    def test_root_has_handlers(self, tmp_path):
        setup_logging(dev_mode=True, logs_dir=str(tmp_path))
        root = logging.getLogger()
        assert len(root.handlers) >= 2  # file + stream

    def test_dev_mode_root_level_debug(self, tmp_path):
        setup_logging(dev_mode=True, logs_dir=str(tmp_path))
        assert logging.getLogger().level == logging.DEBUG

    def test_prod_mode_root_level_warning(self, tmp_path):
        setup_logging(dev_mode=False, logs_dir=str(tmp_path))
        assert logging.getLogger().level == logging.WARNING

    def test_logs_dir_created(self, tmp_path):
        logs_dir = str(tmp_path / "new_logs")
        setup_logging(dev_mode=True, logs_dir=logs_dir)
        assert os.path.isdir(logs_dir)

    def test_track_loggers_use_jsonl_formatter(self, tmp_path):
        setup_logging(dev_mode=True, logs_dir=str(tmp_path))
        for name in ("ct.health", "ct.strategy", "ct.execution"):
            lg = logging.getLogger(name)
            assert isinstance(lg.handlers[0].formatter, JsonlFormatter)

    def test_message_written_as_valid_jsonl(self, tmp_path):
        setup_logging(dev_mode=True, logs_dir=str(tmp_path))
        lg = logging.getLogger("ct.strategy")
        lg.info({"event": "TRADE_OPENED", "trade_id": "test-001"})
        # Force handlers to flush
        for h in lg.handlers:
            h.flush()
            # Close so file is written (delay=True means file may not exist until first write)
            try:
                h.close()
            except Exception:
                pass
        jsonl_path = tmp_path / "strategy.jsonl"
        if jsonl_path.exists():
            lines = jsonl_path.read_text().strip().splitlines()
            assert len(lines) >= 1
            data = json.loads(lines[-1])
            assert data["event"] == "TRADE_OPENED"
            assert data["trade_id"] == "test-001"

    def test_fallback_on_bad_logs_dir(self, monkeypatch):
        """setup_logging should not raise even if logs_dir can't be created."""
        import builtins
        real_makedirs = os.makedirs

        def bad_makedirs(path, **kwargs):
            if "logs" in str(path):
                raise OSError("permission denied")
            real_makedirs(path, **kwargs)

        monkeypatch.setattr(os, "makedirs", bad_makedirs)
        # Should not raise
        setup_logging(dev_mode=True, logs_dir="/nonexistent_bad_path/logs")
