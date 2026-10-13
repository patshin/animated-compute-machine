import { parseAgentEvent, type AgentEvent } from "./api";

export type JsonObject = Record<string, unknown>;

export interface ValidationIssue {
  severity: "error" | "warning";
  section: SectionName;
  path: string;
  code: string;
  message: string;
  hint: string;
}

export type SectionName = "manifest" | "tools" | "graph" | "lessons" | "policies" | "faults" | "evals" | "data";

export interface ValidationSection {
  name: SectionName;
  status: "passed" | "failed";
  checked: number;
  issues: ValidationIssue[];
}

export interface ScenarioValidationReport {
  schema_id: "agent-trace://scenario-validation/1.0.0";
  valid: boolean;
  compatible: boolean;
  fingerprint: string;
  error_count: number;
  warning_count: number;
  sections: ValidationSection[];
}

export interface ScenarioBundle {
  manifest: JsonObject;
  tools: JsonObject;
  graph: JsonObject;
  lessons: JsonObject;
  policies: JsonObject;
  faults: JsonObject;
  evals: JsonObject[];
  data: Record<string, unknown>;
  resources: ScenarioResource[];
}

export interface ScenarioResource {
  path: string;
  media_type: "application/json" | "application/vnd.sqlite3" | "image/gif" | "image/jpeg" | "image/png" | "image/svg+xml" | "image/webp" | "text/csv" | "text/markdown" | "text/plain";
  byte_count: number;
  sha256: string;
  content_base64: string;
}

export interface ScenarioSummary {
  id: string;
  name: string;
  short_description: string;
  domain: string;
  difficulty: "beginner" | "intermediate" | "advanced";
  concepts: string[];
  data_classification: "public_fixture" | "internal_fixture" | "sensitive_fixture";
  local_only: boolean;
  tool_count: number;
  lesson_count: number;
  golden_count: number;
  graph_template: string;
  validation_status: "passed" | "failed";
  compatible: boolean;
}

export interface ScenarioDetail {
  schema_id: "agent-trace://scenario-detail/1.0.0";
  summary: ScenarioSummary;
  manifest: JsonObject;
  report: ScenarioValidationReport;
}

export interface ScenarioDraft {
  schema_id: "agent-trace://scenario-draft/1.0.0";
  draft_id: string;
  revision: number;
  source_scenario_id: string;
  bundle: ScenarioBundle;
  report: ScenarioValidationReport;
  updated_at: string;
  audit: string[];
}

export interface ScenarioPreview {
  schema_id: "agent-trace://scenario-preview/1.0.0";
  source: "built_in" | "draft";
  source_id: string;
  scenario_id: string;
  title: string;
  objective: string;
  graph_template: string;
  nodes: JsonObject[];
  edges: JsonObject[];
  tool_names: string[];
  lesson: JsonObject;
  golden_case: JsonObject;
  validation_fingerprint: string;
}

export interface ScenarioPreviewNode {
  id: string;
  label: string;
  kind: string;
  owner: "model" | "code" | "tool" | "human";
  reads: string[];
  writes: string[];
  status: "pending" | "running" | "completed" | "error";
}

export interface ScenarioPreviewRunState {
  schema_id: "agent-trace://scenario-preview-run-state/1.0.0";
  run_id: string;
  source: "built_in" | "draft";
  source_id: string;
  scenario_id: string;
  status: "idle" | "running" | "completed" | "error";
  input: string;
  cursor: number;
  step_count: number;
  current_node: string | null;
  next_node: string | null;
  nodes: ScenarioPreviewNode[];
  values: JsonObject;
  last_sequence: number;
  validation_fingerprint: string;
}

export interface ScenarioPreviewRunSnapshot {
  schema_id: "agent-trace://scenario-preview-run-snapshot/1.0.0";
  run: ScenarioPreviewRunState;
  events: AgentEvent[];
}

export interface ScenarioArchive {
  schema_id: "agent-trace://scenario-archive/1.0.0";
  filename: string;
  media_type: "application/zip";
  sha256: string;
  byte_count: number;
  archive_base64: string;
  validation_fingerprint: string;
}

