from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, JsonValue, StringConstraints, model_validator

from agent_trace_api.models import ApprovedCommand, StrictModel

TraceId = Annotated[str, StringConstraints(pattern=r"^trace_[a-f0-9]{16}$")]
SpanId = Annotated[str, StringConstraints(pattern=r"^span_[a-f0-9]{16}$")]
EvalRunId = Annotated[str, StringConstraints(pattern=r"^run_eval_[a-f0-9]{16}$")]


class EvalAssertionDefinition(StrictModel):
    kind: Literal[
        "route",
        "node",
        "tool",
        "argument",
        "evidence",
        "answer",
        "security",
        "performance",
        "schema",
    ]
    path: str = Field(min_length=1, max_length=240)
    operator: Literal[
        "equals",
        "contains",
        "not_contains",
        "exists",
        "not_exists",
        "lte",
        "gte",
        "set_equals",
        "subset",
        "matches",
    ]
    expected: JsonValue = None
    message: str | None = Field(default=None, max_length=300)
    critical: bool = False


class EvalCaseDefinition(StrictModel):
    schema_version: Literal["1.0.0"]
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,95}$")
    scenario_id: str = Field(min_length=1, max_length=80)
    module_id: str = Field(pattern=r"^M(?:[0-9]|1[01])$")
    input: dict[str, JsonValue]
    provider: Literal["mock", "replay", "manual_fixture", "codex_cli_optional"]
    seed: int = Field(ge=0)
    assertions: list[EvalAssertionDefinition] = Field(min_length=1)
    tags: list[str]
    severity: Literal["P0", "P1", "P2", "P3"]
    performance_budget_ms: int | None = Field(default=None, ge=1)


class EvalScenarioRuntimeProfile(StrictModel):
    """Versioned scenario inputs consumed by the E9 Golden runtime.

    This is deliberately an eval-owned profile rather than a full Scenario Kit:
    E11 owns authoring/export of complete Kits. The profile still prevents the
    Golden runner from silently projecting seven labels over one trajectory.
    """

    schema_version: Literal["1.0.0"] = "1.0.0"
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,79}$")
    graph_profile: Literal["durable-parallel-evidence@1.0.0"]
    route_intent: str = Field(pattern=r"^[a-z][a-z0-9-]{2,79}$")
    tool_name: Literal["orders.get_status", "analytics.query_metric"]
    tool_arguments: dict[str, JsonValue]
    actor_role: Literal["learner", "reviewer", "approver", "system"]
    retrieval_scenario_id: Literal["research-synthesis", "risk-investigation"] | None = None
    scenario_kit_ref: str | None = Field(default=None, pattern=r"^scenarios/[a-z0-9-]+$")


class TraceSpan(StrictModel):
    span_id: SpanId
    trace_id: TraceId
    parent_span_id: SpanId | None = None
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["run", "node", "provider", "tool", "retrieval", "evaluation"]
    node_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,63}$")
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    status: Literal["ok", "error", "blocked"]
    causation_id: str = Field(min_length=1, max_length=128)
    correlation_id: str = Field(min_length=1, max_length=128)
    provider_id: str | None = Field(default=None, min_length=1, max_length=120)
    tool_name: str | None = Field(default=None, min_length=3, max_length=120)
    input_ref: str | None = Field(default=None, min_length=1, max_length=240)
    output_ref: str | None = Field(default=None, min_length=1, max_length=240)
    error: str | None = Field(default=None, min_length=1, max_length=300)
    redacted_paths: list[str] = Field(default_factory=list)
    attributes: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_timing(self) -> TraceSpan:
        if self.end_ms < self.start_ms or self.end_ms - self.start_ms != self.duration_ms:
            raise ValueError("trace span timing must be internally consistent")
        return self


class TraceEvent(StrictModel):
    event_id: str = Field(pattern=r"^evt_eval_[a-f0-9]{16}$")
    trace_id: TraceId
    run_id: EvalRunId
    sequence: int = Field(ge=1)
    type: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
    timestamp_ms: int = Field(ge=0)
    span_id: SpanId
    node_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,63}$")
    causation_id: str = Field(min_length=1, max_length=128)
    correlation_id: str = Field(min_length=1, max_length=128)
    payload_version: Literal["1.0.0"] = "1.0.0"
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    summary: str = Field(min_length=1, max_length=240)
    payload: dict[str, JsonValue]
    redacted_paths: list[str] = Field(default_factory=list)


class EvalAssertionResult(StrictModel):
    assertion_id: str = Field(pattern=r"^assert_[a-f0-9]{16}$")
    kind: str
    path: str
    operator: str
    expected: JsonValue = None
    actual: JsonValue = None
    critical: bool
    passed: bool
    message: str
    span_id: SpanId


