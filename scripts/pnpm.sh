#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

EXPECTED="11.16.0"
PNPM=()

# Corepack resolves the packageManager declaration from the current working
# directory. Probe from the repository root so this wrapper remains
# path-independent when a release or contract check invokes it elsewhere.
cd "$REPO_ROOT"

if command -v pnpm >/dev/null 2>&1; then
  if ACTUAL="$(pnpm --version 2>/dev/null)" && [[ "$ACTUAL" == "$EXPECTED" ]]; then
    PNPM=(pnpm)
  fi
fi

if [[ "${#PNPM[@]}" -eq 0 ]] && command -v corepack >/dev/null 2>&1; then
  if ACTUAL="$(corepack pnpm --version 2>/dev/null)" && [[ "$ACTUAL" == "$EXPECTED" ]]; then
    PNPM=(corepack pnpm)
  fi
fi

[[ "${#PNPM[@]}" -gt 0 ]] || fail "pnpm $EXPECTED is required through pnpm or direct corepack pnpm. No global changes were made."
exec "${PNPM[@]}" "$@"
