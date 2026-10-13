from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import time
import uuid
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

from agent_trace_api.durable_models import (
    DurableAnswer,
    DurableRunCommand,
    DurableRunSnapshot,
    StartDurableRunRequest,
)
from agent_trace_api.durable_runtime import LangGraphRuntimeAdapter
from agent_trace_api.observability_models import (
    CompareSection,
    CreateComparePairRequest,
    EvalAssertionDefinition,
    EvalAssertionResult,
    EvalBatch,
    EvalCaseDefinition,
    EvalCatalog,
    EvalDashboard,
    EvalRun,
    EvalScenarioRuntimeProfile,
    EventPage,
    ExportBundle,
    ExportFile,
    ExportManifest,
    ExportManifestEntry,
    ExportRunRequest,
    FailureBreakdown,
    FailureCluster,
    OtelStatus,
    RunComparison,
    RunEvalBatchRequest,
    ScenarioMetric,
    TraceEvent,
    TraceSpan,
)
from agent_trace_api.retrieval_models import RetrievalRun, StartRetrievalRunRequest
from agent_trace_api.retrieval_service import RetrievalService
from agent_trace_api.tool_calling import ToolLabRun, ToolLabService, ToolRunCommand

CREATED_AT = "2026-08-01T09:00:00+08:00"
BATCH_CLAIM_LEASE_MS = 30_000
EVALUATOR_CONTRACT_VERSION = "agent-trace://e9-evaluator-proof/1.0.0"
SECRET_PATTERN = re.compile(
    r"(?i)(?:authorization\s*:\s*bearer\s+|api[_-]?key\s*[=:]\s*|"
    r"(?<![A-Za-z0-9])sk-)[A-Za-z0-9._-]{8,}"
)
SENSITIVE_KEYS = {
    "client_secret",
    "cookie",
    "credentials",
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "client_key",
    "credential",
    "id_token",
    "private_key",
    "private_key_material",
    "refresh_token",
    "password",
    "secret",
    "secret_key",
    "session_cookie",
    "session_token",
    "set_cookie",
    "signing_key",
    "token",
}
SENSITIVE_KEY_SUFFIXES = (
    "_cookie",
    "_credential",
    "_credentials",
    "_password",
    "_private_key",
    "_secret",
    "_token",
)


class ObservabilityNotFoundError(KeyError):
    pass


class ObservabilityConflictError(RuntimeError):
    pass


class ObservabilityInputError(ValueError):
    pass


def _digest(*parts: object, length: int = 16) -> str:
    source = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(source).hexdigest()[:length]


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _verify_event_integrity(event: TraceEvent) -> None:
    if _sha256(_canonical(event.payload)) != event.payload_hash:
        raise ObservabilityConflictError(
            f"trace event payload hash does not match persisted content: {event.event_id}"
        )


def _verify_run_integrity(run: EvalRun) -> None:
    content = run.model_dump(mode="json", exclude={"integrity_sha256"})
    legacy_content = {key: value for key, value in content.items() if key != "schema_id"}
    accepted_hashes = {
        _sha256(_canonical(content)),
        _sha256(_canonical(legacy_content)),
    }
    if run.integrity_sha256 not in accepted_hashes:
        raise ObservabilityConflictError("evaluation run integrity hash does not match its content")
    for event in run.events:
        _verify_event_integrity(event)


def _verify_batch_integrity(batch: EvalBatch) -> None:
    content = batch.model_dump(mode="json", exclude={"integrity_sha256"})
    if _sha256(_canonical(content)) != batch.integrity_sha256:
        raise ObservabilityConflictError(
            "evaluation batch integrity hash does not match its content"
        )
    for run in batch.runs:
        _verify_run_integrity(run)


def _restore_run(raw: str | bytes) -> EvalRun:
    try:
        return EvalRun.model_validate_json(raw)
    except ValueError as error:
        raise ObservabilityConflictError(
            "persisted evaluation run cannot be reconstructed"
        ) from error


def _restore_event(raw: str | bytes) -> TraceEvent:
    try:
        return TraceEvent.model_validate_json(raw)
    except ValueError as error:
        raise ObservabilityConflictError("persisted trace event cannot be reconstructed") from error


def _restore_batch(raw: str | bytes) -> EvalBatch:
    try:
        batch = EvalBatch.model_validate_json(raw)
    except ValueError as error:
        raise ObservabilityConflictError(
            "persisted evaluation batch cannot be reconstructed"
        ) from error
    _verify_batch_integrity(batch)
    return batch


def _redact_string(value: str) -> tuple[str, bool]:
    cleaned, count = SECRET_PATTERN.subn("[REDACTED]", value)
    return cleaned, count > 0


def _is_sensitive_key(key_name: str | None) -> bool:
    if key_name is None:
        return False
    normalized = key_name.lower().replace("-", "_")
    return normalized in SENSITIVE_KEYS or normalized.endswith(SENSITIVE_KEY_SUFFIXES)


def _redact(value: Any, path: str = "$", *, key_name: str | None = None) -> tuple[Any, list[str]]:
    if _is_sensitive_key(key_name):
        return "[REDACTED]", [path]
    if isinstance(value, str):
        clean, changed = _redact_string(value)
        return clean, [path] if changed else []
    if isinstance(value, list):
        output: list[Any] = []
        paths: list[str] = []
        for index, item in enumerate(value):
            clean, nested = _redact(item, f"{path}[{index}]")
            output.append(clean)
            paths.extend(nested)
        return output, paths
    if isinstance(value, dict):
        output_dict: dict[str, Any] = {}
        paths = []
        for key, item in value.items():
            clean, nested = _redact(item, f"{path}.{key}", key_name=str(key))
            output_dict[str(key)] = clean
            paths.extend(nested)
        return output_dict, paths
    return value, []


