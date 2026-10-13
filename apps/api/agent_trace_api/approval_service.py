from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import JsonValue, ValidationError

from agent_trace_api.approval_models import (
    ApprovalAuditEntry,
    ApprovalCatalog,
    ApprovalCheckpoint,
    ApprovalDiffEntry,
    ApprovalEffectSummary,
    ApprovalInterrupt,
    ApprovalMatrixEntry,
    ApprovalReceipt,
    ApprovalRun,
    ApprovalScenario,
    ResumeApprovalRequest,
    StalenessRequest,
    StartApprovalRunRequest,
    VersionReferences,
)
from agent_trace_api.models import AgentEvent, RedactionInfo
from agent_trace_api.tool_calling import ActorRole


class ApprovalRunNotFoundError(KeyError):
    pass


class ApprovalConflictError(RuntimeError):
    pass


class ApprovalInputError(ValueError):
    pass


class ApprovalIntegrityError(RuntimeError):
    pass


def _object_schema(properties: dict[str, JsonValue], required: list[str]) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "required": required,
            "properties": properties,
        },
    )


def _scenario_catalog() -> list[ApprovalScenario]:
    currency = cast(
        dict[str, JsonValue],
        {"type": "number", "minimum": 1, "maximum": 5000, "multipleOf": 0.01},
    )
    return [
        ApprovalScenario(
            id="customer-refund",
            label="Customer refund",
            summary="Refund a fictional delivered order after support evidence is reviewed.",
            action="support.issue_refund",
            tool_version="1.0.0",
            target="order://ORD-1042",
            default_args={"order_id": "ORD-1042", "amount_usd": 48.5, "reason": "late delivery"},
            editable_schema=_object_schema(
                {
                    "order_id": {"const": "ORD-1042"},
                    "amount_usd": currency,
                    "reason": {"type": "string", "minLength": 3, "maxLength": 120},
                },
                ["order_id", "amount_usd", "reason"],
            ),
            evidence=["Order ORD-1042 delivered 4 days late", "Refund ceiling: USD 100.00"],
            reason="A refund changes a financial record and requires a human approver.",
            side_effects=[
                "Creates one fictional refund ledger entry",
                "Updates order resolution state",
            ],
            required_permission="support:refund:approve",
        ),
        ApprovalScenario(
            id="purchase-approval",
            label="Purchase approval",
            summary="Approve a fictional software purchase against a bounded cost centre.",
            action="procurement.approve_purchase",
            tool_version="1.0.0",
            target="purchase://PO-2207",
            default_args={"purchase_id": "PO-2207", "amount_usd": 1200.0, "cost_center": "RND-04"},
            editable_schema=_object_schema(
                {
                    "purchase_id": {"const": "PO-2207"},
                    "amount_usd": currency,
                    "cost_center": {"type": "string", "pattern": r"^[A-Z]{3}-[0-9]{2}$"},
                },
                ["purchase_id", "amount_usd", "cost_center"],
            ),
            evidence=["Budget remaining: USD 8,400", "Vendor is on the fictional allowlist"],
            reason="Purchase authorization is a durable financial side effect.",
            side_effects=["Marks PO-2207 approved", "Reserves budget in RND-04"],
            required_permission="procurement:approve",
        ),
        ApprovalScenario(
            id="incident-rollback",
            label="Incident rollback",
            summary="Roll a fictional production service back to an approved release.",
            action="incident.rollback_release",
            tool_version="1.0.0",
            target="service://checkout-api",
            default_args={
                "service": "checkout-api",
                "release": "2026.07.3",
                "reason": "error budget burn",
            },
            editable_schema=_object_schema(
                {
                    "service": {"const": "checkout-api"},
                    "release": {"type": "string", "pattern": r"^2026\.[0-9]{2}\.[0-9]+$"},
                    "reason": {"type": "string", "minLength": 3, "maxLength": 120},
                },
                ["service", "release", "reason"],
            ),
            evidence=["Error rate: 8.2%", "Release 2026.07.3 passed the rollback probe"],
            reason="Rollback changes service state and may interrupt active requests.",
            side_effects=[
                "Moves checkout-api to the selected release",
                "Writes an incident action receipt",
            ],
            required_permission="incident:rollback:approve",
        ),
        ApprovalScenario(
            id="risk-case-write",
            label="Risk-case write",
            summary="Change a fictional risk case only after evidence and ownership review.",
            action="risk.update_case",
            tool_version="1.0.0",
            target="risk-case://RISK-019",
            default_args={
                "case_id": "RISK-019",
                "status": "mitigating",
                "owner": "risk-ops",
                "score": 62,
            },
            editable_schema=_object_schema(
                {
                    "case_id": {"const": "RISK-019"},
                    "status": {"enum": ["open", "mitigating", "closed"]},
                    "owner": {"type": "string", "minLength": 3, "maxLength": 40},
                    "score": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                ["case_id", "status", "owner", "score"],
            ),
            evidence=["Case owner: risk-ops", "Latest review completed 2026-07-28"],
            reason="Risk classification changes require accountable human authorization.",
            side_effects=[
                "Updates the fictional risk register",
                "Appends a governance audit record",
            ],
            required_permission="risk:case:approve",
        ),
    ]


APPROVER_PERMISSIONS = frozenset(
    {
        "support:refund:approve",
        "procurement:approve",
        "incident:rollback:approve",
        "risk:case:approve",
    }
)


class ApprovalService:
    def __init__(
        self,
        database_path: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = database_path.resolve()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._scenarios = {scenario.id: scenario for scenario in _scenario_catalog()}
        for scenario in self._scenarios.values():
            Draft202012Validator.check_schema(scenario.editable_schema)
        self._initialize()

    def _initialize(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS approval_runs (
                    run_id TEXT PRIMARY KEY,
                    scenario_id TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approval_commands (
                    command_key TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    response_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approval_effects (
                    effect_key TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES approval_runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS approval_versions (
                    target TEXT PRIMARY KEY,
                    data_version INTEGER NOT NULL,
                    tool_version TEXT NOT NULL,
                    target_version INTEGER NOT NULL
                );
                """
            )
            for scenario in self._scenarios.values():
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO approval_versions(
                        target, data_version, tool_version, target_version
                    ) VALUES (?, 1, 'registry-rev-1', 1)
                    """,
                    (scenario.target,),
                )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @staticmethod
    def _digest(value: object) -> str:
        material = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _json(model: ApprovalRun) -> str:
        return json.dumps(
            model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )

    def _integrity_material(self, run: ApprovalRun) -> dict[str, object]:
        return cast(
            dict[str, object],
            run.model_dump(mode="json", exclude={"integrity_hash", "command_replayed"}),
        )

    def _seal(self, run: ApprovalRun) -> ApprovalRun:
        run.command_replayed = False
        run.integrity_hash = self._digest(self._integrity_material(run))
        return run

    def _load_row(self, row: sqlite3.Row | None) -> ApprovalRun:
        if row is None:
            raise ApprovalRunNotFoundError("approval run not found")
        try:
            document = json.loads(cast(str, row["state_json"]))
            run = ApprovalRun.model_validate(document)
        except (json.JSONDecodeError, ValidationError, TypeError) as error:
            raise ApprovalIntegrityError("approval checkpoint could not be decoded") from error
        expected = self._digest(self._integrity_material(run))
        if run.integrity_hash != expected:
            raise ApprovalIntegrityError("approval checkpoint integrity hash mismatch")
        return run

    def _load(self, run_id: str) -> ApprovalRun:
        row = self._connection.execute(
            "SELECT state_json FROM approval_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return self._load_row(row)

    def _save(self, run: ApprovalRun) -> None:
        self._seal(run)
        self._connection.execute(
            """
            INSERT INTO approval_runs(run_id, scenario_id, state_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                state_json = excluded.state_json,
                updated_at = excluded.updated_at
            """,
            (run.run_id, run.scenario_id, self._json(run), self._now().isoformat()),
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise RuntimeError("approval clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

    def catalog(self) -> ApprovalCatalog:
        return ApprovalCatalog(
            scenarios=[deepcopy(item) for item in self._scenarios.values()],
            matrix=[
                ApprovalMatrixEntry(
                    risk="read",
                    execution="allowed",
                    explanation="Read-only registered tools may execute after policy checks.",
                ),
                ApprovalMatrixEntry(
                    risk="compute",
                    execution="allowed",
                    explanation="Bounded computation has no external side effect.",
                ),
                ApprovalMatrixEntry(
                    risk="propose",
                    execution="allowed",
                    explanation="Proposals are visible but cannot mutate a target.",
                ),
                ApprovalMatrixEntry(
                    risk="write",
                    execution="approval_required",
                    explanation="An exact, fresh human approval is mandatory.",
                ),
                ApprovalMatrixEntry(
                    risk="restricted",
                    execution="denied",
                    explanation="Restricted tools never execute in the local studio.",
                ),
            ],
        )

    def get(self, run_id: str) -> ApprovalRun:
        with self._lock:
            return deepcopy(self._load(run_id))

    def _versions(self, scenario: ApprovalScenario) -> VersionReferences:
        row = self._connection.execute(
            """
            SELECT data_version, tool_version, target_version
            FROM approval_versions WHERE target = ?
            """,
            (scenario.target,),
        ).fetchone()
        if row is None:
            raise ApprovalIntegrityError("approval version reference is missing")
        return VersionReferences(
            data_version=cast(int, row["data_version"]),
            tool_version=cast(str, row["tool_version"]),
            target_version=cast(int, row["target_version"]),
        )

    def _event(
        self,
        run_id: str,
        sequence: int,
        event_type: str,
        payload: Mapping[str, JsonValue],
        *,
        node_id: str = "approval_gate",
    ) -> AgentEvent:
        digest = self._digest([run_id, sequence, event_type])[:16]
        return AgentEvent(
            event_id=f"evt_hitl_{sequence}_{digest}",
            run_id=run_id,
            sequence=sequence,
            type=event_type,
            timestamp=self._now(),
            node_id=node_id,
            causation_id=f"cmd:{run_id}",
            correlation_id=f"corr_hitl_{run_id.removeprefix('run_')}",
            payload=dict(payload),
            redaction=RedactionInfo(redacted=False, paths=[]),
        )

    def _append_event(
        self,
        run: ApprovalRun,
        event_type: str,
        payload: Mapping[str, JsonValue],
        *,
        node_id: str = "approval_gate",
    ) -> None:
        run.events.append(
            self._event(run.run_id, len(run.events) + 1, event_type, payload, node_id=node_id)
        )

    def _append_audit(
        self,
        run: ApprovalRun,
        *,
        actor_role: str,
        decision: str,
        result: str,
        idempotency_key: str,
        approval_id: str | None,
    ) -> None:
        scenario = self._scenarios[run.scenario_id]
        run.audit.append(
            ApprovalAuditEntry(
                sequence=len(run.audit) + 1,
                actor_role=cast(ActorRole, actor_role),
                action=scenario.action,
                resource=scenario.target,
                decision=decision,
                approval_id=approval_id,
                idempotency_summary=self._digest(idempotency_key)[:12],
                result=result,
                timestamp=self._now(),
                run_id=run.run_id,
            )
        )

    def _checkpoint_hash(self, run: ApprovalRun) -> str:
        interrupt_id = run.interrupt.interrupt_id if run.interrupt else None
        return self._digest(
            {
                "run_id": run.run_id,
                "status": run.status,
                "version": run.checkpoint_version,
                "interrupt_id": interrupt_id,
                "arguments": run.current_arguments,
                "receipt": run.receipt.model_dump(mode="json") if run.receipt else None,
                "last_sequence": len(run.events),
            }
        )

    def _append_checkpoint(self, run: ApprovalRun, reason: str) -> None:
        run.checkpoints.append(
            ApprovalCheckpoint(
                checkpoint_id=f"cp_hitl_{run.run_id.removeprefix('run_')}_{run.checkpoint_version}",
                version=run.checkpoint_version,
                reason=reason,
                state_hash=self._checkpoint_hash(run),
                created_at=self._now(),
            )
        )

    def _interrupt(
        self,
        run: ApprovalRun,
        scenario: ApprovalScenario,
        *,
        evidence: list[str] | None = None,
        suffix: str = "1",
    ) -> ApprovalInterrupt:
        now = self._now()
        return ApprovalInterrupt(
            interrupt_id=f"int_{run.run_id.removeprefix('run_')}_{suffix}",
            title=f"Approve {scenario.label.lower()}",
            exact_action=scenario.action,
            exact_target=scenario.target,
            arguments=deepcopy(run.current_arguments),
            editable_schema=deepcopy(scenario.editable_schema),
            evidence=evidence or deepcopy(scenario.evidence),
            reason=scenario.reason,
            side_effects=deepcopy(scenario.side_effects),
            idempotency_key_summary=self._digest(
                [run.run_id, scenario.action, run.current_arguments]
            )[:12],
            created_at=now,
            expires_at=now + timedelta(minutes=10),
            version_references=self._versions(scenario),
            available_actions=["approve", "reject", "edit", "request_more_evidence"],
        )

    def _command_replay(self, key: str, fingerprint: str) -> ApprovalRun | None:
        row = self._connection.execute(
            "SELECT fingerprint, response_json FROM approval_commands WHERE command_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        if cast(str, row["fingerprint"]) != fingerprint:
            raise ApprovalConflictError("idempotency key was reused with a different command")
        try:
            run = ApprovalRun.model_validate_json(cast(str, row["response_json"]))
        except ValidationError as error:
            raise ApprovalIntegrityError("idempotency response could not be restored") from error
        expected = self._digest(self._integrity_material(run))
        if run.integrity_hash != expected:
            raise ApprovalIntegrityError("idempotency response integrity hash mismatch")
        run.command_replayed = True
        if run.receipt is not None:
            run.receipt.idempotent_replay = True
        run.integrity_hash = self._digest(self._integrity_material(run))
        return run

    def _record_command(self, key: str, fingerprint: str, run: ApprovalRun) -> None:
        self._connection.execute(
            """
            INSERT INTO approval_commands(command_key, fingerprint, response_json)
            VALUES (?, ?, ?)
            """,
            (key, fingerprint, self._json(run)),
        )

    def start(self, command: StartApprovalRunRequest) -> ApprovalRun:
        fingerprint = self._digest(command.model_dump(mode="json", exclude={"idempotency_key"}))
        scoped_key = f"start:{command.idempotency_key}"
        with self._lock, self._connection:
            replay = self._command_replay(scoped_key, fingerprint)
            if replay is not None:
                return replay
            scenario = self._scenarios[command.scenario_id]
            run_digest = self._digest([command.idempotency_key, command.scenario_id, fingerprint])[
                :20
            ]
            run = ApprovalRun(
                run_id=f"run_hitl_{run_digest}",
                scenario_id=command.scenario_id,
                status="interrupted",
                proposer_role=command.proposer_role,
                user_message=command.user_message,
                checkpoint_version=1,
                current_arguments=deepcopy(scenario.default_args),
                interrupt=None,
                outcome_message="Execution is paused before the write boundary.",
                last_outcome="interrupted",
                events=[],
                audit=[],
                checkpoints=[],
                integrity_hash="0" * 64,
            )
            self._append_event(
                run,
                "run.started",
                {
                    "scenario_id": command.scenario_id,
                    "proposer_role": command.proposer_role,
                    "provider": "mock",
                },
                node_id="graph_start",
            )
            self._append_event(
                run,
                "policy.evaluated",
                {
                    "risk": "write",
                    "decision": "approval_required",
                    "prompt_role_claim_ignored": True,
                },
            )
            run.interrupt = self._interrupt(run, scenario)
            self._append_event(
                run,
                "interrupt.created",
                {
                    "interrupt_id": run.interrupt.interrupt_id,
                    "checkpoint_version": run.checkpoint_version,
                    "exact_action": scenario.action,
                },
            )
            self._append_audit(
                run,
                actor_role=command.proposer_role,
                decision="interrupt",
                result="write_blocked_pending_approval",
                idempotency_key=command.idempotency_key,
                approval_id=run.interrupt.interrupt_id,
            )
            self._append_checkpoint(run, "write interrupted before execution")
            self._save(run)
            self._record_command(scoped_key, fingerprint, run)
            return deepcopy(run)

    @staticmethod
    def _diff(
        original: Mapping[str, JsonValue], updated: Mapping[str, JsonValue]
    ) -> list[ApprovalDiffEntry]:
        entries: list[ApprovalDiffEntry] = []
        for key in sorted(set(original) | set(updated)):
            before = original.get(key)
            after = updated.get(key)
            if before != after:
                entries.append(ApprovalDiffEntry(path=f"$.{key}", before=before, after=after))
        return entries

    @staticmethod
    def _validate_arguments(scenario: ApprovalScenario, arguments: dict[str, JsonValue]) -> None:
        errors = sorted(
            Draft202012Validator(
                scenario.editable_schema, format_checker=FormatChecker()
            ).iter_errors(arguments),
            key=lambda error: list(error.path),
        )
        if not errors:
            return
        first = errors[0]
        path = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}" for part in first.path
        )
        raise ApprovalInputError(
            f"edited arguments failed '{first.validator}' validation at {path}"
        )

    def _is_stale(
        self, interrupt: ApprovalInterrupt, scenario: ApprovalScenario
    ) -> tuple[bool, list[str]]:
        current = self._versions(scenario)
        reasons: list[str] = []
        if interrupt.version_references.data_version != current.data_version:
            reasons.append("data version changed")
        if interrupt.version_references.tool_version != current.tool_version:
            reasons.append("tool version changed")
        if interrupt.version_references.target_version != current.target_version:
            reasons.append("target version changed")
        if self._now() >= interrupt.expires_at:
            reasons.append("approval expired")
        return bool(reasons), reasons

    def _renew_for_reapproval(
        self, run: ApprovalRun, scenario: ApprovalScenario, reasons: list[str]
    ) -> None:
        previous = run.interrupt
        if previous is None:
            raise ApprovalIntegrityError("interrupted run is missing its approval payload")
        self._append_event(
            run,
            "interrupt.invalidated",
            {"interrupt_id": previous.interrupt_id, "reasons": cast(JsonValue, reasons)},
        )
        run.checkpoint_version += 1
        evidence = deepcopy(previous.evidence) + [
            f"Freshness check: {reason}" for reason in reasons
        ]
        run.interrupt = self._interrupt(
            run, scenario, evidence=evidence, suffix=str(run.checkpoint_version)
        )
        run.last_outcome = "reapproval_required"
        run.outcome_message = "Context changed; a fresh approval is required before execution."
        self._append_event(
            run,
            "interrupt.created",
            {
                "interrupt_id": run.interrupt.interrupt_id,
                "checkpoint_version": run.checkpoint_version,
                "reason": "reapproval_required",
            },
        )
        self._append_checkpoint(run, "stale approval replaced with fresh interrupt")

    def _execute_write(
        self,
        run: ApprovalRun,
        scenario: ApprovalScenario,
        decision: str,
    ) -> ApprovalReceipt:
        if run.interrupt is None:
            raise ApprovalIntegrityError("approval interrupt was lost before execution")
        effect_key = f"effect:{run.interrupt.idempotency_key_summary}"
        receipt_id = f"receipt_{self._digest([effect_key, run.current_arguments])[:20]}"
        receipt = ApprovalReceipt(
            receipt_id=receipt_id,
            action=scenario.action,
            target=scenario.target,
            arguments=deepcopy(run.current_arguments),
            effect_key=effect_key,
            committed_at=self._now(),
            checkpoint_version=run.checkpoint_version + 1,
        )
        cursor = self._connection.execute(
            """
            INSERT OR IGNORE INTO approval_effects(effect_key, run_id, receipt_json)
            VALUES (?, ?, ?)
            """,
            (effect_key, run.run_id, receipt.model_dump_json()),
        )
        if cursor.rowcount != 1:
            row = self._connection.execute(
                "SELECT receipt_json FROM approval_effects WHERE effect_key = ?", (effect_key,)
            ).fetchone()
            if row is None:
                raise ApprovalIntegrityError("side-effect receipt could not be restored")
            restored = ApprovalReceipt.model_validate_json(cast(str, row["receipt_json"]))
            restored.idempotent_replay = True
            return restored
        self._connection.execute(
            "UPDATE approval_versions SET target_version = target_version + 1 WHERE target = ?",
            (scenario.target,),
        )
        self._append_event(
            run,
            "tool.completed",
            {
                "action": scenario.action,
                "receipt_id": receipt.receipt_id,
                "explicit_approval": True,
                "decision": decision,
            },
            node_id="tool_execute",
        )
        return receipt

    def resume(self, run_id: str, command: ResumeApprovalRequest) -> ApprovalRun:
        fingerprint = self._digest(command.model_dump(mode="json", exclude={"idempotency_key"}))
        scoped_key = f"resume:{command.idempotency_key}"
        with self._lock, self._connection:
            replay = self._command_replay(scoped_key, fingerprint)
            if replay is not None:
                return replay
            run = self._load(run_id)
            if run.status != "interrupted" or run.interrupt is None:
                raise ApprovalConflictError("only interrupted runs can accept a new decision")
            if command.expected_checkpoint_version != run.checkpoint_version:
                raise ApprovalConflictError(
                    f"checkpoint version conflict: expected {command.expected_checkpoint_version}, "
                    f"current {run.checkpoint_version}"
                )
            if command.interrupt_id != run.interrupt.interrupt_id:
                raise ApprovalConflictError("interrupt id does not match the current checkpoint")
            scenario = self._scenarios[run.scenario_id]

            if (
                command.actor_role != "approver"
                or scenario.required_permission not in APPROVER_PERMISSIONS
            ):
                run.last_outcome = "permission_denied"
                run.outcome_message = (
                    "Code-derived actor permissions denied the decision; "
                    "prompt text cannot grant access."
                )
                self._append_event(
                    run,
                    "approval.denied",
                    {"actor_role": command.actor_role, "reason": "permission_denied"},
                )
                self._append_audit(
                    run,
                    actor_role=command.actor_role,
                    decision=command.decision,
                    result="permission_denied",
                    idempotency_key=command.idempotency_key,
                    approval_id=run.interrupt.interrupt_id,
                )
                self._save(run)
                self._record_command(scoped_key, fingerprint, run)
                return deepcopy(run)

            stale, stale_reasons = self._is_stale(run.interrupt, scenario)
            if stale:
                previous_id = run.interrupt.interrupt_id
                self._renew_for_reapproval(run, scenario, stale_reasons)
                self._append_audit(
                    run,
                    actor_role=command.actor_role,
                    decision=command.decision,
                    result="reapproval_required",
                    idempotency_key=command.idempotency_key,
                    approval_id=previous_id,
                )
                self._save(run)
                self._record_command(scoped_key, fingerprint, run)
                return deepcopy(run)

            if command.decision == "request_more_evidence":
                previous_id = run.interrupt.interrupt_id
                run.checkpoint_version += 1
                evidence = deepcopy(run.interrupt.evidence) + [
                    f"Verified target snapshot v{run.interrupt.version_references.target_version}",
                    "Policy owner confirmed the fictional simulation boundary",
                ]
                self._append_event(
                    run,
                    "evidence.requested",
                    {"interrupt_id": previous_id, "actor_role": command.actor_role},
                )
                run.interrupt = self._interrupt(
                    run, scenario, evidence=evidence, suffix=str(run.checkpoint_version)
                )
                run.last_outcome = "evidence_added"
                run.outcome_message = (
                    "Additional local evidence was attached; execution remains paused."
                )
                self._append_event(
                    run,
                    "evidence.added",
                    {"evidence_count": len(evidence), "source": "fictional://approval/evidence"},
                )
                self._append_event(
                    run,
                    "interrupt.created",
                    {
                        "interrupt_id": run.interrupt.interrupt_id,
                        "checkpoint_version": run.checkpoint_version,
                        "reason": "evidence_added",
                    },
                )
                self._append_audit(
                    run,
                    actor_role=command.actor_role,
                    decision=command.decision,
                    result="evidence_added_write_still_blocked",
                    idempotency_key=command.idempotency_key,
                    approval_id=previous_id,
                )
                self._append_checkpoint(run, "additional evidence attached")
                self._save(run)
                self._record_command(scoped_key, fingerprint, run)
                return deepcopy(run)

            if command.decision == "reject":
                approval_id = run.interrupt.interrupt_id
                run.checkpoint_version += 1
                run.status = "rejected"
                run.interrupt = None
                run.last_outcome = "rejected"
                run.outcome_message = "The human rejected the write; no side effect was executed."
                self._append_event(
                    run,
                    "approval.rejected",
                    {"approval_id": approval_id, "safe_terminal": True},
                )
                self._append_event(
                    run, "run.rejected", {"side_effect_count": 0}, node_id="graph_end"
                )
                self._append_audit(
                    run,
                    actor_role=command.actor_role,
                    decision=command.decision,
                    result="rejected_no_side_effect",
                    idempotency_key=command.idempotency_key,
                    approval_id=approval_id,
                )
                self._append_checkpoint(run, "write rejected at human boundary")
                self._save(run)
                self._record_command(scoped_key, fingerprint, run)
                return deepcopy(run)

            original = deepcopy(run.current_arguments)
            if command.decision == "edit":
                edited = deepcopy(command.edited_arguments or {})
                self._validate_arguments(scenario, edited)
                run.current_arguments = edited
                run.argument_diff = self._diff(original, edited)
                self._append_event(
                    run,
                    "approval.edited",
                    {"changed_paths": [entry.path for entry in run.argument_diff]},
                )
            else:
                self._validate_arguments(scenario, run.current_arguments)
                run.argument_diff = []

            approval_id = run.interrupt.interrupt_id
            self._append_event(
                run,
                "approval.decided",
                {
                    "approval_id": approval_id,
                    "decision": command.decision,
                    "actor_role": command.actor_role,
                },
            )
            receipt = self._execute_write(run, scenario, command.decision)
            run.checkpoint_version += 1
            run.status = "completed"
            run.interrupt = None
            run.receipt = receipt
            run.last_outcome = "edited_and_approved" if command.decision == "edit" else "approved"
            run.outcome_message = "The approved fictional side effect committed exactly once."
            self._append_event(
                run,
                "interrupt.resolved",
                {"approval_id": approval_id, "receipt_id": receipt.receipt_id},
            )
            self._append_event(
                run,
                "run.completed",
                {"receipt_id": receipt.receipt_id, "side_effect_count": 1},
                node_id="graph_end",
            )
            self._append_audit(
                run,
                actor_role=command.actor_role,
                decision=command.decision,
                result="write_committed_once",
                idempotency_key=command.idempotency_key,
                approval_id=approval_id,
            )
            self._append_checkpoint(run, "approved side effect committed")
            self._save(run)
            self._record_command(scoped_key, fingerprint, run)
            return deepcopy(run)

    def make_stale(self, run_id: str, command: StalenessRequest) -> ApprovalRun:
        fingerprint = self._digest(command.model_dump(mode="json", exclude={"idempotency_key"}))
        scoped_key = f"stale:{command.idempotency_key}"
        with self._lock, self._connection:
            replay = self._command_replay(scoped_key, fingerprint)
            if replay is not None:
                return replay
            run = self._load(run_id)
            if run.status != "interrupted" or run.interrupt is None:
                raise ApprovalConflictError(
                    "staleness can be simulated only for an interrupted run"
                )
            if command.expected_checkpoint_version != run.checkpoint_version:
                raise ApprovalConflictError(
                    f"checkpoint version conflict: expected {command.expected_checkpoint_version}, "
                    f"current {run.checkpoint_version}"
                )
            scenario = self._scenarios[run.scenario_id]
            if command.dimension == "data":
                self._connection.execute(
                    """
                    UPDATE approval_versions
                    SET data_version = data_version + 1, target_version = target_version + 1
                    WHERE target = ?
                    """,
                    (scenario.target,),
                )
                run.outcome_message = (
                    "The target data changed after this approval card was created."
                )
            elif command.dimension == "tool":
                current = self._versions(scenario).tool_version
                revision = int(current.rsplit("-", 1)[-1]) + 1
                self._connection.execute(
                    "UPDATE approval_versions SET tool_version = ? WHERE target = ?",
                    (f"registry-rev-{revision}", scenario.target),
                )
                run.outcome_message = "The registered tool policy changed after interruption."
            else:
                run.interrupt.expires_at = self._now() - timedelta(seconds=1)
                run.outcome_message = "The approval window expired before a decision was submitted."
            self._save(run)
            self._record_command(scoped_key, fingerprint, run)
            return deepcopy(run)

    def effects(self, run_id: str) -> ApprovalEffectSummary:
        with self._lock:
            self._load(run_id)
            rows = self._connection.execute(
                "SELECT receipt_json FROM approval_effects WHERE run_id = ? ORDER BY rowid",
                (run_id,),
            ).fetchall()
            receipts = [
                ApprovalReceipt.model_validate_json(cast(str, row["receipt_json"])) for row in rows
            ]
            return ApprovalEffectSummary(
                run_id=run_id,
                effect_count=len(receipts),
                latest_receipt=receipts[-1] if receipts else None,
            )
