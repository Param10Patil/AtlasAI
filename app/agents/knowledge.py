'''Knowledge agent that talks to MCP rather than database internals.'''

from pydantic import ValidationError

from app.agents.ports import EvidenceBundle, KnowledgeContext
from app.mcp.server import MCPMalformedResponse, MCPToolClient, MCPUnavailable
from app.models.schemas import EvidenceItem


class KnowledgeAgent:
    def __init__(self, mcp_client: MCPToolClient):
        self.mcp_client = mcp_client

    async def investigate(self, context: KnowledgeContext) -> EvidenceBundle:
        query = ' '.join([context.incident_summary, context.category, *context.symptoms, *context.search_terms])[:500]
        limitations: list[str] = []
        runbooks: list = []
        history: list[dict[str, str]] = []
        retrieval_method = 'vector_cosine_plus_lexical_rerank'
        best_score = 0.0
        candidate_count = 0
        try:
            runbook_result = await self.mcp_client.search_runbooks(query, 3)
            if runbook_result.get('status') == 'unavailable':
                limitations.append('Evidence service unavailable')
            elif runbook_result.get('status') == 'no_evidence':
                limitations.append('No runbook evidence found')
            runbooks = runbook_result.get('items', [])[:5]
            retrieval_method = str(runbook_result.get('retrieval_method', retrieval_method))[:80]
            try:
                best_score = max(0.0, min(1.0, float(runbook_result.get('best_score', 0))))
                candidate_count = max(0, min(20, int(runbook_result.get('candidate_count', len(runbooks)))))
            except (TypeError, ValueError):
                limitations.append('Evidence service returned invalid ranking metadata')
        except (MCPUnavailable, MCPMalformedResponse):
            limitations.append('Evidence service unavailable')
        try:
            history_result = await self.mcp_client.get_incident_history(context.service, context.category, 3)
            if history_result.get('status') == 'no_evidence':
                limitations.append('No historical incidents found')
            history = [
                {key: str(value) for key, value in row.items() if key in {'id', 'service', 'category', 'summary', 'resolution', 'created_at'}}
                for row in history_result.get('items', [])[:5]
            ]
        except (MCPUnavailable, MCPMalformedResponse):
            limitations.append('Incident history service unavailable')
        evidence = []
        for item in runbooks:
            try:
                evidence.append(EvidenceItem.model_validate(item))
            except ValidationError:
                limitations.append('Evidence service returned malformed runbook data')
        evidence = evidence[:5]
        return EvidenceBundle(
            runbook_evidence=evidence,
            historical_incidents=history,
            retrieval_limitations=list(dict.fromkeys(limitations))[:5],
            retrieval_method=retrieval_method,
            best_score=best_score,
            candidate_count=candidate_count,
        )
