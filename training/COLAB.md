# Colab: train, validate, and download the OpsPilot adapter

This exact notebook flow creates a PEFT/LoRA incident classifier and a
validated zip that the AtlasAI runtime can load. It does not train a
generative LLM and never changes the web runtime.

## 1. Clone and install an isolated Python 3.11 runtime

```python
!rm -rf /content/AtlasAI
!git clone --depth 1 https://github.com/Param10Patil/AtlasAI.git /content/AtlasAI
%cd /content/AtlasAI
!python -m pip install -q uv
!uv python install 3.11
!uv venv --seed /content/atlasai-py311 --python 3.11
!/content/atlasai-py311/bin/python -m pip install --prefer-binary -r training/requirements-py311-colab.txt
!/content/atlasai-py311/bin/python -c "import sys, torch, transformers, accelerate, numpy; print(sys.version); print(torch.__version__, transformers.__version__, accelerate.__version__, numpy.__version__)"
```

This leaves Colab's notebook kernel unchanged but forces every training,
evaluation, and prediction command below through Python 3.11. The pinned CPU
stack is the one verified locally and has no dependency conflict. It is
deliberately faster to download than a CUDA-enabled Torch bundle for this
small model; a Colab GPU is not required.

For higher Hugging Face Hub rate limits, add a Colab secret named `HF_TOKEN`
and run this optional cell. Public model downloads work without it; the
unauthenticated message is only a warning.

```python
from google.colab import userdata
from huggingface_hub import login

try:
    hf_token = userdata.get("HF_TOKEN")
except Exception:
    hf_token = None
if hf_token:
    login(token=hf_token, add_to_git_credential=False)
else:
    print("HF_TOKEN not set; continuing with public unauthenticated downloads")
```

Use Colab's normal GitHub authentication for a private fork; never paste a
token in a notebook. A free GPU is optional: `prajjwal1/bert-tiny` and the
120-row CPU-capable dataset are deliberately small.

## 2. Train with fixed, reproducible settings

```python
!rm -rf training/artifacts/opspilot-comparison
!/content/atlasai-py311/bin/python training/train_lora.py --dataset training/dataset/incidents.jsonl --output-dir training/artifacts/opspilot-comparison --base-model prajjwal1/bert-tiny --mode both --run-overfit-test --epochs 8 --batch-size 8 --validation-fraction 0.2 --max-length 128 --seed 42
```

The default learning rates are 5e-5 for LoRA and 2e-5 for the full baseline.
The seed creates 96 training and 24 validation rows (four per label). The
command prints trainable-parameter counts, accuracy, macro-F1, per-class
precision/recall/F1, a confusion matrix, and a 12-row overfit diagnostic.
Treat these as educational offline measurements, not production accuracy.

## 3. Evaluate and validate the artifact

```python
!/content/atlasai-py311/bin/python training/evaluate.py training/artifacts/opspilot-comparison/lora --dataset training/dataset/incidents.jsonl | tee /content/opspilot-lora-evaluation.json
!/content/atlasai-py311/bin/python training/evaluate.py training/artifacts/opspilot-comparison/full --dataset training/dataset/incidents.jsonl | tee /content/opspilot-full-evaluation.json
```

Then run this machine-readable gate:

```python
import json
from pathlib import Path
from training.evaluate import validate_artifact

adapter = Path("training/artifacts/opspilot-comparison/lora")
dataset = Path("training/dataset/incidents.jsonl")
validated = validate_artifact(adapter, dataset)
metadata = json.loads((adapter / "training_metadata.json").read_text())
metrics = json.loads((adapter / "metrics.json").read_text())
manifest = json.loads((adapter / "artifact_manifest.json").read_text())
assert metadata["rows"] == 120 and metadata["train_rows"] == 96
assert metadata["validation_rows"] == 24 and metrics["samples"] == 24
assert metadata["labels"] == validated["label_map"]["labels"]
assert metadata["adapter_sha256"] == metadata["model_sha256"]
assert set(manifest["files"]) >= {"adapter_config.json", "label_map.json", "training_metadata.json", "metrics.json"}
print("adapter validation passed")
print(json.dumps(validated, indent=2, default=str))
```

This checks exact label ordering, base-model metadata, dataset SHA-256,
adapter SHA-256, manifest SHA-256, finite predictions, and metric structure.
Do not copy an artifact when this gate fails.

## 4. Download and integrate locally

```python
!cd training/artifacts/opspilot-comparison && zip -qr /content/opspilot-lora.zip lora
from google.colab import files
files.download("/content/opspilot-lora.zip")
```

On Windows, extract the zip so the environment variable points to the
directory containing `adapter_config.json`:

```powershell
Expand-Archive .\opspilot-lora.zip -DestinationPath .\training\artifacts\opspilot-comparison -Force
python -m pip install -r requirements-dev.txt -r training/requirements.txt
$env:OPSPILOT_LORA_ADAPTER_PATH = (Resolve-Path .\training\artifacts\opspilot-comparison\lora)
$env:OPSPILOT_APP_ENV = 'development'
$env:OPSPILOT_EXECUTION_MODE = 'in_process'
$env:OPSPILOT_DATABASE_URL = 'memory://opspilot'
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

Install `requirements-dev.txt` and `training/requirements.txt` in the
environment that runs the API. Startup validates the adapter, imports
Transformers lazily, reports classifier source `lora`, and falls back to the
declared `rule_fallback` on invalid or failed inference. Startup never trains.
The production Docker/Cloud Run image intentionally excludes training
packages and weights; use a separately reviewed image or read-only mount for
container experiments.

## Optional MLflow and model-scope notes

Pass `--tracking-uri file:///content/mlruns` to record a local run. Local
metrics and artifacts are written first; a tracking failure is returned as a
warning and cannot erase them. Never claim MLflow success without inspecting
the tracking files or UI.

MLflow is intentionally optional to keep the normal Colab install small. If
you want local tracking, install it after the successful training dependency
install with `!/content/atlasai-py311/bin/python -m pip install -r training/requirements-mlflow.txt`.

This is sequence classification, so autoregressive generation, sampling,
temperature, top-k/top-p, greedy decoding, KV cache, and continuous batching
are not used by this model. The API performs single-request sequence
classification; those generation/serving concepts apply to a different model
shape and are intentionally out of scope here.
