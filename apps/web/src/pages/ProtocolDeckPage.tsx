import { useEffect, useRef, useState, type ReactNode } from "react";

import {
  loadProtocolCatalog,
  probeMcpReleaseCandidate,
  runA2ALab,
  runAgUiLab,
  runDecisionGate,
  runMcpLab,
  type A2ARun,
  type AgUiRun,
  type DecisionGateRequest,
  type DecisionGateResult,
  type McpRun,
  type ProtocolCatalog,
  type VersionMismatch
} from "../protocol-api";
import { Badge, Button, EmptyState, ErrorState, Panel, Tabs } from "../ui/primitives";

type ProtocolTab = "mcp" | "a2a" | "ag-ui" | "otel";
type BusyAction = "mcp" | "a2a" | "ag-ui" | "decision" | "version" | null;

const defaultDecision: Omit<DecisionGateRequest, "approved" | "idempotency_key"> = {
  independent_owner: false,
  independent_deployment: false,
  capability_isolation: false,
  asynchronous_task: false,
  replaceable_remote: false,
  coordination_eval_cost_acceptable: false
};

const remoteDecision: Omit<DecisionGateRequest, "approved" | "idempotency_key"> = {
  independent_owner: true,
  independent_deployment: true,
  capability_isolation: true,
  asynchronous_task: true,
  replaceable_remote: true,
  coordination_eval_cost_acceptable: true
};

function JsonBlock({ children, label }: { children: unknown; label: string }) {
  return <pre className="protocol-json" aria-label={label} tabIndex={0}>{JSON.stringify(children, null, 2)}</pre>;
}

function EvidenceBoundary({ children }: { children: ReactNode }) {
  return <div className="protocol-boundary"><span>CANONICAL BOUNDARY</span><i aria-hidden="true" /><strong>{children}</strong></div>;
}

