from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, JsonValue, StringConstraints, model_validator

from agent_trace_api.models import AgentEvent, ApprovedCommand, NodeId, RunId, StrictId, StrictModel

CheckpointId = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$"),
]
IntegrityHash = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]

DurableStatus = Literal[
    "idle",
    "running",
    "paused",
    "completed",
    "cancelled",
    "error",
]
DurableNodeStatus = Literal["pending", "running", "completed", "error", "cancelled"]
DurableCommandName = Literal["start", "step", "run", "resume"]


class DurableNodeSnapshot(StrictModel):
    id: NodeId
    label: str = Field(min_length=1, max_length=80)
    kind: Literal["input", "router", "compute", "retrieval", "subgraph", "validation", "output"]
    lane: Literal["graph", "provider", "evidence", "policy", "output"]
    parent_id: NodeId | None = None
    status: DurableNodeStatus = "pending"
    started_sequence: int | None = Field(default=None, ge=1)
    completed_sequence: int | None = Field(default=None, ge=1)


class DurableCheckpoint(StrictModel):
    checkpoint_id: CheckpointId
    run_id: RunId
    thread_id: StrictId
    sequence: int = Field(ge=1)
    step_count: int = Field(ge=1)
    reason: Literal["step", "fork", "override"]
    state_schema_version: Literal["1.0.0"]
    scenario_version: Literal["1.0.0"]
    provider_version: Literal["mock-1.0.0"]
    integrity_hash: IntegrityHash
    next_nodes: list[NodeId]
    created_at: datetime


class DurableRunState(StrictModel):
    schema_id: Literal["agent-trace://durable-run-state/1.0.0"] = (
        "agent-trace://durable-run-state/1.0.0"
    )
    schema_version: Literal["1.0.0"] = "1.0.0"
    run_id: RunId
    thread_id: StrictId
    parent_run_id: RunId | None = None
    forked_from_checkpoint_id: CheckpointId | None = None
    cycle: int = Field(ge=1)
    scenario_id: Literal["durability-lab"] = "durability-lab"
    scenario_version: Literal["1.0.0"] = "1.0.0"
    provider_id: Literal["mock"] = "mock"
    provider_version: Literal["mock-1.0.0"] = "mock-1.0.0"
    mode: Literal["sandbox"] = "sandbox"
    status: DurableStatus
    input: str = Field(min_length=3, max_length=500)
    seed: int = Field(ge=0, le=2_147_483_647)
    step_count: int = Field(ge=0)
    current_node: NodeId | None = None
    next_nodes: list[NodeId]
    nodes: list[DurableNodeSnapshot]
    values: dict[str, JsonValue]
    last_sequence: int = Field(ge=0)
    checkpoint_count: int = Field(ge=0)


class DurableRunSnapshot(StrictModel):
    schema_id: Literal["agent-trace://durable-run-snapshot/1.0.0"] = (
        "agent-trace://durable-run-snapshot/1.0.0"
    )
    run: DurableRunState
    events: list[AgentEvent]
    checkpoints: list[DurableCheckpoint]


class DurableCommandHandle(StrictModel):
    schema_id: Literal["agent-trace://durable-command-handle/1.0.0"] = (
        "agent-trace://durable-command-handle/1.0.0"
    )
    command_id: StrictId
    command: DurableCommandName
    run_id: RunId
    status: Literal["accepted"] = "accepted"
    accepted_sequence: int = Field(ge=0)
    snapshot_url: str
    events_url: str


class StartDurableRunRequest(ApprovedCommand):
    scenario_id: Literal["durability-lab"] = "durability-lab"
    input: str = Field(min_length=3, max_length=500)
    seed: int = Field(default=2026, ge=0, le=2_147_483_647)


class DurableRunCommand(ApprovedCommand):
    expected_sequence: int = Field(ge=0)


class DurableStateOverride(StrictModel):
    quality_target: float | None = Field(default=None, ge=0.8, le=1)
    brief_label: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def normalize_label(self) -> "DurableStateOverride":
        if self.brief_label is not None:
            normalized = self.brief_label.strip()
            if not normalized:
                raise ValueError("brief label must contain visible text")
            self.brief_label = normalized
        return self

    def changes(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json", exclude_none=True)


class ForkDurableRunRequest(ApprovedCommand):
    checkpoint_id: CheckpointId
    state_override: DurableStateOverride = Field(default_factory=DurableStateOverride)


class OverrideDurableStateRequest(DurableRunCommand):
    checkpoint_id: CheckpointId
    values: DurableStateOverride

    @model_validator(mode="after")
    def restrict_values(self) -> "OverrideDurableStateRequest":
        if not self.values.changes():
            raise ValueError("state override must change at least one allowed value")
        return self


class DurableReplayResponse(StrictModel):
    schema_id: Literal["agent-trace://durable-replay-session/1.0.0"] = (
        "agent-trace://durable-replay-session/1.0.0"
    )
    session_id: StrictId
    source_run_id: RunId
    status: Literal["paused", "playing", "completed"]
    cursor: int = Field(ge=0)
    speed: float = Field(ge=0.5, le=4)
    run: DurableRunState
    events: list[AgentEvent]
    source: Literal["stored-events-only"] = "stored-events-only"
    provider_executions: Literal[0] = 0
    tool_executions: Literal[0] = 0


class DurableReplayCommand(ApprovedCommand):
    action: Literal["pause", "play", "step", "tick", "seek", "checkpoint", "speed", "reset"]
    cursor: int | None = Field(default=None, ge=0)
    speed: float | None = Field(default=None, ge=0.5, le=4)
    checkpoint_id: CheckpointId | None = None

    @model_validator(mode="after")
    def validate_action_argument(self) -> "DurableReplayCommand":
        if self.action == "seek" and self.cursor is None:
            raise ValueError("seek requires a cursor")
        if self.action == "speed" and self.speed is None:
            raise ValueError("speed requires a supported value")
        if self.action == "checkpoint" and self.checkpoint_id is None:
            raise ValueError("checkpoint seek requires a checkpoint identifier")
        if self.speed is not None and self.speed not in {0.5, 1.0, 2.0, 4.0}:
            raise ValueError("speed must be one of 0.5, 1, 2 or 4")
        if self.action != "seek" and self.cursor is not None:
            raise ValueError("cursor is only valid for seek")
        if self.action != "speed" and self.speed is not None:
            raise ValueError("speed is only valid for speed")
        if self.action != "checkpoint" and self.checkpoint_id is not None:
            raise ValueError("checkpoint_id is only valid for checkpoint seek")
        return self


class StartDurableReplayRequest(ApprovedCommand):
    cursor: int | None = Field(default=None, ge=0)


class DurableEventsResponse(StrictModel):
    run_id: RunId
    after_sequence: int = Field(ge=0)
    events: list[AgentEvent]


class BranchEvidence(StrictModel):
    branch_id: Literal["analytics", "research"]
    label: str
    finding: str
    source: str


class DraftArtifact(StrictModel):
    title: str
    summary: str
    evidence_refs: list[str] = Field(min_length=1)
    revision: int = Field(ge=0, le=2)


class QualityResult(StrictModel):
    score: float = Field(ge=0, le=1)
    target: float = Field(ge=0.8, le=1)
    passed: bool
    rationale: str


class DurableAnswer(StrictModel):
    title: str
    summary: str
    evidence_refs: list[str] = Field(min_length=1)
    quality_score: float = Field(ge=0.8, le=1)
