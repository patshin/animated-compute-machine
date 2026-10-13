import { useEffect, useMemo, useState } from "react";

import {
  executeTool,
  loadToolCatalog,
  type ActorRole,
  type ToolCatalog,
  type ToolCatalogEntry,
  type ToolFaultCase,
  type ToolLabRun,
  type ToolRisk
} from "../tool-api";
import { Badge, Button, Drawer, EmptyState, ErrorState, Panel, Select, Tabs } from "../ui/primitives";

type Pending = "catalog" | "execute" | null;
type InspectorTab = "contract" | "request" | "result" | "audit";

const PIPELINE = [
  ["resolve", "RESOLVE"],
  ["input_validation", "INPUT"],
  ["authorize", "AUTH"],
  ["policy", "POLICY"],
  ["approval", "APPROVAL"],
  ["idempotency", "IDEMPOTENCY"],
  ["execute", "EXECUTE"],
  ["output_validation", "OUTPUT"],
  ["redact", "REDACT"],
  ["audit", "AUDIT"]
] as const;

function pretty(value: unknown): string {
  return value === undefined || value === null ? "—" : JSON.stringify(value, null, 2);
}

function riskTone(risk: ToolRisk): "active" | "success" | "warning" | "error" | "neutral" {
  if (risk === "read") return "success";
  if (risk === "compute") return "active";
  if (risk === "propose") return "warning";
  if (risk === "write" || risk === "restricted") return "error";
  return "neutral";
}

function runTone(run: ToolLabRun | null): "active" | "success" | "warning" | "error" | "neutral" {
  if (!run) return "neutral";
  if (run.status === "succeeded") return "success";
  if (run.status === "approval_required") return "warning";
  return "error";
}

function ContractSummary({ spec }: { spec: ToolCatalogEntry["spec"] }) {
  return (
    <section className="tool-contract-summary" aria-label={`Resolved contract ${spec.name}@${spec.version}`}>
      <dl>
        <div><dt>RISK</dt><dd><Badge tone={riskTone(spec.risk)}>{spec.risk.toUpperCase()}</Badge></dd></div>
        <div><dt>PERMISSIONS</dt><dd>{spec.permissions.join(", ")}</dd></div>
        <div><dt>HARD TIMEOUT</dt><dd>{spec.timeout_ms} ms</dd></div>
        <div><dt>RETRY</dt><dd>{spec.risk === "read" ? `${spec.retry.max_attempts} bounded attempts` : "Never automatic"}</dd></div>
        <div><dt>IDEMPOTENCY</dt><dd>{spec.idempotency.mode.replaceAll("_", " ")}</dd></div>
        <div><dt>REDACTION</dt><dd>{spec.redact.length ? spec.redact.join(", ") : "No configured paths"}</dd></div>
      </dl>
      <details>
        <summary>INPUT SCHEMA</summary>
        <pre className="tool-code">{pretty(spec.input_schema)}</pre>
      </details>
      <details>
        <summary>OUTPUT SCHEMA</summary>
        <pre className="tool-code">{pretty(spec.output_schema)}</pre>
      </details>
      <details>
        <summary>DECLARED ERRORS</summary>
        <ul className="tool-error-list">
          {spec.errors.map((error) => (
            <li key={error.code}><code>{error.code}</code><span>{error.user_message}</span><small>{error.retryable ? "RETRYABLE" : "TERMINAL"}</small></li>
          ))}
        </ul>
      </details>
    </section>
  );
}

function ApprovalBoundary({ run }: { run: ToolLabRun }) {
  const approval = run.result.approval;
  if (!approval) return null;
  return (
    <section className="approval-boundary" aria-label="Approval requirement">
      <header><Badge tone="error">WRITE LOCKED</Badge><strong>Explicit approval boundary</strong></header>
      <p>{approval.reason}</p>
      <dl>
        <div><dt>EXACT ACTION</dt><dd>{approval.exact_action}</dd></div>
        <div><dt>TARGET</dt><dd>{approval.exact_target}</dd></div>
        <div><dt>RISK</dt><dd>{approval.risk.toUpperCase()}</dd></div>
        <div><dt>KEY SUMMARY</dt><dd><code>{approval.idempotency_key_summary}</code></dd></div>
        <div><dt>EXPIRES</dt><dd>{new Date(approval.expires_at).toISOString()}</dd></div>
      </dl>
      <pre className="tool-code">{pretty(approval.args_diff)}</pre>
      <div className="approval-evidence">
        <section><strong>EVIDENCE</strong><ul>{approval.evidence.map((item) => <li key={item}>{item}</li>)}</ul></section>
        <section><strong>POTENTIAL SIDE EFFECTS</strong><ul>{approval.side_effects.map((item) => <li key={item}>{item}</li>)}</ul></section>
      </div>
      <div className="approval-actions" aria-label="Actions reserved for E7">
        {approval.available_actions.map((action) => <span key={action}>{action.replaceAll("_", " ").toUpperCase()} · E7</span>)}
      </div>
      <small>Nothing was executed. No side-effect receipt exists.</small>
    </section>
  );
}

