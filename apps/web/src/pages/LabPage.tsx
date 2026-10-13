import { useEffect, useMemo, useState, type KeyboardEvent } from "react";

import type { AgentEvent, ChartArtifact, NodeSnapshot, RunSnapshot } from "../api";
import { eventsInCurrentRunCycle, latestStatePatch } from "../runtime-projection";
import { useLabRuntime } from "../useLabRuntime";
import { Badge, Button, EmptyState, Input, Panel, Select, SplitPane, Tabs } from "../ui/primitives";

const defaultQuestion = "How did Northstar's fictional revenue change from Q1 to Q4 2025?";

function GraphCanvas({ snapshot, selectedId, onSelect }: {
  snapshot: RunSnapshot;
  selectedId: string | null;
  onSelect: (nodeId: string) => void;
}) {
  const announcedNode = snapshot.run.nodes.find((node) => node.id === snapshot.run.current_node)
    ?? [...snapshot.run.nodes].reverse().find((node) => node.status !== "pending");
  const moveFocus = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const keys = ["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp", "Home", "End"];
    if (!keys.includes(event.key)) return;
    event.preventDefault();
    const buttons = Array.from(
      event.currentTarget.closest(".runtime-graph__flow")
        ?.querySelectorAll<HTMLButtonElement>(".graph-node") ?? []
    );
    const last = buttons.length - 1;
    const next = event.key === "Home"
      ? 0
      : event.key === "End"
        ? last
        : event.key === "ArrowRight" || event.key === "ArrowDown"
          ? Math.min(last, index + 1)
          : Math.max(0, index - 1);
    buttons[next]?.focus();
  };
  return (
    <div className="runtime-graph" aria-label="Runtime graph">
      <p className="runtime-graph__announcement visually-hidden" aria-atomic="true" aria-live="polite" role="status">
        Graph update: {announcedNode ? `${announcedNode.label} is ${announcedNode.status}.` : "No node has started."} Run is {snapshot.run.status}. Step {snapshot.run.step_count} of {snapshot.run.nodes.length}.
      </p>
      <div className="runtime-graph__legend" aria-label="Node status legend">
        <span><i className="is-pending" />PENDING</span>
        <span><i className="is-running" />RUNNING</span>
        <span><i className="is-completed" />COMPLETE</span>
      </div>
      <div className="runtime-graph__flow">
        {snapshot.run.nodes.map((node, index) => (
          <div className="graph-node-wrap" key={node.id}>
            <button
              aria-label={`${node.label}, ${node.status}`}
              aria-pressed={selectedId === node.id}
              className={`graph-node graph-node--${node.status}`}
              data-node-id={node.id}
              onClick={() => onSelect(node.id)}
              onKeyDown={(event) => moveFocus(event, index)}
              tabIndex={selectedId === node.id ? 0 : -1}
              type="button"
            >
              <span className="graph-node__index">{String(index + 1).padStart(2, "0")}</span>
              <span className="graph-node__copy"><strong>{node.label}</strong><small>{node.lane.toUpperCase()}</small></span>
              <span className="graph-node__signal" aria-hidden="true" />
            </button>
            {index < snapshot.run.nodes.length - 1 && <span className="graph-edge" aria-hidden="true"><i /></span>}
          </div>
        ))}
      </div>
    </div>
  );
}

function StateView({ snapshot, selectedNode }: { snapshot: RunSnapshot; selectedNode: NodeSnapshot | null }) {
  const patch = latestStatePatch(snapshot, selectedNode?.id);
  const changes = Array.isArray(patch?.payload.changes) ? patch.payload.changes : [];
  return (
    <div className="state-inspector">
      <dl className="state-inspector__meta">
        <div><dt>NODE</dt><dd>{selectedNode?.label ?? "Select a node"}</dd></div>
        <div><dt>STATUS</dt><dd>{selectedNode?.status ?? snapshot.run.status}</dd></div>
        <div><dt>STEP</dt><dd>{snapshot.run.step_count} / {snapshot.run.nodes.length}</dd></div>
        <div><dt>SEQ</dt><dd>{snapshot.run.last_sequence}</dd></div>
      </dl>
      {patch ? (
        <div className="state-diff">
          <div className="state-diff__heading"><span>BEFORE → AFTER</span><strong>{String(patch.node_id)}</strong></div>
          {changes.map((raw, index) => {
            const change = raw as Record<string, unknown>;
            const kind = change.before == null && change.after != null
              ? "Added"
              : change.before != null && change.after == null
                ? "Removed"
                : "Changed";
            return (
              <article aria-label={`${String(change.path)}: ${kind}`} key={`${String(change.path)}-${index}`}>
                <code>{String(change.path)}</code>
                <div aria-label="Before value" role="group"><span aria-hidden="true">−</span><pre>{JSON.stringify(change.before, null, 2) ?? "null"}</pre></div>
                <div aria-label="After value" role="group"><span aria-hidden="true">+</span><pre>{JSON.stringify(change.after, null, 2) ?? "null"}</pre></div>
              </article>
            );
          })}
          <p><span>PROVENANCE</span>{String(patch.payload.provenance)}</p>
        </div>
      ) : <EmptyState title="No patch for this node" description="Step this node to inspect its validated before/after change and provenance." />}
    </div>
  );
}

