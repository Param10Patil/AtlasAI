# Reproducible Colab training

The repository supplies the complete command; Colab is only an optional
compute host. It does not receive credentials and it never changes the API
runtime. The dataset is 120 synthetic, reviewed examples: 20 per label.

1. Open a new Google Colab notebook and choose a CPU (or a free GPU runtime).
2. Upload or clone this repository, then run:

```bash
%cd /content/AtlasAI
!python -m pip install -r training/requirements.txt
!python training/train_lora.py --seed 42 --epochs 3 --batch-size 8 \
  --output-dir training/artifacts/opspilot-lora
!python training/evaluate.py training/artifacts/opspilot-lora
```

The command creates `adapter_config.json`, adapter weights, tokenizer files,
`label_map.json`, `training_metadata.json`, `metrics.json`, and
`artifact_manifest.json`. The manifest and SHA-256 value are checked before
runtime loading. Download the entire `training/artifacts/opspilot-lora`
directory (zip it first if needed) and set `OPSPILOT_LORA_ADAPTER_PATH` to its
local path. Never commit model weights or MLflow runs; `.gitignore` excludes
them.

For optional tracking, start a local MLflow server or use a notebook-local
file store and pass `--tracking-uri`. A tracking failure is reported as a
warning and cannot discard the local metrics/artifact. Incident text is never
logged as an MLflow parameter.

This project cannot start Colab or approve a Google account from the local
agent. The training run is therefore unverified until the commands above are
run and their measured output is inspected.
