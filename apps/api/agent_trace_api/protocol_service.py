from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

from pydantic import JsonValue, ValidationError

from agent_trace_api.a2a_research_adapter import (
    LocalResearchAgentAdapter,
    ResearchAgentBoundaryError,
)
from agent_trace_api.config import REPOSITORY_ROOT
from agent_trace_api.models import AgentEvent, RedactionInfo
from agent_trace_api.protocol_models import (
    A2AAdapterResult,
    A2AJsonRpcRequest,
    A2AJsonRpcResponse,
    A2AMessage,
    A2APart,
    A2ARun,
    A2ARunRequest,
    A2ASendMessageParams,
    AgentCard,
    AgUiEvent,
    AgUiEventType,
    AgUiMappingRecord,
    AgUiRun,
    AgUiRunRequest,
    ArchitectureOption,
    CoordinationCost,
    DecisionGateRequest,
    DecisionGateResult,
    IncidentResearchArtifactPayload,
    McpCapabilitySet,
    McpRun,
    McpRunRequest,
    OtelMapping,
    OtelTeachingPolicy,
    ProtocolCatalog,
    ProtocolComparisonRow,
    ProtocolDescriptor,
    ProtocolExchange,
    ProtocolId,
    ProtocolVersionProbe,
    ProtocolVersionProbeRequest,
)
from agent_trace_api.retrieval_models import (
    Evidence,
    EvidenceContent,
    EvidenceProvenance,
    EvidenceRetrieval,
    EvidenceSource,
)
from agent_trace_api.tool_calling import ToolLabService, ToolRunCommand

MCP_VERSION: Literal["2025-11-25"] = "2025-11-25"
A2A_VERSION: Literal["1.0"] = "1.0"
AG_UI_PROFILE: Literal["agent-trace-ag-ui/0.1"] = "agent-trace-ag-ui/0.1"
OTEL_PROFILE = "agent-trace-otel/1.0"
MAX_MCP_OUTPUT_BYTES = 131_072
MCP_REQUEST_TIMEOUT_SECONDS = 2.0
FIXED_TIME = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)


class ProtocolInputError(ValueError):
    pass


class ProtocolConflictError(RuntimeError):
    pass


class ProtocolVersionError(RuntimeError):
    def __init__(self, protocol: ProtocolId, requested: str, supported: str) -> None:
        self.protocol = protocol
        self.requested = requested
        self.supported = supported
        super().__init__(
            f"VersionNotSupported: {protocol} requested {requested}; supported {supported}"
        )


class ProtocolLabDisabledError(RuntimeError):
    pass


class McpProcessError(RuntimeError):
    pass


def _validate_mcp_response_envelope(
    response: object, *, request_id: int, method: str
) -> dict[str, JsonValue]:
    if not isinstance(response, dict):
        raise McpProcessError("MCP server returned a non-object response")
    if response.get("jsonrpc") != "2.0":
        raise McpProcessError(f"MCP {method} returned an invalid JSON-RPC version")
    response_id = response.get("id")
    if (
        not isinstance(response_id, int)
        or isinstance(response_id, bool)
        or response_id != request_id
    ):
        raise McpProcessError(f"MCP {method} response id did not match request {request_id}")
    if ("result" in response) == ("error" in response):
        raise McpProcessError(f"MCP {method} response must contain exactly one of result or error")
    return cast(dict[str, JsonValue], response)


