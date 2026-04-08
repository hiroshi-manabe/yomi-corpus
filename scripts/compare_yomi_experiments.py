#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from yomi_corpus.yomi.experiments import compare_yomi_experiments


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare two yomi experiment runs.")
    parser.add_argument("--base-run-dir", required=True)
    parser.add_argument("--candidate-run-dir", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    comparison = compare_yomi_experiments(
        base_run_dir=PROJECT_ROOT / args.base_run_dir,
        candidate_run_dir=PROJECT_ROOT / args.candidate_run_dir,
    )
    print(json.dumps(comparison, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
