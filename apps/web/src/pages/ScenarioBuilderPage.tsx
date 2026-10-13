import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import {
  createScenarioDraft,
  exportScenarioDraft,
  importScenarioArchive,
  loadCapstoneReport,
  loadScenarioCatalog,
  loadScenarioDraft,
  saveScenarioDraft,
  validateScenarioDraft,
  type CapstoneReport,
  type JsonObject,
  type ScenarioBundle,
  type ScenarioDraft,
  type ScenarioSummary,
  type SectionName
} from "../scenario-api";
import { Badge, Button, ErrorState, Input, Panel } from "../ui/primitives";

type BuilderStep = "identity" | "data" | "tools" | "graph" | "lessons" | "golden" | "validate" | "export";
const STEPS: Array<{ id: BuilderStep; label: string; short: string; section: SectionName | null }> = [
  { id: "identity", label: "Identity", short: "ID", section: "manifest" },
  { id: "data", label: "Data & evidence", short: "DATA", section: "data" },
  { id: "tools", label: "Tools", short: "TOOLS", section: "tools" },
  { id: "graph", label: "Graph template", short: "GRAPH", section: "graph" },
  { id: "lessons", label: "Lessons", short: "LEARN", section: "lessons" },
  { id: "golden", label: "Golden cases", short: "EVAL", section: "evals" },
  { id: "validate", label: "Validate & preview", short: "CHECK", section: null },
  { id: "export", label: "Export", short: "SHIP", section: null }
];

interface ScenarioBuilderPageProps { onNavigate: (path: string) => void; safeLabMode?: boolean; }
interface ImportSafetySummary { dataClassification: string; requiredPermissions: string[]; }

function asRecords(value: unknown): JsonObject[] { return Array.isArray(value) ? value.filter((item): item is JsonObject => typeof item === "object" && item !== null && !Array.isArray(item)) : []; }
function stringValue(value: unknown): string { return typeof value === "string" ? value : ""; }
function sectionValue(bundle: ScenarioBundle, section: SectionName): unknown { return bundle[section]; }

function replaceSection(bundle: ScenarioBundle, section: SectionName, value: unknown): ScenarioBundle {
  if (section === "evals") {
    if (!Array.isArray(value) || !value.every((item) => typeof item === "object" && item !== null && !Array.isArray(item))) throw new Error("Golden cases must be a JSON array of objects");
    return { ...bundle, evals: value as JsonObject[] };
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error(`${section} must be a JSON object`);
  return { ...bundle, [section]: value as JsonObject };
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("The selected archive could not be read"));
    reader.onload = () => {
      const value = reader.result;
      if (typeof value !== "string" || !value.includes(",")) reject(new Error("The selected archive did not produce a data URL"));
      else resolve(value.slice(value.indexOf(",") + 1));
    };
    reader.readAsDataURL(file);
  });
}

