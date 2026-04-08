#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from yomi_corpus.alphabetic_review import (
    apply_alphabetic_review_submission,
    write_json,
)

ATTACHMENT_RE = re.compile(r"https://github\.com/user-attachments/files/\d+/[A-Za-z0-9._-]+\.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import alphabetic review submissions from JSON attachments in a GitHub issue."
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
        help="Issue number containing review submission attachments.",
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
        default="data/state/alphabetic/last_review_import_summary.json",
        help="Path to write the aggregate import summary JSON.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    issue_payload = fetch_issue(args.repo, args.issue_number)
    comment_payloads = fetch_issue_comments(args.repo, args.issue_number)
    attachments = extract_attachment_records(issue_payload, comment_payloads)
    if not attachments:
        raise SystemExit("No JSON attachment URLs found in the issue body or comments.")

    summaries = []
    seen_submission_ids: set[str] = set()
    skipped = []
    for attachment in attachments:
        submission = download_submission(attachment["url"])
        if str(submission.get("submission_type")) != "review_patch":
            skipped.append({"reason": "wrong_submission_type", "attachment": attachment})
            continue
        if str(submission.get("review_stage")) != "alphabetic_candidate_review":
            skipped.append({"reason": "wrong_review_stage", "attachment": attachment})
            continue
        submission_id = str(submission.get("submission_id", ""))
        if not submission_id:
            skipped.append({"reason": "missing_submission_id", "attachment": attachment})
            continue
        if submission_id in seen_submission_ids:
            skipped.append(
                {
                    "reason": "duplicate_submission_id",
                    "attachment": attachment,
                    "submission_id": submission_id,
                }
            )
            continue
        seen_submission_ids.add(submission_id)
        submission["_source_issue"] = {
            "repo": args.repo,
            "issue_number": args.issue_number,
            "comment_id": attachment.get("comment_id"),
            "attachment_url": attachment["url"],
        }
        try:
            summaries.append(
                apply_alphabetic_review_submission(
                    submission,
                    review_pack_root=PROJECT_ROOT / args.review_pack_root,
                    submission_store_dir=PROJECT_ROOT / args.submission_store_dir,
                    decisions_jsonl=PROJECT_ROOT / args.decisions_jsonl,
                )
            )
        except FileNotFoundError:
            skipped.append(
                {
                    "reason": "unknown_pack_id",
                    "attachment": attachment,
                    "pack_id": submission.get("pack_id"),
                    "submission_id": submission_id,
                }
            )
            continue

    aggregate = {
        "repo": args.repo,
        "issue_number": args.issue_number,
        "attachment_count": len(attachments),
        "imported_submission_count": len(summaries),
        "summaries": summaries,
        "skipped": skipped,
    }
    write_json(PROJECT_ROOT / args.summary_json, aggregate)
    print(json.dumps(aggregate, ensure_ascii=False, indent=2))


def fetch_issue(repo: str, issue_number: int) -> dict:
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
    return fetch_json(url)


def fetch_issue_comments(repo: str, issue_number: int) -> list[dict]:
    page = 1
    rows: list[dict] = []
    while True:
        url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments?per_page=100&page={page}"
        payload = fetch_json(url)
        if not isinstance(payload, list) or not payload:
            break
        rows.extend(payload)
        page += 1
    return rows


def fetch_open_issues(repo: str, *, state: str = "open") -> list[dict]:
    page = 1
    rows: list[dict] = []
    while True:
        url = f"https://api.github.com/repos/{repo}/issues?state={state}&per_page=100&page={page}"
        payload = fetch_json(url)
        if not isinstance(payload, list) or not payload:
            break
        rows.extend(payload)
        page += 1
    return rows


def fetch_json(url: str) -> dict | list[dict]:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "yomi-corpus-review-importer",
        },
    )
    try:
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise SystemExit(f"HTTP {exc.code} while fetching {url}") from exc
    except URLError as exc:
        raise SystemExit(f"Network error while fetching {url}: {exc.reason}") from exc


def extract_attachment_urls(payloads: list[dict]) -> list[str]:
    return [row["url"] for row in extract_attachment_records(payloads[0], payloads[1:])]


def extract_attachment_records(issue_payload: dict, comment_payloads: list[dict]) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    ordered_payloads = [("issue", issue_payload)] + [("comment", row) for row in comment_payloads]
    for source_kind, payload in ordered_payloads:
        body = str(payload.get("body", ""))
        for url in ATTACHMENT_RE.findall(body):
            if url in seen:
                continue
            seen.add(url)
            rows.append(
                {
                    "url": url,
                    "source_kind": source_kind,
                    "issue_number": int(issue_payload.get("number", 0)),
                    "comment_id": payload.get("id") if source_kind == "comment" else None,
                }
            )
    return rows


def download_submission(url: str) -> dict:
    request = Request(url, headers={"User-Agent": "yomi-corpus-review-importer"})
    try:
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise SystemExit(f"HTTP {exc.code} while downloading attachment {url}") from exc
    except URLError as exc:
        raise SystemExit(f"Network error while downloading attachment {url}: {exc.reason}") from exc


if __name__ == "__main__":
    main()
