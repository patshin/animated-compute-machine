import type { components } from "@agent-trace/contracts";

import { nextCommandKey, parseAgentEvent } from "./api";

export type SecurityCatalog = components["schemas"]["SecurityCatalog"];
export type SecurityCase = components["schemas"]["AttackCaseDefinition"];
export type SecurityLayer = components["schemas"]["SecurityLayerCheck"]["layer"];
export type SecurityAttackRun = components["schemas"]["SecurityAttackRun"];
export type AttackSuiteResult = components["schemas"]["AttackSuiteResult"];
export type SecurityDiagnostic = components["schemas"]["SecurityDiagnostic"];
export type ScenarioImportResult = components["schemas"]["ScenarioImportResult"];
export type CodexCapability = components["schemas"]["CodexCapability"];

const layerOrder: SecurityLayer[] = [
  "input_limit",
  "schema_validation",
  "content_isolation",
  "tool_allowlist",
  "authorization",
  "policy",
  "approval",
  "safe_executor",
  "output_validation",
  "redaction",
  "import_validation",
  "browser_sanitization",
  "codex_sandbox"
];
const layers = new Set<SecurityLayer>(layerOrder);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: readonly string[]): boolean {
  const expected = new Set(allowed);
  return Object.keys(value).every((key) => expected.has(key));
}

function isInteger(value: unknown, minimum = 0): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= minimum;
}

function isStrings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isLayer(value: unknown): value is SecurityLayer {
  return typeof value === "string" && layers.has(value as SecurityLayer);
}

function isCase(value: unknown): value is SecurityCase {
  return isRecord(value)
    && hasOnlyKeys(value, ["id", "title", "lesson", "vector", "severity", "expected_layer", "payload_preview"])
    && typeof value.id === "string"
    && /^[a-z][a-z0-9-]{2,95}$/.test(value.id)
    && typeof value.title === "string"
    && typeof value.lesson === "string"
    && ["user_input", "retrieval", "provider_output", "tool_output", "approval", "checkpoint", "scenario_import", "trace", "remote_artifact"].includes(String(value.vector))
    && ["P0", "P1", "P2", "P3"].includes(String(value.severity))
    && isLayer(value.expected_layer)
    && typeof value.payload_preview === "string";
}

function isPolicy(value: unknown): boolean {
  return isRecord(value)
    && hasOnlyKeys(value, [
      "policy_id", "input_limit_chars", "provider_output_limit_bytes", "codex_timeout_ms",
      "codex_concurrency", "network_default", "codex_sandbox", "scenario_max_compressed_bytes",
      "scenario_max_uncompressed_bytes", "scenario_max_files"
    ])
    && value.policy_id === "agent-trace://security-policy/1.0.0"
    && isInteger(value.input_limit_chars, 1)
    && isInteger(value.provider_output_limit_bytes, 1)
    && isInteger(value.codex_timeout_ms, 1)
    && value.codex_concurrency === 1
    && value.network_default === "off"
    && value.codex_sandbox === "read-only"
    && isInteger(value.scenario_max_compressed_bytes, 1)
    && isInteger(value.scenario_max_uncompressed_bytes, 1)
    && isInteger(value.scenario_max_files, 1);
}

function parseCodexCapability(value: unknown): CodexCapability {
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["detected", "enabled", "label", "binary_name", "fixed_argv", "inherited_environment", "policy"])
    || typeof value.detected !== "boolean"
    || typeof value.enabled !== "boolean"
    || value.label !== "LOCAL PROTOTYPE PROVIDER"
    || !(value.binary_name === null || value.binary_name === undefined || typeof value.binary_name === "string")
    || !isStrings(value.fixed_argv)
    || value.fixed_argv.length < 10
    || !value.fixed_argv.includes("read-only")
    || !value.fixed_argv.includes("features.web_search_request=false")
    || !isStrings(value.inherited_environment)
    || !isPolicy(value.policy)) {
    throw new Error("Codex capability crossed an invalid typed security boundary");
  }
  return value as CodexCapability;
}

export function parseSecurityCatalog(value: unknown): SecurityCatalog {
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "cases", "evaluation_case_count", "critical_evaluation_case_count", "defense_layers", "codex", "policy"])
    || value.schema_id !== "agent-trace://security-catalog/1.0.0"
    || !Array.isArray(value.cases)
    || value.cases.length < 10
    || !value.cases.every(isCase)
    || new Set(value.cases.map((item) => item.id)).size !== value.cases.length
    || !isInteger(value.evaluation_case_count, 1)
    || !isInteger(value.critical_evaluation_case_count, 1)
    || value.critical_evaluation_case_count > value.evaluation_case_count
    || !Array.isArray(value.defense_layers)
    || value.defense_layers.length !== layerOrder.length
    || !value.defense_layers.every((layer, index) => isLayer(layer) && layer === layerOrder[index])
    || !isPolicy(value.policy)) {
    throw new Error("security catalog crossed an invalid typed boundary");
  }
  const codex = parseCodexCapability(value.codex);
  return { ...value, codex } as SecurityCatalog;
}

