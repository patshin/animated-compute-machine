import type { components } from "@agent-trace/contracts";

import { nextCommandKey } from "./api";

export type EvalCatalog = components["schemas"]["EvalCatalog"];
export type EvalBatch = components["schemas"]["EvalBatch"];
export type EvalRun = components["schemas"]["EvalRun"];
export type TraceSpan = components["schemas"]["TraceSpan"];
export type TraceEvent = components["schemas"]["TraceEvent"];
export type RunComparison = components["schemas"]["RunComparison"];
export type ExportBundle = components["schemas"]["ExportBundle"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: readonly string[]) {
  const keys = new Set(allowed);
  return Object.keys(value).every((key) => keys.has(key));
}

function isStrings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isInteger(value: unknown, minimum = 0): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= minimum;
}

function isRate(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1;
}

function isSha(value: unknown): value is string {
  return typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
}

function isNullableString(value: unknown): value is string | null {
  return typeof value === "string" || value === null;
}

function ratesMatch(actual: number, numerator: number, denominator: number) {
  return Math.abs(actual - (numerator / denominator)) < 1e-9;
}

function isSpan(value: unknown): value is TraceSpan {
  return isRecord(value)
    && hasOnlyKeys(value, ["span_id", "trace_id", "parent_span_id", "name", "kind", "node_id", "start_ms", "end_ms", "duration_ms", "status", "causation_id", "correlation_id", "provider_id", "tool_name", "input_ref", "output_ref", "error", "redacted_paths", "attributes"])
    && typeof value.span_id === "string"
    && typeof value.trace_id === "string"
    && (typeof value.parent_span_id === "string" || value.parent_span_id === null)
    && typeof value.name === "string"
    && ["run", "node", "provider", "tool", "retrieval", "evaluation"].includes(String(value.kind))
    && (typeof value.node_id === "string" || value.node_id === null)
    && isInteger(value.start_ms)
    && isInteger(value.end_ms)
    && isInteger(value.duration_ms)
    && value.end_ms - value.start_ms === value.duration_ms
    && ["ok", "error", "blocked"].includes(String(value.status))
    && typeof value.causation_id === "string"
    && typeof value.correlation_id === "string"
    && isNullableString(value.provider_id)
    && isNullableString(value.tool_name)
    && isNullableString(value.input_ref)
    && isNullableString(value.output_ref)
    && isNullableString(value.error)
    && isStrings(value.redacted_paths)
    && isRecord(value.attributes);
}

function isEvent(value: unknown): value is TraceEvent {
  return isRecord(value)
    && hasOnlyKeys(value, ["event_id", "trace_id", "run_id", "sequence", "type", "timestamp_ms", "span_id", "node_id", "causation_id", "correlation_id", "payload_version", "payload_hash", "summary", "payload", "redacted_paths"])
    && typeof value.event_id === "string"
    && typeof value.trace_id === "string"
    && typeof value.run_id === "string"
    && isInteger(value.sequence, 1)
    && typeof value.type === "string"
    && isInteger(value.timestamp_ms)
    && typeof value.span_id === "string"
    && (typeof value.node_id === "string" || value.node_id === null)
    && typeof value.causation_id === "string"
    && typeof value.correlation_id === "string"
    && value.payload_version === "1.0.0"
    && isSha(value.payload_hash)
    && typeof value.summary === "string"
    && isRecord(value.payload)
    && isStrings(value.redacted_paths);
}

function isAssertion(value: unknown) {
  return isRecord(value)
    && hasOnlyKeys(value, ["assertion_id", "kind", "path", "operator", "expected", "actual", "critical", "passed", "message", "span_id"])
    && typeof value.assertion_id === "string"
    && typeof value.kind === "string"
    && typeof value.path === "string"
    && typeof value.operator === "string"
    && typeof value.critical === "boolean"
    && typeof value.passed === "boolean"
    && typeof value.message === "string"
    && typeof value.span_id === "string";
}

