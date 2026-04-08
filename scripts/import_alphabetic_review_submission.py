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
    apply_alphabetic_review_submission,
    load_json,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import one alphabetic promotion-candidate review submission."
    )
    parser.add_argument("submission_json", help="Path to a downloaded review submission JSON file.")
    parser.add_argument(
        "--review-pack-root",
        default="data/review_packs",
        help="Root directory containing source review pack JSON files.",
    )
    parser.add_argument(
        "--submission-store-dir",
        default="data/review_submissions/alphabetic_candidate_review",
        help="Directory where imported review submissions are stored.",
    )
    parser.add_argument(
        "--decisions-jsonl",
        default="data/state/alphabetic/token_decisions.jsonl",
        help="Global alphabetic decision registry path.",
    )
    parser.add_argument(
        "--summary-json",
        default="data/state/alphabetic/last_review_import_summary.json",
        help="Path to write the import summary JSON.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    submission = load_json(args.submission_json)
    summary = apply_alphabetic_review_submission(
        submission,
        review_pack_root=PROJECT_ROOT / args.review_pack_root,
        submission_store_dir=PROJECT_ROOT / args.submission_store_dir,
        decisions_jsonl=PROJECT_ROOT / args.decisions_jsonl,
    )
    write_json(PROJECT_ROOT / args.summary_json, summary)
    print(summary)


if __name__ == "__main__":
    main()