function download(filename: string, body: BlobPart, mediaType: string) {
  const url = URL.createObjectURL(new Blob([body], { type: mediaType }));
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = filename; anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function ScenarioBuilderWorkbench({ onNavigate }: ScenarioBuilderPageProps) {
  const [catalog, setCatalog] = useState<ScenarioSummary[] | null>(null);
  const [draft, setDraft] = useState<ScenarioDraft | null>(null);
  const [bundle, setBundle] = useState<ScenarioBundle | null>(null);
  const [step, setStep] = useState<BuilderStep>("identity");
  const [editor, setEditor] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [importSummary, setImportSummary] = useState<ImportSafetySummary | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [capstone, setCapstone] = useState<CapstoneReport | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const activeStep = STEPS.find((item) => item.id === step) ?? STEPS[0];
  const activeSection = activeStep.section;

  useEffect(() => {
    const controller = new AbortController();
    const draftId = new URLSearchParams(window.location.search).get("draft");
    setError(null);
    void Promise.all([loadScenarioCatalog(controller.signal), draftId ? loadScenarioDraft(draftId, controller.signal) : Promise.resolve(null)]).then(([items, loadedDraft]) => {
      setCatalog(items);
      if (loadedDraft) { setDraft(loadedDraft); setBundle(loadedDraft.bundle); }
    }).catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Builder failed to initialize"); });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (bundle && activeSection) setEditor(JSON.stringify(sectionValue(bundle, activeSection), null, 2));
  }, [activeSection, bundle]);

  const report = draft?.report;
  const currentReport = activeSection ? report?.sections.find((section) => section.name === activeSection) : undefined;
  const identity = bundle?.manifest ?? {};
  const firstTool = asRecords(bundle?.tools.tools)[0] ?? {};
  const firstLesson = asRecords(bundle?.lessons.lessons)[0] ?? {};
  const firstGolden = bundle?.evals[0] ?? {};
  const dirty = useMemo(() => Boolean(draft && bundle && JSON.stringify(bundle) !== JSON.stringify(draft.bundle)), [bundle, draft]);

  const updateIdentity = (key: string, value: string) => {
    if (!bundle) return;
    const previousId = stringValue(bundle.manifest.id);
    let next: ScenarioBundle = { ...bundle, manifest: { ...bundle.manifest, [key]: value } };
    if (key === "id" && previousId) {
      const lessons = asRecords(next.lessons.lessons).map((lesson) => lesson.scenario_id === previousId ? { ...lesson, scenario_id: value } : lesson);
      const evals = next.evals.map((item) => item.scenario_id === previousId ? { ...item, scenario_id: value } : item);
      next = { ...next, lessons: { ...next.lessons, lessons }, evals };
    }
    setBundle(next); setCapstone(null);
  };

  const updateFirst = (section: "tools" | "lessons", collection: "tools" | "lessons", key: string, value: string) => {
    if (!bundle) return;
    const container = bundle[section]; const entries = asRecords(container[collection]);
    const next = entries.length ? entries.map((item, index) => index === 0 ? { ...item, [key]: value } : item) : [{ [key]: value }];
    setBundle({ ...bundle, [section]: { ...container, [collection]: next } }); setCapstone(null);
  };

  const updateGolden = (key: string, value: string) => {
    if (!bundle) return;
    const evals = bundle.evals.length ? bundle.evals.map((item, index) => index === 0 ? { ...item, [key]: value } : item) : [{ [key]: value }];
    setBundle({ ...bundle, evals }); setCapstone(null);
  };

  const applyEditor = () => {
    if (!bundle || !activeSection) return;
    try {
      setBundle(replaceSection(bundle, activeSection, JSON.parse(editor) as unknown)); setCapstone(null); setError(null);
      setNotice(`${activeStep.label} JSON applied; unknown namespaced extensions retained.`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "JSON could not be applied"); }
  };

  const clone = async (scenarioId: string) => {
    setBusy("clone"); setError(null); setNotice(null);
    try {
      const next = await createScenarioDraft(scenarioId);
      window.history.replaceState({}, "", `/builder?draft=${encodeURIComponent(next.draft_id)}`);
      setDraft(next); setBundle(next.bundle); setImportSummary(null); setNotice(`Draft ${next.draft_id} created from ${scenarioId}.`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Clone failed"); }
    finally { setBusy(null); }
  };

  const persist = async (): Promise<ScenarioDraft> => {
    if (!draft || !bundle) throw new Error("Open a draft before saving");
    const next = await saveScenarioDraft(draft, bundle); setDraft(next); setBundle(next.bundle); return next;
  };

  const save = async () => {
    setBusy("save"); setError(null);
    try { const next = await persist(); setNotice(`Revision ${next.revision} saved with an audited idempotent command.`); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Save failed"); }
    finally { setBusy(null); }
  };

  const validate = async () => {
    if (!draft) return;
    setBusy("validate"); setError(null);
    try {
      const saved = dirty ? await persist() : draft; const nextReport = await validateScenarioDraft(saved.draft_id);
      setDraft({ ...saved, report: nextReport }); setCapstone(null);
      setNotice(nextReport.valid ? "All eight contract sections passed; Visual Lab preview is unlocked." : `${nextReport.error_count} actionable validation errors found.`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Validation failed"); }
    finally { setBusy(null); }
  };

  const preview = async () => {
    if (!draft) return;
    setBusy("preview"); setError(null);
    try {
      const saved = dirty ? await persist() : draft;
      if (!saved.report.valid) throw new Error("Repair validation errors before entering Visual Lab");
      onNavigate(`/lab/scenario-preview?source=draft&id=${encodeURIComponent(saved.draft_id)}`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Preview could not open"); setBusy(null); }
  };

  const exportDraft = async () => {
    if (!draft) return;
    setBusy("export"); setError(null);
    try {
      const saved = dirty ? await persist() : draft; const archive = await exportScenarioDraft(saved.draft_id);
      const bytes = Uint8Array.from(atob(archive.archive_base64), (character) => character.charCodeAt(0));
      download(archive.filename, bytes, archive.media_type);
      setNotice(`Exported ${archive.filename} · ${archive.byte_count} bytes · ${archive.sha256.slice(0, 12)}.`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Export failed"); }
    finally { setBusy(null); }
  };

  const importFile = async (file: File) => {
    setBusy("import"); setError(null);
    try {
      const imported = await importScenarioArchive(file.name, await fileToBase64(file));
      const requiredPermissions = Array.from(new Set(asRecords(imported.bundle.tools.tools).flatMap((tool) => {
        const declared = tool.required_permissions ?? tool.permissions;
        return Array.isArray(declared) ? declared.filter((permission): permission is string => typeof permission === "string") : [];
      }))).sort();
      window.history.replaceState({}, "", `/builder?draft=${encodeURIComponent(imported.draft_id)}`);
      setDraft(imported); setBundle(imported.bundle);
      setImportSummary({
        dataClassification: stringValue(imported.bundle.manifest.data_classification) || "undeclared",
        requiredPermissions
      });
      setNotice(`Safe archive import created ${imported.draft_id}.`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Import failed"); }
    finally { setBusy(null); if (fileRef.current) fileRef.current.value = ""; }
  };

  const buildCapstone = async () => {
    if (!draft) return;
    setBusy("capstone"); setError(null);
    try {
      const saved = dirty ? await persist() : draft;
      if (!saved.report.valid) throw new Error("Capstone generation requires a valid saved Kit");
      const next = await loadCapstoneReport(saved.draft_id); setCapstone(next);
      setNotice("Capstone architecture report generated from the saved validation and runtime evidence.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Capstone report failed"); }
    finally { setBusy(null); }
  };

  const moveStep = (index: number) => {
    const bounded = Math.max(0, Math.min(STEPS.length - 1, index)); setStep(STEPS[bounded].id);
    window.requestAnimationFrame(() => document.querySelector<HTMLButtonElement>(`[data-builder-step="${STEPS[bounded].id}"]`)?.focus());
  };
  const stepKey = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (event.key === "ArrowRight") { event.preventDefault(); moveStep(index + 1); }
    if (event.key === "ArrowLeft") { event.preventDefault(); moveStep(index - 1); }
    if (event.key === "Home") { event.preventDefault(); moveStep(0); }
    if (event.key === "End") { event.preventDefault(); moveStep(STEPS.length - 1); }
  };

  return (
    <div className="page scenario-builder-page">
      <header className="workspace-header scenario-header">
        <div><span className="machine-label">SCENARIO BUILDER // APPROVED LOCAL WRITES</span><h1>Eight-step Scenario Builder</h1><p>Clone, edit, validate, preview and export a declarative Kit without forking core runtime code.</p></div>
        <div className="builder-header-actions"><input ref={fileRef} className="visually-hidden" type="file" aria-label="Import Scenario Kit ZIP archive" accept=".zip,application/zip" onChange={(event) => { const file = event.target.files?.[0]; if (file) void importFile(file); }} /><Button loading={busy === "import"} onClick={() => fileRef.current?.click()}>Import safe ZIP</Button>{draft && <Badge tone={dirty ? "warning" : "success"}>{dirty ? "UNSAVED" : `REV ${draft.revision}`}</Badge>}</div>
      </header>
      {error && <ErrorState title="Builder boundary rejected" description={error} />}
      {notice && <p className="builder-notice" role="status">{notice}</p>}
      {importSummary && <section className="builder-import-summary" role="status" aria-label="Imported Scenario Kit safety summary"><strong>Import safety summary</strong><span>Data classification: <code>{importSummary.dataClassification}</code></span><span>Required permissions: <code>{importSummary.requiredPermissions.length ? importSummary.requiredPermissions.join(", ") : "none"}</code></span></section>}
      {!draft && <Panel title="Choose a source Kit" context="CLONE // DO NOT FORK CORE CODE"><div className="builder-source-grid">{catalog?.map((scenario) => <button key={scenario.id} onClick={() => void clone(scenario.id)} disabled={busy === "clone"}><span>{scenario.domain}</span><strong>{scenario.name}</strong><small>{scenario.id}</small></button>)}{!catalog && !error && <div className="scenario-loading"><i /><i /><i /><span>Loading validated Kit sources…</span></div>}</div></Panel>}
      {draft && bundle && <>
        <div className="builder-mobile-boundary"><strong>Mobile inspection mode</strong><p>Review, validate, preview and export remain available. Structured JSON editing moves to a wider viewport so long contracts stay legible.</p></div>
        <nav className="builder-steps" aria-label="Scenario Builder steps">{STEPS.map((item, index) => <button data-builder-step={item.id} key={item.id} aria-current={step === item.id ? "step" : undefined} tabIndex={step === item.id ? 0 : -1} onKeyDown={(event) => stepKey(event, index)} onClick={() => setStep(item.id)}><span>{String(index + 1).padStart(2, "0")}</span><strong>{item.label}</strong><small>{item.short}</small></button>)}</nav>
        <div className="builder-command-bar"><div><span className="machine-label">DRAFT</span><strong>{stringValue(identity.name) || draft.draft_id}</strong><small>{draft.draft_id} · source {draft.source_scenario_id}</small></div><Button loading={busy === "save"} onClick={() => void save()}>Save</Button><Button disabled={STEPS.findIndex((item) => item.id === step) === STEPS.length - 1} onClick={() => moveStep(STEPS.findIndex((item) => item.id === step) + 1)}>Next step</Button></div>
        <div className="builder-workbench">
          <section className="builder-section-editor">
            <header><div><span className="machine-label">STEP {String(STEPS.findIndex((item) => item.id === step) + 1).padStart(2, "0")} // {step}</span><h2>{activeStep.label}</h2></div>{activeSection && <Badge tone={currentReport?.status === "passed" ? "success" : "error"}>{currentReport?.status.toUpperCase() ?? "UNCHECKED"}</Badge>}</header>
            {step === "identity" && <div className="builder-focus-fields"><Input label="Scenario ID" value={stringValue(identity.id)} onChange={(event) => updateIdentity("id", event.target.value)} /><Input label="Display name" value={stringValue(identity.name)} onChange={(event) => updateIdentity("name", event.target.value)} /><Input label="Short description" value={stringValue(identity.short_description)} onChange={(event) => updateIdentity("short_description", event.target.value)} /></div>}
            {step === "data" && <div className="builder-evidence-summary"><p><strong>{Object.keys(bundle.data).length}</strong> JSON fixtures</p><p><strong>{bundle.resources.length}</strong> preserved resources</p><ul>{bundle.resources.map((resource) => <li key={resource.path}><code>{resource.path}</code><span>{resource.byte_count} B · {resource.sha256.slice(0, 10)}</span></li>)}</ul></div>}
            {step === "tools" && <div className="builder-focus-fields"><Input label="First tool name" value={stringValue(firstTool.name)} onChange={(event) => updateFirst("tools", "tools", "name", event.target.value)} /><Input label="Tool description" value={stringValue(firstTool.description)} onChange={(event) => updateFirst("tools", "tools", "description", event.target.value)} /></div>}
            {step === "lessons" && <div className="builder-focus-fields"><Input label="Lesson title" value={stringValue(firstLesson.title)} onChange={(event) => updateFirst("lessons", "lessons", "title", event.target.value)} /><Input label="Learning objective" value={stringValue(firstLesson.objective)} onChange={(event) => updateFirst("lessons", "lessons", "objective", event.target.value)} /></div>}
            {step === "golden" && <div className="builder-focus-fields"><Input label="Golden case ID" value={stringValue(firstGolden.id)} onChange={(event) => updateGolden("id", event.target.value)} /><Input label="Scenario reference" value={stringValue(firstGolden.scenario_id)} onChange={(event) => updateGolden("scenario_id", event.target.value)} /></div>}
            {activeSection && <><label className="builder-json-field"><span>{activeStep.label} JSON</span><textarea spellCheck={false} value={editor} onChange={(event) => setEditor(event.target.value)} /></label><span className="builder-apply-json"><Button onClick={applyEditor}>Apply JSON to draft</Button></span></>}
            {step === "validate" && <div className="builder-gate"><span className="machine-label">CONTRACT GATE</span><h3>{report?.valid ? "Ready for Visual Lab" : "Validation repairs required"}</h3><p>Policies ({asRecords(bundle.policies.rules).length}) and faults ({asRecords(bundle.faults.faults).length}) remain part of the same typed bundle and validation matrix.</p><div><Button variant="primary" loading={busy === "validate"} onClick={() => void validate()}>Validate Kit</Button><Button loading={busy === "preview"} disabled={!report?.valid || dirty} onClick={() => void preview()}>Open in Visual Lab</Button></div></div>}
            {step === "export" && <div className="builder-gate"><span className="machine-label">PORTABLE KIT + CAPSTONE</span><h3>Ship reproducible artifacts</h3><p>ZIP export preserves declared resources byte-for-byte. The architecture report is generated from this saved Kit, validation fingerprint, learning evidence and completed preview runs.</p><div><Button loading={busy === "export"} disabled={!report?.valid || dirty} onClick={() => void exportDraft()}>Export Scenario Kit</Button><Button variant="primary" loading={busy === "capstone"} disabled={!report?.valid} onClick={() => void buildCapstone()}>Generate capstone</Button></div>{capstone && <article className="capstone-report"><span>{capstone.scenario_id} · {capstone.mastery_evidence_count} mastery evidence</span><h3>{capstone.title}</h3><Button onClick={() => download(`${capstone.scenario_id}-architecture-review.md`, capstone.markdown, "text/markdown")}>Download Markdown</Button><pre tabIndex={0}>{capstone.markdown}</pre></article>}</div>}
          </section>
          <aside className="builder-validator"><header><span className="machine-label">VALIDATION MATRIX</span><h2>{report?.valid ? "Kit is previewable" : "Repair the contract"}</h2><p>{report?.valid ? "Schema and compatibility gates agree across every section." : `${report?.error_count ?? 0} errors · ${report?.warning_count ?? 0} warnings`}</p></header><ol>{report?.sections.map((section, index) => <li className={section.status === "passed" ? "is-pass" : "is-fail"} key={section.name}><span>{String(index + 1).padStart(2, "0")}</span><div><strong>{section.name}</strong><small>{section.checked} checks</small>{section.issues.map((issue) => <p key={`${issue.path}:${issue.code}`}><code>{issue.path}</code>{issue.message}<em>{issue.hint}</em></p>)}</div><b>{section.status === "passed" ? "✓" : "!"}</b></li>)}</ol><div className="builder-validator__fingerprint"><span>FINGERPRINT</span><code>{report?.fingerprint ?? "not validated"}</code></div></aside>
        </div>
      </>}
    </div>
  );
}

export function ScenarioBuilderPage({ onNavigate, safeLabMode = false }: ScenarioBuilderPageProps) {
  if (safeLabMode) {
    return (
      <div className="page scenario-builder-page">
        <header className="workspace-header scenario-header">
          <div><span className="machine-label">PUBLIC PREVIEW // READ-ONLY BOUNDARY</span><h1>Scenario Builder</h1><p>Scenario authoring is intentionally unavailable in the anonymous public workspace.</p></div>
          <Button variant="primary" onClick={() => onNavigate("/scenarios")}>Browse built-in Kits</Button>
        </header>
        <Panel title="Authoring disabled" context="SAFE LAB MODE"><p>Creating, saving, importing and exporting Scenario Kit drafts are write capabilities. Run AGENT//TRACE locally to use the full eight-step builder.</p></Panel>
      </div>
    );
  }
  return <ScenarioBuilderWorkbench onNavigate={onNavigate} />;
}