export type EvidenceKind = "case_run" | "prediction" | "fault_repair" | "machine_assertion" | "explain_back" | "artifact_export";

export interface LearningEvidence {
  evidence_id: string;
  module_id: string;
  kind: EvidenceKind;
  scenario_id: string;
  passed: boolean;
  summary: string;
  created_at: string;
}

export interface LearningModule {
  id: string;
  title: string;
  concepts: string[];
  estimated_minutes: number;
  prerequisite: string | null;
  recommended_scenario: string;
  lens_copy: { pm: string; builder: string; architect: string };
  mastery_level: "not_started" | "observe" | "diagnose" | "design" | "stress" | "ship";
  completed: boolean;
  evidence: LearningEvidence[];
}

export interface LearningMap {
  schema_id: "agent-trace://learning-map/1.0.0";
  modules: LearningModule[];
  completed_modules: number;
  evidence_count: number;
  completion_rule: string;
}

export interface CapstoneReport {
  schema_id: "agent-trace://capstone-report/1.0.0";
  title: string;
  scenario_id: string;
  validation_fingerprint: string;
  validation_passed: boolean;
  mastery_evidence_count: number;
  architecture_sections: string[];
  markdown: string;
}

const SECTIONS: SectionName[] = ["manifest", "tools", "graph", "lessons", "policies", "faults", "evals", "data"];
const APPROVAL = "local-scenario-builder-write-approved";

function isRecord(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(value: JsonObject, keys: readonly string[]): boolean {
  const allowed = new Set(keys);
  return Object.keys(value).every((key) => allowed.has(key));
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function isObjectArray(value: unknown): value is JsonObject[] {
  return Array.isArray(value) && value.every(isRecord);
}

function parseIssue(value: unknown): ValidationIssue {
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["severity", "section", "path", "code", "message", "hint"])
    || !["error", "warning"].includes(String(value.severity))
    || !SECTIONS.includes(value.section as SectionName)
    || ![value.path, value.code, value.message, value.hint].every((item) => typeof item === "string")) {
    throw new Error("Scenario validator returned an invalid issue");
  }
  return value as unknown as ValidationIssue;
}

export function parseValidationReport(value: unknown): ScenarioValidationReport {
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "valid", "compatible", "fingerprint", "error_count", "warning_count", "sections"])
    || value.schema_id !== "agent-trace://scenario-validation/1.0.0"
    || typeof value.valid !== "boolean"
    || typeof value.compatible !== "boolean"
    || typeof value.fingerprint !== "string"
    || !/^[a-f0-9]{64}$/.test(value.fingerprint)
    || !isInteger(value.error_count)
    || !isInteger(value.warning_count)
    || !Array.isArray(value.sections)
    || value.sections.length !== 8) {
    throw new Error("Scenario validator crossed an invalid report boundary");
  }
  const sections = value.sections.map((section) => {
    if (!isRecord(section)
      || !hasOnlyKeys(section, ["name", "status", "checked", "issues"])
      || !SECTIONS.includes(section.name as SectionName)
      || !["passed", "failed"].includes(String(section.status))
      || !isInteger(section.checked)
      || !Array.isArray(section.issues)) {
      throw new Error("Scenario validator returned an invalid section");
    }
    return { ...section, issues: section.issues.map(parseIssue) } as ValidationSection;
  });
  if (sections.map((section) => section.name).join("|") !== SECTIONS.join("|")) {
    throw new Error("Scenario validation sections are missing or out of order");
  }
  return { ...value, sections } as ScenarioValidationReport;
}

function parseBundle(value: unknown): ScenarioBundle {
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["manifest", "tools", "graph", "lessons", "policies", "faults", "evals", "data", "resources"])
    || !isRecord(value.manifest)
    || !isRecord(value.tools)
    || !isRecord(value.graph)
    || !isRecord(value.lessons)
    || !isRecord(value.policies)
    || !isRecord(value.faults)
    || !isObjectArray(value.evals)
    || !isRecord(value.data)
    || !Array.isArray(value.resources)
    || !value.resources.every((resource) => isRecord(resource)
      && hasOnlyKeys(resource, ["path", "media_type", "byte_count", "sha256", "content_base64"])
      && typeof resource.path === "string"
      && typeof resource.media_type === "string"
      && isInteger(resource.byte_count)
      && typeof resource.sha256 === "string"
      && /^[a-f0-9]{64}$/.test(resource.sha256)
      && typeof resource.content_base64 === "string")) {
    throw new Error("Scenario Kit crossed an invalid eight-section bundle boundary");
  }
  return value as unknown as ScenarioBundle;
}