def _mcp_record(value: object, *, method: str, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise McpProcessError(f"MCP {method} result field {field} must be an object")
    return cast(dict[str, object], value)


def _mcp_records(value: object, *, method: str, field: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise McpProcessError(f"MCP {method} result field {field} must be an object list")
    return cast(list[dict[str, object]], value)


def _require_exact_keys(
    value: dict[str, object], *, method: str, field: str, keys: set[str]
) -> None:
    if set(value) != keys:
        raise McpProcessError(f"MCP {method} result field {field} has unexpected shape")


def _require_string(value: object, *, method: str, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise McpProcessError(f"MCP {method} result field {field} must be a string")
    return value


def _validate_mcp_method_result(method: str, result: dict[str, object]) -> dict[str, JsonValue]:
    """Fail closed when the fixed local child drifts from its taught MCP profile."""

    raw = result
    if method == "initialize":
        _require_exact_keys(
            raw,
            method=method,
            field="result",
            keys={"protocolVersion", "capabilities", "serverInfo", "instructions"},
        )
        if raw["protocolVersion"] != MCP_VERSION:
            raise McpProcessError("MCP initialize negotiated an unsupported version")
        capabilities = _mcp_record(raw["capabilities"], method=method, field="capabilities")
        _require_exact_keys(
            capabilities,
            method=method,
            field="capabilities",
            keys={"tools", "resources", "prompts"},
        )
        if not all(isinstance(value, dict) for value in capabilities.values()):
            raise McpProcessError("MCP initialize capabilities must be objects")
        server_info = _mcp_record(raw["serverInfo"], method=method, field="serverInfo")
        _require_exact_keys(
            server_info, method=method, field="serverInfo", keys={"name", "version"}
        )
        _require_string(server_info["name"], method=method, field="serverInfo.name")
        _require_string(server_info["version"], method=method, field="serverInfo.version")
        _require_string(raw["instructions"], method=method, field="instructions")
    elif method == "tools/list":
        _require_exact_keys(raw, method=method, field="result", keys={"tools"})
        tools = _mcp_records(raw["tools"], method=method, field="tools")
        approved_tools = ["analytics.query_metric", "orders.get_status"]
        if [tool.get("name") for tool in tools] != approved_tools:
            raise McpProcessError("MCP tools/list exposed an unapproved tool catalog")
        for index, tool in enumerate(tools):
            _require_exact_keys(
                tool,
                method=method,
                field=f"tools[{index}]",
                keys={"name", "description", "inputSchema"},
            )
            _require_string(tool["description"], method=method, field=f"tools[{index}].description")
            schema = _mcp_record(
                tool["inputSchema"], method=method, field=f"tools[{index}].inputSchema"
            )
            if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
                raise McpProcessError(
                    f"MCP tools/list result field tools[{index}].inputSchema must be closed"
                )
    elif method == "tools/call":
        _require_exact_keys(
            raw,
            method=method,
            field="result",
            keys={"content", "structuredContent", "isError"},
        )
        if raw["isError"] is not False:
            raise McpProcessError("MCP tools/call returned an error result")
        content = _mcp_records(raw["content"], method=method, field="content")
        if len(content) != 1:
            raise McpProcessError("MCP tools/call content must contain one result item")
        _require_exact_keys(content[0], method=method, field="content[0]", keys={"type", "text"})
        if content[0]["type"] != "text":
            raise McpProcessError("MCP tools/call content type is unsupported")
        _require_string(content[0]["text"], method=method, field="content[0].text")
        proposal = _mcp_record(raw["structuredContent"], method=method, field="structuredContent")
        _require_exact_keys(
            proposal,
            method=method,
            field="structuredContent",
            keys={"tool", "arguments"},
        )
        approved_arguments: dict[str, dict[str, object]] = {
            "analytics.query_metric": {
                "metric": "tickets",
                "periods": ["2025-Q3", "2025-Q4"],
            },
            "orders.get_status": {"order_id": "ORD-1042"},
        }
        tool_name = proposal["tool"]
        if tool_name not in approved_arguments:
            raise McpProcessError("MCP tools/call proposed an unapproved tool")
        arguments = _mcp_record(
            proposal["arguments"], method=method, field="structuredContent.arguments"
        )
        if arguments != approved_arguments[cast(str, tool_name)]:
            raise McpProcessError("MCP tools/call proposed unapproved arguments")
    elif method == "resources/list":
        _require_exact_keys(raw, method=method, field="result", keys={"resources"})
        resources = _mcp_records(raw["resources"], method=method, field="resources")
        if len(resources) != 1:
            raise McpProcessError("MCP resources/list must expose exactly one bounded resource")
        _require_exact_keys(
            resources[0],
            method=method,
            field="resources[0]",
            keys={"uri", "name", "description", "mimeType"},
        )
        if resources[0]["uri"] != "scenario://incident-response/delegation-brief":
            raise McpProcessError("MCP resources/list exposed an unapproved resource")
        for field in ("name", "description", "mimeType"):
            _require_string(resources[0][field], method=method, field=f"resources[0].{field}")
    elif method == "resources/read":
        _require_exact_keys(raw, method=method, field="result", keys={"contents"})
        contents = _mcp_records(raw["contents"], method=method, field="contents")
        if len(contents) != 1:
            raise McpProcessError("MCP resources/read must return exactly one content item")
        _require_exact_keys(
            contents[0],
            method=method,
            field="contents[0]",
            keys={"uri", "mimeType", "text"},
        )
        if contents[0]["uri"] != "scenario://incident-response/delegation-brief":
            raise McpProcessError("MCP resources/read returned an unexpected resource")
        if contents[0]["mimeType"] != "application/json":
            raise McpProcessError("MCP resources/read returned an unsupported media type")
        text = _require_string(contents[0]["text"], method=method, field="contents[0].text")
        try:
            parsed_text = json.loads(text)
        except json.JSONDecodeError as error:
            raise McpProcessError("MCP resources/read text was not valid JSON") from error
        if not isinstance(parsed_text, dict):
            raise McpProcessError("MCP resources/read JSON must be an object")
    elif method == "prompts/list":
        _require_exact_keys(raw, method=method, field="result", keys={"prompts"})
        prompts = _mcp_records(raw["prompts"], method=method, field="prompts")
        if len(prompts) != 1:
            raise McpProcessError("MCP prompts/list must expose exactly one bounded prompt")
        _require_exact_keys(
            prompts[0],
            method=method,
            field="prompts[0]",
            keys={"name", "description", "arguments"},
        )
        if prompts[0]["name"] != "delegate-research":
            raise McpProcessError("MCP prompts/list exposed an unapproved prompt")
        _require_string(prompts[0]["description"], method=method, field="prompts[0].description")
        if not isinstance(prompts[0]["arguments"], list):
            raise McpProcessError("MCP prompts/list arguments must be a list")
    elif method == "prompts/get":
        _require_exact_keys(raw, method=method, field="result", keys={"description", "messages"})
        _require_string(raw["description"], method=method, field="description")
        messages = _mcp_records(raw["messages"], method=method, field="messages")
        if len(messages) != 1:
            raise McpProcessError("MCP prompts/get must return exactly one message")
        _require_exact_keys(
            messages[0], method=method, field="messages[0]", keys={"role", "content"}
        )
        if messages[0]["role"] != "user":
            raise McpProcessError("MCP prompts/get role is unsupported")
        prompt_content = _mcp_record(
            messages[0]["content"], method=method, field="messages[0].content"
        )
        _require_exact_keys(
            prompt_content,
            method=method,
            field="messages[0].content",
            keys={"type", "text"},
        )
        if prompt_content["type"] != "text":
            raise McpProcessError("MCP prompts/get content type is unsupported")
        _require_string(prompt_content["text"], method=method, field="messages[0].content.text")
    else:
        raise McpProcessError(f"MCP method {method} is outside the fixed client profile")
    return cast(dict[str, JsonValue], result)


class ProtocolLabService:
    def __init__(
        self,
        tool_service: ToolLabService,
        *,
        enabled: bool = True,
        mcp_process_enabled: bool = True,
        repository_root: Path = REPOSITORY_ROOT,
        research_agent: LocalResearchAgentAdapter | None = None,
    ) -> None:
        self.tool_service = tool_service
        self.enabled = enabled
        self.mcp_process_enabled = mcp_process_enabled
        self.repository_root = repository_root.resolve()
        self.research_agent = research_agent or LocalResearchAgentAdapter()
        self._mcp_runs: dict[str, tuple[str, McpRun]] = {}
        self._a2a_runs: dict[str, tuple[str, A2ARun]] = {}
        self._agui_runs: dict[str, tuple[str, AgUiRun]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._mcp_gate = asyncio.Semaphore(1)
        self._active_processes: set[asyncio.subprocess.Process] = set()

    @property
    def active_process_count(self) -> int:
        return len(self._active_processes)

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _timestamp(offset: int) -> datetime:
        return FIXED_TIME + timedelta(milliseconds=offset * 17)

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise ProtocolLabDisabledError(
                "Protocol labs are disabled; the core runtime remains available."
            )

    def _require_mcp_process_enabled(self) -> None:
        if not self.mcp_process_enabled:
            raise ProtocolLabDisabledError(
                "Process-backed MCP execution is disabled in public preview."
            )

    def catalog(self) -> ProtocolCatalog:
        self._require_enabled()
        return ProtocolCatalog(
            labs_enabled=True,
            descriptors=[
                ProtocolDescriptor(
                    protocol="mcp",
                    display_name="Model Context Protocol",
                    boundary="Agent host ↔ capability server",
                    stable_version=MCP_VERSION,
                    maturity="stable",
                    solves="Discover and call typed tools, resources and prompt templates.",
                    does_not_solve=(
                        "Business orchestration, authorization or durable application state."
                    ),
                    local_only=True,
                    enabled=self.mcp_process_enabled,
                ),
                ProtocolDescriptor(
                    protocol="a2a",
                    display_name="Agent2Agent Protocol",
                    boundary="Client agent ↔ independently owned agent",
                    stable_version=A2A_VERSION,
                    maturity="stable",
                    solves="Capability discovery, task delegation, status and artifact exchange.",
                    does_not_solve=(
                        "Internal graph design or permission to expose private memory and tools."
                    ),
                    local_only=True,
                    enabled=True,
                ),
                ProtocolDescriptor(
                    protocol="ag-ui",
                    display_name="Agent User Interaction Protocol",
                    boundary="Runtime event stream ↔ frontend projection",
                    stable_version=AG_UI_PROFILE,
                    maturity="evolving",
                    solves="Map a supported run, step, state and tool event subset into UI events.",
                    does_not_solve="Canonical backend state, persistence or business semantics.",
                    local_only=True,
                    enabled=True,
                ),
                ProtocolDescriptor(
                    protocol="otel",
                    display_name="OpenTelemetry semantic projection",
                    boundary="Canonical runtime facts ↔ telemetry backend",
                    stable_version=OTEL_PROFILE,
                    maturity="evolving",
                    solves="Correlate safe span and event attributes across runtime boundaries.",
                    does_not_solve=(
                        "Product event ownership, learner explanations or evaluation truth."
                    ),
                    local_only=True,
                    enabled=True,
                ),
            ],
            comparison=[
                ProtocolComparisonRow(
                    dimension="Participants",
                    mcp="Agent host ↔ capability server",
                    a2a="Independent agent ↔ independent agent",
                    ag_ui="Agent backend ↔ user-facing UI",
                    otel="System component → telemetry backend",
                ),
                ProtocolComparisonRow(
                    dimension="Unit of work",
                    mcp="Tool call, resource read, prompt template",
                    a2a="Task, message and artifact",
                    ag_ui="Run, step, state and interaction event",
                    otel="Trace, span, metric and log",
                ),
                ProtocolComparisonRow(
                    dimension="Transport",
                    mcp="Bounded stdio in this lab",
                    a2a="Declared JSON-RPC interface",
                    ag_ui="REST command plus event stream profile",
                    otel="Exporter protocol; disabled here",
                ),
                ProtocolComparisonRow(
                    dimension="Discovery",
                    mcp="Initialization and capability lists",
                    a2a="Agent Card and skills",
                    ag_ui="Declared supported event profile",
                    otel="Configured semantic mapping",
                ),
                ProtocolComparisonRow(
                    dimension="State",
                    mcp="Owned by host and tool registry",
                    a2a="Private to each agent; task is shared",
                    ag_ui="Canonical runtime remains owner",
                    otel="Application remains owner",
                ),
                ProtocolComparisonRow(
                    dimension="Streaming",
                    mcp="Capability-dependent",
                    a2a="Optional; disabled in local card",
                    ag_ui="Core purpose for supported UI events",
                    otel="Batched or streamed telemetry",
                ),
                ProtocolComparisonRow(
                    dimension="Auth",
                    mcp="Host policy plus tool authorization",
                    a2a="Card scheme plus capability check",
                    ag_ui="Application session policy",
                    otel="Exporter credentials and redaction policy",
                ),
                ProtocolComparisonRow(
                    dimension="Artifacts",
                    mcp="Typed tool/resource results",
                    a2a="First-class replaceable artifacts",
                    ag_ui="Projection only; not durable truth",
                    otel="References only; never product payloads",
                ),
                ProtocolComparisonRow(
                    dimension="Failure semantics",
                    mcp="JSON-RPC error, timeout, closed process",
                    a2a="Task terminal state and typed rejection",
                    ag_ui="Unsupported events fail closed",
                    otel="Export loss must not corrupt runtime",
                ),
                ProtocolComparisonRow(
                    dimension="Not solved",
                    mcp="Planning, user permission, answer quality",
                    a2a="Tool connectivity, trust, business approval",
                    ag_ui="Backend orchestration and persistence",
                    otel="Business state and evaluation truth",
                ),
            ],
            architecture_options=[
                ArchitectureOption(
                    option="single_graph",
                    boundary="One owner, one deployment, shared typed state",
                    use_when="Nodes are implementation steps in the same product boundary.",
                    coordination_cost="low",
                ),
                ArchitectureOption(
                    option="subgraph",
                    boundary="Reusable bounded workflow under the same runtime",
                    use_when="A typed branch needs reuse, isolation or its own checkpoint scope.",
                    coordination_cost="low",
                ),
                ArchitectureOption(
                    option="handoff",
                    boundary="Responsibility changes inside a trusted application",
                    use_when=(
                        "A specialist takes the active role but shares the application authority."
                    ),
                    coordination_cost="medium",
                ),
                ArchitectureOption(
                    option="remote_agent",
                    boundary="Independent owner, deployment and capability contract",
                    use_when=(
                        "Isolation is real and the artifact is replaceable across the boundary."
                    ),
                    coordination_cost="high",
                ),
            ],
            ag_ui_supported_events=[
                "RUN_STARTED",
                "STEP_STARTED",
                "STATE_DELTA",
                "TOOL_CALL_START",
                "TOOL_CALL_RESULT",
                "STEP_FINISHED",
                "RUN_FINISHED",
            ],
            otel_policy=OtelTeachingPolicy(
                trace="agent.run",
                correlation="run.id + correlation_id",
                sampling="local deterministic / exporter off",
                redaction="allowlisted attributes; raw payloads excluded",
            ),
            otel_mappings=[
                OtelMapping(
                    canonical_event="run.started / run.completed",
                    otel_projection="agent.trace run span lifecycle",
                    safe_attributes=["run.id", "scenario.id", "provider.id"],
                    excluded=["input text", "hidden reasoning", "credentials"],
                ),
                OtelMapping(
                    canonical_event="tool.requested / tool.completed",
                    otel_projection="tool execution child span",
                    safe_attributes=["tool.name", "tool.version", "tool.status"],
                    excluded=["raw arguments", "unredacted result", "approval secret"],
                ),
                OtelMapping(
                    canonical_event="state.patch / evidence.created",
                    otel_projection="span event with bounded references",
                    safe_attributes=["event.type", "node.id", "evidence.id"],
                    excluded=["state payload", "document body", "agent memory"],
                ),
            ],
            experimental_profiles=[
                "MCP 2026-07-28 release candidate — isolated and unsupported",
                "AG-UI wider event families — adapter rejects outside the E10 subset",
                "AI semantic conventions — exporter remains disabled by default",
            ],
        )

    def version_probe(self, command: ProtocolVersionProbeRequest) -> ProtocolVersionProbe:
        self._require_enabled()
        supported = {
            "mcp": MCP_VERSION,
            "a2a": A2A_VERSION,
            "ag-ui": AG_UI_PROFILE,
            "otel": OTEL_PROFILE,
        }[command.protocol]
        if command.requested_version != supported:
            raise ProtocolVersionError(command.protocol, command.requested_version, supported)
        return ProtocolVersionProbe(
            protocol=command.protocol,
            requested_version=command.requested_version,
            supported_version=supported,
        )

    def _event(
        self,
        *,
        run_id: str,
        sequence: int,
        event_type: str,
        node_id: str | None,
        payload: dict[str, JsonValue],
        prefix: str,
        causation_id: str | None,
    ) -> AgentEvent:
        digest = self._digest(f"{run_id}:{sequence}:{event_type}")[:18]
        return AgentEvent(
            event_id=f"evt_{prefix}_{digest}",
            run_id=run_id,
            sequence=sequence,
            type=event_type,
            timestamp=self._timestamp(sequence),
            node_id=node_id,
            causation_id=causation_id,
            correlation_id=f"corr_{self._digest(run_id)[:16]}",
            payload=payload,
            redaction=RedactionInfo(redacted=False, paths=[]),
        )

    def _events(
        self,
        *,
        run_id: str,
        prefix: str,
        specs: list[tuple[str, str | None, dict[str, JsonValue]]],
    ) -> list[AgentEvent]:
        events: list[AgentEvent] = []
        for sequence, (event_type, node_id, payload) in enumerate(specs, start=1):
            event = self._event(
                run_id=run_id,
                sequence=sequence,
                event_type=event_type,
                node_id=node_id,
                payload=payload,
                prefix=prefix,
                causation_id=events[-1].event_id if events else None,
            )
            events.append(event)
        return events

    def _evidence_from_tool(self, run_id: str, request_id: str, data: JsonValue) -> Evidence:
        facts = data if isinstance(data, dict) else {"result": data}
        return Evidence(
            evidence_id=f"ev_mcp_{self._digest(run_id)[:16]}",
            kind="metric",
            source=EvidenceSource(
                source_id="mcp-local-analytics",
                title="Fictional analytics metric",
                source_type="tool",
                locator="fictional://analytics/metrics",
                version="1.0.0",
                observed_at=self._timestamp(20),
            ),
            content=EvidenceContent(facts=facts),
            retrieval=EvidenceRetrieval(method="tool", score=1.0),
            authority="internal",
            freshness="current",
            classification="public_fixture",
            provenance=EvidenceProvenance(
                run_id=run_id,
                node_id="mcp_tool",
                tool_request_id=request_id,
                created_by="tool",
            ),
            citation_label="Local MCP analytics fixture",
        )

    async def _exchange_mcp(
        self,
        process: asyncio.subprocess.Process,
        exchanges: list[ProtocolExchange],
        *,
        request_id: int,
        method: str,
        params: dict[str, JsonValue],
        byte_count: list[int],
    ) -> dict[str, JsonValue]:
        request: dict[str, JsonValue] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        encoded = (json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8")
        if process.stdin is None or process.stdout is None:
            raise McpProcessError("MCP stdio pipes were not created")
        process.stdin.write(encoded)
        await process.stdin.drain()
        exchanges.append(
            ProtocolExchange(
                sequence=len(exchanges) + 1,
                direction="client_to_server",
                protocol="mcp",
                method=method,
                request_id=str(request_id),
                payload=request,
                timestamp=self._timestamp(len(exchanges) + 1),
            )
        )
        try:
            raw = await asyncio.wait_for(
                process.stdout.readline(), timeout=MCP_REQUEST_TIMEOUT_SECONDS
            )
        except TimeoutError as error:
            raise McpProcessError(f"MCP {method} exceeded the hard timeout") from error
        byte_count[0] += len(raw)
        if not raw or byte_count[0] > MAX_MCP_OUTPUT_BYTES:
            raise McpProcessError("MCP output was empty or exceeded the bounded output limit")
        try:
            response = json.loads(raw)
        except json.JSONDecodeError as error:
            raise McpProcessError("MCP server returned invalid JSON") from error
        response = _validate_mcp_response_envelope(response, request_id=request_id, method=method)
        exchanges.append(
            ProtocolExchange(
                sequence=len(exchanges) + 1,
                direction="server_to_client",
                protocol="mcp",
                method=method,
                request_id=str(request_id),
                payload=response,
                timestamp=self._timestamp(len(exchanges) + 1),
            )
        )
        if "error" in response:
            raise McpProcessError(str(response["error"]))
        result = response.get("result")
        if not isinstance(result, dict):
            raise McpProcessError(f"MCP {method} omitted its typed result")
        return _validate_mcp_method_result(method, cast(dict[str, object], result))

    async def _notify_mcp(
        self,
        process: asyncio.subprocess.Process,
        exchanges: list[ProtocolExchange],
        *,
        method: str,
        params: dict[str, JsonValue],
    ) -> None:
        notification: dict[str, JsonValue] = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        if process.stdin is None:
            raise McpProcessError("MCP stdio input pipe was not created")
        process.stdin.write(
            (json.dumps(notification, separators=(",", ":")) + "\n").encode("utf-8")
        )
        await process.stdin.drain()
        exchanges.append(
            ProtocolExchange(
                sequence=len(exchanges) + 1,
                direction="client_to_server",
                protocol="mcp",
                method=method,
                payload=notification,
                timestamp=self._timestamp(len(exchanges) + 1),
            )
        )

    async def _run_mcp_process(
        self,
    ) -> tuple[list[ProtocolExchange], dict[str, dict[str, JsonValue]], bool]:
        module_path = self.repository_root / "apps" / "api"
        environment = {
            "PYTHONPATH": str(module_path),
            "PYTHONUNBUFFERED": "1",
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "agent_trace_api.mcp_stdio_server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.repository_root,
            env=environment,
        )
        self._active_processes.add(process)
        exchanges: list[ProtocolExchange] = []
        results: dict[str, dict[str, JsonValue]] = {}
        byte_count = [0]
        try:
            calls: list[tuple[str, dict[str, JsonValue]]] = [
                (
                    "initialize",
                    {
                        "protocolVersion": MCP_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "agent-trace-mcp-client", "version": "1.0.0"},
                    },
                ),
                ("tools/list", {}),
                (
                    "tools/call",
                    {
                        "name": "analytics.query_metric",
                        "arguments": {
                            "metric": "tickets",
                            "periods": ["2025-Q3", "2025-Q4"],
                        },
                    },
                ),
                ("resources/list", {}),
                (
                    "resources/read",
                    {"uri": "scenario://incident-response/delegation-brief"},
                ),
                ("prompts/list", {}),
                ("prompts/get", {"name": "delegate-research"}),
            ]
            for request_id, (method, params) in enumerate(calls, start=1):
                results[method] = await self._exchange_mcp(
                    process,
                    exchanges,
                    request_id=request_id,
                    method=method,
                    params=params,
                    byte_count=byte_count,
                )
                if method == "initialize":
                    await self._notify_mcp(
                        process,
                        exchanges,
                        method="notifications/initialized",
                        params={},
                    )
        finally:
            if process.stdin is not None:
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), timeout=0.5)
            except TimeoutError:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=0.5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            self._active_processes.discard(process)
        return exchanges, results, process.returncode == 0

    async def run_mcp(self, command: McpRunRequest) -> McpRun:
        self._require_enabled()
        self._require_mcp_process_enabled()
        if command.requested_version != MCP_VERSION:
            raise ProtocolVersionError("mcp", command.requested_version, MCP_VERSION)
        fingerprint = command.model_dump_json(exclude={"idempotency_key"})
        previous = self._mcp_runs.get(command.idempotency_key)
        if previous is not None:
            if previous[0] != fingerprint:
                raise ProtocolConflictError("MCP idempotency key was reused with different input")
            return deepcopy(previous[1])
        lock = self._locks.setdefault(command.idempotency_key, asyncio.Lock())
        async with lock, self._mcp_gate:
            previous = self._mcp_runs.get(command.idempotency_key)
            if previous is not None:
                if previous[0] != fingerprint:
                    raise ProtocolConflictError(
                        "MCP idempotency key was reused with different input"
                    )
                return deepcopy(previous[1])
            exchanges, results, cleaned = await self._run_mcp_process()
            if not cleaned:
                raise McpProcessError("Local MCP server did not exit cleanly")
            proposal = results["tools/call"].get("structuredContent")
            if not isinstance(proposal, dict) or proposal.get("tool") != "analytics.query_metric":
                raise McpProcessError("MCP tool result failed the adapter contract")
            arguments = proposal.get("arguments")
            if not isinstance(arguments, dict):
                raise McpProcessError("MCP tool result omitted typed arguments")
            tool_run = await self.tool_service.execute(
                ToolRunCommand(
                    approved=True,
                    idempotency_key=f"mcp-tool:{command.idempotency_key}",
                    tool_name="analytics.query_metric",
                    tool_version="1.0.0",
                    arguments=arguments,
                    actor_role="learner",
                    expected_risk="read",
                    model_rationale=(
                        "The local MCP adapter proposed a bounded fictional metric lookup."
                    ),
                    seed=command.seed,
                )
            )
            if tool_run.status != "succeeded":
                raise McpProcessError("Canonical tool registry rejected the MCP proposal")
            digest = self._digest(f"{command.idempotency_key}\0{fingerprint}")[:16]
            run_id = f"run_mcp_{digest}"
            evidence = self._evidence_from_tool(
                run_id, tool_run.request.request_id, tool_run.result.data
            )
            event_specs: list[tuple[str, str | None, dict[str, JsonValue]]] = [
                ("protocol.started", "mcp_connect", {"protocol": "mcp", "transport": "stdio"}),
                ("protocol.negotiated", "mcp_connect", {"version": MCP_VERSION}),
                (
                    "tool.requested",
                    "mcp_tool",
                    {
                        "request_id": tool_run.request.request_id,
                        "tool_name": tool_run.request.tool_name,
                    },
                ),
                (
                    "tool.completed",
                    "mcp_tool",
                    {"request_id": tool_run.request.request_id, "status": tool_run.result.status},
                ),
                (
                    "evidence.created",
                    "mcp_resource",
                    {
                        "evidence_id": evidence.evidence_id,
                        "source_type": evidence.source.source_type,
                    },
                ),
                (
                    "protocol.completed",
                    "mcp_connect",
                    {"process_cleaned": True, "exchanges": len(exchanges)},
                ),
            ]
            events = self._events(run_id=run_id, prefix="mcp", specs=event_specs)
            capabilities = cast(dict[str, JsonValue], results["initialize"]["capabilities"])
            run = McpRun(
                run_id=run_id,
                status="completed",
                negotiated_version=MCP_VERSION,
                capabilities=McpCapabilitySet(
                    tools=[
                        cast(str, tool["name"])
                        for tool in cast(list[dict[str, JsonValue]], results["tools/list"]["tools"])
                    ],
                    resources=[
                        cast(str, resource["uri"])
                        for resource in cast(
                            list[dict[str, JsonValue]], results["resources/list"]["resources"]
                        )
                    ],
                    prompts=[
                        cast(str, prompt["name"])
                        for prompt in cast(
                            list[dict[str, JsonValue]], results["prompts/list"]["prompts"]
                        )
                    ],
                ),
                exchanges=exchanges,
                canonical_tool_request=tool_run.request,
                canonical_tool_result=tool_run.result,
                canonical_tool_audit=tool_run.audit,
                canonical_evidence=[evidence],
                canonical_events=events,
            )
            if set(capabilities) != {"tools", "resources", "prompts"}:
                raise McpProcessError("MCP capability negotiation drifted from the E10 profile")
            self._mcp_runs[command.idempotency_key] = (fingerprint, deepcopy(run))
            return run

    async def close(self) -> None:
        processes = list(self._active_processes)
        for process in processes:
            if process.returncode is None:
                process.terminate()
        if processes:
            await asyncio.gather(*(process.wait() for process in processes), return_exceptions=True)
        self._active_processes.clear()

    def discover_a2a_agent(self, *, interface_url: str) -> AgentCard:
        self._require_enabled()
        return AgentCard.model_validate_json(
            self.research_agent.discover(interface_url=interface_url).model_dump_json(by_alias=True)
        )

    def execute_a2a_message(
        self, message: A2AMessage, *, presented_capability: str
    ) -> A2AAdapterResult:
        self._require_enabled()
        try:
            adapter_result = A2AAdapterResult.model_validate_json(
                self.research_agent.execute(
                    message.model_dump_json(by_alias=True),
                    presented_capability=presented_capability,
                )
            )
            return self._validate_research_adapter_result(adapter_result, request=message)
        except (ResearchAgentBoundaryError, ValidationError) as error:
            raise ProtocolInputError(f"A2A request denied: {error}") from error

    @staticmethod
    def _validate_research_adapter_result(
        result: A2AAdapterResult, *, request: A2AMessage
    ) -> A2AAdapterResult:
        task = result.task
        if task.id != request.task_id or task.context_id != request.context_id:
            raise ProtocolInputError("A2A research adapter returned an unrelated task")
        if [update.state for update in result.task_updates] != [
            "TASK_STATE_SUBMITTED",
            "TASK_STATE_WORKING",
            "TASK_STATE_COMPLETED",
        ]:
            raise ProtocolInputError("A2A research adapter returned an invalid task lifecycle")
        if task.status != result.task_updates[-1] or len(task.artifacts) != 1:
            raise ProtocolInputError("A2A research adapter omitted its completed artifact")
        if len(task.history) != 2 or task.history[0] != request:
            raise ProtocolInputError("A2A research adapter returned invalid public task history")
        response = task.history[1]
        if (
            response.role != "ROLE_AGENT"
            or response.task_id != request.task_id
            or response.context_id != request.context_id
            or len(response.parts) != 1
            or response.parts[0].text != "The requested evidence artifact is attached."
            or response.parts[0].media_type != "text/plain"
        ):
            raise ProtocolInputError("A2A research adapter returned invalid public task history")
        artifact = task.artifacts[0]
        expected_artifact_id = f"artifact_{request.task_id.removeprefix('task_')}"
        if (
            artifact.artifact_id != expected_artifact_id
            or artifact.name != "Ticket-volume finding"
            or artifact.description
            != "A bounded, replaceable artifact from the isolated research capability."
            or artifact.extensions != ["agent-trace://artifact/evidence/1.0.0"]
            or len(artifact.parts) != 1
            or artifact.parts[0].media_type != "application/json"
            or artifact.parts[0].text is not None
            or artifact.parts[0].data is None
        ):
            raise ProtocolInputError("A2A research adapter returned an invalid incident artifact")
        try:
            payload = IncidentResearchArtifactPayload.model_validate(artifact.parts[0].data)
        except ValidationError as error:
            raise ProtocolInputError(
                "A2A research adapter returned an invalid incident artifact payload"
            ) from error
        sanitized_part = artifact.parts[0].model_copy(
            update={"data": cast(JsonValue, payload.model_dump(mode="json"))}
        )
        sanitized_artifact = artifact.model_copy(update={"parts": [sanitized_part]})
        sanitized_task = task.model_copy(update={"artifacts": [sanitized_artifact]})
        return result.model_copy(update={"task": sanitized_task})

    def run_a2a(
        self,
        command: A2ARunRequest,
        *,
        interface_url: str = "http://127.0.0.1/api/protocols/a2a/v1",
    ) -> A2ARun:
        self._require_enabled()
        if command.requested_version != A2A_VERSION:
            raise ProtocolVersionError("a2a", command.requested_version, A2A_VERSION)
        if not command.trusted_local_agent:
            raise ProtocolInputError(
                "A2A delegation denied: only the trusted local fixture is allowed"
            )
        if command.presented_capability != "incident.research":
            raise ProtocolInputError(
                "A2A capability denied: incident.research is required for this task"
            )
        fingerprint = command.model_dump_json(exclude={"idempotency_key"})
        previous = self._a2a_runs.get(command.idempotency_key)
        if previous is not None:
            if previous[0] != fingerprint:
                raise ProtocolConflictError("A2A idempotency key was reused with different input")
            return deepcopy(previous[1])
        digest = self._digest(f"{command.idempotency_key}\0{fingerprint}")[:16]
        run_id = f"run_a2a_{digest}"
        task_id = f"task_{digest}"
        context_id = f"context_{digest}"
        request_message = A2AMessage(
            messageId=f"message_request_{digest}",
            role="ROLE_USER",
            taskId=task_id,
            contextId=context_id,
            parts=[
                A2APart(
                    text=(
                        "Investigate the fictional Q3→Q4 support-ticket change. Return only a "
                        "cited artifact; do not expose private memory, tools or hidden reasoning."
                    ),
                    mediaType="text/plain",
                )
            ],
        )
        card = self.discover_a2a_agent(interface_url=interface_url)
        adapter_result = self.execute_a2a_message(
            request_message,
            presented_capability=command.presented_capability,
        )
        task = adapter_result.task
        task_updates = adapter_result.task_updates
        artifact = task.artifacts[0]
        wire_request = A2AJsonRpcRequest(
            jsonrpc="2.0",
            id="2",
            method="SendMessage",
            params=A2ASendMessageParams(message=request_message),
        )
        wire_response = A2AJsonRpcResponse(id="2", result=task)
        exchanges = [
            ProtocolExchange(
                sequence=1,
                direction="server_to_client",
                protocol="a2a",
                method="AgentCard",
                request_id="1",
                payload=cast(dict[str, JsonValue], card.model_dump(mode="json", by_alias=True)),
                timestamp=self._timestamp(1),
            ),
            ProtocolExchange(
                sequence=2,
                direction="client_to_server",
                protocol="a2a",
                method="SendMessage",
                request_id="2",
                payload=cast(
                    dict[str, JsonValue],
                    wire_request.model_dump(mode="json", by_alias=True),
                ),
                timestamp=self._timestamp(2),
            ),
            ProtocolExchange(
                sequence=3,
                direction="server_to_client",
                protocol="a2a",
                method="Task",
                request_id="2",
                payload=cast(
                    dict[str, JsonValue],
                    wire_response.model_dump(mode="json", by_alias=True),
                ),
                timestamp=self._timestamp(3),
            ),
        ]
        evidence = Evidence(
            evidence_id=f"ev_a2a_{digest}",
            kind="remote_artifact",
            source=EvidenceSource(
                source_id="isolated-research-agent",
                title="Isolated incident research artifact",
                source_type="remote_agent",
                locator=f"local://research-agent/a2a/tasks/{task_id}",
                version="1.0.0",
                observed_at=self._timestamp(4),
            ),
            content=EvidenceContent(facts=cast(dict[str, JsonValue], artifact.parts[0].data)),
            retrieval=EvidenceRetrieval(method="remote", score=1.0),
            authority="internal",
            freshness="current",
            classification="public_fixture",
            provenance=EvidenceProvenance(
                run_id=run_id,
                node_id="a2a_delegate",
                created_by="remote_agent",
            ),
            citation_label="Isolated local A2A artifact",
        )
        specs: list[tuple[str, str | None, dict[str, JsonValue]]] = [
            ("protocol.started", "a2a_delegate", {"protocol": "a2a", "version": A2A_VERSION}),
            (
                "agent.capability_checked",
                "a2a_delegate",
                {"capability": "incident.research", "allowed": True},
            ),
            ("agent.task_submitted", "a2a_delegate", {"task_id": task_id}),
            ("agent.task_working", "a2a_delegate", {"task_id": task_id}),
            ("agent.artifact_received", "a2a_delegate", {"artifact_id": artifact.artifact_id}),
            ("evidence.created", "a2a_delegate", {"evidence_id": evidence.evidence_id}),
            ("agent.task_completed", "a2a_delegate", {"task_id": task_id}),
            ("protocol.completed", "a2a_delegate", {"private_fields_exposed": False}),
        ]
        events = self._events(run_id=run_id, prefix="a2a", specs=specs)
        run = A2ARun(
            run_id=run_id,
            status="completed",
            negotiated_version=A2A_VERSION,
            agent_card=card,
            task=task,
            task_updates=task_updates,
            exchanges=exchanges,
            canonical_evidence=[evidence],
            canonical_events=events,
            isolation_reason=(
                "A separate serialized local research adapter owns its private metric tool and "
                "memory. The planner receives only its declared Agent Card, task lifecycle and "
                "replaceable artifact DTOs."
            ),
            coordination_cost=CoordinationCost(
                messages=2,
                task_transitions=3,
                artifacts=1,
                boundary_count=1,
                rating="high",
            ),
        )
        self._a2a_runs[command.idempotency_key] = (fingerprint, deepcopy(run))
        return run

    def _agui_source_events(self, run_id: str) -> list[AgentEvent]:
        specs: list[tuple[str, str | None, dict[str, JsonValue]]] = [
            ("run.started", None, {"scenario_id": "incident-response"}),
            ("node.started", "agui_step", {"step_name": "Inspect incident trend"}),
            (
                "state.patch",
                "agui_step",
                {"delta": [{"op": "add", "path": "/values/status", "value": "working"}]},
            ),
            (
                "tool.requested",
                "agui_step",
                {"request_id": "toolreq_agui", "tool_name": "analytics.query_metric"},
            ),
            (
                "tool.completed",
                "agui_step",
                {"request_id": "toolreq_agui", "status": "succeeded"},
            ),
            ("node.completed", "agui_step", {"step_name": "Inspect incident trend"}),
            ("run.completed", None, {"status": "completed"}),
        ]
        return self._events(run_id=run_id, prefix="agui", specs=specs)

    def run_ag_ui(self, command: AgUiRunRequest) -> AgUiRun:
        self._require_enabled()
        if command.requested_profile != AG_UI_PROFILE:
            raise ProtocolVersionError("ag-ui", command.requested_profile, AG_UI_PROFILE)
        fingerprint = command.model_dump_json(exclude={"idempotency_key"})
        previous = self._agui_runs.get(command.idempotency_key)
        if previous is not None:
            if previous[0] != fingerprint:
                raise ProtocolConflictError("AG-UI idempotency key was reused with different input")
            return deepcopy(previous[1])
        digest = self._digest(f"{command.idempotency_key}\0{fingerprint}")[:16]
        run_id = f"run_agui_{digest}"
        canonical = self._agui_source_events(run_id)
        event_map: dict[str, AgUiEventType] = {
            "run.started": "RUN_STARTED",
            "node.started": "STEP_STARTED",
            "state.patch": "STATE_DELTA",
            "tool.requested": "TOOL_CALL_START",
            "tool.completed": "TOOL_CALL_RESULT",
            "node.completed": "STEP_FINISHED",
            "run.completed": "RUN_FINISHED",
        }
        agui = [
            AgUiEvent(
                type=event_map[event.type],
                timestamp=event.timestamp,
                run_id=event.run_id,
                event_id=f"agui_{self._digest(event.event_id)[:16]}",
                payload={
                    "canonical_event_id": event.event_id,
                    "node_id": event.node_id,
                    **event.payload,
                },
            )
            for event in canonical
        ]
        reverse_map = {value: key for key, value in event_map.items()}
        inbound: list[AgentEvent] = []
        for index, event in enumerate(agui, start=1):
            inbound.append(
                AgentEvent(
                    event_id=f"evt_agui_in_{self._digest(event.event_id)[:14]}",
                    run_id=run_id,
                    sequence=index,
                    type=reverse_map[event.type],
                    timestamp=event.timestamp,
                    node_id=cast(str | None, event.payload.get("node_id")),
                    causation_id=inbound[-1].event_id if inbound else None,
                    correlation_id=f"corr_{digest}",
                    payload={
                        key: value
                        for key, value in event.payload.items()
                        if key not in {"canonical_event_id", "node_id"}
                    },
                )
            )
        run = AgUiRun(
            run_id=run_id,
            status="completed",
            adapter_profile=AG_UI_PROFILE,
            canonical_events=canonical,
            ag_ui_events=agui,
            inbound_roundtrip=inbound,
            mappings=[
                AgUiMappingRecord(
                    canonical_event_id=event.event_id,
                    canonical_type=event.type,
                    ag_ui_type=event_map[event.type],
                    reversible=True,
                    note=(
                        "Only the declared E10 subset is round-tripped; canonical runtime "
                        "ownership is unchanged."
                    ),
                )
                for event in canonical
            ],
            unsupported_events=[
                "MESSAGES_SNAPSHOT",
                "ACTIVITY_SNAPSHOT",
                "RAW",
                "CUSTOM",
            ],
        )
        self._agui_runs[command.idempotency_key] = (fingerprint, deepcopy(run))
        return run

    def decision_gate(self, command: DecisionGateRequest) -> DecisionGateResult:
        self._require_enabled()
        positives = [
            command.independent_owner,
            command.independent_deployment,
            command.capability_isolation,
            command.asynchronous_task,
            command.replaceable_remote,
            command.coordination_eval_cost_acceptable,
        ]
        score = sum(1 for value in positives if value)
        reasons: list[str] = []
        if command.independent_owner:
            reasons.append("Ownership crosses an organizational boundary.")
        if command.independent_deployment:
            reasons.append("Lifecycle and deployment can change independently.")
        if command.capability_isolation:
            reasons.append("The remote side must keep private tools and memory isolated.")
        if command.replaceable_remote:
            reasons.append("A typed artifact can replace shared mutable state.")
        if command.coordination_eval_cost_acceptable:
            reasons.append("The team accepts remote coordination and evaluation cost.")
        recommendation: Literal["single_graph", "subgraph", "handoff", "remote_agent"]
        cost: Literal["low", "medium", "high"]
        if all(positives):
            recommendation = "remote_agent"
            cost = "high"
            justified = True
            warning = (
                "Do not create an agent per node. Budget for version negotiation, task "
                "lifecycle, retries and artifact validation."
            )
        elif command.asynchronous_task and score >= 2:
            recommendation = "handoff"
            cost = "medium"
            justified = True
            warning = (
                "Do not create an agent per node. Keep the handoff inside the trusted "
                "application unless ownership is independent."
            )
        elif score >= 1:
            recommendation = "subgraph"
            cost = "low"
            justified = True
            warning = (
                "Do not create an agent per node. A bounded subgraph gives reuse without "
                "a network-style coordination tax."
            )
        else:
            recommendation = "single_graph"
            cost = "low"
            justified = True
            warning = "Do not create an agent per node; keep typed steps in one graph."
        if not reasons:
            reasons = ["One owner, one deployment and shared state form one runtime boundary."]
        return DecisionGateResult(
            recommendation=recommendation,
            justified=justified,
            coordination_cost=cost,
            score=score,
            reasons=reasons,
            warning=warning,
        )
