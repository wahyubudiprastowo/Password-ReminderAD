#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONTAINER_NAME="pce-dashboard"
CONFIG_PATH="/app/config/config.json"

# This runner is safe to execute on a frequent schedule.
# Duplicate reminder emails for the same template/user/day are prevented
# by dashboard.db.has_sent_template_today(), and cron uses flock to avoid
# overlapping runs.

cd "$PROJECT_DIR"

timestamp() {
  date '+%Y-%m-%d %H:%M:%S %Z'
}

echo "[$(timestamp)] [SCHEDULE] Starting reminder run"

if ! /usr/bin/docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
  echo "[$(timestamp)] [SCHEDULE] Container $CONTAINER_NAME is not running, starting it first"
  /usr/bin/docker compose up -d "$CONTAINER_NAME"
fi

/usr/bin/docker exec "$CONTAINER_NAME" python -m dashboard.runner --config "$CONFIG_PATH"

echo "[$(timestamp)] [SCHEDULE] Reminder run finished"
