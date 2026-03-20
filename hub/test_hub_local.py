#!/usr/bin/env python3
"""
Local test harness for the Hub Dashboard.

Creates fake slot directories with sample data and optionally runs
mock control endpoints to simulate live slots.

Usage:
    python hub/test_hub_local.py

Then open http://localhost:8080 in your browser.
Password: test

Press Ctrl+C to stop and clean up.
"""

import json
import os
import shutil
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify

# ── Setup fake slot directories ──────────────────────────────────────────

TEST_BASE = Path("/tmp/ct")
SLOTS = {
    "01": {
        "name": "ATM Straddle 10UTC",
        "exchange": "deribit",
        "environment": "production",
        "port": 9091,
    },
    "02": {
        "name": "Daily Put Sell",
        "exchange": "coincall",
        "environment": "testnet",
        "port": 9092,
    },
    "03": {
        "name": "Blueprint Strangle",
        "exchange": "deribit",
        "environment": "production",
        "port": 9093,
    },
}


def create_fake_slots():
    """Create fake slot directories with sample data."""
    # Clean previous test
    if TEST_BASE.exists():
        shutil.rmtree(TEST_BASE)

    for slot_id, info in SLOTS.items():
        slot_dir = TEST_BASE / f"slot-{slot_id}"
        logs_dir = slot_dir / "logs"
        logs_dir.mkdir(parents=True)

        # Write .env
        env_content = (
            f"SLOT_NAME={info['name']}\n"
            f"EXCHANGE={info['exchange']}\n"
            f"TRADING_ENVIRONMENT={info['environment']}\n"
            f"DASHBOARD_MODE=control\n"
            f"DASHBOARD_PORT={info['port']}\n"
        )
        (slot_dir / ".env").write_text(env_content)

        # Write trades_snapshot.json (simulated active trades)
        if slot_id == "01":
            trades = {
                "trades": [
                    {
                        "id": "straddle-abc123",
                        "strategy_id": "ATM Straddle 10UTC",
                        "state": "OPEN",
                        "legs": [
                            {"symbol": "BTC-28MAR26-90000-C", "side": "sell", "qty": 0.1},
                            {"symbol": "BTC-28MAR26-90000-P", "side": "sell", "qty": 0.1},
                        ],
                    }
                ]
            }
        elif slot_id == "02":
            trades = {"trades": []}
        else:
            trades = {
                "trades": [
                    {
                        "id": "strangle-def456",
                        "strategy_id": "Blueprint Strangle",
                        "state": "OPEN",
                        "legs": [
                            {"symbol": "BTC-28MAR26-95000-C", "side": "sell", "qty": 0.05},
                            {"symbol": "BTC-28MAR26-80000-P", "side": "sell", "qty": 0.05},
                        ],
                    }
                ]
            }
        (logs_dir / "trades_snapshot.json").write_text(json.dumps(trades, indent=2))

        # Write trade_history.jsonl (some completed trades)
        history = []
        if slot_id in ("01", "03"):
            history = [
                {
                    "strategy_id": info["name"],
                    "pnl": 0.0042,
                    "entry_cost": 0.0185,
                    "hold_minutes": 127,
                    "closed_at": "2026-03-19T14:32:00Z",
                },
                {
                    "strategy_id": info["name"],
                    "pnl": -0.0018,
                    "entry_cost": 0.0210,
                    "hold_minutes": 95,
                    "closed_at": "2026-03-18T11:15:00Z",
                },
                {
                    "strategy_id": info["name"],
                    "pnl": 0.0091,
                    "entry_cost": 0.0173,
                    "hold_minutes": 240,
                    "closed_at": "2026-03-17T16:48:00Z",
                },
            ]
        with open(logs_dir / "trade_history.jsonl", "w") as f:
            for entry in history:
                f.write(json.dumps(entry) + "\n")

    print(f"[Test] Created fake slots in {TEST_BASE}")


# ── Mock control endpoints ───────────────────────────────────────────────

