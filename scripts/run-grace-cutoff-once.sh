#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONTAINER_NAME="pce-dashboard"
CONFIG_FILE="$PROJECT_DIR/config/config.json"
LOCK_FILE="/tmp/pce-grace-cutoff.lock"
CRON_MARKER="PCE_ONE_TIME_GRACE_CUTOFF_20260713"

timestamp() {
  date '+%Y-%m-%d %H:%M:%S %Z'
}

cd "$PROJECT_DIR"

cleanup_cron() {
  local tmpfile
  tmpfile="$(mktemp)"
  crontab -l 2>/dev/null | grep -v "$CRON_MARKER" > "$tmpfile" || true
  crontab "$tmpfile"
  rm -f "$tmpfile"
}

echo "[$(timestamp)] [GRACE-CUTOFF] Starting one-time enforcement run"

cleanup_cron

if ! /usr/bin/docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
  echo "[$(timestamp)] [GRACE-CUTOFF] Container $CONTAINER_NAME is not running, starting it first"
  /usr/bin/docker compose up -d "$CONTAINER_NAME"
fi

PCE_CONFIG_FILE="$CONFIG_FILE" python3 - <<'PY'
import json
import os
from pathlib import Path

config_path = Path(os.environ["PCE_CONFIG_FILE"])
config = json.loads(config_path.read_text(encoding="utf-8"))
config.setdefault("Policy", {})
config["Policy"]["RunMode"] = "enforcement"
config_path.write_text(json.dumps(config, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
print("[GRACE-CUTOFF] Config switched to enforcement mode")
PY

/usr/bin/flock -n "$LOCK_FILE" /usr/bin/docker exec "$CONTAINER_NAME" python -m dashboard.runner --config /app/config/config.json

PCE_CONFIG_FILE="$CONFIG_FILE" python3 - <<'PY'
import json
import os
from pathlib import Path

config_path = Path(os.environ["PCE_CONFIG_FILE"])
config = json.loads(config_path.read_text(encoding="utf-8"))
config.setdefault("Policy", {})
config["Policy"]["RunMode"] = "reminder_only"
config["Policy"]["TemporaryExpiredGraceDays"] = 0
config["Policy"]["TemporaryExpiredGraceStart"] = ""
config_path.write_text(json.dumps(config, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
print("[GRACE-CUTOFF] Config restored to reminder_only and temporary grace cleared")
PY

echo "[$(timestamp)] [GRACE-CUTOFF] One-time enforcement run finished"
