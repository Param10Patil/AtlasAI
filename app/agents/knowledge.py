'''Knowledge agent that talks to MCP rather than database internals.'''

from app.agents.ports import EvidenceBundle, KnowledgeContext
from app.mcp.server import MCPMalformedResponse, MCPToolClient, MCPUnavailable


class KnowledgeAgent:
    def __init__(self, mcp_client: MCPToolClient):
        self.mcp_client = mcp_client

    async def investigate(self, context: KnowledgeContext) -> EvidenceBundle:
        query = ' '.join([context.incident_summary, context.category, *context.symptoms, *context.search_terms])[:500]
        limitations: list[str] = []
        runbooks: list = []
        history: list[dict[str, str]] = []
        try:
            runbook_result = await self.mcp_client.search_runbooks(query, 3)
            if runbook_result.get('status') == 'unavailable':
                limitations.append('Evidence service unavailable')
            elif runbook_result.get('status') == 'no_evidence':
                limitations.append('No runbook evidence found')
            runbooks = runbook_result.get('items', [])[:5]
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
        from app.models.schemas import EvidenceItem
        evidence = [EvidenceItem.model_validate(item) for item in runbooks]
        return EvidenceBundle(
            runbook_evidence=evidence,
            historical_incidents=history,
            retrieval_limitations=list(dict.fromkeys(limitations))[:5],
        )