function SafeLineChart({ chart }: { chart: ChartArtifact }) {
  const points = chart.series[0].points;
  const values = points.map((point) => point.y);
  const low = Math.min(...values) - 1;
  const high = Math.max(...values) + 1;
  const x = (index: number) => 38 + index * (444 / Math.max(1, points.length - 1));
  const y = (value: number) => 148 - ((value - low) / Math.max(1, high - low)) * 112;
  const path = points.map((point, index) => `${x(index)},${y(point.y)}`).join(" ");
  return (
    <figure className="answer-chart">
      <figcaption id="answer-chart-caption"><strong>{chart.title}</strong><span>{chart.y_label}</span></figcaption>
      <svg viewBox="0 0 520 190" role="img" aria-label={`${chart.title}: ${points.map((point) => `${point.x} ${point.y}`).join(", ")}`}>
        <path d="M38 18 V148 H496" className="chart-axis" />
        <polyline points={path} className="chart-line" />
        {points.map((point, index) => (
          <g key={point.x}>
            <circle cx={x(index)} cy={y(point.y)} r="6" />
            <text x={x(index)} y={y(point.y) - 13} textAnchor="middle">${point.y.toFixed(1)}M</text>
            <text x={x(index)} y="172" textAnchor="middle">{point.x.replace("2025-", "")}</text>
          </g>
        ))}
      </svg>
      <div className="answer-chart__meta"><p><strong>TAKEAWAY</strong>{chart.takeaway}</p><p><strong>SOURCE</strong>{chart.source}</p></div>
      <div className="visually-hidden">
        <table>
          <caption>{chart.title}. {chart.takeaway}. Source: {chart.source}.</caption>
          <thead><tr><th scope="col">{chart.x_label}</th><th scope="col">{chart.y_label}</th></tr></thead>
          <tbody>{points.map((point) => <tr key={point.x}><th scope="row">{point.x}</th><td>{point.y}</td></tr>)}</tbody>
        </table>
      </div>
    </figure>
  );
}

function TraceTimeline({ events, selectedEventId, onSelect }: {
  events: AgentEvent[];
  selectedEventId: string | null;
  onSelect: (event: AgentEvent) => void;
}) {
  const moveFocus = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const keys = ["ArrowDown", "ArrowUp", "Home", "End"];
    if (!keys.includes(event.key)) return;
    event.preventDefault();
    const buttons = Array.from(
      event.currentTarget.closest(".trace-timeline")
        ?.querySelectorAll<HTMLButtonElement>(".trace-event") ?? []
    );
    const next = event.key === "Home"
      ? 0
      : event.key === "End"
        ? buttons.length - 1
        : event.key === "ArrowDown"
          ? Math.min(buttons.length - 1, index + 1)
          : Math.max(0, index - 1);
    buttons[next]?.focus();
  };
  return (
    <ol className="trace-timeline" aria-label="Canonical event timeline">
      {events.map((runtimeEvent, index) => (
        <li key={runtimeEvent.event_id}>
          <button
            aria-label={`Event ${runtimeEvent.sequence}, ${runtimeEvent.type}, ${runtimeEvent.node_id ?? "run"}`}
            aria-pressed={selectedEventId === runtimeEvent.event_id}
            className={`trace-event trace-event--${runtimeEvent.type.split(".")[0]}`}
            onClick={() => onSelect(runtimeEvent)}
            onKeyDown={(event) => moveFocus(event, index)}
            tabIndex={selectedEventId === runtimeEvent.event_id || (!selectedEventId && index === events.length - 1) ? 0 : -1}
            type="button"
          >
            <span>{String(runtimeEvent.sequence).padStart(3, "0")}</span>
            <i aria-hidden="true" />
            <strong>{runtimeEvent.type}</strong>
            <small>{runtimeEvent.node_id ?? "run"}</small>
            <time dateTime={runtimeEvent.timestamp}>{new Date(runtimeEvent.timestamp).toISOString().slice(11, 19)}</time>
          </button>
        </li>
      ))}
    </ol>
  );
}

