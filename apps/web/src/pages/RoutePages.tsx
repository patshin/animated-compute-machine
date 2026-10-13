import type { ReactNode } from "react";

import type { CapabilitiesResponse, HealthResponse } from "../api";
import { Badge, Button, EmptyState, ErrorState, Panel } from "../ui/primitives";
import { PixelIcon, type PixelIconName } from "../ui/PixelIcon";

interface RoutePageProps { onNavigate: (path: string) => void; }

function PageFrame({ code, title, description, icon, children }: { code: string; title: string; description: string; icon: PixelIconName; children: ReactNode }) {
  return <div className="page route-page"><header className="workspace-header"><div><span className="machine-label">{code}</span><h1>{title}</h1><p>{description}</p></div><span className="page-icon" aria-hidden="true"><PixelIcon name={icon} size={30} /></span></header>{children}</div>;
}

export function RunsPage({ onNavigate }: RoutePageProps) {
  return <PageFrame code="TRACE + COMPARE // LOCAL STORE" title="Runs" description="Replay node trajectories, state patches, tools and evaluations—not just final text." icon="runs"><Panel title="Run ledger" context="DEFERRED // BATCH A"><EmptyState title="Run ledger is not part of Batch A" description="Any canonical run and persisted event stream created in this Batch remains inspectable in the Lab. This page does not claim that persisted runs are absent." action={<Button variant="primary" onClick={() => onNavigate("/lab")}>Open the Lab</Button>} /></Panel></PageFrame>;
}

export function GlossaryPage() {
  const terms = [["Canonical event", "The versioned runtime fact from which every UI state is projected."], ["Typed decision", "A model proposal that crosses schema and semantic validation before routing."], ["Side effect", "A state-changing action that requires permission, approval, idempotency and audit."], ["Evidence", "A source-bound claim input with authority, score, freshness and citation identity."]];
  return <PageFrame code="CONCEPTS + DECISIONS // GLOSSARY" title="Glossary" description="A shared language for tracing responsibility across model, runtime, tool and human boundaries." icon="glossary"><div className="glossary-grid">{terms.map(([term, meaning], index) => <article key={term}><span>{String(index + 1).padStart(2, "0")}</span><h2>{term}</h2><p>{meaning}</p></article>)}</div></PageFrame>;
}

interface SettingsPageProps {
  status: { health: HealthResponse; capabilities: CapabilitiesResponse } | null;
  error: string | null;
  onRetry: () => void;
}

export function SettingsPage({ status, error, onRetry }: SettingsPageProps) {
  return <PageFrame code="SETTINGS // LOCAL RUNTIME" title="Settings" description="Inspect the repository-owned runtime without changing global tools or credentials." icon="settings"><div className="settings-grid"><Panel title="Runtime boundary" context="READ ONLY STATUS" tone="console">{error ? <ErrorState title="API unavailable" description={error} action={<Button variant="console" onClick={onRetry}>Check again</Button>} /> : status ? <dl className="settings-list"><div><dt>Service</dt><dd>{status.health.service}</dd></div><div><dt>Version</dt><dd>{status.health.version}</dd></div><div><dt>Python</dt><dd>{status.capabilities.python_version}</dd></div><div><dt>Provider</dt><dd><Badge tone="success">{status.capabilities.providers.join(", ")}</Badge></dd></div></dl> : <div className="runtime-checking"><i /><i /><i /><span>Checking repository runtime…</span></div>}</Panel><Panel title="Safety defaults" context="NON-NEGOTIABLE"><ul className="safety-list"><li><span>01</span>No API key required</li><li><span>02</span>Network off by default</li><li><span>03</span>Writes require approval</li><li><span>04</span>Mock path is deterministic</li></ul></Panel></div></PageFrame>;
}

export function NotFoundPage({ onNavigate }: RoutePageProps) {
  return <PageFrame code="404 // ROUTE" title="Workspace not found" description="This local route is not registered." icon="runs"><ErrorState title="Unknown route" description={window.location.pathname} action={<Button variant="primary" onClick={() => onNavigate("/")}>Return home</Button>} /></PageFrame>;
}