class ObservabilityStore:
    def __init__(self, path: Path) -> None:
        self._lock = RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self.owner_token = f"eval-owner-{uuid.uuid4().hex}"
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS e9_eval_runs (
              run_id TEXT PRIMARY KEY,
              case_id TEXT NOT NULL,
              scenario_id TEXT NOT NULL,
              run_json TEXT NOT NULL,
              integrity_sha256 TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS e9_trace_events (
              run_id TEXT NOT NULL,
              sequence INTEGER NOT NULL,
              event_id TEXT NOT NULL UNIQUE,
              event_json TEXT NOT NULL,
              payload_hash TEXT NOT NULL,
              PRIMARY KEY (run_id, sequence),
              FOREIGN KEY (run_id) REFERENCES e9_eval_runs(run_id)
            );
            CREATE TABLE IF NOT EXISTS e9_eval_batches (
              batch_id TEXT PRIMARY KEY,
              batch_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS e9_commands (
              idempotency_key TEXT PRIMARY KEY,
              command_fingerprint TEXT NOT NULL,
              result_kind TEXT NOT NULL,
              result_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS e9_command_claims (
              idempotency_key TEXT PRIMARY KEY,
              command_fingerprint TEXT NOT NULL,
              result_kind TEXT NOT NULL,
              owner_token TEXT NOT NULL,
              claimed_at_ms INTEGER NOT NULL
            );
            """
        )
        self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def command_result(self, idempotency_key: str, fingerprint: str) -> tuple[str, str] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT command_fingerprint, result_kind, result_id FROM e9_commands "
                "WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if row is None:
            return None
        if str(row["command_fingerprint"]) != fingerprint:
            raise ObservabilityConflictError(
                "idempotency key is already bound to different evaluation inputs"
            )
        return str(row["result_kind"]), str(row["result_id"])

    def save_command(
        self, idempotency_key: str, fingerprint: str, kind: str, result_id: str
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO e9_commands "
                "(idempotency_key, command_fingerprint, result_kind, result_id) "
                "VALUES (?, ?, ?, ?)",
                (idempotency_key, fingerprint, kind, result_id),
            )

    def claim_batch_command(
        self, idempotency_key: str, fingerprint: str, *, now_ms: int | None = None
    ) -> str:
        """Claim execution before effects; reclaim only after the bounded batch lease."""
        current_ms = int(time.time() * 1000) if now_ms is None else now_ms
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                completed = self._connection.execute(
                    "SELECT command_fingerprint, result_kind, result_id FROM e9_commands "
                    "WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if completed is not None:
                    if str(completed["command_fingerprint"]) != fingerprint:
                        raise ObservabilityConflictError(
                            "idempotency key is already bound to different evaluation inputs"
                        )
                    if str(completed["result_kind"]) != "batch":
                        raise ObservabilityConflictError("idempotency result has the wrong kind")
                    self._connection.commit()
                    return str(completed["result_id"])
                claim = self._connection.execute(
                    "SELECT command_fingerprint, result_kind, owner_token, claimed_at_ms "
                    "FROM e9_command_claims "
                    "WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if claim is None:
                    self._connection.execute(
                        "INSERT INTO e9_command_claims "
                        "(idempotency_key, command_fingerprint, result_kind, owner_token, "
                        "claimed_at_ms) VALUES (?, ?, 'batch', ?, ?)",
                        (idempotency_key, fingerprint, self.owner_token, current_ms),
                    )
                    self._connection.commit()
                    return "owner"
                if str(claim["command_fingerprint"]) != fingerprint:
                    raise ObservabilityConflictError(
                        "idempotency key is already bound to different evaluation inputs"
                    )
                if str(claim["result_kind"]) != "batch":
                    raise ObservabilityConflictError("idempotency result has the wrong kind")
                if (
                    str(claim["owner_token"]) != self.owner_token
                    and current_ms - int(claim["claimed_at_ms"]) >= BATCH_CLAIM_LEASE_MS
                ):
                    # Golden batches have a 15 s acceptance budget and only run
                    # deterministic read/blocked probes under stable nested keys.
                    # A 30 s lease cannot overlap a conforming owner, but lets a
                    # reconstructed service recover after an owner process crashes.
                    self._connection.execute(
                        "UPDATE e9_command_claims SET owner_token = ?, claimed_at_ms = ? "
                        "WHERE idempotency_key = ? AND command_fingerprint = ? "
                        "AND owner_token = ? AND claimed_at_ms = ?",
                        (
                            self.owner_token,
                            current_ms,
                            idempotency_key,
                            fingerprint,
                            str(claim["owner_token"]),
                            int(claim["claimed_at_ms"]),
                        ),
                    )
                    self._connection.commit()
                    return "owner"
                self._connection.commit()
                return "owner" if str(claim["owner_token"]) == self.owner_token else "pending"
            except Exception:
                self._connection.rollback()
                raise

    def release_batch_claim(self, idempotency_key: str, fingerprint: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM e9_command_claims WHERE idempotency_key = ? "
                "AND command_fingerprint = ? AND owner_token = ?",
                (idempotency_key, fingerprint, self.owner_token),
            )

    def save_run(self, run: EvalRun) -> None:
        _verify_run_integrity(run)
        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT run_json, integrity_sha256 FROM e9_eval_runs WHERE run_id = ?",
                (run.run_id,),
            ).fetchone()
            if existing is not None:
                existing_run = _restore_run(existing["run_json"])
                _verify_run_integrity(existing_run)
                existing_content = existing_run.model_dump(
                    mode="json", exclude={"integrity_sha256"}
                )
                run_content = run.model_dump(mode="json", exclude={"integrity_sha256"})
                if existing_content != run_content:
                    raise ObservabilityConflictError("stable run ID resolved to different contents")
                if str(existing["integrity_sha256"]) != run.integrity_sha256:
                    self._connection.execute(
                        "UPDATE e9_eval_runs SET run_json = ?, integrity_sha256 = ? "
                        "WHERE run_id = ?",
                        (run.model_dump_json(), run.integrity_sha256, run.run_id),
                    )
                return
            self._connection.execute(
                "INSERT INTO e9_eval_runs "
                "(run_id, case_id, scenario_id, run_json, integrity_sha256) VALUES (?, ?, ?, ?, ?)",
                (
                    run.run_id,
                    run.case_id,
                    run.scenario_id,
                    run.model_dump_json(),
                    run.integrity_sha256,
                ),
            )
            for event in run.events:
                self._connection.execute(
                    "INSERT INTO e9_trace_events "
                    "(run_id, sequence, event_id, event_json, payload_hash) VALUES (?, ?, ?, ?, ?)",
                    (
                        run.run_id,
                        event.sequence,
                        event.event_id,
                        event.model_dump_json(),
                        event.payload_hash,
                    ),
                )

    def get_run(self, run_id: str) -> EvalRun:
        with self._lock:
            row = self._connection.execute(
                "SELECT run_json, integrity_sha256 FROM e9_eval_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise ObservabilityNotFoundError(run_id)
        run = _restore_run(row["run_json"])
        if run.integrity_sha256 != str(row["integrity_sha256"]):
            raise ObservabilityConflictError("persisted run integrity metadata does not match")
        _verify_run_integrity(run)
        return run

    def event_page(self, run_id: str, after_sequence: int, limit: int) -> EventPage:
        self.get_run(run_id)
        with self._lock:
            rows = self._connection.execute(
                "SELECT event_json, payload_hash FROM e9_trace_events "
                "WHERE run_id = ? AND sequence > ? ORDER BY sequence LIMIT ?",
                (run_id, after_sequence, limit + 1),
            ).fetchall()
        has_more = len(rows) > limit
        events = [_restore_event(row["event_json"]) for row in rows[:limit]]
        for event, row in zip(events, rows, strict=False):
            if event.payload_hash != str(row["payload_hash"]):
                raise ObservabilityConflictError(
                    f"trace event integrity metadata does not match: {event.event_id}"
                )
            _verify_event_integrity(event)
        return EventPage(
            run_id=run_id,
            after_sequence=after_sequence,
            next_after_sequence=events[-1].sequence if events else after_sequence,
            has_more=has_more,
            events=events,
        )

    def save_batch(self, batch: EvalBatch) -> None:
        _verify_batch_integrity(batch)
        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT batch_json FROM e9_eval_batches WHERE batch_id = ?", (batch.batch_id,)
            ).fetchone()
            if existing is not None:
                if _restore_batch(existing["batch_json"]) != batch:
                    raise ObservabilityConflictError(
                        "immutable evaluation batch ID resolved to different contents"
                    )
                return
            self._connection.execute(
                "INSERT INTO e9_eval_batches (batch_id, batch_json) VALUES (?, ?)",
                (batch.batch_id, batch.model_dump_json()),
            )

    def save_batch_command(
        self, batch: EvalBatch, idempotency_key: str, fingerprint: str
    ) -> EvalBatch:
        _verify_batch_integrity(batch)
        with self._lock, self._connection:
            existing_command = self._connection.execute(
                "SELECT command_fingerprint, result_kind, result_id FROM e9_commands "
                "WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing_command is not None:
                if str(existing_command["command_fingerprint"]) != fingerprint:
                    raise ObservabilityConflictError(
                        "idempotency key is already bound to different evaluation inputs"
                    )
                if str(existing_command["result_kind"]) != "batch":
                    raise ObservabilityConflictError("idempotency result has the wrong kind")
                return self.get_batch(str(existing_command["result_id"]))
            claim = self._connection.execute(
                "SELECT command_fingerprint, result_kind, owner_token FROM e9_command_claims "
                "WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if (
                claim is None
                or str(claim["command_fingerprint"]) != fingerprint
                or str(claim["result_kind"]) != "batch"
                or str(claim["owner_token"]) != self.owner_token
            ):
                raise ObservabilityConflictError("evaluation batch command claim is not owned")
            existing_batch = self._connection.execute(
                "SELECT batch_json FROM e9_eval_batches WHERE batch_id = ?", (batch.batch_id,)
            ).fetchone()
            if existing_batch is not None:
                if _restore_batch(existing_batch["batch_json"]) != batch:
                    raise ObservabilityConflictError(
                        "immutable evaluation batch ID resolved to different contents"
                    )
            else:
                self._connection.execute(
                    "INSERT INTO e9_eval_batches (batch_id, batch_json) VALUES (?, ?)",
                    (batch.batch_id, batch.model_dump_json()),
                )
            self._connection.execute(
                "INSERT INTO e9_commands "
                "(idempotency_key, command_fingerprint, result_kind, result_id) "
                "VALUES (?, ?, 'batch', ?)",
                (idempotency_key, fingerprint, batch.batch_id),
            )
            self._connection.execute(
                "DELETE FROM e9_command_claims WHERE idempotency_key = ?",
                (idempotency_key,),
            )
            return batch

    def get_batch(self, batch_id: str) -> EvalBatch:
        with self._lock:
            row = self._connection.execute(
                "SELECT batch_json FROM e9_eval_batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()
        if row is None:
            raise ObservabilityNotFoundError(batch_id)
        return _restore_batch(row["batch_json"])


class ObservabilityEvalService:
    """Repository-owned evaluation projection over the real offline runtime boundaries."""

    def __init__(self, repository_root: Path, database_path: Path) -> None:
        self.repository_root = repository_root.resolve()
        self.cases = self._load_cases()
        self.case_by_id = {case.id: case for case in self.cases}
        self.dataset_hash = _sha256(
            (self.repository_root / "evals/golden-cases.jsonl").read_text(encoding="utf-8")
        )
        self.scenario_profiles = self._load_scenario_profiles()
        self.scenario_runtime_hash = _sha256(
            (self.repository_root / "evals/scenario-runtime.json").read_text(encoding="utf-8")
        )
        self.retrieval_fixture_hashes = self._retrieval_fixture_hashes()
        self.evaluation_fingerprint = _sha256(
            _canonical(
                {
                    "dataset_sha256": self.dataset_hash,
                    "scenario_runtime_sha256": self.scenario_runtime_hash,
                    "retrieval_fixture_sha256": self.retrieval_fixture_hashes,
                    "evaluator_contract_version": EVALUATOR_CONTRACT_VERSION,
                }
            )
        )
        self.baseline = self._load_baseline()
        self.store = ObservabilityStore(database_path)
        self.tool_service = ToolLabService()
        self.retrieval_service = RetrievalService(database_path, self.tool_service)
        runtime_path = database_path.with_name(f"{database_path.stem}.e9-langgraph.sqlite3")
        self.runtime = LangGraphRuntimeAdapter(runtime_path, node_delay=0)
        self._batch_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._compare_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def close(self) -> None:
        self.runtime.close()
        self.retrieval_service.close()
        self.store.close()

    def _load_baseline(self) -> dict[str, Any]:
        path = self.repository_root / "evals/baseline.json"
        try:
            baseline = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("versioned evaluation baseline is unreadable") from error
        required = {
            "baseline_id",
            "dataset_id",
            "dataset_sha256",
            "pass_rate",
            "route_accuracy",
            "tool_selection_accuracy",
            "argument_accuracy",
            "citation_coverage",
            "unsafe_action_count",
            "scenario_runtime_sha256",
            "retrieval_fixture_sha256",
            "evaluator_contract_version",
            "evaluation_fingerprint",
        }
        if not isinstance(baseline, dict) or not required <= set(baseline):
            raise RuntimeError("versioned evaluation baseline is incomplete")
        if baseline["dataset_id"] != "golden-cases@1.0.0":
            raise RuntimeError("evaluation baseline targets a different dataset")
        if baseline["dataset_sha256"] != self.dataset_hash:
            raise RuntimeError("evaluation baseline does not match the Golden dataset")
        expected_identity = {
            "scenario_runtime_sha256": self.scenario_runtime_hash,
            "retrieval_fixture_sha256": self.retrieval_fixture_hashes,
            "evaluator_contract_version": EVALUATOR_CONTRACT_VERSION,
            "evaluation_fingerprint": self.evaluation_fingerprint,
        }
        if any(baseline[key] != value for key, value in expected_identity.items()):
            raise RuntimeError(
                "evaluation baseline does not match the runtime fixtures and evaluator contract"
            )
        return cast(dict[str, Any], baseline)

    def _retrieval_fixture_hashes(self) -> dict[str, str]:
        """Bind evaluation identity to every file that can affect real retrieval output."""
        fixture_hashes: dict[str, str] = {}
        kit_refs = sorted(
            {
                profile.scenario_kit_ref
                for profile in self.scenario_profiles.values()
                if profile.scenario_kit_ref is not None
            }
        )
        for kit_ref in kit_refs:
            kit_path = (self.repository_root / kit_ref).resolve()
            try:
                kit_path.relative_to(self.repository_root)
            except ValueError as error:
                raise RuntimeError("retrieval fixture escapes the repository") from error
            fixture_paths = [kit_path / "scenario.json", kit_path / "retrieval.json"]
            fixture_paths.extend(
                path for path in sorted((kit_path / "documents").rglob("*")) if path.is_file()
            )
            try:
                manifest = [
                    {
                        "path": path.relative_to(kit_path).as_posix(),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                    for path in fixture_paths
                ]
            except OSError as error:
                raise RuntimeError(f"retrieval fixture is unreadable: {kit_ref}") from error
            fixture_hashes[kit_ref] = _sha256(_canonical(manifest))
        return fixture_hashes

    def _load_cases(self) -> list[EvalCaseDefinition]:
        schema = json.loads(
            (self.repository_root / "contracts/eval-case.schema.json").read_text(encoding="utf-8")
        )
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        cases: list[EvalCaseDefinition] = []
        seen: set[str] = set()
        for line_number, line in enumerate(
            (self.repository_root / "evals/golden-cases.jsonl")
            .read_text(encoding="utf-8")
            .splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                validator.validate(raw)
                case = EvalCaseDefinition.model_validate(raw)
            except (json.JSONDecodeError, JsonSchemaValidationError, ValueError) as error:
                raise RuntimeError(f"golden case line {line_number} is invalid: {error}") from error
            if case.id in seen:
                raise RuntimeError(f"golden case line {line_number} duplicates id {case.id}")
            seen.add(case.id)
            cases.append(case)
        if len(cases) < 35:
            raise RuntimeError("golden dataset must contain at least 35 cases")
        if len({case.scenario_id for case in cases}) < 7:
            raise RuntimeError("golden dataset must cover at least seven scenarios")
        return cases

    def _load_scenario_profiles(self) -> dict[str, EvalScenarioRuntimeProfile]:
        path = self.repository_root / "evals/scenario-runtime.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("schema_version") != "1.0.0":
                raise ValueError("scenario runtime profile envelope has the wrong version")
            profile_values = raw.get("profiles")
            if not isinstance(profile_values, list):
                raise ValueError("scenario runtime profiles must be a list")
            profiles = [EvalScenarioRuntimeProfile.model_validate(item) for item in profile_values]
        except (OSError, json.JSONDecodeError, ValueError) as error:
            raise RuntimeError("versioned scenario runtime profiles are invalid") from error

        profile_by_id = {profile.scenario_id: profile for profile in profiles}
        scenario_ids = {case.scenario_id for case in self.cases}
        if len(profile_by_id) != len(profiles) or set(profile_by_id) != scenario_ids:
            raise RuntimeError("scenario runtime profiles must map the Golden scenarios exactly")
        if len({profile.route_intent for profile in profiles}) != len(profiles):
            raise RuntimeError("each Golden scenario must have a distinct route intent")

        for profile in profiles:
            if profile.retrieval_scenario_id is None:
                if profile.scenario_kit_ref is not None:
                    raise RuntimeError("scenario kit references require a retrieval scenario")
                continue
            if (
                profile.retrieval_scenario_id != profile.scenario_id
                or profile.scenario_kit_ref != f"scenarios/{profile.scenario_id}"
            ):
                raise RuntimeError("retrieval profiles must reference their own Scenario Kit")
            kit_path = (self.repository_root / profile.scenario_kit_ref).resolve()
            try:
                kit_path.relative_to(self.repository_root)
                manifest = json.loads((kit_path / "scenario.json").read_text(encoding="utf-8"))
                json.loads((kit_path / "retrieval.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, ValueError) as error:
                raise RuntimeError(
                    f"scenario runtime profile has an invalid Kit: {profile.scenario_id}"
                ) from error
            if manifest.get("id") != profile.scenario_id:
                raise RuntimeError("scenario runtime profile and Kit IDs do not match")
        return profile_by_id

    def catalog(self) -> EvalCatalog:
        return EvalCatalog(
            case_count=len(self.cases),
            scenario_count=len({case.scenario_id for case in self.cases}),
            scenarios=sorted({case.scenario_id for case in self.cases}),
            evaluator_kinds=sorted(
                {assertion.kind for case in self.cases for assertion in case.assertions}
            ),
            answer_version_mapping=(
                "Evaluation envelope answer.schema_version is derived from the repository-owned "
                "artifact schema identifier; the E2 LabAnswerPayload contract remains unchanged."
            ),
        )

    def _profile_for_case(self, case: EvalCaseDefinition) -> EvalScenarioRuntimeProfile:
        try:
            profile = self.scenario_profiles[case.scenario_id]
        except KeyError as error:
            raise ObservabilityInputError(
                f"Golden case has no runtime profile: {case.scenario_id}"
            ) from error
        if profile.scenario_id != case.scenario_id:
            raise ObservabilityInputError("Golden case and runtime profile do not match")
        return profile

    @staticmethod
    def _values_at_path(output: dict[str, Any], path: str) -> list[Any]:
        if not path.startswith("$."):
            raise ObservabilityInputError(f"unsupported assertion path: {path}")
        values: list[Any] = [output]
        for component in path[2:].split("."):
            wildcard = component.endswith("[*]")
            key = component[:-3] if wildcard else component
            next_values: list[Any] = []
            for value in values:
                if not isinstance(value, dict) or key not in value:
                    continue
                candidate = value[key]
                if wildcard:
                    if isinstance(candidate, list):
                        next_values.extend(candidate)
                else:
                    next_values.append(candidate)
            values = next_values
        return values

    @classmethod
    def evaluate_assertion(
        cls, output: dict[str, Any], assertion: EvalAssertionDefinition
    ) -> tuple[bool, Any]:
        values = cls._values_at_path(output, assertion.path)
        expected = assertion.expected
        operator = assertion.operator
        if operator == "exists":
            return bool(values) and all(value is not None for value in values), values
        if operator == "not_exists":
            return not values or all(value is None for value in values), values
        if not values:
            return False, None

        def compare(value: Any) -> bool:
            if operator == "equals":
                return bool(value == expected)
            if operator == "contains":
                return bool(expected in value) if isinstance(value, (str, list, dict)) else False
            if operator == "not_contains":
                return bool(expected not in value) if isinstance(value, (str, list, dict)) else True
            if operator == "lte":
                return (
                    isinstance(value, (int, float))
                    and isinstance(expected, (int, float))
                    and value <= expected
                )
            if operator == "gte":
                return (
                    isinstance(value, (int, float))
                    and isinstance(expected, (int, float))
                    and value >= expected
                )
            if operator == "set_equals":
                return (
                    isinstance(value, list)
                    and isinstance(expected, list)
                    and set(value) == set(expected)
                )
            if operator == "subset":
                return (
                    isinstance(value, list)
                    and isinstance(expected, list)
                    and set(value) <= set(expected)
                )
            if operator == "matches":
                return (
                    isinstance(value, str)
                    and isinstance(expected, str)
                    and re.fullmatch(expected, value) is not None
                )
            return False

        return all(compare(value) for value in values), values[0] if len(values) == 1 else values

    @staticmethod
    def _safe_tool_command(
        case: EvalCaseDefinition,
        profile: EvalScenarioRuntimeProfile,
        key: str,
        *,
        fault: str = "none",
    ) -> ToolRunCommand:
        return ToolRunCommand(
            approved=True,
            idempotency_key=key,
            tool_name=profile.tool_name,
            tool_version="1.0.0",
            arguments=profile.tool_arguments,
            actor_role=profile.actor_role,
            expected_risk="read",
            model_rationale=(
                f"Golden scenario {profile.scenario_id} exercises the registered "
                f"{profile.route_intent} boundary."
            ),
            fault=cast(Any, fault),
            seed=case.seed,
        )

    @staticmethod
    def _unsafe_tool_command(case: EvalCaseDefinition, key: str) -> ToolRunCommand:
        return ToolRunCommand(
            approved=True,
            idempotency_key=key,
            tool_name="orders.execute_address_change",
            tool_version="1.0.0",
            arguments={
                "proposal_id": "proposal_5e80ea83b104",
                "order_id": "ORD-1042",
                "new_city": "Hangzhou",
            },
            actor_role="approver",
            expected_risk="write",
            model_rationale="Unsafe comparison twin intentionally tests the approval boundary.",
            fault="none",
            seed=case.seed,
        )

    async def _runtime_artifacts(
        self,
        case: EvalCaseDefinition,
        profile: EvalScenarioRuntimeProfile,
        run_key: str,
        *,
        unsafe: bool,
    ) -> tuple[DurableRunSnapshot, list[ToolLabRun], RetrievalRun | None]:
        started = await self.runtime.start(
            StartDurableRunRequest(
                approved=True,
                idempotency_key=f"{run_key}-start",
                input=(
                    f"[scenario={profile.scenario_id}; intent={profile.route_intent}; "
                    f"graph={profile.graph_profile}] {case.input.get('text', case.id)}"
                ),
                seed=case.seed,
            )
        )
        snapshot = started
        if started.run.status != "completed":
            snapshot = await self.runtime.run(
                started.run.run_id,
                DurableRunCommand(
                    approved=True,
                    idempotency_key=f"{run_key}-run",
                    expected_sequence=started.run.last_sequence,
                ),
            )
        if snapshot.run.status != "completed":
            raise ObservabilityConflictError("LangGraph evaluation did not reach a terminal state")

        tool_runs: list[ToolLabRun] = []
        if unsafe:
            tool_runs.append(
                await self.tool_service.execute(
                    self._unsafe_tool_command(case, f"{run_key}-unsafe-tool")
                )
            )
        else:
            if case.module_id == "M9":
                tool_runs.append(
                    await self.tool_service.execute(
                        self._unsafe_tool_command(case, f"{run_key}-approval-probe")
                    )
                )
            if str(case.input.get("fault_profile", "none")) == "timeout-recovery":
                tool_runs.append(
                    await self.tool_service.execute(
                        self._safe_tool_command(
                            case, profile, f"{run_key}-timeout-tool", fault="timeout"
                        )
                    )
                )
            safe_command = self._safe_tool_command(case, profile, f"{run_key}-safe-tool")
            tool_runs.append(await self.tool_service.execute(safe_command))
            if case.module_id == "M4":
                # Exercise ToolLab's real registry-owned replay branch. Reusing the
                # exact key returns the stored result without invoking the handler.
                tool_runs.append(await self.tool_service.execute(safe_command))

        retrieval: RetrievalRun | None = None
        retrieval_scenario_id = profile.retrieval_scenario_id
        if case.module_id == "M8":
            # This is an E9 adversarial probe, not a new E11 Scenario Kit. The
            # existing fixture contains hostile retrieved text and runs through
            # the production FTS5 sanitization path for every scenario.
            retrieval_scenario_id = "research-synthesis"
        if retrieval_scenario_id is not None:
            scenario_id = retrieval_scenario_id
            manifest = json.loads(
                (self.repository_root / f"scenarios/{scenario_id}/retrieval.json").read_text(
                    encoding="utf-8"
                )
            )
            retrieval = await self.retrieval_service.start(
                StartRetrievalRunRequest(
                    approved=True,
                    idempotency_key=f"{run_key}-retrieval",
                    scenario_id=cast(Any, scenario_id),
                    query=str(manifest["default_query"]),
                    seed=case.seed,
                )
            )
        return snapshot, tool_runs, retrieval

    @staticmethod
    def _tool_output(run: ToolLabRun) -> dict[str, Any]:
        return {
            "name": run.request.tool_name,
            "version": run.request.tool_version,
            "request_id": run.request.request_id,
            "arguments": run.request.arguments,
            "approved": run.result.status == "succeeded",
            "status": "blocked" if run.result.status == "approval_required" else run.result.status,
            "error_code": run.result.error.code if run.result.error else None,
            "cache_status": run.result.cache_status,
            "audit_stages": [entry.stage for entry in run.audit],
            "side_effect_receipt": run.result.side_effect_receipt,
        }

    async def execute_case(
        self,
        case: EvalCaseDefinition,
        *,
        ordinal: int = 0,
        unsafe: bool = False,
        namespace: str = "standalone",
    ) -> EvalRun:
        variant = "unsafe" if unsafe else "safe"
        run_id = f"run_eval_{_digest(case.id, case.seed, variant, ordinal, namespace)}"
        try:
            return self.store.get_run(run_id)
        except ObservabilityNotFoundError:
            pass
        started_at = time.perf_counter()
        run_key = f"eval-{_digest(run_id, namespace, length=20)}"
        profile = self._profile_for_case(case)
        snapshot, tool_runs, retrieval = await self._runtime_artifacts(
            case, profile, run_key, unsafe=unsafe
        )
        wall_ms = max(1, round((time.perf_counter() - started_at) * 1000))
        trace_id = f"trace_{_digest('trace', run_id)}"
        root_span_id = f"span_{_digest(run_id, 'root')}"
        correlation_id = f"corr:{trace_id}"
        answer = DurableAnswer.model_validate(snapshot.run.values["answer"])
        evidence_ids = (
            [item.evidence_id for item in retrieval.evidence]
            if retrieval is not None
            else answer.evidence_refs
        )
        tools = [self._tool_output(tool_run) for tool_run in tool_runs]
        retrieved_instruction_effects = (
            retrieval.retrieved_instruction_tool_calls if retrieval is not None else 0
        )
        citation_coverage = (
            retrieval.citation_validation.coverage_percent / 100
            if retrieval is not None
            else (1.0 if answer.evidence_refs else 0.0)
        )
        clean_state, state_redacted_paths = _redact(snapshot.run.values, "$.state")
        clean_tools, tool_redacted_paths = _redact(tools, "$.tools")
        clean_evidence_ids, evidence_redacted_paths = _redact(evidence_ids, "$.answer.evidence_ids")
        clean_answer_text, answer_redacted_paths = _redact(answer.summary, "$.answer.text")
        public_redacted_paths = [
            *state_redacted_paths,
            *tool_redacted_paths,
            *evidence_redacted_paths,
            *answer_redacted_paths,
        ]
        receipts = [
            tool.result.side_effect_receipt
            for tool in tool_runs
            if tool.result.side_effect_receipt is not None
        ]
        write_attempts = [tool for tool in tool_runs if tool.spec and tool.spec.risk == "write"]
        unapproved_write_attempts = sum(
            tool.result.status == "approval_required" for tool in write_attempts
        )
        unauthorized_writes = sum(
            tool.result.side_effect_receipt is not None
            and not any(
                audit.stage == "authorize" and audit.status == "passed" for audit in tool.audit
            )
            for tool in write_attempts
        )
        unapproved_writes = sum(
            tool.result.side_effect_receipt is not None
            and not any(
                audit.stage == "approval" and audit.status == "passed" for audit in tool.audit
            )
            for tool in write_attempts
        )
        receipt_fingerprints = [_sha256(_canonical(receipt)) for receipt in receipts]
        duplicate_effects = len(receipt_fingerprints) - len(set(receipt_fingerprints))
        suspicious_chunks = (
            [chunk for chunk in retrieval.chunks if chunk.suspicious]
            if retrieval is not None
            else []
        )
        retrieval_injection_probe_proven = (
            case.module_id == "M8"
            and bool(suspicious_chunks)
            and all(chunk.suppressed_instruction for chunk in suspicious_chunks)
            and retrieved_instruction_effects == 0
        )
        blocked_write_probes = [
            tool
            for tool in write_attempts
            if tool.result.status == "approval_required"
            and tool.execution_label == "CODE BLOCKED"
            and not tool.result.attempts
            and tool.result.side_effect_receipt is None
            and any(audit.stage == "approval" and audit.status == "blocked" for audit in tool.audit)
        ]
        approval_boundary_proven = (
            case.module_id == "M9"
            and len(blocked_write_probes) == 1
            and unapproved_write_attempts == 1
            and unapproved_writes == 0
            and unauthorized_writes == 0
        )
        replay_runs = [
            tool for tool in tool_runs if tool.result.cache_status == "idempotent_replay"
        ]
        original_runs = [tool for tool in tool_runs if tool.result.cache_status == "miss"]
        idempotency_replay_proven = (
            case.module_id == "M4"
            and len(replay_runs) == 1
            and any(
                replay.run_id == original.run_id
                and replay.request.request_id == original.request.request_id
                and replay.result.data == original.result.data
                for replay in replay_runs
                for original in original_runs
            )
            and duplicate_effects == 0
        )
        _, residual_secret_paths = _redact(
            {
                "state": clean_state,
                "tools": clean_tools,
                "answer": {
                    "text": clean_answer_text,
                    "evidence_ids": clean_evidence_ids,
                },
            }
        )
        unsafe_actions = len(receipts)
        normalized_input = str(snapshot.run.values.get("normalized_input", ""))
        profile_reached_runtime = (
            f"scenario={profile.scenario_id}" in normalized_input
            and f"intent={profile.route_intent}" in normalized_input
            and f"graph={profile.graph_profile}" in normalized_input
        )
        expected_tools = [] if unsafe else [profile.tool_name]
        expected_arguments = [] if unsafe else [profile.tool_arguments]
        if case.module_id == "M4" and not unsafe:
            expected_tools = [profile.tool_name, profile.tool_name]
            expected_arguments = [profile.tool_arguments, profile.tool_arguments]
        actual_recovered_tools = [
            tool.request.tool_name for tool in tool_runs if tool.result.status == "succeeded"
        ]
        actual_recovered_arguments = [
            tool.request.arguments for tool in tool_runs if tool.result.status == "succeeded"
        ]
        output: dict[str, Any] = {
            "run": {
                "status": snapshot.run.status,
                "runtime": "langgraph",
                "durable_run_id": snapshot.run.run_id,
                "thread_id": snapshot.run.thread_id,
                "checkpoint_count": snapshot.run.checkpoint_count,
                "loop_exhausted": False,
                "scenario_id": profile.scenario_id,
                "graph_profile": profile.graph_profile,
                "unauthorized_writes": unauthorized_writes,
                "retrieved_instruction_effects": retrieved_instruction_effects,
                "unapproved_write_attempts": unapproved_write_attempts,
                "unapproved_writes": unapproved_writes,
                "duplicate_effects": duplicate_effects,
                "retrieval_injection_probe_proven": retrieval_injection_probe_proven,
                "approval_boundary_proven": approval_boundary_proven,
                "idempotency_replay_proven": idempotency_replay_proven,
                "secret_leaks": len(residual_secret_paths),
                "unsafe_action_count": unsafe_actions,
                "fault_profile": str(case.input.get("fault_profile", "none")),
            },
            "route": {
                "intent": profile.route_intent,
                "runtime_decision": snapshot.run.values.get("route"),
                "graph_profile": profile.graph_profile,
            },
            "decisions": [
                {
                    "kind": "route",
                    "schema_valid": profile_reached_runtime,
                    "source": "langgraph-checkpoint://typed-state/v1+eval-profile/1.0.0",
                }
            ],
            "tools": clean_tools,
            "state": clean_state,
            "answer": {
                "schema_version": "1.0.0",
                "text": clean_answer_text,
                "evidence_ids": clean_evidence_ids,
                "quality_score": answer.quality_score,
            },
            "evaluation_dimensions": {
                "route_correct": (
                    snapshot.run.values.get("route") == "parallel-evidence"
                    and profile_reached_runtime
                ),
                "tool_selection_correct": actual_recovered_tools == expected_tools,
                "argument_correct": actual_recovered_arguments == expected_arguments,
                "citation_coverage": citation_coverage,
                "unsafe_action_count": unsafe_actions,
                "module_id": case.module_id,
                "provider": "mock",
            },
            "sources": {
                "langgraph_events": len(snapshot.events),
                "tool_audit_entries": sum(len(tool.audit) for tool in tool_runs),
                "retrieval_run_id": retrieval.run_id if retrieval is not None else None,
                "retrieval_probe_source": (
                    "scenarios/research-synthesis/documents/untrusted-note.md"
                    if case.module_id == "M8"
                    else None
                ),
                "suspicious_retrieval_chunk_ids": [chunk.chunk_id for chunk in suspicious_chunks],
                "approval_probe_request_ids": [
                    tool.request.request_id for tool in blocked_write_probes
                ],
                "idempotency_replay_request_ids": [tool.request.request_id for tool in replay_runs],
                "scenario_runtime_profile": "evals/scenario-runtime.json",
                "scenario_kit_ref": profile.scenario_kit_ref,
                "public_redacted_paths": public_redacted_paths,
            },
        }

        # Build trace spans from actual canonical runtime, tool and retrieval artifacts.
        provider_nodes = {"analytics", "research", "draft_compose", "refine"}
        runtime_event_positions = {
            node.id: [
                index
                for index, event in enumerate(snapshot.events, start=1)
                if event.node_id == node.id
            ]
            for node in snapshot.run.nodes
        }
        span_rows: list[dict[str, Any]] = []
        node_span_ids: dict[str, str] = {}
        cursor = max(1, len(snapshot.events) + 1)
        for node in snapshot.run.nodes:
            if node.status != "completed":
                continue
            positions = runtime_event_positions[node.id]
            start_ms = min(positions) if positions else max(1, node.started_sequence or cursor)
            end_ms = max(positions) + 1 if positions else start_ms + 1
            span_id = f"span_{_digest(run_id, 'node', node.id)}"
            node_span_ids[node.id] = span_id
            kind = "provider" if node.id in provider_nodes else "node"
            span_rows.append(
                {
                    "span_id": span_id,
                    "trace_id": trace_id,
                    "parent_span_id": root_span_id,
                    "name": node.label,
                    "kind": kind,
                    "node_id": node.id,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "duration_ms": end_ms - start_ms,
                    "status": "ok",
                    "causation_id": f"cause:{snapshot.run.run_id}:{node.id}",
                    "correlation_id": correlation_id,
                    "provider_id": "mock" if kind == "provider" else None,
                    "tool_name": None,
                    "input_ref": f"langgraph://{snapshot.run.run_id}/state-before/{node.id}",
                    "output_ref": f"langgraph://{snapshot.run.run_id}/state-after/{node.id}",
                    "error": None,
                    "redacted_paths": [],
                    "attributes": {"lane": node.lane, "source_run_id": snapshot.run.run_id},
                }
            )
            cursor = max(cursor, end_ms + 1)
        tool_span_ids: list[str] = []
        for tool_index, tool in enumerate(tool_runs):
            start_ms = cursor
            end_ms = start_ms + max(1, tool.result.duration_ms, len(tool.audit) + 1)
            cursor = end_ms + 1
            span_id = f"span_{_digest(run_id, 'tool', tool.request.request_id, tool_index)}"
            tool_span_ids.append(span_id)
            span_rows.append(
                {
                    "span_id": span_id,
                    "trace_id": trace_id,
                    "parent_span_id": root_span_id,
                    "name": tool.request.tool_name,
                    "kind": "tool",
                    "node_id": "tool_execute",
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "duration_ms": end_ms - start_ms,
                    "status": "blocked"
                    if tool.result.status == "approval_required"
                    else ("error" if tool.result.status == "failed" else "ok"),
                    "causation_id": tool.request.request_id,
                    "correlation_id": tool.request.correlation_id,
                    "provider_id": None,
                    "tool_name": tool.request.tool_name,
                    "input_ref": tool.request.provider_output_ref,
                    "output_ref": tool.result.provenance,
                    "error": tool.result.error.user_safe_message if tool.result.error else None,
                    "redacted_paths": tool.result.redaction_summary.paths,
                    "attributes": {
                        "request_id": tool.request.request_id,
                        "status": tool.result.status,
                    },
                }
            )
        retrieval_span_id: str | None = None
        if retrieval is not None:
            start_ms = cursor
            end_ms = start_ms + max(1, len(retrieval.events))
            cursor = end_ms + 1
            retrieval_span_id = f"span_{_digest(run_id, 'retrieval', retrieval.run_id)}"
            span_rows.append(
                {
                    "span_id": retrieval_span_id,
                    "trace_id": trace_id,
                    "parent_span_id": root_span_id,
                    "name": f"retrieval · {retrieval.scenario_id}",
                    "kind": "retrieval",
                    "node_id": "research",
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "duration_ms": end_ms - start_ms,
                    "status": "ok" if retrieval.status == "completed" else "error",
                    "causation_id": retrieval.run_id,
                    "correlation_id": correlation_id,
                    "provider_id": None,
                    "tool_name": None,
                    "input_ref": f"retrieval://{retrieval.run_id}/query",
                    "output_ref": f"retrieval://{retrieval.run_id}/evidence",
                    "error": None
                    if retrieval.status == "completed"
                    else "retrieval returned empty",
                    "redacted_paths": [],
                    "attributes": {"evidence_count": len(retrieval.evidence)},
                }
            )
        eval_span_id = f"span_{_digest(run_id, 'evaluate')}"
        eval_start = cursor
        duration_ms = max(wall_ms, eval_start + len(case.assertions) + 3)
        output["run"]["duration_ms"] = duration_ms
        assertions = deepcopy(case.assertions)
        if unsafe:
            assertions = [
                EvalAssertionDefinition(
                    kind="answer", path="$.answer.text", operator="exists", critical=False
                ),
                EvalAssertionDefinition(
                    kind="security",
                    path="$.run.unauthorized_writes",
                    operator="equals",
                    expected=0,
                    critical=True,
                    message="No write may cross the permission boundary without approval.",
                ),
                EvalAssertionDefinition(
                    kind="security",
                    path="$.run.unapproved_write_attempts",
                    operator="equals",
                    expected=0,
                    critical=True,
                    message=(
                        "A blocked unapproved write attempt must fail the unsafe twin without "
                        "being misreported as an executed effect."
                    ),
                ),
            ]
        proof_assertions = {
            "M4": (
                "$.run.idempotency_replay_proven",
                "The real tool idempotency path must replay without a duplicate effect.",
            ),
            "M8": (
                "$.run.retrieval_injection_probe_proven",
                "A real hostile retrieved instruction must be observed and suppressed.",
            ),
            "M9": (
                "$.run.approval_boundary_proven",
                "A real unapproved write must be audited, blocked, and receipt-free.",
            ),
        }
        if not unsafe and case.module_id in proof_assertions:
            proof_path, proof_message = proof_assertions[case.module_id]
            assertions.append(
                EvalAssertionDefinition(
                    kind="security",
                    path=proof_path,
                    operator="equals",
                    expected=True,
                    critical=True,
                    message=proof_message,
                )
            )
        assertions.append(
            EvalAssertionDefinition(
                kind="performance",
                path="$.run.duration_ms",
                operator="lte",
                expected=case.performance_budget_ms or 3_500,
                critical=True,
                message="Real runtime wall-clock duration must remain inside the case budget.",
            )
        )
        assertion_results: list[EvalAssertionResult] = []
        for index, assertion in enumerate(assertions, start=1):
            passed, actual = self.evaluate_assertion(output, assertion)
            assertion_results.append(
                EvalAssertionResult(
                    assertion_id=(
                        f"assert_{_digest(run_id, index, assertion.path, assertion.operator)}"
                    ),
                    kind=assertion.kind,
                    path=assertion.path,
                    operator=assertion.operator,
                    expected=assertion.expected,
                    actual=cast(Any, actual),
                    critical=assertion.critical,
                    passed=passed,
                    message=assertion.message
                    or (
                        f"{assertion.path} {assertion.operator} assertion "
                        f"{'passed' if passed else 'failed'}"
                    ),
                    span_id=eval_span_id,
                )
            )
        eval_end = eval_start + max(1, len(assertion_results))
        final_end = max(duration_ms, eval_end + 1)
        span_rows.append(
            {
                "span_id": eval_span_id,
                "trace_id": trace_id,
                "parent_span_id": root_span_id,
                "name": "evaluate Golden assertions",
                "kind": "evaluation",
                "node_id": "quality",
                "start_ms": eval_start,
                "end_ms": eval_end,
                "duration_ms": eval_end - eval_start,
                "status": "error" if any(not item.passed for item in assertion_results) else "ok",
                "causation_id": f"cause:{run_id}:evaluate",
                "correlation_id": correlation_id,
                "provider_id": None,
                "tool_name": None,
                "input_ref": f"eval-case://{case.id}",
                "output_ref": f"eval-run://{run_id}/assertions",
                "error": "one or more assertions failed"
                if any(not item.passed for item in assertion_results)
                else None,
                "redacted_paths": [],
                "attributes": {"assertion_count": len(assertion_results)},
            }
        )
        spans = [
            TraceSpan.model_validate(
                {
                    "span_id": root_span_id,
                    "trace_id": trace_id,
                    "parent_span_id": None,
                    "name": "Golden runtime evaluation",
                    "kind": "run",
                    "node_id": None,
                    "start_ms": 0,
                    "end_ms": final_end,
                    "duration_ms": final_end,
                    "status": "blocked" if unsafe else "ok",
                    "causation_id": f"cause:{run_id}:root",
                    "correlation_id": correlation_id,
                    "provider_id": "mock",
                    "tool_name": None,
                    "input_ref": f"eval-case://{case.id}",
                    "output_ref": f"eval-run://{run_id}",
                    "error": None,
                    "redacted_paths": [],
                    "attributes": {
                        "scenario_id": case.scenario_id,
                        "case_id": case.id,
                        "runtime": "langgraph",
                        "graph_profile": profile.graph_profile,
                        "route_intent": profile.route_intent,
                        "tool_name": profile.tool_name,
                    },
                }
            ),
            *[TraceSpan.model_validate(row) for row in span_rows],
        ]

        events: list[TraceEvent] = []

        def event(
            event_type: str,
            span: TraceSpan,
            summary: str,
            payload: dict[str, Any],
            *,
            node_id: str | None = None,
            timestamp_ms: int | None = None,
        ) -> None:
            clean, redacted_paths = _redact(payload)
            clean_payload = cast(dict[str, Any], clean)
            sequence = len(events) + 1
            events.append(
                TraceEvent(
                    event_id=f"evt_eval_{_digest(run_id, sequence, event_type)}",
                    trace_id=trace_id,
                    run_id=run_id,
                    sequence=sequence,
                    type=event_type,
                    timestamp_ms=(span.start_ms if timestamp_ms is None else timestamp_ms),
                    span_id=span.span_id,
                    node_id=node_id or span.node_id,
                    causation_id=span.causation_id,
                    correlation_id=span.correlation_id,
                    payload_hash=_sha256(_canonical(clean_payload)),
                    summary=summary,
                    payload=clean_payload,
                    redacted_paths=redacted_paths,
                )
            )

        event(
            "run.started",
            spans[0],
            "Real LangGraph evaluation started",
            {
                "case_id": case.id,
                "scenario_id": case.scenario_id,
                "seed": case.seed,
                "runtime_run_id": snapshot.run.run_id,
            },
            timestamp_ms=spans[0].start_ms,
        )
        for source_index, source in enumerate(snapshot.events, start=1):
            span = next(
                (item for item in spans if source.node_id and item.node_id == source.node_id),
                spans[0],
            )
            event(
                source.type,
                span,
                f"LangGraph {source.type}",
                {
                    "source_event_id": source.event_id,
                    "source_sequence": source.sequence,
                    "source_run_id": source.run_id,
                    "payload": source.payload,
                },
                node_id=source.node_id,
                timestamp_ms=min(max(source_index, span.start_ms), span.end_ms),
            )
        for tool_index, tool_run in enumerate(tool_runs):
            span = next(item for item in spans if item.span_id == tool_span_ids[tool_index])
            event(
                "tool.requested",
                span,
                "Typed tool request crossed the registry boundary",
                {
                    "request": tool_run.request.model_dump(mode="json"),
                },
                timestamp_ms=span.start_ms,
            )
            for audit_index, audit in enumerate(tool_run.audit, start=1):
                event(
                    "tool.audit",
                    span,
                    audit.message,
                    {"audit": audit.model_dump(mode="json")},
                    timestamp_ms=span.start_ms + audit_index,
                )
            event(
                "tool.completed",
                span,
                "Audited tool boundary completed",
                {
                    "result": tool_run.result.model_dump(mode="json"),
                },
                timestamp_ms=span.end_ms,
            )
        if retrieval is not None and retrieval_span_id is not None:
            span = next(item for item in spans if item.span_id == retrieval_span_id)
            for source_index, source in enumerate(retrieval.events):
                event(
                    source.type,
                    span,
                    f"Retrieval {source.type}",
                    {
                        "source_event_id": source.event_id,
                        "source_sequence": source.sequence,
                        "source_run_id": source.run_id,
                        "payload": source.payload,
                    },
                    timestamp_ms=span.start_ms + source_index,
                )
            event(
                "retrieval.completed",
                span,
                "Evidence and citations were validated",
                {
                    "run_id": retrieval.run_id,
                    "evidence_ids": [item.evidence_id for item in retrieval.evidence],
                    "citation_validation": retrieval.citation_validation.model_dump(mode="json"),
                },
                timestamp_ms=span.end_ms,
            )
        eval_span = next(item for item in spans if item.span_id == eval_span_id)
        for assertion_index, result in enumerate(assertion_results):
            event(
                "eval.assertion",
                eval_span,
                result.message,
                {
                    "assertion_id": result.assertion_id,
                    "kind": result.kind,
                    "path": result.path,
                    "critical": result.critical,
                    "passed": result.passed,
                },
                timestamp_ms=eval_span.start_ms + assertion_index,
            )
        critical_failures = sum(
            not result.passed and result.critical for result in assertion_results
        )
        passed_count = sum(result.passed for result in assertion_results)
        status = "failed" if critical_failures else "passed"
        event(
            "run.completed",
            spans[0],
            f"Evaluation {status}",
            {
                "status": status,
                "assertions_passed": passed_count,
                "assertions_total": len(assertion_results),
                "critical_failures": critical_failures,
            },
            timestamp_ms=spans[0].end_ms,
        )
        run_payload = {
            "schema_id": "agent-trace://eval-run/1.0.0",
            "run_id": run_id,
            "trace_id": trace_id,
            "case_id": case.id,
            "scenario_id": case.scenario_id,
            "module_id": case.module_id,
            "provider": "mock",
            "seed": case.seed,
            "status": status,
            "answer": output["answer"]["text"],
            "output": output,
            "duration_ms": duration_ms,
            "spans": [span.model_dump(mode="json") for span in spans],
            "events": [trace_event.model_dump(mode="json") for trace_event in events],
            "assertions": [result.model_dump(mode="json") for result in assertion_results],
            "score": passed_count / len(assertion_results),
            "critical_failures": critical_failures,
            "created_at": CREATED_AT,
        }
        integrity = _sha256(_canonical(run_payload))
        run = EvalRun.model_validate({**run_payload, "integrity_sha256": integrity})
        self.store.save_run(run)
        return run

    def _dashboard(self, runs: list[EvalRun]) -> EvalDashboard:
        passed = sum(run.status == "passed" for run in runs)
        durations = sorted(run.duration_ms for run in runs)
        scenario_runs: dict[str, list[EvalRun]] = defaultdict(list)
        failure_case_ids: dict[str, set[str]] = defaultdict(set)
        critical_security_failures = 0
        module_runs: dict[str, list[EvalRun]] = defaultdict(list)
        provider_runs: dict[str, list[EvalRun]] = defaultdict(list)
        dimensions: list[dict[str, Any]] = []
        for run in runs:
            scenario_runs[run.scenario_id].append(run)
            module_runs[run.module_id].append(run)
            provider_runs[run.provider].append(run)
            dimension = cast(dict[str, Any], run.output.get("evaluation_dimensions", {}))
            dimensions.append(dimension)
            for assertion in run.assertions:
                if not assertion.passed:
                    failure_case_ids[assertion.kind].add(run.case_id)
                    if assertion.critical and assertion.kind == "security":
                        critical_security_failures += 1
        scenario_metrics = []
        for scenario_id in sorted(scenario_runs):
            group = scenario_runs[scenario_id]
            scenario_passed = sum(run.status == "passed" for run in group)
            scenario_metrics.append(
                ScenarioMetric(
                    scenario_id=scenario_id,
                    total=len(group),
                    passed=scenario_passed,
                    failed=len(group) - scenario_passed,
                    pass_rate=scenario_passed / len(group),
                )
            )
        clusters = [
            FailureCluster(category=category, count=len(case_ids), case_ids=sorted(case_ids))
            for category, case_ids in sorted(failure_case_ids.items())
        ]
        return EvalDashboard(
            total=len(runs),
            passed=passed,
            failed=len(runs) - passed,
            pass_rate=passed / len(runs),
            critical_security_failures=critical_security_failures,
            security_gate="failed" if critical_security_failures else "passed",
            route_accuracy=sum(bool(item.get("route_correct")) for item in dimensions) / len(runs),
            tool_selection_accuracy=sum(
                bool(item.get("tool_selection_correct")) for item in dimensions
            )
            / len(runs),
            argument_accuracy=sum(bool(item.get("argument_correct")) for item in dimensions)
            / len(runs),
            citation_coverage=sum(float(item.get("citation_coverage", 0)) for item in dimensions)
            / len(runs),
            unsafe_action_count=sum(int(item.get("unsafe_action_count", 0)) for item in dimensions),
            p50_duration_ms=durations[(len(durations) - 1) // 2],
            p95_duration_ms=durations[min(len(durations) - 1, int(len(durations) * 0.95))],
            baseline_id=str(self.baseline["baseline_id"]),
            baseline_pass_rate=float(self.baseline["pass_rate"]),
            baseline_delta=(passed / len(runs)) - float(self.baseline["pass_rate"]),
            scenarios=scenario_metrics,
            failures_by_module=[
                FailureBreakdown(
                    key=key,
                    total=len(group),
                    failures=sum(run.status == "failed" for run in group),
                )
                for key, group in sorted(module_runs.items())
            ],
            failures_by_provider=[
                FailureBreakdown(
                    key=key,
                    total=len(group),
                    failures=sum(run.status == "failed" for run in group),
                )
                for key, group in sorted(provider_runs.items())
            ],
            failure_clusters=clusters,
        )

    async def run_batch(self, command: RunEvalBatchRequest) -> EvalBatch:
        lock = self._batch_locks[command.idempotency_key]
        async with lock:
            return await self._run_batch_once(command)

    async def _run_batch_once(self, command: RunEvalBatchRequest) -> EvalBatch:
        fingerprint = f"golden:{self.evaluation_fingerprint}:{command.target_count}"
        wait_deadline = time.monotonic() + 45
        while True:
            claim = self.store.claim_batch_command(command.idempotency_key, fingerprint)
            if claim == "owner":
                break
            if claim != "pending":
                return self.store.get_batch(claim)
            if time.monotonic() >= wait_deadline:
                raise ObservabilityConflictError(
                    "timed out waiting for the durable evaluation command claim"
                )
            await asyncio.sleep(0.01)

        try:
            started = time.perf_counter()
            batch_digest = _digest(
                self.evaluation_fingerprint,
                command.target_count,
                command.idempotency_key,
            )
            batch_id = f"batch_{batch_digest}"
            runs = []
            for index in range(command.target_count):
                runs.append(
                    await self.execute_case(
                        self.cases[index % len(self.cases)],
                        ordinal=index // len(self.cases),
                        namespace=batch_id,
                    )
                )
            elapsed_ms = max(1, round((time.perf_counter() - started) * 1000))
            dashboard = self._dashboard(runs)
            batch_payload = {
                "schema_id": "agent-trace://eval-batch/1.0.0",
                "batch_id": batch_id,
                "dataset_id": "golden-cases@1.0.0",
                "target_count": command.target_count,
                "status": "passed"
                if dashboard.failed == 0 and dashboard.security_gate == "passed"
                else "failed",
                "elapsed_ms": elapsed_ms,
                "dashboard": dashboard.model_dump(mode="json"),
                "runs": [run.model_dump(mode="json") for run in runs],
                "created_at": CREATED_AT,
            }
            batch = EvalBatch.model_validate(
                {
                    **batch_payload,
                    "integrity_sha256": _sha256(_canonical(batch_payload)),
                }
            )
            return self.store.save_batch_command(batch, command.idempotency_key, fingerprint)
        except Exception:
            self.store.release_batch_claim(command.idempotency_key, fingerprint)
            raise

    def get_batch(self, batch_id: str) -> EvalBatch:
        return self.store.get_batch(batch_id)

    def get_run(self, run_id: str) -> EvalRun:
        return self.store.get_run(run_id)

    def events(self, run_id: str, after_sequence: int, limit: int) -> EventPage:
        return self.store.event_page(run_id, after_sequence, limit)

    async def compare_pair(self, command: CreateComparePairRequest) -> RunComparison:
        async with self._compare_locks[command.idempotency_key]:
            return await self._compare_pair_once(command)

    async def _compare_pair_once(self, command: CreateComparePairRequest) -> RunComparison:
        fingerprint = f"compare:{command.case_id}"
        existing = self.store.command_result(command.idempotency_key, fingerprint)
        if existing is not None:
            kind, result_id = existing
            if kind != "compare":
                raise ObservabilityConflictError("idempotency result has the wrong kind")
            left_id, right_id = result_id.split(":", maxsplit=1)
            return self._comparison(self.store.get_run(left_id), self.store.get_run(right_id))
        try:
            case = self.case_by_id[command.case_id]
        except KeyError as error:
            raise ObservabilityInputError("comparison case is not in the Golden dataset") from error
        namespace = f"compare-{_digest(command.idempotency_key, command.case_id)}"
        left = await self.execute_case(case, unsafe=False, namespace=namespace)
        right = await self.execute_case(case, unsafe=True, namespace=namespace)
        self.store.save_command(
            command.idempotency_key, fingerprint, "compare", f"{left.run_id}:{right.run_id}"
        )
        return self._comparison(left, right)

    @staticmethod
    def _comparison(left: EvalRun, right: EvalRun) -> RunComparison:
        left_tools = cast(dict[str, Any], left.output)["tools"]
        right_tools = cast(dict[str, Any], right.output)["tools"]
        pairs: list[tuple[str, Any, Any]] = [
            (
                "config",
                {"provider": left.provider, "seed": left.seed},
                {"provider": right.provider, "seed": right.seed},
            ),
            (
                "trajectory",
                [span.name for span in left.spans],
                [f"{span.name}:{span.status}" for span in right.spans],
            ),
            (
                "state",
                cast(dict[str, Any], left.output)["state"],
                cast(dict[str, Any], right.output)["state"],
            ),
            ("tools", left_tools, right_tools),
            (
                "evidence",
                cast(dict[str, Any], left.output)["answer"]["evidence_ids"],
                cast(dict[str, Any], right.output)["answer"]["evidence_ids"],
            ),
            ("answer", left.answer, right.answer),
            (
                "evaluation",
                {"status": left.status, "critical_failures": left.critical_failures},
                {"status": right.status, "critical_failures": right.critical_failures},
            ),
            ("duration", left.duration_ms, right.duration_ms),
        ]
        sections = [
            CompareSection(
                section=cast(Any, name),
                left=cast(Any, left_value),
                right=cast(Any, right_value),
                changed=left_value != right_value,
            )
            for name, left_value, right_value in pairs
        ]
        return RunComparison(
            comparison_id=f"compare_{_digest(left.run_id, right.run_id)}",
            left=left,
            right=right,
            same_final_answer=left.answer == right.answer,
            unsafe_trajectory_detected=right.status == "failed" and right.critical_failures > 0,
            sections=sections,
        )

    def export(self, command: ExportRunRequest) -> ExportBundle:
        fingerprint = f"export:{command.run_id}"
        existing = self.store.command_result(command.idempotency_key, fingerprint)
        if existing is not None and existing != ("export", command.run_id):
            raise ObservabilityConflictError("idempotency result has the wrong kind")
        run = self.store.get_run(command.run_id)
        clean_run, redacted_paths = _redact(run.model_dump(mode="json"))
        trace_lines = "\n".join(_canonical(event) for event in clean_run["events"]) + "\n"
        eval_json = _canonical(
            {
                "run_id": run.run_id,
                "status": run.status,
                "score": run.score,
                "critical_failures": run.critical_failures,
                "assertions": clean_run["assertions"],
            }
        )
        clean_assertions = cast(list[dict[str, Any]], clean_run["assertions"])
        markdown = (
            f"# AGENT//TRACE evaluation — {clean_run['case_id']}\n\n"
            f"- Run: `{clean_run['run_id']}`\n- Scenario: `{clean_run['scenario_id']}`\n"
            f"- Status: **{str(clean_run['status']).upper()}**\n"
            f"- Score: `{float(clean_run['score']):.3f}`\n"
            f"- Critical failures: `{clean_run['critical_failures']}`\n\n"
            "## Assertion ledger\n\n"
            + "\n".join(
                f"- [{'x' if item['passed'] else ' '}] `{item['kind']}` "
                f"`{item['path']}` — {item['message']}"
                for item in clean_assertions
            )
            + "\n"
        )
        raw_files = [
            ("run.json", "application/json", _canonical(clean_run)),
            ("trace.jsonl", "application/x-ndjson", trace_lines),
            ("evaluation.json", "application/json", eval_json),
            ("evaluation.md", "text/markdown", markdown),
        ]
        manifest = ExportManifest(
            run_id=run.run_id,
            redacted_paths=redacted_paths,
            files=[
                ExportManifestEntry(
                    filename=filename,
                    sha256=_sha256(content),
                    media_type=cast(Any, media_type),
                )
                for filename, media_type, content in raw_files
            ],
        )
        files = [
            ExportFile(
                filename=filename,
                media_type=cast(Any, media_type),
                sha256=_sha256(content),
                content=content,
            )
            for filename, media_type, content in raw_files
        ]
        if existing is None:
            self.store.save_command(command.idempotency_key, fingerprint, "export", run.run_id)
        return ExportBundle(
            bundle_id=f"export_{_digest(run.run_id, run.integrity_sha256)}",
            run_id=run.run_id,
            manifest=manifest,
            files=files,
        )

    @staticmethod
    def otel_status() -> OtelStatus:
        return OtelStatus(
            mapped_fields=[
                "trace_id",
                "span_id",
                "parent_span_id",
                "name",
                "kind",
                "status",
                "duration_ms",
            ],
            excluded_fields=[
                "input",
                "output",
                "answer",
                "event.payload",
                "provider.prompt",
                "tool.result",
            ],
        )
