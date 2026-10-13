from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field, JsonValue, model_validator

from agent_trace_api.models import AgentEvent, ApprovedCommand, StrictModel
from agent_trace_api.retrieval_models import Evidence
from agent_trace_api.tool_calling import ToolAuditEntry, ToolRequest, ToolResult

ProtocolId = Literal["mcp", "a2a", "ag-ui", "otel"]
AgUiEventType = Literal[
    "RUN_STARTED",
    "STEP_STARTED",
    "STATE_DELTA",
    "TOOL_CALL_START",
    "TOOL_CALL_RESULT",
    "STEP_FINISHED",
    "RUN_FINISHED",
]


class ProtocolDescriptor(StrictModel):
    protocol: ProtocolId
    display_name: str
    boundary: str
    stable_version: str
    maturity: Literal["stable", "evolving"]
    solves: str
    does_not_solve: str
    local_only: bool
    enabled: bool


class ProtocolComparisonRow(StrictModel):
    dimension: str
    mcp: str
    a2a: str
    ag_ui: str
    otel: str


class ArchitectureOption(StrictModel):
    option: Literal["single_graph", "subgraph", "handoff", "remote_agent"]
    boundary: str
    use_when: str
    coordination_cost: Literal["low", "medium", "high"]


class OtelMapping(StrictModel):
    canonical_event: str
    otel_projection: str
    safe_attributes: list[str]
    excluded: list[str]


class OtelTeachingPolicy(StrictModel):
    trace: Literal["agent.run"]
    correlation: Literal["run.id + correlation_id"]
    sampling: Literal["local deterministic / exporter off"]
    redaction: Literal["allowlisted attributes; raw payloads excluded"]


class ProtocolCatalog(StrictModel):
    schema_id: Literal["agent-trace://protocol-catalog/1.0.0"] = (
        "agent-trace://protocol-catalog/1.0.0"
    )
    labs_enabled: bool
    core_runtime_independent: Literal[True] = True
    remote_auto_connect: Literal[False] = False
    descriptors: list[ProtocolDescriptor]
    comparison: list[ProtocolComparisonRow]
    architecture_options: list[ArchitectureOption]
    ag_ui_supported_events: list[str]
    otel_exporter_enabled: Literal[False] = False
    otel_policy: OtelTeachingPolicy
    otel_mappings: list[OtelMapping]
    experimental_profiles: list[str]


class ProtocolExchange(StrictModel):
    sequence: int = Field(ge=1)
    direction: Literal["client_to_server", "server_to_client"]
    protocol: ProtocolId
    method: str
    request_id: str | None = None
    payload: dict[str, JsonValue]
    timestamp: datetime


class McpRunRequest(ApprovedCommand):
    requested_version: str = Field(default="2025-11-25", min_length=1, max_length=32)
    seed: int = Field(default=2026, ge=0, le=2_147_483_647)


class McpCapabilitySet(StrictModel):
    tools: list[str]
    resources: list[str]
    prompts: list[str]


class McpRun(StrictModel):
    schema_id: Literal["agent-trace://mcp-run/1.0.0"] = "agent-trace://mcp-run/1.0.0"
    run_id: str = Field(pattern=r"^run_mcp_[a-f0-9]{16}$")
    status: Literal["completed"]
    negotiated_version: Literal["2025-11-25"]
    transport: Literal["stdio"] = "stdio"
    local_process_spawned: Literal[True] = True
    process_cleaned: Literal[True] = True
    remote_auto_connect: Literal[False] = False
    capabilities: McpCapabilitySet
    exchanges: list[ProtocolExchange]
    canonical_tool_request: ToolRequest
    canonical_tool_result: ToolResult
    canonical_tool_audit: list[ToolAuditEntry]
    canonical_evidence: list[Evidence]
    canonical_events: list[AgentEvent]


