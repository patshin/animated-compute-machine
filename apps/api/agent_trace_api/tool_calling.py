from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from pydantic import Field, JsonValue, TypeAdapter

from agent_trace_api.models import ApprovedCommand, RunId, StrictModel

ToolRisk = Literal["read", "compute", "propose", "write", "restricted"]
ActorRole = Literal["learner", "reviewer", "approver", "system"]
FaultId = Literal[
    "none",
    "unknown_tool",
    "missing_argument",
    "invalid_argument",
    "permission_denied",
    "timeout",
    "empty",
    "invalid_output",
]
ToolErrorCode = Literal[
    "TOOL_NOT_FOUND",
    "TOOL_VERSION_UNSUPPORTED",
    "ARGUMENT_MISSING",
    "ARGUMENT_SCHEMA_INVALID",
    "ENTITY_UNRESOLVED",
    "PERMISSION_DENIED",
    "RISK_MISMATCH",
    "APPROVAL_REQUIRED",
    "TIMEOUT",
    "EMPTY_RESULT",
    "OUTPUT_SCHEMA_INVALID",
    "DUPLICATE_REQUEST",
    "SIDE_EFFECT_UNKNOWN",
    "INTERNAL_ERROR",
]
AuditStage = Literal[
    "resolve",
    "input_validation",
    "authorize",
    "policy",
    "approval",
    "idempotency",
    "execute",
    "output_validation",
    "redact",
    "audit",
]

CONTRACTS_DIRECTORY = Path(__file__).resolve().parents[3] / "contracts"
TOOL_SPEC_CONTRACT_PATH = CONTRACTS_DIRECTORY / "tool-spec.schema.json"
MAX_ARGUMENT_NODES = 2_000
MAX_ARGUMENT_DEPTH = 32


class ToolRetryPolicy(StrictModel):
    max_attempts: int = Field(ge=1, le=5)
    retryable_errors: list[str]
    backoff_ms: int = Field(default=0, ge=0, le=30_000)


class ToolIdempotencyPolicy(StrictModel):
    mode: Literal["not_applicable", "request_key", "business_key"]
    key_fields: list[str] = Field(default_factory=list)


class ToolErrorDefinition(StrictModel):
    code: str = Field(pattern=r"^[A-Z0-9_]+$")
    retryable: bool
    user_message: str = Field(min_length=1)


class ToolSpec(StrictModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    description: str = Field(min_length=12, max_length=500)
    risk: ToolRisk
    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue]
    permissions: list[str]
    timeout_ms: int = Field(ge=50, le=120_000)
    retry: ToolRetryPolicy
    idempotency: ToolIdempotencyPolicy
    redact: list[str]
    errors: list[ToolErrorDefinition]
    explanation: str | None = None


class ToolCatalogEntry(StrictModel):
    domain: Literal["customer-support", "data-analyst"]
    spec: ToolSpec
    example_arguments: dict[str, JsonValue]


class ToolFaultCase(StrictModel):
    id: FaultId
    label: str
    stage: AuditStage | Literal["none"]
    expected_error: ToolErrorCode | None = None
    description: str


class ToolCatalogResponse(StrictModel):
    schema_id: Literal["agent-trace://tool-catalog/1.0.0"] = "agent-trace://tool-catalog/1.0.0"
    tools: list[ToolCatalogEntry]
    faults: list[ToolFaultCase]


class ToolRunCommand(ApprovedCommand):
    tool_name: str = Field(
        min_length=3,
        max_length=120,
        pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$",
    )
    tool_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    arguments: dict[str, JsonValue]
    actor_role: ActorRole
    expected_risk: ToolRisk
    model_rationale: str = Field(min_length=3, max_length=500)
    fault: FaultId = "none"
    seed: int = Field(default=2026, ge=0, le=2_147_483_647)


class ToolRequest(StrictModel):
    schema_id: Literal["agent-trace://tool-request/1.0.0"] = "agent-trace://tool-request/1.0.0"
    request_id: str = Field(pattern=r"^toolreq_[A-Za-z0-9_-]+$")
    run_id: RunId
    node_id: Literal["tool_execute"] = "tool_execute"
    tool_name: str
    tool_version: str
    arguments: dict[str, JsonValue]
    actor_role: ActorRole
    granted_permissions: list[str]
    model_rationale: str
    requested_at: datetime
    expected_risk: ToolRisk
    correlation_id: str = Field(pattern=r"^corr_[A-Za-z0-9_-]+$")
    provider_output_ref: str = Field(pattern=r"^mock-provider://tool-proposal/[A-Za-z0-9._-]+$")


class ToolValidationIssue(StrictModel):
    path: str
    keyword: str
    message: str


class ToolExecutionError(StrictModel):
    code: ToolErrorCode
    stage: AuditStage
    retryable: bool
    user_safe_message: str
    issues: list[ToolValidationIssue] = Field(default_factory=list)


class ToolAttempt(StrictModel):
    attempt: int = Field(ge=1, le=5)
    status: Literal["succeeded", "failed"]
    error_code: ToolErrorCode | None = None
    duration_ms: int = Field(ge=0)


