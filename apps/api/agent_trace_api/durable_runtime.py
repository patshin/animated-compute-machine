import asyncio
import hashlib
import json
import queue
import sqlite3
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol, TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import JsonValue, ValidationError

from agent_trace_api.durable_models import (
    BranchEvidence,
    DraftArtifact,
    DurableAnswer,
    DurableCheckpoint,
    DurableCommandHandle,
    DurableEventsResponse,
    DurableNodeSnapshot,
    DurableReplayCommand,
    DurableReplayResponse,
    DurableRunCommand,
    DurableRunSnapshot,
    DurableRunState,
    ForkDurableRunRequest,
    OverrideDurableStateRequest,
    QualityResult,
    StartDurableReplayRequest,
    StartDurableRunRequest,
)
from agent_trace_api.durable_store import SQLiteDurableStore
from agent_trace_api.models import AgentEvent, StateChange, StatePatchPayload
from agent_trace_api.service import EventHub, RunStateError
from agent_trace_api.store import RunNotFoundError, StoreConflictError


class GraphCancelled(RuntimeError):
    pass


def merge_branch_results(
    left: dict[str, dict[str, str]], right: dict[str, dict[str, str]]
) -> dict[str, dict[str, str]]:
    """Commutative reducer: branch scheduling cannot change the persisted result."""
    merged = {**left, **right}
    return {key: merged[key] for key in sorted(merged)}


def merge_completed(left: list[str], right: list[str]) -> list[str]:
    return sorted(set(left) | set(right))


class DurableGraphState(TypedDict, total=False):
    run_id: str
    thread_id: str
    input: str
    seed: int
    quality_target: float
    brief_label: str
    normalized_input: str
    route: str
    branch_results: Annotated[dict[str, dict[str, str]], merge_branch_results]
    evidence_order: list[str]
    outline: list[str]
    draft: dict[str, JsonValue]
    quality: dict[str, JsonValue]
    answer: dict[str, JsonValue]
    retry_count: int
    completed_nodes: Annotated[list[str], merge_completed]


class GraphRuntime(Protocol):
    async def start(self, request: StartDurableRunRequest) -> DurableRunSnapshot: ...

    async def step(self, run_id: str, request: DurableRunCommand) -> DurableRunSnapshot: ...

    async def run(self, run_id: str, request: DurableRunCommand) -> DurableRunSnapshot: ...

    async def pause(self, run_id: str, request: DurableRunCommand) -> DurableRunSnapshot: ...

    async def resume(self, run_id: str, request: DurableRunCommand) -> DurableRunSnapshot: ...

    async def cancel(self, run_id: str, request: DurableRunCommand) -> DurableRunSnapshot: ...

    async def reset(self, run_id: str, request: DurableRunCommand) -> DurableRunSnapshot: ...

    def snapshot(self, run_id: str) -> DurableRunSnapshot: ...

    async def fork(self, run_id: str, request: ForkDurableRunRequest) -> DurableRunSnapshot: ...

    async def override(
        self, run_id: str, request: OverrideDurableStateRequest
    ) -> DurableRunSnapshot: ...

    def create_replay(
        self, run_id: str, request: StartDurableReplayRequest
    ) -> DurableReplayResponse: ...

    def control_replay(
        self, session_id: str, request: DurableReplayCommand
    ) -> DurableReplayResponse: ...

    def events(self, run_id: str, *, after_sequence: int = 0) -> DurableEventsResponse: ...

    def stream_events(
        self, run_id: str, *, after_sequence: int, follow: bool
    ) -> AsyncIterator[str]: ...

    def close(self) -> None: ...

    def request_shutdown(self) -> None: ...

    async def shutdown(self) -> None: ...

    def command_receipt(
        self, run_id: str, command: str, request: DurableRunCommand
    ) -> Literal["pending", "succeeded"] | None: ...


NodeKind = Literal["input", "router", "compute", "retrieval", "subgraph", "validation", "output"]
NodeLane = Literal["graph", "provider", "evidence", "policy", "output"]

NODE_DEFINITIONS: tuple[tuple[str, str, NodeKind, NodeLane, str | None], ...] = (
    ("intake", "Intake", "input", "graph", None),
    ("route", "Conditional route", "router", "graph", None),
    ("analytics", "Analytics evidence", "compute", "provider", None),
    ("research", "Research evidence", "retrieval", "evidence", None),
    ("synthesize", "Deterministic merge", "compute", "graph", None),
    ("draft_workbench", "Draft subgraph", "subgraph", "graph", None),
    ("draft_outline", "Outline", "compute", "graph", "draft_workbench"),
    ("draft_compose", "Compose", "compute", "provider", "draft_workbench"),
    ("quality", "Quality gate", "validation", "policy", None),
    ("refine", "Bounded refine", "compute", "provider", None),
    ("publish", "Typed answer", "output", "output", None),
)

NODE_OUTPUT_KEYS: dict[str, tuple[str, ...]] = {
    "intake": ("normalized_input",),
    "route": ("route",),
    "analytics": ("branch_results",),
    "research": ("branch_results",),
    "synthesize": ("evidence_order",),
    "draft_outline": ("outline",),
    "draft_compose": ("draft",),
    "quality": ("quality",),
    "refine": ("draft", "retry_count"),
    "publish": ("answer",),
}

RECOVERY_START_ORDER: tuple[str, ...] = tuple(item[0] for item in NODE_DEFINITIONS)
RECOVERY_COMPLETE_ORDER: tuple[str, ...] = (
    "intake",
    "route",
    "analytics",
    "research",
    "synthesize",
    "draft_outline",
    "draft_compose",
    "draft_workbench",
    "quality",
    "refine",
    "publish",
)


