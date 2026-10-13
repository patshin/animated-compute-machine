import hashlib
import json
import sqlite3
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, cast

from jsonschema import Draft202012Validator

from agent_trace_api.models import AgentEvent
from agent_trace_api.scenario_builder_models import (
    CreateScenarioPreviewRunRequest,
    ScenarioBundle,
    ScenarioPreviewNode,
    ScenarioPreviewRunCommand,
    ScenarioPreviewRunSnapshot,
    ScenarioPreviewRunState,
)
from agent_trace_api.scenario_builder_service import (
    ScenarioBuilderConflictError,
    ScenarioBuilderService,
    ScenarioNotFoundError,
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()


class ScenarioPreviewRuntime:
    """Durable declarative graph runner used by the E11 Visual Lab preview.

    It executes the validated graph topology and persists repository-owned state and
    canonical AgentEvent envelopes. Tool nodes perform a bounded contract dry-run;
    preview never claims to execute a Kit-specific side effect without a registered
    implementation.
    """

    def __init__(
        self,
        builder: ScenarioBuilderService,
        database_path: Path | str,
    ) -> None:
        self.builder = builder
        self._lock = RLock()
        self._connection = sqlite3.connect(database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS scenario_preview_runs (
              run_id TEXT PRIMARY KEY,
              bundle_json TEXT NOT NULL,
              state_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scenario_preview_events (
              run_id TEXT NOT NULL,
              sequence INTEGER NOT NULL,
              event_id TEXT NOT NULL UNIQUE,
              event_json TEXT NOT NULL,
              PRIMARY KEY (run_id, sequence),
              FOREIGN KEY (run_id) REFERENCES scenario_preview_runs(run_id)
            );
            CREATE TABLE IF NOT EXISTS scenario_preview_commands (
              idempotency_key TEXT PRIMARY KEY,
              fingerprint TEXT NOT NULL,
              action TEXT NOT NULL,
              result_json TEXT NOT NULL
            );
            """
        )
        self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _existing_command(
        self, key: str, fingerprint: str, action: str
    ) -> ScenarioPreviewRunSnapshot | None:
        row = self._connection.execute(
            "SELECT fingerprint, action, result_json FROM scenario_preview_commands "
            "WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        if row["fingerprint"] != fingerprint or row["action"] != action:
            raise ScenarioBuilderConflictError(
                "idempotency key is bound to a different preview runtime command"
            )
        return ScenarioPreviewRunSnapshot.model_validate_json(row["result_json"])

    def _record_command(
        self,
        key: str,
        fingerprint: str,
        action: str,
        result: ScenarioPreviewRunSnapshot,
    ) -> None:
        self._connection.execute(
            "INSERT INTO scenario_preview_commands "
            "(idempotency_key, fingerprint, action, result_json) VALUES (?, ?, ?, ?)",
            (key, fingerprint, action, result.model_dump_json()),
        )

    @staticmethod
    def _owner(node: dict[str, Any]) -> str:
        owner = node.get("owner")
        if owner in {"model", "code", "tool", "human"}:
            return cast(str, owner)
        kind = node.get("kind")
        if kind == "tool":
            return "tool"
        if kind == "human_interrupt":
            return "human"
        if kind in {"model_decision", "output"}:
            return "model"
        return "code"

    @classmethod
    def _initial_state(
        cls,
        run_id: str,
        request: CreateScenarioPreviewRunRequest,
        bundle: ScenarioBundle,
        fingerprint: str,
    ) -> ScenarioPreviewRunState:
        graph_nodes = cast(list[dict[str, Any]], bundle.graph["nodes"])
        entry = cast(str, bundle.graph.get("entry_node") or graph_nodes[0]["id"])
        return ScenarioPreviewRunState(
            run_id=run_id,
            source=request.source,
            source_id=request.source_id,
            scenario_id=cast(Any, bundle.manifest["id"]),
            status="idle",
            input=request.input,
            cursor=0,
            step_count=0,
            current_node=None,
            next_node=entry,
            nodes=[
                ScenarioPreviewNode(
                    id=cast(str, node["id"]),
                    label=cast(str, node["label"]),
                    kind=cast(Any, node["kind"]),
                    owner=cast(Any, cls._owner(node)),
                    reads=cast(list[str], node.get("reads", [])),
                    writes=cast(list[str], node.get("writes", [])),
                )
                for node in graph_nodes
            ],
            values={"input": request.input},
            last_sequence=0,
            validation_fingerprint=fingerprint,
        )

    @staticmethod
    def _event(
        state: ScenarioPreviewRunState,
        event_type: str,
        payload: dict[str, Any],
        *,
        node_id: str | None = None,
        causation_id: str | None = None,
    ) -> AgentEvent:
        sequence = state.last_sequence + 1
        return AgentEvent(
            event_id=f"evt_{_digest(state.run_id, str(sequence), event_type)[:20]}",
            run_id=state.run_id,
            sequence=sequence,
            type=event_type,
            timestamp=datetime.now(UTC),
            node_id=node_id,
            causation_id=causation_id,
            correlation_id=f"corr_{_digest(state.run_id)[:20]}",
            payload=payload,
        )

    def _append(self, state: ScenarioPreviewRunState, event: AgentEvent) -> None:
        if event.sequence != state.last_sequence + 1:
            raise ScenarioBuilderConflictError("preview events must remain monotonic")
        state.last_sequence = event.sequence
        self._connection.execute(
            "INSERT INTO scenario_preview_events "
            "(run_id, sequence, event_id, event_json) VALUES (?, ?, ?, ?)",
            (state.run_id, event.sequence, event.event_id, event.model_dump_json()),
        )
        cursor = self._connection.execute(
            "UPDATE scenario_preview_runs SET state_json = ? WHERE run_id = ?",
            (state.model_dump_json(), state.run_id),
        )
        if cursor.rowcount != 1:
            raise ScenarioNotFoundError(state.run_id)

    def _load(self, run_id: str) -> tuple[ScenarioPreviewRunState, ScenarioBundle]:
        row = self._connection.execute(
            "SELECT state_json, bundle_json FROM scenario_preview_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise ScenarioNotFoundError(run_id)
        return (
            ScenarioPreviewRunState.model_validate_json(row["state_json"]),
            ScenarioBundle.model_validate_json(row["bundle_json"]),
        )

    def snapshot(self, run_id: str) -> ScenarioPreviewRunSnapshot:
        with self._lock:
            state, _bundle = self._load(run_id)
            rows = self._connection.execute(
                "SELECT event_json FROM scenario_preview_events WHERE run_id = ? ORDER BY sequence",
                (run_id,),
            ).fetchall()
            return ScenarioPreviewRunSnapshot(
                run=state,
                events=[AgentEvent.model_validate_json(row["event_json"]) for row in rows],
            )

    def start(self, request: CreateScenarioPreviewRunRequest) -> ScenarioPreviewRunSnapshot:
        bundle = self.builder.preview_bundle(request.source, request.source_id)
        report = self.builder.validate_bundle(bundle)
        if not report.valid:
            raise ScenarioBuilderConflictError(
                "invalid Scenario Kits cannot enter the Visual Lab runtime"
            )
        fingerprint = _digest(_canonical(request.model_dump(mode="json")), report.fingerprint)
        run_id = f"run_preview_{_digest(request.idempotency_key, report.fingerprint)[:20]}"
        with self._lock, self._connection:
            existing = self._existing_command(request.idempotency_key, fingerprint, "start")
            if existing is not None:
                return existing
            state = self._initial_state(run_id, request, bundle, report.fingerprint)
            self._connection.execute(
                "INSERT INTO scenario_preview_runs (run_id, bundle_json, state_json) "
                "VALUES (?, ?, ?)",
                (run_id, bundle.model_dump_json(), state.model_dump_json()),
            )
            started = self._event(
                state,
                "run.started",
                {
                    "mode": "deterministic-declarative-preview",
                    "scenario_id": state.scenario_id,
                    "source": state.source,
                    "source_id": state.source_id,
                    "validation_fingerprint": report.fingerprint,
                    "state": state.model_dump(mode="json"),
                },
            )
            self._append(state, started)
            result = self.snapshot(run_id)
            self._record_command(request.idempotency_key, fingerprint, "start", result)
            return result

    @staticmethod
    def _next_node(
        bundle: ScenarioBundle, node_id: str
    ) -> tuple[str | None, dict[str, Any] | None]:
        terminal = set(cast(list[str], bundle.graph.get("terminal_nodes", [])))
        if node_id in terminal:
            return None, None
        outgoing = [
            edge
            for edge in cast(list[dict[str, Any]], bundle.graph.get("edges", []))
            if edge.get("from") == node_id
        ]
        if not outgoing:
            return None, None
        indexed = list(enumerate(outgoing))
        indexed.sort(key=lambda pair: (cast(int, pair[1].get("priority", 1_000_000)), pair[0]))
        selected = indexed[0][1]
        return cast(str, selected["to"]), selected

    @staticmethod
    def _tool_contract(bundle: ScenarioBundle) -> dict[str, Any]:
        tools = cast(list[dict[str, Any]], bundle.tools.get("tools", []))
        if not tools:
            raise ScenarioBuilderConflictError("tool node has no declared tool contract")
        tool = deepcopy(tools[0])
        for key in ("input_schema", "output_schema"):
            schema = tool.get(key)
            if not isinstance(schema, dict):
                raise ScenarioBuilderConflictError(f"tool {key} must be an object")
            Draft202012Validator.check_schema(schema)
        return tool

    def _advance_one(
        self,
        state: ScenarioPreviewRunState,
        bundle: ScenarioBundle,
        *,
        command_id: str,
    ) -> None:
        if state.status == "completed":
            raise ScenarioBuilderConflictError("completed preview runs reject additional steps")
        if state.status == "error":
            raise ScenarioBuilderConflictError("failed preview runs require Reset")
        if state.next_node is None:
            raise ScenarioBuilderConflictError("preview graph has no runnable next node")
        node = next((item for item in state.nodes if item.id == state.next_node), None)
        if node is None:
            raise ScenarioBuilderConflictError("preview graph routed to an unknown node")
        if node.status == "completed":
            raise ScenarioBuilderConflictError("preview graph contains a runtime cycle")

        node.status = "running"
        state.status = "running"
        state.current_node = node.id
        started = self._event(
            state,
            "node.started",
            {
                "label": node.label,
                "kind": node.kind,
                "owner": node.owner,
                "command_id": command_id,
            },
            node_id=node.id,
        )
        self._append(state, started)

        tool_event: AgentEvent | None = None
        if node.kind == "tool":
            tool = self._tool_contract(bundle)
            tool_event = self._event(
                state,
                "tool.requested",
                {
                    "tool": tool["name"],
                    "mode": "contract-dry-run",
                    "risk": tool.get("risk", "read"),
                    "side_effect_executed": False,
                },
                node_id=node.id,
                causation_id=started.event_id,
            )
            self._append(state, tool_event)

        changes: list[dict[str, Any]] = []
        for key in node.writes:
            before = deepcopy(state.values.get(key))
            after = {
                "node": node.id,
                "kind": node.kind,
                "owner": node.owner,
                "reads": node.reads,
                "step": state.step_count + 1,
                "source": "deterministic-declarative-preview",
            }
            state.values[key] = after
            changes.append(
                {
                    "path": f"values.{key}",
                    "before": before,
                    "after": after,
                    "source_node": node.id,
                }
            )
        if not changes:
            key = f"node_{node.id}"
            after = {"completed": True, "step": state.step_count + 1}
            state.values[key] = after
            changes.append(
                {
                    "path": f"values.{key}",
                    "before": None,
                    "after": after,
                    "source_node": node.id,
                }
            )
        patched = self._event(
            state,
            "state.patch",
            {
                "changes": changes,
                "provenance": (
                    "Validated Scenario Kit graph executed by the repository-local "
                    "deterministic preview runtime."
                ),
            },
            node_id=node.id,
            causation_id=started.event_id,
        )
        self._append(state, patched)

        if tool_event is not None:
            tool = self._tool_contract(bundle)
            completed_tool = self._event(
                state,
                "tool.completed",
                {
                    "tool": tool["name"],
                    "mode": "contract-dry-run",
                    "input_schema_valid": True,
                    "output_schema_valid": True,
                    "side_effect_executed": False,
                    "audit": "persisted",
                },
                node_id=node.id,
                causation_id=tool_event.event_id,
            )
            self._append(state, completed_tool)

        node.status = "completed"
        completed = self._event(
            state,
            "node.completed",
            {"label": node.label, "kind": node.kind},
            node_id=node.id,
            causation_id=started.event_id,
        )
        self._append(state, completed)

        next_node, edge = self._next_node(bundle, node.id)
        if edge is not None:
            selected = self._event(
                state,
                "edge.selected",
                {
                    "from": node.id,
                    "to": next_node,
                    "condition": edge.get("condition"),
                    "selection": "priority-then-declaration-order",
                },
                node_id=node.id,
                causation_id=completed.event_id,
            )
            self._append(state, selected)
        state.cursor += 1
        state.step_count += 1
        state.current_node = None
        state.next_node = next_node
        step_completed = self._event(
            state,
            "step.completed",
            {
                "cursor": state.cursor,
                "step_count": state.step_count,
                "node_id": node.id,
                "next_node": next_node,
            },
            node_id=node.id,
            causation_id=completed.event_id,
        )
        self._append(state, step_completed)
        if next_node is None:
            state.status = "completed"
            run_completed = self._event(
                state,
                "run.completed",
                {
                    "step_count": state.step_count,
                    "terminal_node": node.id,
                    "validation_fingerprint": state.validation_fingerprint,
                },
                causation_id=step_completed.event_id,
            )
            self._append(state, run_completed)
        else:
            state.status = "idle"
            self._connection.execute(
                "UPDATE scenario_preview_runs SET state_json = ? WHERE run_id = ?",
                (state.model_dump_json(), state.run_id),
            )

    def _command(
        self,
        run_id: str,
        request: ScenarioPreviewRunCommand,
        action: str,
    ) -> ScenarioPreviewRunSnapshot:
        fingerprint = _digest(run_id, action, _canonical(request.model_dump(mode="json")))
        with self._lock, self._connection:
            existing = self._existing_command(request.idempotency_key, fingerprint, action)
            if existing is not None:
                return existing
            state, bundle = self._load(run_id)
            if action == "step":
                self._advance_one(state, bundle, command_id=request.idempotency_key)
            elif action == "run":
                remaining = len(state.nodes) - state.step_count
                for _index in range(remaining):
                    if state.status == "completed":
                        break
                    self._advance_one(state, bundle, command_id=request.idempotency_key)
                if state.status != "completed":
                    raise ScenarioBuilderConflictError(
                        "preview graph did not reach a terminal node within its node boundary"
                    )
            elif action == "reset":
                fresh = self._initial_state(
                    state.run_id,
                    CreateScenarioPreviewRunRequest(
                        approval=request.approval,
                        idempotency_key=request.idempotency_key,
                        source=state.source,
                        source_id=state.source_id,
                        input=state.input,
                    ),
                    bundle,
                    state.validation_fingerprint,
                )
                fresh.last_sequence = state.last_sequence
                reset = self._event(
                    fresh,
                    "run.reset",
                    {
                        "state": fresh.model_dump(mode="json"),
                        "reason": "explicit Visual Lab Reset",
                    },
                )
                self._append(fresh, reset)
            else:
                raise ScenarioBuilderConflictError("unknown preview runtime command")
            result = self.snapshot(run_id)
            self._record_command(request.idempotency_key, fingerprint, action, result)
            return result

    def step(self, run_id: str, request: ScenarioPreviewRunCommand) -> ScenarioPreviewRunSnapshot:
        return self._command(run_id, request, "step")

    def run(self, run_id: str, request: ScenarioPreviewRunCommand) -> ScenarioPreviewRunSnapshot:
        return self._command(run_id, request, "run")

    def reset(self, run_id: str, request: ScenarioPreviewRunCommand) -> ScenarioPreviewRunSnapshot:
        return self._command(run_id, request, "reset")
