import pytest
from pydantic import ValidationError

from app.agents.knowledge import KnowledgeAgent
from app.agents.ports import (
    EvidenceBundle,
    KnowledgeContext,
    ResolutionContext,
    TriageContext,
)
from app.agents.resolution import ResolutionAgent
from app.agents.triage import TriageAgent
from app.database.repository import PostgresRepository
from app.database.seed import build_seed_repository
from app.guardrails.contracts import GuardrailContext
from app.guardrails.service import GuardrailService
from app.mcp.server import MCPToolClient, MCPToolServer
from app.models.schemas import AnalysisResult, RecommendedAction, Severity
from app.rag.service import RAGService
from app.remediation.contracts import RemediationContext
from app.remediation.service import RemediationAgent, SimulatedActionExecutor
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
    assert output.details.remediation['status'] == 'verified'


@pytest.mark.asyncio
async def test_workflow_covers_database_and_latency_incidents():
    runtime = await build_runtime()
    database = await runtime.analyze('API cannot connect to PostgreSQL')
    latency = await runtime.analyze('Requests are taking 8-10 seconds')
    assert 'database' in database.result.title
    assert 'performance' in latency.result.title


def test_postgres_driver_url_is_normalized():
    qualified = PostgresRepository('postgresql+psycopg://user:pass@db/app')
    plain = PostgresRepository('postgresql://user:pass@db/app')
    assert qualified._connection_string() == 'postgresql://user:pass@db/app'
    assert plain._connection_string() == 'postgresql://user:pass@db/app'


@pytest.mark.asyncio
async def test_triage_context_rejects_unrelated_state():
    with pytest.raises(ValidationError):
        TriageContext(incident_description='failure', secret_marker='do-not-pass')
    result = await TriageAgent().triage(TriageContext(incident_description='payment API returns 503 after deployment'))
    assert result.service == 'payment'
    assert result.category in {'deployment_failure', 'availability_issue'}


@pytest.mark.asyncio
async def test_malformed_mcp_items_become_a_limitation():
    class MalformedClient:
        async def search_runbooks(self, query, limit):
            return {'status': 'complete', 'items': [{'unexpected': 'payload'}]}

        async def get_incident_history(self, service, category, limit):
            return {'status': 'no_evidence', 'items': []}

    result = await KnowledgeAgent(MalformedClient()).investigate(KnowledgeContext(
        incident_summary='payment outage',
        category='availability_issue',
    ))
    assert result.runbook_evidence == []
    assert any('malformed' in item for item in result.retrieval_limitations)


@pytest.mark.asyncio
async def test_resolution_forwards_only_selected_evidence():
    repository = await build_seed_repository()
    evidence = (await RAGService(repository).retrieve('payment 503 deployment', 3)).evidence

    class SelectiveProvider:
        async def generate_resolution(self, context):
            return {
                'severity': 'high',
                'title': 'Selected evidence',
                'likely_causes': ['release regression'],
                'recommended_actions': [{'text': 'Review the release with an operator.', 'requires_confirmation': True}],
                'confidence': 0.6,
                'evidence_ids': [evidence[0].id],
            }

    triage = await TriageAgent().triage(TriageContext(incident_description='payment API returns 503'))
    result = await ResolutionAgent(SelectiveProvider()).resolve(ResolutionContext(
        incident_summary=triage.incident_summary,
        triage=triage,
        evidence=EvidenceBundle(runbook_evidence=evidence),
    ))
    assert [item.id for item in result.evidence] == [evidence[0].id]


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


@pytest.mark.asyncio
async def test_remediation_is_allowlisted_mcp_mediated_and_health_verified():
    repository = await build_seed_repository()
    executor = SimulatedActionExecutor()
    server = MCPToolServer(RAGService(repository), remediation_executor=executor)
    client = MCPToolClient(server=server)
    result = await RemediationAgent(client).remediate(
        RemediationContext(
            incident_summary='payment release is unhealthy',
            service='payment',
            category='deployment_failure',
        ),
        enabled=True,
    )
    assert result.status == 'verified'
    assert result.health_verified
    assert executor.executions == [('rollback_deployment', 'payment')]
    assert client.calls[-2:] == ['execute_safe_action', 'verify_health']
