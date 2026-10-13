from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)

StrictId = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{2,127}$")]
EventId = Annotated[str, StringConstraints(pattern=r"^evt_[A-Za-z0-9_-]+$", max_length=128)]
RunId = Annotated[str, StringConstraints(pattern=r"^run_[A-Za-z0-9_-]+$", max_length=128)]
NodeId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(StrictModel):
    status: Literal["ok"]
    service: Literal["agent-trace-api"]
    version: str


class ReadinessResponse(StrictModel):
    status: Literal["ready"]
    service: Literal["agent-trace-api"]
    version: str
    deployment_profile: Literal["local", "public-preview"]
    storage: Literal["ready"]


class ToolVersion(StrictModel):
    name: str
    version: str


class CapabilitiesResponse(StrictModel):
    offline_ready: bool
    providers: list[Literal["mock"]]
    python_version: str
    tools: list[ToolVersion]
    deployment_profile: Literal["local", "public-preview"]
    display_mode: Literal["LOCAL WORKSTATION", "PUBLIC PREVIEW / SAFE LAB MODE"]
    safe_lab_mode: bool
    disabled_capabilities: list[
        Literal[
            "codex-cli",
            "external-network",
            "filesystem-access",
            "process-execution",
            "scenario-import-export",
            "persistent-memory-writes",
            "anonymous-learning-writes",
        ]
    ]


RunStatus = Literal["idle", "running", "completed", "error"]
NodeStatus = Literal["pending", "running", "completed", "error"]


class ScenarioNode(StrictModel):
    id: NodeId
    label: str = Field(min_length=1, max_length=80)
    lane: Literal["graph", "provider", "tool", "policy"]
    summary: str = Field(min_length=1, max_length=240)


class ScenarioDefinition(StrictModel):
    schema_version: Literal["1.0.0"]
    scenario_id: Literal["data-analyst"]
    case_id: Literal["case-1"]
    title: str
    description: str
    default_input: str
    nodes: list[ScenarioNode] = Field(min_length=1)


class NodeSnapshot(StrictModel):
    id: NodeId
    label: str
    lane: Literal["graph", "provider", "tool", "policy"]
    summary: str
    status: NodeStatus = "pending"


class ChartPoint(StrictModel):
    x: str = Field(min_length=1, max_length=40)
    y: float


class ChartSeries(StrictModel):
    label: str = Field(min_length=1, max_length=80)
    color: Literal["teal", "orange", "purple"]
    points: list[ChartPoint] = Field(min_length=2, max_length=24)


class LabLineChartArtifact(StrictModel):
    schema_id: Literal["agent-trace://lab-line-chart/1.0.0"] = "agent-trace://lab-line-chart/1.0.0"
    schema_version: Literal["1.0.0"]
    kind: Literal["line"]
    title: str = Field(min_length=1, max_length=120)
    x_label: str = Field(min_length=1, max_length=60)
    y_label: str = Field(min_length=1, max_length=60)
    source: str = Field(min_length=1, max_length=160)
    takeaway: str = Field(min_length=1, max_length=300)
    series: list[ChartSeries] = Field(min_length=1, max_length=3)


class LabAnswerPayload(StrictModel):
    schema_id: Literal["agent-trace://lab-answer/1.0.0"] = "agent-trace://lab-answer/1.0.0"
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=500)
    bullets: list[str] = Field(min_length=1, max_length=5)
    chart: LabLineChartArtifact


class StateChange(StrictModel):
    path: Annotated[str, StringConstraints(pattern=r"^(values\.[a-z][a-z0-9_]*|answer)$")]
    before: JsonValue = None
    after: JsonValue
    source_node: NodeId


class StatePatchPayload(StrictModel):
    changes: list[StateChange] = Field(min_length=1)
    provenance: str = Field(min_length=1, max_length=240)


class RedactionInfo(StrictModel):
    redacted: bool = False
    paths: list[str] = Field(default_factory=list)


class LabRunState(StrictModel):
    schema_id: Literal["agent-trace://lab-run-state/1.0.0"] = "agent-trace://lab-run-state/1.0.0"
    run_id: RunId
    scenario_id: Literal["data-analyst"]
    case_id: Literal["case-1"]
    status: RunStatus
    input: str
    seed: int = Field(ge=0, le=2_147_483_647)
    cursor: int = Field(ge=0)
    step_count: int = Field(ge=0)
    current_node: NodeId | None = None
    nodes: list[NodeSnapshot]
    values: dict[str, JsonValue]
    answer: LabAnswerPayload | None = None
    last_sequence: int = Field(ge=0)


class AgentEvent(StrictModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    event_id: EventId
    run_id: RunId
    sequence: int = Field(ge=1)
    type: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")]
    timestamp: datetime
    node_id: NodeId | None = None
    causation_id: Annotated[str, StringConstraints(min_length=1)] | None = None
    correlation_id: Annotated[str, StringConstraints(min_length=1)]
    payload_version: Literal["1.0.0"] = "1.0.0"
    payload: dict[str, JsonValue]
    redaction: RedactionInfo = Field(default_factory=RedactionInfo)


class LabRunSnapshot(StrictModel):
    schema_id: Literal["agent-trace://lab-run-snapshot/1.0.0"] = (
        "agent-trace://lab-run-snapshot/1.0.0"
    )
    run: LabRunState
    events: list[AgentEvent]


class ApprovedCommand(StrictModel):
    approved: bool
    idempotency_key: StrictId

    @model_validator(mode="after")
    def require_approval(self) -> "ApprovedCommand":
        if not self.approved:
            raise ValueError("explicit approval is required")
        return self


class StartRunRequest(ApprovedCommand):
    scenario_id: Literal["data-analyst"] = "data-analyst"
    case_id: Literal["case-1"] = "case-1"
    input: str = Field(min_length=3, max_length=500)
    seed: int = Field(default=2026, ge=0, le=2_147_483_647)


class StepRunRequest(ApprovedCommand):
    pace: Literal["fast", "observe"] = "observe"


class ResetRunRequest(ApprovedCommand):
    pass


class ReplayResponse(StrictModel):
    run: LabRunState
    source: Literal["stored-events-only"] = "stored-events-only"
    event_count: int = Field(ge=1)


class NormalizedInput(StrictModel):
    question: str
    metric: Literal["revenue"]
    period: Literal["2025-Q1..2025-Q4"]


class RouteDecision(StrictModel):
    route: Literal["metric-analysis"]
    rationale: str


class MetricQueryInput(StrictModel):
    metric: Literal["revenue"]
    periods: list[Literal["2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4"]]


class MetricRow(StrictModel):
    period: Literal["2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4"]
    revenue_musd: float = Field(ge=0)


class MetricQueryResult(StrictModel):
    rows: list[MetricRow] = Field(min_length=4, max_length=4)
    source: Literal["fictional://northstar/revenue-2025"]


class CalculationResult(StrictModel):
    q1_revenue_musd: float
    q4_revenue_musd: float
    absolute_change_musd: float
    growth_percent: float
    strongest_quarter: Literal["2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4"]


class ValidationResult(StrictModel):
    valid: Literal[True]
    checks: list[str] = Field(min_length=3)
