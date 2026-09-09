'''Reproducible LoRA/full fine-tuning experiments for the incident classifier.'''

from __future__ import annotations

import hashlib
import json
import random
import shutil
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
ARTIFACT_SCHEMA_VERSION = 'opspilot-training/v2'
DEFAULT_BASE_MODEL = 'prajjwal1/bert-tiny'


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _load_rows(path: Path) -> tuple[list[dict[str, str]], dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f'dataset not found: {path}')
    rows: list[dict[str, str]] = []
    problems: list[str] = []
    duplicate_text_lines: list[int] = []
    seen_text: dict[str, int] = {}
    seen_rows: dict[str, int] = {}
    for line_number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f'line {line_number}: invalid JSON')
            continue
        if not isinstance(value, dict):
            problems.append(f'line {line_number}: row must be an object')
            continue
        missing = {'text', 'label'} - set(value)
        if missing:
            problems.append(f'line {line_number}: missing columns {sorted(missing)}')
            continue
        text = value.get('text')
        label = value.get('label')
        if not isinstance(text, str) or not isinstance(label, str):
            problems.append(f'line {line_number}: text and label must be strings')
            continue
        text = text.strip()
        label = label.strip()
        if not text:
            problems.append(f'line {line_number}: empty text')
            continue
        if label not in LABELS:
            problems.append(f'line {line_number}: invalid label {label!r}')
        if len(text) < 12:
            problems.append(f'line {line_number}: very-short text ({len(text)} characters)')
        if text in seen_text:
            duplicate_text_lines.append(line_number)
            problems.append(f'line {line_number}: duplicate text from line {seen_text[text]}')
        seen_text.setdefault(text, line_number)
        row_key = json.dumps({'text': text, 'label': label}, sort_keys=True)
        if row_key in seen_rows:
            problems.append(f'line {line_number}: duplicate row from line {seen_rows[row_key]}')
        seen_rows.setdefault(row_key, line_number)
        rows.append({'text': text, 'label': label})
    counts = {label: sum(row['label'] == label for row in rows) for label in LABELS}
    if len(rows) < 30:
        problems.append(f'dataset has {len(rows)} rows; at least 30 are required')
    missing_labels = [label for label, count in counts.items() if count < 2]
    if missing_labels:
        problems.append(f'labels need at least two rows: {missing_labels}')
    diagnostics = {
        'path': str(path),
        'dataset_sha256': _sha256(path),
        'rows': len(rows),
        'class_counts': counts,
        'duplicate_text_lines': duplicate_text_lines,
        'problems': problems,
    }
    if problems:
        raise ValueError('dataset validation failed: ' + json.dumps(diagnostics, sort_keys=True))
    return rows, diagnostics


def _seed_everything(seed: int) -> None:
    random.seed(seed)
    import numpy as np
    import torch
    from transformers import set_seed

    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    set_seed(seed)


def _stratified_split(rows: list[dict[str, str]], validation_fraction: float, seed: int) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    if not 0 < validation_fraction < 0.5:
        raise ValueError('validation_fraction must be greater than zero and below 0.5')
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
    train_text = {row['text'] for row in train}
    validation_text = {row['text'] for row in validation}
    if train_text & validation_text:
        raise RuntimeError('stratified split leaked text between train and validation')
    if any(not any(row['label'] == label for row in train) or not any(row['label'] == label for row in validation) for label in LABELS):
        raise RuntimeError('stratified split did not retain every label in both partitions')
    return train, validation


def _token_length_stats(tokenizer: Any, rows: list[dict[str, str]], max_length: int) -> dict[str, Any]:
    lengths = [len(tokenizer(row['text'], add_special_tokens=True, truncation=False)['input_ids']) for row in rows]
    return {
        'max_untruncated_tokens': max(lengths, default=0),
        'mean_untruncated_tokens': round(sum(lengths) / len(lengths), 3) if lengths else 0.0,
        'over_max_length': sum(length > max_length for length in lengths),
        'max_length': max_length,
    }


def _load_tokenizer(base_model: str) -> Any:
    """Load a tokenizer even when a model card has no fast-tokenizer file."""
    from transformers import AutoTokenizer, BertTokenizer

    try:
        # The slow path is deliberate: bert-tiny ships vocab.txt but not a
        # tokenizer.json, and it does not require sentencepiece/tiktoken.
        return AutoTokenizer.from_pretrained(base_model, use_fast=False)
    except (OSError, ValueError, TypeError):
        if base_model != DEFAULT_BASE_MODEL:
            raise
        return BertTokenizer.from_pretrained(base_model)


