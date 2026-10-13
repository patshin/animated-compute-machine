from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from agent_trace_api.models import AgentEvent, RunId, StrictModel
from agent_trace_api.tool_calling import ActorRole, ToolRisk

ApprovalScenarioId = Literal[
    "customer-refund",
    "purchase-approval",
    "incident-rollback",
    "risk-case-write",
]
ApprovalDecision = Literal["approve", "reject", "edit", "request_more_evidence"]
ApprovalRunStatus = Literal["interrupted", "completed", "rejected"]
StalenessDimension = Literal["data", "tool", "approval"]


class ApprovalMatrixEntry(StrictModel):
    risk: ToolRisk
    execution: Literal["allowed", "approval_required", "denied"]
    explanation: str


class ApprovalScenario(StrictModel):
    id: ApprovalScenarioId
    label: str
    summary: str
    action: str
    tool_version: str
    target: str
    default_args: dict[str, JsonValue]
    editable_schema: dict[str, JsonValue]
    evidence: list[str]
    reason: str
    side_effects: list[str]
    required_permission: str


class ApprovalCatalog(StrictModel):
    schema_id: Literal["agent-trace://approval-catalog/1.0.0"] = (
        "agent-trace://approval-catalog/1.0.0"
    )
    scenarios: list[ApprovalScenario]
    matrix: list[ApprovalMatrixEntry]


class VersionReferences(StrictModel):
    data_version: int = Field(ge=1)
    tool_version: str
    target_version: int = Field(ge=1)


class ApprovalInterrupt(StrictModel):
    schema_id: Literal["agent-trace://approval-interrupt/1.0.0"] = (
        "agent-trace://approval-interrupt/1.0.0"
    )
    interrupt_id: str = Field(pattern=r"^int_[A-Za-z0-9_-]+$")
    title: str
    exact_action: str
    exact_target: str
    arguments: dict[str, JsonValue]
    editable_schema: dict[str, JsonValue]
    evidence: list[str]
    reason: str
    risk: Literal["write"] = "write"
    side_effects: list[str]
    idempotency_key_summary: str
    created_at: datetime
    expires_at: datetime
    version_references: VersionReferences
    available_actions: list[ApprovalDecision]


class ApprovalDiffEntry(StrictModel):
    path: str = Field(pattern=r"^\$\.[a-z][a-z0-9_]*$")
    before: JsonValue
    after: JsonValue


class ApprovalReceipt(StrictModel):
    receipt_id: str = Field(pattern=r"^receipt_[A-Za-z0-9_-]+$")
    action: str
    target: str
    arguments: dict[str, JsonValue]
    effect_key: str
    committed_at: datetime
    checkpoint_version: int = Field(ge=1)
    idempotent_replay: bool = False


class ApprovalAuditEntry(StrictModel):
    sequence: int = Field(ge=1)
    actor_role: ActorRole
    action: str
    resource: str
    decision: str
    policy_version: Literal["agent-trace://approval-policy/1.0.0"] = (
        "agent-trace://approval-policy/1.0.0"
    )
    approval_id: str | None = None
    idempotency_summary: str
    result: str
    timestamp: datetime
    run_id: RunId
    redacted_paths: list[str] = Field(default_factory=list)


class ApprovalCheckpoint(StrictModel):
    checkpoint_id: str = Field(pattern=r"^cp_hitl_[A-Za-z0-9_-]+$")
    version: int = Field(ge=1)
    reason: str
    state_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime


class ApprovalRun(StrictModel):
    schema_id: Literal["agent-trace://approval-run/1.0.0"] = "agent-trace://approval-run/1.0.0"
    run_id: RunId
    scenario_id: ApprovalScenarioId
    status: ApprovalRunStatus
    proposer_role: ActorRole
    user_message: str
    checkpoint_version: int = Field(ge=1)
    current_arguments: dict[str, JsonValue]
    argument_diff: list[ApprovalDiffEntry] = Field(default_factory=list)
    interrupt: ApprovalInterrupt | None
    receipt: ApprovalReceipt | None = None
    outcome_message: str
    last_outcome: Literal[
        "interrupted",
        "approved",
        "edited_and_approved",
        "rejected",
        "evidence_added",
        "reapproval_required",
        "permission_denied",
    ]
    events: list[AgentEvent]
    audit: list[ApprovalAuditEntry]
    checkpoints: list[ApprovalCheckpoint]
    integrity_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    command_replayed: bool = False


class StartApprovalRunRequest(StrictModel):
    scenario_id: ApprovalScenarioId
    proposer_role: ActorRole = "learner"
    user_message: str = Field(min_length=3, max_length=500)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")


class ResumeApprovalRequest(StrictModel):
    interrupt_id: str = Field(pattern=r"^int_[A-Za-z0-9_-]+$")
    decision: ApprovalDecision
    actor_role: ActorRole
    expected_checkpoint_version: int = Field(ge=1)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
    edited_arguments: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def validate_edit_payload(self) -> ResumeApprovalRequest:
        if self.decision == "edit" and self.edited_arguments is None:
            raise ValueError("edited_arguments is required for an edit decision")
        if self.decision != "edit" and self.edited_arguments is not None:
            raise ValueError("edited_arguments is allowed only for an edit decision")
        return self


class StalenessRequest(StrictModel):
    dimension: StalenessDimension
    expected_checkpoint_version: int = Field(ge=1)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")


class ApprovalEffectSummary(StrictModel):
    run_id: RunId
    effect_count: int = Field(ge=0)
    latest_receipt: ApprovalReceipt | None = None
