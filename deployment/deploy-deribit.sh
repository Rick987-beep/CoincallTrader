#!/usr/bin/env bash
# ===========================================================================
# Deploy wrapper for the DERIBIT instance
#
# Points the main deploy.sh at .deploy.deribit.env so it deploys to
# /opt/coincalltrader-deribit with service name coincalltrader-deribit.
#
# Usage:
#   ./deployment/deploy-deribit.sh              Full deploy
#   ./deployment/deploy-deribit.sh --dry-run    Preview rsync
#   ./deployment/deploy-deribit.sh --setup      First-time server setup
#   ./deployment/deploy-deribit.sh --status     Check service status
#   ./deployment/deploy-deribit.sh --logs       Tail logs
#   ./deployment/deploy-deribit.sh [any flag]   Passes through to deploy.sh
# ===========================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Override the deploy env file to point at the Deribit target
export DEPLOY_ENV="$PROJECT_ROOT/.deploy.deribit.env"

# Delegate to the main deploy script
exec "$SCRIPT_DIR/deploy.sh" "$@"
