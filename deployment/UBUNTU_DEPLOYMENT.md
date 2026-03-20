# CoincallTrader — Ubuntu Deployment Guide

## Philosophy: Single Source of Truth

Everything lives on your dev machine — code, `.env`, strategy config, API keys.
The deploy script rsyncs it all to the server in one step.  The only server-side
patch is `DEPLOYMENT_TARGET`, which is automatically set to `production` after
each sync.

No separate `.env` management.  No server-side config files to maintain.
Change something locally, deploy, done.

---

## Architecture

```
┌─────────────────────┐          rsync + SSH          ┌──────────────────────┐
│   Dev Machine (Mac)  │  ─────────────────────────▶  │   VPS (Ubuntu 24.04) │
│                      │                               │                      │
│  VS Code + .venv     │     ./deploy.sh               │  /opt/coincalltrader  │
│  .env (all keys)     │     stop → sync → patch →     │  systemd service     │
│  Strategy config     │     deps → start              │  journalctl logs     │
└─────────────────────┘                               └──────────────────────┘
```

**No git on the server.** Code is synced directly via rsync over SSH.

---

## Quick Start (2 commands)

```bash
# 1. Prepare the VPS (one-time only)
./deployment/deploy.sh --setup

# 2. Deploy & start
./deployment/deploy.sh
```

That's it.  `.env` is included in the sync and patched automatically.

---

## What Happens During a Deploy

1. **Check connectivity** — verify SSH to VPS works
2. **Stop service** — graceful systemd stop (skipped for `--dry-run`)
3. **Rsync everything** — code, `.env`, requirements, templates, strategies
4. **Patch `.env`** — `DEPLOYMENT_TARGET=production` via `sed` on server
5. **Install deps** — `pip install -r requirements.txt` in server venv
6. **Update systemd** — copy service file, reload daemon
7. **Start service** — start + verify it's running
8. **Show logs** — last 20 lines for quick verification

---

## Files

| File | Purpose |
|---|---|
| `deployment/deploy.sh` | Main deploy script — run from your Mac |
| `deployment/server-setup.sh` | One-time VPS setup (Python, venv, systemd, firewall) |
| `deployment/coincalltrader.service` | systemd unit file (installed automatically) |
| `deployment/rsync-exclude.txt` | Files/dirs excluded from sync |
| `.deploy.env` | Your VPS connection settings (gitignored, dev machine only) |

---

## .deploy.env Configuration

Create `.deploy.env` in the project root:

```bash
VPS_HOST=root@46.225.137.92
VPS_APP_DIR=/opt/coincalltrader       # default
VPS_SERVICE=coincalltrader            # default
SSH_KEY=                              # optional, uses default SSH key
```

This file is gitignored and stays on your dev machine only.

---

## Deploy Script Commands

| Command | What it does |
|---|---|
| `./deployment/deploy.sh` | **Full deploy**: stop → sync → patch → deps → start |
| `./deployment/deploy.sh --dry-run` | Preview what would be synced (no changes) |
| `./deployment/deploy.sh --setup` | One-time server setup |
| `./deployment/deploy.sh --stop` | Stop the service |
| `./deployment/deploy.sh --start` | Start the service |
| `./deployment/deploy.sh --restart` | Restart the service |
| `./deployment/deploy.sh --clean` | **Clean restart**: delete all logs/snapshots, start fresh |
| `./deployment/deploy.sh --status` | Show service status + uptime |
| `./deployment/deploy.sh --logs` | Tail live logs (Ctrl+C to stop) |
| `./deployment/deploy.sh --health` | Quick health check (disk, memory, uptime, service) |
| `./deployment/deploy.sh --update` | Update OS packages on the VPS |
| `./deployment/deploy.sh --reboot` | Reboot VPS, wait for it, verify service |
| `./deployment/deploy.sh --ssh` | Open SSH session to VPS |

---

## What Gets Synced

rsync transfers everything except items in `rsync-exclude.txt`:

**Synced** (single source of truth from dev machine):
- All Python code (strategies, modules, `main.py`)
- `.env` (API keys, config — auto-patched for production)
- `requirements.txt`, `templates/`

**Excluded** (see `rsync-exclude.txt`):
- `.venv/` — the VPS has its own venv
- `.deploy.env` — SSH settings, dev machine only
- `logs/` — preserved on the VPS across deploys
- `archive/`, `analysis/`, `docs/`, `tests/` — dev only
- `deployment/` — service file is copied explicitly
- `.git/`, `__pycache__/`, IDE files

---

## Environment Configuration

Your `.env` has both dev and prod settings.  The deploy script handles the
one difference:

| Setting | Dev Machine | Production Server |
|---|---|---|
| `DEPLOYMENT_TARGET` | `development` | `production` (auto-patched by deploy) |
| `TRADING_ENVIRONMENT` | Same | Same (synced from dev) |
| API keys | Same | Same (synced from dev) |

To change API keys, trading environment, or any config: edit `.env` locally,
then run `./deployment/deploy.sh`.

---

## systemd Service

The bot runs as a systemd service called `coincalltrader`.

```bash
# From your Mac via deploy.sh:
./deployment/deploy.sh --status
./deployment/deploy.sh --logs
./deployment/deploy.sh --stop

# Or directly on the VPS:
sudo systemctl status coincalltrader
sudo journalctl -u coincalltrader -f
```

### Crash recovery

