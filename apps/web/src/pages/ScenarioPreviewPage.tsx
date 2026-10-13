import { useEffect, useMemo, useState } from "react";

import type { AgentEvent } from "../api";
import {
  assertScenarioPreviewCorrelation,
  loadScenarioPreview,
  loadScenarioPreviewRun,
  resetScenarioPreview,
  runScenarioPreview,
  startScenarioPreview,
  stepScenarioPreview,
  type JsonObject,
  type ScenarioPreview,
  type ScenarioPreviewNode,
  type ScenarioPreviewRunSnapshot
} from "../scenario-api";
import { Badge, Button, ErrorState, Input, Panel } from "../ui/primitives";

interface ScenarioPreviewPageProps { onNavigate: (path: string) => void; }

function text(value: unknown, fallback = "—"): string { return typeof value === "string" ? value : fallback; }
function record(value: unknown): JsonObject { return typeof value === "object" && value !== null && !Array.isArray(value) ? value as JsonObject : {}; }

function currentCycle(events: AgentEvent[]): AgentEvent[] {
  const resetIndex = events.findLastIndex((event) => event.type === "run.reset");
  return resetIndex < 0 ? events : events.slice(resetIndex);
}

function statePatch(events: AgentEvent[], nodeId: string): JsonObject | null {
  const event = [...currentCycle(events)].reverse().find((item) => item.type === "state.patch" && item.node_id === nodeId);
  return event ? record(event.payload) : null;
}