class RedactionSummary(StrictModel):
    applied: bool
    paths: list[str]
    replacement: Literal["[REDACTED]"] = "[REDACTED]"


class ApprovalRequirement(StrictModel):
    status: Literal["required"] = "required"
    exact_action: str
    exact_target: str
    args_diff: dict[str, JsonValue]
    evidence: list[str]
    reason: str
    risk: Literal["write"] = "write"
    side_effects: list[str]
    idempotency_key_summary: str
    expires_at: datetime
    available_actions: list[Literal["approve", "reject", "edit", "request_evidence"]]
    implementation_status: Literal["locked_until_e7"] = "locked_until_e7"


class ToolAuditEntry(StrictModel):
    sequence: int = Field(ge=1)
    stage: AuditStage
    status: Literal["passed", "failed", "blocked", "skipped"]
    policy_version: Literal["agent-trace://tool-policy/1.0.0"] = "agent-trace://tool-policy/1.0.0"
    actor_role: ActorRole
    action: str
    resource: str
    run_id: RunId
    correlation_id: str
    message: str
    timestamp: datetime


class ToolResult(StrictModel):
    schema_id: Literal["agent-trace://tool-result/1.0.0"] = "agent-trace://tool-result/1.0.0"
    request_id: str
    status: Literal["succeeded", "failed", "approval_required"]
    data: JsonValue | None = None
    error: ToolExecutionError | None = None
    started_at: datetime
    ended_at: datetime
    duration_ms: int = Field(ge=0)
    attempts: list[ToolAttempt]
    cache_status: Literal["miss", "idempotent_replay"]
    side_effect_receipt: JsonValue | None = None
    provenance: str
    redaction_summary: RedactionSummary
    approval: ApprovalRequirement | None = None


class ToolLabRun(StrictModel):
    schema_id: Literal["agent-trace://tool-lab-run/1.0.0"] = "agent-trace://tool-lab-run/1.0.0"
    run_id: RunId
    status: Literal["succeeded", "failed", "approval_required"]
    proposal_label: Literal["LLM PROPOSED"] = "LLM PROPOSED"
    execution_label: Literal["CODE EXECUTED", "CODE BLOCKED"]
    spec: ToolSpec | None
    request: ToolRequest
    result: ToolResult
    audit: list[ToolAuditEntry]


class ToolCallingConflictError(RuntimeError):
    pass


class ToolLabRunNotFoundError(KeyError):
    pass


@dataclass(frozen=True)
class ToolExecutionContext:
    fault: FaultId
    seed: int
    timeout_ms: int
    attempt: int


ToolHandler = Callable[[dict[str, JsonValue], ToolExecutionContext], Awaitable[JsonValue | None]]


@dataclass(frozen=True)
class ToolRegistration:
    domain: Literal["customer-support", "data-analyst"]
    spec: ToolSpec
    example_arguments: dict[str, JsonValue]
    handler: ToolHandler


class ToolRegistry:
    def __init__(self, tool_spec_contract: Mapping[str, object]) -> None:
        self._tool_spec_validator = Draft202012Validator(tool_spec_contract)
        self._registrations: dict[tuple[str, str], ToolRegistration] = {}

    def register(self, registration: ToolRegistration) -> None:
        document = registration.spec.model_dump(mode="json", exclude_none=True)
        errors = sorted(
            self._tool_spec_validator.iter_errors(document), key=lambda error: list(error.path)
        )
        if errors:
            raise RuntimeError(f"tool spec failed the canonical contract: {errors[0].message}")
        try:
            Draft202012Validator.check_schema(registration.spec.input_schema)
            Draft202012Validator.check_schema(registration.spec.output_schema)
        except SchemaError as error:
            raise RuntimeError("registered tool contains an invalid JSON Schema") from error
        if any(
            not path.startswith("$.") or path.count(".") != 1 for path in registration.spec.redact
        ):
            raise RuntimeError("v0.1 tool redaction paths must target one top-level property")
        key = (registration.spec.name, registration.spec.version)
        if key in self._registrations:
            raise RuntimeError(
                f"tool already registered: {registration.spec.name}@{registration.spec.version}"
            )
        self._registrations[key] = registration

    def resolve(self, name: str, version: str) -> ToolRegistration:
        registration = self._registrations.get((name, version))
        if registration is not None:
            return registration
        if any(registered_name == name for registered_name, _ in self._registrations):
            raise ToolExecutorError(
                "TOOL_VERSION_UNSUPPORTED",
                "resolve",
                False,
                "The requested tool version is not registered.",
            )
        raise ToolExecutorError(
            "TOOL_NOT_FOUND",
            "resolve",
            False,
            "The provider proposed a tool outside the registry allowlist.",
        )

    def catalog(self) -> list[ToolCatalogEntry]:
        return [
            ToolCatalogEntry(
                domain=registration.domain,
                spec=deepcopy(registration.spec),
                example_arguments=deepcopy(registration.example_arguments),
            )
            for registration in self._registrations.values()
        ]


