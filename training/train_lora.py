'''Reproducible, optional PEFT/LoRA incident classifier training.

Training is deliberately isolated from the API runtime.  The command writes a
portable adapter directory containing its label map, dataset checksum,
validation metrics, and a manifest that can be checked before inference.
'''

from __future__ import annotations

import hashlib
import json
import random
from argparse import ArgumentParser
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LABELS = (
    'deployment_failure',
    'database_failure',
    'authentication_failure',
    'network_failure',
    'performance_issue',
    'availability_issue',
)
DATASET_VERSION = 'incidents-v1.0.0'
ARTIFACT_SCHEMA_VERSION = 'opspilot-lora/v1'


def _rows(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line_number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f'invalid JSON on dataset line {line_number}') from exc
        if not isinstance(row, dict) or not isinstance(row.get('text'), str) or not isinstance(row.get('label'), str):
            raise TypeError(f'dataset line {line_number} needs string text and label')
        if row['label'] not in LABELS or not row['text'].strip():
            raise ValueError(f'dataset line {line_number} has an unknown label or empty text')
        rows.append({'text': row['text'].strip(), 'label': row['label']})
    if len(rows) < 30:
        raise ValueError('dataset must contain at least 30 examples for a meaningful split')
    if len({row['text'] for row in rows}) != len(rows):
        raise ValueError('dataset contains duplicate text; remove train/validation leakage before training')
    counts = {label: sum(row['label'] == label for row in rows) for label in LABELS}
    if any(count < 2 for count in counts.values()):
        raise ValueError(f'each label needs at least two examples: {counts}')
    return rows


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _stratified_split(rows: list[dict[str, str]], validation_fraction: float, seed: int) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    grouped = {label: [row for row in rows if row['label'] == label] for label in LABELS}
    rng = random.Random(seed)
    train: list[dict[str, str]] = []
    validation: list[dict[str, str]] = []
    for label in LABELS:
        group = grouped[label][:]
        rng.shuffle(group)
        count = min(len(group) - 1, max(1, round(len(group) * validation_fraction)))
        validation.extend(group[:count])
        train.extend(group[count:])
    rng.shuffle(train)
    rng.shuffle(validation)
    return train, validation


def _target_modules(model: Any) -> list[str]:
    names = {name.rsplit('.', 1)[-1] for name, _ in model.named_modules()}
    candidates = ['query', 'value', 'q_proj', 'v_proj', 'c_attn']
    selected = [name for name in candidates if name in names]
    if not selected:
        raise RuntimeError('could not discover LoRA attention target modules in the selected base model')
    return selected


def _metrics(labels: Iterable[int], predictions: Iterable[int]) -> dict[str, Any]:
    actual = list(labels)
    predicted = list(predictions)
    matrix = [[0 for _ in LABELS] for _ in LABELS]
    for expected, received in zip(actual, predicted, strict=True):
        if not 0 <= received < len(LABELS):
            raise ValueError(f'model returned an invalid class index: {received}')
        matrix[expected][received] += 1
    classes: dict[str, dict[str, float]] = {}
    f1_values: list[float] = []
    for index, label in enumerate(LABELS):
        tp = matrix[index][index]
        fp = sum(matrix[row][index] for row in range(len(LABELS)) if row != index)
        fn = sum(matrix[index][column] for column in range(len(LABELS)) if column != index)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        classes[label] = {'precision': precision, 'recall': recall, 'f1': f1}
        f1_values.append(f1)
    return {
        'samples': len(actual),
        'accuracy': sum(expected == received for expected, received in zip(actual, predicted, strict=True)) / len(actual) if actual else 0.0,
        'macro_f1': sum(f1_values) / len(f1_values),
        'classes': classes,
        'confusion_matrix': {'labels': list(LABELS), 'values': matrix},
    }


def _pipeline_predictions(trainer: Any, encoded: Any) -> tuple[list[int], list[int]]:
    import numpy as np

    output = trainer.predict(encoded)
    logits = output.predictions[0] if isinstance(output.predictions, tuple) else output.predictions
    if not np.isfinite(logits).all():
        raise RuntimeError('model produced non-finite validation logits')
    predictions = np.asarray(logits).argmax(axis=-1).tolist()
    labels = np.asarray(output.label_ids).tolist()
    return [int(value) for value in labels], [int(value) for value in predictions]


def _manifest(output_dir: Path) -> dict[str, str]:
    return {
        str(path.relative_to(output_dir)).replace('\\', '/'): _sha256(path)
        for path in sorted(output_dir.rglob('*'))
        if path.is_file() and path.name != 'artifact_manifest.json'
    }


