#!/usr/bin/env python3
"""
Web Dashboard Module

Lightweight Flask + htmx dashboard for CoincallTrader.
Runs on a daemon thread inside the existing process — reads TradingContext
and StrategyRunner state directly, no IPC needed.

Features:
  - Account summary (equity, margin, Greeks)
  - Strategy status cards with Pause / Resume / Stop controls
  - Open positions table
  - Live log tail
  - Kill switch (aggressive close-all)

Setup:
  Add to .env:
    DASHBOARD_PASSWORD=your_secret     (required — dashboard is disabled without it)
    DASHBOARD_PORT=8080                (optional, default 8080)

Usage (wired automatically via main.py):
    from dashboard import start_dashboard
    start_dashboard(ctx, runners, host="0.0.0.0", port=8080)
"""

import logging
import os
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timezone
from functools import wraps
from typing import TYPE_CHECKING, List, Optional

from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for
from position_closer import PositionCloser

if TYPE_CHECKING:
    from strategy import StrategyRunner, TradingContext

logger = logging.getLogger(__name__)

# Maximum log lines to keep in memory for the live tail
_LOG_TAIL_LINES = 200


# =============================================================================
# In-memory log handler — captures recent log entries for the dashboard
# =============================================================================

class DashboardLogHandler(logging.Handler):
    """Ring-buffer handler that keeps the last N log records for display."""

    def __init__(self, maxlen: int = _LOG_TAIL_LINES):
        super().__init__()
        self.records: deque = deque(maxlen=maxlen)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append(self.format(record))
        except Exception:
            pass  # Never break application logging


# Singleton handler — attached to root logger on first start
_log_handler: Optional[DashboardLogHandler] = None
_dashboard_start_time: float = 0.0
# Brute-force guard: maps remote IP → (consecutive_fail_count, lockout_expiry_epoch).
# After 5 failures the IP is soft-locked for 60 s. Reset on success.
_login_rate: dict = {}  # ip -> (fail_count, lockout_until)


def _get_log_lines(n: int = 80) -> List[str]:
    """Return the last *n* formatted log lines."""
    if _log_handler is None:
        return ["(log capture not initialised)"]
    return list(_log_handler.records)[-n:]


# =============================================================================
# Flask App Factory
# =============================================================================

