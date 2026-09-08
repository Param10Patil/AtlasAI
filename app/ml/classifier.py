'''Incident category classifier with an honest deterministic fallback.'''

import re
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

LABELS = (
    'deployment_failure',
    'database_failure',
    'authentication_failure',
    'network_failure',
    'performance_issue',
    'availability_issue',
)


@dataclass(frozen=True)
class Classification:
    category: str
    confidence: float
    source: str
    matched_terms: tuple[str, ...]


class FallbackClassifier:
    '''Small transparent keyword baseline used when no adapter is available.'''

    keywords: ClassVar[dict[str, set[str]]] = {
        'deployment_failure': {'deploy', 'deployment', 'release', 'rollout', 'image', 'crashloop'},
        'database_failure': {'database', 'db', 'postgres', 'sql', 'connection', 'pool', 'deadlock'},
        'authentication_failure': {'auth', 'login', 'token', 'credential', '401', '403', 'permission'},
        'network_failure': {'network', 'dns', 'connectivity', 'route', 'tls', 'socket'},
        'performance_issue': {'latency', 'slow', 'p95', 'p99', 'performance', 'timeout'},
        'availability_issue': {'503', 'unavailable', 'outage', 'downtime', 'down', 'error'},
    }

    def classify(self, description: str) -> Classification:
        tokens = set(re.findall(r'[a-z0-9]+', description.lower()))
        scores = {
            label: len(tokens & terms)
            for label, terms in self.keywords.items()
        }
        best = max(scores, key=scores.get)
        matched = tuple(sorted(tokens & self.keywords[best]))
        if scores[best] == 0:
            return Classification('availability_issue', 0.35, 'fallback', ())
        confidence = min(0.82, 0.45 + (0.09 * scores[best]))
        return Classification(best, round(confidence, 3), 'fallback', matched)


class LoRAClassifier:
    '''Adapter seam. Loading is explicit and failure never masquerades as LoRA.'''

    def __init__(self, adapter_path: str):
        self.adapter_path = Path(adapter_path)
        if not self.adapter_path.exists():
            raise FileNotFoundError(f'LoRA adapter not found: {adapter_path}')
        self._pipeline = None

    def _load(self) -> None:
        try:
            from transformers import pipeline
        except ImportError as exc:
            raise RuntimeError('transformers is not installed for LoRA inference') from exc
        self._pipeline = pipeline('text-classification', model=str(self.adapter_path), top_k=1)

    def classify(self, description: str) -> Classification:
        if self._pipeline is None:
            self._load()
        result = self._pipeline(description)[0][0]
        label = str(result.get('label', '')).lower()
        category = label if label in LABELS else 'availability_issue'
        return Classification(category, float(result.get('score', 0.0)), 'lora', ())


def build_classifier(adapter_path: str | None) -> FallbackClassifier | LoRAClassifier:
    if adapter_path:
        try:
            return LoRAClassifier(adapter_path)
        except (FileNotFoundError, RuntimeError):
            pass
    return FallbackClassifier()
