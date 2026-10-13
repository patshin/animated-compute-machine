import { useEffect, useRef, useState } from "react";

import type { AgentEvent } from "../api";
import {
  commandDurableRun,
  controlDurableReplay,
  forkDurableRun,
  loadDurableRun,
  loadDurableReplay,
  overrideDurableRun,
  projectDurableRun,
  replayBaseRun,
  startDurableRun,
  streamDurableEvents,
  type DurableNode,
  type DurableReplay,
  type DurableSnapshot
} from "../durable-api";
import { Badge, Button, EmptyState, ErrorState, Input, Panel, Select } from "../ui/primitives";

const DEFAULT_INPUT = "Prove that a typed agent run can checkpoint, reload, replay and fork without mutating its parent.";
const ROW_HEIGHT = 34;
const VISIBLE_ROWS = 11;

type Pending = "start" | "step" | "run" | "pause" | "resume" | "cancel" | "reset" | "fork" | "override" | "replay" | "replay-control" | null;

function statusTone(status: string): "active" | "success" | "warning" | "error" | "neutral" {
  if (status === "completed") return "success";
  if (status === "running") return "active";
  if (status === "paused") return "warning";
  if (status === "cancelled" || status === "error") return "error";
  return "neutral";
}

function eventTone(type: string): "active" | "success" | "warning" | "error" | "neutral" {
  if (type.endsWith(".failed") || type === "run.cancelled") return "error";
  if (type.endsWith(".completed") || type === "checkpoint.created") return "success";
  if (type.endsWith(".started") || type === "state.patch") return "active";
  if (type === "run.paused" || type === "state.override") return "warning";
  return "neutral";
}

function eventProjection(snapshot: DurableSnapshot, liveEvents: AgentEvent[]): AgentEvent[] {
  const bySequence = new Map(snapshot.events.map((event) => [event.sequence, event]));
  liveEvents.forEach((event) => bySequence.set(event.sequence, event));
  return Array.from(bySequence.values()).sort((left, right) => left.sequence - right.sequence);
}

function mergeEventBatch(current: AgentEvent[], incoming: AgentEvent[]): AgentEvent[] {
  const bySequence = new Map(current.map((event) => [event.sequence, event]));
  incoming.forEach((event) => bySequence.set(event.sequence, event));
  return Array.from(bySequence.values()).sort((left, right) => left.sequence - right.sequence);
}

function rememberRun(runId: string) {
  const url = new URL(window.location.href);
  url.searchParams.set("run", runId);
  window.history.replaceState(null, "", url);
}