class ToolExecutorError(RuntimeError):
    def __init__(
        self,
        code: ToolErrorCode,
        stage: AuditStage,
        retryable: bool,
        safe_message: str,
        issues: list[ToolValidationIssue] | None = None,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.stage = stage
        self.retryable = retryable
        self.safe_message = safe_message
        self.issues = issues or []


@dataclass(frozen=True)
class _CommandRecord:
    fingerprint: str
    run: ToolLabRun


def _load_tool_spec_contract() -> dict[str, object]:
    path = TOOL_SPEC_CONTRACT_PATH.resolve()
    if path.parent != CONTRACTS_DIRECTORY.resolve():
        raise RuntimeError("tool spec contract escaped the repository contracts directory")
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("canonical tool spec contract must be an object")
    Draft202012Validator.check_schema(raw)
    return cast(dict[str, object], raw)


def _error_definitions(*codes: ToolErrorCode) -> list[ToolErrorDefinition]:
    retryable = {"TIMEOUT"}
    messages: dict[ToolErrorCode, str] = {
        "TOOL_NOT_FOUND": "The tool is not registered.",
        "TOOL_VERSION_UNSUPPORTED": "The tool version is not registered.",
        "ARGUMENT_MISSING": "A required tool argument is missing.",
        "ARGUMENT_SCHEMA_INVALID": "Tool arguments do not match the input contract.",
        "ENTITY_UNRESOLVED": "The requested fictional entity was not found.",
        "PERMISSION_DENIED": "The actor lacks the required permission.",
        "RISK_MISMATCH": "The proposed risk does not match the registry.",
        "APPROVAL_REQUIRED": "A write action requires explicit human approval.",
        "TIMEOUT": "The read tool exceeded its bounded timeout.",
        "EMPTY_RESULT": "The tool returned no result.",
        "OUTPUT_SCHEMA_INVALID": "The tool output failed its contract.",
        "DUPLICATE_REQUEST": "The request was already handled.",
        "SIDE_EFFECT_UNKNOWN": "The write side effect could not be confirmed.",
        "INTERNAL_ERROR": "The executor stopped at an internal boundary.",
    }
    return [
        ToolErrorDefinition(
            code=code,
            retryable=code in retryable,
            user_message=messages[code],
        )
        for code in codes
    ]


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


def _tool_specs() -> list[
    tuple[Literal["customer-support", "data-analyst"], ToolSpec, dict[str, JsonValue]]
]:
    common_codes: tuple[ToolErrorCode, ...] = (
        "ARGUMENT_MISSING",
        "ARGUMENT_SCHEMA_INVALID",
        "PERMISSION_DENIED",
        "RISK_MISMATCH",
        "TIMEOUT",
        "EMPTY_RESULT",
        "OUTPUT_SCHEMA_INVALID",
        "INTERNAL_ERROR",
    )
    return [
        (
            "customer-support",
            ToolSpec(
                name="orders.get_status",
                version="1.0.0",
                description="Read one fictional order status by its stable order identifier.",
                risk="read",
                input_schema=_object_schema(
                    {"order_id": {"type": "string", "pattern": r"^ORD-[0-9]{4}$"}},
                    ["order_id"],
                ),
                output_schema=_object_schema(
                    {
                        "order_id": {"type": "string"},
                        "status": {"enum": ["processing", "shipped", "delivered"]},
                        "customer_name": {"type": "string"},
                        "customer_email": {"type": "string"},
                        "last_update": {"type": "string", "format": "date-time"},
                        "source": {"const": "fictional://support/orders"},
                    },
                    [
                        "order_id",
                        "status",
                        "customer_name",
                        "customer_email",
                        "last_update",
                        "source",
                    ],
                ),
                permissions=["orders:read"],
                timeout_ms=60,
                retry=ToolRetryPolicy(max_attempts=2, retryable_errors=["TIMEOUT"], backoff_ms=0),
                idempotency=ToolIdempotencyPolicy(mode="not_applicable"),
                redact=["$.customer_email"],
                errors=_error_definitions("ENTITY_UNRESOLVED", *common_codes),
                explanation=(
                    "Provider proposes an order id; code resolves, validates, "
                    "authorizes and redacts."
                ),
            ),
            {"order_id": "ORD-1042"},
        ),
        (
            "data-analyst",
            ToolSpec(
                name="analytics.query_metric",
                version="1.0.0",
                description="Read a deterministic fictional metric series for bounded periods.",
                risk="read",
                input_schema=_object_schema(
                    {
                        "metric": {"enum": ["revenue", "tickets"]},
                        "periods": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 4,
                            "uniqueItems": True,
                            "items": {"enum": ["2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4"]},
                        },
                    },
                    ["metric", "periods"],
                ),
                output_schema=_object_schema(
                    {
                        "metric": {"enum": ["revenue", "tickets"]},
                        "rows": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 4,
                            "items": _object_schema(
                                {
                                    "period": {"type": "string"},
                                    "value": {"type": "number"},
                                    "unit": {"enum": ["USD millions", "tickets"]},
                                },
                                ["period", "value", "unit"],
                            ),
                        },
                        "source": {"const": "fictional://analytics/metrics"},
                    },
                    ["metric", "rows", "source"],
                ),
                permissions=["analytics:read"],
                timeout_ms=80,
                retry=ToolRetryPolicy(max_attempts=2, retryable_errors=["TIMEOUT"], backoff_ms=0),
                idempotency=ToolIdempotencyPolicy(mode="not_applicable"),
                redact=[],
                errors=_error_definitions(*common_codes),
                explanation="The deterministic data tool exposes units and fictional provenance.",
            ),
            {"metric": "revenue", "periods": ["2025-Q1", "2025-Q4"]},
        ),
        (
            "customer-support",
            ToolSpec(
                name="orders.propose_address_change",
                version="1.0.0",
                description="Prepare an address-change proposal without mutating any order record.",
                risk="propose",
                input_schema=_object_schema(
                    {
                        "order_id": {"type": "string", "pattern": r"^ORD-[0-9]{4}$"},
                        "new_city": {"type": "string", "minLength": 2, "maxLength": 80},
                    },
                    ["order_id", "new_city"],
                ),
                output_schema=_object_schema(
                    {
                        "proposal_id": {"type": "string", "pattern": r"^proposal_[a-f0-9]{12}$"},
                        "exact_action": {"const": "change_shipping_city"},
                        "target": {"type": "string"},
                        "summary": {"type": "string"},
                        "side_effects": {"type": "array", "items": {"type": "string"}},
                        "source": {"const": "fictional://support/proposals"},
                    },
                    ["proposal_id", "exact_action", "target", "summary", "side_effects", "source"],
                ),
                permissions=["orders:propose"],
                timeout_ms=80,
                retry=ToolRetryPolicy(max_attempts=1, retryable_errors=[]),
                idempotency=ToolIdempotencyPolicy(mode="not_applicable"),
                redact=[],
                errors=_error_definitions(
                    "ARGUMENT_MISSING",
                    "ARGUMENT_SCHEMA_INVALID",
                    "PERMISSION_DENIED",
                    "RISK_MISMATCH",
                    "EMPTY_RESULT",
                    "OUTPUT_SCHEMA_INVALID",
                    "INTERNAL_ERROR",
                ),
                explanation="A proposal is visible and auditable but has zero side effects.",
            ),
            {"order_id": "ORD-1042", "new_city": "Suzhou"},
        ),
        (
            "customer-support",
            ToolSpec(
                name="orders.execute_address_change",
                version="1.0.0",
                description=(
                    "Simulate a protected address write that remains locked until E7 approval."
                ),
                risk="write",
                input_schema=_object_schema(
                    {
                        "proposal_id": {"type": "string", "pattern": r"^proposal_[a-f0-9]{12}$"},
                        "order_id": {"type": "string", "pattern": r"^ORD-[0-9]{4}$"},
                        "new_city": {"type": "string", "minLength": 2, "maxLength": 80},
                    },
                    ["proposal_id", "order_id", "new_city"],
                ),
                output_schema=_object_schema(
                    {
                        "receipt_id": {"type": "string"},
                        "status": {"const": "simulated"},
                    },
                    ["receipt_id", "status"],
                ),
                permissions=["orders:write"],
                timeout_ms=80,
                retry=ToolRetryPolicy(max_attempts=1, retryable_errors=[]),
                idempotency=ToolIdempotencyPolicy(
                    mode="request_key", key_fields=["proposal_id", "order_id", "new_city"]
                ),
                redact=[],
                errors=_error_definitions(
                    "ARGUMENT_MISSING",
                    "ARGUMENT_SCHEMA_INVALID",
                    "PERMISSION_DENIED",
                    "RISK_MISMATCH",
                    "APPROVAL_REQUIRED",
                    "DUPLICATE_REQUEST",
                    "SIDE_EFFECT_UNKNOWN",
                    "INTERNAL_ERROR",
                ),
                explanation="The handler is registered but unreachable before typed E7 approval.",
            ),
            {
                "proposal_id": "proposal_5e80ea83b104",
                "order_id": "ORD-1042",
                "new_city": "Suzhou",
            },
        ),
    ]


