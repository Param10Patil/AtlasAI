'''FastAPI application boundary for OpsPilot.'''

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import __version__
from app.agents.ports import KnowledgeContext, ResolutionContext, TriageContext
from app.services.queue import (
    AnalysisCoordinator,
    JobNotFoundError,
    QueueFullError,
)
from app.services.runtime import ApplicationRuntime, build_runtime


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    description: str = Field(min_length=1, max_length=4000)

    @field_validator('description')
    @classmethod
    def trim_description(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('description must contain non-whitespace text')
        return value


class JobRequest(AnalyzeRequest):
    client_request_id: UUID | None = None


class MCPJsonRpcRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    jsonrpc: str = Field(default='2.0', pattern=r'^2\.0$')
    id: int | str | None = None
    method: str = Field(min_length=1, max_length=80)
    params: dict[str, Any] = Field(default_factory=dict)


def _public_payload(output: Any) -> dict[str, Any]:
    result = output.result
    details = output.details
    return {
        'request_id': str(result.request_id),
        'status': result.status,
        'analysis': {
            'severity': result.severity.value,
            'title': result.title,
            'likely_causes': result.likely_causes,
            'recommended_actions': [action.model_dump(mode='json') for action in result.recommended_actions],
            'confidence': result.confidence,
            'evidence': [item.model_dump(mode='json') for item in result.evidence],
            'limitations': result.limitations,
        },
        'details': {
            'steps': list(details.steps),
            'knowledge': details.knowledge,
            'tools': list(details.tools),
            'models': details.models,
            'safety': list(details.safety),
            'remediation': details.remediation,
            'degraded': details.degraded,
        },
    }


def _public_event(event: dict[str, Any]) -> dict[str, Any]:
    event = dict(event)
    if event.get('result') is not None:
        event['result'] = _public_payload(event['result'])
    return event


def create_app(runtime: ApplicationRuntime | None = None) -> FastAPI:
    active_runtime = runtime

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal active_runtime
        if active_runtime is None:
            active_runtime = await build_runtime()
        app.state.runtime = active_runtime
        app.state.coordinator = AnalysisCoordinator(
            active_runtime.workflow,
            queue_capacity=active_runtime.settings.queue_capacity,
            timeout_seconds=120,
        )
        yield

    app = FastAPI(title='OpsPilot', version=__version__, lifespan=lifespan)
    static_dir = Path(__file__).resolve().parents[1] / 'static'
    if static_dir.exists():
        app.mount('/static', StaticFiles(directory=static_dir), name='static')

    @app.get('/')
    async def index() -> FileResponse:
        return FileResponse(static_dir / 'index.html')

    @app.get('/api/health')
    async def health() -> dict[str, str]:
        return {'status': 'ok', 'service': 'api', 'version': __version__}

    @app.get('/api/ready')
    async def ready(request: Request) -> dict[str, Any]:
        current: ApplicationRuntime = request.app.state.runtime
        if not current.ready:
            return JSONResponse(
                status_code=503,
                content={'status': 'not_ready', 'message': current.readiness_message, 'optional': {'mlflow': 'ignored', 'lora': 'optional'}},
            )
        return {'status': 'ready', 'message': current.readiness_message, 'optional': {'mlflow': 'ignored', 'lora': 'available' if current.workflow.triage_agent.classifier.__class__.__name__ == 'LoRAClassifier' else 'fallback'}}

    @app.post('/api/incidents/analyze')
    async def analyze(payload: AnalyzeRequest, request: Request) -> dict[str, Any]:
        current: ApplicationRuntime = request.app.state.runtime
        if not current.ready:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=503, content={'error': {'code': 'DEPENDENCY_UNAVAILABLE', 'message': 'OpsPilot is not ready. Please try again.', 'retryable': True}})
        try:
            record, _ = await request.app.state.coordinator.submit(payload.description)
            output = await request.app.state.coordinator.wait(record)
            return _public_payload(output)
        except QueueFullError:
            return JSONResponse(status_code=429, content={'error': {'code': 'QUEUE_FULL', 'message': 'One investigation is running and the bounded queue is full.', 'retryable': True}})
        except RuntimeError as exc:
            code = str(exc)
            if code == 'WORKFLOW_TIMEOUT':
                return JSONResponse(status_code=504, content={'error': {'code': code, 'message': 'The investigation took too long. Please try again.', 'retryable': True}})
            if code == 'CANCELLED':
                return JSONResponse(status_code=409, content={'error': {'code': code, 'message': 'The investigation was cancelled.', 'retryable': False}})
            return JSONResponse(status_code=500, content={'error': {'code': code if code.isupper() else 'INVESTIGATION_FAILED', 'message': 'OpsPilot could not complete the investigation. Please try again.', 'retryable': True}})
        # Keep unexpected provider/database details out of the public response.
        except Exception:  # noqa: BLE001
            return JSONResponse(status_code=500, content={'error': {'code': 'INVESTIGATION_FAILED', 'message': 'OpsPilot could not complete the investigation. Please try again.', 'retryable': True}})

    @app.post('/api/incidents/analyze/jobs', status_code=202)
    async def create_job(payload: JobRequest, request: Request) -> dict[str, Any]:
        current: ApplicationRuntime = request.app.state.runtime
        if not current.ready:
            return JSONResponse(status_code=503, content={'error': {'code': 'DEPENDENCY_UNAVAILABLE', 'message': 'OpsPilot is not ready. Please try again.', 'retryable': True}})
        try:
            record, _ = await request.app.state.coordinator.submit(payload.description, payload.client_request_id)
            return _public_event(await request.app.state.coordinator.public_status(record))
        except QueueFullError:
            return JSONResponse(status_code=429, content={'error': {'code': 'QUEUE_FULL', 'message': 'One investigation is running and the bounded queue is full.', 'retryable': True}})

    @app.get('/api/incidents/analyze/jobs/{job_id}')
    async def job_status(job_id: UUID, request: Request) -> dict[str, Any]:
        try:
            record = await request.app.state.coordinator.store.get(job_id)
        except JobNotFoundError:
            return JSONResponse(status_code=404, content={'error': {'code': 'JOB_NOT_FOUND', 'message': 'The investigation job was not found.', 'retryable': False}})
        return _public_event(await request.app.state.coordinator.public_status(record))

    @app.delete('/api/incidents/analyze/jobs/{job_id}')
    async def cancel_job(job_id: UUID, request: Request) -> dict[str, Any]:
        try:
            record = await request.app.state.coordinator.cancel(job_id)
        except JobNotFoundError:
            return JSONResponse(status_code=404, content={'error': {'code': 'JOB_NOT_FOUND', 'message': 'The investigation job was not found.', 'retryable': False}})
        return _public_event(await request.app.state.coordinator.public_status(record))

    @app.post('/internal/v1/mcp')
    async def internal_mcp(payload: MCPJsonRpcRequest, request: Request) -> dict[str, Any]:
        current: ApplicationRuntime = request.app.state.runtime
        server = getattr(current.workflow.knowledge_agent.mcp_client, 'server', None)
        if server is None:
            return JSONResponse(status_code=503, content={'error': {'code': 'MCP_UNAVAILABLE', 'message': 'MCP server is not configured.', 'retryable': True}})
        return await server.handle(payload.model_dump(exclude_none=True))

    @app.get('/api/incidents/analyze/jobs/{job_id}/events')
    async def job_events(job_id: UUID, request: Request) -> StreamingResponse:
        try:
            record = await request.app.state.coordinator.store.get(job_id)
        except JobNotFoundError:
            return JSONResponse(status_code=404, content={'error': {'code': 'JOB_NOT_FOUND', 'message': 'The investigation job was not found.', 'retryable': False}})

        async def stream():
            try:
                async for event in request.app.state.coordinator.events(record):
                    if await request.is_disconnected():
                        break
                    yield json.dumps(_public_event(event), separators=(',', ':')) + '\n'
            except RuntimeError:
                yield json.dumps({'job_id': str(job_id), 'status': 'stream_unavailable'}) + '\n'

        return StreamingResponse(stream(), media_type='application/x-ndjson')

    @app.post('/internal/v1/triage')
    async def internal_triage(payload: TriageContext, request: Request) -> dict[str, Any]:
        current: ApplicationRuntime = request.app.state.runtime
        return (await current.workflow.triage_agent.triage(payload)).model_dump(mode='json')

    @app.post('/internal/v1/knowledge')
    async def internal_knowledge(payload: KnowledgeContext, request: Request) -> dict[str, Any]:
        current: ApplicationRuntime = request.app.state.runtime
        return (await current.workflow.knowledge_agent.investigate(payload)).model_dump(mode='json')

    @app.post('/internal/v1/resolution')
    async def internal_resolution(payload: ResolutionContext, request: Request) -> dict[str, Any]:
        current: ApplicationRuntime = request.app.state.runtime
        return (await current.workflow.resolution_agent.resolve(payload)).model_dump(mode='json')

    return app


app = create_app()