export function VirtualEventTable({ events }: { events: AgentEvent[] }) {
  const [scrollTop, setScrollTop] = useState(0);
  const start = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - 2);
  const end = Math.min(events.length, start + VISIBLE_ROWS + 4);
  const visible = events.slice(start, end);
  return (
    <div
      aria-label={`Canonical event timeline, ${events.length} events`}
      className="durable-trace"
      onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
      role="table"
      tabIndex={0}
    >
      <div className="durable-trace__spacer" style={{ height: events.length * ROW_HEIGHT }}>
        <div style={{ transform: `translateY(${start * ROW_HEIGHT}px)` }}>
          {visible.map((event) => (
            <div className="durable-trace__row" key={event.event_id} role="row">
              <span role="cell">{String(event.sequence).padStart(4, "0")}</span>
              <i aria-hidden="true" />
              <strong role="cell">{event.type}</strong>
              <code role="cell">{event.node_id ?? "run"}</code>
              <Badge tone={eventTone(event.type)}>{event.type.split(".").at(-1)?.toUpperCase()}</Badge>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function GraphNode({ node, selected, hasChildren, expanded, onSelect }: {
  node: DurableNode;
  selected: boolean;
  hasChildren: boolean;
  expanded: boolean;
  onSelect: (nodeId: string, toggleChildren: boolean) => void;
}) {
  return (
    <button
      aria-expanded={hasChildren ? expanded : undefined}
      aria-pressed={selected}
      className={`durable-node is-${node.status} ${node.parent_id ? "is-child" : ""}`}
      onClick={() => onSelect(node.id, hasChildren)}
      type="button"
    >
      <span>{node.kind.toUpperCase()}</span>
      <strong>{node.label}</strong>
      <small>{node.lane} // {node.status}{hasChildren ? ` // ${expanded ? "COLLAPSE" : "EXPAND"}` : ""}</small>
      <i aria-hidden="true" />
    </button>
  );
}

function StateInspector({ snapshot, selectedNode, events }: {
  snapshot: DurableSnapshot;
  selectedNode: DurableNode;
  events: AgentEvent[];
}) {
  const resetIndex = events.findLastIndex((event) => event.type === "run.reset");
  const currentCycle = events.slice(resetIndex + 1);
  const nodeEvents = currentCycle.filter((event) => event.node_id === selectedNode.id);
  const completedNodes = Array.isArray(snapshot.run.values.completed_nodes)
    ? snapshot.run.values.completed_nodes.filter((item): item is string => typeof item === "string")
    : [];
  const branch = snapshot.run.values.branch_results;
  const branchValue = branch && typeof branch === "object" && !Array.isArray(branch)
    ? (branch as Record<string, unknown>)[selectedNode.id]
    : undefined;
  return (
    <div className="durable-inspector">
      <div className="durable-inspector__identity">
        <Badge tone={statusTone(selectedNode.status)}>{selectedNode.status.toUpperCase()}</Badge>
        <div><strong>{selectedNode.label}</strong><code>{selectedNode.id}</code></div>
      </div>
      <dl>
        <div><dt>CYCLE</dt><dd>{snapshot.run.cycle}</dd></div>
        <div><dt>START EVENT</dt><dd>{selectedNode.started_sequence ?? "—"}</dd></div>
        <div><dt>COMPLETE EVENT</dt><dd>{selectedNode.completed_sequence ?? "—"}</dd></div>
        <div><dt>CURRENT-CYCLE EVENTS</dt><dd>{nodeEvents.length}</dd></div>
      </dl>
      {nodeEvents.length === 0 && !completedNodes.includes(selectedNode.id) ? (
        <EmptyState title="No state for this node" description="This node has not emitted a canonical event in the current reset cycle." />
      ) : (
        <pre>{JSON.stringify(branchValue ?? {
          completed: completedNodes.includes(selectedNode.id),
          next_nodes: snapshot.run.next_nodes,
          checkpoint_count: snapshot.run.checkpoint_count
        }, null, 2)}</pre>
      )}
    </div>
  );
}

export function DurableRuntimePage({ onNavigate }: { onNavigate: (path: string) => void }) {
  const [input, setInput] = useState(DEFAULT_INPUT);
  const [snapshot, setSnapshot] = useState<DurableSnapshot | null>(null);
  const [liveEvents, setLiveEvents] = useState<AgentEvent[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState("intake");
  const [pending, setPending] = useState<Pending>(null);
  const [error, setError] = useState<string | null>(null);
  const [replayReceipt, setReplayReceipt] = useState<string | null>(null);
  const [replaySession, setReplaySession] = useState<DurableReplay | null>(null);
  const [qualityTarget, setQualityTarget] = useState("0.90");
  const [briefLabel, setBriefLabel] = useState("Durable runtime brief");
  const [expandedSubgraphs, setExpandedSubgraphs] = useState(() => new Set(["draft_workbench"]));
  const closeStream = useRef<(() => void) | null>(null);
  const replayControlVersion = useRef(0);

  useEffect(() => () => closeStream.current?.(), []);
  const liveProjection = snapshot ? eventProjection(snapshot, liveEvents) : [];
  const events = replaySession?.events ?? liveProjection;
  const liveRun = snapshot ? projectDurableRun(snapshot.run, liveEvents) : null;
  const replayRun = snapshot && replaySession
    ? projectDurableRun(replayBaseRun(snapshot.run), replaySession.events)
    : null;
  const projectedSnapshot = snapshot
    ? { ...snapshot, run: replayRun ?? liveRun ?? snapshot.run, events }
    : null;
  const selectedNode = projectedSnapshot?.run.nodes.find((node) => node.id === selectedNodeId)
    ?? projectedSnapshot?.run.nodes[0]
    ?? null;
  const latestCheckpoint = snapshot?.checkpoints.at(-1) ?? null;

  useEffect(() => {
    const runId = new URL(window.location.href).searchParams.get("run");
    if (!runId) return undefined;
    let active = true;
    setPending("start");
    setError(null);
    void loadDurableRun(runId)
      .then((restored) => {
        if (!active) return;
        setSnapshot(restored);
        setQualityTarget(String(restored.run.values.quality_target ?? 0.9));
        setBriefLabel(String(restored.run.values.brief_label ?? "Durable runtime brief"));
        setSelectedNodeId(restored.run.current_node ?? restored.run.next_nodes[0] ?? "publish");
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setError(reason instanceof Error ? reason.message : "Durable checkpoint restoration failed");
      })
      .finally(() => {
        if (active) setPending(null);
      });
    return () => {
      active = false;
    };
  }, []);

  const apply = async (action: Exclude<Pending, "start" | "fork" | "replay" | null>) => {
    if (!snapshot) return;
    if (action === "override" || action === "replay-control") return;
    setPending(action);
    setError(null);
    setReplayReceipt(null);
    setReplaySession(null);
    if (action === "step" || action === "run" || action === "resume") {
      closeStream.current?.();
      closeStream.current = streamDurableEvents(
        snapshot.run.run_id,
        snapshot.run.last_sequence,
        (incoming) => {
          setLiveEvents((current) => mergeEventBatch(current, incoming));
          const activeNode = incoming.findLast((event) => event.type === "node.started");
          if (activeNode?.node_id) setSelectedNodeId(activeNode.node_id);
        },
        (restored) => {
          setSnapshot(restored);
          setLiveEvents((current) => current.filter(
            (event) => event.sequence > restored.run.last_sequence
          ));
        },
        () => setError("Live event stream disconnected after gap recovery failed")
      );
    }
    try {
      const next = await commandDurableRun(
        snapshot.run.run_id,
        action,
        Math.max(snapshot.run.last_sequence, liveEvents.at(-1)?.sequence ?? 0),
        snapshot.run.step_count
      );
      setSnapshot(next);
      setQualityTarget(String(next.run.values.quality_target ?? 0.9));
      setBriefLabel(String(next.run.values.brief_label ?? "Durable runtime brief"));
      setLiveEvents([]);
      if (action === "reset") setSelectedNodeId("intake");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Durable command failed");
    } finally {
      closeStream.current?.();
      closeStream.current = null;
      setPending(null);
    }
  };

  const start = async () => {
    setPending("start");
    setError(null);
    try {
      const next = await startDurableRun(input);
      setSnapshot(next);
      rememberRun(next.run.run_id);
      setQualityTarget(String(next.run.values.quality_target ?? 0.9));
      setBriefLabel(String(next.run.values.brief_label ?? "Durable runtime brief"));
      setSelectedNodeId("intake");
      setLiveEvents([]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Durable runtime failed to start");
    } finally {
      setPending(null);
    }
  };

  const fork = async () => {
    if (!snapshot || !latestCheckpoint) return;
    setPending("fork");
    setError(null);
    try {
      const child = await forkDurableRun(snapshot.run.run_id, latestCheckpoint.checkpoint_id);
      setSnapshot(child);
      rememberRun(child.run.run_id);
      setLiveEvents([]);
      setQualityTarget(String(child.run.values.quality_target ?? 0.9));
      setBriefLabel(String(child.run.values.brief_label ?? "Durable runtime brief"));
      setSelectedNodeId(child.run.next_nodes[0] ?? "publish");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Fork failed");
    } finally {
      setPending(null);
    }
  };

  const replay = async () => {
    if (!snapshot) return;
    setPending("replay");
    setError(null);
    try {
      const receipt = await loadDurableReplay(snapshot.run.run_id);
      setReplaySession(receipt);
      setReplayReceipt(`session ${receipt.session_id.slice(0, 14)}… · 0 provider · 0 tool executions`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Replay failed");
    } finally {
      setPending(null);
    }
  };

  const override = async () => {
    if (!snapshot || !latestCheckpoint) return;
    const target = Number(qualityTarget);
    if (!Number.isFinite(target) || target < 0.8 || target > 1 || briefLabel.trim().length === 0) {
      setError("State override requires a quality target from 0.80 to 1.00 and a brief label");
      return;
    }
    setPending("override");
    setError(null);
    try {
      const next = await overrideDurableRun(
        snapshot.run.run_id,
        latestCheckpoint.checkpoint_id,
        snapshot.run.last_sequence,
        { quality_target: target, brief_label: briefLabel.trim() }
      );
      setSnapshot(next);
      setLiveEvents([]);
      setReplaySession(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "State override failed");
    } finally {
      setPending(null);
    }
  };

  const controlReplay = async (
    action: "pause" | "play" | "step" | "seek" | "checkpoint" | "speed" | "reset",
    option?: { cursor?: number; speed?: 0.5 | 1 | 2 | 4; checkpoint_id?: string }
  ) => {
    if (!replaySession) return;
    const version = replayControlVersion.current + 1;
    replayControlVersion.current = version;
    setPending("replay-control");
    setError(null);
    try {
      const next = await controlDurableReplay(replaySession.session_id, action, option);
      if (replayControlVersion.current === version) setReplaySession(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Replay control failed");
    } finally {
      setPending(null);
    }
  };

  useEffect(() => {
    if (!replaySession || replaySession.status !== "playing") return undefined;
    const version = replayControlVersion.current;
    const timer = window.setTimeout(() => {
      void controlDurableReplay(replaySession.session_id, "tick")
        .then((next) => {
          if (replayControlVersion.current === version) setReplaySession(next);
        })
        .catch((reason: unknown) => {
          if (replayControlVersion.current === version) {
            setError(reason instanceof Error ? reason.message : "Replay clock failed");
          }
        });
    }, replayDelayMs(replaySession.speed));
    return () => window.clearTimeout(timer);
  }, [replaySession]);

  const canExecute = snapshot && !replaySession && ["idle"].includes(snapshot.run.status) && pending === null;
  const canResume = snapshot?.run.status === "paused" && pending === null;
  const canInterrupt = snapshot && (pending === "run" || projectedSnapshot?.run.status === "running");
  const canCancel = snapshot && !["completed", "cancelled", "error"].includes(snapshot.run.status)
    && (pending === null || pending === "run");

  return (
    <div className="page durable-page">
      <header className="workspace-header durable-header">
        <div>
          <span className="machine-label">LAB 05 // LANGGRAPH DURABILITY</span>
          <h1>Durable execution bay</h1>
          <p>Step a real compiled graph, inspect canonical events, restore SQLite checkpoints and fork an immutable parent.</p>
        </div>
        <div className="durable-header__actions">
          {replaySession ? <Badge tone="warning">REPLAY // {replaySession.cursor}</Badge> : null}
          <Badge tone={statusTone(projectedSnapshot?.run.status ?? "idle")}>{projectedSnapshot?.run.status.toUpperCase() ?? "NOT LOADED"}</Badge>
          <Button variant="ghost" onClick={() => onNavigate("/lab")}>Runtime X-ray</Button>
        </div>
      </header>

      {error ? <div className="runtime-error" role="alert"><strong>DURABLE BOUNDARY ERROR</strong><div><span>{error}</span><small>The last valid checkpoint projection is preserved.</small></div></div> : null}

      <section className="durable-commandbar" aria-label="Durable runtime controls">
        <Input label="RUN BRIEF" value={input} onChange={(event) => setInput(event.target.value)} disabled={pending !== null || snapshot !== null} />
        {!snapshot ? <Button variant="primary" loading={pending === "start"} onClick={() => void start()}>Load durable case</Button> : (
          <div className="durable-commandbar__buttons">
            <Button variant="primary" disabled={!canExecute} loading={pending === "step"} onClick={() => void apply("step")}>Step</Button>
            <Button variant="console" disabled={!canExecute} loading={pending === "run"} onClick={() => void apply("run")}>Run</Button>
            <Button disabled={!canInterrupt} loading={pending === "pause"} onClick={() => void apply("pause")}>Pause</Button>
            <Button disabled={!canResume} loading={pending === "resume"} onClick={() => void apply("resume")}>Resume</Button>
            <Button variant="danger" disabled={!canCancel} loading={pending === "cancel"} onClick={() => void apply("cancel")}>Cancel</Button>
            <Button disabled={pending !== null || snapshot.run.status === "running"} loading={pending === "reset"} onClick={() => void apply("reset")}>Reset</Button>
          </div>
        )}
      </section>

      {!snapshot ? (
        pending === "start"
          ? <div className="durable-loading" aria-live="polite"><i /><strong>COMPILING GRAPH</strong><span>Opening repository-local SQLite checkpointer…</span></div>
          : error
            ? <ErrorState title="Durable runtime unavailable" description="Resolve the typed API error above, then retry the same offline case." action={<Button onClick={() => void start()}>Retry case</Button>} />
            : <EmptyState title="No durable run loaded" description="Load the case to create a real LangGraph thread and its repository-local SQLite projection." />
      ) : (
        <>
          <section className="durable-status-strip" aria-label="Persistence status">
            <div><span>THREAD</span><code>{projectedSnapshot?.run.thread_id.slice(0, 20)}…</code></div>
            <div><span>CHECKPOINTS</span><strong>{projectedSnapshot?.run.checkpoint_count}</strong></div>
            <div><span>SUPERSTEPS</span><strong>{projectedSnapshot?.run.step_count}</strong></div>
            <div><span>EVENT SEQUENCE</span><strong>{events.at(-1)?.sequence ?? 0}</strong></div>
            <div><span>CYCLE</span><strong>{projectedSnapshot?.run.cycle}</strong></div>
            <div><span>SERIALIZER</span><code>STRICT MSGPACK</code></div>
          </section>

          <div className="durable-workbench">
            <Panel title="Runtime graph" context="REAL LANGGRAPH // CONDITIONAL + PARALLEL + LOOP">
              <div className="durable-graph" aria-label="Durable runtime graph">
                {projectedSnapshot?.run.nodes
                  .filter((node) => !node.parent_id || expandedSubgraphs.has(node.parent_id))
                  .map((node) => {
                    const hasChildren = projectedSnapshot.run.nodes.some(
                      (candidate) => candidate.parent_id === node.id
                    );
                    return (
                      <GraphNode
                        expanded={expandedSubgraphs.has(node.id)}
                        hasChildren={hasChildren}
                        key={node.id}
                        node={node}
                        onSelect={(nodeId, toggleChildren) => {
                          setSelectedNodeId(nodeId);
                          if (toggleChildren) {
                            setExpandedSubgraphs((current) => {
                              const next = new Set(current);
                              if (next.has(nodeId)) next.delete(nodeId);
                              else next.add(nodeId);
                              return next;
                            });
                          }
                        }}
                        selected={node.id === selectedNode?.id}
                      />
                    );
                  })}
              </div>
            </Panel>
            <Panel title="State Inspector" context="SELECTED NODE // CURRENT RESET CYCLE">
              {selectedNode && projectedSnapshot ? <StateInspector snapshot={projectedSnapshot} selectedNode={selectedNode} events={events} /> : null}
            </Panel>
          </div>

          <div className="durable-evidence-grid">
            <Panel title="Trace waterfall" context="CANONICAL AGENTEVENT // VIRTUALIZED">
              <VirtualEventTable events={events} />
            </Panel>
            <Panel title="Checkpoint rail" context="SQLITE // IMMUTABLE HISTORY">
              <ol className="durable-checkpoints" aria-label="Durable checkpoints">
                {snapshot.checkpoints.slice(-8).reverse().map((checkpoint) => (
                  <li key={checkpoint.checkpoint_id}>
                    <span>STEP {String(checkpoint.step_count).padStart(2, "0")}</span>
                    <strong>{checkpoint.reason.toUpperCase()}</strong>
                    <code>{checkpoint.checkpoint_id.slice(0, 13)}…</code>
                    <small>{checkpoint.next_nodes.join(" + ") || "TERMINAL"}</small>
                  </li>
                ))}
              </ol>
              <div className="durable-checkpoint-actions">
                <Button disabled={!latestCheckpoint || pending !== null || Boolean(replaySession)} loading={pending === "fork"} onClick={() => void fork()}>Fork latest checkpoint</Button>
                <Button disabled={pending !== null || Boolean(replaySession)} loading={pending === "replay"} onClick={() => void replay()}>Open replay session</Button>
              </div>
              {latestCheckpoint && !replaySession ? (
                <fieldset className="durable-override">
                  <legend>STATE OVERRIDE // SANDBOX</legend>
                  <Input
                    label="QUALITY TARGET"
                    max="1"
                    min="0.8"
                    onChange={(event) => setQualityTarget(event.target.value)}
                    step="0.01"
                    type="number"
                    value={qualityTarget}
                  />
                  <Input label="BRIEF LABEL" maxLength={80} onChange={(event) => setBriefLabel(event.target.value)} value={briefLabel} />
                  <Button disabled={pending !== null || snapshot.run.status === "running"} loading={pending === "override"} onClick={() => void override()}>Apply at latest checkpoint</Button>
                </fieldset>
              ) : null}
              {replaySession ? (
                <div className="durable-replay-controls" aria-label="Stored event replay controls">
                  <p className="durable-replay-receipt" role="status"><strong>PASSIVE REPLAY</strong>{replayReceipt}</p>
                  <div>
                    <Button disabled={pending !== null} onClick={() => void controlReplay("reset")}>Reset cursor</Button>
                    <Button disabled={pending !== null || replaySession.cursor >= snapshot.events.length} onClick={() => void controlReplay("step")}>Step event</Button>
                    <Button disabled={pending !== null || replaySession.status === "playing" || replaySession.cursor >= snapshot.events.length} onClick={() => void controlReplay("play")}>Play</Button>
                    <Button disabled={pending !== null || replaySession.status !== "playing"} onClick={() => void controlReplay("pause")}>Pause</Button>
                    <Button variant="ghost" disabled={pending !== null} onClick={() => setReplaySession(null)}>Exit replay</Button>
                  </div>
                  <Select
                    label="REPLAY SPEED"
                    onChange={(event) => void controlReplay("speed", { speed: Number(event.target.value) as 0.5 | 1 | 2 | 4 })}
                    value={String(replaySession.speed)}
                  >
                    <option value="0.5">0.5×</option>
                    <option value="1">1×</option>
                    <option value="2">2×</option>
                    <option value="4">4×</option>
                  </Select>
                  <Select
                    label="REPLAY CHECKPOINT"
                    onChange={(event) => {
                      if (event.target.value) {
                        void controlReplay("checkpoint", { checkpoint_id: event.target.value });
                      }
                    }}
                    value=""
                  >
                    <option value="">Choose durable boundary…</option>
                    {snapshot.checkpoints.map((checkpoint) => (
                      <option key={checkpoint.checkpoint_id} value={checkpoint.checkpoint_id}>
                        Step {checkpoint.step_count} // {checkpoint.reason}
                      </option>
                    ))}
                  </Select>
                  <label className="durable-replay-seek">
                    <span>SEEK EVENT {replaySession.cursor} / {snapshot.events.length}</span>
                    <input
                      aria-label="Replay event cursor"
                      max={snapshot.events.length}
                      min="0"
                      onChange={(event) => void controlReplay("seek", { cursor: Number(event.target.value) })}
                      type="range"
                      value={replaySession.cursor}
                    />
                  </label>
                </div>
              ) : replayReceipt ? <p className="durable-replay-receipt" role="status"><strong>PASSIVE REPLAY</strong>{replayReceipt}</p> : null}
            </Panel>
          </div>
        </>
      )}
    </div>
  );
}

export function replayDelayMs(speed: number): number {
  return Math.round(500 / speed);
}
