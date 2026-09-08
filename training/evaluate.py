'''Validate and evaluate a previously exported OpsPilot LoRA adapter.'''

from __future__ import annotations

import hashlib
import json
import math
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

LABELS = ('deployment_failure', 'database_failure', 'authentication_failure', 'network_failure', 'performance_issue', 'availability_issue')


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'invalid artifact file: {path.name}') from exc
    if not isinstance(value, dict):
        raise TypeError(f'artifact file must contain an object: {path.name}')
    return value


def validate_artifact(adapter: Path, dataset: Path | None = None) -> dict[str, Any]:
    required = ('adapter_config.json', 'label_map.json', 'training_metadata.json', 'metrics.json', 'artifact_manifest.json')
    missing = [name for name in required if not (adapter / name).is_file()]
    model_files = [path for path in adapter.glob('adapter_model.*') if path.is_file()]
    if missing or not model_files:
        raise ValueError(f'incomplete adapter artifact; missing={missing}, model_file={bool(model_files)}')
    label_map = _load_json(adapter / 'label_map.json')
    if label_map.get('labels') != list(LABELS) or label_map.get('label_to_id') != {label: index for index, label in enumerate(LABELS)}:
        raise ValueError('adapter label map does not match the six-label runtime contract')
    metadata = _load_json(adapter / 'training_metadata.json')
    if metadata.get('labels') != list(LABELS) or not metadata.get('base_model'):
        raise ValueError('adapter metadata has no valid base model or label ordering')
    adapter_checksum = metadata.get('adapter_sha256')
    actual_checksum = _sha256(model_files[0])
    if adapter_checksum != actual_checksum:
        raise ValueError('adapter checksum mismatch')
    manifest = _load_json(adapter / 'artifact_manifest.json').get('files', {})
    if manifest.get(model_files[0].name) != actual_checksum:
        raise ValueError('artifact manifest checksum mismatch')
    if dataset is not None and metadata.get('dataset_sha256') != _sha256(dataset):
        raise ValueError('dataset checksum does not match the training metadata')
    return {'metadata': metadata, 'label_map': label_map, 'model_file': model_files[0].name}


def _metrics(labels: list[str], predictions: list[str]) -> dict[str, object]:
    classes: dict[str, dict[str, float]] = {}
    matrix = {label: {other: 0 for other in LABELS} for label in LABELS}
    for expected, predicted in zip(labels, predictions, strict=True):
        if predicted not in LABELS:
            predicted = 'availability_issue'
        matrix[expected][predicted] += 1
    f1_values: list[float] = []
    for label in LABELS:
        tp = matrix[label][label]
        fp = sum(matrix[other][label] for other in LABELS if other != label)
        fn = sum(matrix[label][other] for other in LABELS if other != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        classes[label] = {'precision': precision, 'recall': recall, 'f1': f1}
        f1_values.append(f1)
    return {'samples': len(labels), 'accuracy': sum(a == b for a, b in zip(labels, predictions, strict=True)) / len(labels) if labels else 0.0, 'macro_f1': sum(f1_values) / len(f1_values), 'classes': classes, 'confusion_matrix': matrix}


def _prediction_label(raw: Any, labels: list[str]) -> tuple[str, float]:
    if isinstance(raw, list) and raw and isinstance(raw[0], list):
        raw = raw[0]
    if isinstance(raw, list):
        raw = raw[0] if raw else {}
    if not isinstance(raw, dict):
        raise TypeError('classifier returned an invalid prediction')
    score = float(raw.get('score', 0.0))
    if not math.isfinite(score):
        raise ValueError('classifier returned a non-finite confidence')
    label = str(raw.get('label', '')).lower()
    if label.startswith('label_') and label[6:].isdigit():
        index = int(label[6:])
        label = labels[index] if 0 <= index < len(labels) else ''
    return label, score


def evaluate(adapter: Path, dataset: Path) -> dict[str, object]:
    validated = validate_artifact(adapter, dataset)
    try:
        from transformers import pipeline
    except ImportError as exc:
        raise RuntimeError('install the pinned training/requirements.txt to evaluate an adapter') from exc
    classifier = pipeline('text-classification', model=str(adapter), top_k=1)
    rows = [json.loads(line) for line in dataset.read_text(encoding='utf-8').splitlines() if line.strip()]
    expected: list[str] = []
    predicted: list[str] = []
    for row in rows:
        label, _ = _prediction_label(classifier(row['text']), list(LABELS))
        expected.append(row['label'])
        predicted.append(label)
    result = _metrics(expected, predicted)
    result['artifact'] = validated
    return result


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = ArgumentParser(description='Validate and evaluate an OpsPilot LoRA classifier')
    parser.add_argument('adapter', type=Path)
    parser.add_argument('--dataset', type=Path, default=root / 'training/dataset/incidents.jsonl')
    args = parser.parse_args()
    if not args.adapter.exists():
        parser.error(f'adapter not found: {args.adapter}')
    if not args.dataset.exists():
        parser.error(f'dataset not found: {args.dataset}')
    try:
        print(json.dumps(evaluate(args.adapter, args.dataset), indent=2))
    except (RuntimeError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