function isRun(value: unknown): value is EvalRun {
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "run_id", "trace_id", "case_id", "scenario_id", "module_id", "provider", "seed", "status", "answer", "output", "duration_ms", "spans", "events", "assertions", "score", "critical_failures", "created_at", "integrity_sha256"])
    || value.schema_id !== "agent-trace://eval-run/1.0.0"
    || typeof value.run_id !== "string"
    || typeof value.trace_id !== "string"
    || typeof value.case_id !== "string"
    || typeof value.scenario_id !== "string"
    || typeof value.module_id !== "string"
    || value.provider !== "mock"
    || !isInteger(value.seed)
    || !["passed", "failed"].includes(String(value.status))
    || typeof value.answer !== "string"
    || !isRecord(value.output)
    || !isInteger(value.duration_ms)
    || !Array.isArray(value.spans) || !value.spans.every(isSpan)
    || !Array.isArray(value.events) || !value.events.every(isEvent)
    || !Array.isArray(value.assertions) || !value.assertions.every(isAssertion)
    || !isRate(value.score)
    || !isInteger(value.critical_failures)
    || typeof value.created_at !== "string"
    || !isSha(value.integrity_sha256)) return false;
  const spans = value.spans as TraceSpan[];
  const events = value.events as TraceEvent[];
  const assertions = value.assertions as EvalRun["assertions"];
  const spanIds = new Set(spans.map((span) => span.span_id));
  return spans.length > 0
    && events.length > 0
    && assertions.length > 0
    && events.every((event, index) => event.sequence === index + 1)
    && new Set(events.map((event) => event.event_id)).size === events.length
    && spanIds.size === spans.length
    && spans.every((span) => span.trace_id === value.trace_id
      && (span.parent_span_id === null || spanIds.has(span.parent_span_id as string)))
    && events.every((event, index) => event.run_id === value.run_id
      && event.trace_id === value.trace_id
      && spanIds.has(event.span_id)
      && (index === 0 || event.timestamp_ms >= events[index - 1]!.timestamp_ms))
    && assertions.every((assertion) => spanIds.has(assertion.span_id));
}

export function parseEvalCatalog(value: unknown): EvalCatalog {
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "dataset_id", "case_count", "scenario_count", "scenarios", "evaluator_kinds", "answer_version_mapping", "otel_enabled"])
    || value.schema_id !== "agent-trace://eval-catalog/1.0.0"
    || value.dataset_id !== "golden-cases@1.0.0"
    || !isInteger(value.case_count, 35)
    || !isInteger(value.scenario_count, 7)
    || !isStrings(value.scenarios)
    || value.scenarios.length !== value.scenario_count
    || !isStrings(value.evaluator_kinds)
    || typeof value.answer_version_mapping !== "string"
    || value.otel_enabled !== false) {
    throw new Error("evaluation catalog crossed an invalid typed boundary");
  }
  return value as EvalCatalog;
}

function isBreakdown(value: unknown) {
  return isRecord(value)
    && hasOnlyKeys(value, ["key", "total", "failures"])
    && typeof value.key === "string"
    && isInteger(value.total, 1)
    && isInteger(value.failures)
    && value.failures <= value.total;
}

function isFailureCluster(value: unknown) {
  return isRecord(value)
    && hasOnlyKeys(value, ["category", "count", "case_ids"])
    && typeof value.category === "string"
    && isInteger(value.count, 1)
    && isStrings(value.case_ids)
    && value.case_ids.length === value.count;
}

