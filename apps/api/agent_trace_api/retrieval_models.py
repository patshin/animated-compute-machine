from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, JsonValue, StringConstraints, model_validator

from agent_trace_api.models import AgentEvent, ApprovedCommand, RunId, StrictModel

EvidenceId = Annotated[str, StringConstraints(pattern=r"^ev_[A-Za-z0-9_-]+$")]
Authority = Literal["primary", "official", "internal", "secondary", "unknown"]
Freshness = Literal["current", "aging", "stale", "superseded", "unknown"]


class RetrievalScenarioSource(StrictModel):
    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    path: str = Field(min_length=1)
    version: str = Field(min_length=1)
    published_at: datetime
    authority: Authority
    freshness: Freshness
    contradiction_group: str | None = None


class RetrievalScenarioRecord(StrictModel):
    record_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    version: str = Field(min_length=1)
    observed_at: datetime
    authority: Authority
    freshness: Freshness
    contradiction_group: str | None = None
    facts: dict[str, JsonValue]


class RetrievalScenarioToolPlan(StrictModel):
    tool_name: Literal["analytics.query_metric"]
    tool_version: Literal["1.0.0"]
    metric: Literal["revenue", "tickets"]
    periods: list[Literal["2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4"]] = Field(
        min_length=2, max_length=2
    )
    calculation: Literal["percent_change"]


class RetrievalScenarioManifest(StrictModel):
    schema_version: Literal["1.0.0"]
    scenario_id: Literal["research-synthesis", "risk-investigation"]
    title: str = Field(min_length=3, max_length=80)
    description: str = Field(min_length=10, max_length=240)
    default_query: str = Field(min_length=2, max_length=400)
    query_chips: list[str] = Field(min_length=1)
    sources: list[RetrievalScenarioSource] = Field(min_length=1)
    records: list[RetrievalScenarioRecord] = Field(default_factory=list)
    planned_tool: RetrievalScenarioToolPlan | None = None


class EvidenceSource(StrictModel):
    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_type: Literal["document", "database", "tool", "calculation", "remote_agent"]
    locator: str | None = None
    version: str | None = None
    published_at: datetime | None = None
    effective_at: datetime | None = None
    observed_at: datetime | None = None


class EvidenceContent(StrictModel):
    excerpt: str | None = Field(default=None, min_length=1, max_length=8_000)
    facts: dict[str, JsonValue] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def require_content(self) -> "EvidenceContent":
        if self.excerpt is None and self.facts is None:
            raise ValueError("evidence requires an excerpt or facts")
        if self.excerpt is not None and not self.excerpt.strip():
            raise ValueError("evidence excerpt must contain non-whitespace text")
        return self


class EvidenceRetrieval(StrictModel):
    method: Literal["fts5", "tool", "calculation", "remote"]
    score: float | None = None
    score_breakdown: dict[str, float] | None = None


class EvidenceProvenance(StrictModel):
    run_id: str = Field(min_length=1)
    node_id: str | None = None
    tool_request_id: str | None = None
    input_evidence_ids: list[str] | None = None
    created_by: Literal["retrieval", "tool", "compute", "remote_agent"]