function McpLab({ run, busy, disabled, available, onRun }: { run: McpRun | null; busy: boolean; disabled: boolean; available: boolean; onRun: () => void }) {
  const [selectedSequence, setSelectedSequence] = useState(1);
  const selectedExchange = run?.exchanges.find((exchange) => exchange.sequence === selectedSequence) ?? run?.exchanges[0];
  return (
    <div className="protocol-lab-grid">
      <section className="protocol-lab-copy">
        <span className="machine-label">LOCAL STDIO // STABLE 2025-11-25</span>
        <h2>MCP capability workbench</h2>
        <p>{available ? "A bounded local server advertises two callable read-only tools, one resource and one prompt. The client negotiates the stable version, then the selected tool proposal still crosses AGENT//TRACE permission, schema, timeout and audit controls." : "The stable MCP boundary and typed transcript shape remain inspectable here. Public safe-lab policy prevents this browser session from spawning the repository-owned stdio process."}</p>
        <div className="protocol-actions">
          <Button variant="primary" loading={busy} disabled={disabled} onClick={onRun}>Run local MCP exchange</Button>
          <Badge tone="success">REMOTE AUTO-CONNECT OFF</Badge>
        </div>
        {!run ? <EmptyState title={available ? "No MCP process launched" : "MCP process execution disabled"} description={available ? "Run the exchange to spawn the repository-owned stdio server and inspect its real protocol transcript." : "Use a local AGENT//TRACE installation to run the bounded stdio exchange; public preview keeps this process boundary closed."} /> : <dl className="protocol-facts">
          <div><dt>TRANSPORT</dt><dd>{run.transport.toUpperCase()}</dd></div>
          <div><dt>VERSION</dt><dd>{run.negotiated_version}</dd></div>
          <div><dt>PROCESS</dt><dd>{run.process_cleaned ? "CLEANED" : "ACTIVE"}</dd></div>
          <div><dt>AUDIT STAGES</dt><dd>{run.canonical_tool_audit.length}</dd></div>
        </dl>}
      </section>
      <section className="protocol-transcript" aria-label="MCP transcript">
        {run ? <>
          <ol className="protocol-exchanges">
            {run.exchanges.map((exchange) => <li key={`${exchange.sequence}-${exchange.method}`} className={exchange.direction === "client_to_server" ? "is-outbound" : "is-inbound"}><span>{String(exchange.sequence).padStart(2, "0")}</span><strong>{exchange.method}</strong><small>{exchange.direction === "client_to_server" ? "CLIENT → SERVER" : "SERVER → CLIENT"}</small></li>)}
          </ol>
          <div className="protocol-raw-inspector"><label>RAW MCP MESSAGE<select aria-label="Select raw MCP message" value={selectedExchange?.sequence ?? 1} onChange={(event) => setSelectedSequence(Number(event.target.value))}>{run.exchanges.map((exchange) => <option key={exchange.sequence} value={exchange.sequence}>{String(exchange.sequence).padStart(2, "0")} · {exchange.method} · {exchange.direction}</option>)}</select></label><JsonBlock label="Selected raw MCP protocol message">{selectedExchange?.payload}</JsonBlock></div>
          <EvidenceBoundary>TOOL REQUEST → TOOL RESULT → EVIDENCE → EVENTS</EvidenceBoundary>
          <div className="protocol-object-grid"><JsonBlock label="Canonical tool result">{run.canonical_tool_result}</JsonBlock><JsonBlock label="Canonical event tail">{run.canonical_events.slice(-3)}</JsonBlock></div>
        </> : <div className="protocol-terminal-empty"><span>stdio://waiting</span><p>No process, network request or synthetic transcript is active.</p></div>}
      </section>
    </div>
  );
}

function A2ALab({ run, busy, disabled, onRun }: { run: A2ARun | null; busy: boolean; disabled: boolean; onRun: () => void }) {
  const [selectedSequence, setSelectedSequence] = useState(1);
  const selectedExchange = run?.exchanges.find((exchange) => exchange.sequence === selectedSequence) ?? run?.exchanges[0];
  return (
    <div className="protocol-lab-grid">
      <section className="protocol-lab-copy">
        <span className="machine-label">TRUSTED LOCAL ADAPTER // A2A 1.0</span>
        <h2>Delegate across a real boundary</h2>
        <p>The research capability has an independent owner and a replaceable artifact contract. Its card exposes capability metadata—not memory, internal tools or hidden reasoning.</p>
        <div className="protocol-actions"><Button variant="primary" loading={busy} disabled={disabled} onClick={onRun}>Delegate research task</Button><Badge tone="model">HIGH COORDINATION COST</Badge></div>
        {run ? <div className="agent-card"><span>AGENT CARD</span><h3>{run.agent_card.name}</h3><p>{run.agent_card.description}</p><strong>{run.agent_card.supportedInterfaces[0]?.protocolBinding} · {run.agent_card.supportedInterfaces[0]?.protocolVersion} · {run.agent_card.skills[0]?.id}</strong></div> : <EmptyState title="No delegated task" description="Start the local task to observe submitted, working and completed lifecycle states." />}
      </section>
      <section className="protocol-transcript" aria-label="A2A task and artifact">
        {run ? <>
          <ol className="a2a-state-line" aria-label="A2A task states">{run.task_updates.map((state, index) => <li key={state.state}><span>{index + 1}</span><strong>{state.state.replace("TASK_STATE_", "").toLowerCase()}</strong></li>)}</ol>
          <div className="protocol-raw-inspector"><label>RAW A2A MESSAGE<select aria-label="Select raw A2A message" value={selectedExchange?.sequence ?? 1} onChange={(event) => setSelectedSequence(Number(event.target.value))}>{run.exchanges.map((exchange) => <option key={exchange.sequence} value={exchange.sequence}>{String(exchange.sequence).padStart(2, "0")} · {exchange.method} · {exchange.direction}</option>)}</select></label><JsonBlock label="Selected raw A2A protocol message">{selectedExchange?.payload}</JsonBlock></div>
          <article className="protocol-artifact"><span>REPLACEABLE ARTIFACT</span><h3>{run.task.artifacts[0]?.name}</h3><p>{run.task.artifacts[0]?.description}</p><JsonBlock label="A2A artifact payload">{run.task.artifacts[0]?.parts[0]?.data}</JsonBlock></article>
          <EvidenceBoundary>ARTIFACT → EVIDENCE; PRIVATE RUNTIME STAYS REMOTE</EvidenceBoundary>
          <p className="protocol-isolation-proof"><strong>Isolation proof.</strong> {run.isolation_reason}</p>
        </> : <div className="protocol-terminal-empty"><span>task://unassigned</span><p>The UI never receives a remote agent memory or tool registry.</p></div>}
      </section>
    </div>
  );
}

function AgUiLab({ run, busy, disabled, onRun }: { run: AgUiRun | null; busy: boolean; disabled: boolean; onRun: () => void }) {
  return (
    <div className="protocol-lab-grid">
      <section className="protocol-lab-copy">
        <span className="machine-label">SUPPORTED SUBSET // EVOLVING</span>
        <h2>Project runtime facts into UI events</h2>
        <p>The adapter maps seven explicit event types in both directions. AG-UI remains a projection: canonical state and persistence stay owned by the repository runtime.</p>
        <div className="protocol-actions"><Button variant="primary" loading={busy} disabled={disabled} onClick={onRun}>Map canonical event stream</Button><Badge tone="warning">PROFILE 0.1</Badge></div>
        {run ? <dl className="protocol-facts"><div><dt>OWNER</dt><dd>{run.canonical_state_owner}</dd></div><div><dt>EVENTS</dt><dd>{run.mappings.length} REVERSIBLE</dd></div><div><dt>UNSUPPORTED</dt><dd>{run.unsupported_events.length} REJECTED</dd></div></dl> : <EmptyState title="No adapter run" description="Run the mapper to compare canonical and AG-UI event identities." />}
      </section>
      <section className="protocol-transcript" aria-label="AG-UI mapping table">
        {run ? <div aria-label="Scrollable AG-UI event mapping" className="protocol-table-wrap" tabIndex={0}><table className="protocol-map-table"><caption>Canonical events mapped to the supported AG-UI subset</caption><thead><tr><th>SEQ</th><th>CANONICAL</th><th>AG-UI</th><th>ROUNDTRIP</th></tr></thead><tbody>{run.mappings.map((mapping, index) => <tr key={mapping.canonical_event_id}><td>{String(index + 1).padStart(2, "0")}</td><td>{mapping.canonical_type}</td><td>{mapping.ag_ui_type}</td><td>{mapping.reversible ? "YES" : "NO"}</td></tr>)}</tbody></table><div className="protocol-rejected"><strong>REJECTED OUTSIDE PROFILE</strong><span>{run.unsupported_events.join(" · ")}</span></div></div> : <div className="protocol-terminal-empty"><span>adapter://idle</span><p>Unsupported event families fail closed; they are not silently coerced.</p></div>}
      </section>
    </div>
  );
}

function OtelLab({ catalog }: { catalog: ProtocolCatalog }) {
  return (
    <div className="protocol-lab-grid">
      <section className="protocol-lab-copy">
        <span className="machine-label">SAFE SEMANTIC PROJECTION // EXPORT OFF</span>
        <h2>Correlate without leaking state</h2>
        <p>OpenTelemetry receives bounded references and operational attributes. It does not become product state, evaluation truth or a channel for inputs, credentials and hidden reasoning.</p>
        <dl className="protocol-facts">
          <div><dt>TRACE ROOT</dt><dd>{catalog.otel_policy.trace}</dd></div>
          <div><dt>CORRELATION</dt><dd>{catalog.otel_policy.correlation}</dd></div>
          <div><dt>SAMPLING</dt><dd>{catalog.otel_policy.sampling}</dd></div>
          <div><dt>REDACTION</dt><dd>{catalog.otel_policy.redaction}</dd></div>
          <div><dt>EXPORTER</dt><dd>{catalog.otel_exporter_enabled ? "ON" : "OFF / LOCAL"}</dd></div>
          <div><dt>MAPPINGS</dt><dd>{catalog.otel_mappings.length} SPAN / EVENT RULES</dd></div>
        </dl>
      </section>
      <section className="otel-board" aria-label="OpenTelemetry mapping visualization">{catalog.otel_mappings.map((mapping, index) => <article key={mapping.canonical_event}><span>{String(index + 1).padStart(2, "0")}</span><div><strong>{mapping.canonical_event}</strong><i aria-hidden="true">→</i><b>{mapping.otel_projection}</b><small>SAFE · {mapping.safe_attributes.join(" · ")}</small><em>EXCLUDED · {mapping.excluded.join(" · ")}</em></div></article>)}</section>
    </div>
  );
}

function ArchitectureLadder({ catalog }: { catalog: ProtocolCatalog }) {
  const labels = {
    single_graph: "Single graph",
    subgraph: "Subgraph",
    handoff: "Handoff",
    remote_agent: "Remote agent"
  } as const;
  return (
    <Panel title="Architecture boundary ladder" context="ONE GRAPH ≠ ONE AGENT PER NODE" className="architecture-panel">
      <ol className="architecture-ladder" aria-label="Four architecture boundary choices">
        {catalog.architecture_options.map((item, index) => <li key={item.option} className={`is-${item.coordination_cost}`}>
          <span>{String(index + 1).padStart(2, "0")}</span>
          <div><h3>{labels[item.option]}</h3><p>{item.boundary}</p><small>{item.use_when}</small></div>
          <Badge tone={item.coordination_cost === "high" ? "warning" : item.coordination_cost === "medium" ? "model" : "success"}>{item.coordination_cost.toUpperCase()} COST</Badge>
        </li>)}
      </ol>
    </Panel>
  );
}

function DecisionGate({ busy, disabled, result, value, onChange, onEvaluate }: { busy: boolean; disabled: boolean; result: DecisionGateResult | null; value: typeof defaultDecision; onChange: (value: typeof defaultDecision) => void; onEvaluate: () => void }) {
  const choices: Array<[keyof typeof defaultDecision, string, string]> = [
    ["independent_owner", "Independent owner", "Another team controls the capability"],
    ["independent_deployment", "Independent deployment", "It releases and fails separately"],
    ["capability_isolation", "Capability isolation", "Only a bounded contract crosses"],
    ["asynchronous_task", "Asynchronous task", "A task can outlive the caller"],
    ["replaceable_remote", "Replaceable remote", "The implementation can be swapped"],
    ["coordination_eval_cost_acceptable", "Coordination and evaluation cost", "The high operational tax is justified"]
  ];
  return (
    <Panel title="Multi-agent decision gate" context="ARCHITECTURE BEFORE ORCHESTRATION" className="decision-panel" actions={<div className="protocol-presets"><Button variant="ghost" disabled={disabled} onClick={() => onChange(defaultDecision)}>Single graph preset</Button><Button variant="ghost" disabled={disabled} onClick={() => onChange(remoteDecision)}>Remote boundary preset</Button></div>}>
      <div className="decision-grid">
        <fieldset disabled={disabled}><legend>Which boundaries are actually true?</legend>{choices.map(([key, label, description]) => <label key={key}><input type="checkbox" checked={value[key]} onChange={(event) => onChange({ ...value, [key]: event.target.checked })} /><span><strong>{label}</strong><small>{description}</small></span></label>)}</fieldset>
        <div className="decision-output">
          <Button variant="primary" loading={busy} disabled={disabled} onClick={onEvaluate}>Evaluate architecture</Button>
          {result ? <article aria-live="polite"><span>RECOMMENDATION</span><h3>{result.recommendation.replaceAll("_", " ")}</h3><Badge tone={result.coordination_cost === "high" ? "warning" : "success"}>{result.coordination_cost.toUpperCase()} COST · {result.score}/6</Badge><ul>{result.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul><p>{result.warning}</p></article> : <EmptyState title="No architecture verdict" description="A remote agent is justified only by a real ownership, deployment and capability boundary—not by the number of graph nodes." />}
        </div>
      </div>
    </Panel>
  );
}

function selectedProtocol(path: string): ProtocolTab {
  const candidate = path.split("/")[2];
  return (["mcp", "a2a", "ag-ui", "otel"] as ProtocolTab[]).includes(candidate as ProtocolTab) ? candidate as ProtocolTab : "mcp";
}

export function ProtocolDeckPage({ path, onNavigate, safeLabMode = false }: { path: string; onNavigate: (path: string) => void; safeLabMode?: boolean }) {
  const [catalog, setCatalog] = useState<ProtocolCatalog | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState<BusyAction>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [mcpRun, setMcpRun] = useState<McpRun | null>(null);
  const [a2aRun, setA2ARun] = useState<A2ARun | null>(null);
  const [aguiRun, setAguiRun] = useState<AgUiRun | null>(null);
  const [decision, setDecision] = useState(defaultDecision);
  const [decisionResult, setDecisionResult] = useState<DecisionGateResult | null>(null);
  const [versionMismatch, setVersionMismatch] = useState<VersionMismatch | null>(null);
  const actionLocked = useRef(false);

  useEffect(() => {
    let active = true;
    setCatalog(null);
    setCatalogError(null);
    void loadProtocolCatalog().then((next) => { if (active) setCatalog(next); }).catch((reason: unknown) => {
      if (active && !(reason instanceof DOMException && reason.name === "AbortError")) setCatalogError(reason instanceof Error ? reason.message : "Protocol catalog failed");
    });
    return () => { active = false; };
  }, [revision]);

  const execute = async <T,>(action: Exclude<BusyAction, null>, operation: () => Promise<T>, commit: (result: T) => void) => {
    if (actionLocked.current) return;
    actionLocked.current = true;
    setBusy(action);
    setActionError(null);
    try { commit(await operation()); }
    catch (reason) { setActionError(reason instanceof Error ? reason.message : "Protocol action failed"); }
    finally { actionLocked.current = false; setBusy(null); }
  };

  if (!catalog && !catalogError) return <div className="page protocol-page"><div className="protocol-loading" role="status"><span>MCP</span><i /><span>A2A</span><i /><span>AG-UI</span><i /><span>OTEL</span><p>Loading typed protocol boundaries…</p></div></div>;
  if (!catalog && catalogError) return <div className="page protocol-page"><ErrorState title="Protocol labs unavailable" description={catalogError} action={<Button variant="primary" onClick={() => setRevision((value) => value + 1)}>Retry protocol catalog</Button>} /></div>;
  if (!catalog) return null;

  const tab = selectedProtocol(path);
  const mcpEnabled = catalog.descriptors.find((descriptor) => descriptor.protocol === "mcp")?.enabled ?? !safeLabMode;
  return (
    <div className="page protocol-page">
      <header className="workspace-header protocol-header"><div><span className="machine-label">M10 // PROTOCOL BOUNDARY DECK</span><h1>Draw the boundary.<br /><span>Then choose the protocol.</span></h1><p>MCP connects capabilities. A2A delegates owned tasks. AG-UI projects runtime events. OpenTelemetry correlates operations. None of them replaces authorization or canonical application state.</p></div><div className="protocol-header__status"><Badge tone={safeLabMode ? "warning" : "success"}>{safeLabMode ? "PUBLIC SAFE LAB" : "LOCAL LABS ON"}</Badge><Badge>REMOTE CONNECT OFF</Badge><Badge>OTEL EXPORT OFF</Badge></div></header>

      {!mcpEnabled && <p className="builder-notice" role="status">Process-backed MCP execution is disabled in public preview. Its typed boundary documentation remains available; A2A, AG-UI and decision exercises stay deterministic and local.</p>}

      {actionError && <div className="protocol-inline-error" role="alert"><strong>BOUNDARY REJECTED</strong><span>{actionError}</span><Button variant="ghost" onClick={() => setActionError(null)}>Dismiss</Button></div>}

      <section className="protocol-card-grid" aria-label="Protocol comparison cards">{catalog.descriptors.map((descriptor, index) => <article key={descriptor.protocol} className={`protocol-summary is-${descriptor.protocol}`}><header><span>{String(index + 1).padStart(2, "0")}</span><Badge tone={descriptor.maturity === "stable" ? "success" : "warning"}>{descriptor.maturity.toUpperCase()}</Badge></header><h2>{descriptor.protocol.toUpperCase()}</h2><strong>{descriptor.boundary}</strong><p>{descriptor.solves}</p><div><b>DOES NOT SOLVE</b><span>{descriptor.does_not_solve}</span></div><small>{descriptor.stable_version}</small></article>)}</section>

      <ArchitectureLadder catalog={catalog} />

      <Panel title="Protocol lab" context="RAW MESSAGES → REPOSITORY CONTRACTS" className="protocol-deck-panel">
        <Tabs label="Protocol labs" selectedId={tab} onChange={(id) => onNavigate(`/protocols/${id}`)} items={[
          { id: "mcp", label: "MCP", content: <McpLab run={mcpRun} busy={busy === "mcp"} disabled={busy !== null || !mcpEnabled} available={mcpEnabled} onRun={() => void execute("mcp", runMcpLab, setMcpRun)} /> },
          { id: "a2a", label: "A2A", content: <A2ALab run={a2aRun} busy={busy === "a2a"} disabled={busy !== null} onRun={() => void execute("a2a", runA2ALab, setA2ARun)} /> },
          { id: "ag-ui", label: "AG-UI", content: <AgUiLab run={aguiRun} busy={busy === "ag-ui"} disabled={busy !== null} onRun={() => void execute("ag-ui", runAgUiLab, setAguiRun)} /> },
          { id: "otel", label: "OTEL", content: <OtelLab catalog={catalog} /> }
        ]} />
      </Panel>

      <section className="version-gate" aria-label="Experimental protocol isolation"><div><span className="machine-label">EXPERIMENTAL AIRLOCK</span><h2>Newer is not silently compatible.</h2><p>The 2026-07-28 MCP release candidate stays isolated from the stable lab until its contract is deliberately adopted.</p></div><Button loading={busy === "version"} disabled={busy !== null} onClick={() => void execute("version", probeMcpReleaseCandidate, setVersionMismatch)}>Probe MCP release candidate</Button>{versionMismatch && <article role="status"><strong>{versionMismatch.code}</strong><span>requested {versionMismatch.requested_version}</span><i aria-hidden="true">≠</i><span>supported {versionMismatch.supported_version}</span></article>}</section>

      <DecisionGate busy={busy === "decision"} disabled={busy !== null} result={decisionResult} value={decision} onChange={setDecision} onEvaluate={() => void execute("decision", () => runDecisionGate(decision), setDecisionResult)} />

      <Panel title="Boundary comparison" context="WHAT THE PROTOCOL CANNOT OWN" className="protocol-comparison-panel"><div aria-label="Scrollable protocol boundary comparison" className="protocol-table-wrap" tabIndex={0}><table className="protocol-comparison"><caption>Protocol responsibilities across ten required dimensions</caption><thead><tr><th>DIMENSION</th><th>MCP</th><th>A2A</th><th>AG-UI</th><th>OTEL</th></tr></thead><tbody>{catalog.comparison.map((row) => <tr key={row.dimension}><th>{row.dimension}</th><td>{row.mcp}</td><td>{row.a2a}</td><td>{row.ag_ui}</td><td>{row.otel}</td></tr>)}</tbody></table></div></Panel>
    </div>
  );
}
