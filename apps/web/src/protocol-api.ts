import type { components } from "@agent-trace/contracts";

import { nextCommandKey } from "./api";

export type ProtocolCatalog = components["schemas"]["ProtocolCatalog"];
export type McpRun = components["schemas"]["McpRun"];
export type A2ARun = components["schemas"]["A2ARun"];
export type AgUiRun = components["schemas"]["AgUiRun"];
export type DecisionGateRequest = components["schemas"]["DecisionGateRequest"];
export type DecisionGateResult = components["schemas"]["DecisionGateResult"];

export interface VersionMismatch {
  schema_id: "agent-trace://protocol-version-mismatch/1.0.0";
  code: "VERSION_NOT_SUPPORTED";
  detail: string;
  protocol: "mcp" | "a2a" | "ag-ui" | "otel";
  requested_version: string;
  supported_version: string;
  compatible: false;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: readonly string[]) {
  const keys = new Set(allowed);
  return Object.keys(value).every((key) => keys.has(key));
}

function isStrings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function containsForbiddenPrivateKey(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(containsForbiddenPrivateKey);
  if (!isRecord(value)) return false;
  return Object.entries(value).some(([key, nested]) =>
    ["memory", "internal_tools", "chain_of_thought"].includes(key)
    || containsForbiddenPrivateKey(nested)
  );
}

function isEvents(value: unknown) {
  return Array.isArray(value) && value.length > 0 && value.every((event, index) =>
    isRecord(event)
    && hasOnlyKeys(event, ["schema_version", "event_id", "run_id", "sequence", "type", "timestamp", "node_id", "causation_id", "correlation_id", "payload_version", "payload", "redaction"])
    && event.schema_version === "1.0.0"
    && typeof event.event_id === "string"
    && typeof event.run_id === "string"
    && event.sequence === index + 1
    && typeof event.type === "string"
    && typeof event.timestamp === "string"
    && (event.node_id === null || typeof event.node_id === "string")
    && (event.causation_id === null || typeof event.causation_id === "string")
    && typeof event.correlation_id === "string"
    && event.payload_version === "1.0.0"
    && isRecord(event.payload)
    && isRecord(event.redaction)
  );
}

function isExchanges(value: unknown, protocol: "mcp" | "a2a") {
  return Array.isArray(value) && value.length > 0 && value.every((exchange, index) =>
    isRecord(exchange)
    && hasOnlyKeys(exchange, ["sequence", "direction", "protocol", "method", "request_id", "payload", "timestamp"])
    && exchange.sequence === index + 1
    && ["client_to_server", "server_to_client"].includes(String(exchange.direction))
    && exchange.protocol === protocol
    && typeof exchange.method === "string"
    && (exchange.request_id === null || typeof exchange.request_id === "string")
    && isRecord(exchange.payload)
    && typeof exchange.timestamp === "string"
  );
}

function isA2AWireExchanges(value: unknown) {
  if (!isExchanges(value, "a2a") || !Array.isArray(value) || value.length !== 3) return false;
  const [discovery, request, response] = value;
  if (!isRecord(discovery) || !isRecord(request) || !isRecord(response)
    || !isRecord(discovery.payload) || !isRecord(request.payload) || !isRecord(response.payload)) return false;
  const requestEnvelope = request.payload;
  const responseEnvelope = response.payload;
  const requestParams = isRecord(requestEnvelope.params) ? requestEnvelope.params : null;
  const responseResult = isRecord(responseEnvelope.result) ? responseEnvelope.result : null;
  return discovery.direction === "server_to_client"
    && discovery.method === "AgentCard"
    && Array.isArray(discovery.payload.supportedInterfaces)
    && request.direction === "client_to_server"
    && request.method === "SendMessage"
    && request.request_id === "2"
    && hasOnlyKeys(requestEnvelope, ["jsonrpc", "id", "method", "params"])
    && requestEnvelope.jsonrpc === "2.0"
    && requestEnvelope.id === "2"
    && requestEnvelope.method === "SendMessage"
    && requestParams !== null
    && hasOnlyKeys(requestParams, ["message"])
    && isRecord(requestParams.message)
    && requestParams.message.role === "ROLE_USER"
    && response.direction === "server_to_client"
    && response.method === "Task"
    && response.request_id === "2"
    && hasOnlyKeys(responseEnvelope, ["jsonrpc", "id", "result"])
    && responseEnvelope.jsonrpc === "2.0"
    && responseEnvelope.id === "2"
    && responseResult !== null
    && isRecord(responseResult.status)
    && responseResult.status.state === "TASK_STATE_COMPLETED";
}

