from typing import Annotated, Literal

from pydantic import Field, JsonValue, StringConstraints, model_validator

from agent_trace_api.models import AgentEvent, ApprovedCommand, RunId, StrictModel

SecuritySeverity = Literal["P0", "P1", "P2", "P3"]
SecurityLayer = Literal[
    "input_limit",
    "schema_validation",
    "content_isolation",
    "tool_allowlist",
    "authorization",
    "policy",
    "approval",
    "safe_executor",
    "output_validation",
    "redaction",
    "import_validation",
    "browser_sanitization",
    "codex_sandbox",
]

CaseId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{2,95}$", max_length=96)]


def _default_suite_severities() -> list[Literal["P0", "P1"]]:
    return ["P0", "P1"]


class SecurityPolicySummary(StrictModel):
    policy_id: Literal["agent-trace://security-policy/1.0.0"] = (
        "agent-trace://security-policy/1.0.0"
    )
    input_limit_chars: int = Field(ge=1)
    provider_output_limit_bytes: int = Field(ge=1)
    codex_timeout_ms: int = Field(ge=1)
    codex_concurrency: Literal[1] = 1
    network_default: Literal["off"] = "off"
    codex_sandbox: Literal["read-only"] = "read-only"
    scenario_max_compressed_bytes: int = Field(ge=1)
    scenario_max_uncompressed_bytes: int = Field(ge=1)
    scenario_max_files: int = Field(ge=1)


class CodexCapability(StrictModel):
    detected: bool
    enabled: bool
    label: Literal["LOCAL PROTOTYPE PROVIDER"] = "LOCAL PROTOTYPE PROVIDER"
    binary_name: str | None = None
    fixed_argv: list[str]
    inherited_environment: list[str]
    policy: SecurityPolicySummary


class AttackCaseDefinition(StrictModel):
    id: CaseId
    title: str = Field(min_length=3, max_length=100)
    lesson: str = Field(min_length=10, max_length=320)
    vector: Literal[
        "user_input",
        "retrieval",
        "provider_output",
        "tool_output",
        "approval",
        "checkpoint",
        "scenario_import",
        "trace",
        "remote_artifact",
    ]
    severity: SecuritySeverity
    expected_layer: SecurityLayer
    payload_preview: str = Field(min_length=1, max_length=500)


class SecurityCatalog(StrictModel):
    schema_id: Literal["agent-trace://security-catalog/1.0.0"] = (
        "agent-trace://security-catalog/1.0.0"
    )
    cases: list[AttackCaseDefinition] = Field(min_length=10)
    evaluation_case_count: int = Field(ge=1)
    critical_evaluation_case_count: int = Field(ge=1)
    defense_layers: list[SecurityLayer]
    codex: CodexCapability
    policy: SecurityPolicySummary


class RunAttackRequest(ApprovedCommand):
    case_id: CaseId
    user_text: str = Field(min_length=1, max_length=2_000)


class SecurityLayerCheck(StrictModel):
    sequence: int = Field(ge=1)
    layer: SecurityLayer
    status: Literal["passed", "blocked", "skipped"]
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    detail: str = Field(min_length=3, max_length=300)


class ProviderPrivacyPreview(StrictModel):
    input_chars: int = Field(ge=0)
    included_fields: list[str]
    withheld_fields: list[str]
    secret_patterns_redacted: int = Field(ge=0)
    network_access: Literal[False] = False
    context_boundary: Literal["instruction-separated-from-untrusted-data"] = (
        "instruction-separated-from-untrusted-data"
    )


class SafeContentBlock(StrictModel):
    kind: Literal["text", "code", "link", "blocked_image"]
    text: str = Field(min_length=1, max_length=1_000)
    href: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_href(self) -> "SafeContentBlock":
        if (self.kind == "link") != (self.href is not None):
            raise ValueError("only safe link blocks may expose a destination")
        return self


class SecurityAttackRun(StrictModel):
    schema_id: Literal["agent-trace://security-attack-run/1.0.0"] = (
        "agent-trace://security-attack-run/1.0.0"
    )
    run_id: RunId
    case: AttackCaseDefinition
    status: Literal["passed", "failed"]
    expected_layer: SecurityLayer
    actual_layer: SecurityLayer
    outcome: str = Field(min_length=3, max_length=400)
    checks: list[SecurityLayerCheck] = Field(min_length=1)
    privacy: ProviderPrivacyPreview
    safe_content: list[SafeContentBlock]
    authorized_tool_calls: int = Field(ge=0)
    side_effect_count: int = Field(ge=0)
    redaction_count: int = Field(ge=0)
    events: list[AgentEvent] = Field(min_length=3)


