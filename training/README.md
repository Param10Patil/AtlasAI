# Optional LoRA training

Training is separate from runtime inference. The intended exercise is a small
incident-category classifier with PEFT/LoRA and a six-label synthetic dataset.
Training runs on a developer machine or Colab, records parameters/metrics to
MLflow when available, and exports an adapter version. The API treats a missing
adapter as a declared rule-based fallback; it never trains during startup.

The runtime image must not install training dependencies, model weights, or
MLflow. `COLAB.md` contains the exact CPU/Colab command; it creates an isolated
Python 3.11 interpreter with the mutually compatible CPU stack, runs both the LoRA
and full-fine-tune baseline, performs a balanced tiny overfit diagnostic, and
then validates each artifact. `dataset/DATASET.md` records provenance, while
`docs/lora.md`, `docs/mlflow.md`, and `docs/development-notes.md` describe the
design and honest verification status.

For a local run from the repository root:

```powershell
$env:HF_HOME = 'E:\\AtlasAI-hf-cache'
python training/train_lora.py --mode both --run-overfit-test --epochs 30 --batch-size 8 --early-stopping-patience 8 --output-dir E:\\AtlasAI-training-runs\\comparison
python training/evaluate.py E:\\AtlasAI-training-runs\\comparison\\lora --dataset training/dataset/incidents.jsonl
python training/dataset/build_v2.py
python training/cross_validate.py --dataset training/dataset/incidents-v2.jsonl --output E:\\AtlasAI-training-runs\\cross-validation.json
```

The v2 command creates a balanced 180-row corpus and the cross-validation
command fits a tiny token/bigram Naive Bayes baseline independently inside
each fold. It catches vocabulary leakage and measures dataset separability
quickly; it does not replace the LoRA holdout evaluation. For a larger LoRA
run, pass `--dataset training/dataset/incidents-v2.jsonl` to `train_lora.py`
and record the new checksum in the artifact metadata.

Install `training/requirements-mlflow.txt` only when local MLflow tracking is
needed; it layers on top of the core training requirements. The generated
weights stay outside Git. Install only a reviewed adapter and
set `OPSPILOT_LORA_ADAPTER_PATH`; runtime startup validates its label map,
metrics, model checksum, and manifest before importing Transformers. The
offline evaluator additionally checks the canonical dataset checksum.

When `--tracking-uri file:///E:/AtlasAI/mlruns` is supplied, each completed
run creates `mlflow_run.json` beside the local artifact and logs scalar
parameters, validation metrics, tags, and the artifact directory to a named
MLflow experiment. A tracking failure is reported as a warning after local
metrics are safely written; it is never converted into a false success.