def _target_modules(model: Any) -> list[str]:
    names = {name.rsplit('.', 1)[-1] for name, _ in model.named_modules()}
    selected = [name for name in ('query', 'value') if name in names]
    if not selected:
        raise RuntimeError(f'could not discover query/value attention modules; found={sorted(names)[:20]}')
    return selected


def _classifier_head(model: Any) -> Any:
    """Return the trainable classifier, including PEFT's wrapped head."""
    candidates = [getattr(model, 'classifier', None)]
    candidates.extend(
        module
        for name, module in model.named_modules()
        if name.endswith(('modules_to_save.default', '.classifier'))
    )
    for classifier in candidates:
        if classifier is not None and hasattr(classifier, 'weight') and hasattr(classifier, 'bias'):
            return classifier
    raise RuntimeError('classifier head with weight and bias was not found')


def _parameter_stats(model: Any, mode: str) -> dict[str, Any]:
    classifier = _classifier_head(model)
    if not classifier.weight.requires_grad or not classifier.bias.requires_grad:
        raise RuntimeError('classifier.weight and classifier.bias must both be trainable')
    total = sum(parameter.numel() for parameter in model.parameters())
    trainable = sum(parameter.numel() for parameter in model.parameters() if parameter.requires_grad)
    classifier_params = classifier.weight.numel() + classifier.bias.numel()
    lora_params = trainable - classifier_params if mode == 'lora' else 0
    stats = {
        'total_parameters': total,
        'trainable_parameters': trainable,
        'trainable_percentage': round((trainable / total) * 100, 4) if total else 0.0,
        'classifier_parameters': classifier_params,
        'lora_parameters': lora_params,
        'classifier_weight_trainable': classifier.weight.requires_grad,
        'classifier_bias_trainable': classifier.bias.requires_grad,
    }
    print(json.dumps({'parameter_stats': stats}, sort_keys=True))
    return stats


def _metrics(labels: Iterable[int], predictions: Iterable[int]) -> dict[str, Any]:
    actual = [int(value) for value in labels]
    predicted = [int(value) for value in predictions]
    if len(actual) != len(predicted):
        raise ValueError('labels and predictions have different lengths')
    matrix = [[0 for _ in LABELS] for _ in LABELS]
    for expected, received in zip(actual, predicted, strict=True):
        if not 0 <= expected < len(LABELS) or not 0 <= received < len(LABELS):
            raise ValueError(f'invalid class index: expected={expected}, received={received}')
        matrix[expected][received] += 1
    classes: dict[str, dict[str, float]] = {}
    precisions: list[float] = []
    recalls: list[float] = []
    f1_values: list[float] = []
    support_values: list[int] = []
    for index, label in enumerate(LABELS):
        tp = matrix[index][index]
        fp = sum(matrix[row][index] for row in range(len(LABELS)) if row != index)
        fn = sum(matrix[index][column] for column in range(len(LABELS)) if column != index)
        support = sum(matrix[index])
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        classes[label] = {'precision': precision, 'recall': recall, 'f1': f1, 'support': support}
        precisions.append(precision)
        recalls.append(recall)
        f1_values.append(f1)
        support_values.append(support)
    total = len(actual)
    weighted_f1 = sum(item['f1'] * item['support'] for item in classes.values()) / total if total else 0.0
    return {
        'samples': total,
        'accuracy': sum(a == b for a, b in zip(actual, predicted, strict=True)) / total if total else 0.0,
        'macro_precision': sum(precisions) / len(precisions),
        'macro_recall': sum(recalls) / len(recalls),
        'macro_f1': sum(f1_values) / len(f1_values),
        'weighted_f1': weighted_f1,
        'classes': classes,
        'samples_per_class': {label: support for label, support in zip(LABELS, support_values, strict=True)},
        'zero_score_classes': [label for label, item in classes.items() if item['f1'] == 0.0 or item['recall'] == 0.0],
        'confusion_matrix': {'labels': list(LABELS), 'values': matrix},
    }


