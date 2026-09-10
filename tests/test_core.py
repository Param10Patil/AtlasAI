import asyncio
from uuid import UUID

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
from app.graph.workflow import InvestigationWorkflow
from app.guardrails.contracts import GuardrailContext
from app.guardrails.service import GuardrailService
from app.mcp.server import MCPToolClient, MCPToolServer
from app.models.schemas import (
    AnalysisResult,
    EvidenceItem,
    Incident,
    RecommendedAction,
    Severity,
)
from app.rag.service import RAGService
from app.remediation.contracts import RemediationContext, RemediationResult
from app.remediation.service import RemediationAgent, SimulatedActionExecutor
from app.services.queue import AnalysisCoordinator, QueueFullError
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
async def test_mcp_observation_client_methods_fail_closed_when_kubernetes_is_offline():
    repository = await build_seed_repository()
    client = MCPToolClient(server=MCPToolServer(RAGService(repository)))
    for method in (
        client.get_cluster_health,
        client.get_service_observations,
        client.get_pod_status,
        client.get_deployment_status,
        client.get_recent_events,
    ):
        response = await method('ops-demo', 'checkout-api')
        assert response['status'] == 'offline'


@pytest.mark.asyncio
async def test_rag_reranks_grounded_matches_and_rejects_unrelated_queries():
    repository = await build_seed_repository()
    rag = RAGService(repository)
    tls = await rag.retrieve('TLS certificate chain expiry is causing handshake failures', 3)
    unrelated = await rag.retrieve('weather forecast for Mumbai', 3)
    assert tls.status == 'complete'
    assert tls.evidence[0].title == 'TLS certificate and trust failures'
    assert tls.evidence[0].score is not None and tls.evidence[0].score > 0.4
    assert tls.retrieval_method == 'vector_cosine_plus_lexical_rerank'
    assert unrelated.status == 'no_evidence'
    assert unrelated.evidence == []


@pytest.mark.asyncio
async def test_workflow_uses_langgraph_or_declared_fallback():
    runtime = await build_runtime()
    output = await runtime.analyze('Our payment API started returning 503 errors after today deployment.')
    assert output.result.severity is Severity.HIGH
    assert output.result.evidence
    assert output.result.recommended_actions[0].rank == 1
    assert output.details.models['orchestrator'] in {'langgraph', 'sequential-fallback'}
    assert output.details.models['classifier'] == 'fallback'
    assert output.details.remediation['status'] == 'verified'
    assert output.details.remediation['execution_preview']['execution_method'].startswith('MCP execute_safe_action')


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
    assert 'simulation' in result.message
    assert executor.executions == [('rollback_deployment', 'payment')]
    assert client.calls[-2:] == ['execute_safe_action', 'verify_health']


@pytest.mark.asyncio
async def test_deployment_failure_policy_overrides_stale_restart_recommendation():
    repository = await build_seed_repository()
    executor = SimulatedActionExecutor()
    client = MCPToolClient(server=MCPToolServer(RAGService(repository), remediation_executor=executor))
    result = await RemediationAgent(client).remediate(
        RemediationContext(
            incident_summary='release template is unhealthy while an old pod still reports restarts',
            service='checkout-api',
            category='deployment_failure',
            recommended_actions=[
                RecommendedAction(text='Restart the affected workload pod.', rank=1, requires_confirmation=True),
                RecommendedAction(text='Rollback the approved deployment.', rank=2, requires_confirmation=True),
            ],
        ),
        enabled=True,
    )
    assert result.status == 'verified'
    assert executor.executions == [('rollback_deployment', 'checkout-api')]