function parseSummary(value: unknown): ScenarioSummary {
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["id", "name", "short_description", "domain", "difficulty", "concepts", "data_classification", "local_only", "tool_count", "lesson_count", "golden_count", "graph_template", "validation_status", "compatible"])
    || ![value.id, value.name, value.short_description, value.domain, value.graph_template].every((item) => typeof item === "string")
    || !["beginner", "intermediate", "advanced"].includes(String(value.difficulty))
    || !["public_fixture", "internal_fixture", "sensitive_fixture"].includes(String(value.data_classification))
    || !isStringArray(value.concepts)
    || typeof value.local_only !== "boolean"
    || ![value.tool_count, value.lesson_count, value.golden_count].every(isInteger)
    || !["passed", "failed"].includes(String(value.validation_status))
    || typeof value.compatible !== "boolean") {
    throw new Error("Scenario catalog returned an invalid Kit summary");
  }
  return value as unknown as ScenarioSummary;
}

export function parseScenarioCatalog(value: unknown): ScenarioSummary[] {
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "scenarios"])
    || value.schema_id !== "agent-trace://scenario-catalog/1.0.0"
    || !Array.isArray(value.scenarios)
    || value.scenarios.length !== 7) {
    throw new Error("Scenario catalog crossed an invalid boundary");
  }
  const scenarios = value.scenarios.map(parseSummary);
  if (new Set(scenarios.map((item) => item.id)).size !== 7) {
    throw new Error("Scenario catalog identities are not unique");
  }
  return scenarios;
}

function parseDetail(value: unknown): ScenarioDetail {
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "summary", "manifest", "report"])
    || value.schema_id !== "agent-trace://scenario-detail/1.0.0"
    || !isRecord(value.manifest)) {
    throw new Error("Scenario detail crossed an invalid boundary");
  }
  return { schema_id: value.schema_id, summary: parseSummary(value.summary), manifest: value.manifest, report: parseValidationReport(value.report) };
}

export function parseScenarioDraft(value: unknown): ScenarioDraft {
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "draft_id", "revision", "source_scenario_id", "bundle", "report", "updated_at", "audit"])
    || value.schema_id !== "agent-trace://scenario-draft/1.0.0"
    || typeof value.draft_id !== "string"
    || !/^draft_[a-f0-9]{16}$/.test(value.draft_id)
    || !isInteger(value.revision) || value.revision < 1
    || typeof value.source_scenario_id !== "string"
    || typeof value.updated_at !== "string"
    || !isStringArray(value.audit)) {
    throw new Error("Scenario draft crossed an invalid typed boundary");
  }
  return { ...value, bundle: parseBundle(value.bundle), report: parseValidationReport(value.report) } as ScenarioDraft;
}

export function parseScenarioPreview(value: unknown): ScenarioPreview {
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "source", "source_id", "scenario_id", "title", "objective", "graph_template", "nodes", "edges", "tool_names", "lesson", "golden_case", "validation_fingerprint"])
    || value.schema_id !== "agent-trace://scenario-preview/1.0.0"
    || !["built_in", "draft"].includes(String(value.source))
    || ![value.source_id, value.scenario_id, value.title, value.objective, value.graph_template].every((item) => typeof item === "string")
    || !isObjectArray(value.nodes) || value.nodes.length < 2
    || !isObjectArray(value.edges) || value.edges.length < 1
    || !isStringArray(value.tool_names) || value.tool_names.length < 1
    || !isRecord(value.lesson)
    || !isRecord(value.golden_case)
    || typeof value.validation_fingerprint !== "string") {
    throw new Error("Scenario preview crossed an invalid projection boundary");
  }
  return value as unknown as ScenarioPreview;
}

