from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from agent_trace_api.models import AgentEvent

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{2,63}$")]
CommandKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")]
JsonObject = dict[str, Any]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ValidationIssue(ClosedModel):
    severity: Literal["error", "warning"]
    section: Literal["manifest", "tools", "graph", "lessons", "policies", "faults", "evals", "data"]
    path: str
    code: str
    message: str
    hint: str


class ValidationSection(ClosedModel):
    name: Literal["manifest", "tools", "graph", "lessons", "policies", "faults", "evals", "data"]
    status: Literal["passed", "failed"]
    checked: int = Field(ge=0)
    issues: list[ValidationIssue]


class ScenarioValidationReport(ClosedModel):
    schema_id: Literal["agent-trace://scenario-validation/1.0.0"] = (
        "agent-trace://scenario-validation/1.0.0"
    )
    valid: bool
    compatible: bool
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    error_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    sections: list[ValidationSection] = Field(min_length=8, max_length=8)


class ScenarioBundle(ClosedModel):
    manifest: JsonObject
    tools: JsonObject
    graph: JsonObject
    lessons: JsonObject
    policies: JsonObject
    faults: JsonObject
    evals: list[JsonObject]
    data: dict[str, Any]
    resources: list["ScenarioResource"] = Field(default_factory=list)


class ScenarioResource(ClosedModel):
    path: str = Field(min_length=1, max_length=240)
    media_type: Literal[
        "application/json",
        "application/vnd.sqlite3",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/svg+xml",
        "image/webp",
        "text/csv",
        "text/markdown",
        "text/plain",
    ]
    byte_count: int = Field(ge=0, le=5_000_000)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_base64: str = Field(max_length=7_000_000)


class ScenarioSummary(ClosedModel):
    id: Identifier
    name: str
    short_description: str
    domain: str
    difficulty: Literal["beginner", "intermediate", "advanced"]
    concepts: list[str]
    data_classification: Literal["public_fixture", "internal_fixture", "sensitive_fixture"]
    local_only: bool
    tool_count: int = Field(ge=0)
    lesson_count: int = Field(ge=0)
    golden_count: int = Field(ge=0)
    graph_template: str
    validation_status: Literal["passed", "failed"]
    compatible: bool


class ScenarioCatalog(ClosedModel):
    schema_id: Literal["agent-trace://scenario-catalog/1.0.0"] = (
        "agent-trace://scenario-catalog/1.0.0"
    )
    scenarios: list[ScenarioSummary] = Field(min_length=7, max_length=7)


class ScenarioDetail(ClosedModel):
    schema_id: Literal["agent-trace://scenario-detail/1.0.0"] = (
        "agent-trace://scenario-detail/1.0.0"
    )
    summary: ScenarioSummary
    manifest: JsonObject
    report: ScenarioValidationReport


class BuilderWrite(ClosedModel):
    idempotency_key: CommandKey
    approval: Literal["local-scenario-builder-write-approved"]


class CreateScenarioDraftRequest(BuilderWrite):
    source_scenario_id: Identifier


class SaveScenarioDraftRequest(BuilderWrite):
    expected_revision: int = Field(ge=1)
    bundle: ScenarioBundle


class ImportScenarioArchiveRequest(BuilderWrite):
    filename: str = Field(min_length=5, max_length=120, pattern=r"^[A-Za-z0-9._-]+\.zip$")
    archive_base64: str = Field(min_length=4, max_length=8_000_000)


class ScenarioDraft(ClosedModel):
    schema_id: Literal["agent-trace://scenario-draft/1.0.0"] = "agent-trace://scenario-draft/1.0.0"
    draft_id: str = Field(pattern=r"^draft_[a-f0-9]{16}$")
    revision: int = Field(ge=1)
    source_scenario_id: Identifier
    bundle: ScenarioBundle
    report: ScenarioValidationReport
    updated_at: str
    audit: list[str] = Field(min_length=1)


class ScenarioPreview(ClosedModel):
    schema_id: Literal["agent-trace://scenario-preview/1.0.0"] = (
        "agent-trace://scenario-preview/1.0.0"
    )
    source: Literal["built_in", "draft"]
    source_id: str
    scenario_id: Identifier
    title: str
    objective: str
    graph_template: str
    nodes: list[JsonObject] = Field(min_length=2)
    edges: list[JsonObject] = Field(min_length=1)
    tool_names: list[str] = Field(min_length=1)
    lesson: JsonObject
    golden_case: JsonObject
    validation_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class CreateScenarioPreviewRunRequest(BuilderWrite):
    source: Literal["built_in", "draft"]
    source_id: str = Field(min_length=3, max_length=128)
    input: str = Field(min_length=1, max_length=2000)


