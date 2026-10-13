import asyncio
import platform
from collections.abc import Iterator
from pathlib import Path

import fastapi
import pydantic
from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from agent_trace_api import __version__
from agent_trace_api.approval_models import (
    ApprovalCatalog,
    ApprovalEffectSummary,
    ApprovalRun,
    ResumeApprovalRequest,
    StalenessRequest,
    StartApprovalRunRequest,
)
from agent_trace_api.approval_service import (
    ApprovalConflictError,
    ApprovalInputError,
    ApprovalIntegrityError,
    ApprovalRunNotFoundError,
    ApprovalService,
)
from agent_trace_api.config import (
    REPOSITORY_ROOT,
    RuntimePolicy,
    default_database_path,
    runtime_policy_from_environment,
)
from agent_trace_api.durable_models import (
    DurableCommandHandle,
    DurableEventsResponse,
    DurableReplayCommand,
    DurableReplayResponse,
    DurableRunCommand,
    DurableRunSnapshot,
    ForkDurableRunRequest,
    OverrideDurableStateRequest,
    StartDurableReplayRequest,
    StartDurableRunRequest,
)
from agent_trace_api.durable_runtime import DurableRunService, LangGraphRuntimeAdapter
from agent_trace_api.models import (
    CapabilitiesResponse,
    HealthResponse,
    LabRunSnapshot,
    ReadinessResponse,
    ReplayResponse,
    ResetRunRequest,
    StartRunRequest,
    StepRunRequest,
    ToolVersion,
)
from agent_trace_api.observability_models import (
    CreateComparePairRequest,
    EvalBatch,
    EvalCatalog,
    EvalRun,
    EventPage,
    ExportBundle,
    ExportRunRequest,
    OtelStatus,
    RunComparison,
    RunEvalBatchRequest,
)
from agent_trace_api.observability_service import (
    ObservabilityConflictError,
    ObservabilityEvalService,
    ObservabilityInputError,
    ObservabilityNotFoundError,
)
from agent_trace_api.protocol_models import (
    A2AJsonRpcRequest,
    A2AJsonRpcResponse,
    A2ARun,
    A2ARunRequest,
    AgentCard,
    AgUiRun,
    AgUiRunRequest,
    DecisionGateRequest,
    DecisionGateResult,
    McpRun,
    McpRunRequest,
    ProtocolCatalog,
    ProtocolVersionMismatch,
    ProtocolVersionProbe,
    ProtocolVersionProbeRequest,
)
from agent_trace_api.protocol_service import (
    McpProcessError,
    ProtocolConflictError,
    ProtocolInputError,
    ProtocolLabDisabledError,
    ProtocolLabService,
    ProtocolVersionError,
)
from agent_trace_api.public_preview import PublicPreviewPolicyMiddleware, bounded_public_stream
from agent_trace_api.retrieval_models import (
    DeletePreferenceRequest,
    MemorySnapshot,
    RetrievalCatalog,
    RetrievalRun,
    SavePreferenceRequest,
    StartRetrievalRunRequest,
)
from agent_trace_api.retrieval_service import (
    RetrievalConflictError,
    RetrievalRunNotFoundError,
    RetrievalService,
)
from agent_trace_api.scenario_builder_models import (
    CapstoneReport,
    CreateScenarioDraftRequest,
    CreateScenarioPreviewRunRequest,
    ImportScenarioArchiveRequest,
    LearningEvidenceRequest,
    LearningMap,
    ResetLearningProgressRequest,
    SaveScenarioDraftRequest,
    ScenarioArchive,
    ScenarioCatalog,
    ScenarioDetail,
    ScenarioDraft,
    ScenarioPreview,
    ScenarioPreviewRunCommand,
    ScenarioPreviewRunSnapshot,
    ScenarioValidationReport,
)
from agent_trace_api.scenario_builder_service import (
    ScenarioBuilderConflictError,
    ScenarioBuilderInputError,
    ScenarioBuilderService,
    ScenarioNotFoundError,
)
from agent_trace_api.scenario_preview_runtime import ScenarioPreviewRuntime
from agent_trace_api.security_models import (
    AttackSuiteResult,
    CodexCancelRequest,
    CodexCapability,
    CodexEnableRequest,
    CodexRunRequest,
    CodexRunResult,
    RunAttackRequest,
    RunAttackSuiteRequest,
    ScenarioImportRequest,
    ScenarioImportResult,
    SecurityAttackRun,
    SecurityCatalog,
    SecurityConflictError,
    SecurityDiagnostic,
    SecurityInputError,
)
from agent_trace_api.security_service import SecurityLabService
from agent_trace_api.service import RunService, RunStateError
from agent_trace_api.store import RunNotFoundError, SQLiteRunStore, StoreConflictError
from agent_trace_api.structured_output import (
    AssertionRequest,
    LessonsResponse,
    ManualStructuredRequest,
    SchemaRevisionRequest,
    SchemaSubmissionResult,
    StartStructuredRunRequest,
    StructuredLabService,
    StructuredOutputConflictError,
    StructuredRun,
    StructuredRunNotFoundError,
    StructuredValidationRequest,
)
from agent_trace_api.tool_calling import (
    ToolCallingConflictError,
    ToolCatalogResponse,
    ToolLabRun,
    ToolLabRunNotFoundError,
    ToolLabService,
    ToolRunCommand,
)


