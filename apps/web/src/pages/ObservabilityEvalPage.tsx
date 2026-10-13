import { useEffect, useMemo, useState, type CSSProperties } from "react";

import {
  createRunComparison,
  exportEvalRun,
  loadEvalCatalog,
  runEvalBatch,
  type EvalBatch,
  type EvalCatalog,
  type EvalRun,
  type ExportBundle,
  type RunComparison,
  type TraceEvent
} from "../observability-api";
import { Badge, Button, EmptyState, ErrorState, Panel, Select, Tabs } from "../ui/primitives";

function download(filename: string, content: string, mediaType: string) {
  const url = URL.createObjectURL(new Blob([content], { type: mediaType }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function formatValue(value: unknown) {
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function MetricRail({ batch }: { batch: EvalBatch }) {
  const { dashboard } = batch;
  return (
    <section className="eval-metric-rail" aria-label="Evaluation metrics">
      <div className="eval-score"><span>GOLDEN PASS RATE</span><strong>{Math.round(dashboard.pass_rate * 100)}<small>%</small></strong><i style={{ "--score": `${dashboard.pass_rate * 100}%` } as CSSProperties} /></div>
      <dl>
        <div><dt>PASSED</dt><dd>{dashboard.passed}/{dashboard.total}</dd></div>
        <div><dt>ROUTE ACCURACY</dt><dd>{Math.round(dashboard.route_accuracy * 100)}%</dd></div>
        <div><dt>TOOL SELECTION</dt><dd>{Math.round(dashboard.tool_selection_accuracy * 100)}%</dd></div>
        <div><dt>ARGUMENT ACCURACY</dt><dd>{Math.round(dashboard.argument_accuracy * 100)}%</dd></div>
        <div><dt>CITATION COVERAGE</dt><dd>{Math.round(dashboard.citation_coverage * 100)}%</dd></div>
        <div><dt>UNSAFE ACTIONS</dt><dd className={dashboard.unsafe_action_count === 0 ? "is-good" : "is-bad"}>{dashboard.unsafe_action_count}</dd></div>
        <div><dt>CRITICAL SECURITY</dt><dd className={dashboard.security_gate === "passed" ? "is-good" : "is-bad"}>{dashboard.security_gate.toUpperCase()}</dd></div>
        <div><dt>P50 / P95</dt><dd>{dashboard.p50_duration_ms} / {dashboard.p95_duration_ms} ms</dd></div>
        <div><dt>ACTUAL BATCH</dt><dd>{batch.elapsed_ms} ms</dd></div>
        <div><dt>BASELINE</dt><dd>{Math.round(dashboard.baseline_pass_rate * 100)}% · {dashboard.baseline_delta >= 0 ? "+" : ""}{(dashboard.baseline_delta * 100).toFixed(1)} pt<small>{dashboard.baseline_id}</small></dd></div>
      </dl>
    </section>
  );
}

function ScenarioMatrix({ batch }: { batch: EvalBatch }) {
  return (
    <ol className="eval-scenario-matrix" aria-label="Golden cases by scenario">
      {batch.dashboard.scenarios.map((scenario, index) => (
        <li key={scenario.scenario_id}>
          <span>{String(index + 1).padStart(2, "0")}</span>
          <div><strong>{scenario.scenario_id.replaceAll("-", " ")}</strong><i><b style={{ width: `${scenario.pass_rate * 100}%` }} /></i></div>
          <em>{scenario.passed}/{scenario.total}</em>
        </li>
      ))}
    </ol>
  );
}

function Waterfall({ run, onSelect }: { run: EvalRun; onSelect: (spanId: string) => void }) {
  const duration = Math.max(...run.spans.map((span) => span.start_ms + span.duration_ms));
  return (
    <ol className="eval-waterfall" aria-label="Trace waterfall">
      {run.spans.map((span, index) => (
        <li key={span.span_id}>
          <button onClick={() => onSelect(span.span_id)} title={`Inspect ${span.name} span`}>
            <span>{String(index + 1).padStart(2, "0")} · {span.kind}</span>
            <strong>{span.name}</strong>
            <i className={`is-${span.status}`} style={{ marginLeft: `${(span.start_ms / duration) * 64}%`, width: `${Math.max(4, (span.duration_ms / duration) * 34)}%` }} />
            <small>{span.duration_ms}ms</small>
          </button>
        </li>
      ))}
    </ol>
  );
}

function CausalTree({ run, onSelect }: { run: EvalRun; onSelect: (spanId: string) => void }) {
  const root = run.spans.find((span) => span.parent_span_id === null) ?? run.spans[0];
  return (
    <div className="eval-causal-tree" role="tree" aria-label="Trace causal tree">
      <button role="treeitem" aria-expanded="true" onClick={() => onSelect(root.span_id)}><b>TRACE</b><strong>{root.name}</strong><small>{root.span_id}</small></button>
      <div role="group">{run.spans.filter((span) => span.parent_span_id === root.span_id).map((span) => <button role="treeitem" key={span.span_id} onClick={() => onSelect(span.span_id)}><b>{span.kind}</b><strong>{span.name}</strong><small>{span.causation_id}</small></button>)}</div>
    </div>
  );
}

export function projectEventWindow<T>(events: readonly T[], offset: number, pageSize: number) {
  const lastOffset = Math.floor((Math.max(events.length, 1) - 1) / pageSize) * pageSize;
  const safeOffset = Math.min(Math.max(0, offset), lastOffset);
  return {
    lastOffset,
    safeOffset,
    visibleEvents: events.slice(safeOffset, safeOffset + pageSize)
  };
}

function EventTable({ run, selectedEventId, onSelect }: { run: EvalRun; selectedEventId: string | undefined; onSelect: (event: TraceEvent) => void }) {
  const pageSize = 40;
  const [offset, setOffset] = useState(0);
  useEffect(() => setOffset(0), [run.run_id]);
  const { lastOffset, safeOffset, visibleEvents } = projectEventWindow(run.events, offset, pageSize);
  return (
    <div className="eval-event-table" role="region" aria-label="Append-only event table" tabIndex={0}>
      <div className="eval-event-table__viewport">
        <table><thead><tr><th>SEQ</th><th>EVENT</th><th>NODE</th><th>CAUSE</th><th>SUMMARY</th></tr></thead><tbody>
          {visibleEvents.map((event) => <tr key={event.event_id} className={event.event_id === selectedEventId ? "is-selected" : undefined}><td>{String(event.sequence).padStart(3, "0")}</td><td>{event.type}</td><td>{event.node_id ?? "run"}</td><td>{event.causation_id}</td><td><button type="button" className="eval-event-select" aria-pressed={event.event_id === selectedEventId} aria-label={`Inspect event ${event.sequence}: ${event.type}`} onClick={() => onSelect(event)}>{event.summary}</button></td></tr>)}
        </tbody></table>
      </div>
      {run.events.length > pageSize && <nav className="eval-event-table__pager" aria-label="Event table pages">
        <p aria-live="polite">Showing {safeOffset + 1}–{safeOffset + visibleEvents.length} of {run.events.length} events</p>
        <button type="button" disabled={safeOffset === 0} onClick={() => setOffset(Math.max(0, safeOffset - pageSize))}>Previous events</button>
        <button type="button" disabled={safeOffset >= lastOffset} onClick={() => setOffset(Math.min(lastOffset, safeOffset + pageSize))}>Next events</button>
      </nav>}
    </div>
  );
}

function TraceWorkbench({ run }: { run: EvalRun }) {
  const [mode, setMode] = useState("waterfall");
  const [selectedSpanId, setSelectedSpanId] = useState(run.spans[0]?.span_id ?? "");
  const [selectedEvent, setSelectedEvent] = useState<TraceEvent | null>(run.events[0] ?? null);
  useEffect(() => {
    setSelectedSpanId(run.spans[0]?.span_id ?? "");
    setSelectedEvent(run.events[0] ?? null);
  }, [run]);
  const span = run.spans.find((candidate) => candidate.span_id === selectedSpanId) ?? run.spans[0];
  const selectSpan = (spanId: string) => {
    setSelectedSpanId(spanId);
    setSelectedEvent(
      run.events.find((event) => event.span_id === spanId && ["tool.requested", "state.patch", "eval.assertion"].includes(event.type))
      ?? run.events.find((event) => event.span_id === spanId)
      ?? null
    );
  };
  return (
    <div className="eval-trace-grid">
      <div className="eval-trace-instrument">
        <Tabs label="Trace projection" selectedId={mode} onChange={setMode} items={[
          { id: "waterfall", label: "Waterfall", content: <Waterfall run={run} onSelect={selectSpan} /> },
          { id: "causal", label: "Causal tree", content: <CausalTree run={run} onSelect={selectSpan} /> },
          { id: "events", label: "Event table", content: <EventTable run={run} selectedEventId={selectedEvent?.event_id} onSelect={(event) => { setSelectedEvent(event); setSelectedSpanId(event.span_id); }} /> }
        ]} />
      </div>
      <aside className="eval-correlation" aria-label="Trace correlation inspector">
        <span className="machine-label">CORRELATION INSPECTOR</span>
        <h3>{span?.name ?? "No span"}</h3>
        <dl>
          <div><dt>SPAN</dt><dd>{span?.span_id}</dd></div>
          <div><dt>NODE</dt><dd>{span?.node_id ?? "run root"}</dd></div>
          <div><dt>EVENT</dt><dd>{selectedEvent?.type ?? "Select an event"}</dd></div>
          <div><dt>PAYLOAD HASH</dt><dd>{selectedEvent?.payload_hash.slice(0, 16) ?? "—"}</dd></div>
        </dl>
        {selectedEvent ? <p className="eval-event-summary">{selectedEvent.summary}</p> : null}
        <pre>{selectedEvent ? formatValue(selectedEvent.payload) : formatValue(span?.attributes)}</pre>
      </aside>
    </div>
  );
}

function ComparisonBoard({ comparison }: { comparison: RunComparison }) {
  return (
    <div className="eval-compare">
      <header>
        <div><span>SAME FINAL ANSWER</span><strong>{comparison.same_final_answer ? "YES" : "NO"}</strong></div>
        <div className="is-critical"><span>UNSAFE TRAJECTORY</span><strong>{comparison.unsafe_trajectory_detected ? "DETECTED" : "CLEAR"}</strong></div>
        <Badge tone="error">CRITICAL GATE {comparison.security_gate.toUpperCase()}</Badge>
      </header>
      <div className="eval-compare-runs">
        {[comparison.left, comparison.right].map((run, index) => <article className={index === 1 ? "is-unsafe" : ""} key={run.run_id}><span>{index === 0 ? "CONTROL RUN" : "UNSAFE TWIN"}</span><h3>{run.status.toUpperCase()}</h3><p>{run.answer}</p><dl><div><dt>SCORE</dt><dd>{Math.round(run.score * 100)}%</dd></div><div><dt>CRITICAL FAILURES</dt><dd>{run.critical_failures}</dd></div><div><dt>TRACE</dt><dd>{run.spans.length} spans / {run.events.length} events</dd></div></dl></article>)}
      </div>
      <ol className="eval-diff-ledger" aria-label="Run comparison sections">
        {comparison.sections.map((section) => <li key={section.section} className={section.changed ? "is-changed" : ""}><strong>{section.section}</strong><pre tabIndex={0}>{formatValue(section.left)}</pre><span aria-hidden="true">{section.changed ? "≠" : "="}</span><pre tabIndex={0}>{formatValue(section.right)}</pre></li>)}
      </ol>
    </div>
  );
}

function exportFile(bundle: ExportBundle, filename: string) {
  const file = bundle.files.find((candidate) => candidate.filename === filename);
  if (!file) throw new Error(`${filename} missing from the verified export bundle`);
  download(file.filename, file.content, file.media_type);
}

export function ObservabilityEvalPage({ onNavigate }: { onNavigate: (path: string) => void }) {
  const [catalog, setCatalog] = useState<EvalCatalog | null>(null);
  const [batch, setBatch] = useState<EvalBatch | null>(null);
  const [runId, setRunId] = useState("");
  const [comparison, setComparison] = useState<RunComparison | null>(null);
  const [busy, setBusy] = useState<string | null>("catalog");
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let active = true;
    setBusy("catalog");
    setError(null);
    void loadEvalCatalog().then((next) => { if (active) setCatalog(next); }).catch((reason: unknown) => {
      if (active && !(reason instanceof DOMException && reason.name === "AbortError")) setError(reason instanceof Error ? reason.message : "Evaluation catalog failed");
    }).finally(() => { if (active) setBusy(null); });
    return () => { active = false; };
  }, [revision]);

  const selectedRun = useMemo(() => batch?.runs.find((run) => run.run_id === runId) ?? batch?.runs[0] ?? null, [batch, runId]);

  const executeBatch = async (count: 70 | 100) => {
    setBusy(`batch-${count}`);
    setError(null);
    try {
      const next = await runEvalBatch(count);
      setBatch(next);
      setRunId(next.runs[0]?.run_id ?? "");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Evaluation batch failed");
    } finally { setBusy(null); }
  };

  const executeCompare = async () => {
    setBusy("compare");
    setError(null);
    try { setComparison(await createRunComparison()); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Run comparison failed"); }
    finally { setBusy(null); }
  };

  const executeExport = async (filename: "run.json" | "evaluation.md") => {
    if (!selectedRun) return;
    setBusy(`export-${filename}`);
    setError(null);
    try {
      const bundle = await exportEvalRun(selectedRun.run_id);
      if (filename === "run.json") {
        download(`agent-trace-${selectedRun.run_id}-bundle.json`, JSON.stringify(bundle, null, 2), "application/json");
      } else exportFile(bundle, filename);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Export failed"); }
    finally { setBusy(null); }
  };

  if (busy === "catalog" && !catalog) return <div className="page eval-page"><div className="eval-loading" role="status"><span>TRACE</span><i /><span>ASSERT</span><i /><span>COMPARE</span><p>Validating the repository Golden dataset…</p></div></div>;
  if (error && !catalog) return <div className="page eval-page"><ErrorState title="Evaluation catalog unavailable" description={error} action={<Button variant="primary" onClick={() => setRevision((value) => value + 1)}>Retry catalog</Button>} /></div>;

  return (
    <div className="page eval-page">
      <header className="workspace-header eval-header"><div><span className="machine-label">M9 // OBSERVABILITY + EVALS</span><h1>Trace evaluation studio</h1><p>Score the trajectory, not just the sentence. Every assertion below is linked to a persisted span and append-only runtime event.</p></div><div className="eval-header__actions"><Badge tone={batch?.status === "failed" ? "error" : batch ? "success" : "neutral"}>{batch ? `${batch.dashboard.passed}/${batch.target_count} GREEN` : `${catalog?.case_count ?? 0} CASES ARMED`}</Badge><Button variant="ghost" onClick={() => onNavigate("/lab")}>Back to Lab</Button></div></header>

      {error && <div className="eval-inline-error" role="alert"><strong>CHECK FAILED</strong><span>{error}</span><Button variant="ghost" onClick={() => setError(null)}>Dismiss</Button></div>}

      <section className="eval-command-deck" aria-label="Evaluation commands">
        <div><span className="machine-label">DATASET</span><strong>{catalog?.dataset_id}</strong><small>{catalog?.scenario_count} scenarios · {catalog?.evaluator_kinds.length} evaluator kinds</small></div>
        <Button variant="primary" loading={busy === "batch-70"} onClick={() => void executeBatch(70)}>Run 70 Golden cases</Button>
        <Button loading={busy === "batch-100"} onClick={() => void executeBatch(100)}>Benchmark 100 cases</Button>
        <Button loading={busy === "compare"} onClick={() => void executeCompare()}>Build unsafe twin</Button>
        <div className="eval-otel"><i /><span>OTEL EXPORT</span><strong>OFF / LOCAL</strong></div>
      </section>

      {!batch ? <Panel title="Evaluation dashboard" context="NO BATCH EXECUTED"><EmptyState title="No scored runtime trajectories" description="Run the versioned Golden dataset. Results, spans and events will be computed by the deterministic Mock runtime and persisted in SQLite—not projected from fixture pass flags." action={<Button variant="primary" onClick={() => void executeBatch(70)}>Run the Golden set</Button>} /></Panel> : <>
        <MetricRail batch={batch} />
        <div className="eval-dashboard-grid">
          <Panel title="Scenario matrix" context="SEVEN DOMAINS // ACTUAL RESULTS"><ScenarioMatrix batch={batch} /></Panel>
          <Panel title="Failure clusters" context="DETERMINISTIC ROOT CAUSE">
            {batch.dashboard.failure_clusters.length ? <ol className="eval-clusters">{batch.dashboard.failure_clusters.map((cluster) => <li key={cluster.category}><strong>{cluster.category}</strong><span>{cluster.count}</span><small>{cluster.case_ids.join(" · ")}</small></li>)}</ol> : <div className="eval-all-clear"><span>00</span><strong>No failed clusters</strong><p>All critical and non-critical Golden assertions passed. The security gate is evaluated separately from the average.</p></div>}
            <div className="eval-breakdown-grid" aria-label="Failure dimensions">
              <section><strong>BY MODULE</strong><dl>{batch.dashboard.failures_by_module.map((item) => <div key={item.key}><dt>{item.key}</dt><dd>{item.failures}/{item.total}</dd></div>)}</dl></section>
              <section><strong>BY PROVIDER</strong><dl>{batch.dashboard.failures_by_provider.map((item) => <div key={item.key}><dt>{item.key}</dt><dd>{item.failures}/{item.total}</dd></div>)}</dl></section>
            </div>
          </Panel>
        </div>

        <Panel title="Case drill-down" context={`${selectedRun?.scenario_id ?? "SCENARIO"} // PERSISTED TRACE`} actions={<div className="eval-case-actions"><Select label="GOLDEN CASE" value={selectedRun?.run_id ?? ""} onChange={(event) => setRunId(event.target.value)}>{batch.runs.map((run) => <option key={run.run_id} value={run.run_id}>{run.case_id}</option>)}</Select><Button variant="ghost" loading={busy === "export-run.json"} onClick={() => void executeExport("run.json")}>Export JSON bundle</Button><Button variant="ghost" loading={busy === "export-evaluation.md"} onClick={() => void executeExport("evaluation.md")}>Export Markdown</Button></div>}>
          {selectedRun && <TraceWorkbench run={selectedRun} />}
        </Panel>

        {selectedRun && <Panel title="Assertion ledger" context="TRACE-LINKED // CRITICAL IS NON-AVERAGEABLE"><ol className="eval-assertions">{selectedRun.assertions.map((assertion, index) => <li key={assertion.assertion_id} className={assertion.passed ? "is-pass" : "is-fail"}><span>{String(index + 1).padStart(2, "0")}</span><div><strong>{assertion.kind} · {assertion.path}</strong><small>{assertion.message}</small></div><code>{assertion.operator}</code><Badge tone={assertion.passed ? "success" : "error"}>{assertion.critical ? "CRITICAL" : "CHECK"} · {assertion.passed ? "PASS" : "FAIL"}</Badge></li>)}</ol></Panel>}
      </>}

      <Panel title="Run comparison" context="SAME ANSWER ≠ SAME SAFETY" actions={!comparison && <Button loading={busy === "compare"} onClick={() => void executeCompare()}>Generate comparison</Button>}>
        {comparison ? <ComparisonBoard comparison={comparison} /> : <EmptyState title="No comparison pair" description="Create a controlled pair with the exact same answer: one follows the approved tool trajectory and one attempts an unapproved write. The critical security assertion must fail visibly." />}
      </Panel>
    </div>
  );
}