@pytest.mark.asyncio
async def test_remediation_preview_is_contextual_and_separates_rag_commands_from_execution():
    evidence = EvidenceItem(
        id='runbook-scale',
        kind='runbook',
        title='Replica recovery runbook',
        excerpt='If capacity is constrained, use kubectl scale deployment checkout-api --replicas=1.',
        score=0.91,
    )
    result = await RemediationAgent(SimulatedActionExecutor()).remediate(
        RemediationContext(
            incident_summary='checkout latency is elevated',
            service='checkout-api',
            category='performance_issue',
            evidence=(evidence,),
            recommended_actions=(RecommendedAction(
                rank=1,
                text='Scale the approved deployment to one replica.',
                evidence_ids=['runbook-scale'],
            ),),
        ),
        enabled=False,
    )
    assert result.status == 'awaiting_approval'
    assert result.execution_preview is not None
    assert result.execution_preview.action.value == 'scale_deployment'
    assert result.execution_preview.target == 'checkout-api'
    assert result.execution_preview.rag_commands == ['kubectl scale deployment checkout-api --replicas=1']
    assert 'MCP execute_safe_action' in result.execution_preview.execution_method
    assert result.execution_preview.approval_required is True


@pytest.mark.asyncio
async def test_mcp_rejects_non_allowlisted_remediation_action():
    repository = await build_seed_repository()
    server = MCPToolServer(RAGService(repository), remediation_executor=SimulatedActionExecutor())
    response = await server.handle({
        'jsonrpc': '2.0',
        'id': 9,
        'method': 'tools/call',
        'params': {'name': 'execute_safe_action', 'arguments': {'action': 'delete_database', 'target': 'prod'}},
    })
    assert response['error']['code'] == -32602


@pytest.mark.asyncio
async def test_analysis_queue_is_fifo_bounded_and_idempotent():
    class FakeWorkflow:
        def __init__(self):
            self.calls = []

        async def analyze(self, incident):
            self.calls.append(incident.description)
            await asyncio.sleep(0.01)
            return object()

    workflow = FakeWorkflow()
    coordinator = AnalysisCoordinator(workflow, queue_capacity=1)
    first_id = UUID('22222222-2222-4222-8222-222222222222')
    first, created = await coordinator.submit('first', first_id)
    assert created
    second, created = await coordinator.submit('second')
    assert created
    duplicate, created = await coordinator.submit('ignored', first_id)
    assert not created
    assert duplicate.job_id == first.job_id
    with pytest.raises(QueueFullError):
        await coordinator.submit('third')
    await coordinator.wait(first)
    await coordinator.wait(second)
    assert workflow.calls == ['first', 'second']


@pytest.mark.asyncio
async def test_running_job_can_be_cancelled_without_leaking_incident_text():
    started = asyncio.Event()

    class SlowWorkflow:
        async def analyze(self, incident):
            started.set()
            await asyncio.sleep(30)

    coordinator = AnalysisCoordinator(SlowWorkflow(), queue_capacity=0)
    record, _ = await coordinator.submit('secret incident text')
    await asyncio.wait_for(started.wait(), timeout=1)
    cancelled = await coordinator.cancel(record.job_id)
    assert cancelled.status == 'cancelled'
    with pytest.raises(RuntimeError, match='CANCELLED'):
        await coordinator.wait(record)
    assert 'secret incident text' not in str(record.events)


@pytest.mark.asyncio
async def test_failed_remediation_rediagnoses_once_then_stops():
    repository = await build_seed_repository()
    mcp = MCPToolClient(server=MCPToolServer(RAGService(repository)))

    class FlakyRemediation:
        def __init__(self):
            self.calls = 0

        async def remediate(self, context, *, enabled):
            self.calls += 1
            if self.calls == 1:
                return RemediationResult(
                    status='failed',
                    action='rollback_deployment',
                    target=context.service or 'payment',
                    message='health check did not pass',
                    retry_recommended=True,
                )
            return RemediationResult(
                status='verified',
                action='restart_pod',
                target=context.service or 'payment',
                message='health recovered',
                health_verified=True,
            )

    remediation = FlakyRemediation()
    workflow = InvestigationWorkflow(
        TriageAgent(),
        KnowledgeAgent(mcp),
        ResolutionAgent(),
        repository,
        remediation_agent=remediation,
        remediation_enabled=True,
    )
    output = await workflow.analyze(Incident(description='payment API returns 503 after deployment'))
    assert remediation.calls == 2
    assert output.details.remediation['status'] == 'verified'
