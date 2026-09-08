'''Small optional PEFT/LoRA classifier training command.

Run from the repository root or from training/. Dependencies are deliberately
isolated in training/requirements.txt and are never imported by the API.
'''

import json
from argparse import ArgumentParser
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


def _rows(path: Path) -> list[dict[str, str]]:
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def train(dataset_path: Path, output_dir: Path, base_model: str, epochs: float, batch_size: int, learning_rate: float, tracking_uri: str | None) -> dict[str, Any]:
    try:
        from datasets import Dataset
        from peft import LoraConfig, TaskType, get_peft_model
        from transformers import (
            AutoModelForSequenceClassification,
            AutoTokenizer,
            Trainer,
            TrainingArguments,
        )
    except ImportError as exc:
        raise RuntimeError('install training/requirements.txt to run the LoRA experiment') from exc
    rows = _rows(dataset_path)
    label_to_id = {label: index for index, label in enumerate(LABELS)}
    tokenizer = AutoTokenizer.from_pretrained(base_model)
    dataset = Dataset.from_list([{'text': row['text'], 'label': label_to_id[row['label']]} for row in rows])

    def tokenize(batch: dict[str, list[str]]) -> dict[str, Any]:
        return tokenizer(batch['text'], truncation=True, padding='max_length', max_length=128)

    encoded = dataset.map(tokenize, batched=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        base_model,
        num_labels=len(LABELS),
        id2label={index: label for index, label in enumerate(LABELS)},
        label2id=label_to_id,
    )
    adapter = get_peft_model(model, LoraConfig(
        task_type=TaskType.SEQ_CLS,
        r=4,
        lora_alpha=8,
        lora_dropout=0.1,
        target_modules=['query', 'value'],
    ))
    args = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        learning_rate=learning_rate,
        report_to=[],
        save_strategy='no',
        logging_steps=1,
    )
    trainer = Trainer(model=adapter, args=args, train_dataset=encoded)
    trainer.train()
    metrics = trainer.evaluate(encoded)
    output_dir.mkdir(parents=True, exist_ok=True)
    adapter.save_pretrained(output_dir)
    tokenizer.save_pretrained(output_dir)
    result = {'base_model': base_model, 'labels': list(LABELS), 'rows': len(rows), 'epochs': epochs, 'batch_size': batch_size, 'learning_rate': learning_rate, 'metrics': metrics}
    if tracking_uri:
        try:
            import mlflow
            mlflow.set_tracking_uri(tracking_uri)
            with mlflow.start_run():
                mlflow.log_params({key: value for key, value in result.items() if key not in {'labels', 'metrics'}})
                mlflow.log_metrics({key: float(value) for key, value in metrics.items() if isinstance(value, (int, float))})
                mlflow.log_artifacts(str(output_dir), artifact_path='adapter')
        except ImportError:
            result['mlflow'] = 'unavailable; metrics retained locally'
    return result


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = ArgumentParser(description='Train the optional OpsPilot LoRA classifier')
    parser.add_argument('--dataset', type=Path, default=root / 'training/dataset/incidents.jsonl')
    parser.add_argument('--output-dir', type=Path, default=root / 'training/artifacts/opspilot-lora')
    parser.add_argument('--base-model', default='prajjwal1/bert-tiny')
    parser.add_argument('--epochs', type=float, default=1.0)
    parser.add_argument('--batch-size', type=int, default=2)
    parser.add_argument('--learning-rate', type=float, default=2e-4)
    parser.add_argument('--tracking-uri', default=None)
    args = parser.parse_args()
    if not args.dataset.exists():
        parser.error(f'dataset not found: {args.dataset}')
    try:
        print(json.dumps(train(args.dataset, args.output_dir, args.base_model, args.epochs, args.batch_size, args.learning_rate, args.tracking_uri), indent=2))
    except RuntimeError as exc:
        parser.error(str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
