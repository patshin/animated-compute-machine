import type { PixelIconName } from "./ui/PixelIcon";

export interface NavigationItem {
  label: string;
  shortLabel: string;
  path: string;
  icon: PixelIconName;
  description: string;
}

export function routeMatches(path: string, base: string) {
  return path === base || (base !== "/" && path.startsWith(`${base}/`));
}

export const navigationItems: NavigationItem[] = [
  { label: "Home", shortLabel: "HOME", path: "/", icon: "home", description: "Learning map and current mission" },
  { label: "Lab", shortLabel: "LAB", path: "/lab", icon: "lab", description: "Visual runtime workbench" },
  { label: "Scenarios", shortLabel: "SCEN", path: "/scenarios", icon: "scenarios", description: "Scenario Kit library" },
  { label: "Runs", shortLabel: "RUNS", path: "/runs", icon: "runs", description: "Trace, replay and compare" },
  { label: "Evals", shortLabel: "EVAL", path: "/evals", icon: "evals", description: "Evaluation studio" },
  { label: "Protocols", shortLabel: "PROTO", path: "/protocols/mcp", icon: "protocols", description: "MCP, A2A and AG-UI boundaries" },
  { label: "Builder", shortLabel: "BUILD", path: "/builder", icon: "builder", description: "Scenario Builder" },
  { label: "Glossary", shortLabel: "WORDS", path: "/glossary", icon: "glossary", description: "Concepts and decisions" },
  { label: "Settings", shortLabel: "SET", path: "/settings", icon: "settings", description: "Local runtime settings" }
];

export const curriculum = [
  ["M0", "Agent Mental Model", "Workflow, autonomy and responsibility"],
  ["M1", "Structured Output", "Schemas, validation and repair"],
  ["M2", "Tool Calling", "Proposal, permission and execution"],
  ["M3", "State + Graph", "Nodes, reducers and typed routes"],
  ["M4", "Streaming Events", "Runtime projection and UI state"],
  ["M5", "Retrieval + Evidence", "Authority, freshness and citation"],
  ["M6", "Memory + Persistence", "Context, threads and checkpoints"],
  ["M7", "HITL + Approval", "Interrupt, scope and resume"],
  ["M8", "Guardrails + Security", "Injection, authz and redaction"],
  ["M9", "Tracing + Evals", "Trajectory, regression and quality"],
  ["M10", "MCP · A2A · AG-UI", "Protocol boundaries and contracts"],
  ["M11", "Production Design", "Architecture and release review"]
] as const;
