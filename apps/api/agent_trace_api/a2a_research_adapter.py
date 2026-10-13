"""Repository-local, opaque research-agent adapter for the E10 A2A lab.

The planner and research implementations exchange serialized public A2A DTOs.
Private research state and the private tool table never enter that boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent_trace_api.protocol_models import (
    A2AAdapterResult,
    A2AArtifact,
    A2AMessage,
    A2APart,
    A2ATask,
    A2ATaskStatus,
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSecurityRequirement,
    AgentSecurityScheme,
    AgentSkill,
    ApiKeySecurityScheme,
    StringList,
)

BASE_TIME = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)


class ResearchAgentBoundaryError(ValueError):
    """Raised when a caller does not satisfy the research-agent contract."""


class LocalResearchAgentAdapter:
    """Independent local adapter exposing only Agent Card/task/artifact DTOs."""

    def __init__(
        self,
        *,
        private_metric_values: tuple[int, int] = (103, 96),
        private_marker: str = "research-agent-private-state",
    ) -> None:
        self.__private_memory = {"marker": private_marker, "last_internal_plan": None}
        self.__metric_values = private_metric_values
        self.__internal_tools = {"private.metric_lookup": self.__query_ticket_metric}
        self.execution_count = 0

    @staticmethod
    def _timestamp(offset: int) -> datetime:
        return BASE_TIME + timedelta(milliseconds=offset * 17)

    def discover(
        self, *, interface_url: str = "http://127.0.0.1/api/protocols/a2a/v1"
    ) -> AgentCard:
        return AgentCard(
            name="Isolated incident research agent",
            description=(
                "Returns a cited fictional research artifact behind a local capability boundary."
            ),
            supportedInterfaces=[
                AgentInterface(
                    url=interface_url,
                    protocolBinding="JSONRPC",
                    protocolVersion="1.0",
                )
            ],
            version="1.0.0",
            capabilities=AgentCapabilities(
                streaming=False,
                pushNotifications=False,
                extendedAgentCard=False,
            ),
            securitySchemes={
                "capability_header": AgentSecurityScheme(
                    apiKeySecurityScheme=ApiKeySecurityScheme(
                        description=(
                            "Repository-local capability token; the fixture stores no credential."
                        ),
                        location="HEADER",
                        name="X-Agent-Trace-Capability",
                    )
                )
            },
            securityRequirements=[
                AgentSecurityRequirement(schemes={"capability_header": StringList(list=[])})
            ],
            skills=[
                AgentSkill(
                    id="incident-ticket-trend",
                    name="Incident ticket trend",
                    description="Compute a bounded trend from approved fictional evidence.",
                    tags=["incident", "research", "analytics"],
                    examples=["Did fictional ticket volume decline from Q3 to Q4?"],
                    inputModes=["text/plain"],
                    outputModes=["application/json"],
                )
            ],
            defaultInputModes=["text/plain"],
            defaultOutputModes=["application/json"],
        )

    def __query_ticket_metric(self) -> tuple[int, int]:
        return self.__metric_values

    def execute(self, request_json: str, *, presented_capability: str) -> str:
        """Validate a serialized message and return only a serialized public result."""

        if presented_capability != "incident.research":
            raise ResearchAgentBoundaryError("incident.research capability is required")
        request = A2AMessage.model_validate_json(request_json)
        if request.role != "ROLE_USER" or not request.parts or request.parts[0].text is None:
            raise ResearchAgentBoundaryError("a user text message is required")

        self.execution_count += 1
        self.__private_memory["last_internal_plan"] = "private.metric_lookup"
        q3, q4 = self.__internal_tools["private.metric_lookup"]()
        change = q4 - q3
        direction = "declined" if change < 0 else "increased" if change > 0 else "held steady"

        artifact = A2AArtifact(
            artifactId=f"artifact_{request.task_id.removeprefix('task_')}",
            name="Ticket-volume finding",
            description="A bounded, replaceable artifact from the isolated research capability.",
            parts=[
                A2APart(
                    data={
                        "claim": (
                            f"Fictional support tickets {direction} from {q3} in Q3 to {q4} in Q4."
                        ),
                        "change": change,
                        "unit": "tickets",
                        "source": "fictional://analytics/metrics",
                    },
                    mediaType="application/json",
                )
            ],
            extensions=["agent-trace://artifact/evidence/1.0.0"],
        )
        response = A2AMessage(
            messageId=f"message_response_{request.task_id.removeprefix('task_')}",
            role="ROLE_AGENT",
            taskId=request.task_id,
            contextId=request.context_id,
            parts=[
                A2APart(
                    text="The requested evidence artifact is attached.",
                    mediaType="text/plain",
                )
            ],
        )
        updates = [
            A2ATaskStatus(state="TASK_STATE_SUBMITTED", timestamp=self._timestamp(1)),
            A2ATaskStatus(state="TASK_STATE_WORKING", timestamp=self._timestamp(2)),
            A2ATaskStatus(state="TASK_STATE_COMPLETED", timestamp=self._timestamp(3)),
        ]
        result = A2AAdapterResult(
            task=A2ATask(
                id=request.task_id,
                contextId=request.context_id,
                status=updates[-1],
                history=[request, response],
                artifacts=[artifact],
            ),
            task_updates=updates,
        )
        return result.model_dump_json(by_alias=True)