export function parseSecurityRun(value: unknown): SecurityAttackRun {
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "run_id", "case", "status", "expected_layer", "actual_layer", "outcome", "checks", "privacy", "safe_content", "authorized_tool_calls", "side_effect_count", "redaction_count", "events"])
    || value.schema_id !== "agent-trace://security-attack-run/1.0.0"
    || typeof value.run_id !== "string"
    || !/^run_[A-Za-z0-9_-]+$/.test(value.run_id)
    || !isCase(value.case)
    || !["passed", "failed"].includes(String(value.status))
    || !isLayer(value.expected_layer)
    || !isLayer(value.actual_layer)
    || typeof value.outcome !== "string"
    || !Array.isArray(value.checks)
    || value.checks.length < 1
    || !value.checks.every((check, index) => isRecord(check)
      && hasOnlyKeys(check, ["sequence", "layer", "status", "code", "detail"])
      && check.sequence === index + 1
      && isLayer(check.layer)
      && ["passed", "blocked", "skipped"].includes(String(check.status))
      && typeof check.code === "string"
      && /^[A-Z][A-Z0-9_]+$/.test(check.code)
      && typeof check.detail === "string")
    || !isRecord(value.privacy)
    || !hasOnlyKeys(value.privacy, ["input_chars", "included_fields", "withheld_fields", "secret_patterns_redacted", "network_access", "context_boundary"])
    || !isInteger(value.privacy.input_chars)
    || !isStrings(value.privacy.included_fields)
    || !isStrings(value.privacy.withheld_fields)
    || !isInteger(value.privacy.secret_patterns_redacted)
    || value.privacy.network_access !== false
    || value.privacy.context_boundary !== "instruction-separated-from-untrusted-data"
    || !Array.isArray(value.safe_content)
    || !value.safe_content.every((block) => isRecord(block)
      && hasOnlyKeys(block, ["kind", "text", "href"])
      && ["text", "code", "link", "blocked_image"].includes(String(block.kind))
      && typeof block.text === "string"
      && (block.kind === "link"
        ? typeof block.href === "string" && /^https?:\/\//.test(block.href)
        : block.href === null || block.href === undefined))
    || !isInteger(value.authorized_tool_calls)
    || !isInteger(value.side_effect_count)
    || !isInteger(value.redaction_count)
    || !Array.isArray(value.events)
    || value.events.length < 3) {
    throw new Error("security run crossed an invalid typed boundary");
  }
  const events = value.events.map(parseAgentEvent);
  if (events.some((event, index) => event.run_id !== value.run_id || event.sequence !== index + 1)
    || value.expected_layer !== value.case.expected_layer
    || value.actual_layer !== value.checks.at(-1)?.layer
    || value.checks.at(-1)?.status !== "blocked"
    || (value.status === "passed") !== (value.actual_layer === value.expected_layer)) {
    throw new Error("security run returned an inconsistent event-backed trajectory");
  }
  return { ...value, events } as SecurityAttackRun;
}

export function parseAttackSuite(value: unknown): AttackSuiteResult {
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "suite_id", "status", "total", "passed", "failed", "cases"])
    || value.schema_id !== "agent-trace://security-suite-result/1.0.0"
    || typeof value.suite_id !== "string"
    || !/^suite_[A-Za-z0-9_-]+$/.test(value.suite_id)
    || !["passed", "failed"].includes(String(value.status))
    || !isInteger(value.total, 1)
    || !isInteger(value.passed)
    || !isInteger(value.failed)
    || value.passed + value.failed !== value.total
    || !Array.isArray(value.cases)
    || value.cases.length !== value.total
    || !value.cases.every((entry) => isRecord(entry)
      && hasOnlyKeys(entry, ["case_id", "scenario_id", "probe_scenario_id", "severity", "status", "expected_layer", "actual_layer", "assertions_passed", "assertions_total"])
      && typeof entry.case_id === "string"
      && typeof entry.scenario_id === "string"
      && typeof entry.probe_scenario_id === "string"
      && ["P0", "P1"].includes(String(entry.severity))
      && ["passed", "failed"].includes(String(entry.status))
      && isLayer(entry.expected_layer)
      && isLayer(entry.actual_layer)
      && isInteger(entry.assertions_passed)
      && isInteger(entry.assertions_total, 1)
      && entry.assertions_passed <= entry.assertions_total)) {
    throw new Error("attack suite crossed an invalid typed boundary");
  }
  return value as AttackSuiteResult;
}