export function parseScenarioPreviewRun(value: unknown): ScenarioPreviewRunSnapshot {
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "run", "events"])
    || value.schema_id !== "agent-trace://scenario-preview-run-snapshot/1.0.0"
    || !isRecord(value.run)
    || !hasOnlyKeys(value.run, ["schema_id", "run_id", "source", "source_id", "scenario_id", "status", "input", "cursor", "step_count", "current_node", "next_node", "nodes", "values", "last_sequence", "validation_fingerprint"])
    || value.run.schema_id !== "agent-trace://scenario-preview-run-state/1.0.0"
    || typeof value.run.run_id !== "string" || !/^run_[A-Za-z0-9_-]+$/.test(value.run.run_id)
    || !["built_in", "draft"].includes(String(value.run.source))
    || ![value.run.source_id, value.run.scenario_id, value.run.input, value.run.validation_fingerprint].every((item) => typeof item === "string")
    || !["idle", "running", "completed", "error"].includes(String(value.run.status))
    || !isInteger(value.run.cursor) || !isInteger(value.run.step_count) || !isInteger(value.run.last_sequence)
    || !(value.run.current_node === null || typeof value.run.current_node === "string")
    || !(value.run.next_node === null || typeof value.run.next_node === "string")
    || !isRecord(value.run.values)
    || !Array.isArray(value.run.nodes)
    || value.run.nodes.length < 2
    || !value.run.nodes.every((node) => isRecord(node)
      && hasOnlyKeys(node, ["id", "label", "kind", "owner", "reads", "writes", "status"])
      && typeof node.id === "string" && /^[a-z][a-z0-9_]{1,63}$/.test(node.id)
      && typeof node.label === "string" && typeof node.kind === "string"
      && ["model", "code", "tool", "human"].includes(String(node.owner))
      && isStringArray(node.reads) && isStringArray(node.writes)
      && ["pending", "running", "completed", "error"].includes(String(node.status)))
    || !Array.isArray(value.events)) {
    throw new Error("Scenario preview runtime crossed an invalid typed boundary");
  }
  const events = value.events.map(parseAgentEvent);
  const run = value.run as unknown as ScenarioPreviewRunState;
  if (events.some((event) => event.run_id !== run.run_id)
    || events.some((event, index) => event.sequence !== index + 1)
    || new Set(events.map((event) => event.event_id)).size !== events.length
    || (events.at(-1)?.sequence ?? 0) !== run.last_sequence) {
    throw new Error("Scenario preview runtime returned an inconsistent event trajectory");
  }
  return { schema_id: value.schema_id, run, events };
}

export function assertScenarioPreviewCorrelation(
  definition: ScenarioPreview,
  snapshot: ScenarioPreviewRunSnapshot
): ScenarioPreviewRunSnapshot {
  if (snapshot.run.source !== definition.source
    || snapshot.run.source_id !== definition.source_id
    || snapshot.run.scenario_id !== definition.scenario_id
    || snapshot.run.validation_fingerprint !== definition.validation_fingerprint) {
    throw new Error(
      "Preview run does not belong to this Scenario Kit revision; remove the run parameter and start a new preview"
    );
  }
  return snapshot;
}

function parseArchive(value: unknown): ScenarioArchive {
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "filename", "media_type", "sha256", "byte_count", "archive_base64", "validation_fingerprint"])
    || value.schema_id !== "agent-trace://scenario-archive/1.0.0"
    || value.media_type !== "application/zip"
    || ![value.filename, value.sha256, value.archive_base64, value.validation_fingerprint].every((item) => typeof item === "string")
    || !isInteger(value.byte_count) || value.byte_count < 1
    || !/^[a-f0-9]{64}$/.test(value.sha256 as string)) {
    throw new Error("Scenario export crossed an invalid archive boundary");
  }
  return value as unknown as ScenarioArchive;
}

