import { parseRunState, type AgentEvent, type RunSnapshot, type RunState } from "./api";

function setNodeStatus(run: RunState, nodeId: string, status: RunState["nodes"][number]["status"]) {
  const node = run.nodes.find((item) => item.id === nodeId);
  if (!node) throw new Error(`event references unknown node ${nodeId}`);
  node.status = status;
}

function readInteger(value: unknown, label: string): number {
  if (!Number.isInteger(value)) throw new Error(`${label} must be an integer`);
  return value as number;
}

export function projectRuntimeEvent(snapshot: RunSnapshot, event: AgentEvent): RunSnapshot {
  if (event.run_id !== snapshot.run.run_id) throw new Error("cannot project an event from another run");
  if (snapshot.events.some((item) => item.event_id === event.event_id) || event.sequence <= snapshot.run.last_sequence) {
    return snapshot;
  }
  if (event.sequence !== snapshot.run.last_sequence + 1) {
    throw new Error(`event sequence gap: expected ${snapshot.run.last_sequence + 1}, got ${event.sequence}`);
  }

  if (event.type === "run.reset") {
    const reset = parseRunState(event.payload.state);
    return {
      schema_id: snapshot.schema_id,
      run: { ...reset, last_sequence: event.sequence },
      events: [...snapshot.events, event]
    };
  }

  const run = structuredClone(snapshot.run);
  if (event.type === "node.started" && event.node_id) {
    run.status = "running";
    run.current_node = event.node_id;
    setNodeStatus(run, event.node_id, "running");
  } else if (event.type === "state.patch") {
    const changes = event.payload.changes;
    if (!Array.isArray(changes)) throw new Error("state.patch is missing changes");
    for (const rawChange of changes) {
      if (typeof rawChange !== "object" || rawChange === null || Array.isArray(rawChange)) {
        throw new Error("state.patch contains an invalid change");
      }
      const change = rawChange as Record<string, unknown>;
      if (typeof change.path !== "string" || typeof change.source_node !== "string") {
        throw new Error("state.patch change is missing its typed path or source");
      }
      if (change.path === "answer") {
        run.answer = change.after as RunState["answer"];
      } else if (/^values\.[a-z][a-z0-9_]*$/.test(change.path)) {
        run.values[change.path.slice("values.".length)] = change.after;
      } else {
        throw new Error(`state.patch path is not allowed: ${change.path}`);
      }
    }
  } else if (event.type === "node.completed" && event.node_id) {
    setNodeStatus(run, event.node_id, "completed");
  } else if (event.type === "node.failed" && event.node_id) {
    setNodeStatus(run, event.node_id, "error");
    run.current_node = null;
    run.status = "error";
  } else if (event.type === "step.completed") {
    run.cursor = readInteger(event.payload.cursor, "cursor");
    run.step_count = readInteger(event.payload.step_count, "step_count");
    run.current_node = null;
    run.status = "idle";
  } else if (event.type === "run.completed") {
    run.status = "completed";
    run.current_node = null;
  }
  run.last_sequence = event.sequence;
  parseRunState(run);
  return { schema_id: snapshot.schema_id, run, events: [...snapshot.events, event] };
}

export function eventsInCurrentRunCycle(snapshot: RunSnapshot): AgentEvent[] {
  const latestResetIndex = snapshot.events.findLastIndex((event) => event.type === "run.reset");
  return snapshot.events.slice(latestResetIndex + 1);
}

export function latestStatePatch(snapshot: RunSnapshot, nodeId?: string | null): AgentEvent | null {
  return eventsInCurrentRunCycle(snapshot).reverse().find((event) =>
    event.type === "state.patch" && (!nodeId || event.node_id === nodeId)
  ) ?? null;
}