export function parseProtocolCatalog(value: unknown): ProtocolCatalog {
  const comparisonDimensions = ["Participants", "Unit of work", "Transport", "Discovery", "State", "Streaming", "Auth", "Artifacts", "Failure semantics", "Not solved"];
  const architectureOptions = ["single_graph", "subgraph", "handoff", "remote_agent"];
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "labs_enabled", "core_runtime_independent", "remote_auto_connect", "descriptors", "comparison", "architecture_options", "ag_ui_supported_events", "otel_exporter_enabled", "otel_policy", "otel_mappings", "experimental_profiles"])
    || value.schema_id !== "agent-trace://protocol-catalog/1.0.0"
    || value.labs_enabled !== true
    || value.core_runtime_independent !== true
    || value.remote_auto_connect !== false
    || value.otel_exporter_enabled !== false
    || !Array.isArray(value.descriptors) || value.descriptors.length !== 4
    || !value.descriptors.every((item) => isRecord(item)
      && hasOnlyKeys(item, ["protocol", "display_name", "boundary", "stable_version", "maturity", "solves", "does_not_solve", "local_only", "enabled"])
      && ["mcp", "a2a", "ag-ui", "otel"].includes(String(item.protocol))
      && typeof item.display_name === "string"
      && typeof item.boundary === "string"
      && typeof item.stable_version === "string"
      && ["stable", "evolving"].includes(String(item.maturity))
      && typeof item.solves === "string"
      && typeof item.does_not_solve === "string"
      && item.local_only === true
      && typeof item.enabled === "boolean")
    || !value.descriptors.every((item) => isRecord(item)
      && (item.protocol === "mcp" || item.enabled === true))
    || new Set(value.descriptors.map((item) => isRecord(item) ? item.protocol : null)).size !== 4
    || !Array.isArray(value.comparison)
    || value.comparison.map((row) => isRecord(row) ? row.dimension : null).join("|") !== comparisonDimensions.join("|")
    || !value.comparison.every((row) => isRecord(row)
      && hasOnlyKeys(row, ["dimension", "mcp", "a2a", "ag_ui", "otel"])
      && [row.dimension, row.mcp, row.a2a, row.ag_ui, row.otel].every((item) => typeof item === "string" && item.length > 0))
    || !Array.isArray(value.architecture_options)
    || value.architecture_options.map((item) => isRecord(item) ? item.option : null).join("|") !== architectureOptions.join("|")
    || !value.architecture_options.every((item) => isRecord(item)
      && hasOnlyKeys(item, ["option", "boundary", "use_when", "coordination_cost"])
      && typeof item.boundary === "string" && item.boundary.length > 0
      && typeof item.use_when === "string" && item.use_when.length > 0
      && ["low", "medium", "high"].includes(String(item.coordination_cost)))
    || !isStrings(value.ag_ui_supported_events) || value.ag_ui_supported_events.length !== 7
    || !isRecord(value.otel_policy)
    || !hasOnlyKeys(value.otel_policy, ["trace", "correlation", "sampling", "redaction"])
    || value.otel_policy.trace !== "agent.run"
    || value.otel_policy.correlation !== "run.id + correlation_id"
    || value.otel_policy.sampling !== "local deterministic / exporter off"
    || value.otel_policy.redaction !== "allowlisted attributes; raw payloads excluded"
    || !Array.isArray(value.otel_mappings) || value.otel_mappings.length < 3
    || !isStrings(value.experimental_profiles)) {
    throw new Error("protocol catalog crossed an invalid typed boundary");
  }
  return value as ProtocolCatalog;
}

export function parseMcpRun(value: unknown): McpRun {
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "run_id", "status", "negotiated_version", "transport", "local_process_spawned", "process_cleaned", "remote_auto_connect", "capabilities", "exchanges", "canonical_tool_request", "canonical_tool_result", "canonical_tool_audit", "canonical_evidence", "canonical_events"])
    || value.schema_id !== "agent-trace://mcp-run/1.0.0"
    || !/^run_mcp_[a-f0-9]{16}$/.test(String(value.run_id))
    || value.status !== "completed"
    || value.negotiated_version !== "2025-11-25"
    || value.transport !== "stdio"
    || value.local_process_spawned !== true
    || value.process_cleaned !== true
    || value.remote_auto_connect !== false
    || !isRecord(value.capabilities)
    || !hasOnlyKeys(value.capabilities, ["tools", "resources", "prompts"])
    || !isStrings(value.capabilities.tools)
    || value.capabilities.tools.join("|") !== "analytics.query_metric|orders.get_status"
    || !isStrings(value.capabilities.resources)
    || value.capabilities.resources.join("|") !== "scenario://incident-response/delegation-brief"
    || !isStrings(value.capabilities.prompts)
    || value.capabilities.prompts.join("|") !== "delegate-research"
    || !isExchanges(value.exchanges, "mcp")
    || !isRecord(value.canonical_tool_request)
    || value.canonical_tool_request.tool_name !== "analytics.query_metric"
    || !isRecord(value.canonical_tool_result)
    || value.canonical_tool_result.status !== "succeeded"
    || !Array.isArray(value.canonical_tool_audit) || value.canonical_tool_audit.length < 1
    || !Array.isArray(value.canonical_evidence) || value.canonical_evidence.length !== 1
    || !isEvents(value.canonical_events)) {
    throw new Error("MCP adapter crossed an invalid typed boundary");
  }
  return value as McpRun;
}

