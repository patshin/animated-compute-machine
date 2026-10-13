#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

[[ $# -eq 1 ]] || fail "Usage: ./scripts/verify-backup.sh /absolute/path/to/backup-bundle"
BUNDLE="$1"
[[ "$BUNDLE" = /* && -d "$BUNDLE" && ! -L "$BUNDLE" ]] || fail "Backup bundle must be an absolute real directory."
BUNDLE_PARENT="$(cd -- "$(dirname -- "$BUNDLE")" && pwd -P)"
BUNDLE_NAME="$(basename -- "$BUNDLE")"

cd "$REPO_ROOT"
docker compose run --rm --no-deps \
  -v "$BUNDLE_PARENT:/backup-source:ro" \
  --entrypoint python api \
  -m agent_trace_api.backup verify \
  --bundle "/backup-source/$BUNDLE_NAME"