FAULTS = [
    ToolFaultCase(
        id="none",
        label="Nominal path",
        stage="none",
        description="Use the selected registered tool and arguments.",
    ),
    ToolFaultCase(
        id="unknown_tool",
        label="Unknown tool",
        stage="resolve",
        expected_error="TOOL_NOT_FOUND",
        description="Provider proposes a name outside the registry allowlist.",
    ),
    ToolFaultCase(
        id="missing_argument",
        label="Missing argument",
        stage="input_validation",
        expected_error="ARGUMENT_MISSING",
        description="A required argument is absent from the typed request.",
    ),
    ToolFaultCase(
        id="invalid_argument",
        label="Invalid argument",
        stage="input_validation",
        expected_error="ARGUMENT_SCHEMA_INVALID",
        description="An argument exists but violates its JSON Schema.",
    ),
    ToolFaultCase(
        id="permission_denied",
        label="Permission denied",
        stage="authorize",
        expected_error="PERMISSION_DENIED",
        description="The actor context cannot be elevated by provider rationale.",
    ),
    ToolFaultCase(
        id="timeout",
        label="Timeout + retry",
        stage="execute",
        expected_error="TIMEOUT",
        description="A read call times out twice and then stops.",
    ),
    ToolFaultCase(
        id="empty",
        label="Empty result",
        stage="output_validation",
        expected_error="EMPTY_RESULT",
        description="An internal handler returns no usable result.",
    ),
    ToolFaultCase(
        id="invalid_output",
        label="Invalid output",
        stage="output_validation",
        expected_error="OUTPUT_SCHEMA_INVALID",
        description="Untrusted tool output fails the declared result schema.",
    ),
]