def _prediction_arrays(output: Any) -> tuple[list[int], list[int]]:
    import numpy as np

    logits = output.predictions[0] if isinstance(output.predictions, tuple) else output.predictions
    logits = np.asarray(logits)
    if not np.isfinite(logits).all():
        raise RuntimeError('model produced non-finite validation logits')
    labels = np.asarray(output.label_ids).astype(int).tolist()
    predictions = logits.argmax(axis=-1).astype(int).tolist()
    return labels, predictions


def _trainer_metrics(eval_pred: Any) -> dict[str, float]:
    labels, predictions = _prediction_arrays(eval_pred)
    metrics = _metrics(labels, predictions)
    return {key: float(metrics[key]) for key in ('accuracy', 'macro_precision', 'macro_recall', 'macro_f1', 'weighted_f1')}


def _build_model(base_model: str, mode: str) -> tuple[Any, list[str]]:
    from peft import LoraConfig, TaskType, get_peft_model
    from transformers import (
        AutoModelForSequenceClassification,
        BertConfig,
        BertForSequenceClassification,
    )

    label_config = {
        'num_labels': len(LABELS),
        'id2label': {index: label for index, label in enumerate(LABELS)},
        'label2id': {label: index for index, label in enumerate(LABELS)},
    }
    try:
        model = AutoModelForSequenceClassification.from_pretrained(base_model, **label_config)
    except (OSError, ValueError, KeyError):
        if base_model != DEFAULT_BASE_MODEL:
            raise
        # The tiny model's legacy config omits model_type. Explicit BERT
        # loading keeps newer Transformers releases deterministic.
        config = BertConfig.from_pretrained(base_model, **label_config)
        model = BertForSequenceClassification.from_pretrained(base_model, config=config)
    targets: list[str] = []
    if mode == 'lora':
        targets = _target_modules(model)
        model = get_peft_model(model, LoraConfig(
            task_type=TaskType.SEQ_CLS,
            r=8,
            lora_alpha=16,
            lora_dropout=0.1,
            target_modules=targets,
            modules_to_save=['classifier'],
        ))
    elif mode != 'full':
        raise ValueError(f'unknown training mode: {mode}')
    for parameter in model.parameters():
        if mode == 'full':
            parameter.requires_grad = True
    return model, targets


