import type { components } from "@agent-trace/contracts";

import { nextCommandKey } from "./api";

export type ToolCatalog = components["schemas"]["ToolCatalogResponse"];
export type ToolCatalogEntry = components["schemas"]["ToolCatalogEntry"];
export type ToolFaultCase = components["schemas"]["ToolFaultCase"];
export type ToolLabRun = components["schemas"]["ToolLabRun"];
export type ToolRisk = components["schemas"]["ToolSpec"]["risk"];
export type ActorRole = components["schemas"]["ToolRequest"]["actor_role"];

const RISKS = ["read", "compute", "propose", "write", "restricted"] as const;
const ROLES = ["learner", "reviewer", "approver", "system"] as const;
const FAULTS = ["none", "unknown_tool", "missing_argument", "invalid_argument", "permission_denied", "timeout", "empty", "invalid_output"] as const;
const ERROR_CODES = ["TOOL_NOT_FOUND", "TOOL_VERSION_UNSUPPORTED", "ARGUMENT_MISSING", "ARGUMENT_SCHEMA_INVALID", "ENTITY_UNRESOLVED", "PERMISSION_DENIED", "RISK_MISMATCH", "APPROVAL_REQUIRED", "TIMEOUT", "EMPTY_RESULT", "OUTPUT_SCHEMA_INVALID", "DUPLICATE_REQUEST", "SIDE_EFFECT_UNKNOWN", "INTERNAL_ERROR"] as const;
const STAGES = ["resolve", "input_validation", "authorize", "policy", "approval", "idempotency", "execute", "output_validation", "redact", "audit"] as const;
const AUDIT_STATUSES = ["passed", "failed", "blocked", "skipped"] as const;
const ACTIONS = ["approve", "reject", "edit", "request_evidence"] as const;
const SEMVER = /^\d+\.\d+\.\d+$/;
const TOOL_NAME = /^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$/;
const ERROR_CODE = /^[A-Z0-9_]+$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const expected = new Set(keys);
  return Object.keys(value).every((key) => expected.has(key))
    && keys.every((key) => key in value);
}

function isJsonValue(value: unknown): value is components["schemas"]["JsonValue"] {
  return value === null
    || typeof value === "string"
    || typeof value === "boolean"
    || (typeof value === "number" && Number.isFinite(value))
    || (Array.isArray(value) && value.every(isJsonValue))
    || (isRecord(value) && Object.values(value).every(isJsonValue));
}