function ResultProjection({ run }: { run: ToolLabRun }) {
  const issues = run.result.error?.issues ?? [];
  return (
    <div className="tool-result-projection">
      <div className="tool-result-projection__header">
        <Badge tone={runTone(run)}>{run.status.replaceAll("_", " ").toUpperCase()}</Badge>
        <span>{run.execution_label}</span>
        <code>{run.result.duration_ms} ms</code>
      </div>
      {run.result.error ? (
        <section className="tool-failure" role="alert">
          <strong>{run.result.error.code}</strong>
          <p>{run.result.error.user_safe_message}</p>
          <span>FAILED AT {run.result.error.stage.toUpperCase()} · {run.result.error.retryable ? "RETRY MAY BE OFFERED" : "NO AUTOMATIC RETRY"}</span>
          {issues.length ? <ol>{issues.map((issue, index) => (
            <li key={`${issue.path}-${issue.keyword}-${index}`}><code>{issue.path}</code><b>{issue.keyword}</b><span>{issue.message}</span></li>
          ))}</ol> : null}
        </section>
      ) : (
        <pre className="tool-code tool-code--result">{pretty(run.result.data)}</pre>
      )}
      {run.result.attempts.length ? (
        <ol className="tool-attempts" aria-label="Execution attempts">
          {run.result.attempts.map((attempt) => (
            <li key={attempt.attempt}>
              <span>ATTEMPT {String(attempt.attempt).padStart(2, "0")}</span>
              <Badge tone={attempt.status === "succeeded" ? "success" : "error"}>{attempt.status}</Badge>
              <code>{attempt.error_code ?? "VALIDATED"}</code>
              <small>{attempt.duration_ms} ms</small>
            </li>
          ))}
        </ol>
      ) : null}
      <div className="tool-receipt-strip">
        <span>PROVENANCE <code>{run.result.provenance}</code></span>
        <span>REDACTION <strong>{run.result.redaction_summary.applied ? run.result.redaction_summary.paths.join(", ") : "NONE"}</strong></span>
        <span>SIDE EFFECT <strong>{run.result.side_effect_receipt == null ? "NO RECEIPT" : "RECORDED"}</strong></span>
        <span>CACHE <strong>{run.result.cache_status.replaceAll("_", " ").toUpperCase()}</strong></span>
      </div>
      <ApprovalBoundary run={run} />
    </div>
  );
}

function AuditView({ run }: { run: ToolLabRun | null }) {
  if (!run) return <EmptyState title="No audit trail" description="Execute a registered example to project each real policy stage." />;
  return (
    <div>
      <dl className="tool-audit-context">
        <div><dt>POLICY</dt><dd>{run.audit[0]?.policy_version}</dd></div>
        <div><dt>ACTOR</dt><dd>{run.request.actor_role}</dd></div>
        <div><dt>ACTION</dt><dd>{run.request.tool_name}</dd></div>
        <div><dt>TRACE</dt><dd>{run.request.correlation_id}</dd></div>
      </dl>
      <ol className="tool-audit-trail" aria-label="Tool execution audit">
        {run.audit.map((entry) => (
          <li className={`is-${entry.status}`} key={entry.sequence}>
            <span>{String(entry.sequence).padStart(2, "0")}</span>
            <i aria-hidden="true" />
            <strong>{entry.stage.replaceAll("_", " ")}</strong>
            <p>{entry.message}</p>
            <Badge tone={entry.status === "failed" || entry.status === "blocked" ? "error" : entry.status === "passed" ? "success" : "neutral"}>{entry.status}</Badge>
          </li>
        ))}
      </ol>
    </div>
  );
}

