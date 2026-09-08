import sys

import pytest

from app.ml.classifier import (
    AdapterValidationError,
    FallbackClassifier,
    build_classifier,
    validate_adapter,
)


def test_rule_fallback_is_explicit_and_conservative_for_empty_signal():
    result = FallbackClassifier().classify('unclassified symptom')
    assert result.source == 'rule_fallback'
    assert result.confidence <= 0.35


def test_missing_or_incomplete_adapter_never_looks_like_lora(tmp_path):
    with pytest.raises(AdapterValidationError):
        validate_adapter(tmp_path / 'missing')
    assert build_classifier(str(tmp_path / 'missing')).__class__ is FallbackClassifier


def test_runtime_module_does_not_import_training_stack():
    assert 'transformers' not in sys.modules
    assert 'peft' not in sys.modules
