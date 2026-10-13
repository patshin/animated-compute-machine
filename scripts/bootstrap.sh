#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

if [[ "${1:-}" != "--install" ]]; then
  printf '%s\n' "Bootstrap is inspect-first and repo-local."
  printf '%s\n' "Planned writes: .venv/, .pip-cache/, .pnpm-store/, workspace node_modules/, and apps/api/*.egg-info/."
  printf '%s\n' "Network may be used only to resolve pinned lockfile dependencies."
  printf '%s\n' "Run: ./scripts/bootstrap.sh --install"
  exit 0
fi

if [[ -e "$REPO_ROOT/.venv" ]]; then
  "$SCRIPT_DIR/python.sh" -c 'print("existing .venv is valid")'
else
  BOOTSTRAP_PYTHON="${AGENT_TRACE_PYTHON:-}"
  if [[ -z "$BOOTSTRAP_PYTHON" ]]; then
    for candidate in python3.13 python3.12; do
      if command -v "$candidate" >/dev/null 2>&1; then
        BOOTSTRAP_PYTHON="$(command -v "$candidate")"
        break
      fi
    done
  fi
  [[ -n "$BOOTSTRAP_PYTHON" && -x "$BOOTSTRAP_PYTHON" ]] || fail "Python 3.12/3.13 is required. Install it safely or set AGENT_TRACE_PYTHON to an approved interpreter; no global changes were made."
  "$BOOTSTRAP_PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] in {(3, 12), (3, 13)} else 2)' \
    || fail "AGENT_TRACE_PYTHON must be Python 3.12 or 3.13."
  SHARED_LIBRARY="$("$BOOTSTRAP_PYTHON" -c 'import pathlib, sys, sysconfig
library = sysconfig.get_config_var("LDLIBRARY")
directory = sysconfig.get_config_var("LIBDIR")
candidates = []
if library and directory:
    candidates.append(pathlib.Path(directory, library))
if library:
    candidates.extend([
        pathlib.Path(sys.base_prefix, "lib", library),
        pathlib.Path(sys.prefix, "lib", library),
        pathlib.Path(sys.executable).resolve().parent.parent / "lib" / library,
    ])
print(next((path.resolve() for path in candidates if path.is_file()), ""))')"
  "$BOOTSTRAP_PYTHON" -m venv --copies --without-pip "$REPO_ROOT/.venv"
  if [[ -n "$SHARED_LIBRARY" && -f "$SHARED_LIBRARY" ]]; then
    cp "$SHARED_LIBRARY" "$REPO_ROOT/.venv/lib/"
  fi
  "$REPO_ROOT/.venv/bin/python" -m ensurepip --upgrade --default-pip
fi

"$SCRIPT_DIR/python.sh" -m pip install --disable-pip-version-check --requirement "$REPO_ROOT/requirements.production.lock"
"$SCRIPT_DIR/pnpm.sh" install --frozen-lockfile
"$SCRIPT_DIR/check-env.sh"
