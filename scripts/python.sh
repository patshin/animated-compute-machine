#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

PYTHON="$REPO_ROOT/.venv/bin/python"
[[ -x "$PYTHON" ]] || fail "Missing repository .venv. Run ./scripts/bootstrap.sh --install with Python 3.12/3.13."

"$PYTHON" -c 'import importlib.util, pathlib, sys
repo = pathlib.Path(sys.argv[1]).resolve()
exe = pathlib.Path(sys.executable).resolve()
prefix = pathlib.Path(sys.prefix).resolve()
expected = (repo / ".venv").resolve()
if expected not in exe.parents or prefix != expected:
    raise SystemExit(f"Invalid .venv: executable={exe}, prefix={prefix}, expected={expected}. Move it aside and run ./scripts/bootstrap.sh --install.")
if sys.version_info[:2] not in {(3, 12), (3, 13)}:
    raise SystemExit(f"Unsupported Python {sys.version.split()[0]}; use repository .venv with 3.12 or 3.13.")
if importlib.util.find_spec("pip") is None:
    raise SystemExit("Invalid .venv: pip is unavailable. Move it aside and run ./scripts/bootstrap.sh --install.")' "$REPO_ROOT"

cd "$REPO_ROOT"
export PIP_CACHE_DIR="$REPO_ROOT/.pip-cache"
exec "$PYTHON" "$@"
