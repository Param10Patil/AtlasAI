'''Investigation orchestration with explicit least-context projectors.'''

from dataclasses import dataclass, field
from datetime import datetime, timezone
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
from app.remediation.contracts import (
    RemediationAudit,
    RemediationContext,
    RemediationResult,
)
from app.remediation.service import RemediationAgent

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
    remediation: dict[str, Any]
    degraded: bool
    observation: dict[str, Any]
    incident_id: str
    # For controlled incidents this is the observation captured before the
    # fault was injected.  Keeping it separate from ``observation`` lets the
    # UI prove both transitions: healthy -> fault and fault -> recovery.
    baseline_observation: dict[str, Any] = field(default_factory=dict)


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
        remediation_agent: RemediationAgent | None = None,
        remediation_enabled: bool = False,
        auto_remediation: bool = False,
    ):
        self.triage_agent = triage
        self.knowledge_agent = knowledge
        self.resolution_agent = resolution
        self.repository = repository
        self.guardrails = guardrails or GuardrailService()
        self.remediation_agent = remediation_agent
        self.remediation_enabled = remediation_enabled
        self.auto_remediation = auto_remediation
        self.engine = 'langgraph' if StateGraph is not None else 'sequential-fallback'
        self.graph = self._build_graph() if StateGraph is not None else None

    def _build_graph(self) -> Any:
        builder = StateGraph(WorkflowState)

        async def triage_node(state: dict[str, Any]) -> dict[str, Any]:
            context = TriageContext(incident_description=state['original_incident'], observation=state.get('observation'))
            return {'triage_result': await self.triage_agent.triage(context)}

        async def knowledge_node(state: dict[str, Any]) -> dict[str, Any]:
            triage = state['triage_result']
            context = KnowledgeContext(
                incident_summary=triage.incident_summary,
                service=triage.service,
                category=triage.category,
                symptoms=triage.symptoms,
                search_terms=triage.search_terms,
                observation=state.get('observation'),
            )
            return {'evidence': await self.knowledge_agent.investigate(context)}

        async def resolution_node(state: dict[str, Any]) -> dict[str, Any]:
            triage = state['triage_result']
            context = ResolutionContext(
                incident_summary=triage.incident_summary,
                triage=triage,
                evidence=state['evidence'],
                observation=state.get('observation'),
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

        async def remediation_node(state: dict[str, Any]) -> dict[str, Any]:
            decision = state['guardrail_decision']
            triage = state['triage_result']
            evidence = state['evidence']
            attempts = int(state.get('remediation_attempts', 0)) + 1
            if not decision.accepted or self.remediation_agent is None:
                return {
                    'remediation': RemediationResult(status='disabled', message='Remediation is not enabled for this result.'),
                    'remediation_attempts': attempts,
                }
            context = RemediationContext(
                incident_summary=triage.incident_summary,
                service=triage.service,
                category=triage.category,
                probable_causes=tuple(triage.likely_causes),
                evidence_ids=tuple(item.id for item in evidence.runbook_evidence),
                recommended_actions=tuple(decision.result.recommended_actions) if decision.result else (),
                observation=state.get('observation'),
            )
            return {
                'remediation': await self.remediation_agent.remediate(context, enabled=bool(state.get('remediation_enabled', self.remediation_enabled))),
                'remediation_attempts': attempts,
            }

        def remediation_route(state: dict[str, Any]) -> str:
            remediation = state.get('remediation')
            attempts = int(state.get('remediation_attempts', 0))
            if remediation is not None and remediation.retry_recommended and attempts < 2:
                return 'rediagnose'
            return 'done'

        builder.add_node('triage', triage_node)
        builder.add_node('knowledge', knowledge_node)
        builder.add_node('resolution', resolution_node)
        builder.add_node('guardrails', guardrail_node)
        builder.add_node('remediation', remediation_node)
        builder.add_edge(START, 'triage')
        builder.add_edge('triage', 'knowledge')
        builder.add_edge('knowledge', 'resolution')
        builder.add_edge('resolution', 'guardrails')
        builder.add_edge('guardrails', 'remediation')
        builder.add_conditional_edges(
            'remediation',
            remediation_route,
            {'rediagnose': 'triage', 'done': END},
        )
        return builder.compile()

    async def analyze(self, incident: Incident) -> WorkflowOutput:
        await self.repository.save_incident(incident)
        connected_observation = incident.observation is not None and incident.observation.connection.value == 'connected'
        state: dict[str, Any] = {
            'original_incident': incident.description,
            'remediation_enabled': self.remediation_enabled and (
                not connected_observation or incident.remediation_requested or self.auto_remediation
            ),
        }
        if incident.observation:
            state['observation'] = incident.observation
        if self.graph is not None:
            state.update(await self.graph.ainvoke(state))
        else:
            triage = await self.triage_agent.triage(TriageContext(incident_description=incident.description, observation=incident.observation))
            knowledge = await self.knowledge_agent.investigate(KnowledgeContext(
                incident_summary=triage.incident_summary,
                service=triage.service,
                category=triage.category,
                symptoms=triage.symptoms,
                search_terms=triage.search_terms,
                observation=incident.observation,
            ))
            resolution = await self.resolution_agent.resolve(ResolutionContext(
                incident_summary=triage.incident_summary,
                triage=triage,
                evidence=knowledge,
                observation=incident.observation,
            ))
            evidence_ids = tuple(item.id for item in knowledge.runbook_evidence)
            state.update({'triage_result': triage, 'evidence': knowledge, 'resolution': resolution})
            state['guardrail_decision'] = self.guardrails.validate(GuardrailContext(
                proposed_resolution=resolution,
                allowed_evidence_ids=evidence_ids,
            ))
            if state['guardrail_decision'].accepted and self.remediation_agent is not None:
                context = RemediationContext(
                    incident_summary=triage.incident_summary,
                    service=triage.service,
                    category=triage.category,
                    probable_causes=tuple(triage.likely_causes),
                    evidence_ids=tuple(item.id for item in knowledge.runbook_evidence),
                    recommended_actions=tuple(state['guardrail_decision'].result.recommended_actions) if state['guardrail_decision'].result else (),
                    observation=incident.observation,
                )
                state['remediation'] = await self.remediation_agent.remediate(context, enabled=bool(state.get('remediation_enabled', self.remediation_enabled)))
            else:
                state['remediation'] = RemediationResult(status='disabled', message='Remediation is not enabled for this result.')
        decision = state['guardrail_decision']
        result = decision.result if decision.accepted and decision.result else self._safe_guardrail_result(state, decision.limitation)
        remediation = state.get('remediation') or RemediationResult(status='disabled', message='Remediation is not enabled for this result.')
        if incident.baseline_observation is not None:
            remediation = remediation.model_copy(update={'before_observation': incident.baseline_observation})
        if remediation.action and remediation.target and remediation.status in {'executed', 'verified', 'failed'}:
            reason = next((item.text for item in result.recommended_actions if item.rank == 1), 'Allowlisted remediation selected by policy')
            audit = RemediationAudit(
                incident_id=incident.incident_key,
                actor='user' if incident.remediation_requested else 'ai',
                action=remediation.action,
                target=remediation.target,
                reason=reason,
                evidence_ids=tuple(result.recommended_actions[0].evidence_ids) if result.recommended_actions else (),
                before_observation=remediation.before_observation,
                after_observation=remediation.after_observation,
                verification_result=remediation.status,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            try:
                await self.repository.save_audit(audit)
            except Exception:  # noqa: BLE001 - audit failure must not hide analysis outcome
                result.limitations = list(dict.fromkeys([*result.limitations, 'Remediation audit persistence unavailable']))[:5]
        await self.repository.save_result(result)
        triage = state.get('triage_result')
        evidence = state.get('evidence')
        details = WorkflowDetails(
            steps=(
                {'name': 'triage', 'status': 'complete'},
                {'name': 'knowledge', 'status': 'complete' if evidence else 'degraded'},
                {'name': 'resolution', 'status': 'complete'},
                {'name': 'safety_validation', 'status': 'complete' if decision.accepted else 'degraded'},
                {'name': 'remediation', 'status': remediation.status},
            ),
            knowledge={
                'runbooks_retrieved': len(evidence.runbook_evidence) if evidence else 0,
                'historical_incidents': len(evidence.historical_incidents) if evidence else 0,
                'retrieval_status': 'degraded' if evidence and evidence.retrieval_limitations else 'complete',
                'retrieval_method': evidence.retrieval_method if evidence else 'unavailable',
                'best_score': evidence.best_score if evidence else 0,
                'candidate_count': evidence.candidate_count if evidence else 0,
            },
            tools=tuple(
                {'name': name, 'status': 'complete'}
                for name in dict.fromkeys(getattr(self.knowledge_agent.mcp_client, 'calls', []))
            ),
            models={
                # Keep the old display value for API compatibility while the
                # typed triage contract exposes the precise rule_fallback name.
                'classifier': ('fallback' if triage and triage.classifier_source == 'rule_fallback' else triage.classifier_source) if triage else 'fallback',
                'resolution': self.resolution_agent.provider_name,
                'orchestrator': self.engine,
            },
            safety=( {'name': 'schema_validation', 'status': 'passed' if decision.accepted else 'limited'}, ),
            remediation=remediation.model_dump(mode='json'),
            degraded=bool(result.limitations),
            observation=state['observation'].model_dump(mode='json') if state.get('observation') else {},
            incident_id=incident.incident_key,
            baseline_observation=incident.baseline_observation.model_dump(mode='json') if incident.baseline_observation else {},
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
