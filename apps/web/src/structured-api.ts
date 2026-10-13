import type { components } from "@agent-trace/contracts";

import { nextCommandKey } from "./api";

export type StructuredLesson = components["schemas"]["StructuredLesson"];
export type StructuredRun = components["schemas"]["StructuredRun"];
export type SchemaSubmissionResult = components["schemas"]["SchemaSubmissionResult"];
export type ValidationArtifact = components["schemas"]["ValidationArtifact"];

type LessonId = StructuredLesson["id"];
type ProviderName = StructuredRun["provider"];

const REPAIR_EVENT_TYPES = [
  "schema.validation_started",
  "schema.validation_failed",
  "schema.validation_passed",
  "provider.retry_requested",
  "provider.retry_completed",
  "interrupt.created",
  "interrupt.invalidated",
  "interrupt.resolved",
  "schema.revision_applied",
  "course.assertion_recorded"
] as const;

const SEMVER_PATTERN = /^\d+\.\d+\.\d+$/;
const SHA256_PATTERN = /^[a-f0-9]{64}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const expected = new Set(keys);
  return Object.keys(value).every((key) => expected.has(key))
    && keys.every((key) => key in value);
}

function isJsonRecord(value: unknown): value is Record<string, components["schemas"]["JsonValue"]> {
  return isRecord(value) && Object.values(value).every(isJsonValue);
}

function isJsonValue(value: unknown): value is components["schemas"]["JsonValue"] {
  return value === null
    || typeof value === "string"
    || typeof value === "boolean"
    || (typeof value === "number" && Number.isFinite(value))
    || (Array.isArray(value) && value.every(isJsonValue))
    || (isRecord(value) && Object.values(value).every(isJsonValue));
}

function isError(value: unknown): boolean {
  return isRecord(value)
    && exactKeys(value, ["path", "keyword", "message"])
    && [value.path, value.keyword, value.message].every((item) => typeof item === "string");
}

function parseLesson(value: unknown): StructuredLesson {
  if (
    !isRecord(value)
    || !exactKeys(value, ["id", "domain", "title", "objective", "default_schema", "valid_output", "exercises"])
    || !["route-decision", "answer-payload"].includes(String(value.id))
    || !["support", "analytics"].includes(String(value.domain))
    || ![value.title, value.objective, value.valid_output].every((item) => typeof item === "string")
    || !isJsonRecord(value.default_schema)
    || !Array.isArray(value.exercises)
    || !value.exercises.every((exercise) => isRecord(exercise)
      && Object.keys(exercise).every((key) => ["id", "label", "prediction_prompt", "raw_output", "schema_document"].includes(key))
      && ["required", "enum", "additional-properties", "nullable", "breaking", "semantic"].includes(String(exercise.id))
      && [exercise.label, exercise.prediction_prompt, exercise.raw_output].every((item) => typeof item === "string")
      && (exercise.schema_document === undefined || exercise.schema_document === null || isJsonRecord(exercise.schema_document)))
  ) throw new Error("structured lessons crossed an invalid typed boundary");
  return value as StructuredLesson;
}

function isRevision(value: unknown): boolean {
  return isRecord(value)
    && exactKeys(value, ["version", "schema_hash", "schema_document", "compatibility", "breaking"])
    && typeof value.version === "string"
    && SEMVER_PATTERN.test(value.version)
    && typeof value.schema_hash === "string"
    && SHA256_PATTERN.test(value.schema_hash)
    && isJsonRecord(value.schema_document)
    && typeof value.breaking === "boolean"
    && Array.isArray(value.compatibility)
    && value.compatibility.every((change) => isRecord(change)
      && exactKeys(change, ["path", "kind", "message", "breaking"])
      && typeof change.path === "string"
      && ["required_added", "enum_removed", "type_changed", "constraint_tightened"].includes(String(change.kind))
      && typeof change.message === "string"
      && typeof change.breaking === "boolean");
}

function isArtifact(value: unknown): value is ValidationArtifact {
  return isRecord(value)
    && exactKeys(value, ["artifact_id", "attempt", "provider", "schema_version", "schema_hash", "raw_output", "parsed_output", "validated_output", "result_kind", "errors"])
    && typeof value.artifact_id === "string"
    && /^artifact_[A-Za-z0-9_-]+$/.test(value.artifact_id)
    && Number.isInteger(value.attempt)
    && Number(value.attempt) >= 1
    && Number(value.attempt) <= 2
    && ["mock", "manual"].includes(String(value.provider))
    && typeof value.schema_version === "string"
    && SEMVER_PATTERN.test(value.schema_version)
    && typeof value.schema_hash === "string"
    && SHA256_PATTERN.test(value.schema_hash)
    && typeof value.raw_output === "string"
    && (value.parsed_output === null || isJsonValue(value.parsed_output))
    && (value.validated_output === null || isJsonValue(value.validated_output))
    && ["invalid_json", "schema_invalid", "semantic_invalid", "passed"].includes(String(value.result_kind))
    && Array.isArray(value.errors)
    && value.errors.every(isError);
}

