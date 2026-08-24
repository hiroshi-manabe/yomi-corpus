from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from yomi_corpus.yomi.final_review_issue_import import (
    download_submission,
    extract_attachment_records,
    extract_inline_submission_records,
    fetch_issue_comments,
    fetch_open_issues,
    is_review_submission_like,
    resolve_review_pack_path,
)


WATCH_RETRY_SECONDS = 300


def run_issue_watch_pass(
    root: Path,
    *,
    track_name: str,
    repo: str,
    now_epoch: int | None = None,
    mark_triggered: bool = True,
    fetch_issues: Callable[..., list[dict]] = fetch_open_issues,
    fetch_comments: Callable[[str, int], list[dict]] = fetch_issue_comments,
    download: Callable[[str], dict] = download_submission,
) -> dict[str, Any]:
    now = int(now_epoch if now_epoch is not None else time.time())
    state_dir = root / "data" / "state" / "issue_watch"
    ledger_path = state_dir / f"{track_name}.ledger.json"
    acknowledgment_path = state_dir / f"{track_name}.acknowledgments.json"
    previous = _read_object(ledger_path)
    records = {
        str(row["record_id"]): dict(row)
        for row in previous.get("records", [])
        if isinstance(row, dict) and row.get("record_id")
    }

    seen_ids: set[str] = set()
    invalid_count = 0
    issue_count = 0
    for issue in fetch_issues(repo, state="open"):
        if "pull_request" in issue:
            continue
        issue_count += 1
        issue_number = int(issue["number"])
        comments = fetch_comments(repo, issue_number)
        candidates: list[tuple[dict, dict]] = []
        for source in extract_attachment_records(issue, comments):
            candidates.append((download(source["url"]), source))
        for source in extract_inline_submission_records(issue, comments):
            candidates.append((dict(source["submission"]), source))
        for submission, source in candidates:
            for child in _flatten_submissions(submission):
                row = _submission_record(root, child, source, issue_number, now)
                if row is None:
                    invalid_count += 1
                    continue
                record_id = row["record_id"]
                seen_ids.add(record_id)
                old = records.get(record_id, {})
                row["first_seen_epoch"] = int(old.get("first_seen_epoch") or now)
                row["last_seen_epoch"] = int(old.get("last_seen_epoch") or now)
                row["last_triggered_epoch"] = old.get("last_triggered_epoch")
                records[record_id] = row

    active: list[dict] = []
    trigger_candidates: list[dict] = []
    for record_id, row in list(records.items()):
        if record_id not in seen_ids:
            # Closed or deleted issues stop being advertised; the importer remains
            # authoritative for any submission already stored locally.
            del records[record_id]
            continue
        active.append(row)
        last_triggered = row.get("last_triggered_epoch")
        if last_triggered is None or now - int(last_triggered) >= WATCH_RETRY_SECONDS:
            trigger_candidates.append(row)

    conflicts = _conflicting_doc_ids(active)
    previous_acknowledgments = _read_object(acknowledgment_path)
    acknowledgments = {
        "schema_version": 1,
        "state_revision": int(previous_acknowledgments.get("state_revision") or 0) + 1,
        "track_name": track_name,
        "generated_at_epoch": now,
        "records": [
            {
                **_public_record(row),
                "conflict": bool(set(row.get("doc_ids", [])) & conflicts),
            }
            for row in sorted(active, key=lambda value: (value["issue_number"], value["record_id"]))
        ],
        "conflicting_doc_ids": sorted(conflicts),
    }

    state_dir.mkdir(parents=True, exist_ok=True)
    changed = _write_if_semantically_changed(
        acknowledgment_path,
        acknowledgments,
        volatile={"generated_at_epoch", "state_revision"},
    )
    if trigger_candidates and mark_triggered:
        for row in trigger_candidates:
            row["last_triggered_epoch"] = now
    ledger = {
        "schema_version": 1,
        "track_name": track_name,
        "updated_at_epoch": now,
        "records": sorted(records.values(), key=lambda value: value["record_id"]),
    }
    _write_if_semantically_changed(ledger_path, ledger, volatile={"updated_at_epoch"})
    return {
        "status": "changed" if changed else "unchanged",
        "issue_count": issue_count,
        "acknowledgment_count": len(active),
        "conflict_count": len(conflicts),
        "invalid_submission_count": invalid_count,
        "trigger_required": bool(trigger_candidates),
        "acknowledgment_path": str(acknowledgment_path),
    }