export function LabPage({ onNavigate }: { onNavigate: (path: string) => void }) {
  const initialTab = new URLSearchParams(window.location.search).get("inspector") ?? "state";
  const [inspectorTab, setInspectorTab] = useState(["state", "schema", "tools"].includes(initialTab) ? initialTab : "state");
  const [inspectorSize, setInspectorSize] = useState(360);
  const [question, setQuestion] = useState(defaultQuestion);
  const [pace, setPace] = useState<"fast" | "observe">("observe");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const runtime = useLabRuntime();
  const snapshot = runtime.snapshot;
  const latestResetEventId = snapshot?.events.findLast((event) => event.type === "run.reset")?.event_id ?? null;

  useEffect(() => {
    if (snapshot?.run.current_node) setSelectedId(snapshot.run.current_node);
    else if (snapshot && !selectedId) setSelectedId(snapshot.run.nodes[0]?.id ?? null);
  }, [selectedId, snapshot]);

  useEffect(() => {
    if (selectedEventId && snapshot && !snapshot.events.some((event) => event.event_id === selectedEventId)) {
      setSelectedEventId(null);
    }
  }, [selectedEventId, snapshot]);

  useEffect(() => {
    setSelectedEventId(null);
  }, [latestResetEventId]);

  const selectedNode = snapshot?.run.nodes.find((node) => node.id === selectedId) ?? null;
  const toolEvents = useMemo(
    () => snapshot ? eventsInCurrentRunCycle(snapshot).filter((event) => event.type.startsWith("tool.")) : [],
    [snapshot]
  );
  const isBusy = runtime.pending !== null || snapshot?.run.status === "running";
  const status = snapshot?.run.status ?? "unloaded";
  const statusTone = status === "completed" ? "success" : status === "running" ? "active" : status === "error" ? "error" : status === "unloaded" ? "warning" : "neutral";
  const changeTab = (id: string) => {
    setInspectorTab(id);
    const url = new URL(window.location.href);
    url.searchParams.set("inspector", id);
    window.history.replaceState({}, "", url);
  };

  const inspectorItems = useMemo(() => [
    {
      id: "state",
      label: "STATE",
      content: snapshot
        ? <StateView snapshot={snapshot} selectedNode={selectedNode} />
        : <EmptyState title="No state yet" description="Load the case to create the first canonical run event." />
    },
    {
      id: "schema",
      label: "SCHEMA",
      content: snapshot ? <dl className="contract-readout">
        <div><dt>EVENT</dt><dd>agent-event@1.0.0</dd></div>
        <div><dt>SCENARIO</dt><dd>{snapshot.run.scenario_id}/{snapshot.run.case_id}</dd></div>
        <div><dt>PROVIDER</dt><dd>mock-offline · seed {snapshot.run.seed}</dd></div>
        <div><dt>PATCH PATHS</dt><dd>values.* | answer</dd></div>
      </dl> : <EmptyState title="Schema boundary ready" description="The case will validate LabRunState, AgentEvent, typed node output and lab chart DSL." />
    },
    {
      id: "tools",
      label: "TOOLS",
      content: toolEvents.length ? <ul className="tool-audit">{toolEvents.map((event) => <li key={event.event_id}><Badge tone={event.type === "tool.completed" ? "success" : event.type === "tool.failed" ? "error" : "warning"}>{event.type}</Badge><span>{String(event.payload.tool)}</span><small>{String(event.payload.requested_permission ?? event.payload.permission_decision ?? event.payload.audit ?? event.payload.error_code)}</small></li>)}</ul> : <EmptyState title="No tool calls yet" description="Query metric will cross the registry, validation, permission, timeout and audit path." />
    }
  ], [selectedNode, snapshot, toolEvents]);

  return (
    <div className="page lab-page">
      <header className="workspace-header">
        <div><span className="machine-label">VISUAL LAB // DATA ANALYST 01</span><h1>Runtime X-ray</h1><p>Advance one typed super-step at a time, then inspect the exact event-backed state change.</p></div>
        <div className="lab-header-actions"><Badge tone={statusTone}>{status.toUpperCase()}</Badge><Button variant="ghost" onClick={() => onNavigate("/lab/structured-output")}>Structured output lab</Button><Button variant="ghost" onClick={() => onNavigate("/lab/tool-calling")}>Tool calling lab</Button><Button variant="ghost" onClick={() => onNavigate("/lab/durable-runtime")}>Durable runtime</Button><Button variant="ghost" onClick={() => onNavigate("/lab/evidence-memory")}>Evidence + memory</Button><Button variant="ghost" onClick={() => onNavigate("/lab/human-approval")}>Human approval</Button><Button variant="ghost" onClick={() => onNavigate("/lab/security")}>Security range</Button></div>
      </header>

      <section className="scenario-loader" aria-label="Scenario input">
        <Input label="ANALYST QUESTION" value={question} onChange={(event) => setQuestion(event.target.value)} disabled={isBusy} />
        <Button onClick={() => void runtime.load(question)} loading={runtime.pending === "load"} disabled={isBusy || question.trim().length < 3}>{snapshot ? "New run" : "Load case"}</Button>
      </section>

      <div className="lab-commandbar">
        <div><span className="machine-label">SCENARIO</span><strong>Quarterly revenue pulse</strong><small>MOCK · SEED 2026 · READ ONLY</small></div>
        <div className="lab-commandbar__steps" aria-label="Learning loop">
          <span className={!snapshot || snapshot.run.status === "idle" ? "is-active" : ""}>OBSERVE</span>
          <span className={snapshot?.run.status === "running" ? "is-active" : ""}>TRACE</span>
          <span className={snapshot?.run.status === "running" ? "is-active" : ""}>STEP</span>
          <span className={snapshot?.run.status === "completed" ? "is-active" : ""}>EXPLAIN</span>
        </div>
        <div className="lab-commandbar__actions">
          <Select label="PACE" value={pace} onChange={(event) => setPace(event.target.value as "fast" | "observe")} disabled={isBusy}>
            <option value="observe">Observe</option><option value="fast">Fast</option>
          </Select>
          <Button onClick={() => void runtime.step(pace)} loading={runtime.pending === "step"} disabled={!snapshot || isBusy || snapshot.run.status === "completed" || snapshot.run.status === "error"}>Step</Button>
          <Button variant="primary" onClick={() => void runtime.run(pace)} loading={runtime.pending === "run"} disabled={!snapshot || isBusy || snapshot.run.status === "completed" || snapshot.run.status === "error"}>Run</Button>
          <Button variant="danger" onClick={() => void runtime.reset()} loading={runtime.pending === "reset"} disabled={!snapshot || isBusy || (snapshot.run.step_count === 0 && snapshot.run.status !== "error")}>Reset</Button>
        </div>
      </div>

      {runtime.error && <div className="runtime-error" role="alert">
        <strong>RUNTIME BOUNDARY ERROR</strong>
        <div>
          <span>The command did not replace the last validated snapshot.</span>
          <small>Retry the command. If this run entered ERROR, use the audited Reset control.</small>
          <code>DETAIL: {runtime.error}</code>
        </div>
      </div>}

      <SplitPane
        primary={
          <Panel title="Graph canvas" context="CANONICAL EVENT PROJECTION" className="graph-panel" actions={<Badge tone={statusTone}>{snapshot ? `${snapshot.run.step_count}/${snapshot.run.nodes.length} STEPS` : "NOT STARTED"}</Badge>}>
            {snapshot
              ? <GraphCanvas snapshot={snapshot} selectedId={selectedId} onSelect={setSelectedId} />
              : <div className="graph-empty-grid"><EmptyState title="Case ready, no simulated state" description="Load the case to persist run.started. Node status will change only when canonical events arrive." action={<Button onClick={() => void runtime.load(question)}>Load data analyst case</Button>} /></div>}
          </Panel>
        }
        secondary={<Panel title="State Inspector" context="EVENT-SYNCED" actions={<Button onClick={() => setInspectorSize(360)}>Reset layout</Button>}><Tabs label="Inspector views" items={inspectorItems} selectedId={inspectorTab} onChange={changeTab} /></Panel>}
        secondarySize={inspectorSize}
        onSecondarySizeChange={setInspectorSize}
      />

      {snapshot?.run.answer && <Panel title={snapshot.run.answer.title} context="VALIDATED ANSWER // SAFE CHART DSL" className="answer-panel" actions={<Badge tone="success">ANSWER READY</Badge>}>
        <div className="answer-layout"><div><p className="answer-summary">{snapshot.run.answer.summary}</p><ul>{snapshot.run.answer.bullets.map((bullet) => <li key={bullet}>{bullet}</li>)}</ul></div><SafeLineChart chart={snapshot.run.answer.chart} /></div>
      </Panel>}

      <Panel title="Trace timeline" context={`${snapshot?.events.length ?? 0} EVENTS // APPEND ONLY`} tone="console" className="trace-panel">
        {snapshot?.events.length ? <TraceTimeline events={snapshot.events} selectedEventId={selectedEventId} onSelect={(event) => {
          setSelectedEventId(event.event_id);
          if (event.node_id) {
            setSelectedId(event.node_id);
            changeTab("state");
          }
        }} /> : <div className="trace-empty-message"><span>GRAPH</span><i /><span>PROVIDER</span><i /><span>TOOL</span><i /><span>POLICY</span><p>No events have been persisted.</p></div>}
      </Panel>
    </div>
  );
}