export function parseSecurityDiagnostic(value: unknown): SecurityDiagnostic {
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "run_id", "policy_id", "case_id", "status", "expected_layer", "actual_layer", "event_types", "redaction_summary", "contains_raw_input"])
    || value.schema_id !== "agent-trace://security-diagnostic/1.0.0"
    || typeof value.run_id !== "string"
    || value.policy_id !== "agent-trace://security-policy/1.0.0"
    || typeof value.case_id !== "string"
    || !["passed", "failed"].includes(String(value.status))
    || !isLayer(value.expected_layer)
    || !isLayer(value.actual_layer)
    || !isStrings(value.event_types)
    || typeof value.redaction_summary !== "string"
    || value.contains_raw_input !== false) {
    throw new Error("security diagnostic crossed an invalid typed boundary");
  }
  return value as SecurityDiagnostic;
}

export function parseScenarioImport(value: unknown): ScenarioImportResult {
  if (!isRecord(value)
    || !hasOnlyKeys(value, ["schema_id", "scenario_id", "package_sha256", "status", "file_count", "compressed_bytes", "uncompressed_bytes", "files", "destination", "audit_message"])
    || value.schema_id !== "agent-trace://scenario-import-result/1.0.0"
    || typeof value.scenario_id !== "string"
    || !/^[a-z][a-z0-9-]{2,63}$/.test(value.scenario_id)
    || typeof value.package_sha256 !== "string"
    || !/^[a-f0-9]{64}$/.test(value.package_sha256)
    || !["imported", "idempotent_replay"].includes(String(value.status))
    || !isInteger(value.file_count, 1)
    || !isInteger(value.compressed_bytes, 1)
    || !isInteger(value.uncompressed_bytes, 1)
    || !Array.isArray(value.files)
    || value.files.length !== value.file_count
    || !value.files.every((entry) => isRecord(entry)
      && hasOnlyKeys(entry, ["path", "sha256", "size"])
      && typeof entry.path === "string"
      && typeof entry.sha256 === "string"
      && /^[a-f0-9]{64}$/.test(entry.sha256)
      && isInteger(entry.size))
    || typeof value.destination !== "string"
    || !/^\.local\/scenario-imports\/[a-z0-9-]+$/.test(value.destination)
    || typeof value.audit_message !== "string") {
    throw new Error("scenario import crossed an invalid typed boundary");
  }
  return value as ScenarioImportResult;
}

async function request<T>(path: string, parse: (value: unknown) => T, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}) }
  });
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const detail = isRecord(body) && typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`;
    throw new Error(`${path} returned ${detail}`);
  }
  return parse(await response.json());
}

export function loadSecurityCatalog(signal?: AbortSignal): Promise<SecurityCatalog> {
  return request("/api/security/catalog", parseSecurityCatalog, { signal });
}

export function runSecurityAttack(caseId: string, userText: string): Promise<SecurityAttackRun> {
  return request("/api/security/runs", parseSecurityRun, {
    method: "POST",
    body: JSON.stringify({
      approved: true,
      idempotency_key: nextCommandKey("security-run"),
      case_id: caseId,
      user_text: userText
    })
  });
}

export function runAttackSuite(): Promise<AttackSuiteResult> {
  return request("/api/security/suite", parseAttackSuite, {
    method: "POST",
    body: JSON.stringify({
      approved: true,
      idempotency_key: nextCommandKey("security-suite"),
      include_severities: ["P0", "P1"]
    })
  });
}

export function loadSecurityDiagnostic(runId: string): Promise<SecurityDiagnostic> {
  return request(`/api/security/runs/${encodeURIComponent(runId)}/diagnostic`, parseSecurityDiagnostic);
}

export function importScenarioZip(filename: string, contentBase64: string): Promise<ScenarioImportResult> {
  return request("/api/security/imports", parseScenarioImport, {
    method: "POST",
    body: JSON.stringify({
      approved: true,
      idempotency_key: nextCommandKey("scenario-import"),
      filename,
      content_base64: contentBase64
    })
  });
}

export function setCodexEnabled(enabled: boolean): Promise<CodexCapability> {
  return request("/api/security/codex/enable", parseCodexCapability, {
    method: "POST",
    body: JSON.stringify({
      approved: true,
      idempotency_key: nextCommandKey("codex-enable"),
      enabled
    })
  });
}