ROLE_PERMISSIONS: dict[ActorRole, frozenset[str]] = {
    "learner": frozenset({"orders:read", "analytics:read", "orders:propose"}),
    "reviewer": frozenset({"analytics:read"}),
    "approver": frozenset({"orders:read", "analytics:read", "orders:propose", "orders:write"}),
    "system": frozenset({"orders:read", "analytics:read"}),
}


class ToolLabService:
    def __init__(self) -> None:
        self.registry = ToolRegistry(_load_tool_spec_contract())
        self.handler_calls: dict[str, int] = {}
        self.cancelled_calls: dict[str, int] = {}
        self._runs: dict[str, ToolLabRun] = {}
        self._commands: dict[str, _CommandRecord] = {}
        self._command_locks: dict[str, asyncio.Lock] = {}
        handlers: dict[str, ToolHandler] = {
            "orders.get_status": self._order_status,
            "analytics.query_metric": self._query_metric,
            "orders.propose_address_change": self._propose_address_change,
            "orders.execute_address_change": self._execute_address_change,
        }
        for domain, spec, arguments in _tool_specs():
            self.handler_calls[spec.name] = 0
            self.cancelled_calls[spec.name] = 0
            self.registry.register(
                ToolRegistration(
                    domain=domain,
                    spec=spec,
                    example_arguments=arguments,
                    handler=handlers[spec.name],
                )
            )

    @staticmethod
    def _digest(material: str) -> str:
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _timestamp(sequence: int) -> datetime:
        return datetime(2026, 2, 4, 10, 0, tzinfo=UTC) + timedelta(milliseconds=sequence * 11)

    def list_catalog(self) -> ToolCatalogResponse:
        return ToolCatalogResponse(tools=self.registry.catalog(), faults=deepcopy(FAULTS))

    def get(self, run_id: str) -> ToolLabRun:
        try:
            return deepcopy(self._runs[run_id])
        except KeyError as error:
            raise ToolLabRunNotFoundError(run_id) from error

    def _request_for_command(self, command: ToolRunCommand) -> ToolRequest:
        fingerprint = command.model_dump_json(exclude={"idempotency_key"})
        digest = self._digest(f"{command.idempotency_key}:{fingerprint}")[:20]
        run_id = f"run_tool_{digest}"
        tool_name = command.tool_name
        tool_version = command.tool_version
        arguments = deepcopy(command.arguments)
        actor_role = command.actor_role
        expected_risk = command.expected_risk
        if command.fault == "unknown_tool":
            tool_name = "provider.do_everything"
            tool_version = "9.9.9"
            arguments = {}
            expected_risk = "read"
        elif command.fault == "missing_argument":
            tool_name = "orders.get_status"
            tool_version = "1.0.0"
            arguments = {}
            actor_role = "learner"
            expected_risk = "read"
        elif command.fault == "invalid_argument":
            tool_name = "orders.get_status"
            tool_version = "1.0.0"
            arguments = {"order_id": 1042}
            actor_role = "learner"
            expected_risk = "read"
        elif command.fault == "permission_denied":
            tool_name = "orders.get_status"
            tool_version = "1.0.0"
            arguments = {"order_id": "ORD-1042"}
            actor_role = "reviewer"
            expected_risk = "read"
        elif command.fault in {"timeout", "empty", "invalid_output"}:
            tool_name = "orders.get_status"
            tool_version = "1.0.0"
            arguments = {"order_id": "ORD-1042"}
            actor_role = "learner"
            expected_risk = "read"
        permissions = sorted(ROLE_PERMISSIONS[actor_role])
        return ToolRequest(
            request_id=f"toolreq_{digest}",
            run_id=run_id,
            tool_name=tool_name,
            tool_version=tool_version,
            arguments=arguments,
            actor_role=actor_role,
            granted_permissions=permissions,
            model_rationale=command.model_rationale,
            requested_at=self._timestamp(0),
            expected_risk=expected_risk,
            correlation_id=f"corr_{digest}",
            provider_output_ref=f"mock-provider://tool-proposal/{command.seed}",
        )

    @staticmethod
    def _complexity_issues(value: JsonValue) -> list[ToolValidationIssue]:
        nodes = 0
        stack: list[tuple[JsonValue, int]] = [(value, 0)]
        while stack:
            current, depth = stack.pop()
            nodes += 1
            if nodes > MAX_ARGUMENT_NODES:
                return [
                    ToolValidationIssue(
                        path="$",
                        keyword="maxNodes",
                        message="Tool arguments exceed the bounded node count.",
                    )
                ]
            if depth > MAX_ARGUMENT_DEPTH:
                return [
                    ToolValidationIssue(
                        path="$",
                        keyword="maxDepth",
                        message="Tool arguments exceed the bounded nesting depth.",
                    )
                ]
            if isinstance(current, dict):
                stack.extend((item, depth + 1) for item in current.values())
            elif isinstance(current, list):
                stack.extend((item, depth + 1) for item in current)
        return []

    @staticmethod
    def _validation_issues(
        schema: Mapping[str, JsonValue], value: JsonValue
    ) -> tuple[list[ToolValidationIssue], bool]:
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        raw_errors = sorted(validator.iter_errors(value), key=lambda error: list(error.path))
        issues = [
            ToolValidationIssue(
                path="$"
                + "".join(
                    f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.path
                ),
                keyword=str(error.validator),
                message=f"Contract check '{error.validator}' failed at the reported path.",
            )
            for error in raw_errors[:24]
        ]
        return issues, any(error.validator == "required" for error in raw_errors)

    @staticmethod
    def _redact(value: JsonValue, paths: list[str]) -> tuple[JsonValue, RedactionSummary]:
        redacted = deepcopy(value)
        applied: list[str] = []
        if isinstance(redacted, dict):
            for path in paths:
                key = path.removeprefix("$.")
                if key in redacted:
                    redacted[key] = "[REDACTED]"
                    applied.append(path)
        return redacted, RedactionSummary(applied=bool(applied), paths=applied)

    def _approval(self, request: ToolRequest) -> ApprovalRequirement:
        key_material = json.dumps(request.arguments, sort_keys=True, separators=(",", ":"))
        summary = self._digest(
            f"{request.actor_role}:{request.tool_name}:{key_material}:{request.run_id}"
        )[:12]
        target = str(request.arguments.get("order_id", "declared target"))
        return ApprovalRequirement(
            exact_action=request.tool_name,
            exact_target=target,
            args_diff=deepcopy(request.arguments),
            evidence=["proposal contract validated", "actor and tool version checked"],
            reason="The registered tool is risk=write and cannot execute without E7 approval.",
            side_effects=["would change a fictional shipping destination"],
            idempotency_key_summary=summary,
            expires_at=self._timestamp(600),
            available_actions=["approve", "reject", "edit", "request_evidence"],
        )

    async def execute(self, command: ToolRunCommand) -> ToolLabRun:
        lock = self._command_locks.setdefault(command.idempotency_key, asyncio.Lock())
        async with lock:
            return await self._execute_once(command)

    async def _execute_once(self, command: ToolRunCommand) -> ToolLabRun:
        fingerprint = command.model_dump_json(exclude={"idempotency_key"})
        previous = self._commands.get(command.idempotency_key)
        if previous is not None:
            if previous.fingerprint != fingerprint:
                raise ToolCallingConflictError(
                    "idempotency key was reused with a different tool command"
                )
            replay = deepcopy(previous.run)
            replay.result.cache_status = "idempotent_replay"
            return replay

        request = self._request_for_command(command)
        audit: list[ToolAuditEntry] = []

        def trace(
            stage: AuditStage,
            status: Literal["passed", "failed", "blocked", "skipped"],
            message: str,
        ) -> None:
            audit.append(
                ToolAuditEntry(
                    sequence=len(audit) + 1,
                    stage=stage,
                    status=status,
                    actor_role=request.actor_role,
                    action=request.tool_name,
                    resource=f"tool://{request.tool_name}@{request.tool_version}",
                    run_id=request.run_id,
                    correlation_id=request.correlation_id,
                    message=message,
                    timestamp=self._timestamp(len(audit) + 1),
                )
            )

        spec: ToolSpec | None = None
        registration: ToolRegistration | None = None
        attempts: list[ToolAttempt] = []
        redaction = RedactionSummary(applied=False, paths=[])
        started_at = self._timestamp(0)
        result_data: JsonValue | None = None
        result_error: ToolExecutionError | None = None
        result_status: Literal["succeeded", "failed", "approval_required"] = "failed"
        approval: ApprovalRequirement | None = None
        execution_label: Literal["CODE EXECUTED", "CODE BLOCKED"] = "CODE BLOCKED"

        try:
            registration = self.registry.resolve(request.tool_name, request.tool_version)
            spec = registration.spec
            trace(
                "resolve", "passed", f"Registry resolved {spec.name}@{spec.version} by exact key."
            )

            complexity = self._complexity_issues(request.arguments)
            issues, missing = self._validation_issues(spec.input_schema, request.arguments)
            issues = complexity + issues
            if issues:
                raise ToolExecutorError(
                    "ARGUMENT_MISSING" if missing else "ARGUMENT_SCHEMA_INVALID",
                    "input_validation",
                    False,
                    "Required arguments are missing."
                    if missing
                    else "Arguments failed the registered input schema.",
                    issues,
                )
            trace(
                "input_validation", "passed", "Arguments passed JSON Schema and complexity bounds."
            )

            missing_permissions = sorted(set(spec.permissions) - set(request.granted_permissions))
            if missing_permissions:
                raise ToolExecutorError(
                    "PERMISSION_DENIED",
                    "authorize",
                    False,
                    "Actor permissions do not authorize this registered tool.",
                    [
                        ToolValidationIssue(
                            path="$.actor_role",
                            keyword="permission",
                            message=f"Missing permission: {permission}",
                        )
                        for permission in missing_permissions
                    ],
                )
            trace(
                "authorize", "passed", "Code-derived actor permissions cover the tool requirements."
            )

            if request.expected_risk != spec.risk:
                raise ToolExecutorError(
                    "RISK_MISMATCH",
                    "policy",
                    False,
                    "Provider risk metadata disagrees with the registered ToolSpec.",
                )
            if spec.risk == "restricted":
                raise ToolExecutorError(
                    "PERMISSION_DENIED",
                    "policy",
                    False,
                    "Restricted tools cannot execute in the local course.",
                )
            trace("policy", "passed", f"Registry-owned risk '{spec.risk}' controls policy.")

            if spec.risk == "write":
                approval = self._approval(request)
                trace(
                    "approval",
                    "blocked",
                    "Write execution stopped at the explicit approval boundary.",
                )
                raise ToolExecutorError(
                    "APPROVAL_REQUIRED",
                    "approval",
                    False,
                    "Write execution is locked until the E7 approval workflow resolves.",
                )
            trace("approval", "skipped", "No human approval is required for this risk level.")
            trace("idempotency", "skipped", "The read/proposal path declares no side-effect key.")

            max_attempts = spec.retry.max_attempts if spec.risk == "read" else 1
            for attempt in range(1, max_attempts + 1):
                context = ToolExecutionContext(
                    fault=command.fault,
                    seed=command.seed,
                    timeout_ms=spec.timeout_ms,
                    attempt=attempt,
                )
                try:
                    raw = await asyncio.wait_for(
                        registration.handler(deepcopy(request.arguments), context),
                        timeout=spec.timeout_ms / 1_000,
                    )
                    execution_label = "CODE EXECUTED"
                    if raw in (None, "", [], {}):
                        raise ToolExecutorError(
                            "EMPTY_RESULT",
                            "output_validation",
                            False,
                            "The handler returned no result to validate.",
                        )
                    raw = TypeAdapter(JsonValue).validate_python(raw)
                    output_issues, _ = self._validation_issues(spec.output_schema, raw)
                    if output_issues:
                        raise ToolExecutorError(
                            "OUTPUT_SCHEMA_INVALID",
                            "output_validation",
                            False,
                            "Tool output failed the registered output schema.",
                            output_issues,
                        )
                    attempts.append(
                        ToolAttempt(attempt=attempt, status="succeeded", duration_ms=4 + attempt)
                    )
                    trace("execute", "passed", f"Code handler completed attempt {attempt}.")
                    trace("output_validation", "passed", "Untrusted tool output passed its schema.")
                    result_data, redaction = self._redact(raw, spec.redact)
                    trace(
                        "redact",
                        "passed" if redaction.applied else "skipped",
                        "Configured sensitive result paths were replaced."
                        if redaction.applied
                        else "The result contains no configured redaction path.",
                    )
                    result_status = "succeeded"
                    break
                except TimeoutError:
                    execution_label = "CODE EXECUTED"
                    attempts.append(
                        ToolAttempt(
                            attempt=attempt,
                            status="failed",
                            error_code="TIMEOUT",
                            duration_ms=spec.timeout_ms,
                        )
                    )
                    trace("execute", "failed", f"Attempt {attempt} reached the hard timeout.")
                    can_retry = (
                        spec.risk == "read"
                        and "TIMEOUT" in spec.retry.retryable_errors
                        and attempt < max_attempts
                    )
                    if can_retry:
                        continue
                    raise ToolExecutorError(
                        "TIMEOUT",
                        "execute",
                        False,
                        f"Read execution stopped after {attempt} bounded attempt(s).",
                    ) from None
                except ToolExecutorError as error:
                    execution_label = "CODE EXECUTED"
                    attempts.append(
                        ToolAttempt(
                            attempt=attempt,
                            status="failed",
                            error_code=error.code,
                            duration_ms=4 + attempt,
                        )
                    )
                    trace(error.stage, "failed", error.safe_message)
                    raise
        except ToolExecutorError as error:
            if (
                not audit
                or audit[-1].stage != error.stage
                or audit[-1].status not in {"failed", "blocked"}
            ):
                trace(error.stage, "failed", error.safe_message)
            result_error = ToolExecutionError(
                code=error.code,
                stage=error.stage,
                retryable=error.retryable,
                user_safe_message=error.safe_message,
                issues=error.issues,
            )
            result_status = "approval_required" if error.code == "APPROVAL_REQUIRED" else "failed"
        except Exception:
            trace("audit", "failed", "An internal exception was contained and redacted.")
            result_error = ToolExecutionError(
                code="INTERNAL_ERROR",
                stage="audit",
                retryable=False,
                user_safe_message="The executor contained an internal boundary failure.",
            )
            result_status = "failed"

        if not audit or audit[-1].stage != "audit":
            trace(
                "audit",
                "passed",
                "Typed request, decision, attempts and redaction summary were recorded.",
            )
        duration_ms = max(
            sum(attempt.duration_ms for attempt in attempts),
            len(audit) * 11,
        )
        result = ToolResult(
            request_id=request.request_id,
            status=result_status,
            data=result_data,
            error=result_error,
            started_at=started_at,
            ended_at=started_at + timedelta(milliseconds=duration_ms),
            duration_ms=duration_ms,
            attempts=attempts,
            cache_status="miss",
            provenance=(
                f"tool://{spec.name}@{spec.version}"
                if spec is not None
                else "registry://unresolved"
            ),
            redaction_summary=redaction,
            approval=approval,
        )
        run = ToolLabRun(
            run_id=request.run_id,
            status=result_status,
            execution_label=execution_label,
            spec=deepcopy(spec),
            request=request,
            result=result,
            audit=audit,
        )
        self._runs[run.run_id] = deepcopy(run)
        self._commands[command.idempotency_key] = _CommandRecord(
            fingerprint=fingerprint, run=deepcopy(run)
        )
        return deepcopy(run)

    async def _order_status(
        self, arguments: dict[str, JsonValue], context: ToolExecutionContext
    ) -> JsonValue | None:
        name = "orders.get_status"
        self.handler_calls[name] += 1
        if context.fault == "timeout":
            try:
                await asyncio.sleep(context.timeout_ms / 1_000 * 3)
            except asyncio.CancelledError:
                self.cancelled_calls[name] += 1
                raise
        if context.fault == "empty":
            return None
        if context.fault == "invalid_output":
            return {
                "order_id": arguments.get("order_id"),
                "status": "shipped",
                "customer_name": "Mira Chen",
                "customer_email": "mira@fictional.invalid",
                "last_update": "2026-02-04T09:40:00Z",
                "unsafe_markdown": "<script>not executable</script>",
            }
        if arguments.get("order_id") != "ORD-1042":
            raise ToolExecutorError(
                "ENTITY_UNRESOLVED",
                "execute",
                False,
                "No fictional order matched the validated identifier.",
            )
        return {
            "order_id": "ORD-1042",
            "status": "shipped",
            "customer_name": "Mira Chen",
            "customer_email": "mira@fictional.invalid",
            "last_update": "2026-02-04T09:40:00Z",
            "source": "fictional://support/orders",
        }

    async def _query_metric(
        self, arguments: dict[str, JsonValue], context: ToolExecutionContext
    ) -> JsonValue | None:
        name = "analytics.query_metric"
        self.handler_calls[name] += 1
        if context.fault == "timeout":
            try:
                await asyncio.sleep(context.timeout_ms / 1_000 * 3)
            except asyncio.CancelledError:
                self.cancelled_calls[name] += 1
                raise
        metric = cast(str, arguments["metric"])
        periods = cast(list[str], arguments["periods"])
        revenue = {"2025-Q1": 18.4, "2025-Q2": 20.1, "2025-Q3": 21.3, "2025-Q4": 24.2}
        tickets = {"2025-Q1": 124.0, "2025-Q2": 118.0, "2025-Q3": 103.0, "2025-Q4": 96.0}
        fixture = revenue if metric == "revenue" else tickets
        unit = "USD millions" if metric == "revenue" else "tickets"
        return {
            "metric": metric,
            "rows": [
                {"period": period, "value": fixture[period], "unit": unit} for period in periods
            ],
            "source": "fictional://analytics/metrics",
        }

    async def _propose_address_change(
        self, arguments: dict[str, JsonValue], _context: ToolExecutionContext
    ) -> JsonValue | None:
        name = "orders.propose_address_change"
        self.handler_calls[name] += 1
        material = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
        proposal_id = f"proposal_{self._digest(material)[:12]}"
        return {
            "proposal_id": proposal_id,
            "exact_action": "change_shipping_city",
            "target": arguments["order_id"],
            "summary": f"Prepare city change to {arguments['new_city']} with no side effect.",
            "side_effects": [],
            "source": "fictional://support/proposals",
        }

    async def _execute_address_change(
        self, _arguments: dict[str, JsonValue], _context: ToolExecutionContext
    ) -> JsonValue | None:
        name = "orders.execute_address_change"
        self.handler_calls[name] += 1
        raise RuntimeError("write handler must remain unreachable before E7 approval")
