import json
from pathlib import Path

import pytest

from training.evaluate import _metrics as evaluate_metrics
from training.evaluate import _prediction_label
from training.train_lora import LABELS, _load_rows, _metrics, _stratified_split

DATASET = 'training/dataset/incidents.jsonl'
EXPECTED_SHA256 = '0ffd0809370d3b1fdebb193b2f78e49c98a469df48f8e64f4b5c4de384cba7af'


def test_repository_dataset_is_balanced_and_versioned():
    rows, diagnostics = _load_rows(Path(DATASET))
    assert len(rows) == 120
    assert diagnostics['dataset_sha256'] == EXPECTED_SHA256
    assert diagnostics['class_counts'] == dict.fromkeys(LABELS, 20)


def test_stratified_split_is_deterministic_disjoint_and_complete():
    rows, _ = _load_rows(Path(DATASET))
    train_a, validation_a = _stratified_split(rows, 0.2, 42)
    train_b, validation_b = _stratified_split(rows, 0.2, 42)
    assert train_a == train_b and validation_a == validation_b
    assert len(train_a) == 96 and len(validation_a) == 24
    assert {row['text'] for row in train_a}.isdisjoint(row['text'] for row in validation_a)
    assert {row['label'] for row in train_a} == set(LABELS)
    assert {row['label'] for row in validation_a} == set(LABELS)


def test_metrics_exposes_per_class_zero_scores_and_confusion_matrix():
    metrics = _metrics(range(6), [0, 0, 0, 0, 0, 0])
    assert metrics['samples'] == 6
    assert metrics['accuracy'] == pytest.approx(1 / 6)
    assert metrics['classes']['deployment_failure']['recall'] == 1.0
    assert set(metrics['zero_score_classes']) == set(LABELS[1:])
    assert metrics['confusion_matrix']['values'][5][0] == 1


def test_dataset_validator_reports_bad_rows(tmp_path):
    path = tmp_path / 'bad.jsonl'
    path.write_text(
        json.dumps({'text': 'too short', 'label': 'deployment_failure'}) + '\n'
        + json.dumps({'text': 'too short', 'label': 'not_a_label'}) + '\n',
        encoding='utf-8',
    )
    with pytest.raises(ValueError, match='dataset validation failed'):
        _load_rows(path)


def test_evaluator_rejects_unknown_labels_and_invalid_confidence():
    with pytest.raises(ValueError, match='unknown predicted label'):
        evaluate_metrics(['deployment_failure'], ['not_a_label'])
    with pytest.raises(ValueError, match='non-finite'):
        _prediction_label({'label': 'LABEL_0', 'score': float('nan')}, list(LABELS))
    with pytest.raises(ValueError, match='out-of-range'):
        _prediction_label({'label': 'LABEL_0', 'score': 1.1}, list(LABELS))