def create_mock_slot_app(slot_id: str, info: dict) -> Flask:
    """Create a tiny Flask app pretending to be a slot's control endpoint."""
    app = Flask(f"mock-slot-{slot_id}")
    _start_time = time.time()

    @app.route("/control/status")
    def status():
        uptime = time.time() - _start_time

        # Mock positions
        positions = []
        if slot_id == "01":
            positions = [
                {"symbol": "BTC-28MAR26-90000-C", "side": "short", "qty": 0.1,
                 "entry_price": 1250.00, "mark_price": 1180.50, "unrealized_pnl": 6.95,
                 "delta": -0.0123, "theta": 0.0045},
                {"symbol": "BTC-28MAR26-90000-P", "side": "short", "qty": 0.1,
                 "entry_price": 980.00, "mark_price": 1015.30, "unrealized_pnl": -3.53,
                 "delta": 0.0098, "theta": 0.0044},
            ]
        elif slot_id == "03":
            positions = [
                {"symbol": "BTC-28MAR26-95000-C", "side": "short", "qty": 0.05,
                 "entry_price": 420.00, "mark_price": 395.20, "unrealized_pnl": 1.24,
                 "delta": -0.0052, "theta": 0.0018},
                {"symbol": "BTC-28MAR26-80000-P", "side": "short", "qty": 0.05,
                 "entry_price": 310.00, "mark_price": 330.80, "unrealized_pnl": -1.04,
                 "delta": 0.0031, "theta": 0.0015},
            ]

        # Mock open orders
        open_orders = []
        if slot_id == "01":
            open_orders = [
                {"order_id": "ord-abc123", "symbol": "BTC-28MAR26-90000-C",
                 "side": "buy", "qty": 0.1, "price": 1150.00, "filled_qty": 0.0,
                 "status": "LIVE", "placed_at": time.time() - 180},
            ]

        # Mock logs
        log_lines = [
            f"10:00:01  INFO     strategy — [{info['name']}] Scanning for entry...",
            f"10:00:02  INFO     market_data — BTC index: $87,234.50",
            f"10:00:05  INFO     account — Snapshot: equity=$" + (
                "15234.50" if slot_id == "01"
                else "8920.75" if slot_id == "02"
                else "22100.00"),
            f"10:00:10  INFO     ema_filter — EMA spread: 0.12% (threshold: 0.20%)",
            f"10:00:15  DEBUG    health — Margin util: " + (
                "20.6%" if slot_id == "01"
                else "0.0%" if slot_id == "02"
                else "16.3%"),
            f"10:05:01  INFO     strategy — [{info['name']}] Cycle complete, sleeping 300s",
        ]

        return jsonify({
            "slot_name": info["name"],
            "exchange": info["exchange"],
            "environment": info["environment"],
            "account_id": "db-a1b2c3d4" if info["exchange"] == "deribit" else "cc-e5f6g7h8",
            "strategies": [
                {
                    "name": info["name"],
                    "enabled": slot_id != "02",
                    "active_trades": 1 if slot_id != "02" else 0,
                    "max_trades": 3,
                    "stats": {
                        "total": 12 if slot_id == "01" else 5,
                        "wins": 8 if slot_id == "01" else 3,
                        "losses": 4 if slot_id == "01" else 2,
                        "total_pnl": 0.0342 if slot_id == "01" else -0.0021,
                        "today_trades": 1 if slot_id == "01" else 0,
                        "today_pnl": 0.0042 if slot_id == "01" else 0.0,
                    },
                }
            ],
            "positions": positions,
            "open_orders": open_orders,
            "health": {
                "uptime_secs": int(uptime),
                "last_snapshot_age_secs": 3.2 if slot_id != "02" else 12.5,
                "margin_warning": False,
                "low_equity_warning": False,
            },
            "logs": log_lines,
            "account": {
                "equity": 15234.50 if slot_id == "01" else (8920.75 if slot_id == "02" else 22100.00),
                "available_margin": 12100.00 if slot_id == "01" else (8920.75 if slot_id == "02" else 18500.00),
                "margin_utilization": 20.6 if slot_id == "01" else (0.0 if slot_id == "02" else 16.3),
                "unrealized_pnl": 42.30 if slot_id == "01" else (0.0 if slot_id == "02" else -18.50),
                "net_delta": -0.0234 if slot_id == "01" else (0.0 if slot_id == "02" else 0.0512),
                "net_theta": 0.0089 if slot_id == "01" else (0.0 if slot_id == "02" else 0.0045),
                "position_count": 2 if slot_id == "01" else (0 if slot_id == "02" else 2),
                "timestamp": time.time(),
            },
        })

    @app.route("/control/pause", methods=["POST"])
    def pause():
        print(f"  [Slot {slot_id}] Received PAUSE command")
        return jsonify({"ok": True, "action": "paused"})

    @app.route("/control/resume", methods=["POST"])
    def resume():
        print(f"  [Slot {slot_id}] Received RESUME command")
        return jsonify({"ok": True, "action": "resumed"})

    @app.route("/control/stop", methods=["POST"])
    def stop():
        print(f"  [Slot {slot_id}] Received STOP command")
        return jsonify({"ok": True, "action": "stopped"})

    @app.route("/control/kill", methods=["POST"])
    def kill():
        print(f"  [Slot {slot_id}] Received KILL command")
        return jsonify({"ok": True, "action": "kill_switch_activated"})

    return app


def run_mock_slots():
    """Start mock control endpoints for each slot on background threads."""
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    for slot_id, info in SLOTS.items():
        app = create_mock_slot_app(slot_id, info)
        port = info["port"]
        t = threading.Thread(
            target=lambda a, p: a.run(host="127.0.0.1", port=p, debug=False, use_reloader=False),
            args=(app, port),
            daemon=True,
            name=f"mock-slot-{slot_id}",
        )
        t.start()
        print(f"[Test] Mock slot-{slot_id} ({info['name']}) on http://127.0.0.1:{port}")


# ── Main ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # 1) Create fake directories
    create_fake_slots()

    # 2) Start mock slot control endpoints
    run_mock_slots()
    time.sleep(0.5)

    # 3) Configure and start the hub
    os.environ["HUB_PASSWORD"] = "test"
    os.environ["HUB_PORT"] = "9090"
    os.environ["HUB_SLOTS_BASE"] = str(TEST_BASE)

    # Add hub directory to path so we can import
    hub_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, hub_dir)

    # Import AFTER setting env vars
    from hub_dashboard import app

    print("")
    print("═" * 50)
    print("  Hub Dashboard: http://localhost:9090")
    print("  Password: test")
    print("═" * 50)
    print("  Press Ctrl+C to stop")
    print("")

    import logging
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    try:
        app.run(host="0.0.0.0", port=9090, debug=False)
    except KeyboardInterrupt:
        print("\n[Test] Shutting down...")
        shutil.rmtree(TEST_BASE, ignore_errors=True)
        print("[Test] Cleaned up /tmp/ct")