class RunAttackSuiteRequest(ApprovedCommand):
    include_severities: list[Literal["P0", "P1"]] = Field(
        default_factory=_default_suite_severities, min_length=1
    )


class AttackSuiteCaseResult(StrictModel):
    case_id: str
    scenario_id: str
    probe_scenario_id: str
    severity: Literal["P0", "P1"]
    status: Literal["passed", "failed"]
    expected_layer: SecurityLayer
    actual_layer: SecurityLayer
    assertions_passed: int = Field(ge=0)
    assertions_total: int = Field(ge=1)


class AttackCaseRuntimeOutcome(StrictModel):
    """Repository-owned projection of a real security probe for fixture assertions."""

    fixture_scenario_id: str
    probe_scenario_id: str
    run_status: Literal["completed", "failed"]
    retrieved_instruction_effects: int = Field(ge=0)
    unapproved_writes: int = Field(ge=0)
    duplicate_effects: int = Field(ge=0)
    secret_leaks: int = Field(ge=0)
    answer_schema_version: Literal["1.0.0"] = "1.0.0"
    actual_layer: SecurityLayer


class AttackSuiteResult(StrictModel):
    schema_id: Literal["agent-trace://security-suite-result/1.0.0"] = (
        "agent-trace://security-suite-result/1.0.0"
    )
    suite_id: str = Field(pattern=r"^suite_[A-Za-z0-9_-]+$")
    status: Literal["passed", "failed"]
    total: int = Field(ge=1)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    cases: list[AttackSuiteCaseResult] = Field(min_length=1)


class ScenarioImportRequest(ApprovedCommand):
    filename: str = Field(min_length=5, max_length=160, pattern=r"^[A-Za-z0-9._-]+\.zip$")
    content_base64: str = Field(min_length=4, max_length=600_000)


class ImportedFileDigest(StrictModel):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(ge=0)


class ScenarioImportResult(StrictModel):
    schema_id: Literal["agent-trace://scenario-import-result/1.0.0"] = (
        "agent-trace://scenario-import-result/1.0.0"
    )
    scenario_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    package_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["imported", "idempotent_replay"]
    file_count: int = Field(ge=1)
    compressed_bytes: int = Field(ge=1)
    uncompressed_bytes: int = Field(ge=1)
    files: list[ImportedFileDigest] = Field(min_length=1)
    destination: str = Field(pattern=r"^\.local/scenario-imports/[a-z0-9-]+$")
    audit_message: str


class CodexEnableRequest(ApprovedCommand):
    enabled: bool


class CodexRunRequest(ApprovedCommand):
    prompt: str = Field(min_length=1, max_length=4_000)


class CodexCancelRequest(ApprovedCommand):
    pass


class ProviderMetadata(StrictModel):
    provider: Literal["codex_cli"]
    mode: str
    simulated: bool
    model: str | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    usage: dict[str, JsonValue] | None = None


class ProviderDecision(StrictModel):
    schema_version: Literal["1.0.0"]
    decision_id: str = Field(pattern=r"^dec_[A-Za-z0-9_-]+$")
    kind: Literal[
        "route", "tool_request", "structured_answer", "repair", "clarification", "evaluation"
    ]
    payload: dict[str, JsonValue]
    confidence: float = Field(ge=0, le=1)
    provider_metadata: ProviderMetadata


class CodexRunResult(StrictModel):
    schema_id: Literal["agent-trace://codex-cli-result/1.0.0"] = (
        "agent-trace://codex-cli-result/1.0.0"
    )
    request_id: str = Field(pattern=r"^codexreq_[A-Za-z0-9_-]+$")
    status: Literal["succeeded", "failed", "timed_out", "cancelled", "output_limited"]
    decision: ProviderDecision | None = None
    duration_ms: int = Field(ge=0)
    stdout_bytes: int = Field(ge=0)
    stderr_bytes: int = Field(ge=0)
    redaction_count: int = Field(ge=0)
    audit: list[str] = Field(min_length=1)
    safe_error: str | None = None


class SecurityDiagnostic(StrictModel):
    schema_id: Literal["agent-trace://security-diagnostic/1.0.0"] = (
        "agent-trace://security-diagnostic/1.0.0"
    )
    run_id: RunId
    policy_id: Literal["agent-trace://security-policy/1.0.0"]
    case_id: CaseId
    status: Literal["passed", "failed"]
    expected_layer: SecurityLayer
    actual_layer: SecurityLayer
    event_types: list[str]
    redaction_summary: str
    contains_raw_input: Literal[False] = False


class SecurityInputError(RuntimeError):
    pass


class SecurityConflictError(RuntimeError):
    pass
