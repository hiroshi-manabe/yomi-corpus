from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from yomi_corpus.yomi.llm_readings import (
    is_standalone_laughter_w,
    is_valid_yomi_reading,
    normalize_hiragana_reading,
)
from yomi_corpus.yomi.furigana import (
    FuriganaConverter,
    has_han,
    kata_to_hira,
    parse_annotated_chunks,
)
from yomi_corpus.yomi.numeric_compounds import (
    canonicalize_final_numeric_compounds,
    numeric_compound_occurrences,
    numeric_compound_rule,
)
from yomi_corpus.yomi.numeric_surfaces import is_numeric_only_surface
from yomi_corpus.yomi.repairs import (
    normalize_parenthesized_laughter,
    normalize_parenthesized_laughter_tokens,
)
from yomi_corpus.yomi.token_codec import (
    YomiTokenError,
    editable_rendered_to_yomi_tokens,
    normalize_yomi_tokens,
    set_canonical_yomi_tokens,
    validate_yomi_token_surfaces,
    yomi_tokens_from_mapping,
    yomi_tokens_to_editable_rendered,
)
from yomi_corpus.document_review_state import (
    STATE_COMPLETE as DOCUMENT_STATE_COMPLETE,
    STATE_FINAL_PENDING as DOCUMENT_STATE_FINAL_PENDING,
    STATE_STRONG_PENDING as DOCUMENT_STATE_STRONG_PENDING,
    BULK_REVIEW_SELECTABLE_STATES as FINAL_REVIEW_SELECTABLE_STATES,
    ESCALATED_REPAIR_SELECTABLE_STATES as STRONG_REPAIR_SELECTABLE_STATES,
    document_workflow_queue_stage,
    document_workflow_state,
)


REVIEW_STAGE = "yomi_final_review"
STRONG_REPAIR_REVIEW_STAGE = "yomi_strong_repair_review"
FINALIZED_CORRECTION_STAGE = "finalized_correction"
FINALIZED_CORRECTION_SUBMISSION_TYPE = "finalized_correction_patch"
QUEUE_ID_FINAL_REVIEW = "final_review"
QUEUE_ID_STRONG_REPAIR = "strong_repair"
SCOPE_KEEP = "Keep"
SCOPE_SKIP = "Skip"
SCOPE_EXCLUDE = "Exclude"
SCOPE_DISPOSITIONS = {SCOPE_KEEP, SCOPE_SKIP, SCOPE_EXCLUDE}

FINAL_REVIEW_READING_ALTERNATIVES: dict[str, tuple[str, ...]] = {
    "kg": ("キロ", "キログラム"),
}
SCHEMA_VERSION = 1
APPLY_RULE = "yomi_final_review_apply_v1"
STRONG_REPAIR_REVIEW_RULE = "yomi_strong_repair_review_v1"
SURFACE_READING_STATS_PATH = Path("data/generated/yomi_surface_reading_stats.tsv")
ANNOTATED_FORMS_PATH = Path("data/external/sudachi_annotated_forms/sudachi_20251022.tsv")
SUPPLEMENTAL_FURIGANA_PATH = Path("data/lexicon/supplemental_furigana.tsv")
READING_HINT_MIN_COUNT = 2
READING_HINT_MIN_SHARE = 0.995
MAX_READING_HINT_SURFACE_LENGTH = 12


def manual_correction_state(unit: dict[str, Any]) -> dict[str, Any]:
    state = (
        unit.get("analysis", {})
        .get("human_review", {})
        .get("manual_correction", {})
    )
    return state if isinstance(state, dict) else {}


def normalize_scope_disposition(value: Any, *, skip: Any = None) -> str:
    disposition = str(value or "")
    if disposition in SCOPE_DISPOSITIONS:
        return disposition
    return SCOPE_SKIP if bool(skip) else SCOPE_KEEP


def manual_correction_required(unit: dict[str, Any]) -> bool:
    return bool(manual_correction_state(unit).get("required"))


def set_manual_correction_required(
    unit: dict[str, Any],
    *,
    required: bool,
    source_stage: str,
    submission_id: str = "",
    generated_at_epoch: int = 0,
    reason: str = "",
) -> bool:
    human_review = unit.setdefault("analysis", {}).setdefault("human_review", {})
    current = human_review.get("manual_correction")
    if not isinstance(current, dict):
        current = {}
    if bool(current.get("required")) == bool(required):
        return False
    event = {
        "required": bool(required),
        "source_stage": source_stage,
        "submission_id": submission_id,
        "generated_at_epoch": int(generated_at_epoch or 0),
    }
    if reason:
        event["reason"] = reason
    events = [row for row in current.get("events", []) if isinstance(row, dict)]
    events.append(event)
    human_review["manual_correction"] = {
        "required": bool(required),
        "events": events,
        "source_stage": source_stage,
        "submission_id": submission_id,
        "generated_at_epoch": int(generated_at_epoch or 0),
        **({"reason": reason} if reason else {}),
    }
    return True


@dataclass(frozen=True)
class YomiFinalReviewPackSummary:
    pack_id: str
    review_stage: str
    item_count: int
    unresolved_item_count: int
    unresolved_target_count: int
    provisional_skip_item_count: int
    output_json: str
    latest_json: str | None


@dataclass(frozen=True)
class YomiStrongRepairReviewPackSummary:
    pack_id: str
    review_stage: str
    item_count: int
    output_json: str
    latest_json: str | None


def materialize_yomi_review_units_file(
    *,
    scope_units_jsonl: Path,
    processed_units_jsonl: Path,
    output_jsonl: Path,
    hybrid_units_jsonl: Path | None = None,
) -> dict[str, Any]:
    """Merge processed units while supporting pre-migration scope-skip artifacts."""
    processed_rows = load_jsonl(processed_units_jsonl)
    scope_rows = load_jsonl(scope_units_jsonl)
    hybrid_rows = (
        load_jsonl(hybrid_units_jsonl)
        if hybrid_units_jsonl is not None and hybrid_units_jsonl.exists()
        else []
    )
    hybrid_by_id: dict[str, dict[str, Any]] = {}
    for row in hybrid_rows:
        unit_id = str(row.get("unit_id") or "")
        if not unit_id or unit_id in hybrid_by_id:
            raise ValueError(f"Invalid or duplicate hybrid unit id: {unit_id!r}")
        hybrid_by_id[unit_id] = row
    processed_by_id: dict[str, dict[str, Any]] = {}
    for row in processed_rows:
        unit_id = str(row.get("unit_id") or "")
        if not unit_id or unit_id in processed_by_id:
            raise ValueError(f"Invalid or duplicate processed unit id: {unit_id!r}")
        processed_by_id[unit_id] = row

    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    restored_skips = 0
    missing_non_skips: list[str] = []
    with output_jsonl.open("w", encoding="utf-8") as dst:
        if not scope_rows:
            for row in processed_rows:
                dst.write(json.dumps(row, ensure_ascii=False) + "\n")
                written += 1
            processed_by_id.clear()
        for scope_unit in scope_rows:
            unit_id = str(scope_unit.get("unit_id") or "")
            processed = processed_by_id.pop(unit_id, None)
            if processed is not None:
                row = processed
            elif is_scope_triage_skipped(scope_unit):
                row = hybrid_by_id.get(unit_id, scope_unit)
                if unit_id not in hybrid_by_id:
                    row.setdefault("analysis", {}).setdefault("pipeline", {})[
                        "yomi_processing"
                    ] = {
                        "status": "skipped",
                        "reason": "scope_triage_skip_without_hybrid",
                    }
                restored_skips += 1
            else:
                missing_non_skips.append(unit_id)
                continue
            dst.write(json.dumps(row, ensure_ascii=False) + "\n")
            written += 1

    if missing_non_skips or processed_by_id:
        output_jsonl.unlink(missing_ok=True)
        raise ValueError(
            "Cannot materialize review units: "
            f"missing non-skips={missing_non_skips[:5]!r}, "
            f"unexpected processed ids={list(processed_by_id)[:5]!r}"
        )
    return {
        "written_units": written,
        "processed_units": written - restored_skips,
        "restored_scope_skips": restored_skips,
        "output_jsonl": str(output_jsonl),
    }


def is_scope_triage_skipped(unit: dict[str, Any]) -> bool:
    return (
        unit.get("analysis", {})
        .get("llm", {})
        .get("scope_triage", {})
        .get("status")
        == "Skip"
    )


