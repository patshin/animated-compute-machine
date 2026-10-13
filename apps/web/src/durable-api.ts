import type { components } from "@agent-trace/contracts";

import { nextCommandKey, parseAgentEvent, type AgentEvent } from "./api";

export type DurableCheckpoint = components["schemas"]["DurableCheckpoint"];
export type DurableNode = components["schemas"]["DurableNodeSnapshot"];
export type DurableRunState = components["schemas"]["DurableRunState"];
export type DurableSnapshot = components["schemas"]["DurableRunSnapshot"];
export type DurableReplay = components["schemas"]["DurableReplayResponse"];
export type DurableCommandHandle = components["schemas"]["DurableCommandHandle"];
type JsonValue = components["schemas"]["JsonValue"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isJsonValue(value: unknown): value is JsonValue {
  return value === null
    || typeof value === "string"
    || typeof value === "boolean"
    || (typeof value === "number" && Number.isFinite(value))
    || (Array.isArray(value) && value.every(isJsonValue))
    || (isRecord(value) && Object.values(value).every(isJsonValue));
}

function isInteger(value: unknown, minimum = 0): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= minimum;
}

function isNode(value: unknown): value is DurableNode {
  return isRecord(value)
    && typeof value.id === "string"
    && /^[a-z][a-z0-9_]{1,63}$/.test(value.id)
    && typeof value.label === "string"
    && ["input", "router", "compute", "retrieval", "subgraph", "validation", "output"].includes(String(value.kind))
    && ["graph", "provider", "evidence", "policy", "output"].includes(String(value.lane))
    && ["pending", "running", "completed", "error", "cancelled"].includes(String(value.status))
    && (value.parent_id == null || typeof value.parent_id === "string")
    && (value.started_sequence == null || isInteger(value.started_sequence, 1))
    && (value.completed_sequence == null || isInteger(value.completed_sequence, 1));
}

function isCheckpoint(value: unknown): value is DurableCheckpoint {
  return isRecord(value)
    && typeof value.checkpoint_id === "string"
    && /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/.test(value.checkpoint_id)
    && typeof value.run_id === "string"
    && typeof value.thread_id === "string"
    && isInteger(value.sequence, 1)
    && isInteger(value.step_count, 1)
    && ["step", "fork", "override"].includes(String(value.reason))
    && value.state_schema_version === "1.0.0"
    && value.scenario_version === "1.0.0"
    && value.provider_version === "mock-1.0.0"
    && typeof value.integrity_hash === "string"
    && /^[a-f0-9]{64}$/.test(value.integrity_hash)
    && Array.isArray(value.next_nodes)
    && value.next_nodes.every((item) => typeof item === "string")
    && typeof value.created_at === "string"
    && !Number.isNaN(Date.parse(value.created_at));
}

export function isRunState(value: unknown): value is DurableRunState {
  if (!isRecord(value)
    || value.schema_id !== "agent-trace://durable-run-state/1.0.0"
    || value.schema_version !== "1.0.0"
    || typeof value.run_id !== "string"
    || !/^run_[A-Za-z0-9_-]+$/.test(value.run_id)
    || typeof value.thread_id !== "string"
    || !["idle", "running", "paused", "completed", "cancelled", "error"].includes(String(value.status))
    || value.scenario_id !== "durability-lab"
    || value.scenario_version !== "1.0.0"
    || value.provider_id !== "mock"
    || value.provider_version !== "mock-1.0.0"
    || value.mode !== "sandbox"
    || typeof value.input !== "string"
    || !isInteger(value.seed)
    || !isInteger(value.cycle, 1)
    || !isInteger(value.step_count)
    || !isInteger(value.last_sequence)
    || !isInteger(value.checkpoint_count)
    || (value.current_node != null && typeof value.current_node !== "string")
    || !Array.isArray(value.next_nodes)
    || !value.next_nodes.every((item) => typeof item === "string")
    || !Array.isArray(value.nodes)
    || !value.nodes.every(isNode)
    || !isRecord(value.values)
    || !Object.values(value.values).every(isJsonValue)) return false;
  const nodeIds = new Set(value.nodes.map((node) => node.id));
  return nodeIds.size === value.nodes.length
    && value.next_nodes.every((nodeId) => nodeIds.has(nodeId))
    && (value.current_node == null || nodeIds.has(value.current_node as string));
}