class Evidence(StrictModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    evidence_id: EvidenceId
    kind: Literal["document", "record", "metric", "calculation", "remote_artifact"]
    source: EvidenceSource
    content: EvidenceContent
    retrieval: EvidenceRetrieval
    authority: Authority
    freshness: Freshness
    contradiction_group: str | None = None
    classification: Literal["public_fixture", "internal_fixture", "sensitive_fixture"]
    provenance: EvidenceProvenance
    citation_label: str | None = None


class IngestionReport(StrictModel):
    scenario_id: str
    inserted_chunks: int = Field(ge=0)
    deduplicated_chunks: int = Field(ge=0)
    stable_chunk_ids: list[str]
    source_count: int = Field(ge=0)


class RetrievalFilters(StrictModel):
    authorities: list[Authority] = Field(default_factory=list)
    include_superseded: bool = True
    exact_phrase: bool = False


class StartRetrievalRunRequest(ApprovedCommand):
    scenario_id: Literal["research-synthesis", "risk-investigation"]
    query: str = Field(min_length=2, max_length=400)
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    seed: int = Field(default=2026, ge=0, le=2_147_483_647)


class QueryRewrite(StrictModel):
    original: str
    rewritten: str
    added_terms: list[str]
    bounded: Literal[True] = True


class RetrievedChunk(StrictModel):
    chunk_id: str
    source_id: str
    title: str
    section: str
    excerpt: str
    version: str
    published_at: datetime
    authority: Authority
    freshness: Freshness
    contradiction_group: str | None = None
    score: float
    score_breakdown: dict[str, float]
    suspicious: bool
    suppressed_instruction: bool


class EvidenceClaim(StrictModel):
    claim_id: str = Field(pattern=r"^claim_[a-z0-9_]+$")
    text: str
    kind: Literal["assertion", "derived"]
    required: bool = True
    citation_ids: list[EvidenceId] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_citations(self) -> "EvidenceClaim":
        if len(self.citation_ids) != len(set(self.citation_ids)):
            raise ValueError("claim citations must be unique")
        return self


class CitationValidation(StrictModel):
    valid: bool
    coverage_percent: float = Field(ge=0, le=100)
    issues: list[str]
    warnings: list[str]


class ToolExecutionSummary(StrictModel):
    tool_name: str
    request_id: str
    status: Literal["succeeded"]
    purpose: Literal["scenario_planned_context"] = "scenario_planned_context"


class RetrievalRun(StrictModel):
    schema_id: Literal["agent-trace://retrieval-run/1.0.0"] = "agent-trace://retrieval-run/1.0.0"
    run_id: RunId
    scenario_id: Literal["research-synthesis", "risk-investigation"]
    status: Literal["completed", "empty"]
    query: QueryRewrite
    filters: RetrievalFilters
    chunks: list[RetrievedChunk]
    evidence: list[Evidence]
    claims: list[EvidenceClaim]
    citation_validation: CitationValidation
    events: list[AgentEvent]
    tool_executions: list[ToolExecutionSummary]
    retrieved_instruction_tool_calls: Literal[0] = 0
    import_report: IngestionReport


class ScenarioSummary(StrictModel):
    scenario_id: Literal["research-synthesis", "risk-investigation"]
    title: str
    description: str
    default_query: str
    query_chips: list[str]


class MemoryBucket(StrictModel):
    bucket_id: Literal[
        "context", "thread", "checkpoint", "long_term", "business", "audit", "learning"
    ]
    label: str
    retention: str
    mutable: bool
    contains_preference: bool


class RetrievalCatalog(StrictModel):
    schema_id: Literal["agent-trace://retrieval-catalog/1.0.0"] = (
        "agent-trace://retrieval-catalog/1.0.0"
    )
    scenarios: list[ScenarioSummary]
    memory_buckets: list[MemoryBucket]
    engine: Literal["sqlite-fts5-bm25"] = "sqlite-fts5-bm25"
    offline: Literal[True] = True


class PreferenceRecord(StrictModel):
    preference_id: str = Field(pattern=r"^pref_[A-Za-z0-9_-]+$")
    key: Literal["answer_detail"]
    value: Literal["concise", "balanced", "detailed"]
    scope: Literal["long_term"] = "long_term"
    created_at: datetime
    updated_at: datetime


class MemoryAuditEntry(StrictModel):
    sequence: int = Field(ge=1)
    action: Literal["preference.saved", "preference.deleted"]
    preference_id: str
    timestamp: datetime
    idempotency_key: str


class MemorySnapshot(StrictModel):
    schema_id: Literal["agent-trace://memory-snapshot/1.0.0"] = (
        "agent-trace://memory-snapshot/1.0.0"
    )
    preferences: list[PreferenceRecord]
    audit: list[MemoryAuditEntry]
    storage: Literal["repository-local-sqlite"] = "repository-local-sqlite"


class SavePreferenceRequest(ApprovedCommand):
    key: Literal["answer_detail"]
    value: Literal["concise", "balanced", "detailed"]
    confirmed: Literal[True]


class DeletePreferenceRequest(ApprovedCommand):
    preference_id: str = Field(pattern=r"^pref_[A-Za-z0-9_-]+$")
    confirmed: Literal[True]
