import { useEffect, useMemo, useState, type KeyboardEvent } from "react";

import {
  deletePreference,
  loadMemory,
  loadRetrievalCatalog,
  savePreference,
  startRetrievalRun,
  type Authority,
  type Evidence,
  type MemorySnapshot,
  type PreferenceValue,
  type RetrievalCatalog,
  type RetrievalRun,
  type RetrievedChunk
} from "../retrieval-api";
import { Badge, Button, EmptyState, Input, Panel, Select } from "../ui/primitives";

const authorityOptions: Array<{ value: "all" | Authority; label: string }> = [
  { value: "all", label: "All authority levels" },
  { value: "primary", label: "Primary only" },
  { value: "official", label: "Official only" },
  { value: "internal", label: "Internal only" },
  { value: "secondary", label: "Secondary only" },
  { value: "unknown", label: "Unknown only" }
];

function scorePercent(score: number) {
  return `${Math.max(4, Math.min(100, Math.round(score * 100)))}%`;
}

function toneForFreshness(freshness: string) {
  if (freshness === "current") return "success" as const;
  if (freshness === "superseded" || freshness === "stale") return "warning" as const;
  return "neutral" as const;
}

function moveChunkFocus(event: KeyboardEvent<HTMLButtonElement>, index: number) {
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const buttons = Array.from(
    event.currentTarget.closest(".evidence-chunk-list")
      ?.querySelectorAll<HTMLButtonElement>(".evidence-chunk") ?? []
  );
  const next = event.key === "Home"
    ? 0
    : event.key === "End"
      ? buttons.length - 1
      : event.key === "ArrowDown"
        ? Math.min(buttons.length - 1, index + 1)
        : Math.max(0, index - 1);
  buttons[next]?.focus();
}

function ChunkList({ chunks, selectedId, onSelect }: {
  chunks: RetrievedChunk[];
  selectedId: string | null;
  onSelect: (chunk: RetrievedChunk) => void;
}) {
  return (
    <ol className="evidence-chunk-list" aria-label="Ranked retrieval chunks">
      {chunks.map((chunk, index) => (
        <li key={chunk.chunk_id}>
          <button
            aria-label={`Rank ${index + 1}, ${chunk.title}, version ${chunk.version}, ${chunk.freshness}`}
            aria-pressed={selectedId === chunk.chunk_id}
            className="evidence-chunk"
            onClick={() => onSelect(chunk)}
            onKeyDown={(event) => moveChunkFocus(event, index)}
            tabIndex={selectedId === chunk.chunk_id || (!selectedId && index === 0) ? 0 : -1}
            type="button"
          >
            <span className="evidence-chunk__rank">{String(index + 1).padStart(2, "0")}</span>
            <span className="evidence-chunk__identity">
              <strong>{chunk.title}</strong>
              <small>{chunk.section} · v{chunk.version}</small>
            </span>
            <Badge tone={toneForFreshness(chunk.freshness)}>{chunk.freshness}</Badge>
            {chunk.suspicious && <Badge tone="error">UNTRUSTED</Badge>}
            <span className="evidence-score" aria-label={`Retrieval score ${chunk.score.toFixed(3)}`}>
              <i style={{ width: scorePercent(chunk.score) }} />
            </span>
            <code>{chunk.score.toFixed(3)}</code>
          </button>
        </li>
      ))}
    </ol>
  );
}

function ScoreBreakdown({ chunk }: { chunk: RetrievedChunk | null }) {
  if (!chunk) return null;
  return (
    <dl className="evidence-score-grid" aria-label="Score breakdown">
      {Object.entries(chunk.score_breakdown).map(([label, value]) => (
        <div key={label}><dt>{label.replaceAll("_", " ")}</dt><dd>{value >= 0 ? "+" : ""}{value.toFixed(3)}</dd></div>
      ))}
    </dl>
  );
}

