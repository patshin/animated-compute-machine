import asyncio
import hashlib
import json
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

from pydantic import JsonValue

from agent_trace_api.models import (
    AgentEvent,
    LabAnswerPayload,
    LabRunSnapshot,
    LabRunState,
    NodeSnapshot,
    ResetRunRequest,
    StartRunRequest,
    StatePatchPayload,
    StepRunRequest,
)
from agent_trace_api.runtime import DataAnalystRuntime
from agent_trace_api.scenario import load_data_analyst_scenario
from agent_trace_api.store import CommandOutcome, SQLiteRunStore


class RunStateError(RuntimeError):
    pass


class EventHub:
    def __init__(self) -> None:
        self._conditions: defaultdict[str, asyncio.Condition] = defaultdict(asyncio.Condition)
        self._versions: defaultdict[str, int] = defaultdict(int)

    async def publish(self, run_id: str) -> None:
        condition = self._conditions[run_id]
        async with condition:
            self._versions[run_id] += 1
            condition.notify_all()

    def version(self, run_id: str) -> int:
        return self._versions[run_id]

    async def wait(self, run_id: str, *, after_version: int, timeout: float) -> None:
        condition = self._conditions[run_id]
        async with condition:
            try:
                await asyncio.wait_for(
                    condition.wait_for(lambda: self._versions[run_id] > after_version),
                    timeout=timeout,
                )
            except TimeoutError:
                return


