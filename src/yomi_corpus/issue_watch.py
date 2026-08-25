from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from yomi_corpus.yomi.final_review import sanitize_submission_id
from yomi_corpus.yomi.final_review_issue_import import (
    download_submission,
    extract_attachment_records,
    extract_inline_submission_records,
    fetch_issue_comments,
    fetch_open_issues,
    is_review_submission_like,
    resolve_review_pack_path,
)


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
    published_state_cache: dict[str, dict[str, str] | None] = {}
    for record_id, row in list(records.items()):
        if record_id not in seen_ids:
            pending = _pending_imported_submission(
                root,
                row,
                track_name=track_name,
                published_state_cache=published_state_cache,
            )
            if pending is None:
                del records[record_id]
                continue
            row = {**row, **pending, "record_id": record_id}
            records[record_id] = row
        active.append(row)
        if row.get("last_triggered_epoch") is None:
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


def _pending_imported_submission(
    root: Path,
    record: dict[str, Any],
    *,
    track_name: str,
    published_state_cache: dict[str, dict[str, str] | None],
) -> dict[str, Any] | None:
    stage = str(record.get("review_stage") or "")
    pending_by_stage = {
        "yomi_final_review": {"final_pending", "final_in_review"},
        "yomi_strong_repair_review": {"strong_pending", "strong_in_review"},
    }
    stage_dirs = {
        "yomi_final_review": "yomi_final",
        "yomi_strong_repair_review": "yomi_strong_repair",
    }
    dirname = stage_dirs.get(stage)
    submission_id = str(record.get("submission_id") or "")
    if dirname is None or not submission_id:
        return None
    submission_path = (
        root
        / "data"
        / "review_submissions"
        / dirname
        / f"{sanitize_submission_id(submission_id)}.json"
    )
    submission = _read_object(submission_path)
    if str(submission.get("submission_id") or "") != submission_id:
        return None
    pack_path = resolve_review_pack_path(
        root / "data" / "review_packs",
        str(submission.get("pack_id") or ""),
        review_stage=stage,
    )
    if pack_path is None:
        return None
    pack = _read_object(pack_path)
    if str(pack.get("track_name") or track_name) != track_name:
        return None
    batch_name = str(pack.get("batch_name") or "")
    if not batch_name:
        return None
    state_path = root / "data" / "pipeline" / "document_states" / f"{batch_name}.json"
    state_payload = _read_object(state_path)
    states = {
        str(row.get("doc_id")): str(row.get("state") or "")
        for row in state_payload.get("documents", [])
        if isinstance(row, dict) and row.get("doc_id")
    }
    submission_doc_ids = _submission_doc_ids(submission)
    pending_states = pending_by_stage[stage]
    local_pending = {
        doc_id
        for doc_id in submission_doc_ids
        if states.get(doc_id) in pending_states or doc_id not in states
    }
    pack_id = str(submission.get("pack_id") or "")
    if pack_id not in published_state_cache:
        published_state_cache[pack_id] = _published_document_states(root, pack_id)
    published_states = published_state_cache[pack_id]
    if published_states is None:
        # A missing/unreadable remote snapshot is uncertainty, not evidence that
        # a globally visible processing marker may be removed.
        published_pending = set(submission_doc_ids)
    else:
        published_pending = {
            doc_id
            for doc_id in submission_doc_ids
            if published_states.get(doc_id) in pending_states or doc_id not in published_states
        }
    pending_doc_ids = sorted(local_pending | published_pending)
    if not pending_doc_ids:
        return None
    return {"doc_ids": pending_doc_ids}


def _published_document_states(root: Path, pack_id: str) -> dict[str, str] | None:
    if not pack_id:
        return None
    try:
        completed = subprocess.run(
            ["git", "show", f"origin/gh-pages:review/packs/{pack_id}.json"],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    return {
        str(row.get("doc_id")): str(row.get("state") or "")
        for row in payload.get("documents", [])
        if isinstance(row, dict) and row.get("doc_id")
    }


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
