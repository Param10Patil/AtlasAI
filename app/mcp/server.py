'''Minimal MCP-compatible JSON-RPC tool server.

The server implements the protocol envelope and tool discovery/call methods
without coupling callers to database rows. A later deployment adapter may
expose the same handler over stdio or HTTP.
'''

import json
from typing import Any

from app.mcp.contracts import GetIncidentHistoryInput, SearchRunbooksInput
from app.rag.service import RAGService


class MCPUnavailable(RuntimeError):
    '''The tool server could not be reached.'''


class MCPMalformedResponse(RuntimeError):
    '''The tool server returned a response outside the contract.'''


class MCPToolServer:
    def __init__(self, rag: RAGService):
        self.rag = rag

    async def search_runbooks(self, query: str, limit: int = 3) -> dict[str, Any]:
        request = SearchRunbooksInput(query=query, limit=limit)
        result = await self.rag.retrieve(request.query, request.limit)
        return {
            'status': result.status,
            'items': [item.model_dump(mode='json') for item in result.evidence],
            'message': result.message,
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
                ]
            }
        elif method == 'tools/call':
            name = params.get('name')
            arguments = params.get('arguments') or {}
            if name == 'search_runbooks':
                payload = await self.search_runbooks(arguments.get('query', ''), arguments.get('limit', 3))
            elif name == 'get_incident_history':
                payload = await self.get_incident_history(arguments.get('service'), arguments.get('category', ''), arguments.get('limit', 3))
            else:
                return {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': -32602, 'message': 'unknown tool'}}
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
        if not isinstance(payload, dict) or payload.get('status') not in {'complete', 'no_evidence', 'unavailable'}:
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