def _create_app(
    ctx: "TradingContext",
    runners: List["StrategyRunner"],
    password: str,
) -> Flask:
    """Build and return the Flask application."""

    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "templates"),
    )
    app.secret_key = secrets.token_hex(32)
    # HTTPONLY: blocks JS document.cookie access (XSS mitigation).
    # SAMESITE=Lax: blocks the session cookie from being sent in cross-site
    # top-level POST requests (CSRF mitigation without a token).
    # SECURE is intentionally omitted — HTTPS termination is handled by nginx.
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    from config import ENVIRONMENT

    # ── Auth helpers ─────────────────────────────────────────────────────

    def login_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get("authenticated"):
                if request.headers.get("HX-Request"):
                    return Response("Unauthorized", status=401)
                return redirect(url_for("login"))
            return f(*args, **kwargs)
        return decorated

    # /control/* routes are called only by the hub dashboard (same host, localhost).
    # This decorator rejects any request that did not originate from 127.0.0.1,
    # regardless of whether DASHBOARD_MODE is 'full' (bound to 0.0.0.0).
    # Covers both IPv4 loopback and IPv4-mapped IPv6 (::ffff:127.0.0.1).
    def localhost_only(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            remote = request.remote_addr
            if remote not in ('127.0.0.1', '::1', '::ffff:127.0.0.1'):
                return Response("Forbidden", status=403)
            return f(*args, **kwargs)
        return decorated

    # ── Pages ────────────────────────────────────────────────────────────

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            ip = request.remote_addr or ""
            count, lockout_until = _login_rate.get(ip, (0, 0.0))
            now = time.time()
            if now < lockout_until:
                remaining = int(lockout_until - now)
                return render_template("login.html", error=f"Too many attempts — try again in {remaining}s")
            # compare_digest does a constant-time comparison, preventing timing
            # attacks that could otherwise leak password characters byte-by-byte.
            if secrets.compare_digest(request.form.get("password") or "", password):
                _login_rate.pop(ip, None)  # clear failure counter on success
                session["authenticated"] = True
                return redirect(url_for("index"))
            count += 1
            _login_rate[ip] = (count, now + 60.0) if count >= 5 else (count, 0.0)
            error = "Invalid password"
        return render_template("login.html", error=error)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    @login_required
    def index():
        return render_template(
            "dashboard.html",
            environment=ENVIRONMENT.upper(),
        )

    # ── htmx Fragment Endpoints ──────────────────────────────────────────

    @app.route("/api/account")
    @login_required
    def api_account():
        snap = ctx.position_monitor.latest
        if not snap:
            return "<p class='muted'>Waiting for first snapshot...</p>"

        ts = datetime.fromtimestamp(snap.timestamp, tz=timezone.utc).strftime("%H:%M:%S UTC")
        return render_template("_account.html", snap=snap, ts=ts)

    @app.route("/api/strategies")
    @login_required
    def api_strategies():
        snap = ctx.position_monitor.latest
        strategy_data = []
        for r in runners:
            strategy_data.append({
                "name": r.config.name,
                "enabled": r._enabled,
                "active_trades": len(r.active_trades),
                "max_trades": r.config.max_concurrent_trades,
                "stats": r.stats,
                "status_lines": r.status(snap).split("\n") if snap else [],
            })
        return render_template("_strategies.html", strategies=strategy_data)

    @app.route("/api/positions")
    @login_required
    def api_positions():
        snap = ctx.position_monitor.latest
        if not snap or not snap.positions:
            return "<p class='muted'>No open positions</p>"
        return render_template("_positions.html", positions=snap.positions)

    @app.route("/api/orders")
    @login_required
    def api_orders():
        om = ctx.lifecycle_manager._order_manager
        engine = ctx.lifecycle_manager

        # Collect live (non-terminal) orders, sorted newest first
        live_orders = sorted(
            [r for r in om._orders.values() if r.is_live],
            key=lambda r: r.placed_at,
            reverse=True,
        )

        # Recent terminal orders (last 10, most recent first)
        recent_terminal = sorted(
            [r for r in om._orders.values() if r.is_terminal],
            key=lambda r: (r.terminal_at or r.placed_at),
            reverse=True,
        )[:10]

        recon_warnings = engine.last_reconciliation_warnings
        recon_time = engine.last_reconciliation_time

        return render_template(
            "_orders.html",
            live_orders=live_orders,
            recent_terminal=recent_terminal,
            recon_warnings=recon_warnings,
            recon_time=recon_time,
            now=time.time(),
        )

    @app.route("/api/logs")
    @login_required
    def api_logs():
        lines = _get_log_lines(80)
        return render_template("_logs.html", lines=lines)

    # ── Control Endpoints ────────────────────────────────────────────────

    def _find_runner(name: str):
        for r in runners:
            if r.config.name == name:
                return r
        return None

    @app.route("/api/strategy/<name>/pause", methods=["POST"])
    @login_required
    def strategy_pause(name: str):
        r = _find_runner(name)
        if r:
            r.disable()
            logger.info(f"[Dashboard] Strategy '{name}' paused by user")
        return api_strategies()

    @app.route("/api/strategy/<name>/resume", methods=["POST"])
    @login_required
    def strategy_resume(name: str):
        r = _find_runner(name)
        if r:
            r.enable()
            logger.info(f"[Dashboard] Strategy '{name}' resumed by user")
        return api_strategies()

    @app.route("/api/strategy/<name>/stop", methods=["POST"])
    @login_required
    def strategy_stop(name: str):
        r = _find_runner(name)
        if r:
            r.stop()
            logger.info(f"[Dashboard] Strategy '{name}' stopped by user")
        return api_strategies()

    # ── Kill switch (two-phase mark-price close) ────────────────────────

    closer = PositionCloser(
        account_manager=ctx.account_manager,
        executor=ctx.executor,
        lifecycle_manager=ctx.lifecycle_manager,
    )

    @app.route("/api/killswitch", methods=["POST"])
    @login_required
    def killswitch():
        """Activate kill switch — close all positions via two-phase mark-price."""
        if closer.is_running:
            return (
                f'<span class="kill-result">'
                f'Kill switch already running — {closer.status}'
                f'</span>'
            )

        closer.start(runners)
        logger.warning("[Dashboard] KILL SWITCH activated by user")
        return (
            '<span class="kill-result">'
            'Kill switch activated — closing positions (check Telegram for progress)'
            '</span>'
        )

    @app.route("/api/killswitch/status")
    @login_required
    def killswitch_status():
        """Poll kill switch progress."""
        return f'<span class="kill-status">{closer.status}</span>'

    # ── Control-only endpoints (used by hub dashboard) ───────────────

    def _get_account_id() -> str:
        """Universal account identifier — works for any exchange."""
        from config import EXCHANGE
        if EXCHANGE == 'coincall':
            from config import API_KEY
            return f"cc-{API_KEY[:8]}" if API_KEY else "cc-unknown"
        elif EXCHANGE == 'deribit':
            from config import DERIBIT_CLIENT_ID
            return f"db-{DERIBIT_CLIENT_ID[:8]}" if DERIBIT_CLIENT_ID else "db-unknown"
        else:
            return f"{EXCHANGE[:4]}-unknown"

    @app.route("/control/status")
    @localhost_only
    def control_status():
        """Lightweight JSON status — localhost only."""
        from config import SLOT_NAME, EXCHANGE, ENVIRONMENT
        snap = ctx.position_monitor.latest
        strategy_data = []
        for r in runners:
            st = r.stats
            strategy_data.append({
                "name": r.config.name,
                "enabled": r._enabled,
                "active_trades": len(r.active_trades),
                "max_trades": r.config.max_concurrent_trades,
                "stats": {
                    "total": st.get("total", 0),
                    "wins": st.get("wins", 0),
                    "losses": st.get("losses", 0),
                    "total_pnl": st.get("total_pnl", 0.0),
                    "today_trades": st.get("today_trades", 0),
                    "today_pnl": st.get("today_pnl", 0.0),
                },
            })

        # Positions
        positions_data = []
        if snap and snap.positions:
            for p in snap.positions:
                positions_data.append({
                    "symbol": p.symbol,
                    "side": p.side,
                    "qty": p.qty,
                    "entry_price": p.entry_price,
                    "mark_price": p.mark_price,
                    "unrealized_pnl": p.unrealized_pnl,
                    "delta": p.delta,
                    "theta": p.theta,
                })

        # Open orders
        orders_data = []
        om = ctx.lifecycle_manager.order_manager
        for rec in om._orders.values():
            if rec.is_live:
                orders_data.append({
                    "order_id": rec.order_id,
                    "symbol": rec.symbol,
                    "side": rec.side,
                    "qty": rec.qty,
                    "price": rec.price,
                    "filled_qty": rec.filled_qty,
                    "status": rec.status.value if hasattr(rec.status, 'value') else str(rec.status),
                    "placed_at": rec.placed_at,
                })

        # Health
        uptime = time.time() - _dashboard_start_time if _dashboard_start_time else 0
        snap_age = (time.time() - snap.timestamp) if snap else None
        health_data = {
            "uptime_secs": int(uptime),
            "last_snapshot_age_secs": round(snap_age, 1) if snap_age is not None else None,
            "margin_warning": (snap.margin_utilization > 80) if snap else False,
            "low_equity_warning": (snap.equity < 100) if snap else False,
        }

        # Logs (last 50 lines)
        log_lines = _get_log_lines(50)

        result = {
            "slot_name": SLOT_NAME,
            "exchange": EXCHANGE,
            "environment": ENVIRONMENT,
            "account_id": _get_account_id(),
            "strategies": strategy_data,
            "positions": positions_data,
            "open_orders": orders_data,
            "health": health_data,
            "logs": log_lines,
            "account": None,
        }
        if snap:
            result["account"] = {
                "equity": snap.equity,
                "available_margin": snap.available_margin,
                "margin_utilization": snap.margin_utilization,
                "unrealized_pnl": snap.unrealized_pnl,
                "net_delta": snap.net_delta,
                "net_theta": snap.net_theta,
                "position_count": snap.position_count,
                "timestamp": snap.timestamp,
            }
        return jsonify(result)

    @app.route("/control/pause", methods=["POST"])
    @localhost_only
    def control_pause():
        """Pause all strategies — localhost only."""
        for r in runners:
            r.disable()
        logger.info("[Control] All strategies paused by hub")
        return jsonify({"ok": True, "action": "paused"})

    @app.route("/control/resume", methods=["POST"])
    @localhost_only
    def control_resume():
        """Resume all strategies — localhost only."""
        for r in runners:
            r.enable()
        logger.info("[Control] All strategies resumed by hub")
        return jsonify({"ok": True, "action": "resumed"})

    @app.route("/control/stop", methods=["POST"])
    @localhost_only
    def control_stop():
        """Stop all strategies — localhost only."""
        for r in runners:
            r.stop()
        logger.info("[Control] All strategies stopped by hub")
        return jsonify({"ok": True, "action": "stopped"})

    @app.route("/control/kill", methods=["POST"])
    @localhost_only
    def control_kill():
        """Kill switch — close all positions — localhost only."""
        if closer.is_running:
            return jsonify({"ok": False, "reason": "already_running", "status": closer.status})
        closer.start(runners)
        logger.warning("[Control] KILL SWITCH activated by hub")
        return jsonify({"ok": True, "action": "kill_switch_activated"})

    return app


# =============================================================================
# Startup
# =============================================================================

def start_dashboard(
    ctx: "TradingContext",
    runners: List["StrategyRunner"],
    host: str = "0.0.0.0",
    port: int = 8080,
) -> None:
    """
    Launch the dashboard on a daemon thread.

    Supports three modes (set DASHBOARD_MODE in .env):
      - 'full'    : Normal dashboard with UI (default) — requires DASHBOARD_PASSWORD
      - 'control' : Headless — only /control/* endpoints, bound to 127.0.0.1
      - 'disabled': No dashboard at all
    """
    global _log_handler

    from config import DASHBOARD_MODE

    if DASHBOARD_MODE == "disabled":
        logger.info("Dashboard disabled via DASHBOARD_MODE=disabled")
        return

    port = int(os.getenv("DASHBOARD_PORT", str(port)))

    _dashboard_start_time = time.time()

    if DASHBOARD_MODE == "control":
        # Control-only mode: bind to localhost, no password required.
        # Still attach log handler so hub can read logs via /control/status.
        _log_handler = DashboardLogHandler(maxlen=_LOG_TAIL_LINES)
        _log_handler.setFormatter(
            logging.Formatter("%(asctime)s  %(levelname)-7s  %(name)s — %(message)s", datefmt="%H:%M:%S")
        )
        logging.getLogger().addHandler(_log_handler)
        password = "control-mode-unused"
        host = "127.0.0.1"
        logger.info(f"Dashboard control endpoint on http://127.0.0.1:{port}")
    else:
        # Full dashboard mode: requires password
        password = os.getenv("DASHBOARD_PASSWORD", "").strip()
        if not password:
            logger.warning(
                "Dashboard disabled — set DASHBOARD_PASSWORD in .env to enable"
            )
            return

        # Attach in-memory log handler to root logger (full mode only)
        _log_handler = DashboardLogHandler(maxlen=_LOG_TAIL_LINES)
        _log_handler.setFormatter(
            logging.Formatter("%(asctime)s  %(levelname)-7s  %(name)s — %(message)s", datefmt="%H:%M:%S")
        )
        logging.getLogger().addHandler(_log_handler)

    app = _create_app(ctx, runners, password)

    def _run():
        # Suppress Flask/Werkzeug startup banner noise
        wlog = logging.getLogger("werkzeug")
        wlog.setLevel(logging.WARNING)
        app.run(host=host, port=port, debug=False, use_reloader=False)

    thread = threading.Thread(target=_run, name="Dashboard", daemon=True)
    thread.start()
    logger.info(f"Dashboard started on http://{host}:{port} (mode={DASHBOARD_MODE})")
