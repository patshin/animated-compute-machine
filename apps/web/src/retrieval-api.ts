import type { components } from "@agent-trace/contracts";

import { nextCommandKey, parseAgentEvent } from "./api";

export type RetrievalCatalog = components["schemas"]["RetrievalCatalog"];
export type RetrievalScenario = components["schemas"]["agent_trace_api__retrieval_models__ScenarioSummary"];
export type RetrievalRun = components["schemas"]["RetrievalRun"];
export type RetrievedChunk = components["schemas"]["RetrievedChunk"];
export type Evidence = components["schemas"]["Evidence"];
export type MemorySnapshot = components["schemas"]["MemorySnapshot"];
export type Authority = components["schemas"]["Evidence"]["authority"];
export type PreferenceValue = components["schemas"]["PreferenceRecord"]["value"];

export interface RetrievalOptions {
  authorities: Authority[];
  includeSuperseded: boolean;
  exactPhrase: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStrings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isFiniteNumbers(value: unknown): value is Record<string, number> {
  return isRecord(value)
    && Object.values(value).every((item) => typeof item === "number" && Number.isFinite(item));
}

function isDate(value: unknown): value is string {
  return typeof value === "string" && !Number.isNaN(Date.parse(value));
}

const authorities = ["primary", "official", "internal", "secondary", "unknown"] as const;
const freshness = ["current", "aging", "stale", "superseded", "unknown"] as const;
const scenarioIds = ["research-synthesis", "risk-investigation"] as const;

function isScenario(value: unknown): value is RetrievalScenario {
  return isRecord(value)
    && scenarioIds.includes(value.scenario_id as typeof scenarioIds[number])
    && typeof value.title === "string"
    && typeof value.description === "string"
    && typeof value.default_query === "string"
    && isStrings(value.query_chips);
}

function isChunk(value: unknown): value is RetrievedChunk {
  return isRecord(value)
    && typeof value.chunk_id === "string"
    && typeof value.source_id === "string"
    && typeof value.title === "string"
    && typeof value.section === "string"
    && typeof value.excerpt === "string"
    && typeof value.version === "string"
    && isDate(value.published_at)
    && authorities.includes(value.authority as Authority)
    && freshness.includes(value.freshness as typeof freshness[number])
    && (value.contradiction_group == null || typeof value.contradiction_group === "string")
    && typeof value.score === "number"
    && Number.isFinite(value.score)
    && isFiniteNumbers(value.score_breakdown)
    && typeof value.suspicious === "boolean"
    && typeof value.suppressed_instruction === "boolean";
}

function isEvidence(value: unknown, runId: string): value is Evidence {
  if (!isRecord(value)
    || value.schema_version !== "1.0.0"
    || typeof value.evidence_id !== "string"
    || !/^ev_[A-Za-z0-9_-]+$/.test(value.evidence_id)
    || !["document", "record", "metric", "calculation", "remote_artifact"].includes(String(value.kind))
    || !authorities.includes(value.authority as Authority)
    || !freshness.includes(value.freshness as typeof freshness[number])
    || !["public_fixture", "internal_fixture", "sensitive_fixture"].includes(String(value.classification))
    || !isRecord(value.source)
    || typeof value.source.source_id !== "string"
    || typeof value.source.title !== "string"
    || !["document", "database", "tool", "calculation", "remote_agent"].includes(String(value.source.source_type))
    || !isRecord(value.content)
    || (typeof value.content.excerpt !== "string" && !isRecord(value.content.facts))
    || !isRecord(value.retrieval)
    || !["fts5", "tool", "calculation", "remote"].includes(String(value.retrieval.method))
    || !isRecord(value.provenance)
    || value.provenance.run_id !== runId
    || !["retrieval", "tool", "compute", "remote_agent"].includes(String(value.provenance.created_by))) {
    return false;
  }
  return value.provenance.input_evidence_ids == null
    || isStrings(value.provenance.input_evidence_ids);
}

export function parseRetrievalCatalog(value: unknown): RetrievalCatalog {
  if (!isRecord(value)
    || value.schema_id !== "agent-trace://retrieval-catalog/1.0.0"
    || value.engine !== "sqlite-fts5-bm25"
    || value.offline !== true
    || !Array.isArray(value.scenarios)
    || !value.scenarios.every(isScenario)
    || !Array.isArray(value.memory_buckets)
    || !value.memory_buckets.every((bucket) => isRecord(bucket)
      && typeof bucket.bucket_id === "string"
      && typeof bucket.label === "string"
      && typeof bucket.retention === "string"
      && typeof bucket.mutable === "boolean"
      && typeof bucket.contains_preference === "boolean")) {
    throw new Error("retrieval catalog crossed an invalid typed boundary");
  }
  return value as RetrievalCatalog;
}

export function parseRetrievalRun(value: unknown): RetrievalRun {
  if (!isRecord(value)
    || value.schema_id !== "agent-trace://retrieval-run/1.0.0"
    || typeof value.run_id !== "string"
    || !/^run_[A-Za-z0-9_-]+$/.test(value.run_id)
    || !scenarioIds.includes(value.scenario_id as typeof scenarioIds[number])
    || !["completed", "empty"].includes(String(value.status))
    || value.retrieved_instruction_tool_calls !== 0
    || !isRecord(value.query)
    || typeof value.query.original !== "string"
    || typeof value.query.rewritten !== "string"
    || value.query.bounded !== true
    || !isStrings(value.query.added_terms)
    || !isRecord(value.filters)
    || (!Array.isArray(value.filters.authorities) && value.filters.authorities !== undefined)
    || (Array.isArray(value.filters.authorities)
      && !value.filters.authorities.every((authority) => authorities.includes(authority as Authority)))
    || typeof value.filters.include_superseded !== "boolean"
    || typeof value.filters.exact_phrase !== "boolean"
    || !Array.isArray(value.chunks)
    || !value.chunks.every(isChunk)
    || !Array.isArray(value.evidence)
    || !value.evidence.every((item) => isEvidence(item, value.run_id as string))
    || !Array.isArray(value.claims)
    || !value.claims.every((claim) => isRecord(claim)
      && typeof claim.claim_id === "string"
      && typeof claim.text === "string"
      && ["assertion", "derived"].includes(String(claim.kind))
      && typeof claim.required === "boolean"
      && isStrings(claim.citation_ids))
    || !isRecord(value.citation_validation)
    || typeof value.citation_validation.valid !== "boolean"
    || typeof value.citation_validation.coverage_percent !== "number"
    || !Number.isFinite(value.citation_validation.coverage_percent)
    || value.citation_validation.coverage_percent < 0
    || value.citation_validation.coverage_percent > 100
    || !isStrings(value.citation_validation.issues)
    || !isStrings(value.citation_validation.warnings)
    || !Array.isArray(value.events)
    || !Array.isArray(value.tool_executions)
    || !value.tool_executions.every((execution) => isRecord(execution)
      && typeof execution.tool_name === "string"
      && typeof execution.request_id === "string"
      && execution.status === "succeeded"
      && execution.purpose === "scenario_planned_context")
    || !isRecord(value.import_report)
    || value.import_report.scenario_id !== value.scenario_id
    || !Number.isInteger(value.import_report.inserted_chunks)
    || !Number.isInteger(value.import_report.deduplicated_chunks)
    || !Number.isInteger(value.import_report.source_count)
    || !isStrings(value.import_report.stable_chunk_ids)) {
    throw new Error("retrieval run crossed an invalid typed boundary");
  }
  const events = value.events.map(parseAgentEvent);
  if (events.some((event, index) => event.run_id !== value.run_id || event.sequence !== index + 1)) {
    throw new Error("retrieval run returned an inconsistent event trajectory");
  }
  const evidenceIds = new Set((value.evidence as Evidence[]).map((item) => item.evidence_id));
  const chunkIds = (value.chunks as RetrievedChunk[]).map((chunk) => chunk.chunk_id);
  if (chunkIds.length !== new Set(chunkIds).size || evidenceIds.size !== value.evidence.length) {
    throw new Error("retrieval run returned duplicate typed identifiers");
  }
  if ((value.claims as RetrievalRun["claims"]).some((claim) =>
    claim.citation_ids.some((citation) => !evidenceIds.has(citation)))) {
    throw new Error("retrieval run returned a dangling citation");
  }
  const derivedWithoutInputs = (value.claims as RetrievalRun["claims"]).some((claim) =>
    claim.kind === "derived" && !claim.citation_ids.some((citation) => {
      const item = (value.evidence as Evidence[]).find((evidence) => evidence.evidence_id === citation);
      return item?.kind === "calculation"
        && Boolean(item.provenance.input_evidence_ids?.length)
        && item.provenance.input_evidence_ids?.every((inputId) => evidenceIds.has(inputId));
    }));
  if (derivedWithoutInputs) {
    throw new Error("retrieval run returned an unsupported derived claim");
  }
  return { ...value, events } as RetrievalRun;
}

export function parseMemorySnapshot(value: unknown): MemorySnapshot {
  if (!isRecord(value)
    || value.schema_id !== "agent-trace://memory-snapshot/1.0.0"
    || value.storage !== "repository-local-sqlite"
    || !Array.isArray(value.preferences)
    || !value.preferences.every((preference) => isRecord(preference)
      && typeof preference.preference_id === "string"
      && preference.key === "answer_detail"
      && ["concise", "balanced", "detailed"].includes(String(preference.value))
      && preference.scope === "long_term"
      && isDate(preference.created_at)
      && isDate(preference.updated_at))
    || !Array.isArray(value.audit)
    || !value.audit.every((entry) => isRecord(entry)
      && Number.isInteger(entry.sequence)
      && ["preference.saved", "preference.deleted"].includes(String(entry.action))
      && typeof entry.preference_id === "string"
      && typeof entry.idempotency_key === "string"
      && isDate(entry.timestamp))) {
    throw new Error("memory snapshot crossed an invalid typed boundary");
  }
  return value as MemorySnapshot;
}

async function request<T>(
  path: string,
  parse: (value: unknown) => T,
  init?: RequestInit
): Promise<T> {
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

export function loadRetrievalCatalog(signal?: AbortSignal): Promise<RetrievalCatalog> {
  return request("/api/retrieval/catalog", parseRetrievalCatalog, { signal });
}

export function loadMemory(signal?: AbortSignal): Promise<MemorySnapshot> {
  return request("/api/retrieval/memory", parseMemorySnapshot, { signal });
}

export function startRetrievalRun(
  scenarioId: RetrievalScenario["scenario_id"],
  query: string,
  options: RetrievalOptions
): Promise<RetrievalRun> {
  return request("/api/retrieval/runs", parseRetrievalRun, {
    method: "POST",
    body: JSON.stringify({
      approved: true,
      idempotency_key: nextCommandKey("retrieval"),
      scenario_id: scenarioId,
      query,
      filters: {
        authorities: options.authorities,
        include_superseded: options.includeSuperseded,
        exact_phrase: options.exactPhrase
      },
      seed: 2026
    })
  });
}

export function savePreference(value: PreferenceValue): Promise<MemorySnapshot> {
  return request("/api/retrieval/memory/preferences", parseMemorySnapshot, {
    method: "POST",
    body: JSON.stringify({
      approved: true,
      confirmed: true,
      idempotency_key: nextCommandKey("save-preference"),
      key: "answer_detail",
      value
    })
  });
}

export function deletePreference(preferenceId: string): Promise<MemorySnapshot> {
  return request("/api/retrieval/memory/preferences/delete", parseMemorySnapshot, {
    method: "POST",
    body: JSON.stringify({
      approved: true,
      confirmed: true,
      idempotency_key: nextCommandKey("delete-preference"),
      preference_id: preferenceId
    })
  });
}
