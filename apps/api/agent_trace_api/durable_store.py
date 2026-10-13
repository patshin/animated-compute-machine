import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

from agent_trace_api.durable_models import (
    DurableCheckpoint,
    DurableRunSnapshot,
    DurableRunState,
)
from agent_trace_api.models import AgentEvent
from agent_trace_api.store import RunNotFoundError, StoreConflictError


@dataclass(frozen=True)
class DurableCommandRecord:
    status: str
    result_json: str | None
    error_message: str | None
    owner_id: str
    claimed_at: str
    completed_at: str | None


class SQLiteDurableStore:
    """Repository-owned projection store for public durable-run contracts."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS durable_runs (
              run_id TEXT PRIMARY KEY,
              state_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS durable_events (
              run_id TEXT NOT NULL,
              sequence INTEGER NOT NULL,
              event_id TEXT NOT NULL UNIQUE,
              event_json TEXT NOT NULL,
              PRIMARY KEY (run_id, sequence),
              FOREIGN KEY (run_id) REFERENCES durable_runs(run_id)
            );
            CREATE TABLE IF NOT EXISTS durable_checkpoints (
              run_id TEXT NOT NULL,
              checkpoint_id TEXT NOT NULL,
              checkpoint_json TEXT NOT NULL,
              PRIMARY KEY (run_id, checkpoint_id),
              FOREIGN KEY (run_id) REFERENCES durable_runs(run_id)
            );
            CREATE TABLE IF NOT EXISTS durable_commands (
              idempotency_key TEXT PRIMARY KEY,
              command TEXT NOT NULL,
              run_id TEXT NOT NULL,
              request_hash TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('pending', 'succeeded', 'failed')),
              result_json TEXT,
              error_message TEXT,
              owner_id TEXT NOT NULL DEFAULT 'legacy',
              claimed_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00',
              completed_at TEXT
            );
            """
        )
        self._migrate_commands_table()
        self._connection.commit()

    def _migrate_commands_table(self) -> None:
        """Migrate the uncommitted E5 prototype table without losing local evidence."""
        columns = {
            str(row["name"]): row
            for row in self._connection.execute("PRAGMA table_info(durable_commands)")
        }
        if not {"request_hash", "status", "error_message"}.issubset(columns):
            self._connection.executescript(
                """
                ALTER TABLE durable_commands RENAME TO durable_commands_e5_prototype;
                CREATE TABLE durable_commands (
                  idempotency_key TEXT PRIMARY KEY,
                  command TEXT NOT NULL,
                  run_id TEXT NOT NULL,
                  request_hash TEXT NOT NULL,
                  status TEXT NOT NULL CHECK(status IN ('pending', 'succeeded', 'failed')),
                  result_json TEXT,
                  error_message TEXT,
                  owner_id TEXT NOT NULL DEFAULT 'legacy',
                  claimed_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00',
                  completed_at TEXT
                );
                INSERT INTO durable_commands (
                  idempotency_key, command, run_id, request_hash, status, result_json,
                  completed_at
                )
                SELECT idempotency_key, command, run_id, '', 'succeeded', result_json,
                       '1970-01-01T00:00:00+00:00'
                FROM durable_commands_e5_prototype;
                DROP TABLE durable_commands_e5_prototype;
                """
            )
            return
        additions = {
            "owner_id": "TEXT NOT NULL DEFAULT 'legacy'",
            "claimed_at": "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'",
            "completed_at": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                self._connection.execute(
                    f"ALTER TABLE durable_commands ADD COLUMN {name} {definition}"
                )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def create_run(self, state: DurableRunState) -> bool:
        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT 1 FROM durable_runs WHERE run_id = ?", (state.run_id,)
            ).fetchone()
            if existing is not None:
                return False
            self._connection.execute(
                "INSERT INTO durable_runs (run_id, state_json) VALUES (?, ?)",
                (state.run_id, state.model_dump_json()),
            )
            return True

    def list_runs(self) -> list[DurableRunState]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT state_json FROM durable_runs ORDER BY run_id"
            ).fetchall()
        return [DurableRunState.model_validate_json(row["state_json"]) for row in rows]

    def get_run(self, run_id: str) -> DurableRunState:
        with self._lock:
            row = self._connection.execute(
                "SELECT state_json FROM durable_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise RunNotFoundError(run_id)
        return DurableRunState.model_validate_json(row["state_json"])

    def save_run(self, state: DurableRunState) -> None:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE durable_runs SET state_json = ? WHERE run_id = ?",
                (state.model_dump_json(), state.run_id),
            )
            if cursor.rowcount != 1:
                raise RunNotFoundError(state.run_id)

    def append_event(self, event: AgentEvent, state: DurableRunState) -> None:
        if event.run_id != state.run_id or event.sequence != state.last_sequence:
            raise StoreConflictError("durable event and run projection disagree")
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS sequence FROM durable_events "
                "WHERE run_id = ?",
                (event.run_id,),
            ).fetchone()
            expected = int(row["sequence"]) + 1
            if event.sequence != expected:
                raise StoreConflictError(
                    f"durable event sequence must be monotonic: expected {expected}, "
                    f"got {event.sequence}"
                )
            self._connection.execute(
                "INSERT INTO durable_events (run_id, sequence, event_id, event_json) "
                "VALUES (?, ?, ?, ?)",
                (event.run_id, event.sequence, event.event_id, event.model_dump_json()),
            )
            cursor = self._connection.execute(
                "UPDATE durable_runs SET state_json = ? WHERE run_id = ?",
                (state.model_dump_json(), state.run_id),
            )
            if cursor.rowcount != 1:
                raise RunNotFoundError(state.run_id)

    def list_events(self, run_id: str, *, after_sequence: int = 0) -> list[AgentEvent]:
        self.get_run(run_id)
        with self._lock:
            rows = self._connection.execute(
                "SELECT event_json FROM durable_events "
                "WHERE run_id = ? AND sequence > ? ORDER BY sequence ASC",
                (run_id, after_sequence),
            ).fetchall()
        return [AgentEvent.model_validate_json(row["event_json"]) for row in rows]

    def save_checkpoint(self, checkpoint: DurableCheckpoint) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO durable_checkpoints "
                "(run_id, checkpoint_id, checkpoint_json) VALUES (?, ?, ?) "
                "ON CONFLICT(run_id, checkpoint_id) DO UPDATE SET "
                "checkpoint_json = excluded.checkpoint_json",
                (checkpoint.run_id, checkpoint.checkpoint_id, checkpoint.model_dump_json()),
            )

    def publish_boundary(
        self,
        state: DurableRunState,
        events: list[AgentEvent],
        checkpoint: DurableCheckpoint,
    ) -> None:
        """Atomically publish one already-persisted LangGraph durable boundary."""
        if not events or any(event.run_id != state.run_id for event in events):
            raise StoreConflictError("boundary events must belong to the projected run")
        if checkpoint.run_id != state.run_id or checkpoint.thread_id != state.thread_id:
            raise StoreConflictError("boundary checkpoint and run projection disagree")
        if state.last_sequence != events[-1].sequence:
            raise StoreConflictError("boundary state and final event sequence disagree")
        checkpoint_events = [
            event
            for event in events
            if event.type == "checkpoint.created"
            and event.payload.get("checkpoint_id") == checkpoint.checkpoint_id
        ]
        if len(checkpoint_events) != 1 or checkpoint_events[0].sequence != checkpoint.sequence:
            raise StoreConflictError("boundary checkpoint lacks one matching canonical event")
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS sequence FROM durable_events "
                "WHERE run_id = ?",
                (state.run_id,),
            ).fetchone()
            first_sequence = int(row["sequence"]) + 1
            expected = list(range(first_sequence, first_sequence + len(events)))
            if [event.sequence for event in events] != expected:
                raise StoreConflictError("boundary events must extend the public event sequence")
            self._connection.executemany(
                "INSERT INTO durable_events (run_id, sequence, event_id, event_json) "
                "VALUES (?, ?, ?, ?)",
                [
                    (event.run_id, event.sequence, event.event_id, event.model_dump_json())
                    for event in events
                ],
            )
            self._connection.execute(
                "INSERT INTO durable_checkpoints "
                "(run_id, checkpoint_id, checkpoint_json) VALUES (?, ?, ?)",
                (checkpoint.run_id, checkpoint.checkpoint_id, checkpoint.model_dump_json()),
            )
            cursor = self._connection.execute(
                "UPDATE durable_runs SET state_json = ? WHERE run_id = ?",
                (state.model_dump_json(), state.run_id),
            )
            if cursor.rowcount != 1:
                raise RunNotFoundError(state.run_id)

    def publish_fork(
        self,
        state: DurableRunState,
        events: list[AgentEvent],
        checkpoint: DurableCheckpoint,
    ) -> None:
        """Publish the complete repository-owned fork projection in one transaction."""
        if not events or [event.sequence for event in events] != list(range(1, len(events) + 1)):
            raise StoreConflictError("fork events must form a complete monotonic sequence")
        if any(event.run_id != state.run_id for event in events):
            raise StoreConflictError("fork events must belong to the child run")
        if state.last_sequence != len(events) or checkpoint.run_id != state.run_id:
            raise StoreConflictError("fork state, events and checkpoint disagree")
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO durable_runs (run_id, state_json) VALUES (?, ?)",
                (state.run_id, state.model_dump_json()),
            )
            self._connection.executemany(
                "INSERT INTO durable_events (run_id, sequence, event_id, event_json) "
                "VALUES (?, ?, ?, ?)",
                [
                    (event.run_id, event.sequence, event.event_id, event.model_dump_json())
                    for event in events
                ],
            )
            self._connection.execute(
                "INSERT INTO durable_checkpoints "
                "(run_id, checkpoint_id, checkpoint_json) VALUES (?, ?, ?)",
                (checkpoint.run_id, checkpoint.checkpoint_id, checkpoint.model_dump_json()),
            )

    def delete_run_projection(self, run_id: str) -> None:
        """Compensate a fork publish failure without touching its immutable parent."""
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM durable_events WHERE run_id = ?", (run_id,))
            self._connection.execute("DELETE FROM durable_checkpoints WHERE run_id = ?", (run_id,))
            self._connection.execute("DELETE FROM durable_runs WHERE run_id = ?", (run_id,))

    def list_checkpoints(self, run_id: str) -> list[DurableCheckpoint]:
        self.get_run(run_id)
        with self._lock:
            rows = self._connection.execute(
                "SELECT checkpoint_json FROM durable_checkpoints "
                "WHERE run_id = ? ORDER BY rowid ASC",
                (run_id,),
            ).fetchall()
        return [DurableCheckpoint.model_validate_json(row["checkpoint_json"]) for row in rows]

    def get_checkpoint(self, run_id: str, checkpoint_id: str) -> DurableCheckpoint:
        with self._lock:
            row = self._connection.execute(
                "SELECT checkpoint_json FROM durable_checkpoints "
                "WHERE run_id = ? AND checkpoint_id = ?",
                (run_id, checkpoint_id),
            ).fetchone()
        if row is None:
            raise RunNotFoundError(f"{run_id}:{checkpoint_id}")
        return DurableCheckpoint.model_validate_json(row["checkpoint_json"])

    def snapshot(self, run_id: str) -> DurableRunSnapshot:
        return DurableRunSnapshot(
            run=self.get_run(run_id),
            events=self.list_events(run_id),
            checkpoints=self.list_checkpoints(run_id),
        )

    def claim_command(
        self,
        idempotency_key: str,
        command: str,
        run_id: str,
        request_hash: str,
        owner_id: str,
    ) -> DurableCommandRecord | None:
        """Atomically bind a key before any runtime or projection side effect."""
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT command, run_id, request_hash, status, result_json, error_message, "
                "owner_id, claimed_at, completed_at "
                "FROM durable_commands WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row is None:
                self._connection.execute(
                    "INSERT INTO durable_commands "
                    "(idempotency_key, command, run_id, request_hash, status, owner_id, "
                    "claimed_at) VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                    (
                        idempotency_key,
                        command,
                        run_id,
                        request_hash,
                        owner_id,
                        datetime.now(UTC).isoformat(),
                    ),
                )
                return None
        stored_hash = str(row["request_hash"])
        if (
            row["command"] != command
            or row["run_id"] != run_id
            or (stored_hash and stored_hash != request_hash)
        ):
            raise StoreConflictError("idempotency key is already bound to another command")
        return DurableCommandRecord(
            status=str(row["status"]),
            result_json=None if row["result_json"] is None else str(row["result_json"]),
            error_message=(None if row["error_message"] is None else str(row["error_message"])),
            owner_id=str(row["owner_id"]),
            claimed_at=str(row["claimed_at"]),
            completed_at=(None if row["completed_at"] is None else str(row["completed_at"])),
        )

    def command_record(
        self,
        idempotency_key: str,
        command: str,
        run_id: str,
        request_hash: str,
    ) -> DurableCommandRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT command, run_id, request_hash, status, result_json, error_message, "
                "owner_id, claimed_at, completed_at "
                "FROM durable_commands WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if row is None:
            return None
        stored_hash = str(row["request_hash"])
        if (
            row["command"] != command
            or row["run_id"] != run_id
            or (stored_hash and stored_hash != request_hash)
        ):
            raise StoreConflictError("idempotency key is already bound to another command")
        return DurableCommandRecord(
            status=str(row["status"]),
            result_json=None if row["result_json"] is None else str(row["result_json"]),
            error_message=(None if row["error_message"] is None else str(row["error_message"])),
            owner_id=str(row["owner_id"]),
            claimed_at=str(row["claimed_at"]),
            completed_at=(None if row["completed_at"] is None else str(row["completed_at"])),
        )

    def fail_orphaned_commands(self, owner_id: str) -> int:
        """Fence commands accepted by an earlier runtime generation."""
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE durable_commands SET status = 'failed', result_json = NULL, "
                "error_message = ?, completed_at = ? "
                "WHERE status = 'pending' AND owner_id != ?",
                (
                    "command interrupted by runtime reconstruction; submit a new idempotency key",
                    datetime.now(UTC).isoformat(),
                    owner_id,
                ),
            )
            return cursor.rowcount

    def complete_command(self, idempotency_key: str, result_json: str) -> None:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE durable_commands SET status = 'succeeded', result_json = ?, "
                "error_message = NULL, completed_at = ? "
                "WHERE idempotency_key = ? AND status = 'pending'",
                (result_json, datetime.now(UTC).isoformat(), idempotency_key),
            )
            if cursor.rowcount != 1:
                raise StoreConflictError("command claim is no longer pending")

    def fail_command(self, idempotency_key: str, error_message: str) -> None:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE durable_commands SET status = 'failed', result_json = NULL, "
                "error_message = ?, completed_at = ? "
                "WHERE idempotency_key = ? AND status = 'pending'",
                (error_message, datetime.now(UTC).isoformat(), idempotency_key),
            )
            if cursor.rowcount != 1:
                raise StoreConflictError("command claim is no longer pending")

    def table_counts(self) -> dict[str, int]:
        """Narrow diagnostics used by persistence tests, not an HTTP contract."""
        with self._lock:
            names = ["durable_runs", "durable_events", "durable_checkpoints", "checkpoints"]
            return {
                name: int(self._connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
                for name in names
            }
