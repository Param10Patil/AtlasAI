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
        observation = context.observation
        evidence_ids = evidence_ids[:5]
        if observation and observation.deployment and not observation.deployment.rollout_complete:
            primary = 'Rollback the approved deployment after confirming the previous revision and rollout evidence.'
            secondary = 'Restart an affected workload pod and re-check readiness if rollback is not appropriate.'
        elif observation and any(p.waiting_reason == 'CrashLoopBackOff' or p.restarts > 0 for p in observation.pods):
            primary = 'Restart the affected workload pod after confirming the restart is within the safety policy.'
            secondary = 'Rollback the approved deployment if the pod remains unhealthy after restart.'
        elif observation and observation.deployment and observation.deployment.available_replicas < observation.deployment.desired_replicas:
            primary = 'Scale the approved deployment back to one replica and verify readiness.'
            secondary = 'Restart an affected workload pod if the replica remains unavailable.'
        elif category == 'database_failure':
            primary = 'Inspect connection-pool usage and recent database changes before applying a reviewed configuration fix.'
            secondary = 'Clear only the known-safe temporary condition after confirming the dependency state.'
        elif category == 'authentication_failure':
            primary = 'Verify token audience, key rotation, and policy configuration without exposing credentials.'
            secondary = 'Clear the known-safe temporary condition only after the identity configuration is corrected.'
        elif category == 'network_failure':
            primary = 'Check service discovery, TLS identity, and network policy before changing an endpoint.'
            secondary = 'Clear the known-safe temporary condition after connectivity is verified.'
        else:
            primary = 'Compare endpoint and dependency timings, then apply a reviewed change to the measured bottleneck.'
            secondary = 'Continue observation and escalate if the health policy remains violated.'
        return {
            'severity': context.triage.severity.value,
            'title': f'{context.triage.service or "Service"} {category.replace("_", " ")}',
            'likely_causes': causes[:5],
            'recommended_actions': [
                {'rank': 1, 'text': primary, 'requires_confirmation': True, 'evidence_ids': evidence_ids},
                {'rank': 2, 'text': secondary, 'requires_confirmation': True, 'evidence_ids': evidence_ids},
                {'rank': 3, 'text': 'Continue bounded observation and escalate if verification does not pass.', 'requires_confirmation': True, 'evidence_ids': evidence_ids},
            ],
            'confidence': min(context.triage.confidence, 0.82 if evidence_ids else 0.58),
            'evidence_ids': evidence_ids,
        }
