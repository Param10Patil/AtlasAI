'''FastAPI application boundary for OpsPilot.'''

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import __version__
from app.agents.ports import KnowledgeContext, ResolutionContext, TriageContext
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
            'degraded': details.degraded,
        },
    }


def create_app(runtime: ApplicationRuntime | None = None) -> FastAPI:
    active_runtime = runtime

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal active_runtime
        if active_runtime is None:
            active_runtime = await build_runtime()
        app.state.runtime = active_runtime
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
            from fastapi.responses import JSONResponse
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
            output = await current.analyze(payload.description)
            return _public_payload(output)
        except TimeoutError:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=504, content={'error': {'code': 'WORKFLOW_TIMEOUT', 'message': 'The investigation took too long. Please try again.', 'retryable': True}})
        except Exception:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=500, content={'error': {'code': 'INVESTIGATION_FAILED', 'message': 'OpsPilot could not complete the investigation. Please try again.', 'retryable': True}})

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
