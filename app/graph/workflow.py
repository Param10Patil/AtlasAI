'''Investigation orchestration with explicit least-context projectors.'''

from dataclasses import dataclass
from typing import Any

from app.agents.knowledge import KnowledgeAgent
from app.agents.ports import KnowledgeContext, ResolutionContext, TriageContext
from app.agents.resolution import ResolutionAgent
from app.agents.triage import TriageAgent
from app.database.repository import Repository
from app.graph.state import WorkflowState
from app.guardrails.contracts import GuardrailContext
from app.guardrails.service import GuardrailService
from app.models.schemas import AnalysisResult, Incident

try:
    from langgraph.graph import END, START, StateGraph
except ImportError:
    END = START = StateGraph = None


@dataclass(frozen=True)
class WorkflowDetails:
    steps: tuple[dict[str, str], ...]
    knowledge: dict[str, Any]
    tools: tuple[dict[str, str], ...]
    models: dict[str, str]
    safety: tuple[dict[str, str], ...]
    degraded: bool


@dataclass(frozen=True)
class WorkflowOutput:
    result: AnalysisResult
    details: WorkflowDetails


class InvestigationWorkflow:
    def __init__(
        self,
        triage: TriageAgent,
        knowledge: KnowledgeAgent,
        resolution: ResolutionAgent,
        repository: Repository,
        guardrails: GuardrailService | None = None,
    ):
        self.triage_agent = triage
        self.knowledge_agent = knowledge
        self.resolution_agent = resolution
        self.repository = repository
        self.guardrails = guardrails or GuardrailService()
        self.engine = 'langgraph' if StateGraph is not None else 'sequential-fallback'
        self.graph = self._build_graph() if StateGraph is not None else None

    def _build_graph(self) -> Any:
        builder = StateGraph(WorkflowState)

        async def triage_node(state: dict[str, Any]) -> dict[str, Any]:
            context = TriageContext(incident_description=state['original_incident'])
            return {'triage_result': await self.triage_agent.triage(context)}

        async def knowledge_node(state: dict[str, Any]) -> dict[str, Any]:
            triage = state['triage_result']
            context = KnowledgeContext(
                incident_summary=triage.incident_summary,
                service=triage.service,
                category=triage.category,
                symptoms=triage.symptoms,
                search_terms=triage.search_terms,
            )
            return {'evidence': await self.knowledge_agent.investigate(context)}

        async def resolution_node(state: dict[str, Any]) -> dict[str, Any]:
            triage = state['triage_result']
            context = ResolutionContext(
                incident_summary=triage.incident_summary,
                triage=triage,
                evidence=state['evidence'],
            )
            return {'resolution': await self.resolution_agent.resolve(context)}

        async def guardrail_node(state: dict[str, Any]) -> dict[str, Any]:
            result = state['resolution']
            evidence_ids = tuple(item.id for item in state['evidence'].runbook_evidence)
            decision = self.guardrails.validate(GuardrailContext(
                proposed_resolution=result,
                allowed_evidence_ids=evidence_ids,
            ))
            return {'guardrail_decision': decision}

        builder.add_node('triage', triage_node)
        builder.add_node('knowledge', knowledge_node)
        builder.add_node('resolution', resolution_node)
        builder.add_node('guardrails', guardrail_node)
        builder.add_edge(START, 'triage')
        builder.add_edge('triage', 'knowledge')
        builder.add_edge('knowledge', 'resolution')
        builder.add_edge('resolution', 'guardrails')
        builder.add_edge('guardrails', END)
        return builder.compile()

    async def analyze(self, incident: Incident) -> WorkflowOutput:
        await self.repository.save_incident(incident)
        state: dict[str, Any] = {'original_incident': incident.description}
        if self.graph is not None:
            state.update(await self.graph.ainvoke(state))
        else:
            triage = await self.triage_agent.triage(TriageContext(incident_description=incident.description))
            knowledge = await self.knowledge_agent.investigate(KnowledgeContext(
                incident_summary=triage.incident_summary,
                service=triage.service,
                category=triage.category,
                symptoms=triage.symptoms,
                search_terms=triage.search_terms,
            ))
            resolution = await self.resolution_agent.resolve(ResolutionContext(
                incident_summary=triage.incident_summary,
                triage=triage,
                evidence=knowledge,
            ))
            evidence_ids = tuple(item.id for item in knowledge.runbook_evidence)
            state.update({'triage_result': triage, 'evidence': knowledge, 'resolution': resolution})
            state['guardrail_decision'] = self.guardrails.validate(GuardrailContext(
                proposed_resolution=resolution,
                allowed_evidence_ids=evidence_ids,
            ))
        decision = state['guardrail_decision']
        result = decision.result if decision.accepted and decision.result else self._safe_guardrail_result(state, decision.limitation)
        await self.repository.save_result(result)
        triage = state.get('triage_result')
        evidence = state.get('evidence')
        details = WorkflowDetails(
            steps=(
                {'name': 'triage', 'status': 'complete'},
                {'name': 'knowledge', 'status': 'complete' if evidence else 'degraded'},
                {'name': 'resolution', 'status': 'complete'},
                {'name': 'safety_validation', 'status': 'complete' if decision.accepted else 'degraded'},
            ),
            knowledge={
                'runbooks_retrieved': len(evidence.runbook_evidence) if evidence else 0,
                'historical_incidents': len(evidence.historical_incidents) if evidence else 0,
                'retrieval_status': 'degraded' if evidence and evidence.retrieval_limitations else 'complete',
            },
            tools=tuple(
                {'name': name, 'status': 'complete'}
                for name in dict.fromkeys(getattr(self.knowledge_agent.mcp_client, 'calls', []))
            ),
            models={
                'classifier': triage.classifier_source if triage else 'fallback',
                'resolution': self.resolution_agent.provider_name,
                'orchestrator': self.engine,
            },
            safety=( {'name': 'schema_validation', 'status': 'passed' if decision.accepted else 'limited'}, ),
            degraded=bool(result.limitations),
        )
        return WorkflowOutput(result, details)

    @staticmethod
    def _safe_guardrail_result(state: dict[str, Any], limitation: str | None) -> AnalysisResult:
        result: AnalysisResult = state['resolution']
        return result.model_copy(update={
            'confidence': min(result.confidence, 0.25),
            'recommended_actions': [],
            'limitations': [*result.limitations, limitation or 'safety validation rejected the draft'],
        })
