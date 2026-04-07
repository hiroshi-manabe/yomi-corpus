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

from yomi_corpus.llm.experiments import compare_prompt_experiments


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare two prompt experiment runs.")
    parser.add_argument("--base-run-dir", required=True, help="Base experiment run directory.")
    parser.add_argument("--candidate-run-dir", required=True, help="Candidate experiment run directory.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    comparison = compare_prompt_experiments(args.base_run_dir, args.candidate_run_dir)
    print(json.dumps(comparison, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