function cloneRun(run: DurableRunState): DurableRunState {
  return JSON.parse(JSON.stringify(run)) as DurableRunState;
}

function applyCanonicalStatePatch(run: DurableRunState, payload: Record<string, unknown>): void {
  if (!Array.isArray(payload.changes)
    || payload.changes.length === 0
    || typeof payload.provenance !== "string"
    || payload.provenance.length === 0) {
    throw new Error("durable state.patch violated the canonical contract");
  }
  payload.changes.forEach((value) => {
    if (!isRecord(value)
      || typeof value.path !== "string"
      || !/^(values\.[a-z][a-z0-9_]*|answer)$/.test(value.path)
      || typeof value.source_node !== "string"
      || !/^[a-z][a-z0-9_]{1,63}$/.test(value.source_node)
      || !isJsonValue(value.before)
      || !isJsonValue(value.after)) {
      throw new Error("durable state.patch violated the canonical contract");
    }
    const key = value.path === "answer" ? "answer" : value.path.slice("values.".length);
    run.values[key] = value.after;
  });
}

export function replayBaseRun(source: DurableRunState): DurableRunState {
  const run = cloneRun(source);
  run.status = "idle";
  run.cycle = 1;
  run.step_count = 0;
  run.current_node = null;
  run.next_nodes = ["intake"];
  run.nodes = run.nodes.map((node) => ({
    ...node,
    status: "pending",
    started_sequence: null,
    completed_sequence: null
  }));
  run.values = {
    quality_target: 0.9,
    brief_label: "Durable runtime brief",
    branch_results: {},
    completed_nodes: [],
    retry_count: 0
  };
  run.last_sequence = 0;
  run.checkpoint_count = 0;
  return run;
}

export function projectDurableRun(
  base: DurableRunState,
  events: AgentEvent[]
): DurableRunState {
  let run = cloneRun(base);
  events
    .filter((event) => event.sequence > run.last_sequence)
    .sort((left, right) => left.sequence - right.sequence)
    .forEach((event) => {
      const state = event.payload.state;
      if (isRunState(state)) run = cloneRun(state);
      const node = run.nodes.find((item) => item.id === event.node_id);
      if (event.type === "node.started" && node) {
        node.status = "running";
        node.started_sequence = event.sequence;
        run.current_node = node.id;
        run.status = "running";
      } else if (event.type === "node.completed" && node) {
        node.status = "completed";
        node.completed_sequence = event.sequence;
      } else if (event.type === "state.patch") {
        applyCanonicalStatePatch(run, event.payload);
      } else if (event.type === "checkpoint.created") {
        run.checkpoint_count += 1;
      } else if (event.type === "step.completed") {
        const count = event.payload.step_count;
        const nextNodes = event.payload.next_nodes;
        if (isInteger(count)) run.step_count = count;
        if (Array.isArray(nextNodes) && nextNodes.every((item) => typeof item === "string")) {
          run.next_nodes = nextNodes;
        }
        run.current_node = null;
        run.status = run.next_nodes.length === 0 ? "completed" : "idle";
      } else if (event.type === "run.paused" || event.type === "run.interrupted") {
        run.status = "paused";
        run.current_node = null;
      } else if (event.type === "run.resumed") {
        run.status = "idle";
      } else if (event.type === "run.cancelled") {
        run.status = "cancelled";
        run.current_node = null;
        run.nodes.forEach((item) => {
          if (item.status === "running") item.status = "cancelled";
        });
      } else if (event.type === "run.failed") {
        run.status = "error";
      } else if (event.type === "run.completed") {
        run.status = "completed";
        run.current_node = null;
        run.next_nodes = [];
      }
      run.last_sequence = event.sequence;
    });
  return run;
}

export function parseDurableSnapshot(value: unknown): DurableSnapshot {
  if (!isRecord(value)
    || value.schema_id !== "agent-trace://durable-run-snapshot/1.0.0"
    || !isRunState(value.run)
    || !Array.isArray(value.events)
    || !Array.isArray(value.checkpoints)) {
    throw new Error("durable runtime crossed an invalid typed boundary");
  }
  const events = value.events.map(parseAgentEvent);
  events.forEach((event) => {
    if (event.type === "state.patch") {
      const probe = cloneRun(value.run as DurableRunState);
      applyCanonicalStatePatch(probe, event.payload);
    }
  });
  const checkpoints = value.checkpoints.filter(isCheckpoint);
  const run = value.run as DurableRunState;
  if (checkpoints.length !== value.checkpoints.length
    || events.some((event, index) => event.run_id !== run.run_id || event.sequence !== index + 1)
    || checkpoints.some((checkpoint) => checkpoint.run_id !== run.run_id)
    || run.last_sequence !== events.length
    || run.checkpoint_count !== checkpoints.length) {
    throw new Error("durable runtime returned inconsistent event or checkpoint history");
  }
  return { schema_id: value.schema_id, run, events, checkpoints };
}

