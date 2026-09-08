import pytest
from pydantic import ValidationError

from app.agents.ports import TriageContext
from app.agents.triage import TriageAgent
from app.database.seed import build_seed_repository
from app.mcp.server import MCPToolClient, MCPToolServer
from app.guardrails.contracts import GuardrailContext
from app.guardrails.service import GuardrailService
from app.models.schemas import AnalysisResult, RecommendedAction, Severity
from app.rag.service import RAGService
from app.services.runtime import build_runtime


@pytest.mark.asyncio
async def test_seed_rag_and_mcp_return_bounded_evidence():
    repository = await build_seed_repository()
    assert len(repository.knowledge) >= 8
    rag = RAGService(repository)
    response = await rag.retrieve('payment API 503 deployment', 5)
    assert response.status == 'complete'
    assert 1 <= len(response.evidence) <= 5
    assert all(len(item.excerpt) <= 800 for item in response.evidence)
    server = MCPToolServer(rag)
    client = MCPToolClient(server=server)
    result = await client.search_runbooks('database connection', 3)
    assert result['status'] in {'complete', 'no_evidence'}
    assert client.calls == ['search_runbooks']


@pytest.mark.asyncio
async def test_workflow_uses_langgraph_or_declared_fallback():
    runtime = await build_runtime()
    output = await runtime.analyze('Our payment API started returning 503 errors after today deployment.')
    assert output.result.severity is Severity.HIGH
    assert output.result.evidence
    assert output.details.models['orchestrator'] in {'langgraph', 'sequential-fallback'}
    assert output.details.models['classifier'] == 'fallback'


@pytest.mark.asyncio
async def test_triage_context_rejects_unrelated_state():
    with pytest.raises(ValidationError):
        TriageContext(incident_description='failure', secret_marker='do-not-pass')
    result = await TriageAgent().triage(TriageContext(incident_description='payment API returns 503 after deployment'))
    assert result.service == 'payment'
    assert result.category in {'deployment_failure', 'availability_issue'}


def test_guardrails_reject_destructive_advice():
    draft = AnalysisResult(
        severity=Severity.HIGH,
        title='Unsafe draft',
        recommended_actions=[RecommendedAction(text='kubectl delete deployment production-api', requires_confirmation=True)],
        confidence=0.8,
    )
    decision = GuardrailService().validate(GuardrailContext(proposed_resolution=draft, allowed_evidence_ids=()))
    assert not decision.accepted
    assert 'destructive' in (decision.limitation or '')