def train(
    dataset_path: Path,
    output_dir: Path,
    base_model: str,
    epochs: float,
    batch_size: int,
    learning_rate: float,
    tracking_uri: str | None,
    seed: int = 42,
    validation_fraction: float = 0.2,
    max_length: int = 128,
) -> dict[str, Any]:
    try:
        from datasets import Dataset
        from peft import LoraConfig, TaskType, get_peft_model
        from transformers import (
            AutoModelForSequenceClassification,
            AutoTokenizer,
            Trainer,
            TrainingArguments,
            set_seed,
        )
    except ImportError as exc:
        raise RuntimeError('install the pinned training/requirements.txt to run the LoRA experiment') from exc
    if not 8 <= max_length <= 512:
        raise ValueError('max_length must be between 8 and 512')
    if epochs <= 0 or batch_size <= 0 or learning_rate <= 0:
        raise ValueError('epochs, batch_size, and learning_rate must be positive')
    rows = _rows(dataset_path)
    train_rows, validation_rows = _stratified_split(rows, validation_fraction, seed)
    label_to_id = {label: index for index, label in enumerate(LABELS)}
    set_seed(seed)
    tokenizer = AutoTokenizer.from_pretrained(base_model)
    train_dataset = Dataset.from_list([{'text': row['text'], 'label': label_to_id[row['label']]} for row in train_rows])
    validation_dataset = Dataset.from_list([{'text': row['text'], 'label': label_to_id[row['label']]} for row in validation_rows])

    def tokenize(batch: dict[str, list[str]]) -> dict[str, Any]:
        return tokenizer(batch['text'], truncation=True, padding='max_length', max_length=max_length)

    train_encoded = train_dataset.map(tokenize, batched=True, remove_columns=['text'])
    validation_encoded = validation_dataset.map(tokenize, batched=True, remove_columns=['text'])
    model = AutoModelForSequenceClassification.from_pretrained(
        base_model,
        num_labels=len(LABELS),
        id2label={index: label for index, label in enumerate(LABELS)},
        label2id=label_to_id,
    )
    targets = _target_modules(model)
    adapter = get_peft_model(model, LoraConfig(
        task_type=TaskType.SEQ_CLS,
        r=4,
        lora_alpha=8,
        lora_dropout=0.1,
        target_modules=targets,
        modules_to_save=['classifier'] if hasattr(model, 'classifier') else None,
    ))
    args = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        learning_rate=learning_rate,
        seed=seed,
        data_seed=seed,
        report_to=[],
        save_strategy='no',
        logging_steps=1,
        remove_unused_columns=True,
    )
    trainer = Trainer(model=adapter, args=args, train_dataset=train_encoded)
    trainer.train()
    actual, predicted = _pipeline_predictions(trainer, validation_encoded)
    metrics = _metrics(actual, predicted)
    output_dir.mkdir(parents=True, exist_ok=True)
    adapter.save_pretrained(output_dir)
    tokenizer.save_pretrained(output_dir)
    dataset_checksum = _sha256(dataset_path)
    adapter_files = [path for path in output_dir.glob('adapter_model.*') if path.is_file()]
    if not adapter_files:
        raise RuntimeError('PEFT did not write an adapter_model file')
    metadata: dict[str, Any] = {
        'artifact_schema_version': ARTIFACT_SCHEMA_VERSION,
        'dataset_version': DATASET_VERSION,
        'dataset_sha256': dataset_checksum,
        'base_model': base_model,
        'labels': list(LABELS),
        'label_map_version': '1.0.0',
        'rows': len(rows),
        'train_rows': len(train_rows),
        'validation_rows': len(validation_rows),
        'class_counts': {label: sum(row['label'] == label for row in rows) for label in LABELS},
        'seed': seed,
        'validation_fraction': validation_fraction,
        'max_length': max_length,
        'epochs': epochs,
        'batch_size': batch_size,
        'learning_rate': learning_rate,
        'lora': {'r': 4, 'alpha': 8, 'dropout': 0.1, 'target_modules': targets},
        'adapter_sha256': _sha256(adapter_files[0]),
        'created_at_utc': datetime.now(UTC).isoformat(),
    }
    (output_dir / 'label_map.json').write_text(json.dumps({'version': '1.0.0', 'labels': list(LABELS), 'label_to_id': label_to_id}, indent=2) + '\n', encoding='utf-8')
    (output_dir / 'metrics.json').write_text(json.dumps(metrics, indent=2) + '\n', encoding='utf-8')
    (output_dir / 'training_metadata.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    manifest = _manifest(output_dir)
    (output_dir / 'artifact_manifest.json').write_text(json.dumps({'schema_version': ARTIFACT_SCHEMA_VERSION, 'files': manifest}, indent=2) + '\n', encoding='utf-8')
    result: dict[str, Any] = {'metadata': metadata, 'metrics': metrics, 'artifact_dir': str(output_dir), 'mlflow': 'disabled'}
    if tracking_uri:
        try:
            import mlflow
            mlflow.set_tracking_uri(tracking_uri)
            with mlflow.start_run():
                mlflow.log_params({key: value for key, value in metadata.items() if key not in {'labels', 'lora', 'class_counts'}})
                mlflow.log_metrics({'validation_accuracy': metrics['accuracy'], 'validation_macro_f1': metrics['macro_f1']})
                mlflow.log_artifacts(str(output_dir), artifact_path='adapter')
            result['mlflow'] = 'logged'
        except Exception as exc:  # noqa: BLE001 - optional tracking must never discard local evidence
            result['mlflow'] = f'warning: unavailable ({type(exc).__name__})'
    return result


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = ArgumentParser(description='Train the optional OpsPilot LoRA classifier')
    parser.add_argument('--dataset', type=Path, default=root / 'training/dataset/incidents.jsonl')
    parser.add_argument('--output-dir', type=Path, default=root / 'training/artifacts/opspilot-lora')
    parser.add_argument('--base-model', default='prajjwal1/bert-tiny')
    parser.add_argument('--epochs', type=float, default=3.0)
    parser.add_argument('--batch-size', type=int, default=8)
    parser.add_argument('--learning-rate', type=float, default=2e-4)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--validation-fraction', type=float, default=0.2)
    parser.add_argument('--max-length', type=int, default=128)
    parser.add_argument('--tracking-uri', default=None)
    args = parser.parse_args()
    if not args.dataset.exists():
        parser.error(f'dataset not found: {args.dataset}')
    try:
        print(json.dumps(train(args.dataset, args.output_dir, args.base_model, args.epochs, args.batch_size, args.learning_rate, args.tracking_uri, args.seed, args.validation_fraction, args.max_length), indent=2))
    except (RuntimeError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