class RunService:
    def __init__(self, store: SQLiteRunStore, *, observe_delay: float = 0.6) -> None:
        self.store = store
        self.runtime = DataAnalystRuntime()
        self.observe_delay = observe_delay
        self.hub = EventHub()
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    @staticmethod
    def _stable_id(prefix: str, material: str) -> str:
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]
        return f"{prefix}{digest}"

    @staticmethod
    def _timestamp(sequence: int) -> datetime:
        return datetime(2026, 1, 15, 9, 30, tzinfo=UTC) + timedelta(milliseconds=sequence * 80)

    def _initial_state(self, run_id: str, request: StartRunRequest) -> LabRunState:
        scenario = load_data_analyst_scenario()
        return LabRunState(
            run_id=run_id,
            scenario_id=scenario.scenario_id,
            case_id=scenario.case_id,
            status="idle",
            input=request.input,
            seed=request.seed,
            cursor=0,
            step_count=0,
            nodes=[NodeSnapshot(**node.model_dump()) for node in scenario.nodes],
            values={"question": request.input},
            last_sequence=0,
        )

    def _make_event(
        self,
        state: LabRunState,
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
            timestamp=self._timestamp(sequence),
            node_id=node_id,
            causation_id=causation_id,
            correlation_id=self._stable_id("corr_", state.run_id),
            payload=payload,
        )

    async def _append(self, state: LabRunState, event: AgentEvent) -> None:
        checkpoint = state.model_copy(deep=True)
        checkpoint.last_sequence = event.sequence
        self.store.append_event_and_save_run(event, checkpoint)
        state.last_sequence = event.sequence
        await self.hub.publish(state.run_id)

    async def start(self, request: StartRunRequest) -> LabRunSnapshot:
        run_id = self._stable_id(
            "run_",
            f"{request.idempotency_key}:{request.input}:{request.seed}:{request.scenario_id}:{request.case_id}",
        )
        async with self._locks[run_id]:
            state = self._initial_state(run_id, request)
            existing = self.store.create_run(state, idempotency_key=request.idempotency_key)
            if existing is not None:
                return self._resolve_command(existing, run_id)
            try:
                event = self._make_event(
                    state,
                    "run.started",
                    {
                        "approval": True,
                        "idempotency_key": request.idempotency_key,
                        "mode": "mock-offline",
                        "state": state.model_dump(mode="json"),
                    },
                )
                await self._append(state, event)
                result = self.snapshot(run_id)
            except Exception as error:
                self.store.fail_command(request.idempotency_key, self._safe_error(error))
                raise
            self.store.complete_command(request.idempotency_key, result)
            return result

    def snapshot(self, run_id: str) -> LabRunSnapshot:
        return self.store.get_snapshot(run_id)

    def _resolve_command(self, outcome: CommandOutcome, run_id: str) -> LabRunSnapshot:
        if outcome.status == "succeeded":
            if outcome.result_json is not None:
                return LabRunSnapshot.model_validate_json(outcome.result_json)
            return self.snapshot(run_id)
        if outcome.status == "failed":
            raise RunStateError(outcome.error_message or "command failed")
        raise RunStateError("command is already in progress")

    @staticmethod
    def _safe_error(error: Exception) -> str:
        if isinstance(error, RunStateError):
            return str(error)
        return f"runtime boundary failed: {type(error).__name__}"

    async def step(self, run_id: str, request: StepRunRequest) -> LabRunSnapshot:
        async with self._locks[run_id]:
            state = self.store.get_run(run_id)
            existing = self.store.begin_command(
                idempotency_key=request.idempotency_key,
                command="step",
                run_id=run_id,
            )
            if existing is not None:
                return self._resolve_command(existing, run_id)
            try:
                if state.status == "completed":
                    raise RunStateError("completed runs reject additional steps")
                if state.status == "error":
                    raise RunStateError("failed runs require Reset before additional steps")
                if state.status == "running":
                    raise RunStateError("run already has an active step")
                await self._advance_one(
                    state, pace=request.pace, command_id=request.idempotency_key
                )
                result = self.snapshot(run_id)
            except Exception as error:
                self.store.fail_command(request.idempotency_key, self._safe_error(error))
                raise
            self.store.complete_command(request.idempotency_key, result)
            return result

    async def run_to_completion(self, run_id: str, request: StepRunRequest) -> LabRunSnapshot:
        async with self._locks[run_id]:
            state = self.store.get_run(run_id)
            existing = self.store.begin_command(
                idempotency_key=request.idempotency_key,
                command="run",
                run_id=run_id,
            )
            if existing is not None:
                return self._resolve_command(existing, run_id)
            try:
                if state.status == "completed":
                    raise RunStateError("completed runs reject additional execution")
                if state.status == "error":
                    raise RunStateError("failed runs require Reset before additional execution")
                while state.cursor < len(state.nodes):
                    await self._advance_one(
                        state,
                        pace=request.pace,
                        command_id=request.idempotency_key,
                    )
                result = self.snapshot(run_id)
            except Exception as error:
                self.store.fail_command(request.idempotency_key, self._safe_error(error))
                raise
            self.store.complete_command(request.idempotency_key, result)
            return result

    async def _advance_one(self, state: LabRunState, *, pace: str, command_id: str) -> None:
        node = state.nodes[state.cursor]
        node.status = "running"
        state.current_node = node.id
        state.status = "running"
        started = self._make_event(
            state,
            "node.started",
            {"label": node.label, "step": state.step_count + 1, "command_id": command_id},
            node_id=node.id,
        )
        await self._append(state, started)
        if pace == "observe" and self.observe_delay > 0:
            await asyncio.sleep(self.observe_delay)

        requested: AgentEvent | None = None
        if node.id == "query_metric":
            requested = self._make_event(
                state,
                "tool.requested",
                {
                    "tool": "query_metric",
                    "requested_permission": "read",
                    "granted_permissions": ["read"],
                    "timeout_ms": self.runtime.tool_timeout_ms,
                    "input_validation": "pending",
                },
                node_id=node.id,
                causation_id=started.event_id,
            )
            await self._append(state, requested)

        try:
            patch = await self.runtime.execute_node(node.id, state)
        except Exception as error:
            if requested is not None:
                tool_failed = self._make_event(
                    state,
                    "tool.failed",
                    {
                        "tool": "query_metric",
                        "error_code": self._error_code(error),
                        "error_type": type(error).__name__,
                        "retryable": isinstance(error, TimeoutError),
                    },
                    node_id=node.id,
                    causation_id=requested.event_id,
                )
                await self._append(state, tool_failed)
            node.status = "error"
            state.current_node = None
            state.status = "error"
            node_failed = self._make_event(
                state,
                "node.failed",
                {
                    "error_code": self._error_code(error),
                    "error_type": type(error).__name__,
                    "retryable": isinstance(error, TimeoutError),
                },
                node_id=node.id,
                causation_id=(requested or started).event_id,
            )
            await self._append(state, node_failed)
            raise RunStateError(
                f"node {node.id} failed at the typed runtime boundary: {type(error).__name__}"
            ) from error
        self._apply_patch(state, patch)
        patch_event = self._make_event(
            state,
            "state.patch",
            patch.model_dump(mode="json"),
            node_id=node.id,
            causation_id=started.event_id,
        )
        await self._append(state, patch_event)

        if node.id == "query_metric":
            result_event = self._make_event(
                state,
                "tool.completed",
                {
                    "tool": "query_metric",
                    "result_path": "values.metric_result",
                    "permission_decision": "granted",
                    "validated_input": True,
                    "timeout_ms": self.runtime.tool_timeout_ms,
                    "audit": "persisted",
                },
                node_id=node.id,
                causation_id=requested.event_id if requested is not None else started.event_id,
            )
            await self._append(state, result_event)

        node.status = "completed"
        completed = self._make_event(
            state,
            "node.completed",
            {"label": node.label},
            node_id=node.id,
            causation_id=started.event_id,
        )
        await self._append(state, completed)

        state.cursor += 1
        state.step_count += 1
        state.current_node = None
        is_terminal = state.cursor == len(state.nodes)
        state.status = "running" if is_terminal else "idle"
        step_completed = self._make_event(
            state,
            "step.completed",
            {"cursor": state.cursor, "step_count": state.step_count, "node_id": node.id},
            node_id=node.id,
            causation_id=started.event_id,
        )
        await self._append(state, step_completed)
        if is_terminal:
            state.status = "completed"
            run_completed = self._make_event(
                state,
                "run.completed",
                {"step_count": state.step_count, "answer_validated": state.answer is not None},
                causation_id=step_completed.event_id,
            )
            await self._append(state, run_completed)

    @staticmethod
    def _error_code(error: Exception) -> str:
        return "tool_timeout" if isinstance(error, TimeoutError) else "runtime_boundary_error"

    async def reset(self, run_id: str, request: ResetRunRequest) -> LabRunSnapshot:
        async with self._locks[run_id]:
            previous = self.store.get_run(run_id)
            existing = self.store.begin_command(
                idempotency_key=request.idempotency_key,
                command="reset",
                run_id=run_id,
            )
            if existing is not None:
                return self._resolve_command(existing, run_id)
            try:
                if previous.status == "running":
                    raise RunStateError("an active step cannot be reset")
                seed_request = StartRunRequest(
                    approved=True,
                    idempotency_key=self._stable_id("reset_", request.idempotency_key),
                    input=previous.input,
                    seed=previous.seed,
                )
                state = self._initial_state(run_id, seed_request)
                state.last_sequence = previous.last_sequence
                reset_event = self._make_event(
                    state,
                    "run.reset",
                    {
                        "approval": True,
                        "idempotency_key": request.idempotency_key,
                        "state": state.model_dump(mode="json"),
                    },
                )
                await self._append(state, reset_event)
                result = self.snapshot(run_id)
            except Exception as error:
                self.store.fail_command(request.idempotency_key, self._safe_error(error))
                raise
            self.store.complete_command(request.idempotency_key, result)
            return result

    @staticmethod
    def _apply_patch(state: LabRunState, patch: StatePatchPayload) -> None:
        for change in patch.changes:
            if change.path == "answer":
                state.answer = LabAnswerPayload.model_validate(change.after)
            elif change.path.startswith("values."):
                state.values[change.path.removeprefix("values.")] = change.after
            else:
                raise RunStateError(f"state patch path is not allowed: {change.path}")

    def replay(self, run_id: str) -> LabRunState:
        events = self.store.list_events(run_id)
        state: LabRunState | None = None
        for event in events:
            if event.type in {"run.started", "run.reset"}:
                state = LabRunState.model_validate(event.payload["state"])
            elif state is None:
                raise RunStateError("event stream does not begin with a state-bearing event")
            elif event.type == "node.started" and event.node_id:
                state.status = "running"
                state.current_node = event.node_id
                self._node(state, event.node_id).status = "running"
            elif event.type == "state.patch":
                self._apply_patch(state, StatePatchPayload.model_validate(event.payload))
            elif event.type == "node.completed" and event.node_id:
                self._node(state, event.node_id).status = "completed"
            elif event.type == "node.failed" and event.node_id:
                self._node(state, event.node_id).status = "error"
                state.current_node = None
                state.status = "error"
            elif event.type == "step.completed":
                cursor = event.payload["cursor"]
                step_count = event.payload["step_count"]
                if not isinstance(cursor, int) or not isinstance(step_count, int):
                    raise RunStateError("step.completed contains invalid counters")
                state.cursor = cursor
                state.step_count = step_count
                state.current_node = None
                state.status = "idle"
            elif event.type == "run.completed":
                state.status = "completed"
                state.current_node = None
            if state is not None:
                state.last_sequence = event.sequence
        if state is None:
            raise RunStateError("run has no replayable events")
        return state

    @staticmethod
    def _node(state: LabRunState, node_id: str) -> NodeSnapshot:
        node = next((item for item in state.nodes if item.id == node_id), None)
        if node is None:
            raise RunStateError(f"event references unknown node: {node_id}")
        return node

    async def stream_events(
        self, run_id: str, *, after_sequence: int, follow: bool
    ) -> AsyncIterator[str]:
        cursor = after_sequence
        while True:
            events = self.store.list_events(run_id, after_sequence=cursor)
            for event in events:
                cursor = event.sequence
                data = event.model_dump_json()
                yield f"id: {event.sequence}\nevent: agent-event\ndata: {data}\n\n"
            state = self.store.get_run(run_id)
            if not follow or state.status in {"completed", "error"}:
                break
            observed_version = self.hub.version(run_id)
            if self.store.list_events(run_id, after_sequence=cursor):
                continue
            state = self.store.get_run(run_id)
            if state.status in {"completed", "error"}:
                continue
            yield ": keep-alive\n\n"
            await self.hub.wait(run_id, after_version=observed_version, timeout=8.0)

    def replay_json(self, run_id: str) -> str:
        return json.dumps(self.replay(run_id).model_dump(mode="json"), sort_keys=True)
