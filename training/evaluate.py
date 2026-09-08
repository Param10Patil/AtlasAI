'''Evaluate a saved classifier adapter without fabricating unavailable metrics.'''

from argparse import ArgumentParser
import json
from pathlib import Path


LABELS = ('deployment_failure', 'database_failure', 'authentication_failure', 'network_failure', 'performance_issue', 'availability_issue')


def evaluate(adapter: Path, dataset: Path) -> dict[str, object]:
    try:
        from transformers import pipeline
    except ImportError as exc:
        raise RuntimeError('install training/requirements.txt to evaluate an adapter') from exc
    classifier = pipeline('text-classification', model=str(adapter), top_k=1)
    rows = [json.loads(line) for line in dataset.read_text(encoding='utf-8').splitlines() if line.strip()]
    counts = {label: {'tp': 0, 'fp': 0, 'fn': 0} for label in LABELS}
    correct = 0
    for row in rows:
        prediction = classifier(row['text'])[0][0]['label'].lower()
        if prediction == row['label']:
            correct += 1
        for label in LABELS:
            if prediction == label and row['label'] == label:
                counts[label]['tp'] += 1
            elif prediction == label:
                counts[label]['fp'] += 1
            elif row['label'] == label:
                counts[label]['fn'] += 1
    metrics: dict[str, object] = {'samples': len(rows), 'accuracy': correct / len(rows) if rows else 0.0, 'classes': {}}
    class_metrics: dict[str, dict[str, float]] = {}
    for label, values in counts.items():
        precision = values['tp'] / max(values['tp'] + values['fp'], 1)
        recall = values['tp'] / max(values['tp'] + values['fn'], 1)
        f1 = 2 * precision * recall / max(precision + recall, 1e-9)
        class_metrics[label] = {'precision': precision, 'recall': recall, 'f1': f1}
    metrics['classes'] = class_metrics
    return metrics


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = ArgumentParser(description='Evaluate an OpsPilot LoRA classifier')
    parser.add_argument('adapter', type=Path)
    parser.add_argument('--dataset', type=Path, default=root / 'training/dataset/incidents.jsonl')
    args = parser.parse_args()
    if not args.adapter.exists():
        parser.error(f'adapter not found: {args.adapter}')
    if not args.dataset.exists():
        parser.error(f'dataset not found: {args.dataset}')
    try:
        print(json.dumps(evaluate(args.adapter, args.dataset), indent=2))
    except RuntimeError as exc:
        parser.error(str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