function isProviderRequest(value: unknown): value is Record<string, unknown> {
  return isRecord(value)
    && exactKeys(value, ["schema_id", "provider", "run_id", "lesson_id", "schema_version", "schema_hash", "schema_document", "attempt", "failed_artifact_id", "raw_output", "errors"])
    && value.schema_id === "agent-trace://structured-provider-request/1.0.0"
    && ["mock", "manual"].includes(String(value.provider))
    && typeof value.run_id === "string"
    && /^run_[A-Za-z0-9_-]+$/.test(value.run_id)
    && ["route-decision", "answer-payload"].includes(String(value.lesson_id))
    && typeof value.schema_version === "string"
    && SEMVER_PATTERN.test(value.schema_version)
    && typeof value.schema_hash === "string"
    && SHA256_PATTERN.test(value.schema_hash)
    && isJsonRecord(value.schema_document)
    && value.attempt === 1
    && typeof value.failed_artifact_id === "string"
    && /^artifact_[A-Za-z0-9_-]+$/.test(value.failed_artifact_id)
    && typeof value.raw_output === "string"
    && value.raw_output.length > 0
    && Array.isArray(value.errors)
    && value.errors.length > 0
    && value.errors.every(isError);
}

function isProviderResult(value: unknown): value is Record<string, unknown> {
  if (!isRecord(value)
    || value.schema_id !== "agent-trace://structured-provider-result/1.0.0"
    || !["mock", "manual"].includes(String(value.provider))
    || typeof value.run_id !== "string"
    || !/^run_[A-Za-z0-9_-]+$/.test(value.run_id)
    || typeof value.schema_version !== "string"
    || !SEMVER_PATTERN.test(value.schema_version)
    || typeof value.schema_hash !== "string"
    || !SHA256_PATTERN.test(value.schema_hash)
    || value.attempt !== 1) return false;
  if (value.kind === "payload") {
    return exactKeys(value, ["schema_id", "kind", "provider", "run_id", "schema_version", "schema_hash", "attempt", "raw_output"])
      && value.provider === "mock"
      && typeof value.raw_output === "string"
      && value.raw_output.length > 0;
  }
  return value.kind === "interrupt"
    && exactKeys(value, ["schema_id", "kind", "provider", "run_id", "schema_version", "schema_hash", "attempt", "interrupt_id", "reason"])
    && value.provider === "manual"
    && typeof value.interrupt_id === "string"
    && /^interrupt_[A-Za-z0-9_-]+$/.test(value.interrupt_id)
    && value.reason === "structured_output_repair";
}

function isProviderExchange(value: unknown, index: number): boolean {
  if (!isRecord(value)
    || !exactKeys(value, ["sequence", "request", "result"])
    || value.sequence !== index + 1
    || !isProviderRequest(value.request)
    || !isProviderResult(value.result)) return false;
  const request = value.request;
  const result = value.result;
  return request.provider === result.provider
    && request.run_id === result.run_id
    && request.schema_version === result.schema_version
    && request.schema_hash === result.schema_hash
    && request.attempt === result.attempt;
}

