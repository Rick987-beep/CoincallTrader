#!/usr/bin/env bash
# ===========================================================================
# ssh-server.sh — Open an SSH session to a named server from servers.toml
#
# Usage:
#   ./deployment/ssh-server.sh trading-prod
#   ./deployment/ssh-server.sh bulk-download
#   ./deployment/ssh-server.sh              (list available servers)
# ===========================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVERS_TOML="${PROJECT_ROOT}/servers.toml"

if [[ ! -f "$SERVERS_TOML" ]]; then
    echo "Error: servers.toml not found at $SERVERS_TOML"
    exit 1
fi

SERVER="${1:-}"

# No argument — list available servers
if [[ -z "$SERVER" ]]; then
    echo "Available servers (from servers.toml):"
    echo ""
    python3 - <<PYEOF
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore
with open("${SERVERS_TOML}", "rb") as f:
    d = tomllib.load(f)
for k, v in d.items():
    print(f"  {k:<20}  {v['user']}@{v['ip']}  —  {v['name']}")
PYEOF
    echo ""
    echo "Usage: ./deployment/ssh-server.sh <server-name>"
    exit 0
fi

# Parse connection details for the requested server
CONNECT=$(python3 - <<PYEOF
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore
with open("${SERVERS_TOML}", "rb") as f:
    d = tomllib.load(f)
if "${SERVER}" not in d:
    import sys
    available = ", ".join(d.keys())
    print(f"ERROR: server '{${SERVER}}' not found in servers.toml. Available: {available}", file=sys.stderr)
    sys.exit(1)
s = d["${SERVER}"]
print(f"{s['user']}@{s['ip']}")
PYEOF
) || exit 1

# Resolve SSH key: prefer .env, fall back to .deploy.slots.env (legacy)
SSH_KEY=$(grep -E '^SSH_KEY=' "${PROJECT_ROOT}/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'") || true
if [[ -z "$SSH_KEY" && -f "${PROJECT_ROOT}/.deploy.slots.env" ]]; then
    SSH_KEY=$(grep -E '^SSH_KEY=' "${PROJECT_ROOT}/.deploy.slots.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'") || true
fi

SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
if [[ -n "${SSH_KEY:-}" ]]; then
    SSH_OPTS="$SSH_OPTS -i $SSH_KEY"
fi

echo "→ $SERVER  ($CONNECT)"
# shellcheck disable=SC2086
exec ssh $SSH_OPTS "$CONNECT"
