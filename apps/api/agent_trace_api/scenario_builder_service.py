import base64
import binascii
import hashlib
import io
import json
import mimetypes
import re
import secrets
import sqlite3
import stat
import zipfile
from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from threading import RLock
from typing import Any, cast
from urllib.parse import urljoin

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError
from referencing import Registry, Resource

from agent_trace_api.scenario_builder_models import (
    CapstoneReport,
    CreateScenarioDraftRequest,
    ImportScenarioArchiveRequest,
    LearningEvidence,
    LearningEvidenceRequest,
    LearningMap,
    LearningModule,
    ResetLearningProgressRequest,
    SaveScenarioDraftRequest,
    ScenarioArchive,
    ScenarioBundle,
    ScenarioCatalog,
    ScenarioDetail,
    ScenarioDraft,
    ScenarioPreview,
    ScenarioResource,
    ScenarioSummary,
    ScenarioValidationReport,
    ValidationIssue,
    ValidationSection,
)

SECTION_NAMES = ("manifest", "tools", "graph", "lessons", "policies", "faults", "evals", "data")
REQUIRED_ARCHIVE_FILES = {
    "scenario.json",
    "tools.json",
    "graph.json",
    "lessons.json",
    "policies.json",
    "faults.json",
    "evals.jsonl",
}
MAX_ARCHIVE_BYTES = 5_000_000
MAX_ARCHIVE_FILES = 128
MAX_UNCOMPRESSED_BYTES = 10_000_000
MAX_COMPRESSION_RATIO = 100
RUNTIME_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
RESOURCE_MEDIA_TYPES = {
    ".csv": "text/csv",
    ".db": "application/vnd.sqlite3",
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".json": "application/json",
    ".md": "text/markdown",
    ".png": "image/png",
    ".sqlite": "application/vnd.sqlite3",
    ".svg": "image/svg+xml",
    ".txt": "text/plain",
    ".webp": "image/webp",
}
AVAILABLE_CAPABILITIES = {
    "approval_checkpoint",
    "deterministic_mock",
    "local_data",
    "scenario_builder",
    "sqlite_fts5",
    "typed_evidence",
    "typed_tools",
}

MODULES: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    ("M0", "Agent Mental Model", ("workflow", "autonomy", "responsibility"), "customer-support"),
    ("M1", "Structured Output", ("schema", "validation", "repair"), "data-analyst"),
    ("M2", "Tool Calling", ("tool", "permission", "runtime"), "customer-support"),
    ("M3", "State + Graph", ("state", "node", "edge"), "travel-planner"),
    ("M4", "Streaming Events", ("event", "stream", "projection"), "incident-response"),
    ("M5", "Retrieval + Evidence", ("retrieval", "evidence", "citation"), "research-synthesis"),
    ("M6", "Memory + Persistence", ("checkpoint", "thread", "retention"), "risk-investigation"),
    ("M7", "HITL + Approval", ("interrupt", "approval", "idempotency"), "workflow-approval"),
    (
        "M8",
        "Guardrails + Security",
        ("injection", "authorization", "redaction"),
        "risk-investigation",
    ),
    ("M9", "Tracing + Evals", ("trace", "evaluation", "trajectory"), "data-analyst"),
    ("M10", "MCP · A2A · AG-UI", ("protocol", "boundary", "handoff"), "incident-response"),
    ("M11", "Production Design", ("boundary", "evaluation", "approval"), "workflow-approval"),
)


class ScenarioBuilderInputError(ValueError):
    pass


class ScenarioBuilderConflictError(RuntimeError):
    pass


