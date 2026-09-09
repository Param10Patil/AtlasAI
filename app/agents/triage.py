'''Triage agent implementation using only incident text and classifier output.'''

import re

from app.agents.ports import TriageContext, TriageResult
from app.kubernetes.contracts import ClusterObservation
from app.ml.classifier import Classification, FallbackClassifier
from app.models.schemas import Severity


class TriageAgent:
    def __init__(self, classifier: object | None = None):
        self.classifier = classifier or FallbackClassifier()

    async def triage(self, context: TriageContext) -> TriageResult:
        observation = context.observation
        classifier_input = context.incident_description
        if observation and observation.connection.value == 'connected':
            classifier_input = f'{classifier_input} {observation.signal_text}'
        classification: Classification = self.classifier.classify(classifier_input[:4000])
        description = context.incident_description.strip()
        service = observation.workload if observation and observation.connection.value == 'connected' else self._service(description)
        symptoms = self._symptoms(description, observation)
        causes = self._causes(classification.category)
        severity = self._severity(classification.category, description, observation)
        limitations = []
        if classification.source != 'lora':
            limitations.append('LoRA adapter unavailable; deterministic fallback classifier used')
        if observation and observation.connection.value != 'connected':
            limitations.append('Kubernetes observation unavailable')
        if observation and observation.health.reasons:
            causes = [*observation.health.reasons[:2], *causes][:5]
        return TriageResult(
            incident_summary=' '.join(description.split())[:500],
            service=service,
            category=classification.category,
            severity=severity,
            symptoms=symptoms,
            likely_causes=causes,
            search_terms=list(dict.fromkeys([classification.category.replace('_', ' '), *classification.matched_terms]))[:10],
            confidence=classification.confidence,
            classifier_source=classification.source,
            limitations=limitations,
            observation_ids=[observation.observation_id] if observation else [],
        )

    @staticmethod
    def _service(description: str) -> str | None:
        match = re.search(r'\b(payment|checkout|orders?|identity|auth|api|database)\b', description.lower())
        return match.group(1) if match else None

    @staticmethod
    def _symptoms(description: str, observation: ClusterObservation | None = None) -> list[str]:
        candidates = []
        lower = description.lower()
        for phrase in ('503', 'timeout', 'latency', 'crash', 'connection', '401', '403', 'unavailable', 'error'):
            if phrase in lower:
                candidates.append(phrase)
        if observation and getattr(observation, 'health', None):
            candidates.extend(getattr(observation.health, 'reasons', [])[:4])
        return list(dict.fromkeys(candidates))[:8]

    @staticmethod
    def _causes(category: str) -> list[str]:
        return {
            'deployment_failure': ['A release or runtime configuration may have failed readiness checks'],
            'database_failure': ['Connection-pool exhaustion or a database dependency failure'],
            'authentication_failure': ['Identity configuration, token validation, or key rotation mismatch'],
            'network_failure': ['Service discovery, TLS, or network policy connectivity problem'],
            'performance_issue': ['A regression or saturated dependency is increasing tail latency'],
            'availability_issue': ['A deployment regression or unavailable dependency is returning errors'],
        }.get(category, ['The available signals do not isolate a likely cause'])

    @staticmethod
    def _severity(category: str, description: str, observation: ClusterObservation | None = None) -> Severity:
        lower = description.lower()
        if observation and observation.health.status.value == 'degraded' and any('available replicas 0/' in reason for reason in observation.health.reasons):
            return Severity.HIGH
        if '503' in lower or category == 'availability_issue':
            return Severity.HIGH
        if category in {'deployment_failure', 'database_failure', 'network_failure'}:
            return Severity.MEDIUM
        return Severity.LOW