export function parseLearningMap(value: unknown): LearningMap {
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "modules", "completed_modules", "evidence_count", "completion_rule"])
    || value.schema_id !== "agent-trace://learning-map/1.0.0"
    || !Array.isArray(value.modules) || value.modules.length !== 12
    || !isInteger(value.completed_modules)
    || !isInteger(value.evidence_count)
    || typeof value.completion_rule !== "string") {
    throw new Error("Learning Map crossed an invalid typed boundary");
  }
  const modules = value.modules.map((module) => {
    if (!isRecord(module)
      || !hasOnlyKeys(module, ["id", "title", "concepts", "estimated_minutes", "prerequisite", "recommended_scenario", "lens_copy", "mastery_level", "completed", "evidence"])
      || typeof module.id !== "string" || !/^M(?:[0-9]|1[01])$/.test(module.id)
      || typeof module.title !== "string" || !isStringArray(module.concepts)
      || !isInteger(module.estimated_minutes)
      || !(typeof module.prerequisite === "string" || module.prerequisite === null)
      || typeof module.recommended_scenario !== "string"
      || !isRecord(module.lens_copy)
      || !hasOnlyKeys(module.lens_copy, ["pm", "builder", "architect"])
      || ![module.lens_copy.pm, module.lens_copy.builder, module.lens_copy.architect].every((item) => typeof item === "string")
      || !["not_started", "observe", "diagnose", "design", "stress", "ship"].includes(String(module.mastery_level))
      || typeof module.completed !== "boolean"
      || !Array.isArray(module.evidence)) {
      throw new Error("Learning Map returned an invalid module");
    }
    const evidence = module.evidence.map((item) => {
      if (!isRecord(item)
        || !hasOnlyKeys(item, ["evidence_id", "module_id", "kind", "scenario_id", "passed", "summary", "created_at"])
        || ![item.evidence_id, item.module_id, item.kind, item.scenario_id, item.summary, item.created_at].every((entry) => typeof entry === "string")
        || typeof item.passed !== "boolean") {
        throw new Error("Learning Map returned invalid mastery evidence");
      }
      return item as unknown as LearningEvidence;
    });
    return { ...module, lens_copy: module.lens_copy, evidence } as unknown as LearningModule;
  });
  return { ...value, modules } as LearningMap;
}

function parseCapstone(value: unknown): CapstoneReport {
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "title", "scenario_id", "validation_fingerprint", "validation_passed", "mastery_evidence_count", "architecture_sections", "markdown"])
    || value.schema_id !== "agent-trace://capstone-report/1.0.0"
    || ![value.title, value.scenario_id, value.validation_fingerprint, value.markdown].every((item) => typeof item === "string")
    || typeof value.validation_passed !== "boolean"
    || !isInteger(value.mastery_evidence_count)
    || !isStringArray(value.architecture_sections)
    || value.architecture_sections.length !== 10) {
    throw new Error("Capstone report crossed an invalid typed boundary");
  }
  return value as unknown as CapstoneReport;
}