def _write_report(path: Path, metrics: dict[str, Any], mode: str, best_epoch: float | None) -> None:
    lines = [
        f'Mode: {mode}',
        f'Best epoch: {best_epoch}',
        f'Samples: {metrics["samples"]}',
        f'Accuracy: {metrics["accuracy"]:.6f}',
        f'Macro precision: {metrics["macro_precision"]:.6f}',
        f'Macro recall: {metrics["macro_recall"]:.6f}',
        f'Macro F1: {metrics["macro_f1"]:.6f}',
        f'Weighted F1: {metrics["weighted_f1"]:.6f}',
        '',
        'Per-class metrics:',
    ]
    for label in LABELS:
        item = metrics['classes'][label]
        lines.append(f'  {label}: precision={item["precision"]:.6f} recall={item["recall"]:.6f} f1={item["f1"]:.6f} support={item["support"]}')
    if metrics['zero_score_classes']:
        lines.extend(['', 'WARNING zero recall/F1: ' + ', '.join(metrics['zero_score_classes'])])
    lines.extend(['', 'Confusion matrix (rows=true, columns=predicted):', json.dumps(metrics['confusion_matrix'], indent=2)])
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _run_overfit_test(tokenizer: Any, train_rows: list[dict[str, str]], base_model: str, mode: str, max_length: int, work_dir: Path, seed: int) -> dict[str, Any]:
    from datasets import Dataset
    from transformers import Trainer, TrainingArguments

    # Keep the diagnostic balanced so a pass cannot be caused by seeing only
    # one or two labels. Two examples per class gives a compact 12-row check.
    subset = []
    for label in LABELS:
        subset.extend([row for row in train_rows if row['label'] == label][:2])
    subset = subset[:min(12, len(train_rows))]
    labels = {label: index for index, label in enumerate(LABELS)}
    dataset = Dataset.from_list([{'text': row['text'], 'label': labels[row['label']]} for row in subset])
    dataset = dataset.map(lambda batch: tokenizer(batch['text'], truncation=True, padding='max_length', max_length=max_length), batched=True, remove_columns=['text'])
    model, _ = _build_model(base_model, mode)
    _parameter_stats(model, mode)
    args = TrainingArguments(
        output_dir=str(work_dir),
        num_train_epochs=40,
        per_device_train_batch_size=4,
        learning_rate=5e-4 if mode == 'lora' else 2e-4,
        seed=seed,
        data_seed=seed,
        report_to=[],
        save_strategy='no',
        save_safetensors=False,
        logging_strategy='no',
        disable_tqdm=True,
    )
    trainer = Trainer(model=model, args=args, train_dataset=dataset)
    trainer.train()
    actual, predicted = _prediction_arrays(trainer.predict(dataset))
    metrics = _metrics(actual, predicted)
    result = {'samples': len(subset), 'accuracy': metrics['accuracy'], 'threshold': 0.8, 'passed': metrics['accuracy'] >= 0.8}
    if not result['passed']:
        result['warning'] = 'tiny subset did not substantially overfit; inspect labels, gradients, and tokenization'
    return result


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
    *,
    mode: str = 'lora',
    early_stopping_patience: int = 2,
    run_overfit: bool = False,
) -> dict[str, Any]:
    try:
        from datasets import Dataset
        from transformers import (
            EarlyStoppingCallback,
            Trainer,
            TrainingArguments,
            set_seed,
        )
    except ImportError as exc:
        raise RuntimeError('install the pinned training/requirements.txt to run the experiment') from exc
    if not 8 <= max_length <= 512 or epochs <= 0 or batch_size <= 0 or learning_rate <= 0:
        raise ValueError('max_length must be 8..512 and epochs, batch_size, learning_rate must be positive')
    if early_stopping_patience < 1:
        raise ValueError('early_stopping_patience must be at least 1')
    _seed_everything(seed)
    set_seed(seed)
    rows, dataset_diagnostics = _load_rows(dataset_path)
    train_rows, validation_rows = _stratified_split(rows, validation_fraction, seed)
    label_to_id = {label: index for index, label in enumerate(LABELS)}
    tokenizer = _load_tokenizer(base_model)
    token_stats = _token_length_stats(tokenizer, rows, max_length)
    train_dataset = Dataset.from_list([{'text': row['text'], 'label': label_to_id[row['label']]} for row in train_rows])
    validation_dataset = Dataset.from_list([{'text': row['text'], 'label': label_to_id[row['label']]} for row in validation_rows])

    def tokenize(batch: dict[str, list[str]]) -> dict[str, Any]:
        return tokenizer(batch['text'], truncation=True, padding='max_length', max_length=max_length)

    train_encoded = train_dataset.map(tokenize, batched=True, remove_columns=['text'])
    validation_encoded = validation_dataset.map(tokenize, batched=True, remove_columns=['text'])
    model, targets = _build_model(base_model, mode)
    parameter_stats = _parameter_stats(model, mode)
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_dir = output_dir / '.checkpoints'
    if checkpoint_dir.exists():
        shutil.rmtree(checkpoint_dir)
    args = TrainingArguments(
        output_dir=str(checkpoint_dir),
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        learning_rate=learning_rate,
        lr_scheduler_type='linear',
        warmup_ratio=0.1,
        weight_decay=0.01,
        seed=seed,
        data_seed=seed,
        evaluation_strategy='epoch',
        save_strategy='epoch',
        load_best_model_at_end=True,
        metric_for_best_model='macro_f1',
        greater_is_better=True,
        save_total_limit=2,
        report_to=[],
        logging_strategy='epoch',
        remove_unused_columns=True,
        save_safetensors=mode != 'full',
    )
    callbacks = [EarlyStoppingCallback(early_stopping_patience=early_stopping_patience)]
    trainer = Trainer(model=model, args=args, train_dataset=train_encoded, eval_dataset=validation_encoded, compute_metrics=_trainer_metrics, callbacks=callbacks)
    if run_overfit:
        overfit = _run_overfit_test(tokenizer, train_rows, base_model, mode, max_length, output_dir / '.overfit', seed)
        print(json.dumps({'tiny_overfit': overfit}, sort_keys=True))
    train_result = trainer.train()
    actual, predicted = _prediction_arrays(trainer.predict(validation_encoded))
    metrics = _metrics(actual, predicted)
    eval_logs = [entry for entry in trainer.state.log_history if 'eval_macro_f1' in entry]
    best_log = max(eval_logs, key=lambda entry: entry['eval_macro_f1'], default={})
    best_epoch = best_log.get('epoch')
    if mode == 'lora':
        model.save_pretrained(output_dir)
    else:
        # Some CPU full-fine-tune tensors are non-contiguous after Trainer
        # updates; PyTorch serialization is lossless and avoids a safetensors
        # save failure while the artifact validator still checks its hash.
        model.save_pretrained(output_dir, safe_serialization=False)
    tokenizer.save_pretrained(output_dir)
    model_files = sorted(
        path for pattern in ('adapter_model.*', 'model.safetensors', 'pytorch_model.bin')
        for path in output_dir.glob(pattern)
        if path.is_file()
    )
    if not model_files:
        raise RuntimeError('training completed but no model weight file was saved')
    model_sha256 = _sha256(model_files[0])
    training_config = {
        'mode': mode,
        'base_model': base_model,
        'seed': seed,
        'validation_fraction': validation_fraction,
        'max_length': max_length,
        'batch_size': batch_size,
        'epochs': epochs,
        'learning_rate': learning_rate,
        'scheduler': 'linear',
        'warmup_ratio': 0.1,
        'weight_decay': 0.01,
        'early_stopping_patience': early_stopping_patience,
        'lora': {'r': 8, 'alpha': 16, 'dropout': 0.1, 'target_modules': targets} if mode == 'lora' else None,
    }
    metadata: dict[str, Any] = {
        'artifact_schema_version': ARTIFACT_SCHEMA_VERSION,
        'artifact_type': 'peft_adapter' if mode == 'lora' else 'full_model',
        'dataset_version': DATASET_VERSION,
        'dataset_sha256': dataset_diagnostics['dataset_sha256'],
        'base_model': base_model,
        'labels': list(LABELS),
        'label_map_version': '1.0.0',
        'rows': len(rows),
        'train_rows': len(train_rows),
        'validation_rows': len(validation_rows),
        'class_counts': dataset_diagnostics['class_counts'],
        'token_length_stats': token_stats,
        'best_epoch': best_epoch,
        'best_validation_macro_f1': metrics['macro_f1'],
        'train_loss': getattr(train_result, 'training_loss', None),
        'parameter_stats': parameter_stats,
        'model_file': model_files[0].name,
        'model_sha256': model_sha256,
        'training_config': training_config,
        'final_metrics': metrics,
        'created_at_utc': datetime.now(UTC).isoformat(),
    }
    if mode == 'lora':
        # The runtime validator uses this explicit name for adapter integrity.
        metadata['adapter_sha256'] = model_sha256
    if run_overfit:
        metadata['tiny_overfit'] = overfit
    (output_dir / 'label_map.json').write_text(json.dumps({'version': '1.0.0', 'labels': list(LABELS), 'label_to_id': label_to_id}, indent=2) + '\n', encoding='utf-8')
    (output_dir / 'metrics.json').write_text(json.dumps(metrics, indent=2) + '\n', encoding='utf-8')
    (output_dir / 'classification_report.json').write_text(json.dumps(metrics['classes'], indent=2) + '\n', encoding='utf-8')
    (output_dir / 'confusion_matrix.json').write_text(json.dumps(metrics['confusion_matrix'], indent=2) + '\n', encoding='utf-8')
    (output_dir / 'training_config.json').write_text(json.dumps(training_config, indent=2) + '\n', encoding='utf-8')
    (output_dir / 'training_metadata.json').write_text(json.dumps(metadata, indent=2, default=str) + '\n', encoding='utf-8')
    _write_report(output_dir / 'classification_report.txt', metrics, mode, best_epoch)
    if checkpoint_dir.exists():
        shutil.rmtree(checkpoint_dir)
    if (output_dir / '.overfit').exists():
        shutil.rmtree(output_dir / '.overfit')
    (output_dir / 'README.md').write_text(f'# OpsPilot {mode} artifact\n\nGenerated by `training/train_lora.py`. Validate with `training/evaluate.py` before loading.\n\nThis is educational offline evidence for {metrics["samples"]} held-out examples; it is not production accuracy.\n', encoding='utf-8')
    manifest = _manifest(output_dir)
    (output_dir / 'artifact_manifest.json').write_text(json.dumps({'schema_version': ARTIFACT_SCHEMA_VERSION, 'files': manifest}, indent=2) + '\n', encoding='utf-8')
    result: dict[str, Any] = {'mode': mode, 'metadata': metadata, 'metrics': metrics, 'artifact_dir': str(output_dir), 'mlflow': 'disabled'}
    if tracking_uri:
        try:
            import mlflow
            mlflow.set_tracking_uri(tracking_uri)
            with mlflow.start_run():
                scalar_params = {key: value for key, value in training_config.items() if isinstance(value, (str, int, float, bool))}
                scalar_params['dataset_sha256'] = dataset_diagnostics['dataset_sha256']
                mlflow.log_params(scalar_params)
                mlflow.log_metrics({f'validation_{key}': float(metrics[key]) for key in ('accuracy', 'macro_f1', 'weighted_f1')})
                mlflow.log_artifacts(str(output_dir), artifact_path='artifact')
            result['mlflow'] = 'logged'
        except Exception as exc:  # noqa: BLE001 - optional tracking must not discard local evidence
            result['mlflow'] = f'warning: unavailable ({type(exc).__name__})'
    return result


