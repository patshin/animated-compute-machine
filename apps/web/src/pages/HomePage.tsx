import { useEffect, useMemo, useState } from "react";

import type { CapabilitiesResponse, HealthResponse } from "../api";
import {
  loadLearningMap,
  recordLearningEvidence,
  resetLearningProgress,
  type EvidenceKind,
  type LearningMap
} from "../scenario-api";
import { Badge, Button, ErrorState, Input, Panel } from "../ui/primitives";

interface HomePageProps {
  status: { health: HealthResponse; capabilities: CapabilitiesResponse } | null;
  error: string | null;
  onNavigate: (path: string) => void;
  onRetry: () => void;
  safeLabMode: boolean;
}

type LearningLens = "pm" | "builder" | "architect";

export function HomePage({ status, error, onNavigate, onRetry, safeLabMode }: HomePageProps) {
  const [learning, setLearning] = useState<LearningMap | null>(null);
  const [learningError, setLearningError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState("M0");
  const [lens, setLens] = useState<LearningLens>("builder");
  const [prediction, setPrediction] = useState("");
  const [explainBack, setExplainBack] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLearning(null); setLearningError(null);
    void loadLearningMap(controller.signal).then(setLearning).catch((reason: unknown) => {
      if (!controller.signal.aborted) setLearningError(reason instanceof Error ? reason.message : "Learning Map failed to load");
    });
    return () => controller.abort();
  }, [revision]);

  const selected = useMemo(() => learning?.modules.find((module) => module.id === selectedId) ?? learning?.modules[0], [learning, selectedId]);

  const record = async (kind: EvidenceKind, response: string) => {
    if (!selected) return;
    setBusy(kind); setLearningError(null);
    try {
      setLearning(await recordLearningEvidence(selected.id, kind, response, selected.recommended_scenario));
    } catch (reason) {
      setLearningError(reason instanceof Error ? reason.message : "Mastery evidence was rejected");
    } finally { setBusy(null); }
  };

  const reset = async () => {
    if (!selected) return;
    setBusy("reset"); setLearningError(null);
    try { setLearning(await resetLearningProgress(selected.id)); }
    catch (reason) { setLearningError(reason instanceof Error ? reason.message : "Learning progress reset failed"); }
    finally { setBusy(null); }
  };

  return (
    <div className="page home-page">
      <header className="page-hero">
        <div className="event-spine" aria-hidden="true" />
        <div>
          <div className="machine-label">LEARNING MAP // EVIDENCE-BASED MASTERY</div>
          <h1>See the whole agent.<br /><span>Prove what you learned.</span></h1>
          <p>Inspect typed decisions, runtime events, tool boundaries and human approvals from one local workstation.</p>
        </div>
        <div className="hero-stamp" aria-label="Local-first and no API key required"><strong>{learning?.completed_modules ?? 0}/12</strong><span>MODULES PROVEN</span><small>{learning?.evidence_count ?? 0} DURABLE EVIDENCE</small></div>
      </header>

      {safeLabMode && <p className="builder-notice" role="status"><strong>PUBLIC PREVIEW / SAFE LAB MODE.</strong> Built-in scenarios, Mock runs, replay, evaluation and approval simulation remain available. Persistent personal preferences, learning-progress writes, imports, exports, subprocesses and external providers are disabled by the API.</p>}

      <section className="home-grid" aria-label="Current studio status">
        <Panel title="Scenario mission deck" context="RUN // INSPECT // EXPLAIN" className="mission-panel"><div className="mission-empty"><div className="mission-empty__route" aria-hidden="true"><i /><span /><i /><span /><i /></div><p>Choose a validated Kit, predict the route, then bring concrete run evidence back to the Learning Map.</p><div className="home-mission-actions"><Button variant="primary" onClick={() => onNavigate("/scenarios")}>Choose a Scenario Kit</Button><Button onClick={() => onNavigate("/lab")}>Open Visual Lab</Button></div></div></Panel>
        <Panel title="Repository handshake" context="REAL LOCAL RUNTIME" tone="console" className="runtime-panel">
          {error ? <ErrorState title="Local API did not answer" description={error} action={<Button variant="console" onClick={onRetry}>Check again</Button>} /> : status ? <div className="runtime-readout"><div><span>API</span><strong>{status.health.status.toUpperCase()}</strong><small>{status.health.service} · v{status.health.version}</small></div><div><span>PROVIDER</span><strong>{status.capabilities.providers[0]?.toUpperCase()}</strong><small>Offline-ready · Python {status.capabilities.python_version}</small></div><div className="runtime-readout__footer"><Badge tone="success">SCHEMA BOUNDARY VERIFIED</Badge><Button variant="console" onClick={onRetry}>Refresh</Button></div></div> : <div className="runtime-checking" aria-label="Checking the local API"><i /><i /><i /><span>Negotiating local capabilities…</span></div>}
        </Panel>
      </section>

      <section className="learning-studio" aria-labelledby="curriculum-title">
        <header className="section-heading"><div><span className="machine-label">12 MODULES // THREE LENSES // NO NEXT BUTTON</span><h2 id="curriculum-title">Learning map</h2></div><p>{learning?.completion_rule ?? "Loading the deterministic mastery rule…"}</p></header>
        {learningError && !error && <ErrorState title="Learning evidence boundary rejected" description={learningError} action={<Button onClick={() => setRevision((value) => value + 1)}>Reload progress</Button>} />}
        {learningError && error && <p className="builder-notice" role="status">Learning progress is also unavailable until the local API reconnects.</p>}
        {!learning && !learningError && <div className="scenario-loading" aria-label="Loading Learning Map"><i /><i /><i /><span>Restoring repository-local mastery evidence…</span></div>}
        {learning && selected && <div className="learning-layout">
          <div className="learning-map-grid" role="group" aria-label="Learning modules">{learning.modules.map((module, index) => <button className={`${selected.id === module.id ? "is-selected" : ""} ${module.completed ? "is-complete" : ""}`} key={module.id} onClick={() => setSelectedId(module.id)}><header><span>{module.id}</span><Badge tone={module.completed ? "success" : module.evidence.length ? "active" : "neutral"}>{module.completed ? "MASTERED" : module.mastery_level.replace("_", " ").toUpperCase()}</Badge></header><strong>{module.title}</strong><small>{module.concepts.join(" · ")}</small><footer><span>{String(index + 1).padStart(2, "0")} / 12</span><span>{module.evidence.filter((item) => item.passed).length} evidence</span></footer></button>)}</div>
          <aside className="mastery-desk">
            <header><span className="machine-label">{selected.id} // {selected.estimated_minutes} MIN</span><h2>{selected.title}</h2><p>{selected.lens_copy[lens]}</p></header>
            <div className="lens-switcher" role="group" aria-label="Learning lens">{(["pm", "builder", "architect"] as LearningLens[]).map((item) => <button aria-pressed={lens === item} key={item} onClick={() => setLens(item)}>{item === "pm" ? "PM" : item[0].toUpperCase() + item.slice(1)}</button>)}</div>
            <dl className="mastery-facts"><div><dt>Prerequisite</dt><dd>{selected.prerequisite ?? "Entry module"}</dd></div><div><dt>Recommended Kit</dt><dd>{selected.recommended_scenario}</dd></div><div><dt>Mastery</dt><dd>{selected.mastery_level}</dd></div></dl>
            <div className="mastery-actions">
              <Input label="Prediction" hint={`Name one concept: ${selected.concepts.join(", ")}`} value={prediction} onChange={(event) => setPrediction(event.target.value)} />
              <Button loading={busy === "prediction"} disabled={safeLabMode || !prediction.trim()} onClick={() => void record("prediction", prediction)}>Record prediction</Button>
              <Input label="Explain back" hint={`Use at least 8 words and two concepts: ${selected.concepts.join(", ")}`} value={explainBack} onChange={(event) => setExplainBack(event.target.value)} />
              <Button loading={busy === "explain_back"} disabled={safeLabMode || !explainBack.trim()} onClick={() => void record("explain_back", explainBack)}>Score explain-back</Button>
              <Button loading={busy === "machine_assertion"} disabled={safeLabMode} onClick={() => void record("machine_assertion", "validate")}>Run machine assertion</Button>
            </div>
            <ol className="mastery-evidence" aria-label="Recorded mastery evidence">{selected.evidence.length ? selected.evidence.map((item) => <li key={item.evidence_id}><span>{item.kind.replace("_", " ")}</span><strong>{item.passed ? "PASS" : "RETRY"}</strong><p>{item.summary}</p></li>) : <li className="is-empty"><p>No evidence recorded. Navigation never counts toward completion.</p></li>}</ol>
            <div className="mastery-reset"><p><strong>{safeLabMode ? "Read-only in public preview." : "Independent reset."}</strong> {safeLabMode ? "Anonymous mastery evidence is not persisted." : `Removes mastery evidence for ${selected.id}; run traces and drafts remain intact.`}</p><Button variant="danger" loading={busy === "reset"} disabled={safeLabMode} onClick={() => void reset()}>Reset module progress</Button></div>
          </aside>
        </div>}
      </section>
    </div>
  );
}
