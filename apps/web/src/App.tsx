import { useCallback, useEffect, useMemo, useState } from "react";

import { navigationItems, routeMatches } from "./app-data";
import { loadSystemStatus, type CapabilitiesResponse, type HealthResponse } from "./api";
import { ComponentCatalog } from "./pages/ComponentCatalog";
import { DurableRuntimePage } from "./pages/DurableRuntimePage";
import { EvidenceMemoryPage } from "./pages/EvidenceMemoryPage";
import { HomePage } from "./pages/HomePage";
import { HumanApprovalPage } from "./pages/HumanApprovalPage";
import { LabPage } from "./pages/LabPage";
import { ObservabilityEvalPage } from "./pages/ObservabilityEvalPage";
import { ProtocolDeckPage } from "./pages/ProtocolDeckPage";
import { GlossaryPage, NotFoundPage, RunsPage, SettingsPage } from "./pages/RoutePages";
import { ScenarioBuilderPage } from "./pages/ScenarioBuilderPage";
import { ScenarioLibraryPage } from "./pages/ScenarioLibraryPage";
import { ScenarioPreviewPage } from "./pages/ScenarioPreviewPage";
import { SecurityAttackLabPage } from "./pages/SecurityAttackLabPage";
import { StructuredOutputPage } from "./pages/StructuredOutputPage";
import { ToolCallingPage } from "./pages/ToolCallingPage";
import { AppShell } from "./ui/AppShell";
import { Button, Dialog, Input, Toast } from "./ui/primitives";

type SystemStatus = { health: HealthResponse; capabilities: CapabilitiesResponse };

function useSystemStatus() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    setStatus(null);
    setError(null);
    const controller = new AbortController();
    void loadSystemStatus(controller.signal).then((next) => { if (active) setStatus(next); }).catch((reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : "Unknown health check failure");
    });
    return () => { active = false; };
  }, [revision]);
  return { status, error, retry: () => setRevision((value) => value + 1) };
}

export function App() {
  const [path, setPath] = useState(window.location.pathname);
  const [commandOpen, setCommandOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [commandQuery, setCommandQuery] = useState("");
  const [toast, setToast] = useState<string | null>(null);
  const { status, error, retry } = useSystemStatus();

  const navigate = useCallback((nextPath: string) => {
    window.history.pushState({}, "", nextPath);
    setPath(new URL(nextPath, window.location.origin).pathname);
    window.scrollTo({ top: 0, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }, []);

  useEffect(() => {
    const onPopState = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    const onShortcut = (event: KeyboardEvent) => {
      const target = event.target;
      const isEditing = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setCommandOpen(true);
      } else if (!isEditing && event.key === "?") {
        event.preventDefault();
        setHelpOpen(true);
      }
    };
    window.addEventListener("keydown", onShortcut);
    return () => window.removeEventListener("keydown", onShortcut);
  }, []);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 2200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const filteredCommands = useMemo(() => navigationItems.filter((item) => `${item.label} ${item.description}`.toLowerCase().includes(commandQuery.toLowerCase())), [commandQuery]);
  const safeLabMode = status?.capabilities.safe_lab_mode ?? true;
  const route = (() => {
    if (import.meta.env.DEV && path === "/__components") return <ComponentCatalog />;
    if (path === "/") return <HomePage status={status} error={error} onRetry={retry} onNavigate={navigate} safeLabMode={safeLabMode} />;
    if (path === "/lab/tool-calling") return <ToolCallingPage onNavigate={navigate} />;
    if (path === "/lab/structured-output") return <StructuredOutputPage onNavigate={navigate} />;
    if (path === "/lab/durable-runtime") return <DurableRuntimePage onNavigate={navigate} />;
    if (path === "/lab/evidence-memory") return <EvidenceMemoryPage onNavigate={navigate} />;
    if (path === "/lab/human-approval") return <HumanApprovalPage onNavigate={navigate} />;
    if (path === "/lab/security") return <SecurityAttackLabPage onNavigate={navigate} />;
    if (path === "/lab/scenario-preview") return <ScenarioPreviewPage onNavigate={navigate} />;
    if (routeMatches(path, "/lab")) return <LabPage onNavigate={navigate} />;
    if (routeMatches(path, "/scenarios")) return <ScenarioLibraryPage onNavigate={navigate} safeLabMode={safeLabMode} />;
    if (routeMatches(path, "/runs")) return <RunsPage onNavigate={navigate} />;
    if (routeMatches(path, "/evals")) return <ObservabilityEvalPage onNavigate={navigate} />;
    if (routeMatches(path, "/protocols")) return <ProtocolDeckPage path={path} onNavigate={navigate} safeLabMode={safeLabMode} />;
    if (routeMatches(path, "/builder")) return <ScenarioBuilderPage onNavigate={navigate} safeLabMode={safeLabMode} />;
    if (routeMatches(path, "/glossary")) return <GlossaryPage />;
    if (routeMatches(path, "/settings")) return <SettingsPage status={status} error={error} onRetry={retry} />;
    return <NotFoundPage onNavigate={navigate} />;
  })();

  const chooseCommand = (target: string, label: string) => {
    setCommandOpen(false);
    setCommandQuery("");
    navigate(target);
    setToast(`${label} workspace opened`);
  };

  return (
    <>
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <AppShell
        path={path}
        runtimeState={error ? "error" : status ? "ready" : "checking"}
        displayMode={status?.capabilities.display_mode ?? "LOCAL WORKSTATION"}
        onNavigate={navigate}
        onOpenCommand={() => setCommandOpen(true)}
        onOpenHelp={() => setHelpOpen(true)}
      >{route}</AppShell>
      <Dialog open={commandOpen} onClose={() => setCommandOpen(false)} title="Command palette">
        <Input data-autofocus label="Find a workspace" placeholder="Type a route or task…" value={commandQuery} onChange={(event) => setCommandQuery(event.target.value)} />
        <div className="command-list">{filteredCommands.map((item) => <Button key={item.path} variant="ghost" onClick={() => chooseCommand(item.path, item.label)}><strong>{item.label}</strong><small>{item.description}</small></Button>)}{filteredCommands.length === 0 && <p>No matching workspace.</p>}</div>
      </Dialog>
      <Dialog open={helpOpen} onClose={() => setHelpOpen(false)} title="Keyboard map">
        <dl className="shortcut-list"><div><dt><kbd>⌘/Ctrl</kbd> + <kbd>K</kbd></dt><dd>Open command palette</dd></div><div><dt><kbd>?</kbd></dt><dd>Open this keyboard map</dd></div><div><dt><kbd>↑</kbd> <kbd>↓</kbd></dt><dd>Move through desktop navigation</dd></div><div><dt><kbd>←</kbd> <kbd>→</kbd></dt><dd>Move tabs or resize split panes</dd></div><div><dt><kbd>Esc</kbd></dt><dd>Close dialog or drawer</dd></div></dl>
      </Dialog>
      <Toast message={toast} />
    </>
  );
}
