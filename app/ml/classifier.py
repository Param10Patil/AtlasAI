'''Incident classification with a validated lazy LoRA adapter and fallback.'''

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

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


class AdapterValidationError(ValueError):
    '''The adapter is absent, corrupt, or incompatible with the runtime.'''


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdapterValidationError(f'invalid {path.name}') from exc
    if not isinstance(value, dict):
        raise AdapterValidationError(f'{path.name} must contain an object')
    return value


def validate_adapter(adapter_path: str | Path) -> dict[str, Any]:
    path = Path(adapter_path)
    if not path.is_dir():
        raise AdapterValidationError(f'LoRA adapter directory not found: {path}')
    required = ('adapter_config.json', 'label_map.json', 'training_metadata.json', 'artifact_manifest.json')
    missing = [name for name in required if not (path / name).is_file()]
    model_files = sorted(candidate for candidate in path.glob('adapter_model.*') if candidate.is_file())
    if missing or not model_files:
        raise AdapterValidationError(f'incomplete LoRA artifact (missing={missing}, model_file={bool(model_files)})')
    label_map = _json_object(path / 'label_map.json')
    expected_map = {label: index for index, label in enumerate(LABELS)}
    if label_map.get('labels') != list(LABELS) or label_map.get('label_to_id') != expected_map:
        raise AdapterValidationError('LoRA label map is incompatible with the runtime contract')
    metadata = _json_object(path / 'training_metadata.json')
    if metadata.get('labels') != list(LABELS) or not isinstance(metadata.get('base_model'), str):
        raise AdapterValidationError('LoRA metadata has no valid base model or label ordering')
    model_file = model_files[0]
    checksum = _sha256(model_file)
    if metadata.get('adapter_sha256') != checksum:
        raise AdapterValidationError('LoRA adapter checksum mismatch')
    manifest = _json_object(path / 'artifact_manifest.json').get('files')
    if not isinstance(manifest, dict) or manifest.get(model_file.name) != checksum:
        raise AdapterValidationError('LoRA artifact manifest checksum mismatch')
    config = _json_object(path / 'adapter_config.json')
    if config.get('base_model_name_or_path') and config['base_model_name_or_path'] != metadata['base_model']:
        raise AdapterValidationError('LoRA base model differs between adapter config and metadata')
    return {'path': path, 'labels': list(LABELS), 'label_map': expected_map, 'metadata': metadata, 'model_file': model_file}


class FallbackClassifier:
    '''Transparent multi-signal baseline used when no valid adapter is installed.'''

    keywords: ClassVar[dict[str, set[str]]] = {
        'deployment_failure': {'deploy', 'deployment', 'release', 'rollout', 'image', 'crashloop', 'startup', 'pod', 'container'},
        'database_failure': {'database', 'db', 'postgres', 'postgresql', 'sql', 'connection', 'connect', 'cannot', 'pool', 'deadlock', 'query'},
        'authentication_failure': {'auth', 'login', 'token', 'credential', '401', '403', 'permission', 'unauthorized', 'oauth'},
        'network_failure': {'network', 'dns', 'connectivity', 'route', 'tls', 'socket', 'upstream', 'firewall', 'ingress', 'certificate', 'handshake', 'expiry', 'chain', 'secure'},
        'performance_issue': {'latency', 'slow', 'p95', 'p99', 'performance', 'timeout', 'taking', 'seconds', 'second', 'duration', 'response', 'cpu', 'cache', 'eviction', 'throttle', '429'},
        'availability_issue': {'503', 'unavailable', 'outage', 'downtime', 'down', 'error', 'healthy', 'serving', 'queue', 'backlog', 'worker', 'starvation'},
    }

    def classify(self, description: str) -> Classification:
        tokens = set(re.findall(r'[a-z0-9]+', description.lower()))
        scores = {label: len(tokens & terms) for label, terms in self.keywords.items()}
        best = max(scores, key=scores.get)
        matched = tuple(sorted(tokens & self.keywords[best]))
        if scores[best] == 0:
            return Classification('availability_issue', 0.35, 'rule_fallback', ())
        confidence = min(0.82, 0.45 + (0.09 * scores[best]))
        return Classification(best, round(confidence, 3), 'rule_fallback', matched)


class LoRAClassifier:
    '''Lazy adapter loader. Validation happens before any optional import.'''

    def __init__(self, adapter_path: str):
        self.artifact = validate_adapter(adapter_path)
        self.adapter_path = Path(adapter_path)
        self._pipeline: Any | None = None
        self._fallback = FallbackClassifier()

    def _load(self) -> None:
        try:
            from peft import PeftModel
            from transformers import (
                AutoModelForSequenceClassification,
                AutoTokenizer,
                pipeline,
            )
            labels = {index: label for index, label in enumerate(LABELS)}
            label_to_id = {label: index for index, label in enumerate(LABELS)}
            base_model = self.artifact['metadata']['base_model']
            base = AutoModelForSequenceClassification.from_pretrained(
                base_model,
                num_labels=len(LABELS),
                id2label=labels,
                label2id=label_to_id,
            )
            # A PEFT directory is an adapter, not a complete Transformers
            # model. Attach it to the recorded base model before inference;
            # passing the directory directly silently reinitializes the
            # classifier head and produces misleading predictions.
            model = PeftModel.from_pretrained(base, str(self.adapter_path)).merge_and_unload()
            tokenizer = AutoTokenizer.from_pretrained(str(self.adapter_path), use_fast=False)
            self._pipeline = pipeline('text-classification', model=model, tokenizer=tokenizer, top_k=1)
        except ImportError as exc:
            raise RuntimeError('transformers and peft are required for LoRA inference') from exc

    def classify(self, description: str) -> Classification:
        try:
            if self._pipeline is None:
                self._load()
            raw = self._pipeline(description)
            if isinstance(raw, list) and raw and isinstance(raw[0], list):
                raw = raw[0]
            if isinstance(raw, list):
                raw = raw[0] if raw else {}
            if not isinstance(raw, dict):
                raise TypeError('LoRA pipeline returned an invalid prediction')
            score = float(raw.get('score', 0.0))
            if not math.isfinite(score) or not 0 <= score <= 1:
                raise RuntimeError('LoRA pipeline returned a non-finite confidence')
            label = str(raw.get('label', '')).lower()
            if label.startswith('label_') and label[6:].isdigit():
                index = int(label[6:])
                label = LABELS[index] if 0 <= index < len(LABELS) else ''
            if label not in LABELS:
                raise RuntimeError('LoRA pipeline returned an unknown label')
            return Classification(label, score, 'lora', ())
        except (OSError, RuntimeError, ValueError, TypeError, IndexError):
            return self._fallback.classify(description)


def build_classifier(adapter_path: str | None) -> FallbackClassifier | LoRAClassifier:
    if adapter_path:
        try:
            return LoRAClassifier(adapter_path)
        except (AdapterValidationError, OSError, RuntimeError):
            pass
    return FallbackClassifier()
