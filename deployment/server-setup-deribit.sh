#!/usr/bin/env bash
# ===========================================================================
# CoincallTrader (Deribit) — Server Setup
#
# Sets up the DERIBIT instance alongside the existing Coincall deployment.
# Only creates the app directory, venv, service, and opens the dashboard port.
# System packages are assumed to already be installed.
#
# Run via: ./deployment/deploy-deribit.sh --setup
# ===========================================================================
set -euo pipefail

APP_DIR="/opt/coincalltrader-deribit"
SERVICE_NAME="coincalltrader-deribit"
DASHBOARD_PORT=8081

echo "═══════════════════════════════════════════════════════"
echo " CoincallTrader (Deribit) — Server Setup"
echo "═══════════════════════════════════════════════════════"

# ── 1) Create application directory ────────────────────────────────────
echo ""
echo "▸ Setting up application directory..."
mkdir -p "$APP_DIR/logs"
echo "  ✓ $APP_DIR created"

# ── 2) Create Python virtual environment ───────────────────────────────
echo ""
echo "▸ Creating Python virtual environment..."
if [ ! -d "$APP_DIR/.venv" ]; then
    python3 -m venv "$APP_DIR/.venv"
    echo "  ✓ Virtual environment created"
else
    echo "  ✓ Virtual environment already exists"
fi

"$APP_DIR/.venv/bin/pip" install --upgrade pip -q
echo "  ✓ pip upgraded"

# ── 3) Install systemd service ─────────────────────────────────────────
echo ""
echo "▸ Installing systemd service..."

if [ ! -f "/etc/systemd/system/${SERVICE_NAME}.service" ]; then
    cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<UNIT
[Unit]
Description=CoincallTrader — Deribit Options Trading Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python main.py
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=$SERVICE_NAME
LimitNOFILE=65535
MemoryMax=1G
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$APP_DIR/logs
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT
    echo "  ✓ Service file installed"
else
    echo "  ✓ Service file already exists"
fi

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" 2>/dev/null || true
echo "  ✓ Service enabled (will start on boot)"

# ── 4) Open dashboard port in firewall ─────────────────────────────────
echo ""
echo "▸ Opening firewall port $DASHBOARD_PORT..."
ufw allow "$DASHBOARD_PORT/tcp" >/dev/null 2>&1
echo "  ✓ Port $DASHBOARD_PORT open"

# ── 5) Summary ──────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════"
echo " Deribit instance setup complete!"
echo ""
echo "   App dir:    $APP_DIR"
echo "   Venv pip:   $($APP_DIR/.venv/bin/pip --version 2>&1 | awk '{print $2}')"
echo "   Service:    $SERVICE_NAME (enabled, not yet started)"
echo "   Dashboard:  port $DASHBOARD_PORT"
echo ""
echo " Next: deploy with ./deployment/deploy-deribit.sh"
echo "═══════════════════════════════════════════════════════"
