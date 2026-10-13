import type { components } from "@agent-trace/contracts";

export type HealthResponse = components["schemas"]["HealthResponse"];
export type CapabilitiesResponse = components["schemas"]["CapabilitiesResponse"];
export type AgentEvent = components["schemas"]["AgentEvent"];
export type AnswerPayload = components["schemas"]["LabAnswerPayload"];
export type ChartArtifact = components["schemas"]["LabLineChartArtifact"];
export type NodeSnapshot = components["schemas"]["NodeSnapshot"];
export type RunState = components["schemas"]["LabRunState"];
export type RunSnapshot = components["schemas"]["LabRunSnapshot"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: readonly string[]): boolean {
  const allowedKeys = new Set(allowed);
  return Object.keys(value).every((key) => allowedKeys.has(key));
}

function isBoundedString(value: unknown, minimum: number, maximum: number): value is string {
  return typeof value === "string" && value.length >= minimum && value.length <= maximum;
}

function parseHealth(value: unknown): HealthResponse {
  if (
    !isRecord(value)
    || !hasOnlyKeys(value, ["status", "service", "version"])
    || value.status !== "ok"
    || value.service !== "agent-trace-api"
    || typeof value.version !== "string"
  ) {
    throw new Error("/api/health returned an invalid HealthResponse");
  }
  return { status: value.status, service: value.service, version: value.version };
}

function parseCapabilities(value: unknown): CapabilitiesResponse {
  const disabledCapabilities = [
    "codex-cli",
    "external-network",
    "filesystem-access",
    "process-execution",
    "scenario-import-export",
    "persistent-memory-writes",
    "anonymous-learning-writes"
  ];
  if (
    !isRecord(value)
    || !hasOnlyKeys(value, [
      "offline_ready",
      "providers",
      "python_version",
      "tools",
      "deployment_profile",
      "display_mode",
      "safe_lab_mode",
      "disabled_capabilities"
    ])
    || typeof value.offline_ready !== "boolean"
    || typeof value.python_version !== "string"
    || !["local", "public-preview"].includes(String(value.deployment_profile))
    || !["LOCAL WORKSTATION", "PUBLIC PREVIEW / SAFE LAB MODE"].includes(String(value.display_mode))
    || typeof value.safe_lab_mode !== "boolean"
    || value.safe_lab_mode !== (value.deployment_profile === "public-preview")
    || !Array.isArray(value.disabled_capabilities)
    || !value.disabled_capabilities.every((capability) => disabledCapabilities.includes(String(capability)))
    || !Array.isArray(value.providers)
    || !value.providers.every((provider) => provider === "mock")
    || !Array.isArray(value.tools)
    || !value.tools.every(
      (tool) => isRecord(tool)
        && hasOnlyKeys(tool, ["name", "version"])
        && typeof tool.name === "string"
        && typeof tool.version === "string"
    )
  ) {
    throw new Error("/api/capabilities returned an invalid CapabilitiesResponse");
  }
  return {
    offline_ready: value.offline_ready,
    providers: value.providers,
    python_version: value.python_version,
    tools: value.tools.map((tool) => ({ name: tool.name as string, version: tool.version as string })),
    deployment_profile: value.deployment_profile as CapabilitiesResponse["deployment_profile"],
    display_mode: value.display_mode as CapabilitiesResponse["display_mode"],
    safe_lab_mode: value.safe_lab_mode,
    disabled_capabilities: value.disabled_capabilities as CapabilitiesResponse["disabled_capabilities"]
  };
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function isRunId(value: unknown): value is string {
  return typeof value === "string" && value.length <= 128 && /^run_[A-Za-z0-9_-]+$/.test(value);
}

function isEventId(value: unknown): value is string {
  return typeof value === "string" && value.length <= 128 && /^evt_[A-Za-z0-9_-]+$/.test(value);
}

function isNodeId(value: unknown): value is string {
  return typeof value === "string" && /^[a-z][a-z0-9_]{1,63}$/.test(value);
}

function isDateTime(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-](\d{2}):(\d{2}))$/.exec(value);
  if (!match) return false;
  const [, rawYear, rawMonth, rawDay, rawHour, rawMinute, rawSecond, rawOffsetHour, rawOffsetMinute] = match;
  const year = Number(rawYear);
  const month = Number(rawMonth);
  const day = Number(rawDay);
  const hour = Number(rawHour);
  const minute = Number(rawMinute);
  const second = Number(rawSecond);
  const offsetHour = rawOffsetHour === undefined ? 0 : Number(rawOffsetHour);
  const offsetMinute = rawOffsetMinute === undefined ? 0 : Number(rawOffsetMinute);
  const daysInMonth = month >= 1 && month <= 12
    ? new Date(Date.UTC(year, month, 0)).getUTCDate()
    : 0;
  return year >= 1
    && day >= 1
    && day <= daysInMonth
    && hour <= 23
    && minute <= 59
    && second <= 59
    && offsetHour <= 23
    && offsetMinute <= 59
    && !Number.isNaN(Date.parse(value));
}