- **Crash restart**: systemd auto-restarts on failure after 10 seconds
- **Boot persistence**: service is enabled, starts automatically on reboot
- No cron jobs needed — systemd handles everything

### Logs

All stdout/stderr goes to journald:

```bash
sudo journalctl -u coincalltrader -n 100 --no-pager   # last 100 lines
sudo journalctl -u coincalltrader -b                    # since last boot
sudo journalctl -u coincalltrader --since "1 hour ago"  # time-based
```

---

## Clean Restart

When the application has stale state (corrupted snapshots, leftover orders),
wipe everything and start fresh:

```bash
./deployment/deploy.sh --clean
```

This deletes all state files in `logs/` on the VPS, then restarts the service.

---

## Typical Daily Workflow

1. Edit code / config on your Mac
2. `./deployment/deploy.sh` — deploys in ~5 seconds
3. `./deployment/deploy.sh --logs` — watch it run

---

## Server Details

| Property | Value |
|---|---|
| Provider | Hetzner |
| Plan | CPX22 (2 vCPU, 4 GB RAM, 80 GB SSD) |
| Location | Nuremberg, Germany |
| OS | Ubuntu 24.04 LTS |
| IP | 46.225.137.92 |
| App directory | /opt/coincalltrader |
| Dashboard | http://46.225.137.92:8080 |
| Firewall | UFW — SSH (22) + Dashboard (8080) |

---

## Troubleshooting

**Can't connect to VPS:**
```bash
ssh -v root@46.225.137.92   # verbose SSH for debugging
```

**Service won't start:**
```bash
./deployment/deploy.sh --logs   # check error output
./deployment/deploy.sh --ssh    # SSH in and inspect manually
```

**Stale state blocking startup:**
```bash
./deployment/deploy.sh --clean  # wipe logs/snapshots, fresh start
```

**Need to start fresh (clean logs + state):**
```bash
./deployment/deploy.sh --ssh
# Then on the VPS:
sudo systemctl stop coincalltrader
rm -f /opt/coincalltrader/logs/*
sudo systemctl start coincalltrader
```

---

## Multi-Instance Deployment (Coincall + Deribit)

The same VPS can run multiple trading bot instances side by side — one per
exchange.  Each instance has its own directory, systemd service, and dashboard
port.

### Architecture

```
┌─────────────────────┐     deploy.sh      ┌────────────────────────────────┐
│   Dev Machine (Mac)  │  ───────────────▶  │  /opt/coincalltrader           │
│                      │                    │  service: coincalltrader       │
│  .deploy.env         │                    │  dashboard: :8080              │
│  .deploy.deribit.env │                    └────────────────────────────────┘
│                      │  deploy-deribit.sh ┌────────────────────────────────┐
│                      │  ───────────────▶  │  /opt/coincalltrader-deribit   │
│                      │                    │  service: coincalltrader-deribit│
│                      │                    │  dashboard: :8081              │
│                      │                    └────────────────────────────────┘
```

### How It Works

The deploy system is fully parameterized via `.deploy.env` files:

| Instance | Config File | App Directory | Service Name | Dashboard |
|----------|------------|---------------|--------------|-----------|
| Coincall | `.deploy.env` | `/opt/coincalltrader` | `coincalltrader` | `:8080` |
| Deribit | `.deploy.deribit.env` | `/opt/coincalltrader-deribit` | `coincalltrader-deribit` | `:8081` |

The Deribit dashboard port is set via `Environment=DASHBOARD_PORT=8081` in the
systemd service file, not in `.env` (since `.env` is shared code and would
otherwise conflict).

### Deploying the Deribit Instance

```bash
# One-time server setup (creates /opt/coincalltrader-deribit, venv, opens port 8081)
bash deployment/deploy-deribit.sh --setup

# Full deploy
bash deployment/deploy-deribit.sh

# Monitor
bash deployment/deploy-deribit.sh --logs
bash deployment/deploy-deribit.sh --status
bash deployment/deploy-deribit.sh --stop
```

### Files (Deribit-Specific)

| File | Purpose |
|---|---|
| `.deploy.deribit.env` | VPS connection settings for Deribit instance (gitignored) |
| `deployment/deploy-deribit.sh` | Wrapper that delegates to `deploy.sh` with Deribit config |
| `deployment/server-setup-deribit.sh` | One-time VPS setup for Deribit (dir, venv, port 8081) |
| `deployment/coincalltrader-deribit.service` | systemd unit for the Deribit instance |

### Adding More Instances

To add a third exchange, follow the same pattern:

1. Create `.deploy.<exchange>.env` with a new `VPS_APP_DIR` and `VPS_SERVICE`
2. Create `deployment/deploy-<exchange>.sh` (copy `deploy-deribit.sh`, change env path)
3. Create `deployment/coincalltrader-<exchange>.service` (update paths, set unique `DASHBOARD_PORT`)
4. Create `deployment/server-setup-<exchange>.sh` (update paths and port)
5. Deploy: `bash deployment/deploy-<exchange>.sh --setup && bash deployment/deploy-<exchange>.sh`

### Managing Both Instances

```bash
# Check both services at once (on the VPS):
sudo systemctl status coincalltrader coincalltrader-deribit

# Or via deploy scripts from dev machine:
bash deployment/deploy.sh --status           # Coincall
bash deployment/deploy-deribit.sh --status   # Deribit
```
