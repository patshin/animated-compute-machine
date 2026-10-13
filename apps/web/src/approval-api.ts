import type { components } from "@agent-trace/contracts";

import { nextCommandKey, parseAgentEvent } from "./api";

export type ApprovalCatalog = components["schemas"]["ApprovalCatalog"];
export type ApprovalScenario = components["schemas"]["ApprovalScenario"];
export type ApprovalRun = components["schemas"]["ApprovalRun"];
export type ApprovalDecision = components["schemas"]["ResumeApprovalRequest"]["decision"];
export type StalenessDimension = components["schemas"]["StalenessRequest"]["dimension"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStrings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isDate(value: unknown): value is string {
  return typeof value === "string" && !Number.isNaN(Date.parse(value));
}

function isScenario(value: unknown): value is ApprovalScenario {
  return isRecord(value)
    && ["customer-refund", "purchase-approval", "incident-rollback", "risk-case-write"].includes(String(value.id))
    && typeof value.label === "string"
    && typeof value.summary === "string"
    && typeof value.action === "string"
    && typeof value.tool_version === "string"
    && typeof value.target === "string"
    && isRecord(value.default_args)
    && isRecord(value.editable_schema)
    && value.editable_schema.additionalProperties === false
    && isStrings(value.evidence)
    && typeof value.reason === "string"
    && isStrings(value.side_effects)
    && typeof value.required_permission === "string";
}

export function parseApprovalCatalog(value: unknown): ApprovalCatalog {
  if (!isRecord(value)
    || value.schema_id !== "agent-trace://approval-catalog/1.0.0"
    || !Array.isArray(value.scenarios)
    || value.scenarios.length !== 4
    || !value.scenarios.every(isScenario)
    || new Set(value.scenarios.map((scenario) => scenario.id)).size !== 4
    || !Array.isArray(value.matrix)
    || value.matrix.length !== 5
    || !value.matrix.every((entry) => isRecord(entry)
      && ["read", "compute", "propose", "write", "restricted"].includes(String(entry.risk))
      && ["allowed", "approval_required", "denied"].includes(String(entry.execution))
      && typeof entry.explanation === "string")) {
    throw new Error("approval catalog crossed an invalid typed boundary");
  }
  return value as ApprovalCatalog;
}

function isInterrupt(value: unknown): boolean {
  return isRecord(value)
    && value.schema_id === "agent-trace://approval-interrupt/1.0.0"
    && typeof value.interrupt_id === "string"
    && /^int_[A-Za-z0-9_-]+$/.test(value.interrupt_id)
    && typeof value.title === "string"
    && typeof value.exact_action === "string"
    && typeof value.exact_target === "string"
    && isRecord(value.arguments)
    && isRecord(value.editable_schema)
    && isStrings(value.evidence)
    && typeof value.reason === "string"
    && value.risk === "write"
    && isStrings(value.side_effects)
    && typeof value.idempotency_key_summary === "string"
    && isDate(value.created_at)
    && isDate(value.expires_at)
    && isRecord(value.version_references)
    && Number.isInteger(value.version_references.data_version)
    && typeof value.version_references.tool_version === "string"
    && Number.isInteger(value.version_references.target_version)
    && Array.isArray(value.available_actions)
    && value.available_actions.length === 4;
}

function isReceipt(value: unknown): boolean {
  return isRecord(value)
    && typeof value.receipt_id === "string"
    && /^receipt_[A-Za-z0-9_-]+$/.test(value.receipt_id)
    && typeof value.action === "string"
    && typeof value.target === "string"
    && isRecord(value.arguments)
    && typeof value.effect_key === "string"
    && isDate(value.committed_at)
    && Number.isInteger(value.checkpoint_version)
    && typeof value.idempotent_replay === "boolean";
}

export function parseApprovalRun(value: unknown): ApprovalRun {
  if (!isRecord(value)
    || value.schema_id !== "agent-trace://approval-run/1.0.0"
    || typeof value.run_id !== "string"
    || !/^run_[A-Za-z0-9_-]+$/.test(value.run_id)
    || !["customer-refund", "purchase-approval", "incident-rollback", "risk-case-write"].includes(String(value.scenario_id))
    || !["interrupted", "completed", "rejected"].includes(String(value.status))
    || !["learner", "reviewer", "approver", "system"].includes(String(value.proposer_role))
    || typeof value.user_message !== "string"
    || !Number.isInteger(value.checkpoint_version)
    || isNaN(Number(value.checkpoint_version))
    || Number(value.checkpoint_version) < 1
    || !isRecord(value.current_arguments)
    || !Array.isArray(value.argument_diff)
    || !value.argument_diff.every((entry) => isRecord(entry)
      && typeof entry.path === "string"
      && /^\$\.[a-z][a-z0-9_]*$/.test(entry.path)
      && "before" in entry
      && "after" in entry)
    || !(value.interrupt === null || isInterrupt(value.interrupt))
    || !(value.receipt === null || isReceipt(value.receipt))
    || typeof value.outcome_message !== "string"
    || !["interrupted", "approved", "edited_and_approved", "rejected", "evidence_added", "reapproval_required", "permission_denied"].includes(String(value.last_outcome))
    || !Array.isArray(value.events)
    || !Array.isArray(value.audit)
    || !value.audit.every((entry) => isRecord(entry)
      && Number.isInteger(entry.sequence)
      && typeof entry.actor_role === "string"
      && typeof entry.action === "string"
      && typeof entry.resource === "string"
      && typeof entry.decision === "string"
      && typeof entry.result === "string"
      && isDate(entry.timestamp))
    || !Array.isArray(value.checkpoints)
    || !value.checkpoints.every((checkpoint) => isRecord(checkpoint)
      && typeof checkpoint.checkpoint_id === "string"
      && Number.isInteger(checkpoint.version)
      && typeof checkpoint.reason === "string"
      && typeof checkpoint.state_hash === "string"
      && /^[a-f0-9]{64}$/.test(checkpoint.state_hash)
      && isDate(checkpoint.created_at))
    || typeof value.integrity_hash !== "string"
    || !/^[a-f0-9]{64}$/.test(value.integrity_hash)
    || typeof value.command_replayed !== "boolean") {
    throw new Error("approval run crossed an invalid typed boundary");
  }
  const events = value.events.map(parseAgentEvent);
  if (events.some((event, index) => event.run_id !== value.run_id || event.sequence !== index + 1)) {
    throw new Error("approval run returned an inconsistent event trajectory");
  }
  if ((value.status === "interrupted") !== (value.interrupt !== null)
    || (value.status === "completed") !== (value.receipt !== null)) {
    throw new Error("approval run returned an inconsistent interrupt or receipt state");
  }
  return { ...value, events } as ApprovalRun;
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

export function loadApprovalCatalog(signal?: AbortSignal): Promise<ApprovalCatalog> {
  return request("/api/approvals/catalog", parseApprovalCatalog, { signal });
}

export function loadApprovalRun(runId: string, signal?: AbortSignal): Promise<ApprovalRun> {
  return request(`/api/approval-runs/${encodeURIComponent(runId)}`, parseApprovalRun, { signal });
}

export function startApprovalRun(
  scenarioId: ApprovalScenario["id"],
  userMessage: string
): Promise<ApprovalRun> {
  return request("/api/approval-runs", parseApprovalRun, {
    method: "POST",
    body: JSON.stringify({
      scenario_id: scenarioId,
      proposer_role: "learner",
      user_message: userMessage,
      idempotency_key: nextCommandKey("approval-start")
    })
  });
}

export function decideApproval(
  run: ApprovalRun,
  decision: ApprovalDecision,
  editedArguments?: Record<string, unknown>,
  actorRole: "learner" | "approver" = "approver"
): Promise<ApprovalRun> {
  if (!run.interrupt) throw new Error("No current interrupt can accept a decision");
  return request(`/api/approval-runs/${encodeURIComponent(run.run_id)}/decisions`, parseApprovalRun, {
    method: "POST",
    body: JSON.stringify({
      interrupt_id: run.interrupt.interrupt_id,
      decision,
      actor_role: actorRole,
      expected_checkpoint_version: run.checkpoint_version,
      idempotency_key: nextCommandKey(`approval-${decision}`),
      ...(editedArguments ? { edited_arguments: editedArguments } : {})
    })
  });
}

export function makeApprovalStale(
  run: ApprovalRun,
  dimension: StalenessDimension
): Promise<ApprovalRun> {
  return request(`/api/approval-runs/${encodeURIComponent(run.run_id)}/staleness`, parseApprovalRun, {
    method: "POST",
    body: JSON.stringify({
      dimension,
      expected_checkpoint_version: run.checkpoint_version,
      idempotency_key: nextCommandKey(`approval-stale-${dimension}`)
    })
  });
}
