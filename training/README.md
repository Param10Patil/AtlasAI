# Optional LoRA training

Training is separate from runtime inference. The intended exercise is a small
incident-category classifier with PEFT/LoRA and a six-label synthetic dataset.
Training runs on a developer machine or Colab, records parameters/metrics to
MLflow when available, and exports an adapter version. The API treats a missing
adapter as a declared rule-based fallback; it never trains during startup.

The runtime image must not install training dependencies, model weights, or
MLflow. See `COLAB.md` for the copy/paste CPU/Colab commands, `dataset/DATASET.md`
for provenance, and `docs/lora.md`, `docs/mlflow.md`, and
`docs/development-notes.md` for the design and honest verification status.