class LangGraphRuntimeAdapter:
    """Real LangGraph + SQLite implementation behind repository-owned contracts."""

    state_schema_version: Literal["1.0.0"] = "1.0.0"
    scenario_version: Literal["1.0.0"] = "1.0.0"
    provider_version: Literal["mock-1.0.0"] = "mock-1.0.0"

    def __init__(self, path: Path, *, node_delay: float = 0.015) -> None:
        self.store = SQLiteDurableStore(path)
        self._checkpointer_connection = sqlite3.connect(path, check_same_thread=False)
        self._checkpointer_connection.execute("PRAGMA journal_mode=WAL")
        self._checkpointer_connection.execute("PRAGMA busy_timeout=5000")
        self.serializer = JsonPlusSerializer(allowed_msgpack_modules=None)
        self.checkpointer = SqliteSaver(
            self._checkpointer_connection,
            serde=self.serializer,
        )
        self.checkpointer.setup()
        self.node_delay = node_delay
        self.hub = EventHub()
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._command_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._cancel: defaultdict[str, threading.Event] = defaultdict(threading.Event)
        self._pause_requested: defaultdict[str, bool] = defaultdict(bool)
        self._active_runs: set[str] = set()
        self._shutting_down = False
        self.owner_id = f"runtime_{uuid.uuid4().hex}"
        self.provider_execution_count = 0
        self.tool_execution_count = 0
        self._graph_lock = asyncio.Lock()
        self.graph = self._build_graph()
        self._replay_sessions: dict[str, DurableReplayResponse] = {}
        self._replay_commands: dict[str, tuple[str, DurableReplayResponse]] = {}
        self.store.fail_orphaned_commands(self.owner_id)
        self._recover_interrupted_runs()

    def close(self) -> None:
        if self._active_runs:
            raise RunStateError("cannot close SQLite while graph workers are active")
        self.store.close()
        self._checkpointer_connection.close()

    def request_shutdown(self) -> None:
        self._shutting_down = True
        for run_id in tuple(self._active_runs):
            self._cancel[run_id].set()

    async def shutdown(self) -> None:
        self.request_shutdown()
        for _ in range(2000):
            if not self._active_runs:
                self.close()
                return
            await asyncio.sleep(0.005)
        raise RunStateError("active LangGraph worker did not stop before SQLite shutdown")

    def _recover_interrupted_runs(self) -> None:
        """Reconcile a persisted graph successor or fence work from a dead process."""
        for state in self.store.list_runs():
            if state.status != "running":
                continue
            if self._reconcile_private_successor(state):
                continue
            state.status = "paused"
            state.current_node = None
            for node in state.nodes:
                if node.status == "running":
                    node.status = "pending"
            event = self._event(
                state,
                "run.interrupted",
                {
                    "reason": "runtime process reconstructed before a durable boundary",
                    "recovery": "paused-at-last-committed-checkpoint",
                },
            )
            state.last_sequence = event.sequence
            self.store.append_event(event, state)

    def _latest_private_successor(self, checkpoint: DurableCheckpoint) -> Any | None:
        """Return the latest private snapshot only when it descends from the public fence."""
        try:
            latest = self.graph.get_state(self._config(checkpoint.thread_id))
            if self._checkpoint_id(latest) == checkpoint.checkpoint_id:
                return None
            cursor = latest
            for _ in range(8):
                parent_config = cursor.parent_config
                if parent_config is None:
                    return None
                cursor = self.graph.get_state(parent_config)
                if self._checkpoint_id(cursor) == checkpoint.checkpoint_id:
                    return latest
        except Exception:
            return None
        return None

    def _reconcile_private_successor(self, state: DurableRunState) -> bool:
        """Project a completed private superstep without executing its side effects twice."""
        checkpoints = self.store.list_checkpoints(state.run_id)
        if not checkpoints or checkpoints[-1].thread_id != state.thread_id:
            return False
        public_checkpoint = checkpoints[-1]
        self._validated_graph_checkpoint(public_checkpoint)
        private = self._latest_private_successor(public_checkpoint)
        if private is None:
            return False
        values = cast(dict[str, Any], private.values)
        if values.get("run_id") != state.run_id or values.get("thread_id") != state.thread_id:
            return False
        before_completed = set(cast(list[str], state.values.get("completed_nodes", [])))
        after_completed = set(cast(list[str], values.get("completed_nodes", [])))
        source_nodes = tuple(
            node_id
            for node_id in RECOVERY_COMPLETE_ORDER
            if node_id in after_completed - before_completed
        )
        if not source_nodes:
            return False
        boundary_events: list[AgentEvent] = []
        for node_id in RECOVERY_START_ORDER:
            if node_id not in source_nodes:
                continue
            node = self._node(state, node_id)
            if node is None or node.status != "pending":
                continue
            node.status = "running"
            started = self._queue_event(
                state,
                "node.started",
                {
                    "label": node.label,
                    "adapter": "langgraph-recovery-projection-v1",
                    "superstep": state.step_count + 1,
                },
                node_id=node_id,
            )
            node.started_sequence = started.sequence
            boundary_events.append(started)
        for node_id in RECOVERY_COMPLETE_ORDER:
            if node_id not in source_nodes:
                continue
            node = self._node(state, node_id)
            if node is None or node.status == "completed":
                continue
            node.status = "completed"
            completed = self._queue_event(
                state,
                "node.completed",
                {
                    "label": node.label,
                    "adapter": "langgraph-recovery-projection-v1",
                    "completion_order": "durable-reconstruction",
                },
                node_id=node_id,
            )
            node.completed_sequence = completed.sequence
            boundary_events.append(completed)
        self._finish_superstep(
            state,
            before=dict(state.values),
            values=values,
            next_nodes=tuple(private.next),
            checkpoint_id=self._checkpoint_id(private),
            source_nodes=source_nodes,
            pending_events=boundary_events,
            publish_notifications=False,
        )
        return True

    @property
    def strict_serialization_active(self) -> bool:
        return getattr(self.serializer, "_allowed_msgpack_modules", object()) is None

    @staticmethod
    def _stable_id(prefix: str, material: str) -> str:
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
        return f"{prefix}{digest}"

    @staticmethod
    def _config(thread_id: str, checkpoint_id: str | None = None) -> RunnableConfig:
        configurable: dict[str, Any] = {"thread_id": thread_id, "checkpoint_ns": ""}
        if checkpoint_id is not None:
            configurable["checkpoint_id"] = checkpoint_id
        return RunnableConfig(configurable=configurable)

    def _wait(self, run_id: str, multiplier: float = 1) -> None:
        remaining = self.node_delay * multiplier
        while remaining > 0:
            if self._cancel[run_id].is_set():
                raise GraphCancelled("run cancellation reached the active graph node")
            interval = min(0.005, remaining)
            time.sleep(interval)
            remaining -= interval

    def _intake(self, state: DurableGraphState) -> DurableGraphState:
        self._wait(state["run_id"])
        return {
            "normalized_input": " ".join(state["input"].split()),
            "completed_nodes": ["intake"],
        }

    def _route(self, state: DurableGraphState) -> DurableGraphState:
        self._wait(state["run_id"])
        return {"route": "parallel-evidence", "completed_nodes": ["route"]}

    @staticmethod
    def _fan_out(_state: DurableGraphState) -> list[str]:
        return ["analytics", "research"]

    def _analytics(self, state: DurableGraphState) -> DurableGraphState:
        self.provider_execution_count += 1
        self._wait(state["run_id"], 2)
        evidence = BranchEvidence(
            branch_id="analytics",
            label="Durability signal",
            finding="Three checkpoint cycles retained a stable canonical event order.",
            source="mock-provider://durability-analytics/v1",
        )
        return {
            "branch_results": {"analytics": evidence.model_dump(mode="json")},
            "completed_nodes": ["analytics"],
        }

    def _research(self, state: DurableGraphState) -> DurableGraphState:
        self.provider_execution_count += 1
        self._wait(state["run_id"], 0.5)
        evidence = BranchEvidence(
            branch_id="research",
            label="Recovery signal",
            finding="SQLite reload preserved the thread cursor and typed state.",
            source="mock-provider://durability-research/v1",
        )
        return {
            "branch_results": {"research": evidence.model_dump(mode="json")},
            "completed_nodes": ["research"],
        }

    def _synthesize(self, state: DurableGraphState) -> DurableGraphState:
        self._wait(state["run_id"])
        order = sorted(state["branch_results"])
        return {"evidence_order": order, "completed_nodes": ["synthesize"]}

    def _outline(self, state: DurableGraphState) -> DurableGraphState:
        self._wait(state["run_id"])
        return {
            "outline": ["Durable execution", "Checkpoint recovery", "Canonical projection"],
            "completed_nodes": ["draft_outline"],
        }

    def _compose(self, state: DurableGraphState) -> DurableGraphState:
        self.provider_execution_count += 1
        self._wait(state["run_id"])
        revision = int(state.get("retry_count", 0))
        artifact = DraftArtifact(
            title=str(state.get("brief_label", "Durable runtime brief")),
            summary=(
                "Real LangGraph execution merged analytics and research evidence, then persisted "
                "the typed state to SQLite."
            ),
            evidence_refs=[f"branch:{item}" for item in state["evidence_order"]],
            revision=revision,
        )
        return {
            "draft": artifact.model_dump(mode="json"),
            "completed_nodes": ["draft_compose", "draft_workbench"],
        }

    def _quality(self, state: DurableGraphState) -> DurableGraphState:
        self._wait(state["run_id"])
        score = 0.84 if int(state.get("retry_count", 0)) == 0 else 0.96
        target = float(state.get("quality_target", 0.9))
        result = QualityResult(
            score=score,
            target=target,
            passed=score >= target,
            rationale=(
                "Initial draft needs one bounded refinement."
                if score < target
                else "Typed evidence and recovery claims satisfy the configured target."
            ),
        )
        return {"quality": result.model_dump(mode="json"), "completed_nodes": ["quality"]}

    @staticmethod
    def _quality_route(state: DurableGraphState) -> Literal["refine", "publish"]:
        quality = QualityResult.model_validate(state["quality"])
        if not quality.passed and int(state.get("retry_count", 0)) < 1:
            return "refine"
        return "publish"

    def _refine(self, state: DurableGraphState) -> DurableGraphState:
        self.provider_execution_count += 1
        self._wait(state["run_id"])
        draft = DraftArtifact.model_validate(state["draft"])
        refined = draft.model_copy(
            update={
                "summary": (
                    f"{draft.summary} Replay reads only stored canonical events; forks retain an "
                    "immutable parent checkpoint."
                ),
                "revision": 1,
            }
        )
        return {
            "draft": refined.model_dump(mode="json"),
            "retry_count": 1,
            "completed_nodes": ["refine"],
        }

    def _publish(self, state: DurableGraphState) -> DurableGraphState:
        self._wait(state["run_id"])
        draft = DraftArtifact.model_validate(state["draft"])
        quality = QualityResult.model_validate(state["quality"])
        answer = DurableAnswer(
            title=draft.title,
            summary=draft.summary,
            evidence_refs=draft.evidence_refs,
            quality_score=quality.score,
        )
        return {"answer": answer.model_dump(mode="json"), "completed_nodes": ["publish"]}

    def _build_graph(self) -> CompiledStateGraph[DurableGraphState, None, DurableGraphState, Any]:
        draft_builder = StateGraph(DurableGraphState)
        draft_builder.add_node("draft_outline", self._outline)
        draft_builder.add_node("draft_compose", self._compose)
        draft_builder.add_edge(START, "draft_outline")
        draft_builder.add_edge("draft_outline", "draft_compose")
        draft_builder.add_edge("draft_compose", END)
        draft_graph = draft_builder.compile()

        builder = StateGraph(DurableGraphState)
        builder.add_node("intake", self._intake)
        builder.add_node("route", self._route)
        builder.add_node("analytics", self._analytics)
        builder.add_node("research", self._research)
        builder.add_node("synthesize", self._synthesize)
        builder.add_node("draft_workbench", draft_graph)
        builder.add_node("quality", self._quality)
        builder.add_node("refine", self._refine)
        builder.add_node("publish", self._publish)
        builder.add_edge(START, "intake")
        builder.add_edge("intake", "route")
        builder.add_conditional_edges("route", self._fan_out)
        builder.add_edge("analytics", "synthesize")
        builder.add_edge("research", "synthesize")
        builder.add_edge("synthesize", "draft_workbench")
        builder.add_edge("draft_workbench", "quality")
        builder.add_conditional_edges("quality", self._quality_route)
        builder.add_edge("refine", "quality")
        builder.add_edge("publish", END)
        compiled = builder.compile(checkpointer=self.checkpointer, interrupt_after="*")
        return cast(CompiledStateGraph[DurableGraphState, None, DurableGraphState, Any], compiled)

    @staticmethod
    def _nodes() -> list[DurableNodeSnapshot]:
        return [
            DurableNodeSnapshot(id=node_id, label=label, kind=kind, lane=lane, parent_id=parent)
            for node_id, label, kind, lane, parent in NODE_DEFINITIONS
        ]

    def _initial_graph_state(
        self, run_id: str, thread_id: str, request: StartDurableRunRequest
    ) -> DurableGraphState:
        return {
            "run_id": run_id,
            "thread_id": thread_id,
            "input": request.input,
            "seed": request.seed,
            "quality_target": 0.9,
            "brief_label": "Durable runtime brief",
            "branch_results": {},
            "completed_nodes": [],
            "retry_count": 0,
        }

    @staticmethod
    def _public_values(values: dict[str, Any]) -> dict[str, JsonValue]:
        public_keys = (
            "quality_target",
            "brief_label",
            "normalized_input",
            "route",
            "branch_results",
            "evidence_order",
            "outline",
            "draft",
            "quality",
            "answer",
            "retry_count",
            "completed_nodes",
        )
        encoded = json.loads(json.dumps({key: values[key] for key in public_keys if key in values}))
        return cast(dict[str, JsonValue], encoded)

    def _initial_run_state(
        self, run_id: str, thread_id: str, request: StartDurableRunRequest
    ) -> DurableRunState:
        graph_state = self._initial_graph_state(run_id, thread_id, request)
        return DurableRunState(
            run_id=run_id,
            thread_id=thread_id,
            cycle=1,
            status="idle",
            input=request.input,
            seed=request.seed,
            step_count=0,
            next_nodes=["intake"],
            nodes=self._nodes(),
            values=self._public_values(cast(dict[str, Any], graph_state)),
            last_sequence=0,
            checkpoint_count=0,
        )

    def _event(
        self,
        state: DurableRunState,
        event_type: str,
        payload: dict[str, JsonValue],
        *,
        node_id: str | None = None,
        causation_id: str | None = None,
    ) -> AgentEvent:
        sequence = state.last_sequence + 1
        return AgentEvent(
            event_id=self._stable_id("evt_", f"{state.run_id}:{sequence}:{event_type}"),
            run_id=state.run_id,
            sequence=sequence,
            type=event_type,
            timestamp=datetime.now(UTC),
            node_id=node_id,
            causation_id=causation_id,
            correlation_id=self._stable_id("corr_", state.run_id),
            payload=payload,
        )

    async def _append(
        self,
        state: DurableRunState,
        event_type: str,
        payload: dict[str, JsonValue],
        *,
        node_id: str | None = None,
        causation_id: str | None = None,
    ) -> AgentEvent:
        event = self._event(state, event_type, payload, node_id=node_id, causation_id=causation_id)
        state.last_sequence = event.sequence
        self.store.append_event(event, state)
        await self.hub.publish(state.run_id)
        return event

    def _queue_event(
        self,
        state: DurableRunState,
        event_type: str,
        payload: dict[str, JsonValue],
        *,
        node_id: str | None = None,
        causation_id: str | None = None,
    ) -> AgentEvent:
        """Build a canonical event for a later atomic public projection."""
        event = self._event(
            state,
            event_type,
            payload,
            node_id=node_id,
            causation_id=causation_id,
        )
        state.last_sequence = event.sequence
        return event

    @staticmethod
    def _state_patch(
        before: dict[str, JsonValue],
        after: dict[str, JsonValue],
        source_nodes: tuple[str, ...],
    ) -> StatePatchPayload:
        changed_keys = [
            key for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)
        ]
        if not changed_keys or not source_nodes:
            raise RunStateError("a durable superstep must expose changed state and source nodes")
        changes: list[StateChange] = []
        for key in changed_keys:
            sources = [node for node in source_nodes if key in NODE_OUTPUT_KEYS.get(node, ())]
            if key == "completed_nodes":
                sources = list(source_nodes)
            if key == "branch_results" and len(sources) > 1:
                prior = before.get(key)
                staged = dict(prior) if isinstance(prior, dict) else {}
                final = after.get(key)
                if isinstance(final, dict):
                    for source in sources:
                        if source not in final:
                            continue
                        next_value = {**staged, source: final[source]}
                        changes.append(
                            StateChange(
                                path=f"values.{key}",
                                before=cast(JsonValue, staged),
                                after=cast(JsonValue, next_value),
                                source_node=source,
                            )
                        )
                        staged = next_value
                    if staged == final:
                        continue
            source = sources[0] if sources else source_nodes[0]
            changes.append(
                StateChange(
                    path=f"values.{key}",
                    before=before.get(key),
                    after=after.get(key),
                    source_node=source,
                )
            )
        return StatePatchPayload(
            changes=changes,
            provenance=("langgraph-checkpoint://typed-state/v1;sources=" + ",".join(source_nodes)),
        )

    def _finish_superstep(
        self,
        state: DurableRunState,
        *,
        before: dict[str, JsonValue],
        values: dict[str, Any],
        next_nodes: tuple[str, ...],
        checkpoint_id: str,
        source_nodes: tuple[str, ...],
        pending_events: list[AgentEvent] | None = None,
        publish_notifications: bool = True,
    ) -> list[AgentEvent]:
        """Commit canonical state, checkpoint and step events as one public boundary."""
        events = list(pending_events or [])
        completed_nodes = cast(list[str], values.get("completed_nodes", []))
        for node_id in completed_nodes:
            node = self._node(state, node_id)
            if node is not None and node.status == "pending":
                node.status = "completed"
        state.values = self._public_values(values)
        state.step_count += 1
        state.current_node = None
        state.next_nodes = list(next_nodes)
        state.status = "completed" if not next_nodes else "idle"
        patch = self._state_patch(before, state.values, source_nodes)
        patch_event = self._queue_event(
            state,
            "state.patch",
            cast(dict[str, JsonValue], patch.model_dump(mode="json")),
            node_id=source_nodes[0] if len(source_nodes) == 1 else None,
        )
        events.append(patch_event)
        checkpoint_sequence = state.last_sequence + 1
        checkpoint = DurableCheckpoint(
            checkpoint_id=checkpoint_id,
            run_id=state.run_id,
            thread_id=state.thread_id,
            sequence=checkpoint_sequence,
            step_count=state.step_count,
            reason="step",
            state_schema_version=self.state_schema_version,
            scenario_version=self.scenario_version,
            provider_version=self.provider_version,
            integrity_hash=self._integrity(values),
            next_nodes=list(next_nodes),
            created_at=datetime.now(UTC),
        )
        state.checkpoint_count += 1
        events.append(
            self._queue_event(
                state,
                "checkpoint.created",
                {
                    "checkpoint_id": checkpoint_id,
                    "integrity_hash": checkpoint.integrity_hash,
                    "state_schema_version": self.state_schema_version,
                    "scenario_version": self.scenario_version,
                    "provider_version": self.provider_version,
                },
            )
        )
        events.append(
            self._queue_event(
                state,
                "step.completed",
                {
                    "step_count": state.step_count,
                    "next_nodes": list(next_nodes),
                    "checkpoint_id": checkpoint_id,
                },
            )
        )
        if not next_nodes:
            events.append(
                self._queue_event(
                    state,
                    "run.completed",
                    {
                        "step_count": state.step_count,
                        "answer_schema": "agent-trace://durable-answer/1.0.0",
                    },
                )
            )
        elif self._pause_requested[state.run_id]:
            state.status = "paused"
            events.append(
                self._queue_event(
                    state,
                    "run.paused",
                    {"reason": "pause requested at a durable superstep boundary"},
                )
            )
        self.store.publish_boundary(state, events, checkpoint)
        if publish_notifications:
            # The caller owns the running event loop and sends one wake-up for the atomic batch.
            return events
        return events

    @staticmethod
    def _node(state: DurableRunState, node_id: str) -> DurableNodeSnapshot | None:
        return next((node for node in state.nodes if node.id == node_id), None)

    @staticmethod
    def _checkpoint_id(snapshot: Any) -> str:
        configurable = cast(dict[str, Any], snapshot.config["configurable"])
        checkpoint_id = configurable.get("checkpoint_id")
        if not isinstance(checkpoint_id, str):
            raise RunStateError("LangGraph did not return a durable checkpoint identifier")
        return checkpoint_id

    @staticmethod
    def _integrity(values: dict[str, Any]) -> str:
        material = json.dumps(values, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _validate_checkpoint_metadata(self, checkpoint: DurableCheckpoint) -> None:
        expected = (
            self.state_schema_version,
            self.scenario_version,
            self.provider_version,
        )
        actual = (
            checkpoint.state_schema_version,
            checkpoint.scenario_version,
            checkpoint.provider_version,
        )
        if actual != expected:
            raise RunStateError("checkpoint metadata is incompatible with this runtime")

    def _validated_graph_checkpoint(self, checkpoint: DurableCheckpoint) -> Any:
        """Load and authenticate the real LangGraph value projection before use."""
        self._validate_checkpoint_metadata(checkpoint)
        try:
            graph_snapshot = self.graph.get_state(
                self._config(checkpoint.thread_id, checkpoint.checkpoint_id)
            )
            values = cast(dict[str, Any], graph_snapshot.values)
        except Exception as error:
            raise RunStateError(
                "checkpoint payload is corrupt or cannot be deserialized"
            ) from error
        if self._integrity(values) != checkpoint.integrity_hash:
            raise RunStateError("checkpoint integrity verification failed")
        return graph_snapshot

    def snapshot(self, run_id: str) -> DurableRunSnapshot:
        try:
            result = self.store.snapshot(run_id)
        except ValidationError as error:
            raise RunStateError("checkpoint metadata is incompatible with this runtime") from error
        for checkpoint in result.checkpoints:
            self._validate_checkpoint_metadata(checkpoint)
        for event in result.events:
            if event.type == "state.patch":
                try:
                    StatePatchPayload.model_validate(event.payload)
                except ValidationError as error:
                    raise RunStateError(
                        "stored state.patch violates the canonical contract"
                    ) from error
        if result.checkpoints:
            self._validated_graph_checkpoint(result.checkpoints[-1])
        return result

    async def start(self, request: StartDurableRunRequest) -> DurableRunSnapshot:
        run_id = self._stable_id("run_", f"durable:{request.idempotency_key}")

        async def operation() -> DurableRunSnapshot:
            thread_id = self._stable_id("thread_", f"{run_id}:cycle:1")
            state = self._initial_run_state(run_id, thread_id, request)
            if not self.store.create_run(state):
                raise StoreConflictError(
                    "derived durable run already exists without a command result"
                )
            await self._append(
                state,
                "run.started",
                {
                    "approval": True,
                    "idempotency_key": request.idempotency_key,
                    "runtime": "langgraph",
                    "checkpointer": "sqlite",
                    "serialization": "strict-msgpack-builtins-only",
                    "state": state.model_dump(mode="json"),
                },
            )
            return self.snapshot(run_id)

        return await self._execute_command(request, "start", run_id, operation)

    @staticmethod
    def _request_hash(request: Any) -> str:
        material = json.dumps(
            request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    async def _execute_command(
        self,
        request: Any,
        command: str,
        run_id: str,
        operation: Callable[[], Awaitable[DurableRunSnapshot]],
    ) -> DurableRunSnapshot:
        """Serialize duplicate keys and claim persistence before side effects."""
        if self._shutting_down:
            raise RunStateError("runtime is shutting down and rejects new commands")
        async with self._command_locks[request.idempotency_key]:
            record = self.store.claim_command(
                request.idempotency_key,
                command,
                run_id,
                self._request_hash(request),
                self.owner_id,
            )
            if record is not None:
                if record.status == "succeeded" and record.result_json is not None:
                    return DurableRunSnapshot.model_validate_json(record.result_json)
                if record.status == "failed":
                    raise RunStateError(record.error_message or "prior command attempt failed")
                raise StoreConflictError("idempotent command is still pending")
            try:
                result = await operation()
            except Exception as error:
                self.store.fail_command(
                    request.idempotency_key,
                    str(error)[:500] or type(error).__name__,
                )
                raise
            self.store.complete_command(request.idempotency_key, result.model_dump_json())
            return result

    def command_receipt(
        self, run_id: str, command: str, request: DurableRunCommand
    ) -> Literal["pending", "succeeded"] | None:
        record = self.store.command_record(
            request.idempotency_key,
            command,
            run_id,
            self._request_hash(request),
        )
        if record is None:
            return None
        if record.status == "failed":
            raise RunStateError(record.error_message or "prior command attempt failed")
        return cast(Literal["pending", "succeeded"], record.status)

    def _verify_sequence(self, state: DurableRunState, expected_sequence: int) -> None:
        if state.last_sequence != expected_sequence:
            raise StoreConflictError(
                f"expected sequence {expected_sequence}, current sequence is {state.last_sequence}"
            )

    def _verify_interrupt_sequence(self, state: DurableRunState, expected_sequence: int) -> None:
        if state.run_id not in self._active_runs:
            self._verify_sequence(state, expected_sequence)
            return
        if expected_sequence > state.last_sequence:
            raise StoreConflictError(
                f"expected sequence {expected_sequence} is ahead of current sequence "
                f"{state.last_sequence}"
            )

    def _graph_boundary(
        self, state: DurableRunState
    ) -> tuple[DurableGraphState | None, RunnableConfig]:
        checkpoints = self.store.list_checkpoints(state.run_id)
        graph_input: DurableGraphState | None = None
        if not checkpoints or checkpoints[-1].thread_id != state.thread_id:
            graph_input = self._initial_graph_state(
                state.run_id,
                state.thread_id,
                StartDurableRunRequest(
                    approved=True,
                    idempotency_key=f"internal_{state.run_id}",
                    input=state.input,
                    seed=state.seed,
                ),
            )
            quality_target = state.values.get("quality_target", 0.9)
            if not isinstance(quality_target, int | float):
                raise RunStateError("quality_target must remain numeric")
            graph_input["quality_target"] = float(quality_target)
            graph_input["brief_label"] = str(
                state.values.get("brief_label", "Durable runtime brief")
            )
            return graph_input, self._config(state.thread_id)
        elif checkpoints[-1].thread_id == state.thread_id:
            self._validated_graph_checkpoint(checkpoints[-1])
            return None, self._config(state.thread_id, checkpoints[-1].checkpoint_id)
        raise RunStateError("durable graph boundary could not be resolved")

    def _stream_superstep_worker(
        self, state: DurableRunState, output: queue.Queue[tuple[str, Any]]
    ) -> None:
        graph_input, config = self._graph_boundary(state)
        try:
            for streamed in self.graph.stream(
                graph_input,
                config,
                stream_mode="tasks",
                version="v2",
                subgraphs=True,
            ):
                item = (
                    streamed[1] if isinstance(streamed, tuple) and len(streamed) == 2 else streamed
                )
                if isinstance(item, dict):
                    output.put(("event", cast(dict[str, Any], item)))
            output.put(("snapshot", self.graph.get_state(self._config(state.thread_id))))
        except BaseException as error:
            output.put(("error", error))
        finally:
            output.put(("done", None))

    @staticmethod
    def _task_data(item: dict[str, Any]) -> dict[str, Any] | None:
        if item.get("type") != "tasks" or not isinstance(item.get("data"), dict):
            return None
        return cast(dict[str, Any], item["data"])

    async def _project_task_event(self, state: DurableRunState, item: dict[str, Any]) -> str | None:
        data = self._task_data(item)
        if data is None or not isinstance(data.get("name"), str):
            return None
        node_id = str(data["name"])
        node = self._node(state, node_id)
        if node is None:
            return None
        if "result" not in data and "error" not in data:
            node.status = "running"
            state.current_node = node_id
            started = await self._append(
                state,
                "node.started",
                {
                    "label": node.label,
                    "adapter": "langgraph-public-stream-v2",
                    "superstep": state.step_count + 1,
                },
                node_id=node_id,
            )
            node.started_sequence = started.sequence
            return None
        elif data.get("error") is None:
            node.status = "completed"
            completed = await self._append(
                state,
                "node.completed",
                {
                    "label": node.label,
                    "adapter": "langgraph-public-stream-v2",
                    "completion_order": "observed",
                },
                node_id=node_id,
            )
            node.completed_sequence = completed.sequence
            return node_id
        return None

    async def _execute_superstep_streaming(
        self, state: DurableRunState
    ) -> tuple[dict[str, Any], tuple[str, ...], str, tuple[str, ...]]:
        output: queue.Queue[tuple[str, Any]] = queue.Queue()
        worker = asyncio.create_task(
            asyncio.to_thread(self._stream_superstep_worker, state, output)
        )
        graph_snapshot: Any | None = None
        worker_error: BaseException | None = None
        completed_sources: set[str] = set()
        while True:
            kind, payload = await asyncio.to_thread(output.get)
            if kind == "event":
                completed = await self._project_task_event(state, cast(dict[str, Any], payload))
                if completed is not None:
                    completed_sources.add(completed)
            elif kind == "snapshot":
                graph_snapshot = payload
            elif kind == "error":
                worker_error = cast(BaseException, payload)
            elif kind == "done":
                break
        await worker
        if worker_error is not None:
            raise worker_error
        if graph_snapshot is None:
            raise RunStateError("LangGraph superstep returned no durable snapshot")
        values = cast(dict[str, Any], graph_snapshot.values)
        return (
            values,
            tuple(graph_snapshot.next),
            self._checkpoint_id(graph_snapshot),
            tuple(sorted(completed_sources)),
        )

    async def _advance_one(self, state: DurableRunState) -> None:
        if not state.next_nodes:
            raise RunStateError("completed durable runs reject additional steps")
        checkpoints = self.store.list_checkpoints(state.run_id)
        if checkpoints and checkpoints[-1].thread_id == state.thread_id:
            self._validated_graph_checkpoint(checkpoints[-1])
        if self._cancel[state.run_id].is_set():
            await self._mark_cancelled(state, "cancelled before the next queued superstep")
            return
        state.status = "running"
        self.store.save_run(state)
        before = dict(state.values)
        try:
            async with self._graph_lock:
                (
                    values,
                    next_nodes,
                    checkpoint_id,
                    source_nodes,
                ) = await self._execute_superstep_streaming(state)
        except GraphCancelled:
            await self._mark_cancelled(state, "cancel propagated into the active graph node")
            return
        except Exception as error:
            state.status = "error"
            await self._append(
                state,
                "run.failed",
                {"error_type": type(error).__name__, "message": "graph boundary failed"},
            )
            raise RunStateError(f"LangGraph superstep failed: {type(error).__name__}") from error

        completed_nodes = cast(list[str], values.get("completed_nodes", []))
        if not completed_nodes:
            raise RunStateError("LangGraph superstep completed without a canonical source node")
        boundary_events = self._finish_superstep(
            state,
            before=before,
            values=values,
            next_nodes=next_nodes,
            checkpoint_id=checkpoint_id,
            source_nodes=source_nodes,
        )
        if boundary_events:
            await self.hub.publish(state.run_id)

    async def step(self, run_id: str, request: DurableRunCommand) -> DurableRunSnapshot:
        async def operation() -> DurableRunSnapshot:
            async with self._locks[run_id]:
                state = self.store.get_run(run_id)
                self._verify_sequence(state, request.expected_sequence)
                if run_id in self._active_runs:
                    raise RunStateError("run already has active execution")
                if state.status in {"completed", "cancelled", "error"}:
                    raise RunStateError(f"{state.status} runs reject additional steps")
                if state.status == "paused":
                    raise RunStateError("paused runs require Resume")
                self._active_runs.add(run_id)
                try:
                    await self._advance_one(state)
                finally:
                    self._active_runs.discard(run_id)
                return self.snapshot(run_id)

        return await self._execute_command(request, "step", run_id, operation)

    async def run(self, run_id: str, request: DurableRunCommand) -> DurableRunSnapshot:
        async def operation() -> DurableRunSnapshot:
            state = self.store.get_run(run_id)
            self._verify_sequence(state, request.expected_sequence)
            if state.status in {"completed", "cancelled", "error"}:
                raise RunStateError(f"{state.status} runs reject additional execution")
            if state.status == "paused":
                raise RunStateError("paused runs require Resume")
            if run_id in self._active_runs:
                raise RunStateError("run already has active execution")
            self._active_runs.add(run_id)
            try:
                while state.next_nodes and not self._pause_requested[run_id]:
                    async with self._locks[run_id]:
                        state = self.store.get_run(run_id)
                        if state.status in {"completed", "cancelled", "error", "paused"}:
                            break
                        await self._advance_one(state)
                    await asyncio.sleep(0)
                return self.snapshot(run_id)
            finally:
                self._active_runs.discard(run_id)

        return await self._execute_command(request, "run", run_id, operation)

    async def pause(self, run_id: str, request: DurableRunCommand) -> DurableRunSnapshot:
        async def operation() -> DurableRunSnapshot:
            state = self.store.get_run(run_id)
            self._verify_interrupt_sequence(state, request.expected_sequence)
            if state.status in {"completed", "cancelled", "error"}:
                raise RunStateError(f"{state.status} runs cannot pause")
            self._pause_requested[run_id] = True
            if state.status == "running":
                for _ in range(200):
                    await asyncio.sleep(0.005)
                    state = self.store.get_run(run_id)
                    if state.status != "running":
                        break
                if state.status == "running":
                    raise RunStateError("active graph node did not reach a durable pause boundary")
            else:
                state.status = "paused"
                await self._append(state, "run.paused", {"reason": "paused before next superstep"})
            return self.snapshot(run_id)

        return await self._execute_command(request, "pause", run_id, operation)

    async def resume(self, run_id: str, request: DurableRunCommand) -> DurableRunSnapshot:
        async def operation() -> DurableRunSnapshot:
            state = self.store.get_run(run_id)
            self._verify_sequence(state, request.expected_sequence)
            if state.status != "paused":
                raise RunStateError("only paused runs can resume")
            if run_id in self._active_runs:
                raise RunStateError("run already has active execution")
            self._pause_requested[run_id] = False
            state.status = "idle"
            await self._append(state, "run.resumed", {"checkpoint_count": state.checkpoint_count})
            self._active_runs.add(run_id)
            try:
                while state.next_nodes:
                    async with self._locks[run_id]:
                        state = self.store.get_run(run_id)
                        await self._advance_one(state)
                    if self._pause_requested[run_id] or state.status in {"cancelled", "error"}:
                        break
                    await asyncio.sleep(0)
                return self.snapshot(run_id)
            finally:
                self._active_runs.discard(run_id)

        return await self._execute_command(request, "resume", run_id, operation)

    async def _mark_cancelled(self, state: DurableRunState, reason: str) -> None:
        if state.status == "cancelled":
            return
        state.status = "cancelled"
        state.current_node = None
        for node in state.nodes:
            if node.status == "running":
                node.status = "cancelled"
        await self._append(
            state,
            "run.cancelled",
            {"reason": reason, "queued_work": "stopped", "durable": True},
        )

    async def cancel(self, run_id: str, request: DurableRunCommand) -> DurableRunSnapshot:
        async def operation() -> DurableRunSnapshot:
            state = self.store.get_run(run_id)
            self._verify_interrupt_sequence(state, request.expected_sequence)
            if state.status in {"completed", "cancelled", "error"}:
                raise RunStateError(f"{state.status} runs cannot cancel")
            self._cancel[run_id].set()
            if state.status == "running":
                for _ in range(200):
                    await asyncio.sleep(0.005)
                    state = self.store.get_run(run_id)
                    if state.status != "running":
                        break
                if state.status == "running":
                    raise RunStateError("active graph node did not acknowledge cancellation")
            else:
                await self._mark_cancelled(state, "explicit cancellation propagated")
            return self.snapshot(run_id)

        return await self._execute_command(request, "cancel", run_id, operation)

    async def reset(self, run_id: str, request: DurableRunCommand) -> DurableRunSnapshot:
        async def operation() -> DurableRunSnapshot:
            if run_id in self._active_runs:
                raise RunStateError("active execution must pause or cancel before reset")
            async with self._locks[run_id]:
                previous = self.store.get_run(run_id)
                self._verify_sequence(previous, request.expected_sequence)
                cycle = previous.cycle + 1
                thread_id = self._stable_id("thread_", f"{run_id}:cycle:{cycle}")
                start = StartDurableRunRequest(
                    approved=True,
                    idempotency_key=f"internal_reset_{run_id}_{cycle}",
                    input=previous.input,
                    seed=previous.seed,
                )
                reset = self._initial_run_state(run_id, thread_id, start)
                reset.cycle = cycle
                reset.last_sequence = previous.last_sequence
                reset.checkpoint_count = previous.checkpoint_count
                self.store.save_run(reset)
                self._cancel[run_id].clear()
                self._pause_requested[run_id] = False
                await self._append(
                    reset,
                    "run.reset",
                    {
                        "cycle": cycle,
                        "thread_id": thread_id,
                        "state": reset.model_dump(mode="json"),
                    },
                )
                return self.snapshot(run_id)

        return await self._execute_command(request, "reset", run_id, operation)

    async def fork(self, run_id: str, request: ForkDurableRunRequest) -> DurableRunSnapshot:
        new_run_id = self._stable_id(
            "run_", f"fork:{run_id}:{request.checkpoint_id}:{request.idempotency_key}"
        )

        async def operation() -> DurableRunSnapshot:
            if run_id in self._active_runs:
                raise RunStateError("active execution must pause before forking")
            async with self._locks[run_id]:
                parent = self.store.get_run(run_id)
                checkpoint = self.store.get_checkpoint(run_id, request.checkpoint_id)
                source_snapshot = self._validated_graph_checkpoint(checkpoint)
                source_tuple = self.checkpointer.get_tuple(
                    self._config(checkpoint.thread_id, checkpoint.checkpoint_id)
                )
                if source_tuple is None:
                    raise RunNotFoundError(
                        f"LangGraph checkpoint missing: {checkpoint.checkpoint_id}"
                    )
                child_thread = self._stable_id("thread_", f"{new_run_id}:cycle:1")
                projection_owned = False
                private_clone_created = False
                try:
                    clone_config = self.checkpointer.put(
                        self._config(child_thread),
                        source_tuple.checkpoint,
                        source_tuple.metadata,
                        source_tuple.checkpoint["channel_versions"],
                    )
                    private_clone_created = True
                    self.graph.update_state(
                        clone_config,
                        {
                            "run_id": new_run_id,
                            "thread_id": child_thread,
                            **request.state_override.changes(),
                        },
                    )
                    graph_snapshot = self.graph.get_state(self._config(child_thread))
                    values = cast(dict[str, Any], graph_snapshot.values)
                    if not cast(dict[str, Any], source_snapshot.values):
                        raise RunStateError("fork source checkpoint has no durable state")
                    child = DurableRunState(
                        run_id=new_run_id,
                        thread_id=child_thread,
                        parent_run_id=run_id,
                        forked_from_checkpoint_id=checkpoint.checkpoint_id,
                        cycle=1,
                        status="completed" if not graph_snapshot.next else "idle",
                        input=parent.input,
                        seed=parent.seed,
                        step_count=checkpoint.step_count,
                        current_node=None,
                        next_nodes=list(graph_snapshot.next),
                        nodes=self._nodes(),
                        values=self._public_values(values),
                        last_sequence=0,
                        checkpoint_count=0,
                    )
                    for node_id in cast(list[str], values.get("completed_nodes", [])):
                        node = self._node(child, node_id)
                        if node is not None:
                            node.status = "completed"
                    override_values = request.state_override.changes()
                    fork_event = self._event(
                        child,
                        "run.forked",
                        cast(
                            dict[str, JsonValue],
                            {
                                "parent_run_id": run_id,
                                "checkpoint_id": checkpoint.checkpoint_id,
                                "source_step_count": checkpoint.step_count,
                                "state_override": override_values,
                                "parent_immutable": True,
                                "state": child.model_dump(mode="json"),
                            },
                        ),
                    )
                    child.last_sequence = fork_event.sequence
                    child_checkpoint_id = self._checkpoint_id(graph_snapshot)
                    child_checkpoint = DurableCheckpoint(
                        checkpoint_id=child_checkpoint_id,
                        run_id=new_run_id,
                        thread_id=child.thread_id,
                        sequence=child.last_sequence + 1,
                        step_count=child.step_count,
                        reason="fork",
                        state_schema_version=self.state_schema_version,
                        scenario_version=self.scenario_version,
                        provider_version=self.provider_version,
                        integrity_hash=self._integrity(values),
                        next_nodes=child.next_nodes,
                        created_at=datetime.now(UTC),
                    )
                    child.checkpoint_count = 1
                    checkpoint_event = self._event(
                        child,
                        "checkpoint.created",
                        {
                            "checkpoint_id": child_checkpoint_id,
                            "reason": "fork",
                            "integrity_hash": child_checkpoint.integrity_hash,
                        },
                    )
                    child.last_sequence = checkpoint_event.sequence
                    self.store.publish_fork(child, [fork_event, checkpoint_event], child_checkpoint)
                    projection_owned = True
                    await self.hub.publish(new_run_id)
                    return self.snapshot(new_run_id)
                except BaseException:
                    if projection_owned:
                        self.store.delete_run_projection(new_run_id)
                    if private_clone_created:
                        self._delete_graph_thread(child_thread)
                    raise

        return await self._execute_command(request, "fork", new_run_id, operation)

    def _delete_graph_thread(self, thread_id: str) -> None:
        """Compensate only the private LangGraph rows created for a failed child."""
        with self._checkpointer_connection:
            self._checkpointer_connection.execute(
                "DELETE FROM writes WHERE thread_id = ?", (thread_id,)
            )
            self._checkpointer_connection.execute(
                "DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,)
            )

    def _graph_write_keys(self, thread_id: str) -> set[tuple[str, str, str, int]]:
        rows = self._checkpointer_connection.execute(
            "SELECT checkpoint_ns, checkpoint_id, task_id, idx FROM writes WHERE thread_id = ?",
            (thread_id,),
        ).fetchall()
        return {
            (str(ns), str(checkpoint), str(task), int(index))
            for ns, checkpoint, task, index in rows
        }

    def _delete_graph_checkpoint(
        self,
        thread_id: str,
        checkpoint_id: str,
        prior_write_keys: set[tuple[str, str, str, int]],
    ) -> None:
        """Compensate exactly one private checkpoint created by a failed override."""
        with self._checkpointer_connection:
            new_writes = self._graph_write_keys(thread_id) - prior_write_keys
            self._checkpointer_connection.executemany(
                "DELETE FROM writes WHERE thread_id = ? AND checkpoint_ns = ? "
                "AND checkpoint_id = ? AND task_id = ? AND idx = ?",
                [
                    (thread_id, namespace, parent_checkpoint, task_id, index)
                    for namespace, parent_checkpoint, task_id, index in new_writes
                ],
            )
            self._checkpointer_connection.execute(
                "DELETE FROM checkpoints WHERE thread_id = ? AND checkpoint_id = ?",
                (thread_id, checkpoint_id),
            )

    async def override(
        self, run_id: str, request: OverrideDurableStateRequest
    ) -> DurableRunSnapshot:
        async def operation() -> DurableRunSnapshot:
            if run_id in self._active_runs:
                raise RunStateError("active execution must pause before state override")
            async with self._locks[run_id]:
                state = self.store.get_run(run_id)
                self._verify_sequence(state, request.expected_sequence)
                checkpoints = self.store.list_checkpoints(run_id)
                if not checkpoints or checkpoints[-1].checkpoint_id != request.checkpoint_id:
                    raise RunStateError("state override requires the latest durable checkpoint")
                checkpoint = checkpoints[-1]
                if checkpoint.thread_id != state.thread_id:
                    raise RunStateError("state override cannot cross reset cycles")
                self._validated_graph_checkpoint(checkpoint)
                if state.status not in {"idle", "paused", "completed"}:
                    raise RunStateError("state override requires a durable boundary")
                existing = self.store.snapshot(run_id)
                private_checkpoint_id: str | None = None
                prior_write_keys = self._graph_write_keys(state.thread_id)
                published = False
                try:
                    config = self.graph.update_state(
                        self._config(checkpoint.thread_id, checkpoint.checkpoint_id),
                        cast(DurableGraphState, request.values.changes()),
                    )
                    graph_snapshot = self.graph.get_state(config)
                    private_checkpoint_id = self._checkpoint_id(graph_snapshot)
                    values = cast(dict[str, Any], graph_snapshot.values)
                    state.values = self._public_values(values)
                    state.next_nodes = list(graph_snapshot.next)
                    state.status = "completed" if not state.next_nodes else "paused"
                    override_event = self._queue_event(
                        state,
                        "state.override",
                        cast(
                            dict[str, JsonValue],
                            {
                                "checkpoint_id": request.checkpoint_id,
                                "changed_keys": sorted(request.values.changes()),
                                "mode": "sandbox",
                                "state": state.model_dump(mode="json"),
                            },
                        ),
                    )
                    durable_checkpoint = DurableCheckpoint(
                        checkpoint_id=private_checkpoint_id,
                        run_id=run_id,
                        thread_id=state.thread_id,
                        sequence=state.last_sequence + 1,
                        step_count=max(1, state.step_count),
                        reason="override",
                        state_schema_version=self.state_schema_version,
                        scenario_version=self.scenario_version,
                        provider_version=self.provider_version,
                        integrity_hash=self._integrity(values),
                        next_nodes=state.next_nodes,
                        created_at=datetime.now(UTC),
                    )
                    state.checkpoint_count += 1
                    checkpoint_event = self._queue_event(
                        state,
                        "checkpoint.created",
                        {
                            "checkpoint_id": private_checkpoint_id,
                            "reason": "override",
                            "integrity_hash": durable_checkpoint.integrity_hash,
                        },
                    )
                    result = DurableRunSnapshot(
                        run=state,
                        events=[*existing.events, override_event, checkpoint_event],
                        checkpoints=[*existing.checkpoints, durable_checkpoint],
                    )
                    self.store.publish_boundary(
                        state,
                        [override_event, checkpoint_event],
                        durable_checkpoint,
                    )
                    published = True
                except BaseException:
                    if private_checkpoint_id is not None and not published:
                        self._delete_graph_checkpoint(
                            state.thread_id,
                            private_checkpoint_id,
                            prior_write_keys,
                        )
                    raise
                await self.hub.publish(run_id)
                return result

        return await self._execute_command(request, "override", run_id, operation)

    @staticmethod
    def _replay_projection(source: DurableRunState, events: list[AgentEvent]) -> DurableRunState:
        projected = source.model_copy(deep=True)
        first_state: DurableRunState | None = None
        for event in events:
            raw_state = event.payload.get("state")
            if isinstance(raw_state, dict):
                try:
                    decoded = DurableRunState.model_validate(raw_state)
                except ValidationError:
                    decoded = None
                if decoded is not None:
                    projected = decoded.model_copy(deep=True)
                    if first_state is None:
                        first_state = decoded
            node = next((item for item in projected.nodes if item.id == event.node_id), None)
            if event.type == "node.started" and node is not None:
                node.status = "running"
                node.started_sequence = event.sequence
                projected.current_node = node.id
            elif event.type == "node.completed" and node is not None:
                node.status = "completed"
                node.completed_sequence = event.sequence
                projected.current_node = None
            elif event.type == "state.patch":
                try:
                    patch = StatePatchPayload.model_validate(event.payload)
                except ValidationError as error:
                    raise RunStateError(
                        "stored state.patch violates the canonical contract"
                    ) from error
                for change in patch.changes:
                    if change.path == "answer":
                        projected.values["answer"] = change.after
                    else:
                        projected.values[change.path.removeprefix("values.")] = change.after
            elif event.type == "step.completed":
                step_count = event.payload.get("step_count")
                next_nodes = event.payload.get("next_nodes")
                if isinstance(step_count, int):
                    projected.step_count = step_count
                if isinstance(next_nodes, list) and all(
                    isinstance(item, str) for item in next_nodes
                ):
                    projected.next_nodes = cast(list[str], next_nodes)
                projected.status = "completed" if not projected.next_nodes else "idle"
            elif event.type == "run.paused" or event.type == "run.interrupted":
                projected.status = "paused"
            elif event.type == "run.resumed":
                projected.status = "idle"
            elif event.type == "run.cancelled":
                projected.status = "cancelled"
            elif event.type == "run.failed":
                projected.status = "error"
            elif event.type == "run.completed":
                projected.status = "completed"
        if not events and first_state is None:
            projected.status = "idle"
            projected.step_count = 0
            projected.current_node = None
            projected.next_nodes = ["intake"]
            projected.nodes = LangGraphRuntimeAdapter._nodes()
            projected.values = {
                "quality_target": 0.9,
                "brief_label": "Durable runtime brief",
                "branch_results": {},
                "completed_nodes": [],
                "retry_count": 0,
            }
        projected.last_sequence = events[-1].sequence if events else 0
        projected.checkpoint_count = sum(event.type == "checkpoint.created" for event in events)
        return projected

    def _replay_response(
        self,
        session_id: str,
        source_run_id: str,
        cursor: int,
        speed: float,
        status: Literal["paused", "playing", "completed"],
    ) -> DurableReplayResponse:
        stored = self.store.list_events(source_run_id)
        visible = stored[:cursor]
        return DurableReplayResponse(
            session_id=session_id,
            source_run_id=source_run_id,
            status=status,
            cursor=len(visible),
            speed=speed,
            run=self._replay_projection(self.store.get_run(source_run_id), visible),
            events=visible,
        )

    def create_replay(
        self, run_id: str, request: StartDurableReplayRequest
    ) -> DurableReplayResponse:
        events = self.store.list_events(run_id)
        if not events:
            raise RunStateError("durable run has no stored events")
        session_id = self._stable_id("replay_", f"{run_id}:{request.idempotency_key}")
        existing = self._replay_sessions.get(session_id)
        if existing is not None:
            return existing
        cursor = len(events) if request.cursor is None else min(request.cursor, len(events))
        response = self._replay_response(
            session_id,
            run_id,
            cursor,
            1.0,
            "completed" if cursor == len(events) else "paused",
        )
        self._replay_sessions[session_id] = response
        return response

    def control_replay(
        self, session_id: str, request: DurableReplayCommand
    ) -> DurableReplayResponse:
        session = self._replay_sessions.get(session_id)
        if session is None:
            raise RunNotFoundError(session_id)
        command_key = f"{session_id}:{request.idempotency_key}"
        request_hash = self._request_hash(request)
        existing = self._replay_commands.get(command_key)
        if existing is not None:
            if existing[0] != request_hash:
                raise StoreConflictError(
                    "replay idempotency key is already bound to another control"
                )
            return existing[1]
        total = len(self.store.list_events(session.source_run_id))
        cursor = session.cursor
        speed = session.speed
        status: Literal["paused", "playing", "completed"] = session.status
        if request.action == "step":
            cursor = min(total, cursor + 1)
            status = "paused"
        elif request.action == "tick":
            if session.status != "playing":
                raise RunStateError("replay tick requires an active playing session")
            cursor = min(total, cursor + 1)
            status = "playing"
        elif request.action == "seek":
            cursor = min(total, request.cursor or 0)
            status = "paused"
        elif request.action == "checkpoint":
            checkpoint = self.store.get_checkpoint(
                session.source_run_id, request.checkpoint_id or ""
            )
            cursor = min(total, checkpoint.sequence)
            status = "paused"
        elif request.action == "reset":
            cursor = 0
            status = "paused"
        elif request.action == "speed":
            speed = request.speed or 1.0
        elif request.action == "play":
            status = "playing"
        elif request.action == "pause":
            status = "paused"
        if cursor == total and request.action != "pause":
            status = "completed"
        response = self._replay_response(
            session.session_id,
            session.source_run_id,
            cursor,
            speed,
            status,
        )
        self._replay_sessions[session_id] = response
        self._replay_commands[command_key] = (request_hash, response)
        return response

    def events(self, run_id: str, *, after_sequence: int = 0) -> DurableEventsResponse:
        return DurableEventsResponse(
            run_id=run_id,
            after_sequence=after_sequence,
            events=self.store.list_events(run_id, after_sequence=after_sequence),
        )

    async def stream_events(
        self, run_id: str, *, after_sequence: int, follow: bool
    ) -> AsyncIterator[str]:
        cursor = after_sequence
        while True:
            version = self.hub.version(run_id)
            events = self.store.list_events(run_id, after_sequence=cursor)
            for event in events:
                cursor = event.sequence
                yield (
                    f"id: {event.sequence}\nevent: agent_event\ndata: {event.model_dump_json()}\n\n"
                )
            if not follow:
                break
            state = self.store.get_run(run_id)
            if state.status in {"completed", "cancelled", "error"} and not events:
                break
            await self.hub.wait(run_id, after_version=version, timeout=15)
            if self.hub.version(run_id) == version:
                yield ": heartbeat\n\n"


class DurableRunService:
    """Application boundary delegates to GraphRuntime, never to LangGraph private APIs."""

    def __init__(self, runtime: GraphRuntime) -> None:
        self.runtime: GraphRuntime = runtime
        self._tasks: dict[str, asyncio.Future[DurableRunSnapshot]] = {}

    @staticmethod
    def _handle(
        command: Literal["start", "step", "run", "resume"],
        run_id: str,
        idempotency_key: str,
        sequence: int,
    ) -> DurableCommandHandle:
        command_id = LangGraphRuntimeAdapter._stable_id(
            "cmd_", f"{command}:{run_id}:{idempotency_key}"
        )
        return DurableCommandHandle(
            command_id=command_id,
            command=command,
            run_id=run_id,
            accepted_sequence=sequence,
            snapshot_url=f"/api/v1/runs/{run_id}",
            events_url=f"/api/v1/runs/{run_id}/events/stream",
        )

    def _schedule(
        self,
        handle: DurableCommandHandle,
        operation: Awaitable[DurableRunSnapshot],
    ) -> None:
        if handle.command_id in self._tasks:
            return
        task = asyncio.ensure_future(operation)
        self._tasks[handle.command_id] = task

        def finish(completed: asyncio.Future[DurableRunSnapshot]) -> None:
            self._tasks.pop(handle.command_id, None)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(finish)

    def _validate_submission(
        self,
        run_id: str,
        request: DurableRunCommand,
        command: Literal["step", "run", "resume"],
    ) -> DurableRunSnapshot:
        snapshot = self.runtime.snapshot(run_id)
        if snapshot.run.last_sequence != request.expected_sequence:
            raise StoreConflictError(
                f"expected sequence {request.expected_sequence}, current sequence is "
                f"{snapshot.run.last_sequence}"
            )
        if command == "resume" and snapshot.run.status != "paused":
            raise RunStateError("only paused runs can resume")
        if command in {"step", "run"} and snapshot.run.status != "idle":
            raise RunStateError(f"{snapshot.run.status} runs reject {command}")
        return snapshot

    async def accept_start(self, request: StartDurableRunRequest) -> DurableCommandHandle:
        snapshot = await self.runtime.start(request)
        return self._handle(
            "start",
            snapshot.run.run_id,
            request.idempotency_key,
            snapshot.run.last_sequence,
        )

    def accept_execution(
        self,
        run_id: str,
        request: DurableRunCommand,
        command: Literal["step", "run", "resume"],
    ) -> DurableCommandHandle:
        handle = self._handle(command, run_id, request.idempotency_key, request.expected_sequence)
        if self.runtime.command_receipt(run_id, command, request) is not None:
            return handle
        snapshot = self._validate_submission(run_id, request, command)
        handle = self._handle(command, run_id, request.idempotency_key, snapshot.run.last_sequence)
        if handle.command_id not in self._tasks:
            self._schedule(handle, getattr(self.runtime, command)(run_id, request))
        return handle

    async def start(self, request: StartDurableRunRequest) -> DurableRunSnapshot:
        return await self.runtime.start(request)

    async def step(self, run_id: str, request: DurableRunCommand) -> DurableRunSnapshot:
        return await self.runtime.step(run_id, request)

    async def run(self, run_id: str, request: DurableRunCommand) -> DurableRunSnapshot:
        return await self.runtime.run(run_id, request)

    async def pause(self, run_id: str, request: DurableRunCommand) -> DurableRunSnapshot:
        return await self.runtime.pause(run_id, request)

    async def resume(self, run_id: str, request: DurableRunCommand) -> DurableRunSnapshot:
        return await self.runtime.resume(run_id, request)

    async def cancel(self, run_id: str, request: DurableRunCommand) -> DurableRunSnapshot:
        return await self.runtime.cancel(run_id, request)

    async def reset(self, run_id: str, request: DurableRunCommand) -> DurableRunSnapshot:
        return await self.runtime.reset(run_id, request)

    def snapshot(self, run_id: str) -> DurableRunSnapshot:
        return self.runtime.snapshot(run_id)

    async def fork(self, run_id: str, request: ForkDurableRunRequest) -> DurableRunSnapshot:
        return await self.runtime.fork(run_id, request)

    async def override(
        self, run_id: str, request: OverrideDurableStateRequest
    ) -> DurableRunSnapshot:
        return await self.runtime.override(run_id, request)

    def create_replay(
        self, run_id: str, request: StartDurableReplayRequest
    ) -> DurableReplayResponse:
        return self.runtime.create_replay(run_id, request)

    def control_replay(
        self, session_id: str, request: DurableReplayCommand
    ) -> DurableReplayResponse:
        return self.runtime.control_replay(session_id, request)

    def events(self, run_id: str, after_sequence: int) -> DurableEventsResponse:
        return self.runtime.events(run_id, after_sequence=after_sequence)

    def stream_events(
        self, run_id: str, *, after_sequence: int, follow: bool
    ) -> AsyncIterator[str]:
        return self.runtime.stream_events(run_id, after_sequence=after_sequence, follow=follow)

    async def close(self) -> None:
        self.runtime.request_shutdown()
        tasks = list(self._tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.runtime.shutdown()
