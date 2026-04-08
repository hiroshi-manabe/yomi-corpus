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
    build_review_import_summary,
    build_review_promoted_decisions,
    find_review_pack,
    load_json,
    load_review_submissions,
    replay_review_submissions,
    rewrite_alphabetic_decisions_with_review_promotions,
    store_review_submission,
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
    if str(submission.get("submission_type")) != "review_patch":
        raise SystemExit("Expected submission_type=review_patch")
    if str(submission.get("review_stage")) != "alphabetic_candidate_review":
        raise SystemExit("Expected review_stage=alphabetic_candidate_review")

    pack_path = find_review_pack(PROJECT_ROOT / args.review_pack_root, str(submission["pack_id"]))
    pack = load_json(pack_path)

    stored_path = store_review_submission(
        submission,
        submission_store_dir=PROJECT_ROOT / args.submission_store_dir,
    )

    all_submissions = load_review_submissions(
        PROJECT_ROOT / args.submission_store_dir,
        review_stage=str(submission["review_stage"]),
        pack_id=str(submission["pack_id"]),
    )
    effective_item_states = replay_review_submissions(pack, all_submissions)
    promoted_decisions = build_review_promoted_decisions(pack, effective_item_states)
    rewrite_alphabetic_decisions_with_review_promotions(
        PROJECT_ROOT / args.decisions_jsonl,
        promoted_decisions,
    )

    summary = build_review_import_summary(
        submission,
        stored_path=str(stored_path),
        pack=pack,
        effective_item_states=effective_item_states,
        promoted_decisions=promoted_decisions,
    )
    write_json(PROJECT_ROOT / args.summary_json, summary)
    print(summary)


if __name__ == "__main__":
    main()