let commandSequence = 0;
function command(action: string) {
  commandSequence += 1;
  const unique = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${commandSequence}`;
  return { approval: APPROVAL, idempotency_key: `ui-${action}-${unique}` };
}

async function request<T>(path: string, parser: (value: unknown) => T, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}), ...init?.headers } });
  if (!response.ok) {
    const detail: unknown = await response.json().catch(() => null);
    const message = isRecord(detail) && typeof detail.detail === "string" ? detail.detail : `HTTP ${response.status}`;
    throw new Error(`${path} returned ${message}`);
  }
  return parser(await response.json());
}

export function loadScenarioCatalog(signal?: AbortSignal): Promise<ScenarioSummary[]> {
  return request("/api/scenarios", parseScenarioCatalog, { signal });
}

export function loadScenarioDetail(scenarioId: string, signal?: AbortSignal): Promise<ScenarioDetail> {
  return request(`/api/scenarios/${encodeURIComponent(scenarioId)}`, parseDetail, { signal });
}

export function createScenarioDraft(scenarioId: string): Promise<ScenarioDraft> {
  return request("/api/scenario-builder/drafts", parseScenarioDraft, { method: "POST", body: JSON.stringify({ ...command("scenario-clone"), source_scenario_id: scenarioId }) });
}

export function loadScenarioDraft(draftId: string, signal?: AbortSignal): Promise<ScenarioDraft> {
  return request(`/api/scenario-builder/drafts/${encodeURIComponent(draftId)}`, parseScenarioDraft, { signal });
}

export function saveScenarioDraft(draft: ScenarioDraft, bundle: ScenarioBundle): Promise<ScenarioDraft> {
  return request(`/api/scenario-builder/drafts/${encodeURIComponent(draft.draft_id)}/save`, parseScenarioDraft, { method: "POST", body: JSON.stringify({ ...command("scenario-save"), expected_revision: draft.revision, bundle }) });
}

export function validateScenarioDraft(draftId: string): Promise<ScenarioValidationReport> {
  return request(`/api/scenario-builder/drafts/${encodeURIComponent(draftId)}/validate`, parseValidationReport, { method: "POST" });
}

export function loadScenarioPreview(source: "built_in" | "draft", sourceId: string, signal?: AbortSignal): Promise<ScenarioPreview> {
  const path = source === "draft"
    ? `/api/scenario-builder/drafts/${encodeURIComponent(sourceId)}/preview`
    : `/api/scenarios/${encodeURIComponent(sourceId)}/preview`;
  return request(path, parseScenarioPreview, { signal });
}

export function startScenarioPreview(source: "built_in" | "draft", sourceId: string, input: string): Promise<ScenarioPreviewRunSnapshot> {
  return request("/api/scenario-builder/previews", parseScenarioPreviewRun, { method: "POST", body: JSON.stringify({ ...command("scenario-preview-start"), source, source_id: sourceId, input }) });
}

export function loadScenarioPreviewRun(runId: string, signal?: AbortSignal): Promise<ScenarioPreviewRunSnapshot> {
  return request(`/api/scenario-builder/previews/${encodeURIComponent(runId)}`, parseScenarioPreviewRun, { signal });
}

function scenarioPreviewCommand(runId: string, action: "step" | "run" | "reset"): Promise<ScenarioPreviewRunSnapshot> {
  return request(`/api/scenario-builder/previews/${encodeURIComponent(runId)}/${action}`, parseScenarioPreviewRun, { method: "POST", body: JSON.stringify({ ...command(`scenario-preview-${action}`), pace: action === "run" ? "fast" : "observe" }) });
}

export function stepScenarioPreview(runId: string): Promise<ScenarioPreviewRunSnapshot> { return scenarioPreviewCommand(runId, "step"); }
export function runScenarioPreview(runId: string): Promise<ScenarioPreviewRunSnapshot> { return scenarioPreviewCommand(runId, "run"); }
export function resetScenarioPreview(runId: string): Promise<ScenarioPreviewRunSnapshot> { return scenarioPreviewCommand(runId, "reset"); }

export function exportScenarioDraft(draftId: string): Promise<ScenarioArchive> {
  return request(`/api/scenario-builder/drafts/${encodeURIComponent(draftId)}/export`, parseArchive);
}

export function importScenarioArchive(filename: string, archiveBase64: string): Promise<ScenarioDraft> {
  return request("/api/scenario-builder/imports", parseScenarioDraft, { method: "POST", body: JSON.stringify({ ...command("scenario-import"), filename, archive_base64: archiveBase64 }) });
}

export function loadLearningMap(signal?: AbortSignal): Promise<LearningMap> {
  return request("/api/learning/map", parseLearningMap, { signal });
}

export function recordLearningEvidence(moduleId: string, kind: EvidenceKind, response: string, scenarioId: string): Promise<LearningMap> {
  return request(`/api/learning/modules/${encodeURIComponent(moduleId)}/evidence`, parseLearningMap, { method: "POST", body: JSON.stringify({ ...command("learning-evidence"), kind, response, scenario_id: scenarioId }) });
}

export function resetLearningProgress(moduleId: string): Promise<LearningMap> {
  return request(`/api/learning/modules/${encodeURIComponent(moduleId)}/reset`, parseLearningMap, { method: "POST", body: JSON.stringify({ ...command("learning-reset"), scope: "module" }) });
}

export function loadCapstoneReport(draftId: string): Promise<CapstoneReport> {
  return request(`/api/scenario-builder/drafts/${encodeURIComponent(draftId)}/capstone-report`, parseCapstone);
}
