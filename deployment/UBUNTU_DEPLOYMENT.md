# CoincallTrader — Ubuntu Deployment Guide

## Philosophy: Single Source of Truth

Everything lives on your dev machine — code, `.env`, strategy config, API keys.
The deploy script rsyncs it all to the server in one step.  The only server-side
patch is `DEPLOYMENT_TARGET`, which is automatically set to `production` after
each sync.

**No git on the server.** Code is synced directly via rsync over SSH.

---

## Architecture

```
┌─────────────────────┐                     ┌──────────────────────────────────┐
│   Dev Machine (Mac)  │  deploy-slot.sh    │   VPS (Ubuntu 24.04)             │
│                      │  ─────────────▶    │                                  │
│  .deploy.slots.env   │                    │   /opt/ct/                       │
│  .env.slot-01        │                    │   ├── slot-01/  (strategy A)     │
│  .env.slot-02        │                    │   ├── slot-02/  (strategy B)     │
│  .env.hub            │                    │   └── hub/      (dashboard)      │
└─────────────────────┘                    └──────────────────────────────────┘
```

Each slot is fully isolated: own `.env`, own venv, own systemd service, own logs.
The hub dashboard auto-discovers slots and aggregates their data.

---

## Quick Start

```bash
# 1. One-time: setup slot + hub on the VPS
./deployment/deploy-slot.sh 01 --setup
./deployment/deploy-slot.sh hub --setup

# 2. Deploy
./deployment/deploy-slot.sh 01
./deployment/deploy-slot.sh hub
```

---

## Configuration Files (Dev Machine, All Gitignored)

| File | Purpose |
|---|---|
| `.deploy.slots.env` | SSH connection (`VPS_HOST`, `SSH_KEY`) |
| `.env.slot-XX` | Per-slot config (exchange, credentials, strategy, port) |
| `.env.hub` | Hub dashboard config (`HUB_PASSWORD`, `HUB_PORT`) |
| `.env` | Local development config (not deployed) |

### .deploy.slots.env

```bash
VPS_HOST=root@46.225.137.92
SSH_KEY=                          # optional, uses default SSH key
```

### .env.slot-XX Template

```bash
SLOT_NAME=My Strategy Name
EXCHANGE=coincall                 # or deribit
TRADING_ENVIRONMENT=production    # or testnet
DEPLOYMENT_TARGET=development     # auto-patched to production on deploy

DASHBOARD_MODE=control            # hub reads this; use 'full' for standalone UI
DASHBOARD_PORT=8091               # unique per slot, localhost only

# Exchange credentials (Coincall)
COINCALL_API_KEY_PROD=...
COINCALL_API_SECRET_PROD=...

# Or Deribit credentials
# DERIBIT_CLIENT_ID_PROD=...
# DERIBIT_CLIENT_SECRET_PROD=...

# Telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

DASHBOARD_PASSWORD=...
```

### .env.hub

```bash
HUB_PASSWORD=...
HUB_PORT=8070
HUB_SLOTS_BASE=/opt/ct
```

---

## Port Layout

| Service | Port | Scope |
|---|---|---|
| Hub dashboard | `HUB_PORT` in `.env.hub` (default 8070) | External (firewall) |
| Slot control endpoints | `DASHBOARD_PORT` in `.env.slot-XX` (8091, 8092, ...) | Localhost only |

---

## Deploy Commands

```bash
# One script, slot number as parameter:
./deployment/deploy-slot.sh 01 --setup    # One-time: create dir, venv, systemd
./deployment/deploy-slot.sh 01            # Deploy: stop → sync → deps → start
./deployment/deploy-slot.sh 01 --logs     # Tail live logs
./deployment/deploy-slot.sh 01 --status   # Service status
./deployment/deploy-slot.sh 01 --restart  # Restart without redeploy
./deployment/deploy-slot.sh 01 --clean    # Wipe logs/state, restart
./deployment/deploy-slot.sh 01 --destroy  # Delete entire slot

# Hub:
./deployment/deploy-slot.sh hub --setup   # One-time: dir, venv, systemd, firewall
./deployment/deploy-slot.sh hub           # Deploy hub code
./deployment/deploy-slot.sh hub --logs    # Tail hub logs
./deployment/deploy-slot.sh hub --status  # Hub service status

# Overview:
./deployment/deploy-slot.sh status        # All slots + hub at a glance
```

---

## What Happens During a Deploy

1. **Check connectivity** — verify SSH to VPS works
2. **Stop service** — graceful systemd stop
3. **Rsync code** — sync Python code, templates, strategies (excludes `.venv`, `.env`, logs)
4. **Copy `.env`** — `.env.slot-XX` → `/opt/ct/slot-XX/.env`
5. **Patch `.env`** — `DEPLOYMENT_TARGET=production` via `sed` on server
6. **Install deps** — `pip install -r requirements.txt` in server venv
7. **Start service** — start + verify it's running
8. **Show logs** — last 15 lines for verification

---

## Deployment Files

| File | Purpose |
|---|---|
| `deployment/deploy-slot.sh` | Single deploy script for all slots + hub |
| `deployment/ct-slot@.service` | systemd template unit (slot-01, slot-02, ...) |
| `deployment/ct-hub.service` | systemd unit for the hub dashboard |
| `deployment/rsync-exclude-slot.txt` | Files excluded from slot sync |
| `deployment/server-setup-slots.sh` | One-time server base setup |
| `deployment/UBUNTU_DEPLOYMENT.md` | This document |

---

## Adding a New Strategy Slot

1. Create `.env.slot-XX` with a unique `DASHBOARD_PORT`
2. `./deployment/deploy-slot.sh XX --setup` (creates dir, venv, systemd)
3. `./deployment/deploy-slot.sh XX` (deploys code + starts)
4. Hub auto-discovers the new slot on next page load

---

## systemd Services

Each slot runs as an instance of the `ct-slot@` template:

```bash
# From dev machine:
./deployment/deploy-slot.sh 01 --status
./deployment/deploy-slot.sh 01 --logs

# Or directly on the VPS:
sudo systemctl status ct-slot@01
sudo journalctl -u ct-slot@01 -f
sudo systemctl status ct-hub
sudo journalctl -u ct-hub -f
```

### Crash recovery

- systemd auto-restarts on failure after 10 seconds
- Services are enabled, start automatically on reboot

### Logs

All stdout/stderr goes to journald:

```bash
sudo journalctl -u ct-slot@01 -n 100 --no-pager   # last 100 lines
sudo journalctl -u ct-slot@01 -b                    # since last boot
sudo journalctl -u ct-slot@01 --since "1 hour ago"  # time-based
```

---

## Server Details

| Property | Value |
|---|---|
| Provider | Hetzner |
| Plan | CPX22 (2 vCPU, 4 GB RAM, 80 GB SSD) |
| Location | Nuremberg, Germany |
| OS | Ubuntu 24.04 LTS |
| IP | 46.225.137.92 |
| Base directory | /opt/ct/ |
| Hub dashboard | http://46.225.137.92:8070 |
| Firewall | UFW — SSH (22) + Hub (8070) |

---

## Troubleshooting

**Can't connect to VPS:**
```bash
ssh -v root@46.225.137.92
```

**Slot won't start:**
```bash
./deployment/deploy-slot.sh 01 --logs
```

**Stale state blocking startup:**
```bash
./deployment/deploy-slot.sh 01 --clean
```

**Check all services at once:**
```bash
./deployment/deploy-slot.sh status
```