def trigger_review_sync_service(track_name: str) -> dict[str, Any]:
    unit = f"yomi-corpus-review-sync-{track_name}.service"
    completed = subprocess.run(
        ["systemctl", "--user", "start", "--no-block", unit],
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "unit": unit,
        "returncode": completed.returncode,
        "stderr": completed.stderr.strip(),
    }


def _flatten_submissions(submission: dict) -> list[dict]:
    if submission.get("submission_type") != "review_bundle":
        return [submission]
    return [row for row in submission.get("submissions", []) if isinstance(row, dict)]


def _submission_record(
    root: Path,
    submission: dict,
    source: dict,
    issue_number: int,
    now: int,
) -> dict[str, Any] | None:
    if not is_review_submission_like(submission) or submission.get("submission_type") == "review_bundle":
        return None
    submission_id = str(submission.get("submission_id") or "")
    stage = str(submission.get("review_stage") or "")
    if not submission_id or stage not in {
        "yomi_final_review",
        "yomi_strong_repair_review",
        "finalized_correction",
    }:
        return None
    doc_ids = _submission_doc_ids(submission)
    if not doc_ids:
        return None
    if stage != "finalized_correction":
        pack_id = str(submission.get("pack_id") or "")
        pack_path = resolve_review_pack_path(
            root / "data" / "review_packs",
            pack_id,
            review_stage=stage,
        )
        if pack_path is None:
            return None
        try:
            pack = json.loads(pack_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        known_doc_ids = {
            str(row.get("doc_id"))
            for row in pack.get("documents", [])
            if isinstance(row, dict) and row.get("doc_id")
        }
        if not set(doc_ids) <= known_doc_ids:
            return None
    canonical = json.dumps(submission, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "record_id": f"{submission_id}:{payload_hash[:16]}",
        "submission_id": submission_id,
        "payload_hash": payload_hash,
        "review_stage": stage,
        "pack_id": str(submission.get("pack_id") or ""),
        "issue_number": issue_number,
        "comment_id": source.get("comment_id"),
        "doc_ids": doc_ids,
        "first_seen_epoch": now,
        "last_seen_epoch": now,
        "last_triggered_epoch": None,
    }


def _submission_doc_ids(submission: dict) -> list[str]:
    values: list[object] = []
    task = submission.get("task")
    if isinstance(task, dict):
        values.extend(task.get("doc_ids") or [])
    for key in ("items", "units"):
        for row in submission.get(key) or []:
            if isinstance(row, dict):
                values.append(row.get("doc_id"))
    return sorted({str(value) for value in values if value})


def _conflicting_doc_ids(records: list[dict]) -> set[str]:
    owners: dict[str, set[str]] = {}
    for row in records:
        for doc_id in row.get("doc_ids", []):
            owners.setdefault(str(doc_id), set()).add(str(row["submission_id"]))
    return {doc_id for doc_id, submissions in owners.items() if len(submissions) > 1}


def _public_record(row: dict) -> dict:
    return {
        key: row.get(key)
        for key in (
            "submission_id",
            "review_stage",
            "pack_id",
            "issue_number",
            "comment_id",
            "doc_ids",
            "first_seen_epoch",
            "last_seen_epoch",
        )
    }


def _read_object(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_if_semantically_changed(path: Path, payload: dict, *, volatile: set[str]) -> bool:
    previous = _read_object(path)
    comparable_previous = {key: value for key, value in previous.items() if key not in volatile}
    comparable_payload = {key: value for key, value in payload.items() if key not in volatile}
    if comparable_previous == comparable_payload:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True
