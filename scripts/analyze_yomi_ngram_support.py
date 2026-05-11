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

from yomi_corpus.yomi.ngram_diagnostics import (
    analyze_batch_ngram_support,
    analyze_override_without_whitelist,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze N-gram support in an existing aligned_hybrid yomi JSONL. "
            "This is a debug helper and does not advance pipeline state."
        )
    )
    parser.add_argument(
        "batch_dir",
        nargs="?",
        default="data/units/batch_0001",
        help="Batch directory containing units.yomi.aligned_hybrid.jsonl.",
    )
    parser.add_argument(
        "--output-dir",
        help="Optional output directory. Defaults to <batch-dir>/debug.",
    )
    parser.add_argument(
        "--override-without-whitelist",
        action="store_true",
        help="Also write same-surface reading override candidates with the surface whitelist removed.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = analyze_batch_ngram_support(
        batch_dir=args.batch_dir,
        output_dir=args.output_dir,
    )
    if args.override_without_whitelist:
        summary["override_without_whitelist"] = analyze_override_without_whitelist(
            batch_dir=args.batch_dir,
            output_dir=args.output_dir,
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
