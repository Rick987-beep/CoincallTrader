#!/usr/bin/env python3
"""
Telegram Notification Module

Sends trading alerts to a Telegram chat via the Bot API.
Designed to be fire-and-forget: a Telegram failure never crashes the bot.

Strategies opt in by calling ``get_notifier()`` — a lazy singleton that
reads credentials from env vars on first use.  Modules outside strategies
should NOT call the notifier; notification decisions belong to strategies.

Available helpers:
  - notify_startup / notify_shutdown
  - notify_error
  - notify_orphan_detected / notify_reconciliation_warning
  - send() / escape() module-level shortcuts for strategy use

Setup:
  1. Message @BotFather on Telegram → /newbot → get your bot token
  2. Message your new bot, then visit:
     https://api.telegram.org/bot<TOKEN>/getUpdates
     to find your chat_id
  3. Add to .env:
     TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
     TELEGRAM_CHAT_ID=123456789

If TELEGRAM_BOT_TOKEN is not set, the notifier silently no-ops.
"""

import html
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Rate limit: minimum seconds between messages
_MIN_INTERVAL = 1.0

# Module-level singleton — initialised lazily by get_notifier()
_instance: Optional["TelegramNotifier"] = None


def get_notifier() -> "TelegramNotifier":
    """Return the shared TelegramNotifier singleton.

    Creates the instance on first call (reads env vars).  Thread-safe
    via the GIL — subsequent calls return the same object.
    """
    global _instance
    if _instance is None:
        _instance = TelegramNotifier()
    return _instance


def send(message: str, parse_mode: str = "HTML") -> bool:
    """Module-level shortcut: send a message via the singleton notifier.

    Equivalent to ``get_notifier().send(message)``.  Import as::

        from telegram_notifier import send
        send(f"📈 <b>Trade opened</b>\n...")
    """
    return get_notifier().send(message, parse_mode)


def escape(text: str) -> str:
    """Escape a string for safe interpolation inside an HTML-mode Telegram message.

    Converts ``<``, ``>``, ``&``, ``"`` to their HTML entities so dynamic
    values (symbols, labels, numbers) cannot accidentally break message parsing.

    Example::

        from telegram_notifier import send, escape
        send(f"Symbol: {escape(symbol)}  Threshold: &lt;{threshold:.0f}")
    """
    return html.escape(str(text))


class TelegramNotifier:
    """
    Sends messages to a Telegram chat via the Bot API.

    Thread-safe, fire-and-forget.  If credentials are missing the
    instance is created but all send() calls are silent no-ops.
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
    ):
        self._token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self._chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self._enabled = bool(self._token and self._chat_id)
        self._lock = threading.Lock()
        self._last_send: float = 0.0

        if self._enabled:
            self._url = f"https://api.telegram.org/bot{self._token}/sendMessage"
            logger.info("TelegramNotifier enabled")
        else:
            self._url = ""
            logger.info("TelegramNotifier disabled (no TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── Core send ────────────────────────────────────────────────────────

    def send(self, message: str, parse_mode: str = "HTML") -> bool:
        """
        Send a message to the configured Telegram chat.

        Returns True if the message was sent successfully, False otherwise.
        Never raises — all errors are caught and logged locally.
        """
        if not self._enabled:
            return False

        with self._lock:
            # Simple rate limiting
            now = time.time()
            elapsed = now - self._last_send
            if elapsed < _MIN_INTERVAL:
                time.sleep(_MIN_INTERVAL - elapsed)
            self._last_send = time.time()

        try:
            resp = requests.post(
                self._url,
                json={
                    "chat_id": self._chat_id,
                    "text": message,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            if resp.status_code != 200:
                logger.warning(f"Telegram send failed (HTTP {resp.status_code}): {resp.text[:200]}")
                return False
            return True
        except Exception as e:
            logger.warning(f"Telegram send error: {e}")
            return False

    # ── High-level notification helpers ──────────────────────────────────

    def notify_startup(self, environment: str) -> None:
        """Send a system startup notification."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        self.send(
            f"🟢 <b>CryoTrader started</b>\n"
            f"Environment: {environment}\n"
            f"Time: {ts}"
        )

    def notify_shutdown(self) -> None:
        """Send a system shutdown notification."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        self.send(f"🔴 <b>CryoTrader stopped</b>\nTime: {ts}")

    def notify_error(self, message: str) -> None:
        """Send a critical error alert."""
        ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
        self.send(f"🚨 <b>Error</b> ({ts})\n{message}")

    def notify_orphan_detected(self, order_ids: list, action_taken: str) -> None:
        """Alert when orphan orders are found on the exchange."""
        ids_text = ", ".join(str(oid) for oid in order_ids[:5])
        if len(order_ids) > 5:
            ids_text += f" (+{len(order_ids) - 5} more)"
        self.send(
            f"⚠️ <b>Orphan Orders Detected</b>\n"
            f"Count: {len(order_ids)}\n"
            f"IDs: {ids_text}\n"
            f"Action: {action_taken}"
        )

    def notify_reconciliation_warning(self, warnings: list) -> None:
        """Alert on reconciliation mismatches (stale ledger entries)."""
        summary = "\n".join(f"• {w}" for w in warnings[:5])
        if len(warnings) > 5:
            summary += f"\n(+{len(warnings) - 5} more)"
        self.send(
            f"⚠️ <b>Reconciliation Warning</b>\n"
            f"{len(warnings)} issue(s):\n{summary}"
        )