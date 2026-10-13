#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

[[ "${1:-}" == "--approve" ]] || fail "Explicit approval required. Re-run with --approve to delete only repository-local runtime data."
DATABASE_PATH_CONFIG="$REPO_ROOT/config/local-database-path.txt"
[[ -f "$DATABASE_PATH_CONFIG" ]] || fail "Missing config/local-database-path.txt. Refusing to guess a deletion target."
IFS= read -r DATABASE_RELATIVE_PATH < "$DATABASE_PATH_CONFIG"
[[ "$DATABASE_RELATIVE_PATH" == .local/* && "$DATABASE_RELATIVE_PATH" != *".."* && "$DATABASE_RELATIVE_PATH" == *.sqlite3 ]] \
  || fail "Configured database target must be a .sqlite3 file inside .local."
DATABASE_FILENAME="${DATABASE_RELATIVE_PATH#.local/}"
[[ -n "$DATABASE_FILENAME" && "$DATABASE_FILENAME" != */* ]] \
  || fail "Configured database target must be one file directly inside .local."
DATABASE_STEM="${DATABASE_FILENAME%.sqlite3}"
RUNTIME_DATABASE_FILENAME="${DATABASE_STEM}.e9-langgraph.sqlite3"
LOCAL_DATA_DIRECTORY="$REPO_ROOT/.local"
[[ ! -L "$LOCAL_DATA_DIRECTORY" ]] || fail "Repository .local must not be a symbolic link."
mkdir -p "$LOCAL_DATA_DIRECTORY"
[[ -d "$LOCAL_DATA_DIRECTORY" && ! -L "$LOCAL_DATA_DIRECTORY" ]] \
  || fail "Repository .local must be a real directory."
REPOSITORY_PHYSICAL_ROOT="$(cd -- "$REPO_ROOT" && pwd -P)"
LOCAL_DATA_PHYSICAL_DIRECTORY="$(cd -- "$LOCAL_DATA_DIRECTORY" && pwd -P)"
[[ "$LOCAL_DATA_PHYSICAL_DIRECTORY" == "$REPOSITORY_PHYSICAL_ROOT/.local" ]] \
  || fail "Repository .local must resolve inside the repository."
AUDIT_PATH="$LOCAL_DATA_DIRECTORY/audit.log"
DATABASE_TARGETS=(
  "$LOCAL_DATA_DIRECTORY/$DATABASE_FILENAME"
  "$LOCAL_DATA_DIRECTORY/$RUNTIME_DATABASE_FILENAME"
)
[[ ! -L "$AUDIT_PATH" ]] || fail "Repository-local audit file must not be a symbolic link."
for database_target in "${DATABASE_TARGETS[@]}"; do
  [[ ! -L "$database_target" && ! -L "$database_target-shm" && ! -L "$database_target-wal" ]] \
    || fail "Repository-local database files must not be symbolic links."
done
printf '%s\treset-local-data\ttargets=.local/%s,.local/%s\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$DATABASE_FILENAME" "$RUNTIME_DATABASE_FILENAME" >> "$AUDIT_PATH"
for database_target in "${DATABASE_TARGETS[@]}"; do
  rm -f -- "$database_target" "$database_target-shm" "$database_target-wal"
done
printf 'Repository-local runtime data is reset; audit retained at .local/audit.log.\n'
