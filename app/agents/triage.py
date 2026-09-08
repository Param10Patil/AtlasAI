'''Triage agent implementation using only incident text and classifier output.'''

import re

from app.agents.ports import TriageContext, TriageResult
from app.ml.classifier import Classification, FallbackClassifier
from app.models.schemas import Severity


class TriageAgent:
    def __init__(self, classifier: object | None = None):
        self.classifier = classifier or FallbackClassifier()

    async def triage(self, context: TriageContext) -> TriageResult:
        classification: Classification = self.classifier.classify(context.incident_description)
        description = context.incident_description.strip()
        service = self._service(description)
        symptoms = self._symptoms(description)
        causes = self._causes(classification.category)
        severity = self._severity(classification.category, description)
        limitations = []
        if classification.source != 'lora':
            limitations.append('LoRA adapter unavailable; deterministic fallback classifier used')
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
        )

    @staticmethod
    def _service(description: str) -> str | None:
        match = re.search(r'\\b(payment|checkout|orders?|identity|auth|api|database)\\b', description.lower())
        return match.group(1) if match else None

    @staticmethod
    def _symptoms(description: str) -> list[str]:
        candidates = []
        lower = description.lower()
        for phrase in ('503', 'timeout', 'latency', 'crash', 'connection', '401', '403', 'unavailable', 'error'):
            if phrase in lower:
                candidates.append(phrase)
        return candidates[:8]

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
    def _severity(category: str, description: str) -> Severity:
        lower = description.lower()
        if '503' in lower or category == 'availability_issue':
            return Severity.HIGH
        if category in {'deployment_failure', 'database_failure', 'network_failure'}:
            return Severity.MEDIUM
        return Severity.LOW