class A2AWireModel(StrictModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AgentSkill(A2AWireModel):
    id: str
    name: str
    description: str
    tags: list[str]
    examples: list[str]
    input_modes: list[str] = Field(alias="inputModes")
    output_modes: list[str] = Field(alias="outputModes")


class AgentInterface(A2AWireModel):
    url: str
    protocol_binding: Literal["JSONRPC", "GRPC", "HTTP+JSON"] = Field(alias="protocolBinding")
    protocol_version: Literal["1.0"] = Field(alias="protocolVersion")


class AgentCapabilities(A2AWireModel):
    streaming: bool
    push_notifications: bool = Field(alias="pushNotifications")
    extended_agent_card: bool = Field(alias="extendedAgentCard")


class ApiKeySecurityScheme(A2AWireModel):
    description: str
    location: Literal["HEADER"]
    name: str


class AgentSecurityScheme(A2AWireModel):
    api_key_security_scheme: ApiKeySecurityScheme = Field(alias="apiKeySecurityScheme")


class StringList(A2AWireModel):
    list: list[str]


class AgentSecurityRequirement(A2AWireModel):
    schemes: dict[str, StringList]


class AgentCard(A2AWireModel):
    name: str
    description: str
    supported_interfaces: list[AgentInterface] = Field(alias="supportedInterfaces")
    version: str
    capabilities: AgentCapabilities
    security_schemes: dict[str, AgentSecurityScheme] = Field(alias="securitySchemes")
    security_requirements: list[AgentSecurityRequirement] = Field(alias="securityRequirements")
    skills: list[AgentSkill]
    default_input_modes: list[str] = Field(alias="defaultInputModes")
    default_output_modes: list[str] = Field(alias="defaultOutputModes")


class A2APart(A2AWireModel):
    text: str | None = None
    data: JsonValue | None = None
    media_type: str = Field(alias="mediaType")

    @model_validator(mode="after")
    def require_exactly_one_content_value(self) -> A2APart:
        if (self.text is None) == (self.data is None):
            raise ValueError("A2A Part must contain exactly one of text or data")
        return self


class A2AMessage(A2AWireModel):
    message_id: str = Field(alias="messageId")
    role: Literal["ROLE_USER", "ROLE_AGENT"]
    parts: list[A2APart]
    task_id: str = Field(alias="taskId")
    context_id: str = Field(alias="contextId")


class A2AArtifact(A2AWireModel):
    artifact_id: str = Field(alias="artifactId")
    name: str
    description: str
    parts: list[A2APart]
    extensions: list[str]


class IncidentResearchArtifactPayload(StrictModel):
    """Closed public payload for the incident.research skill boundary."""

    claim: str = Field(
        min_length=1,
        max_length=160,
        pattern=(
            r"^Fictional support tickets (?P<direction>declined|increased|held steady) "
            r"from (?P<q3>[0-9]{1,7}) in Q3 to (?P<q4>[0-9]{1,7}) in Q4\.$"
        ),
    )
    change: int = Field(ge=-1_000_000, le=1_000_000, strict=True)
    unit: Literal["tickets"]
    source: Literal["fictional://analytics/metrics"]

    @model_validator(mode="after")
    def require_consistent_claim(self) -> IncidentResearchArtifactPayload:
        match = re.fullmatch(
            r"Fictional support tickets (?P<direction>declined|increased|held steady) "
            r"from (?P<q3>[0-9]{1,7}) in Q3 to (?P<q4>[0-9]{1,7}) in Q4\.",
            self.claim,
        )
        if match is None:  # The field pattern normally rejects this first.
            raise ValueError("incident research claim has an invalid shape")
        q3 = int(match.group("q3"))
        q4 = int(match.group("q4"))
        expected_change = q4 - q3
        expected_direction = (
            "declined"
            if expected_change < 0
            else "increased"
            if expected_change > 0
            else "held steady"
        )
        if self.change != expected_change or match.group("direction") != expected_direction:
            raise ValueError("incident research claim and change must agree")
        return self


class A2ATaskStatus(A2AWireModel):
    state: Literal["TASK_STATE_SUBMITTED", "TASK_STATE_WORKING", "TASK_STATE_COMPLETED"]
    timestamp: datetime


class A2ATask(A2AWireModel):
    id: str
    context_id: str = Field(alias="contextId")
    status: A2ATaskStatus
    history: list[A2AMessage]
    artifacts: list[A2AArtifact]


class A2AAdapterResult(StrictModel):
    task: A2ATask
    task_updates: list[A2ATaskStatus]


class A2ASendMessageParams(A2AWireModel):
    message: A2AMessage


class A2AJsonRpcRequest(StrictModel):
    jsonrpc: Literal["2.0"]
    id: str | int
    method: Literal["SendMessage"]
    params: A2ASendMessageParams


class A2AJsonRpcResponse(StrictModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int
    result: A2ATask


class A2ARunRequest(ApprovedCommand):
    requested_version: str = Field(default="1.0", min_length=1, max_length=16)
    presented_capability: str = Field(default="incident.research", min_length=1, max_length=80)
    trusted_local_agent: bool = True
    seed: int = Field(default=2026, ge=0, le=2_147_483_647)


class CoordinationCost(StrictModel):
    messages: int = Field(ge=0)
    task_transitions: int = Field(ge=0)
    artifacts: int = Field(ge=0)
    boundary_count: int = Field(ge=0)
    rating: Literal["low", "medium", "high"]


class A2ARun(StrictModel):
    schema_id: Literal["agent-trace://a2a-run/1.0.0"] = "agent-trace://a2a-run/1.0.0"
    run_id: str = Field(pattern=r"^run_a2a_[a-f0-9]{16}$")
    status: Literal["completed"]
    negotiated_version: Literal["1.0"]
    agent_card: AgentCard
    task: A2ATask
    task_updates: list[A2ATaskStatus]
    exchanges: list[ProtocolExchange]
    canonical_evidence: list[Evidence]
    canonical_events: list[AgentEvent]
    remote_private_fields_exposed: Literal[False] = False
    isolation_reason: str
    coordination_cost: CoordinationCost


class AgUiEvent(StrictModel):
    type: AgUiEventType
    timestamp: datetime
    run_id: str
    event_id: str
    payload: dict[str, JsonValue]


class AgUiMappingRecord(StrictModel):
    canonical_event_id: str
    canonical_type: str
    ag_ui_type: str
    reversible: bool
    note: str


class AgUiRunRequest(ApprovedCommand):
    requested_profile: str = Field(default="agent-trace-ag-ui/0.1", min_length=1, max_length=80)
    seed: int = Field(default=2026, ge=0, le=2_147_483_647)


class AgUiRun(StrictModel):
    schema_id: Literal["agent-trace://ag-ui-run/1.0.0"] = "agent-trace://ag-ui-run/1.0.0"
    run_id: str = Field(pattern=r"^run_agui_[a-f0-9]{16}$")
    status: Literal["completed"]
    adapter_profile: Literal["agent-trace-ag-ui/0.1"]
    maturity: Literal["evolving"] = "evolving"
    canonical_events: list[AgentEvent]
    ag_ui_events: list[AgUiEvent]
    inbound_roundtrip: list[AgentEvent]
    mappings: list[AgUiMappingRecord]
    unsupported_events: list[str]
    canonical_state_owner: Literal["agent-trace-runtime"] = "agent-trace-runtime"


class DecisionGateRequest(ApprovedCommand):
    independent_owner: bool = False
    independent_deployment: bool = False
    capability_isolation: bool = False
    asynchronous_task: bool = False
    replaceable_remote: bool = False
    coordination_eval_cost_acceptable: bool = False


class DecisionGateResult(StrictModel):
    schema_id: Literal["agent-trace://multi-agent-decision/1.0.0"] = (
        "agent-trace://multi-agent-decision/1.0.0"
    )
    recommendation: Literal["single_graph", "subgraph", "handoff", "remote_agent"]
    justified: bool
    coordination_cost: Literal["low", "medium", "high"]
    score: int = Field(ge=0, le=6)
    reasons: list[str]
    warning: str


class ProtocolVersionProbeRequest(ApprovedCommand):
    protocol: ProtocolId
    requested_version: str = Field(min_length=1, max_length=80)


class ProtocolVersionProbe(StrictModel):
    schema_id: Literal["agent-trace://protocol-version-probe/1.0.0"] = (
        "agent-trace://protocol-version-probe/1.0.0"
    )
    protocol: ProtocolId
    requested_version: str
    supported_version: str
    compatible: Literal[True] = True


class ProtocolVersionMismatch(StrictModel):
    schema_id: Literal["agent-trace://protocol-version-mismatch/1.0.0"] = (
        "agent-trace://protocol-version-mismatch/1.0.0"
    )
    code: Literal["VERSION_NOT_SUPPORTED"] = "VERSION_NOT_SUPPORTED"
    detail: str
    protocol: ProtocolId
    requested_version: str
    supported_version: str
    compatible: Literal[False] = False