async function readJson(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(path, {
    ...init,
    headers: { Accept: "application/json", "Content-Type": "application/json", ...init?.headers }
  });
  const value: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = isRecord(value) && typeof value.detail === "string" ? value.detail : `HTTP ${response.status}`;
    throw new Error(`${path} returned ${detail}`);
  }
  return value;
}

function command(action: string, expectedSequence: number): RequestInit {
  return {
    method: "POST",
    body: JSON.stringify({
      approved: true,
      idempotency_key: nextCommandKey(`durable-${action}`),
      expected_sequence: expectedSequence
    })
  };
}

export async function startDurableRun(input: string): Promise<DurableSnapshot> {
  const handle = parseCommandHandle(await readJson("/api/v1/runs", {
    method: "POST",
    body: JSON.stringify({
      approved: true,
      idempotency_key: nextCommandKey("durable-start"),
      scenario_id: "durability-lab",
      input,
      seed: 2026
    })
  }));
  return loadDurableRun(handle.run_id);
}

export async function loadDurableRun(runId: string): Promise<DurableSnapshot> {
  return parseDurableSnapshot(await readJson(`/api/v1/runs/${encodeURIComponent(runId)}`));
}

export async function commandDurableRun(
  runId: string,
  action: "step" | "run" | "pause" | "resume" | "cancel" | "reset",
  expectedSequence: number,
  initialStepCount = 0
): Promise<DurableSnapshot> {
  const value = await readJson(
    `/api/v1/runs/${encodeURIComponent(runId)}/${action}`,
    command(action, expectedSequence)
  );
  if (action === "pause" || action === "cancel" || action === "reset") {
    return parseDurableSnapshot(value);
  }
  const handle = parseCommandHandle(value);
  for (let attempt = 0; attempt < 400; attempt += 1) {
    const snapshot = await loadDurableRun(handle.run_id);
    const boundaryReached = action === "step"
      ? snapshot.run.step_count > initialStepCount && snapshot.run.status !== "running"
      : ["paused", "completed", "cancelled", "error"].includes(snapshot.run.status);
    if (boundaryReached) return snapshot;
    await new Promise((resolve) => window.setTimeout(resolve, 25));
  }
  throw new Error(`${action} was accepted but did not reach a durable boundary`);
}

function parseCommandHandle(value: unknown): DurableCommandHandle {
  if (!isRecord(value)
    || value.schema_id !== "agent-trace://durable-command-handle/1.0.0"
    || typeof value.command_id !== "string"
    || !["start", "step", "run", "resume"].includes(String(value.command))
    || typeof value.run_id !== "string"
    || value.status !== "accepted"
    || !isInteger(value.accepted_sequence)
    || typeof value.snapshot_url !== "string"
    || typeof value.events_url !== "string") {
    throw new Error("durable command crossed an invalid accepted-handle boundary");
  }
  return value as DurableCommandHandle;
}

export async function forkDurableRun(
  runId: string,
  checkpointId: string
): Promise<DurableSnapshot> {
  return parseDurableSnapshot(await readJson(
    `/api/v1/runs/${encodeURIComponent(runId)}/fork`,
    {
      method: "POST",
      body: JSON.stringify({
        approved: true,
        idempotency_key: nextCommandKey("durable-fork"),
        checkpoint_id: checkpointId,
        state_override: { brief_label: "Forked recovery brief" }
      })
    }
  ));
}

export async function overrideDurableRun(
  runId: string,
  checkpointId: string,
  expectedSequence: number,
  values: { quality_target: number; brief_label: string }
): Promise<DurableSnapshot> {
  return parseDurableSnapshot(await readJson(
    `/api/v1/runs/${encodeURIComponent(runId)}/state`,
    {
      method: "POST",
      body: JSON.stringify({
        approved: true,
        idempotency_key: nextCommandKey("durable-override"),
        expected_sequence: expectedSequence,
        checkpoint_id: checkpointId,
        values
      })
    }
  ));
}