function Inspector({ entry, run, tab, onTab }: {
  entry: ToolCatalogEntry;
  run: ToolLabRun | null;
  tab: InspectorTab;
  onTab: (tab: InspectorTab) => void;
}) {
  const resolvedContract = run ? run.spec : entry.spec;
  const items = [
    {
      id: "contract",
      label: "CONTRACT",
      content: resolvedContract
        ? <ContractSummary spec={resolvedContract} />
        : <EmptyState title="No resolved contract" description="The provider proposal failed the registry allowlist before a ToolSpec could be attached." />
    },
    { id: "request", label: "REQUEST", content: run ? <pre className="tool-code">{pretty(run.request)}</pre> : <EmptyState title="No typed request" description="The provider proposal appears here only after the API returns a validated envelope." /> },
    { id: "result", label: "RESULT", content: run ? <ResultProjection run={run} /> : <EmptyState title="No tool result" description="Select a registry entry and execute its bounded example." /> },
    { id: "audit", label: "AUDIT", content: <AuditView run={run} /> }
  ];
  return <Tabs label="Tool inspector views" items={items} selectedId={tab} onChange={(id) => onTab(id as InspectorTab)} />;
}

export function ToolCallingPage({ onNavigate }: { onNavigate: (path: string) => void }) {
  const [catalog, setCatalog] = useState<ToolCatalog | null>(null);
  const [selectedKey, setSelectedKey] = useState("");
  const [role, setRole] = useState<ActorRole>("learner");
  const [fault, setFault] = useState<ToolFaultCase["id"]>("none");
  const [run, setRun] = useState<ToolLabRun | null>(null);
  const [pending, setPending] = useState<Pending>("catalog");
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("contract");
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    let active = true;
    setPending("catalog");
    setError(null);
    void loadToolCatalog()
      .then((next) => {
        if (!active) return;
        setCatalog(next);
        setSelectedKey(`${next.tools[0].spec.name}@${next.tools[0].spec.version}`);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "Tool catalog failed to load");
      })
      .finally(() => {
        if (active) setPending(null);
      });
    return () => {
      active = false;
    };
  }, [revision]);

  const entry = useMemo(() => catalog?.tools.find((tool) => `${tool.spec.name}@${tool.spec.version}` === selectedKey) ?? null, [catalog, selectedKey]);
  const selectedFault = catalog?.faults.find((item) => item.id === fault) ?? null;
  const projectedProposal = run?.request ?? (entry ? {
    tool_name: entry.spec.name,
    tool_version: entry.spec.version,
    arguments: entry.example_arguments,
    actor_role: role,
    expected_risk: entry.spec.risk
  } : null);
  const contractDiverged = Boolean(run && entry
    && (run.request.tool_name !== entry.spec.name || run.request.tool_version !== entry.spec.version));

  const chooseTool = (next: ToolCatalogEntry) => {
    if (pending !== null) return;
    setSelectedKey(`${next.spec.name}@${next.spec.version}`);
    setRole(next.spec.risk === "write" ? "approver" : "learner");
    setFault("none");
    setRun(null);
    setError(null);
    setInspectorTab("contract");
  };

  const execute = async () => {
    if (!entry) return;
    setPending("execute");
    setError(null);
    try {
      const next = await executeTool(entry, role, fault);
      setRun(next);
      setInspectorTab(next.status === "succeeded" ? "result" : "audit");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Tool execution failed");
    } finally {
      setPending(null);
    }
  };

  if (pending === "catalog") {
    return <div className="page tool-page"><div className="tool-loading" aria-live="polite"><span>REGISTRY</span><i /><span>SCHEMAS</span><i /><span>POLICY</span><p>Loading the local typed tool registry…</p></div></div>;
  }
  if (!catalog || !entry) {
    return <div className="page tool-page"><ErrorState title="Tool registry unavailable" description={error ?? "The registry returned no executable entries."} action={<Button variant="primary" onClick={() => setRevision((value) => value + 1)}>Retry registry</Button>} /></div>;
  }

  return (
    <div className="page tool-page">
      <header className="workspace-header tool-header">
        <div><span className="machine-label">LAB 04 // TOOL CALLING BOUNDARY</span><h1>Permission circuit bench</h1><p>Watch a typed provider proposal cross registry, schema, permission, timeout, redaction and audit code.</p></div>
        <div className="tool-header__actions"><Badge tone={runTone(run)}>{run?.status.replaceAll("_", " ").toUpperCase() ?? "READY"}</Badge><Button variant="ghost" onClick={() => onNavigate("/lab")}>Runtime X-ray</Button></div>
      </header>

      {error ? <div className="runtime-error" role="alert"><strong>TOOL BOUNDARY ERROR</strong><div><span>{error}</span><small>The last valid tool projection was preserved.</small></div></div> : null}

      <section className="tool-registry" aria-label="Registered tools">
        <header><span className="machine-label">ALLOWLIST // {catalog.tools.length} VERSIONED TOOLS</span><p>The provider may propose a name and arguments; only these code-owned handlers can run.</p></header>
        <div>{catalog.tools.map((tool, index) => {
          const key = `${tool.spec.name}@${tool.spec.version}`;
          return (
            <button aria-pressed={key === selectedKey} className="tool-registry-card" disabled={pending !== null} key={key} onClick={() => chooseTool(tool)} type="button">
              <span>{String(index + 1).padStart(2, "0")}</span><div><strong>{tool.spec.name}</strong><small>{tool.domain} · v{tool.spec.version}</small></div><Badge tone={riskTone(tool.spec.risk)}>{tool.spec.risk}</Badge>
            </button>
          );
        })}</div>
      </section>

      <section className="tool-commandbar" aria-label="Tool execution controls">
        <div className="tool-commandbar__identity">
          <span>{contractDiverged ? "SELECTED BASELINE" : "SELECTED CONTRACT"}</span>
          <strong>{entry.spec.name}<small>@{entry.spec.version}</small></strong>
          <p>{contractDiverged
            ? run?.spec
              ? `Probe request resolved ${run.spec.name}@${run.spec.version}; Inspector follows the returned registry contract.`
              : `Probe proposed ${run?.request.tool_name}@${run?.request.tool_version}; registry rejected it, so no contract resolved.`
            : entry.spec.description}</p>
        </div>
        <Select label="ACTOR ROLE" value={role} onChange={(event) => { setRole(event.target.value as ActorRole); setRun(null); }} disabled={pending !== null}>
          <option value="learner">Learner</option><option value="reviewer">Reviewer</option><option value="approver">Approver</option><option value="system">System</option>
        </Select>
        <Select label="FAULT PROBE" value={fault} onChange={(event) => { setFault(event.target.value as ToolFaultCase["id"]); setRun(null); }} disabled={pending !== null}>
          {catalog.faults.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
        </Select>
        <div className="tool-commandbar__action"><Button variant="primary" onClick={() => void execute()} loading={pending === "execute"}>Execute registered example</Button><small>{selectedFault?.description}</small></div>
      </section>

      <div className="tool-workbench">
        <div>
          <Panel title="Execution circuit" context="LLM PROPOSAL → CODE-OWNED HANDLER" actions={<div className="tool-trust-labels"><Badge tone="model">LLM PROPOSED</Badge><span aria-hidden="true">→</span><Badge tone={run?.execution_label === "CODE EXECUTED" ? "evidence" : "neutral"}>{run?.execution_label ?? "CODE WAITING"}</Badge></div>}>
            <section className="tool-proposal-card" aria-label="Provider proposal">
              <span>MODEL-LIKE DECISION</span>
              <strong>{projectedProposal?.tool_name}<small>@{projectedProposal?.tool_version}</small></strong>
              <pre>{pretty(projectedProposal?.arguments)}</pre>
              <small>ACTOR {projectedProposal?.actor_role.toUpperCase()} · EXPECTED RISK {projectedProposal?.expected_risk.toUpperCase()} · rationale and risk claims remain untrusted until code checks the registered spec.</small>
            </section>
            <ol className="tool-pipeline" aria-label="Safe tool execution stages">
              {PIPELINE.map(([stage, label], index) => {
                const event = run?.audit.find((item) => item.stage === stage);
                return <li className={event ? `is-${event.status}` : "is-waiting"} key={stage}><span>{String(index + 1).padStart(2, "0")}</span><i aria-hidden="true" /><strong>{label}</strong><small>{event?.status.toUpperCase() ?? "WAITING"}</small></li>;
              })}
            </ol>
            {run ? <ResultProjection run={run} /> : <EmptyState title="Circuit armed, no execution yet" description="Run the registered example or select a controlled fault probe. No event animation is simulated." />}
          </Panel>
        </div>
        <aside className="tool-inspector-desktop" aria-label="Tool contract inspector"><Panel title="Tool Inspector" context="SCHEMA + REQUEST + RESULT + AUDIT"><Inspector entry={entry} run={run} tab={inspectorTab} onTab={setInspectorTab} /></Panel></aside>
      </div>

      <Button className="tool-inspector-trigger" variant="secondary" onClick={() => setDrawerOpen(true)}>Open contract inspector</Button>
      <Drawer open={drawerOpen} onClose={() => setDrawerOpen(false)} title="Tool Inspector"><Inspector entry={entry} run={run} tab={inspectorTab} onTab={setInspectorTab} /></Drawer>
    </div>
  );
}
