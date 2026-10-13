import sqlite3
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Literal, cast

from agent_trace_api.models import AgentEvent, LabRunSnapshot, LabRunState


class StoreConflictError(RuntimeError):
    pass


class RunNotFoundError(KeyError):
    pass


@dataclass(frozen=True)
class CommandOutcome:
    status: Literal["pending", "succeeded", "failed"]
    result_json: str | None
    error_message: str | None


class SQLiteRunStore:
    """Small persistence adapter; runtime code never reaches sqlite directly."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
              run_id TEXT PRIMARY KEY,
              state_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS run_events (
              run_id TEXT NOT NULL,
              sequence INTEGER NOT NULL,
              event_id TEXT NOT NULL UNIQUE,
              event_json TEXT NOT NULL,
              PRIMARY KEY (run_id, sequence),
              FOREIGN KEY (run_id) REFERENCES runs(run_id)
            );
            CREATE TABLE IF NOT EXISTS command_keys (
              idempotency_key TEXT PRIMARY KEY,
              command TEXT NOT NULL,
              run_id TEXT NOT NULL,
              outcome_status TEXT NOT NULL DEFAULT 'pending',
              result_json TEXT,
              error_message TEXT,
              FOREIGN KEY (run_id) REFERENCES runs(run_id)
            );
            """
        )
        columns = {
            str(row["name"])
            for row in self._connection.execute("PRAGMA table_info(command_keys)").fetchall()
        }
        if "outcome_status" not in columns:
            self._connection.execute(
                "ALTER TABLE command_keys ADD COLUMN outcome_status "
                "TEXT NOT NULL DEFAULT 'succeeded'"
            )
        if "result_json" not in columns:
            self._connection.execute("ALTER TABLE command_keys ADD COLUMN result_json TEXT")
        if "error_message" not in columns:
            self._connection.execute("ALTER TABLE command_keys ADD COLUMN error_message TEXT")
        self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def ready(self) -> bool:
        with self._lock:
            row = self._connection.execute("SELECT 1 AS ready").fetchone()
        return row is not None and int(row["ready"]) == 1

    @staticmethod
    def _outcome(row: sqlite3.Row) -> CommandOutcome:
        status = str(row["outcome_status"])
        if status not in {"pending", "succeeded", "failed"}:
            raise StoreConflictError(f"invalid stored command outcome: {status}")
        return CommandOutcome(
            status=cast(Literal["pending", "succeeded", "failed"], status),
            result_json=row["result_json"],
            error_message=row["error_message"],
        )

    def create_run(self, state: LabRunState, *, idempotency_key: str) -> CommandOutcome | None:
        with self._lock, self._connection:
            existing = self._connection.execute(
                """
                SELECT command, run_id, outcome_status, result_json, error_message
                FROM command_keys WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if existing["command"] != "start" or existing["run_id"] != state.run_id:
                    raise StoreConflictError("idempotency key is already bound to another command")
                return self._outcome(existing)
            self._connection.execute(
                "INSERT INTO runs (run_id, state_json) VALUES (?, ?)",
                (state.run_id, state.model_dump_json()),
            )
            self._connection.execute(
                """
                INSERT INTO command_keys (idempotency_key, command, run_id, outcome_status)
                VALUES (?, 'start', ?, 'pending')
                """,
                (idempotency_key, state.run_id),
            )
            return None

    def get_run(self, run_id: str) -> LabRunState:
        with self._lock:
            row = self._connection.execute(
                "SELECT state_json FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise RunNotFoundError(run_id)
        return LabRunState.model_validate_json(row["state_json"])

    def get_snapshot(self, run_id: str) -> LabRunSnapshot:
        with self._lock:
            run_row = self._connection.execute(
                "SELECT state_json FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run_row is None:
                raise RunNotFoundError(run_id)
            event_rows = self._connection.execute(
                "SELECT event_json FROM run_events WHERE run_id = ? ORDER BY sequence ASC",
                (run_id,),
            ).fetchall()
        return LabRunSnapshot(
            run=LabRunState.model_validate_json(run_row["state_json"]),
            events=[AgentEvent.model_validate_json(row["event_json"]) for row in event_rows],
        )

    def begin_command(
        self, *, idempotency_key: str, command: str, run_id: str
    ) -> CommandOutcome | None:
        with self._lock, self._connection:
            existing = self._connection.execute(
                """
                SELECT command, run_id, outcome_status, result_json, error_message
                FROM command_keys WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if existing["command"] != command or existing["run_id"] != run_id:
                    raise StoreConflictError("idempotency key is already bound to another command")
                return self._outcome(existing)
            self._connection.execute(
                """
                INSERT INTO command_keys
                  (idempotency_key, command, run_id, outcome_status)
                VALUES (?, ?, ?, 'pending')
                """,
                (idempotency_key, command, run_id),
            )
            return None

    def complete_command(self, idempotency_key: str, result: LabRunSnapshot) -> None:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE command_keys
                SET outcome_status = 'succeeded', result_json = ?, error_message = NULL
                WHERE idempotency_key = ? AND outcome_status = 'pending'
                """,
                (result.model_dump_json(), idempotency_key),
            )
            if cursor.rowcount != 1:
                raise StoreConflictError("command cannot transition to succeeded")

    def fail_command(self, idempotency_key: str, error_message: str) -> None:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE command_keys
                SET outcome_status = 'failed', result_json = NULL, error_message = ?
                WHERE idempotency_key = ? AND outcome_status = 'pending'
                """,
                (error_message, idempotency_key),
            )
            if cursor.rowcount != 1:
                raise StoreConflictError("command cannot transition to failed")

    def append_event_and_save_run(self, event: AgentEvent, state: LabRunState) -> None:
        if event.run_id != state.run_id:
            raise StoreConflictError("event and checkpoint must belong to the same run")
        if event.sequence != state.last_sequence:
            raise StoreConflictError("checkpoint sequence must match the appended event")
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS sequence FROM run_events WHERE run_id = ?",
                (event.run_id,),
            ).fetchone()
            expected = int(row["sequence"]) + 1
            if event.sequence != expected:
                raise StoreConflictError(
                    f"event sequence must be monotonic: expected {expected}, got {event.sequence}"
                )
            self._connection.execute(
                """
                INSERT INTO run_events (run_id, sequence, event_id, event_json)
                VALUES (?, ?, ?, ?)
                """,
                (event.run_id, event.sequence, event.event_id, event.model_dump_json()),
            )
            cursor = self._connection.execute(
                "UPDATE runs SET state_json = ? WHERE run_id = ?",
                (state.model_dump_json(), state.run_id),
            )
            if cursor.rowcount != 1:
                raise RunNotFoundError(state.run_id)

    def list_events(self, run_id: str, *, after_sequence: int = 0) -> list[AgentEvent]:
        self.get_run(run_id)
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT event_json FROM run_events
                WHERE run_id = ? AND sequence > ? ORDER BY sequence ASC
                """,
                (run_id, after_sequence),
            ).fetchall()
        return [AgentEvent.model_validate_json(row["event_json"]) for row in rows]