function parseNode(value: unknown): NodeSnapshot {
  if (
    !isRecord(value)
    || !hasOnlyKeys(value, ["id", "label", "lane", "summary", "status"])
    || !isNodeId(value.id)
    || typeof value.label !== "string"
    || value.label.length < 1
    || !["graph", "provider", "tool", "policy"].includes(String(value.lane))
    || !["pending", "running", "completed", "error"].includes(String(value.status))
    || typeof value.summary !== "string"
    || value.summary.length < 1
  ) throw new Error("runtime returned an invalid NodeSnapshot");
  return value as NodeSnapshot;
}

function parseChart(value: unknown): ChartArtifact {
  if (
    !isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "schema_version", "kind", "title", "x_label", "y_label", "source", "takeaway", "series"])
    || value.schema_id !== "agent-trace://lab-line-chart/1.0.0"
    || value.schema_version !== "1.0.0"
    || value.kind !== "line"
    || !isBoundedString(value.title, 1, 120)
    || !isBoundedString(value.x_label, 1, 60)
    || !isBoundedString(value.y_label, 1, 60)
    || !isBoundedString(value.source, 1, 160)
    || !isBoundedString(value.takeaway, 1, 300)
    || !Array.isArray(value.series)
    || value.series.length < 1
    || value.series.length > 3
    || !value.series.every((series) =>
      isRecord(series)
      && hasOnlyKeys(series, ["label", "color", "points"])
      && isBoundedString(series.label, 1, 80)
      && ["teal", "orange", "purple"].includes(String(series.color))
      && Array.isArray(series.points)
      && series.points.length >= 2
      && series.points.length <= 24
      && series.points.every((point) => isRecord(point)
        && hasOnlyKeys(point, ["x", "y"])
        && isBoundedString(point.x, 1, 40)
        && typeof point.y === "number"
        && Number.isFinite(point.y))
    )
  ) throw new Error("runtime returned an invalid ChartArtifact");
  return value as ChartArtifact;
}

function parseAnswer(value: unknown): AnswerPayload | null {
  if (value === null || value === undefined) return null;
  if (
    !isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "title", "summary", "bullets", "chart"])
    || value.schema_id !== "agent-trace://lab-answer/1.0.0"
    || !isBoundedString(value.title, 1, 120)
    || !isBoundedString(value.summary, 1, 500)
    || !isStringArray(value.bullets)
    || value.bullets.length < 1
    || value.bullets.length > 5
  ) throw new Error("runtime returned an invalid AnswerPayload");
  return {
    schema_id: value.schema_id,
    title: value.title,
    summary: value.summary,
    bullets: value.bullets,
    chart: parseChart(value.chart)
  };
}

export function parseRunState(value: unknown): RunState {
  if (
    !isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "run_id", "scenario_id", "case_id", "status", "input", "seed", "cursor", "step_count", "current_node", "nodes", "values", "answer", "last_sequence"])
    || value.schema_id !== "agent-trace://lab-run-state/1.0.0"
    || !isRunId(value.run_id)
    || value.scenario_id !== "data-analyst"
    || value.case_id !== "case-1"
    || !["idle", "running", "completed", "error"].includes(String(value.status))
    || typeof value.input !== "string"
    || !isNonNegativeInteger(value.seed)
    || value.seed > 2_147_483_647
    || !isNonNegativeInteger(value.cursor)
    || !isNonNegativeInteger(value.step_count)
    || !(isNodeId(value.current_node) || value.current_node === null || value.current_node === undefined)
    || !Array.isArray(value.nodes)
    || !isRecord(value.values)
    || !isNonNegativeInteger(value.last_sequence)
  ) throw new Error("runtime returned an invalid RunState");
  const nodes = value.nodes.map(parseNode);
  const nodeIds = new Set(nodes.map((node) => node.id));
  if (
    nodes.length < 1
    || nodeIds.size !== nodes.length
    || value.cursor > nodes.length
    || value.step_count > nodes.length
    || value.cursor !== value.step_count
    || (isNodeId(value.current_node) && !nodeIds.has(value.current_node))
  ) throw new Error("runtime returned an inconsistent RunState");
  return {
    schema_id: value.schema_id,
    run_id: value.run_id,
    scenario_id: value.scenario_id,
    case_id: value.case_id,
    status: value.status as RunState["status"],
    input: value.input,
    seed: value.seed,
    cursor: value.cursor,
    step_count: value.step_count,
    current_node: value.current_node as string | null | undefined,
    nodes,
    values: value.values,
    answer: parseAnswer(value.answer),
    last_sequence: value.last_sequence
  };
}

