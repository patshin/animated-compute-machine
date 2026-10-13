#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

[[ "${1:-}" == "--approve" && $# -eq 2 ]] || fail "Usage: ./scripts/restore.sh --approve /absolute/path/to/backup-bundle"
BUNDLE="$2"
[[ "$BUNDLE" = /* && -d "$BUNDLE" && ! -L "$BUNDLE" ]] || fail "Backup bundle must be an absolute real directory."
BUNDLE_PARENT="$(cd -- "$(dirname -- "$BUNDLE")" && pwd -P)"
BUNDLE_NAME="$(basename -- "$BUNDLE")"

cd "$REPO_ROOT"
if docker compose ps --status running --services | grep -qx api; then
  fail "API is running. Stop the stack before restore: docker compose down"
fi
docker compose run --rm --no-deps \
  -v "$BUNDLE_PARENT:/backup-source:ro" \
  --entrypoint python api \
  -m agent_trace_api.backup restore \
  --approve \
  --bundle "/backup-source/$BUNDLE_NAME" \
  --data-dir /app/.local
printf '%s\n' "Restore completed and SQLite integrity verified. Start the stack, open /api/ready, and confirm that status is ready."
