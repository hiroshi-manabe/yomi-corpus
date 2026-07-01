from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from yomi_corpus.yomi.final_review import (
    REVIEW_STAGE,
    STRONG_REPAIR_REVIEW_STAGE,
    store_review_submission,
)


ATTACHMENT_RE = re.compile(r"https://github\.com/user-attachments/files/\d+/[A-Za-z0-9._-]+\.json")
FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def import_issue(
    *,
    repo: str,
    issue_number: int,
    review_pack_root: Path,
    submission_store_dir: Path,
    review_stage: str = REVIEW_STAGE,
) -> dict:
    issue_payload = fetch_issue(repo, issue_number)
    comment_payloads = fetch_issue_comments(repo, issue_number)
    return import_issue_payloads(
        issue_payload=issue_payload,
        comment_payloads=comment_payloads,
        repo=repo,
        issue_number=issue_number,
        review_pack_root=review_pack_root,
        submission_store_dir=submission_store_dir,
        review_stage=review_stage,
    )


def import_open_issue_inbox(
    *,
    repo: str,
    review_pack_root: Path,
    submission_store_dir: Path,
    review_stage: str = REVIEW_STAGE,
) -> dict:
    issues = fetch_open_issues(repo, state="open")
    summaries = []
    skipped = []
    seen_submission_ids: set[str] = set()
    attachment_count = 0
    inline_submission_count = 0
    open_issue_count = 0

    for issue in issues:
        if "pull_request" in issue:
            continue
        open_issue_count += 1
        issue_number = int(issue["number"])
        comments = fetch_issue_comments(repo, issue_number)
        attachments = extract_attachment_records(issue, comments)
        inline_submissions = extract_inline_submission_records(issue, comments)
        attachment_count += len(attachments)
        inline_submission_count += len(inline_submissions)

        for attachment in attachments:
            submission = download_submission(attachment["url"])
            process_submission_record(
                submission,
                source_record=attachment,
                repo=repo,
                issue_number=issue_number,
                review_pack_root=review_pack_root,
                submission_store_dir=submission_store_dir,
                review_stage=review_stage,
                seen_submission_ids=seen_submission_ids,
                summaries=summaries,
                skipped=skipped,
            )

        for inline_record in inline_submissions:
            submission = dict(inline_record["submission"])
            process_submission_record(
                submission,
                source_record=inline_record,
                repo=repo,
                issue_number=issue_number,
                review_pack_root=review_pack_root,
                submission_store_dir=submission_store_dir,
                review_stage=review_stage,
                seen_submission_ids=seen_submission_ids,
                summaries=summaries,
                skipped=skipped,
            )

    return {
        "repo": repo,
        "open_issue_count": open_issue_count,
        "review_stage": review_stage,
        "attachment_count": attachment_count,
        "inline_submission_count": inline_submission_count,
        "imported_submission_count": len(summaries),
        "summaries": summaries,
        "skipped": skipped,
    }


def import_issue_payloads(
    *,
    issue_payload: dict,
    comment_payloads: list[dict],
    repo: str,
    issue_number: int,
    review_pack_root: Path,
    submission_store_dir: Path,
    review_stage: str = REVIEW_STAGE,
) -> dict:
    attachments = extract_attachment_records(issue_payload, comment_payloads)
    inline_submissions = extract_inline_submission_records(issue_payload, comment_payloads)
    if not attachments and not inline_submissions:
        raise SystemExit("No JSON review submissions found in the issue body or comments.")

    summaries: list[dict] = []
    skipped: list[dict] = []
    seen_submission_ids: set[str] = set()
    for attachment in attachments:
        submission = download_submission(attachment["url"])
        process_submission_record(
            submission,
            source_record=attachment,
            repo=repo,
            issue_number=issue_number,
            review_pack_root=review_pack_root,
            submission_store_dir=submission_store_dir,
            review_stage=review_stage,
            seen_submission_ids=seen_submission_ids,
            summaries=summaries,
            skipped=skipped,
        )

    for inline_record in inline_submissions:
        submission = dict(inline_record["submission"])
        process_submission_record(
            submission,
            source_record=inline_record,
            repo=repo,
            issue_number=issue_number,
            review_pack_root=review_pack_root,
            submission_store_dir=submission_store_dir,
            review_stage=review_stage,
            seen_submission_ids=seen_submission_ids,
            summaries=summaries,
            skipped=skipped,
        )

    return {
        "repo": repo,
        "issue_number": issue_number,
        "review_stage": review_stage,
        "attachment_count": len(attachments),
        "inline_submission_count": len(inline_submissions),
        "imported_submission_count": len(summaries),
        "summaries": summaries,
        "skipped": skipped,
    }


