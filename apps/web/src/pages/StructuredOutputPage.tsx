import { useEffect, useMemo, useState } from "react";

import {
  applyStructuredSchema,
  loadStructuredLessons,
  recordStructuredAssertion,
  resumeManualStructuredOutput,
  startStructuredRun,
  type StructuredLesson,
  type StructuredRun,
  type ValidationArtifact,
  validateStructuredOutput
} from "../structured-api";
import { Badge, Button, EmptyState, ErrorState, Panel, Select, Tabs } from "../ui/primitives";

type PendingAction = "load" | "validate" | "manual" | "schema" | "assertion" | null;
type ProviderName = StructuredRun["provider"];

function pretty(value: unknown): string {
  return value === undefined || value === null ? "—" : JSON.stringify(value, null, 2);
}

function outcomeLabel(kind: ValidationArtifact["result_kind"]): string {
  return {
    invalid_json: "INVALID JSON",
    schema_invalid: "SCHEMA INVALID",
    semantic_invalid: "SEMANTIC INVALID",
    passed: "PASSED"
  }[kind];
}

function artifactTone(kind: ValidationArtifact["result_kind"]): "success" | "error" | "warning" {
  return kind === "passed" ? "success" : kind === "semantic_invalid" ? "warning" : "error";
}

function Editor({ id, label, value, onChange, readOnly = false }: {
  id: string;
  label: string;
  value: string;
  onChange?: (value: string) => void;
  readOnly?: boolean;
}) {
  return (
    <label className="structured-editor" htmlFor={id}>
      <span>{label}</span>
      <textarea
        id={id}
        value={value}
        onChange={(event) => onChange?.(event.target.value)}
        readOnly={readOnly}
        rows={16}
        spellCheck={false}
      />
    </label>
  );
}

