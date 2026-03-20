#!/usr/bin/env bash
# ===========================================================================
# CoincallTrader — One-Time Server Setup for Slot Architecture
#
# Run once on a fresh Hetzner VPS to prepare /opt/ct/ for slot deployments.
# Executed remotely via: ./deployment/deploy-slot.sh hub --setup
# (or manually: ssh root@VPS_IP < deployment/server-setup-slots.sh)
#
# What it does:
#   1. Updates system packages
#   2. Installs Python 3, pip, venv, rsync
#   3. Creates /opt/ct/ base directory
#   4. Configures UFW firewall (SSH + port 8080 for hub)
#
# Slot-specific setup (venv, dirs) is handled per-slot by deploy-slot.sh --setup.
# Safe to re-run — all steps are idempotent.
# ===========================================================================
set -euo pipefail

CT_BASE="/opt/ct"
HUB_PORT=8080

echo "═══════════════════════════════════════════════════════"
echo " CoincallTrader — Slot Architecture Server Setup"
echo "═══════════════════════════════════════════════════════"

# ── 1) System update ────────────────────────────────────────────────────
echo ""
echo "▸ Updating system packages..."
apt-get update -qq
apt-get upgrade -y -qq
echo "  ✓ System up to date"

# ── 2) Install Python & essentials ─────────────────────────────────────
echo ""
echo "▸ Installing Python and build tools..."
apt-get install -y -qq python3 python3-pip python3-venv rsync
echo "  ✓ Python $(python3 --version 2>&1 | awk '{print $2}') ready"

# ── 3) Create base directory ───────────────────────────────────────────
echo ""
echo "▸ Setting up base directory..."
mkdir -p "$CT_BASE"
echo "  ✓ $CT_BASE created"

# ── 4) Configure firewall ──────────────────────────────────────────────
echo ""
echo "▸ Configuring firewall (ufw)..."
apt-get install -y -qq ufw
ufw allow OpenSSH >/dev/null 2>&1
ufw allow "$HUB_PORT/tcp" >/dev/null 2>&1
if ! ufw status | grep -q "Status: active"; then
    echo "y" | ufw enable >/dev/null 2>&1
fi
echo "  ✓ Firewall active — SSH + port $HUB_PORT open"

# ── 5) Summary ──────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════"
echo " Server setup complete!"
echo ""
echo "   Base dir:   $CT_BASE"
echo "   Python:     $(python3 --version 2>&1)"
echo "   Firewall:   SSH + port $HUB_PORT (hub only)"
echo ""
echo " Next steps:"
echo "   1. Set up hub:    ./deployment/deploy-slot.sh hub --setup"
echo "   2. Set up slots:  ./deployment/deploy-slot.sh 01 --setup"
echo "   3. Deploy:        ./deployment/deploy-slot.sh 01"
echo "═══════════════════════════════════════════════════════"
