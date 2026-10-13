#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

command -v node >/dev/null 2>&1 || fail "Node.js is missing. Use Node 24.15+ LTS or Node 26 without global repository mutations."
node "$SCRIPT_DIR/check-node-version.mjs" "$(node --version)" >/dev/null \
  || fail "Node $(node --version) is outside the supported ^24.15.0 || >=26.0.0 <27 range."

"$SCRIPT_DIR/pnpm.sh" --version
"$SCRIPT_DIR/python.sh" -c 'import fastapi, jsonschema, langgraph, platform, pydantic, sys, uvicorn; print(f"python={platform.python_version()} executable={sys.executable} prefix={sys.prefix}")'
printf 'node=%s\n' "$(node --version)"
printf 'repo=%s\n' "$REPO_ROOT"
printf 'environment=valid\n'