export function parseA2ARun(value: unknown): A2ARun {
  const card = isRecord(value) && isRecord(value.agent_card) ? value.agent_card : null;
  const task = isRecord(value) && isRecord(value.task) ? value.task : null;
  const updates = isRecord(value) && Array.isArray(value.task_updates) ? value.task_updates : null;
  const cardInterface = card && Array.isArray(card.supportedInterfaces) && isRecord(card.supportedInterfaces[0]) ? card.supportedInterfaces[0] : null;
  const cardCapabilities = card && isRecord(card.capabilities) ? card.capabilities : null;
  const taskStatus = task && isRecord(task.status) ? task.status : null;
  let interfaceIsLocal = false;
  if (cardInterface && typeof cardInterface.url === "string") {
    try {
      const url = new URL(cardInterface.url);
      interfaceIsLocal = ["127.0.0.1", "localhost", "test"].includes(url.hostname) && url.pathname === "/api/protocols/a2a/v1";
    } catch { interfaceIsLocal = false; }
  }
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "run_id", "status", "negotiated_version", "agent_card", "task", "task_updates", "exchanges", "canonical_evidence", "canonical_events", "remote_private_fields_exposed", "isolation_reason", "coordination_cost"])
    || value.schema_id !== "agent-trace://a2a-run/1.0.0"
    || !/^run_a2a_[a-f0-9]{16}$/.test(String(value.run_id))
    || value.status !== "completed"
    || value.negotiated_version !== "1.0"
    || value.remote_private_fields_exposed !== false
    || typeof value.isolation_reason !== "string"
    || containsForbiddenPrivateKey(value)
    || !card
    || !hasOnlyKeys(card, ["name", "description", "supportedInterfaces", "version", "capabilities", "securitySchemes", "securityRequirements", "skills", "defaultInputModes", "defaultOutputModes"])
    || typeof card.name !== "string" || typeof card.description !== "string" || card.version !== "1.0.0"
    || !Array.isArray(card.supportedInterfaces) || card.supportedInterfaces.length !== 1
    || !cardInterface || !hasOnlyKeys(cardInterface, ["url", "protocolBinding", "protocolVersion"])
    || cardInterface.protocolBinding !== "JSONRPC" || cardInterface.protocolVersion !== "1.0" || !interfaceIsLocal
    || !cardCapabilities || !hasOnlyKeys(cardCapabilities, ["streaming", "pushNotifications", "extendedAgentCard"])
    || cardCapabilities.streaming !== false || cardCapabilities.pushNotifications !== false || cardCapabilities.extendedAgentCard !== false
    || !isRecord(card.securitySchemes) || !isRecord(card.securitySchemes.capability_header)
    || !isRecord(card.securitySchemes.capability_header.apiKeySecurityScheme)
    || card.securitySchemes.capability_header.apiKeySecurityScheme.location !== "HEADER"
    || card.securitySchemes.capability_header.apiKeySecurityScheme.name !== "X-Agent-Trace-Capability"
    || !Array.isArray(card.securityRequirements) || card.securityRequirements.length !== 1
    || !Array.isArray(card.skills) || card.skills.length !== 1
    || !isStrings(card.defaultInputModes) || !isStrings(card.defaultOutputModes)
    || !task || !hasOnlyKeys(task, ["id", "contextId", "status", "history", "artifacts"])
    || typeof task.id !== "string" || typeof task.contextId !== "string"
    || !taskStatus || taskStatus.state !== "TASK_STATE_COMPLETED" || typeof taskStatus.timestamp !== "string"
    || !Array.isArray(task.history) || task.history.length !== 2
    || !Array.isArray(task.artifacts) || task.artifacts.length !== 1
    || !updates
    || updates.map((state) => isRecord(state) ? state.state : null).join(",") !== "TASK_STATE_SUBMITTED,TASK_STATE_WORKING,TASK_STATE_COMPLETED"
    || !updates.every((state) => isRecord(state) && hasOnlyKeys(state, ["state", "timestamp"]) && typeof state.timestamp === "string")
    || !isA2AWireExchanges(value.exchanges)
    || !Array.isArray(value.canonical_evidence) || value.canonical_evidence.length !== 1
    || !isEvents(value.canonical_events)
    || !isRecord(value.coordination_cost) || value.coordination_cost.rating !== "high") {
    throw new Error("A2A adapter crossed an invalid typed boundary");
  }
  return value as A2ARun;
}