export function parseStructuredRun(value: unknown): StructuredRun {
  if (
    !isRecord(value)
    || !exactKeys(value, ["schema_id", "run_id", "lesson_id", "provider", "status", "active_schema_version", "schema_history", "artifacts", "provider_exchanges", "repair_trace", "pending_interrupt", "progress"])
    || value.schema_id !== "agent-trace://structured-run/1.0.0"
    || typeof value.run_id !== "string"
    || !/^run_[A-Za-z0-9_-]+$/.test(value.run_id)
    || !["route-decision", "answer-payload"].includes(String(value.lesson_id))
    || !["mock", "manual"].includes(String(value.provider))
    || !["ready", "awaiting_manual", "failed", "passed"].includes(String(value.status))
    || typeof value.active_schema_version !== "string"
    || !SEMVER_PATTERN.test(value.active_schema_version)
    || !Array.isArray(value.schema_history)
    || !value.schema_history.every(isRevision)
    || !Array.isArray(value.artifacts)
    || !value.artifacts.every(isArtifact)
    || !Array.isArray(value.provider_exchanges)
    || !value.provider_exchanges.every(isProviderExchange)
    || !Array.isArray(value.repair_trace)
    || !value.repair_trace.every((event, index) => isRecord(event)
      && exactKeys(event, ["sequence", "type", "message"])
      && event.sequence === index + 1
      && REPAIR_EVENT_TYPES.includes(event.type as typeof REPAIR_EVENT_TYPES[number])
      && typeof event.message === "string")
    || !(value.pending_interrupt === null || isRecord(value.pending_interrupt)
      && exactKeys(value.pending_interrupt, ["interrupt_id", "reason", "expected_schema_version", "attempt"])
      && typeof value.pending_interrupt.interrupt_id === "string"
      && /^interrupt_[A-Za-z0-9_-]+$/.test(value.pending_interrupt.interrupt_id)
      && value.pending_interrupt.reason === "structured_output_repair"
      && typeof value.pending_interrupt.expected_schema_version === "string"
      && SEMVER_PATTERN.test(value.pending_interrupt.expected_schema_version)
      && value.pending_interrupt.attempt === 1)
    || !isRecord(value.progress)
    || !exactKeys(value.progress, ["prediction", "prediction_recorded", "failure_observed", "repair_completed", "assertion_recorded", "completed"])
    || !["pass", "fail"].includes(String(value.progress.prediction))
    || ![value.progress.prediction_recorded, value.progress.failure_observed, value.progress.repair_completed, value.progress.assertion_recorded, value.progress.completed].every((item) => typeof item === "boolean")
  ) throw new Error("structured run crossed an invalid typed boundary");
  if (value.schema_history.at(-1)?.version !== value.active_schema_version) {
    throw new Error("structured run active schema is inconsistent with immutable history");
  }
  if (value.status === "awaiting_manual" && value.pending_interrupt === null) {
    throw new Error("structured run is awaiting a missing typed interrupt");
  }
  if (value.status !== "awaiting_manual" && value.pending_interrupt !== null) {
    throw new Error("structured run exposes an interrupt outside awaiting state");
  }
  if (value.progress.repair_completed && !value.progress.failure_observed) {
    throw new Error("structured run completed repair before observing failure");
  }
  if (value.progress.assertion_recorded && !value.progress.repair_completed) {
    throw new Error("structured run recorded an assertion before repair");
  }
  const allCourseGates = value.progress.prediction_recorded
    && value.progress.failure_observed
    && value.progress.repair_completed
    && value.progress.assertion_recorded;
  if (value.progress.completed !== allCourseGates) {
    throw new Error("structured run course completion disagrees with its gates");
  }
  return value as StructuredRun;
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

function approved(action: string, values: Record<string, unknown>): RequestInit {
  return {
    method: "POST",
    body: JSON.stringify({ approved: true, idempotency_key: nextCommandKey(action), ...values })
  };
}

export async function loadStructuredLessons(signal?: AbortSignal): Promise<StructuredLesson[]> {
  const value = await readJson("/api/structured/lessons", { signal });
  if (!isRecord(value)
    || !exactKeys(value, ["schema_id", "lessons"])
    || value.schema_id !== "agent-trace://structured-lessons/1.0.0"
    || !Array.isArray(value.lessons)) {
    throw new Error("structured lessons returned an invalid envelope");
  }
  return value.lessons.map(parseLesson);
}

export async function startStructuredRun(
  lessonId: LessonId,
  provider: ProviderName,
  prediction: "pass" | "fail"
): Promise<StructuredRun> {
  return parseStructuredRun(await readJson(
    "/api/structured/runs",
    approved("structured-start", { lesson_id: lessonId, provider, prediction })
  ));
}

export async function validateStructuredOutput(runId: string, rawOutput: string): Promise<StructuredRun> {
  return parseStructuredRun(await readJson(
    `/api/structured/runs/${encodeURIComponent(runId)}/validate`,
    approved("structured-validate", { raw_output: rawOutput })
  ));
}

export async function resumeManualStructuredOutput(
  runId: string,
  interruptId: string,
  rawOutput: string
): Promise<StructuredRun> {
  return parseStructuredRun(await readJson(
    `/api/structured/runs/${encodeURIComponent(runId)}/manual`,
    approved("structured-manual", { interrupt_id: interruptId, raw_output: rawOutput })
  ));
}

export async function applyStructuredSchema(
  runId: string,
  version: string,
  schemaText: string
): Promise<SchemaSubmissionResult> {
  const value = await readJson(
    `/api/structured/runs/${encodeURIComponent(runId)}/schema`,
    approved("structured-schema", { version, schema_text: schemaText })
  );
  if (!isRecord(value)
    || !exactKeys(value, ["applied", "check", "run"])
    || typeof value.applied !== "boolean"
    || !isRecord(value.check)
    || !exactKeys(value.check, ["kind", "errors"])
    || !["valid", "invalid_json", "schema_invalid"].includes(String(value.check.kind))
    || !Array.isArray(value.check.errors)
    || !value.check.errors.every(isError)) {
    throw new Error("schema submission crossed an invalid typed boundary");
  }
  return { applied: value.applied, check: value.check, run: parseStructuredRun(value.run) } as SchemaSubmissionResult;
}

export async function recordStructuredAssertion(runId: string): Promise<StructuredRun> {
  return parseStructuredRun(await readJson(
    `/api/structured/runs/${encodeURIComponent(runId)}/assertion`,
    approved("structured-assertion", { assertion: true })
  ));
}
