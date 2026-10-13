#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

API_PORT="${AGENT_TRACE_API_PORT:-8000}"
WEB_PORT="${AGENT_TRACE_WEB_PORT:-4173}"

check_port() {
  node -e 'const net = require("node:net"); const server = net.createServer(); server.once("error", () => process.exit(1)); server.listen(Number(process.argv[1]), "127.0.0.1", () => server.close(() => process.exit(0)));' "$1"
}

check_port "$API_PORT" || fail "API port $API_PORT is unavailable. Set AGENT_TRACE_API_PORT to an unused port."
check_port "$WEB_PORT" || fail "Web port $WEB_PORT is unavailable. Set AGENT_TRACE_WEB_PORT to an unused port."

"$SCRIPT_DIR/python.sh" -m uvicorn agent_trace_api.main:app --app-dir "$REPO_ROOT/apps/api" --host 127.0.0.1 --port "$API_PORT" &
API_PID=$!
cleanup() {
  kill "$API_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

API_READY=0
for ((attempt = 0; attempt < 100; attempt += 1)); do
  if node -e 'fetch(`http://127.0.0.1:${process.argv[1]}/api/health`).then((response) => process.exit(response.ok ? 0 : 1)).catch(() => process.exit(1));' "$API_PORT"; then
    API_READY=1
    break
  fi
  kill -0 "$API_PID" >/dev/null 2>&1 || fail "API process exited before becoming ready."
  sleep 0.05
done
[[ "$API_READY" == "1" ]] || fail "API did not become ready on localhost port $API_PORT."

VITE_API_TARGET="http://127.0.0.1:$API_PORT" "$SCRIPT_DIR/pnpm.sh" --filter @agent-trace/web exec vite --host 127.0.0.1 --port "$WEB_PORT" --strictPort