export function parseAgUiRun(value: unknown): AgUiRun {
  const supported = new Set(["RUN_STARTED", "STEP_STARTED", "STATE_DELTA", "TOOL_CALL_START", "TOOL_CALL_RESULT", "STEP_FINISHED", "RUN_FINISHED"]);
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "run_id", "status", "adapter_profile", "maturity", "canonical_events", "ag_ui_events", "inbound_roundtrip", "mappings", "unsupported_events", "canonical_state_owner"])
    || value.schema_id !== "agent-trace://ag-ui-run/1.0.0"
    || value.status !== "completed"
    || value.adapter_profile !== "agent-trace-ag-ui/0.1"
    || value.maturity !== "evolving"
    || value.canonical_state_owner !== "agent-trace-runtime"
    || !isEvents(value.canonical_events)
    || !Array.isArray(value.ag_ui_events) || value.ag_ui_events.length !== 7
    || !value.ag_ui_events.every((event) => isRecord(event) && supported.has(String(event.type)) && isRecord(event.payload))
    || !isEvents(value.inbound_roundtrip)
    || !Array.isArray(value.mappings) || value.mappings.length !== 7
    || !value.mappings.every((mapping) => isRecord(mapping) && mapping.reversible === true)
    || !isStrings(value.unsupported_events)) {
    throw new Error("AG-UI adapter crossed an invalid typed boundary");
  }
  return value as AgUiRun;
}

export function parseDecisionGate(value: unknown): DecisionGateResult {
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "recommendation", "justified", "coordination_cost", "score", "reasons", "warning"])
    || value.schema_id !== "agent-trace://multi-agent-decision/1.0.0"
    || !["single_graph", "subgraph", "handoff", "remote_agent"].includes(String(value.recommendation))
    || value.justified !== true
    || !["low", "medium", "high"].includes(String(value.coordination_cost))
    || typeof value.score !== "number" || !Number.isInteger(value.score) || value.score < 0 || value.score > 6
    || !isStrings(value.reasons) || value.reasons.length < 1
    || typeof value.warning !== "string") {
    throw new Error("multi-agent decision crossed an invalid typed boundary");
  }
  return value as DecisionGateResult;
}

async function responseJson(response: Response) {
  const value: unknown = await response.json();
  if (!response.ok) {
    const detail = isRecord(value) && typeof value.detail === "string" ? value.detail : `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return value;
}

function approvedBody(label: string) {
  return { approved: true, idempotency_key: nextCommandKey(label) };
}

export async function loadProtocolCatalog(signal?: AbortSignal) {
  return parseProtocolCatalog(await responseJson(await fetch("/api/protocols/catalog", { signal })));
}

export async function runMcpLab() {
  return parseMcpRun(await responseJson(await fetch("/api/protocols/mcp/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...approvedBody("protocol-mcp"), requested_version: "2025-11-25", seed: 2026 })
  })));
}

export async function runA2ALab() {
  return parseA2ARun(await responseJson(await fetch("/api/protocols/a2a/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...approvedBody("protocol-a2a"), requested_version: "1.0", presented_capability: "incident.research", trusted_local_agent: true, seed: 2026 })
  })));
}

export async function runAgUiLab() {
  return parseAgUiRun(await responseJson(await fetch("/api/protocols/ag-ui/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...approvedBody("protocol-agui"), requested_profile: "agent-trace-ag-ui/0.1", seed: 2026 })
  })));
}

export async function runDecisionGate(input: Omit<DecisionGateRequest, "approved" | "idempotency_key">) {
  return parseDecisionGate(await responseJson(await fetch("/api/protocols/decision-gate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...approvedBody("protocol-decision"), ...input })
  })));
}

export async function probeMcpReleaseCandidate(): Promise<VersionMismatch> {
  const response = await fetch("/api/protocols/version-probe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...approvedBody("protocol-version"), protocol: "mcp", requested_version: "2026-07-28" })
  });
  const value: unknown = await response.json();
  if (!response.ok
    || !isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "detail", "code", "protocol", "requested_version", "supported_version", "compatible"])
    || value.schema_id !== "agent-trace://protocol-version-mismatch/1.0.0"
    || value.code !== "VERSION_NOT_SUPPORTED"
    || value.protocol !== "mcp"
    || value.requested_version !== "2026-07-28"
    || value.supported_version !== "2025-11-25"
    || value.compatible !== false
    || typeof value.detail !== "string") {
    throw new Error("version mismatch did not cross the explicit typed error boundary");
  }
  return value as unknown as VersionMismatch;
}
