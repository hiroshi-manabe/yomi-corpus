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
    AlphabeticPromotionCandidate,
    build_review_pack,
    load_jsonl,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a review-pack JSON for alphabetic promotion candidates.")
    parser.add_argument(
        "--input-jsonl",
        default="data/state/alphabetic/promotion_candidates.jsonl",
        help="Promotion candidate JSONL path relative to repo root.",
    )
    parser.add_argument(
        "--output-json",
        required=True,
        help="Review-pack JSON path relative to repo root.",
    )
    parser.add_argument(
        "--pack-id",
        required=True,
        help="Stable pack identifier.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = load_jsonl(PROJECT_ROOT / args.input_jsonl)
    candidates = [AlphabeticPromotionCandidate(**row) for row in rows]
    pack = build_review_pack(candidates, pack_id=args.pack_id)
    write_json(PROJECT_ROOT / args.output_json, pack)
    print(f"review_pack_items={pack['item_count']}")


if __name__ == "__main__":
    main()
