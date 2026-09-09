'''Run representative inference examples against a saved classifier artifact.'''

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from training.evaluate import LABELS, validate_artifact
except ModuleNotFoundError:  # direct `python training/predict.py` execution
    from evaluate import LABELS, validate_artifact


def predict(adapter: Path, text: str) -> dict[str, Any]:
    validate_artifact(adapter)
    from transformers import pipeline

    output = pipeline('text-classification', model=str(adapter), top_k=1)(text)
    if isinstance(output, list) and output and isinstance(output[0], list):
        output = output[0]
    if isinstance(output, list):
        output = output[0]
    if not isinstance(output, dict):
        raise TypeError('classifier returned an invalid prediction')
    label = str(output.get('label', '')).lower()
    if label.startswith('label_') and label[6:].isdigit():
        index = int(label[6:])
        label = LABELS[index] if 0 <= index < len(LABELS) else ''
    if label not in LABELS:
        raise ValueError(f'classifier returned unknown label: {label}')
    return {'label': label, 'confidence': float(output.get('score', 0.0)), 'source': 'trained_artifact'}


def main() -> int:
    parser = argparse.ArgumentParser(description='Predict an incident class')
    parser.add_argument('adapter', type=Path)
    parser.add_argument('--text', required=True)
    args = parser.parse_args()
    if not args.text.strip():
        parser.error('--text must contain non-whitespace text')
    print(json.dumps(predict(args.adapter, args.text), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