def create_app(
    *,
    db_path: Path | None = None,
    observe_delay: float = 0.6,
    protocol_labs_enabled: bool = True,
    runtime_policy: RuntimePolicy | None = None,
) -> FastAPI:
    policy = runtime_policy or runtime_policy_from_environment()
    api = FastAPI(
        title="AGENT//TRACE API",
        version=__version__,
        description="Typed application boundary for AGENT//TRACE Visual Systems Studio.",
        debug=False,
        docs_url=None if policy.public_preview else "/docs",
        redoc_url=None if policy.public_preview else "/redoc",
        openapi_url=None if policy.public_preview else "/openapi.json",
    )
    store = SQLiteRunStore(db_path or default_database_path())
    api.state.runtime_policy = policy
    api.state.run_service = RunService(store, observe_delay=observe_delay)
    api.state.structured_service = StructuredLabService()
    api.state.tool_service = ToolLabService()
    api.state.retrieval_service = RetrievalService(store.path, api.state.tool_service)
    api.state.approval_service = ApprovalService(store.path)
    api.state.security_service = SecurityLabService(
        REPOSITORY_ROOT,
        api.state.tool_service,
        api.state.retrieval_service,
        api.state.approval_service,
    )
    api.state.durable_service = DurableRunService(
        LangGraphRuntimeAdapter(store.path, node_delay=min(observe_delay, 0.025))
    )
    api.state.observability_service = ObservabilityEvalService(REPOSITORY_ROOT, store.path)
    api.state.protocol_service = ProtocolLabService(
        api.state.tool_service,
        enabled=protocol_labs_enabled,
        mcp_process_enabled=not policy.public_preview,
        repository_root=REPOSITORY_ROOT,
    )
    scenario_builder = ScenarioBuilderService(REPOSITORY_ROOT, store.path)
    api.state.scenario_builder_service = scenario_builder
    api.state.scenario_preview_runtime = ScenarioPreviewRuntime(scenario_builder, store.path)

    async def close_services() -> None:
        api.state.security_service.codex.cancel()
        await api.state.protocol_service.close()
        await api.state.durable_service.close()
        api.state.scenario_preview_runtime.close()
        api.state.scenario_builder_service.close()
        api.state.approval_service.close()
        api.state.retrieval_service.close()
        api.state.observability_service.close()
        store.close()

    api.router.add_event_handler("shutdown", close_services)

    api.add_middleware(
        CORSMiddleware,
        allow_origins=list(policy.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Accept", "Content-Type", "Last-Event-ID"],
    )
    if policy.public_preview:
        api.add_middleware(TrustedHostMiddleware, allowed_hosts=list(policy.trusted_hosts))
    api.add_middleware(PublicPreviewPolicyMiddleware, policy=policy)

    def service(request: Request) -> RunService:
        run_service: RunService = request.app.state.run_service
        return run_service

    def structured_service(request: Request) -> StructuredLabService:
        lab_service: StructuredLabService = request.app.state.structured_service
        return lab_service

    def tool_service(request: Request) -> ToolLabService:
        lab_service: ToolLabService = request.app.state.tool_service
        return lab_service

    def durable_service(request: Request) -> DurableRunService:
        run_service: DurableRunService = request.app.state.durable_service
        return run_service

    def retrieval_service(request: Request) -> RetrievalService:
        lab_service: RetrievalService = request.app.state.retrieval_service
        return lab_service

    def approval_service(request: Request) -> ApprovalService:
        lab_service: ApprovalService = request.app.state.approval_service
        return lab_service

    def security_service(request: Request) -> SecurityLabService:
        lab_service: SecurityLabService = request.app.state.security_service
        return lab_service

    def observability_service(request: Request) -> ObservabilityEvalService:
        eval_service: ObservabilityEvalService = request.app.state.observability_service
        return eval_service

    def protocol_service(request: Request) -> ProtocolLabService:
        lab_service: ProtocolLabService = request.app.state.protocol_service
        return lab_service

    def scenario_builder_service(request: Request) -> ScenarioBuilderService:
        builder_service: ScenarioBuilderService = request.app.state.scenario_builder_service
        return builder_service

    def scenario_preview_runtime(request: Request) -> ScenarioPreviewRuntime:
        preview_runtime: ScenarioPreviewRuntime = request.app.state.scenario_preview_runtime
        return preview_runtime

    @api.exception_handler(RunNotFoundError)
    async def not_found_handler(_request: Request, _error: RunNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "run not found"})

    @api.exception_handler(ScenarioNotFoundError)
    async def scenario_not_found_handler(
        _request: Request, _error: ScenarioNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "Scenario Kit or draft not found"})

    @api.exception_handler(ScenarioBuilderInputError)
    async def scenario_input_handler(
        _request: Request, error: ScenarioBuilderInputError
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @api.exception_handler(ScenarioBuilderConflictError)
    async def scenario_conflict_handler(
        _request: Request, error: ScenarioBuilderConflictError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @api.exception_handler(StoreConflictError)
    async def conflict_handler(_request: Request, error: StoreConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @api.exception_handler(RunStateError)
    async def state_handler(_request: Request, error: RunStateError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @api.exception_handler(ProtocolVersionError)
    async def protocol_version_handler(
        _request: Request, error: ProtocolVersionError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "detail": str(error),
                "code": "VERSION_NOT_SUPPORTED",
                "protocol": error.protocol,
                "requested_version": error.requested,
                "supported_version": error.supported,
            },
        )

    @api.exception_handler(ProtocolConflictError)
    async def protocol_conflict_handler(
        _request: Request, error: ProtocolConflictError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @api.exception_handler(ProtocolInputError)
    async def protocol_input_handler(_request: Request, error: ProtocolInputError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(error)})

    @api.exception_handler(ProtocolLabDisabledError)
    async def protocol_disabled_handler(
        _request: Request, error: ProtocolLabDisabledError
    ) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(error)})

    @api.exception_handler(McpProcessError)
    async def mcp_process_handler(_request: Request, error: McpProcessError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(error)})

    @api.exception_handler(StructuredRunNotFoundError)
    async def structured_not_found_handler(
        _request: Request, _error: StructuredRunNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "structured run not found"})

    @api.exception_handler(StructuredOutputConflictError)
    async def structured_conflict_handler(
        _request: Request, error: StructuredOutputConflictError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @api.exception_handler(ToolLabRunNotFoundError)
    async def tool_not_found_handler(
        _request: Request, _error: ToolLabRunNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "tool run not found"})

    @api.exception_handler(ToolCallingConflictError)
    async def tool_conflict_handler(
        _request: Request, error: ToolCallingConflictError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @api.exception_handler(RetrievalRunNotFoundError)
    async def retrieval_not_found_handler(
        _request: Request, _error: RetrievalRunNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "retrieval run not found"})

    @api.exception_handler(RetrievalConflictError)
    async def retrieval_conflict_handler(
        _request: Request, error: RetrievalConflictError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @api.exception_handler(ApprovalRunNotFoundError)
    async def approval_not_found_handler(
        _request: Request, _error: ApprovalRunNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "approval run not found"})

    @api.exception_handler(ApprovalConflictError)
    async def approval_conflict_handler(
        _request: Request, error: ApprovalConflictError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @api.exception_handler(ApprovalInputError)
    async def approval_input_handler(_request: Request, error: ApprovalInputError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @api.exception_handler(ApprovalIntegrityError)
    async def approval_integrity_handler(
        _request: Request, error: ApprovalIntegrityError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @api.exception_handler(SecurityInputError)
    async def security_input_handler(_request: Request, error: SecurityInputError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @api.exception_handler(SecurityConflictError)
    async def security_conflict_handler(
        _request: Request, error: SecurityConflictError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @api.exception_handler(ObservabilityNotFoundError)
    async def observability_not_found_handler(
        _request: Request, _error: ObservabilityNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "evaluation artifact not found"})

    @api.exception_handler(ObservabilityConflictError)
    async def observability_conflict_handler(
        _request: Request, error: ObservabilityConflictError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @api.exception_handler(ObservabilityInputError)
    async def observability_input_handler(
        _request: Request, error: ObservabilityInputError
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(error)})

    if policy.public_preview:

        @api.exception_handler(Exception)
        async def public_preview_error_handler(
            _request: Request, _error: Exception
        ) -> JSONResponse:
            return JSONResponse(
                status_code=500,
                content={"detail": "The request failed inside the safe lab boundary."},
            )

    @api.get("/api/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", service="agent-trace-api", version=__version__)

    @api.get("/api/ready", response_model=ReadinessResponse, tags=["system"])
    async def ready() -> ReadinessResponse:
        if not store.ready():
            raise HTTPException(status_code=503, detail="storage unavailable")
        return ReadinessResponse(
            status="ready",
            service="agent-trace-api",
            version=__version__,
            deployment_profile=policy.deployment_profile,
            storage="ready",
        )

    @api.get("/api/capabilities", response_model=CapabilitiesResponse, tags=["system"])
    async def capabilities() -> CapabilitiesResponse:
        return CapabilitiesResponse(
            offline_ready=True,
            providers=["mock"],
            python_version=platform.python_version(),
            tools=[
                ToolVersion(name="FastAPI", version=fastapi.__version__),
                ToolVersion(name="Pydantic", version=pydantic.__version__),
            ],
            deployment_profile=policy.deployment_profile,
            display_mode=policy.display_mode,
            safe_lab_mode=policy.public_preview,
            disabled_capabilities=list(policy.disabled_capabilities),
        )

    @api.get(
        "/api/protocols/catalog",
        response_model=ProtocolCatalog,
        tags=["protocol-labs"],
    )
    async def protocol_catalog(request: Request) -> ProtocolCatalog:
        return protocol_service(request).catalog()

    @api.post(
        "/api/protocols/mcp/runs",
        response_model=McpRun,
        status_code=status.HTTP_201_CREATED,
        tags=["protocol-labs"],
    )
    async def run_mcp_protocol(command: McpRunRequest, request: Request) -> McpRun:
        return await protocol_service(request).run_mcp(command)

    @api.post(
        "/api/protocols/a2a/runs",
        response_model=A2ARun,
        status_code=status.HTTP_201_CREATED,
        tags=["protocol-labs"],
    )
    async def run_a2a_protocol(command: A2ARunRequest, request: Request) -> A2ARun:
        interface_url = f"{policy.external_base_url(str(request.base_url))}/api/protocols/a2a/v1"
        return protocol_service(request).run_a2a(command, interface_url=interface_url)

    @api.get(
        "/.well-known/agent-card.json",
        response_model=AgentCard,
        tags=["protocol-labs"],
    )
    async def local_a2a_agent_card(request: Request) -> AgentCard:
        interface_url = f"{policy.external_base_url(str(request.base_url))}/api/protocols/a2a/v1"
        return protocol_service(request).discover_a2a_agent(interface_url=interface_url)

    @api.post(
        "/api/protocols/a2a/v1",
        response_model=A2AJsonRpcResponse,
        tags=["protocol-labs"],
    )
    async def local_a2a_json_rpc(
        command: A2AJsonRpcRequest,
        request: Request,
        capability: str = Header(alias="X-Agent-Trace-Capability"),
    ) -> A2AJsonRpcResponse:
        result = protocol_service(request).execute_a2a_message(
            command.params.message,
            presented_capability=capability,
        )
        return A2AJsonRpcResponse(id=command.id, result=result.task)

    @api.post(
        "/api/protocols/ag-ui/runs",
        response_model=AgUiRun,
        status_code=status.HTTP_201_CREATED,
        tags=["protocol-labs"],
    )
    async def run_ag_ui_protocol(command: AgUiRunRequest, request: Request) -> AgUiRun:
        return protocol_service(request).run_ag_ui(command)

    @api.post(
        "/api/protocols/decision-gate",
        response_model=DecisionGateResult,
        tags=["protocol-labs"],
    )
    async def run_protocol_decision_gate(
        command: DecisionGateRequest, request: Request
    ) -> DecisionGateResult:
        return protocol_service(request).decision_gate(command)

    @api.post(
        "/api/protocols/version-probe",
        response_model=ProtocolVersionProbe | ProtocolVersionMismatch,
        tags=["protocol-labs"],
    )
    async def probe_protocol_version(
        command: ProtocolVersionProbeRequest, request: Request
    ) -> ProtocolVersionProbe | ProtocolVersionMismatch:
        try:
            return protocol_service(request).version_probe(command)
        except ProtocolVersionError as error:
            return ProtocolVersionMismatch(
                detail=str(error),
                protocol=error.protocol,
                requested_version=error.requested,
                supported_version=error.supported,
            )

    @api.get(
        "/api/evals/catalog",
        response_model=EvalCatalog,
        tags=["observability-evals"],
    )
    async def eval_catalog(request: Request) -> EvalCatalog:
        return observability_service(request).catalog()

    @api.post(
        "/api/evals/batches",
        response_model=EvalBatch,
        status_code=status.HTTP_201_CREATED,
        tags=["observability-evals"],
    )
    async def run_eval_batch(command: RunEvalBatchRequest, request: Request) -> EvalBatch:
        return await observability_service(request).run_batch(command)

    @api.get(
        "/api/evals/batches/{batch_id}",
        response_model=EvalBatch,
        tags=["observability-evals"],
    )
    async def get_eval_batch(batch_id: str, request: Request) -> EvalBatch:
        return observability_service(request).get_batch(batch_id)

    @api.get(
        "/api/evals/runs/{run_id}",
        response_model=EvalRun,
        tags=["observability-evals"],
    )
    async def get_eval_run(run_id: str, request: Request) -> EvalRun:
        return observability_service(request).get_run(run_id)

    @api.get(
        "/api/evals/runs/{run_id}/events",
        response_model=EventPage,
        tags=["observability-evals"],
    )
    async def get_eval_events(
        run_id: str,
        request: Request,
        after_sequence: int = Query(default=0, ge=0),
        limit: int = Query(default=40, ge=1, le=100),
    ) -> EventPage:
        return observability_service(request).events(run_id, after_sequence, limit)

    @api.get(
        "/api/evals/runs/{run_id}/events/stream",
        response_class=StreamingResponse,
        tags=["observability-evals"],
    )
    async def stream_eval_events(
        run_id: str,
        request: Request,
        last_event_id: int = Header(default=0, alias="Last-Event-ID", ge=0),
    ) -> StreamingResponse:
        eval_service = observability_service(request)

        def stored_events() -> Iterator[str]:
            cursor = last_event_id
            while True:
                page = eval_service.events(run_id, cursor, 100)
                for event in page.events:
                    cursor = event.sequence
                    yield (
                        f"id: {event.sequence}\n"
                        f"event: {event.type}\n"
                        f"data: {event.model_dump_json()}\n\n"
                    )
                if not page.has_more:
                    break
            yield f'event: stream.end\ndata: {{"after_sequence":{cursor}}}\n\n'

        return StreamingResponse(
            stored_events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @api.post(
        "/api/evals/comparisons",
        response_model=RunComparison,
        status_code=status.HTTP_201_CREATED,
        tags=["observability-evals"],
    )
    async def create_eval_comparison(
        command: CreateComparePairRequest, request: Request
    ) -> RunComparison:
        return await observability_service(request).compare_pair(command)

    @api.post(
        "/api/evals/exports",
        response_model=ExportBundle,
        status_code=status.HTTP_201_CREATED,
        tags=["observability-evals"],
    )
    async def export_eval_run(command: ExportRunRequest, request: Request) -> ExportBundle:
        return observability_service(request).export(command)

    @api.get(
        "/api/evals/otel",
        response_model=OtelStatus,
        tags=["observability-evals"],
    )
    async def eval_otel_status(request: Request) -> OtelStatus:
        return observability_service(request).otel_status()

    @api.get(
        "/api/approvals/catalog",
        response_model=ApprovalCatalog,
        tags=["human-approval"],
    )
    async def approval_catalog(request: Request) -> ApprovalCatalog:
        return approval_service(request).catalog()

    @api.post(
        "/api/approval-runs",
        response_model=ApprovalRun,
        status_code=status.HTTP_201_CREATED,
        tags=["human-approval"],
    )
    async def start_approval_run(command: StartApprovalRunRequest, request: Request) -> ApprovalRun:
        return approval_service(request).start(command)

    @api.get(
        "/api/approval-runs/{run_id}",
        response_model=ApprovalRun,
        tags=["human-approval"],
    )
    async def get_approval_run(run_id: str, request: Request) -> ApprovalRun:
        return approval_service(request).get(run_id)

    @api.post(
        "/api/approval-runs/{run_id}/decisions",
        response_model=ApprovalRun,
        tags=["human-approval"],
    )
    async def resume_approval_run(
        run_id: str, command: ResumeApprovalRequest, request: Request
    ) -> ApprovalRun:
        return approval_service(request).resume(run_id, command)

    @api.post(
        "/api/approval-runs/{run_id}/staleness",
        response_model=ApprovalRun,
        tags=["human-approval"],
    )
    async def make_approval_stale(
        run_id: str, command: StalenessRequest, request: Request
    ) -> ApprovalRun:
        return approval_service(request).make_stale(run_id, command)

    @api.get(
        "/api/approval-runs/{run_id}/effects",
        response_model=ApprovalEffectSummary,
        tags=["human-approval"],
    )
    async def approval_effects(run_id: str, request: Request) -> ApprovalEffectSummary:
        return approval_service(request).effects(run_id)

    @api.get("/api/tools/catalog", response_model=ToolCatalogResponse, tags=["tool-calling"])
    async def tool_catalog(request: Request) -> ToolCatalogResponse:
        return tool_service(request).list_catalog()

    @api.get(
        "/api/security/catalog",
        response_model=SecurityCatalog,
        tags=["security-lab"],
    )
    async def security_catalog(request: Request) -> SecurityCatalog:
        return security_service(request).catalog()

    @api.post(
        "/api/security/runs",
        response_model=SecurityAttackRun,
        status_code=status.HTTP_201_CREATED,
        tags=["security-lab"],
    )
    async def start_security_run(command: RunAttackRequest, request: Request) -> SecurityAttackRun:
        return await security_service(request).run_attack(command)

    @api.get(
        "/api/security/runs/{run_id}",
        response_model=SecurityAttackRun,
        tags=["security-lab"],
    )
    async def get_security_run(run_id: str, request: Request) -> SecurityAttackRun:
        return security_service(request).get(run_id)

    @api.get(
        "/api/security/runs/{run_id}/diagnostic",
        response_model=SecurityDiagnostic,
        tags=["security-lab"],
    )
    async def security_diagnostic(run_id: str, request: Request) -> SecurityDiagnostic:
        return security_service(request).diagnostic(run_id)

    @api.post(
        "/api/security/suite",
        response_model=AttackSuiteResult,
        status_code=status.HTTP_201_CREATED,
        tags=["security-lab"],
    )
    async def run_security_suite(
        command: RunAttackSuiteRequest, request: Request
    ) -> AttackSuiteResult:
        return await security_service(request).run_suite(command)

    @api.post(
        "/api/security/imports",
        response_model=ScenarioImportResult,
        status_code=status.HTTP_201_CREATED,
        tags=["security-lab"],
    )
    async def import_security_scenario(
        command: ScenarioImportRequest, request: Request
    ) -> ScenarioImportResult:
        return security_service(request).importer.import_archive(command)

    @api.post(
        "/api/security/codex/enable",
        response_model=CodexCapability,
        tags=["security-lab"],
    )
    async def enable_security_codex(
        command: CodexEnableRequest, request: Request
    ) -> CodexCapability:
        return security_service(request).codex.set_enabled(command.enabled)

    @api.post(
        "/api/security/codex/runs",
        response_model=CodexRunResult,
        status_code=status.HTTP_201_CREATED,
        tags=["security-lab"],
    )
    async def run_security_codex(command: CodexRunRequest, request: Request) -> CodexRunResult:
        return await asyncio.to_thread(security_service(request).codex.invoke, command)

    @api.post(
        "/api/security/codex/cancel",
        response_model=CodexRunResult,
        tags=["security-lab"],
    )
    async def cancel_security_codex(
        command: CodexCancelRequest, request: Request
    ) -> CodexRunResult:
        cancelled = security_service(request).codex.cancel()
        return CodexRunResult(
            request_id="codexreq_cancel_request",
            status="cancelled" if cancelled else "failed",
            duration_ms=0,
            stdout_bytes=0,
            stderr_bytes=0,
            redaction_count=0,
            audit=["explicit cancellation request checked the single active process"],
            safe_error=(
                "The local provider request was cancelled."
                if cancelled
                else "No active local provider request exists."
            ),
        )

    @api.get(
        "/api/retrieval/catalog",
        response_model=RetrievalCatalog,
        tags=["retrieval-evidence"],
    )
    async def retrieval_catalog(request: Request) -> RetrievalCatalog:
        return retrieval_service(request).catalog()

    @api.post(
        "/api/retrieval/runs",
        response_model=RetrievalRun,
        status_code=status.HTTP_201_CREATED,
        tags=["retrieval-evidence"],
    )
    async def start_retrieval_run(
        command: StartRetrievalRunRequest, request: Request
    ) -> RetrievalRun:
        return await retrieval_service(request).start(command)

    @api.get(
        "/api/retrieval/runs/{run_id}",
        response_model=RetrievalRun,
        tags=["retrieval-evidence"],
    )
    async def get_retrieval_run(run_id: str, request: Request) -> RetrievalRun:
        return retrieval_service(request).get(run_id)

    @api.get(
        "/api/retrieval/memory",
        response_model=MemorySnapshot,
        tags=["retrieval-evidence"],
    )
    async def retrieval_memory(request: Request) -> MemorySnapshot:
        return retrieval_service(request).memory()

    @api.post(
        "/api/retrieval/memory/preferences",
        response_model=MemorySnapshot,
        tags=["retrieval-evidence"],
    )
    async def save_retrieval_preference(
        command: SavePreferenceRequest, request: Request
    ) -> MemorySnapshot:
        return retrieval_service(request).save_preference(command)

    @api.post(
        "/api/retrieval/memory/preferences/delete",
        response_model=MemorySnapshot,
        tags=["retrieval-evidence"],
    )
    async def delete_retrieval_preference(
        command: DeletePreferenceRequest, request: Request
    ) -> MemorySnapshot:
        return retrieval_service(request).delete_preference(command)

    @api.post(
        "/api/tools/runs",
        response_model=ToolLabRun,
        status_code=status.HTTP_201_CREATED,
        tags=["tool-calling"],
    )
    async def start_tool_run(command: ToolRunCommand, request: Request) -> ToolLabRun:
        return await tool_service(request).execute(command)

    @api.get("/api/tools/runs/{run_id}", response_model=ToolLabRun, tags=["tool-calling"])
    async def get_tool_run(run_id: str, request: Request) -> ToolLabRun:
        return tool_service(request).get(run_id)

    @api.get("/api/structured/lessons", response_model=LessonsResponse, tags=["structured-output"])
    async def structured_lessons(request: Request) -> LessonsResponse:
        return structured_service(request).list_lessons()

    @api.post(
        "/api/structured/runs",
        response_model=StructuredRun,
        status_code=status.HTTP_201_CREATED,
        tags=["structured-output"],
    )
    async def start_structured_run(
        command: StartStructuredRunRequest, request: Request
    ) -> StructuredRun:
        return structured_service(request).start(command)

    @api.get(
        "/api/structured/runs/{run_id}", response_model=StructuredRun, tags=["structured-output"]
    )
    async def get_structured_run(run_id: str, request: Request) -> StructuredRun:
        return structured_service(request).get(run_id)

    @api.post(
        "/api/structured/runs/{run_id}/validate",
        response_model=StructuredRun,
        tags=["structured-output"],
    )
    async def validate_structured_output(
        run_id: str, command: StructuredValidationRequest, request: Request
    ) -> StructuredRun:
        return structured_service(request).validate(run_id, command)

    @api.post(
        "/api/structured/runs/{run_id}/manual",
        response_model=StructuredRun,
        tags=["structured-output"],
    )
    async def resume_structured_output(
        run_id: str, command: ManualStructuredRequest, request: Request
    ) -> StructuredRun:
        return structured_service(request).resume_manual(run_id, command)

    @api.post(
        "/api/structured/runs/{run_id}/schema",
        response_model=SchemaSubmissionResult,
        tags=["structured-output"],
    )
    async def revise_structured_schema(
        run_id: str, command: SchemaRevisionRequest, request: Request
    ) -> SchemaSubmissionResult:
        return structured_service(request).apply_schema(run_id, command)

    @api.post(
        "/api/structured/runs/{run_id}/assertion",
        response_model=StructuredRun,
        tags=["structured-output"],
    )
    async def assert_structured_output(
        run_id: str, command: AssertionRequest, request: Request
    ) -> StructuredRun:
        return structured_service(request).record_assertion(run_id, command)

    @api.post(
        "/api/v1/runs",
        response_model=DurableCommandHandle,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["durable-runtime"],
    )
    async def start_durable_run(
        command: StartDurableRunRequest, request: Request
    ) -> DurableCommandHandle:
        return await durable_service(request).accept_start(command)

    @api.get(
        "/api/v1/runs/{run_id}",
        response_model=DurableRunSnapshot,
        tags=["durable-runtime"],
    )
    async def get_durable_run(run_id: str, request: Request) -> DurableRunSnapshot:
        return durable_service(request).snapshot(run_id)

    @api.post(
        "/api/v1/runs/{run_id}/step",
        response_model=DurableCommandHandle,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["durable-runtime"],
    )
    async def step_durable_run(
        run_id: str, command: DurableRunCommand, request: Request
    ) -> DurableCommandHandle:
        return durable_service(request).accept_execution(run_id, command, "step")

    @api.post(
        "/api/v1/runs/{run_id}/run",
        response_model=DurableCommandHandle,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["durable-runtime"],
    )
    async def complete_durable_run(
        run_id: str, command: DurableRunCommand, request: Request
    ) -> DurableCommandHandle:
        return durable_service(request).accept_execution(run_id, command, "run")

    @api.post(
        "/api/v1/runs/{run_id}/pause",
        response_model=DurableRunSnapshot,
        tags=["durable-runtime"],
    )
    async def pause_durable_run(
        run_id: str, command: DurableRunCommand, request: Request
    ) -> DurableRunSnapshot:
        return await durable_service(request).pause(run_id, command)

    @api.post(
        "/api/v1/runs/{run_id}/resume",
        response_model=DurableCommandHandle,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["durable-runtime"],
    )
    async def resume_durable_run(
        run_id: str, command: DurableRunCommand, request: Request
    ) -> DurableCommandHandle:
        return durable_service(request).accept_execution(run_id, command, "resume")

    @api.post(
        "/api/v1/runs/{run_id}/cancel",
        response_model=DurableRunSnapshot,
        tags=["durable-runtime"],
    )
    async def cancel_durable_run(
        run_id: str, command: DurableRunCommand, request: Request
    ) -> DurableRunSnapshot:
        return await durable_service(request).cancel(run_id, command)

    @api.post(
        "/api/v1/runs/{run_id}/reset",
        response_model=DurableRunSnapshot,
        tags=["durable-runtime"],
    )
    async def reset_durable_run(
        run_id: str, command: DurableRunCommand, request: Request
    ) -> DurableRunSnapshot:
        return await durable_service(request).reset(run_id, command)

    @api.post(
        "/api/v1/runs/{run_id}/fork",
        response_model=DurableRunSnapshot,
        status_code=status.HTTP_201_CREATED,
        tags=["durable-runtime"],
    )
    async def fork_durable_run(
        run_id: str, command: ForkDurableRunRequest, request: Request
    ) -> DurableRunSnapshot:
        return await durable_service(request).fork(run_id, command)

    @api.post(
        "/api/v1/runs/{run_id}/state",
        response_model=DurableRunSnapshot,
        tags=["durable-runtime"],
    )
    async def override_durable_run(
        run_id: str, command: OverrideDurableStateRequest, request: Request
    ) -> DurableRunSnapshot:
        return await durable_service(request).override(run_id, command)

    @api.post(
        "/api/v1/runs/{run_id}/replay-sessions",
        response_model=DurableReplayResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["durable-runtime"],
    )
    async def replay_durable_run(
        run_id: str, command: StartDurableReplayRequest, request: Request
    ) -> DurableReplayResponse:
        return durable_service(request).create_replay(run_id, command)

    @api.post(
        "/api/v1/replay-sessions/{session_id}/control",
        response_model=DurableReplayResponse,
        tags=["durable-runtime"],
    )
    async def control_durable_replay(
        session_id: str, command: DurableReplayCommand, request: Request
    ) -> DurableReplayResponse:
        return durable_service(request).control_replay(session_id, command)

    @api.get(
        "/api/v1/runs/{run_id}/events/page",
        response_model=DurableEventsResponse,
        tags=["durable-runtime"],
    )
    async def durable_event_page(
        run_id: str, request: Request, after_sequence: int = Query(default=0, ge=0)
    ) -> DurableEventsResponse:
        return durable_service(request).events(run_id, after_sequence)

    @api.get(
        "/api/v1/runs/{run_id}/events/stream",
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {}}}},
        tags=["durable-runtime"],
    )
    async def durable_events(
        run_id: str,
        request: Request,
        after_sequence: int = Query(default=0, ge=0),
        follow: bool = Query(default=False),
    ) -> StreamingResponse:
        run_service = durable_service(request)
        run_service.snapshot(run_id)
        last_event_id = request.headers.get("last-event-id")
        if last_event_id is not None:
            if not last_event_id.isdigit():
                return StreamingResponse(
                    iter(["event: stream-error\ndata: invalid Last-Event-ID\n\n"]),
                    status_code=400,
                    media_type="text/event-stream",
                )
            after_sequence = max(after_sequence, int(last_event_id))
        stream = run_service.stream_events(run_id, after_sequence=after_sequence, follow=follow)
        if policy.public_preview and follow:
            stream = bounded_public_stream(
                stream, max_lifetime_seconds=policy.sse_max_lifetime_seconds
            )
        return StreamingResponse(
            stream,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @api.get("/api/scenarios", response_model=ScenarioCatalog, tags=["scenario-builder"])
    async def list_scenarios(request: Request) -> ScenarioCatalog:
        return scenario_builder_service(request).catalog()

    @api.get(
        "/api/scenarios/{scenario_id}/preview",
        response_model=ScenarioPreview,
        tags=["scenario-builder"],
    )
    async def preview_scenario(scenario_id: str, request: Request) -> ScenarioPreview:
        return scenario_builder_service(request).built_in_preview(scenario_id)

    @api.get(
        "/api/scenarios/{scenario_id}",
        response_model=ScenarioDetail,
        tags=["scenario-builder"],
    )
    async def scenario_detail(scenario_id: str, request: Request) -> ScenarioDetail:
        return scenario_builder_service(request).detail(scenario_id)

    @api.post(
        "/api/scenario-builder/drafts",
        response_model=ScenarioDraft,
        status_code=status.HTTP_201_CREATED,
        tags=["scenario-builder"],
    )
    async def create_scenario_draft(
        command: CreateScenarioDraftRequest, request: Request
    ) -> ScenarioDraft:
        return scenario_builder_service(request).create_draft(command)

    @api.get(
        "/api/scenario-builder/drafts/{draft_id}",
        response_model=ScenarioDraft,
        tags=["scenario-builder"],
    )
    async def get_scenario_draft(draft_id: str, request: Request) -> ScenarioDraft:
        return scenario_builder_service(request).get_draft(draft_id)

    @api.post(
        "/api/scenario-builder/drafts/{draft_id}/save",
        response_model=ScenarioDraft,
        tags=["scenario-builder"],
    )
    async def save_scenario_draft(
        draft_id: str, command: SaveScenarioDraftRequest, request: Request
    ) -> ScenarioDraft:
        return scenario_builder_service(request).save_draft(draft_id, command)

    @api.post(
        "/api/scenario-builder/drafts/{draft_id}/validate",
        response_model=ScenarioValidationReport,
        tags=["scenario-builder"],
    )
    async def validate_scenario_draft(draft_id: str, request: Request) -> ScenarioValidationReport:
        return scenario_builder_service(request).validate_draft(draft_id)

    @api.get(
        "/api/scenario-builder/drafts/{draft_id}/preview",
        response_model=ScenarioPreview,
        tags=["scenario-builder"],
    )
    async def preview_scenario_draft(draft_id: str, request: Request) -> ScenarioPreview:
        return scenario_builder_service(request).draft_preview(draft_id)

    @api.post(
        "/api/scenario-builder/previews",
        response_model=ScenarioPreviewRunSnapshot,
        status_code=status.HTTP_201_CREATED,
        tags=["scenario-builder"],
    )
    async def start_scenario_preview(
        command: CreateScenarioPreviewRunRequest, request: Request
    ) -> ScenarioPreviewRunSnapshot:
        return scenario_preview_runtime(request).start(command)

    @api.get(
        "/api/scenario-builder/previews/{run_id}",
        response_model=ScenarioPreviewRunSnapshot,
        tags=["scenario-builder"],
    )
    async def get_scenario_preview(run_id: str, request: Request) -> ScenarioPreviewRunSnapshot:
        return scenario_preview_runtime(request).snapshot(run_id)

    @api.post(
        "/api/scenario-builder/previews/{run_id}/step",
        response_model=ScenarioPreviewRunSnapshot,
        tags=["scenario-builder"],
    )
    async def step_scenario_preview(
        run_id: str, command: ScenarioPreviewRunCommand, request: Request
    ) -> ScenarioPreviewRunSnapshot:
        return scenario_preview_runtime(request).step(run_id, command)

    @api.post(
        "/api/scenario-builder/previews/{run_id}/run",
        response_model=ScenarioPreviewRunSnapshot,
        tags=["scenario-builder"],
    )
    async def run_scenario_preview(
        run_id: str, command: ScenarioPreviewRunCommand, request: Request
    ) -> ScenarioPreviewRunSnapshot:
        return scenario_preview_runtime(request).run(run_id, command)

    @api.post(
        "/api/scenario-builder/previews/{run_id}/reset",
        response_model=ScenarioPreviewRunSnapshot,
        tags=["scenario-builder"],
    )
    async def reset_scenario_preview(
        run_id: str, command: ScenarioPreviewRunCommand, request: Request
    ) -> ScenarioPreviewRunSnapshot:
        return scenario_preview_runtime(request).reset(run_id, command)

    @api.post(
        "/api/scenario-builder/imports",
        response_model=ScenarioDraft,
        status_code=status.HTTP_201_CREATED,
        tags=["scenario-builder"],
    )
    async def import_scenario_archive(
        command: ImportScenarioArchiveRequest, request: Request
    ) -> ScenarioDraft:
        return scenario_builder_service(request).import_archive(command)

    @api.get(
        "/api/scenario-builder/drafts/{draft_id}/export",
        response_model=ScenarioArchive,
        tags=["scenario-builder"],
    )
    async def export_scenario_archive(draft_id: str, request: Request) -> ScenarioArchive:
        return scenario_builder_service(request).export_archive(draft_id)

    @api.get("/api/learning/map", response_model=LearningMap, tags=["learning"])
    async def learning_map(request: Request) -> LearningMap:
        return scenario_builder_service(request).learning_map()

    @api.post(
        "/api/learning/modules/{module_id}/evidence",
        response_model=LearningMap,
        tags=["learning"],
    )
    async def record_learning_evidence(
        module_id: str, command: LearningEvidenceRequest, request: Request
    ) -> LearningMap:
        return scenario_builder_service(request).record_evidence(module_id, command)

    @api.post(
        "/api/learning/modules/{module_id}/reset",
        response_model=LearningMap,
        tags=["learning"],
    )
    async def reset_learning_progress(
        module_id: str, command: ResetLearningProgressRequest, request: Request
    ) -> LearningMap:
        return scenario_builder_service(request).reset_progress(module_id, command)

    @api.get(
        "/api/scenario-builder/drafts/{draft_id}/capstone-report",
        response_model=CapstoneReport,
        tags=["scenario-builder"],
    )
    async def scenario_capstone_report(draft_id: str, request: Request) -> CapstoneReport:
        return scenario_builder_service(request).capstone_report(draft_id)

    @api.post(
        "/api/runs",
        response_model=LabRunSnapshot,
        status_code=status.HTTP_201_CREATED,
        tags=["runtime"],
    )
    async def start_run(command: StartRunRequest, request: Request) -> LabRunSnapshot:
        return await service(request).start(command)

    @api.get("/api/runs/{run_id}", response_model=LabRunSnapshot, tags=["runtime"])
    async def get_run(run_id: str, request: Request) -> LabRunSnapshot:
        return service(request).snapshot(run_id)

    @api.post("/api/runs/{run_id}/step", response_model=LabRunSnapshot, tags=["runtime"])
    async def step_run(run_id: str, command: StepRunRequest, request: Request) -> LabRunSnapshot:
        return await service(request).step(run_id, command)

    @api.post("/api/runs/{run_id}/run", response_model=LabRunSnapshot, tags=["runtime"])
    async def complete_run(
        run_id: str, command: StepRunRequest, request: Request
    ) -> LabRunSnapshot:
        return await service(request).run_to_completion(run_id, command)

    @api.post("/api/runs/{run_id}/reset", response_model=LabRunSnapshot, tags=["runtime"])
    async def reset_run(run_id: str, command: ResetRunRequest, request: Request) -> LabRunSnapshot:
        return await service(request).reset(run_id, command)

    @api.get("/api/runs/{run_id}/replay", response_model=ReplayResponse, tags=["runtime"])
    async def replay_run(run_id: str, request: Request) -> ReplayResponse:
        run_service = service(request)
        events = run_service.store.list_events(run_id)
        return ReplayResponse(run=run_service.replay(run_id), event_count=len(events))

    @api.get(
        "/api/runs/{run_id}/events",
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {}}}},
        tags=["runtime"],
    )
    async def run_events(
        run_id: str,
        request: Request,
        after_sequence: int = Query(default=0, ge=0),
        follow: bool = Query(default=False),
    ) -> StreamingResponse:
        run_service = service(request)
        run_service.store.get_run(run_id)
        last_event_id = request.headers.get("last-event-id")
        if last_event_id is not None:
            if not last_event_id.isdigit():
                return StreamingResponse(
                    iter(["event: stream-error\ndata: invalid Last-Event-ID\n\n"]),
                    status_code=400,
                    media_type="text/event-stream",
                )
            after_sequence = max(after_sequence, int(last_event_id))
        stream = run_service.stream_events(
            run_id,
            after_sequence=after_sequence,
            follow=follow,
        )
        if policy.public_preview and follow:
            stream = bounded_public_stream(
                stream, max_lifetime_seconds=policy.sse_max_lifetime_seconds
            )
        return StreamingResponse(
            stream,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return api


app = create_app()
