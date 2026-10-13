import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, JsonValue

from agent_trace_api.models import (
    CalculationResult,
    ChartPoint,
    ChartSeries,
    LabAnswerPayload,
    LabLineChartArtifact,
    LabRunState,
    MetricQueryInput,
    MetricQueryResult,
    MetricRow,
    NormalizedInput,
    RouteDecision,
    StateChange,
    StatePatchPayload,
    ValidationResult,
)


class RuntimeBoundaryError(RuntimeError):
    pass


ToolHandler = Callable[[BaseModel], Awaitable[BaseModel]]
ToolPermission = Literal["read", "write"]


@dataclass(frozen=True)
class ToolDefinition:
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: ToolHandler
    permission: ToolPermission


class ToolRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, ToolDefinition] = {}

    def register(
        self,
        name: str,
        input_model: type[BaseModel],
        output_model: type[BaseModel],
        handler: ToolHandler,
        *,
        permission: ToolPermission,
    ) -> None:
        if name in self._handlers:
            raise RuntimeBoundaryError(f"tool already registered: {name}")
        self._handlers[name] = ToolDefinition(
            input_model=input_model,
            output_model=output_model,
            handler=handler,
            permission=permission,
        )

    async def execute(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        granted_permissions: frozenset[ToolPermission],
        timeout_seconds: float = 1.0,
    ) -> BaseModel:
        if name not in self._handlers:
            raise RuntimeBoundaryError(f"tool is not registered: {name}")
        definition = self._handlers[name]
        if definition.permission not in granted_permissions:
            raise RuntimeBoundaryError(
                f"permission denied for tool {name}: requires {definition.permission}"
            )
        typed_input = definition.input_model.model_validate(payload)
        raw_result = await asyncio.wait_for(
            definition.handler(typed_input), timeout=timeout_seconds
        )
        return definition.output_model.model_validate(raw_result.model_dump(mode="json"))


async def query_fictional_revenue(payload: BaseModel) -> BaseModel:
    typed = MetricQueryInput.model_validate(payload.model_dump(mode="json"))
    fixture = {
        "2025-Q1": 18.4,
        "2025-Q2": 20.1,
        "2025-Q3": 21.3,
        "2025-Q4": 24.2,
    }
    return MetricQueryResult(
        rows=[MetricRow(period=period, revenue_musd=fixture[period]) for period in typed.periods],
        source="fictional://northstar/revenue-2025",
    )


class DeterministicMockProvider:
    """A typed, offline decision boundary. It never reaches a model or the network."""

    def __init__(self) -> None:
        self.call_count = 0

    def normalize(self, question: str) -> NormalizedInput:
        self.call_count += 1
        return NormalizedInput(
            question=" ".join(question.split()),
            metric="revenue",
            period="2025-Q1..2025-Q4",
        )

    def route(self, seed: int) -> RouteDecision:
        self.call_count += 1
        rationale_variant = seed % 2
        return RouteDecision(
            route="metric-analysis",
            rationale=(
                "The request asks for a bounded revenue comparison across quarters."
                if rationale_variant == 0
                else "A quarter-bounded revenue question should follow the metric analysis route."
            ),
        )

    def answer(self, state: LabRunState) -> LabAnswerPayload:
        self.call_count += 1
        rows = MetricQueryResult.model_validate(state.values["metric_result"]).rows
        calculation = CalculationResult.model_validate(state.values["calculation"])
        summary = (
            f"Fictional revenue rose from ${calculation.q1_revenue_musd:.1f}M in Q1 "
            f"to ${calculation.q4_revenue_musd:.1f}M in Q4 2025, "
            f"a {calculation.growth_percent:.1f}% increase."
        )
        return LabAnswerPayload(
            title="Northstar revenue gained momentum",
            summary=summary,
            bullets=[
                f"Absolute change: +${calculation.absolute_change_musd:.1f}M",
                f"Strongest quarter: {calculation.strongest_quarter}",
                "Validation passed for coverage, provenance and arithmetic.",
            ],
            chart=LabLineChartArtifact(
                schema_version="1.0.0",
                kind="line",
                title="Fictional quarterly revenue",
                x_label="Quarter",
                y_label="Revenue (USD millions)",
                source="fictional://northstar/revenue-2025",
                takeaway=summary,
                series=[
                    ChartSeries(
                        label="Revenue",
                        color="teal",
                        points=[ChartPoint(x=row.period, y=row.revenue_musd) for row in rows],
                    )
                ],
            ),
        )


class DataAnalystRuntime:
    def __init__(self, *, tool_timeout_seconds: float = 1.0) -> None:
        self.provider = DeterministicMockProvider()
        self.tool_timeout_seconds = tool_timeout_seconds
        self.tools = ToolRegistry()
        self.tools.register(
            "query_metric",
            MetricQueryInput,
            MetricQueryResult,
            query_fictional_revenue,
            permission="read",
        )
        self.tool_call_count = 0

    @property
    def tool_timeout_ms(self) -> int:
        return max(1, round(self.tool_timeout_seconds * 1000))

    async def execute_node(self, node_id: str, state: LabRunState) -> StatePatchPayload:
        before = state.model_copy(deep=True)
        if node_id == "normalize":
            result: BaseModel = self.provider.normalize(state.input)
            path = "values.normalized_input"
            provenance = "mock-provider://normalize/v1"
        elif node_id == "route":
            result = self.provider.route(state.seed)
            path = "values.route_decision"
            provenance = "mock-provider://route/v1"
        elif node_id == "query_metric":
            self.tool_call_count += 1
            result = await self.tools.execute(
                "query_metric",
                {
                    "metric": "revenue",
                    "periods": ["2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4"],
                },
                granted_permissions=frozenset({"read"}),
                timeout_seconds=self.tool_timeout_seconds,
            )
            path = "values.metric_result"
            provenance = "tool://query_metric@1.0.0 read-only"
        elif node_id == "calculate":
            rows = MetricQueryResult.model_validate(state.values["metric_result"]).rows
            q1 = rows[0].revenue_musd
            q4 = rows[-1].revenue_musd
            result = CalculationResult(
                q1_revenue_musd=q1,
                q4_revenue_musd=q4,
                absolute_change_musd=round(q4 - q1, 1),
                growth_percent=round((q4 - q1) / q1 * 100, 1),
                strongest_quarter=max(rows, key=lambda row: row.revenue_musd).period,
            )
            path = "values.calculation"
            provenance = "runtime://deterministic-calculator/v1"
        elif node_id == "validate":
            MetricQueryResult.model_validate(state.values["metric_result"])
            CalculationResult.model_validate(state.values["calculation"])
            result = ValidationResult(
                valid=True,
                checks=[
                    "four-quarter coverage",
                    "fictional source provenance",
                    "growth arithmetic recomputed",
                ],
            )
            path = "values.validation"
            provenance = "policy://answer-validation/v1"
        elif node_id == "answer":
            result = self.provider.answer(state)
            path = "answer"
            provenance = "mock-provider://answer/v1 + validated-chart-dsl@1.0.0"
        else:
            raise RuntimeBoundaryError(f"unknown runtime node: {node_id}")

        after = result.model_dump(mode="json")
        previous: JsonValue = (
            before.answer.model_dump(mode="json") if path == "answer" and before.answer else None
        )
        if path.startswith("values."):
            previous = before.values.get(path.removeprefix("values."))
        return StatePatchPayload(
            changes=[StateChange(path=path, before=previous, after=after, source_node=node_id)],
            provenance=provenance,
        )
