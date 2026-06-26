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

from yomi_corpus.yomi.final_review_issue_import import import_issue


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import yomi final review submissions from a GitHub issue."
    )
    parser.add_argument(
        "--repo",
        default="hiroshi-manabe/yomi-corpus",
        help="GitHub repository in owner/name form.",
    )
    parser.add_argument(
        "--issue-number",
        type=int,
        required=True,
        help="Issue number containing review submission attachments or inline JSON.",
    )
    parser.add_argument(
        "--review-pack-root",
        default="data/review_packs",
        help="Root directory containing source review pack JSON files.",
    )
    parser.add_argument(
        "--submission-store-dir",
        default="data/review_submissions/yomi_final",
        help="Directory where imported yomi final review submissions are stored.",
    )
    parser.add_argument(
        "--summary-json",
        default="data/state/yomi_final/last_review_import_summary.json",
        help="Path to write the aggregate import summary JSON.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = import_issue(
        repo=args.repo,
        issue_number=args.issue_number,
        review_pack_root=PROJECT_ROOT / args.review_pack_root,
        submission_store_dir=PROJECT_ROOT / args.submission_store_dir,
    )
    write_json(PROJECT_ROOT / args.summary_json, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