class EvalRun(StrictModel):
    schema_id: Literal["agent-trace://eval-run/1.0.0"] = "agent-trace://eval-run/1.0.0"
    run_id: EvalRunId
    trace_id: TraceId
    case_id: str
    scenario_id: str
    module_id: str
    provider: Literal["mock"] = "mock"
    seed: int = Field(ge=0)
    status: Literal["passed", "failed"]
    answer: str
    output: dict[str, JsonValue]
    duration_ms: int = Field(ge=0)
    spans: list[TraceSpan] = Field(min_length=1)
    events: list[TraceEvent] = Field(min_length=1)
    assertions: list[EvalAssertionResult] = Field(min_length=1)
    score: float = Field(ge=0, le=1)
    critical_failures: int = Field(ge=0)
    created_at: str
    integrity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_trace_links(self) -> EvalRun:
        span_ids = {span.span_id for span in self.spans}
        if len(span_ids) != len(self.spans):
            raise ValueError("trace span IDs must be unique")
        if [event.sequence for event in self.events] != list(range(1, len(self.events) + 1)):
            raise ValueError("trace event sequences must be contiguous and one-based")
        if len({event.event_id for event in self.events}) != len(self.events):
            raise ValueError("trace event IDs must be unique")
        if any(
            event.run_id != self.run_id
            or event.trace_id != self.trace_id
            or event.span_id not in span_ids
            for event in self.events
        ):
            raise ValueError("trace events must remain linked to their owning run and span")
        if any(span.trace_id != self.trace_id for span in self.spans):
            raise ValueError("trace spans must remain linked to their owning trace")
        if any(
            span.parent_span_id is not None and span.parent_span_id not in span_ids
            for span in self.spans
        ):
            raise ValueError("trace span parents must belong to the same trace")
        if [event.timestamp_ms for event in self.events] != sorted(
            event.timestamp_ms for event in self.events
        ):
            raise ValueError("trace event timestamps must be monotonic")
        if any(assertion.span_id not in span_ids for assertion in self.assertions):
            raise ValueError("evaluation assertions must remain linked to a trace span")
        span_by_id = {span.span_id: span for span in self.spans}
        if any(
            not (
                span_by_id[event.span_id].start_ms
                <= event.timestamp_ms
                <= span_by_id[event.span_id].end_ms
            )
            for event in self.events
        ):
            raise ValueError("trace event timestamps must remain inside their linked span")
        return self


class ScenarioMetric(StrictModel):
    scenario_id: str
    total: int = Field(ge=1)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    pass_rate: float = Field(ge=0, le=1)


class FailureCluster(StrictModel):
    category: str
    count: int = Field(ge=1)
    case_ids: list[str] = Field(min_length=1)


class FailureBreakdown(StrictModel):
    key: str = Field(min_length=1, max_length=80)
    total: int = Field(ge=1)
    failures: int = Field(ge=0)

    @model_validator(mode="after")
    def failures_fit_total(self) -> FailureBreakdown:
        if self.failures > self.total:
            raise ValueError("breakdown failures cannot exceed total")
        return self


class EvalDashboard(StrictModel):
    total: int = Field(ge=1)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    pass_rate: float = Field(ge=0, le=1)
    critical_security_failures: int = Field(ge=0)
    security_gate: Literal["passed", "failed"]
    route_accuracy: float = Field(ge=0, le=1)
    tool_selection_accuracy: float = Field(ge=0, le=1)
    argument_accuracy: float = Field(ge=0, le=1)
    citation_coverage: float = Field(ge=0, le=1)
    unsafe_action_count: int = Field(ge=0)
    p50_duration_ms: int = Field(ge=0)
    p95_duration_ms: int = Field(ge=0)
    baseline_id: str = Field(min_length=1, max_length=120)
    baseline_pass_rate: float = Field(ge=0, le=1)
    baseline_delta: float
    scenarios: list[ScenarioMetric] = Field(min_length=1)
    failures_by_module: list[FailureBreakdown] = Field(min_length=1)
    failures_by_provider: list[FailureBreakdown] = Field(min_length=1)
    failure_clusters: list[FailureCluster]


class EvalCatalog(StrictModel):
    schema_id: Literal["agent-trace://eval-catalog/1.0.0"] = "agent-trace://eval-catalog/1.0.0"
    dataset_id: Literal["golden-cases@1.0.0"] = "golden-cases@1.0.0"
    case_count: int = Field(ge=35)
    scenario_count: int = Field(ge=7)
    scenarios: list[str] = Field(min_length=7)
    evaluator_kinds: list[str] = Field(min_length=1)
    answer_version_mapping: str
    otel_enabled: Literal[False] = False


