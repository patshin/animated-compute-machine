#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

BACKUP_ROOT="${AGENT_TRACE_BACKUP_DIR:-}"
[[ -n "$BACKUP_ROOT" && "$BACKUP_ROOT" = /* ]] || fail \
  "Set AGENT_TRACE_BACKUP_DIR to an absolute encrypted backup directory."
mkdir -p "$BACKUP_ROOT"
[[ ! -L "$BACKUP_ROOT" && -d "$BACKUP_ROOT" ]] || fail "Backup root must be a real directory."
BACKUP_ROOT="$(cd -- "$BACKUP_ROOT" && pwd -P)"
BACKUP_NAME="agent-trace-$(date -u +%Y%m%dT%H%M%SZ)"

cd "$REPO_ROOT"
docker compose run --rm --no-deps \
  -v "$BACKUP_ROOT:/backups" \
  --entrypoint python api \
  -m agent_trace_api.backup backup \
  --data-dir /app/.local \
  --output "/backups/$BACKUP_NAME"
printf '%s\n' "Backup created and verified: $BACKUP_ROOT/$BACKUP_NAME"
