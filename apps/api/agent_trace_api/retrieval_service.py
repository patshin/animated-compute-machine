from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import JsonValue

from agent_trace_api.models import AgentEvent
from agent_trace_api.retrieval_models import (
    CitationValidation,
    DeletePreferenceRequest,
    Evidence,
    EvidenceClaim,
    EvidenceContent,
    EvidenceProvenance,
    EvidenceRetrieval,
    EvidenceSource,
    IngestionReport,
    MemoryAuditEntry,
    MemoryBucket,
    MemorySnapshot,
    PreferenceRecord,
    QueryRewrite,
    RetrievalCatalog,
    RetrievalRun,
    RetrievalScenarioManifest,
    RetrievedChunk,
    SavePreferenceRequest,
    ScenarioSummary,
    StartRetrievalRunRequest,
    ToolExecutionSummary,
)
from agent_trace_api.tool_calling import ToolLabService, ToolRunCommand

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SCENARIOS_ROOT = REPOSITORY_ROOT / "scenarios"
EVIDENCE_CONTRACT = REPOSITORY_ROOT / "contracts/evidence.schema.json"
INSTRUCTION_PATTERN = re.compile(
    r"(?i)(?:ignore\s+(?:all\s+)?previous|system\s*:|call\s+(?:a\s+)?tool|"
    r"execute\s+(?:the\s+)?(?:tool|command)|orders\.[a-z_]+)"
)


class RetrievalRunNotFoundError(KeyError):
    pass


class RetrievalConflictError(RuntimeError):
    pass


def _stable_id(prefix: str, *parts: str, length: int = 18) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:length]
    return f"{prefix}_{digest}"


def _fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _json_object(value: str) -> dict[str, Any]:
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        raise RuntimeError("stored retrieval document must be an object")
    return cast(dict[str, Any], loaded)


