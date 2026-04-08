#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from yomi_corpus.alphabetic_review import build_promotion_candidates, load_jsonl, write_jsonl
from yomi_corpus.alphabetic_state import load_alphabetic_decisions


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build alphabetic promotion candidates from LLM judgments.")
    parser.add_argument(
        "--input-jsonl",
        default="data/state/alphabetic/llm_judgments.jsonl",
        help="Alphabetic LLM judgment ledger path relative to repo root.",
    )
    parser.add_argument(
        "--decisions-jsonl",
        default="data/state/alphabetic/token_decisions.jsonl",
        help="Existing global alphabetic decisions path relative to repo root.",
    )
    parser.add_argument(
        "--output-jsonl",
        default="data/state/alphabetic/promotion_candidates.jsonl",
        help="Promotion candidate JSONL path relative to repo root.",
    )
    parser.add_argument(
        "--threshold-observations",
        type=int,
        default=3,
        help="Minimum number of consistent observations needed to surface a candidate.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    judgments = load_jsonl(PROJECT_ROOT / args.input_jsonl)
    decisions = load_alphabetic_decisions(PROJECT_ROOT / args.decisions_jsonl)
    candidates = build_promotion_candidates(
        judgments,
        threshold_observations=args.threshold_observations,
        existing_decisions=decisions,
    )
    write_jsonl(PROJECT_ROOT / args.output_jsonl, [asdict(candidate) for candidate in candidates])
    print(f"promotion_candidates={len(candidates)}")


if __name__ == "__main__":
    main()