def build_yomi_final_review_pack_file(
    *,
    units_jsonl: Path,
    output_json: Path,
    pack_id: str,
    track_name: str,
    batch_name: str,
    document_state_json: Path | None = None,
    latest_json: Path | None = None,
    created_at_epoch: int | None = None,
) -> YomiFinalReviewPackSummary:
    pack = build_yomi_final_review_pack(
        units_jsonl=units_jsonl,
        pack_id=pack_id,
        track_name=track_name,
        batch_name=batch_name,
        document_state_json=document_state_json,
        created_at_epoch=created_at_epoch,
    )
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(pack, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if latest_json is not None:
        latest_json.parent.mkdir(parents=True, exist_ok=True)
        latest_json.write_text(
            json.dumps(pack, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return YomiFinalReviewPackSummary(
        pack_id=pack_id,
        review_stage=REVIEW_STAGE,
        item_count=int(pack["item_count"]),
        unresolved_item_count=int(pack["summary"]["unresolved_item_count"]),
        unresolved_target_count=int(pack["summary"]["unresolved_target_count"]),
        provisional_skip_item_count=int(pack["summary"]["provisional_skip_item_count"]),
        output_json=str(output_json),
        latest_json=str(latest_json) if latest_json is not None else None,
    )


def build_yomi_strong_repair_review_pack_file(
    *,
    queue_jsonl: Path,
    results_jsonl: Path,
    units_jsonl: Path,
    output_json: Path,
    pack_id: str,
    track_name: str,
    batch_name: str,
    document_state_json: Path | None = None,
    latest_json: Path | None = None,
    created_at_epoch: int | None = None,
) -> YomiStrongRepairReviewPackSummary:
    pack = build_yomi_strong_repair_review_pack(
        queue_jsonl=queue_jsonl,
        results_jsonl=results_jsonl,
        units_jsonl=units_jsonl,
        pack_id=pack_id,
        track_name=track_name,
        batch_name=batch_name,
        document_state_json=document_state_json,
        created_at_epoch=created_at_epoch,
    )
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(pack, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if latest_json is not None:
        latest_json.parent.mkdir(parents=True, exist_ok=True)
        latest_json.write_text(
            json.dumps(pack, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return YomiStrongRepairReviewPackSummary(
        pack_id=pack_id,
        review_stage=STRONG_REPAIR_REVIEW_STAGE,
        item_count=int(pack["item_count"]),
        output_json=str(output_json),
        latest_json=str(latest_json) if latest_json is not None else None,
    )


def build_yomi_final_review_pack(
    *,
    units_jsonl: Path,
    pack_id: str,
    track_name: str,
    batch_name: str,
    document_state_json: Path | None = None,
    created_at_epoch: int | None = None,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    created = created_at_epoch if created_at_epoch is not None else current_epoch()
    units = load_jsonl(units_jsonl)
    documents = build_pack_documents(units)
    doc_seq_by_id = {str(row["doc_id"]): int(row["doc_seq"]) for row in documents}
    track_doc_seq_by_id = {
        str(row["doc_id"]): int(row.get("track_doc_seq") or row["doc_seq"])
        for row in documents
    }

    for unit in units:
        doc_id = str(unit.get("doc_id") or "")
        item = build_review_item(
            unit,
            seq=len(items) + 1,
            doc_seq=doc_seq_by_id.get(doc_id, len(doc_seq_by_id) + 1),
            track_doc_seq=track_doc_seq_by_id.get(doc_id),
        )
        items.append(item)

    unresolved_items = [item for item in items if item["unresolved_target_count"] > 0]
    unresolved_targets = sum(int(item["unresolved_target_count"]) for item in items)
    provisional_skip_items = [item for item in items if item["provisional_skip"]]
    documents = with_queue_document_metadata(
        documents,
        items,
        queue_id=QUEUE_ID_FINAL_REVIEW,
        document_state_json=document_state_json,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "interaction_span_schema_version": 1,
        "review_stage": REVIEW_STAGE,
        "queue_id": QUEUE_ID_FINAL_REVIEW,
        "pack_id": pack_id,
        "track_name": track_name,
        "batch_name": batch_name,
        "created_at_epoch": created,
        "created_at": datetime.fromtimestamp(created, timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "item_count": len(items),
        "summary": {
            "document_count": len(documents),
            "selectable_document_count": sum(1 for doc in documents if doc.get("selectable")),
            "document_state_counts": document_state_counts(documents),
            "unresolved_item_count": len(unresolved_items),
            "unresolved_target_count": unresolved_targets,
            "interaction_span_count": sum(
                len(item.get("interaction_spans", [])) for item in items
            ),
            "provisional_skip_item_count": len(provisional_skip_items),
        },
        "documents": documents,
        "items": items,
    }


def build_yomi_strong_repair_review_pack(
    *,
    queue_jsonl: Path,
    results_jsonl: Path,
    units_jsonl: Path,
    pack_id: str,
    track_name: str,
    batch_name: str,
    document_state_json: Path | None = None,
    created_at_epoch: int | None = None,
) -> dict[str, Any]:
    queue_rows = load_jsonl(queue_jsonl)
    result_rows = {str(row.get("item_id") or ""): row for row in load_jsonl(results_jsonl)}
    units_by_id = {str(row.get("unit_id") or ""): row for row in load_jsonl(units_jsonl)}
    documents = build_pack_documents(units_by_id.values())
    doc_seq_by_id = {str(row["doc_id"]): int(row["doc_seq"]) for row in documents}
    track_doc_seq_by_id = {
        str(row["doc_id"]): int(row.get("track_doc_seq") or row["doc_seq"])
        for row in documents
    }
    created = created_at_epoch if created_at_epoch is not None else current_epoch()
    rows_by_unit: dict[str, list[dict[str, Any]]] = {}
    for queue_row in queue_rows:
        unit_id = str(queue_row.get("unit_id") or "")
        if unit_id:
            rows_by_unit.setdefault(unit_id, []).append(queue_row)

    items = []
    reviewable_rows_by_unit: dict[str, list[dict[str, Any]]] = {}
    for unit_id, unit_queue_rows in rows_by_unit.items():
        for queue_row in unit_queue_rows:
            item_id = str(queue_row.get("item_id") or "")
            if item_id and item_id in result_rows:
                reviewable_rows_by_unit.setdefault(unit_id, []).append(queue_row)

    items = []
    for seq, (unit_id, unit_queue_rows) in enumerate(reviewable_rows_by_unit.items(), start=1):
        unit = units_by_id.get(unit_id, {})
        doc_id = str(unit.get("doc_id") or "")
        regions = [
            build_strong_repair_review_region(
                queue_row,
                result_rows.get(str(queue_row.get("item_id") or ""), {}),
                unit,
            )
            for queue_row in unit_queue_rows
        ]
        assign_strong_repair_region_occurrences(regions, unit)
        items.append(
            build_strong_repair_review_sentence_item(
                unit_queue_rows,
                regions,
                unit,
                seq=seq,
                doc_seq=doc_seq_by_id.get(doc_id),
                track_doc_seq=track_doc_seq_by_id.get(doc_id),
            )
        )
    documents = with_queue_document_metadata(
        documents,
        items,
        queue_id=QUEUE_ID_STRONG_REPAIR,
        document_state_json=document_state_json,
    )
    mapping_error_count = sum(int(item.get("mapping_error_count") or 0) for item in items)
    return {
        "schema_version": SCHEMA_VERSION,
        "review_stage": STRONG_REPAIR_REVIEW_STAGE,
        "queue_id": QUEUE_ID_STRONG_REPAIR,
        "pack_id": pack_id,
        "track_name": track_name,
        "batch_name": batch_name,
        "created_at_epoch": created,
        "created_at": datetime.fromtimestamp(created, timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "item_count": len(items),
        "summary": {
            "document_count": len(documents),
            "selectable_document_count": sum(1 for doc in documents if doc.get("selectable")),
            "document_state_counts": document_state_counts(documents),
            "repaired_item_count": len(items),
            "queued_repair_row_count": len(queue_rows),
            "result_row_count": len(result_rows),
            "reviewable_repair_row_count": sum(len(rows) for rows in reviewable_rows_by_unit.values()),
            "mapping_error_count": mapping_error_count,
        },
        "documents": documents,
        "items": items,
    }


def build_pack_documents(units: Any) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for unit in units:
        if not isinstance(unit, dict):
            continue
        doc_id = str(unit.get("doc_id") or "")
        if not doc_id:
            continue
        if doc_id not in by_id:
            track_doc_seq = unit.get("track_doc_seq")
            if not isinstance(track_doc_seq, int) or track_doc_seq <= 0:
                track_doc_seq = len(documents) + 1
            doc = {
                "doc_id": doc_id,
                "doc_seq": len(documents) + 1,
                "track_doc_seq": track_doc_seq,
                "unit_count": 0,
                "source_file": unit.get("source_file"),
                "source_line_no": unit.get("source_line_no"),
                "preview": str(unit.get("text") or ""),
            }
            by_id[doc_id] = doc
            documents.append(doc)
        by_id[doc_id]["unit_count"] += 1
    return documents


def with_repair_document_counts(
    documents: list[dict[str, Any]],
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    item_counts = Counter(str(item.get("doc_id") or "") for item in items)
    region_counts = Counter()
    for item in items:
        region_counts[str(item.get("doc_id") or "")] += int(item.get("region_count") or 0)
    return [
        {
            **doc,
            "item_count": int(item_counts.get(str(doc.get("doc_id") or ""), 0)),
            "region_count": int(region_counts.get(str(doc.get("doc_id") or ""), 0)),
        }
        for doc in documents
    ]


def with_queue_document_metadata(
    documents: list[dict[str, Any]],
    items: list[dict[str, Any]],
    *,
    queue_id: str,
    document_state_json: Path | None,
) -> list[dict[str, Any]]:
    item_stats = document_item_stats(items)
    state_rows = load_document_state_rows(document_state_json)
    enriched: list[dict[str, Any]] = []
    for doc in documents:
        doc_id = str(doc.get("doc_id") or "")
        stats = item_stats.get(doc_id, {})
        state_row = state_rows.get(doc_id, {})
        state = str(
            state_row.get("state")
            or default_document_state_for_queue(queue_id, int(stats.get("item_count") or 0))
        )
        workflow_state = document_workflow_state(state)
        workflow_queue_stage = document_workflow_queue_stage(state)
        item_count = int(stats.get("item_count") or 0)
        queue_member = (
            item_count > 0 and document_belongs_to_queue(queue_id=queue_id, state=state)
        )
        selectable = document_is_selectable_for_queue(
            queue_id=queue_id,
            state=state,
            item_count=item_count,
        )
        enriched.append(
            {
                **doc,
                "queue_id": queue_id,
                "state": state,
                "track_doc_seq": int(
                    state_row.get("track_doc_seq")
                    or doc.get("track_doc_seq")
                    or doc.get("doc_seq")
                    or 0
                ),
                "workflow_state": workflow_state,
                "workflow_queue_stage": workflow_queue_stage,
                "queue_member": queue_member,
                "selectable": selectable,
                "state_updated_at": str(state_row.get("updated_at") or ""),
                "application_failure_count": len(state_row.get("application_failures") or []),
                "application_failures": state_row.get("application_failures") or [],
                "item_count": item_count,
                "region_count": int(stats.get("region_count") or 0),
                "unresolved_count": int(stats.get("unresolved_count") or 0),
                "from_seq": stats.get("from_seq"),
                "to_seq": stats.get("to_seq"),
                "reviewed_unit_count": int(state_row.get("reviewed_unit_count") or 0),
                "skipped_unit_count": int(state_row.get("skipped_unit_count") or 0),
                "strong_repair_item_count": int(
                    state_row.get("strong_repair_item_count") or stats.get("region_count") or 0
                ),
            }
        )
    return enriched


def document_item_stats(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    stats_by_doc: dict[str, dict[str, Any]] = {}
    for item in items:
        doc_id = str(item.get("doc_id") or "")
        if not doc_id:
            continue
        stats = stats_by_doc.setdefault(
            doc_id,
            {
                "from_seq": int(item.get("seq") or 0),
                "to_seq": int(item.get("seq") or 0),
                "item_count": 0,
                "region_count": 0,
                "unresolved_count": 0,
            },
        )
        seq = int(item.get("seq") or 0)
        if seq:
            stats["from_seq"] = min(int(stats["from_seq"] or seq), seq)
            stats["to_seq"] = max(int(stats["to_seq"] or seq), seq)
        stats["item_count"] += 1
        stats["region_count"] += int(item.get("region_count") or 0)
        stats["unresolved_count"] += int(
            item.get("unresolved_target_count") or item.get("region_count") or 0
        )
    return stats_by_doc


def load_document_state_rows(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    rows = payload.get("documents")
    if not isinstance(rows, list):
        return {}
    return {
        str(row.get("doc_id") or ""): row
        for row in rows
        if isinstance(row, dict) and str(row.get("doc_id") or "")
    }


def default_document_state_for_queue(queue_id: str, item_count: int) -> str:
    if queue_id == QUEUE_ID_FINAL_REVIEW:
        return DOCUMENT_STATE_FINAL_PENDING
    if queue_id == QUEUE_ID_STRONG_REPAIR:
        return DOCUMENT_STATE_STRONG_PENDING if item_count else DOCUMENT_STATE_COMPLETE
    return DOCUMENT_STATE_COMPLETE


def document_is_selectable_for_queue(*, queue_id: str, state: str, item_count: int) -> bool:
    if item_count <= 0:
        return False
    if not document_belongs_to_queue(queue_id=queue_id, state=state):
        return False
    if queue_id == QUEUE_ID_FINAL_REVIEW:
        return state in FINAL_REVIEW_SELECTABLE_STATES
    if queue_id == QUEUE_ID_STRONG_REPAIR:
        return state in STRONG_REPAIR_SELECTABLE_STATES
    return False


def document_belongs_to_queue(*, queue_id: str, state: str) -> bool:
    workflow_queue_stage = document_workflow_queue_stage(state)
    if queue_id == QUEUE_ID_FINAL_REVIEW:
        return workflow_queue_stage == REVIEW_STAGE
    if queue_id == QUEUE_ID_STRONG_REPAIR:
        return workflow_queue_stage == STRONG_REPAIR_REVIEW_STAGE
    return False


def document_state_counts(documents: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(doc.get("state") or "") for doc in documents)
    return {state: count for state, count in sorted(counts.items()) if state}


def build_strong_repair_review_sentence_item(
    queue_rows: list[dict[str, Any]],
    regions: list[dict[str, Any]],
    unit: dict[str, Any],
    *,
    seq: int,
    doc_seq: int | None,
    track_doc_seq: int | None,
) -> dict[str, Any]:
    first_row = queue_rows[0] if queue_rows else {}
    first_region = regions[0] if regions else {}
    pairs, tokenization_error = review_yomi_pairs_for_unit(unit)
    rendered_after = yomi_tokens_to_editable_rendered(pairs) if pairs else ""
    mapping_errors = [
        {
            "region_id": str(region.get("region_id") or ""),
            "error": str(region.get("mapping_error") or ""),
        }
        for region in regions
        if region.get("mapping_error")
    ]
    if tokenization_error:
        mapping_errors.insert(0, {"region_id": "", "error": tokenization_error})
    return {
        "item_id": f"{unit.get('unit_id')}::strong_repair",
        "seq": seq,
        "doc_id": str(unit.get("doc_id") or ""),
        "doc_seq": doc_seq,
        "track_doc_seq": track_doc_seq,
        "unit_id": str(unit.get("unit_id") or first_row.get("unit_id") or ""),
        "unit_seq": unit.get("unit_seq"),
        "source_file": unit.get("source_file"),
        "source_line_no": unit.get("source_line_no"),
        "text": str(unit.get("text") or first_row.get("text") or ""),
        "rendered_yomi_before": str(first_row.get("rendered_yomi") or ""),
        "rendered_yomi_after": rendered_after,
        "rendered_yomi_after_tokens": pairs,
        "rendered_yomi_after_ruby_tokens": yomi_tokens_ruby_tokens(pairs),
        "manual_correction_required": manual_correction_required(unit),
        "manual_correction": manual_correction_state(unit),
        "mapping_error_count": len(mapping_errors),
        "mapping_errors": mapping_errors,
        "repair_scope": "sentence_regions",
        "region_count": len(regions),
        "regions": regions,
        # Compatibility aliases for consumers that still expect one region per item.
        "rejected_span": first_region.get("rejected_span", ""),
        "target_escalations": first_region.get("target_escalations", []),
        "rejected_readings": first_region.get("rejected_readings", []),
        "reading_candidates": first_region.get("reading_candidates", {}),
        "reading_hints": first_region.get("reading_hints", {}),
        "llm_parsed": first_region.get("llm_parsed", []),
        "llm_raw_text": first_region.get("llm_raw_text", ""),
        "llm_comments": first_region.get("llm_comments", []),
        "llm_parse_error": first_region.get("llm_parse_error"),
        "used_web_search": any(bool(region.get("used_web_search")) for region in regions),
        "repair_status": first_region.get("repair_status"),
        "repair_log": first_region.get("repair_log", {}),
    }


def build_strong_repair_review_region(
    queue_row: dict[str, Any],
    result: dict[str, Any],
    unit: dict[str, Any],
) -> dict[str, Any]:
    parsed = result.get("parsed")
    if not isinstance(parsed, list):
        parsed = []
    repairs = (
        unit.get("analysis", {})
        .get("llm", {})
        .get("yomi_strong_repair", {})
        .get("repairs", [])
    )
    repair_log = next(
        (
            row
            for row in repairs
            if isinstance(row, dict) and row.get("item_id") == queue_row.get("item_id")
        ),
        {},
    )
    rejected_readings = []
    for target in queue_row.get("target_escalations", []):
        if not isinstance(target, dict):
            continue
        rejected_readings.extend(
            row for row in target.get("rejected_readings", []) if isinstance(row, dict)
        )
    rejected_span = str(queue_row.get("rejected_span") or "") or "".join(
        str(row.get("surface") or "")
        for row in queue_row.get("target_escalations", [])
        if isinstance(row, dict)
    )
    target_escalations = [
        row
        for row in queue_row.get("target_escalations", [])
        if isinstance(row, dict)
    ]
    llm_comments = []
    for row in parsed:
        if not isinstance(row, dict):
            continue
        comment = str(row.get("comment") or "").strip()
        if comment and comment not in llm_comments:
            llm_comments.append(comment)
    span_reading_candidates = build_strong_repair_reading_candidates(rejected_span)
    pairs, tokenization_error = review_yomi_pairs_for_unit(unit)
    span_matches = find_rendered_surface_spans(pairs, rejected_span) if pairs else []
    display_mapping = (
        select_rendered_surface_span(
            pairs,
            rejected_span,
            targets=target_escalations,
            reference_rendered=str(queue_row.get("rendered_yomi") or ""),
        )
        if pairs
        else None
    )
    if tokenization_error:
        mapping_error = tokenization_error
    elif not span_matches:
        mapping_error = f"rejected span {rejected_span!r} is absent from canonical yomi surfaces"
    elif display_mapping is None:
        mapping_error = f"rejected span {rejected_span!r} is ambiguous in canonical yomi surfaces"
    else:
        mapping_error = ""
    return {
        "region_id": str(queue_row.get("item_id") or ""),
        "item_id": str(queue_row.get("item_id") or ""),
        "unit_id": str(queue_row.get("unit_id") or ""),
        "text": str(queue_row.get("text") or ""),
        "rendered_yomi_before": str(queue_row.get("rendered_yomi") or ""),
        "rendered_yomi_after": yomi_tokens_to_editable_rendered(pairs) if pairs else "",
        "display_mapping": display_mapping,
        "mapping_error": mapping_error or None,
        "repair_scope": str(queue_row.get("repair_scope") or ""),
        "reasons": list(queue_row.get("reasons") or []),
        "target_constraints": [
            row for row in queue_row.get("target_constraints", []) if isinstance(row, dict)
        ],
        "target_escalations": target_escalations,
        "rejected_span": rejected_span,
        "reading_candidates": span_reading_candidates,
        "reading_hints": {
            surface: readings[0]
            for surface, readings in span_reading_candidates.items()
            if readings
        },
        "rejected_readings": rejected_readings,
        "llm_raw_text": str(result.get("raw_text") or ""),
        "llm_parsed": parsed,
        "llm_comments": llm_comments,
        "llm_parse_error": result.get("parse_error"),
        "used_web_search": any(bool(row.get("used_web_search")) for row in parsed if isinstance(row, dict)),
        "repair_status": repair_log.get("status"),
        "repair_log": repair_log,
    }


def assign_strong_repair_region_occurrences(
    regions: list[dict[str, Any]],
    unit: dict[str, Any],
) -> None:
    pairs, tokenization_error = review_yomi_pairs_for_unit(unit)
    if tokenization_error or not pairs:
        return
    grouped: dict[str, list[dict[str, Any]]] = {}
    for region in regions:
        span = str(region.get("rejected_span") or "")
        if span:
            grouped.setdefault(span, []).append(region)
    for span, span_regions in grouped.items():
        matches = find_rendered_surface_spans(pairs, span)
        if len(span_regions) != len(matches) or len(matches) < 2:
            continue
        ordered = sorted(span_regions, key=strong_repair_region_target_index)
        for occurrence_index, region in enumerate(ordered):
            region["surface_occurrence_index"] = occurrence_index
            region["display_mapping"] = matches[occurrence_index]
            region["mapping_error"] = None


def strong_repair_region_target_index(region: dict[str, Any]) -> int:
    indexes = [
        int(target["token_index"])
        for target in region.get("target_escalations", [])
        if isinstance(target, dict) and isinstance(target.get("token_index"), int)
    ]
    return min(indexes) if indexes else 10**9


def build_review_item(
    unit: dict[str, Any],
    *,
    seq: int,
    doc_seq: int,
    track_doc_seq: int | None,
) -> dict[str, Any]:
    safety = unit.get("analysis", {}).get("safety", {}).get("yomi", {})
    targets = safety.get("targets", [])
    if not isinstance(targets, list):
        targets = []
    text = str(unit.get("text") or "")
    rendered_yomi = str(
        unit.get("analysis", {}).get("mechanical", {}).get("yomi", {}).get("rendered") or ""
    )
    rendered_yomi = normalize_parenthesized_laughter(rendered_yomi).rendered
    review_targets = [
        build_review_target(
            normalize_parenthesized_laughter_target(
                target,
                text=text,
                rendered_yomi=rendered_yomi,
            )
        )
        for target in targets
        if isinstance(target, dict)
    ]
    review_targets.extend(
        build_numeric_compound_review_targets(
            unit_id=str(unit.get("unit_id") or ""),
            text=text,
            rendered_yomi=rendered_yomi,
            existing_targets=review_targets,
        )
    )
    review_targets.sort(
        key=lambda target: (
            int(target.get("target_start") or 0),
            int(target.get("target_end") or 0),
        )
    )
    interaction_spans = build_interaction_spans(
        unit_id=str(unit.get("unit_id") or ""),
        text=text,
        rendered_yomi=rendered_yomi,
        targets=review_targets,
    )
    unresolved_count = sum(1 for target in review_targets if not target["is_safe"])
    scope = unit.get("analysis", {}).get("llm", {}).get("scope_triage", {})
    alphabetic_scope = unit.get("analysis", {}).get("mechanical", {}).get("alphabetic_scope", {})
    scope_status = normalize_scope_disposition(scope.get("status"))
    provisional_skip = bool(
        scope_status == SCOPE_SKIP
        and (
            scope.get("provisional")
            or scope.get("source") == "provisional_alphabetic_skip"
            or alphabetic_scope.get("provisional_skip")
        )
    )
    rendered_yomi = rendered_yomi_with_review_defaults(rendered_yomi, interaction_spans)
    rendered_yomi = normalize_parenthesized_laughter(rendered_yomi).rendered
    return {
        "item_id": str(unit.get("unit_id", "")),
        "seq": seq,
        "doc_id": str(unit.get("doc_id") or ""),
        "doc_seq": doc_seq,
        "track_doc_seq": track_doc_seq,
        "unit_id": str(unit.get("unit_id", "")),
        "unit_seq": unit.get("unit_seq"),
        "source_file": unit.get("source_file"),
        "source_line_no": unit.get("source_line_no"),
        "text": text,
        "ruby_segments": build_ruby_segments(
            text,
            interaction_spans,
            rendered_yomi=rendered_yomi,
        ),
        "rendered_yomi": rendered_yomi,
        "rendered_yomi_ruby_tokens": rendered_yomi_ruby_tokens(rendered_yomi),
        "scope_status": scope_status,
        "scope_default": SCOPE_SKIP if provisional_skip else scope_status,
        "exclude_default": scope_status == SCOPE_EXCLUDE,
        "provisional_skip": provisional_skip,
        "skip_default": bool(scope_status == SCOPE_SKIP or provisional_skip),
        "skip_reasons": list(alphabetic_scope.get("reasons") or [])
        if isinstance(alphabetic_scope, dict)
        else [],
        "manual_correction_required": manual_correction_required(unit),
        "manual_correction": manual_correction_state(unit),
        "target_count": len(review_targets),
        "safe_target_count": len(review_targets) - unresolved_count,
        "unresolved_target_count": unresolved_count,
        "all_targets_safe": bool(review_targets) and unresolved_count == 0,
        "targets": review_targets,
        "interaction_spans": interaction_spans,
        "interaction_span_count": len(interaction_spans),
        "reading_hints": build_reading_hints(review_targets),
    }


def build_numeric_compound_review_targets(
    *,
    unit_id: str,
    text: str,
    rendered_yomi: str,
    existing_targets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    existing_ranges = [
        (int(target["target_start"]), int(target["target_end"]))
        for target in existing_targets
        if isinstance(target.get("target_start"), int)
        and isinstance(target.get("target_end"), int)
    ]
    targets: list[dict[str, Any]] = []
    for occurrence in numeric_compound_occurrences(text, rendered_yomi):
        if any(
            occurrence.start < target_end and occurrence.end > target_start
            for target_start, target_end in existing_ranges
        ):
            continue
        reading_hiragana = kata_to_hira(occurrence.reading)
        targets.append(
            build_review_target(
                {
                    "item_id": f"{unit_id}:n{occurrence.start + 1:04d}",
                    "surface": occurrence.surface,
                    "token_surface": occurrence.surface,
                    "target_start": occurrence.start,
                    "target_end": occurrence.end,
                    "token_index": occurrence.pair_index,
                    "chunk_index": 0,
                    "current_reading": occurrence.reading,
                    "current_reading_hiragana": reading_hiragana,
                    "is_safe": True,
                    "review_status": "safe",
                    "highlight_level": "none",
                    "accepted_signal_names": ["safe_by_numeric_compound"],
                    "status_reason": "accepted_numeric_compound_rule",
                    "signals": [
                        {
                            "name": "safe_by_numeric_compound",
                            "accepted": True,
                            "reading": reading_hiragana,
                        }
                    ],
                }
            )
        )
    return targets


def rendered_yomi_with_review_defaults(
    rendered: str,
    targets: list[dict[str, Any]],
) -> str:
    if not rendered:
        return rendered
    pairs = parse_rendered_pairs(rendered)
    if not pairs:
        return rendered
    updated = False
    for target in targets:
        if not isinstance(target, dict):
            continue
        source = str(target.get("default_choice_source") or "current")
        if source == "current":
            continue
        surface = str(target.get("surface") or "")
        token_surface = str(target.get("token_surface") or "")
        if surface != token_surface:
            continue
        replacement_index: int | None = None
        token_index = target.get("token_index")
        if isinstance(token_index, int) and 0 <= token_index < len(pairs):
            if pairs[token_index][0] == token_surface:
                replacement_index = token_index
        if replacement_index is None:
            span = find_unique_rendered_span(pairs, token_surface)
            if span is not None and span[1] - span[0] == 1:
                replacement_index = span[0]
        if replacement_index is None:
            continue
        selected = target.get("default_reading")
        new_reading = "" if source == "none" else hira_to_kata(str(selected or ""))
        old_surface, old_reading = pairs[replacement_index]
        if old_reading != new_reading:
            pairs[replacement_index] = (old_surface, new_reading)
            updated = True
    if not updated:
        return rendered
    return " ".join(f"{surface}/{reading}" for surface, reading in pairs)


def build_review_target(target: dict[str, Any]) -> dict[str, Any]:
    candidates = reading_candidates(target)
    default_choice_source = default_candidate_source(target, candidates)
    default_candidate = candidate_by_source(candidates, default_choice_source)
    default_candidate_id = default_candidate.get("id") if default_candidate else None
    surface = str(target.get("surface") or "")
    candidates = [with_ruby_display_nodes(surface, candidate) for candidate in candidates]
    return {
        "item_id": str(target.get("item_id") or ""),
        "surface": surface,
        "token_surface": str(target.get("token_surface") or ""),
        "target_start": target.get("target_start"),
        "target_end": target.get("target_end"),
        "token_index": target.get("token_index"),
        "chunk_index": target.get("chunk_index"),
        "current_reading": target.get("current_reading"),
        "current_reading_hiragana": target.get("current_reading_hiragana"),
        "is_safe": bool(target.get("is_safe")),
        "review_status": target.get("review_status"),
        "highlight_level": target.get("highlight_level"),
        "accepted_signal_names": list(target.get("accepted_signal_names") or []),
        "status_reason": target.get("status_reason"),
        "default_candidate_id": default_candidate_id,
        "default_choice_source": default_choice_source,
        "default_reading": default_candidate.get("reading") if default_candidate else None,
        "candidates": candidates,
        "signals": target.get("signals") if isinstance(target.get("signals"), list) else [],
    }


def normalize_parenthesized_laughter_target(
    target: dict[str, Any],
    *,
    text: str,
    rendered_yomi: str,
) -> dict[str, Any]:
    surface = str(target.get("surface") or "")
    token_surface = str(target.get("token_surface") or surface)
    if token_surface not in {"(笑)", "（笑）"} or surface not in {token_surface, "笑"}:
        return target
    start = target.get("target_start")
    end = target.get("target_end")
    if not isinstance(start, int) or not isinstance(end, int):
        return target
    if surface == token_surface:
        if end - start != 3:
            return target
        normalized_start = start + 1
        normalized_end = end - 1
    else:
        normalized_start = start
        normalized_end = end
    if text[normalized_start:normalized_end] != "笑":
        return target
    token_index = rendered_token_index_for_range(
        text,
        rendered_yomi,
        start=normalized_start,
        end=normalized_end,
    )
    if token_index is None:
        return target
    return {
        **target,
        "surface": "笑",
        "token_surface": "笑",
        "target_start": normalized_start,
        "target_end": normalized_end,
        "token_index": token_index,
        "current_reading": "ワライ",
        "current_reading_hiragana": "わらい",
        "is_safe": True,
        "review_status": "safe",
        "highlight_level": "none",
        "accepted_signal_names": ["safe_by_parenthesized_laughter_normalization"],
        "status_reason": "normalized_parenthesized_laughter",
        "signals": [
            {
                "name": "safe_by_parenthesized_laughter_normalization",
                "accepted": True,
                "reason": "punctuation_unread_laugh_read_as_warai",
            }
        ],
    }


def rendered_token_index_for_range(
    text: str,
    rendered_yomi: str,
    *,
    start: int,
    end: int,
) -> int | None:
    cursor = 0
    for index, (surface, _reading) in enumerate(parse_rendered_pairs(rendered_yomi)):
        source_surface = surface.replace("\u00a0", " ")
        token_end = cursor + len(source_surface)
        if not source_surfaces_equal(text[cursor:token_end], source_surface):
            return None
        if cursor == start and token_end == end:
            return index
        cursor = token_end
    return None


def build_interaction_spans(
    *,
    unit_id: str,
    text: str,
    rendered_yomi: str,
    targets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    pairs = parse_rendered_pairs(rendered_yomi) if rendered_yomi else []
    grouped: dict[tuple[int, int, int, str], list[dict[str, Any]]] = {}
    for target in targets:
        bounds = interaction_token_bounds(text, target)
        if bounds is None:
            continue
        start, end = bounds
        token_index = target.get("token_index")
        key = (
            start,
            end,
            int(token_index) if isinstance(token_index, int) else -1,
            text[start:end],
        )
        grouped.setdefault(key, []).append(target)

    spans: list[dict[str, Any]] = []
    for (start, end, token_index, surface), span_targets in sorted(grouped.items()):
        candidates = interaction_span_candidates(
            surface=surface,
            token_index=token_index,
            pairs=pairs,
            targets=span_targets,
        )
        if not candidates:
            continue
        default_candidate = interaction_span_default_candidate(candidates, span_targets)
        spans.append(
            {
                "item_id": f"{unit_id}:s{start + 1:04d}-{end:04d}",
                "span_id": f"{unit_id}:s{start + 1:04d}-{end:04d}",
                "surface": surface,
                "token_surface": surface,
                "target_start": start,
                "target_end": end,
                "token_index": token_index if token_index >= 0 else None,
                "chunk_index": 0,
                "current_reading": hira_to_kata(
                    str(next((row["reading"] for row in candidates if row["source"] == "current"), ""))
                ),
                "current_reading_hiragana": next(
                    (row["reading"] for row in candidates if row["source"] == "current"),
                    None,
                ),
                "is_safe": all(bool(target.get("is_safe")) for target in span_targets),
                "review_status": (
                    "safe"
                    if all(bool(target.get("is_safe")) for target in span_targets)
                    else "unresolved"
                ),
                "highlight_level": (
                    "none"
                    if all(bool(target.get("is_safe")) for target in span_targets)
                    else "target"
                ),
                "default_candidate_id": default_candidate.get("id"),
                "default_choice_source": default_candidate.get("source"),
                "default_reading": default_candidate.get("reading"),
                "candidates": candidates,
                "legacy_target_item_ids": [str(target.get("item_id") or "") for target in span_targets],
                "signals": [
                    signal
                    for target in span_targets
                    for signal in target.get("signals", [])
                    if isinstance(signal, dict)
                ],
            }
        )
    validate_interaction_spans(text, spans)
    return spans


def validate_interaction_spans(text: str, spans: list[dict[str, Any]]) -> None:
    previous_end = 0
    for span in sorted(spans, key=lambda row: (int(row["target_start"]), int(row["target_end"]))):
        start = int(span["target_start"])
        end = int(span["target_end"])
        surface = str(span.get("surface") or "")
        if start < previous_end:
            raise ValueError(f"overlapping interaction span at {start}:{end}")
        if not (0 <= start < end <= len(text)) or not source_surfaces_equal(text[start:end], surface):
            raise ValueError(f"interaction span surface mismatch at {start}:{end}")
        for candidate in span.get("candidates", []):
            if not isinstance(candidate, dict) or candidate.get("source") == "none":
                continue
            tokens = candidate.get("tokens")
            if not isinstance(tokens, list) or "".join(
                str(token[0])
                for token in tokens
                if isinstance(token, list) and len(token) == 2
            ) != surface:
                raise ValueError(f"interaction candidate does not reproduce {surface!r}")
            ruby_nodes = candidate.get("ruby_nodes")
            if not isinstance(ruby_nodes, list) or "".join(
                str(node.get("text") or "")
                for node in ruby_nodes
                if isinstance(node, dict)
            ) != surface:
                raise ValueError(f"interaction ruby projection does not reproduce {surface!r}")
        previous_end = end


def interaction_token_bounds(
    text: str,
    target: dict[str, Any],
) -> tuple[int, int] | None:
    surface = str(target.get("surface") or "")
    token_surface = str(target.get("token_surface") or surface)
    start = target.get("target_start")
    end = target.get("target_end")
    if not surface or not token_surface or not isinstance(start, int) or not isinstance(end, int):
        return None
    candidates: list[tuple[int, int]] = []
    offset = token_surface.find(surface)
    while offset >= 0:
        token_start = start - offset
        token_end = token_start + len(token_surface)
        if (
            token_start >= 0
            and token_end <= len(text)
            and source_surfaces_equal(text[token_start:token_end], token_surface)
        ):
            candidates.append((token_start, token_end))
        offset = token_surface.find(surface, offset + 1)
    if len(set(candidates)) == 1:
        return candidates[0]
    if source_surfaces_equal(text[start:end], surface):
        return start, end
    return None


def source_surfaces_equal(left: str, right: str) -> bool:
    return left.replace(" ", "\u00a0") == right.replace(" ", "\u00a0")


def interaction_span_candidates(
    *,
    surface: str,
    token_index: int,
    pairs: list[tuple[str, str]],
    targets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    def add(candidate_id: str, source: str, label: str, reading: object) -> None:
        if not isinstance(reading, str) or not reading:
            return
        normalized = normalize_hiragana_reading(reading)
        if not is_valid_yomi_reading(normalized):
            return
        if any(row.get("reading") == normalized for row in candidates):
            return
        candidates.append(
            {
                "id": candidate_id,
                "source": source,
                "label": label,
                "reading": normalized,
                "tokens": [[surface, hira_to_kata(normalized)]],
                "ruby_nodes": ruby_nodes_for_surface_reading(surface, normalized),
            }
        )

    current = interaction_current_reading(surface, token_index, pairs)
    add("current", "current", "Current mechanical/hybrid", current)
    full_dictionary_readings = final_review_surface_readings(surface)
    for target in targets:
        for candidate in target.get("candidates", []):
            if not isinstance(candidate, dict) or candidate.get("source") == "none":
                continue
            completed = complete_interaction_reading(
                surface,
                target,
                candidate.get("reading"),
                full_dictionary_readings=full_dictionary_readings,
                current_reading=current,
            )
            add(
                str(candidate.get("id") or candidate.get("source") or "candidate"),
                str(candidate.get("source") or "candidate"),
                str(candidate.get("label") or "Reading candidate"),
                completed,
            )
    for index, reading in enumerate(full_dictionary_readings):
        add(f"dictionary:{index}", "dictionary", "Dictionary", reading)
    candidates.append(
        {
            "id": "none",
            "source": "none",
            "label": "No ruby",
            "reading": None,
            "tokens": [],
            "ruby_nodes": [{"type": "text", "text": surface}],
        }
    )
    return candidates


def interaction_current_reading(
    surface: str,
    token_index: int,
    pairs: list[tuple[str, str]],
) -> str | None:
    if 0 <= token_index < len(pairs) and source_surfaces_equal(pairs[token_index][0], surface):
        return normalize_hiragana_reading(pairs[token_index][1])
    matches = [reading for pair_surface, reading in pairs if source_surfaces_equal(pair_surface, surface)]
    if len(matches) == 1:
        return normalize_hiragana_reading(matches[0])
    return None


def final_review_surface_readings(surface: str) -> tuple[str, ...]:
    return load_final_review_surface_readings().get(surface, ())


def complete_interaction_reading(
    token_surface: str,
    target: dict[str, Any],
    reading: object,
    *,
    full_dictionary_readings: tuple[str, ...],
    current_reading: str | None,
) -> str | None:
    if not isinstance(reading, str) or not reading:
        return None
    normalized = normalize_hiragana_reading(reading)
    full_known = {normalize_hiragana_reading(value) for value in full_dictionary_readings}
    if normalized == current_reading or normalized in full_known:
        return normalized
    target_surface = str(target.get("surface") or "")
    offset = token_surface.find(target_surface)
    if not target_surface or offset < 0:
        return None
    prefix = token_surface[:offset]
    suffix = token_surface[offset + len(target_surface) :]
    if (prefix and not all(is_kana(char) for char in prefix)) or (
        suffix and not all(is_kana(char) for char in suffix)
    ):
        return None
    prefix_reading = kana_surface_to_hira(prefix)
    suffix_reading = kana_surface_to_hira(suffix)
    if prefix_reading and not normalized.startswith(prefix_reading):
        normalized = prefix_reading + normalized
    if suffix_reading and not normalized.endswith(suffix_reading):
        normalized += suffix_reading
    return normalized


def interaction_span_default_candidate(
    candidates: list[dict[str, Any]],
    targets: list[dict[str, Any]],
) -> dict[str, Any]:
    preferred_ids = [str(target.get("default_candidate_id") or "") for target in targets]
    for preferred_id in preferred_ids:
        for candidate in candidates:
            if candidate.get("id") == preferred_id:
                return candidate
    return next(
        (candidate for candidate in candidates if candidate.get("source") == "current"),
        candidates[0],
    )


def with_ruby_display_nodes(surface: str, candidate: dict[str, Any]) -> dict[str, Any]:
    reading = candidate.get("reading")
    if isinstance(reading, str) and reading:
        return {
            **candidate,
            "ruby_nodes": ruby_nodes_for_surface_reading(surface, reading),
        }
    return {
        **candidate,
        "ruby_nodes": [{"type": "text", "text": surface}],
    }


def default_candidate_source(
    target: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> str:
    if has_accepted_no_ruby_signal(target):
        return "none"
    if bool(target.get("is_safe")):
        return "current"
    for signal in target.get("signals") or []:
        if not isinstance(signal, dict):
            continue
        if signal.get("name") != "safe_by_llm_match":
            continue
        if signal.get("accepted"):
            continue
        if signal.get("status") != "mismatched":
            continue
        llm_reading = signal.get("llm_reading")
        if not isinstance(llm_reading, str) or not llm_reading:
            continue
        if candidate_by_source(candidates, "llm") is not None:
            return "llm"
    return "current"


def has_accepted_no_ruby_signal(target: dict[str, Any]) -> bool:
    for signal in target.get("signals") or []:
        if not isinstance(signal, dict):
            continue
        if signal.get("accepted") and signal.get("preferred_choice_source") == "none":
            return True
    return False


def candidate_by_source(
    candidates: list[dict[str, Any]],
    source: str,
) -> dict[str, Any] | None:
    for candidate in candidates:
        if candidate.get("source") == source:
            return candidate
    return None


def reading_candidates(target: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    accepted_no_ruby = has_accepted_no_ruby_signal(target)

    def add(
        source: str,
        label: str,
        reading: object,
        *,
        accepted: bool = False,
        candidate_id: str | None = None,
    ) -> None:
        if not isinstance(reading, str) or not reading:
            return
        normalized = normalize_hiragana_reading(reading)
        if not is_valid_yomi_reading(normalized):
            return
        if any(candidate["reading"] == normalized for candidate in candidates):
            return
        candidates.append(
            {
                "id": candidate_id or source,
                "source": source,
                "label": label,
                "reading": normalized,
                "accepted": accepted,
            }
        )

    accepted_names = set(target.get("accepted_signal_names") or [])
    add(
        "current",
        "Current mechanical/hybrid",
        target.get("current_reading_hiragana") or target.get("current_reading"),
        accepted=bool(target.get("is_safe")) and not accepted_no_ruby,
    )
    numeric_rule = numeric_compound_rule(str(target.get("surface") or ""))
    if numeric_rule is not None:
        for index, reading in enumerate(numeric_rule.review_readings):
            add(
                "numeric_compound",
                "Japanese numeric compound",
                reading,
                candidate_id=f"numeric_compound:{index}",
            )
    for signal in target.get("signals") or []:
        if not isinstance(signal, dict):
            continue
        name = str(signal.get("name") or "")
        if name == "safe_by_llm_match":
            add(
                "llm",
                "LLM reading",
                signal.get("llm_reading"),
                accepted="safe_by_llm_match" in accepted_names,
            )
        elif name == "safe_by_corpus_frequency":
            dominant = signal.get("dominant")
            if isinstance(dominant, dict):
                reading = dominant.get("reading")
                if signal.get("evidence_scope") == "token":
                    reading = project_token_reading_to_target(target, reading)
                add(
                    "corpus_frequency",
                    "Corpus-frequency dominant",
                    reading,
                    accepted="safe_by_corpus_frequency" in accepted_names,
                )
        elif name == "safe_by_stable_dictionary" and signal.get("accepted"):
            add(
                "stable_dictionary",
                "Stable dictionary",
                target.get("current_reading_hiragana") or target.get("current_reading"),
                accepted=True,
            )
    for index, reading in enumerate(final_review_dictionary_readings(target)):
        add(
            "dictionary",
            "Dictionary",
            reading,
            candidate_id=f"dictionary:{index}",
        )
    for index, reading in enumerate(final_review_reading_alternatives(target)):
        add(
            "usage_alternative",
            "Common usage alternative",
            reading,
            candidate_id=f"usage_alternative:{index}",
        )
    candidates.append(
        {
            "id": "none",
            "source": "none",
            "label": "No ruby",
            "reading": None,
            "accepted": accepted_no_ruby,
        }
    )
    return candidates


def final_review_dictionary_readings(target: dict[str, Any]) -> tuple[str, ...]:
    surface = str(target.get("surface") or "")
    if not surface:
        return ()
    inventory = load_final_review_surface_readings()
    readings = list(inventory.get(surface, ()))
    token_surface = str(target.get("token_surface") or "")
    if token_surface and token_surface != surface:
        for token_reading in inventory.get(token_surface, ()):
            projected = project_token_reading_to_target(target, token_reading)
            if projected and projected not in readings:
                readings.append(projected)
    return tuple(readings)


def project_token_reading_to_target(target: dict[str, Any], reading: object) -> str | None:
    if not isinstance(reading, str) or not reading:
        return None
    surface = str(target.get("surface") or "")
    token_surface = str(target.get("token_surface") or "")
    if not surface or not token_surface:
        return None
    if surface == token_surface:
        return reading
    result = FuriganaConverter().convert(token_surface, reading)
    if not result.annotated_surface:
        return None
    matches = [
        chunk_reading
        for chunk_surface, chunk_reading in parse_annotated_chunks(result.annotated_surface)
        if chunk_surface == surface
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def final_review_reading_alternatives(target: dict[str, Any]) -> tuple[str, ...]:
    surface = str(target.get("surface") or "")
    normalized = unicodedata.normalize("NFKC", surface).casefold()
    return FINAL_REVIEW_READING_ALTERNATIVES.get(normalized, ())


def build_reading_hints(targets: list[dict[str, Any]]) -> dict[str, str]:
    hints: dict[str, str] = {}
    for target in targets:
        surface = str(target.get("surface") or "")
        for candidate in target.get("candidates", []):
            if not isinstance(candidate, dict):
                continue
            reading = candidate.get("reading")
            if isinstance(reading, str) and reading and surface:
                hints.setdefault(surface, reading)
                break

    stats = load_surface_reading_stats()
    for surface in target_substrings_for_hints(targets):
        if surface in hints:
            continue
        reading = stats.get(surface)
        if reading:
            hints[surface] = reading
    return hints


def target_substrings_for_hints(targets: list[dict[str, Any]]) -> set[str]:
    ordered = sorted(
        [
            target
            for target in targets
            if isinstance(target.get("target_start"), int)
            and isinstance(target.get("target_end"), int)
        ],
        key=lambda target: (int(target["target_start"]), int(target["target_end"])),
    )
    surfaces: set[str] = set()
    for index, target in enumerate(ordered):
        if target.get("is_safe"):
            continue
        end_index = index
        while (
            end_index + 1 < len(ordered)
            and ordered[end_index].get("target_end") == ordered[end_index + 1].get("target_start")
        ):
            end_index += 1
        span = "".join(str(row.get("surface") or "") for row in ordered[index : end_index + 1])
        chars = list(span)
        for start in range(len(chars)):
            for end in range(start + 2, min(len(chars), start + MAX_READING_HINT_SURFACE_LENGTH) + 1):
                surfaces.add("".join(chars[start:end]))
    return surfaces


def build_strong_repair_reading_candidates(rejected_span: str) -> dict[str, list[str]]:
    candidates: dict[str, list[str]] = {}
    surface_readings = load_annotated_form_surface_readings()
    for surface in substrings_for_reading_candidates(rejected_span):
        readings = surface_readings.get(surface)
        if readings:
            candidates[surface] = list(readings)
    return candidates


def substrings_for_reading_candidates(surface: str) -> list[str]:
    chars = list(surface)
    surfaces: list[str] = []
    seen: set[str] = set()
    for start in range(len(chars)):
        for end in range(start + 1, min(len(chars), start + MAX_READING_HINT_SURFACE_LENGTH) + 1):
            candidate = "".join(chars[start:end])
            if candidate in seen:
                continue
            seen.add(candidate)
            surfaces.append(candidate)
    return surfaces


@lru_cache(maxsize=1)
def load_annotated_form_surface_readings() -> dict[str, tuple[str, ...]]:
    return load_surface_readings_from_tsv_paths((ANNOTATED_FORMS_PATH,))


@lru_cache(maxsize=1)
def load_final_review_surface_readings() -> dict[str, tuple[str, ...]]:
    return load_surface_readings_from_tsv_paths((SUPPLEMENTAL_FURIGANA_PATH, ANNOTATED_FORMS_PATH))


def load_surface_readings_from_tsv_paths(paths: tuple[Path, ...]) -> dict[str, tuple[str, ...]]:
    counts: dict[str, Counter[str]] = {}
    for path in paths:
        if not path.exists():
            continue
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            required = {"surface", "reading"}
            if not required.issubset(reader.fieldnames or set()):
                continue
            for row in reader:
                surface = row["surface"]
                reading = normalize_hiragana_reading(row["reading"])
                if not surface or not reading or not is_valid_yomi_reading(reading):
                    continue
                counts.setdefault(surface, Counter())[reading] += 1
    return {
        surface: tuple(
            reading
            for reading, _count in sorted(
                counter.items(),
                key=lambda item: (-item[1], item[0]),
            )
        )
        for surface, counter in counts.items()
    }


@lru_cache(maxsize=1)
def load_surface_reading_stats() -> dict[str, str]:
    if not SURFACE_READING_STATS_PATH.exists():
        return {}
    stats: dict[str, str] = {}
    with SURFACE_READING_STATS_PATH.open(encoding="utf-8") as handle:
        header = next(handle, "").rstrip("\n").split("\t")
        columns = {name: index for index, name in enumerate(header)}
        required = {"surface", "reading", "count", "share"}
        if not required.issubset(columns):
            return {}
        for line in handle:
            fields = line.rstrip("\n").split("\t")
            try:
                surface = fields[columns["surface"]]
                reading = fields[columns["reading"]]
                count = int(fields[columns["count"]])
                share = float(fields[columns["share"]])
            except (IndexError, ValueError):
                continue
            normalized = normalize_hiragana_reading(reading)
            if (
                surface
                and normalized
                and is_valid_yomi_reading(normalized)
                and count >= READING_HINT_MIN_COUNT
                and share >= READING_HINT_MIN_SHARE
            ):
                stats[surface] = normalized
    return stats


def build_ruby_segments(
    text: str,
    targets: list[dict[str, Any]],
    *,
    rendered_yomi: str = "",
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    cursor = 0
    ordered_targets = sorted(
        [
            target
            for target in targets
            if isinstance(target.get("target_start"), int)
            and isinstance(target.get("target_end"), int)
            and int(target["target_start"]) >= 0
            and int(target["target_end"]) > int(target["target_start"])
        ],
        key=lambda target: (int(target["target_start"]), int(target["target_end"])),
    )
    display_spans: list[tuple[int, int, dict[str, Any] | None, str | None]] = [
        (
            int(target["target_start"]),
            int(target["target_end"]),
            target,
            None,
        )
        for target in ordered_targets
    ]
    target_ranges = [(start, end) for start, end, _, _ in display_spans]
    for occurrence in numeric_compound_occurrences(text, rendered_yomi):
        if any(
            occurrence.start < target_end and occurrence.end > target_start
            for target_start, target_end in target_ranges
        ):
            continue
        display_spans.append(
            (
                occurrence.start,
                occurrence.end,
                None,
                kata_to_hira(occurrence.reading),
            )
        )
    display_spans.sort(key=lambda span: (span[0], span[1], span[2] is None))
    for start, end, target, static_reading in display_spans:
        if start < cursor:
            continue
        if cursor < start:
            segments.append({"type": "text", "text": text[cursor:start]})
        if target is not None:
            segments.append(
                {
                    "type": "ruby",
                    "text": text[start:end],
                    "target_item_id": target["item_id"],
                    "reading": ruby_segment_reading(target),
                    "is_safe": target.get("is_safe"),
                    "highlight_level": target.get("highlight_level"),
                }
            )
        else:
            segments.append(
                {
                    "type": "ruby",
                    "text": text[start:end],
                    "reading": static_reading,
                    "display_only": True,
                }
            )
        cursor = end
    if cursor < len(text):
        segments.append({"type": "text", "text": text[cursor:]})
    return segments


def ruby_segment_reading(target: dict[str, Any]) -> str | None:
    if target.get("default_choice_source") == "none":
        return None
    return target.get("default_reading") or target.get("current_reading_hiragana")


def rendered_yomi_ruby_tokens(rendered: str) -> list[dict[str, Any]]:
    try:
        pairs = editable_rendered_to_yomi_tokens(rendered)
    except YomiTokenError:
        pairs = [
            list(split_rendered_yomi_token(raw_token))
            for raw_token in str(rendered or "").strip().split()
        ]
    return yomi_tokens_ruby_tokens(pairs)


def yomi_tokens_ruby_tokens(pairs: list[list[str]]) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    for surface, reading in normalize_yomi_tokens(pairs):
        tokens.append(
            {
                "surface": surface,
                "reading": reading,
                "nodes": ruby_nodes_for_surface_reading(surface, reading),
            }
        )
    return tokens


def split_rendered_yomi_token(token: str) -> tuple[str, str]:
    separator = token.rfind("/")
    if separator < 0:
        return token, ""
    return token[:separator], token[separator + 1 :]


def ruby_nodes_for_surface_reading(surface: str, reading: str) -> list[dict[str, str]]:
    if not should_display_ruby(surface, reading):
        return [{"type": "text", "text": surface}]
    reading_hira = kata_to_hira(reading)
    if has_han(surface) and re.search(r"[0-9０-９]", surface):
        return [{"type": "ruby", "text": surface, "reading": reading_hira}]
    if has_han(surface) and has_latin(surface):
        return [{"type": "ruby", "text": surface, "reading": reading_hira}]
    if has_han(surface):
        result = furigana_converter().convert(surface, reading)
        if result.annotated_surface:
            return annotated_furigana_nodes(result.annotated_surface)
        return [{"type": "ruby", "text": surface, "reading": reading_hira}]
    mixed_nodes = mixed_latin_kana_ruby_nodes(surface, reading_hira)
    if mixed_nodes is not None:
        return mixed_nodes
    return [{"type": "ruby", "text": surface, "reading": reading_hira}]


def should_display_ruby(surface: str, reading: str) -> bool:
    if not surface or not reading or surface == reading:
        return False
    return bool(re.search(r"[一-龯々〆ヵヶA-Za-zＡ-Ｚａ-ｚ]", surface))


def mixed_latin_kana_ruby_nodes(surface: str, reading_hira: str) -> list[dict[str, str]] | None:
    if not has_latin(surface) or not has_kana(surface):
        return None
    elements = surface_reading_elements(surface)
    if not any(kind == "latin" for kind, _ in elements):
        return None

    result: list[dict[str, str]] | None = None

    def rec(index: int, reading_index: int, nodes: list[dict[str, str]]) -> None:
        nonlocal result
        if result is not None:
            return
        if index == len(elements):
            if reading_index == len(reading_hira):
                result = [node for node in nodes if node.get("text")]
            return
        kind, text = elements[index]
        if kind == "kana":
            fixed = kana_surface_to_hira(text)
            if reading_hira.startswith(fixed, reading_index):
                nodes.append({"type": "text", "text": text})
                rec(index + 1, reading_index + len(fixed), nodes)
                nodes.pop()
            return
        if kind == "other":
            if reading_hira.startswith(text, reading_index):
                nodes.append({"type": "text", "text": text})
                rec(index + 1, reading_index + len(text), nodes)
                nodes.pop()
            return

        remaining_min = minimum_remaining_surface_reading(elements[index + 1 :])
        max_end = len(reading_hira) - remaining_min
        for end in range(reading_index + 1, max_end + 1):
            nodes.append({"type": "ruby", "text": text, "reading": reading_hira[reading_index:end]})
            rec(index + 1, end, nodes)
            nodes.pop()
            if result is not None:
                return

    rec(0, 0, [])
    return result


def surface_reading_elements(surface: str) -> list[tuple[str, str]]:
    elements: list[tuple[str, str]] = []
    for char in surface:
        kind = surface_reading_kind(char)
        if elements and elements[-1][0] == kind:
            elements[-1] = (kind, elements[-1][1] + char)
        else:
            elements.append((kind, char))
    return elements


def surface_reading_kind(char: str) -> str:
    if is_latin(char):
        return "latin"
    if is_kana(char):
        return "kana"
    return "other"


def minimum_remaining_surface_reading(elements: list[tuple[str, str]]) -> int:
    total = 0
    for kind, text in elements:
        if kind == "kana":
            total += len(kana_surface_to_hira(text))
        elif kind == "other":
            total += len(text)
        else:
            total += 1
    return total


def has_latin(text: str) -> bool:
    return any(is_latin(char) for char in text)


def is_latin(char: str) -> bool:
    return bool(re.match(r"[A-Za-zＡ-Ｚａ-ｚ]", char))


def has_kana(text: str) -> bool:
    return any(is_kana(char) for char in text)


def is_kana(char: str) -> bool:
    return "\u3041" <= char <= "\u3096" or "\u30a1" <= char <= "\u30fa" or char in {"ー", "ｰ"}


def kana_surface_to_hira(text: str) -> str:
    return kata_to_hira(text).replace("ｰ", "ー")


def annotated_furigana_nodes(annotated: str) -> list[dict[str, str]]:
    nodes: list[dict[str, str]] = []
    cursor = 0
    for match in re.finditer(r"([^（）]+?)（([^（）]+)）", annotated):
        if cursor < match.start():
            nodes.append({"type": "text", "text": annotated[cursor : match.start()]})
        surface_prefix, ruby_surface = split_trailing_ruby_surface(match.group(1))
        if surface_prefix:
            nodes.append({"type": "text", "text": surface_prefix})
        if ruby_surface:
            nodes.append({"type": "ruby", "text": ruby_surface, "reading": match.group(2)})
        cursor = match.end()
    if cursor < len(annotated):
        nodes.append({"type": "text", "text": annotated[cursor:]})
    return [node for node in nodes if node.get("text")]


def split_trailing_ruby_surface(text: str) -> tuple[str, str]:
    if has_han(text) and all(has_han(char) or char in {"ヶ", "ケ", "ヵ"} for char in text):
        return "", text
    end = len(text)
    start = end
    while start > 0 and has_han(text[start - 1]):
        start -= 1
    return text[:start], text[start:end]


@lru_cache(maxsize=1)
def furigana_converter() -> FuriganaConverter:
    return FuriganaConverter.from_tsv_many([ANNOTATED_FORMS_PATH, SUPPLEMENTAL_FURIGANA_PATH])


def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def store_review_submission(
    submission: dict[str, Any],
    *,
    submission_store_dir: str | Path,
) -> Path:
    store_dir = Path(submission_store_dir)
    store_dir.mkdir(parents=True, exist_ok=True)
    submission_id = sanitize_submission_id(str(submission["submission_id"]))
    output_path = store_dir / f"{submission_id}.json"
    output_path.write_text(
        json.dumps(submission, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path


def load_review_submissions(
    submission_store_dir: str | Path,
    *,
    pack_id: str,
) -> list[dict[str, Any]]:
    store_dir = Path(submission_store_dir)
    rows: list[dict[str, Any]] = []
    if not store_dir.exists():
        return rows
    for path in sorted(store_dir.glob("*.json")):
        try:
            payload = load_json(path)
        except json.JSONDecodeError:
            continue
        if str(payload.get("review_stage")) != REVIEW_STAGE:
            continue
        if str(payload.get("pack_id")) != pack_id:
            continue
        payload["_source_path"] = str(path)
        rows.append(payload)
    rows.sort(
        key=lambda row: (
            int(row.get("generated_at_epoch", 0)),
            str(row.get("submission_id", "")),
            str(row.get("_source_path", "")),
        )
    )
    return rows


def finalized_correction_submission_id(submission: dict[str, Any]) -> str:
    raw = submission.get("submission_id")
    if raw:
        return str(raw)
    source = submission.get("_source_issue")
    issue_part = ""
    if isinstance(source, dict):
        issue = source.get("issue_number")
        comment = source.get("comment_id")
        issue_part = f"issue_{issue or 'unknown'}"
        if comment:
            issue_part += f"__comment_{comment}"
    digest = hashlib.sha256(
        json.dumps(submission, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    return f"finalized_correction__{issue_part or 'local'}__{digest}"


def load_finalized_correction_submissions(
    submission_store_dir: str | Path,
    *,
    track_name: str | None = None,
) -> list[dict[str, Any]]:
    store_dir = Path(submission_store_dir)
    rows: list[dict[str, Any]] = []
    if not store_dir.exists():
        return rows
    for path in sorted(store_dir.glob("*.json")):
        try:
            payload = load_json(path)
        except json.JSONDecodeError:
            continue
        if str(payload.get("submission_type")) != FINALIZED_CORRECTION_SUBMISSION_TYPE:
            continue
        if str(payload.get("review_stage")) != FINALIZED_CORRECTION_STAGE:
            continue
        if track_name is not None and str(payload.get("track_name") or "") != track_name:
            continue
        payload["_source_path"] = str(path)
        payload.setdefault("submission_id", finalized_correction_submission_id(payload))
        rows.append(payload)
    rows.sort(
        key=lambda row: (
            int(row.get("generated_at_epoch", 0)),
            str(row.get("submission_id", "")),
            str(row.get("_source_path", "")),
        )
    )
    return rows


def apply_finalized_correction_submissions_file(
    *,
    root: Path,
    submission_store_dir: Path,
    track_name: str,
    summary_json: Path,
) -> dict[str, Any]:
    submissions = load_finalized_correction_submissions(
        submission_store_dir,
        track_name=track_name,
    )
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    skipped: list[dict[str, Any]] = []
    for submission in submissions:
        batch_name = str(submission.get("batch_name") or "")
        if not batch_name:
            skipped.append(
                {
                    "reason": "missing_batch_name",
                    "submission_id": finalized_correction_submission_id(submission),
                    "source": submission.get("_source_issue"),
                }
            )
            continue
        for unit_patch in submission.get("units") or []:
            if not isinstance(unit_patch, dict):
                skipped.append(
                    {
                        "reason": "invalid_unit_patch",
                        "submission_id": finalized_correction_submission_id(submission),
                        "source": submission.get("_source_issue"),
                    }
                )
                continue
            grouped.setdefault(batch_name, []).append((submission, unit_patch))

    batches: list[dict[str, Any]] = []
    total_applied = 0
    total_skipped = len(skipped)
    for batch_name, patches in sorted(grouped.items()):
        batch_summary = apply_finalized_correction_patches_to_batch(
            root=root,
            batch_name=batch_name,
            patches=patches,
        )
        batches.append(batch_summary)
        total_applied += int(batch_summary.get("applied_count") or 0)
        total_skipped += int(batch_summary.get("skipped_count") or 0)

    summary = {
        "rule": "finalized_correction_apply_v1",
        "track_name": track_name,
        "submission_count": len(submissions),
        "submission_paths": [str(row.get("_source_path", "")) for row in submissions],
        "batch_count": len(batches),
        "applied_count": total_applied,
        "skipped_count": total_skipped,
        "pre_group_skipped": skipped,
        "batches": batches,
        "summary_json": str(summary_json),
        "stage_complete": total_skipped == 0,
    }
    if total_skipped:
        summary["blocking_reason"] = f"{total_skipped} finalized correction patch(es) were not applied."
    summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def apply_finalized_correction_patches_to_batch(
    *,
    root: Path,
    batch_name: str,
    patches: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    final_jsonl = root / "data" / "units" / batch_name / "units.yomi.final.jsonl"
    skipped_jsonl = root / "data" / "units" / batch_name / "units.yomi.skipped.jsonl"
    excluded_jsonl = root / "data" / "units" / batch_name / "units.yomi.excluded.jsonl"
    if not final_jsonl.exists() and not skipped_jsonl.exists() and not excluded_jsonl.exists():
        return {
            "batch_name": batch_name,
            "status": "missing_finalized_artifacts",
            "applied_count": 0,
            "skipped_count": len(patches),
            "skipped": [
                finalized_correction_skip_record(
                    submission,
                    patch,
                    reason="missing_finalized_artifacts",
                )
                for submission, patch in patches
            ],
        }

    latest_by_unit: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    skipped: list[dict[str, Any]] = []
    for submission, patch in patches:
        unit_id = str(patch.get("unit_id") or "")
        if not unit_id:
            skipped.append(
                finalized_correction_skip_record(
                    submission,
                    patch,
                    reason="missing_unit_id",
                )
            )
            continue
        latest_by_unit[unit_id] = (submission, patch)

    final_rows = load_jsonl(final_jsonl) if final_jsonl.exists() else []
    skipped_rows = load_jsonl(skipped_jsonl) if skipped_jsonl.exists() else []
    excluded_rows = load_jsonl(excluded_jsonl) if excluded_jsonl.exists() else []
    final_ids = {str(row.get("unit_id") or "") for row in final_rows}
    skipped_ids = {str(row.get("unit_id") or "") for row in skipped_rows}
    excluded_ids = {str(row.get("unit_id") or "") for row in excluded_rows}
    duplicate_ids = (
        (final_ids & skipped_ids)
        | (final_ids & excluded_ids)
        | (skipped_ids & excluded_ids)
    ) - {""}
    if duplicate_ids:
        return {
            "batch_name": batch_name,
            "status": "duplicate_finalized_unit_ids",
            "applied_count": 0,
            "skipped_count": len(patches),
            "skipped": [
                finalized_correction_skip_record(
                    submission,
                    patch,
                    reason="duplicate_finalized_unit_id",
                )
                for submission, patch in patches
            ],
        }

    applied: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    restored: list[dict[str, Any]] = []
    newly_skipped: list[dict[str, Any]] = []
    newly_excluded: list[dict[str, Any]] = []
    matched_units: set[str] = set()
    retained_final_rows: list[dict[str, Any]] = []
    for row in final_rows:
        unit_id = str(row.get("unit_id") or "")
        patch_pair = latest_by_unit.get(unit_id)
        if patch_pair is None:
            retained_final_rows.append(row)
            continue
        matched_units.add(unit_id)
        submission, patch = patch_pair
        result = apply_finalized_correction_patch_to_unit(row, submission, patch)
        if result["status"] not in {"applied", "already_applied"}:
            skipped.append(result)
            retained_final_rows.append(row)
            continue
        disposition = finalized_correction_patch_disposition(patch)
        if disposition == SCOPE_SKIP:
            record_finalized_correction_disposition(
                row,
                disposition=SCOPE_SKIP,
                submission=submission,
            )
            moved = {**result, "status": "applied", "skipped": True}
            applied.append(moved)
            accepted.append(moved)
            newly_skipped.append(row)
        elif disposition == SCOPE_EXCLUDE:
            tombstone = exclusion_tombstone_from_unit(
                row,
                confirmation_submission_id=finalized_correction_submission_id(submission),
                confirmed_at_epoch=int(submission.get("generated_at_epoch") or 0),
            )
            moved = {**result, "status": "applied", "excluded": True}
            applied.append(moved)
            accepted.append(moved)
            newly_excluded.append(tombstone)
        else:
            retained_final_rows.append(row)
            if result["status"] == "applied":
                applied.append(result)
            accepted.append(result)

    retained_skipped_rows: list[dict[str, Any]] = []
    for row in skipped_rows:
        unit_id = str(row.get("unit_id") or "")
        patch_pair = latest_by_unit.get(unit_id)
        if patch_pair is None:
            retained_skipped_rows.append(row)
            continue
        matched_units.add(unit_id)
        submission, patch = patch_pair
        disposition = finalized_correction_patch_disposition(patch, default=SCOPE_SKIP)
        if disposition == SCOPE_SKIP:
            result = apply_finalized_correction_patch_to_unit(row, submission, patch)
            if result["status"] not in {"applied", "already_applied"}:
                skipped.append(result)
            else:
                retained_skipped_rows.append(row)
                if result["status"] == "applied":
                    applied.append(result)
                accepted.append(result)
            continue
        if disposition == SCOPE_EXCLUDE:
            result = apply_finalized_correction_patch_to_unit(row, submission, patch)
            if result["status"] not in {"applied", "already_applied"}:
                skipped.append(result)
                retained_skipped_rows.append(row)
                continue
            tombstone = exclusion_tombstone_from_unit(
                row,
                confirmation_submission_id=finalized_correction_submission_id(submission),
                confirmed_at_epoch=int(submission.get("generated_at_epoch") or 0),
            )
            moved = {**result, "status": "applied", "excluded": True}
            applied.append(moved)
            accepted.append(moved)
            newly_excluded.append(tombstone)
            continue
        if disposition != SCOPE_KEEP:
            skipped.append(
                finalized_correction_skip_record(
                    submission,
                    patch,
                    reason="restoration_not_requested",
                )
            )
            retained_skipped_rows.append(row)
            continue
        restoration_baseline_tokens = current_yomi_tokens_for_correction(row)
        result = apply_finalized_correction_patch_to_unit(row, submission, patch)
        if result["status"] not in {"applied", "already_applied"}:
            skipped.append(result)
            retained_skipped_rows.append(row)
            continue
        record_skip_restoration(row, submission=submission)
        canonicalize_finalized_unit_yomi(
            row,
            grandfathered_tokens=restoration_baseline_tokens,
        )
        result = {**result, "status": "applied", "restored": True}
        applied.append(result)
        accepted.append(result)
        restored.append(result)
        retained_final_rows.append(row)

    for row in excluded_rows:
        unit_id = str(row.get("unit_id") or "")
        patch_pair = latest_by_unit.get(unit_id)
        if patch_pair is None:
            continue
        matched_units.add(unit_id)
        submission, patch = patch_pair
        submission_id = finalized_correction_submission_id(submission)
        if (
            finalized_correction_patch_disposition(patch, default=SCOPE_EXCLUDE)
            == SCOPE_EXCLUDE
            and str(row.get("confirmation_submission_id") or "") == submission_id
        ):
            accepted.append(
                {
                    "status": "already_applied",
                    "submission_id": submission_id,
                    "unit_id": unit_id,
                    "excluded": True,
                    "source": submission.get("_source_issue"),
                }
            )
        else:
            skipped.append(
                finalized_correction_skip_record(
                    submission,
                    patch,
                    reason="terminal_exclusion_cannot_be_modified",
                )
            )

    for unit_id, (submission, patch) in latest_by_unit.items():
        if unit_id not in matched_units:
            skipped.append(
                finalized_correction_skip_record(
                    submission,
                    patch,
                    reason="unknown_unit_id",
                )
            )

    if applied:
        write_jsonl(final_jsonl, retained_final_rows)
        write_jsonl(skipped_jsonl, [*retained_skipped_rows, *newly_skipped])
        write_jsonl(excluded_jsonl, [*excluded_rows, *newly_excluded])

    return {
        "batch_name": batch_name,
        "status": "applied" if applied else "no_changes",
        "final_jsonl": str(final_jsonl),
        "skipped_jsonl": str(skipped_jsonl),
        "excluded_jsonl": str(excluded_jsonl),
        "read_units": len(final_rows) + len(skipped_rows) + len(excluded_rows),
        "applied_count": len(applied),
        "accepted_count": len(accepted),
        "restored_count": len(restored),
        "newly_skipped_count": len(newly_skipped),
        "newly_excluded_count": len(newly_excluded),
        "skipped_count": len(skipped),
        "applied": applied,
        "accepted": accepted,
        "skipped": skipped,
    }


def finalized_correction_patch_disposition(
    patch: dict[str, Any],
    *,
    default: str = SCOPE_KEEP,
) -> str:
    if "disposition" in patch:
        return normalize_scope_disposition(patch.get("disposition"))
    if "skip" in patch:
        return SCOPE_SKIP if bool(patch.get("skip")) else SCOPE_KEEP
    return default


def record_finalized_correction_disposition(
    unit: dict[str, Any],
    *,
    disposition: str,
    submission: dict[str, Any],
) -> None:
    submission_id = finalized_correction_submission_id(submission)
    generated_at_epoch = int(submission.get("generated_at_epoch") or 0)
    human_review = unit.setdefault("analysis", {}).setdefault("human_review", {})
    review = human_review.setdefault("yomi_final", {})
    review.update(
        {
            "reviewed": True,
            "disposition": disposition,
            "skip": disposition != SCOPE_KEEP,
            "submission_id": submission_id,
            "generated_at_epoch": generated_at_epoch,
        }
    )
    history = human_review.setdefault("skip_history", [])
    if not any(
        isinstance(event, dict)
        and event.get("event") == "skipped"
        and str(event.get("submission_id") or "") == submission_id
        for event in history
    ):
        history.append(
            {
                "event": "skipped",
                "submission_id": submission_id,
                "review_stage": FINALIZED_CORRECTION_STAGE,
                "source": submission.get("_source_issue"),
                "generated_at_epoch": generated_at_epoch,
            }
        )


def record_skip_restoration(unit: dict[str, Any], *, submission: dict[str, Any]) -> None:
    submission_id = finalized_correction_submission_id(submission)
    human_review = unit.setdefault("analysis", {}).setdefault("human_review", {})
    review = human_review.setdefault("yomi_final", {})
    history = human_review.setdefault("skip_history", [])
    if not any(
        isinstance(event, dict)
        and event.get("event") == "restored"
        and str(event.get("submission_id") or "") == submission_id
        for event in history
    ):
        history.append(
            {
                "event": "restored",
                "submission_id": submission_id,
                "review_stage": FINALIZED_CORRECTION_STAGE,
                "source": submission.get("_source_issue"),
                "generated_at_epoch": int(submission.get("generated_at_epoch") or 0),
            }
        )
    review["skip"] = False
    review["restored"] = True
    review["restoration_submission_id"] = submission_id


def apply_finalized_correction_patch_to_unit(
    unit: dict[str, Any],
    submission: dict[str, Any],
    patch: dict[str, Any],
) -> dict[str, Any]:
    submission_id = finalized_correction_submission_id(submission)
    unit_id = str(patch.get("unit_id") or "")
    source = submission.get("_source_issue")
    if str(patch.get("text") or "") and str(patch.get("text") or "") != str(unit.get("text") or ""):
        return finalized_correction_skip_record(
            submission,
            patch,
            reason="text_mismatch",
        )
    text = str(unit.get("text") or "")
    try:
        current_tokens = current_yomi_tokens_for_correction(unit)
        original_tokens = correction_patch_yomi_tokens(patch, "original", text=text)
        proposed_tokens = correction_patch_yomi_tokens(patch, "proposed", text=text)
    except YomiTokenError as exc:
        return finalized_correction_skip_record(
            submission,
            patch,
            reason="invalid_yomi_tokens",
            validation_error=str(exc),
        )
    current = yomi_tokens_to_editable_rendered(current_tokens)
    original = yomi_tokens_to_editable_rendered(original_tokens) if original_tokens else ""
    proposed = yomi_tokens_to_editable_rendered(proposed_tokens)
    if current_tokens == proposed_tokens:
        recorded = record_finalized_correction_acknowledgement(
            unit=unit,
            submission_id=submission_id,
            source=source,
            original_rendered_yomi=current,
            proposed_rendered_yomi=proposed,
        )
        cleared = set_manual_correction_required(
            unit,
            required=False,
            source_stage=FINALIZED_CORRECTION_STAGE,
            submission_id=submission_id,
            generated_at_epoch=int(submission.get("generated_at_epoch") or 0),
            reason="finalized correction applied",
        )
        return {
            "status": "applied" if recorded or cleared else "already_applied",
            "submission_id": submission_id,
            "unit_id": unit_id,
            "source": source,
        }
    if original_tokens and current_tokens != original_tokens:
        return finalized_correction_skip_record(
            submission,
            patch,
            reason="original_rendered_yomi_mismatch",
            current_rendered_yomi=current,
        )
    validation = validate_finalized_correction_rendered_yomi(
        unit=unit,
        proposed=proposed,
    )
    if not validation["ok"]:
        return finalized_correction_skip_record(
            submission,
            patch,
            reason="invalid_proposed_rendered_yomi",
            validation_error=str(validation["error"]),
        )
    set_current_yomi_tokens_for_correction(unit, proposed_tokens)
    record_finalized_correction_acknowledgement(
        unit=unit,
        submission_id=submission_id,
        source=source,
        original_rendered_yomi=current,
        proposed_rendered_yomi=proposed,
    )
    set_manual_correction_required(
        unit,
        required=False,
        source_stage=FINALIZED_CORRECTION_STAGE,
        submission_id=submission_id,
        generated_at_epoch=int(submission.get("generated_at_epoch") or 0),
        reason="finalized correction applied",
    )
    return {
        "status": "applied",
        "submission_id": submission_id,
        "unit_id": unit_id,
        "source": source,
    }


def record_finalized_correction_acknowledgement(
    *,
    unit: dict[str, Any],
    submission_id: str,
    source: Any,
    original_rendered_yomi: str,
    proposed_rendered_yomi: str,
) -> bool:
    corrections = (
        unit.setdefault("analysis", {})
        .setdefault("human_review", {})
        .setdefault("finalized_corrections", [])
    )
    if any(
        isinstance(correction, dict)
        and str(correction.get("submission_id") or "") == submission_id
        for correction in corrections
    ):
        return False
    corrections.append(
        {
            "submission_id": submission_id,
            "review_stage": FINALIZED_CORRECTION_STAGE,
            "source": source,
            "original_rendered_yomi": original_rendered_yomi,
            "proposed_rendered_yomi": proposed_rendered_yomi,
        }
    )
    return True


def finalized_correction_skip_record(
    submission: dict[str, Any],
    patch: dict[str, Any],
    *,
    reason: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "status": "skipped",
        "reason": reason,
        "submission_id": finalized_correction_submission_id(submission),
        "unit_id": str(patch.get("unit_id") or ""),
        "source": submission.get("_source_issue"),
        **extra,
    }


def current_rendered_yomi_for_correction(unit: dict[str, Any]) -> str:
    return yomi_tokens_to_editable_rendered(current_yomi_tokens_for_correction(unit))


def current_yomi_tokens_for_correction(unit: dict[str, Any]) -> list[list[str]]:
    direct = unit.get("rendered_yomi")
    if isinstance(direct, str) and direct:
        return editable_rendered_to_yomi_tokens(direct, text=str(unit.get("text") or ""))
    yomi = (
        unit.get("analysis", {})
        .get("mechanical", {})
        .get("yomi", {})
    )
    if isinstance(yomi, dict):
        return yomi_tokens_from_mapping(yomi, text=str(unit.get("text") or ""))
    return []


def set_current_rendered_yomi_for_correction(unit: dict[str, Any], rendered: str) -> None:
    tokens = editable_rendered_to_yomi_tokens(rendered, text=str(unit.get("text") or ""))
    set_current_yomi_tokens_for_correction(unit, tokens)


def set_current_yomi_tokens_for_correction(unit: dict[str, Any], tokens: list[list[str]]) -> None:
    if isinstance(unit.get("rendered_yomi"), str):
        unit["rendered_yomi"] = yomi_tokens_to_editable_rendered(tokens)
        unit["yomi_tokens"] = tokens
        return
    yomi = (
        unit.setdefault("analysis", {})
        .setdefault("mechanical", {})
        .setdefault("yomi", {})
    )
    set_canonical_yomi_tokens(yomi, tokens)


def correction_patch_yomi_tokens(
    patch: dict[str, Any],
    prefix: str,
    *,
    text: str,
) -> list[list[str]]:
    compact = patch.get(f"{prefix}_yomi_tokens")
    if compact is not None:
        tokens = normalize_correction_yomi_tokens(compact)
        if tokens:
            validate_yomi_token_surfaces(tokens, text=text)
        return tokens
    rendered = str(patch.get(f"{prefix}_rendered_yomi") or "")
    if not rendered:
        return []
    return normalize_correction_yomi_tokens(
        editable_rendered_to_yomi_tokens(rendered, text=text)
    )


def normalize_correction_yomi_tokens(value: Any) -> list[list[str]]:
    tokens = normalize_yomi_tokens(value)
    normalized: list[list[str]] = []
    for surface, reading in tokens:
        normalized_reading = hiragana_to_katakana_for_finalized_correction(reading)
        if numeric_compound_rule(surface) is not None:
            pass
        elif is_numeric_only_finalized_correction_surface(surface):
            normalized_reading = ""
        elif re.fullmatch(r"[ \u00a0\u3000]+", surface):
            if normalized_reading and not re.fullmatch(r"[ \u00a0\u3000]+", normalized_reading):
                normalized_reading = surface
        elif not re.search(r"[一-龯々〆A-Za-zＡ-Ｚａ-ｚ]", surface):
            normalized_reading = hiragana_to_katakana_for_finalized_correction(surface)
        normalized.append([surface, normalized_reading])
    return normalized


def validate_finalized_correction_rendered_yomi(
    *,
    unit: dict[str, Any],
    proposed: str,
) -> dict[str, Any]:
    if not proposed:
        return {"ok": False, "error": "rendered yomi is empty"}
    try:
        tokens = editable_rendered_to_yomi_tokens(
            proposed,
            text=str(unit.get("text") or ""),
        )
    except YomiTokenError as exc:
        return {"ok": False, "error": str(exc)}
    if not tokens:
        return {"ok": False, "error": "rendered yomi has no tokens"}
    baseline_counts = Counter(
        (surface, reading)
        for surface, reading in current_yomi_tokens_for_correction(unit)
    )
    for surface, reading in tokens:
        if baseline_counts[(surface, reading)]:
            baseline_counts[(surface, reading)] -= 1
            continue
        reading_validation = validate_finalized_correction_reading(
            surface,
            reading,
        )
        if not reading_validation["ok"]:
            return {
                "ok": False,
                "error": f"token {surface}/{reading}: {reading_validation['error']}",
            }
    return {"ok": True}


def parse_rendered_yomi_tokens_for_finalized_correction(rendered: str) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    for raw in re.split(r"[ \t\r\n]+", str(rendered or "").strip()):
        if not raw:
            continue
        if raw == "/":
            tokens.append({"ok": True, "raw": raw, "surface": " ", "reading": ""})
            continue
        slash_index = raw.rfind("/")
        if slash_index < 0:
            tokens.append({"ok": False, "raw": raw, "error": f"token {raw} must be surface/reading"})
            continue
        surface = raw[:slash_index]
        if not surface:
            tokens.append({"ok": False, "raw": raw, "error": f"token {raw} has no surface before the slash"})
            continue
        tokens.append({"ok": True, "raw": raw, "surface": surface, "reading": raw[slash_index + 1 :]})
    return tokens


def normalize_rendered_yomi_for_finalized_correction(rendered: str) -> str:
    tokens: list[str] = []
    for token in parse_rendered_yomi_tokens_for_finalized_correction(rendered):
        if not token["ok"]:
            tokens.append(str(token["raw"]))
            continue
        surface = str(token["surface"])
        reading = hiragana_to_katakana_for_finalized_correction(str(token["reading"]))
        if is_numeric_only_finalized_correction_surface(surface):
            reading = ""
        tokens.append(f"{surface}/{reading}")
    return " ".join(tokens)


def validate_finalized_correction_reading(surface: str, reading: str) -> dict[str, Any]:
    if re.fullmatch(r"[ \u00a0\u3000]+", surface):
        if reading and not re.fullmatch(r"[ \u00a0\u3000]+", reading):
            return {"ok": False, "error": "space tokens must have an empty or whitespace reading"}
        return {"ok": True}
    if is_numeric_only_finalized_correction_surface(surface):
        if reading:
            return {"ok": False, "error": "numeric-only surfaces must have an empty reading"}
        return {"ok": True}
    numeric_rule = numeric_compound_rule(surface)
    if numeric_rule is not None:
        allowed = (numeric_rule.reading, *numeric_rule.review_readings)
        if reading in allowed:
            return {"ok": True}
        return {
            "ok": False,
            "error": f"reading should be one of {', '.join(allowed)}",
        }
    if reading == "カオモジ":
        if is_symbolic_kaomoji_correction_surface(surface):
            return {"ok": True}
        return {"ok": False, "error": "カオモジ is reserved for symbolic kaomoji surfaces"}
    if is_standalone_laughter_w(surface) and not reading:
        return {"ok": True}
    if has_han(surface) or has_latin(surface):
        if not reading:
            return {"ok": False, "error": "kanji or alphabetic surfaces need a kana reading"}
        if not re.fullmatch(r"[ァ-ヺー]+", reading):
            return {"ok": False, "error": "reading for kanji or alphabetic surfaces must be katakana"}
        return {"ok": True}
    expected = hiragana_to_katakana_for_finalized_correction(surface)
    if reading == expected:
        return {"ok": True}
    return {"ok": False, "error": f"reading should be {expected or '(empty)'}"}


def is_symbolic_kaomoji_correction_surface(surface: str) -> bool:
    return (
        len(surface) >= 3
        and not re.fullmatch(
            r"(?:\([\u3041-\u3096\u30a1-\u30fa\u3400-\u9fff\uf900-\ufaff々〆〻]+\)"
            r"|（[\u3041-\u3096\u30a1-\u30fa\u3400-\u9fff\uf900-\ufaff々〆〻]+）)",
            surface,
        )
        and any(not char.isalnum() and not char.isspace() for char in surface)
    )


def is_numeric_only_finalized_correction_surface(surface: str) -> bool:
    return is_numeric_only_surface(surface)


def normalize_finalized_correction_source_text(value: str) -> str:
    return re.sub(r"[ \t\r\n\u00a0]+", "", str(value or ""))


def hiragana_to_katakana_for_finalized_correction(value: str) -> str:
    return "".join(
        chr(ord(char) + 0x60) if "ぁ" <= char <= "ゖ" else char
        for char in str(value or "")
    )


def replay_review_submissions(
    pack: dict[str, Any],
    submissions: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    items_by_id = {str(item["item_id"]): item for item in pack.get("items", [])}
    items_by_seq = {int(item["seq"]): item for item in pack.get("items", [])}
    effective: dict[str, dict[str, Any]] = {}

    for submission in submissions:
        terminal_exclusion_confirmation = submission.get("terminal_exclusion_confirmation", {})
        confirmed_terminal_exclusion_ids = {
            str(item_id)
            for item_id in terminal_exclusion_confirmation.get("item_ids", [])
        } if (
            isinstance(terminal_exclusion_confirmation, dict)
            and terminal_exclusion_confirmation.get("confirmed") is True
        ) else set()
        overrides = {
            str(row["item_id"]): row
            for row in submission.get("overrides", [])
            if isinstance(row, dict) and str(row.get("item_id", "")) in items_by_id
        }
        for reviewed_range in submission.get("reviewed_ranges", []):
            from_seq = int(reviewed_range["from_seq"])
            to_seq = int(reviewed_range["to_seq"])
            if from_seq > to_seq:
                from_seq, to_seq = to_seq, from_seq
            for seq in range(from_seq, to_seq + 1):
                item = items_by_seq.get(seq)
                if item is None:
                    continue
                item_id = str(item["item_id"])
                effective[item_id] = {
                    "item_id": item_id,
                    "reviewed": True,
                    "disposition": normalize_scope_disposition(
                        item.get("scope_default"),
                        skip=item.get("skip_default", False),
                    ),
                    "skip": bool(item.get("skip_default", False)),
                    "terminal_exclusion_confirmed": item_id in confirmed_terminal_exclusion_ids,
                    "manual_correction_required": bool(
                        item.get("manual_correction_required", False)
                    ),
                    "targets": default_target_rows(item),
                    "span_overrides": [],
                    "note": "",
                    "submission_id": str(submission.get("submission_id", "")),
                    "generated_at_epoch": int(submission.get("generated_at_epoch", 0)),
                }
            for item_id, override in overrides.items():
                item = items_by_id[item_id]
                item_seq = int(item["seq"])
                if item_seq < from_seq or item_seq > to_seq:
                    continue
                current = effective.setdefault(
                    item_id,
                    {
                        "item_id": item_id,
                        "reviewed": True,
                        "disposition": normalize_scope_disposition(
                            item.get("scope_default"),
                            skip=item.get("skip_default", False),
                        ),
                        "skip": bool(item.get("skip_default", False)),
                        "terminal_exclusion_confirmed": item_id in confirmed_terminal_exclusion_ids,
                        "manual_correction_required": bool(
                            item.get("manual_correction_required", False)
                        ),
                        "targets": default_target_rows(item),
                        "span_overrides": [],
                        "note": "",
                    },
                )
                if "skip" in override:
                    current["skip"] = bool(override["skip"])
                    if "disposition" not in override:
                        current["disposition"] = normalize_scope_disposition(
                            None,
                            skip=override["skip"],
                        )
                if "disposition" in override:
                    current["disposition"] = normalize_scope_disposition(
                        override.get("disposition")
                    )
                    current["skip"] = current["disposition"] != SCOPE_KEEP
                if "manual_correction_required" in override:
                    current["manual_correction_required"] = bool(
                        override["manual_correction_required"]
                    )
                current["targets"] = merge_default_and_explicit_target_rows(
                    item,
                    [row for row in override.get("targets", []) if isinstance(row, dict)],
                )
                current["span_overrides"] = [
                    row for row in override.get("span_overrides", []) if isinstance(row, dict)
                ]
                current["note"] = str(override.get("note", "")).strip()
                current["submission_id"] = str(submission.get("submission_id", ""))
                current["generated_at_epoch"] = int(submission.get("generated_at_epoch", 0))
                current["terminal_exclusion_confirmed"] = (
                    item_id in confirmed_terminal_exclusion_ids
                )
    return effective


def apply_strong_repair_review_file(
    *,
    pack_json: Path,
    submission_store_dir: Path,
    strong_apply_summary_json: Path,
    output_summary_json: Path,
    units_jsonl: Path | None = None,
) -> dict[str, Any]:
    pack = load_json(pack_json)
    submissions = load_review_submissions_for_stage(
        submission_store_dir,
        pack_id=str(pack["pack_id"]),
        review_stage=STRONG_REPAIR_REVIEW_STAGE,
    )
    output_summary_json.parent.mkdir(parents=True, exist_ok=True)
    if not submissions:
        summary = {
            "stage_complete": False,
            "rule": STRONG_REPAIR_REVIEW_RULE,
            "pack_id": str(pack["pack_id"]),
            "submission_count": 0,
            "blocking_reason": (
                f"No strong yomi repair review submissions found for pack {pack['pack_id']}."
            ),
        }
        output_summary_json.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return summary

    effective = replay_simple_accept_reject_submissions(pack, submissions)
    items = [row for row in pack.get("items", []) if isinstance(row, dict)]
    reviewed_items = [row for row in items if str(row.get("item_id") or "") in effective]
    rejected_items = [
        item_id
        for item_id, state in effective.items()
        if str(state.get("decision") or "accept") == "reject"
    ]
    unreviewed_count = len(items) - len(reviewed_items)
    manual_summary = apply_manual_strong_repair_review_segments_file(
        pack=pack,
        effective=effective,
        units_jsonl=units_jsonl,
    )
    evidence_summary = apply_strong_repair_evidence_file(
        pack=pack,
        effective=effective,
        units_jsonl=units_jsonl,
    )
    manual_correction_summary = apply_manual_correction_flags_file(
        pack=pack,
        effective=effective,
        units_jsonl=units_jsonl,
        source_stage=STRONG_REPAIR_REVIEW_STAGE,
    )
    invalid_manual_items = manual_summary["invalid_items"]
    stage_complete = unreviewed_count == 0 and not rejected_items and invalid_manual_items == 0
    strong_summary = load_json(strong_apply_summary_json)
    if "stage_complete_before_confirmation" not in strong_summary:
        strong_summary["stage_complete_before_confirmation"] = bool(
            strong_summary.get("stage_complete")
        )
    strong_summary["confirmed"] = bool(stage_complete)
    if stage_complete:
        # Human review can explicitly accept a no-op strong-repair result. In that case the
        # raw LLM apply step remains diagnostic, but the overall strong-repair gate is complete.
        strong_summary["stage_complete"] = True
        strong_summary["confirmation_resolved_noop_items"] = int(
            strong_summary.get("noop_items") or 0
        )
        strong_summary["post_confirmation_unresolved_items"] = 0
        strong_summary.pop("blocking_reason", None)
    strong_summary["confirmation_pack_id"] = str(pack["pack_id"])
    strong_summary["confirmation_submission_count"] = len(submissions)
    strong_summary["confirmation_rejected_items"] = rejected_items
    strong_summary["confirmation_unreviewed_items"] = unreviewed_count
    strong_summary["confirmation_manual_segment_overrides"] = manual_summary["applied_items"]
    strong_summary["confirmation_manual_correction_flag_changes"] = manual_correction_summary[
        "changed_units"
    ]
    strong_summary["confirmation_invalid_manual_segment_overrides"] = invalid_manual_items
    strong_apply_summary_json.write_text(
        json.dumps(strong_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = {
        "stage_complete": stage_complete,
        "rule": STRONG_REPAIR_REVIEW_RULE,
        "pack_id": str(pack["pack_id"]),
        "submission_count": len(submissions),
        "submission_paths": [str(row.get("_source_path", "")) for row in submissions],
        "item_count": len(items),
        "reviewed_items": len(reviewed_items),
        "unreviewed_items": unreviewed_count,
        "rejected_items": rejected_items,
        "manual_segment_overrides": manual_summary,
        "preserved_evidence": evidence_summary,
        "manual_correction_flags": manual_correction_summary,
        "strong_apply_summary_json": str(strong_apply_summary_json),
        "summary_json": str(output_summary_json),
    }
    if not stage_complete:
        summary["blocking_reason"] = (
            "Strong yomi repair review is incomplete or contains rejected repairs."
        )
    output_summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def apply_strong_repair_evidence_file(
    *,
    pack: dict[str, Any],
    effective: dict[str, dict[str, Any]],
    units_jsonl: Path | None,
) -> dict[str, int]:
    evidence_by_unit: dict[str, list[dict[str, Any]]] = {}
    for item in pack.get("items", []):
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("item_id") or "")
        state = effective.get(item_id)
        if not state or str(state.get("decision") or "accept") == "reject":
            continue
        unit_id = str(item.get("unit_id") or "")
        for region in item.get("regions", []) or [item]:
            if not isinstance(region, dict):
                continue
            for comment in region.get("llm_comments", []) or []:
                comment = str(comment or "").strip()
                surface = str(region.get("rejected_span") or "")
                if unit_id and surface and comment:
                    evidence_by_unit.setdefault(unit_id, []).append(
                        {
                            "region_id": str(region.get("region_id") or region.get("item_id") or item_id),
                            "surface": surface,
                            "comment": comment,
                            "used_web_search": bool(region.get("used_web_search")),
                            "surface_occurrence_index": region.get("surface_occurrence_index"),
                        }
                    )
    if units_jsonl is None or not evidence_by_unit:
        return {"reviewed_units": len(evidence_by_unit), "changed_units": 0}
    changed = 0
    output_rows = []
    for unit in load_jsonl(units_jsonl):
        evidence = evidence_by_unit.get(str(unit.get("unit_id") or ""))
        if evidence:
            review = unit.setdefault("analysis", {}).setdefault("human_review", {})
            if review.get("strong_repair_evidence") != evidence:
                review["strong_repair_evidence"] = evidence
                changed += 1
        output_rows.append(json.dumps(unit, ensure_ascii=False))
    tmp_path = units_jsonl.with_suffix(units_jsonl.suffix + ".tmp")
    tmp_path.write_text("\n".join(output_rows) + ("\n" if output_rows else ""), encoding="utf-8")
    tmp_path.replace(units_jsonl)
    return {"reviewed_units": len(evidence_by_unit), "changed_units": changed}


def apply_manual_correction_flags_file(
    *,
    pack: dict[str, Any],
    effective: dict[str, dict[str, Any]],
    units_jsonl: Path | None,
    source_stage: str,
) -> dict[str, Any]:
    states_by_unit: dict[str, dict[str, Any]] = {}
    for item in pack.get("items", []):
        if not isinstance(item, dict):
            continue
        state = effective.get(str(item.get("item_id") or ""))
        unit_id = str(item.get("unit_id") or "")
        if state is not None and unit_id:
            states_by_unit[unit_id] = state
    if not states_by_unit or units_jsonl is None:
        return {"reviewed_units": len(states_by_unit), "changed_units": 0}

    changed_units = 0
    output_rows: list[str] = []
    with units_jsonl.open(encoding="utf-8") as src:
        for line in src:
            if not line.strip():
                continue
            unit = json.loads(line)
            state = states_by_unit.get(str(unit.get("unit_id") or ""))
            if state is not None and set_manual_correction_required(
                unit,
                required=bool(state.get("manual_correction_required")),
                source_stage=source_stage,
                submission_id=str(state.get("submission_id") or ""),
                generated_at_epoch=int(state.get("generated_at_epoch") or 0),
                reason=str(state.get("note") or "").strip(),
            ):
                changed_units += 1
            output_rows.append(json.dumps(unit, ensure_ascii=False))

    tmp_path = units_jsonl.with_suffix(units_jsonl.suffix + ".tmp")
    tmp_path.write_text("\n".join(output_rows) + ("\n" if output_rows else ""), encoding="utf-8")
    tmp_path.replace(units_jsonl)
    return {"reviewed_units": len(states_by_unit), "changed_units": changed_units}


def apply_manual_strong_repair_review_segments_file(
    *,
    pack: dict[str, Any],
    effective: dict[str, dict[str, Any]],
    units_jsonl: Path | None,
) -> dict[str, Any]:
    item_states: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for item in pack.get("items", []):
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("item_id") or "")
        state = effective.get(item_id)
        if not state or str(state.get("decision") or "accept") == "reject":
            continue
        if state.get("manual_segments"):
            item_states.append((item, state))
        region_by_id = {
            str(region.get("region_id") or region.get("item_id") or ""): region
            for region in item.get("regions", [])
            if isinstance(region, dict)
        }
        for region_state in state.get("regions", []):
            if not isinstance(region_state, dict) or not region_state.get("manual_segments"):
                continue
            region_id = str(region_state.get("region_id") or "")
            region = region_by_id.get(region_id)
            if region is not None:
                item_states.append(
                    (
                        {
                            **region,
                            "doc_id": region.get("doc_id") or item.get("doc_id"),
                        },
                        {
                            **region_state,
                            "submission_id": region_state.get("submission_id")
                            or state.get("submission_id"),
                        },
                    )
                )
    if not item_states:
        return {"applied_items": 0, "invalid_items": 0, "invalid": []}
    if units_jsonl is None:
        return {
            "applied_items": 0,
            "invalid_items": len(item_states),
            "invalid": [
                {
                    "item_id": str(item.get("region_id") or item.get("item_id") or ""),
                    "reason": "manual segments require units_jsonl",
                }
                for item, _state in item_states
            ],
        }

    overrides_by_unit: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for item, state in item_states:
        unit_id = str(item.get("unit_id") or "")
        if unit_id:
            overrides_by_unit.setdefault(unit_id, []).append((item, state))

    applied_items = 0
    invalid: list[dict[str, Any]] = []
    output_rows: list[str] = []
    with units_jsonl.open(encoding="utf-8") as src:
        for line in src:
            if not line.strip():
                continue
            unit = json.loads(line)
            unit_id = str(unit.get("unit_id") or "")
            for item, state in overrides_by_unit.get(unit_id, []):
                result = apply_manual_strong_repair_segments(unit, item, state)
                if result["status"] == "applied":
                    applied_items += 1
                elif result["status"] != "unchanged":
                    invalid.append(
                        {
                            "item_id": str(item.get("region_id") or item.get("item_id") or ""),
                            "doc_id": str(item.get("doc_id") or unit.get("doc_id") or ""),
                            "unit_id": unit_id,
                            "submission_id": str(state.get("submission_id") or ""),
                            **result,
                        }
                    )
            output_rows.append(json.dumps(unit, ensure_ascii=False))

    tmp_path = units_jsonl.with_suffix(units_jsonl.suffix + ".tmp")
    tmp_path.write_text("\n".join(output_rows) + ("\n" if output_rows else ""), encoding="utf-8")
    tmp_path.replace(units_jsonl)
    return {
        "applied_items": applied_items,
        "invalid_items": len(invalid),
        "invalid": invalid,
    }


def apply_manual_strong_repair_segments(
    unit: dict[str, Any],
    item: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    original_surface = str(item.get("rejected_span") or "")
    override = {
        "original_surface": original_surface,
        "segments": state.get("manual_segments") or [],
    }
    replacement_pairs = replacement_pairs_from_span_override(override)
    if not original_surface:
        return {"status": "invalid_manual_segments", "reason": "missing rejected span"}
    if not replacement_pairs:
        return {"status": "invalid_manual_segments", "reason": "invalid manual segment readings"}
    replacement_span = "".join(surface for surface, _reading in replacement_pairs)
    yomi = unit.get("analysis", {}).get("mechanical", {}).get("yomi", {})
    rendered = str(yomi.get("rendered") or "")
    try:
        pairs = yomi_tokens_from_mapping(yomi, text=str(unit.get("text") or ""))
    except YomiTokenError as exc:
        return {"status": "invalid_unit", "reason": str(exc)}
    if not pairs:
        return {"status": "invalid_unit", "reason": "missing rendered yomi"}
    targets = [
        target
        for target in item.get("target_escalations", [])
        if isinstance(target, dict)
    ]
    mapping = select_rendered_surface_span(
        pairs,
        original_surface,
        targets=targets,
        reference_rendered=str(item.get("rendered_yomi_before") or ""),
        occurrence_index=item.get("surface_occurrence_index"),
    )
    if mapping is None:
        return {
            "status": "surface_mismatch",
            "reason": "manual rejected span is not unique in rendered yomi",
            "rejected_span": original_surface,
        }
    start = int(mapping["start"])
    end = int(mapping["end"])
    mapped_surface = "".join(surface for surface, _reading in pairs[start:end])
    if replacement_span == original_surface:
        mapped_pairs = mapped_replacement_pairs(replacement_pairs, mapping)
    elif replacement_span == mapped_surface:
        mapped_pairs = replacement_pairs
    else:
        return {
            "status": "invalid_manual_segments",
            "reason": "manual segment surfaces do not match rejected or mapped span",
            "rejected_span": original_surface,
            "mapped_span": mapped_surface,
            "replacement_span": replacement_span,
        }
    if mapped_pairs is None:
        return {
            "status": "surface_mismatch",
            "reason": "manual rejected span has unsupported surrounding text",
            "rejected_span": original_surface,
        }
    if pairs[start:end] == [list(pair) for pair in mapped_pairs]:
        return {"status": "unchanged", "rejected_span": original_surface}
    yomi.setdefault(
        "rendered_before_strong_repair_review",
        rendered or yomi_tokens_to_editable_rendered(pairs),
    )
    pairs[start:end] = [list(pair) for pair in mapped_pairs]
    set_canonical_yomi_tokens(yomi, pairs)
    yomi["rendered"] = " ".join(f"{surface}/{reading}" for surface, reading in pairs)
    unit.setdefault("analysis", {}).setdefault("human_review", {})["yomi_strong_repair"] = {
        "rule": STRONG_REPAIR_REVIEW_RULE,
        "item_id": str(item.get("item_id") or ""),
        "manual_segments": [
            {"surface": surface, "reading": reading}
            for surface, reading in mapped_pairs
        ],
    }
    return {
        "status": "applied",
        "rejected_span": original_surface,
        "replacement": [
            {"surface": surface, "reading": reading}
            for surface, reading in mapped_pairs
        ],
    }


def load_review_submissions_for_stage(
    submission_store_dir: str | Path,
    *,
    pack_id: str,
    review_stage: str,
) -> list[dict[str, Any]]:
    store_dir = Path(submission_store_dir)
    rows: list[dict[str, Any]] = []
    if not store_dir.exists():
        return rows
    for path in sorted(store_dir.glob("*.json")):
        try:
            payload = load_json(path)
        except json.JSONDecodeError:
            continue
        if str(payload.get("review_stage")) != review_stage:
            continue
        if str(payload.get("pack_id")) != pack_id:
            continue
        payload["_source_path"] = str(path)
        rows.append(payload)
    rows.sort(
        key=lambda row: (
            int(row.get("generated_at_epoch", 0)),
            str(row.get("submission_id", "")),
            str(row.get("_source_path", "")),
        )
    )
    return rows


def replay_simple_accept_reject_submissions(
    pack: dict[str, Any],
    submissions: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    items_by_id = {str(item["item_id"]): item for item in pack.get("items", [])}
    items_by_seq = {int(item["seq"]): item for item in pack.get("items", [])}
    effective: dict[str, dict[str, Any]] = {}
    for submission in submissions:
        overrides = {
            str(row["item_id"]): row
            for row in submission.get("overrides", [])
            if isinstance(row, dict) and str(row.get("item_id", "")) in items_by_id
        }
        for reviewed_range in submission.get("reviewed_ranges", []):
            from_seq = int(reviewed_range["from_seq"])
            to_seq = int(reviewed_range["to_seq"])
            if from_seq > to_seq:
                from_seq, to_seq = to_seq, from_seq
            for seq in range(from_seq, to_seq + 1):
                item = items_by_seq.get(seq)
                if item is None:
                    continue
                item_id = str(item["item_id"])
                override = overrides.get(item_id, {})
                effective[item_id] = {
                    "item_id": item_id,
                    "decision": str(override.get("decision") or "accept"),
                    "manual_correction_required": bool(
                        override.get(
                            "manual_correction_required",
                            item.get("manual_correction_required", False),
                        )
                    ),
                    "manual_segments": [
                        row for row in override.get("manual_segments", []) if isinstance(row, dict)
                    ],
                    "regions": [
                        row for row in override.get("regions", []) if isinstance(row, dict)
                    ],
                    "note": str(override.get("note", "")).strip(),
                    "submission_id": str(submission.get("submission_id", "")),
                    "generated_at_epoch": int(submission.get("generated_at_epoch", 0)),
                }
    return effective


def default_target_rows(item: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for target in review_action_targets(item):
        if not isinstance(target, dict):
            continue
        source = str(target.get("default_choice_source") or "current")
        if source == "current":
            continue
        rows.append(
            {
                "item_id": str(target.get("item_id") or ""),
                "choice_source": source,
                "selected_reading": target.get("default_reading"),
                "automatic_default": True,
            }
        )
    return [row for row in rows if row["item_id"]]


def review_action_targets(item: dict[str, Any]) -> list[dict[str, Any]]:
    interaction_spans = item.get("interaction_spans")
    if isinstance(interaction_spans, list) and interaction_spans:
        return [row for row in interaction_spans if isinstance(row, dict)]
    return [row for row in item.get("targets", []) if isinstance(row, dict)]


def merge_default_and_explicit_target_rows(
    item: dict[str, Any],
    explicit_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = {str(row["item_id"]): row for row in default_target_rows(item)}
    for row in translate_legacy_target_rows(item, explicit_rows):
        item_id = str(row.get("item_id") or "")
        if item_id:
            merged[item_id] = row
    return list(merged.values())


def translate_legacy_target_rows(
    item: dict[str, Any],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    spans_by_legacy_id = {
        str(legacy_id): span
        for span in item.get("interaction_spans", [])
        if isinstance(span, dict)
        for legacy_id in span.get("legacy_target_item_ids", [])
        if str(legacy_id)
    }
    translated: list[dict[str, Any]] = []
    for row in rows:
        target_id = str(row.get("item_id") or "")
        span = spans_by_legacy_id.get(target_id)
        if span is None:
            translated.append(row)
            continue
        candidate = interaction_candidate_for_legacy_row(span, row)
        translated.append(
            {
                **row,
                "item_id": str(span.get("span_id") or span.get("item_id") or ""),
                "choice_id": candidate.get("id") if candidate else row.get("choice_id"),
                "choice_source": candidate.get("source") if candidate else row.get("choice_source"),
                "selected_reading": candidate.get("reading") if candidate else row.get("selected_reading"),
                "legacy_target_item_id": target_id,
            }
        )
    return translated


def interaction_candidate_for_legacy_row(
    span: dict[str, Any],
    row: dict[str, Any],
) -> dict[str, Any] | None:
    candidates = [candidate for candidate in span.get("candidates", []) if isinstance(candidate, dict)]
    if row.get("choice_source") == "none":
        return next((candidate for candidate in candidates if candidate.get("source") == "none"), None)
    choice_id = str(row.get("choice_id") or "")
    if choice_id:
        match = next((candidate for candidate in candidates if candidate.get("id") == choice_id), None)
        if match is not None:
            return match
    source = str(row.get("choice_source") or "")
    if source:
        match = next((candidate for candidate in candidates if candidate.get("source") == source), None)
        if match is not None:
            return match
    selected = row.get("selected_reading")
    if isinstance(selected, str) and selected:
        normalized = normalize_hiragana_reading(selected)
        return next((candidate for candidate in candidates if candidate.get("reading") == normalized), None)
    return None


def apply_final_review_file(
    *,
    units_jsonl: Path,
    pack_json: Path,
    submission_store_dir: Path,
    output_jsonl: Path,
    summary_json: Path,
) -> dict[str, Any]:
    pack = load_json(pack_json)
    submissions = load_review_submissions(
        submission_store_dir,
        pack_id=str(pack["pack_id"]),
    )
    if not submissions:
        return {
            "stage_complete": False,
            "pack_id": str(pack["pack_id"]),
            "submission_count": 0,
            "blocking_reason": (
                f"No yomi final review submissions found for pack {pack['pack_id']}."
            ),
        }
    effective = replay_review_submissions(pack, submissions)
    items_by_id = {str(item["item_id"]): item for item in pack.get("items", [])}
    targets_by_id = {
        str(target["item_id"]): target
        for item in pack.get("items", [])
        for target in [*item.get("targets", []), *item.get("interaction_spans", [])]
        if isinstance(target, dict) and target.get("item_id")
    }

    read_units = 0
    written_units = 0
    reviewed_units = 0
    skipped_units = 0
    excluded_units = 0
    unconfirmed_excluded_units = 0
    target_override_count = 0
    span_override_count = 0
    exact_rendered_updates = 0
    exact_rendered_span_updates = 0
    no_ruby_target_count = 0
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    with units_jsonl.open(encoding="utf-8") as src, output_jsonl.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            read_units += 1
            unit = json.loads(line)
            item_id = str(unit.get("unit_id", ""))
            item_state = effective.get(item_id)
            if item_state is not None:
                reviewed_units += 1
                item = items_by_id.get(item_id, {})
                target_overrides = [
                    build_target_override(row, targets_by_id)
                    for row in item_state.get("targets", [])
                    if isinstance(row, dict)
                ]
                target_overrides = [row for row in target_overrides if row is not None]
                span_overrides = translate_span_override_target_ids(
                    item,
                    normalize_span_overrides(item_state.get("span_overrides", [])),
                )
                target_override_count += len(target_overrides)
                span_override_count += len(span_overrides)
                no_ruby_target_count += sum(
                    1 for row in target_overrides if row.get("choice_source") == "none"
                )
                disposition = normalize_scope_disposition(
                    item_state.get("disposition"),
                    skip=item_state.get("skip"),
                )
                if (
                    disposition == SCOPE_EXCLUDE
                    and not item_state.get("terminal_exclusion_confirmed")
                ):
                    unconfirmed_excluded_units += 1
                    dst.write(json.dumps(unit, ensure_ascii=False) + "\n")
                    written_units += 1
                    continue
                if disposition == SCOPE_SKIP:
                    skipped_units += 1
                elif disposition == SCOPE_EXCLUDE:
                    excluded_units += 1
                if disposition != SCOPE_KEEP:
                    exact_target_updates = 0
                    exact_span_updates = 0
                else:
                    exact_target_updates = apply_exact_rendered_target_overrides(unit, target_overrides)
                    exact_span_updates = apply_exact_rendered_span_overrides(unit, span_overrides)
                exact_updates = exact_target_updates + exact_span_updates
                exact_rendered_updates += exact_updates
                exact_rendered_span_updates += exact_span_updates
                set_final_review_payload(
                    unit,
                    pack_id=str(pack["pack_id"]),
                    item_state=item_state,
                    item=item,
                    target_overrides=target_overrides,
                    span_overrides=span_overrides,
                    exact_rendered_updates=exact_updates,
                    exact_rendered_span_updates=exact_span_updates,
                )
            dst.write(json.dumps(unit, ensure_ascii=False) + "\n")
            written_units += 1

    unreviewed_units = read_units - reviewed_units
    stage_complete = unreviewed_units == 0 and unconfirmed_excluded_units == 0
    summary = {
        "rule": APPLY_RULE,
        "pack_id": str(pack["pack_id"]),
        "submission_count": len(submissions),
        "submission_paths": [str(row.get("_source_path", "")) for row in submissions],
        "read_units": read_units,
        "written_units": written_units,
        "reviewed_units": reviewed_units,
        "unreviewed_units": unreviewed_units,
        "skipped_units": skipped_units,
        "excluded_units": excluded_units,
        "unconfirmed_excluded_units": unconfirmed_excluded_units,
        "target_override_count": target_override_count,
        "span_override_count": span_override_count,
        "no_ruby_target_count": no_ruby_target_count,
        "exact_rendered_updates": exact_rendered_updates,
        "exact_rendered_span_updates": exact_rendered_span_updates,
        "output_jsonl": str(output_jsonl),
        "summary_json": str(summary_json),
    }
    if unconfirmed_excluded_units:
        summary["blocking_reason"] = (
            f"Yomi final review has {unconfirmed_excluded_units} terminal exclusions "
            "without explicit confirmation."
        )
    elif not stage_complete:
        summary["blocking_reason"] = (
            f"Yomi final review is incomplete: {unreviewed_units} of {read_units} "
            "units have not been reviewed."
        )
    summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"stage_complete": stage_complete, **summary}


def build_target_override(
    row: dict[str, Any],
    targets_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    target_id = str(row.get("item_id", ""))
    target = targets_by_id.get(target_id)
    if target is None:
        return None
    override = {
        "item_id": target_id,
        "choice_id": str(row.get("choice_id") or ""),
        "choice_source": str(row.get("choice_source") or ""),
        "selected_reading": row.get("selected_reading"),
        "surface": target.get("surface"),
        "token_surface": target.get("token_surface"),
        "token_index": target.get("token_index"),
        "chunk_index": target.get("chunk_index"),
        "current_reading_hiragana": target.get("current_reading_hiragana"),
        "automatic_default": bool(row.get("automatic_default")),
    }
    if row.get("legacy_target_item_id"):
        override["legacy_target_item_id"] = str(row["legacy_target_item_id"])
    if (
        override["choice_source"] == "none"
        and override["current_reading_hiragana"]
        and not override["automatic_default"]
    ):
        override["rejected_readings"] = [
            {
                "surface": target.get("surface"),
                "reading": override["current_reading_hiragana"],
                "source": "human_no_ruby",
            }
        ]
    return override


def normalize_span_overrides(rows: object) -> list[dict[str, Any]]:
    normalized_rows: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return normalized_rows
    for row in rows:
        if not isinstance(row, dict):
            continue
        decision = str(row.get("decision") or "")
        if decision not in {"reading", "segmentation"}:
            continue
        original_surface = str(row.get("original_surface") or "")
        segments = []
        for segment in row.get("segments", []):
            if not isinstance(segment, dict):
                continue
            segments.append(
                {
                    "surface": str(segment.get("surface") or ""),
                    "reading": str(segment.get("reading") or ""),
                }
            )
        normalized_rows.append(
            {
                "id": str(row.get("id") or ""),
                "decision": decision,
                "target_item_ids": [
                    str(target_id)
                    for target_id in row.get("target_item_ids", [])
                    if str(target_id)
                ],
                "original_surface": original_surface,
                "segments": segments,
                "repair_required": bool(row.get("repair_required")),
                "repair_reason": str(row.get("repair_reason") or ""),
            }
        )
    return normalized_rows


def translate_span_override_target_ids(
    item: dict[str, Any],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    span_ids_by_legacy_id = {
        str(legacy_id): str(span.get("span_id") or span.get("item_id") or "")
        for span in item.get("interaction_spans", [])
        if isinstance(span, dict)
        for legacy_id in span.get("legacy_target_item_ids", [])
        if str(legacy_id)
    }
    translated: list[dict[str, Any]] = []
    for row in rows:
        target_ids = [
            span_ids_by_legacy_id.get(str(target_id), str(target_id))
            for target_id in row.get("target_item_ids", [])
        ]
        translated.append({**row, "target_item_ids": list(dict.fromkeys(target_ids))})
    return translated


def apply_exact_rendered_target_overrides(
    unit: dict[str, Any],
    target_overrides: list[dict[str, Any]],
) -> int:
    yomi = unit.get("analysis", {}).get("mechanical", {}).get("yomi", {})
    rendered = str(yomi.get("rendered") or "")
    if not rendered:
        return 0
    pairs = parse_rendered_pairs(rendered)
    updated = 0
    for override in target_overrides:
        selected = override.get("selected_reading")
        if not isinstance(selected, str) or not selected:
            continue
        token_surface = str(override.get("token_surface") or "")
        if override.get("surface") != token_surface:
            continue
        replacement_index: int | None = None
        token_index = override.get("token_index")
        if isinstance(token_index, int) and 0 <= token_index < len(pairs):
            if pairs[token_index][0] == token_surface:
                replacement_index = token_index
        if replacement_index is None:
            span = find_unique_rendered_span(pairs, token_surface)
            if span is not None and span[1] - span[0] == 1:
                replacement_index = span[0]
        if replacement_index is None:
            continue
        surface, old_reading = pairs[replacement_index]
        new_reading = hira_to_kata(selected)
        if old_reading != new_reading:
            pairs[replacement_index] = (surface, new_reading)
            updated += 1
    if updated:
        yomi["rendered_before_final_review"] = rendered
        yomi["rendered"] = " ".join(f"{surface}/{reading}" for surface, reading in pairs)
    return updated


def apply_exact_rendered_span_overrides(
    unit: dict[str, Any],
    span_overrides: list[dict[str, Any]],
) -> int:
    yomi = unit.get("analysis", {}).get("mechanical", {}).get("yomi", {})
    rendered = str(yomi.get("rendered") or "")
    if not rendered:
        return 0
    pairs = parse_rendered_pairs(rendered)
    updated = 0
    for override in span_overrides:
        original_surface = str(override.get("original_surface") or "")
        replacement_pairs = replacement_pairs_from_span_override(override)
        if not original_surface or not replacement_pairs:
            continue
        if "".join(surface for surface, _reading in replacement_pairs) != original_surface:
            continue
        span = find_unique_rendered_span(pairs, original_surface)
        if span is None:
            continue
        start, end = span
        if pairs[start:end] == replacement_pairs:
            continue
        pairs[start:end] = replacement_pairs
        updated += 1
    if updated:
        yomi.setdefault("rendered_before_final_review", rendered)
        yomi["rendered"] = " ".join(f"{surface}/{reading}" for surface, reading in pairs)
    return updated


def replacement_pairs_from_span_override(
    override: dict[str, Any],
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for segment in override.get("segments", []):
        if not isinstance(segment, dict):
            return []
        surface = str(segment.get("surface") or "")
        reading = str(segment.get("reading") or "")
        if not surface:
            return []
        if reading == "/" and not has_han(surface) and not has_latin(surface):
            reading = ""
        if is_numeric_only_finalized_correction_surface(surface):
            normalized = ""
        elif not has_han(surface) and not has_latin(surface) and not reading:
            normalized = hiragana_to_katakana_for_finalized_correction(surface)
        else:
            normalized = hira_to_kata(normalize_hiragana_reading(reading))
        if not validate_finalized_correction_reading(surface, normalized)["ok"]:
            return []
        pairs.append((surface, normalized))
    return pairs


def set_final_review_payload(
    unit: dict[str, Any],
    *,
    pack_id: str,
    item_state: dict[str, Any],
    item: dict[str, Any],
    target_overrides: list[dict[str, Any]],
    span_overrides: list[dict[str, Any]],
    exact_rendered_updates: int,
    exact_rendered_span_updates: int,
) -> None:
    human_review = unit.setdefault("analysis", {}).setdefault("human_review", {})
    disposition = normalize_scope_disposition(
        item_state.get("disposition"),
        skip=item_state.get("skip"),
    )
    human_review["yomi_final"] = {
        "rule": APPLY_RULE,
        "pack_id": pack_id,
        "reviewed": True,
        "item_id": item.get("item_id"),
        "disposition": disposition,
        "skip": disposition != SCOPE_KEEP,
        "exclude": disposition == SCOPE_EXCLUDE,
        "target_overrides": target_overrides,
        "span_overrides": span_overrides,
        "note": str(item_state.get("note", "")),
        "submission_id": str(item_state.get("submission_id", "")),
        "generated_at_epoch": int(item_state.get("generated_at_epoch", 0)),
        "exact_rendered_updates": exact_rendered_updates,
        "exact_rendered_span_updates": exact_rendered_span_updates,
    }
    set_manual_correction_required(
        unit,
        required=bool(item_state.get("manual_correction_required")),
        source_stage=REVIEW_STAGE,
        submission_id=str(item_state.get("submission_id", "")),
        generated_at_epoch=int(item_state.get("generated_at_epoch", 0)),
        reason=str(item_state.get("note", "")).strip(),
    )


def build_strong_repair_queue_file(
    *,
    units_jsonl: Path,
    output_jsonl: Path,
    summary_json: Path,
) -> dict[str, Any]:
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    read_units = 0
    queued_items = 0
    target_escalations = 0
    with units_jsonl.open(encoding="utf-8") as src, output_jsonl.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            read_units += 1
            unit = json.loads(line)
            review = (
                unit.get("analysis", {})
                .get("human_review", {})
                .get("yomi_final", {})
            )
            if not isinstance(review, dict) or not review.get("reviewed"):
                continue
            if review.get("skip"):
                continue
            target_constraints = [
                row
                for row in review.get("target_overrides", [])
                if isinstance(row, dict)
            ]
            span_repair_overrides = [
                row
                for row in review.get("span_overrides", [])
                if isinstance(row, dict) and row.get("repair_required")
            ]
            span_repair_target_ids = {
                str(target_id)
                for span in span_repair_overrides
                for target_id in span.get("target_item_ids", [])
                if str(target_id)
            }
            target_escalation_overrides = [
                row
                for row in target_constraints
                if row.get("choice_source") == "none"
                and not row.get("automatic_default")
                and str(row.get("item_id") or "") not in span_repair_target_ids
                and not is_no_ruby_laughter_w_override(row)
            ]
            if not target_escalation_overrides and not span_repair_overrides:
                continue
            rendered_yomi = (
                unit.get("analysis", {})
                .get("mechanical", {})
                .get("yomi", {})
                .get("rendered")
            )
            target_groups = group_consecutive_target_overrides(target_escalation_overrides)
            target_escalations += len(target_escalation_overrides)
            for group_index, target_group in enumerate(target_groups, start=1):
                rejected_span = target_group_rejected_span(
                    str(unit.get("text") or ""),
                    target_group,
                )
                dst.write(
                    json.dumps(
                        {
                            "item_id": f"{unit.get('unit_id')}::target_group:{group_index}",
                            "unit_id": unit.get("unit_id"),
                            "doc_id": unit.get("doc_id"),
                            "text": unit.get("text"),
                            "rendered_yomi": rendered_yomi,
                            "repair_scope": "target_group",
                            "rejected_span": rejected_span,
                            "repair_order": 1,
                            "reasons": ["target_no_ruby"],
                            "target_constraints": target_group,
                            "target_escalations": target_group,
                            # Backward-compatible alias for existing mock consumers.
                            "target_overrides": target_group,
                            "status": "mock_pending",
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                queued_items += 1
            for span_index, span_override in enumerate(span_repair_overrides, start=1):
                span_target = span_repair_target(span_override)
                if not span_target:
                    continue
                target_escalations += 1
                dst.write(
                    json.dumps(
                        {
                            "item_id": f"{unit.get('unit_id')}::span_group:{span_index}",
                            "unit_id": unit.get("unit_id"),
                            "doc_id": unit.get("doc_id"),
                            "text": unit.get("text"),
                            "rendered_yomi": rendered_yomi,
                            "repair_scope": "target_group",
                            "repair_order": 1,
                            "reasons": [span_override.get("repair_reason") or "span_repair_required"],
                            "target_constraints": [span_target],
                            "target_escalations": [span_target],
                            "target_overrides": [span_target],
                            "span_override": span_override,
                            "status": "mock_pending",
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                queued_items += 1
    summary = {
        "rule": "yomi_strong_repair_queue_v1",
        "read_units": read_units,
        "queued_items": queued_items,
        "target_escalations": target_escalations,
        "output_jsonl": str(output_jsonl),
        "summary_json": str(summary_json),
        "mock_only": False,
    }
    summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def is_no_ruby_laughter_w_override(row: dict[str, Any]) -> bool:
    if row.get("choice_source") != "none":
        return False
    return is_standalone_laughter_w(str(row.get("surface") or ""))


def span_repair_target(span_override: dict[str, Any]) -> dict[str, Any] | None:
    surface = str(span_override.get("original_surface") or "")
    if not surface:
        segment_surfaces = [
            str(segment.get("surface") or "")
            for segment in span_override.get("segments", [])
            if isinstance(segment, dict)
        ]
        surface = "".join(segment_surfaces)
    if not surface:
        return None
    return {
        "surface": surface,
        "token_surface": surface,
        "choice_source": "none",
        "selected_reading": None,
        "source_span_override_id": str(span_override.get("id") or ""),
        "rejected_readings": [
            {
                "surface": surface,
                "reading": "",
                "source": span_override.get("repair_reason") or "human_span_repair",
            }
        ],
    }


def apply_yomi_strong_repair_results_file(
    *,
    units_jsonl: Path,
    queue_jsonl: Path,
    results_jsonl: Path,
    output_jsonl: Path,
    summary_json: Path,
) -> dict[str, Any]:
    queue_rows = load_jsonl(queue_jsonl)
    result_rows = {str(row.get("item_id") or ""): row for row in load_jsonl(results_jsonl)}
    rows_by_unit: dict[str, list[dict[str, Any]]] = {}
    for row in queue_rows:
        unit_id = str(row.get("unit_id") or "")
        if unit_id:
            rows_by_unit.setdefault(unit_id, []).append(row)

    read_units = 0
    written_units = 0
    queued_items = len(queue_rows)
    applied_items = 0
    missing_results = 0
    parse_error_items = 0
    invalid_items = 0
    noop_items = 0
    unsupported_items = 0
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    with units_jsonl.open(encoding="utf-8") as src, output_jsonl.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            read_units += 1
            unit = json.loads(line)
            unit_id = str(unit.get("unit_id") or "")
            repair_log: list[dict[str, Any]] = []
            for queue_row in sorted(
                rows_by_unit.get(unit_id, []),
                key=strong_repair_apply_sort_key,
                reverse=True,
            ):
                item_id = str(queue_row.get("item_id") or "")
                result = result_rows.get(item_id)
                if result is None:
                    missing_results += 1
                    repair_log.append({"item_id": item_id, "status": "missing_result"})
                    continue
                if result.get("parse_error"):
                    parse_error_items += 1
                    repair_log.append(
                        {
                            "item_id": item_id,
                            "status": "parse_error",
                            "parse_error": result.get("parse_error"),
                        }
                    )
                    continue
                if queue_row.get("repair_scope") != "target_group":
                    unsupported_items += 1
                    repair_log.append(
                        {
                            "item_id": item_id,
                            "status": "unsupported_scope",
                            "repair_scope": queue_row.get("repair_scope"),
                        }
                    )
                    continue
                apply_result = apply_target_group_strong_repair(unit, queue_row, result)
                repair_log.append(
                    {
                        "item_id": item_id,
                        **apply_result,
                        "evidence": strong_repair_evidence(queue_row, result),
                    }
                )
                if apply_result["status"] == "applied":
                    applied_items += 1
                elif apply_result["status"] in {"reused_rejected_reading", "unchanged"}:
                    noop_items += 1
                else:
                    invalid_items += 1
            if repair_log:
                unit.setdefault("analysis", {}).setdefault("llm", {})["yomi_strong_repair"] = {
                    "rule": "yomi_strong_repair_apply_v1",
                    "repairs": repair_log,
                }
            dst.write(json.dumps(unit, ensure_ascii=False) + "\n")
            written_units += 1

    unresolved_items = missing_results + parse_error_items + invalid_items + unsupported_items
    review_pending_items = noop_items
    unapplied_items = queued_items - applied_items
    summary = {
        "rule": "yomi_strong_repair_apply_v1",
        "stage_complete": unapplied_items == 0,
        "read_units": read_units,
        "written_units": written_units,
        "queued_items": queued_items,
        "result_count": len(result_rows),
        "applied_items": applied_items,
        "unapplied_items": unapplied_items,
        "unresolved_items": unresolved_items,
        "review_pending_items": review_pending_items,
        "missing_results": missing_results,
        "parse_error_items": parse_error_items,
        "invalid_items": invalid_items,
        "noop_items": noop_items,
        "unsupported_items": unsupported_items,
        "output_jsonl": str(output_jsonl),
        "summary_json": str(summary_json),
    }
    if unapplied_items:
        if unresolved_items:
            summary["blocking_reason"] = (
                "Strong yomi repair has unresolved failed items; inspect "
                "yomi_strong_repair_apply_summary.json."
            )
        else:
            summary["blocking_reason"] = (
                "Strong yomi repair has no-op items awaiting human confirmation; inspect "
                "yomi_strong_repair_apply_summary.json."
            )
    summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def strong_repair_evidence(
    queue_row: dict[str, Any],
    result: dict[str, Any],
) -> list[dict[str, Any]]:
    parsed = result.get("parsed")
    if not isinstance(parsed, list):
        return []
    surface = str(queue_row.get("rejected_span") or "") or "".join(
        str(row.get("surface") or "")
        for row in queue_row.get("target_escalations", [])
        if isinstance(row, dict)
    )
    comments: list[str] = []
    used_web_search = False
    for row in parsed:
        if not isinstance(row, dict):
            continue
        comment = str(row.get("comment") or "").strip()
        if comment and comment not in comments:
            comments.append(comment)
        used_web_search = used_web_search or bool(row.get("used_web_search"))
    return [
        {
            "region_id": str(queue_row.get("item_id") or ""),
            "surface": surface,
            "comment": comment,
            "used_web_search": used_web_search,
            "surface_occurrence_index": queue_row.get("surface_occurrence_index"),
        }
        for comment in comments
    ]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    return rows


def strong_repair_apply_sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    targets = [target for target in row.get("target_escalations", []) if isinstance(target, dict)]
    token_indexes = [
        int(target["token_index"])
        for target in targets
        if isinstance(target.get("token_index"), int)
    ]
    return (
        int(row.get("repair_order") or 0),
        max(token_indexes) if token_indexes else -1,
        str(row.get("item_id") or ""),
    )


def apply_target_group_strong_repair(
    unit: dict[str, Any],
    queue_row: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    parsed = result.get("parsed")
    if not isinstance(parsed, list) or not parsed:
        return {"status": "invalid_result", "reason": "parsed result is not a non-empty array"}
    replacement_pairs = []
    for item in parsed:
        if not isinstance(item, dict):
            return {"status": "invalid_result", "reason": "parsed item is not an object"}
        surface = str(item.get("surface") or "")
        reading = str(item.get("reading") or "")
        if not surface:
            return {"status": "invalid_result", "reason": "empty replacement surface"}
        normalized = normalize_hiragana_reading(reading)
        if not is_valid_yomi_reading(normalized):
            return {
                "status": "invalid_result",
                "reason": "invalid replacement reading",
                "surface": surface,
                "reading": reading,
            }
        replacement_pairs.append((surface, hira_to_kata(normalized)))

    targets = [target for target in queue_row.get("target_escalations", []) if isinstance(target, dict)]
    rejected_pairs = rejected_surface_reading_pairs(targets)
    for surface, reading in replacement_pairs:
        if (surface, normalize_hiragana_reading(reading)) in rejected_pairs:
            return {
                "status": "reused_rejected_reading",
                "reason": "replacement reused rejected reading",
                "surface": surface,
                "reading": reading,
            }
    if not targets:
        return {"status": "invalid_queue", "reason": "target group lacks targets"}
    rejected_span = str(queue_row.get("rejected_span") or "") or "".join(
        str(target.get("surface") or "") for target in targets
    )
    replacement_pairs = interleave_repair_span_whitespace(
        rejected_span,
        replacement_pairs,
    )
    if replacement_pairs is None:
        return {
            "status": "surface_mismatch",
            "reason": "replacement items cross a source whitespace boundary",
            "rejected_span": rejected_span,
        }
    replacement_span = "".join(surface for surface, _reading in replacement_pairs)
    if replacement_span != rejected_span:
        return {
            "status": "surface_mismatch",
            "rejected_span": rejected_span,
            "replacement_span": replacement_span,
        }

    yomi = unit.setdefault("analysis", {}).setdefault("mechanical", {}).setdefault("yomi", {})
    rendered = str(yomi.get("rendered") or "")
    try:
        pairs = yomi_tokens_from_mapping(yomi, text=str(unit.get("text") or ""))
    except YomiTokenError as exc:
        return {"status": "invalid_unit", "reason": str(exc)}
    if not pairs:
        return {"status": "invalid_unit", "reason": "missing rendered yomi"}
    mapping = select_rendered_surface_span(
        pairs,
        rejected_span,
        targets=targets,
        reference_rendered=str(queue_row.get("rendered_yomi") or ""),
    )
    if mapping is None:
        return {
            "status": "surface_mismatch",
            "reason": "rejected span is absent or ambiguous in canonical yomi surfaces",
            "rejected_span": rejected_span,
        }
    mapped_pairs = mapped_replacement_pairs(replacement_pairs, mapping)
    if mapped_pairs is None:
        return {
            "status": "surface_mismatch",
            "reason": "rejected span has unsupported surrounding text",
            "rejected_span": rejected_span,
        }
    start = int(mapping["start"])
    end = int(mapping["end"])
    yomi.setdefault(
        "rendered_before_strong_repair",
        rendered or yomi_tokens_to_editable_rendered(pairs),
    )
    pairs[start:end] = [list(pair) for pair in mapped_pairs]
    set_canonical_yomi_tokens(yomi, pairs)
    yomi["rendered"] = " ".join(f"{surface}/{reading}" for surface, reading in pairs)
    return {
        "status": "applied",
        "rejected_span": rejected_span,
        "replacement": [
            {"surface": surface, "reading": reading}
            for surface, reading in mapped_pairs
        ],
    }


def interleave_repair_span_whitespace(
    rejected_span: str,
    replacement_pairs: list[tuple[str, str]],
) -> list[tuple[str, str]] | None:
    parts = [part for part in re.split(r"(\s+)", rejected_span) if part]
    if not any(part.isspace() for part in parts):
        return replacement_pairs
    output: list[tuple[str, str]] = []
    pair_index = 0
    for part in parts:
        if part.isspace():
            output.append((part, part))
            continue
        consumed = ""
        while len(consumed) < len(part) and pair_index < len(replacement_pairs):
            surface, reading = replacement_pairs[pair_index]
            if any(char.isspace() for char in surface):
                return None
            if not part.startswith(surface, len(consumed)):
                return None
            consumed += surface
            output.append((surface, reading))
            pair_index += 1
        if consumed != part:
            return None
    if pair_index != len(replacement_pairs):
        return None
    return output


def rejected_surface_reading_pairs(targets: list[dict[str, Any]]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for target in targets:
        for rejected in target.get("rejected_readings", []) or []:
            if not isinstance(rejected, dict):
                continue
            surface = str(rejected.get("surface") or "")
            reading = str(rejected.get("reading") or "")
            if surface and reading:
                pairs.add((surface, normalize_hiragana_reading(reading)))
    return pairs


def find_unique_rendered_span(
    pairs: list[tuple[str, str]],
    surface_span: str,
) -> tuple[int, int] | None:
    matches: list[tuple[int, int]] = []
    for start in range(len(pairs)):
        joined = ""
        for end in range(start + 1, len(pairs) + 1):
            joined += pairs[end - 1][0]
            if joined == surface_span:
                matches.append((start, end))
                break
            if not surface_span.startswith(joined):
                break
    if len(matches) != 1:
        return None
    return matches[0]


def group_consecutive_target_overrides(targets: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    def sort_key(target: dict[str, Any]) -> tuple[int, int, str]:
        token_index = target.get("token_index")
        chunk_index = target.get("chunk_index")
        return (
            int(token_index) if isinstance(token_index, int) else 10**9,
            int(chunk_index) if isinstance(chunk_index, int) else 0,
            str(target.get("item_id", "")),
        )

    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    previous_chunk: int | None = None
    previous_token: int | None = None
    for target in sorted(targets, key=sort_key):
        chunk_index = target.get("chunk_index")
        token_index = target.get("token_index")
        chunk = chunk_index if isinstance(chunk_index, int) else None
        token = token_index if isinstance(token_index, int) else None
        continues_previous = (
            bool(current)
            and chunk is not None
            and token is not None
            and previous_chunk is not None
            and previous_token is not None
            and (
                (token == previous_token and chunk == previous_chunk + 1)
                or (token == previous_token + 1 and chunk == 0)
            )
        )
        if not continues_previous:
            if current:
                groups.append(current)
            current = []
        current.append(target)
        previous_chunk = chunk
        previous_token = token
    if current:
        groups.append(current)
    return groups


def target_group_rejected_span(text: str, targets: list[dict[str, Any]]) -> str:
    starts = [
        int(target["target_start"])
        for target in targets
        if isinstance(target.get("target_start"), int)
    ]
    ends = [
        int(target["target_end"])
        for target in targets
        if isinstance(target.get("target_end"), int)
    ]
    if starts and ends:
        start = min(starts)
        end = max(ends)
        last_target = max(
            (
                target
                for target in targets
                if isinstance(target.get("target_end"), int)
            ),
            key=lambda target: int(target["target_end"]),
        )
        token_surface = str(last_target.get("token_surface") or "")
        surface = str(last_target.get("surface") or "")
        surface_end = token_surface.rfind(surface) + len(surface) if surface else 0
        suffix = token_surface[surface_end:] if surface_end >= len(surface) else ""
        if suffix and all(is_kana(char) for char in suffix) and text.startswith(suffix, end):
            end += len(suffix)
        if 0 <= start < end <= len(text):
            return text[start:end]

    # Older review artifacts do not carry absolute offsets. Reconstruct each
    # affected token from its first through last selected chunk so connectors
    # such as the ヶ in 島ヶ原 are not lost between chunk surfaces.
    token_parts: list[str] = []
    index = 0
    while index < len(targets):
        target = targets[index]
        token_index = target.get("token_index")
        token_surface = str(target.get("token_surface") or "")
        same_token = [target]
        index += 1
        while (
            index < len(targets)
            and token_index is not None
            and targets[index].get("token_index") == token_index
        ):
            same_token.append(targets[index])
            index += 1

        if not token_surface or any(
            str(item.get("token_surface") or "") != token_surface
            for item in same_token
        ):
            token_parts.extend(str(item.get("surface") or "") for item in same_token)
            continue

        cursor = 0
        start: int | None = None
        end: int | None = None
        for item in same_token:
            surface = str(item.get("surface") or "")
            position = token_surface.find(surface, cursor)
            if not surface or position < 0:
                start = None
                break
            if start is None:
                start = position
            end = position + len(surface)
            cursor = end
        if start is None or end is None:
            token_parts.extend(str(item.get("surface") or "") for item in same_token)
        else:
            suffix = token_surface[end:]
            if suffix and all(is_kana(char) for char in suffix):
                end = len(token_surface)
            token_parts.append(token_surface[start:end])

    return "".join(token_parts)


def finalize_reviewed_yomi_file(
    *,
    units_jsonl: Path,
    reviewed_units_jsonl: Path | None = None,
    strong_queue_summary_json: Path,
    strong_apply_summary_json: Path | None = None,
    output_jsonl: Path,
    summary_json: Path,
    skipped_output_jsonl: Path | None = None,
    excluded_output_jsonl: Path | None = None,
) -> dict[str, Any]:
    strong_summary = load_json(strong_queue_summary_json)
    queued_items = int(strong_summary.get("queued_items", 0))
    if queued_items:
        if strong_apply_summary_json is None or not strong_apply_summary_json.exists():
            return {
                "stage_complete": False,
                "queued_items": queued_items,
                "blocking_reason": "Strong yomi repair queue is not empty and has not been applied yet.",
            }
        strong_apply_summary = load_json(strong_apply_summary_json)
        if not strong_apply_summary.get("stage_complete"):
            return {
                "stage_complete": False,
                "queued_items": queued_items,
                "blocking_reason": str(
                    strong_apply_summary.get(
                        "blocking_reason",
                        "Strong yomi repair has unapplied items.",
                    )
                ),
            }
        if not strong_apply_summary.get("confirmed"):
            return {
                "stage_complete": False,
                "queued_items": queued_items,
                "blocking_reason": (
                    "Strong yomi repair results require human confirmation before finalization."
                ),
            }
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    if skipped_output_jsonl is not None:
        skipped_output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    if excluded_output_jsonl is not None:
        excluded_output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    read_units = 0
    written_units = 0
    skipped_units = 0
    excluded_units = 0
    unreviewed_units = 0
    review_by_unit_id = load_yomi_final_review_by_unit_id(reviewed_units_jsonl)
    skipped_dst = (
        skipped_output_jsonl.open("w", encoding="utf-8")
        if skipped_output_jsonl is not None
        else None
    )
    excluded_dst = (
        excluded_output_jsonl.open("w", encoding="utf-8")
        if excluded_output_jsonl is not None
        else None
    )
    try:
        with units_jsonl.open(encoding="utf-8") as src, output_jsonl.open("w", encoding="utf-8") as dst:
            for line in src:
                if not line.strip():
                    continue
                read_units += 1
                unit = json.loads(line)
                merge_yomi_final_review(unit, review_by_unit_id)
                review = (
                    unit.get("analysis", {})
                    .get("human_review", {})
                    .get("yomi_final", {})
                )
                if not isinstance(review, dict) or not review.get("reviewed"):
                    unreviewed_units += 1
                    continue
                disposition = normalize_scope_disposition(
                    review.get("disposition"),
                    skip=review.get("skip"),
                )
                if disposition == SCOPE_EXCLUDE:
                    excluded_units += 1
                    if excluded_dst is not None:
                        excluded_dst.write(
                            json.dumps(exclusion_tombstone_from_unit(unit), ensure_ascii=False)
                            + "\n"
                        )
                    continue
                if disposition == SCOPE_SKIP:
                    skipped_units += 1
                    if skipped_dst is not None:
                        skipped_dst.write(json.dumps(unit, ensure_ascii=False) + "\n")
                    continue
                canonicalize_finalized_unit_yomi(unit)
                dst.write(json.dumps(unit, ensure_ascii=False) + "\n")
                written_units += 1
    finally:
        if skipped_dst is not None:
            skipped_dst.close()
        if excluded_dst is not None:
            excluded_dst.close()
    summary = {
        "rule": "yomi_finalized_no_strong_repairs_v1",
        "read_units": read_units,
        "written_units": written_units,
        "skipped_units": skipped_units,
        "excluded_units": excluded_units,
        "unreviewed_units": unreviewed_units,
        "strong_queue_items": queued_items,
        "output_jsonl": str(output_jsonl),
        "skipped_output_jsonl": str(skipped_output_jsonl)
        if skipped_output_jsonl is not None
        else None,
        "excluded_output_jsonl": str(excluded_output_jsonl)
        if excluded_output_jsonl is not None
        else None,
        "summary_json": str(summary_json),
    }
    summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"stage_complete": True, **summary}


def exclusion_tombstone_from_unit(
    unit: dict[str, Any],
    *,
    reason_category: str = "sensitive_content",
    confirmation_submission_id: str | None = None,
    confirmed_at_epoch: int | None = None,
) -> dict[str, Any]:
    review = (
        unit.get("analysis", {})
        .get("human_review", {})
        .get("yomi_final", {})
    )
    return {
        "schema_version": 1,
        "excluded": True,
        "tombstone_label": "Removed",
        "doc_id": str(unit.get("doc_id") or ""),
        "track_doc_seq": unit.get("track_doc_seq"),
        "unit_id": str(unit.get("unit_id") or ""),
        "unit_seq": unit.get("unit_seq"),
        "reason_category": reason_category,
        "confirmation_submission_id": (
            str(confirmation_submission_id)
            if confirmation_submission_id is not None
            else str(review.get("submission_id") or "")
            if isinstance(review, dict)
            else ""
        ),
        "confirmed_at_epoch": (
            int(confirmed_at_epoch)
            if confirmed_at_epoch is not None
            else int(review.get("generated_at_epoch") or 0)
            if isinstance(review, dict)
            else 0
        ),
    }


def canonicalize_finalized_unit_yomi(
    unit: dict[str, Any],
    *,
    grandfathered_tokens: list[list[str]] | None = None,
) -> None:
    yomi = (
        unit.setdefault("analysis", {})
        .setdefault("mechanical", {})
        .setdefault("yomi", {})
    )
    tokens = canonicalize_final_numeric_compounds(
        normalize_parenthesized_laughter_tokens(
            normalize_correction_yomi_tokens(
                yomi_tokens_from_mapping(yomi, text=str(unit.get("text") or ""))
            )
        )
    )
    if not tokens:
        raise YomiTokenError(f"finalized unit {unit.get('unit_id') or '<unknown>'} has no yomi tokens")
    grandfathered_counts = Counter(
        (str(surface), str(reading))
        for surface, reading in (grandfathered_tokens or [])
    )
    human_readings = finalized_human_readings_by_surface(unit)
    for index, (surface, reading) in enumerate(tokens):
        validation = validate_finalized_correction_reading(surface, reading)
        if validation["ok"]:
            continue
        if grandfathered_counts[(surface, reading)]:
            grandfathered_counts[(surface, reading)] -= 1
            continue
        candidates = human_readings.get(surface, set())
        valid_candidates = sorted(
            candidate
            for candidate in candidates
            if validate_finalized_correction_reading(surface, candidate)["ok"]
        )
        if len(valid_candidates) == 1:
            tokens[index][1] = valid_candidates[0]
            continue
        raise YomiTokenError(
            f"finalized unit {unit.get('unit_id') or '<unknown>'} token {index} "
            f"{surface!r}/{reading!r} is structurally invalid: {validation['error']}"
        )
    set_canonical_yomi_tokens(yomi, tokens)


def finalized_human_readings_by_surface(unit: dict[str, Any]) -> dict[str, set[str]]:
    review = (
        unit.get("analysis", {})
        .get("human_review", {})
        .get("yomi_final", {})
    )
    readings: dict[str, set[str]] = {}
    for override in review.get("target_overrides", []) if isinstance(review, dict) else []:
        if not isinstance(override, dict) or override.get("selected_reading") is None:
            continue
        surface = str(override.get("token_surface") or override.get("surface") or "")
        if not surface:
            continue
        reading = hiragana_to_katakana_for_finalized_correction(
            str(override.get("selected_reading") or "")
        )
        readings.setdefault(surface, set()).add(reading)
    return readings


def load_yomi_final_review_by_unit_id(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    reviews: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            unit = json.loads(line)
            unit_id = str(unit.get("unit_id") or "")
            human_review = unit.get("analysis", {}).get("human_review", {})
            review = human_review.get("yomi_final", {})
            if unit_id and isinstance(review, dict) and review.get("reviewed"):
                reviews[unit_id] = {
                    "yomi_final": review,
                    "manual_correction": human_review.get("manual_correction"),
                }
    return reviews


def merge_yomi_final_review(unit: dict[str, Any], review_by_unit_id: dict[str, dict[str, Any]]) -> None:
    if not review_by_unit_id:
        return
    unit_id = str(unit.get("unit_id") or "")
    review_metadata = review_by_unit_id.get(unit_id)
    if not review_metadata:
        return
    human_review = unit.setdefault("analysis", {}).setdefault("human_review", {})
    current = human_review.get("yomi_final")
    if not isinstance(current, dict) or not current.get("reviewed"):
        human_review["yomi_final"] = review_metadata["yomi_final"]
    manual_correction = review_metadata.get("manual_correction")
    if "manual_correction" not in human_review and isinstance(manual_correction, dict):
        human_review["manual_correction"] = manual_correction


def harvest_yomi_finalization_artifacts_file(
    *,
    final_units_jsonl: Path,
    batch_manual_rewrites_jsonl: Path,
    batch_supplemental_furigana_tsv: Path,
    global_manual_rewrites_jsonl: Path,
    global_supplemental_furigana_tsv: Path,
    summary_json: Path,
    batch_name: str,
    track_name: str,
) -> dict[str, Any]:
    units = load_jsonl(final_units_jsonl)
    manual_rewrites = harvest_manual_yomi_rewrites(
        units,
        batch_name=batch_name,
        track_name=track_name,
    )
    supplemental_furigana = harvest_supplemental_furigana(
        units,
        batch_name=batch_name,
        track_name=track_name,
    )
    write_jsonl(batch_manual_rewrites_jsonl, manual_rewrites)
    write_tsv(
        batch_supplemental_furigana_tsv,
        supplemental_furigana,
        [
            "surface",
            "reading",
            "annotated_surface",
            "source_batch",
            "source_track",
            "source_unit_id",
            "source_method",
        ],
    )
    appended_rewrites = append_unique_jsonl(
        global_manual_rewrites_jsonl,
        manual_rewrites,
        key_fields=["original_surface", "replacement_rendered"],
    )
    appended_furigana = append_unique_tsv(
        global_supplemental_furigana_tsv,
        supplemental_furigana,
        [
            "surface",
            "reading",
            "annotated_surface",
            "source_batch",
            "source_track",
            "source_unit_id",
            "source_method",
        ],
        key_fields=["surface", "reading", "annotated_surface"],
    )
    summary = {
        "rule": "yomi_finalization_harvest_v1",
        "batch_name": batch_name,
        "track_name": track_name,
        "unit_count": len(units),
        "manual_rewrite_count": len(manual_rewrites),
        "manual_rewrite_appended_count": appended_rewrites,
        "supplemental_furigana_count": len(supplemental_furigana),
        "supplemental_furigana_appended_count": appended_furigana,
        "batch_manual_rewrites_jsonl": str(batch_manual_rewrites_jsonl),
        "batch_supplemental_furigana_tsv": str(batch_supplemental_furigana_tsv),
        "global_manual_rewrites_jsonl": str(global_manual_rewrites_jsonl),
        "global_supplemental_furigana_tsv": str(global_supplemental_furigana_tsv),
    }
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def harvest_manual_yomi_rewrites(
    units: list[dict[str, Any]],
    *,
    batch_name: str,
    track_name: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for unit in units:
        unit_id = str(unit.get("unit_id") or "")
        for repair in (
            unit.get("analysis", {})
            .get("llm", {})
            .get("yomi_strong_repair", {})
            .get("repairs", [])
        ):
            if not isinstance(repair, dict) or repair.get("status") != "applied":
                continue
            row = manual_rewrite_row_from_repair(
                repair,
                batch_name=batch_name,
                track_name=track_name,
                unit_id=unit_id,
                source="llm_strong_repair",
            )
            if row and (row["original_surface"], row["replacement_rendered"]) not in seen:
                seen.add((row["original_surface"], row["replacement_rendered"]))
                rows.append(row)
        manual = (
            unit.get("analysis", {})
            .get("human_review", {})
            .get("yomi_strong_repair", {})
        )
        if isinstance(manual, dict) and manual.get("manual_segments"):
            row = manual_rewrite_row_from_repair(
                {
                    "rejected_span": "".join(
                        str(segment.get("surface") or "")
                        for segment in manual.get("manual_segments", [])
                        if isinstance(segment, dict)
                    ),
                    "replacement": manual.get("manual_segments"),
                    "item_id": manual.get("item_id"),
                },
                batch_name=batch_name,
                track_name=track_name,
                unit_id=unit_id,
                source="human_strong_repair",
            )
            if row and (row["original_surface"], row["replacement_rendered"]) not in seen:
                seen.add((row["original_surface"], row["replacement_rendered"]))
                rows.append(row)
    return rows


def manual_rewrite_row_from_repair(
    repair: dict[str, Any],
    *,
    batch_name: str,
    track_name: str,
    unit_id: str,
    source: str,
) -> dict[str, Any] | None:
    original_surface = str(repair.get("rejected_span") or "")
    replacement = [row for row in repair.get("replacement", []) if isinstance(row, dict)]
    if not original_surface or not replacement:
        return None
    replacement_pairs: list[tuple[str, str]] = []
    for row in replacement:
        surface = str(row.get("surface") or "")
        reading = str(row.get("reading") or "")
        if not surface:
            return None
        replacement_pairs.append((surface, hira_to_kata(reading)))
    if "".join(surface for surface, _reading in replacement_pairs) != original_surface:
        return None
    replacement_rendered = " ".join(f"{surface}/{reading}" for surface, reading in replacement_pairs)
    return {
        "original_surface": original_surface,
        "replacement_rendered": replacement_rendered,
        "replacement": [
            {"surface": surface, "reading": reading}
            for surface, reading in replacement_pairs
        ],
        "source": source,
        "source_batch": batch_name,
        "source_track": track_name,
        "source_unit_id": unit_id,
        "source_item_id": str(repair.get("item_id") or ""),
    }


def harvest_supplemental_furigana(
    units: list[dict[str, Any]],
    *,
    batch_name: str,
    track_name: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    converter = base_furigana_converter()
    for unit in units:
        unit_id = str(unit.get("unit_id") or "")
        yomi = (
            unit.get("analysis", {})
            .get("mechanical", {})
            .get("yomi", {})
        )
        pairs = yomi_tokens_from_mapping(yomi, text=str(unit.get("text") or "")) if isinstance(yomi, dict) else []
        for surface, reading in pairs:
            if not surface or not reading or not has_han(surface):
                continue
            if surface in {"(笑)", "（笑）"}:
                continue
            result = converter.convert(surface, reading)
            if not result.annotated_surface or result.method == "exact_lookup":
                continue
            key = (surface, result.reading, result.annotated_surface)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "surface": surface,
                    "reading": result.reading,
                    "annotated_surface": result.annotated_surface,
                    "source_batch": batch_name,
                    "source_track": track_name,
                    "source_unit_id": unit_id,
                    "source_method": result.method,
                }
            )
    return rows


@lru_cache(maxsize=1)
def base_furigana_converter() -> FuriganaConverter:
    return FuriganaConverter.from_tsv_many([ANNOTATED_FORMS_PATH])


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_tsv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def append_unique_jsonl(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    key_fields: list[str],
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: set[tuple[str, ...]] = set()
    if path.exists():
        for row in load_jsonl(path):
            existing.add(tuple(str(row.get(field) or "") for field in key_fields))
    appended = 0
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            key = tuple(str(row.get(field) or "") for field in key_fields)
            if key in existing:
                continue
            existing.add(key)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            appended += 1
    return appended


def append_unique_tsv(
    path: Path,
    rows: list[dict[str, Any]],
    fieldnames: list[str],
    *,
    key_fields: list[str],
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: set[tuple[str, ...]] = set()
    if path.exists():
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            for row in reader:
                existing.add(tuple(str(row.get(field) or "") for field in key_fields))
    needs_header = not path.exists() or path.stat().st_size == 0
    appended = 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        if needs_header:
            writer.writeheader()
        for row in rows:
            key = tuple(str(row.get(field) or "") for field in key_fields)
            if key in existing:
                continue
            existing.add(key)
            writer.writerow({field: row.get(field, "") for field in fieldnames})
            appended += 1
    return appended


def parse_rendered_pairs(rendered: str) -> list[tuple[str, str]]:
    pairs = []
    for token in rendered.split():
        if "/" not in token:
            pairs.append((token, ""))
            continue
        surface, reading = token.rsplit("/", 1)
        pairs.append((surface, reading))
    return pairs


def review_yomi_pairs_for_unit(unit: dict[str, Any]) -> tuple[list[list[str]], str]:
    yomi = unit.get("analysis", {}).get("mechanical", {}).get("yomi", {})
    if not isinstance(yomi, dict):
        return [], "unit has no mechanical yomi object"
    try:
        pairs = yomi_tokens_from_mapping(yomi, text=str(unit.get("text") or ""))
    except YomiTokenError as exc:
        return [], f"canonical yomi tokenization failed: {exc}"
    if not pairs:
        return [], "unit has no canonical yomi tokens"
    return pairs, ""


def find_rendered_surface_spans(
    pairs: list[list[str]] | list[tuple[str, str]],
    surface_span: str,
) -> list[dict[str, Any]]:
    if not surface_span:
        return []
    surfaces = [str(surface) for surface, _reading in pairs]
    combined = "".join(surfaces)
    token_starts: list[int] = []
    cursor = 0
    for surface in surfaces:
        token_starts.append(cursor)
        cursor += len(surface)
    matches: list[dict[str, Any]] = []
    search_from = 0
    while True:
        char_start = combined.find(surface_span, search_from)
        if char_start < 0:
            break
        char_end = char_start + len(surface_span)
        start_index = next(
            (
                index
                for index, token_start in enumerate(token_starts)
                if token_start <= char_start < token_start + len(surfaces[index])
            ),
            None,
        )
        end_index = next(
            (
                index
                for index, token_start in enumerate(token_starts)
                if token_start < char_end <= token_start + len(surfaces[index])
            ),
            None,
        )
        if start_index is not None and end_index is not None:
            start_offset = char_start - token_starts[start_index]
            end_offset = char_end - token_starts[end_index]
            matches.append(
                {
                    "start": start_index,
                    "end": end_index + 1,
                    "char_start": char_start,
                    "char_end": char_end,
                    "start_offset": start_offset,
                    "end_offset": end_offset,
                    "prefix": surfaces[start_index][:start_offset],
                    "suffix": surfaces[end_index][end_offset:],
                }
            )
        search_from = char_start + 1
    return matches


def select_rendered_surface_span(
    pairs: list[list[str]] | list[tuple[str, str]],
    surface_span: str,
    *,
    targets: list[dict[str, Any]] | None = None,
    reference_rendered: str = "",
    occurrence_index: object = None,
) -> dict[str, Any] | None:
    matches = find_rendered_surface_spans(pairs, surface_span)
    if isinstance(occurrence_index, int) and 0 <= occurrence_index < len(matches):
        return matches[occurrence_index]
    token_indexes = [
        int(target["token_index"])
        for target in targets or []
        if isinstance(target.get("token_index"), int)
    ]
    if token_indexes:
        target_start = min(token_indexes)
        target_end = max(token_indexes)
        indexed = [
            match
            for match in matches
            if int(match["start"]) <= target_start
            and target_end < int(match["end"])
        ]
        if len(indexed) == 1:
            return indexed[0]
    occurrence_index = reference_surface_occurrence_index(
        reference_rendered,
        surface_span,
        targets=targets or [],
    )
    if occurrence_index is not None and occurrence_index < len(matches):
        return matches[occurrence_index]
    return matches[0] if len(matches) == 1 else None


def reference_surface_occurrence_index(
    rendered: str,
    surface_span: str,
    *,
    targets: list[dict[str, Any]],
) -> int | None:
    token_indexes = [
        int(target["token_index"])
        for target in targets
        if isinstance(target.get("token_index"), int)
    ]
    if not rendered or not surface_span or not token_indexes:
        return None
    pairs = parse_rendered_pairs(rendered)
    token_index = min(token_indexes)
    if not (0 <= token_index < len(pairs)):
        return None
    expected_start = sum(len(surface) for surface, _reading in pairs[:token_index])
    first_target = min(
        (
            target
            for target in targets
            if isinstance(target.get("token_index"), int)
        ),
        key=lambda target: int(target["token_index"]),
    )
    token_surface = str(first_target.get("token_surface") or "")
    target_surface = str(first_target.get("surface") or "")
    if token_surface and target_surface and target_surface in token_surface:
        expected_start += token_surface.index(target_surface)
    combined = "".join(surface for surface, _reading in pairs)
    starts: list[int] = []
    search_from = 0
    while True:
        start = combined.find(surface_span, search_from)
        if start < 0:
            break
        starts.append(start)
        search_from = start + 1
    if not starts:
        return None
    return min(range(len(starts)), key=lambda index: abs(starts[index] - expected_start))


def mapped_replacement_pairs(
    replacement_pairs: list[tuple[str, str]],
    mapping: dict[str, Any],
) -> list[tuple[str, str]] | None:
    if not replacement_pairs:
        return None
    prefix = str(mapping.get("prefix") or "")
    suffix = str(mapping.get("suffix") or "")
    if not re.fullmatch(r"[\u3040-\u30ffー]*", prefix + suffix):
        return None
    updated = list(replacement_pairs)
    if prefix:
        surface, reading = updated[0]
        updated[0] = (
            prefix + surface,
            hira_to_kata(kana_surface_to_hira(prefix)) + reading,
        )
    if suffix:
        surface, reading = updated[-1]
        updated[-1] = (
            surface + suffix,
            reading + hira_to_kata(kana_surface_to_hira(suffix)),
        )
    return updated


def hira_to_kata(text: str) -> str:
    chars = []
    for char in text:
        code = ord(char)
        if 0x3041 <= code <= 0x3096:
            chars.append(chr(code + 0x60))
        else:
            chars.append(char)
    return "".join(chars)


def sanitize_submission_id(submission_id: str) -> str:
    keep = []
    for char in submission_id:
        if char.isalnum() or char in {"_", "-", "."}:
            keep.append(char)
        else:
            keep.append("_")
    return "".join(keep)


def current_epoch() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def write_summary(summary: YomiFinalReviewPackSummary, summary_json: Path) -> None:
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    summary_json.write_text(
        json.dumps(asdict(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