function isDashboard(value: unknown) {
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["total", "passed", "failed", "pass_rate", "critical_security_failures", "security_gate", "route_accuracy", "tool_selection_accuracy", "argument_accuracy", "citation_coverage", "unsafe_action_count", "p50_duration_ms", "p95_duration_ms", "baseline_id", "baseline_pass_rate", "baseline_delta", "scenarios", "failures_by_module", "failures_by_provider", "failure_clusters"])
    || !isInteger(value.total, 1)
    || !isInteger(value.passed)
    || !isInteger(value.failed)
    || value.passed + value.failed !== value.total
    || !isRate(value.pass_rate)
    || !ratesMatch(value.pass_rate, value.passed, value.total)
    || !isInteger(value.critical_security_failures)
    || !["passed", "failed"].includes(String(value.security_gate))
    || !isRate(value.route_accuracy)
    || !isRate(value.tool_selection_accuracy)
    || !isRate(value.argument_accuracy)
    || !isRate(value.citation_coverage)
    || !isInteger(value.unsafe_action_count)
    || !isInteger(value.p50_duration_ms)
    || !isInteger(value.p95_duration_ms)
    || typeof value.baseline_id !== "string"
    || !isRate(value.baseline_pass_rate)
    || typeof value.baseline_delta !== "number"
    || Math.abs(value.baseline_delta - (value.pass_rate - value.baseline_pass_rate)) >= 1e-9
    || !Array.isArray(value.scenarios)
    || !value.scenarios.every((scenario) => isRecord(scenario)
      && hasOnlyKeys(scenario, ["scenario_id", "total", "passed", "failed", "pass_rate"])
      && typeof scenario.scenario_id === "string"
      && isInteger(scenario.total, 1)
      && isInteger(scenario.passed)
      && isInteger(scenario.failed)
      && scenario.passed + scenario.failed === scenario.total
      && isRate(scenario.pass_rate)
      && ratesMatch(scenario.pass_rate, scenario.passed, scenario.total))
    || !Array.isArray(value.failures_by_module) || !value.failures_by_module.every(isBreakdown)
    || !Array.isArray(value.failures_by_provider) || !value.failures_by_provider.every(isBreakdown)
    || !Array.isArray(value.failure_clusters) || !value.failure_clusters.every(isFailureCluster)) return false;
  const scenarios = value.scenarios as Array<{ total: number }>;
  const modules = value.failures_by_module as Array<{ total: number }>;
  const providers = value.failures_by_provider as Array<{ total: number }>;
  return scenarios.reduce((total, item) => total + item.total, 0) === value.total
    && modules.reduce((total, item) => total + item.total, 0) === value.total
    && providers.reduce((total, item) => total + item.total, 0) === value.total
    && ((value.security_gate === "passed" && value.critical_security_failures === 0 && value.unsafe_action_count === 0)
      || (value.security_gate === "failed" && (value.critical_security_failures > 0 || value.unsafe_action_count > 0)));
}

export function parseEvalBatch(value: unknown): EvalBatch {
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "batch_id", "dataset_id", "target_count", "status", "elapsed_ms", "dashboard", "runs", "created_at", "integrity_sha256"])
    || value.schema_id !== "agent-trace://eval-batch/1.0.0"
    || typeof value.batch_id !== "string"
    || value.dataset_id !== "golden-cases@1.0.0"
    || !isInteger(value.target_count, 1) || value.target_count > 100
    || !["passed", "failed"].includes(String(value.status))
    || !isInteger(value.elapsed_ms)
    || !isDashboard(value.dashboard)
    || !Array.isArray(value.runs) || value.runs.length !== value.target_count || !value.runs.every(isRun)
    || typeof value.created_at !== "string"
    || !isSha(value.integrity_sha256)) {
    throw new Error("evaluation batch crossed an invalid typed boundary");
  }
  const dashboard = value.dashboard as EvalBatch["dashboard"];
  const runs = value.runs as EvalRun[];
  const passed = runs.filter((run) => run.status === "passed").length;
  const expectedStatus = dashboard.failed === 0 && dashboard.security_gate === "passed" ? "passed" : "failed";
  if (dashboard.total !== value.target_count
    || dashboard.passed !== passed
    || dashboard.failed !== value.target_count - passed
    || value.status !== expectedStatus) {
    throw new Error("evaluation batch crossed an invalid typed boundary");
  }
  return value as EvalBatch;
}

export function parseRunComparison(value: unknown): RunComparison {
  const allowedSections = new Set(["config", "trajectory", "state", "tools", "evidence", "answer", "evaluation", "duration"]);
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "comparison_id", "left", "right", "same_final_answer", "unsafe_trajectory_detected", "security_gate", "sections"])
    || value.schema_id !== "agent-trace://run-comparison/1.0.0"
    || typeof value.comparison_id !== "string"
    || !isRun(value.left) || !isRun(value.right)
    || typeof value.same_final_answer !== "boolean"
    || typeof value.unsafe_trajectory_detected !== "boolean"
    || value.security_gate !== "failed"
    || !Array.isArray(value.sections) || value.sections.length !== 8
    || !value.sections.every((section) => isRecord(section)
      && hasOnlyKeys(section, ["section", "left", "right", "changed"])
      && typeof section.section === "string" && allowedSections.has(section.section)
      && typeof section.changed === "boolean")
    || new Set(value.sections.map((section) => isRecord(section) ? section.section : null)).size !== 8) {
    throw new Error("run comparison crossed an invalid typed boundary");
  }
  return value as RunComparison;
}

