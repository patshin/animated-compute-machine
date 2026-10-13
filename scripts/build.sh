#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
"$SCRIPT_DIR/pnpm.sh" build
"$SCRIPT_DIR/python.sh" -m compileall -q "$REPO_ROOT/apps/api/agent_trace_api"
