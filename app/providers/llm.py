'''Replaceable structured-output LLM providers.'''

import asyncio
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.agents.ports import ResolutionContext


class LLMProviderError(RuntimeError):
    '''Safe provider failure without exposing payloads or credentials.'''


class HTTPLLMProvider:
    def __init__(self, base_url: str, api_key: str, model: str, timeout_seconds: float = 20):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def generate(self, prompt: str, *, timeout_seconds: float | None = None) -> str:
        return await asyncio.wait_for(
            asyncio.to_thread(self._request, prompt),
            timeout=timeout_seconds or self.timeout_seconds,
        )

    def _request(self, prompt: str) -> str:
        body = json.dumps({
            'model': self.model,
            'temperature': 0,
            'messages': [
                {'role': 'system', 'content': 'Return only valid JSON matching the requested schema.'},
                {'role': 'user', 'content': prompt},
            ],
        }).encode('utf-8')
        request = Request(
            f'{self.base_url}/chat/completions',
            data=body,
            headers={'Authorization': f'Bearer {self.api_key}', 'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode('utf-8'))
        except HTTPError as exc:
            if exc.code == 429:
                raise LLMProviderError('provider rate limit') from exc
            if 400 <= exc.code < 500:
                raise LLMProviderError('provider authentication or request error') from exc
            raise LLMProviderError('provider unavailable') from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise LLMProviderError('provider unavailable') from exc
        try:
            content = data['choices'][0]['message']['content']
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError('provider returned malformed output') from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMProviderError('provider returned empty output')
        return content


class RuleBasedLLMProvider:
    '''Deterministic provider for local use and credential-free tests.'''

    name = 'deterministic-fallback'

    async def generate(self, prompt: str, *, timeout_seconds: float | None = None) -> str:
        return prompt

    async def generate_resolution(self, context: ResolutionContext) -> dict[str, Any]:
        category = context.triage.category
        causes = context.triage.likely_causes or ['The available evidence is insufficient to isolate one cause']
        evidence_ids = [item.id for item in context.evidence.runbook_evidence]
        if category in {'deployment_failure', 'availability_issue'}:
            recommendation = 'Check deployment health and consider rolling back to the previous stable version after operator confirmation.'
        elif category == 'database_failure':
            recommendation = 'Inspect connection-pool usage and recent database changes before applying a reviewed configuration fix.'
        elif category == 'authentication_failure':
            recommendation = 'Verify token audience, key rotation, and policy configuration without exposing credentials.'
        elif category == 'network_failure':
            recommendation = 'Check service discovery, TLS identity, and network policy before changing an endpoint.'
        else:
            recommendation = 'Compare endpoint and dependency timings, then apply a reviewed change to the measured bottleneck.'
        return {
            'severity': context.triage.severity.value,
            'title': f'{context.triage.service or "Service"} {category.replace("_", " ")}',
            'likely_causes': causes[:5],
            'recommended_actions': [{'text': recommendation, 'requires_confirmation': True}],
            'confidence': min(context.triage.confidence, 0.82 if evidence_ids else 0.58),
            'evidence_ids': evidence_ids,
        }
