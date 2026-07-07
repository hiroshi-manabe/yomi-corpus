from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1

STATE_FINAL_PENDING = "final_pending"
STATE_FINAL_IN_REVIEW = "final_in_review"
STATE_FINAL_REVIEWED = "final_reviewed"
STATE_STRONG_PENDING = "strong_pending"
STATE_STRONG_IN_REVIEW = "strong_in_review"
STATE_STRONG_REVIEWED = "strong_reviewed"
STATE_COMPLETE = "complete"
STATE_SKIPPED = "skipped"

VALID_DOCUMENT_STATES = frozenset(
    {
        STATE_FINAL_PENDING,
        STATE_FINAL_IN_REVIEW,
        STATE_FINAL_REVIEWED,
        STATE_STRONG_PENDING,
        STATE_STRONG_IN_REVIEW,
        STATE_STRONG_REVIEWED,
        STATE_COMPLETE,
        STATE_SKIPPED,
    }
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def load_document_review_state(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    validate_document_review_state(payload)
    return payload


def write_document_review_state(path: Path, payload: dict[str, Any]) -> None:
    validate_document_review_state(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def validate_document_review_state(payload: dict[str, Any]) -> None:
    if int(payload.get("schema_version", 0)) != SCHEMA_VERSION:
        raise ValueError(f"Unsupported document review state schema: {payload.get('schema_version')}")
    documents = payload.get("documents")
    if not isinstance(documents, list):
        raise ValueError("Document review state must contain a documents list.")
    seen: set[str] = set()
    for document in documents:
        if not isinstance(document, dict):
            raise ValueError("Document review state document row must be an object.")
        doc_id = str(document.get("doc_id") or "")
        if not doc_id:
            raise ValueError("Document review state document row is missing doc_id.")
        if doc_id in seen:
            raise ValueError(f"Duplicate document review state doc_id: {doc_id}")
        seen.add(doc_id)
        state = str(document.get("state") or "")
        if state not in VALID_DOCUMENT_STATES:
            raise ValueError(f"Unsupported document review state for {doc_id}: {state}")


def build_initial_document_review_state(
    *,
    units_jsonl: Path,
    batch_name: str,
    track_name: str,
    initial_state: str = STATE_FINAL_PENDING,
) -> dict[str, Any]:
    if initial_state not in VALID_DOCUMENT_STATES:
        raise ValueError(f"Unsupported initial document state: {initial_state}")
    created = now_iso()
    documents: list[dict[str, Any]] = []
    by_doc: dict[str, dict[str, Any]] = {}
    for unit in load_jsonl(units_jsonl):
        doc_id = str(unit.get("doc_id") or "")
        if not doc_id:
            continue
        if doc_id not in by_doc:
            row = {
                "doc_id": doc_id,
                "doc_seq": len(documents) + 1,
                "state": initial_state,
                "unit_count": 0,
                "reviewed_unit_count": 0,
                "skipped_unit_count": 0,
                "strong_repair_item_count": 0,
                "updated_at": created,
            }
            by_doc[doc_id] = row
            documents.append(row)
        by_doc[doc_id]["unit_count"] += 1
    payload = {
        "schema_version": SCHEMA_VERSION,
        "batch_name": batch_name,
        "track_name": track_name,
        "created_at": created,
        "updated_at": created,
        "documents": documents,
    }
    return with_summary(payload)


def update_document_review_state_after_final_review(
    *,
    state: dict[str, Any],
    reviewed_units_jsonl: Path,
) -> dict[str, Any]:
    updated = clone_state(state)
    rows_by_doc = group_units_by_doc(load_jsonl(reviewed_units_jsonl))
    now = now_iso()
    for document in updated["documents"]:
        doc_id = str(document["doc_id"])
        units = rows_by_doc.get(doc_id, [])
        if not units:
            continue
        reviewed = 0
        skipped = 0
        for unit in units:
            review = (
                unit.get("analysis", {})
                .get("human_review", {})
                .get("yomi_final", {})
            )
            if isinstance(review, dict) and review.get("reviewed"):
                reviewed += 1
                if review.get("skip"):
                    skipped += 1
        old_reviewed = int(document.get("reviewed_unit_count") or 0)
        old_skipped = int(document.get("skipped_unit_count") or 0)
        document["reviewed_unit_count"] = reviewed
        document["skipped_unit_count"] = skipped
        if reviewed < int(document.get("unit_count", 0)):
            next_state = STATE_FINAL_IN_REVIEW if reviewed else STATE_FINAL_PENDING
        elif reviewed > 0 and skipped == reviewed:
            next_state = STATE_SKIPPED
        else:
            next_state = STATE_FINAL_REVIEWED
        current_state = str(document.get("state") or "")
        beyond_final = {
            STATE_STRONG_PENDING,
            STATE_STRONG_IN_REVIEW,
            STATE_STRONG_REVIEWED,
            STATE_COMPLETE,
        }
        if (
            current_state in beyond_final
            and old_reviewed == reviewed
            and old_skipped == skipped
        ):
            continue
        if current_state == next_state and old_reviewed == reviewed and old_skipped == skipped:
            continue
        document["state"] = next_state
        document["updated_at"] = now
    if updated != state:
        updated["updated_at"] = now
    return with_summary(updated)


def update_document_review_state_after_strong_queue(
    *,
    state: dict[str, Any],
    queue_jsonl: Path,
) -> dict[str, Any]:
    updated = clone_state(state)
    queue_counts = Counter(
        str(row.get("doc_id") or "")
        for row in load_jsonl(queue_jsonl)
        if str(row.get("doc_id") or "")
    )
    now = now_iso()
    for document in updated["documents"]:
        doc_id = str(document["doc_id"])
        count = int(queue_counts.get(doc_id, 0))
        document["strong_repair_item_count"] = count
        if document["state"] == STATE_FINAL_REVIEWED:
            document["state"] = STATE_STRONG_PENDING if count else STATE_COMPLETE
            document["updated_at"] = now
    updated["updated_at"] = now
    return with_summary(updated)


def update_document_review_state_after_strong_review(
    *,
    state: dict[str, Any],
    pack_json: Path,
    review_summary: dict[str, Any],
) -> dict[str, Any]:
    updated = clone_state(state)
    pack = load_json(pack_json)
    reviewed_doc_counts = reviewed_strong_item_counts_by_doc(pack, review_summary)
    rejected_items = set(str(item_id) for item_id in review_summary.get("rejected_items", []))
    item_doc_ids = {
        str(item.get("item_id") or ""): str(item.get("doc_id") or "")
        for item in pack.get("items", [])
        if isinstance(item, dict) and item.get("item_id")
    }
    rejected_doc_ids = {item_doc_ids.get(item_id, "") for item_id in rejected_items}
    invalid_manual_count = int(
        review_summary.get("manual_segment_overrides", {}).get("invalid_items", 0)
    )
    now = now_iso()
    for document in updated["documents"]:
        if document["state"] not in {STATE_STRONG_PENDING, STATE_STRONG_IN_REVIEW}:
            continue
        doc_id = str(document["doc_id"])
        total = int(document.get("strong_repair_item_count") or 0)
        reviewed = int(reviewed_doc_counts.get(doc_id, 0))
        if total <= 0:
            document["state"] = STATE_COMPLETE
        elif reviewed < total:
            document["state"] = STATE_STRONG_IN_REVIEW if reviewed else STATE_STRONG_PENDING
        elif doc_id in rejected_doc_ids or invalid_manual_count:
            document["state"] = STATE_STRONG_IN_REVIEW
        else:
            document["state"] = STATE_STRONG_REVIEWED
        document["updated_at"] = now
    updated["updated_at"] = now
    return with_summary(updated)


def mark_document_review_state_finalized(state: dict[str, Any]) -> dict[str, Any]:
    updated = clone_state(state)
    now = now_iso()
    for document in updated["documents"]:
        if document["state"] != STATE_SKIPPED:
            document["state"] = STATE_COMPLETE
            document["updated_at"] = now
    updated["updated_at"] = now
    return with_summary(updated)


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def clone_state(state: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(state, ensure_ascii=False))


def group_units_by_doc(units: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for unit in units:
        doc_id = str(unit.get("doc_id") or "")
        if doc_id:
            grouped.setdefault(doc_id, []).append(unit)
    return grouped


def reviewed_strong_item_counts_by_doc(
    pack: dict[str, Any],
    review_summary: dict[str, Any],
) -> Counter[str]:
    reviewed_ids = set()
    for path in review_summary.get("submission_paths", []):
        if not path:
            continue
        submission_path = Path(str(path))
        if not submission_path.exists():
            continue
        submission = load_json(submission_path)
        reviewed_ids.update(reviewed_item_ids_from_submission(pack, submission))
    by_id = {
        str(item.get("item_id") or ""): item
        for item in pack.get("items", [])
        if isinstance(item, dict) and item.get("item_id")
    }
    counts: Counter[str] = Counter()
    for item_id in reviewed_ids:
        doc_id = str(by_id.get(item_id, {}).get("doc_id") or "")
        if doc_id:
            counts[doc_id] += 1
    return counts


def reviewed_item_ids_from_submission(pack: dict[str, Any], submission: dict[str, Any]) -> set[str]:
    items = [
        item
        for item in pack.get("items", [])
        if isinstance(item, dict) and str(item.get("item_id") or "")
    ]
    reviewed: set[str] = set()
    for review_range in submission.get("reviewed_ranges", []):
        if not isinstance(review_range, dict):
            continue
        from_seq = int(review_range.get("from_seq") or 0)
        to_seq = int(review_range.get("to_seq") or 0)
        if from_seq > to_seq:
            from_seq, to_seq = to_seq, from_seq
        for item in items:
            seq = int(item.get("seq") or 0)
            if from_seq <= seq <= to_seq:
                reviewed.add(str(item["item_id"]))
    for override in submission.get("overrides", []):
        if isinstance(override, dict) and override.get("item_id"):
            reviewed.add(str(override["item_id"]))
    return reviewed


def with_summary(payload: dict[str, Any]) -> dict[str, Any]:
    counts = Counter(str(row.get("state") or "") for row in payload.get("documents", []))
    payload["summary"] = {
        "document_count": len(payload.get("documents", [])),
        "state_counts": {state: counts.get(state, 0) for state in sorted(VALID_DOCUMENT_STATES)},
    }
    validate_document_review_state(payload)
    return payload