class RunEvalBatchRequest(ApprovedCommand):
    target_count: int = Field(default=70, ge=1, le=100)


class EvalBatch(StrictModel):
    schema_id: Literal["agent-trace://eval-batch/1.0.0"] = "agent-trace://eval-batch/1.0.0"
    batch_id: str = Field(pattern=r"^batch_[a-f0-9]{16}$")
    dataset_id: Literal["golden-cases@1.0.0"] = "golden-cases@1.0.0"
    target_count: int = Field(ge=1, le=100)
    status: Literal["passed", "failed"]
    elapsed_ms: int = Field(ge=0)
    dashboard: EvalDashboard
    runs: list[EvalRun] = Field(min_length=1, max_length=100)
    created_at: str
    integrity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def counts_match(self) -> EvalBatch:
        if len(self.runs) != self.target_count or self.dashboard.total != self.target_count:
            raise ValueError("batch counts must match target_count")
        passed = sum(run.status == "passed" for run in self.runs)
        if self.dashboard.passed != passed or self.dashboard.failed != self.target_count - passed:
            raise ValueError("batch dashboard result counts must match its runs")
        expected_status = (
            "passed"
            if self.dashboard.failed == 0 and self.dashboard.security_gate == "passed"
            else "failed"
        )
        if self.status != expected_status:
            raise ValueError("batch status must match dashboard gates")
        return self


class EventPage(StrictModel):
    run_id: EvalRunId
    after_sequence: int = Field(ge=0)
    next_after_sequence: int = Field(ge=0)
    has_more: bool
    events: list[TraceEvent] = Field(max_length=100)


class CreateComparePairRequest(ApprovedCommand):
    case_id: str = Field(default="data-analyst-tool-boundary", min_length=3, max_length=96)


class CompareSection(StrictModel):
    section: Literal[
        "config",
        "trajectory",
        "state",
        "tools",
        "evidence",
        "answer",
        "evaluation",
        "duration",
    ]
    left: JsonValue
    right: JsonValue
    changed: bool


class RunComparison(StrictModel):
    schema_id: Literal["agent-trace://run-comparison/1.0.0"] = "agent-trace://run-comparison/1.0.0"
    comparison_id: str = Field(pattern=r"^compare_[a-f0-9]{16}$")
    left: EvalRun
    right: EvalRun
    same_final_answer: bool
    unsafe_trajectory_detected: bool
    security_gate: Literal["failed"] = "failed"
    sections: list[CompareSection] = Field(min_length=8, max_length=8)


class ExportRunRequest(ApprovedCommand):
    run_id: EvalRunId


class ExportFile(StrictModel):
    filename: str = Field(pattern=r"^[a-zA-Z0-9._-]+$")
    media_type: Literal["application/json", "application/x-ndjson", "text/markdown"]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content: str


class ExportManifestEntry(StrictModel):
    filename: str = Field(pattern=r"^[a-zA-Z0-9._-]+$")
    media_type: Literal["application/json", "application/x-ndjson", "text/markdown"]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ExportManifest(StrictModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    run_id: EvalRunId
    dataset_id: Literal["golden-cases@1.0.0"] = "golden-cases@1.0.0"
    redaction_policy: Literal["agent-trace://redaction/e9-safe-export/1.0.0"] = (
        "agent-trace://redaction/e9-safe-export/1.0.0"
    )
    redacted_paths: list[str]
    files: list[ExportManifestEntry] = Field(min_length=4, max_length=4)


class ExportBundle(StrictModel):
    schema_id: Literal["agent-trace://export-bundle/1.0.0"] = "agent-trace://export-bundle/1.0.0"
    bundle_id: str = Field(pattern=r"^export_[a-f0-9]{16}$")
    run_id: EvalRunId
    redaction_policy: Literal["agent-trace://redaction/e9-safe-export/1.0.0"] = (
        "agent-trace://redaction/e9-safe-export/1.0.0"
    )
    manifest: ExportManifest
    files: list[ExportFile] = Field(min_length=4, max_length=4)


class OtelStatus(StrictModel):
    schema_id: Literal["agent-trace://otel-status/1.0.0"] = "agent-trace://otel-status/1.0.0"
    enabled: Literal[False] = False
    endpoint: None = None
    mode: Literal["disabled-by-default"] = "disabled-by-default"
    mapped_fields: list[str]
    excluded_fields: list[str]
