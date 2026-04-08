#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from yomi_corpus.alphabetic_review import (
    append_alphabetic_llm_judgments,
    build_llm_judgments_from_results,
    load_jsonl,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest alphabetic-entity LLM judgments into persistent state.")
    parser.add_argument(
        "--input-jsonl",
        required=True,
        help="Parsed LLM results JSONL path relative to repo root.",
    )
    parser.add_argument(
        "--batch-name",
        required=True,
        help="Batch name recorded in the judgment ledger.",
    )
    parser.add_argument(
        "--output-jsonl",
        default="data/state/alphabetic/llm_judgments.jsonl",
        help="Persistent judgment ledger path relative to repo root.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = PROJECT_ROOT / args.input_jsonl
    output_path = PROJECT_ROOT / args.output_jsonl
    rows = load_jsonl(input_path)
    judgments = build_llm_judgments_from_results(
        rows,
        batch_name=args.batch_name,
        source_path=args.input_jsonl,
    )
    append_alphabetic_llm_judgments(output_path, judgments)
    print(f"ingested_judgments={len(judgments)}")


if __name__ == "__main__":
    main()