def _compare(results: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    comparison = {
        mode: {
            'artifact_dir': value['artifact_dir'],
            'accuracy': value['metrics']['accuracy'],
            'macro_f1': value['metrics']['macro_f1'],
            'weighted_f1': value['metrics']['weighted_f1'],
            'zero_score_classes': value['metrics']['zero_score_classes'],
        }
        for mode, value in results.items()
    }
    ranked = sorted(comparison, key=lambda mode: comparison[mode]['macro_f1'], reverse=True)
    comparison['winner_by_macro_f1'] = ranked[0] if ranked else None
    (output_dir / 'comparison.json').write_text(json.dumps(comparison, indent=2) + '\n', encoding='utf-8')
    (output_dir / 'comparison.txt').write_text('\n'.join([f'{mode}: macro_f1={comparison[mode]["macro_f1"]:.6f} accuracy={comparison[mode]["accuracy"]:.6f}' for mode in ('lora', 'full')]) + f'\nWinner by macro-F1: {comparison["winner_by_macro_f1"]}\n', encoding='utf-8')
    return comparison


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = ArgumentParser(description='Train and evaluate the optional OpsPilot LoRA/full classifier')
    parser.add_argument('--dataset', type=Path, default=root / 'training/dataset/incidents.jsonl')
    parser.add_argument('--output-dir', type=Path, default=root / 'training/artifacts/opspilot-lora')
    parser.add_argument('--base-model', default=DEFAULT_BASE_MODEL)
    parser.add_argument('--mode', choices=('lora', 'full', 'both'), default='lora')
    parser.add_argument('--epochs', type=float, default=8.0)
    parser.add_argument('--batch-size', type=int, default=8)
    parser.add_argument('--learning-rate', type=float, default=None)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--validation-fraction', type=float, default=0.2)
    parser.add_argument('--max-length', type=int, default=128)
    parser.add_argument('--early-stopping-patience', type=int, default=2)
    parser.add_argument('--run-overfit-test', action='store_true')
    parser.add_argument('--tracking-uri', default=None)
    args = parser.parse_args()
    if not args.dataset.exists():
        parser.error(f'dataset not found: {args.dataset}')
    modes = ('lora', 'full') if args.mode == 'both' else (args.mode,)
    results: dict[str, Any] = {}
    try:
        for mode in modes:
            learning_rate = args.learning_rate if args.learning_rate is not None else (5e-5 if mode == 'lora' else 2e-5)
            output_dir = args.output_dir / mode if args.mode == 'both' else args.output_dir
            results[mode] = train(args.dataset, output_dir, args.base_model, args.epochs, args.batch_size, learning_rate, args.tracking_uri, args.seed, args.validation_fraction, args.max_length, mode=mode, early_stopping_patience=args.early_stopping_patience, run_overfit=args.run_overfit_test)
        if args.mode == 'both':
            results['comparison'] = _compare({key: value for key, value in results.items() if key in {'lora', 'full'}}, args.output_dir)
        print(json.dumps(results, indent=2, default=str))
    except (RuntimeError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