def fetch_issue(repo: str, issue_number: int) -> dict:
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
    payload = fetch_json(url)
    if not isinstance(payload, dict):
        raise SystemExit(f"Expected GitHub issue object from {url}")
    return payload


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


def extract_inline_submission_records(issue_payload: dict, comment_payloads: list[dict]) -> list[dict]:
    rows: list[dict] = []
    ordered_payloads = [("issue", issue_payload)] + [("comment", row) for row in comment_payloads]
    for source_kind, payload in ordered_payloads:
        body = str(payload.get("body", ""))
        for submission in parse_submissions_from_text(body):
            rows.append(
                {
                    "source_kind": source_kind,
                    "issue_number": int(issue_payload.get("number", 0)),
                    "comment_id": payload.get("id") if source_kind == "comment" else None,
                    "submission": submission,
                }
            )
    return rows


def parse_submissions_from_text(text: str) -> list[dict]:
    submissions: list[dict] = []
    seen_keys: set[str] = set()
    candidates = [match.group(1).strip() for match in FENCED_JSON_RE.finditer(text)]
    candidates.extend(scan_json_object_strings(text))

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not is_review_submission_like(payload):
            continue
        key = str(payload.get("submission_id") or json.dumps(payload, sort_keys=True))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        submissions.append(payload)
    return submissions


def scan_json_object_strings(text: str) -> list[str]:
    decoder = json.JSONDecoder()
    rows: list[str] = []
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            _, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        rows.append(text[index : index + end])
    return rows


def is_review_submission_like(payload: object) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("submission_type") == "review_patch"
        and "review_stage" in payload
        and "pack_id" in payload
        and "submission_id" in payload
    )


def process_submission_record(
    submission: dict,
    *,
    source_record: dict,
    repo: str,
    issue_number: int,
    review_pack_root: Path,
    submission_store_dir: Path,
    review_stage: str,
    seen_submission_ids: set[str],
    summaries: list[dict],
    skipped: list[dict],
) -> None:
    if str(submission.get("submission_type")) != "review_patch":
        skipped.append({"reason": "wrong_submission_type", "source": source_record})
        return
    if str(submission.get("review_stage")) != review_stage:
        skipped.append({"reason": "wrong_review_stage", "source": source_record})
        return
    submission_id = str(submission.get("submission_id", ""))
    if not submission_id:
        skipped.append({"reason": "missing_submission_id", "source": source_record})
        return
    if submission_id in seen_submission_ids:
        skipped.append(
            {
                "reason": "duplicate_submission_id",
                "source": source_record,
                "submission_id": submission_id,
            }
        )
        return
    pack_id = str(submission.get("pack_id") or "")
    pack_path = resolve_review_pack_path(review_pack_root, pack_id, review_stage=review_stage)
    if pack_path is None:
        skipped.append(
            {
                "reason": "unknown_pack_id",
                "source": source_record,
                "pack_id": pack_id,
                "submission_id": submission_id,
            }
        )
        return

    seen_submission_ids.add(submission_id)
    source_issue = {
        "repo": repo,
        "issue_number": issue_number,
        "comment_id": source_record.get("comment_id"),
    }
    if "url" in source_record:
        source_issue["attachment_url"] = source_record["url"]
    submission["_source_issue"] = source_issue
    stored_path = store_review_submission(
        submission,
        submission_store_dir=submission_store_dir,
    )
    summaries.append(
        {
            "submission_id": submission_id,
            "pack_id": pack_id,
            "review_stage": review_stage,
            "stored_path": str(stored_path),
            "review_pack_path": str(pack_path),
            "source": source_record,
        }
    )


def resolve_review_pack_path(
    review_pack_root: Path,
    pack_id: str,
    *,
    review_stage: str = REVIEW_STAGE,
) -> Path | None:
    if not pack_id:
        return None
    candidates = [
        review_pack_root / f"{pack_id}.json",
    ]
    stage_dir = review_stage_directory(review_stage)
    if stage_dir:
        candidates.insert(0, review_pack_root / stage_dir / f"{pack_id}.json")
    for path in candidates:
        if path.exists():
            return path
    return None


def review_stage_directory(review_stage: str) -> str | None:
    if review_stage == REVIEW_STAGE:
        return "yomi_final"
    if review_stage == STRONG_REPAIR_REVIEW_STAGE:
        return "yomi_strong_repair"
    return None


def download_submission(url: str) -> dict:
    request = Request(url, headers={"User-Agent": "yomi-corpus-review-importer"})
    try:
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise SystemExit(f"HTTP {exc.code} while downloading attachment {url}") from exc
    except URLError as exc:
        raise SystemExit(f"Network error while downloading attachment {url}: {exc.reason}") from exc
