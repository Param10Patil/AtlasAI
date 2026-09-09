'''Minimal MCP-compatible JSON-RPC tool server.

The server implements the protocol envelope and tool discovery/call methods
without coupling callers to database rows. A later deployment adapter may
expose the same handler over stdio or HTTP.
'''

import json
from typing import Any

from pydantic import ValidationError

from app.kubernetes.service import KubernetesService, KubernetesUnavailable
from app.mcp.contracts import (
    ExecuteSafeActionInput,
    GetIncidentHistoryInput,
    SearchRunbooksInput,
    VerifyHealthInput,
)
from app.rag.service import RAGService
from app.remediation.service import RemediationToolClient


class MCPUnavailable(RuntimeError):
    '''The tool server could not be reached.'''


class MCPMalformedResponse(RuntimeError):
    '''The tool server returned a response outside the contract.'''


class MCPToolServer:
    def __init__(
        self,
        rag: RAGService,
        remediation_executor: RemediationToolClient | None = None,
        kubernetes: KubernetesService | None = None,
    ):
        self.rag = rag
        self.remediation_executor = remediation_executor
        self.kubernetes = kubernetes

    async def search_runbooks(self, query: str, limit: int = 3) -> dict[str, Any]:
        request = SearchRunbooksInput(query=query, limit=limit)
        result = await self.rag.retrieve(request.query, request.limit)
        return {
            'status': result.status,
            'items': [item.model_dump(mode='json') for item in result.evidence],
            'message': result.message,
            'retrieval_method': result.retrieval_method,
            'best_score': result.best_score,
            'candidate_count': result.candidate_count,
        }

    async def get_incident_history(self, service: str | None, category: str, limit: int = 3) -> dict[str, Any]:
        request = GetIncidentHistoryInput(service=service, category=category, limit=limit)
        rows = await self.rag.history(request.service, request.category, request.limit)
        return {
            'status': 'complete' if rows else 'no_evidence',
            'items': [
                {
                    'id': row.id,
                    'service': row.service,
                    'category': row.category,
                    'summary': row.summary,
                    'resolution': row.resolution,
                    'created_at': row.created_at.isoformat(),
                }
                for row in rows
            ],
        }

    async def execute_safe_action(self, action: str, target: str) -> dict[str, Any]:
        request = ExecuteSafeActionInput(action=action, target=target)
        if self.remediation_executor is None:
            return {'status': 'unavailable', 'message': 'remediation executor is not configured'}
        try:
            result = await self.remediation_executor.execute_safe_action(request.action.value, request.target)
        except Exception:  # noqa: BLE001
            return {'status': 'unavailable', 'message': 'remediation executor is unavailable'}
        return {
            'status': 'executed' if result else 'failed',
            'action': request.action.value,
            'target': request.target,
            'simulated': bool(getattr(self.remediation_executor, 'simulated', False)),
            'message': 'allowlisted action executed in the configured remediation mode' if result else 'allowlisted action was not executed',
        }

    async def verify_health(self, target: str) -> dict[str, Any]:
        request = VerifyHealthInput(target=target)
        if self.remediation_executor is None:
            return {'status': 'unavailable', 'message': 'remediation executor is not configured'}
        try:
            healthy = await self.remediation_executor.verify_health(request.target)
        except Exception:  # noqa: BLE001
            return {'status': 'unavailable', 'message': 'health verifier is unavailable'}
        return {
            'status': 'healthy' if healthy else 'unhealthy',
            'target': request.target,
            'message': 'health check passed' if healthy else 'health check did not pass',
        }

    async def get_service_observations(self, namespace: str, workload: str) -> dict[str, Any]:
        if self.kubernetes is None:
            return {'status': 'offline', 'message': 'Kubernetes observation is not configured'}
        try:
            observation = await self.kubernetes.observe(namespace, workload)
        except KubernetesUnavailable as exc:
            return {'status': 'offline', 'message': str(exc)}
        return {
            'status': observation.health.status.value if observation.connection.value == 'connected' else 'offline',
            'observation': observation.model_dump(mode='json'),
            'message': 'Kubernetes observation retrieved',
        }

    async def get_cluster_health(self, namespace: str, workload: str) -> dict[str, Any]:
        return await self.get_service_observations(namespace, workload)

    async def get_pod_status(self, namespace: str, workload: str) -> dict[str, Any]:
        payload = await self.get_service_observations(namespace, workload)
        observation = payload.get('observation') or {}
        return {**payload, 'items': observation.get('pods', [])}

    async def get_deployment_status(self, namespace: str, workload: str) -> dict[str, Any]:
        payload = await self.get_service_observations(namespace, workload)
        observation = payload.get('observation') or {}
        return {**payload, 'item': observation.get('deployment')}

    async def get_recent_events(self, namespace: str, workload: str) -> dict[str, Any]:
        payload = await self.get_service_observations(namespace, workload)
        observation = payload.get('observation') or {}
        return {**payload, 'items': observation.get('events', [])}

    async def handle(self, message: dict[str, Any]) -> dict[str, Any]:
        request_id = message.get('id')
        method = message.get('method')
        params = message.get('params') or {}
        if method == 'initialize':
            result = {
                'protocolVersion': '2025-06-18',
                'capabilities': {'tools': {}},
                'serverInfo': {'name': 'opspilot-knowledge', 'version': '0.1.0'},
            }
        elif method == 'notifications/initialized':
            return {'jsonrpc': '2.0'}
        elif method == 'tools/list':
            result = {
                'tools': [
                    {'name': 'search_runbooks', 'description': 'Search bounded runbook evidence', 'inputSchema': {'type': 'object'}},
                    {'name': 'get_incident_history', 'description': 'Find bounded historical incidents', 'inputSchema': {'type': 'object'}},
                    {'name': 'get_cluster_health', 'description': 'Read approved namespace workload health', 'inputSchema': {'type': 'object'}},
                    {'name': 'get_service_observations', 'description': 'Read structured workload observations', 'inputSchema': {'type': 'object'}},
                    {'name': 'get_pod_status', 'description': 'Read approved workload pod status', 'inputSchema': {'type': 'object'}},
                    {'name': 'get_deployment_status', 'description': 'Read approved deployment status', 'inputSchema': {'type': 'object'}},
                    {'name': 'get_recent_events', 'description': 'Read recent Kubernetes events for the workload', 'inputSchema': {'type': 'object'}},
                    {'name': 'execute_safe_action', 'description': 'Execute one policy-allowlisted remediation action', 'inputSchema': {'type': 'object'}},
                    {'name': 'verify_health', 'description': 'Verify health after a remediation action', 'inputSchema': {'type': 'object'}},
                ]
            }
        elif method == 'tools/call':
            name = params.get('name')
            arguments = params.get('arguments') or {}
            try:
                if name == 'search_runbooks':
                    payload = await self.search_runbooks(arguments.get('query', ''), arguments.get('limit', 3))
                elif name == 'get_incident_history':
                    payload = await self.get_incident_history(arguments.get('service'), arguments.get('category', ''), arguments.get('limit', 3))
                elif name == 'get_cluster_health':
                    payload = await self.get_cluster_health(arguments.get('namespace', ''), arguments.get('workload', ''))
                elif name == 'get_service_observations':
                    payload = await self.get_service_observations(arguments.get('namespace', ''), arguments.get('workload', ''))
                elif name == 'get_pod_status':
                    payload = await self.get_pod_status(arguments.get('namespace', ''), arguments.get('workload', ''))
                elif name == 'get_deployment_status':
                    payload = await self.get_deployment_status(arguments.get('namespace', ''), arguments.get('workload', ''))
                elif name == 'get_recent_events':
                    payload = await self.get_recent_events(arguments.get('namespace', ''), arguments.get('workload', ''))
                elif name == 'execute_safe_action':
                    payload = await self.execute_safe_action(arguments.get('action', ''), arguments.get('target', ''))
                elif name == 'verify_health':
                    payload = await self.verify_health(arguments.get('target', ''))
                else:
                    return {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': -32602, 'message': 'unknown tool'}}
            except ValidationError:
                return {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': -32602, 'message': 'invalid tool arguments'}}
            result = {
                'content': [{'type': 'text', 'text': json.dumps(payload)}],
                'structuredContent': payload,
                'isError': payload.get('status') == 'unavailable',
            }
        else:
            return {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': -32601, 'message': 'method not found'}}
        return {'jsonrpc': '2.0', 'id': request_id, 'result': result}


class MCPToolClient:
    '''Client used by Knowledge Agent; direct server mode is deterministic.'''

    def __init__(self, server: MCPToolServer | None = None, server_url: str | None = None):
        self.server = server
        self.server_url = server_url
        self.calls: list[str] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(name)
        if self.server is not None:
            response = await self.server.handle({
                'jsonrpc': '2.0',
                'id': len(self.calls),
                'method': 'tools/call',
                'params': {'name': name, 'arguments': arguments},
            })
        elif self.server_url:
            response = await self._http_call(name, arguments)
        else:
            raise MCPUnavailable('MCP server is not configured')
        if 'error' in response:
            raise MCPMalformedResponse('MCP returned a tool error')
        result = response.get('result')
        if not isinstance(result, dict) or 'structuredContent' not in result:
            raise MCPMalformedResponse('MCP response did not contain structured content')
        payload = result['structuredContent']
        if not isinstance(payload, dict) or payload.get('status') not in {'complete', 'no_evidence', 'unavailable', 'executed', 'failed', 'healthy', 'unhealthy', 'degraded', 'offline', 'unknown'}:
            raise MCPMalformedResponse('MCP structured content was invalid')
        return payload

    async def _http_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        import asyncio
        from urllib.request import Request, urlopen

        body = json.dumps({
            'jsonrpc': '2.0',
            'id': len(self.calls),
            'method': 'tools/call',
            'params': {'name': name, 'arguments': arguments},
        }).encode('utf-8')

        def request() -> dict[str, Any]:
            try:
                with urlopen(
                    Request(self.server_url, data=body, headers={'Content-Type': 'application/json'}, method='POST'),
                    timeout=10,
                ) as response:
                    return json.loads(response.read().decode('utf-8'))
            except Exception as exc:
                raise MCPUnavailable('MCP server could not be reached') from exc

        return await asyncio.to_thread(request)

    async def search_runbooks(self, query: str, limit: int = 3) -> dict[str, Any]:
        return await self.call_tool('search_runbooks', {'query': query, 'limit': limit})

    async def get_incident_history(self, service: str | None, category: str, limit: int = 3) -> dict[str, Any]:
        return await self.call_tool('get_incident_history', {'service': service, 'category': category, 'limit': limit})

    async def execute_safe_action(self, action: str, target: str) -> dict[str, Any]:
        return await self.call_tool('execute_safe_action', {'action': action, 'target': target})

    async def verify_health(self, target: str) -> dict[str, Any]:
        return await self.call_tool('verify_health', {'target': target})
