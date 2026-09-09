from pathlib import Path

from training.cross_validate import evaluate, stratified_folds
from training.dataset.build_v2 import build
from training.train_lora import LABELS, _load_rows


def test_v2_builder_is_balanced_and_reproducible(tmp_path):
    source = Path('training/dataset/incidents.jsonl')
    first = tmp_path / 'v2-a.jsonl'
    second = tmp_path / 'v2-b.jsonl'
    first_report = build(source, first)
    second_report = build(source, second)
    assert first.read_bytes() == second.read_bytes()
    assert {key: value for key, value in first_report.items() if key != 'output'} == {key: value for key, value in second_report.items() if key != 'output'}
    rows, diagnostics = _load_rows(first)
    assert len(rows) == 180
    assert diagnostics['class_counts'] == dict.fromkeys(LABELS, 30)


def test_cross_validation_is_stratified_and_disjoint():
    rows, _ = _load_rows(Path('training/dataset/incidents.jsonl'))
    partitions = stratified_folds(rows, folds=5, seed=42)
    assert all(len(train) == 96 and len(validation) == 24 for train, validation in partitions)
    for train, validation in partitions:
        assert {row['text'] for row in train}.isdisjoint(row['text'] for row in validation)
        assert {row['label'] for row in validation} == set(LABELS)


def test_cross_validation_returns_aggregate_quality_metrics():
    rows, _ = _load_rows(Path('training/dataset/incidents.jsonl'))
    report = evaluate(rows, folds=3, seed=7)
    assert report['model'] == 'multinomial_naive_bayes_baseline'
    assert report['samples'] == 120
    assert set(report['aggregate']) == {'accuracy', 'macro_precision', 'macro_recall', 'macro_f1', 'weighted_f1'}
    assert all(0 <= report['aggregate'][name]['mean'] <= 1 for name in report['aggregate'])