function EvidenceInspector({ evidence, chunk }: { evidence: Evidence | null; chunk: RetrievedChunk | null }) {
  if (!evidence && !chunk) {
    return <EmptyState title="Select evidence" description="Choose a ranked chunk or a claim citation to inspect its source and provenance." />;
  }
  const content = evidence?.content.excerpt ?? evidence?.content.facts ?? chunk?.excerpt;
  return (
    <div className="evidence-inspector">
      <div className="evidence-inspector__badges">
        <Badge tone="evidence">{evidence?.kind ?? "document chunk"}</Badge>
        <Badge tone={toneForFreshness(evidence?.freshness ?? chunk?.freshness ?? "unknown")}>
          {evidence?.freshness ?? chunk?.freshness}
        </Badge>
        <Badge>{evidence?.authority ?? chunk?.authority}</Badge>
      </div>
      <h3>{evidence?.source.title ?? chunk?.title}</h3>
      <code>{evidence?.evidence_id ?? chunk?.chunk_id}</code>
      {chunk?.suppressed_instruction && (
        <p className="evidence-injection" role="alert">
          UNTRUSTED DOCUMENT INSTRUCTION SUPPRESSED · created 0 tool calls
        </p>
      )}
      <pre>{typeof content === "string" ? content : JSON.stringify(content, null, 2)}</pre>
      {evidence && (
        <dl className="evidence-inspector__meta">
          <div><dt>SOURCE</dt><dd>{evidence.source.source_type} · {evidence.source.source_id}</dd></div>
          <div><dt>LOCATOR</dt><dd>{evidence.source.locator ?? "n/a"}</dd></div>
          <div><dt>CREATED BY</dt><dd>{evidence.provenance.created_by}</dd></div>
          <div><dt>RUN</dt><dd>{evidence.provenance.run_id}</dd></div>
          <div><dt>INPUT REFS</dt><dd>{evidence.provenance.input_evidence_ids?.join(", ") ?? "none"}</dd></div>
          <div><dt>CONFLICT</dt><dd>{evidence.contradiction_group ?? "none"}</dd></div>
        </dl>
      )}
      <ScoreBreakdown chunk={chunk} />
    </div>
  );
}

function SourceTimeline({ run }: { run: RetrievalRun }) {
  const documents = run.evidence.filter((item) => item.kind === "document");
  return (
    <ol className="source-timeline" aria-label="Source and version timeline">
      {documents.map((item) => (
        <li className={`is-${item.freshness}`} key={item.evidence_id}>
          <i aria-hidden="true" />
          <div><strong>{item.source.title}</strong><span>v{item.source.version ?? "?"}</span></div>
          <Badge tone={toneForFreshness(item.freshness)}>{item.freshness}</Badge>
          <small>
            {item.authority} · <time dateTime={item.source.published_at ?? undefined}>
              {item.source.published_at ? new Date(item.source.published_at).toISOString().slice(0, 10) : "date unknown"}
            </time> · {item.citation_label}
          </small>
        </li>
      ))}
    </ol>
  );
}

function ClaimGraph({ run, onSelectEvidence }: {
  run: RetrievalRun;
  onSelectEvidence: (evidenceId: string) => void;
}) {
  return (
    <div className="claim-graph">
      <div className="claim-validation" role="status">
        <strong>{run.citation_validation.coverage_percent.toFixed(0)}%</strong>
        <span>REQUIRED CLAIM COVERAGE</span>
        <Badge tone={run.citation_validation.valid ? "success" : "error"}>
          {run.citation_validation.valid ? "VALID" : "INVALID"}
        </Badge>
      </div>
      <ol aria-label="Evidence to claim links">
        {run.claims.map((claim) => (
          <li key={claim.claim_id}>
            <span>{claim.kind.toUpperCase()}</span>
            <p>{claim.text}</p>
            <div>
              {claim.citation_ids.map((citation) => (
                <button key={citation} onClick={() => onSelectEvidence(citation)} type="button">
                  {run.evidence.find((item) => item.evidence_id === citation)?.citation_label ?? citation}
                </button>
              ))}
            </div>
          </li>
        ))}
      </ol>
      {run.citation_validation.warnings.map((warning) => <p className="claim-warning" key={warning}>{warning}</p>)}
    </div>
  );
}

function MemoryWorkbench({ catalog, memory, onMemoryChange }: {
  catalog: RetrievalCatalog;
  memory: MemorySnapshot;
  onMemoryChange: (memory: MemorySnapshot) => void;
}) {
  const [value, setValue] = useState<PreferenceValue>(memory.preferences[0]?.value ?? "balanced");
  const [confirmed, setConfirmed] = useState(false);
  const [pending, setPending] = useState<"save" | "delete" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const preference = memory.preferences[0];

  const save = async () => {
    setPending("save");
    setError(null);
    try {
      onMemoryChange(await savePreference(value));
      setConfirmed(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Preference save failed");
    } finally {
      setPending(null);
    }
  };
  const remove = async () => {
    if (!preference) return;
    setPending("delete");
    setError(null);
    try {
      onMemoryChange(await deletePreference(preference.preference_id));
      setConfirmed(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Preference deletion failed");
    } finally {
      setPending(null);
    }
  };

  return (
    <section className="memory-workbench" aria-labelledby="memory-heading">
      <header><div><span className="machine-label">CONTROLLED MEMORY // USER VISIBLE</span><h2 id="memory-heading">Memory taxonomy console</h2></div><Badge tone="success">LOCAL SQLITE</Badge></header>
      <div className="memory-buckets">
        {catalog.memory_buckets.map((bucket) => (
          <article className={bucket.bucket_id === "long_term" ? "is-active" : ""} key={bucket.bucket_id}>
            <span>{bucket.bucket_id.replaceAll("_", " ")}</span>
            <strong>{bucket.label}</strong>
            <small>{bucket.retention}</small>
            <i>{bucket.mutable ? "USER MUTABLE" : "POLICY OWNED"}</i>
          </article>
        ))}
      </div>
      <div className="memory-control-grid">
        <fieldset>
          <legend>LONG-TERM PREFERENCE</legend>
          <Select label="Answer detail" value={value} onChange={(event) => setValue(event.target.value as PreferenceValue)}>
            <option value="concise">Concise</option>
            <option value="balanced">Balanced</option>
            <option value="detailed">Detailed</option>
          </Select>
          <label className="evidence-check"><input checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} type="checkbox" /> I confirm this repository-local preference change.</label>
          <div>
            <Button disabled={!confirmed} loading={pending === "save"} onClick={() => void save()} variant="primary">Save preference</Button>
            <Button disabled={!confirmed || !preference} loading={pending === "delete"} onClick={() => void remove()} variant="danger">Delete preference</Button>
          </div>
          {error && <p className="runtime-error" role="alert">{error}</p>}
        </fieldset>
        <section className="memory-receipt" aria-label="Long-term preference and memory audit">
          <h3>{preference ? "Stored preference" : "No long-term preference"}</h3>
          {preference ? <dl><div><dt>KEY</dt><dd>{preference.key}</dd></div><div><dt>VALUE</dt><dd>{preference.value}</dd></div><div><dt>ID</dt><dd>{preference.preference_id}</dd></div><div><dt>SCOPE</dt><dd>{preference.scope}</dd></div></dl> : <p>Run evidence and audit history remain separate from this deletable preference bucket.</p>}
          <ol>{memory.audit.slice(-4).map((entry) => <li key={entry.sequence}><span>{String(entry.sequence).padStart(2, "0")}</span><strong>{entry.action}</strong><code>{entry.preference_id}</code></li>)}</ol>
        </section>
      </div>
    </section>
  );
}

export function EvidenceMemoryPage({ onNavigate }: { onNavigate: (path: string) => void }) {
  const [catalog, setCatalog] = useState<RetrievalCatalog | null>(null);
  const [memory, setMemory] = useState<MemorySnapshot | null>(null);
  const [scenarioId, setScenarioId] = useState<"research-synthesis" | "risk-investigation">("research-synthesis");
  const [query, setQuery] = useState("");
  const [authority, setAuthority] = useState<"all" | Authority>("all");
  const [includeSuperseded, setIncludeSuperseded] = useState(true);
  const [exactPhrase, setExactPhrase] = useState(false);
  const [run, setRun] = useState<RetrievalRun | null>(null);
  const [selectedChunkId, setSelectedChunkId] = useState<string | null>(null);
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    void Promise.all([loadRetrievalCatalog(), loadMemory()])
      .then(([nextCatalog, nextMemory]) => {
        if (!active) return;
        setCatalog(nextCatalog);
        setMemory(nextMemory);
        const scenario = nextCatalog.scenarios[0];
        if (scenario) setQuery(scenario.default_query);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "Evidence lab failed to load");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [revision]);

  const scenario = catalog?.scenarios.find((item) => item.scenario_id === scenarioId) ?? null;
  const chunkSelection = run?.chunks.find((chunk) => chunk.chunk_id === selectedChunkId) ?? null;
  const explicitEvidence = run?.evidence.find((item) => item.evidence_id === selectedEvidenceId)
    ?? null;
  const selectedEvidence = explicitEvidence
    ?? (chunkSelection ? run?.evidence.find((item) => item.source.locator?.includes(chunkSelection.chunk_id)) : null)
    ?? null;
  const selectedChunk = explicitEvidence
    ? run?.chunks.find((chunk) => explicitEvidence.source.locator?.includes(chunk.chunk_id)) ?? null
    : chunkSelection;
  const conflicts = useMemo(() => run?.evidence.filter((item) => item.contradiction_group) ?? [], [run]);

  const changeScenario = (next: "research-synthesis" | "risk-investigation") => {
    setScenarioId(next);
    const nextScenario = catalog?.scenarios.find((item) => item.scenario_id === next);
    if (nextScenario) setQuery(nextScenario.default_query);
    setRun(null);
    setSelectedChunkId(null);
    setSelectedEvidenceId(null);
  };

  const execute = async () => {
    setRunning(true);
    setError(null);
    try {
      const next = await startRetrievalRun(scenarioId, query, {
        authorities: authority === "all" ? [] : [authority],
        includeSuperseded,
        exactPhrase
      });
      setRun(next);
      setSelectedChunkId(next.chunks[0]?.chunk_id ?? null);
      setSelectedEvidenceId(next.evidence[0]?.evidence_id ?? null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Retrieval failed");
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="page evidence-page">
      <header className="workspace-header evidence-header">
        <div><span className="machine-label">M5 // RETRIEVAL + EVIDENCE + MEMORY</span><h1>Evidence signal desk</h1><p>Run deterministic FTS5 retrieval, expose every ranking term, then prove each claim against typed in-run evidence.</p></div>
        <div className="evidence-header__actions"><Badge tone="success">OFFLINE</Badge><Button variant="ghost" onClick={() => onNavigate("/lab/durable-runtime")}>Durable runtime</Button><Button variant="ghost" onClick={() => onNavigate("/lab")}>Runtime X-ray</Button></div>
      </header>

      {loading ? (
        <div className="evidence-loading" role="status"><i aria-hidden="true" /><strong>INDEXING LOCAL SOURCES</strong><span>Opening repository SQLite FTS5 and memory tables…</span></div>
      ) : error && !catalog ? (
        <div className="runtime-error" role="alert"><h2>Evidence lab unavailable</h2><p>{error}</p><Button onClick={() => setRevision((value) => value + 1)}>Retry local load</Button></div>
      ) : catalog && memory ? (
        <>
          <section className="evidence-commandbar" aria-label="Retrieval controls">
            <Select label="Scenario" value={scenarioId} onChange={(event) => changeScenario(event.target.value as typeof scenarioId)}>
              {catalog.scenarios.map((item) => <option key={item.scenario_id} value={item.scenario_id}>{item.title}</option>)}
            </Select>
            <Input label="Evidence query" value={query} onChange={(event) => setQuery(event.target.value)} />
            <Select label="Authority filter" value={authority} onChange={(event) => setAuthority(event.target.value as typeof authority)}>
              {authorityOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </Select>
            <Button disabled={query.trim().length < 2} loading={running} onClick={() => void execute()} variant="console">Run retrieval</Button>
            <div className="evidence-options">
              <label className="evidence-check"><input checked={includeSuperseded} onChange={(event) => setIncludeSuperseded(event.target.checked)} type="checkbox" /> Show superseded</label>
              <label className="evidence-check"><input checked={exactPhrase} onChange={(event) => setExactPhrase(event.target.checked)} type="checkbox" /> Exact phrase</label>
              <span>SQLite FTS5 · BM25 · fixed seed 2026</span>
            </div>
            <div className="evidence-query-chips" aria-label="Suggested evidence queries">
              {scenario?.query_chips.map((chip) => <button key={chip} onClick={() => setQuery(chip)} type="button">{chip}</button>)}
            </div>
          </section>
          {error && <div className="runtime-error" role="alert"><h2>Retrieval interrupted</h2><p>{error}</p></div>}

          {!run && !running ? (
            <EmptyState title="No evidence run loaded" description="Choose a mixed-evidence scenario and run the local query to create a real retrieval trajectory." />
          ) : run?.status === "empty" ? (
            <EmptyState title="No matching evidence" description="The FTS5 query returned zero chunks. No evidence, claim or tool result was invented." />
          ) : run ? (
            <>
              <section className="evidence-status-strip" aria-label="Retrieval run status">
                <div><span>ENGINE</span><strong>SQLITE FTS5 / BM25</strong></div>
                <div><span>RUN</span><code>{run.run_id}</code></div>
                <div><span>CHUNKS</span><strong>{run.chunks.length}</strong></div>
                <div><span>EVIDENCE</span><strong>{run.evidence.length}</strong></div>
                <div><span>CITATION</span><strong>{run.citation_validation.coverage_percent.toFixed(0)}%</strong></div>
                <div><span>INJECTION CALLS</span><strong>{run.retrieved_instruction_tool_calls}</strong></div>
              </section>
              <div className="evidence-workbench">
                <Panel context="RANKING // EXPLAINABLE" title="Ranked chunks" actions={<Badge>{run.query.added_terms.length ? `+ ${run.query.added_terms.join(", ")}` : "NO REWRITE"}</Badge>}>
                  <ChunkList chunks={run.chunks} selectedId={selectedChunkId} onSelect={(chunk) => { setSelectedChunkId(chunk.chunk_id); setSelectedEvidenceId(null); }} />
                </Panel>
                <Panel context="NORMALIZED // CONTRACT 1.0" title="Evidence Inspector">
                  <EvidenceInspector evidence={selectedEvidence} chunk={selectedChunk} />
                </Panel>
              </div>
              <div className="evidence-analysis-grid">
                <Panel context="SOURCE LINEAGE" title="Version timeline"><SourceTimeline run={run} /></Panel>
                <Panel context="CONTRADICTION GROUP" title="Conflict circuit">
                  <div className="conflict-circuit">
                    <strong>{conflicts[0]?.contradiction_group ?? "No active conflict"}</strong>
                    {conflicts.map((item) => <button key={item.evidence_id} onClick={() => { setSelectedEvidenceId(item.evidence_id); setSelectedChunkId(null); }} type="button"><span>{item.authority}</span><b>{item.source.title}</b><small>{item.freshness} · v{item.source.version ?? "?"}</small></button>)}
                  </div>
                </Panel>
                <Panel context="CITATION VALIDATOR" title="Claim links"><ClaimGraph run={run} onSelectEvidence={(evidenceId) => { setSelectedEvidenceId(evidenceId); setSelectedChunkId(null); }} /></Panel>
              </div>
              <Panel className="evidence-trace-panel" context="CANONICAL AGENT EVENT" title="Run event trace">
                <ol className="evidence-event-trace" aria-label="Retrieval event timeline">{run.events.map((event) => <li key={event.event_id}><span>{String(event.sequence).padStart(2, "0")}</span><i aria-hidden="true" /><strong>{event.type}</strong><code>{event.node_id ?? "run"}</code></li>)}</ol>
              </Panel>
            </>
          ) : null}
          <MemoryWorkbench catalog={catalog} memory={memory} onMemoryChange={setMemory} />
        </>
      ) : null}
    </div>
  );
}
