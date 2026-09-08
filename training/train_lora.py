"""CLI seam for the optional classifier experiment."""

from argparse import ArgumentParser
from pathlib import Path


def main() -> int:
    parser = ArgumentParser(description="Run the optional OpsPilot LoRA experiment")
    parser.add_argument("--dataset", type=Path, default=Path("training/dataset/incidents.jsonl"))
    args = parser.parse_args()
    if not args.dataset.exists():
        parser.error(f"dataset not found: {args.dataset}")
    print("Training is isolated from the runtime; the PEFT loop belongs to the training pass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
