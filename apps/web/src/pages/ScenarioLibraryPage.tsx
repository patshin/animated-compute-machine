import { useEffect, useState } from "react";

import {
  createScenarioDraft,
  loadScenarioCatalog,
  loadScenarioDetail,
  type ScenarioDetail,
  type ScenarioSummary
} from "../scenario-api";
import { Badge, Button, ErrorState, Panel } from "../ui/primitives";

interface ScenarioLibraryPageProps {
  onNavigate: (path: string) => void;
  safeLabMode?: boolean;
}

export function ScenarioLibraryPage({ onNavigate, safeLabMode = false }: ScenarioLibraryPageProps) {
  const [scenarios, setScenarios] = useState<ScenarioSummary[] | null>(null);
  const [selected, setSelected] = useState<ScenarioDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setScenarios(null);
    setError(null);
    void loadScenarioCatalog(controller.signal)
      .then((items) => {
        setScenarios(items);
        return loadScenarioDetail(items[0].id, controller.signal);
      })
      .then(setSelected)
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Scenario Library failed to load");
      });
    return () => controller.abort();
  }, [revision]);

  const inspect = async (scenarioId: string) => {
    setBusy(`inspect:${scenarioId}`);
    setError(null);
    try {
      setSelected(await loadScenarioDetail(scenarioId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Scenario detail failed to load");
    } finally {
      setBusy(null);
    }
  };

  const clone = async (scenarioId: string) => {
    setBusy(`clone:${scenarioId}`);
    setError(null);
    try {
      const draft = await createScenarioDraft(scenarioId);
      onNavigate(`/builder?draft=${encodeURIComponent(draft.draft_id)}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Scenario clone failed");
      setBusy(null);
    }
  };

  return (
    <div className="page scenario-library-page">
      <header className="workspace-header scenario-header">
        <div><span className="machine-label">SCENARIO SDK // 7 VALIDATED KITS</span><h1>Scenario Library</h1><p>{safeLabMode ? "Inspect and preview validated built-in fixtures in the read-only public workspace." : "Inspect, clone and preview declarative local fixtures without adding scenario branches to the product core."}</p></div>
        <Button variant="primary" disabled={safeLabMode} onClick={() => onNavigate("/builder")}>Open Builder</Button>
      </header>

      {safeLabMode && <p className="builder-notice" role="status">Scenario authoring, cloning, import and export are disabled in public preview. Built-in Kit inspection and live preview remain available.</p>}

      {error && <ErrorState title="Scenario boundary rejected" description={error} action={<Button onClick={() => setRevision((value) => value + 1)}>Retry catalog</Button>} />}
      {!scenarios && !error && <div className="scenario-loading" aria-label="Loading Scenario Kits"><i /><i /><i /><span>Validating eight-section Kit contracts…</span></div>}
      {scenarios && (
        <div className="scenario-library-layout">
          <section className="scenario-card-grid" aria-label="Built-in Scenario Kits">
            {scenarios.map((scenario, index) => (
              <article className={`scenario-card scenario-card--${index % 4} ${selected?.summary.id === scenario.id ? "is-selected" : ""}`} key={scenario.id}>
                <header><span>{String(index + 1).padStart(2, "0")}</span><Badge tone={scenario.compatible ? "success" : "error"}>{scenario.validation_status.toUpperCase()}</Badge></header>
                <div><span className="machine-label">{scenario.domain} // {scenario.difficulty}</span><h2>{scenario.name}</h2><p>{scenario.short_description}</p></div>
                <ul aria-label={`${scenario.name} concepts`}>{scenario.concepts.map((concept) => <li key={concept}>{concept}</li>)}</ul>
                <dl><div><dt>TOOLS</dt><dd>{scenario.tool_count}</dd></div><div><dt>LESSONS</dt><dd>{scenario.lesson_count}</dd></div><div><dt>GOLDENS</dt><dd>{scenario.golden_count}</dd></div></dl>
                <footer><Button loading={busy === `inspect:${scenario.id}`} onClick={() => void inspect(scenario.id)}>Inspect</Button><Button variant="primary" disabled={safeLabMode} loading={busy === `clone:${scenario.id}`} onClick={() => void clone(scenario.id)}>Clone Kit</Button></footer>
              </article>
            ))}
          </section>

          <aside className="scenario-detail" aria-live="polite">
            {selected && <Panel title={selected.summary.name} context="KIT DETAIL // CONTRACT BACKED" tone="console" actions={<Badge tone={selected.report.valid ? "success" : "error"}>{selected.report.valid ? "8/8 VALID" : `${selected.report.error_count} ERRORS`}</Badge>}>
              <p>{selected.summary.short_description}</p>
              <dl className="scenario-detail__ledger">
                <div><dt>Kit ID</dt><dd>{selected.summary.id}</dd></div><div><dt>Graph</dt><dd>{selected.summary.graph_template}</dd></div><div><dt>Classification</dt><dd>{selected.summary.data_classification}</dd></div><div><dt>Fingerprint</dt><dd><code>{selected.report.fingerprint.slice(0, 16)}</code></dd></div>
              </dl>
              <ol className="scenario-section-strip" aria-label="Validation sections">{selected.report.sections.map((section) => <li key={section.name}><span>{section.name}</span><strong>{section.status}</strong></li>)}</ol>
              <div className="scenario-detail__actions"><Button variant="console" onClick={() => onNavigate(`/lab/scenario-preview?source=built_in&id=${encodeURIComponent(selected.summary.id)}`)}>Live Preview</Button><Button variant="console" disabled={safeLabMode} loading={busy === `clone:${selected.summary.id}`} onClick={() => void clone(selected.summary.id)}>Clone into Builder</Button></div>
            </Panel>}
          </aside>
        </div>
      )}
    </div>
  );
}
