"""Commit independently reviewed documents without closing their preparation batch."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import os
from typing import Any

from yomi_corpus.document_review_state import (
    load_document_review_state,
    now_iso,
    with_summary,
)

MANIFEST = "document_finalization.json"
OUTPUTS = (
    "units.yomi.final.jsonl",
    "units.yomi.skipped.jsonl",
    "units.yomi.excluded.jsonl",
)


def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def read_manifest(batch_dir: Path) -> dict[str, Any]:
    path = batch_dir / MANIFEST
    return (
        json.loads(path.read_text(encoding="utf-8"))
        if path.exists()
        else {"schema_version": 1, "documents": {}}
    )


def committed_document_ids(batch_dir: Path) -> set[str]:
    return set(read_manifest(batch_dir)["documents"])


def atomic_text(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def finalize_ready_documents(batch_dir: Path, state_path: Path) -> dict[str, Any]:
    from yomi_corpus.yomi.final_review import (
        canonicalize_finalized_unit_yomi,
        exclusion_tombstone_from_unit,
        merge_yomi_final_review,
        normalize_scope_disposition,
        load_yomi_final_review_by_unit_id,
        SCOPE_SKIP,
        SCOPE_EXCLUDE,
    )

    required = (
        "units.jsonl",
        "units.yomi.reviewed.jsonl",
        "yomi_strong_repair_queue.jsonl",
    )
    if not state_path.exists() or not all(
        (batch_dir / name).exists() for name in required
    ):
        return {"finalized_documents": [], "failures": {}}
    state = load_document_review_state(state_path)
    manifest = read_manifest(batch_dir)
    original_manifest = deepcopy(manifest)
    original_state = deepcopy(state)
    committed = set(manifest["documents"])
    # Discard only uncommitted remnants of an interrupted append. Existing
    # finalized rows, including later Corpus Map corrections, are authoritative.
    previous_outputs = [read_rows(batch_dir / name) for name in OUTPUTS]
    outputs = [
        [row for row in rows if row.get("doc_id") in committed]
        for rows in previous_outputs
    ]
    discarded = any(
        len(before) != len(after) for before, after in zip(previous_outputs, outputs)
    )
    actual = Counter(
        (row.get("doc_id"), row.get("unit_id")) for rows in outputs for row in rows
    )
    expected_committed = Counter(
        (doc_id, unit_id)
        for doc_id, item in manifest["documents"].items()
        for unit_id in item["unit_ids"]
    )
    if actual != expected_committed:
        raise ValueError(
            "Committed document coverage differs from finalization manifest"
        )
    expected = {}
    for row in read_rows(batch_dir / "units.jsonl"):
        expected.setdefault(row["doc_id"], set()).add(row["unit_id"])
    reviewed = read_rows(batch_dir / "units.yomi.reviewed.jsonl")
    repaired = read_rows(batch_dir / "units.yomi.strong_repaired.jsonl")
    reviews = load_yomi_final_review_by_unit_id(batch_dir / "units.yomi.reviewed.jsonl")
    queue_counts = Counter(
        row.get("doc_id")
        for row in read_rows(batch_dir / "yomi_strong_repair_queue.jsonl")
    )
    added = []
    failures = {}
    for document in state["documents"]:
        doc_id = document["doc_id"]
        if doc_id in committed:
            document["finalized_at"] = manifest["documents"][doc_id]["finalized_at"]
            if document["state"] != "skipped":
                document["state"] = "complete"
            continue
        strong = document["state"] == "strong_reviewed"
        if not strong and not (
            document["state"] in {"complete", "skipped"} and not queue_counts[doc_id]
        ):
            continue
        rows = [
            deepcopy(row)
            for row in (repaired if strong else reviewed)
            if row.get("doc_id") == doc_id
        ]
        try:
            ids = [row["unit_id"] for row in rows]
            if not ids or len(ids) != len(set(ids)) or set(ids) != expected.get(doc_id):
                raise ValueError("Document unit coverage is incomplete or duplicated")
            buckets = [[], [], []]
            for row in rows:
                merge_yomi_final_review(row, reviews)
                review = (
                    row.get("analysis", {})
                    .get("human_review", {})
                    .get("yomi_final", {})
                )
                if not review.get("reviewed"):
                    raise ValueError(f"Unreviewed unit: {row['unit_id']}")
                disposition = normalize_scope_disposition(
                    review.get("disposition"), skip=review.get("skip")
                )
                if disposition == SCOPE_EXCLUDE:
                    buckets[2].append(exclusion_tombstone_from_unit(row))
                elif disposition == SCOPE_SKIP:
                    buckets[1].append(row)
                else:
                    canonicalize_finalized_unit_yomi(row)
                    buckets[0].append(row)
        except (ValueError, KeyError, TypeError) as exc:
            failures[doc_id] = str(exc)
            continue
        timestamp = now_iso()
        for target, source in zip(outputs, buckets, strict=True):
            target.extend(source)
        manifest["documents"][doc_id] = {"finalized_at": timestamp, "unit_ids": ids}
        document.update(finalized_at=timestamp, updated_at=timestamp)
        if document["state"] != "skipped":
            document["state"] = "complete"
        added.append(doc_id)
    if added or discarded:
        for name, rows in zip(OUTPUTS, outputs, strict=True):
            atomic_text(
                batch_dir / name,
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            )
    manifest["failures"] = failures
    if manifest != original_manifest:
        # Publication ignores new rows until this commit marker exists.
        atomic_text(
            batch_dir / MANIFEST,
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
    if state != original_state:
        state["updated_at"] = now_iso()
        atomic_text(
            state_path,
            json.dumps(with_summary(state), ensure_ascii=False, indent=2) + "\n",
        )
    return {"finalized_documents": added, "failures": failures}


def close_finalized_documents(
    batch_dir: Path, state_path: Path, summary_path: Path
) -> dict[str, Any] | None:
    if not (batch_dir / MANIFEST).exists():
        return None
    manifest = read_manifest(batch_dir)
    documents = load_document_review_state(state_path)["documents"]
    missing = {doc["doc_id"] for doc in documents} - set(manifest["documents"])
    if missing:
        return {
            "stage_complete": False,
            "blocking_reason": f"Documents not finalized: {sorted(missing)}; {manifest.get('failures', {})}",
            "queued_items": len(missing),
        }
    counts = [len(read_rows(batch_dir / name)) for name in OUTPUTS]
    summary = {
        "stage_complete": True,
        "rule": "document_finalization_v1",
        "read_units": sum(counts),
        "written_units": counts[0],
        "skipped_units": counts[1],
        "excluded_units": counts[2],
        "unreviewed_units": 0,
    }
    atomic_text(summary_path, json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary
