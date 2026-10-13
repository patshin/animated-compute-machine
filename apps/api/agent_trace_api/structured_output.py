from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast
from urllib.parse import urljoin
from uuid import uuid4

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import Field, JsonValue, TypeAdapter
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable

from agent_trace_api.models import ApprovedCommand, RunId, StrictModel

LessonId = Literal["route-decision", "answer-payload"]
ProviderName = Literal["mock", "manual"]
ValidationKind = Literal["invalid_json", "schema_invalid", "semantic_invalid", "passed"]
RepairEventType = Literal[
    "schema.validation_started",
    "schema.validation_failed",
    "schema.validation_passed",
    "provider.retry_requested",
    "provider.retry_completed",
    "interrupt.created",
    "interrupt.invalidated",
    "interrupt.resolved",
    "schema.revision_applied",
    "course.assertion_recorded",
]

MAX_VALIDATION_ATTEMPTS = 2
MAX_DOCUMENT_DEPTH = 64
MAX_DOCUMENT_NODES = 10_000


class StructuredError(StrictModel):
    path: str
    keyword: str
    message: str


class SchemaCheck(StrictModel):
    kind: Literal["valid", "invalid_json", "schema_invalid"]
    errors: list[StructuredError] = Field(default_factory=list)


class CompatibilityChange(StrictModel):
    path: str
    kind: Literal["required_added", "enum_removed", "type_changed", "constraint_tightened"]
    message: str
    breaking: bool


class SchemaRevision(StrictModel):
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    schema_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    schema_document: dict[str, JsonValue]
    compatibility: list[CompatibilityChange] = Field(default_factory=list)
    breaking: bool = False


class LessonExercise(StrictModel):
    id: Literal["required", "enum", "additional-properties", "nullable", "breaking", "semantic"]
    label: str
    prediction_prompt: str
    raw_output: str
    schema_document: dict[str, JsonValue] | None = None


class StructuredLesson(StrictModel):
    id: LessonId
    domain: Literal["support", "analytics"]
    title: str
    objective: str
    default_schema: dict[str, JsonValue]
    valid_output: str
    exercises: list[LessonExercise]


class LessonsResponse(StrictModel):
    schema_id: Literal["agent-trace://structured-lessons/1.0.0"] = (
        "agent-trace://structured-lessons/1.0.0"
    )
    lessons: list[StructuredLesson]


class RepairTraceEntry(StrictModel):
    sequence: int = Field(ge=1)
    type: RepairEventType
    message: str


class ValidationArtifact(StrictModel):
    artifact_id: str = Field(pattern=r"^artifact_[A-Za-z0-9_-]+$")
    attempt: int = Field(ge=1, le=MAX_VALIDATION_ATTEMPTS)
    provider: ProviderName
    schema_version: str
    schema_hash: str
    raw_output: str
    parsed_output: JsonValue | None = None
    validated_output: JsonValue | None = None
    result_kind: ValidationKind
    errors: list[StructuredError] = Field(default_factory=list)


class ManualInterrupt(StrictModel):
    interrupt_id: str = Field(pattern=r"^interrupt_[A-Za-z0-9_-]+$")
    reason: Literal["structured_output_repair"] = "structured_output_repair"
    expected_schema_version: str
    attempt: Literal[1] = 1


class CourseProgress(StrictModel):
    prediction: Literal["pass", "fail"]
    prediction_recorded: bool = True
    failure_observed: bool = False
    repair_completed: bool = False
    assertion_recorded: bool = False
    completed: bool = False


class StructuredRun(StrictModel):
    schema_id: Literal["agent-trace://structured-run/1.0.0"] = "agent-trace://structured-run/1.0.0"
    run_id: RunId
    lesson_id: LessonId
    provider: ProviderName
    status: Literal["ready", "awaiting_manual", "failed", "passed"]
    active_schema_version: str
    schema_history: list[SchemaRevision]
    artifacts: list[ValidationArtifact]
    provider_exchanges: list[ProviderExchange]
    repair_trace: list[RepairTraceEntry]
    pending_interrupt: ManualInterrupt | None = None
    progress: CourseProgress


class StartStructuredRunRequest(ApprovedCommand):
    lesson_id: LessonId
    provider: ProviderName
    prediction: Literal["pass", "fail"]


class StructuredValidationRequest(ApprovedCommand):
    raw_output: str = Field(min_length=1, max_length=20_000)


class ManualStructuredRequest(ApprovedCommand):
    interrupt_id: str = Field(pattern=r"^interrupt_[A-Za-z0-9_-]+$")
    raw_output: str = Field(min_length=1, max_length=20_000)


class SchemaRevisionRequest(ApprovedCommand):
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    schema_text: str = Field(min_length=2, max_length=40_000)


class AssertionRequest(ApprovedCommand):
    assertion: Literal[True]


class SchemaSubmissionResult(StrictModel):
    applied: bool
    check: SchemaCheck
    run: StructuredRun


class StructuredOutputConflictError(RuntimeError):
    pass


class StructuredRunNotFoundError(KeyError):
    pass


class StructuredRequest(StrictModel):
    schema_id: Literal["agent-trace://structured-provider-request/1.0.0"] = (
        "agent-trace://structured-provider-request/1.0.0"
    )
    provider: ProviderName
    run_id: RunId
    lesson_id: LessonId
    schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    schema_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    schema_document: dict[str, JsonValue]
    attempt: Literal[1] = 1
    failed_artifact_id: str = Field(pattern=r"^artifact_[A-Za-z0-9_-]+$")
    raw_output: str = Field(min_length=1, max_length=20_000)
    errors: list[StructuredError] = Field(min_length=1, max_length=32)


class StructuredPayloadResult(StrictModel):
    schema_id: Literal["agent-trace://structured-provider-result/1.0.0"] = (
        "agent-trace://structured-provider-result/1.0.0"
    )
    kind: Literal["payload"] = "payload"
    provider: Literal["mock"] = "mock"
    run_id: RunId
    schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    schema_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    attempt: Literal[1] = 1
    raw_output: str = Field(min_length=1, max_length=20_000)


class StructuredInterruptResult(StrictModel):
    schema_id: Literal["agent-trace://structured-provider-result/1.0.0"] = (
        "agent-trace://structured-provider-result/1.0.0"
    )
    kind: Literal["interrupt"] = "interrupt"
    provider: Literal["manual"] = "manual"
    run_id: RunId
    schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    schema_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    attempt: Literal[1] = 1
    interrupt_id: str = Field(pattern=r"^interrupt_[A-Za-z0-9_-]+$")
    reason: Literal["structured_output_repair"] = "structured_output_repair"


StructuredResult = StructuredPayloadResult | StructuredInterruptResult
STRUCTURED_RESULT_ADAPTER: TypeAdapter[StructuredResult] = TypeAdapter(StructuredResult)


class ProviderExchange(StrictModel):
    sequence: int = Field(ge=1)
    request: StructuredRequest
    result: StructuredResult = Field(discriminator="kind")


StructuredRun.model_rebuild()


class StructuredProvider(Protocol):
    @property
    def name(self) -> ProviderName: ...

    def repair(self, request: StructuredRequest) -> StructuredResult: ...


class MockStructuredProvider:
    @property
    def name(self) -> ProviderName:
        return "mock"

    def repair(self, request: StructuredRequest) -> StructuredResult:
        lesson = _lessons()[request.lesson_id]
        repaired: object = json.loads(
            lesson.valid_output.replace("run_PLACEHOLDER", request.run_id)
        )
        if not isinstance(repaired, dict):
            raise StructuredOutputConflictError("mock provider repair fixture must be an object")
        repaired["schema_version"] = request.schema_version
        return StructuredPayloadResult(
            run_id=request.run_id,
            schema_version=request.schema_version,
            schema_hash=request.schema_hash,
            raw_output=_pretty(repaired),
        )


class ManualStructuredProvider:
    @property
    def name(self) -> ProviderName:
        return "manual"

    def repair(self, request: StructuredRequest) -> StructuredResult:
        return StructuredInterruptResult(
            run_id=request.run_id,
            schema_version=request.schema_version,
            schema_hash=request.schema_hash,
            interrupt_id=f"interrupt_{uuid4().hex}",
        )


ROUTE_SCHEMA: dict[str, JsonValue] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "agent-trace://route-decision/1.0.0",
    "title": "RouteDecision",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "route", "rationale", "clarification_question"],
    "properties": {
        "schema_version": {"const": "1.0.0"},
        "route": {"type": "string", "enum": ["solve", "clarify", "escalate"]},
        "rationale": {"type": "string", "minLength": 3},
        "clarification_question": {"type": ["string", "null"], "minLength": 3},
    },
}

CONTRACTS_DIRECTORY = Path(__file__).resolve().parents[3] / "contracts"


def _load_contract_schema(filename: str) -> dict[str, JsonValue]:
    path = (CONTRACTS_DIRECTORY / filename).resolve()
    if path.parent != CONTRACTS_DIRECTORY.resolve():
        raise RuntimeError("contract schema path escaped the repository contract directory")
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"canonical contract {filename} must contain one schema object")
    return cast(dict[str, JsonValue], value)


ANSWER_SCHEMA = _load_contract_schema("answer-payload.schema.json")
CHART_SCHEMA = _load_contract_schema("chart-dsl.schema.json")


def _compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _schema_hash(schema: dict[str, JsonValue]) -> str:
    return hashlib.sha256(_compact(schema).encode()).hexdigest()


def _pretty(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _lessons() -> dict[LessonId, StructuredLesson]:
    route_valid = {
        "schema_version": "1.0.0",
        "route": "clarify",
        "rationale": "The account identifier is missing.",
        "clarification_question": "Which account should I inspect?",
    }
    answer_valid = {
        "schema_version": "1.0.0",
        "run_id": "run_PLACEHOLDER",
        "answer_markdown": "Revenue increased through the final quarter.",
        "summary": "Revenue finished above the opening quarter.",
        "evidence_ids": ["metric-q1", "metric-q4"],
        "claims": [{"text": "Q4 exceeded Q1.", "evidence_ids": ["metric-q1", "metric-q4"]}],
        "confidence": {
            "label": "high",
            "reasons": ["The cited quarterly metrics agree."],
        },
        "limitations": ["Only the supplied quarterly series was evaluated."],
        "chart_spec": None,
        "proposed_actions": [],
        "requires_human_review": False,
    }
    breaking = deepcopy(ROUTE_SCHEMA)
    route_properties = cast(dict[str, JsonValue], breaking["properties"])
    route_property = cast(dict[str, JsonValue], route_properties["route"])
    route_property["enum"] = ["solve", "clarify"]
    version_property = cast(dict[str, JsonValue], route_properties["schema_version"])
    version_property["const"] = "2.0.0"
    breaking["$id"] = "agent-trace://route-decision/2.0.0"
    breaking_valid = {**route_valid, "schema_version": "2.0.0"}

    missing_evidence = deepcopy(answer_valid)
    missing_evidence.pop("evidence_ids")
    additional_property = {**deepcopy(answer_valid), "notes": "private"}
    nullable_answer = {**deepcopy(answer_valid), "answer_markdown": None}
    unknown_evidence = deepcopy(answer_valid)
    unknown_evidence["evidence_ids"] = ["metric-q1"]
    unknown_evidence["claims"] = [{"text": "Q4 led.", "evidence_ids": ["metric-q4"]}]
    return {
        "route-decision": StructuredLesson(
            id="route-decision",
            domain="support",
            title="RouteDecision / Support",
            objective=(
                "Predict routing, separate contract failures, then repair through a typed boundary."
            ),
            default_schema=ROUTE_SCHEMA,
            valid_output=_pretty(route_valid),
            exercises=[
                LessonExercise(
                    id="required",
                    label="Missing required",
                    prediction_prompt="Will a missing rationale pass?",
                    raw_output=_compact(
                        {
                            "schema_version": "1.0.0",
                            "route": "solve",
                            "clarification_question": None,
                        }
                    ),
                ),
                LessonExercise(
                    id="enum",
                    label="Unknown enum",
                    prediction_prompt="Will route=refund pass?",
                    raw_output=_compact(
                        {
                            "schema_version": "1.0.0",
                            "route": "refund",
                            "rationale": "Refund requested.",
                            "clarification_question": None,
                        }
                    ),
                ),
                LessonExercise(
                    id="additional-properties",
                    label="Extra property",
                    prediction_prompt="Does extra debug data cross the boundary?",
                    raw_output=_compact(
                        {
                            "schema_version": "1.0.0",
                            "route": "solve",
                            "rationale": "Known answer.",
                            "clarification_question": None,
                            "debug": True,
                        }
                    ),
                ),
                LessonExercise(
                    id="nullable",
                    label="Nullable ≠ absent",
                    prediction_prompt="Is null different from omitting the field?",
                    raw_output=_compact(
                        {
                            "schema_version": "1.0.0",
                            "route": "solve",
                            "rationale": "Known answer.",
                        }
                    ),
                ),
                LessonExercise(
                    id="semantic",
                    label="Semantic mismatch",
                    prediction_prompt="Can clarify omit its question semantically?",
                    raw_output=_compact(
                        {
                            "schema_version": "1.0.0",
                            "route": "clarify",
                            "rationale": "More context needed.",
                            "clarification_question": None,
                        }
                    ),
                ),
                LessonExercise(
                    id="breaking",
                    label="Breaking enum",
                    prediction_prompt="Will removing an enum member break producers?",
                    raw_output=_pretty(breaking_valid),
                    schema_document=breaking,
                ),
            ],
        ),
        "answer-payload": StructuredLesson(
            id="answer-payload",
            domain="analytics",
            title="AnswerPayload / Analytics",
            objective="Bind claims to evidence and keep runtime identity inside the typed answer.",
            default_schema=ANSWER_SCHEMA,
            valid_output=_pretty(answer_valid),
            exercises=[
                LessonExercise(
                    id="required",
                    label="Missing evidence",
                    prediction_prompt="Can an answer omit evidence_ids?",
                    raw_output=_compact(missing_evidence),
                ),
                LessonExercise(
                    id="enum",
                    label="JSON-looking string",
                    prediction_prompt="Does a quoted JSON object count as an object?",
                    raw_output=_compact('{"schema_version":"1.0.0","answer":"Revenue rose."}'),
                ),
                LessonExercise(
                    id="additional-properties",
                    label="Extra property",
                    prediction_prompt="Can internal notes cross this boundary?",
                    raw_output=_compact(additional_property),
                ),
                LessonExercise(
                    id="nullable",
                    label="Null answer",
                    prediction_prompt="Is answer_markdown nullable?",
                    raw_output=_compact(nullable_answer),
                ),
                LessonExercise(
                    id="semantic",
                    label="Unknown evidence",
                    prediction_prompt="Can a claim cite evidence outside the top-level set?",
                    raw_output=_compact(unknown_evidence),
                ),
            ],
        ),
    }


def _path(parts: Iterable[str | int]) -> str:
    values = list(parts)
    return "$" + "".join(f"[{item}]" if isinstance(item, int) else f".{item}" for item in values)


def _safe_parse(raw_output: str) -> tuple[JsonValue | None, list[StructuredError]]:
    candidate = raw_output.strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3 and lines[0] in {"```", "```json"} and lines[-1] == "```":
            candidate = "\n".join(lines[1:-1]).strip()
    try:
        return cast(JsonValue, json.loads(candidate)), []
    except json.JSONDecodeError as error:
        return None, [
            StructuredError(
                path=f"line {error.lineno}, column {error.colno}",
                keyword="json.parse",
                message=error.msg,
            )
        ]
    except RecursionError:
        return None, [
            StructuredError(
                path="$",
                keyword="json.depth",
                message=f"JSON document exceeds the supported depth of {MAX_DOCUMENT_DEPTH}",
            )
        ]


def _document_complexity_errors(value: JsonValue) -> list[StructuredError]:
    stack: list[tuple[JsonValue, str, int]] = [(value, "$", 0)]
    nodes = 0
    while stack:
        current, path, depth = stack.pop()
        nodes += 1
        if depth > MAX_DOCUMENT_DEPTH:
            return [
                StructuredError(
                    path=path,
                    keyword="json.depth",
                    message=f"document exceeds the supported depth of {MAX_DOCUMENT_DEPTH}",
                )
            ]
        if nodes > MAX_DOCUMENT_NODES:
            return [
                StructuredError(
                    path="$",
                    keyword="json.nodes",
                    message=f"document exceeds the supported size of {MAX_DOCUMENT_NODES} nodes",
                )
            ]
        if isinstance(current, dict):
            stack.extend((child, f"{path}.{key}", depth + 1) for key, child in current.items())
        elif isinstance(current, list):
            stack.extend(
                (child, f"{path}[{index}]", depth + 1) for index, child in enumerate(current)
            )
    return []


def _schema_identity_errors(schema: dict[str, JsonValue], version: str) -> list[StructuredError]:
    errors: list[StructuredError] = []
    schema_id = schema.get("$id")
    valid_suffixes = (f"/{version}", f"/{version.replace('.', '-')}")
    if not isinstance(schema_id, str) or not schema_id.endswith(valid_suffixes):
        errors.append(
            StructuredError(
                path="$.$id",
                keyword="schema.identity",
                message=f"$id must end with the declared version {version}",
            )
        )
    properties = schema.get("properties")
    version_schema = properties.get("schema_version") if isinstance(properties, dict) else None
    payload_version = version_schema.get("const") if isinstance(version_schema, dict) else None
    if payload_version != version:
        errors.append(
            StructuredError(
                path="$.properties.schema_version.const",
                keyword="schema.identity",
                message=f"payload schema_version const must equal {version}",
            )
        )
    return errors


def _schema_registry(schema: dict[str, JsonValue]) -> Registry[bool | Mapping[str, Any]]:
    chart_id = cast(str, CHART_SCHEMA["$id"])
    base_id = schema.get("$id")
    chart_resource = cast(Resource[bool | Mapping[str, Any]], Resource.from_contents(CHART_SCHEMA))
    registry: Registry[bool | Mapping[str, Any]] = Registry().with_resource(
        chart_id, chart_resource
    )
    if isinstance(base_id, str):
        registry = registry.with_resource(urljoin(base_id, "chart-dsl.schema.json"), chart_resource)
    return registry


def _validate_schema(schema: dict[str, JsonValue]) -> list[StructuredError]:
    try:
        Draft202012Validator.check_schema(schema)
        return []
    except SchemaError as error:
        return [
            StructuredError(
                path=_path(error.absolute_schema_path), keyword="json-schema", message=error.message
            )
        ]


def _schema_errors(schema: dict[str, JsonValue], value: JsonValue) -> list[StructuredError]:
    validator = Draft202012Validator(schema, registry=_schema_registry(schema))
    try:
        return [
            StructuredError(
                path=_path(error.absolute_path),
                keyword=str(error.validator or "schema"),
                message=error.message,
            )
            for error in sorted(
                validator.iter_errors(value),
                key=lambda item: (list(item.absolute_path), item.message),
            )
        ]
    except Unresolvable:
        return [
            StructuredError(
                path="$",
                keyword="$ref",
                message="schema reference could not be resolved from the local document",
            )
        ]
    except RecursionError:
        return [
            StructuredError(
                path="$",
                keyword="json.depth",
                message=f"validation exceeds the supported depth of {MAX_DOCUMENT_DEPTH}",
            )
        ]


def _external_reference_errors(value: JsonValue, path: str = "$") -> list[StructuredError]:
    errors: list[StructuredError] = []
    chart_id = cast(str, CHART_SCHEMA["$id"])
    allowed_references = {"chart-dsl.schema.json", chart_id}
    stack: list[tuple[JsonValue, str]] = [(value, path)]
    while stack:
        current, current_path = stack.pop()
        if isinstance(current, dict):
            reference = current.get("$ref")
            if (
                isinstance(reference, str)
                and not reference.startswith("#")
                and reference not in allowed_references
            ):
                errors.append(
                    StructuredError(
                        path=f"{current_path}.$ref",
                        keyword="$ref",
                        message="external schema references are disabled in the offline lab",
                    )
                )
            stack.extend((child, f"{current_path}.{key}") for key, child in current.items())
        elif isinstance(current, list):
            stack.extend((child, f"{current_path}[{index}]") for index, child in enumerate(current))
    return errors


def _semantic_errors(lesson_id: LessonId, run_id: str, value: JsonValue) -> list[StructuredError]:
    if not isinstance(value, dict):
        return []
    errors: list[StructuredError] = []
    if lesson_id == "route-decision":
        if value.get("route") == "clarify" and not value.get("clarification_question"):
            errors.append(
                StructuredError(
                    path="$.clarification_question",
                    keyword="semantic",
                    message="clarify requires a non-empty clarification question",
                )
            )
    else:
        if value.get("run_id") != run_id:
            errors.append(
                StructuredError(
                    path="$.run_id",
                    keyword="semantic",
                    message="run_id must match the active structured run",
                )
            )
        raw_evidence = value.get("evidence_ids", [])
        evidence = (
            {item for item in raw_evidence if isinstance(item, str)}
            if isinstance(raw_evidence, list)
            else set()
        )
        claims = value.get("claims", [])
        if isinstance(claims, list):
            for index, claim in enumerate(claims):
                if isinstance(claim, dict):
                    raw_citations = claim.get("evidence_ids", [])
                    cited = (
                        {item for item in raw_citations if isinstance(item, str)}
                        if isinstance(raw_citations, list)
                        else set()
                    )
                    unknown = cited - evidence
                    if unknown:
                        errors.append(
                            StructuredError(
                                path=f"$.claims[{index}].evidence_ids",
                                keyword="semantic",
                                message=f"unknown evidence ids: {', '.join(sorted(unknown))}",
                            )
                        )
    return errors


def _compatibility(
    old: dict[str, JsonValue], new: dict[str, JsonValue], path: str = "$"
) -> list[CompatibilityChange]:
    changes: list[CompatibilityChange] = []

    old_required = {
        item for item in cast(list[JsonValue], old.get("required", [])) if isinstance(item, str)
    }
    new_required = {
        item for item in cast(list[JsonValue], new.get("required", [])) if isinstance(item, str)
    }
    for field in sorted(new_required - old_required):
        changes.append(
            CompatibilityChange(
                path=f"{path}.{field}",
                kind="required_added",
                message=f"Required field '{field}' was added; existing producers may omit it.",
                breaking=True,
            )
        )

    if "type" in old and "type" in new and old["type"] != new["type"]:
        changes.append(
            CompatibilityChange(
                path=path,
                kind="type_changed",
                message=f"Type at '{path}' changed.",
                breaking=True,
            )
        )

    old_enum = old.get("enum")
    new_enum = new.get("enum")
    if isinstance(old_enum, list) and isinstance(new_enum, list):
        removed = [str(item) for item in old_enum if item not in new_enum]
        if removed:
            changes.append(
                CompatibilityChange(
                    path=path,
                    kind="enum_removed",
                    message=f"Enum values removed: {', '.join(removed)}.",
                    breaking=True,
                )
            )

    if old.get("additionalProperties") is not False and new.get("additionalProperties") is False:
        changes.append(
            CompatibilityChange(
                path=path,
                kind="constraint_tightened",
                message=f"additionalProperties became false at '{path}'.",
                breaking=True,
            )
        )

    for minimum in ("minLength", "minItems", "minimum", "exclusiveMinimum"):
        old_value = old.get(minimum)
        new_value = new.get(minimum)
        if (
            isinstance(old_value, (int, float))
            and not isinstance(old_value, bool)
            and isinstance(new_value, (int, float))
            and not isinstance(new_value, bool)
            and new_value > old_value
        ):
            changes.append(
                CompatibilityChange(
                    path=path,
                    kind="constraint_tightened",
                    message=f"{minimum} increased from {old_value} to {new_value} at '{path}'.",
                    breaking=True,
                )
            )
    for maximum in ("maxLength", "maxItems", "maximum", "exclusiveMaximum"):
        old_value = old.get(maximum)
        new_value = new.get(maximum)
        if (
            isinstance(old_value, (int, float))
            and not isinstance(old_value, bool)
            and isinstance(new_value, (int, float))
            and not isinstance(new_value, bool)
            and new_value < old_value
        ):
            changes.append(
                CompatibilityChange(
                    path=path,
                    kind="constraint_tightened",
                    message=f"{maximum} decreased from {old_value} to {new_value} at '{path}'.",
                    breaking=True,
                )
            )
    old_pattern = old.get("pattern")
    new_pattern = new.get("pattern")
    if isinstance(new_pattern, str) and new_pattern != old_pattern:
        changes.append(
            CompatibilityChange(
                path=path,
                kind="constraint_tightened",
                message=f"Pattern constraint changed at '{path}'.",
                breaking=True,
            )
        )
    if (
        path != "$.schema_version"
        and "const" in old
        and "const" in new
        and old["const"] != new["const"]
    ):
        changes.append(
            CompatibilityChange(
                path=path,
                kind="constraint_tightened",
                message=f"Const value changed at '{path}'.",
                breaking=True,
            )
        )

    old_properties = old.get("properties")
    new_properties = new.get("properties")
    old_properties = old_properties if isinstance(old_properties, dict) else {}
    new_properties = new_properties if isinstance(new_properties, dict) else {}
    for field in sorted(old_properties.keys() & new_properties.keys()):
        old_property = old_properties[field]
        new_property = new_properties[field]
        if isinstance(old_property, dict) and isinstance(new_property, dict):
            changes.extend(
                _compatibility(
                    old_property,
                    new_property,
                    f"{path}.{field}",
                )
            )

    old_items = old.get("items")
    new_items = new.get("items")
    if isinstance(old_items, dict) and isinstance(new_items, dict):
        changes.extend(
            _compatibility(
                old_items,
                new_items,
                f"{path}[]",
            )
        )

    for definitions_key in ("$defs", "definitions"):
        old_definitions = old.get(definitions_key)
        new_definitions = new.get(definitions_key)
        if not isinstance(old_definitions, dict) or not isinstance(new_definitions, dict):
            continue
        for name in sorted(old_definitions.keys() & new_definitions.keys()):
            old_definition = old_definitions[name]
            new_definition = new_definitions[name]
            if isinstance(old_definition, dict) and isinstance(new_definition, dict):
                changes.extend(
                    _compatibility(
                        old_definition,
                        new_definition,
                        f"{path}.{definitions_key}.{name}",
                    )
                )

    for combinator in ("allOf", "anyOf", "oneOf"):
        old_branches = old.get(combinator)
        new_branches = new.get(combinator)
        if not isinstance(old_branches, list) or not isinstance(new_branches, list):
            continue
        if combinator in {"anyOf", "oneOf"} and len(new_branches) < len(old_branches):
            changes.append(
                CompatibilityChange(
                    path=path,
                    kind="constraint_tightened",
                    message=f"{combinator} removed accepted branches at '{path}'.",
                    breaking=True,
                )
            )
        for index, (old_branch, new_branch) in enumerate(
            zip(old_branches, new_branches, strict=False)
        ):
            if isinstance(old_branch, dict) and isinstance(new_branch, dict):
                changes.extend(
                    _compatibility(
                        old_branch,
                        new_branch,
                        f"{path}.{combinator}[{index}]",
                    )
                )
    return changes


@dataclass
class _IdempotentResult:
    fingerprint: str
    value: StructuredRun | SchemaSubmissionResult


class StructuredLabService:
    def __init__(self) -> None:
        self.lessons = _lessons()
        self.providers: dict[ProviderName, StructuredProvider] = {
            "mock": MockStructuredProvider(),
            "manual": ManualStructuredProvider(),
        }
        self._runs: dict[str, StructuredRun] = {}
        self._commands: dict[tuple[str, str], _IdempotentResult] = {}

    def list_lessons(self) -> LessonsResponse:
        return LessonsResponse(lessons=[deepcopy(lesson) for lesson in self.lessons.values()])

    def get(self, run_id: str) -> StructuredRun:
        try:
            return deepcopy(self._runs[run_id])
        except KeyError as error:
            raise StructuredRunNotFoundError(run_id) from error

    def _remember(
        self, scope: str, command: ApprovedCommand, value: StructuredRun | SchemaSubmissionResult
    ) -> StructuredRun | SchemaSubmissionResult:
        key = (scope, command.idempotency_key)
        fingerprint = command.model_dump_json(exclude={"idempotency_key"})
        previous = self._commands.get(key)
        if previous:
            if previous.fingerprint != fingerprint:
                raise StructuredOutputConflictError(
                    "idempotency key was reused with a different command"
                )
            return deepcopy(previous.value)
        self._commands[key] = _IdempotentResult(fingerprint, deepcopy(value))
        return deepcopy(value)

    def start(self, command: StartStructuredRunRequest) -> StructuredRun:
        scope = f"start:{command.lesson_id}:{command.provider}"
        previous = self._commands.get((scope, command.idempotency_key))
        if previous:
            result = self._remember(scope, command, cast(StructuredRun, previous.value))
            return cast(StructuredRun, result)
        lesson = self.lessons[command.lesson_id]
        schema = deepcopy(lesson.default_schema)
        revision = SchemaRevision(
            version="1.0.0", schema_hash=_schema_hash(schema), schema_document=schema
        )
        run = StructuredRun(
            run_id=f"run_structured_{uuid4().hex}",
            lesson_id=command.lesson_id,
            provider=command.provider,
            status="ready",
            active_schema_version="1.0.0",
            schema_history=[revision],
            artifacts=[],
            provider_exchanges=[],
            repair_trace=[],
            progress=CourseProgress(prediction=command.prediction),
        )
        self._runs[run.run_id] = deepcopy(run)
        return cast(StructuredRun, self._remember(scope, command, run))

    @staticmethod
    def _trace(run: StructuredRun, event_type: RepairEventType, message: str) -> None:
        run.repair_trace.append(
            RepairTraceEntry(sequence=len(run.repair_trace) + 1, type=event_type, message=message)
        )

    def _artifact(
        self, run: StructuredRun, raw_output: str, provider: ProviderName
    ) -> ValidationArtifact:
        revision = run.schema_history[-1]
        cycle_attempt = 1 + sum(
            artifact.schema_hash == revision.schema_hash for artifact in run.artifacts
        )
        if cycle_attempt > MAX_VALIDATION_ATTEMPTS:
            raise StructuredOutputConflictError(
                f"structured validation is bounded to {MAX_VALIDATION_ATTEMPTS} "
                "artifacts per schema cycle"
            )
        self._trace(
            run,
            "schema.validation_started",
            f"Attempt {cycle_attempt} started against schema {revision.version}.",
        )
        parsed, parse_errors = _safe_parse(raw_output)
        errors = parse_errors
        kind: ValidationKind = "invalid_json" if errors else "passed"
        if not errors:
            errors = _document_complexity_errors(parsed)
            if errors:
                kind = "invalid_json"
                parsed = None
        if not errors:
            errors = _schema_errors(revision.schema_document, parsed)
            if errors:
                kind = "schema_invalid"
        if not errors:
            errors = _semantic_errors(run.lesson_id, run.run_id, parsed)
            if errors:
                kind = "semantic_invalid"
        artifact = ValidationArtifact(
            artifact_id=f"artifact_{uuid4().hex}",
            attempt=cycle_attempt,
            provider=provider,
            schema_version=revision.version,
            schema_hash=revision.schema_hash,
            raw_output=raw_output,
            parsed_output=parsed,
            validated_output=parsed if kind == "passed" else None,
            result_kind=kind,
            errors=errors,
        )
        run.artifacts.append(artifact)
        if kind == "passed":
            self._trace(
                run,
                "schema.validation_passed",
                f"Artifact {artifact.artifact_id} crossed the typed boundary.",
            )
        else:
            run.progress.failure_observed = True
            self._trace(
                run,
                "schema.validation_failed",
                f"{kind} produced {len(errors)} actionable error(s).",
            )
        return artifact

    def _provider_result(
        self, run: StructuredRun, artifact: ValidationArtifact
    ) -> StructuredResult:
        revision = run.schema_history[-1]
        request = StructuredRequest(
            provider=run.provider,
            run_id=run.run_id,
            lesson_id=run.lesson_id,
            schema_version=revision.version,
            schema_hash=revision.schema_hash,
            schema_document=deepcopy(revision.schema_document),
            failed_artifact_id=artifact.artifact_id,
            raw_output=artifact.raw_output,
            errors=artifact.errors[:32],
        )
        proposed = self.providers[run.provider].repair(request)
        result = STRUCTURED_RESULT_ADAPTER.validate_python(proposed.model_dump(mode="json"))
        if (
            result.provider != request.provider
            or result.run_id != request.run_id
            or result.schema_version != request.schema_version
            or result.schema_hash != request.schema_hash
            or result.attempt != request.attempt
        ):
            raise StructuredOutputConflictError(
                "structured provider result identity did not match its typed request"
            )
        run.provider_exchanges.append(
            ProviderExchange(
                sequence=len(run.provider_exchanges) + 1,
                request=deepcopy(request),
                result=deepcopy(result),
            )
        )
        return result

    @staticmethod
    def _refresh_completion(run: StructuredRun) -> None:
        run.progress.completed = (
            run.progress.prediction_recorded
            and run.progress.failure_observed
            and run.progress.repair_completed
            and run.progress.assertion_recorded
        )

    def validate(self, run_id: str, command: StructuredValidationRequest) -> StructuredRun:
        scope = f"validate:{run_id}"
        previous = self._commands.get((scope, command.idempotency_key))
        if previous:
            return cast(
                StructuredRun, self._remember(scope, command, cast(StructuredRun, previous.value))
            )
        run = self.get(run_id)
        if run.status == "awaiting_manual":
            raise StructuredOutputConflictError(
                "resolve the pending manual interrupt before validating again"
            )
        if run.status in {"passed", "failed"}:
            raise StructuredOutputConflictError(
                "terminal structured runs reject validation until a new schema revision is applied"
            )
        artifact = self._artifact(run, command.raw_output, run.provider)
        provider_result: StructuredResult | None = None
        if artifact.result_kind == "passed":
            run.status = "passed"
        else:
            provider_result = self._provider_result(run, artifact)
        if artifact.result_kind != "passed" and isinstance(
            provider_result, StructuredPayloadResult
        ):
            self._trace(
                run,
                "provider.retry_requested",
                "MockProvider received one controlled structured-output retry.",
            )
            retry = self._artifact(run, provider_result.raw_output, "mock")
            self._trace(
                run, "provider.retry_completed", f"Controlled retry ended with {retry.result_kind}."
            )
            run.status = "passed" if retry.result_kind == "passed" else "failed"
            run.progress.repair_completed = retry.result_kind == "passed"
        elif artifact.result_kind != "passed" and isinstance(
            provider_result, StructuredInterruptResult
        ):
            interrupt = ManualInterrupt(
                interrupt_id=provider_result.interrupt_id,
                expected_schema_version=provider_result.schema_version,
            )
            run.pending_interrupt = interrupt
            run.status = "awaiting_manual"
            self._trace(
                run,
                "interrupt.created",
                f"ManualProvider paused run {run.run_id} at {interrupt.interrupt_id}.",
            )
        self._refresh_completion(run)
        self._runs[run_id] = deepcopy(run)
        return cast(StructuredRun, self._remember(scope, command, run))

    def resume_manual(self, run_id: str, command: ManualStructuredRequest) -> StructuredRun:
        scope = f"manual:{run_id}"
        previous = self._commands.get((scope, command.idempotency_key))
        if previous:
            return cast(
                StructuredRun, self._remember(scope, command, cast(StructuredRun, previous.value))
            )
        run = self.get(run_id)
        interrupt = run.pending_interrupt
        if run.provider != "manual" or run.status != "awaiting_manual" or interrupt is None:
            raise StructuredOutputConflictError(
                "run has no pending manual structured-output interrupt"
            )
        if interrupt.interrupt_id != command.interrupt_id:
            raise StructuredOutputConflictError("interrupt id does not match the pending boundary")
        if interrupt.expected_schema_version != run.active_schema_version:
            raise StructuredOutputConflictError(
                "schema changed while the manual interrupt was pending"
            )
        run.pending_interrupt = None
        self._trace(run, "interrupt.resolved", f"Manual payload resumed the same run {run.run_id}.")
        artifact = self._artifact(run, command.raw_output, "manual")
        run.status = "passed" if artifact.result_kind == "passed" else "failed"
        run.progress.repair_completed = artifact.result_kind == "passed"
        self._refresh_completion(run)
        self._runs[run_id] = deepcopy(run)
        return cast(StructuredRun, self._remember(scope, command, run))

    def apply_schema(self, run_id: str, command: SchemaRevisionRequest) -> SchemaSubmissionResult:
        scope = f"schema:{run_id}"
        previous = self._commands.get((scope, command.idempotency_key))
        if previous:
            return cast(
                SchemaSubmissionResult,
                self._remember(scope, command, cast(SchemaSubmissionResult, previous.value)),
            )
        run = self.get(run_id)
        parsed, parse_errors = _safe_parse(command.schema_text)
        if parse_errors or not isinstance(parsed, dict):
            errors = parse_errors or [
                StructuredError(path="$", keyword="type", message="a JSON Schema must be an object")
            ]
            result = SchemaSubmissionResult(
                applied=False, check=SchemaCheck(kind="invalid_json", errors=errors), run=run
            )
            return cast(SchemaSubmissionResult, self._remember(scope, command, result))
        schema = parsed
        errors = _document_complexity_errors(schema)
        if not errors:
            errors = _external_reference_errors(schema)
        if not errors:
            errors = _validate_schema(schema)
        errors.extend(_schema_identity_errors(schema, command.version))
        schema_hash = _schema_hash(schema) if not _document_complexity_errors(schema) else None
        if schema_hash is not None and any(
            revision.schema_hash == schema_hash and revision.version != command.version
            for revision in run.schema_history
        ):
            errors.append(
                StructuredError(
                    path="$",
                    keyword="schema.identity",
                    message="an identical schema document cannot be aliased to a different version",
                )
            )
        if errors:
            result = SchemaSubmissionResult(
                applied=False, check=SchemaCheck(kind="schema_invalid", errors=errors), run=run
            )
            return cast(SchemaSubmissionResult, self._remember(scope, command, result))
        if any(revision.version == command.version for revision in run.schema_history):
            raise StructuredOutputConflictError(
                "schema version already exists in immutable history"
            )
        compatibility = _compatibility(run.schema_history[-1].schema_document, schema)
        if run.pending_interrupt:
            self._trace(
                run,
                "interrupt.invalidated",
                (
                    f"Schema revision {command.version} invalidated interrupt "
                    f"{run.pending_interrupt.interrupt_id}."
                ),
            )
        run.schema_history.append(
            SchemaRevision(
                version=command.version,
                schema_hash=cast(str, schema_hash),
                schema_document=deepcopy(schema),
                compatibility=compatibility,
                breaking=any(change.breaking for change in compatibility),
            )
        )
        run.active_schema_version = command.version
        run.status = "ready"
        run.pending_interrupt = None
        self._trace(
            run,
            "schema.revision_applied",
            f"Schema {command.version} became active with immutable prior artifacts.",
        )
        self._runs[run_id] = deepcopy(run)
        result = SchemaSubmissionResult(applied=True, check=SchemaCheck(kind="valid"), run=run)
        return cast(SchemaSubmissionResult, self._remember(scope, command, result))

    def record_assertion(self, run_id: str, command: AssertionRequest) -> StructuredRun:
        scope = f"assertion:{run_id}"
        previous = self._commands.get((scope, command.idempotency_key))
        if previous:
            return cast(
                StructuredRun, self._remember(scope, command, cast(StructuredRun, previous.value))
            )
        run = self.get(run_id)
        if (
            run.status != "passed"
            or not run.progress.failure_observed
            or not run.progress.repair_completed
        ):
            raise StructuredOutputConflictError(
                "course assertion requires a passed repair after an observed failure"
            )
        if run.progress.assertion_recorded:
            raise StructuredOutputConflictError("course assertion is already recorded")
        run.progress.assertion_recorded = True
        self._trace(
            run,
            "course.assertion_recorded",
            "Learner asserted the validated payload is safe to route into state.",
        )
        self._refresh_completion(run)
        self._runs[run_id] = deepcopy(run)
        return cast(StructuredRun, self._remember(scope, command, run))