class ScenarioPreviewRunCommand(BuilderWrite):
    pace: Literal["fast", "observe"] = "observe"


class ScenarioPreviewNode(ClosedModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    label: str = Field(min_length=1, max_length=80)
    kind: Literal[
        "input",
        "normalize",
        "model_decision",
        "tool",
        "retrieval",
        "compute",
        "validation",
        "guardrail",
        "human_interrupt",
        "subgraph",
        "output",
        "eval",
    ]
    owner: Literal["model", "code", "tool", "human"]
    reads: list[str]
    writes: list[str]
    status: Literal["pending", "running", "completed", "error"] = "pending"


class ScenarioPreviewRunState(ClosedModel):
    schema_id: Literal["agent-trace://scenario-preview-run-state/1.0.0"] = (
        "agent-trace://scenario-preview-run-state/1.0.0"
    )
    run_id: str = Field(pattern=r"^run_[A-Za-z0-9_-]+$", max_length=128)
    source: Literal["built_in", "draft"]
    source_id: str
    scenario_id: Identifier
    status: Literal["idle", "running", "completed", "error"]
    input: str
    cursor: int = Field(ge=0)
    step_count: int = Field(ge=0)
    current_node: str | None = None
    next_node: str | None = None
    nodes: list[ScenarioPreviewNode] = Field(min_length=2)
    values: JsonObject
    last_sequence: int = Field(ge=0)
    validation_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class ScenarioPreviewRunSnapshot(ClosedModel):
    schema_id: Literal["agent-trace://scenario-preview-run-snapshot/1.0.0"] = (
        "agent-trace://scenario-preview-run-snapshot/1.0.0"
    )
    run: ScenarioPreviewRunState
    events: list[AgentEvent]


class ScenarioArchive(ClosedModel):
    schema_id: Literal["agent-trace://scenario-archive/1.0.0"] = (
        "agent-trace://scenario-archive/1.0.0"
    )
    filename: str
    media_type: Literal["application/zip"] = "application/zip"
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    byte_count: int = Field(gt=0)
    archive_base64: str
    validation_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class LearningEvidenceRequest(BuilderWrite):
    kind: Literal[
        "case_run",
        "prediction",
        "fault_repair",
        "machine_assertion",
        "explain_back",
        "artifact_export",
    ]
    response: str = Field(min_length=1, max_length=4000)
    scenario_id: Identifier


class ResetLearningProgressRequest(BuilderWrite):
    scope: Literal["module", "all"]


class LearningEvidence(ClosedModel):
    evidence_id: str = Field(pattern=r"^learn_[a-f0-9]{20}$")
    module_id: str = Field(pattern=r"^M(?:[0-9]|1[01])$")
    kind: Literal[
        "case_run",
        "prediction",
        "fault_repair",
        "machine_assertion",
        "explain_back",
        "artifact_export",
    ]
    scenario_id: Identifier
    passed: bool
    summary: str
    created_at: str


class LearningModule(ClosedModel):
    id: str = Field(pattern=r"^M(?:[0-9]|1[01])$")
    title: str
    concepts: list[str] = Field(min_length=1)
    estimated_minutes: int = Field(ge=25, le=50)
    prerequisite: str | None
    recommended_scenario: Identifier
    lens_copy: dict[Literal["pm", "builder", "architect"], str]
    mastery_level: Literal["not_started", "observe", "diagnose", "design", "stress", "ship"]
    completed: bool
    evidence: list[LearningEvidence]


class LearningMap(ClosedModel):
    schema_id: Literal["agent-trace://learning-map/1.0.0"] = "agent-trace://learning-map/1.0.0"
    modules: list[LearningModule] = Field(min_length=12, max_length=12)
    completed_modules: int = Field(ge=0, le=12)
    evidence_count: int = Field(ge=0)
    completion_rule: str


class CapstoneReport(ClosedModel):
    schema_id: Literal["agent-trace://capstone-report/1.0.0"] = (
        "agent-trace://capstone-report/1.0.0"
    )
    title: str
    scenario_id: Identifier
    validation_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    validation_passed: bool
    mastery_evidence_count: int = Field(ge=0)
    architecture_sections: list[str] = Field(min_length=10, max_length=10)
    markdown: str
