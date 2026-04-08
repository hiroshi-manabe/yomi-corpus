#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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

ATTACHMENT_RE = re.compile(
    r"https://github\.com/user-attachments/files/\d+/[A-Za-z0-9._-]+\.json"
)


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
    attachment_urls = extract_attachment_urls([issue_payload] + comment_payloads)
    if not attachment_urls:
        raise SystemExit("No JSON attachment URLs found in the issue body or comments.")

    summaries = []
    for url in attachment_urls:
        submission = download_submission(url)
        if str(submission.get("submission_type")) != "review_patch":
            continue
        if str(submission.get("review_stage")) != "alphabetic_candidate_review":
            continue

        pack_path = find_review_pack(
            PROJECT_ROOT / args.review_pack_root,
            str(submission["pack_id"]),
        )
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
        summaries.append(
            build_review_import_summary(
                submission,
                stored_path=str(stored_path),
                pack=pack,
                effective_item_states=effective_item_states,
                promoted_decisions=promoted_decisions,
            )
        )

    aggregate = {
        "repo": args.repo,
        "issue_number": args.issue_number,
        "attachment_count": len(attachment_urls),
        "imported_submission_count": len(summaries),
        "summaries": summaries,
    }
    write_json(PROJECT_ROOT / args.summary_json, aggregate)
    print(json.dumps(aggregate, ensure_ascii=False, indent=2))


def fetch_issue(repo: str, issue_number: int) -> dict:
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
    return fetch_json(url)


def fetch_issue_comments(repo: str, issue_number: int) -> list[dict]:
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments?per_page=100"
    return fetch_json(url)


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
    urls: list[str] = []
    seen: set[str] = set()
    for payload in payloads:
        body = str(payload.get("body", ""))
        for url in ATTACHMENT_RE.findall(body):
            if url in seen:
                continue
            seen.add(url)
            urls.append(url)
    return urls


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