export function ScenarioPreviewPage({ onNavigate }: ScenarioPreviewPageProps) {
  const params = new URLSearchParams(window.location.search);
  const source = params.get("source") === "draft" ? "draft" : "built_in";
  const sourceId = params.get("id") ?? "customer-support";
  const persistedRunId = params.get("run");
  const [definition, setDefinition] = useState<ScenarioPreview | null>(null);
  const [snapshot, setSnapshot] = useState<ScenarioPreviewRunSnapshot | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [input, setInput] = useState("Inspect this validated Scenario Kit through its real graph path.");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setDefinition(null); setSnapshot(null); setSelectedNodeId(null); setError(null);
    void Promise.all([
      loadScenarioPreview(source, sourceId, controller.signal),
      persistedRunId ? loadScenarioPreviewRun(persistedRunId, controller.signal) : Promise.resolve(null)
    ]).then(([nextDefinition, nextSnapshot]) => {
      if (nextSnapshot) assertScenarioPreviewCorrelation(nextDefinition, nextSnapshot);
      setDefinition(nextDefinition);
      setSnapshot(nextSnapshot);
      setSelectedNodeId(nextSnapshot?.run.next_node ?? nextSnapshot?.run.nodes[0]?.id ?? nextDefinition.nodes[0]?.id as string);
      if (nextSnapshot) setInput(nextSnapshot.run.input);
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Preview failed to load");
    });
    return () => controller.abort();
  }, [persistedRunId, source, sourceId]);

  const nodes: ScenarioPreviewNode[] = snapshot?.run.nodes ?? (definition?.nodes.map((node) => ({
    id: text(node.id), label: text(node.label, text(node.id)), kind: text(node.kind, "compute"),
    owner: ["model", "code", "tool", "human"].includes(text(node.owner)) ? text(node.owner) as ScenarioPreviewNode["owner"] : "code",
    reads: Array.isArray(node.reads) ? node.reads.filter((item): item is string => typeof item === "string") : [],
    writes: Array.isArray(node.writes) ? node.writes.filter((item): item is string => typeof item === "string") : [], status: "pending"
  })) ?? []);
  const selectedNode = nodes.find((node) => node.id === selectedNodeId) ?? nodes[0];
  const patch = useMemo(() => snapshot && selectedNode ? statePatch(snapshot.events, selectedNode.id) : null, [selectedNode, snapshot]);

  const start = async () => {
    setBusy("start"); setError(null);
    try {
      const next = await startScenarioPreview(source, sourceId, input);
      setSnapshot(next); setSelectedNodeId(next.run.next_node ?? next.run.nodes[0]?.id ?? null);
      const nextParams = new URLSearchParams({ source, id: sourceId, run: next.run.run_id });
      window.history.replaceState({}, "", `/lab/scenario-preview?${nextParams.toString()}`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Preview run could not start"); }
    finally { setBusy(null); }
  };

  const execute = async (action: "step" | "run" | "reset") => {
    if (!snapshot) return;
    setBusy(action); setError(null);
    try {
      const next = action === "step" ? await stepScenarioPreview(snapshot.run.run_id)
        : action === "run" ? await runScenarioPreview(snapshot.run.run_id)
          : await resetScenarioPreview(snapshot.run.run_id);
      setSnapshot(next);
      setSelectedNodeId(next.run.next_node ?? next.run.nodes.find((node) => node.status === "completed")?.id ?? next.run.nodes[0]?.id ?? null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : `Preview ${action} failed`); }
    finally { setBusy(null); }
  };

  return (
    <div className="page scenario-preview-page">
      <header className="workspace-header scenario-header">
        <div><span className="machine-label">VISUAL LAB // CANONICAL EVENT RUNTIME</span><h1>{definition?.title ?? "Scenario preview"}</h1><p>{definition?.objective ?? "Loading a validated Kit through the repository runtime boundary."}</p></div>
        <div className="builder-header-actions"><Button onClick={() => onNavigate(source === "draft" ? `/builder?draft=${encodeURIComponent(sourceId)}` : "/scenarios")}>{source === "draft" ? "Back to Builder" : "Back to Library"}</Button></div>
      </header>
      {error && <ErrorState title="Preview boundary rejected" description={error} />}
      {!definition && !error && <div className="scenario-loading" aria-label="Loading scenario preview"><i /><i /><i /><span>Loading validated graph contracts…</span></div>}
      {definition && !snapshot && <Panel title="Start a durable preview run" context="REAL GRAPH // CONTRACT DRY-RUN"><div className="preview-start"><Input label="Runtime input" value={input} onChange={(event) => setInput(event.target.value)} /><p>Step and Run execute the validated graph and persist canonical events. Tool nodes validate declared schemas without claiming an unavailable side effect.</p><Button variant="primary" loading={busy === "start"} disabled={!input.trim()} onClick={() => void start()}>Start live preview</Button></div></Panel>}
      {definition && snapshot && <>
        <div className="preview-status-rail"><div><span>RUN</span><code>{snapshot.run.run_id}</code></div><div><span>STATUS</span><strong>{snapshot.run.status}</strong></div><div><span>STEP</span><strong>{snapshot.run.step_count} / {snapshot.run.nodes.length}</strong></div><div><span>EVENTS</span><strong>{snapshot.run.last_sequence}</strong></div><div><span>FINGERPRINT</span><code>{snapshot.run.validation_fingerprint.slice(0, 16)}</code></div><Badge tone={snapshot.run.status === "completed" ? "success" : "active"}>CONTRACT DRY-RUN</Badge></div>
        <div className="builder-command-bar preview-command-bar"><div><span className="machine-label">RUNTIME CONTROL</span><strong>{snapshot.run.next_node ? `Next: ${snapshot.run.next_node}` : "Terminal reached"}</strong><small>Persistent state · canonical AgentEvent sequence</small></div><Button loading={busy === "step"} disabled={snapshot.run.status === "completed" || busy !== null} onClick={() => void execute("step")}>Step</Button><Button variant="primary" loading={busy === "run"} disabled={snapshot.run.status === "completed" || busy !== null} onClick={() => void execute("run")}>Run</Button><Button loading={busy === "reset"} disabled={busy !== null} onClick={() => void execute("reset")}>Reset</Button></div>
        <div className="preview-workbench">
          <Panel title="Runtime Graph" context={`${snapshot.run.nodes.length} NODES // ${definition.edges.length} EDGES`} className="preview-graph-panel">
            <div className="preview-graph" role="group" aria-label="Scenario runtime graph nodes">
              {nodes.map((node, index) => <div className="preview-node-wrap" key={node.id}><button className={`${selectedNode?.id === node.id ? "is-selected" : ""} is-${node.status}`} aria-pressed={selectedNode?.id === node.id} onClick={() => setSelectedNodeId(node.id)}><span>{String(index + 1).padStart(2, "0")}</span><strong>{node.label}</strong><small>{node.owner} · {node.kind}</small><em>{node.status}</em></button>{index < nodes.length - 1 && <i aria-hidden="true">→</i>}</div>)}
            </div>
            <ol className="preview-event-strip" aria-label="Canonical runtime event timeline">{snapshot.events.map((event) => <li key={event.event_id}><span>{String(event.sequence).padStart(2, "0")}</span><div><strong>{event.type}</strong><small>{event.node_id ?? "run"} · {event.timestamp}</small></div><Badge tone={event.type.endsWith("completed") ? "success" : event.type === "run.reset" ? "warning" : "neutral"}>EVENT</Badge></li>)}</ol>
          </Panel>
          <Panel title="State Inspector" context="CURRENT RESET CYCLE" tone="console" className="preview-inspector">
            {selectedNode && <><dl><div><dt>node</dt><dd>{selectedNode.id}</dd></div><div><dt>status</dt><dd>{selectedNode.status}</dd></div><div><dt>reads</dt><dd>{selectedNode.reads.join(", ") || "none"}</dd></div><div><dt>writes</dt><dd>{selectedNode.writes.join(", ") || "none"}</dd></div></dl>{patch ? <pre aria-label="Selected node state patch" tabIndex={0}>{JSON.stringify(patch.changes, null, 2)}</pre> : <p className="preview-no-patch">No patch for this node in the current run cycle.</p>}<div className="preview-tool-ledger"><span>STATE VALUES</span><pre tabIndex={0}>{JSON.stringify(snapshot.run.values, null, 2)}</pre></div></>}
          </Panel>
        </div>
        <div className="preview-learning-grid">
          <article><span className="machine-label">LESSON CONTRACT</span><h2>{text(definition.lesson.title)}</h2><p>{text(definition.lesson.objective)}</p><dl><div><dt>Module</dt><dd>{text(definition.lesson.module)}</dd></div><div><dt>Explain back</dt><dd>{text(definition.lesson.explain_back)}</dd></div></dl></article>
          <article><span className="machine-label">GOLDEN CASE</span><h2>{text(definition.golden_case.id)}</h2><p>Loaded from the validated Kit and held outside framework-private runtime state.</p><pre tabIndex={0}>{JSON.stringify(definition.golden_case.assertions, null, 2)}</pre></article>
        </div>
      </>}
    </div>
  );
}