function ArtifactInspector({ artifact }: { artifact: ValidationArtifact | null }) {
  const [tab, setTab] = useState("raw");
  if (!artifact) {
    return <EmptyState title="No validation artifact" description="Choose an exercise and validate it against the active schema." />;
  }
  const errorDiff = artifact.errors?.length
    ? artifact.errors.map((error) => `− ${error.path}\n+ ${error.keyword}: ${error.message}`).join("\n\n")
    : "+ Typed payload accepted without coercion.";
  return (
    <div className="artifact-inspector">
      <div className="artifact-inspector__meta">
        <Badge tone={artifactTone(artifact.result_kind)}>{outcomeLabel(artifact.result_kind)}</Badge>
        <code>SCHEMA v{artifact.schema_version}</code>
        <code>ATTEMPT {artifact.attempt}</code>
      </div>
      <Tabs
        label="Artifact projections"
        selectedId={tab}
        onChange={setTab}
        items={[
          { id: "raw", label: "RAW", content: <pre className="structured-code">{artifact.raw_output}</pre> },
          { id: "validated", label: "VALIDATED", content: <pre className="structured-code">{pretty(artifact.validated_output)}</pre> },
          { id: "diff", label: "DIFF", content: <pre className="structured-code structured-code--diff">{errorDiff}</pre> }
        ]}
      />
      {artifact.errors?.length ? (
        <ol className="structured-errors" aria-label="Validation errors">
          {artifact.errors.map((error, index) => (
            <li key={`${error.path}-${error.keyword}-${index}`}>
              <code>{error.path}</code><strong>{error.keyword}</strong><span>{error.message}</span>
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}

export function StructuredOutputPage({ onNavigate }: { onNavigate: (path: string) => void }) {
  const [lessons, setLessons] = useState<StructuredLesson[]>([]);
  const [lessonId, setLessonId] = useState<StructuredLesson["id"]>("route-decision");
  const [provider, setProvider] = useState<ProviderName>("manual");
  const [prediction, setPrediction] = useState<"pass" | "fail">("fail");
  const [run, setRun] = useState<StructuredRun | null>(null);
  const [output, setOutput] = useState("");
  const [schemaText, setSchemaText] = useState("");
  const [schemaVersion, setSchemaVersion] = useState("1.1.0");
  const [schemaFeedback, setSchemaFeedback] = useState<string | null>(null);
  const [selectedArtifact, setSelectedArtifact] = useState(0);
  const [pending, setPending] = useState<PendingAction>("load");
  const [error, setError] = useState<string | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);

  const lesson = useMemo(
    () => lessons.find((item) => item.id === lessonId) ?? null,
    [lessonId, lessons]
  );
  const artifact = run?.artifacts[selectedArtifact] ?? run?.artifacts.at(-1) ?? null;
  const latestRevision = run?.schema_history.at(-1) ?? null;

  useEffect(() => {
    let active = true;
    void loadStructuredLessons()
      .then((next) => {
        if (!active) return;
        setLessons(next);
        const first = next[0];
        if (first) {
          setLessonId(first.id);
          setSchemaText(JSON.stringify(first.default_schema, null, 2));
          setOutput(first.exercises[0]?.raw_output ?? first.valid_output);
        }
        setPending(null);
      })
      .catch((reason: unknown) => {
        if (active) {
          setError(reason instanceof Error ? reason.message : "Structured lessons failed to load");
          setPending(null);
        }
      });
    return () => { active = false; };
  }, [loadAttempt]);

  const replaceRunPlaceholder = (raw: string, activeRun: StructuredRun) => raw.replaceAll("run_PLACEHOLDER", activeRun.run_id);

  const beginRun = async (allowExisting = true): Promise<StructuredRun> => {
    if (allowExisting && run && run.status !== "failed" && run.status !== "passed") return run;
    const created = await startStructuredRun(lessonId, provider, prediction);
    setRun(created);
    return created;
  };

  const perform = async (action: Exclude<PendingAction, "load" | null>, task: () => Promise<void>) => {
    setPending(action);
    setError(null);
    try {
      await task();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Structured command failed");
    } finally {
      setPending(null);
    }
  };

  const resetForLesson = (nextId: StructuredLesson["id"]) => {
    const next = lessons.find((item) => item.id === nextId);
    setLessonId(nextId);
    setRun(null);
    setSelectedArtifact(0);
    setSchemaFeedback(null);
    if (next) {
      setSchemaText(JSON.stringify(next.default_schema, null, 2));
      setOutput(next.exercises[0]?.raw_output ?? next.valid_output);
    }
  };

  const chooseExercise = (exercise: StructuredLesson["exercises"][number]) => {
    setOutput(exercise.raw_output);
    setSchemaFeedback(null);
    if (exercise.schema_document) {
      setSchemaText(JSON.stringify(exercise.schema_document, null, 2));
      setSchemaVersion("2.0.0");
    }
  };

  const validate = () => void perform("validate", async () => {
    const activeRun = await beginRun();
    const resolvedOutput = replaceRunPlaceholder(output, activeRun);
    setOutput(resolvedOutput);
    const next = await validateStructuredOutput(activeRun.run_id, resolvedOutput);
    setRun(next);
    setSelectedArtifact(next.artifacts.length - 1);
  });

  const useValidRepair = () => {
    if (lesson && run) setOutput(replaceRunPlaceholder(lesson.valid_output, run));
  };

  const resumeManual = () => void perform("manual", async () => {
    if (!run?.pending_interrupt) throw new Error("No typed manual interrupt is pending");
    const next = await resumeManualStructuredOutput(
      run.run_id,
      run.pending_interrupt.interrupt_id,
      replaceRunPlaceholder(output, run)
    );
    setRun(next);
    setSelectedArtifact(next.artifacts.length - 1);
  });

  const applySchema = () => void perform("schema", async () => {
    const activeRun = run ?? await beginRun();
    const result = await applyStructuredSchema(activeRun.run_id, schemaVersion, schemaText);
    setRun(result.run);
    setSchemaFeedback(result.applied
      ? `Schema ${schemaVersion} activated; historical artifacts remain pinned.`
      : `${result.check.kind}: ${result.check.errors?.map((item) => item.message).join("; ")}`);
  });

  const recordAssertion = () => void perform("assertion", async () => {
    if (!run) throw new Error("No structured run exists");
    setRun(await recordStructuredAssertion(run.run_id));
  });

  const retryLessons = () => {
    setError(null);
    setPending("load");
    setLoadAttempt((attempt) => attempt + 1);
  };

  if (pending === "load") {
    return <div className="page structured-page"><div className="structured-loading" aria-live="polite"><i /><i /><i /><span>Loading typed lessons from the local runtime…</span></div></div>;
  }
  if (!lesson) {
    return <div className="page structured-page"><ErrorState title="Structured lessons unavailable" description={error ?? "The lesson envelope was empty."} action={<Button variant="primary" onClick={retryLessons}>Retry lessons</Button>} /></div>;
  }

  return (
    <div className="page structured-page">
      <header className="workspace-header structured-header">
        <div>
          <span className="machine-label">LAB 03 // STRUCTURED OUTPUT BOUNDARY</span>
          <h1>Schema pressure bench</h1>
          <p>Predict the failure, inspect the exact boundary, then repair one payload without rewriting history.</p>
        </div>
        <div className="structured-header__actions">
          <Badge tone={run?.progress.completed ? "success" : run ? "active" : "neutral"}>{run?.progress.completed ? "COURSE COMPLETE" : run?.status.toUpperCase() ?? "NOT STARTED"}</Badge>
          <Button variant="ghost" onClick={() => onNavigate("/lab")}>Runtime X-ray</Button>
        </div>
      </header>

      {error ? <div className="runtime-error" role="alert"><strong>STRUCTURED BOUNDARY ERROR</strong><div><span>{error}</span><small>The last validated run projection was preserved.</small></div></div> : null}

      <section className="structured-controls" aria-label="Lesson controls">
        <Select label="LESSON" value={lessonId} onChange={(event) => resetForLesson(event.target.value as StructuredLesson["id"])} disabled={pending !== null}>
          {lessons.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
        </Select>
        <Select label="PROVIDER" value={provider} onChange={(event) => { setProvider(event.target.value as ProviderName); setRun(null); }} disabled={pending !== null}>
          <option value="manual">Manual / interrupt</option><option value="mock">Mock / one retry</option>
        </Select>
        <Select label="PREDICTION" value={prediction} onChange={(event) => { setPrediction(event.target.value as "pass" | "fail"); setRun(null); }} disabled={pending !== null}>
          <option value="fail">This payload will fail</option><option value="pass">This payload will pass</option>
        </Select>
        <div className="structured-controls__readout"><span>DOMAIN</span><strong>{lesson.domain.toUpperCase()}</strong><small>{lesson.objective}</small></div>
      </section>

      <section className="exercise-strip" aria-label="Contract exercises">
        <span className="machine-label">LOAD A FAILURE PROBE</span>
        <div>{lesson.exercises.map((exercise) => (
          <Button key={exercise.id} variant={exercise.id === "breaking" ? "danger" : "secondary"} onClick={() => chooseExercise(exercise)} disabled={pending !== null}>{exercise.label}</Button>
        ))}</div>
      </section>

      <div className="structured-workbench">
        <Panel
          title="Schema editor"
          context={`JSON SCHEMA 2020-12 // ACTIVE ${run?.active_schema_version ?? "1.0.0"}`}
          className="structured-schema-panel"
          actions={<Badge tone={latestRevision?.breaking ? "error" : "success"}>{latestRevision?.breaking ? "BREAKING" : "VALID SCHEMA"}</Badge>}
        >
          <Editor id="schema-editor" label="SCHEMA DOCUMENT" value={schemaText} onChange={setSchemaText} />
          <div className="schema-commandbar">
            <label htmlFor="schema-version"><span>NEW VERSION</span><input id="schema-version" value={schemaVersion} onChange={(event) => setSchemaVersion(event.target.value)} pattern="\d+\.\d+\.\d+" /></label>
            <Button onClick={applySchema} loading={pending === "schema"}>Validate + activate</Button>
          </div>
          <p className="schema-feedback" role="status">{schemaFeedback ?? "Syntax and meta-schema validation run before a revision can become active."}</p>
          <ol className="schema-history" aria-label="Immutable schema history">
            {(run?.schema_history ?? []).map((revision) => (
              <li key={revision.version}>
                <Badge tone={revision.breaking ? "error" : "success"}>v{revision.version}</Badge>
                <code>{revision.schema_hash.slice(0, 12)}</code>
                <span>{revision.compatibility?.map((change) => change.message).join(" ") || "Initial contract."}</span>
              </li>
            ))}
          </ol>
        </Panel>

        <Panel
          title="Output boundary"
          context={`${provider.toUpperCase()} PROVIDER // ${run?.run_id ?? "RUN NOT CREATED"}`}
          className="structured-output-panel"
          actions={<Button variant="primary" onClick={validate} loading={pending === "validate"}>Validate output</Button>}
        >
          <Editor id="output-editor" label="PROVIDER OUTPUT" value={output} onChange={setOutput} />
          {run?.pending_interrupt ? (
            <div className="manual-interrupt" role="status">
              <div><strong>MANUAL INTERRUPT</strong><code>{run.pending_interrupt.interrupt_id}</code><span>Same run · expected schema {run.pending_interrupt.expected_schema_version}</span></div>
              <div><Button onClick={useValidRepair}>Load valid repair</Button><Button variant="primary" onClick={resumeManual} loading={pending === "manual"}>Submit manual payload</Button></div>
            </div>
          ) : null}
          {run?.artifacts.length ? (
            <div className="artifact-selector" aria-label="Validation attempts">{run.artifacts.map((item, index) => (
              <button key={item.artifact_id} type="button" aria-pressed={artifact?.artifact_id === item.artifact_id} onClick={() => setSelectedArtifact(index)}>
                <span>{String(item.attempt).padStart(2, "0")}</span><strong>{outcomeLabel(item.result_kind)}</strong><small>schema {item.schema_version}</small>
              </button>
            ))}</div>
          ) : null}
          <ArtifactInspector artifact={artifact} />
        </Panel>
      </div>

      <div className="structured-evidence-grid">
        <Panel title="Repair trace" context={`${run?.repair_trace.length ?? 0} EVENTS // BOUNDED`} tone="console">
          {run?.repair_trace.length ? <ol className="repair-trace">{run.repair_trace.map((event) => (
            <li key={event.sequence}><span>{String(event.sequence).padStart(2, "0")}</span><strong>{event.type}</strong><p>{event.message}</p></li>
          ))}</ol> : <EmptyState title="No repair events" description="Validation events appear only after a real API command." />}
        </Panel>
        <Panel title="Course assertion" context="PREDICT → FAIL → REPAIR → ASSERT" className="course-panel">
          <ul className="course-gates">
            <li data-complete={Boolean(run?.progress.prediction_recorded)}><span>01</span><strong>Prediction recorded</strong></li>
            <li data-complete={Boolean(run?.progress.failure_observed)}><span>02</span><strong>Failure observed</strong></li>
            <li data-complete={Boolean(run?.progress.repair_completed)}><span>03</span><strong>Repair crossed boundary</strong></li>
            <li data-complete={Boolean(run?.progress.assertion_recorded)}><span>04</span><strong>State-safety asserted</strong></li>
          </ul>
          <Button onClick={recordAssertion} disabled={run?.status !== "passed" || !run.progress.failure_observed || !run.progress.repair_completed || Boolean(run.progress.assertion_recorded)} loading={pending === "assertion"}>Assert safe for state</Button>
          <p aria-live="polite">{run?.progress.completed ? "Course complete: all four event-backed gates passed." : "Completion remains locked until every gate is backed by this run."}</p>
        </Panel>
      </div>
    </div>
  );
}