class RetrievalService:
    def __init__(
        self,
        database_path: Path,
        tool_service: ToolLabService,
        *,
        scenarios_root: Path = SCENARIOS_ROOT,
        blocked_source_ids: frozenset[str] = frozenset(),
    ) -> None:
        self.database_path = database_path
        self.scenarios_root = scenarios_root
        self.tool_service = tool_service
        self.blocked_source_ids = blocked_source_ids
        self._lock = threading.RLock()
        self._start_lock = asyncio.Lock()
        self._connection = sqlite3.connect(database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._evidence_validator = Draft202012Validator(
            json.loads(EVIDENCE_CONTRACT.read_text(encoding="utf-8")),
            format_checker=FormatChecker(),
        )
        self._initialize()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _initialize(self) -> None:
        with self._lock:
            self._connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE VIRTUAL TABLE IF NOT EXISTS retrieval_chunks USING fts5(
                    chunk_id UNINDEXED,
                    scenario_id UNINDEXED,
                    source_id UNINDEXED,
                    title,
                    section,
                    body,
                    metadata_json UNINDEXED,
                    content_hash UNINDEXED,
                    tokenize = 'unicode61'
                );
                CREATE TABLE IF NOT EXISTS scenario_retrieval_chunk_hashes(
                    scenario_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    PRIMARY KEY(scenario_id, chunk_id),
                    UNIQUE(scenario_id, content_hash)
                );
                CREATE TABLE IF NOT EXISTS scenario_retrieval_records(
                    scenario_id TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    PRIMARY KEY(scenario_id, record_id)
                );
                CREATE TABLE IF NOT EXISTS retrieval_runs(
                    run_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    command_fingerprint TEXT,
                    snapshot_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS long_term_preferences(
                    preference_id TEXT PRIMARY KEY,
                    preference_key TEXT NOT NULL UNIQUE,
                    preference_value TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_audit(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL,
                    preference_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    command_fingerprint TEXT
                );
                """
            )
            legacy_hash_table = self._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'retrieval_chunk_hashes'"
            ).fetchone()
            if legacy_hash_table is not None:
                self._connection.execute(
                    """INSERT OR IGNORE INTO scenario_retrieval_chunk_hashes(
                        scenario_id, chunk_id, content_hash
                    )
                    SELECT scenario_id, chunk_id, content_hash FROM retrieval_chunks"""
                )
            legacy_record_table = self._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'retrieval_records'"
            ).fetchone()
            if legacy_record_table is not None:
                self._connection.execute(
                    """INSERT OR IGNORE INTO scenario_retrieval_records(
                        scenario_id, record_id, payload_json, metadata_json
                    )
                    SELECT scenario_id, record_id, payload_json, metadata_json
                    FROM retrieval_records"""
                )
            run_columns = {
                str(row[1]) for row in self._connection.execute("PRAGMA table_info(retrieval_runs)")
            }
            if "command_fingerprint" not in run_columns:
                self._connection.execute(
                    "ALTER TABLE retrieval_runs ADD COLUMN command_fingerprint TEXT"
                )
            audit_columns = {
                str(row[1]) for row in self._connection.execute("PRAGMA table_info(memory_audit)")
            }
            if "command_fingerprint" not in audit_columns:
                self._connection.execute(
                    "ALTER TABLE memory_audit ADD COLUMN command_fingerprint TEXT"
                )
            self._connection.commit()

    def _manifest(self, scenario_id: str) -> dict[str, Any]:
        scenario_directory = self.scenarios_root / scenario_id
        scenarios_root = self.scenarios_root.resolve()
        if scenario_directory.is_symlink():
            raise RuntimeError("scenario package must not be a symbolic link")
        resolved_directory = scenario_directory.resolve()
        if resolved_directory.parent != scenarios_root:
            raise RuntimeError("scenario path escaped the package root")
        manifest_path = scenario_directory / "retrieval.json"
        if manifest_path.is_symlink() or manifest_path.resolve().parent != resolved_directory:
            raise RuntimeError("scenario manifest must be a package-local, real file")
        manifest = RetrievalScenarioManifest.model_validate(
            _json_object(manifest_path.read_text(encoding="utf-8"))
        ).model_dump(mode="json")
        if manifest.get("scenario_id") != scenario_id:
            raise RuntimeError("scenario manifest identity mismatch")
        return manifest

    def ingest(self, scenario_id: str) -> IngestionReport:
        manifest = self._manifest(scenario_id)
        inserted = 0
        deduplicated = 0
        chunk_ids: list[str] = []
        scenario_directory = self.scenarios_root / scenario_id
        with self._lock:
            sources = manifest.get("sources")
            if not isinstance(sources, list):
                raise RuntimeError("scenario sources must be a list")
            for raw_source in sources:
                if not isinstance(raw_source, dict):
                    raise RuntimeError("scenario source must be an object")
                relative = Path(str(raw_source["path"]))
                if relative.is_absolute() or ".." in relative.parts:
                    raise RuntimeError("scenario source path is unsafe")
                source_path = scenario_directory / relative
                if source_path.is_symlink() or not source_path.resolve().is_relative_to(
                    scenario_directory.resolve()
                ):
                    raise RuntimeError("scenario source must be a package-local, real file")
                text = source_path.read_text(encoding="utf-8")
                for section, body in self._split_markdown(text):
                    content_hash = hashlib.sha256(body.encode()).hexdigest()
                    chunk_id = _stable_id(
                        "chunk",
                        scenario_id,
                        str(raw_source["source_id"]),
                        str(raw_source["version"]),
                        section,
                        body,
                    )
                    chunk_ids.append(chunk_id)
                    exists = self._connection.execute(
                        "SELECT 1 FROM scenario_retrieval_chunk_hashes "
                        "WHERE scenario_id = ? AND (chunk_id = ? OR content_hash = ?)",
                        (scenario_id, chunk_id, content_hash),
                    ).fetchone()
                    if exists is not None:
                        deduplicated += 1
                        continue
                    metadata = {key: value for key, value in raw_source.items() if key != "path"}
                    self._connection.execute(
                        """INSERT INTO retrieval_chunks(
                            chunk_id, scenario_id, source_id, title, section, body,
                            metadata_json, content_hash
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            chunk_id,
                            scenario_id,
                            raw_source["source_id"],
                            raw_source["title"],
                            section,
                            body,
                            json.dumps(metadata, sort_keys=True),
                            content_hash,
                        ),
                    )
                    self._connection.execute(
                        """INSERT INTO scenario_retrieval_chunk_hashes(
                            scenario_id, chunk_id, content_hash
                        ) VALUES (?, ?, ?)""",
                        (scenario_id, chunk_id, content_hash),
                    )
                    inserted += 1
            records = manifest.get("records", [])
            if not isinstance(records, list):
                raise RuntimeError("scenario records must be a list")
            for raw_record in records:
                if not isinstance(raw_record, dict):
                    raise RuntimeError("scenario record must be an object")
                record_id = str(raw_record["record_id"])
                payload = raw_record.get("facts")
                if not isinstance(payload, dict):
                    raise RuntimeError("scenario record facts must be an object")
                metadata = {key: value for key, value in raw_record.items() if key != "facts"}
                self._connection.execute(
                    """INSERT OR REPLACE INTO scenario_retrieval_records(
                        scenario_id, record_id, payload_json, metadata_json
                    ) VALUES (?, ?, ?, ?)""",
                    (
                        scenario_id,
                        record_id,
                        json.dumps(payload, sort_keys=True),
                        json.dumps(metadata, sort_keys=True),
                    ),
                )
            self._connection.commit()
        return IngestionReport(
            scenario_id=scenario_id,
            inserted_chunks=inserted,
            deduplicated_chunks=deduplicated,
            stable_chunk_ids=chunk_ids,
            source_count=len(sources),
        )

    @staticmethod
    def _split_markdown(text: str) -> list[tuple[str, str]]:
        chunks: list[tuple[str, str]] = []
        heading = "Document"
        body: list[str] = []
        for line in text.splitlines():
            if line.startswith("## "):
                if body and "\n".join(body).strip():
                    chunks.append((heading, "\n".join(body).strip()))
                heading = line[3:].strip()
                body = []
            elif not line.startswith("# "):
                body.append(line)
        if body and "\n".join(body).strip():
            chunks.append((heading, "\n".join(body).strip()))
        return chunks

    def catalog(self) -> RetrievalCatalog:
        scenarios: list[ScenarioSummary] = []
        for scenario_id in ("research-synthesis", "risk-investigation"):
            manifest = self._manifest(scenario_id)
            scenarios.append(
                ScenarioSummary(
                    scenario_id=cast(Any, scenario_id),
                    title=str(manifest["title"]),
                    description=str(manifest["description"]),
                    default_query=str(manifest["default_query"]),
                    query_chips=[str(value) for value in manifest["query_chips"]],
                )
            )
        preferences = bool(
            self._connection.execute("SELECT 1 FROM long_term_preferences LIMIT 1").fetchone()
        )
        buckets = [
            ("context", "Context window", "current request", False),
            ("thread", "Thread state", "until thread deletion", False),
            ("checkpoint", "Runtime checkpoint", "run lifecycle", False),
            ("long_term", "Long-term preference", "until user deletion", True),
            ("business", "Business record", "source policy", False),
            ("audit", "Audit history", "append-only", False),
            ("learning", "Learning note", "scenario session", False),
        ]
        return RetrievalCatalog(
            scenarios=scenarios,
            memory_buckets=[
                MemoryBucket(
                    bucket_id=cast(Any, bucket_id),
                    label=label,
                    retention=retention,
                    mutable=mutable,
                    contains_preference=preferences if bucket_id == "long_term" else False,
                )
                for bucket_id, label, retention, mutable in buckets
            ],
        )

    @staticmethod
    def _rewrite(query: str) -> QueryRewrite:
        normalized = " ".join(query.split())
        additions: list[str] = []
        lowered = normalized.lower()
        if "policy" in lowered and "version" not in lowered:
            additions.append("version")
        if "risk" in lowered and "authority" not in lowered:
            additions.append("authority")
        rewritten = " ".join([normalized, *additions])[:460]
        return QueryRewrite(original=normalized, rewritten=rewritten, added_terms=additions)

    @staticmethod
    def _match_expression(query: str, exact_phrase: bool) -> str:
        tokens = re.findall(r"[\w-]+", query, flags=re.UNICODE)[:18]
        if not tokens:
            return '"__empty__"'
        if exact_phrase:
            phrase = " ".join(tokens).replace('"', '""')
            return f'"{phrase}"'
        return " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)

    @staticmethod
    def _score(metadata: dict[str, Any], lexical: float) -> dict[str, float]:
        authority = {
            "primary": 0.24,
            "official": 0.22,
            "internal": 0.16,
            "secondary": 0.08,
            "unknown": 0.0,
        }[str(metadata["authority"])]
        freshness = {
            "current": 0.18,
            "aging": 0.08,
            "stale": -0.04,
            "superseded": -0.22,
            "unknown": 0.0,
        }[str(metadata["freshness"])]
        conflict = -0.04 if metadata.get("contradiction_group") else 0.0
        return {
            "lexical": round(lexical, 4),
            "authority": authority,
            "freshness": freshness,
            "superseded_penalty": -0.18 if metadata["freshness"] == "superseded" else 0.0,
            "conflict_penalty": conflict,
        }

    @staticmethod
    def _sanitize_excerpt(body: str) -> tuple[str, bool]:
        suspicious = bool(INSTRUCTION_PATTERN.search(body))
        if not suspicious:
            return body[:8000], False
        safe_lines = [
            "[UNTRUSTED INSTRUCTION REMOVED]" if INSTRUCTION_PATTERN.search(line) else line
            for line in body.splitlines()
        ]
        return "\n".join(safe_lines)[:8000], True

    def _retrieve(
        self, command: StartRetrievalRunRequest
    ) -> tuple[list[RetrievedChunk], IngestionReport, QueryRewrite]:
        report = self.ingest(command.scenario_id)
        rewrite = self._rewrite(command.query)
        match_query = command.query if command.filters.exact_phrase else rewrite.rewritten
        match = self._match_expression(match_query, command.filters.exact_phrase)
        rows = self._connection.execute(
            """SELECT chunk_id, source_id, title, section, body, metadata_json,
                      bm25(retrieval_chunks, 0.0, 0.0, 0.0, 5.0, 2.0, 1.0, 0.0, 0.0)
                        AS rank
               FROM retrieval_chunks
               WHERE retrieval_chunks MATCH ? AND scenario_id = ?
               ORDER BY rank""",
            (match, command.scenario_id),
        ).fetchall()
        eligible_rows: list[tuple[sqlite3.Row, dict[str, Any]]] = []
        for row in rows:
            metadata = _json_object(str(row["metadata_json"]))
            if (
                command.filters.authorities
                and metadata["authority"] not in command.filters.authorities
            ):
                continue
            if not command.filters.include_superseded and metadata["freshness"] == "superseded":
                continue
            eligible_rows.append((row, metadata))
        raw_relevance = [-float(row["rank"]) for row, _metadata in eligible_rows]
        minimum_relevance = min(raw_relevance, default=0.0)
        relevance_span = max(raw_relevance, default=0.0) - minimum_relevance
        chunks: list[RetrievedChunk] = []
        for row, metadata in eligible_rows:
            relevance = -float(row["rank"])
            lexical = (
                0.34
                if relevance_span <= 1e-12
                else 0.18 + ((relevance - minimum_relevance) / relevance_span) * 0.40
            )
            breakdown = self._score(metadata, lexical)
            excerpt, suppressed = self._sanitize_excerpt(str(row["body"]))
            chunks.append(
                RetrievedChunk(
                    chunk_id=str(row["chunk_id"]),
                    source_id=str(row["source_id"]),
                    title=str(row["title"]),
                    section=str(row["section"]),
                    excerpt=excerpt,
                    version=str(metadata["version"]),
                    published_at=datetime.fromisoformat(
                        str(metadata["published_at"]).replace("Z", "+00:00")
                    ),
                    authority=metadata["authority"],
                    freshness=metadata["freshness"],
                    contradiction_group=metadata.get("contradiction_group"),
                    score=round(sum(breakdown.values()), 4),
                    score_breakdown=breakdown,
                    suspicious=suppressed,
                    suppressed_instruction=suppressed,
                )
            )
        chunks.sort(key=lambda chunk: (-chunk.score, chunk.chunk_id))
        return chunks[:12], report, rewrite

    def _document_evidence(self, run_id: str, chunks: list[RetrievedChunk]) -> list[Evidence]:
        evidence: list[Evidence] = []
        for chunk in chunks:
            item = Evidence(
                evidence_id=_stable_id("ev", run_id, chunk.chunk_id),
                kind="document",
                source=EvidenceSource(
                    source_id=chunk.source_id,
                    title=chunk.title,
                    source_type="document",
                    locator=f"chunk://{chunk.chunk_id}#{chunk.section}",
                    version=chunk.version,
                    published_at=chunk.published_at,
                ),
                content=EvidenceContent(excerpt=chunk.excerpt),
                retrieval=EvidenceRetrieval(
                    method="fts5",
                    score=chunk.score,
                    score_breakdown=chunk.score_breakdown,
                ),
                authority=chunk.authority,
                freshness=chunk.freshness,
                contradiction_group=chunk.contradiction_group,
                classification="public_fixture",
                provenance=EvidenceProvenance(
                    run_id=run_id,
                    node_id="retrieve",
                    created_by="retrieval",
                ),
                citation_label=f"[{len(evidence) + 1}]",
            )
            self._validate_evidence(item)
            evidence.append(item)
        return evidence

    def _record_evidence(self, run_id: str, scenario_id: str) -> list[Evidence]:
        rows = self._connection.execute(
            "SELECT record_id, payload_json, metadata_json "
            "FROM scenario_retrieval_records "
            "WHERE scenario_id = ? ORDER BY record_id",
            (scenario_id,),
        ).fetchall()
        evidence: list[Evidence] = []
        for row in rows:
            metadata = _json_object(str(row["metadata_json"]))
            item = Evidence(
                evidence_id=_stable_id("ev", run_id, str(row["record_id"])),
                kind="record",
                source=EvidenceSource(
                    source_id=str(row["record_id"]),
                    title=str(metadata["title"]),
                    source_type="database",
                    locator=(
                        f"sqlite://scenario_retrieval_records/{scenario_id}/{row['record_id']}"
                    ),
                    version=str(metadata["version"]),
                    observed_at=_iso(str(metadata["observed_at"])),
                ),
                content=EvidenceContent(
                    facts=cast(dict[str, Any], json.loads(row["payload_json"]))
                ),
                retrieval=EvidenceRetrieval(method="fts5", score=0.56),
                authority=metadata["authority"],
                freshness=metadata["freshness"],
                contradiction_group=metadata.get("contradiction_group"),
                classification="internal_fixture",
                provenance=EvidenceProvenance(
                    run_id=run_id,
                    node_id="normalize_record",
                    created_by="retrieval",
                ),
                citation_label=f"[R{len(evidence) + 1}]",
            )
            self._validate_evidence(item)
            evidence.append(item)
        return evidence

    async def _tool_and_calculation_evidence(
        self,
        run_id: str,
        key: str,
        plan: dict[str, Any],
        seed: int,
    ) -> tuple[list[Evidence], list[ToolExecutionSummary]]:
        tool_run = await self.tool_service.execute(
            ToolRunCommand(
                approved=True,
                idempotency_key=f"e6-{key}",
                tool_name=str(plan["tool_name"]),
                tool_version=str(plan["tool_version"]),
                arguments={"metric": plan["metric"], "periods": plan["periods"]},
                actor_role="system",
                expected_risk="read",
                model_rationale=(
                    "Scenario plan requests bounded revenue context; "
                    "retrieved text cannot alter it."
                ),
                seed=seed,
            )
        )
        if tool_run.status != "succeeded" or not isinstance(tool_run.result.data, dict):
            raise RuntimeError("planned read-only scenario tool did not produce typed evidence")
        tool_evidence = Evidence(
            evidence_id=_stable_id("ev", run_id, tool_run.request.request_id),
            kind="metric",
            source=EvidenceSource(
                source_id="analytics.query_metric@1.0.0",
                title=f"Scenario-planned {plan['metric']} context",
                source_type="tool",
                locator=str(tool_run.result.data["source"]),
                observed_at=tool_run.result.ended_at,
            ),
            content=EvidenceContent(facts=cast(dict[str, Any], tool_run.result.data)),
            retrieval=EvidenceRetrieval(method="tool"),
            authority="internal",
            freshness="current",
            classification="internal_fixture",
            provenance=EvidenceProvenance(
                run_id=run_id,
                node_id="tool_context",
                tool_request_id=tool_run.request.request_id,
                created_by="tool",
            ),
            citation_label="[T1]",
        )
        rows = cast(list[dict[str, Any]], tool_run.result.data["rows"])
        start = float(rows[0]["value"])
        end = float(rows[-1]["value"])
        growth = round((end - start) / start * 100, 1)
        metric_label = str(plan["metric"])
        first_period, last_period = cast(list[str], plan["periods"])
        calculation = Evidence(
            evidence_id=_stable_id("ev", run_id, "revenue-growth-calculation"),
            kind="calculation",
            source=EvidenceSource(
                source_id=f"calc.{metric_label}_percent_change@1.0.0",
                title=f"{first_period} to {last_period} {metric_label} change",
                source_type="calculation",
                locator=f"calculation://{metric_label}-percent-change",
            ),
            content=EvidenceContent(
                facts={
                    "metric": metric_label,
                    "start_period": first_period,
                    "end_period": last_period,
                    "start_value": start,
                    "end_value": end,
                    "percent_change": growth,
                    "formula": "(end - start) / start * 100",
                }
            ),
            retrieval=EvidenceRetrieval(method="calculation"),
            authority="internal",
            freshness="current",
            classification="internal_fixture",
            provenance=EvidenceProvenance(
                run_id=run_id,
                node_id="calculate",
                input_evidence_ids=[tool_evidence.evidence_id],
                created_by="compute",
            ),
            citation_label="[C1]",
        )
        self._validate_evidence(tool_evidence)
        self._validate_evidence(calculation)
        return [tool_evidence, calculation], [
            ToolExecutionSummary(
                tool_name=tool_run.request.tool_name,
                request_id=tool_run.request.request_id,
                status="succeeded",
            )
        ]

    def _validate_evidence(self, evidence: Evidence) -> None:
        errors = sorted(
            self._evidence_validator.iter_errors(
                evidence.model_dump(mode="json", exclude_none=True)
            ),
            key=lambda error: list(error.path),
        )
        if errors:
            raise RuntimeError(f"evidence contract violation: {errors[0].message}")

    @staticmethod
    def _claims(evidence: list[Evidence]) -> list[EvidenceClaim]:
        current_official = next(
            (
                item
                for item in evidence
                if item.kind == "document"
                and item.authority in {"official", "primary"}
                and item.freshness == "current"
            ),
            None,
        )
        secondary_conflict = next(
            (
                item
                for item in evidence
                if item.kind == "document"
                and item.authority == "secondary"
                and item.contradiction_group is not None
            ),
            None,
        )
        record = next((item for item in evidence if item.kind == "record"), None)
        calculation = next((item for item in evidence if item.kind == "calculation"), None)
        claims: list[EvidenceClaim] = []
        if current_official is not None:
            citations = [current_official.evidence_id]
            if secondary_conflict is not None:
                citations.append(secondary_conflict.evidence_id)
            claims.append(
                EvidenceClaim(
                    claim_id="claim_current_position",
                    text=(
                        "The current official position is preferred, with a visible "
                        "secondary-source conflict."
                    ),
                    kind="assertion",
                    citation_ids=citations,
                )
            )
        if record is not None:
            claims.append(
                EvidenceClaim(
                    claim_id="claim_internal_record",
                    text="The internal risk record provides a dated, structured observation.",
                    kind="assertion",
                    citation_ids=[record.evidence_id],
                )
            )
        if calculation is not None:
            facts = calculation.content.facts or {}
            metric = str(facts.get("metric", "metric")).replace("_", " ")
            percent_value = facts.get("percent_change", 0.0)
            percent_change = (
                float(percent_value)
                if isinstance(percent_value, (int, float)) and not isinstance(percent_value, bool)
                else 0.0
            )
            direction = "increased" if percent_change >= 0 else "decreased"
            claims.append(
                EvidenceClaim(
                    claim_id="claim_planned_metric_context",
                    text=(
                        f"{metric.capitalize()} {direction} {abs(percent_change):.1f}% "
                        "across the scenario-planned fictional periods."
                    ),
                    kind="derived",
                    citation_ids=[calculation.evidence_id],
                )
            )
        return claims

    def _validate_citations(
        self, run_id: str, evidence: list[Evidence], claims: list[EvidenceClaim]
    ) -> CitationValidation:
        evidence_map = {item.evidence_id: item for item in evidence}
        issues: list[str] = []
        warnings: list[str] = []
        covered = 0
        for claim in claims:
            missing_ids = [
                citation for citation in claim.citation_ids if citation not in evidence_map
            ]
            if missing_ids:
                issues.append(
                    f"{claim.claim_id}: citation does not exist in this run: "
                    + ", ".join(missing_ids)
                )
                continue
            cited_evidence = [evidence_map[citation] for citation in claim.citation_ids]
            if any(item.provenance.run_id != run_id for item in cited_evidence):
                issues.append(f"{claim.claim_id}: citation belongs to another run")
                continue
            blocked = [
                item.source.source_id
                for item in cited_evidence
                if item.source.source_id in self.blocked_source_ids
            ]
            if blocked:
                issues.append(
                    f"{claim.claim_id}: citation source is blocked: " + ", ".join(blocked)
                )
                continue
            if any(
                (item.content.excerpt is not None and not item.content.excerpt.strip())
                or (item.content.facts is not None and not item.content.facts)
                for item in cited_evidence
            ):
                issues.append(f"{claim.claim_id}: citation content is empty")
                continue
            if claim.kind == "derived":
                inputs = [
                    input_id
                    for item in cited_evidence
                    if item.kind == "calculation"
                    for input_id in (item.provenance.input_evidence_ids or [])
                ]
                if not inputs:
                    issues.append(f"{claim.claim_id}: derived claim lacks calculation inputs")
                    continue
                if any(input_id not in evidence_map for input_id in inputs):
                    issues.append(
                        f"{claim.claim_id}: calculation references evidence outside this run"
                    )
                    continue
            if claim.required:
                covered += 1
        if any(item.freshness == "superseded" for item in evidence):
            warnings.append("Superseded evidence remains visible and is ranking-penalized.")
        groups: dict[str, set[str]] = {}
        for item in evidence:
            if item.contradiction_group:
                groups.setdefault(item.contradiction_group, set()).add(item.authority)
        if any(
            "secondary" in authorities and authorities & {"official", "primary"}
            for authorities in groups.values()
        ):
            warnings.append(
                "Official and secondary sources conflict; synthesis must state a limitation."
            )
        required = sum(claim.required for claim in claims)
        coverage = 100.0 if required == 0 else round(covered / required * 100, 1)
        return CitationValidation(
            valid=not issues and required > 0,
            coverage_percent=coverage,
            issues=issues,
            warnings=warnings,
        )

    @staticmethod
    def _events(
        run_id: str,
        rewrite: QueryRewrite,
        chunks: list[RetrievedChunk],
        evidence: list[Evidence],
        validation: CitationValidation,
        tool_summaries: list[ToolExecutionSummary],
    ) -> list[AgentEvent]:
        base = datetime(2026, 4, 8, 9, 0, tzinfo=UTC)
        correlation = f"corr_{run_id[4:]}"
        raw: list[tuple[str, str, dict[str, JsonValue]]] = [
            (
                "query.rewritten",
                "rewrite_query",
                {"original": rewrite.original, "rewritten": rewrite.rewritten},
            ),
            (
                "retrieval.completed",
                "retrieve",
                {"engine": "sqlite-fts5-bm25", "chunk_count": len(chunks)},
            ),
        ]
        if tool_summaries:
            raw.append(
                (
                    "tool.completed",
                    "tool_context",
                    {
                        "tool": tool_summaries[0].tool_name,
                        "purpose": "scenario_planned_context",
                    },
                )
            )
        raw.append(
            (
                "evidence.normalized",
                "normalize_record",
                {"evidence_count": len(evidence)},
            )
        )
        raw.append(
            (
                "citation.validated",
                "validate_citations",
                {
                    "valid": validation.valid,
                    "coverage_percent": validation.coverage_percent,
                },
            )
        )
        return [
            AgentEvent(
                event_id=_stable_id("evt", run_id, str(index)),
                run_id=run_id,
                sequence=index,
                type=event_type,
                timestamp=base + timedelta(milliseconds=index * 40),
                node_id=node_id,
                correlation_id=correlation,
                payload=payload,
            )
            for index, (event_type, node_id, payload) in enumerate(raw, start=1)
        ]

    async def start(self, command: StartRetrievalRunRequest) -> RetrievalRun:
        async with self._start_lock:
            return await self._start_once(command)

    async def _start_once(self, command: StartRetrievalRunRequest) -> RetrievalRun:
        fingerprint = _fingerprint(command.model_dump(mode="json"))
        existing = self._connection.execute(
            "SELECT command_fingerprint, snapshot_json FROM retrieval_runs "
            "WHERE idempotency_key = ?",
            (command.idempotency_key,),
        ).fetchone()
        if existing is not None:
            if existing["command_fingerprint"] != fingerprint:
                raise RetrievalConflictError(
                    "retrieval idempotency key belongs to a different command"
                )
            return RetrievalRun.model_validate_json(str(existing["snapshot_json"]))
        chunks, report, rewrite = self._retrieve(command)
        manifest = self._manifest(command.scenario_id)
        run_id = _stable_id("run", command.scenario_id, command.idempotency_key)
        evidence = self._document_evidence(run_id, chunks)
        tool_summaries: list[ToolExecutionSummary] = []
        if chunks:
            evidence.extend(self._record_evidence(run_id, command.scenario_id))
            planned_tool = manifest.get("planned_tool")
            if isinstance(planned_tool, dict):
                tool_evidence, tool_summaries = await self._tool_and_calculation_evidence(
                    run_id, command.idempotency_key, planned_tool, command.seed
                )
                evidence.extend(tool_evidence)
        claims = self._claims(evidence)
        validation = self._validate_citations(run_id, evidence, claims)
        run = RetrievalRun(
            run_id=run_id,
            scenario_id=command.scenario_id,
            status="completed" if chunks else "empty",
            query=rewrite,
            filters=command.filters,
            chunks=chunks,
            evidence=evidence,
            claims=claims,
            citation_validation=validation,
            events=self._events(run_id, rewrite, chunks, evidence, validation, tool_summaries),
            tool_executions=tool_summaries,
            import_report=report,
        )
        with self._lock:
            try:
                self._connection.execute(
                    "INSERT INTO retrieval_runs("
                    "run_id, idempotency_key, command_fingerprint, snapshot_json"
                    ") VALUES (?, ?, ?, ?)",
                    (
                        run_id,
                        command.idempotency_key,
                        fingerprint,
                        run.model_dump_json(),
                    ),
                )
                self._connection.commit()
            except sqlite3.IntegrityError as error:
                raise RetrievalConflictError("retrieval idempotency key already exists") from error
        return run

    def get(self, run_id: str) -> RetrievalRun:
        row = self._connection.execute(
            "SELECT snapshot_json FROM retrieval_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise RetrievalRunNotFoundError(run_id)
        return RetrievalRun.model_validate_json(str(row["snapshot_json"]))

    def memory(self) -> MemorySnapshot:
        with self._lock:
            preferences = [
                PreferenceRecord(
                    preference_id=str(row["preference_id"]),
                    key=cast(Any, row["preference_key"]),
                    value=cast(Any, row["preference_value"]),
                    created_at=_iso(str(row["created_at"])),
                    updated_at=_iso(str(row["updated_at"])),
                )
                for row in self._connection.execute(
                    "SELECT * FROM long_term_preferences ORDER BY preference_key"
                ).fetchall()
            ]
            audit = [
                MemoryAuditEntry(
                    sequence=int(row["sequence"]),
                    action=cast(Any, row["action"]),
                    preference_id=str(row["preference_id"]),
                    timestamp=_iso(str(row["timestamp"])),
                    idempotency_key=str(row["idempotency_key"]),
                )
                for row in self._connection.execute(
                    "SELECT * FROM memory_audit ORDER BY sequence"
                ).fetchall()
            ]
            return MemorySnapshot(preferences=preferences, audit=audit)

    def save_preference(self, command: SavePreferenceRequest) -> MemorySnapshot:
        fingerprint = _fingerprint(command.model_dump(mode="json"))
        with self._lock:
            prior = self._connection.execute(
                "SELECT command_fingerprint FROM memory_audit WHERE idempotency_key = ?",
                (command.idempotency_key,),
            ).fetchone()
            if prior is not None:
                if prior["command_fingerprint"] != fingerprint:
                    raise RetrievalConflictError(
                        "memory idempotency key belongs to a different command"
                    )
                return self.memory()
            now = datetime.now(UTC).replace(microsecond=0).isoformat()
            preference_id = _stable_id("pref", command.key)
            existing = self._connection.execute(
                "SELECT created_at FROM long_term_preferences WHERE preference_key = ?",
                (command.key,),
            ).fetchone()
            created_at = str(existing["created_at"]) if existing else now
            self._connection.execute(
                """INSERT OR REPLACE INTO long_term_preferences(
                    preference_id, preference_key, preference_value, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)""",
                (preference_id, command.key, command.value, created_at, now),
            )
            self._connection.execute(
                "INSERT INTO memory_audit("
                "action, preference_id, timestamp, idempotency_key, command_fingerprint"
                ") VALUES ('preference.saved', ?, ?, ?, ?)",
                (preference_id, now, command.idempotency_key, fingerprint),
            )
            self._connection.commit()
            return self.memory()

    def delete_preference(self, command: DeletePreferenceRequest) -> MemorySnapshot:
        fingerprint = _fingerprint(command.model_dump(mode="json"))
        with self._lock:
            prior = self._connection.execute(
                "SELECT command_fingerprint FROM memory_audit WHERE idempotency_key = ?",
                (command.idempotency_key,),
            ).fetchone()
            if prior is not None:
                if prior["command_fingerprint"] != fingerprint:
                    raise RetrievalConflictError(
                        "memory idempotency key belongs to a different command"
                    )
                return self.memory()
            exists = self._connection.execute(
                "SELECT 1 FROM long_term_preferences WHERE preference_id = ?",
                (command.preference_id,),
            ).fetchone()
            if exists is None:
                raise RetrievalConflictError("long-term preference does not exist")
            now = datetime.now(UTC).replace(microsecond=0).isoformat()
            self._connection.execute(
                "DELETE FROM long_term_preferences WHERE preference_id = ?",
                (command.preference_id,),
            )
            self._connection.execute(
                "INSERT INTO memory_audit("
                "action, preference_id, timestamp, idempotency_key, command_fingerprint"
                ") VALUES ('preference.deleted', ?, ?, ?, ?)",
                (command.preference_id, now, command.idempotency_key, fingerprint),
            )
            self._connection.commit()
            return self.memory()