export async function loadDurableReplay(runId: string): Promise<DurableReplay> {
  const value = await readJson(`/api/v1/runs/${encodeURIComponent(runId)}/replay-sessions`, {
    method: "POST",
    body: JSON.stringify({
      approved: true,
      idempotency_key: nextCommandKey("durable-replay"),
      cursor: 0
    })
  });
  return parseReplay(value);
}

function parseReplay(value: unknown): DurableReplay {
  if (!isRecord(value)
    || value.schema_id !== "agent-trace://durable-replay-session/1.0.0"
    || typeof value.session_id !== "string"
    || typeof value.source_run_id !== "string"
    || !["paused", "playing", "completed"].includes(String(value.status))
    || !isInteger(value.cursor)
    || ![0.5, 1, 2, 4].includes(Number(value.speed))
    || value.source !== "stored-events-only"
    || value.provider_executions !== 0
    || value.tool_executions !== 0
    || !isRunState(value.run)
    || !Array.isArray(value.events)) {
    throw new Error("durable replay crossed an invalid typed boundary");
  }
  return { ...value, events: value.events.map(parseAgentEvent) } as DurableReplay;
}

export async function controlDurableReplay(
  sessionId: string,
  action: "pause" | "play" | "step" | "tick" | "seek" | "checkpoint" | "speed" | "reset",
  option?: { cursor?: number; speed?: 0.5 | 1 | 2 | 4; checkpoint_id?: string }
): Promise<DurableReplay> {
  return parseReplay(await readJson(
    `/api/v1/replay-sessions/${encodeURIComponent(sessionId)}/control`,
    {
      method: "POST",
      body: JSON.stringify({
        approved: true,
        idempotency_key: nextCommandKey(`durable-replay-${action}`),
        action,
        ...option
      })
    }
  ));
}

async function loadEventPage(runId: string, afterSequence: number): Promise<AgentEvent[]> {
  const value = await readJson(
    `/api/v1/runs/${encodeURIComponent(runId)}/events/page?after_sequence=${afterSequence}`
  );
  if (!isRecord(value) || !Array.isArray(value.events)) {
    throw new Error("durable event gap crossed an invalid typed boundary");
  }
  return value.events.map(parseAgentEvent);
}

export function streamDurableEvents(
  runId: string,
  afterSequence: number,
  onEvents: (events: AgentEvent[]) => void,
  onSnapshot: (snapshot: DurableSnapshot) => void,
  onError: () => void
): () => void {
  let cursor = afterSequence;
  let source: EventSource | null = null;
  let closed = false;
  let reconnectTimer: number | null = null;
  let batch: AgentEvent[] = [];
  let flushQueued = false;

  const flush = () => {
    flushQueued = false;
    if (batch.length === 0) return;
    const next = batch;
    batch = [];
    onEvents(next);
  };
  const enqueue = (events: AgentEvent[]) => {
    events.forEach((event) => {
      if (event.sequence > cursor) {
        cursor = event.sequence;
        batch.push(event);
      }
    });
    if (!flushQueued && batch.length > 0) {
      flushQueued = true;
      queueMicrotask(flush);
    }
  };
  const connect = () => {
    if (closed) return;
    source = new EventSource(
      `/api/v1/runs/${encodeURIComponent(runId)}/events/stream?after_sequence=${cursor}&follow=true`
    );
    source.addEventListener("agent_event", (message) => {
      try {
        enqueue([parseAgentEvent(JSON.parse((message as MessageEvent<string>).data))]);
      } catch {
        source?.close();
        onError();
      }
    });
    source.onerror = () => {
      source?.close();
      if (closed) return;
      const disconnectedAt = cursor;
      void Promise.all([
        loadDurableRun(runId),
        loadEventPage(runId, disconnectedAt)
      ])
        .then(([snapshot, gap]) => {
          onSnapshot(snapshot);
          cursor = snapshot.run.last_sequence;
          enqueue(gap);
        })
        .catch(onError)
        .finally(() => {
          if (!closed) reconnectTimer = window.setTimeout(connect, 200);
        });
    };
  };
  connect();
  return () => {
    closed = true;
    source?.close();
    if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
    flush();
  };
}
