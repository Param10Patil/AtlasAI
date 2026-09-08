"""CLI seam for evaluating a saved classifier adapter."""

from argparse import ArgumentParser
from pathlib import Path


def main() -> int:
    parser = ArgumentParser(description="Evaluate an OpsPilot classifier adapter")
    parser.add_argument("adapter", type=Path)
    args = parser.parse_args()
    if not args.adapter.exists():
        parser.error(f"adapter not found: {args.adapter}")
    print("Evaluation is intentionally separate from API startup and deployment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
