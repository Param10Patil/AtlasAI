'''Validate, reload, and evaluate a saved OpsPilot classifier artifact.'''

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
    common = ('label_map.json', 'training_metadata.json', 'metrics.json', 'artifact_manifest.json')
    missing = [name for name in common if not (adapter / name).is_file()]
    if missing:
        raise ValueError(f'incomplete artifact; missing={missing}')
    label_map = _load_json(adapter / 'label_map.json')
    expected_map = {label: index for index, label in enumerate(LABELS)}
    if label_map.get('labels') != list(LABELS) or label_map.get('label_to_id') != expected_map:
        raise ValueError('artifact label map does not match the six-label runtime contract')
    metadata = _load_json(adapter / 'training_metadata.json')
    if metadata.get('labels') != list(LABELS) or not isinstance(metadata.get('base_model'), str):
        raise ValueError('artifact metadata has no valid base model or label ordering')
    training_config = metadata.get('training_config', {})
    if not isinstance(training_config, dict):
        raise TypeError('artifact training_config must be an object')
    mode = training_config.get('mode', 'lora')
    if mode not in {'lora', 'full'}:
        raise ValueError(f'unsupported artifact mode: {mode!r}')
    if mode == 'lora' or metadata.get('artifact_type') == 'peft_adapter':
        model_files = sorted(path for path in adapter.glob('adapter_model.*') if path.is_file())
        if not (adapter / 'adapter_config.json').is_file() or not model_files:
            raise ValueError('incomplete LoRA artifact; adapter_config.json and adapter_model.* are required')
    else:
        model_files = sorted(path for path in adapter.glob('model.safetensors')) + sorted(path for path in adapter.glob('pytorch_model.bin'))
        if not (adapter / 'config.json').is_file() or not model_files:
            raise ValueError('incomplete full-model artifact; config.json and model weights are required')
    manifest = _load_json(adapter / 'artifact_manifest.json').get('files')
    if not isinstance(manifest, dict):
        raise TypeError('artifact manifest files must be an object')
    for name, checksum in manifest.items():
        path = adapter / name
        if not path.is_file() or not isinstance(checksum, str) or _sha256(path) != checksum:
            raise ValueError(f'artifact manifest checksum mismatch: {name}')
    for model_file in model_files:
        if manifest.get(model_file.name) != _sha256(model_file):
            raise ValueError(f'model checksum missing or mismatched: {model_file.name}')
    model_checksum = _sha256(model_files[0])
    if metadata.get('model_sha256') != model_checksum:
        raise ValueError('artifact metadata model checksum is missing or mismatched')
    if mode == 'lora' and metadata.get('adapter_sha256') != model_checksum:
        raise ValueError('artifact metadata adapter checksum is missing or mismatched')
    if dataset is not None and metadata.get('dataset_sha256') != _sha256(dataset):
        raise ValueError('dataset checksum does not match training metadata')
    metrics = _load_json(adapter / 'metrics.json')
    for key in ('accuracy', 'macro_precision', 'macro_recall', 'macro_f1', 'weighted_f1'):
        value = metrics.get(key)
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError(f'metrics has invalid {key}')
    return {'valid': True, 'metadata': metadata, 'label_map': label_map, 'model_files': [path.name for path in model_files], 'mode': mode}


def _metrics(labels: list[str], predictions: list[str]) -> dict[str, object]:
    if len(labels) != len(predictions):
        raise ValueError('labels and predictions have different lengths')
    matrix = {label: {other: 0 for other in LABELS} for label in LABELS}
    for expected, predicted in zip(labels, predictions, strict=True):
        if expected not in LABELS:
            raise ValueError(f'unknown expected label: {expected}')
        if predicted not in LABELS:
            raise ValueError(f'unknown predicted label: {predicted}')
        matrix[expected][predicted] += 1
    classes: dict[str, dict[str, float | int]] = {}
    precisions: list[float] = []
    recalls: list[float] = []
    f1_values: list[float] = []
    total = len(labels)
    for label in LABELS:
        tp = matrix[label][label]
        fp = sum(matrix[other][label] for other in LABELS if other != label)
        fn = sum(matrix[label][other] for other in LABELS if other != label)
        support = sum(matrix[label].values())
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        classes[label] = {'precision': precision, 'recall': recall, 'f1': f1, 'support': support}
        precisions.append(precision)
        recalls.append(recall)
        f1_values.append(f1)
    return {
        'samples': total,
        'accuracy': sum(a == b for a, b in zip(labels, predictions, strict=True)) / total if total else 0.0,
        'macro_precision': sum(precisions) / len(precisions),
        'macro_recall': sum(recalls) / len(recalls),
        'macro_f1': sum(f1_values) / len(f1_values),
        'weighted_f1': sum(float(item['f1']) * int(item['support']) for item in classes.values()) / total if total else 0.0,
        'classes': classes,
        'samples_per_class': {label: int(classes[label]['support']) for label in LABELS},
        'zero_score_classes': [label for label in LABELS if classes[label]['f1'] == 0.0 or classes[label]['recall'] == 0.0],
        'confusion_matrix': matrix,
    }


def _prediction_label(raw: Any, labels: list[str]) -> tuple[str, float]:
    if isinstance(raw, list) and raw and isinstance(raw[0], list):
        raw = raw[0]
    if isinstance(raw, list):
        raw = raw[0] if raw else {}
    if not isinstance(raw, dict):
        raise TypeError('classifier returned an invalid prediction')
    score = float(raw.get('score', 0.0))
    if not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError('classifier returned a non-finite or out-of-range confidence')
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
        raise RuntimeError('install the pinned training/requirements.txt to evaluate an artifact') from exc
    classifier = pipeline('text-classification', model=str(adapter), top_k=1)
    expected: list[str] = []
    predicted: list[str] = []
    for line_number, line in enumerate(dataset.read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or row.get('label') not in LABELS or not isinstance(row.get('text'), str):
            raise ValueError(f'invalid dataset row at line {line_number}')
        label, _ = _prediction_label(classifier(row['text']), list(LABELS))
        expected.append(row['label'])
        predicted.append(label)
    result = _metrics(expected, predicted)
    result['artifact'] = validated
    return result


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = ArgumentParser(description='Validate and evaluate an OpsPilot classifier artifact')
    parser.add_argument('adapter', type=Path)
    parser.add_argument('--dataset', type=Path, default=root / 'training/dataset/incidents.jsonl')
    args = parser.parse_args()
    if not args.adapter.exists():
        parser.error(f'artifact not found: {args.adapter}')
    if not args.dataset.exists():
        parser.error(f'dataset not found: {args.dataset}')
    try:
        print(json.dumps(evaluate(args.adapter, args.dataset), indent=2))
    except (RuntimeError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