class ScenarioNotFoundError(KeyError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class ScenarioBuilderService:
    def __init__(self, repository_root: Path, database_path: Path) -> None:
        self.repository_root = repository_root.resolve()
        self.scenarios_root = (self.repository_root / "scenarios").resolve()
        self.contracts_root = (self.repository_root / "contracts").resolve()
        self._lock = RLock()
        self._connection = sqlite3.connect(database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS scenario_drafts (
              draft_id TEXT PRIMARY KEY,
              source_scenario_id TEXT NOT NULL,
              revision INTEGER NOT NULL,
              bundle_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scenario_builder_commands (
              idempotency_key TEXT PRIMARY KEY,
              fingerprint TEXT NOT NULL,
              result_kind TEXT NOT NULL,
              result_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS learning_evidence (
              evidence_id TEXT PRIMARY KEY,
              module_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              scenario_id TEXT NOT NULL,
              passed INTEGER NOT NULL,
              summary TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )
        self._connection.commit()
        self._schemas = self._load_schemas()
        self._registry = self._build_registry()
        self._built_ins = self._load_built_ins()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _load_schemas(self) -> dict[str, dict[str, Any]]:
        names = (
            "scenario-manifest.schema.json",
            "tool-catalog.schema.json",
            "tool-spec.schema.json",
            "graph-definition.schema.json",
            "lesson-catalog.schema.json",
            "policy-set.schema.json",
            "fault-catalog.schema.json",
            "eval-case.schema.json",
        )
        schemas: dict[str, dict[str, Any]] = {}
        for name in names:
            raw = json.loads((self.contracts_root / name).read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise RuntimeError(f"contract {name} must be a JSON object")
            Draft202012Validator.check_schema(raw)
            schemas[name] = raw
        return schemas

    def _build_registry(self) -> Registry[Any]:
        registry: Registry[Any] = Registry()
        resources: dict[str, Resource[Any]] = {}
        for name, schema in self._schemas.items():
            resource = Resource.from_contents(schema)
            resources[name] = resource
            registry = registry.with_resource(name, resource)
            schema_id = schema.get("$id")
            if isinstance(schema_id, str):
                registry = registry.with_resource(schema_id, resource)
        for schema in self._schemas.values():
            schema_id = schema.get("$id")
            if not isinstance(schema_id, str):
                continue
            for name, resource in resources.items():
                registry = registry.with_resource(urljoin(schema_id, name), resource)
        return registry

    def _validator(self, name: str) -> Draft202012Validator:
        return Draft202012Validator(
            self._schemas[name], registry=self._registry, format_checker=FormatChecker()
        )

    @staticmethod
    def _json_file(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError(f"{path.name} must contain a JSON object")
        return value

    def _load_bundle(self, directory: Path) -> ScenarioBundle:
        resolved = directory.resolve()
        if resolved.parent != self.scenarios_root or directory.is_symlink():
            raise RuntimeError("Scenario Kit directory must resolve directly inside scenarios")
        evals: list[dict[str, Any]] = []
        for line in (resolved / "evals.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise RuntimeError("eval JSONL entries must be objects")
                evals.append(value)
        data: dict[str, Any] = {}
        resources: list[ScenarioResource] = []
        data_root = resolved / "data"
        if data_root.is_dir() and not data_root.is_symlink():
            for path in sorted(data_root.rglob("*.json")):
                if path.is_symlink() or not path.is_file():
                    raise RuntimeError("Scenario data files must be regular JSON files")
                relative = path.relative_to(data_root).as_posix()
                data[relative] = json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(resolved.rglob("*")):
            if path.is_symlink():
                raise RuntimeError("Scenario Kit resources must not be symbolic links")
            if not path.is_file():
                continue
            relative = path.relative_to(resolved).as_posix()
            if relative in REQUIRED_ARCHIVE_FILES or (
                relative.startswith("data/") and path.suffix.lower() == ".json"
            ):
                continue
            media_type = self._resource_media_type(relative)
            if media_type is None:
                continue
            mode = path.stat().st_mode
            if mode & 0o111 or not stat.S_ISREG(mode):
                raise RuntimeError("Scenario Kit resources must be non-executable regular files")
            body = path.read_bytes()
            if len(body) > MAX_ARCHIVE_BYTES:
                raise RuntimeError("Scenario Kit resource exceeds the per-file byte boundary")
            resources.append(
                ScenarioResource(
                    path=relative,
                    media_type=cast(Any, media_type),
                    byte_count=len(body),
                    sha256=hashlib.sha256(body).hexdigest(),
                    content_base64=base64.b64encode(body).decode("ascii"),
                )
            )
        return ScenarioBundle(
            manifest=self._json_file(resolved / "scenario.json"),
            tools=self._json_file(resolved / "tools.json"),
            graph=self._json_file(resolved / "graph.json"),
            lessons=self._json_file(resolved / "lessons.json"),
            policies=self._json_file(resolved / "policies.json"),
            faults=self._json_file(resolved / "faults.json"),
            evals=evals,
            data=data,
            resources=resources,
        )

    @staticmethod
    def _resource_media_type(name: str) -> str | None:
        path = PurePosixPath(name)
        suffix = path.suffix.lower()
        allowed = (
            name == "retrieval.json"
            or (len(path.parts) >= 2 and path.parts[0] == "documents" and suffix == ".md")
            or (
                len(path.parts) >= 2
                and path.parts[0] == "data"
                and suffix in {".csv", ".sqlite", ".db", ".md"}
            )
            or (
                len(path.parts) >= 2
                and path.parts[0] == "assets"
                and suffix in RESOURCE_MEDIA_TYPES
            )
        )
        if not allowed:
            return None
        return RESOURCE_MEDIA_TYPES.get(suffix) or mimetypes.types_map.get(suffix)

    def _load_built_ins(self) -> dict[str, ScenarioBundle]:
        expected = {
            "customer-support",
            "research-synthesis",
            "data-analyst",
            "travel-planner",
            "incident-response",
            "workflow-approval",
            "risk-investigation",
        }
        bundles: dict[str, ScenarioBundle] = {}
        for scenario_id in sorted(expected):
            bundle = self._load_bundle(self.scenarios_root / scenario_id)
            report = self.validate_bundle(bundle)
            if not report.valid:
                messages = "; ".join(
                    issue.message for section in report.sections for issue in section.issues
                )
                raise RuntimeError(f"built-in Scenario Kit {scenario_id} is invalid: {messages}")
            if bundle.manifest.get("id") != scenario_id:
                raise RuntimeError(f"built-in Scenario Kit identity mismatch: {scenario_id}")
            bundles[scenario_id] = bundle
        return bundles

    @staticmethod
    def _issue(
        section: str,
        path: str,
        code: str,
        message: str,
        hint: str,
        *,
        severity: str = "error",
    ) -> ValidationIssue:
        return ValidationIssue(
            severity=cast(Any, severity),
            section=cast(Any, section),
            path=path,
            code=code,
            message=message,
            hint=hint,
        )

    def _schema_issues(self, section: str, schema: str, value: Any) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        errors = sorted(
            self._validator(schema).iter_errors(value), key=lambda item: list(item.path)
        )
        for error in errors:
            path = "$" + "".join(
                f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.path
            )
            issues.append(
                self._issue(
                    section,
                    path,
                    "SCHEMA_INVALID",
                    error.message,
                    "Repair the value against the repository contract shown in this section.",
                )
            )
        return issues

    def validate_bundle(self, bundle: ScenarioBundle) -> ScenarioValidationReport:
        section_values: dict[str, tuple[str, Any, int]] = {
            "manifest": ("scenario-manifest.schema.json", bundle.manifest, 1),
            "tools": (
                "tool-catalog.schema.json",
                bundle.tools,
                len(cast(list[Any], bundle.tools.get("tools", []))),
            ),
            "graph": (
                "graph-definition.schema.json",
                bundle.graph,
                len(cast(list[Any], bundle.graph.get("nodes", []))),
            ),
            "lessons": (
                "lesson-catalog.schema.json",
                bundle.lessons,
                len(cast(list[Any], bundle.lessons.get("lessons", []))),
            ),
            "policies": (
                "policy-set.schema.json",
                bundle.policies,
                len(cast(list[Any], bundle.policies.get("rules", []))),
            ),
            "faults": (
                "fault-catalog.schema.json",
                bundle.faults,
                len(cast(list[Any], bundle.faults.get("faults", []))),
            ),
            "evals": ("eval-case.schema.json", bundle.evals, len(bundle.evals)),
            "data": ("", bundle.data, len(bundle.data)),
        }
        issues: dict[str, list[ValidationIssue]] = {name: [] for name in SECTION_NAMES}
        for section, (schema, value, _checked) in section_values.items():
            if section == "evals":
                if not bundle.evals:
                    issues[section].append(
                        self._issue(
                            section,
                            "$",
                            "EVALS_EMPTY",
                            "At least one Golden case is required.",
                            "Add a deterministic eval case before preview or export.",
                        )
                    )
                for index, case in enumerate(bundle.evals):
                    for issue in self._schema_issues(section, schema, case):
                        issues[section].append(
                            issue.model_copy(update={"path": f"$[{index}]{issue.path[1:]}"})
                        )
            elif section == "data":
                if not bundle.data:
                    issues[section].append(
                        self._issue(
                            section,
                            "$",
                            "DATA_EMPTY",
                            "At least one local JSON data fixture is required.",
                            "Add a fictional UTF-8 JSON fixture under data/.",
                        )
                    )
                seen_paths: set[str] = set()
                for name in bundle.data:
                    path = PurePosixPath(name)
                    if (
                        "\x00" in name
                        or "\\" in name
                        or path.is_absolute()
                        or not path.parts
                        or any(part in {"", ".", ".."} for part in path.parts)
                        or name != path.as_posix()
                        or path.suffix != ".json"
                        or name.casefold() in seen_paths
                    ):
                        issues[section].append(
                            self._issue(
                                section,
                                f"$.{name}",
                                "DATA_PATH_UNSAFE",
                                "Data fixtures must use safe relative .json paths.",
                                "Use a name such as cases.json without parent traversal.",
                            )
                        )
                    seen_paths.add(name.casefold())
                for resource in bundle.resources:
                    path = PurePosixPath(resource.path)
                    decode_failed = False
                    try:
                        body = base64.b64decode(resource.content_base64, validate=True)
                    except (binascii.Error, ValueError):
                        decode_failed = True
                        body = b""
                    if (
                        decode_failed
                        or "\x00" in resource.path
                        or "\\" in resource.path
                        or path.is_absolute()
                        or not path.parts
                        or any(part in {"", ".", ".."} for part in path.parts)
                        or resource.path != path.as_posix()
                        or self._resource_media_type(resource.path) != resource.media_type
                        or resource.path.casefold() in seen_paths
                        or len(body) != resource.byte_count
                        or hashlib.sha256(body).hexdigest() != resource.sha256
                    ):
                        issues[section].append(
                            self._issue(
                                section,
                                f"$.resources[{resource.path}]",
                                "RESOURCE_INVALID",
                                "Packaged resources must use an allowed path, "
                                "unique name and exact digest.",
                                "Keep resources inside their declared package roots.",
                            )
                        )
                    seen_paths.add(resource.path.casefold())
            else:
                issues[section].extend(self._schema_issues(section, schema, value))

        manifest_id = bundle.manifest.get("id")
        graph_id = bundle.graph.get("id")
        if bundle.manifest.get("default_graph_id") != graph_id:
            issues["graph"].append(
                self._issue(
                    "graph",
                    "$.id",
                    "GRAPH_REF_MISMATCH",
                    "Manifest default_graph_id does not match graph.id.",
                    "Use one stable graph identifier in both files.",
                )
            )
        expected_refs = {
            "tool_catalog_ref": "tools.json",
            "lesson_ref": "lessons.json",
            "eval_ref": "evals.jsonl",
            "policy_ref": "policies.json",
            "fault_ref": "faults.json",
        }
        for key, expected in expected_refs.items():
            if bundle.manifest.get(key) != expected:
                issues["manifest"].append(
                    self._issue(
                        "manifest",
                        f"$.{key}",
                        "REF_MISSING_OR_UNSAFE",
                        f"{key} must resolve to the package-local {expected} file.",
                        f"Set {key} to {expected}; parent and absolute paths are forbidden.",
                    )
                )
        compatibility_value = bundle.manifest.get("compatibility")
        if isinstance(compatibility_value, dict):
            required = compatibility_value.get("required_capabilities", [])
            compatible = (
                compatibility_value.get("core") == ">=0.1.0 <0.2.0"
                and compatibility_value.get("event_schema") == "1.0.0"
                and isinstance(required, list)
                and all(item in AVAILABLE_CAPABILITIES for item in required)
            )
        else:
            compatible = False
        if not compatible:
            issues["manifest"].append(
                self._issue(
                    "manifest",
                    "$.compatibility",
                    "CORE_INCOMPATIBLE",
                    "Scenario compatibility does not match this local runtime.",
                    "Target core >=0.1.0 <0.2.0, event schema 1.0.0 and available capabilities.",
                )
            )
        node_ids = {
            node.get("id")
            for node in cast(list[dict[str, Any]], bundle.graph.get("nodes", []))
            if isinstance(node, dict)
        }
        for index, node in enumerate(cast(list[dict[str, Any]], bundle.graph.get("nodes", []))):
            if not isinstance(node, dict):
                continue
            node_id = node.get("id")
            if not isinstance(node_id, str) or RUNTIME_IDENTIFIER.fullmatch(node_id) is None:
                issues["graph"].append(
                    self._issue(
                        "graph",
                        f"$.nodes[{index}].id",
                        "RUNTIME_NODE_ID_INVALID",
                        "Node IDs must be safe canonical AgentEvent node identifiers.",
                        "Use lowercase letters, digits and underscores, beginning with a letter.",
                    )
                )
            for field in ("reads", "writes"):
                for state_index, state_key in enumerate(cast(list[Any], node.get(field, []))):
                    if (
                        not isinstance(state_key, str)
                        or RUNTIME_IDENTIFIER.fullmatch(state_key) is None
                    ):
                        issues["graph"].append(
                            self._issue(
                                "graph",
                                f"$.nodes[{index}].{field}[{state_index}]",
                                "RUNTIME_STATE_KEY_INVALID",
                                "State keys must be safe canonical runtime identifiers.",
                                "Use lowercase letters, digits and underscores; "
                                "begin with a letter.",
                            )
                        )
        for index, edge in enumerate(cast(list[dict[str, Any]], bundle.graph.get("edges", []))):
            if isinstance(edge, dict) and (
                edge.get("from") not in node_ids or edge.get("to") not in node_ids
            ):
                issues["graph"].append(
                    self._issue(
                        "graph",
                        f"$.edges[{index}]",
                        "EDGE_REF_INVALID",
                        "Graph edge refers to a node that is not declared.",
                        "Choose existing node IDs for both edge endpoints.",
                    )
                )
        for key in ("entry_node",):
            if bundle.graph.get(key) not in node_ids:
                issues["graph"].append(
                    self._issue(
                        "graph",
                        f"$.{key}",
                        "NODE_REF_INVALID",
                        f"{key} must name a declared graph node.",
                        "Select a node ID from graph.nodes.",
                    )
                )
        for node_id in cast(list[Any], bundle.graph.get("terminal_nodes", [])):
            if node_id not in node_ids:
                issues["graph"].append(
                    self._issue(
                        "graph",
                        "$.terminal_nodes",
                        "NODE_REF_INVALID",
                        "terminal_nodes contains an undeclared node.",
                        "Remove the value or add the node to graph.nodes.",
                    )
                )
        for index, lesson in enumerate(
            cast(list[dict[str, Any]], bundle.lessons.get("lessons", []))
        ):
            if isinstance(lesson, dict) and lesson.get("scenario_id") != manifest_id:
                issues["lessons"].append(
                    self._issue(
                        "lessons",
                        f"$.lessons[{index}].scenario_id",
                        "SCENARIO_REF_MISMATCH",
                        "Lesson scenario_id must match the manifest identity.",
                        "Update the lesson reference after cloning or renaming the Kit.",
                    )
                )
        for index, fault in enumerate(cast(list[dict[str, Any]], bundle.faults.get("faults", []))):
            if (
                isinstance(fault, dict)
                and fault.get("node_id") is not None
                and fault.get("node_id") not in node_ids
            ):
                issues["faults"].append(
                    self._issue(
                        "faults",
                        f"$.faults[{index}].node_id",
                        "NODE_REF_INVALID",
                        "Fault node_id must name a declared graph node.",
                        "Select a node ID from graph.nodes or remove the optional node reference.",
                    )
                )
        for index, case in enumerate(bundle.evals):
            if case.get("scenario_id") != manifest_id:
                issues["evals"].append(
                    self._issue(
                        "evals",
                        f"$[{index}].scenario_id",
                        "SCENARIO_REF_MISMATCH",
                        "Golden case scenario_id must match the manifest identity.",
                        "Update the Golden case reference after cloning or renaming the Kit.",
                    )
                )
        sections: list[ValidationSection] = []
        for name in SECTION_NAMES:
            section_issues = issues[name]
            sections.append(
                ValidationSection(
                    name=cast(Any, name),
                    status="failed"
                    if any(item.severity == "error" for item in section_issues)
                    else "passed",
                    checked=section_values[name][2],
                    issues=section_issues,
                )
            )
        error_count = sum(
            item.severity == "error" for section in sections for item in section.issues
        )
        warning_count = sum(
            item.severity == "warning" for section in sections for item in section.issues
        )
        return ScenarioValidationReport(
            valid=error_count == 0,
            compatible=compatible,
            fingerprint=_fingerprint(bundle.model_dump(mode="json")),
            error_count=error_count,
            warning_count=warning_count,
            sections=sections,
        )

    def _summary(self, bundle: ScenarioBundle) -> ScenarioSummary:
        report = self.validate_bundle(bundle)
        return ScenarioSummary(
            id=cast(str, bundle.manifest["id"]),
            name=cast(str, bundle.manifest["name"]),
            short_description=cast(str, bundle.manifest["short_description"]),
            domain=cast(str, bundle.manifest["domain"]),
            difficulty=cast(Any, bundle.manifest["difficulty"]),
            concepts=cast(list[str], bundle.manifest["concepts"]),
            data_classification=cast(Any, bundle.manifest["data_classification"]),
            local_only=cast(bool, bundle.manifest["local_only"]),
            tool_count=len(cast(list[Any], bundle.tools["tools"])),
            lesson_count=len(cast(list[Any], bundle.lessons["lessons"])),
            golden_count=len(bundle.evals),
            graph_template=cast(str, bundle.graph["template"]),
            validation_status="passed" if report.valid else "failed",
            compatible=report.compatible,
        )

    def catalog(self) -> ScenarioCatalog:
        return ScenarioCatalog(
            scenarios=[self._summary(self._built_ins[key]) for key in sorted(self._built_ins)]
        )

    def detail(self, scenario_id: str) -> ScenarioDetail:
        bundle = self._built_ins.get(scenario_id)
        if bundle is None:
            raise ScenarioNotFoundError(scenario_id)
        return ScenarioDetail(
            summary=self._summary(bundle),
            manifest=deepcopy(bundle.manifest),
            report=self.validate_bundle(bundle),
        )

    def _existing_command(self, key: str, fingerprint: str, result_kind: str) -> str | None:
        row = self._connection.execute(
            "SELECT fingerprint, result_kind, result_json "
            "FROM scenario_builder_commands WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        if row["fingerprint"] != fingerprint or row["result_kind"] != result_kind:
            raise ScenarioBuilderConflictError(
                "idempotency key is bound to a different builder command"
            )
        return cast(str, row["result_json"])

    def _record_command(
        self, key: str, fingerprint: str, result_kind: str, result_json: str
    ) -> None:
        self._connection.execute(
            "INSERT INTO scenario_builder_commands "
            "(idempotency_key, fingerprint, result_kind, result_json) "
            "VALUES (?, ?, ?, ?)",
            (key, fingerprint, result_kind, result_json),
        )

    def _stored_draft(self, draft_id: str) -> ScenarioDraft:
        row = self._connection.execute(
            "SELECT source_scenario_id, revision, bundle_json, updated_at "
            "FROM scenario_drafts WHERE draft_id = ?",
            (draft_id,),
        ).fetchone()
        if row is None:
            raise ScenarioNotFoundError(draft_id)
        bundle = ScenarioBundle.model_validate_json(row["bundle_json"])
        return ScenarioDraft(
            draft_id=draft_id,
            revision=row["revision"],
            source_scenario_id=row["source_scenario_id"],
            bundle=bundle,
            report=self.validate_bundle(bundle),
            updated_at=row["updated_at"],
            audit=["Loaded from repository-local SQLite draft storage."],
        )

    def create_draft(self, command: CreateScenarioDraftRequest) -> ScenarioDraft:
        source = self._built_ins.get(command.source_scenario_id)
        if source is None:
            raise ScenarioNotFoundError(command.source_scenario_id)
        fingerprint = _fingerprint(command.model_dump(mode="json"))
        with self._lock, self._connection:
            existing = self._existing_command(command.idempotency_key, fingerprint, "draft")
            if existing is not None:
                return ScenarioDraft.model_validate_json(existing)
            draft_id = f"draft_{secrets.token_hex(8)}"
            updated_at = _now()
            bundle = source.model_copy(deep=True)
            draft = ScenarioDraft(
                draft_id=draft_id,
                revision=1,
                source_scenario_id=command.source_scenario_id,
                bundle=bundle,
                report=self.validate_bundle(bundle),
                updated_at=updated_at,
                audit=[
                    "Explicit local Builder approval verified.",
                    f"Cloned built-in Scenario Kit {command.source_scenario_id} "
                    "without changing core code.",
                ],
            )
            self._connection.execute(
                "INSERT INTO scenario_drafts "
                "(draft_id, source_scenario_id, revision, bundle_json, updated_at) "
                "VALUES (?, ?, 1, ?, ?)",
                (draft_id, command.source_scenario_id, bundle.model_dump_json(), updated_at),
            )
            self._record_command(
                command.idempotency_key, fingerprint, "draft", draft.model_dump_json()
            )
            return draft

    def get_draft(self, draft_id: str) -> ScenarioDraft:
        with self._lock:
            return self._stored_draft(draft_id)

    def save_draft(self, draft_id: str, command: SaveScenarioDraftRequest) -> ScenarioDraft:
        fingerprint = _fingerprint({"draft_id": draft_id, **command.model_dump(mode="json")})
        with self._lock, self._connection:
            existing = self._existing_command(command.idempotency_key, fingerprint, "draft")
            if existing is not None:
                return ScenarioDraft.model_validate_json(existing)
            row = self._connection.execute(
                "SELECT source_scenario_id, revision FROM scenario_drafts WHERE draft_id = ?",
                (draft_id,),
            ).fetchone()
            if row is None:
                raise ScenarioNotFoundError(draft_id)
            if row["revision"] != command.expected_revision:
                raise ScenarioBuilderConflictError("draft revision is stale; reload before saving")
            next_revision = command.expected_revision + 1
            updated_at = _now()
            report = self.validate_bundle(command.bundle)
            draft = ScenarioDraft(
                draft_id=draft_id,
                revision=next_revision,
                source_scenario_id=row["source_scenario_id"],
                bundle=command.bundle,
                report=report,
                updated_at=updated_at,
                audit=[
                    "Explicit local Builder approval verified.",
                    "Form and raw manifest state passed through the same typed draft boundary.",
                ],
            )
            cursor = self._connection.execute(
                "UPDATE scenario_drafts SET revision = ?, bundle_json = ?, updated_at = ? "
                "WHERE draft_id = ? AND revision = ?",
                (
                    next_revision,
                    command.bundle.model_dump_json(),
                    updated_at,
                    draft_id,
                    command.expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise ScenarioBuilderConflictError("draft revision changed while saving")
            self._record_command(
                command.idempotency_key, fingerprint, "draft", draft.model_dump_json()
            )
            return draft

    def validate_draft(self, draft_id: str) -> ScenarioValidationReport:
        return self.validate_bundle(self.get_draft(draft_id).bundle)

    @staticmethod
    def _preview(bundle: ScenarioBundle, source: str, source_id: str) -> ScenarioPreview:
        report_fingerprint = _fingerprint(bundle.model_dump(mode="json"))
        lesson = cast(list[dict[str, Any]], bundle.lessons["lessons"])[0]
        golden = bundle.evals[0]
        nodes = cast(list[dict[str, Any]], bundle.graph["nodes"])
        return ScenarioPreview(
            source=cast(Any, source),
            source_id=source_id,
            scenario_id=cast(str, bundle.manifest["id"]),
            title=cast(str, bundle.manifest["name"]),
            objective=cast(str, lesson["objective"]),
            graph_template=cast(str, bundle.graph["template"]),
            nodes=nodes,
            edges=cast(list[dict[str, Any]], bundle.graph["edges"]),
            tool_names=[item["name"] for item in cast(list[dict[str, Any]], bundle.tools["tools"])],
            lesson=lesson,
            golden_case=golden,
            validation_fingerprint=report_fingerprint,
        )

    def preview_bundle(self, source: str, source_id: str) -> ScenarioBundle:
        if source == "built_in":
            bundle = self._built_ins.get(source_id)
            if bundle is None:
                raise ScenarioNotFoundError(source_id)
        elif source == "draft":
            draft = self.get_draft(source_id)
            if not draft.report.valid:
                raise ScenarioBuilderConflictError(
                    "invalid drafts are inspectable but cannot enter live preview"
                )
            bundle = draft.bundle
        else:
            raise ScenarioBuilderInputError("preview source must be built_in or draft")
        return bundle.model_copy(deep=True)

    def built_in_preview(self, scenario_id: str) -> ScenarioPreview:
        bundle = self._built_ins.get(scenario_id)
        if bundle is None:
            raise ScenarioNotFoundError(scenario_id)
        return self._preview(bundle, "built_in", scenario_id)

    def draft_preview(self, draft_id: str) -> ScenarioPreview:
        draft = self.get_draft(draft_id)
        if not draft.report.valid:
            raise ScenarioBuilderConflictError(
                "invalid drafts are inspectable but cannot enter live preview"
            )
        return self._preview(draft.bundle, "draft", draft_id)

    @staticmethod
    def _archive_member(info: zipfile.ZipInfo) -> str | None:
        original_name = info.orig_filename
        name = info.filename
        path = PurePosixPath(name)
        mode = info.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        is_directory = info.is_dir()
        canonical_name = f"{path.as_posix()}/" if is_directory else path.as_posix()
        if (
            original_name != name
            or "\x00" in original_name
            or "\\" in name
            or path.is_absolute()
            or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)
            or name != canonical_name
            or len(path.parts) > 3
            or stat.S_ISLNK(mode)
            or info.flag_bits & 0x1
        ):
            raise ScenarioBuilderInputError("Scenario archive contains an unsafe member")
        if is_directory:
            if file_type not in {0, stat.S_IFDIR} or path.parts[0] not in {
                "assets",
                "data",
                "documents",
            }:
                raise ScenarioBuilderInputError("Scenario archive contains an unsafe member")
            return None
        if file_type not in {0, stat.S_IFREG} or mode & 0o111:
            raise ScenarioBuilderInputError("Scenario archive contains an unsafe member")
        if (
            name in REQUIRED_ARCHIVE_FILES
            or (path.parts[0] == "data" and path.suffix == ".json")
            or ScenarioBuilderService._resource_media_type(name) is not None
        ):
            return name
        raise ScenarioBuilderInputError("Scenario archive contains a disallowed file")

    def import_archive(self, command: ImportScenarioArchiveRequest) -> ScenarioDraft:
        try:
            archive = base64.b64decode(command.archive_base64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ScenarioBuilderInputError("Scenario archive base64 is invalid") from error
        if not archive or len(archive) > MAX_ARCHIVE_BYTES:
            raise ScenarioBuilderInputError("Scenario archive exceeds the compressed byte boundary")
        fingerprint = _fingerprint(
            {"filename": command.filename, "archive_sha256": hashlib.sha256(archive).hexdigest()}
        )
        with self._lock, self._connection:
            existing = self._existing_command(command.idempotency_key, fingerprint, "draft")
            if existing is not None:
                return ScenarioDraft.model_validate_json(existing)
            try:
                package = zipfile.ZipFile(io.BytesIO(archive))
            except (zipfile.BadZipFile, OSError) as error:
                raise ScenarioBuilderInputError(
                    "Scenario archive is not a valid ZIP file"
                ) from error
            payloads: dict[str, bytes] = {}
            try:
                with package:
                    infos = package.infolist()
                    if not infos or len(infos) > MAX_ARCHIVE_FILES:
                        raise ScenarioBuilderInputError(
                            "Scenario archive violates the file-count boundary"
                        )
                    if sum(info.file_size for info in infos) > MAX_UNCOMPRESSED_BYTES:
                        raise ScenarioBuilderInputError(
                            "Scenario archive exceeds the uncompressed boundary"
                        )
                    for info in infos:
                        name = self._archive_member(info)
                        if name is None:
                            continue
                        if name.casefold() in {item.casefold() for item in payloads}:
                            raise ScenarioBuilderInputError(
                                "Scenario archive contains duplicate paths"
                            )
                        if info.file_size / max(info.compress_size, 1) > MAX_COMPRESSION_RATIO:
                            raise ScenarioBuilderInputError(
                                "Scenario archive contains a compression bomb"
                            )
                        body = package.read(info)
                        if len(body) != info.file_size:
                            raise ScenarioBuilderInputError(
                                "Scenario archive member failed bounded inspection"
                            )
                        media_type = self._resource_media_type(name)
                        if (
                            media_type is None
                            or media_type.startswith("text/")
                            or media_type == "application/json"
                        ):
                            if b"\x00" in body:
                                raise ScenarioBuilderInputError(
                                    "Scenario archive text member contains a NUL byte"
                                )
                            try:
                                body.decode("utf-8")
                            except UnicodeDecodeError as error:
                                raise ScenarioBuilderInputError(
                                    "Scenario archive text resources must be UTF-8"
                                ) from error
                        payloads[name] = body
            except ScenarioBuilderInputError:
                raise
            except (
                EOFError,
                NotImplementedError,
                OSError,
                RuntimeError,
                zipfile.BadZipFile,
            ) as error:
                raise ScenarioBuilderInputError(
                    "Scenario archive member failed integrity inspection"
                ) from error
            if not REQUIRED_ARCHIVE_FILES.issubset(payloads):
                missing = sorted(REQUIRED_ARCHIVE_FILES - payloads.keys())
                raise ScenarioBuilderInputError(
                    f"Scenario archive is missing: {', '.join(missing)}"
                )
            try:
                evals = [
                    json.loads(line)
                    for line in payloads["evals.jsonl"].decode("utf-8").splitlines()
                    if line.strip()
                ]
                bundle = ScenarioBundle(
                    manifest=json.loads(payloads["scenario.json"]),
                    tools=json.loads(payloads["tools.json"]),
                    graph=json.loads(payloads["graph.json"]),
                    lessons=json.loads(payloads["lessons.json"]),
                    policies=json.loads(payloads["policies.json"]),
                    faults=json.loads(payloads["faults.json"]),
                    evals=evals,
                    data={
                        name.removeprefix("data/"): json.loads(body)
                        for name, body in payloads.items()
                        if name.startswith("data/") and name.endswith(".json")
                    },
                    resources=[
                        ScenarioResource(
                            path=name,
                            media_type=cast(Any, self._resource_media_type(name)),
                            byte_count=len(body),
                            sha256=hashlib.sha256(body).hexdigest(),
                            content_base64=base64.b64encode(body).decode("ascii"),
                        )
                        for name, body in payloads.items()
                        if name not in REQUIRED_ARCHIVE_FILES
                        and not (name.startswith("data/") and name.endswith(".json"))
                    ],
                )
            except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as error:
                raise ScenarioBuilderInputError("Scenario archive JSON is invalid") from error
            report = self.validate_bundle(bundle)
            if not report.valid:
                first = next(issue for section in report.sections for issue in section.issues)
                raise ScenarioBuilderInputError(
                    f"Scenario archive validation failed at {first.section}{first.path}: "
                    f"{first.message}"
                )
            source_id = cast(str, bundle.manifest["id"])
            draft_id = f"draft_{secrets.token_hex(8)}"
            updated_at = _now()
            draft = ScenarioDraft(
                draft_id=draft_id,
                revision=1,
                source_scenario_id=source_id,
                bundle=bundle,
                report=report,
                updated_at=updated_at,
                audit=[
                    "Explicit local Builder approval verified.",
                    "Bounded ZIP inspection rejected traversal, links, executables "
                    "and oversized members.",
                    "Imported files remained data; no package code was executed.",
                ],
            )
            self._connection.execute(
                "INSERT INTO scenario_drafts "
                "(draft_id, source_scenario_id, revision, bundle_json, updated_at) "
                "VALUES (?, ?, 1, ?, ?)",
                (draft_id, source_id, bundle.model_dump_json(), updated_at),
            )
            self._record_command(
                command.idempotency_key, fingerprint, "draft", draft.model_dump_json()
            )
            return draft

    def export_archive(self, draft_id: str) -> ScenarioArchive:
        draft = self.get_draft(draft_id)
        if not draft.report.valid:
            raise ScenarioBuilderConflictError("only valid Scenario Kits can be exported")
        output = io.BytesIO()
        with zipfile.ZipFile(
            output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as package:

            def write_text(name: str, value: str) -> None:
                member = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                member.compress_type = zipfile.ZIP_DEFLATED
                member.external_attr = 0o100644 << 16
                package.writestr(member, value)

            files: Mapping[str, Any] = {
                "scenario.json": draft.bundle.manifest,
                "tools.json": draft.bundle.tools,
                "graph.json": draft.bundle.graph,
                "lessons.json": draft.bundle.lessons,
                "policies.json": draft.bundle.policies,
                "faults.json": draft.bundle.faults,
            }
            for name, value in files.items():
                write_text(name, json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            eval_text = "\n".join(_canonical(case) for case in draft.bundle.evals) + "\n"
            write_text("evals.jsonl", eval_text)
            for name, value in sorted(draft.bundle.data.items()):
                write_text(f"data/{name}", json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            for resource in sorted(draft.bundle.resources, key=lambda item: item.path):
                member = zipfile.ZipInfo(resource.path, date_time=(1980, 1, 1, 0, 0, 0))
                member.compress_type = zipfile.ZIP_DEFLATED
                member.external_attr = 0o100644 << 16
                package.writestr(member, base64.b64decode(resource.content_base64, validate=True))
        body = output.getvalue()
        scenario_id = cast(str, draft.bundle.manifest["id"])
        return ScenarioArchive(
            filename=f"{scenario_id}-scenario-kit.zip",
            sha256=hashlib.sha256(body).hexdigest(),
            byte_count=len(body),
            archive_base64=base64.b64encode(body).decode("ascii"),
            validation_fingerprint=draft.report.fingerprint,
        )

    def _evidence(self, module_id: str) -> list[LearningEvidence]:
        rows = self._connection.execute(
            "SELECT evidence_id, module_id, kind, scenario_id, passed, summary, created_at "
            "FROM learning_evidence WHERE module_id = ? ORDER BY created_at, evidence_id",
            (module_id,),
        ).fetchall()
        return [
            LearningEvidence(
                evidence_id=row["evidence_id"],
                module_id=row["module_id"],
                kind=row["kind"],
                scenario_id=row["scenario_id"],
                passed=bool(row["passed"]),
                summary=row["summary"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def learning_map(self) -> LearningMap:
        modules: list[LearningModule] = []
        total_evidence = 0
        for index, (module_id, title, concepts, scenario_id) in enumerate(MODULES):
            evidence = self._evidence(module_id)
            total_evidence += len(evidence)
            passed_kinds = {item.kind for item in evidence if item.passed}
            count = len(passed_kinds)
            levels = ("not_started", "observe", "diagnose", "design", "stress", "ship")
            modules.append(
                LearningModule(
                    id=module_id,
                    title=title,
                    concepts=list(concepts),
                    estimated_minutes=25 + (index % 6) * 5,
                    prerequisite=MODULES[index - 1][0] if index else None,
                    recommended_scenario=cast(Any, scenario_id),
                    lens_copy={
                        "pm": f"Frame {title} through value, authority and failure cost.",
                        "builder": f"Inspect the typed contracts, events and tests behind {title}.",
                        "architect": (
                            f"Defend the boundaries, reliability and trade-offs of {title}."
                        ),
                    },
                    mastery_level=cast(Any, levels[min(count, 5)]),
                    completed=count >= 3,
                    evidence=evidence,
                )
            )
        return LearningMap(
            modules=modules,
            completed_modules=sum(module.completed for module in modules),
            evidence_count=total_evidence,
            completion_rule=(
                "Three distinct passing evidence kinds are required; navigation actions "
                "are never evidence."
            ),
        )

    def record_evidence(self, module_id: str, command: LearningEvidenceRequest) -> LearningMap:
        module = next((item for item in MODULES if item[0] == module_id), None)
        if module is None:
            raise ScenarioNotFoundError(module_id)
        if command.scenario_id not in self._built_ins:
            raise ScenarioNotFoundError(command.scenario_id)
        fingerprint = _fingerprint({"module_id": module_id, **command.model_dump(mode="json")})
        with self._lock, self._connection:
            existing = self._existing_command(command.idempotency_key, fingerprint, "learning-map")
            if existing is not None:
                return LearningMap.model_validate_json(existing)
            response = command.response.strip().lower()
            concepts = module[2]
            if command.kind == "prediction":
                passed = any(concept in response for concept in concepts)
                summary = (
                    "Prediction names a relevant system concept."
                    if passed
                    else "Prediction needs one module concept."
                )
            elif command.kind == "machine_assertion":
                report = self.validate_bundle(self._built_ins[command.scenario_id])
                passed = report.valid and response in {"run", "validate", "assert"}
                summary = (
                    f"Scenario contract assertion {'passed' if passed else 'failed'} "
                    f"({report.fingerprint[:12]})."
                )
            elif command.kind == "explain_back":
                matches = sum(concept in response for concept in concepts)
                passed = matches >= 2 and len(response.split()) >= 8
                summary = f"Explain-back rubric matched {matches}/3 required concepts."
            elif command.kind == "case_run":
                passed = response.startswith("run:") and len(response) > 8
                summary = (
                    "A concrete run identity was supplied."
                    if passed
                    else "A run identity is required."
                )
            elif command.kind == "fault_repair":
                passed = "repair" in response and "trace" in response
                summary = (
                    "Repair evidence names both the repair and trace."
                    if passed
                    else "Repair evidence is incomplete."
                )
            else:
                passed = response.startswith("sha256:") and len(response) == 71
                summary = (
                    "Export artifact digest recorded."
                    if passed
                    else "A SHA-256 export digest is required."
                )
            evidence = LearningEvidence(
                evidence_id=f"learn_{secrets.token_hex(10)}",
                module_id=module_id,
                kind=command.kind,
                scenario_id=command.scenario_id,
                passed=passed,
                summary=summary,
                created_at=_now(),
            )
            self._connection.execute(
                "INSERT INTO learning_evidence "
                "(evidence_id, module_id, kind, scenario_id, passed, summary, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    evidence.evidence_id,
                    module_id,
                    command.kind,
                    command.scenario_id,
                    int(passed),
                    summary,
                    evidence.created_at,
                ),
            )
            result = self.learning_map()
            self._record_command(
                command.idempotency_key, fingerprint, "learning-map", result.model_dump_json()
            )
            return result

    def reset_progress(self, module_id: str, command: ResetLearningProgressRequest) -> LearningMap:
        if command.scope == "module" and not any(item[0] == module_id for item in MODULES):
            raise ScenarioNotFoundError(module_id)
        fingerprint = _fingerprint({"module_id": module_id, **command.model_dump(mode="json")})
        with self._lock, self._connection:
            existing = self._existing_command(command.idempotency_key, fingerprint, "learning-map")
            if existing is not None:
                return LearningMap.model_validate_json(existing)
            if command.scope == "all":
                self._connection.execute("DELETE FROM learning_evidence")
            else:
                self._connection.execute(
                    "DELETE FROM learning_evidence WHERE module_id = ?", (module_id,)
                )
            result = self.learning_map()
            self._record_command(
                command.idempotency_key, fingerprint, "learning-map", result.model_dump_json()
            )
            return result

    def capstone_report(self, draft_id: str) -> CapstoneReport:
        draft = self.get_draft(draft_id)
        if not draft.report.valid:
            raise ScenarioBuilderConflictError(
                "capstone generation requires a saved, valid Scenario Kit"
            )
        scenario_id = cast(str, draft.bundle.manifest["id"])
        sections = [
            "Agent necessity",
            "User journey",
            "State graph",
            "Tool catalog",
            "Schemas",
            "Evidence policy",
            "Approval and safety",
            "Evaluation set",
            "Rollout",
            "Architecture defense",
        ]
        graph_nodes = cast(list[dict[str, Any]], draft.bundle.graph["nodes"])
        graph_edges = cast(list[dict[str, Any]], draft.bundle.graph["edges"])
        tools = cast(list[dict[str, Any]], draft.bundle.tools["tools"])
        lessons = cast(list[dict[str, Any]], draft.bundle.lessons["lessons"])
        rules = cast(list[dict[str, Any]], draft.bundle.policies.get("rules", []))
        faults = cast(list[dict[str, Any]], draft.bundle.faults.get("faults", []))
        assertion_count = sum(
            len(cast(list[Any], case.get("assertions", []))) for case in draft.bundle.evals
        )
        preview_runs = 0
        preview_events = 0
        table = self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='scenario_preview_runs'"
        ).fetchone()
        if table is not None:
            rows = self._connection.execute(
                "SELECT run_id, state_json FROM scenario_preview_runs"
            ).fetchall()
            matching_run_ids: list[str] = []
            for row in rows:
                state = json.loads(row["state_json"])
                if (
                    state.get("source") == "draft"
                    and state.get("source_id") == draft_id
                    and state.get("validation_fingerprint") == draft.report.fingerprint
                    and state.get("status") == "completed"
                ):
                    matching_run_ids.append(cast(str, row["run_id"]))
            preview_runs = len(matching_run_ids)
            if matching_run_ids:
                placeholders = ",".join("?" for _item in matching_run_ids)
                query = (
                    "SELECT COUNT(*) AS count FROM scenario_preview_events "
                    f"WHERE run_id IN ({placeholders})"
                )
                count_row = self._connection.execute(query, matching_run_ids).fetchone()
                preview_events = cast(int, count_row["count"])
        mastery_rows = self._connection.execute(
            "SELECT module_id, kind FROM learning_evidence "
            "WHERE passed = 1 AND scenario_id IN (?, ?)",
            (scenario_id, draft.source_scenario_id),
        ).fetchall()
        mastery_evidence_count = len(
            {(cast(str, row["module_id"]), cast(str, row["kind"])) for row in mastery_rows}
        )
        write_tools = [
            cast(str, tool.get("name"))
            for tool in tools
            if tool.get("risk") in {"write", "destructive"}
        ]
        content = [
            (
                "Agent necessity",
                f"{draft.bundle.manifest['short_description']} The agent boundary is justified for "
                f"the `{draft.bundle.manifest['domain']}` domain and teaches "
                f"{', '.join(cast(list[str], draft.bundle.manifest['concepts']))}.",
            ),
            (
                "User journey",
                f"The primary learning journey contains {len(lessons)} lesson(s). It begins with "
                f"**{lessons[0]['title']}** and the objective: {lessons[0]['objective']}",
            ),
            (
                "State graph",
                f"Template `{draft.bundle.graph['template']}` contains "
                f"{len(graph_nodes)} typed nodes and {len(graph_edges)} declared edges. "
                f"Entry `{draft.bundle.graph['entry_node']}` "
                f"must reach {', '.join(cast(list[str], draft.bundle.graph['terminal_nodes']))}.",
            ),
            (
                "Tool catalog",
                f"{len(tools)} typed tool contract(s): "
                + ", ".join(f"`{tool['name']}` ({tool.get('risk', 'read')})" for tool in tools)
                + ". Public contracts remain repository-owned.",
            ),
            (
                "Schemas",
                f"All 8 validation sections passed with fingerprint `{draft.report.fingerprint}`. "
                f"The manifest uses schema version `{draft.bundle.manifest['schema_version']}`.",
            ),
            (
                "Evidence policy",
                f"Classification is `{draft.bundle.manifest['data_classification']}`. "
                f"The Kit packages {len(draft.bundle.data)} JSON fixture(s) and "
                f"{len(draft.bundle.resources)} bounded "
                "local evidence resource(s); citations must resolve inside the Kit.",
            ),
            (
                "Approval and safety",
                f"{len(rules)} policy rule(s) and {len(faults)} deterministic "
                "fault case(s) are declared. Write-risk tools requiring explicit approval: "
                f"{', '.join(write_tools) if write_tools else 'none'}. "
                "Builder writes are approved, idempotent and audited.",
            ),
            (
                "Evaluation set",
                f"{len(draft.bundle.evals)} Golden case(s) contain "
                f"{assertion_count} machine assertion(s). "
                "The same validation fingerprint gates preview and export.",
            ),
            (
                "Rollout",
                f"Compatibility targets core `{draft.bundle.manifest['compatibility']['core']}` "
                f"and event schema `{draft.bundle.manifest['compatibility']['event_schema']}`. "
                "Keep Mock mode local, "
                "deterministic and provider-independent before enabling any provider adapter.",
            ),
            (
                "Architecture defense",
                f"Durable Visual Lab evidence records {preview_runs} completed preview run(s) "
                f"and {preview_events} canonical event(s); the learning map records "
                f"{mastery_evidence_count} attributable passing mastery artifact(s). "
                "UI projections consume "
                "repository AgentEvent envelopes, never "
                "framework-private objects.",
            ),
        ]
        markdown = "\n".join(
            [
                f"# {draft.bundle.manifest['name']} — Architecture Review",
                "",
                f"- Scenario: `{scenario_id}`",
                "- Validation: PASS",
                f"- Fingerprint: `{draft.report.fingerprint}`",
                f"- Mastery evidence: {mastery_evidence_count}",
                "",
            ]
            + [f"## {index}. {title}\n\n{body}" for index, (title, body) in enumerate(content, 1)]
        )
        return CapstoneReport(
            title=f"{draft.bundle.manifest['name']} architecture review",
            scenario_id=cast(Any, scenario_id),
            validation_fingerprint=draft.report.fingerprint,
            validation_passed=draft.report.valid,
            mastery_evidence_count=mastery_evidence_count,
            architecture_sections=sections,
            markdown=markdown,
        )
