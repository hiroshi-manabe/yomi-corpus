#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from yomi_corpus.alphabetic_review import apply_alphabetic_review_submission, write_json
from import_alphabetic_review_issue import (
    download_submission,
    extract_attachment_records,
    extract_inline_submission_records,
    fetch_issue_comments,
    fetch_open_issues,
    process_submission_record,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import alphabetic review submissions from all open GitHub issues."
    )
    parser.add_argument(
        "--repo",
        default="hiroshi-manabe/yomi-corpus",
        help="GitHub repository in owner/name form.",
    )
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
        default="data/state/alphabetic/last_review_inbox_import_summary.json",
        help="Path to write the aggregate import summary JSON.",
    )
    parser.add_argument(
        "--review-stage",
        default="alphabetic_candidate_review",
        help="Review stage to import.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    issues = fetch_open_issues(args.repo, state="open")
    attachments = []
    inline_submissions = []
    for issue in issues:
        if "pull_request" in issue:
            continue
        comments = fetch_issue_comments(args.repo, int(issue["number"]))
        attachments.extend(extract_attachment_records(issue, comments))
        inline_submissions.extend(extract_inline_submission_records(issue, comments))

    summaries = []
    skipped = []
    seen_submission_ids: set[str] = set()
    for attachment in attachments:
        submission = download_submission(attachment["url"])
        process_submission_record(
            submission,
            source_record=attachment,
            repo=args.repo,
            issue_number=attachment["issue_number"],
            review_pack_root=PROJECT_ROOT / args.review_pack_root,
            submission_store_dir=PROJECT_ROOT / args.submission_store_dir,
            decisions_jsonl=PROJECT_ROOT / args.decisions_jsonl,
            seen_submission_ids=seen_submission_ids,
            summaries=summaries,
            skipped=skipped,
        )

    for inline_record in inline_submissions:
        submission = dict(inline_record["submission"])
        process_submission_record(
            submission,
            source_record=inline_record,
            repo=args.repo,
            issue_number=inline_record["issue_number"],
            review_pack_root=PROJECT_ROOT / args.review_pack_root,
            submission_store_dir=PROJECT_ROOT / args.submission_store_dir,
            decisions_jsonl=PROJECT_ROOT / args.decisions_jsonl,
            seen_submission_ids=seen_submission_ids,
            summaries=summaries,
            skipped=skipped,
        )

    aggregate = {
        "repo": args.repo,
        "open_issue_count": len([issue for issue in issues if "pull_request" not in issue]),
        "attachment_count": len(attachments),
        "inline_submission_count": len(inline_submissions),
        "imported_submission_count": len(summaries),
        "review_stage": args.review_stage,
        "summaries": summaries,
        "skipped": skipped,
    }
    write_json(PROJECT_ROOT / args.summary_json, aggregate)
    print(json.dumps(aggregate, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