export function parseExportBundle(value: unknown): ExportBundle {
  const expectedFiles = new Set(["run.json", "trace.jsonl", "evaluation.json", "evaluation.md"]);
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "bundle_id", "run_id", "redaction_policy", "manifest", "files"])
    || value.schema_id !== "agent-trace://export-bundle/1.0.0"
    || typeof value.bundle_id !== "string"
    || typeof value.run_id !== "string"
    || value.redaction_policy !== "agent-trace://redaction/e9-safe-export/1.0.0"
    || !isRecord(value.manifest)
    || !Array.isArray(value.files)) {
    throw new Error("evaluation export crossed an invalid typed boundary");
  }
  const manifest = value.manifest;
  const files = value.files;
  const manifestFiles = manifest.files;
  if (!hasOnlyKeys(manifest, ["schema_version", "run_id", "dataset_id", "redaction_policy", "redacted_paths", "files"])
    || manifest.schema_version !== "1.0.0"
    || manifest.run_id !== value.run_id
    || manifest.dataset_id !== "golden-cases@1.0.0"
    || manifest.redaction_policy !== value.redaction_policy
    || !isStrings(manifest.redacted_paths)
    || !Array.isArray(manifestFiles) || manifestFiles.length !== 4
    || !manifestFiles.every((file) => isRecord(file)
      && hasOnlyKeys(file, ["filename", "sha256", "media_type"])
      && typeof file.filename === "string" && expectedFiles.has(file.filename)
      && isSha(file.sha256)
      && ["application/json", "application/x-ndjson", "text/markdown"].includes(String(file.media_type)))
    || files.length !== 4
    || !files.every((file) => isRecord(file)
      && hasOnlyKeys(file, ["filename", "media_type", "sha256", "content"])
      && typeof file.filename === "string" && expectedFiles.has(file.filename)
      && ["application/json", "application/x-ndjson", "text/markdown"].includes(String(file.media_type))
      && isSha(file.sha256)
      && typeof file.content === "string")
    || new Set(files.map((file) => isRecord(file) ? file.filename : null)).size !== 4) {
    throw new Error("evaluation export crossed an invalid typed boundary");
  }
  if (files.some((file) => isRecord(file) && !manifestFiles.some((entry) => isRecord(entry)
    && entry.filename === file.filename
    && entry.sha256 === file.sha256
    && entry.media_type === file.media_type))) {
    throw new Error("evaluation export crossed an invalid typed boundary");
  }
  return value as ExportBundle;
}

async function sha256Text(content: string) {
  if (!globalThis.crypto?.subtle) throw new Error("evaluation export hashing is unavailable");
  const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(content));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function verifyExportBundle(value: unknown): Promise<ExportBundle> {
  const bundle = parseExportBundle(value);
  const checks = await Promise.all(bundle.files.map(async (file) => ({ file, actual: await sha256Text(file.content) })));
  if (checks.some(({ file, actual }) => file.sha256 !== actual)) {
    throw new Error("evaluation export content hash mismatch");
  }
  return bundle;
}

async function responseJson(response: Response) {
  const value: unknown = await response.json();
  if (!response.ok) {
    const detail = isRecord(value) && typeof value.detail === "string" ? value.detail : `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return value;
}

export async function loadEvalCatalog(signal?: AbortSignal) {
  return parseEvalCatalog(await responseJson(await fetch("/api/evals/catalog", { signal })));
}

export async function runEvalBatch(targetCount: 70 | 100) {
  return parseEvalBatch(await responseJson(await fetch("/api/evals/batches", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved: true, idempotency_key: nextCommandKey(`eval-${targetCount}`), target_count: targetCount })
  })));
}

export async function createRunComparison() {
  return parseRunComparison(await responseJson(await fetch("/api/evals/comparisons", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved: true, idempotency_key: nextCommandKey("eval-compare"), case_id: "data-analyst-tool-boundary" })
  })));
}

export async function exportEvalRun(runId: string) {
  return verifyExportBundle(await responseJson(await fetch("/api/evals/exports", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved: true, idempotency_key: nextCommandKey("eval-export"), run_id: runId })
  })));
}