export function parseAgentEvent(value: unknown): AgentEvent {
  if (
    !isRecord(value)
    || !hasOnlyKeys(value, ["schema_version", "event_id", "run_id", "sequence", "type", "timestamp", "node_id", "causation_id", "correlation_id", "payload_version", "payload", "redaction"])
    || value.schema_version !== "1.0.0"
    || !isEventId(value.event_id)
    || !isRunId(value.run_id)
    || typeof value.sequence !== "number"
    || !Number.isInteger(value.sequence)
    || value.sequence < 1
    || typeof value.type !== "string"
    || !/^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$/.test(value.type)
    || !isDateTime(value.timestamp)
    || !(isNodeId(value.node_id) || value.node_id === null || value.node_id === undefined)
    || !(typeof value.causation_id === "string" && value.causation_id.length > 0
      || value.causation_id === null
      || value.causation_id === undefined)
    || typeof value.correlation_id !== "string"
    || value.correlation_id.length < 1
    || value.payload_version !== "1.0.0"
    || !isRecord(value.payload)
    || !isRecord(value.redaction)
    || !hasOnlyKeys(value.redaction, ["redacted", "paths"])
    || typeof value.redaction.redacted !== "boolean"
    || !(value.redaction.paths === undefined
      || isStringArray(value.redaction.paths)
      && new Set(value.redaction.paths).size === value.redaction.paths.length)
  ) throw new Error("runtime returned an invalid AgentEvent");
  return value as AgentEvent;
}

export function parseRunSnapshot(value: unknown): RunSnapshot {
  if (
    !isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "run", "events"])
    || value.schema_id !== "agent-trace://lab-run-snapshot/1.0.0"
    || !Array.isArray(value.events)
  ) {
    throw new Error("runtime returned an invalid RunSnapshot");
  }
  const run = parseRunState(value.run);
  const events = value.events.map(parseAgentEvent);
  if (events.some((event) => event.run_id !== run.run_id)) {
    throw new Error("runtime returned events for a different run");
  }
  const eventIds = new Set(events.map((event) => event.event_id));
  const contiguous = events.every((event, index) => event.sequence === index + 1);
  const finalSequence = events.at(-1)?.sequence ?? 0;
  if (!contiguous || eventIds.size !== events.length || run.last_sequence !== finalSequence) {
    throw new Error("runtime returned an inconsistent event trajectory");
  }
  return { schema_id: value.schema_id, run, events };
}

async function readJson<T>(path: string, signal: AbortSignal, parse: (value: unknown) => T): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" }, signal });
  if (!response.ok) {
    throw new Error(`${path} returned HTTP ${response.status}`);
  }
  return parse(await response.json());
}

async function postRuntime(path: string, body: Record<string, unknown>): Promise<RunSnapshot> {
  const response = await fetch(path, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  if (!response.ok) {
    const detail: unknown = await response.json().catch(() => null);
    const message = isRecord(detail) && typeof detail.detail === "string" ? detail.detail : `HTTP ${response.status}`;
    throw new Error(`${path} returned ${message}`);
  }
  return parseRunSnapshot(await response.json());
}

export async function loadRuntimeSnapshot(runId: string, signal?: AbortSignal): Promise<RunSnapshot> {
  const path = `/api/runs/${encodeURIComponent(runId)}`;
  const response = await fetch(path, { headers: { Accept: "application/json" }, signal });
  if (!response.ok) throw new Error(`${path} returned HTTP ${response.status}`);
  return parseRunSnapshot(await response.json());
}

let commandSequence = 0;
export function nextCommandKey(action: string): string {
  commandSequence += 1;
  const random = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${commandSequence}`;
  return `ui-${action}-${random}`;
}

export function startRuntimeRun(input: string): Promise<RunSnapshot> {
  return postRuntime("/api/runs", {
    approved: true,
    idempotency_key: nextCommandKey("start"),
    scenario_id: "data-analyst",
    case_id: "case-1",
    input,
    seed: 2026
  });
}

export function stepRuntimeRun(runId: string, pace: "fast" | "observe"): Promise<RunSnapshot> {
  return postRuntime(`/api/runs/${encodeURIComponent(runId)}/step`, {
    approved: true,
    idempotency_key: nextCommandKey("step"),
    pace
  });
}

export function completeRuntimeRun(runId: string, pace: "fast" | "observe"): Promise<RunSnapshot> {
  return postRuntime(`/api/runs/${encodeURIComponent(runId)}/run`, {
    approved: true,
    idempotency_key: nextCommandKey("run"),
    pace
  });
}

export function resetRuntimeRun(runId: string): Promise<RunSnapshot> {
  return postRuntime(`/api/runs/${encodeURIComponent(runId)}/reset`, {
    approved: true,
    idempotency_key: nextCommandKey("reset")
  });
}

export async function loadSystemStatus(signal: AbortSignal) {
  const [health, capabilities] = await Promise.all([
    readJson("/api/health", signal, parseHealth),
    readJson("/api/capabilities", signal, parseCapabilities)
  ]);
  return { health, capabilities };
}
