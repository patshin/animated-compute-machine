"""Consistent backup, verification, and explicit restore for repository-owned SQLite data."""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DATABASE_NAMES = ("agent-trace.sqlite3", "agent-trace.e9-langgraph.sqlite3")
MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = "1.0.0"


def _real_directory(path: Path, *, create: bool = False) -> Path:
    if path.is_symlink():
        raise RuntimeError(f"directory must not be a symbolic link: {path}")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise RuntimeError(f"directory does not exist: {path}")
    return path.resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quick_check(path: Path) -> None:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        result = connection.execute("PRAGMA quick_check").fetchone()
    finally:
        connection.close()
    if result is None or result[0] != "ok":
        raise RuntimeError(f"SQLite integrity check failed: {path.name}")


def _online_backup(source: Path, destination: Path) -> None:
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
        journal_mode = destination_connection.execute("PRAGMA journal_mode=DELETE").fetchone()
        if journal_mode is None or str(journal_mode[0]).lower() != "delete":
            raise RuntimeError(f"could not normalize backup journal mode: {destination.name}")
    finally:
        destination_connection.close()
        source_connection.close()
    _quick_check(destination)


def create_backup(data_directory: Path, output_directory: Path) -> dict[str, Any]:
    data_root = _real_directory(data_directory)
    output_parent = _real_directory(output_directory.parent, create=True)
    if output_directory.exists() or output_directory.is_symlink():
        raise RuntimeError("backup destination already exists")
    if output_directory.parent.resolve() != output_parent:
        raise RuntimeError("backup destination must be directly inside its declared parent")

    temporary = Path(tempfile.mkdtemp(prefix=".agent-trace-backup-", dir=output_parent))
    try:
        files: list[dict[str, Any]] = []
        for name in DATABASE_NAMES:
            source = data_root / name
            if source.is_symlink():
                raise RuntimeError(f"database must not be a symbolic link: {name}")
            if not source.exists():
                continue
            if not source.is_file():
                raise RuntimeError(f"database must be a regular file: {name}")
            if source.parent.resolve() != data_root:
                raise RuntimeError(f"database escaped data directory: {name}")
            destination = temporary / name
            _online_backup(source, destination)
            files.append(
                {"name": name, "sha256": _sha256(destination), "bytes": destination.stat().st_size}
            )
        if not files or files[0]["name"] != DATABASE_NAMES[0]:
            raise RuntimeError("primary AGENT//TRACE database is missing")
        manifest = {
            "schema_version": MANIFEST_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "files": files,
        }
        (temporary / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        verify_backup(temporary)
        os.replace(temporary, output_directory)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return manifest


def verify_backup(bundle_directory: Path) -> dict[str, Any]:
    bundle_root = _real_directory(bundle_directory)
    manifest_path = bundle_root / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeError("backup manifest is missing or unsafe")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "created_at", "files"}:
        raise RuntimeError("backup manifest has an invalid shape")
    if manifest["schema_version"] != MANIFEST_VERSION or not isinstance(
        manifest["created_at"], str
    ):
        raise RuntimeError("backup manifest version or timestamp is invalid")
    try:
        created_at = datetime.fromisoformat(manifest["created_at"])
    except ValueError as error:
        raise RuntimeError("backup manifest version or timestamp is invalid") from error
    if created_at.tzinfo is None:
        raise RuntimeError("backup manifest version or timestamp is invalid")
    files = manifest["files"]
    if not isinstance(files, list) or not files:
        raise RuntimeError("backup manifest contains no databases")
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict) or set(item) != {"name", "sha256", "bytes"}:
            raise RuntimeError("backup file record has an invalid shape")
        name = item["name"]
        if name not in DATABASE_NAMES or name in seen:
            raise RuntimeError("backup manifest contains an unknown or duplicate filename")
        if (
            not isinstance(item["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None
        ):
            raise RuntimeError("backup manifest contains an invalid digest")
        if type(item["bytes"]) is not int or item["bytes"] <= 0:
            raise RuntimeError("backup manifest contains an invalid size")
        database = bundle_root / name
        if (
            database.is_symlink()
            or not database.is_file()
            or database.parent.resolve() != bundle_root
        ):
            raise RuntimeError("backup database path is missing or unsafe")
        if database.stat().st_size != item["bytes"] or _sha256(database) != item["sha256"]:
            raise RuntimeError(f"backup digest mismatch: {name}")
        _quick_check(database)
        seen.add(name)
    if DATABASE_NAMES[0] not in seen:
        raise RuntimeError("backup does not contain the primary database")
    actual_entries = {path.name for path in bundle_root.iterdir()}
    if actual_entries != seen | {MANIFEST_NAME}:
        raise RuntimeError("backup bundle contains unmanifested or unsafe entries")
    return manifest


def restore_backup(bundle_directory: Path, data_directory: Path) -> dict[str, Any]:
    manifest = verify_backup(bundle_directory)
    bundle_root = bundle_directory.resolve()
    data_root = _real_directory(data_directory)
    staging = Path(tempfile.mkdtemp(prefix=".agent-trace-restore-", dir=data_root))
    rollback = Path(tempfile.mkdtemp(prefix=".agent-trace-rollback-", dir=data_root))
    installed: list[Path] = []
    moved: list[tuple[Path, Path]] = []
    try:
        names = [str(item["name"]) for item in manifest["files"]]
        for name in names:
            _online_backup(bundle_root / name, staging / name)
        for name in DATABASE_NAMES:
            destination = data_root / name
            if destination.is_symlink():
                raise RuntimeError(f"restore target must not be a symbolic link: {name}")
            if destination.exists():
                if not destination.is_file():
                    raise RuntimeError(f"restore target must be a regular file: {name}")
                rollback_path = rollback / name
                os.replace(destination, rollback_path)
                moved.append((rollback_path, destination))
            for suffix in ("-wal", "-shm"):
                sidecar = data_root / f"{name}{suffix}"
                if sidecar.is_symlink():
                    raise RuntimeError(
                        f"restore sidecar must not be a symbolic link: {sidecar.name}"
                    )
                if sidecar.exists():
                    if not sidecar.is_file():
                        raise RuntimeError(
                            f"restore sidecar must be a regular file: {sidecar.name}"
                        )
                    sidecar.unlink()
            if name in names:
                os.replace(staging / name, destination)
                installed.append(destination)
        for destination in installed:
            _quick_check(destination)
    except BaseException:
        for destination in installed:
            if destination.exists():
                destination.unlink()
        for rollback_path, destination in reversed(moved):
            if rollback_path.exists():
                os.replace(rollback_path, destination)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(rollback, ignore_errors=True)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="AGENT//TRACE SQLite backup lifecycle")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("--data-dir", type=Path, required=True)
    backup_parser.add_argument("--output", type=Path, required=True)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--bundle", type=Path, required=True)

    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--bundle", type=Path, required=True)
    restore_parser.add_argument("--data-dir", type=Path, required=True)
    restore_parser.add_argument("--approve", action="store_true")

    arguments = parser.parse_args()
    if arguments.command == "backup":
        result = create_backup(arguments.data_dir, arguments.output)
    elif arguments.command == "verify":
        result = verify_backup(arguments.bundle)
    else:
        if not arguments.approve:
            parser.error("restore requires --approve and must run while the API is stopped")
        result = restore_backup(arguments.bundle, arguments.data_dir)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
