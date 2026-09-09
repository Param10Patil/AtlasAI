"""Leakage-safe stratified cross-validation for incident text quality checks.

This fast Multinomial Naive Bayes baseline is a dataset-quality gate, not a
replacement for the LoRA adapter. Every fold fits only on its training rows
and reports mean/std metrics before expensive transformer experiments.
"""

from __future__ import annotations

import itertools
import json
import math
import random
import re
import sys
from argparse import ArgumentParser
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from training.train_lora import LABELS, _load_rows, _metrics
except ModuleNotFoundError:  # supports `python training/cross_validate.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from training.train_lora import LABELS, _load_rows, _metrics

TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    words = TOKEN_RE.findall(text.lower())
    return words + [f'{left}__{right}' for left, right in itertools.pairwise(words)]


class MultinomialBaseline:
    def __init__(self, alpha: float = 1.0) -> None:
        if alpha <= 0:
            raise ValueError('alpha must be positive')
        self.alpha = alpha
        self._class_counts: Counter[str] = Counter()
        self._token_counts: dict[str, Counter[str]] = {label: Counter() for label in LABELS}
        self._vocabulary: set[str] = set()

    def fit(self, rows: list[dict[str, str]]) -> MultinomialBaseline:
        if not rows:
            raise ValueError('cannot fit on an empty fold')
        for row in rows:
            label = row['label']
            tokens = _tokens(row['text'])
            self._class_counts[label] += 1
            self._token_counts[label].update(tokens)
            self._vocabulary.update(tokens)
        return self

    def predict(self, text: str) -> str:
        vocabulary_size = max(1, len(self._vocabulary))
        total_rows = sum(self._class_counts.values())
        scores: dict[str, float] = {}
        for label in LABELS:
            prior = (self._class_counts[label] + self.alpha) / (total_rows + self.alpha * len(LABELS))
            denominator = sum(self._token_counts[label].values()) + self.alpha * vocabulary_size
            score = math.log(prior)
            for token in _tokens(text):
                score += math.log((self._token_counts[label][token] + self.alpha) / denominator)
            scores[label] = score
        return max(LABELS, key=lambda label: (scores[label], -LABELS.index(label)))


def stratified_folds(rows: list[dict[str, str]], folds: int, seed: int) -> list[tuple[list[dict[str, str]], list[dict[str, str]]]]:
    if folds < 2:
        raise ValueError('folds must be at least 2')
    grouped = {label: [row for row in rows if row['label'] == label] for label in LABELS}
    if any(len(group) < folds for group in grouped.values()):
        raise ValueError('each label needs at least as many rows as folds')
    rng = random.Random(seed)
    assignments: list[list[dict[str, str]]] = [[] for _ in range(folds)]
    for label in LABELS:
        group = grouped[label][:]
        rng.shuffle(group)
        for index, row in enumerate(group):
            assignments[index % folds].append(row)
    all_text = {row['text'] for row in rows}
    result = []
    for index in range(folds):
        validation = assignments[index]
        validation_text = {row['text'] for row in validation}
        train = [row for fold, partition in enumerate(assignments) if fold != index for row in partition]
        train_text = {row['text'] for row in train}
        if train_text & validation_text or train_text | validation_text != all_text:
            raise RuntimeError(f'fold {index + 1} has text leakage or omission')
        result.append((train, validation))
    return result


def evaluate(rows: list[dict[str, str]], folds: int = 5, seed: int = 42, alpha: float = 1.0) -> dict[str, Any]:
    partitions = stratified_folds(rows, folds, seed)
    reports: list[dict[str, Any]] = []
    for index, (train, validation) in enumerate(partitions, 1):
        model = MultinomialBaseline(alpha).fit(train)
        actual = [LABELS.index(row['label']) for row in validation]
        predicted = [LABELS.index(model.predict(row['text'])) for row in validation]
        reports.append({'fold': index, 'train_samples': len(train), 'validation_samples': len(validation), **_metrics(actual, predicted)})
    scalar_names = ('accuracy', 'macro_precision', 'macro_recall', 'macro_f1', 'weighted_f1')
    aggregate: dict[str, dict[str, float]] = {}
    for name in scalar_names:
        mean = sum(report[name] for report in reports) / len(reports)
        aggregate[name] = {'mean': mean, 'std': (sum((report[name] - mean) ** 2 for report in reports) / len(reports)) ** 0.5}
    return {
        'schema_version': 'opspilot-quality/cv-v1',
        'model': 'multinomial_naive_bayes_baseline', 'folds': folds, 'seed': seed, 'alpha': alpha,
        'samples': len(rows),
        'class_counts': {label: sum(row['label'] == label for row in rows) for label in LABELS},
        'aggregate': aggregate, 'fold_reports': reports,
        'interpretation': 'Fast dataset-separability baseline; LoRA quality must still be measured on a held-out split.',
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=root / 'training/dataset/incidents-v2.jsonl')
    parser.add_argument('--folds', type=int, default=5)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--alpha', type=float, default=1.0)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    rows, diagnostics = _load_rows(args.dataset)
    report = evaluate(rows, args.folds, args.seed, args.alpha)
    report['dataset'] = diagnostics
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + '\n', encoding='utf-8')
    print(rendered)


if __name__ == '__main__':
    main()