function isJsonRecord(value: unknown): value is Record<string, components["schemas"]["JsonValue"]> {
  return isRecord(value) && Object.values(value).every(isJsonValue);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isUniqueStringArray(value: unknown, requireNonEmpty = false): value is string[] {
  return isStringArray(value)
    && new Set(value).size === value.length
    && (!requireNonEmpty || value.every((item) => item.length > 0));
}

function isDate(value: unknown): value is string {
  return typeof value === "string" && !Number.isNaN(Date.parse(value));
}

function isErrorCode(value: unknown): boolean {
  return value === null || value === undefined || ERROR_CODES.includes(value as typeof ERROR_CODES[number]);
}

function isValidationIssue(value: unknown): boolean {
  return isRecord(value)
    && exactKeys(value, ["path", "keyword", "message"])
    && [value.path, value.keyword, value.message].every((item) => typeof item === "string");
}

function isToolSpec(value: unknown): value is components["schemas"]["ToolSpec"] {
  return isRecord(value)
    && exactKeys(value, ["name", "version", "description", "risk", "input_schema", "output_schema", "permissions", "timeout_ms", "retry", "idempotency", "redact", "errors", "schema_version", "explanation"])
    && typeof value.name === "string"
    && TOOL_NAME.test(value.name)
    && typeof value.version === "string"
    && SEMVER.test(value.version)
    && typeof value.description === "string"
    && value.description.length >= 12
    && value.description.length <= 500
    && RISKS.includes(value.risk as ToolRisk)
    && isJsonRecord(value.input_schema)
    && isJsonRecord(value.output_schema)
    && isUniqueStringArray(value.permissions, true)
    && Number.isInteger(value.timeout_ms)
    && Number(value.timeout_ms) >= 50
    && Number(value.timeout_ms) <= 120_000
    && isRecord(value.retry)
    && exactKeys(value.retry, ["max_attempts", "retryable_errors", "backoff_ms"])
    && Number.isInteger(value.retry.max_attempts)
    && Number(value.retry.max_attempts) >= 1
    && Number(value.retry.max_attempts) <= 5
    && isUniqueStringArray(value.retry.retryable_errors)
    && Number.isInteger(value.retry.backoff_ms)
    && Number(value.retry.backoff_ms) >= 0
    && Number(value.retry.backoff_ms) <= 30_000
    && isRecord(value.idempotency)
    && Object.keys(value.idempotency).every((key) => ["mode", "key_fields"].includes(key))
    && "mode" in value.idempotency
    && ["not_applicable", "request_key", "business_key"].includes(String(value.idempotency.mode))
    && (value.idempotency.key_fields === undefined || isUniqueStringArray(value.idempotency.key_fields))
    && isUniqueStringArray(value.redact)
    && Array.isArray(value.errors)
    && value.errors.every((error) => isRecord(error)
      && exactKeys(error, ["code", "retryable", "user_message"])
      && typeof error.code === "string"
      && ERROR_CODE.test(error.code)
      && typeof error.retryable === "boolean"
      && typeof error.user_message === "string"
      && error.user_message.length > 0)
    && value.schema_version === "1.0.0"
    && (value.explanation === null || typeof value.explanation === "string");
}

function isCatalogEntry(value: unknown): value is ToolCatalogEntry {
  return isRecord(value)
    && exactKeys(value, ["domain", "spec", "example_arguments"])
    && ["customer-support", "data-analyst"].includes(String(value.domain))
    && isToolSpec(value.spec)
    && isJsonRecord(value.example_arguments);
}

function parseToolCatalog(value: unknown): ToolCatalog {
  if (!isRecord(value)
    || !exactKeys(value, ["schema_id", "tools", "faults"])
    || value.schema_id !== "agent-trace://tool-catalog/1.0.0"
    || !Array.isArray(value.tools)
    || value.tools.length < 2
    || !value.tools.every(isCatalogEntry)
    || new Set(value.tools.map((entry) => `${entry.spec.name}@${entry.spec.version}`)).size !== value.tools.length
    || !Array.isArray(value.faults)
    || !value.faults.every((fault) => isRecord(fault)
      && exactKeys(fault, ["id", "label", "stage", "expected_error", "description"])
      && FAULTS.includes(fault.id as ToolFaultCase["id"])
      && typeof fault.label === "string"
      && (fault.stage === "none" || STAGES.includes(fault.stage as typeof STAGES[number]))
      && isErrorCode(fault.expected_error)
      && typeof fault.description === "string")) {
    throw new Error("tool catalog crossed an invalid typed boundary");
  }
  return value as ToolCatalog;
}

function isToolRequest(value: unknown): boolean {
  return isRecord(value)
    && exactKeys(value, ["schema_id", "request_id", "run_id", "node_id", "tool_name", "tool_version", "arguments", "actor_role", "granted_permissions", "model_rationale", "requested_at", "expected_risk", "correlation_id", "provider_output_ref"])
    && value.schema_id === "agent-trace://tool-request/1.0.0"
    && typeof value.request_id === "string"
    && /^toolreq_[A-Za-z0-9_-]+$/.test(value.request_id)
    && typeof value.run_id === "string"
    && /^run_[A-Za-z0-9_-]+$/.test(value.run_id)
    && value.node_id === "tool_execute"
    && typeof value.tool_name === "string"
    && typeof value.tool_version === "string"
    && SEMVER.test(value.tool_version)
    && isJsonRecord(value.arguments)
    && ROLES.includes(value.actor_role as ActorRole)
    && isStringArray(value.granted_permissions)
    && typeof value.model_rationale === "string"
    && isDate(value.requested_at)
    && RISKS.includes(value.expected_risk as ToolRisk)
    && typeof value.correlation_id === "string"
    && /^corr_[A-Za-z0-9_-]+$/.test(value.correlation_id)
    && typeof value.provider_output_ref === "string"
    && value.provider_output_ref.startsWith("mock-provider://tool-proposal/");
}

function isToolError(value: unknown): boolean {
  return isRecord(value)
    && exactKeys(value, ["code", "stage", "retryable", "user_safe_message", "issues"])
    && isErrorCode(value.code)
    && value.code !== null
    && STAGES.includes(value.stage as typeof STAGES[number])
    && typeof value.retryable === "boolean"
    && typeof value.user_safe_message === "string"
    && Array.isArray(value.issues)
    && value.issues.every(isValidationIssue);
}

function isApproval(value: unknown): boolean {
  return isRecord(value)
    && exactKeys(value, ["status", "exact_action", "exact_target", "args_diff", "evidence", "reason", "risk", "side_effects", "idempotency_key_summary", "expires_at", "available_actions", "implementation_status"])
    && value.status === "required"
    && [value.exact_action, value.exact_target, value.reason, value.idempotency_key_summary].every((item) => typeof item === "string")
    && isJsonRecord(value.args_diff)
    && isStringArray(value.evidence)
    && value.risk === "write"
    && isStringArray(value.side_effects)
    && isDate(value.expires_at)
    && Array.isArray(value.available_actions)
    && value.available_actions.every((action) => ACTIONS.includes(action as typeof ACTIONS[number]))
    && value.implementation_status === "locked_until_e7";
}

function isToolResult(value: unknown): boolean {
  return isRecord(value)
    && exactKeys(value, ["schema_id", "request_id", "status", "data", "error", "started_at", "ended_at", "duration_ms", "attempts", "cache_status", "side_effect_receipt", "provenance", "redaction_summary", "approval"])
    && value.schema_id === "agent-trace://tool-result/1.0.0"
    && typeof value.request_id === "string"
    && ["succeeded", "failed", "approval_required"].includes(String(value.status))
    && (value.data === null || isJsonValue(value.data))
    && (value.error === null || isToolError(value.error))
    && isDate(value.started_at)
    && isDate(value.ended_at)
    && Number.isInteger(value.duration_ms)
    && Number(value.duration_ms) >= 0
    && Array.isArray(value.attempts)
    && value.attempts.length <= 5
    && value.attempts.every((attempt, index) => isRecord(attempt)
      && exactKeys(attempt, ["attempt", "status", "error_code", "duration_ms"])
      && attempt.attempt === index + 1
      && ["succeeded", "failed"].includes(String(attempt.status))
      && isErrorCode(attempt.error_code)
      && Number.isInteger(attempt.duration_ms)
      && Number(attempt.duration_ms) >= 0)
    && ["miss", "idempotent_replay"].includes(String(value.cache_status))
    && value.side_effect_receipt === null
    && typeof value.provenance === "string"
    && isRecord(value.redaction_summary)
    && exactKeys(value.redaction_summary, ["applied", "paths", "replacement"])
    && typeof value.redaction_summary.applied === "boolean"
    && isStringArray(value.redaction_summary.paths)
    && value.redaction_summary.replacement === "[REDACTED]"
    && (value.approval === null || isApproval(value.approval));
}

export function parseToolRun(value: unknown): ToolLabRun {
  if (!isRecord(value)
    || !exactKeys(value, ["schema_id", "run_id", "status", "proposal_label", "execution_label", "spec", "request", "result", "audit"])
    || value.schema_id !== "agent-trace://tool-lab-run/1.0.0"
    || typeof value.run_id !== "string"
    || !/^run_[A-Za-z0-9_-]+$/.test(value.run_id)
    || !["succeeded", "failed", "approval_required"].includes(String(value.status))
    || value.proposal_label !== "LLM PROPOSED"
    || !["CODE EXECUTED", "CODE BLOCKED"].includes(String(value.execution_label))
    || !(value.spec === null || isToolSpec(value.spec))
    || !isToolRequest(value.request)
    || !isToolResult(value.result)
    || !Array.isArray(value.audit)
    || !value.audit.every((entry, index) => isRecord(entry)
      && exactKeys(entry, ["sequence", "stage", "status", "policy_version", "actor_role", "action", "resource", "run_id", "correlation_id", "message", "timestamp"])
      && entry.sequence === index + 1
      && STAGES.includes(entry.stage as typeof STAGES[number])
      && AUDIT_STATUSES.includes(entry.status as typeof AUDIT_STATUSES[number])
      && entry.policy_version === "agent-trace://tool-policy/1.0.0"
      && ROLES.includes(entry.actor_role as ActorRole)
      && typeof entry.action === "string"
      && typeof entry.resource === "string"
      && entry.run_id === value.run_id
      && typeof entry.correlation_id === "string"
      && typeof entry.message === "string"
      && isDate(entry.timestamp))) {
    throw new Error("tool run crossed an invalid typed boundary");
  }
  const request = value.request as components["schemas"]["ToolRequest"];
  const result = value.result as components["schemas"]["ToolResult"];
  const spec = value.spec as components["schemas"]["ToolSpec"] | null;
  const startedAt = Date.parse(result.started_at);
  const endedAt = Date.parse(result.ended_at);
  const auditTimes = value.audit.map((entry) => Date.parse(String(entry.timestamp)));
  if (endedAt - startedAt !== result.duration_ms
    || auditTimes.some((timestamp, index) => timestamp < startedAt
      || timestamp > endedAt
      || (index > 0 && timestamp < auditTimes[index - 1]))) {
    throw new Error("tool run timing disagrees across result and audit envelopes");
  }
  if (request.run_id !== value.run_id || result.request_id !== request.request_id) {
    throw new Error("tool run identifiers disagree across typed envelopes");
  }
  if (value.audit.some((entry) => entry.actor_role !== request.actor_role
    || entry.action !== request.tool_name
    || entry.resource !== `tool://${request.tool_name}@${request.tool_version}`
    || entry.correlation_id !== request.correlation_id)) {
    throw new Error("tool audit context disagrees with its typed request");
  }
  if (value.status !== result.status) {
    throw new Error("tool run status disagrees with its result");
  }
  if (spec && (spec.name !== request.tool_name || spec.version !== request.tool_version)) {
    throw new Error("tool run request disagrees with the registry-owned spec");
  }
  if (value.status === "succeeded" && (result.data == null || result.error != null)) {
    throw new Error("successful tool run lacks validated data");
  }
  if (value.status === "failed" && (result.data != null || result.error == null)) {
    throw new Error("failed tool run has an invalid result projection");
  }
  if (value.status === "approval_required" && (result.approval == null || result.attempts.length > 0 || value.execution_label !== "CODE BLOCKED")) {
    throw new Error("write approval boundary did not fail closed");
  }
  return value as ToolLabRun;
}

async function readJson(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(path, {
    ...init,
    headers: { Accept: "application/json", "Content-Type": "application/json", ...init?.headers }
  });
  const value: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = isRecord(value) && typeof value.detail === "string" ? value.detail : `HTTP ${response.status}`;
    throw new Error(`${path} returned ${detail}`);
  }
  return value;
}

export async function loadToolCatalog(signal?: AbortSignal): Promise<ToolCatalog> {
  return parseToolCatalog(await readJson("/api/tools/catalog", { signal }));
}

export async function executeTool(
  entry: ToolCatalogEntry,
  actorRole: ActorRole,
  fault: ToolFaultCase["id"]
): Promise<ToolLabRun> {
  return parseToolRun(await readJson("/api/tools/runs", {
    method: "POST",
    body: JSON.stringify({
      approved: true,
      idempotency_key: nextCommandKey("tool-run"),
      tool_name: entry.spec.name,
      tool_version: entry.spec.version,
      arguments: entry.example_arguments,
      actor_role: actorRole,
      expected_risk: entry.spec.risk,
      model_rationale: `Typed mock provider proposed ${entry.spec.name}; registry code owns execution.`,
      fault,
      seed: 2026
    })
  }));
}
